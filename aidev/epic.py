"""Epic -> slice list -> a sequential queue of v0.3 slices.

An epic is decomposed once, the list is approved by a human once, and then every
item runs as an ordinary slice. Nothing here re-implements the slice loop: the
queue calls ``pipeline.launch_slice()`` / ``pipeline.continue_slice()`` and the
whole v0.3 flow - worktree, requirement commit, gates, quota waits, stage
commits - happens unchanged inside it. That is also why a quota wait cannot
break the queue and an in-slice gate still stops it: both are the slice's own
business, and the queue is simply waiting for that slice to end.

    <repo>/.aidev/epics/<epic-id>/
        epic.md          the original, copied in
        slices.md        what decompose produced (a human edits it before approving)
        slices/01-x.md   one executable requirement per item, cut from slices.md
        state.json       authoritative epic progress, single writer: this process
        approvals/decompose.md
        runs.json        the decompose runs

The list gate is not a front matter option. Decomposition is the one step where
a model decides what work exists at all, so it is always shown to a human before
anything runs - ``approval: none`` cannot turn it off.

Where each slice branches from is the decision this design turns on. Slice N+1
is cut from slice N's branch, so a dependent slice actually sees what the one
before it built, while ``base`` stays the epic's base branch for all of them:
the chain is where they fork, the base is where they land. Nothing here ever
merges - that is still only ever a human typing --merge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import pipeline, reporter, workspace
from .storage import read_json_tolerant, write_json_atomic

EPICS_DIRNAME = "epics"
EPIC_BRANCH_PREFIX = "epic/"
DECOMPOSE = "decompose"
SLICES_FILENAME = "slices.md"
EPIC_STATE_SCHEMA = 1

# Same profile as plan, and for the same reason: decomposing is reading and
# writing prose about the code, never touching it.
DECOMPOSE_POLICY: Dict[str, Optional[str]] = {"safety_profile": "readonly", "permission_mode": None}

# Epic status values, all published through the epic's own state.json.
STATUS_DONE = pipeline.STATUS_DONE
STATUS_FAILED = pipeline.STATUS_FAILED
STATUS_REJECTED = pipeline.STATUS_REJECTED


# ------------------------------------------------------------------ the record


def epics_root(repo: Path) -> Path:
    return Path(repo) / pipeline.AIDEV_DIRNAME / EPICS_DIRNAME


@dataclass
class EpicRecord:
    """One ``<repo>/.aidev/epics/<id>/`` directory and its files.

    Deliberately the same shape as ``SliceRecord``: ``run_stage``,
    ``execute_stage``, ``await_approval``, ``record_run``, ``resume_quota_wait``
    and ``SliceLock`` are given this object as-is.
    """

    root: Path
    epic_id: str

    kind = "epic"

    @property
    def dir(self) -> Path:
        return Path(self.root) / self.epic_id

    @property
    def label(self) -> str:
        return self.epic_id

    @property
    def requirement_path(self) -> Path:
        return self.dir / "epic.md"

    @property
    def slices_path(self) -> Path:
        return self.dir / SLICES_FILENAME

    @property
    def slices_dir(self) -> Path:
        return self.dir / "slices"

    @property
    def state_path(self) -> Path:
        return self.dir / pipeline.STATE_FILENAME

    @property
    def runs_path(self) -> Path:
        return self.dir / "runs.json"

    @property
    def approvals_dir(self) -> Path:
        return self.dir / "approvals"

    @property
    def slug(self) -> str:
        """The name part of the id, used to label runs."""
        _, _, rest = self.epic_id.partition("-")
        return rest or self.epic_id

    def approval_path(self, stage: str) -> Path:
        return self.approvals_dir / "{0}.md".format(stage)

    def ensure(self) -> "EpicRecord":
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        self.slices_dir.mkdir(parents=True, exist_ok=True)
        return self

    def read_state(self) -> Optional[Dict[str, Any]]:
        return read_json_tolerant(self.state_path)

    def write_state(self, state: Dict[str, Any]) -> None:
        pipeline.write_state_atomic(self.state_path, state)

    def write_slices(self, text: str) -> None:
        pipeline.write_text_atomic(self.slices_path, strip_fence(text).rstrip() + "\n")

    def read_slices(self) -> str:
        try:
            return self.slices_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def append_run(self, entry: Dict[str, Any]) -> None:
        data = read_json_tolerant(self.runs_path) or {}
        runs = data.get("runs")
        if not isinstance(runs, list):
            runs = []
        runs.append(entry)
        write_json_atomic(self.runs_path, {"epic_id": self.epic_id, "runs": runs})

    def runs(self) -> List[Dict[str, Any]]:
        data = read_json_tolerant(self.runs_path) or {}
        runs = data.get("runs")
        return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []


def new_epic_state(
    epic_id: str, repo: Path, base: Optional[str], base_commit: str
) -> Dict[str, Any]:
    """The epic's own state.json, under the same rules as a slice's."""
    return {
        "schema": EPIC_STATE_SCHEMA,
        "epic_id": epic_id,
        "status": "pending",
        "repo": str(repo),
        "base": base,
        "base_commit": base_commit,
        # Not from front matter: the list gate is the one gate nobody can disarm.
        "gates": [DECOMPOSE],
        "stage_order": [DECOMPOSE],
        "stages": {DECOMPOSE: {"status": "pending"}},
        "quota": {"waiting": False, "resume_at": None, "retries": 0},
        "current": 0,
        "slices": [],
        "created_at": pipeline.now_iso(),
        "updated_at": pipeline.now_iso(),
    }


# ------------------------------------------------------------------ the prompt

_DECOMPOSE_PROMPT = """\
You are the DECOMPOSE STAGE of an unattended pipeline running on this repository.
Nobody is watching: never ask a question, produce the list.

EPIC
----
{epic}

INSTRUCTIONS
- This stage is read-only. Do not create, modify or delete any file, and do not
  run commands. Mutating tools are blocked; do not look for a way around them.
- Read the real code before you write anything. Do not guess file or symbol names.
- Cut this epic into the smallest number of slices that each stand on their own.
  A slice is one requirement that one unattended run can finish.

TURN BUDGET - this is the quality bar for the split
- Every step of every slice runs with a budget of {max_turns} turns.
- Measured on 2026-08-15: two slices that were cut too large died at 81 turns
  with the work half done. That is the failure this budget exists to prevent.
- If you are unsure whether a slice fits, cut it smaller. A slice that genuinely
  cannot be cut smaller may raise its own budget with 'max_turns: implement=140'.

ORDER IS DEPENDENCY
- Slice N+1 starts in a worktree that already contains slice N's result, so a
  later slice may build on an earlier one but never the other way round.
- Each item is executed on its own, with only its own text in front of the
  agent. So say in prose what the slices before it already finished.

FORMAT - your final message is saved verbatim as slices.md, so emit exactly this
and nothing else around it (no code fence, no preamble):

=== SLICE 1: <title> ===
---
approval: plan
---
# <title>

## What it must do
<the requirement, concretely>

## Scope
<the files and areas it may touch>

## Tests
<how it is verified>

## Not in this slice
<what is deliberately left out>

=== SLICE 2: <title> ===
...

- The front matter accepts exactly six keys: 'approval:' (stage names, or the
  word none), 'setup:' (one plain command, no shell operators), 'test_commands:'
  (how this project is verified, comma separated), 'max_turns:' (a number, or
  '<stage>=<number>'), 'model:' (a model name, or '<stage>=<model>') and
  'spec_check:' (on/off). If you are not sure, leave the line out and the
  defaults apply.
- Any text before the first === marker is kept as a note and never executed.
- Write the body in the language the epic is written in.
"""


def decompose_spec(cfg: pipeline.PipelineConfig, epic_body: str) -> pipeline.StageSpec:
    return pipeline.StageSpec(
        stage=DECOMPOSE,
        policy=DECOMPOSE_POLICY,
        phase=DECOMPOSE,
        allowed=(),
        # The number the prompt quotes has to be the number the run actually gets.
        prompt=_DECOMPOSE_PROMPT.format(epic=epic_body.strip(), max_turns=cfg.turns_for(DECOMPOSE)),
    )


# ------------------------------------------------------------------ the parser

# Tolerant on purpose: the numbering, the colon and the case are all things a
# model varies, and none of them change what the list means.
_MARKER_RE = re.compile(
    r"^===[ \t]*SLICE[ \t]*(\d+)?[ \t]*:?[ \t]*(.*?)[ \t]*===[ \t]*$",
    re.MULTILINE | re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^```[^\n]*\n(.*)\n```[ \t]*$", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def strip_fence(text: str) -> str:
    """Unwrap a whole document that came back inside one code fence."""
    match = _FENCE_RE.match(text.strip())
    return match.group(1) if match is not None else text


@dataclass
class SliceItem:
    """One executable requirement cut out of slices.md."""

    index: int  # the position in the file is the running order; the marker's number is a hint
    title: str
    text: str  # front matter included: this is the requirement, verbatim


def parse_slices(text: str) -> Tuple[str, List[SliceItem]]:
    """Split slices.md into its leading note and its items, in file order."""
    body = strip_fence(text).lstrip("﻿")
    marks = list(_MARKER_RE.finditer(body))
    if not marks:
        return body.strip(), []
    notes = body[: marks[0].start()].strip()
    items: List[SliceItem] = []
    for position, match in enumerate(marks, start=1):
        end = marks[position].start() if position < len(marks) else len(body)
        chunk = body[match.end() : end].strip()
        title = match.group(2).strip()
        if not title:
            heading = _HEADING_RE.search(chunk)
            title = heading.group(1).strip() if heading else "slice {0}".format(position)
        items.append(SliceItem(index=position, title=title, text=chunk + "\n"))
    return notes, items


def validate_items(items: Sequence[SliceItem], path: Path) -> None:
    """Every item has to be a requirement this pipeline can actually run.

    Checked once, before the queue starts, so a typo in item 4 is not discovered
    after items 1..3 have already built branches.
    """
    for item in items:
        where = "{0}, slice {1} ('{2}')".format(path, item.index, item.title)
        fields, body = pipeline.parse_front_matter(item.text)
        if not body.strip():
            raise pipeline.PipelineError("{0}: no requirement text under the marker".format(where))
        try:
            pipeline.resolve_gates(fields)
            pipeline.resolve_setup(fields)
            pipeline.resolve_test_commands(fields)
            pipeline.resolve_max_turns(fields)
        except pipeline.PipelineError as exc:
            raise pipeline.PipelineError("{0}: {1}".format(where, exc))


def _slug(text: str) -> str:
    """storage.slugify without its 'run' fallback: an empty slug is dropped instead."""
    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()[:40]


def slice_name(erec: EpicRecord, item: SliceItem) -> str:
    """``<epic 20 chars>-04-<title>``: the index stays inside make_slice_id's 40-char cut."""
    parts = [_slug(erec.slug)[:20].strip("-"), "{0:02d}".format(item.index), _slug(item.title)]
    return "-".join(part for part in parts if part)


def slice_file(erec: EpicRecord, item: SliceItem) -> Path:
    return erec.slices_dir / "{0:02d}-{1}.md".format(item.index, _slug(item.title) or "slice")


# ------------------------------------------------------------------ the list


def _slice_record(repo: Path, slice_id: str) -> pipeline.SliceRecord:
    return pipeline.SliceRecord(pipeline.slices_root(repo), slice_id)


def slice_status(repo: Path, entry: Dict[str, Any]) -> str:
    """The slice's own state.json is the authority; the epic only projects it."""
    slice_id = entry.get("slice_id")
    if not slice_id:
        return str(entry.get("status") or "pending")
    state = _slice_record(repo, str(slice_id)).read_state() or {}
    return str(state.get("status") or entry.get("status") or "pending")


def _started(repo: Path, entry: Dict[str, Any]) -> bool:
    """Started, and still there: a discarded slice is a way back to 'not started'."""
    if not entry.get("slice_id"):
        return False
    return slice_status(repo, entry) != pipeline.STATUS_DISCARDED


def reconcile_items(
    repo: Path, state: Dict[str, Any], items: Sequence[SliceItem]
) -> List[Dict[str, Any]]:
    """Merge the current slices.md into the list, keeping what has already run.

    An item that has already been launched is kept exactly as it is: its
    requirement is committed on a branch, and rewriting it now would make the
    epic's record disagree with git. Everything from the first item that has not
    started is taken from slices.md as it reads today - which is what makes
    "fix the list and resume" work.
    """
    entries = [e for e in (state.get("slices") or []) if isinstance(e, dict)]
    keep: List[Dict[str, Any]] = []
    for entry in entries:
        if not _started(repo, entry):
            break
        keep.append(entry)
    dropped = [e for e in entries[len(keep) :] if e.get("slice_id")]
    if len(items) < len(keep):
        raise pipeline.PipelineError(
            "slices.md now has {0} item(s) but {1} have already run: {2}\n"
            "    put them back (or discard those slices) before resuming".format(
                len(items), len(keep), ", ".join(str(e.get("slice_id")) for e in keep)
            )
        )
    # The names and files of the fresh items are filled in by materialize(),
    # which is also what renumbers them - here they are only positions.
    fresh = [
        {"index": item.index, "title": item.title, "requirement": None, "status": "pending"}
        for item in items[len(keep) :]
    ]
    if dropped:
        pipeline.say(
            "note: these slices are no longer in the list and were left alone: {0}".format(
                ", ".join(str(e.get("slice_id")) for e in dropped)
            )
        )
    if fresh and entries[len(keep) :]:
        pipeline.say(
            "slices.md changed: pending item(s) {0}..{1} replaced by {2}..{3}".format(
                len(keep) + 1, len(entries), len(keep) + 1, len(keep) + len(fresh)
            )
        )
    return keep + fresh


def materialize(erec: EpicRecord, items: Sequence[SliceItem], entries: List[Dict[str, Any]],
                repo: Path) -> None:
    """Write one requirement file per item that has not started, and renumber."""
    erec.slices_dir.mkdir(parents=True, exist_ok=True)
    for position, entry in enumerate(entries, start=1):
        entry["index"] = position
        if entry.get("slice_id"):
            continue
        item = items[position - 1]
        item = SliceItem(index=position, title=item.title, text=item.text)
        path = slice_file(erec, item)
        pipeline.write_text_atomic(path, item.text)
        entry["title"] = item.title
        entry["name"] = slice_name(erec, item)
        entry["requirement"] = _relative(path, repo)


def _relative(path: Path, repo: Path) -> str:
    try:
        return Path(path).relative_to(Path(repo)).as_posix()
    except ValueError:
        return str(path)


def requirement_text(entry: Dict[str, Any], repo: Path) -> str:
    raw = entry.get("requirement")
    if not raw:
        raise pipeline.PipelineError("epic item {0} has no requirement file".format(entry.get("index")))
    path = Path(raw)
    if not path.is_absolute():
        path = Path(repo) / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise pipeline.PipelineError("cannot read {0}: {1}".format(path, exc))


# ------------------------------------------------------------------- the queue


def set_status(erec: EpicRecord, state: Dict[str, Any], status: str) -> None:
    state["status"] = status
    erec.write_state(state)


def fail_epic(erec: EpicRecord, state: Dict[str, Any], reason: str, code: int = pipeline.EXIT_FAILED) -> int:
    state["reason"] = reason
    set_status(erec, state, STATUS_FAILED)
    pipeline.say("EPIC FAILED - {0}".format(reason))
    pipeline.say(
        "  fix it, then:  aidev pipeline --repo {0} --resume-epic {1}".format(
            state.get("repo", "<repo>"), erec.epic_id
        )
    )
    pipeline.say("  the remaining list can be rewritten first: {0}".format(erec.slices_path))
    return code


def _start_ref(state: Dict[str, Any], entries: List[Dict[str, Any]], position: int,
               repo: Path) -> str:
    """Where this slice's worktree is cut from: the epic's base, or the slice before it."""
    if position == 0:
        return str(state.get("base_commit") or state.get("base") or "HEAD")
    previous = entries[position - 1]
    branch = previous.get("branch")
    if not branch:
        raise pipeline.PipelineError(
            "slice {0} has no branch recorded, so slice {1} has nothing to build on".format(
                previous.get("index"), position + 1
            )
        )
    if not workspace.branch_exists(repo, str(branch)):
        # Falling back to the base would quietly drop the dependency this whole
        # ordering exists for, and the next slice would work against code that
        # is not there.
        raise pipeline.PipelineError(
            "slice {0} depends on '{1}', which no longer exists.\n"
            "    aidev will not silently branch from the base instead - that would drop the "
            "dependency.\n"
            "    Restore the branch, or remove the finished slices from {2} and resume.".format(
                position + 1, branch, "the epic's slices.md"
            )
        )
    return str(branch)


def _run_entry(
    args: Any,
    cfg: pipeline.PipelineConfig,
    state: Dict[str, Any],
    entries: List[Dict[str, Any]],
    position: int,
) -> int:
    """Run one queue item, as an ordinary slice. Returns the slice's exit code."""
    repo, data_dir = cfg.repo, cfg.data_dir
    entry = entries[position]
    try:
        if entry.get("slice_id"):
            rec = _slice_record(repo, str(entry["slice_id"]))
            pipeline.say(
                "epic slice {0}/{1}: resuming {2}".format(
                    position + 1, len(entries), entry["slice_id"]
                )
            )
            return pipeline.continue_slice(args, repo, data_dir, rec)

        start = _start_ref(state, entries, position, repo)
        pipeline.say(
            "epic slice {0}/{1}: {2}  (from {3})".format(
                position + 1, len(entries), entry.get("title", "?"), start
            )
        )
        outcome = pipeline.launch_slice(
            args,
            repo,
            data_dir,
            requirement_text(entry, repo),
            str(entry.get("name") or "slice"),
            base=state.get("base"),
            start=start,
            epic={"epic_id": state.get("epic_id"), "index": entry.get("index")},
            quiet_unfinished=True,
        )
        entry["slice_id"] = outcome.rec.slice_id
        entry["branch"] = (outcome.state.get("workspace") or {}).get("branch")
        entry["start"] = start
        return outcome.code
    except pipeline.PipelineError as exc:
        # A refusal about one slice is that slice's failure, not a crash of the
        # queue: the epic has to record it and print how to resume.
        entry["error"] = str(exc)
        return exc.code


def run_epic(args: Any, cfg: pipeline.PipelineConfig, erec: EpicRecord, state: Dict[str, Any],
             epic_body: str) -> int:
    """Decompose, gate the list, then run the list in order."""
    repo = cfg.repo
    state.pop("reason", None)
    pipeline.resume_quota_wait(cfg, erec, state)

    entry = pipeline.stage_entry(state, DECOMPOSE)
    if entry.get("status") != "done":
        reason = pipeline.workspace_dirty_reason(cfg.cwd, DECOMPOSE)
        if reason is not None:
            return fail_epic(erec, state, reason)
        before = pipeline.repo_changes(cfg.cwd, include_slice_files=True)
        try:
            run = pipeline.run_stage(
                cfg, erec, state, DECOMPOSE, epic_body, spec=decompose_spec(cfg, epic_body)
            )
        except pipeline.PipelineError as exc:
            entry["status"] = "failed"
            fail_epic(erec, state, str(exc))
            raise
        if not run.ok:
            entry["status"] = "failed"
            if not run.quota:
                pipeline.note_stage_failure(state, DECOMPOSE)
            return fail_epic(
                erec,
                state,
                "stage 'decompose' {0} (run {1}, exit {2})".format(
                    run.status, run.run_id, run.exit_code
                ),
            )
        violations = ((run.telemetry.get("observed") or {}).get("safety_violations")) or []
        if violations:
            entry["safety_violations"] = len(violations)
        if not run.text.strip():
            entry["status"] = "failed"
            return fail_epic(erec, state, "decompose stage produced no text to save as slices.md")
        after = pipeline.repo_changes(cfg.cwd, include_slice_files=True)
        if before is not None and after is not None and before != after:
            entry["status"] = "failed"
            return fail_epic(
                erec,
                state,
                "decompose stage changed the repository despite the readonly profile:\n"
                + "\n".join("    " + line for line in after.splitlines()[:20]),
            )
        erec.write_slices(run.text)
        entry.update({"status": "done", "run_id": run.run_id})
        pipeline.say("slice list saved: {0}".format(erec.slices_path))
        erec.write_state(state)

    decision = pipeline.await_approval(cfg, erec, state, DECOMPOSE, artifact=SLICES_FILENAME)
    if decision.verdict == pipeline.REJECTED:
        state["reason"] = "rejected at 'decompose': {0}".format(
            decision.reason or "(no reason given)"
        )
        set_status(erec, state, STATUS_REJECTED)
        _drop_workspace(repo, state)
        erec.write_state(state)
        pipeline.say("REJECTED - {0}".format(state["reason"]))
        return pipeline.EXIT_REJECTED
    if decision.verdict != pipeline.APPROVED:
        return fail_epic(erec, state, "timed out waiting for approval of 'decompose'")

    # The list is settled, so the decomposing worktree has done its job.
    _drop_workspace(repo, state)
    erec.write_state(state)

    _, items = parse_slices(erec.read_slices())
    if not items:
        return fail_epic(
            erec,
            state,
            "no '=== SLICE n: ... ===' markers in {0}\n"
            "    write the list yourself and resume, or reject and start over".format(
                erec.slices_path
            ),
        )
    try:
        validate_items(items, erec.slices_path)
        entries = reconcile_items(repo, state, items)
    except pipeline.PipelineError as exc:
        return fail_epic(erec, state, str(exc))
    materialize(erec, items, entries, repo)
    state["slices"] = entries
    erec.write_state(state)

    for position, entry in enumerate(entries):
        if slice_status(repo, entry) == pipeline.STATUS_DONE and entry.get("slice_id"):
            entry["status"] = pipeline.STATUS_DONE
            continue
        state["current"] = position + 1
        set_status(erec, state, "running:slice")
        code = _run_entry(args, cfg, state, entries, position)
        entry["status"] = slice_status(repo, entry)
        erec.write_state(state)
        if code != pipeline.EXIT_DONE:
            return fail_epic(
                erec,
                state,
                "slice {0} ({1}) stopped: {2}".format(
                    position + 1,
                    entry.get("slice_id") or entry.get("title"),
                    entry.get("error") or _slice_reason(repo, entry) or entry["status"],
                ),
                code=code,
            )

    state["current"] = len(entries)
    set_status(erec, state, STATUS_DONE)
    return pipeline.EXIT_DONE


def _slice_reason(repo: Path, entry: Dict[str, Any]) -> str:
    slice_id = entry.get("slice_id")
    if not slice_id:
        return ""
    state = _slice_record(repo, str(slice_id)).read_state() or {}
    return str(state.get("reason") or "")


def _drop_workspace(repo: Path, state: Dict[str, Any]) -> None:
    """Remove the decomposing worktree. A failure here is a warning, never a stop."""
    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is None:
        return
    try:
        workspace.destroy(repo, ws)
    except workspace.GitError as exc:
        pipeline.say("could not remove the epic worktree: {0}".format(exc))
        pipeline.say("  worktree {0}".format(ws.path))
        pipeline.say("  branch   {0}".format(ws.branch))
    state.pop("workspace", None)


# ------------------------------------------------------------------- commands


def start_epic(args: Any, repo: Path, data_dir: Path) -> int:
    if getattr(args, "no_worktree", False):
        raise pipeline.PipelineError(
            "--epic needs worktrees: slice N+1 branches from slice N, which is a branch"
        )
    source = pipeline.resolve_requirement(args.epic, repo, flag="--epic")
    text = source.read_text(encoding="utf-8")
    if not text.strip():
        raise pipeline.PipelineError("epic file is empty: {0}".format(source))
    _, body = pipeline.parse_front_matter(text)
    if not body.strip():
        raise pipeline.PipelineError("epic has front matter but no body: {0}".format(source))

    root = epics_root(repo)
    epic_id = pipeline.make_slice_id(source.stem, root)
    erec = EpicRecord(root, epic_id)
    plan = pipeline._plan_workspace(args, repo, epic_id, branch=EPIC_BRANCH_PREFIX + epic_id)

    if args.dry_run:
        print("epic      {0}".format(epic_id))
        print("repo      {0}".format(repo))
        print("base      {0} @ {1}".format(plan.base or "detached HEAD", plan.base_commit[:7]))
        print("branch    {0}  (decompose only; removed once the list is approved)".format(plan.branch))
        print("worktree  {0}".format(plan.path))
        for reason in workspace.blocking_reasons(repo, plan):
            print("BLOCKED   {0}".format(reason))
        print("stages    {0}".format(DECOMPOSE))
        print("gates     {0}  (always: the list is never run unseen)".format(DECOMPOSE))
        print("slices    {0} -> one worktree each, N+1 branching from N".format(erec.slices_path))
        print("epic dir  {0}".format(erec.dir))
        print("run dir   {0}".format(data_dir / "runs"))
        return pipeline.EXIT_DONE

    pipeline._refuse_blocked_workspace(repo, plan)
    erec.ensure()
    pipeline.write_text_atomic(erec.requirement_path, text)
    state = new_epic_state(epic_id, repo, plan.base, plan.base_commit)
    pipeline.say("epic {0}".format(epic_id))
    pipeline.say("  dir   {0}".format(erec.dir))
    pipeline.say("  gates {0} (the list is always approved by a human)".format(DECOMPOSE))

    try:
        ws = workspace.create(repo, plan)
    except workspace.GitError as exc:
        raise pipeline.PipelineError(str(exc))
    pipeline.say("  work  {0}  (branch {1} from {2})".format(ws.path, ws.branch, ws.base or "?"))
    state["workspace"] = ws.to_dict()
    erec.write_state(state)

    return _finish(args, repo, data_dir, erec, state, body, ws)


def resume_epic(args: Any, repo: Path, data_dir: Path, repo_given: bool = True) -> int:
    erec = find_epic(repo, args.resume_epic, repo_given, data_dir)
    state = erec.read_state()
    if state is None:
        raise pipeline.PipelineError("no readable state.json in {0}".format(erec.dir))
    status = str(state.get("status", ""))
    _, body = pipeline.parse_front_matter(erec.requirement_path.read_text(encoding="utf-8"))

    if args.dry_run:
        print("epic    {0}  ({1})".format(erec.epic_id, status))
        print("slices  {0}".format(erec.slices_path))
        for entry in state.get("slices") or []:
            print(
                "  {0:>2}  {1:<24}{2}".format(
                    entry.get("index", "?"),
                    str(entry.get("slice_id") or "(not started)"),
                    slice_status(repo, entry),
                )
            )
        return pipeline.EXIT_DONE

    if status == STATUS_DONE:
        pipeline.say("epic {0} is already done".format(erec.epic_id))
        print(render_epic_summary(repo, erec, state))
        return pipeline.EXIT_DONE

    ws = workspace.Workspace.from_dict(state.get("workspace"))
    if ws is not None:
        broken = workspace.verify(repo, ws)
        if broken is not None:
            if pipeline.stage_entry(state, DECOMPOSE).get("status") == "done":
                # Decomposing is over; the worktree it used is not needed again.
                pipeline.say("note: the epic worktree is gone ({0}) - it is no longer needed".format(broken))
                state.pop("workspace", None)
                ws = None
            else:
                raise pipeline.PipelineError(
                    "{0}\n"
                    "    aidev does not re-create it. Restore it yourself, or start the epic "
                    "again.".format(broken)
                )

    pipeline.say("resuming epic {0} (was {1})".format(erec.epic_id, status))
    state.setdefault("quota", {"waiting": False, "resume_at": None, "retries": 0})
    return _finish(args, repo, data_dir, erec, state, body, ws)


def _finish(
    args: Any,
    repo: Path,
    data_dir: Path,
    erec: EpicRecord,
    state: Dict[str, Any],
    body: str,
    ws: Optional[workspace.Workspace],
) -> int:
    cfg = pipeline._config(args, repo, data_dir, ws)
    with pipeline.SliceLock(erec):
        code = run_epic(args, cfg, erec, state, body)
        print(render_epic_summary(repo, erec, state))
        print("epic dir: {0}".format(erec.dir))
        return code


# --------------------------------------------------------------------- output


def _existing_epics(root: Path) -> List[EpicRecord]:
    if not Path(root).is_dir():
        return []
    return [
        EpicRecord(root, path.name)
        for path in sorted(Path(root).iterdir(), key=lambda p: p.name)
        if path.is_dir()
    ]


def find_epic(
    repo: Path, pattern: str, repo_given: bool = True, data_dir: Optional[Path] = None
) -> EpicRecord:
    root = epics_root(repo)
    return pipeline.find_record(
        _existing_epics(root),
        pattern,
        "epic",
        root,
        pipeline._repo_hint(repo, repo_given, data_dir),
    )


def list_epics(repo: Path) -> None:
    """Epic progress above the slice list. Silent when there are no epics at all."""
    records = _existing_epics(epics_root(repo))
    if not records:
        return
    widths = (34, 26, 34)
    print(pipeline._list_row(("EPIC", "STATUS", "SLICES"), widths, "UPDATED"))
    for erec in records:
        state = erec.read_state() or {}
        entries = [e for e in (state.get("slices") or []) if isinstance(e, dict)]
        detail = " ".join(
            "{0}={1}".format(entry.get("index", "?"), slice_status(repo, entry))
            for entry in entries
        )
        status = str(state.get("status", "(unreadable)"))
        if entries and status == "running:slice":
            status = "{0} {1}/{2}".format(status, state.get("current", "?"), len(entries))
        print(
            pipeline._list_row(
                (erec.epic_id, status, detail), widths, str(state.get("updated_at", ""))[:19]
            )
        )
    print("")


def render_epic_summary(repo: Path, erec: EpicRecord, state: Dict[str, Any]) -> str:
    row = "{0:<4}{1:<34}{2:<22}{3:>7}{4:>11}".format
    lines = ["", "EPIC  {0}".format(state.get("epic_id", erec.epic_id)), reporter.rule()]
    lines.append(row("#", "Slice", "Status", "Turns", "Cost"))

    turns_total, cost_total = 0, 0.0
    for entry in erec.runs():
        turns_total += int(entry.get("turns") or 0)
        cost_total += float(entry.get("cost_usd") or 0.0)
    lines.append(
        row(
            "-",
            "decompose",
            str(pipeline.stage_entry(state, DECOMPOSE).get("status", "pending")),
            turns_total,
            reporter.format_cost(cost_total),
        )
    )

    merges: List[str] = []
    for entry in [e for e in (state.get("slices") or []) if isinstance(e, dict)]:
        slice_id = str(entry.get("slice_id") or "")
        turns, cost = 0, 0.0
        if slice_id:
            for run in _slice_record(repo, slice_id).runs():
                turns += int(run.get("turns") or 0)
                cost += float(run.get("cost_usd") or 0.0)
        status = slice_status(repo, entry)
        lines.append(
            row(
                str(entry.get("index", "?")),
                slice_id or str(entry.get("title", ""))[:33],
                status,
                turns,
                reporter.format_cost(cost),
            )
        )
        turns_total += turns
        cost_total += cost
        if slice_id and status == pipeline.STATUS_DONE:
            merges.append(slice_id)
    lines.append(reporter.rule())
    lines.append(row("", "total", "", turns_total, reporter.format_cost(cost_total)))

    lines.append("")
    lines.append("Status    {0}".format(state.get("status", "?")))
    if state.get("reason"):
        lines.append("Reason    {0}".format(state["reason"]))
    lines.append("List      {0}".format(erec.slices_path))
    repo_str = state.get("repo", "<repo>")
    if str(state.get("status")) != STATUS_DONE:
        lines.append(
            "Resume    aidev pipeline --repo {0} --resume-epic {1}".format(repo_str, erec.epic_id)
        )
    if merges:
        lines.append("")
        lines.append("Merge in this order (each branch contains the ones before it):")
        for slice_id in merges:
            lines.append(
                "          aidev pipeline --repo {0} --merge {1}".format(repo_str, slice_id)
            )
    return "\n".join(lines)
