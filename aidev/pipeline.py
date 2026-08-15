"""The slice loop: one requirement driven through plan -> implement -> test.

    aidev pipeline --repo ~/jokertest --requirement tasks/doctor.md

Every stage is exactly one ``runner.execute()`` call, so everything v0.1 already
measures (events.jsonl, telemetry.json, live.json, the SQLite row) keeps working
per stage, unchanged.

Between any two stages there can be a *gate*. A gate is not a property of a
stage: the loop asks the same question after every stage - "is this gate on?" -
and the requirement's front matter only decides which ones are. When a gate is
on, the loop publishes ``waiting_approval:<stage>`` and polls
``approvals/<stage>.md`` until a human writes ``approved`` or
``rejected: <reason>``.

State lives with the project it describes, not with the tool:

    <repo>/.aidev/slices/<slice-id>/
        requirement.md   the original, copied in
        plan.md          what the plan stage produced (a human may edit it)
        state.json       authoritative progress, single writer: this process
        approvals/<stage>.md
        runs.json        stage -> run id, pointing into the tool's data/runs/

``state.json`` inherits the rules live.json already proved: one writer, written
through ``write_json_atomic``, read through ``read_json_tolerant``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__, events as ev, reporter, runner
from .storage import (
    Database,
    RunStore,
    default_data_dir,
    make_run_id,
    read_json_tolerant,
    slugify,
    write_json_atomic,
)
from .telemetry import Telemetry

# The stages of a v0.2 slice. Every name is an existing telemetry phase, so
# `aidev stats` keeps aggregating pipeline runs together with hand-driven ones.
STAGES: Tuple[str, ...] = ("plan", "implement", "test")
DEFAULT_GATES: Tuple[str, ...] = ("plan",)

# How each stage is constrained. plan may not touch the repository at all, so it
# runs under the readonly profile with no permission mode to unlock it.
STAGE_POLICY: Dict[str, Dict[str, Optional[str]]] = {
    "plan": {"safety_profile": "readonly", "permission_mode": None},
    "implement": {"safety_profile": "default", "permission_mode": "acceptEdits"},
    "test": {"safety_profile": "default", "permission_mode": "acceptEdits"},
}

# acceptEdits auto-approves edits only: Bash still asks. Without rules granting
# the verification commands up front, the test stage cannot run the suite even
# once - measured, not guessed. Unrestricted Bash (--dangerously-skip-permissions)
# stays forbidden: only what is listed here is opened.
TEST_COMMAND_TOOLS: Tuple[str, ...] = (
    "Bash(npm test)",
    "Bash(npm test:*)",
    "Bash(npm run test:*)",
    "Bash(node --test)",
    "Bash(node --test:*)",
    "Bash(pytest)",
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
)
# Both the exact and the prefix form of each command: whether a bare ``npm test``
# matches a ``:*`` rule depends on the CLI version, and holding both passes either
# way. plan is absent on purpose - it is readonly, so Bash is blocked outright.
STAGES_WITH_TEST_COMMANDS: Tuple[str, ...] = ("implement", "test")

AIDEV_DIRNAME = ".aidev"
STATE_SCHEMA = 1
STATE_FILENAME = "state.json"

# Slice status values, all published through state.json.
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
STATUS_QUOTA_WAIT = "quota_wait"

# Approval verdicts.
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

# Exit codes, distinct so a script wrapping the pipeline can tell the terminal
# outcomes apart without parsing text.
EXIT_DONE = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REJECTED = 3

DEFAULT_MAX_TURNS = 80
DEFAULT_POLL_INTERVAL = 3.0
DEFAULT_QUOTA_WAIT_S = 15 * 60.0
DEFAULT_QUOTA_MAX_RETRIES = 20

# A resumed session carries its own conclusion with it ("I was blocked"), so
# after this many consecutive non-quota failures of one stage the retry starts a
# fresh session instead. 1: the very first retry is the one that follows a fix.
DEFAULT_SESSION_RESET_AFTER = 1

# A quota wait is chunked so the process stays interruptible; the chunk count is
# fixed up front, never derived from the wall clock, so tests can stub _sleep.
_WAIT_CHUNK_S = 30.0
_STATE_REPLACE_RETRIES = 20
_STATE_REPLACE_RETRY_S = 0.05

_sleep = time.sleep  # indirection so tests can drive the waits without real time


class PipelineError(RuntimeError):
    """A precondition the pipeline refuses to run against, with its exit code."""

    def __init__(self, message: str, code: int = EXIT_USAGE) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------- requirement

_FRONT_MATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)


def parse_front_matter(text: str) -> Tuple[Dict[str, str], str]:
    """Split a leading ``---`` block off the requirement; the body is untouched."""
    text = text.lstrip("﻿")  # editors leave a BOM behind; tasks/ already has one
    match = _FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text
    fields: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = _strip_inline_comment(value)
    return fields, text[match.end() :]


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ``# note`` - requirement front matter is written by hand."""
    return re.split(r"\s+#", value.strip(), maxsplit=1)[0].strip()


def resolve_gates(fields: Dict[str, str], stages: Sequence[str] = STAGES) -> Tuple[str, ...]:
    """Which gates the front matter turns on. Unspecified means the plan gate.

    An unknown name is an error rather than an ignored line: a typo that
    silently switched a gate off would run the whole slice unattended.
    """
    raw = fields.get("approval")
    if raw is None:
        return DEFAULT_GATES
    names = [name for name in re.split(r"[,\s]+", raw.strip().strip("[]")) if name]
    lowered = [name.lower() for name in names]
    if not names:
        # An empty value must not silently disarm every gate: say 'none' and mean it.
        raise PipelineError("approval: needs stage names or the word 'none'")
    if lowered == ["none"]:
        return ()
    gates: List[str] = []
    for name in lowered:
        if name == "none":
            raise PipelineError("approval: 'none' cannot be combined with stage names")
        if name not in stages:
            raise PipelineError(
                "unknown approval gate '{0}' (stages: {1})".format(name, ", ".join(stages))
            )
        if name not in gates:
            gates.append(name)
    return tuple(gate for gate in stages if gate in gates)


# ------------------------------------------------------------------ approvals

APPROVAL_TEMPLATE = """\
# Approval gate: {stage}   (slice {slice_id})
#
# Write the decision on its own line below. Lines starting with '#' are ignored.
#
#   approved
#   rejected: <reason>
#
# You may edit plan.md before approving - the next stage reads plan.md as it is
# at the moment of approval.
"""


@dataclass
class Decision:
    verdict: str
    reason: str = ""


def read_decision(path: Path) -> Decision:
    """The first line that is neither blank nor a comment decides."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Decision(PENDING)
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        head, _, rest = line.partition(":")
        word = head.strip().lower().rstrip(".!")
        if word in ("approved", "approve"):
            return Decision(APPROVED)
        if word in ("rejected", "reject"):
            return Decision(REJECTED, rest.strip())
        return Decision(PENDING)  # anything else: the human is still writing
    return Decision(PENDING)


# ----------------------------------------------------------------- slice dirs


def slices_root(repo: Path) -> Path:
    return Path(repo) / AIDEV_DIRNAME / "slices"


def make_slice_id(name: str, root: Path, now: Optional[datetime] = None) -> str:
    """``20260815-doctor``, suffixed if that id is already taken today."""
    stamp = (now or datetime.now()).strftime("%Y%m%d")
    # slugify strips dashes before truncating, so a long name can end on one.
    base = "{0}-{1}".format(stamp, slugify(name).rstrip("-") or "slice")
    candidate = base
    counter = 1
    while (Path(root) / candidate).exists():
        counter += 1
        candidate = "{0}-{1}".format(base, counter)
    return candidate


@dataclass
class SliceRecord:
    """One ``<repo>/.aidev/slices/<id>/`` directory and its files."""

    root: Path
    slice_id: str

    @property
    def dir(self) -> Path:
        return Path(self.root) / self.slice_id

    @property
    def requirement_path(self) -> Path:
        return self.dir / "requirement.md"

    @property
    def plan_path(self) -> Path:
        return self.dir / "plan.md"

    @property
    def state_path(self) -> Path:
        return self.dir / STATE_FILENAME

    @property
    def runs_path(self) -> Path:
        return self.dir / "runs.json"

    @property
    def approvals_dir(self) -> Path:
        return self.dir / "approvals"

    @property
    def slug(self) -> str:
        """The requirement part of the id, used to label runs."""
        _, _, rest = self.slice_id.partition("-")
        return rest or self.slice_id

    def approval_path(self, stage: str) -> Path:
        return self.approvals_dir / "{0}.md".format(stage)

    def ensure(self) -> "SliceRecord":
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        return self

    def read_state(self) -> Optional[Dict[str, Any]]:
        return read_json_tolerant(self.state_path)

    def write_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = now_iso()
        for attempt in range(_STATE_REPLACE_RETRIES):
            try:
                write_json_atomic(self.state_path, state)
                return
            except PermissionError:
                # Windows readers may briefly open state.json without delete
                # sharing, which makes os.replace fail until that handle closes.
                if attempt + 1 >= _STATE_REPLACE_RETRIES:
                    raise
                _sleep(_STATE_REPLACE_RETRY_S)

    def read_plan(self) -> str:
        try:
            return self.plan_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_plan(self, text: str) -> None:
        write_text_atomic(self.plan_path, text.rstrip() + "\n")

    def append_run(self, entry: Dict[str, Any]) -> None:
        data = read_json_tolerant(self.runs_path) or {}
        runs = data.get("runs")
        if not isinstance(runs, list):
            runs = []
        runs.append(entry)
        write_json_atomic(self.runs_path, {"slice_id": self.slice_id, "runs": runs})

    def runs(self) -> List[Dict[str, Any]]:
        data = read_json_tolerant(self.runs_path) or {}
        runs = data.get("runs")
        return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# storage.slugify truncates a run label to this many characters.
RUN_SLUG_LIMIT = 40


def stage_run_id(runs_root: Path, slug: str, stage: str, attempt: int) -> str:
    """A run id whose stage and attempt survive the label truncation.

    ``make_run_id`` slugifies the whole label and cuts it to 40 characters, so a
    long requirement name would push the stage off the end and hand every stage
    of the slice the same id - and therefore the same run directory. The head is
    trimmed to leave room, and the result is then checked against the directory
    that already exists, because run ids are timestamped only to the second.
    """
    suffix = stage if attempt <= 1 else "{0}-{1}".format(stage, attempt)
    head = slugify(slug)[: max(1, RUN_SLUG_LIMIT - len(suffix) - 1)].strip("-")
    run_id = make_run_id("{0}-{1}".format(head, suffix) if head else suffix)
    candidate = run_id
    counter = 1
    while (Path(runs_root) / candidate).exists():
        counter += 1
        candidate = "{0}-{1}".format(run_id, counter)
    return candidate


def write_text_atomic(path: Path, text: str) -> None:
    """Same discipline as ``write_json_atomic``: no reader ever sees half a file."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))


class SliceLock:
    """One process per slice, so state.json genuinely has a single writer.

    v0.2 does not run slices in parallel, so a second process on the same slice
    is a mistake rather than a case to merge: it is refused, not queued.
    """

    def __init__(self, rec: SliceRecord) -> None:
        self.path = rec.dir / ".lock"
        self.slice_id = rec.slice_id

    def __enter__(self) -> "SliceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PipelineError(
                "slice {0} is already running ({1}).\n"
                "    If that process is gone, delete {2}".format(
                    self.slice_id, self._holder() or "owner unknown", self.path
                )
            )
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write("pid {0}\nsince {1}\n".format(os.getpid(), now_iso()))
        return self

    def _holder(self) -> str:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return ""
        return "; ".join(line.strip() for line in lines if line.strip())

    def __exit__(self, *exc_info: Any) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass


def new_state(
    slice_id: str,
    repo: Path,
    gates: Sequence[str],
    stages: Sequence[str] = STAGES,
) -> Dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "slice_id": slice_id,
        "status": "pending",
        "repo": str(repo),
        "gates": list(gates),
        "stage_order": list(stages),
        "stages": {name: {"status": "pending"} for name in stages},
        "quota": {"waiting": False, "resume_at": None, "retries": 0},
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


def stage_entry(state: Dict[str, Any], stage: str) -> Dict[str, Any]:
    stages = state.setdefault("stages", {})
    entry = stages.get(stage)
    if not isinstance(entry, dict):
        entry = {"status": "pending"}
        stages[stage] = entry
    return entry


def stage_order(state: Dict[str, Any]) -> List[str]:
    """The stage list this slice started with, so a tool upgrade cannot reorder it."""
    order = state.get("stage_order")
    if isinstance(order, list) and order:
        return [str(name) for name in order]
    stages = state.get("stages")
    if isinstance(stages, dict) and stages:
        return [name for name in STAGES if name in stages] or list(stages)
    return list(STAGES)


# ------------------------------------------------------------------------ git


def git_porcelain(repo: Path) -> Optional[str]:
    """``git status --porcelain``, or ``None`` when this is not a usable git repo.

    ``-uall`` matters: without it git collapses an untracked directory into a
    single ``?? .aidev/`` line, which would hide a plan stage writing its own
    approval file inside it.
    """
    exe = shutil.which("git")
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "status", "--porcelain", "-uall"],
            cwd=str(repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


# The only files the pipeline itself rewrites while a stage is running. Nothing
# else under .aidev/ is ours to excuse - a plan run that writes its own approval
# file has to show up as a change.
LIVE_STATE_FILES = ("state.json", "runs.json", "state.json.tmp", "runs.json.tmp", ".lock")


def _porcelain_path(line: str) -> str:
    path = line[3:].strip().strip('"')
    if " -> " in path:  # a rename is judged by where it landed
        path = path.split(" -> ", 1)[1].strip().strip('"')
    return path


def _under_aidev(path: str) -> bool:
    return path == AIDEV_DIRNAME or path.startswith(AIDEV_DIRNAME + "/")


def repo_changes(repo: Path, include_slice_files: bool = False) -> Optional[str]:
    """Working-tree changes, with the pipeline's own writes filtered out.

    Two different questions need two different filters. "Is there uncommitted
    human work here?" ignores all of ``.aidev/``, which is tool state. "Did this
    stage change anything?" keeps ``.aidev/`` except the few files the pipeline
    rewrites mid-stage, so tampering with an approval or the requirement is
    still visible.
    """
    status = git_porcelain(repo)
    if status is None:
        return None
    kept = []
    for line in status.splitlines():
        if not line:
            continue
        path = _porcelain_path(line)
        if _under_aidev(path):
            if not include_slice_files:
                continue
            if path.rsplit("/", 1)[-1] in LIVE_STATE_FILES:
                continue
        kept.append(line)
    return "\n".join(kept)


def dirty_reason(repo: Path, quiet: bool = False) -> Optional[str]:
    """Why this repo is not safe to start work in, or ``None`` if it is.

    Temporary restriction: v0.3 replaces it with worktree isolation, and inside
    an isolated workspace this check goes away.
    """
    status = repo_changes(repo)
    if status is None:
        if not quiet:
            say("note: not a git repository (or git unavailable) - dirty check skipped")
        return None
    if not status.strip():
        return None
    return (
        "repo has uncommitted changes; commit or stash first "
        "(temporary restriction until v0.3 workspace isolation)\n"
        + "\n".join("    " + line for line in status.strip().splitlines()[:20])
    )


def ensure_clean_repo(repo: Path) -> None:
    reason = dirty_reason(repo)
    if reason is not None:
        raise PipelineError(reason)


# ------------------------------------------------------------- quota handling

# Provisional: these markers are guesses until a real limit is hit and the text
# in events.jsonl / stderr.log is measured. The requirement is only that quota
# failures are told apart from ordinary ones and retried on the same session.
QUOTA_MARKERS = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "quota",
    "too many requests",
    "429",
    "resource_exhausted",
    "overloaded",
)

# Claude Code appends the reset moment as a unix timestamp: "...reached|1755302400".
_EPOCH_RE = re.compile(r"\b(1\d{9})\b")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
_CLOCK_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)

# A parsed reset time this far out is not a reset time, it is a false match.
_MAX_RESET_AHEAD_S = 24 * 3600.0


def tail_text(path: Path, limit: int = 65536) -> str:
    try:
        with open(str(path), "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def last_result_event(events_path: Path) -> Optional[Dict[str, Any]]:
    """The final ``result`` line: where the CLI says why the run ended."""
    for line in reversed(tail_text(events_path).splitlines()):
        event = ev.parse_line(line)
        if event is not None and event.get("type") == "result":
            return event
    return None


def failure_text(run_dir: Path) -> str:
    """What the CLI itself said when the run ended - not what the model said mid-run.

    Scanning the whole stream would misread an agent that merely *talked* about
    rate limits, so only the terminal result event and our own stderr count.
    """
    parts: List[str] = []
    event = last_result_event(Path(run_dir) / "events.jsonl")
    if event is not None:
        for key in ("subtype", "result", "error", "message"):
            value = event.get(key)
            if isinstance(value, str):
                parts.append(value)
    parts.append(tail_text(Path(run_dir) / "stderr.log", 8192))
    return "\n".join(parts)


def looks_like_quota(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in QUOTA_MARKERS)


def parse_reset_at(text: str, now: Optional[float] = None) -> Optional[float]:
    """When the limit resets, as a unix timestamp. ``None`` means "unknown".

    Anything that lands in the past or more than a day out is treated as a false
    match, which is also what keeps the bare-epoch pattern honest.
    """
    now = time.time() if now is None else now

    def accept(candidate: float) -> Optional[float]:
        if now < candidate <= now + _MAX_RESET_AHEAD_S:
            return candidate
        return None

    for match in _EPOCH_RE.finditer(text):
        value = accept(float(match.group(1)))
        if value is not None:
            return value

    match = _ISO_RE.search(text)
    if match is not None:
        year, month, day, hour, minute = (int(g) for g in match.groups())
        try:
            stamp = datetime(year, month, day, hour, minute).timestamp()
        except ValueError:
            stamp = None
        if stamp is not None:
            value = accept(stamp)
            if value is not None:
                return value

    match = _CLOCK_RE.search(text)
    if match is not None:
        hour = int(match.group(1)) % 12
        minute = int(match.group(2) or 0)
        if match.group(3).lower() == "pm":
            hour += 12
        base = datetime.fromtimestamp(now).replace(
            hour=min(hour, 23), minute=min(minute, 59), second=0, microsecond=0
        )
        for day_offset in (0, 1):  # "3pm" may well mean tomorrow's 3pm
            candidate = base.timestamp() + day_offset * 86400
            value = accept(candidate)
            if value is not None:
                return value
    return None


def sleep_seconds(total: float) -> None:
    """Sleep in chunks so Ctrl-C lands quickly, in a fixed number of steps."""
    remaining = max(0.0, total)
    while remaining > 0:
        chunk = min(_WAIT_CHUNK_S, remaining)
        _sleep(chunk)
        remaining -= chunk


# ------------------------------------------------------------------- prompts

_PLAN_PROMPT = """\
You are the PLAN stage of an unattended pipeline running on this repository.

REQUIREMENT
-----------
{requirement}

INSTRUCTIONS
- This stage is read-only. Do not create, modify or delete any file, and do not
  run commands. Mutating tools are blocked; do not look for a way around them.
- Read the real code before you write anything. Do not guess file or symbol names.
- Produce an implementation plan: which files change, what changes in each, how
  it will be verified, and what could go wrong.

Your final message is saved verbatim as plan.md, and plan.md plus the
requirement is all the implement stage receives. Reply with the plan itself in
markdown - no preamble, no closing question.
"""

_IMPLEMENT_PROMPT = """\
You are the IMPLEMENT stage of an unattended pipeline running on this repository.
Nobody is watching: never ask a question, make the change.

REQUIREMENT
-----------
{requirement}

APPROVED PLAN
-------------
{plan}

INSTRUCTIONS
- Follow the approved plan. Where the real code contradicts it, follow the code
  and say so in your final message.
- Change only what the requirement needs. Match the conventions of every file
  you touch.
- Do not commit, do not branch, do not touch git state.
{allowed}
Finish with a short summary: files changed and anything the plan got wrong.
"""

_TEST_PROMPT = """\
You are the TEST stage of an unattended pipeline running on this repository.
Nobody is watching: never ask a question.

REQUIREMENT
-----------
{requirement}

PLAN THAT WAS IMPLEMENTED
-------------------------
{plan}

INSTRUCTIONS
- Run this project's own test suite the way the project runs it. Find the
  command from the project's config rather than assuming one.
{allowed}- Run the test command as ONE plain command. Do not chain it with '&&' or
  'cd x && ...': a chained command is a different command and may not be
  approved.
- If a command is refused for permission reasons, say so verbatim and report
  FAIL. Never report a suite as passing that you could not run.
- Do not fix the code and do not rewrite tests to make them pass. Reporting a
  failure honestly is the deliverable of this stage.

Report the exact command, the pass/fail counts, and every failure with its
error. Then make the LAST line of your final message exactly one of:

TEST_RESULT: PASS
TEST_RESULT: FAIL

PASS only if the suite ran and nothing failed. If you could not run the tests at
all, that is FAIL.
"""

_RESUME_NOTE = """\

---
This session was interrupted before it finished and has been resumed. Continue
the {stage} work from where it stopped; do not start over.
"""

_FRESH_SESSION_NOTE = """\

---
A previous attempt at this stage failed and this is a NEW session with no memory
of it. Judge the current state of the repository and the tools you have access to
now, from scratch. Do not assume an earlier obstacle is still in place.
"""

# Rendered into the stages that are granted commands, so the model is told exactly
# what --allowedTools already carries rather than guessing at a command shape. It
# is built from the same tuple that is granted, so the two cannot drift apart.
_ALLOWED_NOTE = """\
- These Bash commands are pre-approved; prefer one of them:
{rules}
"""


_VERDICT_RE = re.compile(r"^[^\S\n]*TEST_RESULT:[^\S\n]*(PASS|FAIL)\b", re.IGNORECASE | re.MULTILINE)

VERDICT_UNKNOWN = "unknown"


def parse_test_verdict(text: str) -> str:
    """PASS / FAIL from the stage's own report, or ``unknown`` if it never said.

    The exit code cannot answer this: ``claude`` exits 0 after faithfully
    reporting a red suite, so without the verdict line a failing test run would
    finish the slice as done.
    """
    matches = _VERDICT_RE.findall(text or "")
    return matches[-1].lower() if matches else VERDICT_UNKNOWN


def build_prompt(
    stage: str, requirement: str, plan: str = "", allowed: Sequence[str] = ()
) -> str:
    if stage == "plan":
        return _PLAN_PROMPT.format(requirement=requirement.strip())
    template = _IMPLEMENT_PROMPT if stage == "implement" else _TEST_PROMPT
    note = ""
    if allowed:
        note = _ALLOWED_NOTE.format(rules="\n".join("    " + rule for rule in allowed))
    return template.format(
        requirement=requirement.strip(),
        plan=(plan.strip() or "(no plan recorded)"),
        allowed=note,
    )


# ------------------------------------------------------------------ execution


@dataclass
class PipelineConfig:
    repo: Path
    data_dir: Path
    max_turns: int = DEFAULT_MAX_TURNS
    model: Optional[str] = None
    permission_mode: Optional[str] = None
    claude_cmd: List[str] = field(default_factory=lambda: ["claude"])
    extra_args: List[str] = field(default_factory=list)
    live: bool = True
    poll_interval: float = DEFAULT_POLL_INTERVAL
    approval_timeout: float = 0.0
    quota_wait_s: float = DEFAULT_QUOTA_WAIT_S
    quota_max_retries: int = DEFAULT_QUOTA_MAX_RETRIES
    allow_tools: List[str] = field(default_factory=list)
    session_reset_after: int = DEFAULT_SESSION_RESET_AFTER


def allowed_tools_for(stage: str, cfg: PipelineConfig) -> Tuple[str, ...]:
    """The permission rules this stage is granted, in the order they were added.

    Empty for a stage that runs no commands, which is what keeps plan readonly.
    The same tuple feeds both ``--allowedTools`` and the prompt, so what the
    stage is told it may run can never drift from what it actually may run.
    """
    if stage not in STAGES_WITH_TEST_COMMANDS:
        return ()
    rules: List[str] = []
    for rule in tuple(TEST_COMMAND_TOOLS) + tuple(cfg.allow_tools):
        rule = str(rule).strip()
        if rule and rule not in rules:
            rules.append(rule)
    return tuple(rules)


@dataclass
class StageRun:
    stage: str
    attempt: int
    run_id: str
    run_dir: Path
    status: str
    exit_code: Optional[int]
    session_id: Optional[str]
    telemetry: Dict[str, Any]
    text: str
    quota: bool = False
    reset_at: Optional[float] = None

    @property
    def ok(self) -> bool:
        return self.status == "completed"


class _TextCapture:
    """Collects what the model said, so a readonly stage can still produce a file.

    plan.md cannot be written by the plan run itself - Write is blocked - so the
    plan is taken from the stream, deduplicated the same way telemetry does it.
    """

    def __init__(self) -> None:
        self.chunks: List[str] = []
        self.final: Optional[str] = None
        self._seen: set = set()

    def feed(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "assistant":
            key = ev.message_key(event)
            if key is not None:
                if key in self._seen:
                    return
                self._seen.add(key)
            message = event.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        self.chunks.append(text)
        elif kind == "result":
            text = event.get("result")
            if isinstance(text, str) and text.strip():
                self.final = text

    def text(self) -> str:
        # The result event carries the final answer; the streamed chunks are the
        # fallback for a run that ended without one.
        return (self.final or "\n\n".join(self.chunks)).strip()


def _run_meta(cfg: runner.RunConfig, telemetry: Telemetry, command: List[str]) -> Dict[str, Any]:
    return {
        "run_id": telemetry.run_id,
        "aidev_version": __version__,
        "status": telemetry.status,
        "started_at": telemetry.started_at_iso,
        "finished_at": telemetry.finished_at_iso,
        "session_id": telemetry.session_id,
        "exit_code": telemetry.exit_code,
        "command": command,
        "config": cfg.to_dict(),
    }


def execute_stage(
    cfg: PipelineConfig,
    rec: SliceRecord,
    stage: str,
    prompt: str,
    attempt: int,
    resume_session: Optional[str] = None,
) -> StageRun:
    """One stage = one ``runner.execute()``, stored exactly like ``aidev run`` stores it."""
    policy = STAGE_POLICY.get(stage, STAGE_POLICY["implement"])
    permission_mode = policy["permission_mode"]
    # An override may relax the editing stages but must never unlock plan.
    if permission_mode is not None and cfg.permission_mode:
        permission_mode = cfg.permission_mode

    run_id = stage_run_id(cfg.data_dir / "runs", rec.slug, stage, attempt)
    task = "{0}-{1}".format(rec.slug, stage)
    run_cfg = runner.RunConfig(
        repo=cfg.repo,
        prompt_path=rec.requirement_path,
        prompt=prompt,
        phase=stage,
        project=cfg.repo.name,
        task=task,
        max_turns=cfg.max_turns,
        model=cfg.model,
        permission_mode=permission_mode,
        # Rule-scoped, never a blanket Bash unlock: acceptEdits alone would leave
        # every verification command waiting for an approval nobody is there to give.
        allowed_tools=",".join(allowed_tools_for(stage, cfg)) or None,
        safety_profile=policy["safety_profile"] or "default",
        resume_session=resume_session,
        claude_cmd=list(cfg.claude_cmd),
        extra_args=list(cfg.extra_args),
    )
    telemetry = Telemetry(
        run_id=run_id,
        project=cfg.repo.name,
        phase=stage,
        repo=str(cfg.repo),
        prompt_path=str(rec.requirement_path),
        task=task,
        safety_profile=run_cfg.safety_profile,
        disallowed_tools=runner.resolve_disallowed_tools(run_cfg) or "",
    )

    store = RunStore(cfg.data_dir / "runs", run_id)
    live = reporter.LiveReporter(enabled=cfg.live)
    capture = _TextCapture()

    with store:
        store.write_prompt(prompt)
        store.write_run_json(_run_meta(run_cfg, telemetry, runner.build_command(run_cfg)))

        def on_event(event: Dict[str, Any], tel: Telemetry) -> None:
            capture.feed(event)
            is_result = event.get("type") == "result"
            snapshot = tel.snapshot()
            live.update(snapshot, force=is_result)
            store.write_live(snapshot, force=is_result)

        first = telemetry.snapshot()
        live.update(first, force=True)
        store.write_live(first, force=True)
        try:
            result = runner.execute(run_cfg, store, telemetry, on_event=on_event)
        except runner.ClaudeNotFound as exc:
            telemetry.finish("failed", None)
            store.write_telemetry(telemetry.to_dict())
            store.write_live(telemetry.snapshot(), force=True)
            raise PipelineError(str(exc), code=127)

        telemetry.finish(result.status, result.exit_code)
        live.finish(telemetry.snapshot())
        data = telemetry.to_dict()
        store.write_telemetry(data)
        store.write_run_json(_run_meta(run_cfg, telemetry, result.command))
        # Last, so a watcher seeing a terminal status finds telemetry.json there.
        store.write_live(telemetry.snapshot(), force=True)

    with Database(cfg.data_dir / "aidev.db") as db:
        db.save_run(data)

    quota = False
    reset_at = None
    if result.status == "failed":
        text = failure_text(store.dir)
        quota = looks_like_quota(text)
        if quota:
            reset_at = parse_reset_at(text)

    return StageRun(
        stage=stage,
        attempt=attempt,
        run_id=run_id,
        run_dir=store.dir,
        status=result.status,
        exit_code=result.exit_code,
        session_id=telemetry.session_id,
        telemetry=data,
        text=capture.text(),
        quota=quota,
        reset_at=reset_at,
    )


def run_stage(
    cfg: PipelineConfig,
    rec: SliceRecord,
    state: Dict[str, Any],
    stage: str,
    requirement: str,
) -> StageRun:
    """Run one stage, waiting out usage limits on the same session as it goes."""
    entry = stage_entry(state, stage)
    attempt = int(entry.get("attempts") or 0)
    # Session policy: recovering the same stage resumes its session; a new stage
    # always starts a new one. Except after this stage has *concluded* it failed:
    # a quota wait only interrupted the session, but an ordinary failure left it
    # holding "I was blocked", and resuming that re-decides on a stale memory.
    failures = int(entry.get("failures") or 0)
    session = entry.get("session_id")
    fresh = False
    if session and failures and failures >= cfg.session_reset_after:
        say(
            "stage '{0}' failed {1}x - starting a new session instead of resuming {2}".format(
                stage, failures, session
            )
        )
        entry.pop("session_id", None)
        session = None
        fresh = True
    retries = int((state.get("quota") or {}).get("retries") or 0)
    allowed = allowed_tools_for(stage, cfg)

    while True:
        attempt += 1
        entry["status"] = "running"
        entry["attempts"] = attempt
        set_status(rec, state, "running:{0}".format(stage))

        prompt = build_prompt(stage, requirement, rec.read_plan(), allowed=allowed)
        if session:
            prompt += _RESUME_NOTE.format(stage=stage)
        elif fresh:
            prompt += _FRESH_SESSION_NOTE
        banner(stage, rec.slice_id, attempt, resumed=bool(session), fresh=fresh)

        run = execute_stage(cfg, rec, stage, prompt, attempt, resume_session=session)
        record_run(rec, run)
        if run.session_id:
            entry["session_id"] = run.session_id
            session = run.session_id

        if not run.quota:
            if run.ok:
                entry["failures"] = 0
                if isinstance(state.get("quota"), dict):
                    state["quota"].update({"waiting": False, "resume_at": None})
            return run

        if retries >= cfg.quota_max_retries:
            say("usage limit again after {0} retries - giving up".format(retries))
            return run

        retries += 1
        wait = cfg.quota_wait_s
        source = "fallback interval"
        if run.reset_at is not None:
            wait = max(0.0, run.reset_at - time.time()) + 30.0  # a little past the reset
            source = "reset time from the error"
        resume_at = datetime.fromtimestamp(time.time() + wait).astimezone()
        state["quota"] = {
            "waiting": True,
            "resume_at": resume_at.isoformat(timespec="seconds"),
            "retries": retries,
            "source": source,
        }
        set_status(rec, state, STATUS_QUOTA_WAIT)
        say(
            "usage limit hit - retry {0}/{1} in {2} ({3}), resuming session {4}".format(
                retries,
                cfg.quota_max_retries,
                reporter.format_duration(wait),
                source,
                session or "(unknown)",
            )
        )
        sleep_seconds(wait)
        # The wait is over; only the retry count is still worth reporting.
        state["quota"].update({"waiting": False, "resume_at": None})


def record_run(rec: SliceRecord, run: StageRun) -> None:
    exact = run.telemetry.get("exact") or {}
    rec.append_run(
        {
            "stage": run.stage,
            "attempt": run.attempt,
            "run_id": run.run_id,
            "run_dir": str(run.run_dir),
            "status": run.status,
            "exit_code": run.exit_code,
            "session_id": run.session_id,
            "turns": exact.get("num_turns"),
            "cost_usd": exact.get("total_cost_usd"),
            "tokens": exact.get("tokens"),
            "quota": run.quota,
            "finished_at": run.telemetry.get("finished_at"),
            "summary": run.text[:2000],
        }
    )


# ---------------------------------------------------------------- the loop


def note_stage_failure(state: Dict[str, Any], stage: str) -> None:
    """Count a conclusion this stage reached, so the next retry can drop its session."""
    entry = stage_entry(state, stage)
    entry["failures"] = int(entry.get("failures") or 0) + 1


def set_status(rec: SliceRecord, state: Dict[str, Any], status: str) -> None:
    state["status"] = status
    rec.write_state(state)


def fail_slice(rec: SliceRecord, state: Dict[str, Any], reason: str) -> int:
    state["reason"] = reason
    set_status(rec, state, STATUS_FAILED)
    say("FAILED - {0}".format(reason))
    return EXIT_FAILED


def await_approval(
    cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any], stage: str
) -> Decision:
    """Block until ``approvals/<stage>.md`` says approved or rejected.

    The file is the durable record, so a process killed while waiting resumes
    into exactly this function and reads the same answer.
    """
    path = rec.approval_path(stage)
    decision = read_decision(path)
    if decision.verdict == PENDING:
        rec.approvals_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            write_text_atomic(path, APPROVAL_TEMPLATE.format(stage=stage, slice_id=rec.slice_id))
        set_status(rec, state, "waiting_approval:{0}".format(stage))
        say("waiting for approval of '{0}'".format(stage))
        say("  edit {0}".format(path))
        say("  write 'approved' or 'rejected: <reason>' on its own line")
        deadline = time.time() + cfg.approval_timeout if cfg.approval_timeout else None
        while decision.verdict == PENDING:
            if deadline is not None and time.time() >= deadline:
                return decision
            _sleep(cfg.poll_interval)
            decision = read_decision(path)

    entry = stage_entry(state, stage)
    entry["approval"] = decision.verdict
    if decision.reason:
        entry["approval_reason"] = decision.reason
    rec.write_state(state)
    say("gate '{0}': {1}".format(stage, decision.verdict))
    return decision


def finish_stage(
    cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any], run: StageRun, before: Optional[str]
) -> Optional[str]:
    """Stage-specific bookkeeping. Returns a reason to fail the slice, or None."""
    # Recorded before any verdict, so a failing stage still reports what it did.
    entry = stage_entry(state, run.stage)
    violations = ((run.telemetry.get("observed") or {}).get("safety_violations")) or []
    if violations:
        entry["safety_violations"] = len(violations)

    if run.stage == "plan":
        if not run.text.strip():
            return "plan stage produced no text to save as plan.md"
        # Verified before plan.md is written, so the comparison sees only what
        # the run itself did - including any approval file it tried to forge.
        after = repo_changes(cfg.repo, include_slice_files=True)
        if before is not None and after is not None and before != after:
            return "plan stage changed the repository despite the readonly profile:\n" + _diff_lines(
                before, after
            )
        rec.write_plan(run.text)
        say("plan saved: {0}".format(rec.plan_path))

    if run.stage == "test":
        verdict = parse_test_verdict(run.text)
        entry["verdict"] = verdict
        state["test_verdict"] = verdict
        if verdict == "fail":
            return "test stage reported failing tests (TEST_RESULT: FAIL)"
        if verdict == VERDICT_UNKNOWN:
            # Not fatal - we cannot claim tests failed when nothing was claimed -
            # but it must never read as a pass.
            say("warning: the test stage gave no TEST_RESULT line; result is unknown")
    return None


def _diff_lines(before: str, after: str) -> str:
    """The porcelain lines that appeared or changed while a stage ran."""
    seen = set(before.splitlines())
    new = [line for line in after.splitlines() if line not in seen]
    return "\n".join("    " + line for line in (new or after.splitlines())[:20])


def resume_quota_wait(cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any]) -> None:
    """A slice resumed mid-wait finishes the wait; it does not retry straight away."""
    quota = state.get("quota")
    if not isinstance(quota, dict) or not quota.get("waiting"):
        return
    remaining = 0.0
    resume_at = quota.get("resume_at")
    if isinstance(resume_at, str):
        try:
            remaining = datetime.fromisoformat(resume_at).timestamp() - time.time()
        except ValueError:
            remaining = 0.0
    if remaining > 0:
        say("still inside a usage-limit wait, {0} left".format(reporter.format_duration(remaining)))
        set_status(rec, state, STATUS_QUOTA_WAIT)
        sleep_seconds(remaining)
    quota.update({"waiting": False, "resume_at": None})
    rec.write_state(state)


def run_pipeline(
    cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any], requirement: str
) -> int:
    """The single loop: run the stage if it is not done, then ask about its gate."""
    # Why it stopped last time is stale the moment it moves again.
    state.pop("reason", None)
    resume_quota_wait(cfg, rec, state)
    gates = {str(name) for name in (state.get("gates") or ())}
    for stage in stage_order(state):
        entry = stage_entry(state, stage)
        if entry.get("status") != "done":
            # Until this slice has edited anything, the tree must still be clean:
            # a gate can be open for hours, and a resumed slice may be picking up
            # after a readonly violation. Once a stage has edited, its own work is
            # what makes the tree dirty, so the question stops making sense.
            if not state.get("mutated"):
                reason = dirty_reason(cfg.repo, quiet=True)
                if reason is not None:
                    return fail_slice(rec, state, "before stage '{0}': {1}".format(stage, reason))

            readonly = STAGE_POLICY.get(stage, {}).get("safety_profile") == "readonly"
            # Only a readonly stage is verified against the working tree afterwards.
            before = repo_changes(cfg.repo, include_slice_files=True) if readonly else None
            if not readonly:
                state["mutated"] = True
            try:
                run = run_stage(cfg, rec, state, stage, requirement)
            except PipelineError as exc:
                # The run never produced a result, so leave the slice failed
                # rather than frozen at running:<stage>.
                entry["status"] = "failed"
                fail_slice(rec, state, str(exc))
                raise
            if not run.ok:
                entry["status"] = "failed"
                entry["run_id"] = run.run_id
                # Quota is excluded: a stage abandoned on a usage limit reached no
                # conclusion of its own, so its session is still worth resuming.
                if not run.quota:
                    note_stage_failure(state, stage)
                return fail_slice(
                    rec,
                    state,
                    "stage '{0}' {1} (run {2}, exit {3})".format(
                        stage, run.status, run.run_id, run.exit_code
                    ),
                )
            entry.update({"status": "done", "run_id": run.run_id})
            reason = finish_stage(cfg, rec, state, run, before)
            if reason is not None:
                entry["status"] = "failed"
                # The run itself succeeded but the stage did not - a TEST_RESULT:
                # FAIL verdict lands here, and that is a session conclusion too.
                note_stage_failure(state, stage)
                return fail_slice(rec, state, reason)
            rec.write_state(state)

        # Stage-independent: the same question after every stage, and the front
        # matter only decided which gates are on.
        if stage in gates:
            decision = await_approval(cfg, rec, state, stage)
            if decision.verdict == REJECTED:
                state["reason"] = "rejected at '{0}': {1}".format(
                    stage, decision.reason or "(no reason given)"
                )
                set_status(rec, state, STATUS_REJECTED)
                say("REJECTED - {0}".format(state["reason"]))
                return EXIT_REJECTED
            if decision.verdict != APPROVED:
                return fail_slice(rec, state, "timed out waiting for approval of '{0}'".format(stage))

    set_status(rec, state, STATUS_DONE)
    return EXIT_DONE


# --------------------------------------------------------------------- output


def say(message: str) -> None:
    print("[pipeline] {0}".format(message), flush=True)


def banner(
    stage: str, slice_id: str, attempt: int, resumed: bool = False, fresh: bool = False
) -> None:
    label = "{0}  {1}".format(stage.upper(), slice_id)
    if attempt > 1:
        note = ", resumed session" if resumed else (", new session" if fresh else "")
        label += "  (attempt {0}{1})".format(attempt, note)
    print("\n{0}\n{1}".format(label, reporter.rule()), flush=True)


def render_summary(state: Dict[str, Any], runs: Sequence[Dict[str, Any]]) -> str:
    """Per-stage tokens / cost / changed files, using the existing formatters.

    Every attempt counts: a stage retried through a usage limit spent tokens on
    each try, and a summary showing only the last one understates the bill.
    """
    row = "{0:<11}{1:<9}{2:>4}{3:>7}{4:>11}{5:>11}{6:>10}".format
    lines = ["", "PIPELINE  {0}".format(state.get("slice_id", "?")), reporter.rule()]
    lines.append(row("Stage", "Status", "Try", "Turns", "Peak ctx", "Output", "Cost"))

    totals = {"turns": 0, "output": 0, "cost": 0.0, "attempts": 0}
    for stage in stage_order(state):
        attempts = [entry for entry in runs if str(entry.get("stage")) == stage]
        status = str(stage_entry(state, stage).get("status", "pending"))
        if not attempts:
            lines.append(row(stage, status, "-", "-", "-", "-", "-"))
            continue
        turns = sum(int(e.get("turns") or 0) for e in attempts)
        output = sum(int((e.get("tokens") or {}).get("output") or 0) for e in attempts)
        peak = max(int((e.get("tokens") or {}).get("peak_context") or 0) for e in attempts)
        cost = sum(float(e.get("cost_usd") or 0.0) for e in attempts)
        totals["turns"] += turns
        totals["output"] += output
        totals["cost"] += cost
        totals["attempts"] += len(attempts)
        lines.append(
            row(
                stage,
                status,
                len(attempts),
                turns,
                reporter.format_tokens(peak),
                reporter.format_tokens(output),
                reporter.format_cost(cost),
            )
        )
    lines.append(reporter.rule())
    lines.append(
        row(
            "total",
            "",
            totals["attempts"],
            totals["turns"],
            "",
            reporter.format_tokens(totals["output"]),
            reporter.format_cost(totals["cost"]),
        )
    )

    changed = changed_files(runs)
    if changed:
        lines.append("")
        lines.append("Changed files ({0})".format(len(changed)))
        for path in changed[:15]:
            lines.append("  {0}".format(path))
        if len(changed) > 15:
            lines.append("  ... {0} more".format(len(changed) - 15))

    lines.append("")
    lines.append("Status    {0}".format(state.get("status", "?")))
    verdict = state.get("test_verdict")
    if verdict:
        note = {"pass": "", "fail": "  <- tests failed", "unknown": "  <- no TEST_RESULT line"}
        lines.append("Tests     {0}{1}".format(str(verdict).upper(), note.get(verdict, "")))
    if state.get("reason"):
        lines.append("Reason    {0}".format(state["reason"]))
    return "\n".join(lines)


def changed_files(runs: Sequence[Dict[str, Any]]) -> List[str]:
    """Files the runs wrote, read back from each run's stored telemetry."""
    paths: List[str] = []
    for entry in runs:
        run_dir = entry.get("run_dir")
        if not isinstance(run_dir, str):
            continue
        data = read_json_tolerant(Path(run_dir) / "telemetry.json")
        if not data:
            continue
        for access in (data.get("observed") or {}).get("file_accesses") or []:
            if not isinstance(access, dict) or access.get("operation") not in ("edit", "write"):
                continue
            path = access.get("path")
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
    return paths


# -------------------------------------------------------------------- command


def add_parser(sub: Any) -> Any:
    cmd = sub.add_parser(
        "pipeline", help="drive one requirement through plan -> approval -> implement -> test"
    )
    cmd.add_argument(
        "--repo", type=Path, default=None, help="target repository (default: current directory)"
    )
    cmd.add_argument("--requirement", type=Path, default=None, help="requirement markdown file")
    cmd.add_argument(
        "--resume-slice", default=None, help="continue a stopped slice (id, prefix, or 'last')"
    )
    cmd.add_argument(
        "--list", action="store_true", dest="list_slices", help="list slices of --repo and exit"
    )
    cmd.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS, help="per stage (default 80)")
    cmd.add_argument("--model", default=None)
    cmd.add_argument(
        "--permission-mode",
        default=None,
        help="for implement/test, e.g. bypassPermissions (plan stays readonly)",
    )
    cmd.add_argument(
        "--allow-tool",
        action="append",
        default=[],
        dest="allow_tools",
        metavar="RULE",
        help="extra permission rule for implement/test, e.g. --allow-tool 'Bash(npx vitest:*)'",
    )
    cmd.add_argument(
        "--session-reset-after",
        type=int,
        default=DEFAULT_SESSION_RESET_AFTER,
        metavar="N",
        help="start a fresh session after N consecutive non-quota failures of the same stage",
    )
    cmd.add_argument("--claude-bin", default="claude")
    cmd.add_argument("--claude-arg", action="append", default=[], dest="claude_args")
    cmd.add_argument("--no-live", action="store_true", help="disable the live panel")
    cmd.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="approval poll seconds"
    )
    cmd.add_argument(
        "--approval-timeout",
        type=float,
        default=0.0,
        help="give up waiting after N seconds (default 0: wait forever)",
    )
    cmd.add_argument(
        "--quota-wait", type=float, default=DEFAULT_QUOTA_WAIT_S, help="fallback retry interval"
    )
    cmd.add_argument("--quota-max-retries", type=int, default=DEFAULT_QUOTA_MAX_RETRIES)
    cmd.add_argument("--dry-run", action="store_true", help="print what would run and exit")
    cmd.set_defaults(func=cmd_pipeline)
    return cmd


def cmd_pipeline(args: Any) -> int:
    try:
        return _dispatch(args)
    except PipelineError as exc:
        print("error: {0}".format(exc), file=sys.stderr)
        return exc.code


def _dispatch(args: Any) -> int:
    repo = (args.repo or Path.cwd()).expanduser().resolve()
    if not repo.is_dir():
        raise PipelineError("--repo not found: {0}".format(repo))
    data_dir = (getattr(args, "data_dir", None) or default_data_dir()).expanduser()

    # Whether the repo was chosen or merely defaulted to, so an empty answer can
    # say which of the two it is.
    repo_given = args.repo is not None
    if args.list_slices:
        return list_slices(repo, repo_given)
    if args.resume_slice:
        return resume_slice(args, repo, data_dir, repo_given)
    if args.requirement:
        return start_slice(args, repo, data_dir)
    raise PipelineError("one of --requirement, --resume-slice or --list is required")


def _config(args: Any, repo: Path, data_dir: Path) -> PipelineConfig:
    return PipelineConfig(
        repo=repo,
        data_dir=data_dir,
        max_turns=args.max_turns,
        model=args.model,
        permission_mode=args.permission_mode,
        claude_cmd=[args.claude_bin],
        extra_args=list(args.claude_args or []),
        live=not args.no_live,
        poll_interval=args.poll_interval,
        approval_timeout=args.approval_timeout,
        quota_wait_s=args.quota_wait,
        quota_max_retries=args.quota_max_retries,
        allow_tools=list(getattr(args, "allow_tools", None) or []),
        session_reset_after=getattr(args, "session_reset_after", DEFAULT_SESSION_RESET_AFTER),
    )


def resolve_requirement(path: Path, repo: Path) -> Path:
    candidates = [path.expanduser()]
    if not path.is_absolute():
        candidates.append((Path.cwd() / path).resolve())
        candidates.append((repo / path).resolve())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PipelineError("--requirement not found: {0}".format(path))


def start_slice(args: Any, repo: Path, data_dir: Path) -> int:
    source = resolve_requirement(args.requirement, repo)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise PipelineError("requirement file is empty: {0}".format(source))
    fields, body = parse_front_matter(text)
    if not body.strip():
        raise PipelineError("requirement has front matter but no body: {0}".format(source))
    gates = resolve_gates(fields)

    root = slices_root(repo)
    unfinished = [
        rec.slice_id
        for rec in _existing_slices(root)
        if str((rec.read_state() or {}).get("status", "")) not in (STATUS_DONE, STATUS_REJECTED)
    ]
    slice_id = make_slice_id(source.stem, root)
    rec = SliceRecord(root, slice_id)

    if args.dry_run:
        print("slice     {0}".format(slice_id))
        print("repo      {0}".format(repo))
        print("stages    {0}".format(" -> ".join(STAGES)))
        print("gates     {0}".format(", ".join(gates) or "(none: fully unattended)"))
        print("slice dir {0}".format(rec.dir))
        print("run dir   {0}".format(data_dir / "runs"))
        return EXIT_DONE

    ensure_clean_repo(repo)
    if unfinished:
        say("note: unfinished slice(s) here: {0}".format(", ".join(unfinished)))
        say("      use --resume-slice <id> to continue one instead of starting over")

    rec.ensure()
    write_text_atomic(rec.requirement_path, text)
    state = new_state(slice_id, repo, gates)
    rec.write_state(state)
    say("slice {0}".format(slice_id))
    say("  dir   {0}".format(rec.dir))
    say("  gates {0}".format(", ".join(gates) or "(none: fully unattended)"))

    return _finish(_config(args, repo, data_dir), rec, state, body)


def resume_slice(args: Any, repo: Path, data_dir: Path, repo_given: bool = True) -> int:
    rec = find_slice(repo, args.resume_slice, repo_given)
    state = rec.read_state()
    if state is None:
        raise PipelineError("no readable state.json in {0}".format(rec.dir))
    status = str(state.get("status", ""))
    if status == STATUS_DONE:
        say("slice {0} is already done".format(rec.slice_id))
        print(render_summary(state, rec.runs()))
        return EXIT_DONE

    _, body = parse_front_matter(rec.requirement_path.read_text(encoding="utf-8"))
    if args.dry_run:
        print("slice   {0}  ({1})".format(rec.slice_id, status))
        print("stages  {0}".format(
            ", ".join(
                "{0}={1}".format(name, stage_entry(state, name).get("status", "?"))
                for name in stage_order(state)
            )
        ))
        return EXIT_DONE

    say("resuming slice {0} (was {1})".format(rec.slice_id, status))
    if status == STATUS_REJECTED:
        # The approval file is the record: a rejection stands until a human edits it.
        say("this slice was rejected: {0}".format(state.get("reason", "")))
    state.setdefault("quota", {"waiting": False, "resume_at": None, "retries": 0})
    return _finish(_config(args, repo, data_dir), rec, state, body)


def _finish(cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any], body: str) -> int:
    with SliceLock(rec):
        code = run_pipeline(cfg, rec, state, body)
        print(render_summary(state, rec.runs()))
        print("slice dir: {0}".format(rec.dir))
        return code


def _existing_slices(root: Path) -> List[SliceRecord]:
    if not Path(root).is_dir():
        return []
    return [
        SliceRecord(root, path.name)
        for path in sorted(Path(root).iterdir(), key=lambda p: p.name)
        if path.is_dir()
    ]


def slice_touched_at(rec: SliceRecord) -> float:
    """When this slice last moved. Ids sort alphabetically, which is not the same."""
    state = rec.read_state() or {}
    for key in ("updated_at", "created_at"):
        value = state.get(key)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).timestamp()
            except ValueError:
                pass
    try:
        return rec.state_path.stat().st_mtime
    except OSError:
        return 0.0


def _repo_hint(repo: Path, repo_given: bool) -> str:
    """An empty answer from the default repo says why it is empty, not just that."""
    if repo_given or (Path(repo) / AIDEV_DIRNAME).is_dir():
        return ""
    return (
        "\n    no {0}/ here - this is probably not the target repository."
        "\n    pass --repo <path>".format(AIDEV_DIRNAME)
    )


def find_slice(repo: Path, slice_id: str, repo_given: bool = True) -> SliceRecord:
    """Exact id, then unique prefix, then unique substring. ``last`` is by time.

    An ambiguous pattern is refused rather than resolved: picking the wrong
    slice would resume the wrong work in someone's repository.
    """
    records = _existing_slices(slices_root(repo))
    if not records:
        raise PipelineError(
            "no slices in {0}{1}".format(slices_root(repo), _repo_hint(repo, repo_given))
        )
    if slice_id in ("last", "latest", "-"):
        return max(records, key=lambda rec: (slice_touched_at(rec), rec.slice_id))

    exact = [rec for rec in records if rec.slice_id == slice_id]
    if exact:
        return exact[0]
    for candidates in (
        [rec for rec in records if rec.slice_id.startswith(slice_id)],
        [rec for rec in records if slice_id in rec.slice_id],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise PipelineError(
                "'{0}' matches {1} slices: {2}\n    use the full id".format(
                    slice_id, len(candidates), ", ".join(rec.slice_id for rec in candidates[:8])
                )
            )
    raise PipelineError("no slice matching '{0}' in {1}".format(slice_id, slices_root(repo)))


def list_slices(repo: Path, repo_given: bool = True) -> int:
    records = _existing_slices(slices_root(repo))
    if not records:
        print("(no slices in {0}){1}".format(slices_root(repo), _repo_hint(repo, repo_given)))
        return EXIT_DONE
    widths = (34, 26, 34)
    print(_list_row(("SLICE", "STATUS", "STAGES"), widths, "UPDATED"))
    for rec in records:
        state = rec.read_state() or {}
        stages = state.get("stages")
        # Unknown stage keys are shown, never a reason to stop reading.
        detail = " ".join(
            "{0}={1}".format(name, (entry or {}).get("status", "?"))
            for name, entry in (stages.items() if isinstance(stages, dict) else [])
        )
        print(
            _list_row(
                (rec.slice_id, str(state.get("status", "(unreadable)")), detail),
                widths,
                str(state.get("updated_at", ""))[:19],
            )
        )
    return EXIT_DONE


def _list_row(cells: Sequence[str], widths: Sequence[int], last: str) -> str:
    """Pad each cell to its column, truncating so a long id cannot eat the next one.

    The ellipsis comes from the reporter, which already falls back to ASCII on a
    console that cannot encode it.
    """
    mark = reporter.glyph("ellipsis")
    out = []
    for cell, width in zip(cells, widths):
        limit = width - 1  # always leave a gap before the next column
        text = cell if len(cell) <= limit else cell[: max(0, limit - len(mark))] + mark
        out.append(text.ljust(width))
    return "".join(out) + last
