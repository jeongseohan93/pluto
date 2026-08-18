"""``aidev graph`` - the six verbs, and how their answers are printed.

Two builders (``build``, ``update``), one report (``status``) and four questions
(``show``, ``callers``, ``calls``, ``summaries``). The questions are the point:
an agent that can ask for a coordinate does not have to go looking for one.

Every answer is written for a reader who pays by the token: one or two lines per
item, a ``path:line`` on every one of them, and a long list cut off with ``+N``
and the name of the verb that would print the rest. A name two files define comes
back twice, told apart by its path - the engine never picks for you.

Exit codes follow the house reading: 0 answered, 1 nothing of that name, 2 the
question could not be asked at all (no graph yet, a stale schema, a locked file).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence

from .build import GRAPH_SCHEMA, build_repo, db_path, refresh_if_dirty, update_repo
from .db import GraphDB, open_db

# Past this a list stops informing and starts being a file dump; the verb that
# prints the whole thing is named instead.
MAX_INLINE = 12


def add_parser(sub: Any) -> Any:
    """Register ``aidev graph`` and its verbs on the top level subparsers.

    @param sub  the subparsers object from ``cli.build_parser``
    @flow  the graph parser (bare -> usage) -> one subparser per verb
    주요 내부 변수: cmd(graph 파서), verbs(그 아래 서브파서들)
    """
    cmd = sub.add_parser(
        "graph", help="the function graph: build it, then ask it where things are"
    )
    # Set before the verbs exist, so `aidev graph` alone prints *these* verbs
    # rather than the top-level help.
    cmd.set_defaults(func=_usage, graph_parser=cmd)
    verbs = cmd.add_subparsers(dest="graph_command")

    _common(verbs.add_parser("build", help="parse every source file from scratch")).set_defaults(
        func=cmd_build
    )
    _common(verbs.add_parser("update", help="re-parse only the files that changed")).set_defaults(
        func=cmd_update
    )
    _common(verbs.add_parser("status", help="what the graph knows, and what it could not read")
            ).set_defaults(func=cmd_status)

    show = _common(verbs.add_parser("show", help="spec, signature, coordinates, calls, callers"))
    show.add_argument("name", help="a function name or a dotted qualname")
    show.set_defaults(func=cmd_show)

    callers = _common(verbs.add_parser("callers", help="every place that calls this function"))
    callers.add_argument("name", help="a function name or a dotted qualname")
    callers.set_defaults(func=cmd_callers)

    calls = _common(verbs.add_parser("calls", help="everything this function calls"))
    calls.add_argument("name", help="a function name or a dotted qualname")
    calls.set_defaults(func=cmd_calls)

    summaries = _common(verbs.add_parser("summaries", help="name + one line, for a whole directory"))
    summaries.add_argument("--dir", dest="directory", default="", metavar="PATH",
                           help="limit to a directory or a single file (repo-relative or absolute)")
    summaries.add_argument("--limit", type=int, default=0, metavar="N",
                           help="print at most N (default 0: all of them)")
    summaries.set_defaults(func=cmd_summaries)
    return cmd


def _common(parser: Any) -> Any:
    """Give one verb the two options every verb takes.

    ``--graph-dir`` is for tests and side-by-side builds. There is deliberately
    no environment variable for it: one global would mix two repositories' graphs
    into one file, and every answer after that would be a lie.

    @param parser  the verb's own parser
    """
    parser.add_argument(
        "--repo", type=Path, default=None, help="the repository (default: current directory)"
    )
    parser.add_argument(
        "--graph-dir", type=Path, default=None, dest="graph_directory",
        help="where the graph lives (default: <repo>/.aidev/graph)",
    )
    return parser


def _usage(args: Any) -> int:
    """``aidev graph`` with no verb: print the verbs, and say it was not a question.

    @param args  the parsed arguments, which carry the graph parser itself
    """
    args.graph_parser.print_help()
    return 2


# ------------------------------------------------------------------ plumbing


def _repo_of(args: Any) -> Path:
    """The repository this invocation is about, resolved the way pipeline resolves it.

    @param args  the parsed arguments
    """
    return (getattr(args, "repo", None) or Path.cwd()).expanduser().resolve()


def _dir_of(args: Any) -> Optional[Path]:
    """The ``--graph-dir`` override, or None for the default place.

    @param args  the parsed arguments
    """
    override = getattr(args, "graph_directory", None)
    return Path(override).expanduser() if override else None


def _db(args: Any) -> Optional[GraphDB]:
    """The graph a question is asked of, brought up to date first if it was marked.

    This is where the pipeline's lazy hook is paid off: a stage commit only wrote
    a marker, so the first query after it re-parses the files that moved and then
    answers. Nothing was spent on a cache nobody looked at.

    @param args  the parsed arguments
    @flow  refresh if marked -> no file -> None ; wrong schema -> None ; else the handle
    주요 내부 변수: report(갱신이 실제로 한 일), path(graph.db 위치)
    """
    repo, directory = _repo_of(args), _dir_of(args)
    try:
        report = refresh_if_dirty(repo, directory)
    except Exception as exc:  # a stale cache must not stop a question being answered
        print("warning: graph refresh skipped ({0})".format(exc), file=sys.stderr)
    else:
        if report is not None:
            print(report.one_line(), file=sys.stderr)
    path = db_path(repo, directory)
    if not path.exists():
        print(
            "no graph yet - aidev graph build --repo {0}".format(repo), file=sys.stderr
        )
        return None
    db = open_db(path)
    if db is None:
        print("graph unreadable (locked or corrupt): {0}".format(path), file=sys.stderr)
        return None
    if db.schema_version() != GRAPH_SCHEMA:
        db.close()
        print(
            "graph was written by another schema - aidev graph build --repo {0}".format(repo),
            file=sys.stderr,
        )
        return None
    return db


def _relative(repo: Path, value: str) -> str:
    """A ``--dir`` argument in the form the DB stores paths: relative, '/' separated.

    @param repo   the repository
    @param value  what the user typed, absolute or relative
    @flow  empty -> everything ; absolute -> strip the repo ; else normalise the separators
    """
    if not value:
        return ""
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repo).as_posix()
        except ValueError:
            return candidate.as_posix()
    return str(PurePosixPath(str(value).replace("\\", "/"))).strip("/")


def _coord(path: str, line: Any) -> str:
    """One coordinate, the shape every line of every answer ends up carrying.

    @param path  a repository-relative path
    @param line  a 1-based line number
    """
    return "{0}:{1}".format(path, line)


def _titled(row: sqlite3.Row) -> str:
    """A signature under its qualname, so ``Store.save`` does not print as ``save``.

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
    """A capped one-line list: the first few, then how many were left and where they are.

    @param items  the rendered entries, already in order
    @param label  what the line is called, e.g. 'calls'
    @param more   the verb that would print the rest
    @flow  short enough -> all of them ; too long -> the first MAX_INLINE and '+N'
    """
    shown = list(items[:MAX_INLINE])
    if len(items) > MAX_INLINE:
        shown.append("+{0} more ({1})".format(len(items) - MAX_INLINE, more))
    return "  {0} {1}: {2}".format(label, len(items), " | ".join(shown))


# -------------------------------------------------------------- build verbs


def cmd_build(args: Any) -> int:
    """Parse the whole repository into a fresh Function DB.

    @param args  the parsed arguments: --repo, --graph-dir
    """
    report = build_repo(_repo_of(args), _dir_of(args))
    print(report.one_line())
    return 0


def cmd_update(args: Any) -> int:
    """Re-parse only what moved since the last build, and say what that was.

    @param args  the parsed arguments: --repo, --graph-dir
    @flow  update -> the one-line report -> the changed paths, when there are few
    """
    report = update_repo(_repo_of(args), _dir_of(args))
    print(report.one_line())
    changed = report.reparsed + report.added + report.removed
    if changed and len(changed) <= MAX_INLINE:
        for path in changed:
            print("  {0}".format(path))
    return 0


def cmd_status(args: Any) -> int:
    """What the graph holds: its base commit, its counts, its coverage, its failures.

    @param args  the parsed arguments: --repo, --graph-dir
    @flow  no graph -> 2 ; else meta, files, functions, tags, calls, then what failed
    주요 내부 변수: totals(stats()가 센 것들), meta(어느 커밋에서 떴는지)
    """
    db = _db(args)
    if db is None:
        return 2
    try:
        meta, totals = db.meta(), db.stats()
        print(
            "graph  {0}   (schema {1}, built {2})".format(
                db_path(_repo_of(args), _dir_of(args)),
                meta.get("schema", "?"),
                meta.get("built_at", "?"),
            )
        )
        print(
            "base   {0}  {1}{2}".format(
                (meta.get("base_commit") or "-")[:7],
                meta.get("branch") or "-",
                "  (worktree dirty)" if meta.get("dirty") == "1" else "",
            )
        )
        langs = "  ".join(
            "{0} {1}".format(lang or "?", count)
            for lang, count in sorted(totals["files_by_lang"].items())
        )
        print(
            "files  {0} parsed, {1} failed   ({2})".format(
                totals["files_parsed"], totals["files_failed"], langs or "none"
            )
        )
        core = " ".join(
            "@{0} {1}".format(tag, count) for tag, count in totals["core_tags"].items()
        )
        print(
            "funcs  {0}   spec {1:.0f}% ({2}/{3} non-test)   tags {4}".format(
                totals["functions"],
                totals["coverage"],
                totals["spec_non_test"],
                totals["functions_non_test"],
                core or "none",
            )
        )
        unknown = totals["unknown_tags"]
        print(
            "unknown tags  {0}".format(
                ", ".join("@{0} {1}".format(tag, count) for tag, count in unknown)
                if unknown
                else "none"
            )
        )
        print(
            "calls  {0} edges, {1} resolved, {2} unresolved (of which {3} ambiguous)".format(
                totals["calls"],
                totals["calls_resolved"],
                totals["calls_unresolved"],
                totals["calls_ambiguous"],
            )
        )
        for row in db.failures():
            print("failed {0}  {1}".format(row["path"], row["error"]))
    finally:
        db.close()
    return 0


# -------------------------------------------------------------- query verbs


def cmd_show(args: Any) -> int:
    """One function's whole card: spec, signature, coordinates, calls, callers.

    Every tag is echoed in source order exactly as it was written - the four core
    ones and every unknown ``@tag`` beside them. An unknown tag is data here, not
    an error and not an omission.

    @param args  the parsed arguments: name, --repo, --graph-dir
    @flow  no graph -> 2 ; no such name -> 1 ; else one block per definition
    주요 내부 변수: rows(그 이름이 가리킬 수 있는 함수들)
    """
    db = _db(args)
    if db is None:
        return 2
    try:
        rows = db.find(args.name)
        if not rows:
            print("no function named {0}".format(args.name), file=sys.stderr)
            return 1
        for index, row in enumerate(rows):
            if index:
                print("")
            _show_one(db, row)
    finally:
        db.close()
    return 0


def _show_one(db: GraphDB, row: sqlite3.Row) -> None:
    """Print one definition's card.

    @param db   the open graph
    @param row  the ``functions`` row to print
    @flow  header -> summary -> tags as written -> calls -> callers
    주요 내부 변수: outgoing(부르는 것들), incoming(부르는 곳들)
    """
    print(
        "{0}  {1}-{2}  {3}".format(
            _titled(row), _coord(row["path"], row["lineno"]), row["end_lineno"], row["lang"]
        )
    )
    if row["summary"]:
        print("  {0}".format(row["summary"]))
    for tag in db.tags_of(row["id"]):
        print("  @{0} {1}".format(tag["tag"], tag["value"]).rstrip())
    # Edges that landed somewhere first: a coordinate is the answer, a bare name
    # is only a hint, and the cap below should spend its room on answers.
    resolved, unresolved = [], []
    for call in db.calls_of(row["id"]):
        if call["target_path"]:
            resolved.append(
                "{0} {1}".format(call["callee"], _coord(call["target_path"], call["target_line"]))
            )
        else:
            unresolved.append("{0} ?".format(call["callee"]))
    outgoing = _unique(resolved) + _unique(unresolved)
    if outgoing:
        print(_joined(outgoing, "calls", "graph calls " + row["name"]))
    incoming = [
        "{0} {1}".format(site["caller"], _coord(site["path"], site["call_line"]))
        for site in db.callers(row["name"])
    ]
    if incoming:
        print(_joined(_unique(incoming), "callers", "graph callers " + row["name"]))


def _unique(items: Sequence[str]) -> List[str]:
    """The same list with repeats dropped and the order kept.

    @param items  rendered entries, in the order they were found
    """
    seen: Dict[str, bool] = {}
    for item in items:
        seen.setdefault(item, True)
    return list(seen)


def cmd_callers(args: Any) -> int:
    """Where a function is called from, one ``path:line`` per line.

    A call site whose target could not be pinned down is still printed: when two
    files define ``main`` the engine refuses to choose, but the call site is
    exactly where a human has to look.

    @param args  the parsed arguments: name, --repo, --graph-dir
    @flow  no graph -> 2 ; nothing of that name anywhere -> 1 ; else definitions then sites
    주요 내부 변수: rows(정의들), sites(호출 지점들)
    """
    db = _db(args)
    if db is None:
        return 2
    try:
        rows = db.find(args.name)
        names = _unique([row["name"] for row in rows]) or [args.name]
        sites = []
        for name in names:
            sites.extend(db.callers(name))
        if not rows and not sites:
            print("no function named {0}".format(args.name), file=sys.stderr)
            return 1
        print(_definition_line(args.name, rows))
        for site in sites:
            print("  {0}  {1}".format(_coord(site["path"], site["call_line"]), site["caller"]))
        if not sites:
            print("  (no call site in this repository)")
    finally:
        db.close()
    return 0


def _definition_line(name: str, rows: Sequence[sqlite3.Row]) -> str:
    """The header of a ``callers`` answer: the name, then where it is defined.

    @param name  what the user asked for
    @param rows  the definitions found, possibly none and possibly several
    @flow  none -> say so ; one -> its coordinate ; several -> all of them
    """
    if not rows:
        return "{0}  (no definition indexed)".format(name)
    coordinates = "  ".join(_coord(row["path"], row["lineno"]) for row in rows)
    return "{0}  {1}  ({2} definition{3})".format(
        name, coordinates, len(rows), "" if len(rows) == 1 else "s"
    )


def cmd_calls(args: Any) -> int:
    """What a function calls, in the order the calls are written.

    @param args  the parsed arguments: name, --repo, --graph-dir
    @flow  no graph -> 2 ; no such name -> 1 ; else one block per definition
    주요 내부 변수: rows(그 이름의 정의들)
    """
    db = _db(args)
    if db is None:
        return 2
    try:
        rows = db.find(args.name)
        if not rows:
            print("no function named {0}".format(args.name), file=sys.stderr)
            return 1
        for index, row in enumerate(rows):
            if index:
                print("")
            _calls_of_one(db, row)
    finally:
        db.close()
    return 0


def _calls_of_one(db: GraphDB, row: sqlite3.Row) -> None:
    """Print one definition's outgoing edges, unresolved ones included.

    An edge with no target says which kind of ignorance it is: nothing of that
    name is indexed (external), or several things are (ambiguous).

    @param db   the open graph
    @param row  the caller's ``functions`` row
    @flow  header -> per call: resolved coordinate, or why there is none
    주요 내부 변수: edges(호출들), counts(미해석 이름별 후보 수)
    """
    print("{0}  {1}".format(row["qualname"], _coord(row["path"], row["lineno"])))
    edges = db.calls_of(row["id"])
    counts = db.name_counts([edge["callee"] for edge in edges if edge["target_id"] is None])
    if not edges:
        print("  (calls nothing this parser can see)")
        return
    for edge in edges:
        if edge["target_id"] is not None:
            where = _coord(edge["target_path"], edge["target_line"])
        elif counts.get(edge["callee"], 0) > 1:
            where = "(ambiguous: {0})".format(counts[edge["callee"]])
        else:
            where = "(unresolved)"
        print("  {0:<6}{1:<28}{2}".format(edge["call_line"], edge["raw"] or edge["callee"], where))


def cmd_summaries(args: Any) -> int:
    """Name and one-line summary of every function in a directory: a table of contents.

    @param args  the parsed arguments: --dir, --limit, --repo, --graph-dir
    @flow  no graph -> 2 ; nothing in that scope -> 1 ; else a line each
    주요 내부 변수: prefix(정규화된 범위), rows(그 범위의 함수들)
    """
    db = _db(args)
    if db is None:
        return 2
    try:
        prefix = _relative(_repo_of(args), getattr(args, "directory", "") or "")
        rows = db.summaries(prefix, max(0, int(getattr(args, "limit", 0) or 0)))
        if not rows:
            print(
                "no functions in {0}".format(prefix or "this graph"), file=sys.stderr
            )
            return 1
        print("{0} functions in {1}".format(len(rows), prefix or "the whole repository"))
        for row in rows:
            print(
                "{0}  {1}  {2}".format(
                    _coord(row["path"], row["lineno"]), row["qualname"], row["summary"] or "-"
                )
            )
    finally:
        db.close()
    return 0
