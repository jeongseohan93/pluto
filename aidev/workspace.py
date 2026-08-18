"""Git worktree isolation: the pipeline's own workspace, never the user's checkout.

v0.2 ran Claude in the target repository itself, so the only way to keep the
user's work safe was to refuse a dirty tree - a restriction that cost a manual
worktree, a manual requirement commit and a manual merge on every slice
(measured on 2026-08-15).

v0.3 moves the work instead of restricting it:

    <repo>                      the user's checkout - the AI never runs here
    <repo>-slices/<slice-id>    a worktree on branch slice/<slice-id>

Everything here is plain git plumbing. It knows nothing about slices, state.json
or stages: it takes paths and refs, runs ``git``, and reports what happened.
Nothing in this module ever cleans up a workspace it did not just create -
detecting and refusing leftovers is ours, removing them is the human's.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Long enough for a merge on a large repository, short enough that a hung git
# cannot freeze an unattended loop forever.
GIT_TIMEOUT_S = 300.0

# <repo>-slices, beside the repo rather than inside it: a worktree nested in its
# own repository shows up in that repository's status.
DEFAULT_ROOT_SUFFIX = "-slices"


class GitError(RuntimeError):
    """A git command that failed, with everything needed to explain why."""

    def __init__(self, argv: Sequence[str], returncode: Optional[int], stderr: str) -> None:
        self.argv = list(argv)
        self.returncode = returncode
        self.stderr = (stderr or "").strip()
        super().__init__(
            "git {0} failed ({1}){2}".format(
                " ".join(str(a) for a in argv[:4]),
                returncode,
                "\n    " + "\n    ".join(self.stderr.splitlines()[:10]) if self.stderr else "",
            )
        )


def git_available() -> bool:
    return shutil.which("git") is not None


def _exe() -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitError(["git"], None, "git is not installed or not on PATH")
    return exe


# ``user.name``/``user.email`` are looked up once per repository: an unattended
# commit must not die on a machine that never configured an identity, but a
# machine that *has* one must keep using it.
_IDENTITY_CACHE: Dict[str, List[str]] = {}


def _config_value(repo: Path, name: str) -> str:
    proc = _spawn(repo, ["config", "--get", name])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def identity_args(repo: Path) -> List[str]:
    """``-c user.name=...`` only when git has no identity of its own."""
    key = os.path.normcase(str(Path(repo)))
    cached = _IDENTITY_CACHE.get(key)
    if cached is None:
        cached = []
        for setting, fallback in (("user.name", "aidev"), ("user.email", "aidev@localhost")):
            if not _config_value(repo, setting):
                cached += ["-c", "{0}={1}".format(setting, fallback)]
        _IDENTITY_CACHE[key] = cached
    return list(cached)


def _base_args(repo: Path) -> List[str]:
    # Signing is a human's choice about their own commits; the pipeline's stage
    # snapshots must not stop an unattended loop waiting for a passphrase.
    return ["-c", "commit.gpgsign=false"] + identity_args(repo)


def _spawn(repo: Path, args: Sequence[str], timeout: float = GIT_TIMEOUT_S) -> Any:
    """Raw git call with no ``-c`` preamble - used by the identity lookup itself."""
    return subprocess.run(
        [_exe()] + [str(a) for a in args],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run(repo: Path, args: Sequence[str], check: bool = True, timeout: float = GIT_TIMEOUT_S) -> Any:
    """Run git in ``repo``. Returns the CompletedProcess; raises on failure if asked."""
    argv = _base_args(repo) + [str(a) for a in args]
    try:
        proc = _spawn(repo, argv, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(args, None, str(exc))
    if check and proc.returncode != 0:
        raise GitError(args, proc.returncode, proc.stderr)
    return proc


def git(repo: Path, *args: Any, **kwargs: Any) -> str:
    """The common case: run git and return its stdout."""
    return run(repo, [str(a) for a in args], **kwargs).stdout


# ------------------------------------------------------------------ inspection


def is_git_repo(path: Path) -> bool:
    if not Path(path).is_dir() or not git_available():
        return False
    try:
        return run(path, ["rev-parse", "--git-dir"], check=False).returncode == 0
    except GitError:
        return False


def current_branch(repo: Path) -> Optional[str]:
    """The checked-out branch, or ``None`` on a detached HEAD."""
    proc = run(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"], check=False)
    name = proc.stdout.strip()
    return name or None


def resolve_commit(repo: Path, ref: str) -> Optional[str]:
    """The commit ``ref`` names, or ``None``. An unborn HEAD resolves to nothing."""
    proc = run(repo, ["rev-parse", "--verify", "--quiet", "{0}^{{commit}}".format(ref)], check=False)
    return proc.stdout.strip() or None


def is_ancestor(repo: Path, maybe_ancestor: str, ref: str) -> bool:
    """Is the first commit reachable from the second? A commit is its own ancestor.

    The one question a rewind has to ask about a ref, and it is asked of git
    rather than of a label order: an amend puts ``amend1/implement`` on the
    branch after ``test``, so "which commits does this rewind drop" cannot be
    answered by reading the ``commits`` map top to bottom.
    """
    return run(
        repo, ["merge-base", "--is-ancestor", maybe_ancestor, ref], check=False
    ).returncode == 0


def diff_paths(repo: Path, a: str, b: str) -> List[str]:
    """Paths that differ between two commits. Empty when git cannot answer."""
    proc = run(repo, ["diff", "--name-only", a, b], check=False)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def diff_unified(
    repo: Path, a: str, b: str, paths: Sequence[str] = (), context: int = 0
) -> str:
    """A unified diff between two commits. Empty when git cannot answer.

    ``context=0`` is the useful default here: the spec check attributes changed
    *lines* to functions, and a hunk carrying three lines of context on each side
    would claim the neighbours changed too.

    @param repo     the repository or worktree to ask
    @param a        the older commit
    @param b        the newer commit
    @param paths    pathspecs to limit the diff to, e.g. ``("*.py",)``
    @param context  lines of context per hunk
    """
    args = ["diff", "--unified={0}".format(int(context)), "--no-color", a, b]
    if paths:
        args += ["--"] + [str(p) for p in paths]
    proc = run(repo, args, check=False)
    return proc.stdout if proc.returncode == 0 else ""


def show_file(repo: Path, rev: str, path: str) -> Optional[str]:
    """One file's contents at one commit, or ``None`` when it is not there.

    @param repo  the repository or worktree to ask
    @param rev   the commit
    @param path  a repository-relative path
    """
    proc = run(repo, ["show", "{0}:{1}".format(rev, path)], check=False)
    return proc.stdout if proc.returncode == 0 else None


def branch_exists(repo: Path, name: str) -> bool:
    return run(
        repo, ["show-ref", "--verify", "--quiet", "refs/heads/{0}".format(name)], check=False
    ).returncode == 0


def head_commit(repo: Path) -> Optional[str]:
    return resolve_commit(repo, "HEAD")


def log_subjects(repo: Path, rev_range: str) -> List[str]:
    """Commit subjects, oldest first - what the stage commits actually say."""
    proc = run(repo, ["log", "--format=%s", "--reverse", rev_range], check=False)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def porcelain(repo: Path) -> Optional[str]:
    """``git status --porcelain -uall``, or ``None`` when this is not a usable repo.

    ``-uall`` matters: without it git collapses an untracked directory into a
    single ``?? .aidev/`` line, which would hide a plan stage writing its own
    approval file inside it.
    """
    if not git_available():
        return None
    try:
        proc = run(repo, ["status", "--porcelain", "-uall"], check=False)
    except GitError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def is_clean(repo: Path, tracked_only: bool = False) -> bool:
    status = porcelain(repo)
    if status is None:
        return True
    for line in status.splitlines():
        if not line.strip():
            continue
        if tracked_only and line.startswith("??"):
            continue
        return False
    return True


@dataclass
class WorktreeEntry:
    """One record of ``git worktree list --porcelain``."""

    path: Path
    branch: Optional[str] = None
    head: Optional[str] = None
    prunable: Optional[str] = None
    locked: bool = False
    bare: bool = False

    @property
    def stale(self) -> bool:
        return bool(self.prunable) or self.locked


def _same_path(a: Path, b: Path) -> bool:
    def norm(p: Path) -> str:
        try:
            resolved = Path(p).resolve()
        except OSError:
            resolved = Path(p)
        return os.path.normcase(str(resolved))

    return norm(a) == norm(b)


def list_worktrees(repo: Path) -> List[WorktreeEntry]:
    """Every worktree git knows about, including the main one (first)."""
    proc = run(repo, ["worktree", "list", "--porcelain"], check=False)
    if proc.returncode != 0:
        return []
    entries: List[WorktreeEntry] = []
    current: Optional[WorktreeEntry] = None
    for raw in proc.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            current = None
            continue
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "worktree":
            current = WorktreeEntry(path=Path(value))
            entries.append(current)
            continue
        if current is None:
            continue
        if key == "HEAD":
            current.head = value or None
        elif key == "branch":
            current.branch = value.split("refs/heads/", 1)[-1] if value else None
        elif key == "locked":
            current.locked = True
        elif key == "prunable":
            # Older git omits the reason; the annotation itself is the signal.
            current.prunable = value or "prunable"
        elif key == "bare":
            current.bare = True
    return entries


def find_worktree(repo: Path, path: Path) -> Optional[WorktreeEntry]:
    for entry in list_worktrees(repo):
        if _same_path(entry.path, path):
            return entry
    return None


def stale_worktrees(repo: Path) -> List[WorktreeEntry]:
    """Leftovers git still has registered. Reported, never cleaned."""
    return [entry for entry in list_worktrees(repo)[1:] if entry.stale]


# ------------------------------------------------------------------- mutation


def worktree_add(repo: Path, path: Path, branch: str, start_commit: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-b", branch, str(path), start_commit)


def worktree_remove(repo: Path, path: Path) -> None:
    """``--force`` because the worktree legitimately holds untracked build output."""
    git(repo, "worktree", "remove", "--force", str(path))


def delete_branch(repo: Path, name: str) -> Optional[str]:
    """Delete a branch and return the commit it pointed at, so it can be recovered."""
    sha = resolve_commit(repo, "refs/heads/{0}".format(name))
    git(repo, "branch", "-D", name)
    return sha


def commit_all(
    worktree: Path,
    message: str,
    allow_empty: bool = True,
    force_paths: Sequence[str] = (),
) -> Optional[str]:
    """Stage everything in ``worktree`` and commit it. Returns the new HEAD.

    ``force_paths`` is for the pipeline's own history snapshot: a project whose
    .gitignore covers ``.aidev/`` would otherwise commit stages with no record of
    what the slice was.

    ``--no-verify``: these commits are mechanical snapshots, not a human's work,
    and one pre-commit hook in the target repository must not be able to stop an
    unattended loop. The merge at the end is a human's commit and is not exempted.
    """
    git(worktree, "add", "-A")
    for path in force_paths:
        run(worktree, ["add", "-f", "--", str(path)], check=False)
    args = ["commit", "--no-verify", "-m", message]
    if allow_empty:
        args.insert(1, "--allow-empty")
    git(worktree, *args)
    return head_commit(worktree)


def reset_hard(worktree: Path, commit: str) -> None:
    """Move a branch and its working tree back to ``commit``.

    Untracked files are deliberately left alone - ``git clean`` is not run here.
    A worktree's node_modules/ and build output are derivatives nobody asked to
    lose, and reset does not touch them anyway. Tracked changes *are* thrown
    away, so the caller checks the tree is clean before calling this.
    """
    git(worktree, "reset", "--hard", commit)


def branch_remote(repo: Path, branch: str) -> Optional[str]:
    """The remote this branch tracks, or ``None`` when it tracks nothing."""
    return _config_value(repo, "branch.{0}.remote".format(branch)) or None


def remote_exists(repo: Path, name: str) -> bool:
    proc = run(repo, ["remote"], check=False)
    return name in [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def push_branch(repo: Path, remote: str, branch: str) -> str:
    """Push exactly one branch, by explicit refspec.

    The refspec is spelled out rather than left to ``push.default`` so nothing
    can ride along: whatever the repository is configured to do, only this one
    branch is named in the argv. No force, no ``--all``, no ``--tags``, and no
    upstream is set - pushing is a backup here, not a change of configuration.
    """
    return git(repo, "push", remote, "refs/heads/{0}:refs/heads/{0}".format(branch))


@dataclass
class MergeResult:
    ok: bool
    commit: Optional[str] = None
    conflicts: List[str] = field(default_factory=list)
    detail: str = ""


def merge_branch(repo: Path, branch: str, message: str) -> MergeResult:
    """Merge ``branch`` into whatever is checked out. A conflict is aborted, never resolved.

    The conflicted paths are read *before* the abort, because ``merge --abort``
    is what makes them unreadable again.
    """
    proc = run(repo, ["merge", "--no-ff", "--no-edit", "-m", message, branch], check=False)
    if proc.returncode == 0:
        return MergeResult(ok=True, commit=head_commit(repo), detail=proc.stdout.strip())
    conflicts = [
        line.strip()
        for line in run(
            repo, ["diff", "--name-only", "--diff-filter=U"], check=False
        ).stdout.splitlines()
        if line.strip()
    ]
    run(repo, ["merge", "--abort"], check=False)
    detail = (proc.stderr.strip() or proc.stdout.strip())
    return MergeResult(ok=False, conflicts=conflicts, detail=detail)


def revert_commit(repo: Path, commit: str, mainline: int = 1) -> MergeResult:
    """Put a revert of ``commit`` on whatever is checked out. Same result shape as a merge.

    ``-m 1`` because the commit being reverted is a merge: mainline 1 is the base
    branch, so what is undone is everything the slice branch brought in.

    No ``--no-verify``: unlike a stage snapshot this is a commit on the user's own
    branch, made because a human asked for it, and it goes through their hooks for
    the same reason the merge does.

    A conflict is aborted, never resolved, and the conflicted paths are read
    *before* the abort - exactly what ``merge_branch`` does and for the same reason.
    """
    proc = run(repo, ["revert", "-m", str(mainline), "--no-edit", commit], check=False)
    if proc.returncode == 0:
        return MergeResult(ok=True, commit=head_commit(repo), detail=proc.stdout.strip())
    conflicts = [
        line.strip()
        for line in run(
            repo, ["diff", "--name-only", "--diff-filter=U"], check=False
        ).stdout.splitlines()
        if line.strip()
    ]
    run(repo, ["revert", "--abort"], check=False)
    detail = (proc.stderr.strip() or proc.stdout.strip())
    return MergeResult(ok=False, conflicts=conflicts, detail=detail)


# ------------------------------------------------------------------ the plan


def default_worktree_root(repo: Path) -> Path:
    repo = Path(repo)
    return repo.parent / "{0}{1}".format(repo.name, DEFAULT_ROOT_SUFFIX)


@dataclass
class WorkspacePlan:
    """What would be created, before anything is."""

    path: Path
    branch: str
    base: Optional[str]
    base_commit: str
    start: Optional[str] = None


@dataclass
class Workspace:
    """A created worktree, as it is serialised into state.json."""

    path: Path
    branch: str
    base: Optional[str]
    base_commit: str
    # Where the branch was cut, when that is not the base itself. v0.4 chains one
    # epic slice onto the previous one's branch while every slice still merges
    # into the same base, so "where it forked" and "where it lands" are two facts.
    start: Optional[str] = None
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "branch": self.branch,
            "base": self.base,
            "base_commit": self.base_commit,
            "start": self.start,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Workspace"]:
        """``None`` for a v0.2 slice, or for a record too broken to act on."""
        if not isinstance(data, dict):
            return None
        path = data.get("path")
        branch = data.get("branch")
        if not isinstance(path, str) or not path or not isinstance(branch, str) or not branch:
            return None
        base = data.get("base")
        start = data.get("start")  # absent in every v0.3 record, and that is fine
        return cls(
            path=Path(path),
            branch=branch,
            base=base if isinstance(base, str) and base else None,
            base_commit=str(data.get("base_commit") or ""),
            start=start if isinstance(start, str) and start else None,
            created_at=str(data.get("created_at") or ""),
        )


def plan_workspace(
    repo: Path,
    slice_id: str,
    branch: str,
    base: Optional[str] = None,
    start: Optional[str] = None,
    root: Optional[Path] = None,
) -> WorkspacePlan:
    """Where the slice would live. ``base`` defaults to the repo's current HEAD.

    main is never assumed: a repository whose development line is
    ``windows-handoff-20260808`` must branch from what is checked out, not from
    a name this tool made up.

    ``start`` forks the branch somewhere other than the base without changing
    where it merges back to. Without it the two are the same thing, which is
    what every single-slice run does.
    """
    if base is not None and not resolve_commit(repo, base):
        raise GitError(["rev-parse", base], 1, "base ref not found: {0}".format(base))
    resolved_base = base if base is not None else current_branch(repo)
    if start is not None:
        commit = resolve_commit(repo, start)
        if commit is None:
            raise GitError(["rev-parse", start], 1, "start ref not found: {0}".format(start))
    else:
        commit = resolve_commit(repo, base if base is not None else "HEAD")
    if commit is None:
        raise GitError(
            ["rev-parse", "HEAD"],
            1,
            "{0} does not resolve to a commit (an empty repository has nothing to "
            "branch from - make one commit first)".format(base or "HEAD"),
        )
    directory = Path(root) if root is not None else default_worktree_root(repo)
    return WorkspacePlan(
        path=Path(directory) / slice_id,
        branch=branch,
        base=resolved_base,
        base_commit=commit,
        start=start,
    )


def _path_occupant(path: Path) -> str:
    try:
        entries = sorted(p.name for p in Path(path).iterdir())
    except OSError:
        return ""
    if not entries:
        return "(empty directory)"
    return ", ".join(entries[:5]) + (" ..." if len(entries) > 5 else "")


def blocking_reasons(repo: Path, plan: WorkspacePlan) -> List[str]:
    """Why this workspace cannot be created. Empty means: go ahead.

    A leftover is refused with its reason rather than reused or cleaned. Silently
    reusing someone else's worktree is how a slice ends up committing on top of
    work nobody asked it to touch.
    """
    reasons: List[str] = []
    registered = find_worktree(repo, plan.path)
    if Path(plan.path).exists():
        detail = "path already exists: {0}".format(plan.path)
        contents = _path_occupant(plan.path)
        if contents:
            detail += "\n    contains: {0}".format(contents)
        if registered is not None:
            detail += "\n    registered to {0}{1}".format(
                "branch {0}".format(registered.branch) if registered.branch else "a detached HEAD",
                ", prunable: {0}".format(registered.prunable) if registered.prunable else "",
            )
        detail += (
            "\n    aidev never reuses or cleans a worktree. Remove it yourself, "
            "or pass --worktree-root <path>."
        )
        reasons.append(detail)
    elif registered is not None:
        reasons.append(
            "path is registered as a worktree but missing on disk: {0}\n"
            "    run 'git worktree prune' yourself, or pass --worktree-root <path>.".format(
                plan.path
            )
        )
    if branch_exists(repo, plan.branch):
        reasons.append(
            "branch already exists: {0} (at {1})\n"
            "    delete it yourself, or discard the slice that owns it.".format(
                plan.branch, (resolve_commit(repo, plan.branch) or "?")[:7]
            )
        )
    return reasons


def create(repo: Path, plan: WorkspacePlan) -> Workspace:
    worktree_add(repo, plan.path, plan.branch, plan.base_commit)
    return Workspace(
        path=Path(plan.path),
        branch=plan.branch,
        base=plan.base,
        base_commit=plan.base_commit,
        start=plan.start,
        created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def destroy(repo: Path, ws: "Workspace") -> None:
    """Undo a workspace this process just created; failures are the caller's to report."""
    worktree_remove(repo, ws.path)
    delete_branch(repo, ws.branch)


def verify(repo: Path, ws: "Workspace") -> Optional[str]:
    """Why a recorded workspace can no longer be used, or ``None`` if it can.

    Nothing is re-created here: a worktree that came back would start from a
    different commit than the stage commits already on the branch.
    """
    if not Path(ws.path).is_dir():
        return "worktree is gone: {0}".format(ws.path)
    entry = find_worktree(repo, ws.path)
    if entry is None:
        return "worktree is no longer registered with {0}: {1}".format(repo, ws.path)
    if entry.branch != ws.branch:
        return "worktree {0} is on '{1}', not '{2}'".format(
            ws.path, entry.branch or "a detached HEAD", ws.branch
        )
    return None
