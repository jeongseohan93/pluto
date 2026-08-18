"""Function graph: 파서 코어 + Function DB.

Three halves. The parser is tested on source written here as strings, the DB and
the query verbs on a tiny repository built in tmp_path, and the last few tests
point the whole thing at *this* repository - the one real answer to "does it work
on code nobody wrote for the test".
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aidev import graph
from aidev.cli import main
from aidev.graph import build as graph_build
from aidev.graph import jsparse, pyparse
from aidev.graph.db import GraphDB

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
    git,
    log,
    repo,
    requirement,
    worktree,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

PY_SOURCE = '''\
"""A module."""

import json


def top(a, b=2, *rest, key=None, **extra):
    """Do the top thing.

    @param a      the first
    @param b      the second
    @param *rest  the rest
    @param key    a keyword
    @param **extra  the leftovers
    @flow  a -> b
    @custom  이 태그는 도구가 해석하지 않는다
    """
    helper(a)
    return json.dumps(b)


# A comment block is a spec too.
# @param x  the only one
def commented(x):
    return top(x)


class Store:
    """A store."""

    def __init__(self, initial):
        self.value = initial

    async def save(self, item: str, force: bool = False) -> None:
        """Save one item.

        @param item   what to save
        @param force  overwrite silently
        """
        def inner():
            return sanitize(item)

        helper(inner())
'''

JSX_SOURCE = """\
import { useState } from 'react'

/**
 * The whole app shell.
 * @param props  what the host passes in
 * @custom 도구가 해석하지 않는 태그
 */
export function App(props) {
  const [n, setN] = useState(0)
  return <div className={`n-${n}`}>{render(n)}</div>
}

// One row of the table.
const Row = ({ label, value }) => (
  <tr><td>{label}</td><td>{value}</td></tr>
)

const twice = x => x * 2

export class Store {
  constructor(initial) {
    this.value = initial
  }

  /** Put one item in.
   *  @param item  the item
   */
  add(item) {
    this.value = merge(this.value, item)
    return this.value
  }

  reset = () => {
    this.value = null
  }
}
"""


def by_name(parsed):
    return {func.qualname: func for func in parsed.functions}


# ---------------------------------------------------------------- the parsers


def test_python_functions_carry_exact_coordinates():
    parsed = pyparse.parse_python(PY_SOURCE, "sample.py")

    functions = by_name(parsed)
    assert parsed.error == ""
    assert set(functions) >= {
        "top", "commented", "Store.__init__", "Store.save", "Store.save.inner"
    }
    top = functions["top"]
    assert top.lineno == 6
    assert top.end_lineno == 18
    assert top.params == ["a", "b", "*rest", "key", "**extra"]
    assert top.signature == "top(a, b=2, *rest, key=None, **extra)"
    assert top.kind == "function"
    assert functions["Store.save"].kind == "method"
    assert functions["Store.save"].signature == "save(self, item: str, force: bool = False)"


def test_python_spec_is_summary_core_tags_and_unknown_tags():
    functions = by_name(pyparse.parse_python(PY_SOURCE, "sample.py"))

    top = functions["top"]
    assert top.summary == "Do the top thing."
    tags = {(tag.tag, tag.core) for tag in top.tags}
    assert ("param", True) in tags and ("flow", True) in tags
    custom = [tag for tag in top.tags if tag.tag == "custom"]
    assert [(tag.value, tag.core) for tag in custom] == [("이 태그는 도구가 해석하지 않는다", False)]
    # docstring 없으면 def 위의 '#' 블록이 명세다 - specs와 같은 규약
    assert functions["commented"].summary == "A comment block is a spec too."
    assert [tag.tag for tag in functions["commented"].tags] == ["param"]


def test_python_calls_belong_to_the_function_that_wrote_them():
    functions = by_name(pyparse.parse_python(PY_SOURCE, "sample.py"))

    assert [(call.name, call.raw) for call in functions["top"].calls] == [
        ("helper", "helper"),
        ("dumps", "json.dumps"),
    ]
    # inner() is called by save; sanitize() is called by inner, not by save.
    assert [call.name for call in functions["Store.save"].calls] == ["helper", "inner"]
    assert [call.name for call in functions["Store.save.inner"].calls] == ["sanitize"]


def test_a_python_file_that_does_not_parse_reports_why():
    parsed = pyparse.parse_python("def broken(:\n    pass\n", "broken.py")

    assert parsed.functions == []
    assert "SyntaxError" in parsed.error and "line" in parsed.error


def test_jsx_declarations_are_found_with_their_ranges():
    parsed = jsparse.parse_js(JSX_SOURCE, "sample.jsx")

    functions = by_name(parsed)
    assert parsed.error == ""
    assert parsed.lang == "js"
    assert set(functions) == {"App", "Row", "twice", "Store.constructor", "Store.add", "Store.reset"}
    assert functions["App"].lineno == 8
    assert functions["App"].end_lineno == 11
    assert functions["App"].params == ["props"]
    assert functions["Row"].kind == "arrow"
    assert functions["Row"].params == ["{ label, value }"]
    assert functions["twice"].params == ["x"]
    assert functions["Store.add"].kind == "method"
    assert functions["Store.reset"].kind == "method"
    # The declaration's own line, not the blank line above the JSDoc block.
    assert (functions["Store.add"].lineno, functions["Store.add"].end_lineno) == (28, 31)


def test_jsdoc_is_read_the_same_way_as_a_docstring():
    functions = by_name(jsparse.parse_js(JSX_SOURCE, "sample.jsx"))

    app = functions["App"]
    assert app.summary == "The whole app shell."
    assert [(tag.tag, tag.core) for tag in app.tags] == [("param", True), ("custom", False)]
    assert functions["Row"].summary == "One row of the table."
    assert functions["Store.add"].summary == "Put one item in."


def test_jsx_calls_survive_templates_and_markup():
    functions = by_name(jsparse.parse_js(JSX_SOURCE, "sample.jsx"))

    assert [call.name for call in functions["App"].calls] == ["useState", "render"]
    assert [call.name for call in functions["Store.add"].calls] == ["merge"]


def test_tsx_is_the_same_parser_with_another_suffix():
    parsed = jsparse.parse_js(JSX_SOURCE, "sample.tsx")

    assert parsed.lang == "ts"
    assert "App" in by_name(parsed)


def test_typescript_annotations_do_not_confuse_the_body():
    source = (
        "export function shape(a: number, b: {x: number} = {x: 1}): {ok: boolean} {\n"
        "  return {ok: a > b.x}\n"
        "}\n"
    )

    functions = by_name(jsparse.parse_js(source, "shape.ts"))

    assert functions["shape"].lineno == 1
    assert functions["shape"].end_lineno == 3
    assert functions["shape"].params == ["a", "b"]


def test_literals_are_blanked_so_braces_inside_them_cannot_count():
    source = "const a = '{'\nconst b = `${x}}`\n// {\n/* } */\nconst re = /[{]/g\n"

    cleaned = jsparse.blank_strings(source)

    assert cleaned.count("{") == 0 and cleaned.count("}") == 0
    assert cleaned.splitlines()[0].startswith("const a =")
    assert len(cleaned) == len(source)


def test_jsx_tags_are_tags_and_not_regex_literals():
    # Both spellings of a slash in markup: a self-close nested inside a prop, and
    # two closing tags on one line. Reading either as a regex eats a brace.
    source = (
        "export function Row({ a, b }) {\n"
        "  return (\n"
        "    <KeyValue label={<Badge state={a} />} value={b} />\n"
        "  )\n"
        "}\n"
        "\n"
        "export function Cells({ a, b }) {\n"
        "  return <tr><td>{a}</td><td>{b}</td></tr>\n"
        "}\n"
    )

    parsed = jsparse.parse_js(source, "row.tsx")

    assert parsed.error == ""
    functions = by_name(parsed)
    assert (functions["Row"].lineno, functions["Row"].end_lineno) == (1, 5)
    assert (functions["Cells"].lineno, functions["Cells"].end_lineno) == (7, 9)


def test_a_js_file_the_scanner_cannot_read_is_one_failure_not_a_crash():
    parsed = jsparse.parse_js("function f() {\n  return 1\n", "broken.ts")

    assert parsed.functions == []
    assert "unbalanced braces" in parsed.error


# --------------------------------------------------------------- a tiny repo

OTHER_SOURCE = '''\
"""The other module."""


def helper(value):
    """Help with exactly one value.

    @param value  what to help with
    """
    return value


def top(a):
    """A second function of the same name, in another file.

    @param a  the only one
    @origin  이 태그도 도구가 해석하지 않는다
    """
    return helper(a)
'''

USER_SOURCE = '''\
"""Somebody who calls the ambiguous name."""


def use():
    """Call whichever top that is. Takes no arguments."""
    return top(1)
'''


@pytest.fixture
def mini(tmp_path):
    """A repository small enough to read: two languages, two failures, one duplicate name."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    root = tmp_path / "mini"
    (root / "pkg").mkdir(parents=True)
    (root / "web").mkdir()
    (root / "pkg" / "core.py").write_text(PY_SOURCE, encoding="utf-8")
    (root / "pkg" / "other.py").write_text(OTHER_SOURCE, encoding="utf-8")
    (root / "pkg" / "user.py").write_text(USER_SOURCE, encoding="utf-8")
    (root / "pkg" / "unreadable.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    (root / "web" / "app.jsx").write_text(JSX_SOURCE, encoding="utf-8")
    (root / "web" / "unbalanced.ts").write_text("function f() {\n  return 1\n", encoding="utf-8")
    (root / "README.md").write_text("mini\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.fixture
def built(mini, tmp_path):
    """The mini repo already indexed, with the graph beside it rather than inside it."""
    directory = tmp_path / "g"
    graph_build.build_repo(mini, directory)
    return directory


def cli(mini, directory, *args):
    return main(["graph"] + list(args) + ["--repo", str(mini), "--graph-dir", str(directory)])


def functions_of(directory, path):
    """Every function row of one file, keyed by qualname, as plain tuples."""
    with GraphDB(Path(directory) / "graph.db") as db:
        return {
            row["qualname"]: (row["id"], row["lineno"], row["summary"])
            for row in db.conn.execute("SELECT * FROM functions WHERE path = ?", (path,))
        }


def test_a_build_indexes_both_languages_and_survives_the_files_it_cannot_read(mini, tmp_path):
    report = graph_build.build_repo(mini, tmp_path / "g")

    assert report.full is True
    assert report.scanned == 6  # 4 python + 2 js/ts; README.md is not source
    assert sorted(report.failed) == ["pkg/unreadable.py", "web/unbalanced.ts"]
    assert report.functions > 0
    with GraphDB(tmp_path / "g" / "graph.db") as db:
        assert {row["path"] for row in db.failures()} == {
            "pkg/unreadable.py", "web/unbalanced.ts"
        }
        # the broken files did not take their neighbours with them
        assert [row["qualname"] for row in db.find("App")] == ["App"]
        assert len(db.find("top")) == 2


def test_status_reports_counts_coverage_failures_and_unknown_tags(mini, built, capsys):
    assert cli(mini, built, "status") == 0

    out = capsys.readouterr().out
    assert "schema 1" in out
    assert "files  4 parsed, 2 failed" in out
    assert re.search(r"funcs  \d+   spec \d+% \(\d+/\d+ non-test\)", out)
    assert "@param" in out and "@flow" in out
    assert "@custom" in out and "@origin" in out  # unknown tags are counted, never judged
    assert "failed pkg/unreadable.py  SyntaxError" in out
    assert "failed web/unbalanced.ts  unbalanced braces" in out


def test_the_graph_dir_ignores_itself_so_no_repository_ever_commits_it(mini, built):
    assert (built / ".gitignore").read_text(encoding="utf-8").strip() == "*"


def test_update_reparses_only_the_file_that_changed(mini, built):
    before = functions_of(built, "pkg/other.py")
    core = mini / "pkg" / "core.py"
    core.write_text(
        PY_SOURCE.replace("Do the top thing.", "Do the top thing, differently."), encoding="utf-8"
    )

    report = graph_build.update_repo(mini, built)

    assert report.full is False
    assert report.reparsed == ["pkg/core.py"]
    assert report.added == [] and report.removed == []
    assert report.unchanged == 5
    # the untouched file was not even re-inserted: same ids, same lines
    assert functions_of(built, "pkg/other.py") == before
    assert functions_of(built, "pkg/core.py")["top"][2] == "Do the top thing, differently."


def test_a_function_that_goes_away_goes_away(mini, built):
    (mini / "pkg" / "other.py").write_text('"""Emptied."""\n', encoding="utf-8")

    graph_build.update_repo(mini, built)

    assert functions_of(built, "pkg/other.py") == {}
    with GraphDB(built / "graph.db") as db:
        assert [row["path"] for row in db.find("top")] == ["pkg/core.py"]


def test_a_touched_file_with_the_same_bytes_is_not_reparsed(mini, built):
    core = mini / "pkg" / "core.py"
    os.utime(str(core), (core.stat().st_atime + 120, core.stat().st_mtime + 120))

    report = graph_build.update_repo(mini, built)

    assert report.reparsed == [] and report.unchanged == report.scanned


def test_a_deleted_file_is_dropped_from_the_graph(mini, built):
    (mini / "web" / "app.jsx").unlink()

    report = graph_build.update_repo(mini, built)

    assert report.removed == ["web/app.jsx"]
    with GraphDB(built / "graph.db") as db:
        assert db.find("App") == []


def test_a_schema_this_version_does_not_know_is_rebuilt_not_migrated(mini, built):
    with GraphDB(built / "graph.db") as db:
        db.set_meta({"schema": 99})

    report = graph_build.update_repo(mini, built)

    assert report.full is True
    with GraphDB(built / "graph.db") as db:
        assert db.schema_version() == graph.GRAPH_SCHEMA


# ------------------------------------------------------------- the query verbs


def test_show_prints_spec_signature_coordinates_and_both_call_directions(mini, built, capsys):
    assert cli(mini, built, "show", "commented") == 0

    out = capsys.readouterr().out
    assert "commented(x)  pkg/core.py:23-24  py" in out
    assert "A comment block is a spec too." in out
    assert "@param x  the only one" in out
    # the same file wins over the repository, so this one is pinned
    assert "calls 1: top pkg/core.py:6" in out


def test_show_echoes_unknown_tags_exactly_as_they_were_written(mini, built, capsys):
    assert cli(mini, built, "show", "App") == 0

    out = capsys.readouterr().out
    assert "@param props  what the host passes in" in out
    assert "@custom 도구가 해석하지 않는 태그" in out


def test_show_lists_every_definition_of_a_name_told_apart_by_path(mini, built, capsys):
    assert cli(mini, built, "show", "top") == 0

    out = capsys.readouterr().out
    assert "pkg/core.py:6" in out and "pkg/other.py:12" in out
    assert "Do the top thing." in out
    assert "A second function of the same name, in another file." in out


def test_callers_answers_with_coordinates(mini, built, capsys):
    assert cli(mini, built, "callers", "helper") == 0

    out = capsys.readouterr().out
    assert "helper  pkg/other.py:4  (1 definition)" in out
    assert "pkg/core.py:17  top" in out
    assert "pkg/other.py:18  top" in out


def test_calls_says_which_kind_of_ignorance_an_unresolved_edge_is(mini, built, capsys):
    assert cli(mini, built, "calls", "use") == 0

    out = capsys.readouterr().out
    assert "use  pkg/user.py:4" in out
    assert "(ambiguous: 2)" in out  # two files define top, so the engine refuses to pick


def test_summaries_is_a_table_of_contents_for_one_directory(mini, built, capsys):
    assert cli(mini, built, "summaries", "--dir", "web") == 0

    out = capsys.readouterr().out
    assert "functions in web" in out
    assert "web/app.jsx:8  App  The whole app shell." in out
    assert "pkg/" not in out


def test_a_name_nothing_defines_is_one_and_no_graph_at_all_is_two(mini, built, tmp_path, capsys):
    assert cli(mini, built, "show", "nowhere_at_all") == 1
    assert cli(mini, tmp_path / "empty", "show", "top") == 2

    assert "no graph yet" in capsys.readouterr().err


def test_bare_graph_prints_its_verbs_rather_than_the_top_level_help(capsys):
    assert main(["graph"]) == 2

    assert "summaries" in capsys.readouterr().out


# ------------------------------------------------------------ the lazy hook


def test_a_stage_commit_marks_the_graph_stale_without_parsing_anything(
    repo, tmp_path, claude_bin, log
):
    requirement(repo, front="approval: none")

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    tree = worktree(repo, tmp_path)
    marker = tree / ".aidev" / "graph" / "dirty"
    assert marker.exists()
    assert "stage commit" in marker.read_text(encoding="utf-8")
    # lazy: the hook wrote a marker and nothing else. Nobody has asked yet.
    assert not (tree / ".aidev" / "graph" / "graph.db").exists()
    # and neither the commit nor the readonly-stage cleanliness check can see it
    assert ".aidev/graph" not in git(tree, "ls-files").stdout
    assert git(tree, "status", "--porcelain", "-uall").stdout.strip() == ""


def test_the_first_query_after_a_commit_pays_for_the_refresh(
    repo, tmp_path, claude_bin, log, capsys
):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    tree = worktree(repo, tmp_path)
    (tree / "widget.py").write_text(
        'def widget():\n    """One widget. Takes no arguments."""\n    return 1\n', encoding="utf-8"
    )

    # There is no DB at all yet, so answering this at all means the marker drove
    # a build first - and the build saw a file written after the last commit.
    assert main(["graph", "show", "widget", "--repo", str(tree)]) == 0

    assert "widget.py:1" in capsys.readouterr().out
    assert (tree / ".aidev" / "graph" / "graph.db").exists()
    assert not (tree / ".aidev" / "graph" / "dirty").exists()


def test_a_broken_graph_hook_costs_one_warning_and_not_the_slice(
    repo, tmp_path, claude_bin, log, monkeypatch, capsys
):
    def explode(*args, **kwargs):
        raise RuntimeError("disk is on fire")

    monkeypatch.setattr(graph, "mark_dirty", explode)
    requirement(repo, front="approval: none")

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    assert "warning: graph not marked stale (disk is on fire)" in capsys.readouterr().out


def test_no_graph_leaves_the_worktree_without_one(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")

    assert main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--no-graph")
    ) == 0

    assert not (worktree(repo, tmp_path) / ".aidev" / "graph").exists()


# ------------------------------------------------------------- this repository


@pytest.fixture
def self_graph(tmp_path):
    """This very worktree, indexed - the only test whose input nobody wrote for it."""
    if not (REPO_ROOT / "aidev" / "pipeline.py").is_file():
        pytest.skip("not running from a source checkout")
    directory = tmp_path / "self"
    report = graph_build.build_repo(REPO_ROOT, directory)
    return directory, report


def line_of(path, needle):
    """The 1-based line a marker is on, so a moved function is not a failed test."""
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith(needle):
            return number
    raise AssertionError("{0} has no line starting {1!r}".format(path, needle))


def test_this_repository_builds_and_show_answers_with_the_real_coordinate(self_graph, capsys):
    directory, report = self_graph
    expected = line_of(REPO_ROOT / "aidev" / "pipeline.py", "def run_pipeline(")

    assert main(["graph", "show", "run_pipeline", "--repo", str(REPO_ROOT),
                 "--graph-dir", str(directory)]) == 0

    out = capsys.readouterr().out
    assert "aidev/pipeline.py:{0}".format(expected) in out
    assert report.functions > 500
    assert len(report.failed) < 0.05 * report.scanned


def test_callers_of_a_real_function_point_at_real_call_sites(self_graph, capsys):
    directory, _ = self_graph

    assert main(["graph", "callers", "merge_slice", "--repo", str(REPO_ROOT),
                 "--graph-dir", str(directory)]) == 0

    out = capsys.readouterr().out
    assert "aidev/pipeline.py:{0}".format(
        line_of(REPO_ROOT / "aidev" / "pipeline.py", "def merge_slice(")
    ) in out
    assert "_dispatch" in out  # the one place that calls it
    assert out.count("aidev/pipeline.py:") >= 2  # the definition and at least one call site


def test_the_js_parser_reads_this_repository_s_own_front_end(self_graph):
    directory, _ = self_graph

    with GraphDB(directory / "graph.db") as db:
        rows = db.conn.execute(
            "SELECT COUNT(*) FROM functions WHERE lang = 'ts' AND path LIKE 'desktop/src/%'"
        ).fetchone()

    assert rows[0] > 20
