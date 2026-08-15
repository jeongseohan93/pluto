import json
import sys
from pathlib import Path

import pytest

from aidev import runner
from aidev.storage import RunStore
from aidev.telemetry import Telemetry

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


def make_config(repo: Path, prompt: Path, **kwargs) -> runner.RunConfig:
    defaults = dict(
        repo=repo,
        prompt_path=prompt,
        prompt=prompt.read_text(encoding="utf-8"),
        phase="implement",
        project="joker",
        task="doctor",
        claude_cmd=[sys.executable, str(FAKE_CLAUDE)],
    )
    defaults.update(kwargs)
    return runner.RunConfig(**defaults)


def test_build_command_has_the_required_flags():
    cfg = runner.RunConfig(
        repo=Path("."), prompt_path=Path("p.md"), prompt="hi", max_turns=80, model="opus"
    )
    cmd = runner.build_command(cfg)
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in cmd
    assert cmd[cmd.index("--max-turns") + 1] == "80"
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert "--permission-mode" not in cmd


def test_build_command_optional_flags():
    cfg = runner.RunConfig(
        repo=Path("."),
        prompt_path=Path("p.md"),
        prompt="hi",
        permission_mode="acceptEdits",
        allowed_tools="Read,Edit",
        resume_session="abc",
        extra_args=["--foo"],
    )
    cmd = runner.build_command(cfg)
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--allowedTools") + 1] == "Read,Edit"
    assert cmd[cmd.index("--resume") + 1] == "abc"
    assert cmd[-1] == "--foo"


def test_readonly_safety_profile_blocks_mutating_tools():
    cfg = runner.RunConfig(repo=Path("."), prompt_path=Path("p.md"), prompt="hi", safety_profile="readonly")
    blocked = runner.resolve_disallowed_tools(cfg).split(",")
    assert {"Bash", "Edit", "MultiEdit", "Write", "NotebookEdit", "Task"} <= set(blocked)
    assert "Read" not in blocked
    assert "Grep" not in blocked

    cmd = runner.build_command(cfg)
    assert cmd[cmd.index("--disallowedTools") + 1] == ",".join(blocked)
    assert cfg.to_dict()["effective_disallowed_tools"] == ",".join(blocked)


def test_default_profile_adds_no_disallowed_tools():
    cfg = runner.RunConfig(repo=Path("."), prompt_path=Path("p.md"), prompt="hi")
    assert runner.resolve_disallowed_tools(cfg) is None
    assert "--disallowedTools" not in runner.build_command(cfg)


def test_disallowed_tools_flag_alone():
    cfg = runner.RunConfig(
        repo=Path("."), prompt_path=Path("p.md"), prompt="hi", disallowed_tools="WebFetch, WebSearch"
    )
    cmd = runner.build_command(cfg)
    assert cmd[cmd.index("--disallowedTools") + 1] == "WebFetch,WebSearch"


def test_profile_and_flag_merge_without_duplicates():
    cfg = runner.RunConfig(
        repo=Path("."),
        prompt_path=Path("p.md"),
        prompt="hi",
        safety_profile="readonly",
        disallowed_tools="Bash,WebFetch,,",
    )
    blocked = runner.resolve_disallowed_tools(cfg).split(",")
    assert blocked.count("Bash") == 1
    assert blocked[-1] == "WebFetch"
    assert "" not in blocked


def test_resolve_command_missing_binary():
    with pytest.raises(runner.ClaudeNotFound):
        runner.resolve_command(["definitely-not-a-real-binary-xyz"])


def test_execute_collects_everything(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    prompt = tmp_path / "doctor.md"
    prompt.write_text("implement the doctor night action", encoding="utf-8")

    cfg = make_config(repo, prompt)
    telemetry = Telemetry("run-1", "joker", "implement", str(repo), str(prompt), task="doctor")
    seen = []

    with RunStore(tmp_path / "runs", "run-1") as store:
        result = runner.execute(
            cfg, store, telemetry, on_event=lambda event, tel: seen.append(event["type"])
        )
        telemetry.finish(result.status, result.exit_code)
        store.write_telemetry(telemetry.to_dict())

    assert result.exit_code == 0
    assert result.status == "completed"
    assert seen[0] == "system" and seen[-1] == "result"

    # raw events are preserved verbatim, including the unparsable line
    raw = (tmp_path / "runs" / "run-1" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 8  # includes the duplicate and the unparsable line
    assert raw[4] == "this line is not json"
    assert json.loads(raw[0])["type"] == "system"
    assert json.loads(raw[1]) == json.loads(raw[2])  # the duplicate is preserved raw

    data = telemetry.to_dict()
    assert data["exact"]["session_id"] == "fake-session"
    assert data["exact"]["num_turns"] == 2
    assert data["exact"]["total_cost_usd"] == 0.0421
    assert data["observed"]["malformed_lines"] == 1
    assert data["observed"]["tool_counts"] == {"Read": 1, "Bash": 1}
    assert data["observed"]["test_runs"] == 1
    assert data["estimated"]["hotspots"]["bytes"]["exploration"] == 512

    # the duplicate is dropped once, and only once, from every counter
    assert data["observed"]["duplicate_messages"] == 1
    assert data["observed"]["turns_seen"] == 2
    assert data["exact"]["usage_streamed"] == {
        "input_tokens": 50,
        "cache_creation_input_tokens": 300,
        "cache_read_input_tokens": 2400,
        "output_tokens": 50,
    }
    assert data["exact"]["reconciliation"]["matches"] is True
    assert data["exact"]["tokens"]["peak_context"] == 1510
    assert data["estimated"]["exact_vs_estimated"]["comparable"] is True

    # the prompt really reached the child process through stdin
    stderr = (tmp_path / "runs" / "run-1" / "stderr.log").read_text(encoding="utf-8")
    assert "got {0} prompt chars".format(len(cfg.prompt)) in stderr
    assert "--output-format stream-json" in stderr


def test_execute_marks_failure_on_nonzero_exit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    prompt = tmp_path / "p.md"
    prompt.write_text("hi", encoding="utf-8")
    boom = tmp_path / "boom.py"
    boom.write_text("import sys; sys.stdin.read(); sys.exit(3)", encoding="utf-8")

    cfg = make_config(repo, prompt, claude_cmd=[sys.executable, str(boom)])
    telemetry = Telemetry("run-2", "joker", "implement", str(repo), str(prompt))
    with RunStore(tmp_path / "runs", "run-2") as store:
        result = runner.execute(cfg, store, telemetry)

    assert result.exit_code == 3
    assert result.status == "failed"
