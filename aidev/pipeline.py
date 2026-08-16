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

v0.3 adds workspace isolation. The state above stays exactly where it is - in
the user's checkout, written by this one process - but the *work* moves into a
worktree the pipeline creates (``aidev.workspace``), on its own branch, with one
commit per stage. So ``cfg.repo`` is the user's repository and ``cfg.cwd`` is
where Claude actually runs, and those are only the same thing when isolation is
off. A read-only projection of the slice is committed to ``.aidev/history/`` on
the branch, which is a *different path* from ``.aidev/slices/`` on purpose:
merging a branch that carried the same path would collide with the untracked
copy already sitting in the user's checkout.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import __version__, events as ev, reporter, runner, workspace
from .storage import (
    Database,
    RunStore,
    default_data_dir,
    make_run_id,
    read_json_tolerant,
    recent_repos,
    remember_repo,
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
# 2: state.json may carry "workspace", "commits" and "setup". A reader must keep
# accepting 1 - a v0.2 slice has none of them and resumes in place.
STATE_SCHEMA = 2
STATE_FILENAME = "state.json"
# Every rollback this slice has ever taken, append-only. The future Ledger reads
# this file and nothing else: state.json keeps only the latest one, as a summary.
ROLLBACKS_FILENAME = "rollbacks.json"

# The branch a slice owns, and the directory its own record is committed into.
# HISTORY_DIR is deliberately not ``.aidev/slices``: that path already exists as
# untracked state in the user's checkout, and a merge bringing the same path in
# as tracked would be refused by git.
SLICE_BRANCH_PREFIX = "slice/"
HISTORY_DIR = "history"
REQUIREMENT_STAGE = "requirement"
COMMIT_MESSAGE = "slice({0}): {1}"

# An amend re-opens these stages of a finished slice. plan is not among them:
# the amendment itself is that cycle's plan, and re-planning a finished slice
# would cost a whole readonly stage plus a gate on every correction.
AMEND_STAGES: Tuple[str, ...] = ("implement", "test")
# The label an amended stage commits under. The 'slice(<id>): ' prefix is
# unchanged, so every existing reader of these commits keeps working.
AMEND_LABEL = "amend{0}/{1}"

# A stage commit that would sweep in this many files is a mistake, not a stage:
# an unignored node_modules/ from a setup command would otherwise ride the merge
# straight into the user's repository.
MAX_COMMIT_FILES = 2000
SETUP_TIMEOUT_S = 1800.0

# Slice status values, all published through state.json.
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_REJECTED = "rejected"
STATUS_QUOTA_WAIT = "quota_wait"
STATUS_MERGED = "merged"
STATUS_DISCARDED = "discarded"
# Rewound to a stage commit: a midpoint, not an ending - --resume-slice goes on
# from there. 'reverted' is an ending: the merge was taken back off the base.
STATUS_ROLLED_BACK = "rolled_back"
STATUS_REVERTED = "reverted"

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
EXIT_CONFLICT = 4

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

# Only these keys accumulate across repeated lines, joined with a newline.
# Everything else stays last-wins, which is what approval: and setup: have
# always been and what their tests pin down.
_MULTI_FIELDS: Tuple[str, ...] = ("test_commands",)


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
        # A dash and an underscore are the same key: nothing today uses a dash,
        # so accepting 'max-turns:' beside 'max_turns:' can only add.
        key = key.strip().lower().replace("-", "_")
        value = _strip_inline_comment(value)
        if key in _MULTI_FIELDS and key in fields:
            fields[key] = "{0}\n{1}".format(fields[key], value)
        else:
            fields[key] = value
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


# A front matter line is written by a human, but it is still a string that ends
# up as an argv. No shell is opened for it, so anything that only means something
# to a shell is refused rather than quietly taken literally.
_SHELL_METACHARS = ("&&", "||", "|", ";", ">", "<", "`", "$(", "\n")


def resolve_setup(fields: Dict[str, str]) -> Optional[str]:
    """The one command this slice may run before implement. Absent means: run nothing.

    ``setup:`` with no value is an error for the same reason ``approval:`` is: a
    line that was meant to do something and silently does nothing is worse than
    a stop.
    """
    raw = fields.get("setup")
    if raw is None:
        return None
    command = raw.strip()
    if not command:
        raise PipelineError("setup: needs a command, or leave the line out entirely")
    for token in _SHELL_METACHARS:
        if token in command:
            raise PipelineError(
                "setup: '{0}' is not allowed - the command runs without a shell, so it "
                "must be one plain command (found {1!r})".format(command, token)
            )
    if not split_command(command):
        raise PipelineError("setup: could not read a command from {0!r}".format(command))
    return command


def resolve_test_commands(fields: Dict[str, str]) -> Tuple[str, ...]:
    """The verification commands this slice declares. Empty means: use the built-ins.

    Measured 2026-08-15: ``setup: npm ci --prefix backend`` granted ``Bash(npm
    ci:*)`` and nothing that could run the project's frontend tests, so the
    agent could not verify anything and left the plan. Deriving the verification
    rules from the setup command was the mistake; they are declared separately.

    Commas separate commands and repeated ``test_commands:`` lines accumulate, so
    a command containing a comma cannot be expressed - use ``--allow-tool``.
    """
    raw = fields.get("test_commands")
    if raw is None:
        return ()
    commands: List[str] = []
    for part in re.split(r"[\n,]", raw):
        command = part.strip()
        if not command:
            continue
        for token in _SHELL_METACHARS:
            if token in command:
                raise PipelineError(
                    "test_commands: '{0}' is not allowed - a permission rule matches one "
                    "plain command, so a chained one could never be approved "
                    "(found {1!r})".format(command, token)
                )
        if not split_command(command):
            raise PipelineError(
                "test_commands: could not read a command from {0!r}".format(command)
            )
        if command not in commands:
            commands.append(command)
    if not commands:
        raise PipelineError("test_commands: needs a command, or leave the line out entirely")
    return tuple(commands)


def resolve_max_turns(
    fields: Dict[str, str], stages: Sequence[str] = STAGES
) -> Tuple[Optional[int], Dict[str, int]]:
    """``(base, per-stage)`` from ``max_turns:``. ``(None, {})`` when absent.

    Measured 2026-08-15/16: three implement stages died at turn 81 with the work
    half done, each costing a resume. One budget for every stage was the wrong
    shape - plan and test finish comfortably inside 80, implement is the one that
    needs room::

        max_turns: 120                  every stage
        max_turns: implement=140        one stage
        max_turns: 100, implement=140   a base plus an override
    """
    raw = fields.get("max_turns")
    if raw is None:
        return None, {}
    if not raw.strip():
        raise PipelineError(
            "max_turns: needs a number, or <stage>=<number>, or leave the line out entirely"
        )
    return parse_turn_tokens([raw], stages, "max_turns:")


def parse_turn_tokens(
    tokens: Sequence[str],
    stages: Sequence[str] = STAGES,
    where: str = "max_turns:",
    allow_base: bool = True,
) -> Tuple[Optional[int], Dict[str, int]]:
    """Read ``120`` / ``implement=140`` tokens the same way for the file and the flag."""
    base: Optional[int] = None
    per_stage: Dict[str, int] = {}
    for raw in tokens:
        for token in re.split(r"[,\s]+", str(raw).strip()):
            if not token:
                continue
            name, sep, value = token.partition("=")
            if not sep:
                if not allow_base:
                    raise PipelineError(
                        "{0} needs <stage>=<number>, got {1!r} (stages: {2})".format(
                            where, token, ", ".join(stages)
                        )
                    )
                base = _turn_count(token, where)
                continue
            name = name.strip().lower()
            if name not in stages:
                raise PipelineError(
                    "unknown stage '{0}' in {1} (stages: {2})".format(
                        name, where, ", ".join(stages)
                    )
                )
            count = _turn_count(value, where)
            if per_stage.get(name, count) != count:
                # Two different answers for one stage: guessing which was meant
                # would silently pick a budget nobody asked for.
                raise PipelineError(
                    "{0} sets '{1}' twice, to {2} and {3}".format(
                        where, name, per_stage[name], count
                    )
                )
            per_stage[name] = count
    return base, per_stage


def _turn_count(value: str, where: str) -> int:
    text = str(value).strip()
    try:
        count = int(text)
    except ValueError:
        count = 0
    if count <= 0:
        raise PipelineError(
            "{0} needs a positive whole number, got {1!r}".format(where, text)
        )
    return count


def split_command(command: str) -> List[str]:
    """Split a declared command into argv. Windows keeps its backslashes."""
    if os.name == "nt":
        # POSIX mode would eat ``C:\path\to`` one backslash at a time.
        tokens = shlex.split(command, posix=False)
        return [token[1:-1] if len(token) > 1 and token[0] == token[-1] == '"' else token
                for token in tokens]
    return shlex.split(command)


def command_tool_rules(command: Optional[str]) -> Tuple[str, ...]:
    """Permission rules for one declared command - exact form and prefix form.

    Both, for the same reason ``TEST_COMMAND_TOOLS`` holds both: whether a bare
    command matches a ``:*`` rule depends on the CLI version.
    """
    if not command:
        return ()
    tokens = split_command(command)
    if not tokens:
        return ()
    rules = ["Bash({0})".format(command.strip())]
    prefix = " ".join(tokens[:2]) if len(tokens) > 1 else tokens[0]
    rules.append("Bash({0}:*)".format(prefix))
    return tuple(dict.fromkeys(rules))


def setup_tool_rules(command: Optional[str]) -> Tuple[str, ...]:
    """The setup command's own rules. Same shape as any other declared command."""
    return command_tool_rules(command)


# --------------------------------------------------------------- plan scale
#
# Measured 2026-08-15/16: three implement stages died at turn 81 with the work
# half done. What those three plans had in common is how much work they named,
# so that is the trigger. This is a text heuristic over a document that lists
# file paths: it only ever adds one advisory line, never blocks and never
# changes a budget by itself.

PLAN_FILE_WARN = 12
PLAN_TEST_WARN = 5
# Twice the default, which is roughly what the three resumes actually cost.
PLAN_SUGGESTED_TURNS = 160

_PLAN_PATH_RE = re.compile(
    r"[\w./\\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|toml|ya?ml|md|css|html)\b"
)
_TEST_SEGMENTS = ("test", "tests", "spec", "__tests__")


def plan_scale(text: str) -> Dict[str, int]:
    """How much work plan.md names: distinct file paths, and how many are tests."""
    paths: List[str] = []
    for match in _PLAN_PATH_RE.finditer(text or ""):
        path = match.group(0).replace("\\", "/").lower()
        while path.startswith("./"):
            path = path[2:]
        if path and path not in paths:
            paths.append(path)
    return {
        "files": len(paths),
        "test_files": len([path for path in paths if _looks_like_test(path)]),
    }


def _looks_like_test(path: str) -> bool:
    segments = path.split("/")
    if any(segment in _TEST_SEGMENTS for segment in segments):
        return True
    stem = segments[-1].rsplit(".", 1)[0]
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
    )


def plan_scale_note(scale: Optional[Dict[str, Any]]) -> str:
    """The warning a plan too big for its turn budget gets. Empty when it fits."""
    if not isinstance(scale, dict) or not scale.get("warn"):
        return ""
    return (
        "warning: this plan names {0} file(s), {1} of them tests - the budget of {2}\n"
        "         turns may not be enough (three slices this size died at turn 81,\n"
        "         measured 2026-08-15)\n"
        "         raise it before approving:\n"
        "           max_turns: implement={3}     in the requirement front matter\n"
        "           --max-turns-stage implement={3}".format(
            scale.get("files", 0),
            scale.get("test_files", 0),
            scale.get("budget", DEFAULT_MAX_TURNS),
            PLAN_SUGGESTED_TURNS,
        )
    )


# ------------------------------------------------------------------ migrations
#
# git can put the files back; it cannot put the database back. A rollback whose
# range contains migrations warns about exactly that and then does the rollback
# anyway - this is a text heuristic over paths, so it must never block. The rules
# are written down here and in the README so a human can judge a false positive.

MIGRATION_SEGMENTS = ("migrations", "migration", "migrate")
# V1__init.sql (flyway), 003_add_seat.sql / 0004-add-seat.sql (everything else).
_MIGRATION_NAME_RE = re.compile(r"^(v\d+__|\d{3,}[_-])", re.IGNORECASE)


def looks_like_migration(path: str) -> bool:
    normalised = str(path).replace("\\", "/").lower()
    segments = normalised.split("/")
    if any(segment in MIGRATION_SEGMENTS for segment in segments[:-1]):
        return True
    if "alembic" in segments[:-1] and "versions" in segments[:-1]:
        return True
    name = segments[-1]
    return name.endswith(".sql") and bool(_MIGRATION_NAME_RE.match(name))


def migration_paths(paths: Sequence[str]) -> List[str]:
    return [path for path in paths if looks_like_migration(path)]


def migration_note(paths: Sequence[str]) -> str:
    """The warning a rollback range containing migrations gets. Empty when there are none."""
    found = migration_paths(paths)
    if not found:
        return ""
    return (
        "warning: this rollback range contains {0} migration file(s) - the database is NOT\n"
        "         rolled back by this command\n".format(len(found))
        + "\n".join("           {0}".format(path) for path in found[:20])
    )


# ------------------------------------------------------------------ approvals

APPROVAL_TEMPLATE = """\
# Approval gate: {stage}   ({label})
#
# Write the decision on its own line below. Lines starting with '#' are ignored.
#
#   approved
#   rejected: <reason>
#
# You may edit {artifact} before approving - what follows reads {artifact} as it
# is at the moment of approval.
{note}"""


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
    def rollbacks_path(self) -> Path:
        return self.dir / ROLLBACKS_FILENAME

    @property
    def approvals_dir(self) -> Path:
        return self.dir / "approvals"

    @property
    def amends_dir(self) -> Path:
        return self.dir / "amends"

    def amend_path(self, number: int) -> Path:
        return self.amends_dir / "{0:03d}.md".format(number)

    @property
    def slug(self) -> str:
        """The requirement part of the id, used to label runs."""
        _, _, rest = self.slice_id.partition("-")
        return rest or self.slice_id

    @property
    def label(self) -> str:
        """What this record is called in messages. An epic record answers too."""
        return self.slice_id

    #: What kind of record this is, in the one message that has to name it.
    kind = "slice"

    def approval_path(self, stage: str) -> Path:
        return self.approvals_dir / "{0}.md".format(stage)

    def ensure(self) -> "SliceRecord":
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        return self

    def read_state(self) -> Optional[Dict[str, Any]]:
        return read_json_tolerant(self.state_path)

    def write_state(self, state: Dict[str, Any]) -> None:
        write_state_atomic(self.state_path, state)

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

    def append_rollback(self, entry: Dict[str, Any]) -> None:
        """The 번복 ledger, written exactly like runs.json so readers of one read both."""
        data = read_json_tolerant(self.rollbacks_path) or {}
        rollbacks = data.get("rollbacks")
        if not isinstance(rollbacks, list):
            rollbacks = []
        rollbacks.append(entry)
        write_json_atomic(
            self.rollbacks_path, {"slice_id": self.slice_id, "rollbacks": rollbacks}
        )

    def rollbacks(self) -> List[Dict[str, Any]]:
        data = read_json_tolerant(self.rollbacks_path) or {}
        entries = data.get("rollbacks")
        return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_state_atomic(path: Path, state: Dict[str, Any]) -> None:
    """Publish a state.json - a slice's or an epic's - under the live.json rules."""
    state["updated_at"] = now_iso()
    for attempt in range(_STATE_REPLACE_RETRIES):
        try:
            write_json_atomic(path, state)
            return
        except PermissionError:
            # Windows readers may briefly open state.json without delete
            # sharing, which makes os.replace fail until that handle closes.
            if attempt + 1 >= _STATE_REPLACE_RETRIES:
                raise
            _sleep(_STATE_REPLACE_RETRY_S)


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
    """One process per record, so state.json genuinely has a single writer.

    Slices are not run in parallel, so a second process on the same record is a
    mistake rather than a case to merge: it is refused, not queued. An epic holds
    the same lock over its own directory while its queue runs.
    """

    def __init__(self, rec: Any) -> None:
        self.path = rec.dir / ".lock"
        self.label = "{0} {1}".format(getattr(rec, "kind", "slice"), rec.label)

    def __enter__(self) -> "SliceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PipelineError(
                "{0} is already running ({1}).\n"
                "    If that process is gone, delete {2}".format(
                    self.label, self._holder() or "owner unknown", self.path
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
    epic: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    state = {
        "schema": STATE_SCHEMA,
        "slice_id": slice_id,
        "status": "pending",
        "repo": str(repo),
        "gates": list(gates),
        "stage_order": list(stages),
        "stages": {name: {"status": "pending"} for name in stages},
        "quota": {"waiting": False, "resume_at": None, "retries": 0},
        # stage -> commit sha on the slice branch; empty without isolation.
        "commits": {},
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    if epic:
        # Which epic queued this slice, and where in the list it sat. Optional,
        # like every other key added after schema 2: a reader that does not know
        # it is not wrong, it is only older.
        state["epic"] = dict(epic)
    return state


def stage_entry(state: Dict[str, Any], stage: str) -> Dict[str, Any]:
    stages = state.setdefault("stages", {})
    entry = stages.get(stage)
    if not isinstance(entry, dict):
        entry = {"status": "pending"}
        stages[stage] = entry
    return entry


# ---------------------------------------------------------------- amendments
#
# An amend is neither a new stage nor a new slice: it is a new *cycle* over the
# same record, re-opening the stages a correction can change. Every key it adds
# is optional, which is why STATE_SCHEMA stays 2 - a reader that does not know
# about them is not wrong, it is only older.


def current_amend(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The amend this slice is inside, or ``None`` when it is not inside one."""
    if not state.get("amend_open"):
        return None
    amends = state.get("amends")
    if not isinstance(amends, list) or not amends:
        return None
    entry = amends[-1]
    return entry if isinstance(entry, dict) else None


def previous_result(rec: SliceRecord) -> Optional[Dict[str, Any]]:
    """The last thing this slice reported - the RESULT an amend is answering."""
    for entry in reversed(rec.runs()):
        summary = entry.get("summary")
        if isinstance(summary, str) and summary.strip():
            return {
                "stage": entry.get("stage"),
                "run_id": entry.get("run_id"),
                "summary": summary,
            }
    return None


def open_amend(
    rec: SliceRecord, state: Dict[str, Any], instruction: str, stages: Sequence[str]
) -> Dict[str, Any]:
    """Re-open ``stages`` for one more cycle and record why. Append-only."""
    amends = state.get("amends")
    if not isinstance(amends, list):
        amends = []
        state["amends"] = amends
    entry = {
        "n": len(amends) + 1,
        "instruction": instruction,
        "stages": list(stages),
        "opened_at": now_iso(),
        "previous_result": previous_result(rec),
    }
    amends.append(entry)
    state["amend_open"] = True
    for stage in stages:
        stage_state = stage_entry(state, stage)
        stage_state["status"] = "pending"
        # A session that concluded "I finished" must not be resumed with a new
        # instruction: it would judge the amendment against a stale memory.
        stage_state.pop("session_id", None)
        stage_state.pop("verdict", None)
        stage_state["failures"] = 0
    state.pop("test_verdict", None)
    rec.amends_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(rec.amend_path(entry["n"]), instruction.strip() + "\n")
    rec.write_state(state)
    return entry


def close_amend(state: Dict[str, Any]) -> None:
    """Only a finished cycle closes. A failed one stays open so a resume continues it."""
    amend = current_amend(state)
    if amend is not None:
        amend["closed_at"] = now_iso()
    state["amend_open"] = False


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

    Every git call the pipeline makes lives in ``aidev.workspace``; this is the
    one the loop reaches for often enough to keep a name here.
    """
    return workspace.porcelain(repo)


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

    Only the un-isolated path (``--no-worktree``, or a legacy v0.2 slice) still
    asks this. An isolated slice never edits the user's checkout, so its
    cleanliness stopped being a precondition - see ``workspace_dirty_reason``.
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
        "(--no-worktree edits your checkout directly, so it needs a clean one)\n"
        + "\n".join("    " + line for line in status.strip().splitlines()[:20])
    )


def ensure_clean_repo(repo: Path) -> None:
    reason = dirty_reason(repo)
    if reason is not None:
        raise PipelineError(reason)


def workspace_dirty_reason(cwd: Path, stage: str) -> Optional[str]:
    """The isolated replacement for the v0.2 dirty gate, and it asks about the worktree.

    Only before a readonly stage. That stage is judged by comparing the working
    tree before and after, which needs a clean baseline - and a violation left by
    an earlier run must not be laundered by the next one. A mutating stage has no
    such check: the worktree is the AI's sandbox, and its mess is the point.
    """
    status = repo_changes(cwd, include_slice_files=True)
    if not status or not status.strip():
        return None
    return (
        "workspace has uncommitted changes before readonly stage '{0}'; "
        "commit or revert them in {1}\n".format(stage, cwd)
        + "\n".join("    " + line for line in status.strip().splitlines()[:20])
    )


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
{amend}
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
{workspace}- Do not commit, do not branch, do not touch git state. The pipeline commits
  each stage for you when it finishes.
{allowed}
Finish with a short summary: files changed and anything the plan got wrong.
"""

_TEST_PROMPT = """\
You are the TEST stage of an unattended pipeline running on this repository.
Nobody is watching: never ask a question.
{amend}
REQUIREMENT
-----------
{requirement}

PLAN THAT WAS IMPLEMENTED
-------------------------
{plan}

INSTRUCTIONS
- Run this project's own test suite the way the project runs it. Find the
  command from the project's config rather than assuming one.
{workspace}{allowed}- Run the test command as ONE plain command. Do not chain it with '&&' or
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

# The other half of the measured defect: the agent left the plan because it had
# concluded verification was impossible. When the requirement says what the
# commands are, the stage is told so in as many words.
_TEST_COMMANDS_NOTE = """\
- This project's verification commands are declared by the requirement:
{commands}
  Run these. Do not go looking for another command, and do not conclude the
  project cannot be verified.
"""

# What an amend cycle puts in front of implement and test: the instruction, and
# what the cycle before it reported.
_AMEND_NOTE = """\

AMENDMENT - THIS IS WHAT YOU MUST DO NOW
----------------------------------------
{instruction}

WHAT THE PREVIOUS CYCLE REPORTED ({stage})
------------------------------------------
{previous}

The REQUIREMENT and PLAN below are history: they describe what this slice already
built and are here so you understand the code you are changing. The amendment
above is the change being asked for now. Do only that, and do not undo work the
amendment does not mention.
"""

# Only rendered when the slice is isolated, so the un-isolated path reads exactly
# as it did in v0.2.
_WORKSPACE_NOTE = """\
- You are in an isolated git worktree on branch {branch}, created from {base}.
  It is yours alone: the user's checkout is somewhere else and you must not go
  looking for it.
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
    stage: str,
    requirement: str,
    plan: str = "",
    allowed: Sequence[str] = (),
    branch: Optional[str] = None,
    base: Optional[str] = None,
    test_commands: Sequence[str] = (),
    amend: Optional[Dict[str, Any]] = None,
) -> str:
    if stage == "plan":
        return _PLAN_PROMPT.format(requirement=requirement.strip())
    template = _IMPLEMENT_PROMPT if stage == "implement" else _TEST_PROMPT
    note = ""
    if allowed:
        note = _ALLOWED_NOTE.format(rules="\n".join("    " + rule for rule in allowed))
    if test_commands:
        note += _TEST_COMMANDS_NOTE.format(
            commands="\n".join("    " + command for command in test_commands)
        )
    where = ""
    if branch:
        where = _WORKSPACE_NOTE.format(branch=branch, base=base or "the repository's HEAD")
    return template.format(
        requirement=requirement.strip(),
        plan=(plan.strip() or "(no plan recorded)"),
        allowed=note,
        workspace=where,
        amend=_amend_note(amend),
    )


def _amend_note(amend: Optional[Dict[str, Any]]) -> str:
    if not amend:
        return ""
    previous = amend.get("previous_result")
    previous = previous if isinstance(previous, dict) else {}
    return _AMEND_NOTE.format(
        instruction=str(amend.get("instruction") or "").strip(),
        stage=str(previous.get("stage") or "unknown stage"),
        previous=str(previous.get("summary") or "(nothing was recorded)").strip(),
    )


# ------------------------------------------------------------------ execution


@dataclass
class PipelineConfig:
    repo: Path
    data_dir: Path
    # The base budget, already resolved from the flag and the front matter.
    max_turns: int = DEFAULT_MAX_TURNS
    # Per-stage overrides of it: implement is the one that outgrew 80.
    stage_max_turns: Dict[str, int] = field(default_factory=dict)
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
    # None = in-place, which is v0.2's behaviour: a legacy slice or --no-worktree.
    workspace: Optional[workspace.Workspace] = None
    setup_command: Optional[str] = None
    test_commands: List[str] = field(default_factory=list)
    setup_timeout: float = SETUP_TIMEOUT_S
    commit_file_limit: int = MAX_COMMIT_FILES

    @property
    def cwd(self) -> Path:
        """Where Claude actually runs: the worktree when isolated, the repo otherwise."""
        return self.workspace.path if self.workspace is not None else self.repo

    def turns_for(self, stage: str) -> int:
        return self.stage_max_turns.get(stage, self.max_turns)


def allowed_tools_for(stage: str, cfg: PipelineConfig) -> Tuple[str, ...]:
    """The permission rules this stage is granted, in the order they were added.

    Empty for a stage that runs no commands, which is what keeps plan readonly.
    The same tuple feeds both ``--allowedTools`` and the prompt, so what the
    stage is told it may run can never drift from what it actually may run.
    """
    if stage not in STAGES_WITH_TEST_COMMANDS:
        return ()
    rules: List[str] = []
    if cfg.test_commands:
        # Declared: only these. The measured defect is exactly the setup-derived
        # rule crowding out the real test command until the agent believed the
        # project could not be verified at all.
        declared = tuple(
            rule for command in cfg.test_commands for rule in command_tool_rules(command)
        )
    else:
        declared = tuple(TEST_COMMAND_TOOLS) + setup_tool_rules(cfg.setup_command)
    # --allow-tool is an explicit human override in either branch, never dropped.
    for rule in declared + tuple(cfg.allow_tools):
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


@dataclass
class StageSpec:
    """How one stage is run, for a stage that is not necessarily a slice stage.

    An epic's ``decompose`` is driven by exactly the loop below - same quota
    waits, same session policy, same storage - but it is not in ``STAGES`` and
    has no entry in ``STAGE_POLICY``, so what used to be looked up is passed in.
    The default is the lookup, which is why every slice stage behaves as before.
    """

    stage: str
    policy: Dict[str, Optional[str]]
    phase: str
    allowed: Tuple[str, ...] = ()
    prompt: Optional[str] = None  # a fixed prompt; None means build one per attempt

    @classmethod
    def for_slice(cls, stage: str, cfg: "PipelineConfig") -> "StageSpec":
        return cls(
            stage=stage,
            policy=STAGE_POLICY.get(stage, STAGE_POLICY["implement"]),
            phase=stage,
            allowed=allowed_tools_for(stage, cfg),
        )


def execute_stage(
    cfg: PipelineConfig,
    rec: SliceRecord,
    stage: str,
    prompt: str,
    attempt: int,
    resume_session: Optional[str] = None,
    spec: Optional[StageSpec] = None,
) -> StageRun:
    """One stage = one ``runner.execute()``, stored exactly like ``aidev run`` stores it."""
    spec = spec or StageSpec.for_slice(stage, cfg)
    policy = spec.policy
    permission_mode = policy["permission_mode"]
    # An override may relax the editing stages but must never unlock plan.
    if permission_mode is not None and cfg.permission_mode:
        permission_mode = cfg.permission_mode

    run_id = stage_run_id(cfg.data_dir / "runs", rec.slug, stage, attempt)
    task = "{0}-{1}".format(rec.slug, stage)
    run_cfg = runner.RunConfig(
        # The worktree when isolated. ``project`` stays the real repository name
        # so `aidev stats` keeps aggregating a project instead of one slice.
        repo=cfg.cwd,
        prompt_path=rec.requirement_path,
        prompt=prompt,
        phase=spec.phase,
        project=cfg.repo.name,
        task=task,
        max_turns=cfg.turns_for(spec.stage),
        model=cfg.model,
        permission_mode=permission_mode,
        # Rule-scoped, never a blanket Bash unlock: acceptEdits alone would leave
        # every verification command waiting for an approval nobody is there to give.
        allowed_tools=",".join(spec.allowed) or None,
        safety_profile=policy["safety_profile"] or "default",
        resume_session=resume_session,
        claude_cmd=list(cfg.claude_cmd),
        extra_args=list(cfg.extra_args),
    )
    telemetry = Telemetry(
        run_id=run_id,
        project=cfg.repo.name,
        phase=spec.phase,
        repo=str(cfg.cwd),
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
    spec: Optional[StageSpec] = None,
    amend: Optional[Dict[str, Any]] = None,
) -> StageRun:
    """Run one stage, waiting out usage limits on the same session as it goes."""
    spec = spec or StageSpec.for_slice(stage, cfg)
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

    while True:
        attempt += 1
        entry["status"] = "running"
        entry["attempts"] = attempt
        set_status(rec, state, "running:{0}".format(stage))

        prompt = spec.prompt or build_prompt(
            stage,
            requirement,
            rec.read_plan(),
            allowed=spec.allowed,
            branch=cfg.workspace.branch if cfg.workspace else None,
            # What the worktree was actually cut from, which in an epic chain is
            # the previous slice's branch rather than the base it merges into.
            base=(cfg.workspace.start or cfg.workspace.base) if cfg.workspace else None,
            test_commands=cfg.test_commands,
            amend=amend,
        )
        if session:
            prompt += _RESUME_NOTE.format(stage=stage)
        elif fresh:
            prompt += _FRESH_SESSION_NOTE
        banner(stage, rec.label, attempt, resumed=bool(session), fresh=fresh)

        run = execute_stage(cfg, rec, stage, prompt, attempt, resume_session=session, spec=spec)
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


# ------------------------------------------------------------------- setup

# A fresh worktree has no node_modules/, no venv, no build output: git only
# carries what is tracked. The pipeline still runs nothing of its own accord -
# the requirement has to say what, once, in its front matter.


def run_setup(cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any]) -> Optional[str]:
    """Run the declared setup command once. Returns a reason to fail the slice, or None."""
    command = cfg.setup_command
    if not command:
        return None  # nothing declared: nothing runs, and nothing is granted
    entry = state.get("setup")
    if isinstance(entry, dict) and entry.get("status") == "done" and entry.get("command") == command:
        say("setup already ran for this slice - not repeating it")
        return None

    argv = split_command(command)
    exe = shutil.which(argv[0], path=os.environ.get("PATH"))
    if exe is None:
        return "setup command not found on PATH: {0}".format(argv[0])
    log_path = rec.dir / "setup.log"
    say("setup: {0}".format(command))
    try:
        proc = subprocess.run(
            [exe] + argv[1:],
            cwd=str(cfg.cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            timeout=cfg.setup_timeout,
        )
        output, code = proc.stdout or "", proc.returncode
    except subprocess.TimeoutExpired:
        output, code = "timed out after {0}s".format(cfg.setup_timeout), None
    except OSError as exc:
        output, code = str(exc), None

    write_text_atomic(log_path, output)
    state["setup"] = {
        "command": command,
        "status": "done" if code == 0 else "failed",
        "exit_code": code,
        "ran_at": now_iso(),
        "log": str(log_path),
    }
    rec.write_state(state)
    if code == 0:
        return None
    # Implementing on top of a half-installed environment produces failures that
    # look like the model's and are not.
    tail = "\n".join("    " + line for line in output.strip().splitlines()[-20:])
    return "setup command failed (exit {0}): {1}\n{2}\n    full log: {3}".format(
        code, command, tail, log_path
    )


# -------------------------------------------------------- branch bookkeeping


def history_dir(cfg: PipelineConfig, slice_id: str) -> Path:
    return Path(cfg.cwd) / AIDEV_DIRNAME / HISTORY_DIR / slice_id


def history_snapshot(rec: SliceRecord, state: Dict[str, Any]) -> Dict[str, Any]:
    """What the commit carries about the slice it belongs to.

    A projection, not a second source of truth: nothing reads it back. state.json
    in the user's checkout stays the one authority, with one writer.
    """
    stages = {}
    for name in stage_order(state):
        entry = stage_entry(state, name)
        stages[name] = {
            key: entry.get(key)
            for key in ("status", "approval", "approval_reason", "run_id", "verdict", "attempts")
            if entry.get(key) is not None
        }
        commit = (state.get("commits") or {}).get(name)
        if commit:
            stages[name]["commit"] = commit
    runs = [
        {
            key: entry.get(key)
            for key in ("stage", "attempt", "run_id", "status", "turns", "tokens", "cost_usd")
        }
        for entry in rec.runs()
    ]
    ws = state.get("workspace") or {}
    return {
        "slice_id": rec.slice_id,
        "status": state.get("status"),
        "branch": ws.get("branch"),
        "base": ws.get("base"),
        "base_commit": ws.get("base_commit"),
        "start": ws.get("start"),
        "epic": state.get("epic"),
        "gates": state.get("gates"),
        "setup": state.get("setup"),
        "plan_scale": state.get("plan_scale"),
        "amends": state.get("amends"),
        "stages": stages,
        "runs": runs,
        "updated_at": now_iso(),
    }


def mirror_history(cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any]) -> Path:
    """Project the slice's own record into the worktree, so the commit carries it.

    Rewritten before every stage commit, which is also how a plan.md edited by a
    human during the gate reaches the branch: the next stage commit carries it.
    """
    directory = history_dir(cfg, rec.slice_id)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        write_text_atomic(directory / "requirement.md", rec.requirement_path.read_text(encoding="utf-8"))
    except OSError:
        pass
    plan = rec.read_plan()
    if plan.strip():
        write_text_atomic(directory / "plan.md", plan)
    for entry in state.get("amends") or []:
        if not isinstance(entry, dict) or not entry.get("n"):
            continue
        source = rec.amend_path(int(entry["n"]))
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        (directory / "amends").mkdir(parents=True, exist_ok=True)
        write_text_atomic(directory / "amends" / source.name, text)
    write_json_atomic(directory / "slice.json", history_snapshot(rec, state))
    return directory


def _pending_files(cwd: Path) -> List[str]:
    status = git_porcelain(cwd) or ""
    return [_porcelain_path(line) for line in status.splitlines() if line.strip()]


def _explosion_reason(cfg: PipelineConfig, stage: str, paths: Sequence[str]) -> str:
    counts: Dict[str, int] = {}
    for path in paths:
        counts[path.split("/", 1)[0]] = counts.get(path.split("/", 1)[0], 0) + 1
    top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    return (
        "stage '{0}' left {1} changed files, over the --commit-file-limit of {2}, so "
        "nothing was committed.\n"
        "    The work is still in {3} - this usually means a setup command produced "
        "output the project does not .gitignore.\n".format(
            stage, len(paths), cfg.commit_file_limit, cfg.cwd
        )
        + "\n".join("    {0:<40}{1}".format(name, count) for name, count in top)
    )


def commit_stage(
    cfg: PipelineConfig,
    rec: SliceRecord,
    state: Dict[str, Any],
    stage: str,
    label: Optional[str] = None,
) -> Optional[str]:
    """One commit per stage, made by the pipeline - never by the agent.

    Returns a reason to fail the slice, or None. A failed stage is not committed:
    whatever it left behind rides along with the next stage that succeeds, where
    a human sees it in one diff at merge time.

    ``label`` is what the commit is called and how it is keyed in ``commits``;
    it defaults to the stage. An amend cycle passes ``amend1/implement``, so the
    original ``implement`` commit survives beside it.
    """
    if cfg.workspace is None:
        return None
    label = label or stage
    mirror_history(cfg, rec, state)
    pending = _pending_files(cfg.cwd)
    if cfg.commit_file_limit and len(pending) > cfg.commit_file_limit:
        return _explosion_reason(cfg, stage, pending)
    history = "{0}/{1}/{2}".format(AIDEV_DIRNAME, HISTORY_DIR, rec.slice_id)
    try:
        sha = workspace.commit_all(
            cfg.cwd, COMMIT_MESSAGE.format(rec.slice_id, label), force_paths=(history,)
        )
    except workspace.GitError as exc:
        return "could not commit stage '{0}': {1}".format(stage, exc)
    commits = state.setdefault("commits", {})
    if isinstance(commits, dict) and sha:
        commits[label] = sha
    if sha:
        # 'requirement' is a commit, not a stage: it must not appear in stages{}.
        if stage in stage_order(state):
            stage_entry(state, stage)["commit"] = sha
        say("committed {0} ({1})".format(COMMIT_MESSAGE.format(rec.slice_id, label), sha[:7]))
    return None


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
    cfg: PipelineConfig,
    rec: SliceRecord,
    state: Dict[str, Any],
    stage: str,
    artifact: str = "plan.md",
    note: str = "",
) -> Decision:
    """Block until ``approvals/<stage>.md`` says approved or rejected.

    The file is the durable record, so a process killed while waiting resumes
    into exactly this function and reads the same answer. ``artifact`` is what
    the human may edit first - plan.md for a slice, slices.md for an epic.
    ``note`` is advice the deciding human should see: it is printed here and
    rendered into the template as comment lines, which ``read_decision`` ignores.
    """
    path = rec.approval_path(stage)
    decision = read_decision(path)
    if decision.verdict == PENDING:
        rec.approvals_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            write_text_atomic(
                path,
                APPROVAL_TEMPLATE.format(
                    stage=stage, label=rec.label, artifact=artifact, note=_comment_lines(note)
                ),
            )
        set_status(rec, state, "waiting_approval:{0}".format(stage))
        say("waiting for approval of '{0}'".format(stage))
        say("  edit {0}".format(path))
        say("  write 'approved' or 'rejected: <reason>' on its own line")
        say_lines(note)
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
        # Isolation keeps a forgery away from the user's checkout; this keeps it
        # from counting as a legal move inside the worktree either.
        after = repo_changes(cfg.cwd, include_slice_files=True)
        if before is not None and after is not None and before != after:
            return "plan stage changed the repository despite the readonly profile:\n" + _diff_lines(
                before, after
            )
        rec.write_plan(run.text)
        say("plan saved: {0}".format(rec.plan_path))
        state["plan_scale"] = _measure_plan(cfg, run.text)
        say_lines(plan_scale_note(state["plan_scale"]))

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


def _measure_plan(cfg: PipelineConfig, text: str) -> Dict[str, Any]:
    """What plan.md names, and whether that is more than implement's budget fits."""
    scale: Dict[str, Any] = dict(plan_scale(text))
    budget = cfg.turns_for("implement")
    scale["budget"] = budget
    scale["warn"] = bool(
        (scale["files"] >= PLAN_FILE_WARN or scale["test_files"] >= PLAN_TEST_WARN)
        # Already raised: there is nothing left to advise.
        and budget < PLAN_SUGGESTED_TURNS
    )
    return scale


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
    # Inside an amend only that cycle's stages run, so plan is history and its
    # gate is never re-read - which is also what lets a rejected slice be amended.
    amend = current_amend(state)
    amend_stages = tuple(amend.get("stages") or ()) if amend is not None else ()
    for stage in stage_order(state):
        if amend is not None and stage not in amend_stages:
            continue
        entry = stage_entry(state, stage)
        if entry.get("status") != "done":
            readonly = STAGE_POLICY.get(stage, {}).get("safety_profile") == "readonly"
            if cfg.workspace is not None:
                # Isolated: the user's checkout is not this slice's business, so
                # only the worktree is asked, and only where a clean baseline is
                # actually needed.
                if readonly:
                    reason = workspace_dirty_reason(cfg.cwd, stage)
                    if reason is not None:
                        return fail_slice(rec, state, reason)
            elif not state.get("mutated"):
                # In place: until this slice has edited anything, the tree must
                # still be clean. A gate can be open for hours, and a resumed
                # slice may be picking up after a readonly violation. Once a
                # stage has edited, its own work is what makes the tree dirty.
                reason = dirty_reason(cfg.repo, quiet=True)
                if reason is not None:
                    return fail_slice(rec, state, "before stage '{0}': {1}".format(stage, reason))

            # Only a readonly stage is verified against the working tree afterwards.
            before = repo_changes(cfg.cwd, include_slice_files=True) if readonly else None
            if not readonly:
                state["mutated"] = True
            if stage == "implement":
                reason = run_setup(cfg, rec, state)
                if reason is not None:
                    return fail_slice(rec, state, reason)
            try:
                run = run_stage(cfg, rec, state, stage, requirement, amend=amend)
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
            label = AMEND_LABEL.format(amend["n"], stage) if amend is not None else None
            reason = commit_stage(cfg, rec, state, stage, label=label)
            if reason is not None:
                # The stage itself succeeded and stays done; what failed is the
                # snapshot, and a human has to say what belongs on the branch.
                return fail_slice(rec, state, reason)
            rec.write_state(state)

        # Stage-independent: the same question after every stage, and the front
        # matter only decided which gates are on.
        if stage in gates:
            note = plan_scale_note(state.get("plan_scale")) if stage == "plan" else ""
            decision = await_approval(cfg, rec, state, stage, note=note)
            if decision.verdict == REJECTED:
                state["reason"] = "rejected at '{0}': {1}".format(
                    stage, decision.reason or "(no reason given)"
                )
                set_status(rec, state, STATUS_REJECTED)
                say("REJECTED - {0}".format(state["reason"]))
                return EXIT_REJECTED
            if decision.verdict != APPROVED:
                return fail_slice(rec, state, "timed out waiting for approval of '{0}'".format(stage))

    if amend is not None:
        close_amend(state)
    set_status(rec, state, STATUS_DONE)
    return EXIT_DONE


# --------------------------------------------------------------------- output


def say(message: str) -> None:
    print("[pipeline] {0}".format(message), flush=True)


def say_lines(text: str) -> None:
    """A multi-line note, every line carrying the prefix. Silent when empty."""
    for line in (text or "").splitlines():
        say(line)


def _comment_lines(text: str) -> str:
    """The same note as '#' lines, so it can sit inside an approval file inertly."""
    if not text:
        return ""
    return "#\n" + "".join(("# " + line).rstrip() + "\n" for line in text.splitlines())


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
    amends = [entry for entry in (state.get("amends") or []) if isinstance(entry, dict)]
    if amends:
        said = str(amends[-1].get("instruction") or "").strip().splitlines()
        lines.append(
            "Amends    {0}  (last: {1})".format(len(amends), _clip(said[0] if said else "", 48))
        )
    if state.get("reason"):
        lines.append("Reason    {0}".format(state["reason"]))
    lines.extend(_workspace_lines(state))
    return "\n".join(lines)


def _workspace_lines(state: Dict[str, Any]) -> List[str]:
    """Where the work landed and what to do with it. Silent without isolation."""
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is None:
        return []
    lines = ["", "Workspace {0}".format(ws.path)]
    # In an epic chain the branch was cut from the slice before it, but it still
    # merges into the base - saying only one of the two would mislead.
    start = ws.start or ""
    # Nothing to add when the fork point is the base itself, by name or by commit.
    forked = "" if start in ("", ws.base, ws.base_commit) else ", from {0}".format(start)
    lines.append(
        "Branch    {0}  (base: {1} @ {2}{3})".format(
            ws.branch, ws.base or "detached HEAD", (ws.base_commit or "?")[:7], forked
        )
    )
    commits = state.get("commits")
    if isinstance(commits, dict) and commits:
        order = [REQUIREMENT_STAGE] + stage_order(state)
        # The amend keys are not stages, so they come after rather than vanish.
        order += [name for name in commits if name not in order]
        shown = [
            "{0} {1}".format(name, str(commits[name])[:7]) for name in order if commits.get(name)
        ]
        if shown:
            lines.append("Commits   {0}".format("  ".join(shown)))
    setup = state.get("setup")
    if isinstance(setup, dict) and setup.get("command"):
        lines.append(
            "Setup     {0}  ({1}, exit {2})".format(
                setup["command"], setup.get("status", "?"), setup.get("exit_code")
            )
        )
    rollback = state.get("rollback")
    if isinstance(rollback, dict) and rollback.get("n"):
        lines.append(
            "Rollback  #{0} {1} -> {2}  ({3} -> {4})".format(
                rollback.get("n"),
                rollback.get("kind", "?"),
                rollback.get("target", "?"),
                str(rollback.get("from") or "?")[:7],
                str(rollback.get("to") or "?")[:7],
            )
        )
    slice_id = state.get("slice_id", "?")
    repo = state.get("repo", "<repo>")
    status = state.get("status")
    if status == STATUS_DONE:
        lines.append("")
        lines.append("Next      aidev pipeline --repo {0} --merge {1}".format(repo, slice_id))
        lines.append("          aidev pipeline --repo {0} --discard {1}".format(repo, slice_id))
    elif status == STATUS_ROLLED_BACK:
        lines.append("")
        lines.append(
            "Next      aidev pipeline --repo {0} --resume-slice {1}".format(repo, slice_id)
        )
    elif status == STATUS_REVERTED:
        lines.append("")
        lines.append(
            'Next      aidev pipeline --repo {0} --amend {1} "<what to fix>"'.format(
                repo, slice_id
            )
        )
        lines.append("          aidev pipeline --repo {0} --discard {1}".format(repo, slice_id))
    return lines


def _clip(text: str, limit: int) -> str:
    mark = reporter.glyph("ellipsis")
    return text if len(text) <= limit else text[: max(0, limit - len(mark))] + mark


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
        "--epic",
        type=Path,
        default=None,
        help="epic markdown file: decompose it into slices, approve the list, run them in order",
    )
    cmd.add_argument(
        "--resume-slice", default=None, help="continue a stopped slice (id, prefix, or 'last')"
    )
    cmd.add_argument(
        "--resume-epic", default=None, help="continue a stopped epic queue (id, prefix, or 'last')"
    )
    cmd.add_argument(
        "--list",
        action="store_true",
        dest="list_slices",
        help="list slices and epics of --repo and exit",
    )
    cmd.add_argument("--merge", default=None, metavar="SLICE", help="merge a finished slice into its base")
    cmd.add_argument(
        "--push",
        action="store_true",
        help="with --merge: push the base branch to its remote (never the slice branch)",
    )
    cmd.add_argument(
        "--discard", default=None, metavar="SLICE", help="remove a slice's worktree and branch"
    )
    cmd.add_argument(
        "--amend",
        default=None,
        metavar="SLICE",
        help="run a fix cycle on a finished slice, on the branch it already owns",
    )
    cmd.add_argument(
        "--rollback",
        default=None,
        metavar="SLICE",
        help="rewind a slice's branch and state to a stage commit (needs --to)",
    )
    cmd.add_argument(
        "--to",
        dest="to_stage",
        default=None,
        metavar="STAGE",
        help="with --rollback: the stage commit to rewind to (or 'requirement')",
    )
    cmd.add_argument(
        "--revert-merge",
        default=None,
        metavar="SLICE",
        help="put a revert of this slice's merge commit on its base branch",
    )
    cmd.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help="why you are rolling back - stored in the slice's rollback record",
    )
    cmd.add_argument(
        "--base",
        default=None,
        metavar="BRANCH",
        help="branch the slice starts from (default: the repo's current HEAD, never 'main')",
    )
    cmd.add_argument(
        "--worktree-root",
        type=Path,
        default=None,
        metavar="DIR",
        help="where slice worktrees live (default: <repo>-slices)",
    )
    cmd.add_argument(
        "--no-worktree",
        action="store_true",
        help="v0.2 behaviour: run in --repo itself, with no isolation",
    )
    cmd.add_argument(
        "--setup-timeout",
        type=float,
        default=SETUP_TIMEOUT_S,
        metavar="S",
        help="seconds the front matter's setup command may take (default 1800)",
    )
    cmd.add_argument(
        "--commit-file-limit",
        type=int,
        default=MAX_COMMIT_FILES,
        metavar="N",
        help="refuse a stage commit touching more files than this (default 2000)",
    )
    # No default: 'the user said 80' and 'the user said nothing' have to be told
    # apart, because the front matter sits between them in the precedence order.
    cmd.add_argument("--max-turns", type=int, default=None, help="per stage (default 80)")
    cmd.add_argument(
        "--max-turns-stage",
        action="append",
        default=[],
        dest="stage_turns",
        metavar="STAGE=N",
        help="budget for one stage, e.g. --max-turns-stage implement=140 (repeatable)",
    )
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
    # Optional, so every invocation that existed before this argument still parses.
    cmd.add_argument(
        "instruction", nargs="?", default=None, help="what to change (only with --amend)"
    )
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
    # A repo without .aidev/ is almost certainly a junk cwd, and offering it back
    # later as a candidate would be worse than offering nothing.
    if (repo / AIDEV_DIRNAME).is_dir():
        remember_repo(data_dir, repo)
    # Each of these is a whole command, not a modifier of another one.
    modes = (
        ("list_slices", "--list"),
        ("merge", "--merge"),
        ("discard", "--discard"),
        ("amend", "--amend"),
        ("rollback", "--rollback"),
        ("revert_merge", "--revert-merge"),
        ("resume_slice", "--resume-slice"),
        ("resume_epic", "--resume-epic"),
        ("requirement", "--requirement"),
        ("epic", "--epic"),
    )
    chosen = [flag for attr, flag in modes if getattr(args, attr, None)]
    if len(chosen) > 1:
        raise PipelineError("these cannot be combined: {0}".format(", ".join(chosen)))
    instruction = getattr(args, "instruction", None)
    if instruction is not None and not getattr(args, "amend", None):
        raise PipelineError(
            "unexpected argument {0!r} - a bare argument is the instruction for --amend, "
            "and there is no --amend here".format(instruction)
        )
    if getattr(args, "push", False) and not getattr(args, "merge", None):
        raise PipelineError("--push only makes sense with --merge")
    rollback = getattr(args, "rollback", None)
    if rollback and not getattr(args, "to_stage", None):
        raise PipelineError(
            "--rollback needs --to <stage>: the stage commit to rewind to.\n"
            "    aidev pipeline --repo {0} --rollback {1} --to plan".format(repo, rollback)
        )
    if getattr(args, "to_stage", None) and not rollback:
        raise PipelineError("--to only makes sense with --rollback")
    if getattr(args, "reason", None) and not (rollback or getattr(args, "revert_merge", None)):
        raise PipelineError("--reason only makes sense with --rollback or --revert-merge")
    # Local, and only here: the epic module is a layer built on this one, so it
    # imports pipeline at the top. Importing it back at module level would be a cycle.
    from . import epic as epic_module

    if args.list_slices:
        epic_module.list_epics(repo)
        return list_slices(repo, repo_given, data_dir)
    if rollback:
        return rollback_slice(args, repo, repo_given, data_dir)
    if getattr(args, "revert_merge", None):
        return revert_merge_slice(args, repo, repo_given, data_dir)
    if getattr(args, "merge", None):
        return merge_slice(args, repo, repo_given, data_dir)
    if getattr(args, "discard", None):
        return discard_slice(args, repo, repo_given, data_dir)
    if getattr(args, "amend", None):
        return amend_slice(args, repo, data_dir, repo_given)
    if args.resume_slice:
        return resume_slice(args, repo, data_dir, repo_given)
    if getattr(args, "resume_epic", None):
        return epic_module.resume_epic(args, repo, data_dir, repo_given)
    if args.requirement:
        return start_slice(args, repo, data_dir)
    if getattr(args, "epic", None):
        return epic_module.start_epic(args, repo, data_dir)
    raise PipelineError(
        "one of --requirement, --epic, --amend, --rollback, --revert-merge, --resume-slice "
        "or --list is required"
    )


def _config(
    args: Any,
    repo: Path,
    data_dir: Path,
    ws: Optional[workspace.Workspace] = None,
    setup_command: Optional[str] = None,
    fields: Optional[Dict[str, str]] = None,
) -> PipelineConfig:
    """The one place turn budgets are resolved, so every entry point agrees.

    CLI stage > front-matter stage > CLI base > front-matter base > the default.
    A caller with no front matter to offer (an epic's own decompose) gets exactly
    what it got before.
    """
    fields = fields or {}
    front_base, front_stage = resolve_max_turns(fields)
    _, cli_stage = parse_turn_tokens(
        getattr(args, "stage_turns", None) or [], STAGES, "--max-turns-stage", allow_base=False
    )
    stage_turns = dict(front_stage)
    stage_turns.update(cli_stage)
    cli_base = getattr(args, "max_turns", None)
    base = cli_base if cli_base is not None else front_base
    return PipelineConfig(
        repo=repo,
        data_dir=data_dir,
        max_turns=DEFAULT_MAX_TURNS if base is None else base,
        stage_max_turns=stage_turns,
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
        workspace=ws,
        setup_command=setup_command,
        test_commands=list(resolve_test_commands(fields)),
        setup_timeout=getattr(args, "setup_timeout", SETUP_TIMEOUT_S),
        commit_file_limit=getattr(args, "commit_file_limit", MAX_COMMIT_FILES),
    )


def resolve_requirement(path: Path, repo: Path, flag: str = "--requirement") -> Path:
    candidates = [path.expanduser()]
    if not path.is_absolute():
        candidates.append((Path.cwd() / path).resolve())
        candidates.append((repo / path).resolve())
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise PipelineError("{0} not found: {1}".format(flag, path))


@dataclass
class SliceOutcome:
    """What launching a slice produced, for a caller that has to keep going."""

    code: int
    rec: SliceRecord
    state: Dict[str, Any]


def start_slice(args: Any, repo: Path, data_dir: Path) -> int:
    source = resolve_requirement(args.requirement, repo)
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise PipelineError("requirement file is empty: {0}".format(source))

    if args.dry_run:
        fields, body = parse_front_matter(text)
        if not body.strip():
            raise PipelineError("requirement has front matter but no body: {0}".format(source))
        gates = resolve_gates(fields)
        setup_command = resolve_setup(fields)
        test_commands = resolve_test_commands(fields)
        turns = _config(args, repo, data_dir, fields=fields)
        root = slices_root(repo)
        slice_id = make_slice_id(source.stem, root)
        rec = SliceRecord(root, slice_id)
        isolate = not getattr(args, "no_worktree", False)
        plan = _plan_workspace(args, repo, slice_id) if isolate else None
        print("slice     {0}".format(slice_id))
        print("repo      {0}".format(repo))
        if plan is not None:
            print("base      {0} @ {1}".format(plan.base or "detached HEAD", plan.base_commit[:7]))
            print("branch    {0}".format(plan.branch))
            print("worktree  {0}".format(plan.path))
            for reason in workspace.blocking_reasons(repo, plan):
                print("BLOCKED   {0}".format(reason))
        else:
            print("worktree  (none: --no-worktree runs in the repo itself)")
        print("setup     {0}".format(setup_command or "(none declared: nothing runs)"))
        print("tests     {0}".format(
            ", ".join(test_commands) or "(none declared: the built-in rules apply)"
        ))
        print("stages    {0}".format(" -> ".join(STAGES)))
        print("turns     {0}".format(
            "  ".join("{0}={1}".format(name, turns.turns_for(name)) for name in STAGES)
        ))
        print("gates     {0}".format(", ".join(gates) or "(none: fully unattended)"))
        print("slice dir {0}".format(rec.dir))
        print("run dir   {0}".format(data_dir / "runs"))
        return EXIT_DONE

    return launch_slice(args, repo, data_dir, text, source.stem).code


def launch_slice(
    args: Any,
    repo: Path,
    data_dir: Path,
    text: str,
    name: str,
    base: Optional[str] = None,
    start: Optional[str] = None,
    epic: Optional[Dict[str, Any]] = None,
    quiet_unfinished: bool = False,
) -> SliceOutcome:
    """Create a slice from requirement text and run it to its first stop.

    Everything a new slice needs, with nothing about *where the text came from*:
    a file the human named, or one item of an approved epic list. The epic queue
    is only the second caller of this, which is why it inherits the whole v0.3
    flow rather than re-stating it.
    """
    fields, body = parse_front_matter(text)
    if not body.strip():
        raise PipelineError("requirement has front matter but no body: {0}".format(name))
    gates = resolve_gates(fields)
    setup_command = resolve_setup(fields)
    # Read up front, so a typo in either line is refused before a worktree exists.
    resolve_test_commands(fields)
    resolve_max_turns(fields)

    root = slices_root(repo)
    unfinished = [
        rec.slice_id
        for rec in _existing_slices(root)
        if str((rec.read_state() or {}).get("status", "")) not in (STATUS_DONE, STATUS_REJECTED)
    ]
    slice_id = make_slice_id(name, root)
    rec = SliceRecord(root, slice_id)
    isolate = not getattr(args, "no_worktree", False)
    plan = _plan_workspace(args, repo, slice_id, base=base, start=start) if isolate else None

    if plan is None:
        say("warning: --no-worktree - the AI will edit {0} directly".format(repo))
        ensure_clean_repo(repo)
    else:
        _refuse_blocked_workspace(repo, plan)
    # Inside a queue the advice is wrong: the unfinished slices are the queue's own.
    if unfinished and not quiet_unfinished:
        say("note: unfinished slice(s) here: {0}".format(", ".join(unfinished)))
        say("      use --resume-slice <id> to continue one instead of starting over")

    rec.ensure()
    write_text_atomic(rec.requirement_path, text)
    state = new_state(slice_id, repo, gates, epic=epic)
    if setup_command:
        state["setup"] = {"command": setup_command, "status": "pending"}
    say("slice {0}".format(slice_id))
    say("  dir   {0}".format(rec.dir))
    say("  gates {0}".format(", ".join(gates) or "(none: fully unattended)"))

    ws = None
    if plan is not None:
        ws = _open_workspace(repo, plan, rec, state)
    rec.write_state(state)

    code = _finish(_config(args, repo, data_dir, ws, setup_command, fields), rec, state, body)
    return SliceOutcome(code=code, rec=rec, state=state)


def _plan_workspace(
    args: Any,
    repo: Path,
    name: str,
    branch: Optional[str] = None,
    base: Optional[str] = None,
    start: Optional[str] = None,
) -> workspace.WorkspacePlan:
    """Work out where this workspace would live, refusing a repo that cannot host one."""
    if not workspace.git_available():
        raise PipelineError(
            "git is not on PATH, so no worktree can be created.\n"
            "    Install git, or pass --no-worktree to run in {0} itself.".format(repo)
        )
    if not workspace.is_git_repo(repo):
        raise PipelineError(
            "{0} is not a git repository, so no worktree can be created.\n"
            "    Run 'git init' there, or pass --no-worktree to run in it directly.".format(repo)
        )
    try:
        return workspace.plan_workspace(
            repo,
            name,
            branch or SLICE_BRANCH_PREFIX + name,
            base=base if base is not None else getattr(args, "base", None),
            start=start,
            root=getattr(args, "worktree_root", None),
        )
    except workspace.GitError as exc:
        raise PipelineError(str(exc))


def _refuse_blocked_workspace(repo: Path, plan: workspace.WorkspacePlan) -> None:
    """Leftovers are reported, never reused and never cleaned - cleaning is the human's."""
    reasons = workspace.blocking_reasons(repo, plan)
    if reasons:
        raise PipelineError(
            "cannot create the workspace for this slice:\n"
            + "\n".join("  - " + reason for reason in reasons)
        )
    stale = workspace.stale_worktrees(repo)
    if stale:
        say("note: {0} stale worktree(s) registered in this repo - left alone:".format(len(stale)))
        for entry in stale[:12]:
            say("      {0}  ({1})".format(entry.path, entry.prunable or "locked"))
        say("      aidev never prunes. If they are really gone: git worktree prune")


def _open_workspace(
    repo: Path, plan: workspace.WorkspacePlan, rec: SliceRecord, state: Dict[str, Any]
) -> workspace.Workspace:
    """Create the worktree and make the requirement its first commit.

    The requirement being a commit rather than a working-tree file is what ends
    v0.2's circle, where copying the requirement in was itself what made the repo
    dirty enough to refuse the next stage.
    """
    injected = workspace.identity_args(repo)
    if injected:
        say("note: git has no user.name/user.email here - slice commits use aidev <aidev@localhost>")
    try:
        ws = workspace.create(repo, plan)
    except workspace.GitError as exc:
        raise PipelineError(str(exc))
    say("  work  {0}  (branch {1} from {2})".format(ws.path, ws.branch, ws.base or "detached HEAD"))
    state["workspace"] = ws.to_dict()

    cfg = PipelineConfig(repo=repo, data_dir=Path("."), workspace=ws)
    try:
        reason = commit_stage(cfg, rec, state, REQUIREMENT_STAGE)
        if reason is not None:
            raise PipelineError(reason)
    except BaseException:
        # We made this worktree seconds ago, so undoing it is not the leftover
        # cleanup we refuse to do; leaving half a workspace behind would be worse.
        _rollback_workspace(repo, ws)
        state.pop("workspace", None)
        raise
    return ws


def _rollback_workspace(repo: Path, ws: workspace.Workspace) -> None:
    try:
        workspace.destroy(repo, ws)
    except workspace.GitError as exc:
        say("could not undo the half-made workspace: {0}".format(exc))
        say("  worktree {0}".format(ws.path))
        say("  branch   {0}".format(ws.branch))


def resume_slice(args: Any, repo: Path, data_dir: Path, repo_given: bool = True) -> int:
    rec = find_slice(repo, args.resume_slice, repo_given, data_dir)
    if args.dry_run:
        state = rec.read_state()
        if state is None:
            raise PipelineError("no readable state.json in {0}".format(rec.dir))
        ws = workspace.Workspace.from_dict(state.get("workspace"))
        print("slice   {0}  ({1})".format(rec.slice_id, state.get("status", "")))
        print("work    {0}".format(ws.path if ws else "{0} (no worktree)".format(repo)))
        print("stages  {0}".format(
            ", ".join(
                "{0}={1}".format(name, stage_entry(state, name).get("status", "?"))
                for name in stage_order(state)
            )
        ))
        return EXIT_DONE
    return continue_slice(args, repo, data_dir, rec)


def continue_slice(args: Any, repo: Path, data_dir: Path, rec: SliceRecord) -> int:
    """Pick a stopped slice up where it stopped. The queue resumes slices this way too."""
    state = rec.read_state()
    if state is None:
        raise PipelineError("no readable state.json in {0}".format(rec.dir))
    status = str(state.get("status", ""))
    if status == STATUS_DONE:
        say("slice {0} is already done".format(rec.slice_id))
        print(render_summary(state, rec.runs()))
        return EXIT_DONE

    # 'reverted' belongs here for a reason of its own: every stage of a reverted
    # slice is still done, so a resume would walk straight through the loop and
    # set the status back to 'done' - laundering the fact that it was taken off
    # the base. Amending it is the way forward, and that re-opens the stages.
    if status in (STATUS_MERGED, STATUS_DISCARDED, STATUS_REVERTED):
        raise PipelineError(
            "slice {0} is {1}; there is nothing left to resume".format(rec.slice_id, status)
        )

    fields, body = parse_front_matter(rec.requirement_path.read_text(encoding="utf-8"))
    setup_command = resolve_setup(fields)
    ws = workspace.Workspace.from_dict(state.get("workspace"))

    if ws is None:
        # A slice started before v0.3 has no workspace key, and re-creating one
        # now would put its remaining stages somewhere its earlier ones never were.
        say("note: this slice has no workspace - continuing in {0} (v0.2 behaviour)".format(repo))
    else:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            raise PipelineError(
                "{0}\n"
                "    aidev does not re-create it: the stage commits already on '{1}' were made "
                "there.\n"
                "    Restore it yourself, or: aidev pipeline --repo {2} --discard {3}".format(
                    broken, ws.branch, repo, rec.slice_id
                )
            )

    amend = current_amend(state)
    if amend is not None:
        # An amend that failed left its cycle open, so this continues the cycle
        # rather than the whole slice: the stages it did not re-open stay done.
        say("resuming amend #{0} of slice {1} (was {2})".format(
            amend.get("n"), rec.slice_id, status
        ))
    else:
        say("resuming slice {0} (was {1})".format(rec.slice_id, status))
    if status == STATUS_REJECTED:
        # The approval file is the record: a rejection stands until a human edits it.
        say("this slice was rejected: {0}".format(state.get("reason", "")))
    state.setdefault("quota", {"waiting": False, "resume_at": None, "retries": 0})
    return _finish(_config(args, repo, data_dir, ws, setup_command, fields), rec, state, body)


# ------------------------------------------------------------------- amend
#
# The 사후 감독 channel: a finished slice is corrected by throwing one more
# instruction at it, not by writing a second requirement. This is the most
# frequent action once a slice runs unattended, so it is a command of its own.


def amend_slice(args: Any, repo: Path, data_dir: Path, repo_given: bool = True) -> int:
    """Run one more implement -> test cycle on a slice, on the branch it already owns."""
    instruction = (getattr(args, "instruction", None) or "").strip()
    if not instruction:
        raise PipelineError(
            "--amend needs an instruction, as one argument:\n"
            '    aidev pipeline --repo {0} --amend <slice-id> "<what to change>"'.format(repo)
        )
    rec = find_slice(repo, args.amend, repo_given, data_dir)
    state = rec.read_state()
    if state is None:
        raise PipelineError("no readable state.json in {0}".format(rec.dir))
    status = str(state.get("status", ""))
    if status == STATUS_DISCARDED:
        raise PipelineError(
            "slice {0} is discarded - its worktree and branch are gone, so there is "
            "nothing to amend".format(rec.slice_id)
        )

    fields, body = parse_front_matter(rec.requirement_path.read_text(encoding="utf-8"))
    setup_command = resolve_setup(fields)
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is None:
        # Same as a resume: a slice from before v0.3 is amended where its earlier
        # stages actually ran, not in a worktree invented after the fact.
        say("note: this slice has no workspace - amending in {0} (v0.2 behaviour)".format(repo))
    else:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            raise PipelineError(
                "{0}\n"
                "    aidev does not re-create it: the stage commits already on '{1}' were made "
                "there.\n"
                "    Restore it yourself, or: aidev pipeline --repo {2} --discard {3}".format(
                    broken, ws.branch, repo, rec.slice_id
                )
            )

    stages = [name for name in stage_order(state) if name in AMEND_STAGES]
    if not stages:
        raise PipelineError(
            "slice {0} has none of the stages an amend re-runs ({1})".format(
                rec.slice_id, ", ".join(AMEND_STAGES)
            )
        )
    number = len([e for e in (state.get("amends") or []) if isinstance(e, dict)]) + 1

    if args.dry_run:
        print("slice     {0}  ({1})".format(rec.slice_id, status or "?"))
        print("amend     #{0}".format(number))
        print("work      {0}".format(ws.path if ws else "{0} (no worktree)".format(repo)))
        print("stages    {0}   (plan is not re-run: the instruction is the plan)".format(
            " -> ".join(stages)
        ))
        print("commits   {0}".format(
            COMMIT_MESSAGE.format(rec.slice_id, AMEND_LABEL.format(number, "<stage>"))
        ))
        print("says      {0}".format(instruction))
        return EXIT_DONE

    entry = open_amend(rec, state, instruction, stages)
    say("amend #{0} of slice {1} (was {2})".format(entry["n"], rec.slice_id, status or "?"))
    say("  {0}".format(_clip(instruction.splitlines()[0], 70)))
    say("  stages {0} (plan is history: the instruction is this cycle's plan)".format(
        ", ".join(stages)
    ))
    if status == STATUS_MERGED:
        say("note: this slice was already merged, so the branch now has commits the base "
            "does not - merge it again when the amend is done:")
        say("      aidev pipeline --repo {0} --merge {1}".format(repo, rec.slice_id))
    elif status == STATUS_REVERTED:
        say("note: this slice was reverted off its base, so nothing of it is there right "
            "now - merge it again when the amend is done:")
        say("      aidev pipeline --repo {0} --merge {1}".format(repo, rec.slice_id))
    epic = state.get("epic")
    if isinstance(epic, dict) and epic.get("epic_id"):
        say("note: this slice is part of epic {0}; later slices were branched from its "
            "earlier tip and do not contain this amend".format(epic["epic_id"]))
    state.setdefault("quota", {"waiting": False, "resume_at": None, "retries": 0})
    return _finish(_config(args, repo, data_dir, ws, setup_command, fields), rec, state, body)


# ------------------------------------------------------- merge and discard
#
# The final gate. Approval is a merge and rejection is a discard, and both are a
# human's decision typed as a command - the loop never reaches either by itself.


def _finished_workspace(
    repo: Path, slice_id: str, repo_given: bool, data_dir: Optional[Path] = None
) -> Tuple[SliceRecord, Dict[str, Any], workspace.Workspace]:
    rec = find_slice(repo, slice_id, repo_given, data_dir)
    state = rec.read_state()
    if state is None:
        raise PipelineError("no readable state.json in {0}".format(rec.dir))
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is None:
        raise PipelineError(
            "slice {0} has no workspace - it ran in {1} itself, so there is no branch "
            "to merge or discard".format(rec.slice_id, repo)
        )
    return rec, state, ws


def _require_checkout_on_base(repo: Path, ws: workspace.Workspace) -> None:
    """What must be true of the user's checkout before we write a commit into it.

    Both commands that touch the base branch ask this, in the same words: a merge
    and the revert of one. Everything else the pipeline does happens in a worktree.
    """
    on = workspace.current_branch(repo)
    if on != ws.base:
        raise PipelineError(
            "{0} is on '{1}', not on the base '{2}'.\n"
            "    aidev does not switch your branches: git -C {0} checkout {2}".format(
                repo, on or "a detached HEAD", ws.base
            )
        )
    if not workspace.is_clean(repo, tracked_only=True):
        raise PipelineError(
            "{0} has uncommitted changes to tracked files; commit or stash them first.\n"
            "    This is the one moment aidev writes to your checkout.".format(repo)
        )


def merge_slice(
    args: Any, repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None
) -> int:
    """Land a finished slice on its base branch. A conflict stops, it is never resolved."""
    rec, state, ws = _finished_workspace(repo, args.merge, repo_given, data_dir)
    status = str(state.get("status", ""))
    if status != STATUS_DONE:
        raise PipelineError(
            "slice {0} is '{1}', not '{2}' - finish or discard it before merging".format(
                rec.slice_id, status or "(unknown)", STATUS_DONE
            )
        )
    if ws.base is None:
        raise PipelineError(
            "slice {0} started from a detached HEAD, so there is no base branch to merge "
            "into. Merge '{1}' wherever you want it yourself.".format(rec.slice_id, ws.branch)
        )
    broken = workspace.verify(repo, ws)
    if broken is not None:
        raise PipelineError(broken)
    if not workspace.is_clean(ws.path):
        raise PipelineError(
            "the worktree still has uncommitted work: {0}\n"
            "    aidev will not throw it away by merging without it.".format(ws.path)
        )
    _require_checkout_on_base(repo, ws)

    with SliceLock(rec):
        result = workspace.merge_branch(
            repo, ws.branch, COMMIT_MESSAGE.format(rec.slice_id, "merge")
        )
        if not result.ok:
            state["merge"] = {
                "at": now_iso(),
                "status": "conflict",
                "conflicts": result.conflicts,
                "branch": ws.branch,
                "base": ws.base,
            }
            rec.write_state(state)
            say("MERGE CONFLICT - {0} is untouched, the merge was aborted".format(repo))
            for path in result.conflicts[:20] or ["(git named no paths)"]:
                say("  {0}".format(path))
            if result.detail:
                say("  {0}".format(result.detail.splitlines()[0]))
            say("resolve it yourself:")
            say("  git -C {0} merge --no-ff {1}".format(repo, ws.branch))
            return EXIT_CONFLICT

        state["merge"] = {
            "at": now_iso(),
            "status": "merged",
            "commit": result.commit,
            "branch": ws.branch,
            "base": ws.base,
        }
        set_status(rec, state, STATUS_MERGED)
        push = _push_base(repo, ws, state, rec) if getattr(args, "push", False) else None

    say("merged {0} into {1} ({2})".format(ws.branch, ws.base, (result.commit or "?")[:7]))
    say("the worktree is still there - aidev does not remove what it did not just create:")
    say("  aidev pipeline --repo {0} --discard {1}".format(repo, rec.slice_id))
    if push is not None and push["status"] != "pushed":
        # The merge itself stands - the commit is real and state says 'merged'.
        # But the whole point of --push is "if you forget, the only copy is local",
        # so a failed backup must not read as success.
        say("PUSH FAILED - {0}".format(push.get("error", "")))
        say("  the merge stands; only the backup did not happen")
        say("  retry it yourself: git -C {0} push {1} {2}".format(repo, push["remote"], ws.base))
        return EXIT_FAILED
    if push is not None:
        say("pushed {0} to {1} (the slice branch was not pushed)".format(ws.base, push["remote"]))
    return EXIT_DONE


def _push_base(
    repo: Path, ws: workspace.Workspace, state: Dict[str, Any], rec: SliceRecord
) -> Dict[str, Any]:
    """Back the merge up to a remote - the base branch only, never the slice branch."""
    remote = workspace.branch_remote(repo, ws.base) or "origin"
    if not workspace.remote_exists(repo, remote):
        push: Dict[str, Any] = {
            "status": "failed",
            "remote": remote,
            "error": "no git remote named '{0}'".format(remote),
        }
    else:
        try:
            workspace.push_branch(repo, remote, ws.base)
            push = {
                "status": "pushed",
                "remote": remote,
                "branch": ws.base,
                "at": now_iso(),
            }
        except workspace.GitError as exc:
            push = {"status": "failed", "remote": remote, "error": str(exc)}
    state["merge"]["push"] = push
    rec.write_state(state)
    return push


# ------------------------------------------------------------------ rollback
#
# 철학 0조: "일단 만든다, 언제든 롤백된다, 그래서 거침없다." Undoing stopped being a
# question about git - the commits carry the slice and the stage, so the undo is
# spelled in those terms too. Two shapes, because the two situations differ: a
# slice branch is local, so it is *rewound*; a base branch may already be pushed,
# so it is only ever *reverted*. Both print where they are before they move, and
# both leave a line in the slice's own rollback ledger.


def _rollback_targets(state: Dict[str, Any]) -> List[str]:
    """The labels this slice can be rewound to, in the order they were committed."""
    commits = state.get("commits")
    commits = commits if isinstance(commits, dict) else {}
    return [name for name in [REQUIREMENT_STAGE] + stage_order(state) if commits.get(name)]


def _stages_after(state: Dict[str, Any], target: str) -> List[str]:
    """Which stages a rewind to ``target`` undoes. The requirement is before them all."""
    order = stage_order(state)
    if target == REQUIREMENT_STAGE or target not in order:
        return list(order)
    return order[order.index(target) + 1 :]


def _rewind_state(
    state: Dict[str, Any], target: str, dropped: Dict[str, Any], number: int
) -> List[str]:
    """Wind the stages after ``target`` back to pending. Nothing is deleted.

    ``attempts`` and ``failures`` stay: they are what this slice already cost and
    already learned. What each stage looked like before the rewind is kept in its
    own ``rewound`` record - the requirement asks for the fact to be *recorded*,
    not erased. The dropped commits leave ``commits`` because they are no longer
    on the branch; the ledger keeps their shas.
    """
    rewound = _stages_after(state, target)
    for name in rewound:
        entry = stage_entry(state, name)
        entry["rewound"] = {
            "n": number,
            "at": now_iso(),
            "was": {
                key: entry.get(key)
                for key in ("status", "run_id", "commit", "verdict")
                if entry.get(key) is not None
            },
        }
        entry["status"] = "pending"
        # The session concluded something about work that is no longer on the
        # branch, so resuming it would judge a repository that no longer exists.
        for key in ("run_id", "commit", "verdict", "session_id"):
            entry.pop(key, None)
    commits = state.get("commits")
    if isinstance(commits, dict):
        for label in dropped:
            commits.pop(label, None)
    if "test" in rewound:
        state.pop("test_verdict", None)
    return rewound


def rollback_slice(
    args: Any, repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None
) -> int:
    """Rewind a slice's branch and state to one of its own stage commits."""
    rec, state, ws = _finished_workspace(repo, args.rollback, repo_given, data_dir)
    status = str(state.get("status", ""))
    if status == STATUS_MERGED:
        raise PipelineError(
            "slice {0} is merged - its commits are on '{1}' now, and moving the branch "
            "would not take them off it:\n"
            "    aidev pipeline --repo {2} --revert-merge {0}".format(
                rec.slice_id, ws.base or "the base", repo
            )
        )
    if status == STATUS_REVERTED:
        raise PipelineError(
            "slice {0} was reverted off '{1}'; rewinding the branch now would only lose the "
            "work the revert took out.\n"
            "    Amend it and merge again, or discard it.".format(
                rec.slice_id, ws.base or "the base"
            )
        )
    if status == STATUS_DISCARDED:
        raise PipelineError(
            "slice {0} is discarded - its worktree and branch are gone, so there is "
            "nothing to rewind".format(rec.slice_id)
        )

    targets = _rollback_targets(state)
    target = getattr(args, "to_stage", None)
    if not target or target not in targets:
        raise PipelineError(
            "--rollback needs --to <stage>, naming a stage this slice has a commit for.\n"
            "    slice {0} can be rewound to: {1}\n"
            "    an amend label ('{2}') is not a target - name the stage it re-ran".format(
                rec.slice_id,
                ", ".join(targets) or "(nothing - it has no stage commits)",
                AMEND_LABEL.format(1, "implement"),
            )
        )

    broken = workspace.verify(repo, ws)
    if broken is not None:
        raise PipelineError(
            "{0}\n"
            "    aidev does not re-create it: the stage commits already on '{1}' were made "
            "there.\n"
            "    Restore it yourself, or: aidev pipeline --repo {2} --discard {3}".format(
                broken, ws.branch, repo, rec.slice_id
            )
        )
    # Only tracked changes: a rewind throws those away. Untracked files are the
    # worktree's build output and reset does not touch them.
    if not workspace.is_clean(ws.path, tracked_only=True):
        raise PipelineError(
            "the worktree has uncommitted changes to tracked files: {0}\n"
            "    a rewind would throw them away; commit or revert them first.".format(ws.path)
        )

    target_sha = str((state.get("commits") or {})[target])
    before = workspace.resolve_commit(repo, ws.branch)
    if before is None:
        raise PipelineError("branch '{0}' does not resolve to a commit".format(ws.branch))
    if not workspace.is_ancestor(repo, target_sha, before):
        raise PipelineError(
            "the '{0}' commit {1} is not on '{2}' any more - the branch was moved by hand.\n"
            "    aidev does not guess what that meant: sort the branch out yourself.".format(
                target, target_sha[:7], ws.branch
            )
        )
    if before == target_sha:
        say(
            "slice {0} is already at '{1}' ({2}) - nothing to rewind".format(
                rec.slice_id, target, target_sha[:7]
            )
        )
        return EXIT_DONE

    commits = dict(state.get("commits") or {})
    # Reachability, not label order: an amend commits under 'amend1/implement'
    # after 'test', so no ordering of the map could tell these apart.
    dropped = {
        label: str(sha)
        for label, sha in commits.items()
        if sha and not workspace.is_ancestor(repo, str(sha), target_sha)
    }
    paths = workspace.diff_paths(repo, target_sha, before)
    number = len(rec.rollbacks()) + 1
    reason = (getattr(args, "reason", None) or "").strip() or None

    say("rollback slice {0} -> '{1}' ({2})".format(rec.slice_id, target, target_sha[:7]))
    say("  branch  {0}".format(ws.branch))
    say(
        "  was at  {0}   ({1} commit(s) dropped{2})".format(
            before[:7], len(dropped), (": " + ", ".join(sorted(dropped))) if dropped else ""
        )
    )
    say("  undo    git -C {0} reset --hard {1}".format(ws.path, before[:10]))
    say_lines(migration_note(paths))
    if getattr(args, "dry_run", False):
        say("--dry-run: nothing was changed")
        return EXIT_DONE

    with SliceLock(rec):
        try:
            workspace.reset_hard(ws.path, target_sha)
        except workspace.GitError as exc:
            raise PipelineError("could not rewind {0}: {1}".format(ws.branch, exc))
        # The ledger before the state: git has already moved, and the undo command
        # printed above stays correct whatever happens to the two writes after it.
        rewound = _stages_after(state, target)
        rec.append_rollback(
            {
                "n": number,
                "kind": "stage",
                "at": now_iso(),
                "target": target,
                "branch": ws.branch,
                "from": before,
                "to": target_sha,
                "dropped_commits": dropped,
                "rewound_stages": rewound,
                "migrations": migration_paths(paths),
                "reason": reason,
                "undo": "git -C {0} reset --hard {1}".format(ws.path, before),
            }
        )
        _rewind_state(state, target, dropped, number)
        state["rollback"] = {
            "n": number,
            "kind": "stage",
            "target": target,
            "at": now_iso(),
            "from": before,
            "to": target_sha,
        }
        state["reason"] = "rolled back to '{0}' ({1})".format(target, target_sha[:7])
        set_status(rec, state, STATUS_ROLLED_BACK)

    say("rewound {0} to {1}".format(ws.branch, target_sha[:7]))
    say("  pending again: {0}".format(", ".join(rewound) or "(nothing after '{0}')".format(target)))
    say("  recorded in {0}".format(rec.rollbacks_path))
    say("  continue from there:")
    say("    aidev pipeline --repo {0} --resume-slice {1}".format(repo, rec.slice_id))
    return EXIT_DONE


def revert_merge_slice(
    args: Any, repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None
) -> int:
    """Take a merged slice back off its base branch, with a revert commit.

    Never a reset: the base may already be pushed, and rewriting a branch other
    people have is not an undo, it is a second problem.
    """
    rec, state, ws = _finished_workspace(repo, args.revert_merge, repo_given, data_dir)
    merge = state.get("merge")
    merge = merge if isinstance(merge, dict) else {}
    merge_commit = merge.get("commit")
    if merge.get("status") != "merged" or not merge_commit:
        raise PipelineError(
            "slice {0} was never merged{1}, so there is no merge commit to revert.".format(
                rec.slice_id,
                " (its last merge ended in a conflict)"
                if merge.get("status") == "conflict"
                else "",
            )
        )
    previous = state.get("revert")
    if isinstance(previous, dict) and previous.get("status") == "reverted":
        raise PipelineError(
            "slice {0} was already reverted, by {1}.\n"
            "    To put the work back, revert the revert yourself:\n"
            "    git -C {2} revert --no-edit {1}".format(
                rec.slice_id, str(previous.get("commit") or "?")[:10], repo
            )
        )
    if ws.base is None:
        raise PipelineError(
            "slice {0} has no base branch recorded, so there is nothing to revert it "
            "from".format(rec.slice_id)
        )

    # Same four preconditions as --merge: this writes to the user's checkout too.
    _require_checkout_on_base(repo, ws)
    if not workspace.is_ancestor(repo, str(merge_commit), ws.base):
        raise PipelineError(
            "the merge commit {0} is not on '{1}' - the history was rewritten, or this is a "
            "different base.\n"
            "    aidev will not revert something that is not there.".format(
                str(merge_commit)[:7], ws.base
            )
        )

    before = workspace.head_commit(repo) or "?"
    paths = workspace.diff_paths(repo, "{0}^".format(merge_commit), str(merge_commit))
    number = len(rec.rollbacks()) + 1
    reason = (getattr(args, "reason", None) or "").strip() or None

    say("revert merge of slice {0} on {1}".format(rec.slice_id, ws.base))
    say(
        "  merge commit   {0}  ({1})".format(
            str(merge_commit)[:7], COMMIT_MESSAGE.format(rec.slice_id, "merge")
        )
    )
    say("  base is at     {0}".format(before[:7]))
    say("  undo (not pushed yet)  git -C {0} reset --hard {1}".format(repo, before[:10]))
    say("  undo (already pushed)  git -C {0} revert --no-edit <the revert commit>".format(repo))
    say_lines(migration_note(paths))
    if getattr(args, "dry_run", False):
        say("--dry-run: nothing was changed")
        return EXIT_DONE

    with SliceLock(rec):
        result = workspace.revert_commit(repo, str(merge_commit))
        if not result.ok:
            state["revert"] = {
                "at": now_iso(),
                "status": "conflict",
                "conflicts": result.conflicts,
                "merge_commit": merge_commit,
                "base": ws.base,
                "branch": ws.branch,
            }
            rec.write_state(state)
            say("REVERT CONFLICT - {0} is untouched, the revert was aborted".format(repo))
            for path in result.conflicts[:20] or ["(git named no paths)"]:
                say("  {0}".format(path))
            if result.detail:
                say("  {0}".format(result.detail.splitlines()[0]))
            say("aidev does not resolve a conflict for you. Do it yourself:")
            say("  git -C {0} revert -m 1 {1}".format(repo, merge_commit))
            return EXIT_CONFLICT

        state["revert"] = {
            "at": now_iso(),
            "status": "reverted",
            "commit": result.commit,
            "merge_commit": merge_commit,
            "base": ws.base,
            "branch": ws.branch,
        }
        rec.append_rollback(
            {
                "n": number,
                "kind": "revert-merge",
                "at": now_iso(),
                "branch": ws.branch,
                "base": ws.base,
                "merge_commit": merge_commit,
                "from": before,
                "to": result.commit,
                "migrations": migration_paths(paths),
                "reason": reason,
                "undo": "git -C {0} revert --no-edit {1}".format(repo, result.commit),
            }
        )
        set_status(rec, state, STATUS_REVERTED)

    say("reverted {0} out of {1} ({2})".format(ws.branch, ws.base, (result.commit or "?")[:7]))
    say("  undo (already pushed)  git -C {0} revert --no-edit {1}".format(repo, result.commit))
    say("  recorded in {0}".format(rec.rollbacks_path))
    say("the worktree and the branch are untouched - aidev removes nothing it did not "
        "just create. From here:")
    say('  aidev pipeline --repo {0} --amend {1} "<what to fix>"   then --merge again'.format(
        repo, rec.slice_id
    ))
    say("  aidev pipeline --repo {0} --discard {1}".format(repo, rec.slice_id))
    if merge.get("push"):
        say("this command does not push. The revert is local until you send it:")
        say("  git -C {0} push {1} {2}".format(
            repo, (merge.get("push") or {}).get("remote") or "origin", ws.base
        ))
    return EXIT_DONE


def discard_slice(
    args: Any, repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None
) -> int:
    """Throw the slice's workspace away. The user's checkout keeps no git trace of it."""
    rec, state, ws = _finished_workspace(repo, args.discard, repo_given, data_dir)
    with SliceLock(rec):
        if Path(ws.path).exists() or workspace.find_worktree(repo, ws.path) is not None:
            try:
                workspace.worktree_remove(repo, ws.path)
            except workspace.GitError as exc:
                # Windows holds files open for editors, watchers and node. Deleting
                # the branch anyway would leave the worse leftover: a worktree with
                # nothing to go back to.
                raise PipelineError(
                    "could not remove the worktree, so the branch was kept: {0}\n"
                    "    {1}\n"
                    "    Close whatever holds files in there and run --discard again.".format(
                        ws.path, exc
                    )
                )
        else:
            say("worktree was already gone: {0}".format(ws.path))
            say("  if git still lists it, prune it yourself: git -C {0} worktree prune".format(repo))
        sha = None
        if workspace.branch_exists(repo, ws.branch):
            try:
                sha = workspace.delete_branch(repo, ws.branch)
            except workspace.GitError as exc:
                raise PipelineError("could not delete branch {0}: {1}".format(ws.branch, exc))
        state["discard"] = {"at": now_iso(), "branch": ws.branch, "commit": sha}
        set_status(rec, state, STATUS_DISCARDED)

    say("discarded {0}".format(rec.slice_id))
    say("  worktree removed: {0}".format(ws.path))
    say("  branch deleted:   {0} (was {1})".format(ws.branch, (sha or "?")[:7]))
    if sha:
        say("  recoverable for now: git -C {0} branch {1} {2}".format(repo, ws.branch, sha[:10]))
    say("no git trace left in {0}. The slice's own record stays at {1}".format(repo, rec.dir))
    return EXIT_DONE


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


def record_touched_at(rec: Any) -> float:
    """When this record last moved. Ids sort alphabetically, which is not the same."""
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


def _repo_hint(repo: Path, repo_given: bool, data_dir: Optional[Path] = None) -> str:
    """An empty answer from the default repo says why it is empty, not just that.

    The repos this tool has been pointed at before are offered as candidates,
    spelled as the flag to copy. They are never applied: naming the repository
    stays the human's, because guessing it would act on the wrong checkout.
    """
    if repo_given or (Path(repo) / AIDEV_DIRNAME).is_dir():
        return ""
    hint = (
        "\n    no {0}/ here - this is probably not the target repository."
        "\n    pass --repo <path>".format(AIDEV_DIRNAME)
    )
    candidates = recent_repos(data_dir) if data_dir is not None else []
    if candidates:
        hint += "\n    recently used (aidev never picks one for you - name it):"
        hint += "".join("\n      --repo {0}".format(path) for path in candidates)
    return hint


def find_record(
    records: Sequence[Any], pattern: str, kind: str, root: Path, hint: str = ""
) -> Any:
    """Exact id, then unique prefix, then unique substring. ``last`` is by time.

    An ambiguous pattern is refused rather than resolved: picking the wrong
    record would resume the wrong work in someone's repository.
    """
    if not records:
        raise PipelineError("no {0}s in {1}{2}".format(kind, root, hint))
    if pattern in ("last", "latest", "-"):
        return max(records, key=lambda rec: (record_touched_at(rec), rec.label))

    exact = [rec for rec in records if rec.label == pattern]
    if exact:
        return exact[0]
    for candidates in (
        [rec for rec in records if rec.label.startswith(pattern)],
        [rec for rec in records if pattern in rec.label],
    ):
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise PipelineError(
                "'{0}' matches {1} {2}s: {3}\n    use the full id".format(
                    pattern,
                    len(candidates),
                    kind,
                    ", ".join(rec.label for rec in candidates[:8]),
                )
            )
    raise PipelineError("no {0} matching '{1}' in {2}".format(kind, pattern, root))


def find_slice(
    repo: Path, slice_id: str, repo_given: bool = True, data_dir: Optional[Path] = None
) -> SliceRecord:
    """The slice half of ``find_record``, with the repo's own hint attached."""
    root = slices_root(repo)
    return find_record(
        _existing_slices(root), slice_id, "slice", root, _repo_hint(repo, repo_given, data_dir)
    )


def list_slices(repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None) -> int:
    records = _existing_slices(slices_root(repo))
    if not records:
        print(
            "(no slices in {0}){1}".format(
                slices_root(repo), _repo_hint(repo, repo_given, data_dir)
            )
        )
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
