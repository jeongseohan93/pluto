import json
import os
import stat
import sys
from pathlib import Path

import pytest

from aidev.cli import main

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture
def fake_claude_bin(tmp_path):
    """A launcher shim so the CLI can be driven exactly like the real thing."""
    if os.name == "nt":
        path = tmp_path / "claude.cmd"
        path.write_text('@echo off\r\n"{0}" "{1}" %*\r\n'.format(sys.executable, FAKE_CLAUDE))
    else:
        path = tmp_path / "claude.sh"
        path.write_text('#!/bin/sh\nexec "{0}" "{1}" "$@"\n'.format(sys.executable, FAKE_CLAUDE))
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return str(path)


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "jokertest"
    (repo / "tasks").mkdir(parents=True)
    (repo / "tasks" / "doctor.md").write_text("implement the doctor action", encoding="utf-8")
    return repo


def test_run_end_to_end(tmp_path, workspace, fake_claude_bin, capsys):
    data_dir = tmp_path / "data"
    code = main(
        [
            "--data-dir", str(data_dir),
            "run",
            "--repo", str(workspace),
            "--prompt", "tasks/doctor.md",
            "--phase", "implement",
            "--claude-bin", fake_claude_bin,
            "--no-live",
        ]
    )
    assert code == 0

    out = capsys.readouterr().out
    assert "TOKEN / CONTEXT HOTSPOTS" in out
    assert "fake-session" in out
    assert "Tokens (EXACT)" in out
    assert "EXACT vs ESTIMATED" in out
    assert "Estimated attributable tool context" in out
    assert "duplicate assistant message(s) dropped by request_id" in out

    run_dirs = list((data_dir / "runs").iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert run_dir.name.endswith("-doctor")
    for name in ("run.json", "events.jsonl", "telemetry.json", "prompt.md", "stderr.log"):
        assert (run_dir / name).exists(), name

    telemetry = json.loads((run_dir / "telemetry.json").read_text(encoding="utf-8"))
    assert telemetry["project"] == "jokertest"
    assert telemetry["phase"] == "implement"
    assert telemetry["status"] == "completed"
    assert telemetry["exact"]["session_id"] == "fake-session"
    assert telemetry["observed"]["tool_counts"] == {"Read": 1, "Bash": 1}

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["config"]["phase"] == "implement"
    assert "--output-format" in meta["command"]
    assert (data_dir / "aidev.db").exists()

    # report / list / stats all read back what the run wrote
    assert main(["--data-dir", str(data_dir), "report", "last"]) == 0
    assert "TOKEN / CONTEXT HOTSPOTS" in capsys.readouterr().out

    assert main(["--data-dir", str(data_dir), "report", run_dir.name, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == run_dir.name

    assert main(["--data-dir", str(data_dir), "list"]) == 0
    assert run_dir.name in capsys.readouterr().out

    assert main(["--data-dir", str(data_dir), "stats"]) == 0
    assert "IMPLEMENT" in capsys.readouterr().out


def test_run_with_readonly_profile(tmp_path, workspace, fake_claude_bin, capsys):
    data_dir = tmp_path / "data"
    code = main(
        [
            "--data-dir", str(data_dir),
            "run",
            "--repo", str(workspace),
            "--prompt", "tasks/doctor.md",
            "--phase", "review",
            "--safety", "readonly",
            "--disallowed-tools", "WebFetch",
            "--claude-bin", fake_claude_bin,
            "--no-live",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Safety    readonly" in out
    # the stub ignores --disallowedTools and runs Bash anyway, so we must notice
    assert "SAFETY VIOLATION" in out

    run_dir = next((data_dir / "runs").iterdir())
    telemetry = json.loads((run_dir / "telemetry.json").read_text(encoding="utf-8"))
    assert telemetry["safety_profile"] == "readonly"
    assert telemetry["disallowed_tools"].startswith("Bash,")
    assert telemetry["disallowed_tools"].endswith(",WebFetch")
    assert [v["tool"] for v in telemetry["observed"]["safety_violations"]] == ["Bash"]

    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    command = meta["command"]
    assert "WebFetch" in command[command.index("--disallowedTools") + 1]


def test_dry_run_prints_command(tmp_path, workspace, capsys):
    code = main(
        [
            "--data-dir", str(tmp_path / "data"),
            "run",
            "--repo", str(workspace),
            "--prompt", "tasks/doctor.md",
            "--dry-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "claude -p --output-format stream-json --verbose --max-turns 80" in out
    assert not (tmp_path / "data").exists()


def test_missing_repo_and_prompt(tmp_path, workspace, capsys):
    assert main(["run", "--repo", str(tmp_path / "nope"), "--prompt", "x.md"]) == 2
    assert "--repo not found" in capsys.readouterr().err

    assert main(["run", "--repo", str(workspace), "--prompt", "missing.md"]) == 2
    assert "--prompt not found" in capsys.readouterr().err


def test_a_prompt_that_is_not_utf8_is_refused_in_one_line(tmp_path, workspace, capsys):
    """The same refusal the pipeline gives: which file, and what to do about it.

    Nothing above cmd_run catches PipelineError, so this path says the sentence
    itself rather than raising it - a traceback about codecs names no file.
    """
    (workspace / "tasks" / "doctor.md").write_bytes("# 의사\n밤 페이즈 보호.\n".encode("cp949"))

    code = main(
        [
            "--data-dir", str(tmp_path / "data"),
            "run",
            "--repo", str(workspace),
            "--prompt", "tasks/doctor.md",
            "--no-live",
        ]
    )

    assert code == 2
    err = capsys.readouterr().err
    assert "doctor.md" in err
    assert "utf-8 인코딩 확인" in err
    assert "Traceback" not in err
    assert not (tmp_path / "data" / "runs").exists()


def test_missing_claude_binary(tmp_path, workspace, capsys):
    code = main(
        [
            "--data-dir", str(tmp_path / "data"),
            "run",
            "--repo", str(workspace),
            "--prompt", "tasks/doctor.md",
            "--claude-bin", "definitely-not-a-real-binary-xyz",
            "--no-live",
        ]
    )
    assert code == 127
    assert "not found on PATH" in capsys.readouterr().err


def test_report_without_runs(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path / "data"), "report", "last"]) == 2
    assert "no run matching" in capsys.readouterr().err
    assert main(["--data-dir", str(tmp_path / "data"), "list"]) == 0
    assert "(no runs yet)" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 1
    assert "usage: aidev" in capsys.readouterr().out
