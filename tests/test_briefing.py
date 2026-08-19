"""브리핑 생성기: LOD 3단, 상한, 신선도, 캐시.

The renderer is tested against the same tiny repository the graph tests use -
two languages, a duplicate name, two files that do not parse - because a
briefing is only worth anything on a repository somebody did not tidy first.
"""

import json
from pathlib import Path

import pytest

from aidev import briefing, graph
from aidev.graph import build as graph_build

from test_graph import mini  # noqa: F401  (pytest fixture, used by name)
from test_pipeline import commit_all

REPO_ROOT = Path(__file__).resolve().parent.parent

PLAN = """\
# Plan

1. `pkg/core.py` - change `top` so it does the top thing twice.
2. Add `save_user()` beside it.
3. Leave pkg/other.py:helper alone.
"""

# Names only, no paths: the reuse section can only speak when the plan leaves
# something outside its own scope for it to point at.
NAMED_PLAN = "Change `helper` and add `save_thing()`.\n"


@pytest.fixture
def indexed(mini, tmp_path):
    """The mini repo with its graph beside it, which is what a briefing reads."""
    directory = tmp_path / "g"
    graph_build.build_repo(mini, directory)
    return directory


@pytest.fixture
def committed(mini):
    """The same repo with a commit in it - without one the cache cannot prove a thing."""
    return commit_all(mini, "mini")


def build(mini, indexed, stage, requirement="top helper", plan=PLAN, **kwargs):
    return briefing.build(
        mini, stage, requirement, plan, graph_directory=indexed, **kwargs
    )


# ------------------------------------------------------------------ the three scales


def test_the_briefing_has_all_three_scales(mini, indexed):
    """plan gets the two coarse ones; implement pays for the fine one as well."""
    plan_brief = build(mini, indexed, "plan", plan=NAMED_PLAN)
    implement_brief = build(mini, indexed, "implement", plan=NAMED_PLAN)

    assert plan_brief.sections == [briefing.SECTION_REPO_MAP, briefing.SECTION_RELATED]
    assert "## 1. REPO MAP" in plan_brief.text
    assert "## 2. RELATED" in plan_brief.text
    assert "## 3. SCOPE" not in plan_brief.text

    assert implement_brief.sections == [
        briefing.SECTION_REPO_MAP,
        briefing.SECTION_RELATED,
        briefing.SECTION_SCOPE,
        briefing.SECTION_REUSE,
    ]
    assert "## 3. SCOPE" in implement_brief.text
    assert "## 4. ALREADY EXISTS" in implement_brief.text
    # the coarse scale really is coarse: counts and front doors, not a file dump
    assert "pkg/" in plan_brief.text and "files" in plan_brief.text


def test_the_footer_is_verbatim_and_last(mini, indexed):
    """The query invitation is fixed text, and nothing is allowed to follow it."""
    text = build(mini, indexed, "implement").text

    assert briefing.QUERY_FOOTER in text
    assert text.rstrip().endswith(briefing.QUERY_FOOTER)
    assert "aidev graph show/callers/calls/summaries로 요청하라." in text
    assert "파일 통읽기 전에 조회 우선." in text


def test_related_matches_identifiers_not_magic(mini, indexed):
    """Substring matching over identifiers, and nothing that cannot be explained."""
    hit = build(mini, indexed, "plan", requirement="helper 를 고친다", plan="")
    miss = build(mini, indexed, "plan", requirement="문서를 다듬는다", plan="")

    assert "helper" in hit.text
    assert "pkg/other.py:" in hit.text
    assert "matched on: helper" in hit.text
    # A requirement with no ASCII identifier in it calls nothing: there is no
    # embedding here to invent a neighbour.
    assert briefing.SECTION_RELATED not in miss.sections


def test_scope_carries_the_spec_record_and_a_real_excerpt(mini, indexed):
    """고해상: the spec as written, the edges, and the function's own source."""
    text = build(mini, indexed, "implement").text
    scope = text.split("## 3. SCOPE", 1)[1].split("## 4.", 1)[0]

    assert "top(a, b=2, *rest, key=None, **extra)" in scope
    assert "pkg/core.py:6-18" in scope
    assert "@param a      the first" in scope
    assert "@flow  a -> b" in scope
    # the excerpt is the real file, carried with its line numbers
    assert "6 | def top(a, b=2, *rest, key=None, **extra):" in scope
    assert "calls" in scope


def test_a_file_the_plan_names_that_does_not_exist_yet_is_said_out_loud(mini, indexed):
    """A new file is the ordinary case, so silence about it would be the defect."""
    text = build(mini, indexed, "implement", plan="Add `pkg/brand_new.py`.").text

    assert "pkg/brand_new.py  (not indexed yet" in text


def test_reuse_candidates_only_for_implement(mini, indexed):
    """'이미 있는 것' is implement's question; plan has nothing to reinvent yet."""
    implement_brief = build(mini, indexed, "implement", plan="Add `save_item()`.")
    plan_brief = build(mini, indexed, "plan", plan="Add `save_item()`.")

    exists = implement_brief.text.split("## 4. ALREADY EXISTS", 1)[1]
    assert "save" in exists  # Store.save is already there
    assert exists.rstrip().endswith(briefing.QUERY_FOOTER)
    assert briefing.REUSE_RULE in exists
    assert "기존 함수 재사용 우선" in exists

    assert "ALREADY EXISTS" not in plan_brief.text


def test_every_section_respects_its_token_cap(mini, indexed):
    """A briefing without ceilings spends the tokens it was built to save."""
    lines = ['"""Many."""\n']
    for index in range(200):
        lines.append(
            "def handler_{0}(request, session):\n"
            '    """Handle request {0} in the handler pipeline.\n\n'
            "    @param request  the request\n"
            "    @param session  the session\n"
            '    """\n'
            "    return {0}\n".format(index)
        )
    (mini / "pkg" / "many.py").write_text("\n".join(lines), encoding="utf-8")
    graph_build.update_repo(mini, indexed)

    brief = build(
        mini,
        indexed,
        "implement",
        requirement="handler request session",
        plan="Change `pkg/many.py` and `handler_7`.",
    )

    assert brief.tokens <= (
        briefing.REPO_MAP_TOKENS
        + briefing.RELATED_TOKEN_CAP
        + briefing.SCOPE_TOKEN_CAP
        + briefing.REUSE_TOKEN_CAP
    )
    assert "+" in brief.text and "more" in brief.text
    # a big file the plan names is named with the verb that would open it, not opened
    assert "aidev graph summaries --dir pkg/many.py" in brief.text


def test_a_briefing_never_cuts_a_line_in_half(mini, indexed):
    """A truncated coordinate sends the agent looking for the rest of it."""
    kept, dropped = briefing._fit(["one", "two", "three"], cap=1)

    assert kept == ["one"]
    assert dropped == 2


# ------------------------------------------------------------------- freshness


def test_a_missing_graph_is_built_before_the_briefing(mini, tmp_path):
    """A fresh worktree has no graph - .aidev/graph ignores itself - so build one."""
    directory = tmp_path / "fresh"

    brief = briefing.build(mini, "plan", "top helper", "", graph_directory=directory)

    assert (directory / "graph.db").exists()
    assert brief.graph["functions"] > 0
    assert "helper" in brief.text


def test_a_dirty_graph_is_refreshed_first(mini, indexed):
    """The marker a stage commit left is paid off before the table is set."""
    (mini / "pkg" / "fresh.py").write_text(
        'def freshly_added(seat):\n'
        '    """Add one freshly added seat.\n\n    @param seat  which seat\n    """\n'
        "    return seat\n",
        encoding="utf-8",
    )
    graph.mark_dirty(mini, indexed, reason="stage commit: implement")

    brief = briefing.build(
        mini, "plan", "freshly_added seat", "", graph_directory=indexed
    )

    assert "freshly_added" in brief.text
    assert not graph.is_dirty(mini, indexed)


# ---------------------------------------------------------------------- cache


def test_the_same_commit_and_plan_reuse_the_briefing(mini, committed, indexed, tmp_path):
    """Same commit, same plan, same table: the second launch pays nothing."""
    cache = tmp_path / "briefings"

    first = build(mini, indexed, "implement", cache_dir=cache)
    stamp = (cache / "implement.md").stat().st_mtime_ns
    second = build(mini, indexed, "implement", cache_dir=cache)

    assert first.reused is False and second.reused is True
    assert second.text == first.text
    assert second.tokens == first.tokens
    assert (cache / "implement.md").stat().st_mtime_ns == stamp
    assert json.loads((cache / "implement.json").read_text(encoding="utf-8"))["key"] == first.key

    # A human editing plan.md at the gate is exactly the case a narrower key
    # would miss, so the plan is part of what "the same briefing" means.
    third = build(mini, indexed, "implement", plan=PLAN + "\n4. Also `commented`.\n", cache_dir=cache)
    assert third.reused is False
    assert third.key != first.key


def test_a_briefing_is_rebuilt_when_the_commit_cannot_be_proved(
    mini, committed, indexed, tmp_path, monkeypatch
):
    """Cannot prove two trees are the same tree -> build it again."""
    monkeypatch.setattr(briefing.workspace, "head_commit", lambda repo: None)
    cache = tmp_path / "briefings"

    build(mini, indexed, "plan", cache_dir=cache)
    again = build(mini, indexed, "plan", cache_dir=cache)

    assert again.reused is False


def test_a_broken_graph_costs_no_briefing_and_no_exception(mini, indexed, monkeypatch):
    """A derived cache that cannot be opened is an empty table, never a traceback."""
    monkeypatch.setattr(briefing.graph, "open_db", lambda path: None)

    brief = build(mini, indexed, "plan")

    assert brief.text == ""
    assert brief.tokens == 0
    assert brief.sections == []


# ------------------------------------------------------------- this repository


def test_a_real_repository_still_fits_on_the_table(tmp_path):
    """The caps mean nothing on a six-file fixture; this is the only honest scale.

    A thousand-odd functions is where an unbounded briefing would quietly cost
    more than the searching it replaced, so the ceiling is proved here.
    """
    if not (REPO_ROOT / "aidev" / "pipeline.py").is_file():
        pytest.skip("not running from a source checkout")
    directory = tmp_path / "self"
    graph_build.build_repo(REPO_ROOT, directory)

    brief = briefing.build(
        REPO_ROOT,
        "implement",
        "브리핑 생성기: graph 를 선조회해 프롬프트에 동봉한다",
        "1. `aidev/briefing.py` - add `build`.\n2. `aidev/pipeline.py` - `prepare_briefing`.\n",
        graph_directory=directory,
    )

    assert brief.graph["functions"] > 500
    # Measured 2026-08-19 on this repository: 3619 tokens for 1252 functions.
    assert brief.tokens < 6000
    assert brief.sections == [
        briefing.SECTION_REPO_MAP,
        briefing.SECTION_RELATED,
        briefing.SECTION_SCOPE,
        briefing.SECTION_REUSE,
    ]
    # pipeline.py is far too big to open, so it is named with the verb instead
    assert "aidev graph summaries --dir aidev/pipeline.py" in brief.text
    assert "prepare_briefing" in brief.text
    assert brief.text.rstrip().endswith(briefing.QUERY_FOOTER)


# --------------------------------------------------------------- the plumbing


@pytest.mark.parametrize(
    "text, expected",
    [
        ("build_prompt", ["build", "build_prompt", "prompt"]),
        ("readPlan", ["read", "plan", "readplan"]),
        ("the and for", []),  # stop words are the words that match everything
        ("브리핑을 차린다", []),  # no ASCII identifier, no keyword, no magic
        ("a bc", []),  # too short to search on
    ],
)
def test_keywords_are_identifiers_split_and_nothing_else(text, expected):
    assert sorted(briefing.keywords(text)) == sorted(expected)


@pytest.mark.parametrize(
    "plan, paths, names",
    [
        ("touch `aidev/core.py`", ["aidev/core.py"], []),
        ("call `helper` first", [], ["helper"]),
        ("see aidev/core.py:helper", ["aidev/core.py"], ["helper"]),
        ("run save_user() twice", [], ["save_user"]),
        ("rewrite ./pkg/a.py", ["pkg/a.py"], []),
        ("update README.md and notes.json", [], []),  # the graph indexes neither
    ],
)
def test_scope_reads_the_three_shapes_a_plan_is_written_in(plan, paths, names):
    assert briefing.scope_of(plan) == (paths, names)
