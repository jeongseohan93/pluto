"""Persistence: one directory per run (JSON + raw JSONL) plus a shared SQLite db.

    data/
    ├── aidev.db
    └── runs/
        └── 20260813-194500-doctor/
            ├── run.json
            ├── events.jsonl
            ├── telemetry.json
            ├── live.json      <- rewritten a few times a second while running
            ├── prompt.md
            └── stderr.log

Every JSON file is written to ``<name>.tmp`` and then ``os.replace``d into
place, so a reader (``aidev watch``) either sees the previous complete file or
the next complete one, never a half-written one.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

LIVE_FILENAME = "live.json"
LIVE_SCHEMA = 1

# How long an atomic write keeps trying when Windows refuses the replace because
# a reader still holds the file: 20 x 0.05s, so one second at the outside.
_REPLACE_RETRIES = 20
_REPLACE_RETRY_S = 0.05

_sleep = time.sleep  # same test seam as pipeline._sleep / cli._sleep

# Declared once so the schema and the migration can never drift apart.
RUN_COLUMNS = (
    ("id", "TEXT PRIMARY KEY"),
    ("project", "TEXT"),
    ("task", "TEXT"),
    ("phase", "TEXT"),
    ("repo", "TEXT"),
    ("prompt_path", "TEXT"),
    ("started_at", "TEXT"),
    ("finished_at", "TEXT"),
    ("status", "TEXT"),
    ("safety_profile", "TEXT"),
    ("session_id", "TEXT"),
    ("model", "TEXT"),
    ("turns", "INTEGER"),
    ("duration_ms", "INTEGER"),
    ("duration_api_ms", "INTEGER"),
    ("cost_usd", "REAL"),
    ("exit_code", "INTEGER"),
    ("input_tokens", "INTEGER"),
    ("cache_creation_tokens", "INTEGER"),
    ("cache_read_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
    ("peak_context_tokens", "INTEGER"),
    ("attributable_tokens", "INTEGER"),
    ("gap_tokens", "INTEGER"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
{run_columns}
);

CREATE TABLE IF NOT EXISTS tool_calls (
    run_id       TEXT,
    turn         INTEGER,
    tool         TEXT,
    target       TEXT,
    category     TEXT,
    result_bytes INTEGER,
    duration_ms  INTEGER
);

CREATE TABLE IF NOT EXISTS file_accesses (
    run_id    TEXT,
    path      TEXT,
    operation TEXT,
    count     INTEGER,
    bytes     INTEGER
);

CREATE TABLE IF NOT EXISTS phases (
    run_id           TEXT,
    phase            TEXT,
    started_at       TEXT,
    ended_at         TEXT,
    estimated_tokens INTEGER
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_file_accesses_run ON file_accesses(run_id);
CREATE INDEX IF NOT EXISTS idx_phases_run ON phases(run_id);
""".format(
    run_columns=",\n".join(
        "    {0:<22}{1}".format(name, column_type) for name, column_type in RUN_COLUMNS
    )
)


def default_data_dir() -> Path:
    """``$AIDEV_DATA_DIR``, else the orchestrator's own ``data/``, else ``./data``."""
    env = os.environ.get("AIDEV_DATA_DIR")
    if env:
        return Path(env).expanduser()
    package_root = Path(__file__).resolve().parent.parent
    if (package_root / "pyproject.toml").exists():
        return package_root / "data"
    return Path.cwd() / "data"


# ------------------------------------------------------------- recent repos
#
# Which repositories this tool has been pointed at. Only ever used to *offer*
# candidates when a command was given no --repo: naming the repo stays the
# human's, so nothing here is ever applied automatically.

RECENT_REPOS_FILENAME = "repos.json"
RECENT_REPOS_SCHEMA = 1
RECENT_REPOS_LIMIT = 10


def recent_repos_path(data_dir: Path) -> Path:
    return Path(data_dir) / RECENT_REPOS_FILENAME


def remember_repo(data_dir: Path, repo: Path) -> None:
    """Record a repo the tool was pointed at. Best effort: this never raises.

    A read-only or full data directory must not be what stops a pipeline
    command; the candidate list is a convenience, not state anything depends on.
    """
    try:
        path = recent_repos_path(data_dir)
        current = str(Path(repo))
        entry = {
            "path": current,
            "last_used": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        data = read_json_tolerant(path) or {}
        kept = [
            e
            for e in (data.get("repos") or [])
            if isinstance(e, dict)
            and isinstance(e.get("path"), str)
            and os.path.normcase(e["path"]) != os.path.normcase(current)
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            path,
            {"schema": RECENT_REPOS_SCHEMA, "repos": ([entry] + kept)[:RECENT_REPOS_LIMIT]},
        )
    except (OSError, ValueError):
        pass


def recent_repos(data_dir: Path, limit: int = 5) -> List[str]:
    """Most recently used first. Paths that no longer exist are dropped."""
    data = read_json_tolerant(recent_repos_path(data_dir)) or {}
    found: List[str] = []
    for entry in data.get("repos") or []:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("path")
        if not isinstance(raw, str) or not raw or raw in found:
            continue
        try:
            if not Path(raw).is_dir():
                continue
        except OSError:
            continue
        found.append(raw)
        if len(found) >= limit:
            break
    return found


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug[:40] or "run"


def make_run_id(task: str, now: Optional[datetime] = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return "{0}-{1}".format(stamp, slugify(task))


class RunStore:
    """Owns one ``data/runs/<run-id>/`` directory and its open file handles."""

    def __init__(self, runs_root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.dir = Path(runs_root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events = None
        self._stderr = None
        self._last_live_write = 0.0

    # paths
    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"

    @property
    def stderr_path(self) -> Path:
        return self.dir / "stderr.log"

    @property
    def prompt_path(self) -> Path:
        return self.dir / "prompt.md"

    @property
    def run_json_path(self) -> Path:
        return self.dir / "run.json"

    @property
    def telemetry_path(self) -> Path:
        return self.dir / "telemetry.json"

    @property
    def live_path(self) -> Path:
        return self.dir / LIVE_FILENAME

    # lifecycle
    def open(self) -> "RunStore":
        self._events = self.events_path.open("a", encoding="utf-8", newline="\n")
        self._stderr = self.stderr_path.open("a", encoding="utf-8", newline="\n")
        return self

    def close(self) -> None:
        for handle in (self._events, self._stderr):
            if handle is not None:
                try:
                    handle.flush()
                    handle.close()
                except (OSError, ValueError):
                    pass
        self._events = None
        self._stderr = None

    def __enter__(self) -> "RunStore":
        return self.open()

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # writes
    def write_raw_event(self, line: str) -> None:
        """Store the line exactly as Claude emitted it, before any parsing."""
        if self._events is None:
            self.open()
        self._events.write(line.rstrip("\r\n") + "\n")
        self._events.flush()

    def write_stderr(self, line: str) -> None:
        if self._stderr is None:
            self.open()
        self._stderr.write(line.rstrip("\r\n") + "\n")
        self._stderr.flush()

    def write_prompt(self, text: str) -> None:
        self.prompt_path.write_text(text, encoding="utf-8")

    def write_run_json(self, meta: Dict[str, Any]) -> None:
        _write_json(self.run_json_path, meta)

    def write_telemetry(self, data: Dict[str, Any]) -> None:
        _write_json(self.telemetry_path, data)

    def write_live(
        self,
        snapshot: Dict[str, Any],
        force: bool = False,
        min_interval: float = 0.3,
        now: Optional[float] = None,
    ) -> bool:
        """Publish the current snapshot for watchers. Throttled unless forced.

        Only the runner ever writes this file; watchers are read-only.
        """
        now = time.monotonic() if now is None else now
        if not force and (now - self._last_live_write) < min_interval:
            return False
        self._last_live_write = now
        payload = dict(snapshot)
        payload.update(
            {
                "schema": LIVE_SCHEMA,
                "run_dir": str(self.dir),
                "pid": os.getpid(),
                "updated_at": time.time(),
                "updated_at_iso": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        _write_json(self.live_path, payload)
        return True

    def read_telemetry(self) -> Dict[str, Any]:
        return json.loads(self.telemetry_path.read_text(encoding="utf-8"))

    def iter_raw_events(self) -> Iterable[str]:
        if not self.events_path.exists():
            return []
        with self.events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                yield line


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    write_json_atomic(path, data)


def write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Write to ``<path>.tmp`` then ``os.replace`` - readers never see a partial file.

    On Windows a reader that merely has the file open makes ``os.replace`` fail
    with WinError 5: CPython's ``open()`` asks for no delete sharing, so a single
    watcher reading live.json is enough. That is a momentary collision rather
    than a wait, so it is retried briefly (1 second at most) and then raised.

    The whole body is retried, not just the replace: ``tmp.open()`` can fail the
    same way, and rewriting the temporary file is safer than reusing a partially
    written one. ``SliceRecord.write_state`` retries on top of this, which is why
    state.json's worst case is 20x this second - only reachable if a handle is
    never released, and it still ends in the exception.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    for attempt in range(_REPLACE_RETRIES):
        try:
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp), str(path))
            return
        except PermissionError:
            if attempt + 1 >= _REPLACE_RETRIES:
                raise
            _sleep(_REPLACE_RETRY_S)


def read_json_tolerant(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON dict, returning ``None`` for anything unusable.

    A watcher polls files that another process is replacing underneath it, so
    every failure mode here is expected and must never raise: the file may be
    missing, momentarily locked (Windows), truncated, or not JSON at all.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def read_live(run_dir: Path) -> Optional[Dict[str, Any]]:
    return read_json_tolerant(Path(run_dir) / LIVE_FILENAME)


def read_telemetry(run_dir: Path) -> Optional[Dict[str, Any]]:
    return read_json_tolerant(Path(run_dir) / "telemetry.json")


# --------------------------------------------------- which run is still alive
#
# Measured 2026-08-18: `aidev watch` attached to a run that had died two days
# earlier. A killed runner leaves live.json saying ``running`` forever, so the
# status alone cannot answer "is anyone still working on this".

# The threshold for *choosing* a run, which has to be far more generous than the
# one for *displaying* it (reporter.STALE_AFTER_S, 10 seconds): a session inside
# one long Bash call emits no events at all, and verify.VERIFY_TIMEOUT_S allows
# exactly this much of it. Beyond that there is no runner, only a stale file.
RUN_STALE_AFTER_S = 1800.0


def live_age(
    live: Optional[Dict[str, Any]],
    run_dir: Optional[Path] = None,
    now: Optional[float] = None,
) -> Optional[float]:
    """Seconds since this run last published anything, or ``None`` if unmeasurable.

    @param live     the parsed live.json
    @param run_dir  where it lives, so the file's mtime can stand in for a
                    missing ``updated_at``
    @param now      the current time, for tests
    @flow  updated_at -> live.json mtime -> None
    주요 내부 변수: stamp(마지막 갱신 시각)
    """
    now = time.time() if now is None else now
    stamp = (live or {}).get("updated_at")
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        stamp = None
        if run_dir is not None:
            try:
                stamp = Path(run_dir).joinpath(LIVE_FILENAME).stat().st_mtime
            except OSError:
                stamp = None
    if stamp is None:
        return None
    return max(0.0, now - float(stamp))


def is_stale(
    live: Optional[Dict[str, Any]],
    run_dir: Optional[Path] = None,
    now: Optional[float] = None,
    stale_after: float = RUN_STALE_AFTER_S,
) -> bool:
    """Has nobody updated this run for long enough that its runner must be gone?

    A run whose age cannot be measured is never called stale: not knowing is not
    the same as knowing it is dead, and hiding a live run is the worse mistake.

    @param live         the parsed live.json
    @param run_dir      where it lives, for the mtime fallback
    @param now          the current time, for tests
    @param stale_after  how many seconds of silence is too many
    """
    age = live_age(live, run_dir, now)
    return age is not None and age > stale_after


def run_repo(run_dir: Path) -> Optional[str]:
    """Which repository (or worktree) this run worked in, or ``None`` if it never said.

    ``run.json`` is written when the run *starts*, so a run still going has it.

    @param run_dir  the run directory
    @flow  run.json config.repo -> telemetry.json repo -> None
    주요 내부 변수: meta(run.json), config(그 안의 실행 설정)
    """
    meta = read_json_tolerant(Path(run_dir) / "run.json")
    config = (meta or {}).get("config")
    if isinstance(config, dict):
        repo = config.get("repo")
        if isinstance(repo, str) and repo.strip():
            return repo
    telemetry = read_json_tolerant(Path(run_dir) / "telemetry.json")
    repo = (telemetry or {}).get("repo")
    return repo if isinstance(repo, str) and repo.strip() else None


def _normalised(path: Any) -> Optional[str]:
    """One comparable form of a path, the way ``workspace._same_path`` compares them.

    @param path  anything path-shaped, or empty for "nothing to compare"
    """
    if not path:
        return None
    try:
        resolved = Path(str(path)).resolve()
    except OSError:
        resolved = Path(str(path))
    return os.path.normcase(str(resolved))


def in_repo_scope(
    repo_of_run: Optional[str], repo: Path, worktree_root: Optional[Path] = None
) -> bool:
    """Does a run that worked in ``repo_of_run`` belong to ``repo``?

    A pipeline stage records the *worktree* it ran in, not the user's checkout,
    so the slice worktree root counts as inside the scope too. A run that never
    recorded where it worked is out: a run that cannot prove it belongs here is
    exactly the one that attached to the wrong repository.

    @param repo_of_run    what ``run_repo`` returned
    @param repo           the repository the scope is about
    @param worktree_root  where this repo's slice worktrees live, if known
    @flow  unrecorded -> False ; repo or below -> True ; worktree root or below -> True
    주요 내부 변수: target(비교 가능한 형태의 run 경로), roots(허용 범위)
    """
    target = _normalised(repo_of_run)
    if target is None:
        return False
    roots = [_normalised(repo), _normalised(worktree_root)]
    for root in roots:
        if root is None:
            continue
        if target == root or target.startswith(root + os.sep):
            return True
    return False


def runs_for_repo(
    runs_root: Path, repo: Optional[Path] = None, worktree_root: Optional[Path] = None
) -> List[Path]:
    """Run directories in scope, oldest first. Without ``repo`` that is all of them.

    @param runs_root      where runs are stored
    @param repo           limit to runs that worked in this repository
    @param worktree_root  where this repo's slice worktrees live, if known
    """
    dirs = list_run_dirs(runs_root)
    if repo is None:
        return dirs
    return [path for path in dirs if in_repo_scope(run_repo(path), repo, worktree_root)]


def find_running_run(
    runs_root: Path,
    repo: Optional[Path] = None,
    worktree_root: Optional[Path] = None,
    now: Optional[float] = None,
) -> Optional[Path]:
    """Newest run that says ``running`` *and* has been updated recently enough to mean it.

    @param runs_root      where runs are stored
    @param repo           limit to runs that worked in this repository
    @param worktree_root  where this repo's slice worktrees live, if known
    @param now            the current time, for tests
    @flow  newest first -> status running -> not stale -> that one
    주요 내부 변수: live(그 run의 live.json)
    """
    for path in reversed(runs_for_repo(runs_root, repo, worktree_root)):
        live = read_live(path)
        if live and live.get("status") == "running" and not is_stale(live, path, now):
            return path
    return None


class Database:
    """Cross-run comparison store. One row per run, plus flattened detail tables."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add any ``runs`` column a db created by an older version is missing."""
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(runs)")}
        for name, column_type in RUN_COLUMNS:
            if name in existing:
                continue
            # PRIMARY KEY cannot be added later; the rest are plain nullable columns.
            self.conn.execute(
                "ALTER TABLE runs ADD COLUMN {0} {1}".format(
                    name, column_type.replace(" PRIMARY KEY", "")
                )
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def save_run(self, telemetry: Dict[str, Any]) -> None:
        run_id = telemetry["run_id"]
        exact = telemetry.get("exact", {})
        observed = telemetry.get("observed", {})
        estimated = telemetry.get("estimated", {})

        with self.conn:
            self.conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self.conn.execute("DELETE FROM tool_calls WHERE run_id = ?", (run_id,))
            self.conn.execute("DELETE FROM file_accesses WHERE run_id = ?", (run_id,))
            self.conn.execute("DELETE FROM phases WHERE run_id = ?", (run_id,))

            tokens = exact.get("tokens") or {}
            gap = estimated.get("exact_vs_estimated") or {}
            self.conn.execute(
                """INSERT INTO runs (id, project, task, phase, repo, prompt_path,
                                     started_at, finished_at, status, safety_profile,
                                     session_id, model, turns, duration_ms, duration_api_ms,
                                     cost_usd, exit_code, input_tokens, cache_creation_tokens,
                                     cache_read_tokens, output_tokens, peak_context_tokens,
                                     attributable_tokens, gap_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    telemetry.get("project"),
                    telemetry.get("task"),
                    telemetry.get("phase"),
                    telemetry.get("repo"),
                    telemetry.get("prompt_path"),
                    telemetry.get("started_at"),
                    telemetry.get("finished_at"),
                    telemetry.get("status"),
                    telemetry.get("safety_profile"),
                    exact.get("session_id"),
                    exact.get("model"),
                    exact.get("num_turns"),
                    exact.get("duration_ms"),
                    exact.get("duration_api_ms"),
                    exact.get("total_cost_usd"),
                    exact.get("exit_code"),
                    tokens.get("input"),
                    tokens.get("cache_creation"),
                    tokens.get("cache_read"),
                    tokens.get("output"),
                    tokens.get("peak_context"),
                    gap.get("attributable_tokens"),
                    gap.get("gap_tokens"),
                ),
            )
            self.conn.executemany(
                """INSERT INTO tool_calls (run_id, turn, tool, target, category,
                                           result_bytes, duration_ms)
                   VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        call.get("turn"),
                        call.get("tool"),
                        call.get("target"),
                        call.get("category"),
                        call.get("result_bytes"),
                        call.get("duration_ms"),
                    )
                    for call in observed.get("calls", [])
                ],
            )
            self.conn.executemany(
                """INSERT INTO file_accesses (run_id, path, operation, count, bytes)
                   VALUES (?,?,?,?,?)""",
                [
                    (
                        run_id,
                        access.get("path"),
                        access.get("operation"),
                        access.get("count"),
                        access.get("bytes"),
                    )
                    for access in observed.get("file_accesses", [])
                ],
            )
            self.conn.execute(
                """INSERT INTO phases (run_id, phase, started_at, ended_at, estimated_tokens)
                   VALUES (?,?,?,?,?)""",
                (
                    run_id,
                    telemetry.get("phase"),
                    telemetry.get("started_at"),
                    telemetry.get("finished_at"),
                    estimated.get("attributable_tool_context_tokens"),
                ),
            )

    def recent_runs(self, limit: int = 20) -> List[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def phase_totals(self, limit: int = 20) -> List[sqlite3.Row]:
        cur = self.conn.execute(
            """SELECT p.phase                AS phase,
                      COUNT(*)               AS runs,
                      SUM(COALESCE(p.estimated_tokens, 0)) AS tokens,
                      SUM(COALESCE(r.cost_usd, 0))         AS cost_usd
                 FROM phases p
                 JOIN (SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT ?) recent
                   ON recent.id = p.run_id
                 JOIN runs r ON r.id = p.run_id
                GROUP BY p.phase
                ORDER BY tokens DESC""",
            (limit,),
        )
        return cur.fetchall()


def list_run_dirs(runs_root: Path) -> List[Path]:
    root = Path(runs_root)
    if not root.exists():
        return []
    dirs = [p for p in root.iterdir() if p.is_dir()]
    dirs.sort(key=lambda p: p.name)
    return dirs


def resolve_run_dir(runs_root: Path, run_id: str) -> Optional[Path]:
    """Accepts an exact id, a unique prefix, or ``last``."""
    dirs = list_run_dirs(runs_root)
    if not dirs:
        return None
    if run_id in ("last", "latest", "-"):
        return dirs[-1]
    exact = [p for p in dirs if p.name == run_id]
    if exact:
        return exact[0]
    matches = [p for p in dirs if p.name.startswith(run_id) or run_id in p.name]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return matches[-1]
    return None
