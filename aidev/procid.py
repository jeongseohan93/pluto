"""lock을 잡은 것이 정말 '그' 프로세스인가 — 신원을 적고, 묻고, 안전하게 끝낸다.

A pid alone is not an identity. The operating system hands the same number out
again once a process is reaped, so "pid 4812 is alive" answers a different
question from "the process that took this lock is alive". The gap between those
two is small in wall-clock terms and total in consequence: a `--stop` built on
pid alone would eventually kill a stranger's process.

So a lock records four things - pid, the process's creation time, which record
it was taken for, and which repository that record belongs to - and every
judgement below needs all four to agree before it will call the owner *live*.
Anything less is refused: an unreadable creation time, a lock written by an
older version, a pid that is alive but was born at a different moment. Refusing
costs a human one command; guessing costs someone their process.

Deliberately a leaf, like ``recovery.py`` and ``progress.py``: standard library
only (the project has no dependencies), and no import of ``pipeline``, which
imports this.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# The lock file's format version. A reader that meets a number it does not know
# treats the lock as 'legacy': unknown, never taken over, never killed.
SCHEMA = 1

# What a lock's owner turns out to be. Only ``LIVE`` may be stopped and only
# ``STALE`` may be taken over; everything else is reported to a human as-is.
ABSENT = "absent"  # no lock file at all
UNKNOWN = "unknown"  # a lock we cannot judge - empty, unreadable, or a pid we cannot probe
LEGACY = "legacy"  # a lock from before this module: pid only, no identity
FOREIGN = "foreign"  # a lock for some other record or some other repository
STALE = "stale"  # the owner is gone, or its pid has been handed to someone else
LIVE = "live"  # pid, creation time, record and repository all still agree

# Windows error codes ``OpenProcess`` answers with. 87 means the pid does not
# exist; 5 means it does and belongs to someone else.
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
# ``GetExitCodeProcess`` says this while a process is still running.
_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# ``ps`` is only reached on a POSIX system that is not Linux, and a hung ``ps``
# must never hang the pipeline.
_PS_TIMEOUT_S = 5.0
# Killing a process tree should be immediate; this is only a backstop.
_TASKKILL_TIMEOUT_S = 30.0


class TerminateRefused(Exception):
    """Asked to end a process, and would not - the caller turns this into a message."""


@dataclass(frozen=True)
class Identity:
    """Who holds a lock, in the four terms that have to agree before anything is done to them.

    ``start`` is an opaque platform token, not a timestamp to be compared with
    ``<``: on Linux it is a jiffy count since boot, on Windows a FILETIME, on
    other POSIX systems whatever ``ps`` prints. It is only ever tested for
    equality with itself, which is all pid-reuse detection needs.
    """

    pid: int
    start: Optional[str]
    label: str
    repo: str
    pgid: Optional[int] = None
    since: str = ""


@dataclass(frozen=True)
class Holder:
    """One lock, judged: what it is, who it names, and why it was called that."""

    status: str
    identity: Optional[Identity]
    text: str
    detail: str


def normalise_repo(repo: Any) -> str:
    """One spelling for a repository path, so two processes describing the same repo agree.

    ``resolve`` collapses symlinks and relative segments; ``normcase`` settles
    Windows' drive-letter and separator casing. An empty answer stays empty:
    a record built outside a repository has no repo to compare, and two of those
    match each other rather than everything.

    @param repo  a path, a string, or nothing at all
    @flow  empty -> "" ; else resolve -> normcase, and an unresolvable path stays as written
    """
    if not repo:
        return ""
    try:
        return os.path.normcase(str(Path(repo).resolve()))
    except OSError:
        return os.path.normcase(str(repo))


def _windows_probe(pid: int) -> Tuple[Optional[bool], Optional[str]]:
    """Ask Windows both questions about a pid in one handle: is it alive, and when did it start?

    Failure is never an exception here. A pid that cannot be opened at all is
    reported as not existing only for ``ERROR_INVALID_PARAMETER``; access denied
    means it does exist and belongs to another account, which is 'alive but
    unknowable' rather than 'gone'.

    @param pid  the process id to probe
    @flow  OpenProcess -> failed? (87 -> dead | 5 -> alive, unknown start | else unknown)
           -> GetExitCodeProcess -> GetProcessTimes -> creation FILETIME as one integer
    주요 내부 변수: handle(프로세스 핸들), creation(생성 시각 FILETIME)
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        code = ctypes.get_last_error()
        if code == _ERROR_INVALID_PARAMETER:
            return False, None
        if code == _ERROR_ACCESS_DENIED:
            return True, None
        return None, None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            alive: Optional[bool] = None
        else:
            alive = exit_code.value == _STILL_ACTIVE
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel_time = wintypes.FILETIME()
        user_time = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
        if not ok:
            return alive, None
        stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return alive, str(stamp)
    finally:
        kernel32.CloseHandle(handle)


def _linux_start(pid: int) -> Optional[str]:
    """Field 22 of ``/proc/<pid>/stat``: the jiffy this process was born on.

    The comm field is parenthesised and may itself contain spaces and brackets,
    so the line is cut at its *last* ``)`` before it is split - the usual way
    this parse is got wrong.

    @param pid  the process id to read
    @flow  read stat -> cut after the last ')' -> token 19 is field 22
    주요 내부 변수: tail(comm 뒤의 나머지), fields(그 토큰들)
    """
    text = Path("/proc/{0}/stat".format(pid)).read_text(encoding="utf-8", errors="replace")
    cut = text.rfind(")")
    if cut < 0:
        return None
    fields = text[cut + 1 :].split()
    if len(fields) <= 19:
        return None
    return fields[19]


def _ps_start(pid: int) -> Optional[str]:
    """What ``ps`` says this process started at, for the POSIX systems without /proc.

    ``lstart`` is used rather than ``etime`` because it names an absolute moment:
    a recycled pid gets a different one, which is the whole point.

    @param pid  the process id to ask about
    @flow  run ps -> non-zero or empty -> None ; else the line, whitespace collapsed
    """
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
        timeout=_PS_TIMEOUT_S,
    )
    if result.returncode != 0:
        return None
    line = " ".join((result.stdout or "").split())
    return line or None


def process_start(pid: int) -> Optional[str]:
    """When this pid's current occupant was created, as an opaque platform token.

    ``None`` means "not answerable" and never "not running": a pid on another
    account, a kernel that will not say, a ``ps`` that timed out. Every caller
    treats an unanswerable start as a reason to refuse rather than to act, so
    this function's only invariant is that it does not raise.

    @param pid  the process id to ask about
    @flow  pid <= 0 -> None ; Windows -> probe ; Linux -> /proc ; else -> ps
    """
    if pid is None or pid <= 0:
        return None
    try:
        if os.name == "nt":
            return _windows_probe(pid)[1]
        if Path("/proc/{0}/stat".format(pid)).exists():
            return _linux_start(pid)
        return _ps_start(pid)
    except Exception:
        # Includes OSError, subprocess.TimeoutExpired and anything ctypes raises
        # on a platform that does not have the call. None is the honest answer
        # to all of them, and a probe must never be why a command fails.
        return None


def process_alive(pid: int) -> Optional[bool]:
    """Is anything running under this pid right now - True, False, or 'cannot tell'.

    Signal 0 is the POSIX question that costs nothing. ``PermissionError`` is a
    yes: only an existing process can refuse us. Non-positive pids are answered
    False without asking, because ``os.kill(0, 0)`` would signal our own group.

    @param pid  the process id to test
    @flow  pid <= 0 -> False ; Windows -> probe ; else kill(pid, 0) -> yes / gone / denied=yes
    """
    if pid is None or pid <= 0:
        return False
    try:
        if os.name == "nt":
            return _windows_probe(pid)[0]
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return None


_own_start: Dict[int, Optional[str]] = {}


def own_start() -> Optional[str]:
    """This process's creation token, read once and remembered - it cannot change while we run.

    Worth caching: on a POSIX system without ``/proc`` reading it costs a
    subprocess, and a lock is constructed on every command.

    @flow  cached for this pid -> return it ; else read it and remember
    """
    pid = os.getpid()
    if pid not in _own_start:
        _own_start[pid] = process_start(pid)
    return _own_start[pid]


def current_identity(label: str, repo: Any) -> Identity:
    """Who we are, in the terms a lock records.

    ``pgid`` is only meaningful on POSIX, and only says something when it equals
    our own pid: that is what "launched into its own process group" looks like
    from the inside, and it is the only case in which a group may be signalled.

    @param label  what this lock is for, e.g. ``slice 20260822-x``
    @param repo   the repository the record belongs to
    @flow  read our start -> POSIX? also read our process group -> Identity
    """
    pid = os.getpid()
    pgid: Optional[int] = None
    if os.name != "nt":
        try:
            pgid = os.getpgid(pid)
        except (AttributeError, OSError):
            pgid = None
    return Identity(
        pid=pid,
        start=own_start(),
        label=str(label),
        repo=normalise_repo(repo),
        pgid=pgid,
        since=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def encode(identity: Identity) -> str:
    """The identity as the one line a lock file holds.

    JSON rather than the old ``key value`` lines because an older ``aidev``
    reading this file only ever joins its lines into a message - it cannot
    misread the new shape, only quote it.

    @param identity  who is taking the lock
    """
    return (
        json.dumps(
            {
                "schema": SCHEMA,
                "pid": identity.pid,
                "start": identity.start,
                "label": identity.label,
                "repo": identity.repo,
                "pgid": identity.pgid,
                "since": identity.since,
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def decode(text: str) -> Optional[Identity]:
    """Read a lock file's text back, or None when it is not one of ours.

    None covers every 'older or broken' case at once - the pre-identity two-line
    format, a truncated write, a schema from a future version - and every caller
    turns None into a refusal rather than into an assumption.

    @param text  the lock file's whole contents
    @flow  not JSON / not an object / no integer pid / unknown schema -> None ; else Identity
    주요 내부 변수: data(파싱된 객체), pid(정수여야 하는 유일한 필드)
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    pgid = data.get("pgid")
    start = data.get("start")
    return Identity(
        pid=pid,
        start=str(start) if isinstance(start, (str, int)) else None,
        label=str(data.get("label") or ""),
        repo=str(data.get("repo") or ""),
        pgid=pgid if isinstance(pgid, int) and not isinstance(pgid, bool) else None,
        since=str(data.get("since") or ""),
    )


def describe(identity: Identity) -> str:
    """One line naming the holder, in the shape the refusal message has always used.

    @param identity  the holder read out of a lock
    """
    parts = ["pid {0}".format(identity.pid)]
    if identity.since:
        parts.append("since {0}".format(identity.since))
    return "; ".join(parts)


def inspect(path: Any, expected: Optional[Identity] = None) -> Holder:
    """Judge one lock file: absent, ours to take over, someone else's, or unreadable.

    The order of the questions is the safety argument. Record and repository are
    compared before the process is probed, so we never reason about a pid that
    was never ours to reason about. Then liveness, then - only for a live pid -
    the creation time, which is what tells a running owner apart from a stranger
    who inherited its number.

    @param path      the lock file
    @param expected  who we are, for the record/repository comparison; None skips it
    @flow  missing -> absent ; empty/unreadable -> unknown ; not ours -> legacy ;
           other record or repo -> foreign ; dead pid -> stale ; unprobeable -> unknown ;
           start differs -> stale (pid reuse) ; all four agree -> live
    주요 내부 변수: identity(lock이 적어둔 신원), alive(생존 여부), start(현재 점유자의 생성 시각)
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return Holder(ABSENT, None, "", "")
    except OSError as exc:
        return Holder(UNKNOWN, None, "", "the lock file could not be read: {0}".format(exc))
    if not raw.strip():
        return Holder(
            UNKNOWN, None, "", "the lock file is empty - another process may be writing it right now"
        )
    identity = decode(raw)
    if identity is None:
        legacy = "; ".join(line.strip() for line in raw.splitlines() if line.strip())
        return Holder(
            LEGACY,
            None,
            legacy,
            "this lock carries no process identity (an older format), so it is neither "
            "taken over nor stopped automatically",
        )
    text = describe(identity)
    if expected is not None and identity.label != expected.label:
        return Holder(
            FOREIGN,
            identity,
            text,
            "this lock was taken for {0!r}, not {1!r}".format(identity.label, expected.label),
        )
    if expected is not None and identity.repo != expected.repo:
        return Holder(
            FOREIGN,
            identity,
            text,
            "this lock names another repository: {0} (here: {1})".format(
                identity.repo or "(none)", expected.repo or "(none)"
            ),
        )
    alive = process_alive(identity.pid)
    if alive is False:
        return Holder(STALE, identity, text, "pid {0} is gone".format(identity.pid))
    if alive is None:
        return Holder(
            UNKNOWN, identity, text, "whether pid {0} is running cannot be told".format(identity.pid)
        )
    if not identity.start:
        return Holder(
            UNKNOWN,
            identity,
            text,
            "pid {0} is running but the lock never recorded a creation time, so it "
            "cannot be told from a process that merely reuses the number".format(identity.pid),
        )
    start = process_start(identity.pid)
    if start is None:
        return Holder(
            UNKNOWN,
            identity,
            text,
            "pid {0} is running but its creation time cannot be read".format(identity.pid),
        )
    if start != identity.start:
        return Holder(
            STALE,
            identity,
            text,
            "pid {0} is running but was created at a different moment - the number has "
            "been handed to another process".format(identity.pid),
        )
    return Holder(LIVE, identity, text, "")


def owns_group(identity: Identity) -> bool:
    """Was this process launched into a process group of its own, so signalling the group is safe?

    Only true when the recorded group id is the process's own pid. Any other
    value means the group holds processes we did not start - a shell, a terminal,
    the pipeline's own parent - and signalling it would reach them too.

    @param identity  the holder read out of a lock
    """
    return os.name != "nt" and identity.pgid is not None and identity.pgid == identity.pid


def _killpg(pgid: int, sig: int) -> None:
    """Send one signal to a whole process group. A seam, so tests can watch it instead of dying.

    @param pgid  the process group to signal
    @param sig   the signal number
    """
    os.killpg(pgid, sig)


def _taskkill(pid: int) -> Any:
    """End a process and everything under it, the only way Windows offers. Also a test seam.

    @param pid  the process at the root of the tree
    """
    return subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        timeout=_TASKKILL_TIMEOUT_S,
    )


def terminate(identity: Identity) -> str:
    """End the process this identity names, or refuse - and say which in one line.

    The two platforms are deliberately asymmetric. POSIX gets ``SIGTERM`` to the
    process group, and only when that group is the process's own, so a pipeline
    started from someone's shell is never signalled through their terminal; a
    bare ``SIGTERM`` to the parent pid is not offered at all, because it leaves
    the session's children orphaned. Windows has no polite group signal, so the
    tree is ended forcibly - that is the only form there that leaves nothing
    behind.

    @param identity  the holder, already judged live by ``inspect``
    @flow  Windows -> taskkill /T /F, non-zero exit -> refused ;
           POSIX -> not its own group? refused ; else SIGTERM to the group
    주요 내부 변수: result(taskkill의 결과)
    """
    if os.name == "nt":
        result = _taskkill(identity.pid)
        if getattr(result, "returncode", 1) != 0:
            raise TerminateRefused(
                "taskkill could not end pid {0}: {1}".format(
                    identity.pid, (getattr(result, "stderr", "") or "").strip() or "no reason given"
                )
            )
        return "ended pid {0} and its children (taskkill /T /F)".format(identity.pid)
    if not owns_group(identity):
        raise TerminateRefused(
            "pid {0} was not launched into a process group of its own, so there is no "
            "group that is only it - stop it where it runs instead".format(identity.pid)
        )
    import signal as signal_module

    _killpg(identity.pgid, signal_module.SIGTERM)
    return "sent SIGTERM to process group {0} (pid {1})".format(identity.pgid, identity.pid)
