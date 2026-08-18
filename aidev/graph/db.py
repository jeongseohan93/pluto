"""The Function DB: one SQLite file, ``.aidev/graph/graph.db``.

Why SQLite and not json, measured against the two things this store actually
does. **Asking**: an agent wants one name, and here that is one index lookup
(``functions(name)``, ``calls(callee_name)``); a json graph would have to be
parsed whole into memory to answer it. **Updating**: one changed file is a
``DELETE ... WHERE path=?`` plus a few dozen inserts, while json would
re-serialise the entire document for every stage commit. It is also stdlib, and
``aidev.storage.Database`` already sets the house pattern for it.

There is no migration code and there will not be: the DB is a consumable cache
of a truth that lives in the files. A schema this file does not recognise is
deleted and rebuilt, which costs one build and cannot corrupt anything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import specs
from .model import ParsedFile

# Bumped whenever the tables below change shape. An older file is thrown away.
GRAPH_SCHEMA = 1


def language_family(lang: str) -> str:
    """Which names may resolve to each other: Python is one world, js/ts another.

    ``.ts`` importing from ``.js`` is one codebase; ``str`` in Python and ``str``
    in TypeScript are two unrelated words that happen to be spelled alike.

    @param lang  a file's language label
    """
    return "py" if lang == "py" else "web"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    lang TEXT,
    hash TEXT,
    size INTEGER,
    mtime REAL,
    parsed INTEGER,
    error TEXT,
    funcs INTEGER,
    scanned_at TEXT
);

CREATE TABLE IF NOT EXISTS functions (
    id INTEGER PRIMARY KEY,
    qualname TEXT,
    name TEXT,
    path TEXT,
    lang TEXT,
    lineno INTEGER,
    end_lineno INTEGER,
    params TEXT,
    signature TEXT,
    kind TEXT,
    summary TEXT,
    spec TEXT,
    has_spec INTEGER,
    is_test INTEGER
);

CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    caller_id INTEGER,
    callee_name TEXT,
    callee_raw TEXT,
    lineno INTEGER,
    resolved_id INTEGER
);

CREATE TABLE IF NOT EXISTS tags (
    func_id INTEGER,
    tag TEXT,
    value TEXT,
    core INTEGER,
    lineno INTEGER
);

CREATE INDEX IF NOT EXISTS idx_func_name ON functions(name);
CREATE INDEX IF NOT EXISTS idx_func_qual ON functions(qualname);
CREATE INDEX IF NOT EXISTS idx_func_path ON functions(path);
CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_name);
CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_id);
CREATE INDEX IF NOT EXISTS idx_tags_func ON tags(func_id);
"""


class GraphDB:
    """Every read and write of the Function DB, and nothing about parsing."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A stage hook and a human at a prompt can arrive together; a short wait
        # is cheaper than either of them failing.
        self.conn = sqlite3.connect(str(self.path), timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        """Let go of the file, so another process can write it. Takes no arguments."""
        self.conn.close()

    def __enter__(self) -> "GraphDB":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # ----------------------------------------------------------------- meta

    def meta(self) -> Dict[str, str]:
        """Everything in the meta table, as a plain dict. Takes no arguments."""
        return {row["key"]: row["value"] for row in self.conn.execute("SELECT key, value FROM meta")}

    def set_meta(self, values: Dict[str, Any]) -> None:
        """Write meta keys, replacing what was there.

        @param values  key to value; every value is stored as text
        @flow  one transaction, one REPLACE per key
        """
        with self.conn:
            self.conn.executemany(
                "REPLACE INTO meta (key, value) VALUES (?, ?)",
                [(key, "" if value is None else str(value)) for key, value in values.items()],
            )

    def schema_version(self) -> int:
        """The schema this file was written with, 0 when it never said. Takes no arguments."""
        try:
            return int(self.meta().get("schema") or 0)
        except (TypeError, ValueError):
            return 0

    # ---------------------------------------------------------------- writes

    def file_rows(self) -> Dict[str, sqlite3.Row]:
        """Every file the DB knows, keyed by path - the cache side of the build. Takes no arguments."""
        return {row["path"]: row for row in self.conn.execute("SELECT * FROM files")}

    def drop_file(self, path: str) -> None:
        """Forget one file: its record, its functions, their tags and calls.

        @param path  the repository-relative path
        @flow  collect the function ids -> delete tags and calls -> delete the rows
        주요 내부 변수: ids(그 파일의 함수 id들)
        """
        ids = [
            row["id"] for row in self.conn.execute("SELECT id FROM functions WHERE path = ?", (path,))
        ]
        with self.conn:
            for func_id in ids:
                self.conn.execute("DELETE FROM tags WHERE func_id = ?", (func_id,))
                self.conn.execute("DELETE FROM calls WHERE caller_id = ?", (func_id,))
            self.conn.execute("DELETE FROM functions WHERE path = ?", (path,))
            self.conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def replace_file(
        self, parsed: ParsedFile, hash_: str, size: int, mtime: float, scanned_at: str
    ) -> None:
        """Put one freshly parsed file in, replacing whatever was there for that path.

        @param parsed      what the parser produced, error included
        @param hash_       the file's sha256, the cache key
        @param size        its size in bytes
        @param mtime       its modification time
        @param scanned_at  when this scan happened, ISO 8601
        @flow  drop the old rows -> file row -> per function: row, tags, calls
        주요 내부 변수: func_id(방금 삽입한 함수의 id)
        """
        self.drop_file(parsed.path)
        is_test = 1 if specs.looks_like_test(parsed.path) else 0
        with self.conn:
            self.conn.execute(
                """INSERT INTO files (path, lang, hash, size, mtime, parsed, error, funcs,
                                      scanned_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    parsed.path,
                    parsed.lang,
                    hash_,
                    int(size),
                    float(mtime),
                    0 if parsed.error else 1,
                    parsed.error,
                    len(parsed.functions),
                    scanned_at,
                ),
            )
            for function in parsed.functions:
                cursor = self.conn.execute(
                    """INSERT INTO functions (qualname, name, path, lang, lineno, end_lineno,
                                              params, signature, kind, summary, spec, has_spec,
                                              is_test)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        function.qualname,
                        function.name,
                        parsed.path,
                        parsed.lang,
                        function.lineno,
                        function.end_lineno,
                        ", ".join(function.params),
                        function.signature,
                        function.kind,
                        function.summary,
                        function.spec,
                        1 if function.summary else 0,
                        is_test,
                    ),
                )
                func_id = cursor.lastrowid
                self.conn.executemany(
                    "INSERT INTO tags (func_id, tag, value, core, lineno) VALUES (?,?,?,?,?)",
                    [
                        (func_id, tag.tag, tag.value, 1 if tag.core else 0, tag.line)
                        for tag in function.tags
                    ],
                )
                self.conn.executemany(
                    """INSERT INTO calls (caller_id, callee_name, callee_raw, lineno, resolved_id)
                       VALUES (?,?,?,?,NULL)""",
                    [(func_id, call.name, call.raw, call.lineno) for call in function.calls],
                )

    def resolve_calls(self) -> None:
        """Point every call at a function, or leave it pointing at nothing. Takes no arguments.

        The rule is deliberately timid: the one function of that name in the same
        file, else the one in the whole repository, else nothing. Zero matches
        means it is external; two or more means it is ambiguous, and a graph that
        guesses there is worse than one that says it does not know.

        A candidate must also speak the same language. Python's ``str(x)`` is not
        a TypeScript function called ``str``, and a name that means two different
        things in two different worlds is not one edge.

        It runs whole after every build and update, because re-parsing a file
        replaces its function ids and leaves every edge pointing at them stale.

        @flow  index by name -> per call: same language -> same file -> repo-wide -> NULL
        주요 내부 변수: by_name(이름별 후보), updates(한 번에 반영할 갱신들)
        """
        by_name: Dict[str, List[Any]] = {}
        for row in self.conn.execute("SELECT id, name, path, lang FROM functions"):
            by_name.setdefault(row["name"], []).append(
                (row["id"], row["path"], language_family(row["lang"]))
            )
        updates = []
        query = """SELECT c.id AS call_id, c.callee_name AS callee, f.path AS path,
                          f.lang AS lang
                   FROM calls c JOIN functions f ON f.id = c.caller_id"""
        for row in self.conn.execute(query):
            family = language_family(row["lang"])
            candidates = [
                (func_id, path)
                for func_id, path, other in (by_name.get(row["callee"]) or [])
                if other == family
            ]
            same_file = [func_id for func_id, path in candidates if path == row["path"]]
            if len(same_file) == 1:
                target = same_file[0]
            elif len(candidates) == 1:
                target = candidates[0][0]
            else:
                target = None
            updates.append((target, row["call_id"]))
        with self.conn:
            self.conn.executemany("UPDATE calls SET resolved_id = ? WHERE id = ?", updates)

    # ---------------------------------------------------------------- reads

    def find(self, name: str) -> List[sqlite3.Row]:
        """Functions a name could mean, all of them, nearest reading first.

        Exact qualname, then exact name, then a qualname ending in ``.name`` - so
        ``Store.save`` and ``save`` both work, and a name two files define comes
        back twice for the caller to print with its path.

        @param name  what the user typed
        @flow  qualname -> name -> '%.name', stopping at the first that answers
        주요 내부 변수: rows(찾은 것들)
        """
        for sql, parameter in (
            ("SELECT * FROM functions WHERE qualname = ? ORDER BY path, lineno", name),
            ("SELECT * FROM functions WHERE name = ? ORDER BY path, lineno", name),
            ("SELECT * FROM functions WHERE qualname LIKE ? ORDER BY path, lineno", "%." + name),
        ):
            rows = list(self.conn.execute(sql, (parameter,)))
            if rows:
                return rows
        return []

    def tags_of(self, func_id: int) -> List[sqlite3.Row]:
        """One function's spec tags, in the order they were written.

        @param func_id  the function's row id
        """
        return list(
            self.conn.execute(
                "SELECT tag, value, core, lineno FROM tags WHERE func_id = ? ORDER BY lineno, rowid",
                (func_id,),
            )
        )

    def calls_of(self, func_id: int) -> List[sqlite3.Row]:
        """What one function calls, with the target's coordinates when it is known.

        @param func_id  the caller's row id
        """
        return list(
            self.conn.execute(
                """SELECT c.lineno AS call_line, c.callee_name AS callee, c.callee_raw AS raw,
                          c.resolved_id AS target_id, t.path AS target_path,
                          t.lineno AS target_line, t.qualname AS target_qualname
                   FROM calls c LEFT JOIN functions t ON t.id = c.resolved_id
                   WHERE c.caller_id = ? ORDER BY c.lineno, c.id""",
                (func_id,),
            )
        )

    def callers(self, name: str) -> List[sqlite3.Row]:
        """Every call site that names this function, resolved or not.

        An unresolved edge is kept: when two files define ``main`` the engine
        refuses to pick one, but the call site is still where a human has to
        look.

        @param name  the callee's bare name
        """
        return list(
            self.conn.execute(
                """SELECT f.path AS path, f.qualname AS caller, f.lineno AS caller_line,
                          c.lineno AS call_line, c.resolved_id AS target_id
                   FROM calls c JOIN functions f ON f.id = c.caller_id
                   WHERE c.callee_name = ? ORDER BY f.path, c.lineno""",
                (name,),
            )
        )

    def name_counts(self, names: Sequence[str]) -> Dict[str, int]:
        """How many functions carry each of these names - what 'ambiguous' means.

        @param names  the names to count
        @flow  one query per distinct name, each on the name index
        주요 내부 변수: counts(이름 -> 개수)
        """
        counts: Dict[str, int] = {}
        for name in set(names):
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM functions WHERE name = ?", (name,)
            ).fetchone()
            counts[name] = int(row["n"]) if row else 0
        return counts

    def summaries(self, prefix: str = "", limit: int = 0) -> List[sqlite3.Row]:
        """Name and one-line summary of every function under a path prefix.

        @param prefix  a repository-relative directory or file, '' for everything
        @param limit   how many at most, 0 for all of them
        @flow  prefix? LIKE on the path index : everything -> ordered by coordinate
        """
        sql = "SELECT qualname, name, path, lineno, summary, kind FROM functions"
        parameters: List[Any] = []
        if prefix:
            sql += " WHERE path = ? OR path LIKE ?"
            parameters += [prefix, prefix.rstrip("/") + "/%"]
        sql += " ORDER BY path, lineno"
        if limit:
            sql += " LIMIT ?"
            parameters.append(int(limit))
        return list(self.conn.execute(sql, parameters))

    def failures(self) -> List[sqlite3.Row]:
        """The files that could not be parsed, with the reason each gave. Takes no arguments."""
        return list(
            self.conn.execute(
                "SELECT path, error FROM files WHERE parsed = 0 ORDER BY path"
            )
        )

    def unknown_tags(self) -> List[sqlite3.Row]:
        """Every non-core tag anyone wrote, with how often - collected, never judged. Takes no arguments."""
        return list(
            self.conn.execute(
                "SELECT tag, COUNT(*) AS n FROM tags WHERE core = 0 GROUP BY tag ORDER BY n DESC, tag"
            )
        )

    def _count(self, sql: str) -> int:
        """One ``SELECT COUNT(*)``, as a plain int.

        @param sql  the query, which must select exactly one number
        """
        row = self.conn.execute(sql).fetchone()
        return int(row[0] or 0) if row else 0

    def function_count(self) -> int:
        """How many functions are indexed right now. Takes no arguments."""
        return self._count("SELECT COUNT(*) FROM functions")

    def touch(self, path: str, size: int, mtime: float, scanned_at: str) -> None:
        """Record that a file is unchanged: same hash, new stat. No re-parse.

        @param path        the repository-relative path
        @param size        its size now
        @param mtime       its modification time now
        @param scanned_at  when this was checked
        """
        with self.conn:
            self.conn.execute(
                "UPDATE files SET size = ?, mtime = ?, scanned_at = ? WHERE path = ?",
                (int(size), float(mtime), scanned_at, path),
            )

    def stats(self) -> Dict[str, Any]:
        """The numbers ``graph status`` prints, in one pass over the tables. Takes no arguments.

        Test files are indexed (a test is a caller like any other) but left out
        of the spec coverage figure, which is about code the convention applies
        to. Both numbers are reported so neither has to be guessed at.

        @flow  files -> functions -> coverage -> tags -> call edges
        주요 내부 변수: totals(모아 담는 dict)
        """
        one = self._count
        by_lang = {
            row["lang"]: row["n"]
            for row in self.conn.execute(
                "SELECT lang, COUNT(*) AS n FROM files WHERE parsed = 1 GROUP BY lang"
            )
        }
        unresolved = list(
            self.conn.execute(
                """SELECT c.callee_name AS callee, COUNT(*) AS n FROM calls c
                   WHERE c.resolved_id IS NULL GROUP BY c.callee_name"""
            )
        )
        names = [row["callee"] for row in unresolved]
        counts = self.name_counts(names)
        ambiguous = sum(row["n"] for row in unresolved if counts.get(row["callee"], 0) > 1)
        totals: Dict[str, Any] = {
            "files_parsed": one("SELECT COUNT(*) FROM files WHERE parsed = 1"),
            "files_failed": one("SELECT COUNT(*) FROM files WHERE parsed = 0"),
            "files_by_lang": by_lang,
            "functions": one("SELECT COUNT(*) FROM functions"),
            "functions_non_test": one("SELECT COUNT(*) FROM functions WHERE is_test = 0"),
            "spec_non_test": one(
                "SELECT COUNT(*) FROM functions WHERE is_test = 0 AND has_spec = 1"
            ),
            "spec_all": one("SELECT COUNT(*) FROM functions WHERE has_spec = 1"),
            "core_tags": {
                row["tag"]: row["n"]
                for row in self.conn.execute(
                    "SELECT tag, COUNT(*) AS n FROM tags WHERE core = 1 GROUP BY tag ORDER BY tag"
                )
            },
            "unknown_tags": [(row["tag"], row["n"]) for row in self.unknown_tags()],
            "calls": one("SELECT COUNT(*) FROM calls"),
            "calls_resolved": one("SELECT COUNT(*) FROM calls WHERE resolved_id IS NOT NULL"),
            "calls_ambiguous": ambiguous,
        }
        totals["calls_unresolved"] = totals["calls"] - totals["calls_resolved"]
        covered, total = totals["spec_non_test"], totals["functions_non_test"]
        totals["coverage"] = (100.0 * covered / total) if total else 0.0
        return totals


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    """Plain dicts, for a caller that must not hold a cursor open.

    @param rows  what a query returned
    """
    return [dict(row) for row in rows]


def open_db(path: Path) -> Optional[GraphDB]:
    """Open a graph DB, or None when the file is not there or not readable.

    @param path  where graph.db lives
    @flow  missing -> None ; locked or corrupt -> None ; otherwise the handle
    """
    if not Path(path).exists():
        return None
    try:
        return GraphDB(path)
    except sqlite3.Error:
        return None
