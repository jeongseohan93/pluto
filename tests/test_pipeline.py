import json
import os
import stat
import subprocess
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aidev import pipeline
from aidev.cli import main
from aidev.telemetry import PHASES

STUB = Path(__file__).with_name("fake_pipeline_claude.py")


@pytest.fixture
def claude_bin(tmp_path):
    """Launcher shim, so the CLI runs the stub exactly like the real binary."""
    if os.name == "nt":
        path = tmp_path / "claude.cmd"
        path.write_text('@echo off\r\n"{0}" "{1}" %*\r\n'.format(sys.executable, STUB))
    else:
        path = tmp_path / "claude.sh"
        path.write_text('#!/bin/sh\nexec "{0}" "{1}" "$@"\n'.format(sys.executable, STUB))
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


# The measured target repo does not use main: its development line is
# windows-handoff-20260808. Every pipeline test starts from a branch by that name,
# so "base is the current HEAD, never main" is proved by the whole suite and not
# by one test.
BASE_BRANCH = "windows-handoff-20260808"

GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t"]


def git(path, *args, check=True):
    return subprocess.run(
        ["git"] + GIT_ID + ["-C", str(path)] + list(args),
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
    )


def commit_all(path, message="snapshot"):
    git(path, "add", "-A")
    git(path, "commit", "--allow-empty", "-qm", message)
    return path


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "jokertest"
    (path / "tasks").mkdir(parents=True)
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    # `git init -b` is not in every git the tool has to run on.
    git(path, "symbolic-ref", "HEAD", "refs/heads/{0}".format(BASE_BRANCH))
    (path / "README.md").write_text("jokertest\n", encoding="utf-8")
    commit_all(path, "init")
    return path


# Kept as a name because tests read better saying it: "everything so far is
# committed". The repo fixture is already a git repo, so this only snapshots.
git_repo = commit_all


@pytest.fixture
def log(tmp_path, monkeypatch):
    """Every stub invocation, so tests can assert on argv and prompts."""
    path = tmp_path / "invocations.jsonl"
    monkeypatch.setenv("AIDEV_FAKE_LOG", str(path))
    return path


def invocations(log_path):
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]


def requirement(repo, body="# doctor\n\n밤 페이즈 의사 보호 로직을 구현한다.\n", front=None):
    text = "---\n{0}\n---\n{1}".format(front, body) if front is not None else body
    path = repo / "tasks" / "doctor.md"
    path.write_text(text, encoding="utf-8")
    return path


def argv(repo, tmp_path, claude_bin, *extra):
    return [
        "--data-dir", str(tmp_path / "data"),
        "pipeline",
        "--repo", str(repo),
        "--claude-bin", claude_bin,
        "--no-live",
        # Short, and beside the repo rather than derived from it: Windows MAX_PATH
        # is a real limit once a worktree carries a slice id and node_modules.
        "--worktree-root", str(tmp_path / "wt"),
    ] + list(extra)


def worktree(repo, tmp_path, slice_id=None):
    return tmp_path / "wt" / (slice_id or state_of(repo)["slice_id"])


def subjects(repo, rev_range):
    return [line for line in git(repo, "log", "--format=%s", "--reverse", rev_range).stdout.splitlines() if line]


def state_of(repo, slice_id=None):
    root = pipeline.slices_root(repo)
    directory = root / slice_id if slice_id else sorted(root.iterdir())[-1]
    return json.loads((directory / "state.json").read_text(encoding="utf-8"))


def slice_dir(repo):
    return sorted(pipeline.slices_root(repo).iterdir())[-1]


# ------------------------------------------------------------------ full loop


@pytest.mark.skipif(os.name != "nt", reason="Windows .cmd encoding regression")
def test_windows_cmd_stub_reads_utf8_prompt(repo, claude_bin, log):
    prompt = "밤 페이즈 의사 보호 로직"
    result = subprocess.run(
        ["cmd", "/c", claude_bin],
        cwd=str(repo),
        input=prompt,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert invocations(log)[0]["prompt"] == prompt


def test_unattended_slice_runs_every_stage(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none")

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 0

    state = state_of(repo)
    assert state["status"] == "done"
    assert [state["stages"][name]["status"] for name in ("plan", "implement", "test")] == [
        "done", "done", "done"
    ]
    assert state["gates"] == []
    assert state["quota"] == {"waiting": False, "resume_at": None, "retries": 0}
    assert state["slice_id"].endswith("-doctor")
    for name in ("plan", "implement", "test"):
        assert state["stages"][name]["session_id"] == "pipe-session"

    directory = slice_dir(repo)
    assert (directory / "requirement.md").read_text(encoding="utf-8").startswith("---")
    assert "1. touch aidev/thing.py" in (directory / "plan.md").read_text(encoding="utf-8")

    runs = json.loads((directory / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert [r["stage"] for r in runs] == ["plan", "implement", "test"]
    assert all(r["status"] == "completed" for r in runs)
    # every stage is a real run, stored the way v0.1 stores runs
    for entry in runs:
        assert (Path(entry["run_dir"]) / "telemetry.json").exists()
        assert (Path(entry["run_dir"]) / "events.jsonl").exists()
    assert len(list((tmp_path / "data" / "runs").iterdir())) == 3

    out = capsys.readouterr().out
    assert "PIPELINE" in out and "aidev/thing.py" in out  # change summary

    calls = invocations(log)
    assert len(calls) == 3
    # the approved plan reaches implement, and the requirement reaches every stage
    assert "1. touch aidev/thing.py" in calls[1]["prompt"]
    assert "밤 페이즈 의사 보호 로직" in calls[0]["prompt"]
    assert "밤 페이즈 의사 보호 로직" in calls[2]["prompt"]
    # no stage resumes another stage's session
    assert "--resume" not in calls[1]["argv"]


def test_stage_policies_reach_the_claude_command(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    plan, implement, test = (call["argv"] for call in invocations(log))
    blocked = plan[plan.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Edit", "Write", "Task"} <= set(blocked)
    assert "--permission-mode" not in plan
    assert implement[implement.index("--permission-mode") + 1] == "acceptEdits"
    assert test[test.index("--permission-mode") + 1] == "acceptEdits"
    assert "--disallowedTools" not in implement
    assert plan[plan.index("--max-turns") + 1] == "80"

    # acceptEdits does not cover Bash, so the verification commands are granted
    # by rule - and plan, which may run nothing at all, is granted none.
    assert "--allowedTools" not in plan
    for stage_argv in (implement, test):
        rules = stage_argv[stage_argv.index("--allowedTools") + 1].split(",")
        # the exact strings survive the shell, including spaces, '*' and parentheses
        assert {"Bash(npm test)", "Bash(npm run test:*)", "Bash(node --test)"} <= set(rules)
        assert "Bash" not in rules  # rule-scoped, never a blanket unlock


def test_allowed_tools_for_grants_only_the_command_stages(tmp_path):
    cfg = pipeline.PipelineConfig(repo=tmp_path, data_dir=tmp_path / "data")
    assert pipeline.allowed_tools_for("plan", cfg) == ()
    for stage in ("implement", "test"):
        assert "Bash(npm run test:*)" in pipeline.allowed_tools_for(stage, cfg)
        assert "Bash(pytest:*)" in pipeline.allowed_tools_for(stage, cfg)

    # an extra rule is appended, and a duplicate of a built-in one is not repeated
    cfg.allow_tools = ["Bash(npx vitest:*)", "Bash(npm test)"]
    rules = pipeline.allowed_tools_for("test", cfg)
    assert rules[-1] == "Bash(npx vitest:*)"
    assert rules.count("Bash(npm test)") == 1
    assert pipeline.allowed_tools_for("plan", cfg) == ()  # an override cannot open plan


def test_the_test_stage_can_actually_run_the_suite(repo, tmp_path, claude_bin, log, monkeypatch):
    """The measured v0.2 defect: without a rule the command is refused and never runs."""
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "requires_approval")

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    state = state_of(repo)
    assert state["status"] == "done"
    assert state["test_verdict"] == "pass"

    runs = json.loads((slice_dir(repo) / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert "345 passing" in runs[-1]["summary"]
    assert "requires approval" not in runs[-1]["summary"]
    # the stage was told what it may run, from the same list that was granted
    assert "Bash(npm run test:*)" in invocations(log)[-1]["prompt"]


def test_without_the_rule_the_test_command_is_refused(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Proof the test above depends on the rule: take it away and the stage fails."""
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "requires_approval")
    monkeypatch.setattr(pipeline, "TEST_COMMAND_TOOLS", ())

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert state["test_verdict"] == "fail"
    assert "--allowedTools" not in invocations(log)[-1]["argv"]


def test_permission_mode_override_never_unlocks_plan(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/doctor.md",
            "--permission-mode", "bypassPermissions",
        )
    ) == 0
    plan, implement, _ = (call["argv"] for call in invocations(log))
    assert "--permission-mode" not in plan
    assert implement[implement.index("--permission-mode") + 1] == "bypassPermissions"


# -------------------------------------------------------------------- gates


def test_plan_gate_blocks_until_approved(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo)  # no front matter: the plan gate is on by default
    seen = {}

    def approve(_seconds):
        directory = slice_dir(repo)
        seen["status"] = json.loads((directory / "state.json").read_text(encoding="utf-8"))["status"]
        seen["template"] = (directory / "approvals" / "plan.md").read_text(encoding="utf-8")
        (directory / "approvals" / "plan.md").write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", approve)
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    # while waiting, state.json named the gate; the file we left explained itself
    assert seen["status"] == "waiting_approval:plan"
    assert "rejected: <reason>" in seen["template"]
    state = state_of(repo)
    assert state["status"] == "done"
    assert state["gates"] == ["plan"]
    assert state["stages"]["plan"]["approval"] == "approved"
    assert len(invocations(log)) == 3


def test_rejection_stops_the_slice_with_its_reason(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo)

    monkeypatch.setattr(
        pipeline,
        "_sleep",
        lambda _s: (slice_dir(repo) / "approvals" / "plan.md").write_text(
            "rejected: 계획이 파일을 너무 많이 건드린다\n", encoding="utf-8"
        ),
    )
    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 3

    state = state_of(repo)
    assert state["status"] == "rejected"
    assert "계획이 파일을 너무 많이 건드린다" in state["reason"]
    assert state["stages"]["plan"]["approval"] == "rejected"
    assert state["stages"]["implement"]["status"] == "pending"
    assert len(invocations(log)) == 1  # implement never started


def test_approval_timeout_fails_the_slice_without_losing_the_stage(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo)
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: None)

    code = main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/doctor.md",
            "--approval-timeout", "0.05",
            "--poll-interval", "0.01",
        )
    )
    assert code == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "timed out" in state["reason"]
    assert state["stages"]["plan"]["status"] == "done"  # the work itself survived
    assert len(invocations(log)) == 1


def test_resuming_a_rejected_slice_stays_rejected(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo)
    monkeypatch.setattr(
        pipeline,
        "_sleep",
        lambda _s: (slice_dir(repo) / "approvals" / "plan.md").write_text(
            "rejected: 범위가 너무 넓다\n", encoding="utf-8"
        ),
    )
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 3

    # the file still says rejected, so resuming re-reads it and stops again
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: pytest.fail("must not wait"))
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 3
    assert len(invocations(log)) == 1
    assert state_of(repo)["status"] == "rejected"

    # changing the file to approved is what lets it continue
    (slice_dir(repo) / "approvals" / "plan.md").write_text("approved\n", encoding="utf-8")
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0
    assert len(invocations(log)) == 3
    assert state_of(repo)["status"] == "done"


def test_a_pre_approved_gate_does_not_wait(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: plan, implement")

    def never(_seconds):
        raise AssertionError("a gate that is already approved must not wait")

    # a human who has already written 'approved' by the time we look
    monkeypatch.setattr(pipeline, "_sleep", never)
    monkeypatch.setattr(pipeline, "read_decision", lambda _p: pipeline.Decision(pipeline.APPROVED))
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    state = state_of(repo)
    assert state["gates"] == ["plan", "implement"]
    assert state["stages"]["implement"]["approval"] == "approved"
    assert len(invocations(log)) == 3


# ------------------------------------------------------------------- resume


def test_resume_slice_continues_after_the_process_is_killed(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo)

    def killed(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(pipeline, "_sleep", killed)
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 130

    state = state_of(repo)
    assert state["status"] == "waiting_approval:plan"
    assert state["stages"]["plan"]["status"] == "done"
    assert len(invocations(log)) == 1

    # the human approves, then resumes in a fresh process: the file is the
    # record, so the resumed run reads the answer instead of waiting again
    (slice_dir(repo) / "approvals" / "plan.md").write_text("approved\n", encoding="utf-8")
    monkeypatch.setattr(
        pipeline, "_sleep", lambda _s: pytest.fail("resume must not wait on a decided gate")
    )
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0

    state = state_of(repo)
    assert state["status"] == "done"
    calls = invocations(log)
    assert len(calls) == 3  # plan was not run twice
    assert "IMPLEMENT stage" in calls[1]["prompt"]


def test_a_failed_stage_retries_in_a_new_session(repo, tmp_path, claude_bin, log, monkeypatch):
    """A session that concluded 'blocked' must not be the one that judges the retry."""
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "fail")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1

    plan_entry = state_of(repo)["stages"]["plan"]
    assert plan_entry["failures"] == 1
    assert plan_entry["session_id"] == "pipe-session"  # it exists, and is still not resumed

    monkeypatch.setenv("AIDEV_FAKE_MODE", "ok")
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0

    retry = invocations(log)[1]
    assert "--resume" not in retry["argv"]
    assert "has been resumed" not in retry["prompt"]
    assert "NEW session with no memory" in retry["prompt"]
    state = state_of(repo)
    assert state["status"] == "done"
    assert state["stages"]["plan"]["failures"] == 0  # cleared by the attempt that worked


def test_session_reset_after_can_be_raised_back_to_the_old_behaviour(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "fail")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1

    monkeypatch.setenv("AIDEV_FAKE_MODE", "ok")
    assert main(
        argv(repo, tmp_path, claude_bin, "--resume-slice", "last", "--session-reset-after", "5")
    ) == 0

    retry = invocations(log)[1]
    assert retry["argv"][retry["argv"].index("--resume") + 1] == "pipe-session"


def test_a_test_verdict_fail_counts_as_a_stage_failure(repo, tmp_path, claude_bin, log, monkeypatch):
    """The shape of the real regression: FAIL is a conclusion, so the retry starts clean."""
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_VERDICT", "FAIL")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1
    assert state_of(repo)["stages"]["test"]["failures"] == 1

    monkeypatch.setenv("AIDEV_FAKE_VERDICT", "PASS")
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0

    calls = invocations(log)
    assert len(calls) == 4  # only the test stage re-ran
    assert "TEST STAGE" in calls[3]["prompt"].upper()
    assert "--resume" not in calls[3]["argv"]
    state = state_of(repo)
    assert state["status"] == "done"
    assert state["test_verdict"] == "pass"


def test_a_quota_failure_does_not_count_as_a_stage_failure(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Giving up on a usage limit interrupted the session; it did not conclude it."""
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "99")
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: None)

    assert main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/doctor.md",
            "--quota-wait", "1",
            "--quota-max-retries", "1",
        )
    ) == 1
    assert state_of(repo)["stages"]["plan"].get("failures", 0) == 0


def test_resume_by_prefix_and_repeat_resume_of_a_done_slice(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    slice_id = state_of(repo)["slice_id"]

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id[:8])) == 0
    assert len(invocations(log)) == 3  # nothing re-ran


# -------------------------------------------------------------------- quota


def test_quota_failure_waits_for_the_reset_and_resumes_the_session(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "1")
    monkeypatch.setenv("AIDEV_FAKE_RESET_IN", "120")

    slept = []
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: slept.append(seconds))

    code = main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--quota-wait", "900")
    )
    assert code == 0

    # the reset time in the error wins over the fallback interval
    assert 120 <= sum(slept) <= 160
    calls = invocations(log)
    assert len(calls) == 4  # plan failed once, then plan, implement, test
    assert calls[1]["argv"][calls[1]["argv"].index("--resume") + 1] == "pipe-session"
    assert "has been resumed" in calls[1]["prompt"]

    state = state_of(repo)
    assert state["status"] == "done"
    assert state["quota"]["retries"] == 1
    assert state["quota"]["waiting"] is False
    assert state["stages"]["plan"]["attempts"] == 2

    # the retry is its own run, even though it started in the same second
    runs = json.loads((slice_dir(repo) / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert len({r["run_dir"] for r in runs}) == len(runs) == 4
    assert len(list((tmp_path / "data" / "runs").iterdir())) == 4


def test_quota_without_a_reset_time_falls_back_to_the_interval(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "1")
    slept = []
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: slept.append(seconds))

    code = main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--quota-wait", "60")
    )
    assert code == 0
    assert sum(slept) == 60


def test_quota_retries_are_capped(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "99")
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: None)

    code = main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/doctor.md",
            "--quota-wait", "1",
            "--quota-max-retries", "2",
        )
    )
    assert code == 1
    assert len(invocations(log)) == 3  # first attempt plus two retries
    state = state_of(repo)
    assert state["status"] == "failed"
    assert state["stages"]["plan"]["status"] == "failed"
    assert state["quota"]["retries"] == 2


def test_ordinary_failure_is_not_retried(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "fail")

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 1
    assert len(invocations(log)) == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "stage 'plan' failed" in state["reason"]
    assert state["quota"]["retries"] == 0


# ------------------------------------------------------------------- safety


def test_dirty_repo_is_no_longer_refused(repo, tmp_path, claude_bin, log):
    """v0.3 removes v0.2's dirty gate: the AI never touches this tree at all."""
    requirement(repo, front="approval: none")
    git_repo(repo)
    (repo / "unstaged.txt").write_text("work in progress", encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    assert state_of(repo)["status"] == "done"
    assert len(invocations(log)) == 3
    # the human's half-finished work is exactly where they left it
    assert (repo / "unstaged.txt").read_text(encoding="utf-8") == "work in progress"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head


def test_clean_git_repo_runs_and_its_own_state_is_not_dirt(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    git_repo(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    assert state_of(repo)["status"] == "done"
    # the slice landed on its own branch; the checked-out one did not move
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head


def test_plan_stage_that_touches_the_repo_fails_the_slice(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    git_repo(repo)
    monkeypatch.setenv("AIDEV_FAKE_MODE", "touch")

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "readonly" in state["reason"]
    assert state["stages"]["plan"]["status"] == "failed"
    assert len(invocations(log)) == 1  # implement never started
    # the stub used a blocked tool, and that is recorded rather than hidden
    assert state["stages"]["plan"]["safety_violations"] >= 1


# -------------------------------------------------------------- list / usage


def test_list_survives_unknown_stage_keys(repo, tmp_path, claude_bin, capsys):
    root = pipeline.slices_root(repo)
    (root / "20260815-doctor").mkdir(parents=True)
    (root / "20260815-doctor" / "state.json").write_text(
        json.dumps(
            {
                "slice_id": "20260815-doctor",
                "status": "waiting_approval:browser",
                "stages": {"plan": {"status": "done"}, "browser": {"status": "running"}},
                "updated_at": "2026-08-15T21:04:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    (root / "20260815-broken").mkdir(parents=True)
    (root / "20260815-broken" / "state.json").write_text("{ half written", encoding="utf-8")

    long_id = "20260815-night-phase-doctor-protection-with-a-very-long-name"
    (root / long_id).mkdir(parents=True)
    (root / long_id / "state.json").write_text(
        json.dumps({"slice_id": long_id, "status": "done", "stages": {}}), encoding="utf-8"
    )

    assert main(argv(repo, tmp_path, claude_bin, "--list")) == 0
    out = capsys.readouterr().out
    assert "browser=running" in out
    assert "waiting_approval:browser" in out
    assert "(unreadable)" in out
    # a long id is truncated instead of running into the next column
    assert "namedone" not in out
    for line in out.splitlines():
        assert " " in line[30:36], line  # the column gap survives


def test_list_without_repo_says_which_repo_it_looked_in(tmp_path, capsys, monkeypatch):
    """An empty answer from the default cwd explains itself instead of just being empty."""
    elsewhere = tmp_path / "not-the-target"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main(["--data-dir", str(tmp_path / "data"), "pipeline", "--list"]) == 0
    out = capsys.readouterr().out
    assert "no slices in" in out
    assert "--repo" in out

    # a chosen repo that happens to be empty is not a mistake, so it stays quiet
    assert main(["--data-dir", str(tmp_path / "data"), "pipeline", "--repo", str(elsewhere),
                 "--list"]) == 0
    assert "--repo <path>" not in capsys.readouterr().out


def test_resume_without_repo_points_at_the_repo_flag(tmp_path, capsys, monkeypatch):
    elsewhere = tmp_path / "not-the-target"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert main(["--data-dir", str(tmp_path / "data"), "pipeline", "--resume-slice", "last"]) == 2
    err = capsys.readouterr().err
    assert "no slices in" in err
    assert "pass --repo <path>" in err


def test_dry_run_touches_nothing(repo, tmp_path, claude_bin, capsys):
    requirement(repo, front="approval: plan, implement")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--dry-run")) == 0
    out = capsys.readouterr().out
    assert "plan -> implement -> test" in out
    assert "plan, implement" in out
    assert not pipeline.slices_root(repo).exists()
    assert not (tmp_path / "data").exists()


def test_usage_errors(repo, tmp_path, claude_bin, capsys):
    assert main(argv(repo, tmp_path, claude_bin)) == 2
    assert "one of --requirement" in capsys.readouterr().err

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "nope.md")) == 2
    assert "--requirement not found" in capsys.readouterr().err

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "nothing")) == 2
    assert "no slices in" in capsys.readouterr().err

    requirement(repo, front="approval: pan")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 2
    assert "unknown approval gate 'pan'" in capsys.readouterr().err


def test_missing_claude_binary_reports_127(repo, tmp_path, capsys):
    requirement(repo, front="approval: none")
    code = main(argv(repo, tmp_path, "definitely-not-a-real-binary-xyz", "--requirement", "tasks/doctor.md"))
    assert code == 127
    assert "not found on PATH" in capsys.readouterr().err


# ------------------------------------------------- regressions from review v1


def test_long_requirement_names_still_get_one_directory_per_run(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """slugify() cuts labels at 40 chars; the stage must not be what gets cut."""
    long_name = "night-phase-doctor-protection-with-a-very-long-name"
    (repo / "tasks" / "{0}.md".format(long_name)).write_text(
        "---\napproval: none\n---\n# 긴 이름\n\n본문\n", encoding="utf-8"
    )
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "1")
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: None)

    code = main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/{0}.md".format(long_name),
            "--quota-wait", "1",
        )
    )
    assert code == 0

    runs = json.loads((slice_dir(repo) / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert len(runs) == 4  # plan failed once, then plan, implement, test
    assert len({r["run_id"] for r in runs}) == 4
    assert len({r["run_dir"] for r in runs}) == 4
    assert len(list((tmp_path / "data" / "runs").iterdir())) == 4
    # every run kept its own telemetry rather than overwriting a shared file
    for entry in runs:
        assert (Path(entry["run_dir"]) / "telemetry.json").exists()


def test_failing_tests_do_not_finish_the_slice(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_VERDICT", "FAIL")

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 1

    state = state_of(repo)
    assert state["status"] == "failed"
    assert state["test_verdict"] == "fail"
    assert state["stages"]["test"]["verdict"] == "fail"
    assert "TEST_RESULT: FAIL" in state["reason"]


def test_missing_verdict_is_reported_as_unknown_not_pass(
    repo, tmp_path, claude_bin, log, monkeypatch, capsys
):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_VERDICT", "none")

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    out = capsys.readouterr().out
    assert state_of(repo)["test_verdict"] == "unknown"
    assert "no TEST_RESULT line" in out


def test_verdict_parsing():
    assert pipeline.parse_test_verdict("all good\nTEST_RESULT: PASS") == "pass"
    assert pipeline.parse_test_verdict("TEST_RESULT: FAIL\n") == "fail"
    assert pipeline.parse_test_verdict("  test_result: pass  ") == "pass"
    assert pipeline.parse_test_verdict("12 passed") == pipeline.VERDICT_UNKNOWN
    # the last word wins: the model may quote the format before answering
    assert pipeline.parse_test_verdict("write TEST_RESULT: PASS\nTEST_RESULT: FAIL") == "fail"


def test_plan_cannot_forge_its_own_approval(repo, tmp_path, claude_bin, log, monkeypatch):
    """A readonly stage writing inside .aidev/ must still be caught."""
    requirement(repo)
    git_repo(repo)

    def never(_seconds):
        raise AssertionError("a forged approval must never let the loop continue")

    monkeypatch.setattr(pipeline, "_sleep", never)
    monkeypatch.setenv("AIDEV_FAKE_MODE", "forge")

    code = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert code == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "readonly" in state["reason"]
    assert len(invocations(log)) == 1
    # two independent things held: the forgery never reached the real approval
    # file, and writing under .aidev/ at all was still caught
    assert not (slice_dir(repo) / "approvals" / "plan.md").exists()
    assert (worktree(repo, tmp_path) / ".aidev" / "slices" / "forged" / "approvals").is_dir()


def test_editing_the_repo_during_a_gate_no_longer_stops_implement(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """The point of isolation: a human can keep working while a gate is open."""
    requirement(repo)
    git_repo(repo)

    def approve_and_edit(_seconds):
        # a human approves, and has also been editing in the meantime
        (repo / "unrelated.py").write_text("half-finished work\n", encoding="utf-8")
        (slice_dir(repo) / "approvals" / "plan.md").write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", approve_and_edit)
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    assert state_of(repo)["status"] == "done"
    assert len(invocations(log)) == 3  # implement ran, beside that work and not on it
    assert (repo / "unrelated.py").read_text(encoding="utf-8") == "half-finished work\n"
    assert not (worktree(repo, tmp_path) / "unrelated.py").exists()


def test_resume_after_a_readonly_violation_does_not_launder_it(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    git_repo(repo)
    monkeypatch.setenv("AIDEV_FAKE_MODE", "touch")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1

    # the file the plan stage left behind is still there, so resuming must refuse
    monkeypatch.setenv("AIDEV_FAKE_MODE", "ok")
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 1
    state = state_of(repo)
    assert "uncommitted changes" in state["reason"]
    assert len(invocations(log)) == 1


def test_a_second_process_cannot_run_the_same_slice(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo)
    lock_seen = {}

    def take_the_lock_again(_seconds):
        # while the first process waits at the gate, a second one tries to resume
        lock_seen["code"] = main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last"))
        (slice_dir(repo) / "approvals" / "plan.md").write_text("approved\n", encoding="utf-8")

    monkeypatch.setattr(pipeline, "_sleep", take_the_lock_again)
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    assert lock_seen["code"] == 2  # refused, not silently interleaved
    assert not (slice_dir(repo) / ".lock").exists()  # released on the way out


def test_last_follows_time_not_alphabet(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    first = state_of(repo)["slice_id"]

    # a slice touched later whose id sorts earlier
    root = pipeline.slices_root(repo)
    older_name = root / "20260101-alpha"
    (older_name / "approvals").mkdir(parents=True)
    later = (datetime.now().astimezone() + timedelta(hours=1)).isoformat(timespec="seconds")
    (older_name / "state.json").write_text(
        json.dumps({"slice_id": "20260101-alpha", "status": "running:plan", "updated_at": later}),
        encoding="utf-8",
    )
    assert pipeline.find_slice(repo, "last").slice_id == "20260101-alpha"
    assert pipeline.find_slice(repo, first).slice_id == first


def test_ambiguous_slice_pattern_is_refused(repo, tmp_path, claude_bin, capsys):
    root = pipeline.slices_root(repo)
    for name in ("20260815-doctor", "20260815-doctor-2"):
        (root / name).mkdir(parents=True)
        (root / name / "state.json").write_text(
            json.dumps({"slice_id": name, "status": "failed"}), encoding="utf-8"
        )
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "20260815-d")) == 2
    err = capsys.readouterr().err
    assert "matches 2 slices" in err
    # the exact id still resolves
    assert pipeline.find_slice(repo, "20260815-doctor").slice_id == "20260815-doctor"


def test_quota_wait_is_finished_on_resume_not_skipped(repo, tmp_path, claude_bin, log, monkeypatch):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    # rewind the slice into a quota wait that still has time left on it
    directory = slice_dir(repo)
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    state["status"] = "quota_wait"
    state["stages"]["test"] = {"status": "pending"}
    resume_at = datetime.now().astimezone() + timedelta(seconds=90)
    state["quota"] = {"waiting": True, "resume_at": resume_at.isoformat(timespec="seconds"), "retries": 1}
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")

    slept = []
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: slept.append(seconds))
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0

    assert 60 <= sum(slept) <= 95  # it waited out the remainder instead of retrying now
    state = state_of(repo)
    assert state["status"] == "done"
    assert state["quota"]["waiting"] is False
    assert state["quota"]["resume_at"] is None


def test_missing_binary_leaves_the_slice_failed_not_running(repo, tmp_path, capsys):
    requirement(repo, front="approval: none")
    code = main(
        argv(repo, tmp_path, "definitely-not-a-real-binary-xyz", "--requirement", "tasks/doctor.md")
    )
    assert code == 127
    state = state_of(repo)
    assert state["status"] == "failed"
    assert state["stages"]["plan"]["status"] == "failed"
    assert "not found on PATH" in state["reason"]


def test_summary_counts_every_attempt_not_just_the_last(repo, tmp_path, claude_bin, log, monkeypatch, capsys):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "quota")
    monkeypatch.setenv("AIDEV_FAKE_QUOTA_FAILS", "1")
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: None)

    assert main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--quota-wait", "1")
    ) == 0
    out = capsys.readouterr().out
    # 4 runs at $0.01 each: the failed attempt cost real money too
    assert "$0.0400" in out
    assert "Try" in out


# ------------------------------------------------------- workspace isolation


def run_slice(repo, tmp_path, claude_bin, *extra):
    return main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", *extra))


def branches(repo):
    # git marks the checked-out branch with '*' and a worktree's with '+'
    return [line[2:].strip() for line in git(repo, "branch", "--list").stdout.splitlines()]


def test_one_command_creates_the_workspace_and_finishes(repo, tmp_path, claude_bin, log):
    """The whole Done Criteria in one run: worktree, branch, four commits, no trace here."""
    requirement(repo, front="approval: none")
    git_repo(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    assert run_slice(repo, tmp_path, claude_bin) == 0
    state = state_of(repo)
    slice_id = state["slice_id"]
    work = worktree(repo, tmp_path, slice_id)

    assert work.is_dir()
    assert "slice/{0}".format(slice_id) in branches(repo)
    assert subjects(repo, "{0}..slice/{1}".format(head, slice_id)) == [
        "slice({0}): requirement".format(slice_id),
        "slice({0}): plan".format(slice_id),
        "slice({0}): implement".format(slice_id),
        "slice({0}): test".format(slice_id),
    ]

    # every stage really ran in the worktree, not in the user's checkout
    for call in invocations(log):
        assert Path(call["cwd"]).resolve() == work.resolve()

    # the checkout did not move and grew nothing but the slice's own record
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head
    porcelain = git(repo, "status", "--porcelain", "-uall").stdout.splitlines()
    assert porcelain and all(".aidev/slices/" in line for line in porcelain)

    ws = state["workspace"]
    assert ws["branch"] == "slice/{0}".format(slice_id)
    assert ws["base"] == BASE_BRANCH
    assert Path(ws["path"]).resolve() == work.resolve()
    assert state["schema"] == 2


def test_base_defaults_to_head_not_main(repo, tmp_path, claude_bin, log):
    """The measured repo's development line is not main, and main is not assumed."""
    requirement(repo, front="approval: none")
    git_repo(repo)
    git(repo, "branch", "main")
    (repo / "only-on-main.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "main moves on")
    git(repo, "branch", "-f", "main", "HEAD")
    git(repo, "reset", "-q", "--hard", "HEAD~1")

    assert run_slice(repo, tmp_path, claude_bin) == 0
    slice_id = state_of(repo)["slice_id"]

    merge_base = git(repo, "merge-base", "slice/" + slice_id, BASE_BRANCH).stdout.strip()
    assert merge_base == git(repo, "rev-parse", BASE_BRANCH).stdout.strip()
    assert not (worktree(repo, tmp_path) / "only-on-main.txt").exists()


def test_base_flag_selects_another_branch(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    git_repo(repo)
    git(repo, "checkout", "-q", "-b", "main")
    (repo / "only-on-main.txt").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "main moves on")
    git(repo, "checkout", "-q", BASE_BRANCH)

    assert run_slice(repo, tmp_path, claude_bin, "--base", "main") == 0
    assert state_of(repo)["workspace"]["base"] == "main"
    assert (worktree(repo, tmp_path) / "only-on-main.txt").exists()


def test_existing_worktree_path_is_refused(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none")
    slice_id = "{0}-doctor".format(datetime.now().strftime("%Y%m%d"))
    occupied = tmp_path / "wt" / slice_id
    occupied.mkdir(parents=True)
    (occupied / "someone-elses-file.txt").write_text("not ours\n", encoding="utf-8")

    assert run_slice(repo, tmp_path, claude_bin) == 2
    err = capsys.readouterr().err
    assert "already exists" in err and slice_id in err
    assert invocations(log) == []
    assert not pipeline.slices_root(repo).exists()
    # refused, and left exactly as it was: cleaning is the human's job
    assert (occupied / "someone-elses-file.txt").exists()


def test_registered_stale_worktree_is_refused_not_reused(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none")
    slice_id = "{0}-doctor".format(datetime.now().strftime("%Y%m%d"))
    path = tmp_path / "wt" / slice_id
    git(repo, "worktree", "add", "-q", "-b", "orca-leftover", str(path))
    (path / "orca.txt").write_text("half a migration\n", encoding="utf-8")

    assert run_slice(repo, tmp_path, claude_bin) == 2
    assert "orca-leftover" in capsys.readouterr().err
    assert (path / "orca.txt").read_text(encoding="utf-8") == "half a migration\n"
    assert "orca-leftover" in branches(repo)


def test_existing_slice_branch_is_refused(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none")
    slice_id = "{0}-doctor".format(datetime.now().strftime("%Y%m%d"))
    git(repo, "branch", "slice/{0}".format(slice_id))

    assert run_slice(repo, tmp_path, claude_bin) == 2
    assert "branch already exists" in capsys.readouterr().err
    assert invocations(log) == []


def test_stale_worktrees_elsewhere_are_reported_not_cleaned(
    repo, tmp_path, claude_bin, log, capsys
):
    """12 Orca leftovers in the target repo must warn, not block, and not be pruned."""
    requirement(repo, front="approval: none")
    for name in ("orca-1", "orca-2"):
        path = tmp_path / "leftovers" / name
        git(repo, "worktree", "add", "-q", "-b", name, str(path))
        shutil.rmtree(str(path))

    assert run_slice(repo, tmp_path, claude_bin) == 0
    out = capsys.readouterr().out
    registered = git(repo, "worktree", "list", "--porcelain").stdout
    assert "orca-1" in registered and "orca-2" in registered  # never pruned for them
    if pipeline.workspace.stale_worktrees(repo):  # 'prunable' needs git >= 2.36
        assert "stale worktree" in out
        assert "orca-1" in out and "orca-2" in out


def workspace_clean(path):
    return not git(path, "status", "--porcelain", "-uall").stdout.strip()


def test_requirement_is_the_first_commit_not_a_dirty_file(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """v0.2's circle: copying the requirement in was itself what made the tree dirty."""
    requirement(repo, front="approval: plan")
    git_repo(repo)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(pipeline, "_sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt))

    assert run_slice(repo, tmp_path, claude_bin) == 130  # killed while the gate is open
    slice_id = state_of(repo)["slice_id"]
    work = worktree(repo, tmp_path, slice_id)

    commits = subjects(repo, "{0}..slice/{1}".format(head, slice_id))
    assert commits[0] == "slice({0}): requirement".format(slice_id)
    first = git(repo, "rev-list", "--reverse", "{0}..slice/{1}".format(head, slice_id)
                ).stdout.split()[0]
    tree = git(repo, "ls-tree", "-r", "--name-only", first).stdout
    assert ".aidev/history/{0}/requirement.md".format(slice_id) in tree
    # committed, so the plan stage that follows starts from a clean baseline
    assert workspace_clean(work)


def test_stage_commits_carry_the_slice_metadata(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    git_repo(repo)
    assert run_slice(repo, tmp_path, claude_bin) == 0

    state = state_of(repo)
    slice_id = state["slice_id"]
    commits = state["commits"]
    assert set(commits) == {"requirement", "plan", "implement", "test"}
    for stage in ("plan", "implement", "test"):
        assert state["stages"][stage]["commit"] == commits[stage]

    path = ".aidev/history/{0}/slice.json".format(slice_id)
    at_implement = json.loads(git(repo, "show", "{0}:{1}".format(commits["implement"], path)).stdout)
    assert at_implement["slice_id"] == slice_id
    assert at_implement["branch"] == "slice/{0}".format(slice_id)
    assert at_implement["base"] == BASE_BRANCH
    assert at_implement["stages"]["implement"]["status"] == "done"
    assert at_implement["stages"]["test"]["status"] == "pending"  # not yet run
    assert at_implement["stages"]["plan"]["commit"] == commits["plan"]
    assert sum(int(r["turns"] or 0) for r in at_implement["runs"]) == 2

    # the plan the human could have edited is on the branch too
    plan_md = git(repo, "show", "{0}:.aidev/history/{1}/plan.md".format(commits["plan"], slice_id))
    assert "touch aidev/thing.py" in plan_md.stdout


def test_the_slice_state_stays_in_the_checkout_not_in_the_worktree(
    repo, tmp_path, claude_bin, log
):
    """Live state has one writer and one home; the branch only gets a projection."""
    requirement(repo, front="approval: none")
    assert run_slice(repo, tmp_path, claude_bin) == 0
    work = worktree(repo, tmp_path)

    assert (slice_dir(repo) / "state.json").exists()
    assert not (work / ".aidev" / "slices").exists()
    # the two paths differ on purpose: the same path would collide at merge time
    assert (work / ".aidev" / "history" / state_of(repo)["slice_id"] / "slice.json").exists()


# ------------------------------------------------------------------- setup


def setup_script(tmp_path, body):
    path = tmp_path / "setup_action.py"
    path.write_text(body, encoding="utf-8")
    return '"{0}" "{1}"'.format(sys.executable, path)


def test_setup_runs_once_before_implement(repo, tmp_path, claude_bin, log):
    counter = tmp_path / "setup-calls.txt"
    command = setup_script(
        tmp_path,
        "import pathlib\n"
        "pathlib.Path(r'{0}').open('a').write('ran\\n')\n"
        "pathlib.Path('installed.txt').write_text('deps')\n".format(counter),
    )
    requirement(repo, front="approval: none\nsetup: {0}".format(command))

    assert run_slice(repo, tmp_path, claude_bin) == 0
    # it ran once, in the worktree
    assert counter.read_text(encoding="utf-8").count("ran") == 1
    assert (worktree(repo, tmp_path) / "installed.txt").exists()
    assert not (repo / "installed.txt").exists()

    state = state_of(repo)
    assert state["setup"]["status"] == "done"
    assert state["setup"]["exit_code"] == 0
    assert (slice_dir(repo) / "setup.log").exists()

    # the same command is granted to the stages that may run commands
    plan, implement, test = (call["argv"] for call in invocations(log))
    assert "--allowedTools" not in plan
    for stage_argv in (implement, test):
        rules = stage_argv[stage_argv.index("--allowedTools") + 1].split(",")
        assert "Bash({0})".format(command) in rules
        assert any(rule.endswith(':*)') and rule.startswith("Bash(") for rule in rules)

    # resuming does not install again
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", "last")) == 0
    assert counter.read_text(encoding="utf-8").count("ran") == 1


def test_no_setup_declared_runs_nothing(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert run_slice(repo, tmp_path, claude_bin) == 0

    assert "setup" not in state_of(repo)
    implement = invocations(log)[1]["argv"]
    rules = implement[implement.index("--allowedTools") + 1].split(",")
    assert set(rules) == set(pipeline.TEST_COMMAND_TOOLS)


def test_setup_failure_stops_before_implement(repo, tmp_path, claude_bin, log):
    command = setup_script(tmp_path, "import sys\nsys.stderr.write('no lockfile\\n')\nraise SystemExit(3)\n")
    requirement(repo, front="approval: none\nsetup: {0}".format(command))

    assert run_slice(repo, tmp_path, claude_bin) == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "setup command failed (exit 3)" in state["reason"]
    assert state["setup"]["status"] == "failed"
    assert "no lockfile" in (slice_dir(repo) / "setup.log").read_text(encoding="utf-8")
    # implement never ran on a half-built environment
    assert len(invocations(log)) == 1
    assert state["stages"]["implement"]["status"] == "pending"


def test_setup_rejects_a_shell_chain(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none\nsetup: npm ci && rm -rf /")
    assert run_slice(repo, tmp_path, claude_bin) == 2
    assert "not allowed" in capsys.readouterr().err
    assert invocations(log) == []


# ---------------------------------------------------------- merge / discard


def finished_slice(repo, tmp_path, claude_bin):
    requirement(repo, front="approval: none")
    git_repo(repo)
    assert run_slice(repo, tmp_path, claude_bin) == 0
    return state_of(repo)["slice_id"]


def test_merge_lands_on_a_non_main_base(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    tip = git(repo, "rev-parse", "slice/{0}".format(slice_id)).stdout.strip()

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 0
    assert BASE_BRANCH in git(repo, "branch", "--contains", tip).stdout
    assert "slice({0}): merge".format(slice_id) in git(repo, "log", "--format=%s", "-1").stdout
    assert (repo / ".aidev" / "history" / slice_id / "requirement.md").exists()

    state = state_of(repo)
    assert state["status"] == "merged"
    assert state["merge"]["base"] == BASE_BRANCH
    # the worktree is not ours to delete just because the merge worked
    assert worktree(repo, tmp_path, slice_id).is_dir()
    assert "--discard" in capsys.readouterr().out


def test_merge_conflict_aborts_and_reports(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    work = worktree(repo, tmp_path, slice_id)

    (work / "README.md").write_text("the slice's version\n", encoding="utf-8")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "slice edit")
    (repo / "README.md").write_text("the human's version\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "human edit")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 4
    out = capsys.readouterr().out
    assert "MERGE CONFLICT" in out and "README.md" in out

    # nothing was resolved, and the checkout is exactly as it was
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before
    assert (repo / "README.md").read_text(encoding="utf-8") == "the human's version\n"
    assert not git(repo, "status", "--porcelain").stdout.strip().startswith("UU")
    assert "slice/{0}".format(slice_id) in branches(repo)
    assert state_of(repo)["merge"]["status"] == "conflict"


def test_merge_refuses_an_unfinished_slice(repo, tmp_path, claude_bin, log, capsys, monkeypatch):
    requirement(repo, front="approval: none")
    monkeypatch.setenv("AIDEV_FAKE_MODE", "fail")
    assert run_slice(repo, tmp_path, claude_bin) == 1
    slice_id = state_of(repo)["slice_id"]

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 2
    assert "not 'done'" in capsys.readouterr().err


def test_merge_refuses_when_the_repo_is_not_on_the_base(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    git(repo, "checkout", "-q", "-b", "somewhere-else")

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 2
    err = capsys.readouterr().err
    assert "somewhere-else" in err and "checkout {0}".format(BASE_BRANCH) in err
    # we never switch the user's branch for them
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "somewhere-else"


def test_merge_refuses_to_drop_uncommitted_work_in_the_worktree(
    repo, tmp_path, claude_bin, log, capsys
):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    (worktree(repo, tmp_path, slice_id) / "unfinished.py").write_text("wip\n", encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 2
    assert "uncommitted work" in capsys.readouterr().err


def test_merge_refuses_a_dirty_checkout(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    (repo / "README.md").write_text("edited but not committed\n", encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--merge", slice_id)) == 2
    assert "commit or stash" in capsys.readouterr().err


def test_discard_leaves_no_git_trace(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    work = worktree(repo, tmp_path, slice_id)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()

    assert main(argv(repo, tmp_path, claude_bin, "--discard", slice_id)) == 0
    assert not work.exists()
    assert str(work) not in git(repo, "worktree", "list", "--porcelain").stdout
    assert not [name for name in branches(repo) if name.startswith("slice/")]
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head

    state = state_of(repo)
    assert state["status"] == "discarded"
    assert state["discard"]["branch"] == "slice/{0}".format(slice_id)
    # the record of what happened survives the workspace it describes
    assert (slice_dir(repo) / "state.json").exists()
    assert "recoverable" in capsys.readouterr().out


def test_a_discarded_slice_cannot_be_resumed(repo, tmp_path, claude_bin, log, capsys):
    slice_id = finished_slice(repo, tmp_path, claude_bin)
    assert main(argv(repo, tmp_path, claude_bin, "--discard", slice_id)) == 0
    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id)) == 2
    assert "nothing left to resume" in capsys.readouterr().err


def test_a_vanished_worktree_is_reported_not_recreated(repo, tmp_path, claude_bin, log, capsys):
    requirement(repo, front="approval: none")
    monkeypatch_free = main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md"))
    assert monkeypatch_free == 0
    slice_id = state_of(repo)["slice_id"]
    shutil.rmtree(str(worktree(repo, tmp_path, slice_id)))

    # rewind one stage so the resume has something to do
    directory = slice_dir(repo)
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    state["stages"]["test"] = {"status": "pending"}
    state["status"] = "failed"
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id)) == 2
    err = capsys.readouterr().err
    assert "gone" in err and "--discard" in err


# --------------------------------------------------------------- edge cases


def test_legacy_slice_without_workspace_resumes_in_place(repo, tmp_path, claude_bin, log):
    """A v0.2 state.json has no workspace key, and v0.2's rules still apply to it."""
    requirement(repo, front="approval: none")
    assert run_slice(repo, tmp_path, claude_bin) == 0
    slice_id = state_of(repo)["slice_id"]

    directory = slice_dir(repo)
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    for key in ("workspace", "commits", "mutated"):
        state.pop(key, None)
    state["schema"] = 1
    state["stages"]["test"] = {"status": "pending"}
    state["status"] = "failed"
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    git_repo(repo)  # v0.2 needed a clean checkout, and still does

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id)) == 0
    assert Path(invocations(log)[-1]["cwd"]).resolve() == repo.resolve()

    # and the v0.2 dirty rule is still the one guarding it
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    state.pop("mutated", None)
    state["stages"]["test"] = {"status": "pending"}
    state["status"] = "failed"
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (repo / "human-work.txt").write_text("in progress\n", encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id)) == 1
    assert "uncommitted changes" in state_of(repo, slice_id)["reason"]


def test_non_git_repo_is_refused_with_a_way_out(tmp_path, claude_bin, log, capsys):
    plain = tmp_path / "not-a-repo"
    (plain / "tasks").mkdir(parents=True)
    requirement(plain, front="approval: none")

    assert run_slice(plain, tmp_path, claude_bin) == 2
    err = capsys.readouterr().err
    assert "not a git repository" in err and "--no-worktree" in err
    assert invocations(log) == []

    assert run_slice(plain, tmp_path, claude_bin, "--no-worktree") == 0
    assert state_of(plain)["status"] == "done"
    assert "workspace" not in state_of(plain)
    assert Path(invocations(log)[0]["cwd"]).resolve() == plain.resolve()


def test_commit_file_limit_refuses_a_node_modules_explosion(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    requirement(repo, front="approval: none")
    git_repo(repo)
    monkeypatch.setenv("AIDEV_FAKE_FILES", "3")

    assert run_slice(repo, tmp_path, claude_bin, "--commit-file-limit", "2") == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "--commit-file-limit of 2" in state["reason"]
    # the stage's own work is untouched; only the commit was refused
    assert state["stages"]["implement"]["status"] == "done"
    assert set(state["commits"]) == {"requirement", "plan"}
    assert (worktree(repo, tmp_path) / "generated-0.txt").exists()


def test_dry_run_prints_the_workspace_plan_and_touches_nothing(
    repo, tmp_path, claude_bin, capsys
):
    requirement(repo, front="approval: none\nsetup: npm ci --prefix backend")
    assert run_slice(repo, tmp_path, claude_bin, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "base      {0}".format(BASE_BRANCH) in out
    assert "branch    slice/" in out
    assert "npm ci --prefix backend" in out
    assert str(tmp_path / "wt") in out

    assert not pipeline.slices_root(repo).exists()
    assert not (tmp_path / "wt").exists()
    assert not [name for name in branches(repo) if name.startswith("slice/")]


def test_two_commands_at_once_are_a_usage_error(repo, tmp_path, claude_bin, capsys):
    assert main(argv(repo, tmp_path, claude_bin, "--merge", "a", "--discard", "b")) == 2
    assert "cannot be combined" in capsys.readouterr().err


# --------------------------------------------------------------- unit checks


def test_stages_are_real_telemetry_phases():
    assert set(pipeline.STAGES) <= set(PHASES)
    assert set(pipeline.STAGE_POLICY) == set(pipeline.STAGES)


@pytest.mark.parametrize(
    "front, expected",
    [
        (None, ("plan",)),
        ("approval: none", ()),
        ("approval: plan", ("plan",)),
        ("approval: plan, implement", ("plan", "implement")),
        ("approval: implement plan", ("plan", "implement")),  # normalised to stage order
        ("approval: [plan, test]", ("plan", "test")),
        ("title: something else", ("plan",)),
        # the spec's own example carries a trailing comment
        ("approval: plan, implement    # 게이트 추가 지정", ("plan", "implement")),
        ("approval: none             # 완전 무인", ()),
    ],
)
def test_gate_resolution(front, expected):
    text = "---\n{0}\n---\nbody\n".format(front) if front is not None else "body\n"
    fields, body = pipeline.parse_front_matter(text)
    assert body.strip() == "body"
    assert pipeline.resolve_gates(fields) == expected


@pytest.mark.parametrize("front", ["approval: browser", "approval: none, plan", "approval:"])
def test_gates_that_must_be_refused(front):
    """An empty value must not silently disarm the gate - 'none' has to be explicit."""
    fields, _ = pipeline.parse_front_matter("---\n{0}\n---\nbody\n".format(front))
    with pytest.raises(pipeline.PipelineError):
        pipeline.resolve_gates(fields)


def test_front_matter_survives_a_bom_and_crlf():
    fields, body = pipeline.parse_front_matter("﻿---\r\napproval: none\r\n---\r\n# title\r\n")
    assert fields == {"approval": "none"}
    assert body.strip() == "# title"


@pytest.mark.parametrize(
    "text, verdict, reason",
    [
        ("approved\n", pipeline.APPROVED, ""),
        ("# comment\n\napproved\n", pipeline.APPROVED, ""),
        ("APPROVED", pipeline.APPROVED, ""),
        ("rejected: too broad\n", pipeline.REJECTED, "too broad"),
        ("reject: nope", pipeline.REJECTED, "nope"),
        ("rejected\n", pipeline.REJECTED, ""),
        ("# only the template\n", pipeline.PENDING, ""),
        ("", pipeline.PENDING, ""),
        ("thinking about it\n", pipeline.PENDING, ""),
    ],
)
def test_decision_parsing(tmp_path, text, verdict, reason):
    path = tmp_path / "plan.md"
    path.write_text(text, encoding="utf-8")
    decision = pipeline.read_decision(path)
    assert (decision.verdict, decision.reason) == (verdict, reason)


def test_decision_of_a_missing_file_is_pending(tmp_path):
    assert pipeline.read_decision(tmp_path / "absent.md").verdict == pipeline.PENDING


def test_state_write_retries_a_transient_windows_replace_collision(tmp_path, monkeypatch):
    rec = pipeline.SliceRecord(tmp_path, "20260815-doctor").ensure()
    real_write = pipeline.write_json_atomic
    calls = []
    slept = []

    def locked_once(path, data):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("state.json is briefly open by a reader")
        real_write(path, data)

    monkeypatch.setattr(pipeline, "write_json_atomic", locked_once)
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: slept.append(seconds))
    rec.write_state({"status": "pending"})

    assert len(calls) == 2
    assert slept == [pipeline._STATE_REPLACE_RETRY_S]
    assert rec.read_state()["status"] == "pending"
    assert not rec.state_path.with_name("state.json.tmp").exists()


def test_quota_markers_do_not_fire_on_ordinary_failures():
    assert pipeline.looks_like_quota("Claude AI usage limit reached|1755302400")
    assert pipeline.looks_like_quota("API Error: 429 Too Many Requests")
    assert not pipeline.looks_like_quota("error_max_turns")
    assert not pipeline.looks_like_quota("TypeError: undefined is not a function")


def test_reset_time_parsing():
    now = 1_755_300_000.0
    assert pipeline.parse_reset_at("usage limit reached|1755302400", now=now) == 1_755_302_400
    # a timestamp in the past, or far in the future, is a false match
    assert pipeline.parse_reset_at("usage limit reached|1755200000", now=now) is None
    assert pipeline.parse_reset_at("no time here at all", now=now) is None
    assert pipeline.parse_reset_at("total 1200000000 tokens", now=now) is None


def test_wait_is_chunked_but_exact(monkeypatch):
    slept = []
    monkeypatch.setattr(pipeline, "_sleep", lambda seconds: slept.append(seconds))
    pipeline.sleep_seconds(75)
    assert sum(slept) == 75
    assert max(slept) <= pipeline._WAIT_CHUNK_S
