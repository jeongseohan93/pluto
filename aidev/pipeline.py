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

import json
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

from . import __version__, events as ev, progress, reporter, runner, specs, verify, workspace
from .verify import split_command
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

# Stages that are not slice stages but are still run, budgeted and modelled the
# same way: an epic's 'decompose' and the repair loop's 'diagnose'.
DIAGNOSE_STAGE = "diagnose"
TURN_STAGES: Tuple[str, ...] = STAGES + (DIAGNOSE_STAGE,)
MODEL_STAGES: Tuple[str, ...] = STAGES + (DIAGNOSE_STAGE, "decompose")
# A diagnosis that needs 80 turns is not a diagnosis. It reads and answers.
STAGE_TURN_DEFAULTS: Dict[str, int] = {DIAGNOSE_STAGE: 20}

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
# What a repair cycle commits under, same shape and for the same reason: the
# original 'implement' commit survives beside the fix the verify loop drove.
REPAIR_LABEL = "repair{0}/{1}"

# How many times the verify engine may send implement back in before it gives up
# and leaves failure.md / diagnosis.md for a human. One: measured, the second
# repair of the same failure almost never differs from the first.
DEFAULT_MAX_REPAIRS = 1
# More failures than this and a diagnosis session is worth its price; at or below
# it, failure.md alone is enough context for the retry.
DEFAULT_DIAGNOSE_THRESHOLD = 3

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
    fields: Dict[str, str], stages: Sequence[str] = TURN_STAGES
) -> Tuple[Optional[int], Dict[str, int]]:
    """``(base, per-stage)`` from ``max_turns:``. ``(None, {})`` when absent.

    Measured 2026-08-15/16: three implement stages died at turn 81 with the work
    half done, each costing a resume. One budget for every stage was the wrong
    shape - plan and test finish comfortably inside 80, implement is the one that
    needs room::

        max_turns: 120                  every stage
        max_turns: implement=140        one stage
        max_turns: 100, implement=140   a base plus an override

    @param fields  the requirement's front matter
    @param stages  which stage names a ``<stage>=`` token may name - ``diagnose``
                   joined them in v0.5, so a repair round can be budgeted too
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
    stages: Sequence[str] = TURN_STAGES,
    where: str = "max_turns:",
    allow_base: bool = True,
) -> Tuple[Optional[int], Dict[str, int]]:
    """Read ``120`` / ``implement=140`` tokens the same way for the file and the flag.

    @param tokens      the raw strings, from the file or from a repeated flag
    @param stages      the stage names a token may name
    @param where       what to call this in an error message
    @param allow_base  whether a bare token (no ``=``) is allowed as the base
    """
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


def resolve_models(
    fields: Dict[str, str], stages: Sequence[str] = MODEL_STAGES
) -> Tuple[Optional[str], Dict[str, str]]:
    """``(base, per-stage)`` from ``model:``. ``(None, {})`` when the line is absent.

    Exactly ``max_turns:``'s grammar, because the two lines answer the same shape
    of question - "for every stage, or for this one?"::

        model: claude-sonnet-5                     every stage
        model: diagnose=claude-haiku-4-5           one stage
        model: claude-opus-5, diagnose=claude-sonnet-5

    @param fields  the requirement's front matter
    @param stages  which stage names a ``<stage>=`` token may name
    """
    raw = fields.get("model")
    if raw is None:
        return None, {}
    if not raw.strip():
        raise PipelineError(
            "model: needs a model name, or <stage>=<model>, or leave the line out entirely"
        )
    return parse_model_tokens([raw], stages, "model:")


def parse_model_tokens(
    tokens: Sequence[str],
    stages: Sequence[str] = MODEL_STAGES,
    where: str = "model:",
    allow_base: bool = True,
) -> Tuple[Optional[str], Dict[str, str]]:
    """Read ``<model>`` / ``<stage>=<model>`` tokens, the way turn budgets are read.

    @param tokens      the raw strings, from the file or from a repeated flag
    @param stages      the stage names a token may name
    @param where       what to call this in an error message
    @param allow_base  whether a bare token (no ``=``) is allowed as the base
    @flow  split on comma/space -> bare token is the base -> <stage>=<model> otherwise
    주요 내부 변수: base(전체 기본 모델), per_stage(단계별 지정)
    """
    base: Optional[str] = None
    per_stage: Dict[str, str] = {}
    for raw in tokens:
        for token in re.split(r"[,\s]+", str(raw).strip()):
            if not token:
                continue
            name, sep, value = token.partition("=")
            if not sep:
                if not allow_base:
                    raise PipelineError(
                        "{0} needs <stage>=<model>, got {1!r} (stages: {2})".format(
                            where, token, ", ".join(stages)
                        )
                    )
                base = token
                continue
            name = name.strip().lower()
            if name not in stages:
                raise PipelineError(
                    "unknown stage '{0}' in {1} (stages: {2})".format(
                        name, where, ", ".join(stages)
                    )
                )
            model = value.strip()
            if not model:
                raise PipelineError("{0} names stage '{1}' with no model".format(where, name))
            if per_stage.get(name, model) != model:
                raise PipelineError(
                    "{0} sets '{1}' twice, to {2} and {3}".format(
                        where, name, per_stage[name], model
                    )
                )
            per_stage[name] = model
    return base, per_stage


_OFF_WORDS = ("off", "false", "no", "0", "none")
_ON_WORDS = ("on", "true", "yes", "1")


def resolve_spec_check(fields: Dict[str, str]) -> bool:
    """Is the 함수 명세 machine check on for this slice? On unless it says otherwise.

    The escape hatch exists because the convention is checked over a *diff*, and
    a slice that has to move a hundred existing functions would spend its budget
    documenting code it did not write.

    @param fields  the requirement's front matter
    """
    raw = fields.get("spec_check")
    if raw is None:
        return True
    value = raw.strip().lower()
    if value in _OFF_WORDS:
        return False
    if value in _ON_WORDS:
        return True
    raise PipelineError(
        "spec_check: needs 'on' or 'off', got {0!r}".format(raw.strip())
    )


# ------------------------------------------------------- unknown front matter
#
# Measured 2026-08-17: 'model:' was written into a requirement's front matter
# before this tool knew the key. It was dropped in silence, and the slice ran a
# whole stage on a budget nobody had asked for. Silence is the defect, not the
# ignoring: a key this version does not know may be a typo, a note a human left,
# or a key the next version adds - none of which is worth killing a slice over.

# Every key a reader here actually pulls out. Adding one to the readers without
# adding it here is what turns a working line into a warning, so they move together.
KNOWN_FRONT_MATTER_KEYS: Tuple[str, ...] = (
    "approval",
    "setup",
    "test_commands",
    "max_turns",
    "model",
    "spec_check",
)


def unknown_front_matter_keys(fields: Dict[str, str]) -> List[str]:
    """Front matter keys nothing reads, in the order they were written.

    @param fields  the parsed front matter - keys already lowercased and de-dashed
    """
    return [key for key in fields if key not in KNOWN_FRONT_MATTER_KEYS]


def warn_unknown_front_matter(fields: Dict[str, str], where: str = "") -> str:
    """One warning line for keys that will be ignored, or ``""`` when there are none.

    Returned rather than printed: the caller decides where a warning goes, and a
    function that returns a string can be tested without capturing output.

    @param fields  the parsed front matter
    @param where   what to name after the warning - a path, or a path and an item
    """
    unknown = unknown_front_matter_keys(fields)
    if not unknown:
        return ""
    return "warning: front matter key(s) ignored: {0} - known: {1}{2}".format(
        ", ".join(unknown),
        ", ".join(KNOWN_FRONT_MATTER_KEYS),
        " ({0})".format(where) if where else "",
    )


# ------------------------------------------------------------ requirement body
#
# Measured 2026-08-17: an empty requirement launched a slice, a plan stage and a
# gate before anyone noticed there was nothing in it. Two of the four entry
# points checked for this and two did not, so the check moved to one function
# every entry point calls.

# Deliberately near zero. The trap this guards against is an *empty* file, and
# anything more opinionated refuses real requirements: '# 긴 이름\n\n본문' is a
# whole requirement and counts five characters, because Korean says in five what
# English says in thirty. A guard that judged length would be a guard that
# judged language.
MIN_REQUIREMENT_CHARS = 5


def guard_requirement(text: str, where: str) -> str:
    """The body of a requirement, refusing one there is nothing to work from.

    @param text   the requirement file, front matter and all
    @param where  what to name in the error - a path, or a slice id
    @flow  empty file -> front matter only -> too short -> return the body
    주요 내부 변수: body(front matter를 뗀 본문)
    """
    if not (text or "").strip():
        raise PipelineError("requirement file is empty: {0}".format(where))
    _, body = parse_front_matter(text)
    if not body.strip():
        raise PipelineError("requirement has front matter but no body: {0}".format(where))
    dense = "".join(body.split())
    if len(dense) < MIN_REQUIREMENT_CHARS:
        raise PipelineError(
            "requirement body is effectively empty ({0} chars): {1}\n"
            "    A stage cannot plan from this. Write what it must do.".format(len(dense), where)
        )
    return body


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


# The one rule for "is this a test file", now that the spec check needs it too:
# defined once in aidev.specs, used from both.
_looks_like_test = specs.looks_like_test


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
#   approved: <condition>   the condition follows every later stage, retry and
#                           repair prompt of this slice
#   rejected: <reason>
#
# You may edit {artifact} before approving - what follows reads {artifact} as it
# is at the moment of approval.
#
# Optional: write the detail into approvals/rejected-detail.md - what is wrong,
# where (path:line), what you want instead, and how far it reaches ('부분 반려'
# by default, '전면 반려' to ask for a rewrite). --replan injects that file
# verbatim, so a rejection stops being one line of hindsight.
{note}"""

# Where a structured rejection may live, most specific first. Absent is normal:
# 'rejected: <reason>' on its own has always been the whole contract and stays it.
REJECT_DETAIL_NAMES: Tuple[str, ...] = ("{stage}-rejected-detail.md", "rejected-detail.md")

# 부분 반려 is the default because it is what a rejection almost always is: the
# plan was mostly right. Saying so costs one word; the alternative costs a rewrite.
GRADE_PARTIAL = "partial"
GRADE_FULL = "full"
_FULL_REJECTION_WORDS = ("전면", "full rejection", "rewrite from scratch", "start over")


@dataclass
class Decision:
    verdict: str
    # Why it was rejected, or the condition attached to an approval. Both are the
    # same thing to a reader: the sentence the deciding human wrote after the word.
    reason: str = ""


def read_decision(path: Path) -> Decision:
    """The first line that is neither blank nor a comment decides, with what follows it.

    ``approved: scope=A only`` keeps ``scope=A only``. Measured 2026-08-17: the
    words after ``approved:`` were dropped here, so a condition a human typed at
    the gate reached nothing downstream and attempt 2 built past it.

    @param path  the approval file
    @flow  read -> first non-comment line -> approved | rejected | pending
    주요 내부 변수: word(판정 단어), rest(그 뒤에 사람이 쓴 문장)
    """
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
            return Decision(APPROVED, rest.strip())
        if word in ("rejected", "reject"):
            return Decision(REJECTED, rest.strip())
        return Decision(PENDING)  # anything else: the human is still writing
    return Decision(PENDING)


def read_rejection(rec: "SliceRecord", stage: str, decision: Decision) -> Dict[str, Any]:
    """A rejection as a record: the one-line reason, and the detail file if there is one.

    The one-line form is not deprecated by the file - it is still the whole
    decision, and a slice rejected before this existed reads back identically.

    @param rec       the slice whose approvals directory is searched
    @param stage     which gate was rejected
    @param decision  what ``read_decision`` returned
    @flow  reason -> look for <stage>-rejected-detail.md then rejected-detail.md -> grade it
    주요 내부 변수: detail(본문), path(찾은 파일)
    """
    detail, path = "", ""
    for name in REJECT_DETAIL_NAMES:
        candidate = rec.approvals_dir / name.format(stage=stage)
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            detail, path = text.strip(), str(candidate)
            break
    haystack = "{0}\n{1}".format(decision.reason, detail).lower()
    grade = GRADE_FULL if any(word in haystack for word in _FULL_REJECTION_WORDS) else GRADE_PARTIAL
    return {
        "stage": stage,
        "reason": decision.reason,
        "detail": detail,
        "grade": grade,
        "path": path,
        "at": now_iso(),
    }


# A condition attached to an approval is state, not a sentence somebody said once.
# Measured 2026-08-17/18: a plan approved with 'scope=A only' was implemented past
# that scope on attempt 2, because the new session had never been told. It is kept
# where the approval itself is kept - stages.<stage>.approval_reason - so a replan,
# which wipes that key, cannot leak the old cycle's condition into the next one.


def approval_conditions(state: Dict[str, Any]) -> List[Dict[str, str]]:
    """The ``approved: <condition>`` lines this slice was given, in stage order.

    Read-only on purpose: this runs while a prompt is being built, and a builder
    that grows state.json would blur the single-writer rule the loop depends on.

    @param state  state.json
    @flow  stage_order -> approved stages carrying a comment -> list
    주요 내부 변수: stages(기록된 단계들), found(승인 조건 목록)
    """
    stages = state.get("stages")
    stages = stages if isinstance(stages, dict) else {}
    found: List[Dict[str, str]] = []
    for stage in stage_order(state):
        entry = stages.get(stage)
        if not isinstance(entry, dict) or entry.get("approval") != APPROVED:
            continue
        comment = str(entry.get("approval_reason") or "").strip()
        if comment:
            found.append({"stage": stage, "comment": comment})
    return found


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

    # --- v0.5: what a failure, a diagnosis and a dying stage leave behind. Each
    # is a document a human can read on its own, and each is fed back into the
    # session that has to act on it.

    @property
    def failure_path(self) -> Path:
        """The report a failed verification writes. Absent means it never failed."""
        return self.dir / "failure.md"

    @property
    def diagnosis_path(self) -> Path:
        """What the diagnose session answered, when a failure was large enough to buy one."""
        return self.dir / "diagnosis.md"

    @property
    def progress_path(self) -> Path:
        """What a stage had done when it ran out of turns, or died holding it."""
        return self.dir / "progress.md"

    @property
    def verify_dir(self) -> Path:
        """Where the full output of every verification command is kept, per attempt."""
        return self.dir / "verify"

    @property
    def plans_dir(self) -> Path:
        """Where superseded plans are kept, so a re-plan never destroys one."""
        return self.dir / "plans"

    def archived_plan_path(self, number: int) -> Path:
        """Where the Nth superseded plan is kept.

        @param number  which replan superseded it, counting from 1
        """
        return self.plans_dir / "{0:03d}.md".format(number)

    @property
    def hooks_dir(self) -> Path:
        """The slice's own settings file for the CLI - never the target repo's."""
        return self.dir / "hooks"

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

    def read_plan_archive(self, relative: str) -> str:
        """A superseded plan, by the path a replan record stores. Empty when absent.

        @param relative  a slice-relative path such as ``plans/001.md``
        """
        if not relative:
            return ""
        try:
            return (self.dir / relative).read_text(encoding="utf-8")
        except OSError:
            return ""

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
{amend}{repair}
REQUIREMENT
-----------
{requirement}

APPROVED PLAN
-------------
{plan}
{approval}
INSTRUCTIONS
- Follow the approved plan. Where the real code contradicts it, follow the code
  and say so in your final message.
- Change only what the requirement needs. Match the conventions of every file
  you touch.
{spec}{workspace}- Do not commit, do not branch, do not touch git state. The pipeline commits
  each stage for you when it finishes.
{allowed}
Finish with a short summary: files changed and anything the plan got wrong.
"""

# The one instruction this slice adds. Everything else it introduces is a
# mechanism (the write guard, the verify engine, the spec checker) or a document
# injected as context - because 기계 > 양식 > 지시, in that order.
_SPEC_NOTE = """\
- Every function you add or change carries a spec comment: one line saying what
  it does, then one '@param <name>' line per parameter, and '@flow' when it
  branches or loops. Changing a function without changing its spec is a failure
  the pipeline checks for, not a matter of taste. Untouched code is left alone.
"""

# What a verification failure hands the retry. Not an instruction: the same kind
# of context injection _AMEND_NOTE already is.
_REPAIR_NOTE = """\

VERIFICATION FAILED - THIS IS WHAT YOU MUST FIX NOW (repair {n})
----------------------------------------------------------------
{failure}
{diagnosis}
The plan below is what was already built. Fix what the report above names, and
do not undo or redo anything it does not mention.
"""

_DIAGNOSIS_BLOCK = """
DIAGNOSIS
---------
{diagnosis}
"""

# What the human wrote after 'approved:'. It sits directly under the plan because
# that is what it qualifies, and it is context rather than an instruction - the
# same kind of injection _REPAIR_NOTE and _AMEND_NOTE already are.
_APPROVAL_NOTE = """
APPROVAL CONDITIONS - attached by the human who approved this
-------------------------------------------------------------
{conditions}

These are part of what was approved. They hold for every attempt, including this
one. Where a condition and the plan disagree, the condition wins.
"""

# Injected into a stage that is picking up after one that ran out of turns.
_PROGRESS_NOTE = """\

WHERE THE PREVIOUS ATTEMPT GOT TO
---------------------------------
{progress}
"""

# What a re-planned plan stage is given: its own rejected plan, and why.
_REPLAN_NOTE = """\

PREVIOUS PLAN (rejected)
------------------------
{plan}

REJECTION
---------
reason: {reason}
grade:  {grade}
{detail}
{instruction}
"""

_REPLAN_PARTIAL = (
    "Change ONLY what the rejection names; keep everything else exactly as it is.\n"
    "This is a diff against the plan above, not a rewrite of it."
)
_REPLAN_FULL = "This was a 전면 반려: re-plan from scratch, keeping nothing but the requirement."

_DIAGNOSE_PROMPT = """\
You are the DIAGNOSE step of an unattended pipeline running on this repository.
Nobody is watching: never ask a question.

FAILURE REPORT
--------------
{failure}

REQUIREMENT
-----------
{requirement}

PLAN THAT WAS IMPLEMENTED
-------------------------
{plan}
{approval}
INSTRUCTIONS
- Read the failing code and the failing test. Do not run anything and do not edit
  anything: every mutating tool is blocked, and looking for a way around them is
  the one way to waste this step.
- Your final message is saved verbatim as diagnosis.md and injected into the
  session that fixes this. Emit exactly this and nothing else:

# DIAGNOSIS
## Cause
<one line: what is actually wrong>
## Fix direction
<2-5 lines: what to change, and why that fixes it>
## Where
- <path>:<line> - <what is wrong there>
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
{approval}
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

# The diet's half of the same note. Measured 2026-08-16/17: the raw output of a
# 233-test suite went into the session's context after every single edit. The
# wrapper runs exactly the commands below and prints failures only.
_DIET_COMMANDS_NOTE = """\
- This project's verification runs through one command:

    aidev verify

  It runs what the requirement declared:
{commands}
  and prints only the failures - successes are a count, the full output is a log
  file it names. Run that, not the commands themselves.
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
    repair: Optional[Dict[str, Any]] = None,
    replan: Optional[Dict[str, Any]] = None,
    spec_check: bool = True,
    diet: bool = False,
    approvals: Sequence[Dict[str, Any]] = (),
) -> str:
    """The prompt one stage is given, built from what the engine already knows.

    @param stage          which stage is about to run
    @param requirement    the requirement body
    @param plan           plan.md, as it stands at this moment
    @param allowed        the permission rules this stage was actually granted
    @param branch         the slice branch, when the slice is isolated
    @param base           what that branch was cut from
    @param test_commands  the declared verification commands
    @param amend          the open amend cycle, if any
    @param repair         a failed verification this run has to fix, if any
    @param replan         the rejected plan and its rejection, for a re-plan
    @param spec_check     whether the 함수 명세 convention is being enforced
    @param diet           whether verification reaches the stage through ``aidev verify``
    @param approvals      conditions a human attached to this slice's approvals
    @flow  plan (+replan) -> implement/test template -> allowed/test-command/workspace/approval notes
    주요 내부 변수: note(허용 규칙 + 검증 명령 안내), where(워크스페이스 안내)
    """
    if stage == "plan":
        return _PLAN_PROMPT.format(requirement=requirement.strip()) + _replan_note(replan)
    template = _IMPLEMENT_PROMPT if stage == "implement" else _TEST_PROMPT
    note = ""
    if allowed:
        note = _ALLOWED_NOTE.format(rules="\n".join("    " + rule for rule in allowed))
    if test_commands:
        note += (_DIET_COMMANDS_NOTE if diet else _TEST_COMMANDS_NOTE).format(
            commands="\n".join("    " + command for command in test_commands)
        )
    where = ""
    if branch:
        where = _WORKSPACE_NOTE.format(branch=branch, base=base or "the repository's HEAD")
    extra: Dict[str, str] = {}
    if stage == "implement":
        extra = {
            "repair": _repair_note(repair),
            "spec": _SPEC_NOTE if spec_check else "",
        }
    return template.format(
        requirement=requirement.strip(),
        plan=(plan.strip() or "(no plan recorded)"),
        allowed=note,
        workspace=where,
        amend=_amend_note(amend),
        approval=_approval_note(approvals),
        **extra
    )


def _repair_note(repair: Optional[Dict[str, Any]]) -> str:
    """The failure report a retry is handed, or nothing when this is a first try.

    @param repair  ``{"n":.., "failure": <failure.md>, "diagnosis": <diagnosis.md>}``
    """
    if not repair:
        return ""
    diagnosis = str(repair.get("diagnosis") or "").strip()
    return _REPAIR_NOTE.format(
        n=repair.get("n", 1),
        failure=str(repair.get("failure") or "(the report is missing)").strip(),
        diagnosis=_DIAGNOSIS_BLOCK.format(diagnosis=diagnosis) if diagnosis else "",
    )


def _approval_note(conditions: Sequence[Dict[str, Any]]) -> str:
    """The conditions attached to this slice's approvals, or nothing when there are none.

    Empty is byte-for-byte the prompt as it was before conditions existed, which
    is what keeps an ordinary ``approved`` from changing anything at all.

    @param conditions  what ``approval_conditions`` returned
    """
    if not conditions:
        return ""
    lines = [
        "- {0}: {1}".format(entry.get("stage") or "?", str(entry.get("comment") or "").strip())
        for entry in conditions
    ]
    return _APPROVAL_NOTE.format(conditions="\n".join(lines))


def _replan_note(replan: Optional[Dict[str, Any]]) -> str:
    """The rejected plan and the rejection, for a plan stage that is running again.

    @param replan  ``{"previous_plan": <text>, "rejection": {...}}``
    """
    if not replan:
        return ""
    rejection = replan.get("rejection")
    rejection = rejection if isinstance(rejection, dict) else {}
    grade = str(rejection.get("grade") or GRADE_PARTIAL)
    return _REPLAN_NOTE.format(
        plan=str(replan.get("previous_plan") or "(the previous plan was not kept)").strip(),
        reason=str(rejection.get("reason") or "(no reason given)").strip(),
        grade=grade,
        detail=str(rejection.get("detail") or "").strip(),
        instruction=_REPLAN_FULL if grade == GRADE_FULL else _REPLAN_PARTIAL,
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
    # Per-stage model overrides from ``model:``; empty means cfg.model everywhere.
    stage_models: Dict[str, str] = field(default_factory=dict)
    # v0.5 switches, all defaulting to the new behaviour. Every one of them has a
    # flag that puts the v0.4 behaviour back, because none of them is a judgement
    # about the user's project.
    spec_check: bool = True
    verify_engine: bool = True
    output_diet: bool = True
    write_guard: bool = True
    max_repairs: int = DEFAULT_MAX_REPAIRS
    diagnose_threshold: int = DEFAULT_DIAGNOSE_THRESHOLD
    verify_timeout: float = verify.VERIFY_TIMEOUT_S

    @property
    def cwd(self) -> Path:
        """Where Claude actually runs: the worktree when isolated, the repo otherwise."""
        return self.workspace.path if self.workspace is not None else self.repo

    def turns_for(self, stage: str) -> int:
        """This stage's turn budget: its own override, its default, then the base.

        @param stage  the stage name, including 'diagnose' which is not a slice stage
        """
        return self.stage_max_turns.get(stage) or STAGE_TURN_DEFAULTS.get(stage) or self.max_turns

    def model_for(self, stage: str) -> Optional[str]:
        """Which model this stage runs on. ``model:`` names it, or everything shares one.

        @param stage  the stage name
        """
        return self.stage_models.get(stage) or self.model

    def engine_verifies(self) -> bool:
        """Does the engine run verification itself, or does an agent test stage?

        A slice that declared no ``test_commands:`` has nothing for the engine to
        run, so it keeps v0.4's agent stage rather than being handed a guess.
        """
        return bool(self.verify_engine and self.test_commands)


def verify_wrapper_command() -> Optional[str]:
    """``aidev verify`` when ``aidev`` is on PATH, else None - the diet needs the binary.

    A stage cannot be granted a rule for a command that does not exist, so when
    the tool is not installed as an entry point the diet is simply off and the
    raw commands are granted, exactly as in v0.4.
    """
    return "aidev verify" if shutil.which("aidev") else None


# What the diet grants instead of the declared commands: one wrapper, in both
# rule forms for the same reason every other command is granted in both.
DIET_TOOLS: Tuple[str, ...] = ("Bash(aidev verify)", "Bash(aidev verify:*)")
# So implement can check the 함수 명세 convention on itself before verify does.
SPEC_CHECK_TOOLS: Tuple[str, ...] = (
    "Bash(python -m aidev.specs)",
    "Bash(python -m aidev.specs:*)",
)


def allowed_tools_for(stage: str, cfg: PipelineConfig) -> Tuple[str, ...]:
    """The permission rules this stage is granted, in the order they were added.

    Empty for a stage that runs no commands, which is what keeps plan readonly.
    The same tuple feeds both ``--allowedTools`` and the prompt, so what the
    stage is told it may run can never drift from what it actually may run.

    @param stage  which stage is being granted
    @param cfg    the slice's configuration - declared commands, diet, spec check
    @flow  not a command stage -> () ; diet -> the wrapper ; declared -> those ; else built-ins
    주요 내부 변수: declared(이 단계가 받는 기본 규칙 묶음), rules(중복 제거된 최종 목록)
    """
    if stage not in STAGES_WITH_TEST_COMMANDS:
        return ()
    rules: List[str] = []
    if stage == "implement" and diet_is_on(cfg):
        # The whole diet: the session may run the wrapper and not the suite, so
        # what comes back into its context is a summary and never 233 test names.
        declared = DIET_TOOLS
    elif cfg.test_commands:
        # Declared: only these. The measured defect is exactly the setup-derived
        # rule crowding out the real test command until the agent believed the
        # project could not be verified at all.
        declared = tuple(
            rule for command in cfg.test_commands for rule in command_tool_rules(command)
        )
    else:
        declared = tuple(TEST_COMMAND_TOOLS) + setup_tool_rules(cfg.setup_command)
    if stage == "implement" and cfg.spec_check:
        declared += SPEC_CHECK_TOOLS
    # --allow-tool is an explicit human override in either branch, never dropped.
    for rule in declared + tuple(cfg.allow_tools):
        rule = str(rule).strip()
        if rule and rule not in rules:
            rules.append(rule)
    return tuple(rules)


def diet_is_on(cfg: PipelineConfig) -> bool:
    """Is implement's verification going through the summarising wrapper?

    @param cfg  the slice's configuration
    """
    return bool(cfg.output_diet and cfg.test_commands and verify_wrapper_command())


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
    # Environment for the child, and the settings file carrying this slice's hooks.
    env: Dict[str, str] = field(default_factory=dict)
    settings: Optional[str] = None

    @classmethod
    def for_slice(
        cls, stage: str, cfg: "PipelineConfig", rec: Optional["SliceRecord"] = None
    ) -> "StageSpec":
        """How one slice stage runs: its policy, its rules, its hooks, its environment.

        @param stage  the stage name
        @param cfg    the slice's configuration
        @param rec    the slice record, needed to place the hook file and the verify logs
        @flow  policy lookup -> allowed rules -> write_guard settings -> diet environment
        주요 내부 변수: env(자식 프로세스 환경), settings(훅 설정 파일 경로)
        """
        env: Dict[str, str] = {}
        settings: Optional[str] = None
        if rec is not None:
            if stage in STAGES_WITH_TEST_COMMANDS:
                path = write_hook_settings(cfg, rec)
                settings = str(path) if path is not None else None
            if stage == "implement" and diet_is_on(cfg):
                env = {
                    "AIDEV_VERIFY_COMMANDS": json.dumps(list(cfg.test_commands)),
                    "AIDEV_VERIFY_CWD": str(cfg.cwd),
                    "AIDEV_VERIFY_LOG_DIR": str(rec.verify_dir),
                }
        return cls(
            stage=stage,
            policy=STAGE_POLICY.get(stage, STAGE_POLICY["implement"]),
            phase=stage,
            allowed=allowed_tools_for(stage, cfg),
            env=env,
            settings=settings,
        )


def execute_stage(
    cfg: PipelineConfig,
    rec: SliceRecord,
    stage: str,
    prompt: str,
    attempt: int,
    resume_session: Optional[str] = None,
    spec: Optional[StageSpec] = None,
    state: Optional[Dict[str, Any]] = None,
) -> StageRun:
    """One stage = one ``runner.execute()``, stored exactly like ``aidev run`` stores it.

    @param cfg             the slice's configuration - model, turns, tool rules
    @param rec             the slice record, for the run store and the log paths
    @param stage           which stage this is
    @param prompt          the whole prompt, already assembled
    @param attempt         which attempt of this stage, counting from 1
    @param resume_session  a session id to ``--resume``, or None for a fresh one
    @param spec            a pre-built StageSpec, for the stages outside STAGES
    @param state           state.json, so a progress snapshot can be recorded live
    @flow  spec -> RunConfig -> runner.execute (turn watcher on_event) -> StageRun
    주요 내부 변수: policy(안전 프로파일), wrote_progress(스냅샷을 이미 썼는지)
    """
    spec = spec or StageSpec.for_slice(stage, cfg, rec)
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
        model=cfg.model_for(spec.stage),
        permission_mode=permission_mode,
        # Rule-scoped, never a blanket Bash unlock: acceptEdits alone would leave
        # every verification command waiting for an approval nobody is there to give.
        allowed_tools=",".join(spec.allowed) or None,
        safety_profile=policy["safety_profile"] or "default",
        resume_session=resume_session,
        claude_cmd=list(cfg.claude_cmd),
        extra_args=list(cfg.extra_args),
        settings_path=spec.settings,
        env=dict(spec.env),
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

        budget = cfg.turns_for(spec.stage)
        wrote_progress = {"done": False}

        def on_event(event: Dict[str, Any], tel: Telemetry) -> None:
            capture.feed(event)
            is_result = event.get("type") == "result"
            snapshot = tel.snapshot()
            live.update(snapshot, force=is_result)
            store.write_live(snapshot, force=is_result)
            # The stage cannot be told it is running out of turns - there is no
            # channel into a headless session - so the engine writes down what it
            # has observed instead, while there are still turns left to observe.
            if progress.should_snapshot(int(snapshot.get("turn") or 0), budget, wrote_progress["done"]):
                wrote_progress["done"] = True
                write_progress(
                    cfg,
                    rec,
                    spec.stage,
                    attempt,
                    tel.to_dict(),
                    capture.text(),
                    progress.REASON_BUDGET,
                    turn=int(snapshot.get("turn") or 0),
                    budget=budget,
                    state=state,
                )

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
    repair: Optional[Dict[str, Any]] = None,
    replan: Optional[Dict[str, Any]] = None,
) -> StageRun:
    """Run one stage, waiting out usage limits on the same session as it goes.

    @param cfg          the slice's configuration
    @param rec          the record whose state and runs.json this writes
    @param state        the state dict, updated in place
    @param stage        which stage to run
    @param requirement  the requirement body, injected into the prompt
    @param spec         how to run it; the default is the slice-stage lookup
    @param amend        the open amend cycle, if any
    @param repair       a failed verification this run must fix, if any
    @param replan       the rejected plan and its rejection, for a re-plan
    @flow  session policy -> build prompt (+approval conditions) -> execute -> quota? wait and retry : return
    주요 내부 변수: attempt(회차), session(이어붙일 세션), retries(쿼터 재시도 수)
    """
    spec = spec or StageSpec.for_slice(stage, cfg, rec)
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
            repair=repair,
            replan=replan,
            spec_check=cfg.spec_check,
            diet=diet_is_on(cfg),
            # Rebuilt every time round this loop, so a quota retry, a resume, a
            # repair and an amend all carry the condition without asking for it.
            approvals=approval_conditions(state),
        )
        if session:
            prompt += _RESUME_NOTE.format(stage=stage)
        elif fresh:
            prompt += _FRESH_SESSION_NOTE
        prompt += resume_progress_note(rec, state, stage)
        banner(stage, rec.label, attempt, resumed=bool(session), fresh=fresh)

        run = execute_stage(
            cfg, rec, stage, prompt, attempt, resume_session=session, spec=spec, state=state
        )
        record_run(rec, run)
        if run.session_id:
            entry["session_id"] = run.session_id
            session = run.session_id

        if not run.quota:
            if run.ok:
                entry["failures"] = 0
                if isinstance(state.get("quota"), dict):
                    state["quota"].update({"waiting": False, "resume_at": None})
            else:
                # A stage that stopped without finishing is exactly the case the
                # resume has nothing to go on. Rewritten, so it reflects the end.
                write_progress(
                    cfg,
                    rec,
                    stage,
                    attempt,
                    run.telemetry,
                    run.text,
                    progress.REASON_INCOMPLETE,
                    turn=int((run.telemetry.get("exact") or {}).get("num_turns") or 0),
                    budget=cfg.turns_for(spec.stage),
                    state=state,
                )
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


# ------------------------------------------------------------------ progress
#
# The requirement asked the engine to *signal* a session at 90% of its budget so
# the session writes its own progress.md. A headless ``claude -p`` has no such
# channel - there is no way to speak to a session that is already running - so
# the allowed alternative is taken: the engine writes the file from what
# telemetry already observed. It needs no cooperation, which is also why it
# still works for a session that died mid-sentence.


def write_progress(
    cfg: PipelineConfig,
    rec: Any,
    stage: str,
    attempt: int,
    telemetry: Optional[Dict[str, Any]],
    last_text: str,
    reason: str,
    turn: int = 0,
    budget: int = 0,
    state: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Write progress.md for a stage that is about to run out, or already has.

    @param cfg        the slice's configuration, for the repo path in the resume line
    @param rec        the record whose directory the file lands in
    @param stage      the stage being recorded
    @param attempt    which attempt of it
    @param telemetry  the telemetry dict the 'Done' list is read from
    @param last_text  what the session last said
    @param reason     why this was written
    @param turn       turns used at this moment
    @param budget     the stage's turn budget
    @param state      state.json, so a resume can tell whether this file is stale
    @flow  plan paths -> progress.render -> write -> record it in state
    주요 내부 변수: path(progress.md 경로), plan_paths(plan.md가 지목한 경로들)
    """
    path = Path(rec.dir) / "progress.md"
    plan_text = rec.read_plan() if hasattr(rec, "read_plan") else ""
    plan_paths = [match.group(0) for match in _PLAN_PATH_RE.finditer(plan_text or "")]
    try:
        write_text_atomic(
            path,
            progress.render(
                slice_id=rec.label,
                stage=stage,
                attempt=attempt,
                telemetry=telemetry,
                plan_paths=list(dict.fromkeys(plan_paths)),
                last_text=last_text,
                reason=reason,
                turn=turn,
                budget=budget,
                repo=str(cfg.repo),
            ),
        )
    except OSError as exc:
        say("note: could not write progress.md ({0})".format(exc))
        return None
    if state is not None:
        state["progress"] = {
            "stage": stage,
            "attempt": attempt,
            "at": now_iso(),
            "turn": turn,
            "budget": budget,
            "reason": reason,
            "path": str(path),
        }
        try:
            rec.write_state(state)
        except OSError:
            pass
    say("progress recorded: {0}  ({1})".format(path, reason))
    return path


def resume_progress_note(rec: Any, state: Dict[str, Any], stage: str) -> str:
    """The progress block a stage picking up unfinished work is given, if there is one.

    Only for the stage that wrote it, and only while that stage is unfinished -
    injecting a done stage's notes would describe work that is already committed.

    @param rec    the record holding progress.md
    @param state  state.json, which says which stage the note belongs to
    @param stage  the stage about to run
    """
    record = state.get("progress")
    if not isinstance(record, dict) or record.get("stage") != stage:
        return ""
    if stage_entry(state, stage).get("status") == "done":
        return ""
    text = progress.read(Path(rec.dir) / "progress.md").strip()
    return _PROGRESS_NOTE.format(progress=text) if text else ""


# ------------------------------------------------------------- the write guard


def guard_script_path() -> Optional[Path]:
    """Where the PreToolUse write guard lives, or None if this install lacks it."""
    path = Path(__file__).resolve().parent / "hooks" / "write_guard.py"
    return path if path.is_file() else None


def write_hook_settings(cfg: PipelineConfig, rec: Any) -> Optional[Path]:
    """Write the settings file that arms the write guard, and return its path.

    Deliberately a file of the *slice's*, handed over with ``--settings``: the
    target repository's own ``.claude/settings.json`` is the user's and the
    pipeline has never written to it.

    @param cfg  the slice's configuration - ``write_guard`` decides whether to bother
    @param rec  the record whose hooks/ directory holds the file
    @flow  guard off or missing -> None ; else render settings.json -> path
    주요 내부 변수: script(가드 스크립트 경로), payload(설정 JSON)
    """
    if not cfg.write_guard:
        return None
    script = guard_script_path()
    if script is None:
        return None
    path = Path(rec.dir) / "hooks" / "settings.json"
    payload = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [
                        {
                            "type": "command",
                            "command": '"{0}" "{1}"'.format(sys.executable, script),
                            "timeout": 10,
                        }
                    ],
                }
            ]
        }
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, payload)
    except OSError as exc:
        say("note: could not arm the write guard ({0}) - Write stays unrestricted".format(exc))
        return None
    return path


# -------------------------------------------------------- branch bookkeeping


def history_dir(cfg: PipelineConfig, slice_id: str) -> Path:
    return Path(cfg.cwd) / AIDEV_DIRNAME / HISTORY_DIR / slice_id


def history_snapshot(rec: SliceRecord, state: Dict[str, Any]) -> Dict[str, Any]:
    """What the commit carries about the slice it belongs to.

    A projection, not a second source of truth: nothing reads it back. state.json
    in the user's checkout stays the one authority, with one writer.

    @param rec    the slice being projected
    @param state  state.json, the thing being projected
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
        # v0.5, all optional: what verification cost, what a human rejected, and
        # what was re-planned because of it.
        "repairs": state.get("repairs"),
        "replans": state.get("replans"),
        "rejections": state.get("rejections"),
        "stages": stages,
        "runs": runs,
        "updated_at": now_iso(),
    }


def mirror_history(cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any]) -> Path:
    """Project the slice's own record into the worktree, so the commit carries it.

    Rewritten before every stage commit, which is also how a plan.md edited by a
    human during the gate reaches the branch: the next stage commit carries it.

    @param cfg    the slice's configuration, for the worktree the mirror lives in
    @param rec    the slice whose documents are copied
    @param state  state.json, projected into slice.json beside them
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
    # The documents a failure leaves behind ride the branch too, so a human
    # reviewing the merge sees why it went the way it did without leaving git.
    for source in (rec.failure_path, rec.diagnosis_path, rec.progress_path):
        try:
            write_text_atomic(directory / source.name, source.read_text(encoding="utf-8"))
        except OSError:
            continue
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


# ------------------------------------------------------------- verify engine
#
# Measured 2026-08-16/17: the test stage was an agent session that re-ran a
# command and wrote one verdict line. Eight of eight passed - $0.4 each for zero
# information. The commands are declared, so the engine runs them: a pass costs
# no session at all, and the money a failure costs is spent on diagnosing it.
#
# The stage is still called 'test'. "verify" is the name of the *engine* that
# runs it, and it stays out of state.json's stage keys on purpose: stage_order,
# approvals/test.md, commits["test"], --to test and telemetry's phase list are
# all that string, and renaming it would break every one of them to gain a label.


def spec_base_commit(cfg: PipelineConfig, state: Dict[str, Any]) -> Optional[str]:
    """The commit this slice's diff is measured from, or None when there is none.

    The requirement commit first: it is exactly "before this slice touched
    anything". A legacy slice (v0.2, or --no-worktree) has neither that nor a
    workspace, and then the check is skipped rather than guessed at.

    @param cfg    the slice's configuration
    @param state  state.json, holding the commit map and the workspace record
    """
    commits = state.get("commits")
    if isinstance(commits, dict) and commits.get(REQUIREMENT_STAGE):
        return str(commits[REQUIREMENT_STAGE])
    ws = state.get("workspace")
    if isinstance(ws, dict) and ws.get("base_commit"):
        return str(ws["base_commit"])
    return None


def run_spec_check(cfg: PipelineConfig, state: Dict[str, Any]) -> List[Any]:
    """Check the 함수 명세 convention over everything this slice changed.

    @param cfg    the slice's configuration - ``spec_check`` may turn this off
    @param state  state.json, which knows what commit the slice started from
    @flow  off -> [] ; no commit range -> say so and [] ; else specs.check(base..HEAD)
    주요 내부 변수: base(비교 기준 커밋), head(현재 커밋)
    """
    if not cfg.spec_check:
        return []
    base = spec_base_commit(cfg, state)
    if not base:
        say("note: no commit range for this slice - spec check skipped")
        return []
    head = workspace.head_commit(cfg.cwd)
    if not head:
        say("note: this workspace has no commit yet - spec check skipped")
        return []
    try:
        return specs.check(cfg.cwd, base, head)
    except workspace.GitError as exc:
        # A checker that cannot read the diff must not be the reason a stage fails.
        say("note: spec check could not read the diff ({0}) - skipped".format(exc))
        return []


def record_verify(
    rec: SliceRecord, state: Dict[str, Any], attempt: int, result: verify.VerifyResult
) -> None:
    """Put one verification attempt into state.json, under the test stage.

    @param rec      the slice record, written through so a watcher sees it
    @param state    state.json
    @param attempt  which round this is
    @param result   what the engine measured
    """
    entry = stage_entry(state, "test")
    record = entry.get("verify")
    if not isinstance(record, dict):
        record = {"engine": True, "attempts": []}
        entry["verify"] = record
    record["engine"] = True
    attempts = record.setdefault("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
        record["attempts"] = attempts
    payload = result.to_dict()
    payload["n"] = attempt
    payload["at"] = now_iso()
    attempts.append(payload)
    state["test_verdict"] = "pass" if result.clean else "fail"
    entry["verdict"] = state["test_verdict"]
    rec.write_state(state)


def failure_grade(cfg: PipelineConfig, result: verify.VerifyResult) -> str:
    """'small' when failure.md is enough on its own, 'large' when it is not.

    Output nobody could parse counts as large: not being able to summarise a
    failure is exactly when a diagnosis session is worth what it costs.

    @param cfg     the slice's configuration - the threshold lives there
    @param result  the failed attempt
    """
    if not result.parsed and not result.spec_violations:
        return "large"
    count = result.failure_count()
    return "small" if 0 < count <= cfg.diagnose_threshold else "large"


def run_diagnosis(
    cfg: PipelineConfig,
    rec: SliceRecord,
    state: Dict[str, Any],
    requirement: str,
    failure_md: str,
) -> str:
    """One readonly session whose only output is diagnosis.md. Returns its text.

    Readonly on purpose: it cannot edit code and it cannot re-run the suite, so
    it can only read and answer - which is what makes it cheap enough to be
    worth summoning at all.

    @param cfg          the slice's configuration
    @param rec          the record diagnosis.md is written into
    @param state        state.json, where the run is recorded like any other
    @param requirement  the requirement body
    @param failure_md   the failure report, injected verbatim
    @flow  StageSpec(readonly, phase=repair, +approval conditions) -> run_stage -> save diagnosis.md
    주요 내부 변수: spec(진단 세션 설정), run(StageRun), text(모델의 최종 답)
    """
    spec = StageSpec(
        stage=DIAGNOSE_STAGE,
        policy={"safety_profile": "readonly", "permission_mode": None},
        # An existing telemetry phase, so `aidev stats` aggregates it without
        # knowing this step exists.
        phase="repair",
        allowed=(),
        prompt=_DIAGNOSE_PROMPT.format(
            failure=failure_md.strip(),
            requirement=requirement.strip(),
            plan=(rec.read_plan().strip() or "(no plan recorded)"),
            # A diagnosis of work done under a condition has to know the condition,
            # or it proposes a fix that reaches outside what was approved.
            approval=_approval_note(approval_conditions(state)),
        ),
    )
    run = run_stage(cfg, rec, state, DIAGNOSE_STAGE, requirement, spec=spec)
    entry = stage_entry(state, DIAGNOSE_STAGE)
    entry["status"] = "done" if run.ok else "failed"
    entry["run_id"] = run.run_id
    text = run.text.strip()
    if not text:
        say("the diagnosis session produced no text - continuing with failure.md alone")
        return ""
    write_text_atomic(rec.diagnosis_path, text + "\n")
    say("diagnosis saved: {0}".format(rec.diagnosis_path))
    return text


def run_verify(
    cfg: PipelineConfig,
    rec: SliceRecord,
    state: Dict[str, Any],
    requirement: str,
    amend: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Verify by running the commands, and on failure fix once instead of reporting.

    A pass spawns no session at all: the engine runs the commands, records the
    result and returns. A failure writes failure.md, grades itself, optionally
    buys a diagnosis, and sends implement back in with both documents. Past the
    repair limit those documents are the deliverable - a human reads them.

    @param cfg          the slice's configuration - commands, limits, timeouts
    @param rec          the record failure.md, diagnosis.md and the logs land in
    @param state        state.json, updated in place
    @param requirement  the requirement body, for the retry and the diagnosis
    @param amend        the open amend cycle, if any
    @flow  run -> spec check -> clean? return None : failure.md -> grade -> diagnose? -> implement -> repeat
    주요 내부 변수: attempt(회차), result(VerifyResult), grade(small|large)
    """
    attempt = 0
    while True:
        attempt += 1
        say("verify: {0}".format("  ".join(cfg.test_commands)))
        result = verify.run_commands(
            cfg.test_commands,
            cfg.cwd,
            log_dir=rec.verify_dir,
            timeout=cfg.verify_timeout,
            attempt=attempt,
        )
        # Always, whatever the commands said: a green suite over an undocumented
        # function is still a slice that broke the convention.
        result.spec_violations = list(run_spec_check(cfg, state))
        record_verify(rec, state, attempt, result)
        for command in result.commands:
            say("  {0}  exit {1}".format(command.command, command.exit_code))
        if result.clean:
            say("verify passed ({0} test(s), no session spent)".format(result.passed))
            return None

        head = workspace.head_commit(cfg.cwd) or ""
        failure_md = verify.render_failure_md(
            result,
            slice_id=rec.slice_id,
            attempt=attempt,
            last_commit=head,
            last_subject=(workspace.log_subjects(cfg.cwd, "-1") or [""])[-1] if head else "",
        )
        write_text_atomic(rec.failure_path, failure_md)
        say("verify FAILED - {0}".format(rec.failure_path))
        say_lines(verify.summarize_for_agent(result))

        if attempt > cfg.max_repairs:
            note_stage_failure(state, "test")
            return (
                "verification failed after {0} repair attempt(s).\n"
                "    {1}\n"
                "    {2}".format(
                    cfg.max_repairs,
                    rec.failure_path,
                    rec.diagnosis_path if rec.diagnosis_path.exists() else "(no diagnosis)",
                )
            )

        grade = failure_grade(cfg, result)
        say("failure grade: {0} ({1} failing)".format(grade, result.failure_count()))
        diagnosis = ""
        if grade == "large":
            diagnosis = run_diagnosis(cfg, rec, state, requirement, failure_md)
        repairs = state.setdefault("repairs", [])
        if not isinstance(repairs, list):
            repairs = []
            state["repairs"] = repairs
        repairs.append(
            {
                "n": attempt,
                "grade": grade,
                "at": now_iso(),
                "failed": result.failure_count(),
                "spec_violations": len(result.spec_violations),
                "diagnosis": bool(diagnosis),
            }
        )

        # implement runs again on the same record: a new attempt of the same
        # stage, committed under its own label so the first one survives beside it.
        entry = stage_entry(state, "implement")
        entry["status"] = "pending"
        entry.pop("session_id", None)  # it concluded 'done'; that memory is now wrong
        rec.write_state(state)
        run = run_stage(
            cfg,
            rec,
            state,
            "implement",
            requirement,
            amend=amend,
            repair={"n": attempt, "failure": failure_md, "diagnosis": diagnosis},
        )
        if not run.ok:
            entry["status"] = "failed"
            note_stage_failure(state, "implement")
            return "repair {0} failed: stage 'implement' {1} (run {2}, exit {3})\n    {4}".format(
                attempt, run.status, run.run_id, run.exit_code, rec.failure_path
            )
        entry.update({"status": "done", "run_id": run.run_id})
        reason = commit_stage(
            cfg, rec, state, "implement", label=REPAIR_LABEL.format(attempt, "implement")
        )
        if reason is not None:
            return reason
        rec.write_state(state)


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

    @param cfg       the slice's configuration, for the poll interval and timeout
    @param rec       the record whose approvals directory is watched
    @param state     state.json, where the decision and any rejection are recorded
    @param stage     which gate this is
    @param artifact  what the human may edit before deciding
    @param note      advice rendered into the template as comments
    @flow  read -> write template -> poll -> approved (+condition) | rejected (+ rejection record)
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
        say("  write 'approved', 'approved: <condition>' or 'rejected: <reason>' on its own line")
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
    if decision.verdict == APPROVED and decision.reason:
        # Said out loud at the moment it is written down, so the human sees that
        # the condition was taken rather than trusting it was.
        say("approval condition: {0}".format(decision.reason))
        say("  it goes into every stage, retry and repair prompt of this slice")
    if decision.verdict == REJECTED:
        # Append-only, and optional like every key added after schema 2: what was
        # rejected, why, and how far it reaches - the input --replan reads.
        rejection = read_rejection(rec, stage, decision)
        rejections = state.setdefault("rejections", [])
        if isinstance(rejections, list):
            rejections.append(rejection)
        entry["rejection_grade"] = rejection["grade"]
        if rejection["path"]:
            say("rejection detail: {0}  ({1})".format(rejection["path"], rejection["grade"]))
    rec.write_state(state)
    say("gate '{0}': {1}".format(stage, decision.verdict))
    return decision


def finish_stage(
    cfg: PipelineConfig, rec: SliceRecord, state: Dict[str, Any], run: StageRun, before: Optional[str]
) -> Optional[str]:
    """Stage-specific bookkeeping. Returns a reason to fail the slice, or None.

    @param cfg     the slice's configuration
    @param rec     the record plan.md and any failure.md are written into
    @param state   state.json, where the verdict and the plan scale land
    @param run     what the stage actually did
    @param before  ``git status`` from before a readonly stage, or None
    @flow  safety violations -> plan (readonly proof, save) -> test (verdict, spec check)
    """
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
        # The convention is checked on this path too: the engine is not what
        # makes it a rule, the diff is.
        violations = run_spec_check(cfg, state)
        if violations:
            state["test_verdict"] = "fail"
            entry["verdict"] = "fail"
            result = verify.VerifyResult(ok=True, spec_violations=list(violations))
            write_text_atomic(
                rec.failure_path,
                verify.render_failure_md(result, slice_id=rec.slice_id, attempt=1),
            )
            return "{0} function(s) changed without their spec keeping up:\n{1}\n    {2}".format(
                len(violations),
                "\n".join("    " + str(v) for v in violations[:20]),
                rec.failure_path,
            )
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
    """The single loop: run the stage if it is not done, then ask about its gate.

    @param cfg          the slice's configuration
    @param rec          the slice record - state.json's single writer is this loop
    @param state        state.json, updated in place
    @param requirement  the requirement body every stage is given
    @flow  per stage: dirty check -> setup? -> run (or verify engine) -> commit -> gate
    주요 내부 변수: gates(켜진 승인 게이트), amend(열려 있는 amend 사이클)
    """
    # The last safety pin for the empty-requirement guard: every entry point
    # passes through here, so a body nobody checked cannot reach a session.
    if not (requirement or "").strip():
        raise PipelineError(
            "requirement body is empty for slice {0} - there is nothing to plan "
            "from.\n    Write the requirement into {1}".format(
                rec.slice_id, rec.requirement_path
            )
        )
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
            if stage == "test" and cfg.engine_verifies():
                # No session at all on the pass path: the engine runs the declared
                # commands itself, and only spends money when something failed.
                reason = run_verify(cfg, rec, state, requirement, amend=amend)
                if reason is not None:
                    entry["status"] = "failed"
                    return fail_slice(rec, state, reason)
                entry["status"] = "done"
            else:
                # Only the plan stage is re-planning, and only until it produces
                # the plan that replaces the rejected one.
                replan = state.get("replan_open") if stage == "plan" else None
                try:
                    run = run_stage(
                        cfg, rec, state, stage, requirement, amend=amend, replan=replan
                    )
                except PipelineError as exc:
                    # The run never produced a result, so leave the slice failed
                    # rather than frozen at running:<stage>.
                    entry["status"] = "failed"
                    fail_slice(rec, state, str(exc))
                    raise
                if not run.ok:
                    entry["status"] = "failed"
                    entry["run_id"] = run.run_id
                    # Quota is excluded: a stage abandoned on a usage limit reached
                    # no conclusion of its own, so its session is worth resuming.
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
                    # The run itself succeeded but the stage did not - a
                    # TEST_RESULT: FAIL lands here, and that is a conclusion too.
                    note_stage_failure(state, stage)
                    return fail_slice(rec, state, reason)
            # A stage that finished has nothing left to resume into, so its
            # progress notes stop being injected - the file itself stays.
            if (state.get("progress") or {}).get("stage") == stage:
                state.pop("progress", None)
            if stage == "plan":
                # The new plan is written: the rejected one is history now.
                state.pop("replan_open", None)
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

    @param state  state.json, which names the stages and what they decided
    @param runs   the stored runs, where the tokens and the cost come from
    """
    row = "{0:<11}{1:<9}{2:>4}{3:>7}{4:>11}{5:>11}{6:>10}".format
    lines = ["", "PIPELINE  {0}".format(state.get("slice_id", "?")), reporter.rule()]
    lines.append(row("Stage", "Status", "Try", "Turns", "Peak ctx", "Output", "Cost"))

    totals = {"turns": 0, "output": 0, "cost": 0.0, "attempts": 0}
    # Plus any stage that is not in stage_order: 'diagnose' is a real run with a
    # real bill, and a summary that hid it would understate what the slice cost.
    shown = stage_order(state) + [
        name for name in (state.get("stages") or {}) if name not in stage_order(state)
    ]
    for stage in shown:
        attempts = [entry for entry in runs if str(entry.get("stage")) == stage]
        status = str(stage_entry(state, stage).get("status", "pending"))
        if not attempts:
            engine = stage_entry(state, stage).get("verify")
            if isinstance(engine, dict):
                # The engine ran it: no session, so no tokens and no cost. The
                # try count is how many verification rounds it took.
                lines.append(
                    row(stage, status, len(engine.get("attempts") or []), "-", "-", "-", "$0.0000")
                )
                continue
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
        lines.extend(_verify_lines(state, note.get(verdict, ""), verdict))
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


def _verify_lines(state: Dict[str, Any], note: str, verdict: Any) -> List[str]:
    """The Tests line, plus what the engine actually ran when it was the engine.

    @param state    state.json
    @param note     the marker the verdict earns, e.g. '<- tests failed'
    @param verdict  pass / fail / unknown
    """
    lines = ["Tests     {0}{1}".format(str(verdict).upper(), note)]
    record = stage_entry(state, "test").get("verify")
    if not isinstance(record, dict):
        return lines
    attempts = [a for a in (record.get("attempts") or []) if isinstance(a, dict)]
    last = attempts[-1] if attempts else {}
    for command in last.get("commands") or []:
        lines.append(
            "Verify    {0}  exit {1}  ({2} passed)".format(
                command.get("command", "?"), command.get("exit_code"), last.get("passed", 0)
            )
        )
    for violation in (last.get("spec_violations") or [])[:5]:
        lines.append("Spec      {0}".format(violation))
    repairs = [r for r in (state.get("repairs") or []) if isinstance(r, dict)]
    if repairs:
        lines.append(
            "Repairs   {0}  (diagnosis: {1})".format(
                len(repairs), "yes" if any(r.get("diagnosis") for r in repairs) else "no"
            )
        )
    return lines


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
    """Register the ``aidev pipeline`` subcommand and all of its flags.

    @param sub  the subparsers object from ``cli.build_parser``
    """
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
        "--model-stage",
        action="append",
        default=[],
        dest="stage_models",
        metavar="STAGE=MODEL",
        help="model for one stage, e.g. --model-stage diagnose=claude-haiku-4-5 (repeatable)",
    )
    cmd.add_argument(
        "--replan",
        default=None,
        metavar="SLICE",
        help="re-run the plan stage of a rejected slice, with the rejection injected",
    )
    cmd.add_argument(
        "--max-repairs",
        type=int,
        default=DEFAULT_MAX_REPAIRS,
        metavar="N",
        help="how many times a failed verification may send implement back in (default 1)",
    )
    cmd.add_argument(
        "--diagnose-threshold",
        type=int,
        default=DEFAULT_DIAGNOSE_THRESHOLD,
        metavar="N",
        help="more failures than this buys a diagnosis session (default 3)",
    )
    cmd.add_argument(
        "--verify-timeout",
        type=float,
        default=verify.VERIFY_TIMEOUT_S,
        metavar="S",
        help="seconds one verification command may take (default 1800)",
    )
    cmd.add_argument(
        "--no-verify-engine",
        action="store_true",
        help="v0.4 behaviour: the test stage is an agent session, not the engine",
    )
    cmd.add_argument(
        "--no-write-guard",
        action="store_true",
        help="do not block Write on files that already exist",
    )
    cmd.add_argument(
        "--no-output-diet",
        action="store_true",
        help="give implement the raw test commands instead of the 'aidev verify' wrapper",
    )
    cmd.add_argument(
        "--no-spec-check",
        action="store_true",
        help="do not check that changed functions carry a spec comment",
    )
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
    """Pick the one command the flags asked for, and refuse two at once.

    @param args  the parsed arguments
    @flow  resolve repo -> refuse combined modes -> hand off to that command
    주요 내부 변수: modes(각 명령의 (attr, 플래그) 쌍), chosen(실제로 주어진 것들)
    """
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
        ("replan", "--replan"),
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
    if getattr(args, "replan", None):
        return replan_slice(args, repo, data_dir, repo_given)
    if args.resume_slice:
        return resume_slice(args, repo, data_dir, repo_given)
    if getattr(args, "resume_epic", None):
        return epic_module.resume_epic(args, repo, data_dir, repo_given)
    if args.requirement:
        return start_slice(args, repo, data_dir)
    if getattr(args, "epic", None):
        return epic_module.start_epic(args, repo, data_dir)
    raise PipelineError(
        "one of --requirement, --epic, --amend, --replan, --rollback, --revert-merge, "
        "--resume-slice or --list is required"
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
    what it got before. The ``model:`` line resolves by the same precedence.

    @param args           the parsed arguments
    @param repo           the user's repository
    @param data_dir       where runs are stored
    @param ws             the slice's workspace, when it has one
    @param setup_command  the declared setup command, if any
    @param fields         the requirement's front matter
    @flow  warn about unknown keys -> turns (CLI > file) -> models (same) -> PipelineConfig
    주요 내부 변수: stage_turns / stage_models(단계별 덮어쓰기)
    """
    fields = fields or {}
    # Every launch path - start, resume, amend, replan, dry-run - passes through
    # here exactly once with the front matter in hand, so one line here is one
    # warning per run rather than one per reader.
    say_lines(warn_unknown_front_matter(fields))
    front_base, front_stage = resolve_max_turns(fields)
    _, cli_stage = parse_turn_tokens(
        getattr(args, "stage_turns", None) or [],
        TURN_STAGES,
        "--max-turns-stage",
        allow_base=False,
    )
    stage_turns = dict(front_stage)
    stage_turns.update(cli_stage)
    cli_base = getattr(args, "max_turns", None)
    base = cli_base if cli_base is not None else front_base
    # Same precedence for the model: CLI stage > front-matter stage > CLI base >
    # front-matter base > whatever the CLI defaults to.
    front_model, front_stage_models = resolve_models(fields)
    _, cli_stage_models = parse_model_tokens(
        getattr(args, "stage_models", None) or [], MODEL_STAGES, "--model-stage", allow_base=False
    )
    stage_models = dict(front_stage_models)
    stage_models.update(cli_stage_models)
    return PipelineConfig(
        repo=repo,
        data_dir=data_dir,
        max_turns=DEFAULT_MAX_TURNS if base is None else base,
        stage_max_turns=stage_turns,
        model=args.model if args.model is not None else front_model,
        stage_models=stage_models,
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
        spec_check=resolve_spec_check(fields) and not getattr(args, "no_spec_check", False),
        verify_engine=not getattr(args, "no_verify_engine", False),
        output_diet=not getattr(args, "no_output_diet", False),
        write_guard=not getattr(args, "no_write_guard", False),
        max_repairs=getattr(args, "max_repairs", DEFAULT_MAX_REPAIRS),
        diagnose_threshold=getattr(args, "diagnose_threshold", DEFAULT_DIAGNOSE_THRESHOLD),
        verify_timeout=getattr(args, "verify_timeout", verify.VERIFY_TIMEOUT_S),
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
    """``--requirement``: the ordinary launch, and the ``--dry-run`` that previews it.

    @param args      the parsed arguments
    @param repo      the user's repository
    @param data_dir  where runs are stored
    @flow  read + guard the requirement -> dry-run? print and stop -> launch_slice
    """
    source = resolve_requirement(args.requirement, repo)
    text = source.read_text(encoding="utf-8")
    # Measured 2026-08-17: an empty requirement bought a worktree, a plan stage
    # and a gate before anyone noticed. Refused at the moment of launch instead.
    guard_requirement(text, str(source))

    if args.dry_run:
        fields, _ = parse_front_matter(text)
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
        print("verify    {0}".format(
            "engine: {0}".format(", ".join(test_commands))
            if turns.engine_verifies()
            else "agent (no test_commands declared)"
        ))
        print("stages    {0}".format(" -> ".join(STAGES)))
        print("turns     {0}".format(
            "  ".join("{0}={1}".format(name, turns.turns_for(name)) for name in STAGES)
        ))
        print("models    {0}".format(
            "  ".join(
                "{0}={1}".format(name, turns.model_for(name) or "(cli default)")
                for name in STAGES
            )
        ))
        print("guards    write-guard {0}, spec-check {1}, output-diet {2}".format(
            "on" if turns.write_guard else "off",
            "on" if turns.spec_check else "off",
            "on" if diet_is_on(turns) else "off",
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

    @param args              the parsed arguments
    @param repo              the user's repository
    @param data_dir          where runs are stored
    @param text              the requirement file, front matter and all
    @param name              what to call it in errors and in the slice id
    @param base              the branch to cut from, or None for the repo's HEAD
    @param start             an explicit commit to start at, for a queued slice
    @param epic              the epic this slice belongs to, when it has one
    @param quiet_unfinished  suppress the "other slices are open" notice, for a queue
    @flow  guard + validate front matter -> slice record -> workspace -> run_pipeline
    """
    body = guard_requirement(text, name)
    fields, _ = parse_front_matter(text)
    gates = resolve_gates(fields)
    setup_command = resolve_setup(fields)
    # Read up front, so a typo in any of these is refused before a worktree exists.
    resolve_test_commands(fields)
    resolve_max_turns(fields)
    resolve_models(fields)
    resolve_spec_check(fields)

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


def _broken_workspace_error(
    repo: Path, rec: SliceRecord, ws: workspace.Workspace, broken: str
) -> PipelineError:
    """Refuse a workspace that cannot be used, with the commands that get out of it.

    Both callers - resume and amend - say exactly this, so they say it from one
    place: two copies of the same paragraph are two copies to keep in step, and
    ``worktree repair`` had to be added to both.

    @param repo    the user's repository
    @param rec     the slice whose workspace this is
    @param ws      the recorded workspace
    @param broken  what ``workspace.verify`` said is wrong with it
    """
    return PipelineError(
        "{0}\n"
        "    aidev does not re-create it: the stage commits already on '{1}' were made "
        "there.\n"
        "    Try to restore it:  git -C {2} worktree repair {3}\n"
        "    Or give it up:      aidev pipeline --repo {2} --discard {4}".format(
            broken, ws.branch, repo, ws.path, rec.slice_id
        )
    )


def continue_slice(args: Any, repo: Path, data_dir: Path, rec: SliceRecord) -> int:
    """Pick a stopped slice up where it stopped. The queue resumes slices this way too.

    @param args      the parsed arguments
    @param repo      the user's repository
    @param data_dir  where runs are stored
    @param rec       the slice to continue, already located
    @flow  refuse finished states -> re-guard the requirement -> verify workspace -> _finish
    """
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

    text = rec.requirement_path.read_text(encoding="utf-8")
    # The same guard as at launch: a requirement that was emptied between two
    # runs must not be resumed into a session that has nothing to work from.
    body = guard_requirement(text, rec.slice_id)
    fields, _ = parse_front_matter(text)
    setup_command = resolve_setup(fields)
    ws = workspace.Workspace.from_dict(state.get("workspace"))

    if ws is None:
        # A slice started before v0.3 has no workspace key, and re-creating one
        # now would put its remaining stages somewhere its earlier ones never were.
        say("note: this slice has no workspace - continuing in {0} (v0.2 behaviour)".format(repo))
    else:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            raise _broken_workspace_error(repo, rec, ws, broken)

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
    """Run one more implement -> test cycle on a slice, on the branch it already owns.

    @param args       parsed arguments - ``--amend <slice> "<instruction>"``
    @param repo       the user's repository
    @param data_dir   where runs are stored
    @param repo_given whether --repo was named, for the "no slices here" hint
    @flow  instruction -> find slice -> re-guard the requirement -> open the cycle -> _finish
    """
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

    text = rec.requirement_path.read_text(encoding="utf-8")
    body = guard_requirement(text, rec.slice_id)
    fields, _ = parse_front_matter(text)
    setup_command = resolve_setup(fields)
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is None:
        # Same as a resume: a slice from before v0.3 is amended where its earlier
        # stages actually ran, not in a worktree invented after the fact.
        say("note: this slice has no workspace - amending in {0} (v0.2 behaviour)".format(repo))
    else:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            raise _broken_workspace_error(repo, rec, ws, broken)

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


# ------------------------------------------------------------------- replan
#
# 차분 재계획. A rejected plan is not a dead slice: almost every rejection names
# one thing that is wrong with an otherwise sound plan. Resuming a rejected slice
# deliberately does not re-plan - it reads the same approval file and stops
# again, which is the protection - so re-planning is its own command, a human
# types it, and the plan stage is handed its own rejected plan plus the reason.


def latest_rejection(state: Dict[str, Any], stage: str = "plan") -> Dict[str, Any]:
    """The most recent rejection of one stage, from the ledger or from the stage entry.

    A slice rejected before this ledger existed still answers: the stage's own
    ``approval_reason`` has always been there and is read as the fallback.

    @param state  state.json
    @param stage  which gate's rejection to find
    """
    for entry in reversed(state.get("rejections") or []):
        if isinstance(entry, dict) and entry.get("stage", stage) == stage:
            return dict(entry)
    legacy = stage_entry(state, stage)
    return {
        "stage": stage,
        "reason": str(legacy.get("approval_reason") or ""),
        "detail": "",
        "grade": GRADE_PARTIAL,
        "path": "",
    }


def reopen_plan(rec: SliceRecord, state: Dict[str, Any]) -> Dict[str, Any]:
    """Archive the rejected plan and its approval, then wind the slice back to plan.

    Nothing is deleted: the plan moves to ``plans/NNN.md`` and the approval to
    ``approvals/plan-NNN.md``, so the record of what was rejected survives the
    thing that replaces it. The commits on the branch are not rewound either -
    ``--rollback --to plan`` is the command for that, and it is a different question.

    @param rec    the slice record whose files are moved
    @param state  state.json, wound back in place
    @flow  archive plan.md + approvals/plan.md -> stages pending -> record the replan
    주요 내부 변수: number(이번이 몇 번째 재계획인지), record(state에 남기는 기록)
    """
    replans = state.setdefault("replans", [])
    if not isinstance(replans, list):
        replans = []
        state["replans"] = replans
    number = len(replans) + 1

    previous_plan = rec.read_plan()
    archived = ""
    if previous_plan.strip():
        rec.plans_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(rec.archived_plan_path(number), previous_plan)
        archived = "plans/{0:03d}.md".format(number)
    approval = rec.approval_path("plan")
    archived_approval = ""
    try:
        text = approval.read_text(encoding="utf-8")
    except OSError:
        text = ""
    if text.strip():
        kept = rec.approvals_dir / "plan-{0:03d}.md".format(number)
        write_text_atomic(kept, text)
        archived_approval = kept.name
    try:
        # The next gate writes a fresh template; leaving the old 'rejected' line
        # there would make the loop stop on the answer it was just given.
        approval.unlink()
    except OSError:
        pass

    record = {
        "n": number,
        "at": now_iso(),
        "previous_plan": archived,
        "previous_approval": archived_approval,
        "rejection": latest_rejection(state, "plan"),
    }
    replans.append(record)
    for name in stage_order(state):
        entry = stage_entry(state, name)
        entry["status"] = "pending"
        # The plan is about to change, so everything under it is about to be
        # decided again; a session holding the old plan must not be resumed.
        for key in ("session_id", "approval", "approval_reason", "verdict", "run_id"):
            entry.pop(key, None)
    state.pop("test_verdict", None)
    state.pop("progress", None)
    return record


def replan_slice(args: Any, repo: Path, data_dir: Path, repo_given: bool = True) -> int:
    """Re-run the plan stage of a rejected slice, handing it the rejection.

    @param args       parsed arguments - ``--replan <slice>``, ``--dry-run``
    @param repo       the user's repository
    @param data_dir   where runs are stored
    @param repo_given whether --repo was named, for the "no slices here" hint
    @flow  find slice -> read requirement -> dry-run? -> reopen_plan -> _finish
    주요 내부 변수: rec(대상 slice), rejection(직전 반려), body(요구사항 본문)
    """
    rec = find_slice(repo, args.replan, repo_given, data_dir)
    state = rec.read_state()
    if state is None:
        raise PipelineError("no readable state.json in {0}".format(rec.dir))
    status = str(state.get("status", ""))
    if status in (STATUS_DISCARDED, STATUS_MERGED):
        raise PipelineError(
            "slice {0} is {1} - re-planning it would produce work with nowhere to "
            "go. Amend it instead.".format(rec.slice_id, status)
        )

    text = rec.requirement_path.read_text(encoding="utf-8")
    body = guard_requirement(text, rec.slice_id)
    fields, _ = parse_front_matter(text)
    setup_command = resolve_setup(fields)
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is not None:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            raise PipelineError(broken)
    rejection = latest_rejection(state, "plan")

    if args.dry_run:
        print("slice     {0}  ({1})".format(rec.slice_id, status or "?"))
        print("replan    #{0}".format(len([e for e in (state.get("replans") or [])]) + 1))
        print("keeps     {0} -> plans/{1:03d}.md".format(
            rec.plan_path.name, len([e for e in (state.get("replans") or [])]) + 1
        ))
        print("rejection {0}  ({1})".format(rejection.get("reason") or "(none recorded)",
                                            rejection.get("grade", GRADE_PARTIAL)))
        print("detail    {0}".format(rejection.get("path") or "(none)"))
        print("pending   {0}".format(", ".join(stage_order(state))))
        return EXIT_DONE

    record = reopen_plan(rec, state)
    rec.write_state(state)
    say("replan #{0} of slice {1} (was {2})".format(record["n"], rec.slice_id, status or "?"))
    say("  rejected: {0}".format(rejection.get("reason") or "(no reason recorded)"))
    say("  grade:    {0}".format(rejection.get("grade", GRADE_PARTIAL)))
    if record["previous_plan"]:
        say("  kept:     {0}".format(record["previous_plan"]))
    state.setdefault("quota", {"waiting": False, "resume_at": None, "retries": 0})
    cfg = _config(args, repo, data_dir, ws, setup_command, fields)
    state["replan_open"] = {
        "n": record["n"],
        "previous_plan": rec.read_plan_archive(record["previous_plan"]),
        "rejection": record["rejection"],
    }
    return _finish(cfg, rec, state, body)


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
        raise _broken_workspace_error(repo, rec, ws, broken)
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


def _discard_failed(
    repo: Path,
    rec: SliceRecord,
    state: Dict[str, Any],
    ws: workspace.Workspace,
    exc: Exception,
) -> None:
    """Report a failed worktree removal by measuring what it actually left behind.

    Three states are possible and only one of them is the dead end. Guessing
    between them is how the orphan went unnoticed, so each is asked about rather
    than assumed. Always raises, except when the directory turns out to be gone
    after all - then the goal is reached and the caller may go on.

    @param repo   the user's repository
    @param rec    the slice being discarded
    @param state  state.json, where a dead end is written down
    @param ws     its recorded workspace
    @param exc    what git said
    @flow  still registered -> nothing changed ; gone from git -> repair | orphan ; no directory -> continue
    주요 내부 변수: registered(다시 잰 등록 여부), exists(디렉터리가 남았는지)
    """
    registered = workspace.find_worktree(repo, ws.path) is not None
    exists = Path(ws.path).exists()
    if not exists:
        # git complained, but the directory is what we came for and it is gone.
        say("git reported an error removing the worktree, but the directory is gone:")
        say("  {0}".format(exc))
        return
    if registered:
        raise PipelineError(
            "could not remove the worktree, so the branch was kept: {0}\n"
            "    {1}\n"
            "    Nothing changed: it is still registered and still on disk.\n"
            "    Close whatever holds files in there and run --discard again.".format(
                ws.path, exc
            )
        )
    if workspace.repair_worktree(repo, ws.path):
        raise PipelineError(
            "could not remove the worktree, so it was registered again and the "
            "branch was kept: {0}\n"
            "    {1}\n"
            "    Close whatever holds files in there and run --discard again.".format(
                ws.path, exc
            )
        )
    state["discard_failed"] = {"at": now_iso(), "path": str(ws.path), "error": str(exc)}
    rec.write_state(state)
    raise PipelineError(
        "the worktree was unregistered but the directory could not be deleted, so "
        "the branch was kept: {0}\n"
        "    {1}\n"
        "    git no longer knows this path, so it cannot remove it either. Either\n"
        "    run --discard again - it deletes an orphaned directory - or do it by\n"
        "    hand:\n{2}".format(ws.path, exc, _discard_recovery(repo, rec, ws))
    )


def _discard_recovery(repo: Path, rec: SliceRecord, ws: workspace.Workspace) -> str:
    """The three commands that get a half-removed workspace out of its dead end.

    The third one matters most: running ``--discard`` again now finishes the job,
    because an orphaned directory is a case it handles rather than one it refuses.

    @param repo  the user's repository
    @param rec   the slice being discarded
    @param ws    its recorded workspace
    """
    return (
        "    git -C {0} worktree prune\n"
        "    rm -rf {1}\n"
        "    aidev pipeline --repo {0} --discard {2}".format(repo, ws.path, rec.slice_id)
    )


def discard_slice(
    args: Any, repo: Path, repo_given: bool = True, data_dir: Optional[Path] = None
) -> int:
    """Throw the slice's workspace away. The user's checkout keeps no git trace of it.

    ``git worktree remove`` unregisters and then deletes, and measured 2026-08-18
    it did the first and failed the second - leaving an orphaned directory that
    resume refused and that ``--discard`` itself could never remove, because git
    will not remove a path it no longer has registered. So: look before leaping,
    re-measure after a failure instead of assuming what it left behind, put the
    registration back when git can, and treat an orphan as a case rather than an
    error. The branch is still never deleted first - going back is worth more
    than a tidy directory.

    @param args        parsed arguments - ``--discard <slice>``
    @param repo        the user's repository
    @param repo_given  whether --repo was named, for the "no slices here" hint
    @param data_dir    where runs are stored
    @flow  blockers? refuse -> remove -> failed? re-measure (repair | orphan) -> branch -> discarded
    주요 내부 변수: entry(git의 등록 정보), sha(지운 브랜치의 커밋)
    """
    rec, state, ws = _finished_workspace(repo, args.discard, repo_given, data_dir)
    with SliceLock(rec):
        entry = workspace.find_worktree(repo, ws.path)
        if entry is not None:
            blockers = workspace.removal_blockers(repo, ws.path)
            if blockers:
                # Nothing has been touched yet, which is the whole point of asking
                # first: the slice is exactly as it was and the branch is intact.
                raise PipelineError(
                    "the worktree cannot be removed as it stands, so nothing was "
                    "changed:\n"
                    + "\n".join("  - " + reason for reason in blockers)
                    + "\n    then: aidev pipeline --repo {0} --discard {1}".format(
                        repo, rec.slice_id
                    )
                )
            try:
                workspace.worktree_remove(repo, ws.path)
            except workspace.GitError as exc:
                _discard_failed(repo, rec, state, ws, exc)
        elif Path(ws.path).exists():
            # The half-removed state an earlier --discard left behind. git has
            # nothing to remove any more, so the directory is ours to delete.
            say("worktree is no longer registered but the directory is still there:")
            say("  {0}".format(ws.path))
            problem = workspace.remove_tree(ws.path)
            if problem is not None:
                raise PipelineError(
                    "could not delete the orphaned worktree directory, so the branch "
                    "was kept: {0}\n    {1}\n{2}".format(
                        ws.path, problem, _discard_recovery(repo, rec, ws)
                    )
                )
            say("  removed it")
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
        # An earlier failure is history now: the workspace really is gone.
        state.pop("discard_failed", None)
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
