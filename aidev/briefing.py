"""상차림: 세션이 발사되기 전에 엔진이 그래프에 먼저 물어본다.

"에이전트는 찾지 않는다. 좌표로 요청한다. 좌표는 그래프가 안다."

Measured 2026-08-15/18: a plan stage spent 20-59 turns *looking* for the code it
was about to plan against, implement paid $2-4 per slice re-finding the same
files, and the same file was read five to eleven times inside one session. None
of that is thinking - it is a table being set, over and over, by the most
expensive person in the room. So the engine sets it once, before the session
starts, out of a database it already has.

Three scales, coarse to fine, in that order on purpose: the coarse half does not
change between stages of a slice, so it sits at the front where a prompt cache
can hit it, and the part that changes with every plan sits at the back.

    1. REPO MAP      the whole repository at a distance - counts and front doors
    2. RELATED       the functions the requirement's own words name
    3. SCOPE         what the plan points at, in full: spec, edges, source
    4. ALREADY EXISTS  what implement is about to reinvent

There is no embedding, no ranking model and no semantic search here. Matching is
substring matching over identifiers, and that is the whole of it: a briefing
whose contents cannot be explained by looking at the words in the requirement is
one nobody can debug at three in the morning.

This module is one layer above ``aidev.graph`` - it knows about stages, plans
and requirements, which the data layer must not. It imports ``graph``,
``events`` and ``workspace``, and never ``pipeline``, which imports this one.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import events as ev
from . import graph, workspace

# Every section's ceiling, in estimated tokens. A briefing without ceilings
# spends exactly the tokens it was built to save, so the caps are not tuning -
# they are the point. Lines are dropped whole from the end: half a coordinate is
# worse than no coordinate.
REPO_MAP_TOKENS = 900
RELATED_TOKEN_CAP = 1200
SCOPE_TOKEN_CAP = 2500
REUSE_TOKEN_CAP = 500

RELATED_MAX = 30
REUSE_MAX = 20
ENTRY_POINTS = 10
# How many functions get the full high-resolution treatment, and how long an
# excerpt of one may run.
SCOPE_MAX = 12
EXCERPT_LINES = 40
# A file the plan names that holds no more than this is shown whole; a bigger
# one is named with the verb that would print it.
SCOPE_PATH_FUNCS = 8
# How many words the middle scale is allowed to search on.
KEYWORD_MAX = 14

#: Fixed, verbatim, and always last: the whole point of the briefing is that
#: there is a cheaper move than reading a file.
QUERY_FOOTER = (
    "이외는 aidev graph show/callers/calls/summaries로 요청하라.\n"
    "파일 통읽기 전에 조회 우선."
)

#: The one instruction the reuse section carries, verbatim from the requirement.
REUSE_RULE = "기존 함수 재사용 우선. 유사 기능 신설 시 사유 명시. 통합 리팩토링은 금지."

SECTION_REPO_MAP = "repo_map"
SECTION_RELATED = "related"
SECTION_SCOPE = "scope"
SECTION_REUSE = "reuse"

# ASCII identifiers only, which is deliberate: a Korean requirement contributes
# its code words and nothing else, and there is no tokenizer to be wrong about.
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# Words that match everything and therefore mean nothing here: English glue, and
# the handful of words this tool says in every second function it owns.
_STOPWORDS = frozenset(
    """the and for are with that this from into not all any one two new use used
    using when what which than then only also but its has have had will can may
    must should does done run runs way out off yes true false none self def
    class import return param flow why aidev test tests testing python file
    files line lines code text name names value values other others thing things
    here there they them their been being was were what where how each per via
    such same both more most less least very much many few every some
    """.split()
)

# Only the suffixes the graph actually parses. ``pipeline.plan_scale`` has a
# regex that looks like this one and is not this one: it answers "how big is
# this plan" and counts .md, .json and .yaml, none of which the graph indexes.
# The two are separate questions, and importing that one here would be a cycle.
_SCOPE_PATH_RE = re.compile(r"[\w./\\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs)\b")
# ``path.py:name`` - the coordinate shape the graph itself prints.
_SCOPE_COORD_RE = re.compile(r"[\w./\\-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs):([A-Za-z_]\w*)")
# `backticked` identifiers, and ``name()`` written as a call.
_SCOPE_BACKTICK_RE = re.compile(r"`([A-Za-z_][\w.]*)`")
_SCOPE_CALL_RE = re.compile(r"\b([A-Za-z_]\w{2,})\(\)")


@dataclass
class Briefing:
    """One stage's 상차림: the markdown, what it cost, and what it covered."""

    text: str = ""
    tokens: int = 0
    sections: List[str] = field(default_factory=list)
    scope: List[str] = field(default_factory=list)
    key: str = ""
    reused: bool = False
    graph: Dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> Dict[str, Any]:
        """The sidecar written beside the md: everything but the text itself.

        Kept apart from the markdown so the cache can be judged without parsing
        the document, and so a human can read the md without a header of
        bookkeeping in front of it.
        """
        return {
            "key": self.key,
            "tokens": self.tokens,
            "chars": len(self.text),
            "sections": list(self.sections),
            "scope": list(self.scope),
            "graph": dict(self.graph),
        }


def tokens_of(text: str) -> int:
    """A briefing's size in the only token estimate this project has.

    @param text  the rendered markdown
    """
    return ev.estimate_tokens(len((text or "").encode("utf-8")))


# --------------------------------------------------------------- freshness


def ensure_graph(
    repo: Path, graph_directory: Optional[Path] = None
) -> Optional[graph.UpdateReport]:
    """The graph a briefing is about to read, brought up to date first.

    A worktree is created without one - ``.aidev/graph/`` ignores itself, so it
    never rides a branch - which makes "there is no DB" the ordinary first case
    rather than an error.

    @param repo             the repository or worktree the briefing is about
    @param graph_directory  where the graph lives, for tests and side-by-side builds
    @flow  no DB -> a full build ; marked stale -> an incremental update ; else None
    """
    if not graph.db_path(repo, graph_directory).exists():
        return graph.update_repo(repo, graph_directory)
    return graph.refresh_if_dirty(repo, graph_directory)


# ------------------------------------------------------------------- cache


def cache_key(stage: str, commit: str, requirement: str, plan: str) -> str:
    """What makes two briefings the same one: the stage, the commit, the documents.

    The plan's digest is a superset of the scope - the scope is read *out of*
    the plan - so this covers "same commit and same scope" exactly, and covers
    the case a human edits plan.md at the gate, which a narrower key would miss.

    @param stage        which stage the briefing is for
    @param commit       HEAD of the worktree the briefing describes
    @param requirement  the requirement body
    @param plan         plan.md as it stands
    @flow  one sha1 over the digest of each part, so a long plan costs no more than a short one
    주요 내부 변수: digest(누적 해시)
    """
    digest = hashlib.sha1()
    for part in (stage, commit, requirement or "", plan or ""):
        digest.update(hashlib.sha1(part.encode("utf-8")).hexdigest().encode("ascii"))
    return digest.hexdigest()


def read_cached(md_path: Path, meta_path: Path, key: str) -> Optional[Briefing]:
    """The briefing already paid for, when it answers exactly this key.

    @param md_path    where the markdown was written
    @param meta_path  its sidecar
    @param key        the key this stage needs an answer to
    @flow  no key -> None ; unreadable or a different key -> None ; else the briefing
    주요 내부 변수: meta(사이드카 내용)
    """
    if not key:
        return None
    try:
        meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        text = Path(md_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("key") != key or not text.strip():
        return None
    return Briefing(
        text=text,
        tokens=int(meta.get("tokens") or tokens_of(text)),
        sections=list(meta.get("sections") or []),
        scope=list(meta.get("scope") or []),
        key=key,
        reused=True,
        graph=dict(meta.get("graph") or {}),
    )


def write_cache(md_path: Path, meta_path: Path, brief: Briefing) -> None:
    """Keep the briefing, so a retry, a repair and an amend do not buy it again.

    @param md_path    where the markdown goes
    @param meta_path  where its sidecar goes
    @param brief      what was just built
    """
    md_path, meta_path = Path(md_path), Path(meta_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(md_path, brief.text)
    _write_atomic(meta_path, json.dumps(brief.to_meta(), ensure_ascii=False, indent=2) + "\n")


def _write_atomic(path: Path, text: str) -> None:
    """The house write: a reader never sees half a file.

    @param path  where it goes
    @param text  what to write
    """
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(tmp), str(path))


# ------------------------------------------------------------------- build


def build(
    repo: Path,
    stage: str,
    requirement: str,
    plan: str = "",
    graph_directory: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> Briefing:
    """Set the table for one stage: refresh the graph, read it, render the md.

    The dirty marker is consulted *before* the cache is: the commit can be
    unchanged while the files under it have moved, and a marker that is not
    consumed would make the next query pay for the same refresh twice.

    @param repo             the worktree the session will run in
    @param stage            which stage is about to be launched
    @param requirement      the requirement body, the middle scale's vocabulary
    @param plan             plan.md, which is where the high resolution comes from
    @param graph_directory  where the graph lives, for tests and side-by-side builds
    @param cache_dir        where to keep the md and its sidecar, None for no cache
    @flow  stale? refresh -> cache hit? return it -> ensure the graph -> render -> keep
    주요 내부 변수: key(캐시 키), db(열린 그래프), brief(차려진 상)
    """
    repo = Path(repo)
    commit = workspace.head_commit(repo) or ""
    key = cache_key(stage, commit, requirement, plan)
    md_path = Path(cache_dir) / "{0}.md".format(stage) if cache_dir else None
    meta_path = Path(cache_dir) / "{0}.json".format(stage) if cache_dir else None

    if graph.is_dirty(repo, graph_directory):
        ensure_graph(repo, graph_directory)
    if md_path is not None and commit:
        # No commit means no way to prove two briefings describe the same tree,
        # and a briefing that cannot be proved current is built again.
        cached = read_cached(md_path, meta_path, key)
        if cached is not None:
            return cached

    ensure_graph(repo, graph_directory)
    db = graph.open_db(graph.db_path(repo, graph_directory))
    if db is None:
        return Briefing(key=key)
    try:
        text, sections, scope, stats = _render(db, repo, stage, requirement, plan)
    finally:
        db.close()

    brief = Briefing(
        text=text, tokens=tokens_of(text), sections=sections, scope=scope, key=key, graph=stats
    )
    if md_path is not None:
        write_cache(md_path, meta_path, brief)
    return brief


def _render(
    db: graph.GraphDB, repo: Path, stage: str, requirement: str, plan: str
) -> Tuple[str, List[str], List[str], Dict[str, Any]]:
    """The whole document, coarse scale first so a prompt cache can hit the front.

    @param db           the open graph
    @param repo         the worktree, which is where excerpts are read from
    @param stage        which stage this is for
    @param requirement  the requirement body
    @param plan         plan.md
    @flow  header -> repo map -> related -> scope (not plan) -> reuse (implement) -> footer
    주요 내부 변수: sections(실제로 존재한 절), scope(고해상이 다룬 대상)
    """
    stats = db.stats()
    meta = db.meta()
    lines = [
        "# BRIEFING  {0}".format(stage),
        "",
        "graph: {0} functions, {1} files, spec {2:.0f}%   (base {3}, {4})".format(
            stats["functions"],
            stats["files_parsed"],
            stats["coverage"],
            (meta.get("base_commit") or "-")[:7],
            meta.get("updated_at") or "-",
        ),
    ]
    sections: List[str] = []

    lines += [""] + _repo_map(db)
    sections.append(SECTION_REPO_MAP)

    words = keywords(requirement + ("\n" + plan if stage != "plan" else ""))
    related = _related(db, words)
    if related:
        lines += [""] + related
        sections.append(SECTION_RELATED)

    scope: List[str] = []
    names: List[str] = []
    if stage != "plan":
        # High resolution costs a file read per function, so it is bought only
        # once there is a plan naming what to spend it on.
        paths, names = scope_of(plan)
        scope = paths + names
        block, shown = _scope(db, repo, paths, names)
        if block:
            lines += [""] + block
            sections.append(SECTION_SCOPE)
        if stage == "implement":
            reuse = _reuse(db, names, shown)
            if reuse:
                lines += [""] + reuse
                sections.append(SECTION_REUSE)

    lines += ["", "---", QUERY_FOOTER, ""]
    summary = {
        "functions": stats["functions"],
        "files": stats["files_parsed"],
        "coverage": round(stats["coverage"], 1),
        "commit": meta.get("base_commit") or "",
    }
    return "\n".join(lines), sections, scope, summary


# ------------------------------------------------------ 1. the coarse scale


def _repo_map(db: graph.GraphDB) -> List[str]:
    """Directory counts and the most-called functions: the repository at a distance.

    @param db  the open graph
    @flow  per directory a line -> the front doors -> cut to the section's cap
    주요 내부 변수: lines(자를 후보 줄들)
    """
    lines = ["## 1. REPO MAP   (the whole repository, at a distance)"]
    for entry in db.directory_counts():
        lines.append(
            "{0:<28}{1:>4} files{2:>7} functions".format(
                entry["directory"], entry["files"], entry["functions"]
            )
        )
    doors = db.entry_points(ENTRY_POINTS)
    if doors:
        lines.append("entry points (most called):")
        for row in doors:
            lines.append(
                "  {0:<24}{1:<34}{2} callers".format(
                    _clip(row["qualname"] or row["name"], 23),
                    _coord(row["path"], row["lineno"]),
                    row["callers"],
                )
            )
    kept, dropped = _fit(lines, REPO_MAP_TOKENS)
    if dropped:
        kept.append("  +{0} more (aidev graph status)".format(dropped))
    return kept


# ------------------------------------------------------ 2. the middle scale


def keywords(text: str, limit: int = KEYWORD_MAX) -> List[str]:
    """The identifiers a document is about: split, lowercased, most frequent first.

    ``build_prompt`` yields ``build_prompt``, ``build`` and ``prompt``, so the
    whole name and its halves can each find something. Anything shorter than
    three characters, and anything on the stop list, is noise a substring search
    would drown in.

    @param text   the requirement, and the plan once there is one
    @param limit  how many words to keep
    @flow  identifiers -> snake/camel split -> drop noise -> count -> the top few
    주요 내부 변수: counts(단어 -> 등장 횟수)
    """
    counts: Dict[str, int] = {}
    for match in _WORD_RE.finditer(text or ""):
        word = match.group(0)
        pieces = [word.lower()]
        for part in word.split("_"):
            pieces += [piece.lower() for piece in _CAMEL_RE.findall(part)]
        for piece in pieces:
            if len(piece) < 3 or piece in _STOPWORDS:
                continue
            counts[piece] = counts.get(piece, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ordered[:limit]]


def _related(db: graph.GraphDB, words: Sequence[str]) -> List[str]:
    """The functions the requirement's own words name, one line and a coordinate each.

    @param db     the open graph
    @param words  what ``keywords`` pulled out
    @flow  nothing to search on -> [] ; else search, rank, cut, name the verb for the rest
    주요 내부 변수: rows(맞은 함수들), lines(자를 후보 줄들)
    """
    if not words:
        return []
    rows = db.search(words)
    if not rows:
        return []
    rows.sort(
        key=lambda row: (
            not row["exact"],
            -int(row["name_hits"]),
            -int(row["hits"]),
            row["path"],
            row["lineno"],
        )
    )
    total = len(rows)
    lines = [
        "## 2. RELATED   (what the requirement's words name)",
        "matched on: {0}".format(", ".join(words)),
    ]
    for row in rows[:RELATED_MAX]:
        lines.append(
            "  {0:<24}{1:<34}{2}".format(
                _clip(row["qualname"] or row["name"], 23),
                _coord(row["path"], row["lineno"]),
                _clip(row["summary"] or "-", 70),
            )
        )
    kept, dropped = _fit(lines, RELATED_TOKEN_CAP)
    left = total - (len(kept) - 2)
    if left > 0 or dropped:
        kept.append(
            "  +{0} more (aidev graph summaries --dir {1})".format(
                max(left, dropped), _busiest_dir(rows)
            )
        )
    return kept


def _busiest_dir(rows: Sequence[Dict[str, Any]]) -> str:
    """Where most of the matches live, so ``+N more`` names a verb worth running.

    @param rows  the matched functions
    @flow  count the parent of every match -> nothing matched -> '.' : the busiest one
    주요 내부 변수: counts(디렉터리 -> 개수)
    """
    counts: Dict[str, int] = {}
    for row in rows:
        parent = str(PurePosixPath(str(row["path"])).parent)
        counts[parent] = counts.get(parent, 0) + 1
    if not counts:
        return "."
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


# -------------------------------------------------------- 3. the fine scale


def scope_of(plan: str) -> Tuple[List[str], List[str]]:
    """What a plan points at: the files it names and the functions it names.

    Three shapes, because plans are written by three habits: a bare path, a
    ``path:name`` coordinate, and a name in backticks or written as a call.

    @param plan  plan.md
    @flow  coordinates -> paths -> backticked and called names -> de-duplicated, in order
    주요 내부 변수: paths(지목된 파일), names(지목된 함수)
    """
    text = plan or ""
    paths: List[str] = []
    names: List[str] = []
    for match in _SCOPE_COORD_RE.finditer(text):
        _add(names, match.group(1))
    for match in _SCOPE_PATH_RE.finditer(text):
        path = match.group(0).replace("\\", "/")
        while path.startswith("./"):
            path = path[2:]
        _add(paths, path)
    for regex in (_SCOPE_BACKTICK_RE, _SCOPE_CALL_RE):
        for match in regex.finditer(text):
            candidate = match.group(1)
            if _SCOPE_PATH_RE.match(candidate) or candidate.lower() in _STOPWORDS:
                continue
            _add(names, candidate)
    return paths, names


def _add(items: List[str], value: str) -> None:
    """Append once, keeping the order the document wrote them in.

    @param items  the list being collected
    @param value  what was just found
    @flow  empty or already there -> nothing ; otherwise append
    """
    if value and value not in items:
        items.append(value)


def _scope(
    db: graph.GraphDB, repo: Path, paths: Sequence[str], names: Sequence[str]
) -> Tuple[List[str], List[str]]:
    """The plan's own targets in full: spec record, edges, and real source.

    A file the plan names that the graph has never seen is said out loud rather
    than left silent - a new file is the ordinary case, not a failure.

    @param db     the open graph
    @param repo   the worktree, where the excerpts are read from
    @param paths  the files the plan named
    @param names  the functions the plan named
    @flow  per path: missing? say so : count it and maybe open it -> per name: the full card
    주요 내부 변수: rows(전문을 찍을 함수들), shown(이미 다룬 이름)
    """
    if not paths and not names:
        return [], []
    lines = ["## 3. SCOPE   (what the plan names, in full)"]
    rows: List[sqlite3.Row] = []
    seen: set = set()
    for path in paths:
        in_file = db.by_path([path])
        if not in_file:
            lines.append(
                "{0}  (not indexed yet - this file does not exist on this branch)".format(path)
            )
            continue
        if len(in_file) <= SCOPE_PATH_FUNCS:
            for row in in_file:
                if row["id"] not in seen:
                    seen.add(row["id"])
                    rows.append(row)
        else:
            lines.append(
                "{0}  {1} functions indexed   (aidev graph summaries --dir {0})".format(
                    path, len(in_file)
                )
            )
    for name in names:
        for row in db.find(name):
            if row["id"] not in seen:
                seen.add(row["id"])
                rows.append(row)

    cache: Dict[str, List[str]] = {}
    shown: List[str] = []
    for row in rows[:SCOPE_MAX]:
        shown.append(row["name"])
        lines.append("")
        lines += _card(db, repo, row, cache)
    if len(rows) > SCOPE_MAX:
        lines.append("")
        lines.append(
            "  +{0} more named function(s) - aidev graph show <name>".format(len(rows) - SCOPE_MAX)
        )
    kept, dropped = _fit(lines, SCOPE_TOKEN_CAP)
    if dropped:
        kept.append("  (cut at {0} tokens - aidev graph show <name> for the rest)".format(
            SCOPE_TOKEN_CAP
        ))
    return kept, shown


def _card(
    db: graph.GraphDB, repo: Path, row: sqlite3.Row, cache: Dict[str, List[str]]
) -> List[str]:
    """One function at full resolution: signature, spec as written, edges, source.

    @param db     the open graph
    @param repo   the worktree the source is read from
    @param row    the function's row
    @param cache  files already read this render, so one file costs one read
    @flow  header -> summary -> every tag -> calls -> callers -> the body itself
    주요 내부 변수: outgoing(부르는 것들), incoming(부르는 곳들)
    """
    lines = [
        "{0}  {1}-{2}  {3}".format(
            _titled(row), _coord(row["path"], row["lineno"]), row["end_lineno"], row["lang"]
        )
    ]
    if row["summary"]:
        lines.append("  {0}".format(row["summary"]))
    for tag in db.tags_of(row["id"]):
        lines.append("  @{0} {1}".format(tag["tag"], tag["value"]).rstrip())
    outgoing = [
        "{0} {1}".format(call["callee"], _coord(call["target_path"], call["target_line"]))
        for call in db.calls_of(row["id"])
        if call["target_path"]
    ]
    if outgoing:
        lines.append(_joined(outgoing, "calls", "graph calls " + row["name"]))
    incoming = [
        "{0} {1}".format(site["caller"], _coord(site["path"], site["call_line"]))
        for site in db.callers(row["name"])
    ]
    if incoming:
        lines.append(_joined(incoming, "callers", "graph callers " + row["name"]))
    lines += _excerpt(repo, row["path"], int(row["lineno"] or 0), int(row["end_lineno"] or 0), cache)
    return lines


def _excerpt(
    repo: Path, path: str, start: int, end: int, cache: Dict[str, List[str]]
) -> List[str]:
    """The function's own source, line-numbered, capped at ``EXCERPT_LINES``.

    Numbered because a coordinate is what the briefing is for: the agent should
    be able to name the line it means without opening the file.

    @param repo   the worktree
    @param path   the file, repository-relative
    @param start  the function's first line, 1-based
    @param end    its last line
    @param cache  files already read this render
    @flow  unreadable -> nothing ; else the slice, cut to the line cap
    주요 내부 변수: source(파일의 줄들), body(뽑아낸 줄들)
    """
    if start <= 0:
        return []
    source = cache.get(path)
    if source is None:
        try:
            source = (Path(repo) / path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            source = []
        cache[path] = source
    if not source:
        return []
    last = min(max(end, start), len(source))
    body = source[start - 1 : last]
    lines = [
        "  {0:>5} | {1}".format(start + index, text)
        for index, text in enumerate(body[:EXCERPT_LINES])
    ]
    if len(body) > EXCERPT_LINES:
        lines.append("  {0:>5} | ... {1} more lines".format("", len(body) - EXCERPT_LINES))
    return lines


# --------------------------------------------------------- 4. what exists


def _reuse(db: graph.GraphDB, names: Sequence[str], shown: Sequence[str]) -> List[str]:
    """Functions that already do something like what the plan is about to name.

    Deliberately crude: name tokens overlapping name tokens, counted. There is
    no edit distance and no tf-idf, because the job is to make a session look
    before it reinvents, and a list that is roughly right does that.

    @param db     the open graph
    @param names  the functions the plan named
    @param shown  the functions the scope section printed in full
    @flow  nothing in scope -> [] ; tokenise the names -> search -> drop what is already shown -> rank
    주요 내부 변수: words(이름에서 뽑은 토큰), rows(후보들)
    """
    # Both halves of the scope, because a plan that names a *file* has put every
    # function in it in scope just as surely as one that names a function.
    words = keywords(" ".join(list(names) + list(shown)), limit=KEYWORD_MAX)
    if not words:
        return []
    already = set(shown) | set(names)
    rows = [
        row
        for row in db.search(words)
        if int(row["name_hits"]) > 0 and row["name"] not in already
    ]
    if not rows:
        return []
    rows.sort(key=lambda row: (-int(row["name_hits"]), row["path"], row["lineno"]))
    lines = ["## 4. ALREADY EXISTS   (before you write a new one)"]
    for row in rows[:REUSE_MAX]:
        lines.append(
            "  {0:<24}{1:<34}{2}".format(
                _clip(row["qualname"] or row["name"], 23),
                _coord(row["path"], row["lineno"]),
                _clip(row["summary"] or "-", 70),
            )
        )
    kept, _ = _fit(lines, REUSE_TOKEN_CAP)
    kept.append(REUSE_RULE)
    return kept


# ---------------------------------------------------------------- plumbing


def _fit(lines: Sequence[str], cap: int) -> Tuple[List[str], int]:
    """Drop whole lines off the end until the section fits its token ceiling.

    Never cuts inside a line: a truncated coordinate is worse than a missing
    one, because the agent would go looking for the rest of it.

    @param lines  the section's candidate lines, most valuable first
    @param cap    the ceiling, in estimated tokens
    @flow  measure -> still too big? drop the last line -> repeat
    주요 내부 변수: kept(남긴 줄들)
    """
    kept = list(lines)
    while len(kept) > 1 and tokens_of("\n".join(kept)) > cap:
        kept.pop()
    return kept, len(lines) - len(kept)


def _coord(path: Any, line: Any) -> str:
    """One coordinate, the shape every line of a briefing ends with.

    @param path  a repository-relative path
    @param line  a 1-based line number
    """
    return "{0}:{1}".format(path, line)


def _titled(row: sqlite3.Row) -> str:
    """A signature under its qualname, so ``Store.save`` does not read as ``save``.

    @param row  a ``functions`` row
    @flow  no prefix -> the signature as stored ; a prefix -> swap the bare name for it
    """
    signature = row["signature"] or "{0}()".format(row["name"])
    qualname = row["qualname"] or row["name"]
    if qualname == row["name"]:
        return signature
    if signature.startswith(row["name"]):
        return qualname + signature[len(row["name"]) :]
    return signature


def _joined(items: Sequence[str], label: str, more: str) -> str:
    """A capped one-line list: the first few, then how many are left and where.

    @param items  the rendered entries, in order
    @param label  what the line is called, e.g. 'calls'
    @param more   the verb that would print the rest
    @flow  short enough -> all of them ; too long -> the first few and '+N'
    주요 내부 변수: unique(중복 없는 목록)
    """
    unique = list(dict.fromkeys(items))
    shown = unique[:6]
    if len(unique) > 6:
        shown.append("+{0} more ({1})".format(len(unique) - 6, more))
    return "  {0} {1}: {2}".format(label, len(unique), " | ".join(shown))


def _clip(text: Any, width: int) -> str:
    """One line of a table, never wider than its column.

    @param text   whatever the row held
    @param width  how much room it has
    @flow  collapse the whitespace -> fits? as is : cut and mark it
    """
    value = " ".join(str(text or "").split())
    return value if len(value) <= width else value[: width - 1] + "…"
