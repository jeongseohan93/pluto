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
        return self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "log": self.log_path,
        }


@dataclass
class VerifyResult:
    """Everything one verification attempt produced, in one value."""

    ok: bool = True
    commands: List[CommandResult] = field(default_factory=list)
    failures: List[FailedTest] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    parsed: bool = False
    spec_violations: List[Any] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Did everything pass, including the spec check? What a PASS really means."""
        return self.ok and not self.spec_violations

    def failure_count(self) -> int:
        """How big the failure is, for the diagnosis grade. Spec violations count too."""
        counted = self.failed or len(self.failures)
        return counted + len(self.spec_violations)

    def first_failing(self) -> Optional[CommandResult]:
        for result in self.commands:
            if not result.ok:
                return result
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "commands": [result.to_dict() for result in self.commands],
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "parsed": self.parsed,
            "failures": [failure.to_dict() for failure in self.failures[:MAX_FAILURES_LISTED]],
            "spec_violations": [str(violation) for violation in self.spec_violations],
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
        result.passed += counts.get("passed", 0)
        result.failed += counts.get("failed", 0) + counts.get("error", 0)
        result.skipped += counts.get("skipped", 0)
        if failures:
            result.failures.extend(failures)
        result.parsed = result.parsed or parsed
        if not single.ok:
            result.ok = False
            break  # the rest would only cost time; this one already decided it
    return result


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


def traceback_summary(text: str, limit: int = TRACEBACK_LINES) -> str:
    """The last traceback in the output, clipped. Empty when there is none."""
    body = text or ""
    marker = body.rfind("Traceback (most recent call last)")
    if marker < 0:
        return ""
    return "\n".join(body[marker:].splitlines()[:limit])


# ------------------------------------------------------------------- rendering


def summarize_for_agent(result: VerifyResult) -> str:
    """The diet: failures with a location, successes as a number, log as a path.

    Measured 2026-08-16/17: implement re-ran the whole suite after every edit and
    the raw output - 233 test names - went into the session's context each time.
    This is what the ``aidev verify`` wrapper prints instead.

    @param result  what ``run_commands`` produced
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
    counted = "passed {0}".format(result.passed)
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
    to be in here, and nothing that only mattered inside that session.

    @param result        the failed verification attempt
    @param slice_id      which slice this belongs to
    @param attempt       which verification round produced it
    @param last_commit   the sha the failure sits on top of
    @param last_subject  that commit's subject line
    @flow  header -> commands -> failures (or output tail) -> spec violations -> counts -> traceback
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

    lines += [
        "",
        "## Counts",
        "passed {0}   failed {1}   skipped {2}".format(
            result.passed, result.failed, result.skipped
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
