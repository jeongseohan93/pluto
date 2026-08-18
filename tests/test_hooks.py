"""The write guard: Write may create a file, never rewrite one.

Two halves, because two different things have to be true. The guard itself is
run as a real subprocess with real JSON on stdin, which is exactly how the CLI
runs it. The pipeline half only proves the *wiring* - that ``--settings`` points
at a file whose contents arm the hook - because ``fake_pipeline_claude.py`` is
not Claude Code and does not execute hooks. That limit is the reason the two
halves exist separately.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aidev import pipeline
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
    invocations,
    log,
    repo,
    requirement,
    slice_dir,
)

GUARD = Path(pipeline.__file__).with_name("hooks") / "write_guard.py"


def call(payload, env=None, raw=None):
    """Run the guard exactly as the CLI would: JSON on stdin, exit code out."""
    proc = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload) if raw is None else raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        env=({**dict(__import__("os").environ), **env} if env else None),
    )
    return proc.returncode, proc.stderr


def test_write_to_an_existing_file_is_blocked(tmp_path):
    target = tmp_path / "big.py"
    target.write_text("x = 1\n", encoding="utf-8")

    code, err = call({"tool_name": "Write", "tool_input": {"file_path": str(target)}})

    assert code == 2  # PreToolUse: 2 blocks the call and shows stderr to the model
    assert "Use Edit" in err
    assert "big.py" in err


def test_write_to_a_new_file_is_allowed(tmp_path):
    code, err = call({"tool_name": "Write", "tool_input": {"file_path": str(tmp_path / "new.py")}})

    assert code == 0
    assert err == ""


def test_a_relative_path_is_resolved_against_cwd(tmp_path):
    (tmp_path / "there.py").write_text("x\n", encoding="utf-8")

    blocked, _ = call(
        {"tool_name": "Write", "tool_input": {"file_path": "there.py"}, "cwd": str(tmp_path)}
    )
    allowed, _ = call(
        {"tool_name": "Write", "tool_input": {"file_path": "elsewhere.py"}, "cwd": str(tmp_path)}
    )

    assert (blocked, allowed) == (2, 0)


def test_edit_is_never_touched(tmp_path):
    target = tmp_path / "big.py"
    target.write_text("x = 1\n", encoding="utf-8")

    code, _ = call({"tool_name": "Edit", "tool_input": {"file_path": str(target)}})

    assert code == 0


@pytest.mark.parametrize(
    "raw", ["", "not json at all", "[1, 2, 3]", '{"tool_name": "Write"}']
)
def test_anything_it_cannot_read_fails_open(raw):
    """A broken guard must cost the pipeline nothing more than the guard itself."""
    code, _ = call(None, raw=raw)
    assert code == 0


def test_the_env_switch_turns_it_off(tmp_path):
    target = tmp_path / "big.py"
    target.write_text("x = 1\n", encoding="utf-8")

    code, _ = call(
        {"tool_name": "Write", "tool_input": {"file_path": str(target)}},
        env={"AIDEV_WRITE_GUARD": "off"},
    )

    assert code == 0


# ---------------------------------------------------------------- the wiring


def settings_of(call_entry):
    argv_ = call_entry["argv"]
    if "--settings" not in argv_:
        return None
    return json.loads(Path(argv_[argv_.index("--settings") + 1]).read_text(encoding="utf-8"))


def test_the_editing_stages_are_handed_the_hook(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    plan, implement, test = invocations(log)
    assert settings_of(plan) is None  # readonly: Write is already blocked outright
    for entry in (implement, test):
        hook = settings_of(entry)["hooks"]["PreToolUse"][0]
        assert hook["matcher"] == "Write"
        assert "write_guard.py" in hook["hooks"][0]["command"]
        assert hook["hooks"][0]["type"] == "command"

    # it lives in the slice's own directory - the repo's .claude/ is the user's
    assert (slice_dir(repo) / "hooks" / "settings.json").exists()
    assert not (repo / ".claude").exists()


def test_no_write_guard_removes_it_entirely(repo, tmp_path, claude_bin, log):
    requirement(repo, front="approval: none")
    assert main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", "--no-write-guard")
    ) == 0

    assert all("--settings" not in entry["argv"] for entry in invocations(log))
    assert not (slice_dir(repo) / "hooks").exists()
