"""Finding the files, hashing them, and keeping the DB in step with them.

The cache rule is the whole design in one sentence: **the code is the truth**.
A file whose ``mtime`` and size are unchanged is not even read; one that was
touched is hashed, and only a changed hash costs a re-parse. Nothing here is
precious - deleting ``.aidev/graph/`` loses no information at all.

``.aidev/graph/.gitignore`` (containing ``*``) is written on every build. That
one line is what keeps a derived cache out of a slice branch: the pipeline
commits with ``git add -A``, and the readonly-stage cleanliness check reads
``git status``, so a directory that ignores itself is invisible to both - in
this repository *and* in whatever repository the tool is pointed at.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import List, Optional

from .. import __version__, workspace
from .db import GRAPH_SCHEMA, GraphDB
from .jsparse import parse_js
from .model import LANG_PY, SUFFIX_LANG, ParsedFile
from .pyparse import parse_python

# ``pipeline.AIDEV_DIRNAME`` says the same thing; importing it here would make a
# cycle, because pipeline imports this package.
AIDEV_DIRNAME = ".aidev"
GRAPH_DIRNAME = "graph"
DB_FILENAME = "graph.db"
# Written after a stage commit and read by the next query: 조회 시점에 갱신한다.
DIRTY_FILENAME = "dirty"
SELF_IGNORE = ".gitignore"

# Only used when git cannot list the files. git already knows all of this.
WALK_SKIP = frozenset(
    """.git .hg .svn .aidev node_modules .venv venv env dist build out __pycache__
    .pytest_cache .mypy_cache .ruff_cache .vite dist-electron coverage data""".split()
)


def graph_dir(repo: Path, override: Optional[Path] = None) -> Path:
    """Where this repository's graph lives: ``<repo>/.aidev/graph`` unless told otherwise.

    @param repo      the repository or worktree
    @param override  an explicit directory, for tests and side-by-side builds
    """
    if override is not None:
        return Path(override)
    return Path(repo) / AIDEV_DIRNAME / GRAPH_DIRNAME


def db_path(repo: Path, override: Optional[Path] = None) -> Path:
    """The graph DB file itself.

    @param repo      the repository or worktree
    @param override  an explicit graph directory
    """
    return graph_dir(repo, override) / DB_FILENAME


def ensure_graph_dir(directory: Path) -> Path:
    """Create the graph directory and the ``*`` .gitignore that hides it.

    @param directory  the graph directory
    @flow  mkdir -> write the self-ignore when it is missing or was emptied
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    ignore = directory / SELF_IGNORE
    if not ignore.exists() or not ignore.read_text(encoding="utf-8").strip():
        ignore.write_text("*\n", encoding="utf-8")
    return directory


def discover(repo: Path) -> List[str]:
    """The source files to parse, '/' separated and repository-relative.

    @param repo  the repository or worktree
    @flow  git ls-files -> else walk -> keep known suffixes that really exist
    주요 내부 변수: listed(후보 경로), found(살아남은 것들)
    """
    repo = Path(repo)
    listed = workspace.list_files(repo)
    if listed is None:
        listed = _walk(repo)
    found = set()
    for entry in listed:
        relative = str(entry).replace("\\", "/")
        if PurePosixPath(relative).suffix.lower() not in SUFFIX_LANG:
            continue
        if not (repo / relative).is_file():
            continue  # a tracked file that has been deleted on disk
        found.add(relative)
    return sorted(found)


def _walk(repo: Path) -> List[str]:
    """Every file under a repository git cannot list, minus the obvious noise.

    @param repo  the directory to walk
    @flow  os.walk -> prune the skip list in place -> collect relative paths
    주요 내부 변수: collected(모은 경로들)
    """
    collected: List[str] = []
    root = Path(repo)
    for current, directories, files in os.walk(str(root)):
        directories[:] = [name for name in directories if name not in WALK_SKIP]
        for name in files:
            relative = Path(current, name).relative_to(root)
            collected.append(relative.as_posix())
    return collected


def parse_text(text: str, path: str) -> ParsedFile:
    """Hand one file to the parser its suffix names, and never raise.

    @param text  the file's source
    @param path  its repository-relative path
    @flow  .py -> ast ; js/ts -> the scanner ; anything unexpected -> a failed file
    """
    lang = SUFFIX_LANG.get(PurePosixPath(path).suffix.lower(), "")
    try:
        if lang == LANG_PY:
            return parse_python(text, path)
        return parse_js(text, path)
    except Exception as exc:  # a parser bug must cost one file, never the build
        return ParsedFile(
            path=path, lang=lang, error="parser error: {0}: {1}".format(type(exc).__name__, exc)
        )


@dataclass
class UpdateReport:
    """What one build or update did, in numbers a single line can carry."""

    full: bool = False
    scanned: int = 0
    unchanged: int = 0
    reparsed: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    functions: int = 0

    @property
    def changed(self) -> int:
        """How many files moved in any sense: re-parsed, added or dropped. Takes no arguments."""
        return len(self.reparsed) + len(self.added) + len(self.removed)

    def one_line(self) -> str:
        """The whole report as one line, because a hook may only cost one line. Takes no arguments."""
        head = "graph: built {0} files".format(self.scanned) if self.full else "graph: {0} files".format(
            self.scanned
        )
        parts = ["{0} functions".format(self.functions)]
        if not self.full:
            parts.append(
                "{0} changed ({1} reparsed, {2} added, {3} removed)".format(
                    self.changed, len(self.reparsed), len(self.added), len(self.removed)
                )
            )
        if self.failed:
            parts.append("{0} failed".format(len(self.failed)))
        return "{0}, {1}".format(head, ", ".join(parts))


def build_repo(repo: Path, directory: Optional[Path] = None) -> UpdateReport:
    """Parse every source file of a repository from scratch.

    @param repo       the repository or worktree to read
    @param directory  where to put the graph, defaulting to ``<repo>/.aidev/graph``
    """
    return _scan(Path(repo), graph_dir(repo, directory), full=True)


def update_repo(repo: Path, directory: Optional[Path] = None) -> UpdateReport:
    """Re-parse only what changed since the last build.

    A missing DB, or one written by a schema this version does not know, is not
    an error here: it becomes a full build, because the DB is a cache.

    @param repo       the repository or worktree to read
    @param directory  where the graph lives
    @flow  no db or stale schema -> full build ; otherwise hash-compare and patch
    """
    directory = graph_dir(repo, directory)
    path = directory / DB_FILENAME
    if not path.exists():
        return _scan(Path(repo), directory, full=True)
    with GraphDB(path) as db:
        stale = db.schema_version() != GRAPH_SCHEMA
    if stale:
        return _scan(Path(repo), directory, full=True)
    return _scan(Path(repo), directory, full=False)


def _scan(repo: Path, directory: Path, full: bool) -> UpdateReport:
    """The one loop both verbs run: stat, hash, parse what moved, then resolve.

    @param repo       the repository or worktree
    @param directory  the graph directory
    @param full       start from an empty DB instead of patching the one there
    @flow  per file: same stat -> skip ; same hash -> touch ; else parse and replace
    주요 내부 변수: known(DB가 아는 파일들), report(무엇이 바뀌었는지)
    """
    ensure_graph_dir(directory)
    path = directory / DB_FILENAME
    if full and path.exists():
        path.unlink()
    report = UpdateReport(full=full)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with GraphDB(path) as db:
        known = {} if full else db.file_rows()
        seen = set()
        for relative in discover(repo):
            seen.add(relative)
            absolute = Path(repo) / relative
            try:
                stat = absolute.stat()
                row = known.get(relative)
                if row is not None and _same_stat(row, stat):
                    report.unchanged += 1
                    continue
                data = absolute.read_bytes()
            except OSError as exc:
                report.failed.append(relative)
                db.replace_file(
                    ParsedFile(path=relative, lang="", error="unreadable: {0}".format(exc)),
                    "",
                    0,
                    0.0,
                    now,
                )
                continue
            digest = hashlib.sha256(data).hexdigest()
            if row is not None and row["hash"] == digest:
                db.touch(relative, stat.st_size, stat.st_mtime, now)
                report.unchanged += 1
                continue
            parsed = parse_text(data.decode("utf-8", errors="replace"), relative)
            db.replace_file(parsed, digest, stat.st_size, stat.st_mtime, now)
            (report.added if row is None else report.reparsed).append(relative)
            if parsed.error:
                report.failed.append(relative)
        for gone in sorted(set(known) - seen):
            db.drop_file(gone)
            report.removed.append(gone)
        db.resolve_calls()
        report.scanned = len(seen)
        report.functions = db.stats()["functions"]
        db.set_meta(_meta(repo, full, now))
    clear_dirty(repo, directory)
    return report


def _same_stat(row: object, stat: os.stat_result) -> bool:
    """Is this the file the DB already read? Size and mtime, no hashing.

    @param row   the ``files`` row
    @param stat  what the filesystem says now
    """
    try:
        return int(row["size"]) == int(stat.st_size) and abs(
            float(row["mtime"]) - float(stat.st_mtime)
        ) < 1e-6
    except (KeyError, TypeError, ValueError):
        return False


def _meta(repo: Path, full: bool, now: str) -> dict:
    """The meta row: which commit and branch this graph was taken from.

    @param repo  the repository or worktree
    @param full  was this a full build?
    @param now   the timestamp both fields share
    @flow  always write updated_at ; built_at only when the DB was made from scratch
    """
    status = workspace.porcelain(repo)
    values = {
        "schema": GRAPH_SCHEMA,
        "repo": str(Path(repo).resolve()),
        "updated_at": now,
        "base_commit": workspace.head_commit(repo) or "",
        "branch": workspace.current_branch(repo) or "",
        "dirty": "1" if (status or "").strip() else "0",
        "aidev_version": __version__,
    }
    if full:
        values["built_at"] = now
    return values


# ------------------------------------------------------------- the lazy hook
#
# The pipeline does not rebuild the graph after a stage commit; it writes the
# marker below and moves on. Nothing in a slice reads the graph yet, so paying
# for a re-parse per commit would buy a cache nobody looked at. The first query
# after the commit pays instead, and pays only for the files that moved.


def mark_dirty(repo: Path, directory: Optional[Path] = None, reason: str = "") -> Path:
    """Note that the code moved, so the next query refreshes before it answers.

    @param repo       the repository or worktree
    @param directory  where the graph lives
    @param reason     what moved, for a human reading the file
    """
    target = ensure_graph_dir(graph_dir(repo, directory))
    marker = target / DIRTY_FILENAME
    marker.write_text(
        "{0}\n{1}\n".format(datetime.now().astimezone().isoformat(timespec="seconds"), reason),
        encoding="utf-8",
    )
    return marker


def is_dirty(repo: Path, directory: Optional[Path] = None) -> bool:
    """Has anything marked this graph stale since the last refresh?

    @param repo       the repository or worktree
    @param directory  where the graph lives
    """
    return (graph_dir(repo, directory) / DIRTY_FILENAME).exists()


def clear_dirty(repo: Path, directory: Optional[Path] = None) -> None:
    """Forget the marker - the graph now matches the files.

    @param repo       the repository or worktree
    @param directory  where the graph lives
    """
    marker = graph_dir(repo, directory) / DIRTY_FILENAME
    try:
        marker.unlink()
    except OSError:
        pass


def refresh_if_dirty(repo: Path, directory: Optional[Path] = None) -> Optional[UpdateReport]:
    """Update the graph when it was marked stale, and say what that cost.

    ``None`` means nothing was marked, so the caller answers from the DB as it
    stands. A failed update keeps the marker: the next query tries again.

    @param repo       the repository or worktree
    @param directory  where the graph lives
    @flow  no marker -> None ; marker -> update (which clears it) -> the report
    """
    if not is_dirty(repo, directory):
        return None
    return update_repo(repo, directory)
