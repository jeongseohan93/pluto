"""함수 명세 규약, checked by machine over the slice's own diff.

The unit half calls ``specs.check_text`` with an explicit set of changed lines,
so the rule is tested without git. The git half builds a two-commit repository
and asks ``specs.check`` the same questions through the real plumbing, because
"which lines did this slice touch" is exactly the part a hand-written set cannot
prove.
"""

import subprocess

import pytest

from aidev import specs
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
    git,
    invocations,
    log,
    repo,
    requirement,
    slice_dir,
    state_of,
)

SPECKED = '''\
def protect(seat, phase):
    """Protect one seat for one phase.

    @param seat   the seat index
    @param phase  the phase number
    """
    return seat + phase
'''

SPECLESS = """\
def protect(seat, phase):
    return seat + phase
"""


def lines_of(text):
    """Every line number in a file - "this slice rewrote all of it"."""
    return set(range(1, len(text.splitlines()) + 1))


# ------------------------------------------------------------------ the rule


def test_a_new_function_without_a_spec_is_a_violation():
    found = specs.check_text("aidev/thing.py", SPECLESS, lines_of(SPECLESS))

    assert [v.kind for v in found] == [specs.KIND_MISSING]
    assert found[0].func == "protect"
    assert found[0].line == 1
    assert "@param" in found[0].detail


def test_a_spec_with_a_missing_param_line_is_a_violation():
    text = SPECKED.replace("    @param phase  the phase number\n", "")

    found = specs.check_text("aidev/thing.py", text, lines_of(text))

    assert [v.kind for v in found] == [specs.KIND_PARAM]
    assert "phase" in found[0].detail


def test_a_full_spec_passes():
    assert specs.check_text("aidev/thing.py", SPECKED, lines_of(SPECKED)) == []


def test_a_comment_block_above_the_def_counts_as_a_spec():
    text = (
        "# Protect one seat for one phase.\n"
        "# @param seat   the seat index\n"
        "# @param phase  the phase number\n"
        "def protect(seat, phase):\n"
        "    return seat + phase\n"
    )
    assert specs.check_text("aidev/thing.py", text, lines_of(text)) == []


def test_changing_a_function_without_changing_its_spec_is_a_violation():
    """The failure mode this is really aimed at: a spec that no longer describes the code."""
    changed = SPECKED.replace("return seat + phase", "return seat * phase")

    found = specs.check_text("aidev/thing.py", changed, lines_of(changed), old_text=SPECKED)

    assert [v.kind for v in found] == [specs.KIND_UNCHANGED]
    assert "one line is enough" in found[0].detail


def test_changing_a_function_and_its_spec_passes():
    changed = SPECKED.replace("Protect one seat", "Protect one seat, clamped,").replace(
        "return seat + phase", "return seat * phase"
    )
    assert specs.check_text("aidev/thing.py", changed, lines_of(changed), old_text=SPECKED) == []


def test_a_function_this_slice_never_touched_is_never_checked():
    """소급 금지: the convention accumulates, it is not applied retroactively."""
    text = SPECLESS + "\n" + SPECKED.replace("protect", "resolve")

    # only the second function's lines are in the diff
    touched = set(range(len(SPECLESS.splitlines()) + 2, len(text.splitlines()) + 1))
    assert specs.check_text("aidev/thing.py", text, touched) == []
    # and with the first function's lines in it, it is caught
    assert specs.check_text("aidev/thing.py", text, lines_of(text))


def test_nested_functions_and_dunders_are_left_out():
    text = (
        "class Seat:\n"
        "    def __init__(self, n):\n"
        "        self.n = n\n"
        "\n"
        "\n"
        "def outer(a):\n"
        '    """Do a thing.\n'
        "\n"
        "    @param a  the thing\n"
        '    """\n'
        "    def inner(b):\n"
        "        return b\n"
        "    return inner(a)\n"
    )
    assert specs.check_text("aidev/thing.py", text, lines_of(text)) == []


def test_a_file_that_does_not_parse_is_skipped():
    """A checker must never be the reason a stage fails."""
    assert specs.functions("def broken(:\n") == []
    assert specs.check_text("aidev/thing.py", "def broken(:\n", {1}) == []


@pytest.mark.parametrize(
    "path, exempt",
    [
        ("tests/test_night.py", True),
        ("aidev/thing_test.py", True),
        ("spec/thing.py", True),
        (".aidev/slices/x/gen.py", True),
        ("src/app.ts", True),  # only Python is checked
        ("aidev/pipeline.py", False),
    ],
)
def test_which_files_the_convention_reaches(path, exempt):
    assert specs.is_exempt(path) is exempt


def test_changed_lines_reads_a_zero_context_diff():
    diff = (
        "diff --git a/aidev/thing.py b/aidev/thing.py\n"
        "--- a/aidev/thing.py\n"
        "+++ b/aidev/thing.py\n"
        "@@ -3,0 +4,2 @@\n"
        "+one\n"
        "+two\n"
        "@@ -10 +12 @@\n"
        "-old\n"
        "+new\n"
    )
    assert specs.changed_lines(diff) == {"aidev/thing.py": {4, 5, 12}}


# -------------------------------------------------------------- through git


@pytest.fixture
def two_commits(tmp_path):
    """A repository with a base commit, ready for a second one to be measured."""
    path = tmp_path / "specrepo"
    (path / "aidev").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "aidev" / "old.py").write_text(SPECLESS.replace("protect", "untouched"), "utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "base")
    return path


def head_of(path):
    return git(path, "rev-parse", "HEAD").stdout.strip()


def test_check_reads_the_real_diff(two_commits):
    base = head_of(two_commits)
    (two_commits / "aidev" / "thing.py").write_text(SPECLESS, encoding="utf-8")
    git(two_commits, "add", "-A")
    git(two_commits, "commit", "-qm", "work")

    found = specs.check(two_commits, base, "HEAD")

    assert [(v.file, v.func, v.kind) for v in found] == [
        ("aidev/thing.py", "protect", specs.KIND_MISSING)
    ]
    # the untouched file from the base commit is not in the diff, so not checked
    assert all(v.file != "aidev/old.py" for v in found)


def test_a_test_file_written_by_the_slice_is_exempt(two_commits):
    base = head_of(two_commits)
    (two_commits / "tests").mkdir()
    (two_commits / "tests" / "test_thing.py").write_text(SPECLESS, encoding="utf-8")
    git(two_commits, "add", "-A")
    git(two_commits, "commit", "-qm", "tests")

    assert specs.check(two_commits, base, "HEAD") == []


def test_editing_one_line_of_a_documented_function_asks_for_its_spec(two_commits):
    (two_commits / "aidev" / "thing.py").write_text(SPECKED, encoding="utf-8")
    git(two_commits, "add", "-A")
    git(two_commits, "commit", "-qm", "first")
    base = head_of(two_commits)

    (two_commits / "aidev" / "thing.py").write_text(
        SPECKED.replace("return seat + phase", "return seat * phase"), encoding="utf-8"
    )
    git(two_commits, "add", "-A")
    git(two_commits, "commit", "-qm", "second")

    found = specs.check(two_commits, base, "HEAD")
    assert [v.kind for v in found] == [specs.KIND_UNCHANGED]


# ------------------------------------------------------- through the pipeline


def test_verify_fails_when_a_changed_function_has_no_spec(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Done Criteria: 명세 없는 함수 수정 시 verify FAIL."""
    command = '"{0}" -c "print(\'1 passed\')"'.format(__import__("sys").executable)
    monkeypatch.setenv("AIDEV_FAKE_SPECLESS", "1")
    requirement(repo, front="approval: none\ntest_commands: {0}".format(command))

    code = main(
        argv(
            repo, tmp_path, claude_bin,
            "--requirement", "tasks/doctor.md",
            "--max-repairs", "0",
        )
    )

    assert code == 1
    state = state_of(repo)
    assert state["test_verdict"] == "fail"
    text = (slice_dir(repo) / "failure.md").read_text(encoding="utf-8")
    assert "## Spec violations (1)" in text
    assert "generated_helper" in text
    # the commands themselves passed - the convention is what failed
    assert state["stages"]["test"]["verify"]["attempts"][0]["commands"][0]["exit_code"] == 0


def test_the_repair_round_can_fix_a_spec_violation(repo, tmp_path, claude_bin, log, monkeypatch):
    command = '"{0}" -c "print(\'1 passed\')"'.format(__import__("sys").executable)
    monkeypatch.setenv("AIDEV_FAKE_SPECLESS", "1")
    # the third invocation onwards (plan, implement, then the repair) writes a spec
    monkeypatch.setenv("AIDEV_FAKE_SPEC_FIX", "2")
    requirement(repo, front="approval: none\ntest_commands: {0}".format(command))

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    assert state_of(repo)["test_verdict"] == "pass"


def test_spec_check_off_in_the_front_matter_is_honoured(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    command = '"{0}" -c "print(\'1 passed\')"'.format(__import__("sys").executable)
    monkeypatch.setenv("AIDEV_FAKE_SPECLESS", "1")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0
    assert state_of(repo)["test_verdict"] == "pass"
    # and the stage is not told about a convention it is not held to
    assert "@param" not in invocations(log)[1]["prompt"]
