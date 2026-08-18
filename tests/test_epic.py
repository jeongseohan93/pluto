"""The v0.4 loop: one epic, a decomposed list, a human's approval, a queue.

Everything here runs on ``fake_pipeline_claude.py``, so it costs nothing and is
deterministic. The fixtures come from test_pipeline unchanged - the single
--requirement path is what these tests must not disturb, so they reuse its
machinery rather than growing a second copy of it.
"""

import json
import os
from pathlib import Path

import pytest

from aidev import epic as epic_module
from aidev import pipeline
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    BASE_BRANCH,
    argv,
    branches,
    claude_bin,
    git,
    git_repo,
    invocations,
    log,
    repo,
    slice_dir,
    state_of,
    subjects,
    worktree,
)

EPIC_BODY = """# 에픽: 야간 페이즈

의사 보호와 사망 처리를 나눠서 넣는다. 두 단계로 자를 것.
"""


def epic_file(repo, body=EPIC_BODY, front=None, name="big"):
    text = "---\n{0}\n---\n{1}".format(front, body) if front is not None else body
    path = repo / "tasks" / "{0}.md".format(name)
    path.write_text(text, encoding="utf-8")
    return path


def epic_dir(repo):
    return sorted(epic_module.epics_root(repo).iterdir())[-1]


def epic_state(repo):
    return json.loads((epic_dir(repo) / "state.json").read_text(encoding="utf-8"))


def epic_approval(repo):
    return epic_dir(repo) / "approvals" / "decompose.md"


def approve_the_list(repo):
    """The one hook every test needs: a human saying yes to the slice list."""
    def hook(_seconds):
        path = epic_approval(repo)
        if path.exists():
            path.write_text("approved\n", encoding="utf-8")
    return hook


def slice_states(repo):
    root = pipeline.slices_root(repo)
    if not root.is_dir():
        return []
    return [
        json.loads((directory / "state.json").read_text(encoding="utf-8"))
        for directory in sorted(root.iterdir())
        if (directory / "state.json").exists()
    ]


def run_epic(repo, tmp_path, claude_bin, *extra):
    return main(argv(repo, tmp_path, claude_bin, "--epic", "tasks/big.md", *extra))


# ------------------------------------------------------------------ the loop


def test_epic_decomposes_approves_and_runs_two_slices(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """The whole Done Criteria in one run: decompose, gate, two slices in order."""
    epic_file(repo)
    git_repo(repo)
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))

    assert run_epic(repo, tmp_path, claude_bin) == 0

    state = epic_state(repo)
    assert state["status"] == "done"
    assert state["stages"]["decompose"]["status"] == "done"
    assert state["base"] == BASE_BRANCH
    assert [entry["status"] for entry in state["slices"]] == ["done", "done"]
    assert [entry["index"] for entry in state["slices"]] == [1, 2]

    # one decompose plus a full v0.3 slice each
    assert len(invocations(log)) == 7
    assert [s["status"] for s in slice_states(repo)] == ["done", "done"]

    directory = epic_dir(repo)
    assert "=== SLICE 1" in (directory / "slices.md").read_text(encoding="utf-8")
    written = sorted(p.name for p in (directory / "slices").iterdir())
    assert written == ["01-fake-slice-1.md", "02-fake-slice-2.md"]

    # decompose ran in the epic's own worktree, which is gone once the list is settled
    epic_id = state["epic_id"]
    assert Path(invocations(log)[0]["cwd"]).resolve() == (tmp_path / "wt" / epic_id).resolve()
    assert "workspace" not in state
    assert not (tmp_path / "wt" / epic_id).exists()
    assert "epic/{0}".format(epic_id) not in branches(repo)


def test_slice_two_starts_on_top_of_slice_one(repo, tmp_path, claude_bin, log, monkeypatch):
    """(b): the chain is where they fork; the base is still where they all land."""
    epic_file(repo)
    git_repo(repo)
    monkeypatch.setenv("AIDEV_FAKE_FILES", "1")
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))

    assert run_epic(repo, tmp_path, claude_bin) == 0

    first, second = epic_state(repo)["slices"]
    assert second["start"] == first["branch"]
    # slice 1's work is genuinely inside slice 2's branch
    assert git(
        repo, "merge-base", "--is-ancestor", first["branch"], second["branch"], check=False
    ).returncode == 0
    assert "slice({0}): implement".format(first["slice_id"]) in subjects(
        repo, "{0}..{1}".format(BASE_BRANCH, second["branch"])
    )

    # both merge into the base, not into each other
    states = {s["slice_id"]: s for s in slice_states(repo)}
    for entry in (first, second):
        assert states[entry["slice_id"]]["workspace"]["base"] == BASE_BRANCH
    # slice one forks from the commit the epic pinned when it started, so a base
    # branch that moves under a long epic cannot change what it was cut from
    assert states[first["slice_id"]]["workspace"]["start"] == epic_state(repo)["base_commit"]
    assert states[second["slice_id"]]["workspace"]["start"] == first["branch"]

    # and the implement stage was told where it really came from
    implement = invocations(log)[5]["prompt"]
    assert first["branch"] in implement


def test_the_list_gate_cannot_be_turned_off(repo, tmp_path, claude_bin, log, monkeypatch):
    epic_file(repo, front="approval: none")
    seen = {}

    def approve(_seconds):
        seen.setdefault("status", epic_state(repo)["status"])
        seen.setdefault("template", epic_approval(repo).read_text(encoding="utf-8"))
        epic_approval(repo).write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", approve)
    assert run_epic(repo, tmp_path, claude_bin) == 0

    assert seen["status"] == "waiting_approval:decompose"
    assert "slices.md" in seen["template"]
    assert epic_state(repo)["gates"] == ["decompose"]


def test_a_human_edit_to_slices_md_is_what_runs(repo, tmp_path, claude_bin, log, monkeypatch):
    """The list is a document a human owns: what they leave behind is what runs."""
    epic_file(repo)

    def rewrite_then_approve(_seconds):
        (epic_dir(repo) / "slices.md").write_text(
            "=== SLICE 1: 사람이 다시 쓴 것 ===\n"
            "---\napproval: none\n---\n"
            "# 사람이 다시 쓴 것\n\n손으로 고친 요구사항.\n",
            encoding="utf-8",
        )
        epic_approval(repo).write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", rewrite_then_approve)
    assert run_epic(repo, tmp_path, claude_bin) == 0

    entries = epic_state(repo)["slices"]
    assert len(entries) == 1
    assert entries[0]["title"] == "사람이 다시 쓴 것"
    assert len(invocations(log)) == 4  # decompose plus one slice
    requirement = (slice_dir(repo) / "requirement.md").read_text(encoding="utf-8")
    assert "손으로 고친 요구사항" in requirement


def test_a_failed_slice_stops_the_queue_and_resume_epic_continues(
    repo, tmp_path, claude_bin, log, monkeypatch, capsys
):
    epic_file(repo)
    git_repo(repo)
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))
    # 0 decompose, 1-3 slice one, 4 slice two's first stage
    monkeypatch.setenv("AIDEV_FAKE_FAIL_AFTER", "4")

    assert run_epic(repo, tmp_path, claude_bin) == 1
    out = capsys.readouterr().out

    state = epic_state(repo)
    assert state["status"] == "failed"
    assert "slice 2" in state["reason"]
    assert [entry["status"] for entry in state["slices"]] == ["done", "failed"]
    assert state["current"] == 2
    assert "--resume-epic" in out
    assert len(invocations(log)) == 5

    monkeypatch.delenv("AIDEV_FAKE_FAIL_AFTER")
    epic_id = state["epic_id"]
    assert main(argv(repo, tmp_path, claude_bin, "--resume-epic", epic_id)) == 0

    state = epic_state(repo)
    assert state["status"] == "done"
    assert [entry["status"] for entry in state["slices"]] == ["done", "done"]
    # slice one was not run again, and slice two picked up at its failed stage
    assert len(invocations(log)) == 8


def test_an_in_slice_gate_works_inside_the_queue(repo, tmp_path, claude_bin, log, monkeypatch):
    """A queue parked on a slice's own plan gate is a normal state, not a stall."""
    epic_file(repo)
    monkeypatch.setenv("AIDEV_FAKE_SLICE_APPROVAL", "plan")
    seen = []

    def approve_whatever_is_open(_seconds):
        if epic_approval(repo).exists():
            epic_approval(repo).write_text("approved\n", encoding="utf-8")
        root = pipeline.slices_root(repo)
        for directory in sorted(root.iterdir()) if root.is_dir() else []:
            gate = directory / "approvals" / "plan.md"
            if gate.exists() and pipeline.read_decision(gate).verdict == pipeline.PENDING:
                seen.append(
                    json.loads((directory / "state.json").read_text(encoding="utf-8"))["status"]
                )
                gate.write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", approve_whatever_is_open)
    assert run_epic(repo, tmp_path, claude_bin) == 0

    assert seen.count("waiting_approval:plan") == 2  # both slices really stopped for a human
    state = epic_state(repo)
    assert state["status"] == "done"
    assert all(s["stages"]["plan"]["approval"] == "approved" for s in slice_states(repo))


def test_a_quota_wait_does_not_break_the_queue(repo, tmp_path, claude_bin, log, monkeypatch):
    epic_file(repo)
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "1")
    slept = []
    approve = approve_the_list(repo)

    def wait_or_approve(seconds):
        slept.append(seconds)
        approve(seconds)

    monkeypatch.setattr(pipeline, "_sleep", wait_or_approve)
    assert run_epic(repo, tmp_path, claude_bin, "--quota-wait", "60") == 0

    state = epic_state(repo)
    assert state["status"] == "done"
    assert state["quota"]["retries"] == 1
    assert state["quota"]["waiting"] is False
    assert [entry["status"] for entry in state["slices"]] == ["done", "done"]
    assert len(invocations(log)) == 8  # the limit cost one decompose attempt


def test_decompose_that_writes_a_file_fails_the_epic(repo, tmp_path, claude_bin, log, monkeypatch):
    epic_file(repo)
    git_repo(repo)
    monkeypatch.setenv("AIDEV_FAKE_MODE", "forge")

    def never(_seconds):
        raise AssertionError("a decompose that wrote to the repo must never reach the gate")

    monkeypatch.setattr(pipeline, "_sleep", never)
    assert run_epic(repo, tmp_path, claude_bin) == 1

    state = epic_state(repo)
    assert state["status"] == "failed"
    assert "readonly" in state["reason"]
    assert state["slices"] == []
    assert not pipeline.slices_root(repo).exists()
    assert not epic_approval(repo).exists()
    # the forgery stayed inside the epic's own worktree
    forged = tmp_path / "wt" / state["epic_id"] / ".aidev" / "slices"
    assert forged.is_dir()


def test_unparsable_slices_md_fails_with_a_way_out(
    repo, tmp_path, claude_bin, log, monkeypatch, capsys
):
    epic_file(repo)
    monkeypatch.setenv("AIDEV_FAKE_TEXT", "there are no markers in this answer at all")
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))

    assert run_epic(repo, tmp_path, claude_bin) == 1
    out = capsys.readouterr().out
    state = epic_state(repo)
    assert state["status"] == "failed"
    assert "SLICE" in state["reason"] and "slices.md" in state["reason"]
    assert "--resume-epic" in out
    assert len(invocations(log)) == 1

    # the human writes the list themselves and resumes: decompose does not re-run
    monkeypatch.delenv("AIDEV_FAKE_TEXT")
    (epic_dir(repo) / "slices.md").write_text(
        "=== SLICE 1: 손으로 쓴 목록 ===\n---\napproval: none\n---\n# 손으로 쓴 목록\n\n본문.\n",
        encoding="utf-8",
    )
    assert main(argv(repo, tmp_path, claude_bin, "--resume-epic", "last")) == 0
    assert epic_state(repo)["status"] == "done"
    assert len(invocations(log)) == 4


def test_a_shorter_list_that_drops_finished_work_is_refused(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    epic_file(repo)
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))
    monkeypatch.setenv("AIDEV_FAKE_FAIL_AFTER", "4")
    assert run_epic(repo, tmp_path, claude_bin) == 1

    # slice one is done and committed; a list that no longer contains it is a lie
    (epic_dir(repo) / "slices.md").write_text("(nothing left)\n", encoding="utf-8")
    monkeypatch.delenv("AIDEV_FAKE_FAIL_AFTER")
    assert main(argv(repo, tmp_path, claude_bin, "--resume-epic", "last")) == 1
    assert "SLICE" in epic_state(repo)["reason"]


# ------------------------------------------------------------- list / usage


def test_list_shows_epic_progress(repo, tmp_path, claude_bin, log, monkeypatch, capsys):
    epic_file(repo)
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))
    assert run_epic(repo, tmp_path, claude_bin) == 0
    capsys.readouterr()

    assert main(argv(repo, tmp_path, claude_bin, "--list")) == 0
    out = capsys.readouterr().out
    assert "EPIC" in out and "SLICE" in out
    assert epic_state(repo)["epic_id"] in out
    assert "1=done 2=done" in out


def test_epic_dry_run_touches_nothing(repo, tmp_path, claude_bin, log, capsys):
    epic_file(repo)
    assert run_epic(repo, tmp_path, claude_bin, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "base      {0}".format(BASE_BRANCH) in out
    assert "branch    epic/" in out
    assert "decompose" in out
    assert not epic_module.epics_root(repo).exists()
    assert not (tmp_path / "wt").exists()
    assert not [name for name in branches(repo) if name.startswith("epic/")]
    assert invocations(log) == []


def test_epic_usage_errors(repo, tmp_path, claude_bin, log, capsys):
    epic_file(repo)
    assert main(
        argv(repo, tmp_path, claude_bin, "--epic", "tasks/big.md", "--requirement", "tasks/big.md")
    ) == 2
    assert "cannot be combined" in capsys.readouterr().err

    assert run_epic(repo, tmp_path, claude_bin, "--no-worktree") == 2
    assert "--epic needs worktrees" in capsys.readouterr().err

    assert main(argv(repo, tmp_path, claude_bin, "--epic", "tasks/nope.md")) == 2
    assert "--epic not found" in capsys.readouterr().err

    assert main(argv(repo, tmp_path, claude_bin, "--resume-epic", "nothing")) == 2
    assert "no epics in" in capsys.readouterr().err
    assert invocations(log) == []


def test_a_second_process_cannot_run_the_same_epic(repo, tmp_path, claude_bin, log, monkeypatch):
    epic_file(repo)
    seen = {}

    def resume_while_the_gate_is_open(_seconds):
        seen["code"] = main(argv(repo, tmp_path, claude_bin, "--resume-epic", "last"))
        epic_approval(repo).write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", resume_while_the_gate_is_open)
    assert run_epic(repo, tmp_path, claude_bin) == 0
    assert seen["code"] == 2
    assert not (epic_dir(repo) / ".lock").exists()


# ------------------------------------------------------------------- records


def test_epic_slice_records_its_epic(repo, tmp_path, claude_bin, log, monkeypatch):
    epic_file(repo)
    git_repo(repo)
    monkeypatch.setattr(pipeline, "_sleep", approve_the_list(repo))
    assert run_epic(repo, tmp_path, claude_bin) == 0

    epic_id = epic_state(repo)["epic_id"]
    second = slice_states(repo)[1]
    assert second["epic"] == {"epic_id": epic_id, "index": 2}

    # and the branch carries the same fact, in its own projection
    path = ".aidev/history/{0}/slice.json".format(second["slice_id"])
    snapshot = json.loads(
        git(repo, "show", "{0}:{1}".format(second["commits"]["test"], path)).stdout
    )
    assert snapshot["epic"] == {"epic_id": epic_id, "index": 2}
    assert snapshot["base"] == BASE_BRANCH
    assert snapshot["start"] == epic_state(repo)["slices"][0]["branch"]


# --------------------------------------------------------------- unit checks


def test_parse_slices_is_tolerant_about_form():
    notes, items = parse = epic_module.parse_slices(
        "메모: 셋으로 잘랐다.\n\n"
        "=== SLICE 1: 첫째 ===\n---\napproval: none\n---\n# 첫째\n\n하나.\n\n"
        "=== slice 7 : 둘째 ===\n# 둘째\n\n둘.\n\n"
        "===SLICE===\n# 셋째\n\n셋.\n"
    )
    assert notes.startswith("메모")
    assert [item.index for item in items] == [1, 2, 3]  # file order, renumbered
    assert [item.title for item in items] == ["첫째", "둘째", "셋째"]
    assert items[0].text.startswith("---")
    assert parse[1][2].title == "셋째"


def test_parse_slices_unwraps_a_whole_document_fence():
    _, items = epic_module.parse_slices(
        "```markdown\n=== SLICE 1: 하나 ===\n# 하나\n\n본문.\n```"
    )
    assert len(items) == 1
    assert "본문." in items[0].text


def test_parse_slices_finds_nothing_when_there_are_no_markers():
    notes, items = epic_module.parse_slices("just prose, no list at all")
    assert items == []
    assert notes == "just prose, no list at all"


def test_a_bad_front_matter_line_names_the_item(tmp_path):
    _, items = epic_module.parse_slices(
        "=== SLICE 1: 좋음 ===\n---\napproval: none\n---\n# 좋음\n\n본문.\n\n"
        "=== SLICE 2: 나쁨 ===\n---\napproval: browser\n---\n# 나쁨\n\n본문.\n"
    )
    with pytest.raises(pipeline.PipelineError) as excinfo:
        epic_module.validate_items(items, tmp_path / "slices.md")
    message = str(excinfo.value)
    assert "slice 2" in message and "나쁨" in message and "browser" in message


@pytest.mark.parametrize(
    "front, needle",
    [
        ("max_turns: planz=140", "unknown stage 'planz'"),
        ("max_turns: abc", "positive whole number"),
        ("test_commands: npm test && rm -rf /", "not allowed"),
        ("test_commands:", "needs a command"),
        # the README said six keys were validated and four of them were
        ("model: planz=claude-opus-5", "unknown stage 'planz'"),
        ("model: implement=", "no model"),
        ("spec_check: maybe", "needs 'on' or 'off'"),
    ],
)
def test_a_listed_slice_with_a_bad_new_key_is_refused_before_anything_runs(
    tmp_path, front, needle
):
    """Every declared key is read up front, so item 4's typo stops items 1-3 too."""
    _, items = epic_module.parse_slices(
        "=== SLICE 1: 좋음 ===\n---\napproval: none\n---\n# 좋음\n\n본문.\n\n"
        "=== SLICE 2: 나쁨 ===\n---\napproval: none\n{0}\n---\n# 나쁨\n\n본문.\n".format(front)
    )
    with pytest.raises(pipeline.PipelineError) as excinfo:
        epic_module.validate_items(items, tmp_path / "slices.md")
    message = str(excinfo.value)
    assert "slice 2" in message and "나쁨" in message and needle in message


def test_a_listed_slice_with_an_unknown_key_is_warned_about_not_refused(tmp_path, capsys):
    """A whole queue is worth running with one line ignored - but not in silence."""
    _, items = epic_module.parse_slices(
        "=== SLICE 1: 좋음 ===\n---\napproval: none\nmodle: claude-opus-5\n---\n# 좋음\n\n본문.\n"
    )
    epic_module.validate_items(items, tmp_path / "slices.md")

    out = capsys.readouterr().out
    assert "front matter key(s) ignored: modle" in out
    assert "slice 1" in out and "좋음" in out


def test_the_decompose_prompt_quotes_the_budget_decompose_actually_gets(tmp_path):
    """The number the model is told to cut against has to be the number it is given."""
    cfg = pipeline.PipelineConfig(repo=tmp_path, data_dir=tmp_path / "data", max_turns=130)
    spec = epic_module.decompose_spec(cfg, "# 에픽\n\n본문.\n")
    assert "budget of 130 turns" in spec.prompt
    assert spec.prompt.count("80 turns") == 0
    # and a slice that genuinely cannot be cut smaller is told how to raise its own
    assert "max_turns: implement=140" in spec.prompt


def test_slice_names_keep_the_index_inside_the_id_limit():
    erec = epic_module.EpicRecord(Path("."), "20260815-a-very-long-epic-name-that-keeps-going")
    item = epic_module.SliceItem(
        index=4, title="an equally long slice title that will not fit either", text="x"
    )
    name = epic_module.slice_name(erec, item)
    assert name.startswith("a-very-long-epic-nam-04-")
    assert "-04-" in pipeline.make_slice_id(name, Path(os.devnull))
