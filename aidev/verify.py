"""Verification the engine runs itself, and the documents a failure leaves behind.

Measured 2026-08-16/17: the ``test`` stage was an agent session whose whole job
was to re-run a command and write one ``TEST_RESULT:`` line. Eight runs out of
eight passed - $0.4 each for zero information. The commands were already declared
in the requirement's front matter, so the engine can run them itself: a pass
costs no session at all, and a failure spends its money on *diagnosis* instead of
on re-reading a green suite.

This module knows nothing about the pipeline. It takes commands, runs them,
turns their output into a structure, and renders that structure as markdown.
``aidev.pipeline`` imports it; it imports nothing back, so there is no cycle and
the parsing half can be tested without a repository.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# A verification command that has not finished in half an hour is not going to.
VERIFY_TIMEOUT_S = 1800.0
# failure.md lists this many failures by name; the rest are a count plus the log.
MAX_FAILURES_LISTED = 20
TRACEBACK_LINES = 15
# What the summary handed back to a session may say about a run that produced no
# parseable failures - a tail, never the whole output.
TAIL_LINES = 60


def split_command(command: str) -> List[str]:
    """Split a declared command into argv. Windows keeps its backslashes.

    Lived in ``pipeline`` until the engine needed it too; ``pipeline`` imports
    this name so every existing caller of ``pipeline.split_command`` still works.

    @param command  one plain command as a human wrote it in the front matter
    """
    if os.name == "nt":
        # POSIX mode would eat ``C:\path\to`` one backslash at a time.
        tokens = shlex.split(command, posix=False)
        return [token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"' else token
                for token in tokens]
    return shlex.split(command)


# ------------------------------------------------------- temporary file names
#
# Measured: three slices left scratch work on the branch (.tsscratch,
# reindent_tmp.py, reindent.py). The convention said not to and the convention
# was not read, so the name is judged by machine instead. Nothing here knows
# about git or the repository - it is a predicate over a filename, which is why
# it lives beside the parsers and not in the pipeline.

# Whole tokens, matched against the basename split on [^A-Za-z0-9]+: 'oldest.py'
# is not 'old' and 'useDebug.ts' is not 'debug'. One false positive kills a whole
# slice, so this list is chosen to be exact rather than wide.
TEMP_TOKENS: Tuple[str, ...] = (
    "tmp", "temp", "scratch", "scratchpad", "reindent",
    "debug", "bak", "backup", "wip", "untitled",
)
# Temporary wherever it sits in the name - what does not split into tokens,
# '.tsscratch' being the measured one.
TEMP_SUBSTRINGS: Tuple[str, ...] = ("scratch",)
# Extensions that are the leftovers themselves: editors, patches, merges.
TEMP_SUFFIXES: Tuple[str, ...] = (".tmp", ".temp", ".bak", ".orig", ".rej", ".swp", ".swo")

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def looks_temporary(path: str) -> bool:
    """Does this path's filename read as temporary working scratch?

    Only the basename is judged: ``src/debug/panel.ts`` is a module in a folder
    called debug, ``src/debug.ts`` is a file called debug. A directory name is
    somebody's architecture and not their leftovers.

    @param path  a repository-relative path, with either separator
    @flow  basename lowered -> '~' tail -> suffix -> substring -> whole tokens
    주요 내부 변수: name(소문자 basename), tokens(이름을 쪼갠 조각들)
    """
    name = str(path or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not name:
        return False
    if name.endswith("~"):  # the editor's own backup
        return True
    if name.endswith(TEMP_SUFFIXES):
        return True
    for fragment in TEMP_SUBSTRINGS:
        if fragment in name:
            return True
    tokens = [token for token in _TOKEN_SPLIT_RE.split(name) if token]
    return any(token in TEMP_TOKENS for token in tokens)


@dataclass
class FailedTest:
    """One failing test, located precisely enough for an editor to jump to it."""

    name: str
    file: str = ""
    line: Optional[int] = None
    message: str = ""

    def where(self) -> str:
        """``tests/test_x.py:42``, or the test's own name when there is no location."""
        if self.file and self.line:
            return "{0}:{1}".format(self.file, self.line)
        return self.file or self.name

    def to_dict(self) -> Dict[str, Any]:
        """The whole failure as JSON - message included, since this is what a report renders from."""
        return {"name": self.name, "file": self.file, "line": self.line, "message": self.message}


@dataclass
class CommandResult:
    """One command that was run, and what came of it."""

    command: str
    exit_code: Optional[int]  # None: it never ran (missing binary, or a timeout)
    duration_s: float = 0.0
    output: str = ""
    log_path: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Only exit 0 passes - a command that never ran carries ``None`` and is not a pass."""
        return self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        """The JSON form, deliberately without ``output`` - state.json points at the log instead."""
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "log": self.log_path,
        }


@dataclass
class VerifyResult:
    """Everything one verification attempt produced, in one value.

    The three counts are ``None`` until a runner's output says otherwise: a
    suite that printed no summary line is unknown, not zero. Measured
    2026-08-19, a whole passing suite was reported as ``0 passed`` because the
    number was invented here rather than read.
    """

    ok: bool = True
    commands: List[CommandResult] = field(default_factory=list)
    failures: List[FailedTest] = field(default_factory=list)
    passed: Optional[int] = None
    failed: Optional[int] = None
    skipped: Optional[int] = None
    parsed: bool = False
    spec_violations: List[Any] = field(default_factory=list)
    # Paths this slice added whose names read as scratch work. Filled by the
    # pipeline, which is the only side that knows what "this slice added" means.
    temp_files: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Did everything pass - the commands, the spec check, and the temp-file guard?"""
        return self.ok and not self.spec_violations and not self.temp_files

    def failure_count(self) -> int:
        """How big the failure is, for the diagnosis grade. Spec violations count too.

        An unread count (``None``) falls through to the listed failures, exactly
        as a zero one did - which is why this body did not have to change. A
        temporary file counts as one failure for the same reason a violation does.
        """
        counted = self.failed or len(self.failures)
        return counted + len(self.spec_violations) + len(self.temp_files)

    def first_failing(self) -> Optional[CommandResult]:
        """The command that stopped the run, in declared order - the one a failure report quotes.

        @flow  commands in order -> first not ok ; none failed -> None
        """
        for result in self.commands:
            if not result.ok:
                return result
        return None

    def to_dict(self) -> Dict[str, Any]:
        """What state.json keeps of an attempt: counts in full, failures capped, violations and temp files as text.

        A count nobody could read is stored as ``null``, so a reader can tell
        "the output never said" from "it really was none". The temporary files
        go in whole: there are never many, and which file it was is the answer.
        """
        return {
            "ok": self.ok,
            "commands": [result.to_dict() for result in self.commands],
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "parsed": self.parsed,
            "failures": [failure.to_dict() for failure in self.failures[:MAX_FAILURES_LISTED]],
            "spec_violations": [str(violation) for violation in self.spec_violations],
            "temp_files": list(self.temp_files),
        }


# --------------------------------------------------------------------- running


def _slug(command: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", command.strip().lower()).strip("-")
    return (text or "command")[:32]


def _write_log(path: Path, text: str) -> Optional[str]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", errors="replace", newline="\n")
    except OSError:
        return None
    return str(path)


def run_commands(
    commands: Sequence[str],
    cwd: Path,
    log_dir: Optional[Path] = None,
    timeout: float = VERIFY_TIMEOUT_S,
    attempt: int = 1,
    env: Optional[Dict[str, str]] = None,
) -> VerifyResult:
    """Run the declared verification commands in order and read their output.

    Stops at the first failing command: a project that declares ``lint`` before
    ``test`` should not pay for the whole suite to learn what lint already said.
    A count the output never mentioned stays ``None`` rather than becoming 0 -
    ``_add`` is what keeps "unknown" from being reported as "none".

    @param commands  the declared commands, each one plain command with no shell
    @param cwd       where they run - the worktree, when the slice is isolated
    @param log_dir   where each command's full output is written; None keeps it in memory
    @param timeout   seconds one command may take before it is killed
    @param attempt   which verification round this is, used only to name the logs
    @param env       extra environment for the child processes
    @flow  for each command: which() -> subprocess.run -> log -> parse -> stop on failure
    주요 내부 변수: result(누적 VerifyResult), started(명령 시작 시각)
    """
    result = VerifyResult(ok=True)
    for index, command in enumerate(commands, start=1):
        single = _run_one(command, cwd, timeout, env)
        if log_dir is not None:
            single.log_path = _write_log(
                Path(log_dir) / "{0:02d}-{1}-{2}.log".format(attempt, index, _slug(command)),
                single.output,
            )
        result.commands.append(single)
        failures, counts, parsed = parse_output(single.output)
        result.passed = _add(result.passed, counts.get("passed"))
        result.failed = _add(result.failed, _sum_present(counts, "failed", "error"))
        result.skipped = _add(result.skipped, counts.get("skipped"))
        if failures:
            result.failures.extend(failures)
        result.parsed = result.parsed or parsed
        if not single.ok:
            result.ok = False
            break  # the rest would only cost time; this one already decided it
    return result


def _add(total: Optional[int], seen: Optional[int]) -> Optional[int]:
    """Add a count one command reported to the running total, keeping "never said" as ``None``.

    @param total  what the earlier commands added up to, or None if none said
    @param seen   what this command's output said, or None if it said nothing
    @flow  nothing seen -> the total as it was ; else add, treating None as 0
    """
    if seen is None:
        return total
    return int(seen) + (total or 0)


def _sum_present(counts: Dict[str, int], *keys: str) -> Optional[int]:
    """The keys this output actually carried, added up - ``None`` when it carried none of them.

    @param counts  what ``parse_output`` read out of one command
    @param *keys   the count names that belong together, e.g. failed and error
    @flow  each key present -> add ; nothing present -> None
    """
    total: Optional[int] = None
    for key in keys:
        if key in counts:
            total = int(counts[key]) + (total or 0)
    return total


def _run_one(
    command: str, cwd: Path, timeout: float, env: Optional[Dict[str, str]] = None
) -> CommandResult:
    """Run one declared command with no shell, the way ``run_setup`` already does.

    @param command  the declared command
    @param cwd      working directory
    @param timeout  seconds before it is killed
    @param env      extra environment variables, merged over the current one
    """
    argv = split_command(command)
    if not argv:
        return CommandResult(command=command, exit_code=None, output="empty command")
    exe = shutil.which(argv[0], path=os.environ.get("PATH"))
    if exe is None:
        return CommandResult(
            command=command,
            exit_code=None,
            output="command not found on PATH: {0}".format(argv[0]),
        )
    started = time.time()
    try:
        proc = subprocess.run(
            [exe] + argv[1:],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=({**os.environ, **env} if env else None),
        )
        output, code = proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        partial = exc.output or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output = "{0}\ntimed out after {1}s".format(partial, timeout)
        code = None
    except OSError as exc:
        output, code = str(exc), None
    return CommandResult(
        command=command, exit_code=code, duration_s=time.time() - started, output=output
    )


# --------------------------------------------------------------------- parsing
#
# Best effort, and deliberately so: when the output cannot be read the failure
# report carries the raw tail instead, and the grade goes to 'large' - a run
# nobody can summarise is exactly when a diagnosis session earns its money.

# pytest's short summary: "FAILED tests/test_x.py::test_y - AssertionError: ..."
_PYTEST_SUMMARY_RE = re.compile(
    r"^(?:FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE
)
# The location line pytest prints above a traceback: "tests/test_x.py:42: in f".
_LOCATION_RE = re.compile(r"^([^\s:]+\.py):(\d+):\s*(.*)$", re.MULTILINE)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")
# jest / vitest, enough to place the file and name the failing test.
_JS_FAIL_RE = re.compile(r"^\s*(?:FAIL|✕|×)\s+(\S+.*?)\s*$", re.MULTILINE)
_JS_COUNT_RE = re.compile(r"Tests?:\s+(?:(\d+)\s+failed,\s+)?(\d+)\s+passed")

_MESSAGE_LIMIT = 160


def parse_output(text: str) -> Tuple[List[FailedTest], Dict[str, int], bool]:
    """Pull failing tests and pass/fail counts out of a runner's stdout.

    pytest is read properly; jest and vitest are read well enough to name a file.
    Anything else returns ``parsed=False`` and the caller falls back to the tail.

    @param text  the command's combined stdout+stderr
    @flow  pytest summary -> locations -> counts -> (nothing found) js patterns
    주요 내부 변수: failures(수집된 실패), counts(passed/failed/skipped), locations(파일별 첫 위치)
    """
    text = text or ""
    counts: Dict[str, int] = {}
    for match in _COUNT_RE.finditer(text):
        key = match.group(2).rstrip("s") if match.group(2).startswith("error") else match.group(2)
        key = "error" if key.startswith("error") else key
        counts[key] = counts.get(key, 0) + int(match.group(1))

    locations = _locations(text)
    failures: List[FailedTest] = []
    for match in _PYTEST_SUMMARY_RE.finditer(text):
        nodeid = match.group(1)
        message = _clip((match.group(2) or "").strip(), _MESSAGE_LIMIT)
        path, _, _rest = nodeid.partition("::")
        file = path if path.endswith(".py") else ""
        line, located_message = locations.get(file, (None, ""))
        failures.append(
            FailedTest(
                name=nodeid,
                file=file.replace("\\", "/"),
                line=line,
                message=message or _clip(located_message, _MESSAGE_LIMIT),
            )
        )
    if failures:
        return failures, counts, True

    for match in _JS_FAIL_RE.finditer(text):
        target = match.group(1).strip()
        if not target or target.lower().startswith(("to run", "tests")):
            continue
        failures.append(FailedTest(name=target, file=target.split(" ")[0]))
    js = _JS_COUNT_RE.search(text)
    if js is not None:
        counts.setdefault("failed", int(js.group(1) or 0))
        counts.setdefault("passed", int(js.group(2)))
    return failures, counts, bool(failures)


def _locations(text: str) -> Dict[str, Tuple[Optional[int], str]]:
    """First ``file.py:line: message`` seen per file - where a traceback points."""
    found: Dict[str, Tuple[Optional[int], str]] = {}
    for match in _LOCATION_RE.finditer(text):
        path = match.group(1)
        if path in found:
            continue
        try:
            line = int(match.group(2))
        except ValueError:
            continue
        found[path] = (line, match.group(3).strip())
        found[path.replace("\\", "/")] = (line, match.group(3).strip())
    return found


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _tail(text: str, lines: int) -> str:
    return "\n".join((text or "").rstrip().splitlines()[-lines:])


def result_from_report(
    text: str, ok: bool = False, command: str = "(the test stage's own report)"
) -> VerifyResult:
    """Read an agent's own report into the same shape the engine produces.

    The fallback path - a slice that declared no ``test_commands:`` - has no
    command and no exit code. What it has is the sentences the session wrote,
    and those sentences usually quote pytest's or jest's summary verbatim: the
    same letters the engine parses. Running them through the same parser is what
    makes a fallback failure.md carry the same reasons and coordinates as an
    engine one. When nothing parses the report is still the output, so
    ``render_failure_md`` quotes its tail and the reason is never lost.

    @param text     the session's final report
    @param ok       did the run itself succeed - False once the verdict is FAIL
    @param command  what to call it in the report's 'Command' section
    @flow  parse the report -> one CommandResult standing in for the session
    주요 내부 변수: failures(파싱된 실패), counts(passed/failed/skipped)
    """
    failures, counts, parsed = parse_output(text)
    return VerifyResult(
        ok=ok,
        commands=[
            CommandResult(command=command, exit_code=0 if ok else 1, output=text or "")
        ],
        failures=list(failures),
        passed=counts.get("passed"),
        failed=_sum_present(counts, "failed", "error"),
        skipped=counts.get("skipped"),
        parsed=parsed,
    )


def traceback_summary(text: str, limit: int = TRACEBACK_LINES) -> str:
    """The last traceback in the output, clipped. Empty when there is none."""
    body = text or ""
    marker = body.rfind("Traceback (most recent call last)")
    if marker < 0:
        return ""
    return "\n".join(body[marker:].splitlines()[:limit])


# ------------------------------------------------------------------- rendering


def count_text(value: Optional[int]) -> str:
    """A count as it should be shown: the number, or ``n/a`` when nothing read one.

    Measured 2026-08-19: a suite of 470 passing tests was reported as
    ``0 passed`` because a missing summary line was rendered as a zero. A
    dashboard that invents a number is worse than one that admits it has none.

    @param value  the count, or None when the runner's output never said
    """
    return "n/a" if value is None else str(value)


def count_note(passed: Optional[int]) -> str:
    """The pass count as a phrase for the end of a line: '470 passed' or 'test count n/a'.

    @param passed  how many tests passed, or None when the output never said
    """
    return "test count n/a" if passed is None else "{0} passed".format(passed)


def summarize_for_agent(result: VerifyResult) -> str:
    """The diet: failures with a location, successes as a number, log as a path.

    Measured 2026-08-16/17: implement re-ran the whole suite after every edit and
    the raw output - 233 test names - went into the session's context each time.
    This is what the ``aidev verify`` wrapper prints instead. A count nobody
    could read prints as ``n/a``; failed and skipped stay silent when they are
    ``None``, exactly as they did when they were zero. Spec violations and
    temporary files are listed after the tests, because a green suite that broke
    a convention still has to say which convention and where.

    @param result  what ``run_commands`` produced
    @flow  one line per command -> counts -> failures (or the output's tail)
           -> spec violations -> temporary files -> log paths
    """
    lines: List[str] = []
    for command in result.commands:
        lines.append(
            "{0}: {1}  ({2:.1f}s)".format(
                "PASS" if command.ok else "FAIL",
                command.command,
                command.duration_s,
            )
        )
    counted = "passed {0}".format(count_text(result.passed))
    if result.failed:
        counted += "   failed {0}".format(result.failed)
    if result.skipped:
        counted += "   skipped {0}".format(result.skipped)
    lines.append(counted)

    if result.failures:
        lines.append("")
        lines.append("Failures ({0}):".format(len(result.failures)))
        for failure in result.failures[:MAX_FAILURES_LISTED]:
            lines.append("  {0} - {1}".format(failure.where(), failure.message or failure.name))
        if len(result.failures) > MAX_FAILURES_LISTED:
            lines.append("  ... {0} more".format(len(result.failures) - MAX_FAILURES_LISTED))
    elif not result.ok:
        failing = result.first_failing()
        lines.append("")
        lines.append("The output could not be summarised; its last lines:")
        lines.append(_indent(_tail(failing.output if failing else "", 20)))

    for violation in result.spec_violations[:MAX_FAILURES_LISTED]:
        lines.append("  spec: {0}".format(violation))
    for path in result.temp_files[:MAX_FAILURES_LISTED]:
        lines.append("  temp file: {0}".format(path))

    for command in result.commands:
        if command.log_path:
            lines.append("full log: {0}".format(command.log_path))
    return "\n".join(lines)


def worst_exit_code(result: VerifyResult) -> int:
    """The wrapper's own exit code: 0 only when everything passed."""
    if result.clean:
        return 0
    for command in result.commands:
        if command.exit_code:
            return int(command.exit_code)
        if command.exit_code is None:
            return 1
    return 1


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in (text or "").splitlines())


def render_failure_md(
    result: VerifyResult,
    slice_id: str = "",
    attempt: int = 1,
    last_commit: str = "",
    last_subject: str = "",
) -> str:
    """The FAILURE report: what ran, what failed and where, and where the log is.

    The whole point of writing this to a file is that the session that produced
    the failure is gone by the time anyone reads it. Everything a retry needs has
    to be in here, and nothing that only mattered inside that session. The
    counts are printed as the runner gave them - ``n/a`` where it gave none.

    @param result        the failed verification attempt
    @param slice_id      which slice this belongs to
    @param attempt       which verification round produced it
    @param last_commit   the sha the failure sits on top of
    @param last_subject  that commit's subject line
    @flow  header -> commands -> failures (or output tail) -> spec violations
           -> temporary files -> counts -> traceback
    주요 내부 변수: lines(누적 출력), failing(첫 실패 명령)
    """
    lines = [
        "# FAILURE - {0} · verify attempt {1}".format(slice_id or "(slice)", attempt),
        datetime.now().astimezone().isoformat(timespec="seconds"),
        "",
        "## Command",
    ]
    if result.commands:
        for command in result.commands:
            lines.append(
                "    {0:<50} exit {1}   {2:.1f}s".format(
                    command.command,
                    "?" if command.exit_code is None else command.exit_code,
                    command.duration_s,
                )
            )
    else:
        lines.append("    (no command ran - the failure is the spec check below)")

    if result.failures:
        lines += ["", "## Failed tests ({0})".format(len(result.failures))]
        for failure in result.failures[:MAX_FAILURES_LISTED]:
            lines.append(
                "- {0} - {1} - {2}".format(
                    failure.where(), failure.name, failure.message or "(no message)"
                )
            )
        if len(result.failures) > MAX_FAILURES_LISTED:
            lines.append(
                "- ... {0} more (the full list is in the log below)".format(
                    len(result.failures) - MAX_FAILURES_LISTED
                )
            )
    elif not result.ok:
        failing = result.first_failing()
        lines += [
            "",
            "## Output (tail {0} lines)".format(TAIL_LINES),
            "The runner's output could not be summarised, so it is quoted as it came.",
            "",
            _indent(_tail(failing.output if failing else "", TAIL_LINES)),
        ]

    if result.spec_violations:
        lines += ["", "## Spec violations ({0})".format(len(result.spec_violations))]
        for violation in result.spec_violations[:MAX_FAILURES_LISTED]:
            lines.append("- {0}".format(violation))
        lines.append("")
        lines.append(
            "A function that changed must carry a spec comment, and an existing spec "
            "must change with it (one line is enough)."
        )

    if result.temp_files:
        lines += ["", "## Temporary files ({0})".format(len(result.temp_files))]
        for path in result.temp_files[:MAX_FAILURES_LISTED]:
            lines.append("- {0}".format(path))
        if len(result.temp_files) > MAX_FAILURES_LISTED:
            lines.append("- ... {0} more".format(len(result.temp_files) - MAX_FAILURES_LISTED))
        lines.append("")
        lines.append(
            "이 slice가 새로 추가한 파일이고, 이름이 임시 작업 잔재로 읽힌다. 브랜치에 "
            "남길 파일이 아니면 지우고, 남길 파일이면 임시로 읽히지 않는 이름으로 바꿔라."
        )

    lines += [
        "",
        "## Counts",
        "passed {0}   failed {1}   skipped {2}".format(
            count_text(result.passed), count_text(result.failed), count_text(result.skipped)
        ),
    ]

    failing = result.first_failing()
    trace = traceback_summary(failing.output if failing else "")
    if trace:
        lines += ["", "## Traceback (summary)", "", _indent(trace)]

    logs = [command.log_path for command in result.commands if command.log_path]
    lines.append("")
    lines.append("full log: {0}".format(logs[-1] if logs else "(not written)"))
    if last_commit:
        lines.append(
            "last commit: {0} {1}".format(str(last_commit)[:7], last_subject or "").rstrip()
        )
    return "\n".join(lines) + "\n"
