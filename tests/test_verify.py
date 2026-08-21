"""The verify engine: a pass costs no session, a failure produces documents.

Measured 2026-08-16/17: eight test stages in a row reported PASS, at $0.4 each,
for information nobody used. The fixtures come from test_pipeline unchanged, the
way test_epic already reuses them - the single --requirement path is what these
tests must not disturb.
"""

import json
import sys

import pytest

from aidev import verify
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
    git_repo,
    invocations,
    log,
    repo,
    requirement,
    slice_dir,
    state_of,
    subjects,
)


# ------------------------------------------------------------------- parsing


PYTEST_OUTPUT = """\
============================= test session starts =============================
collected 148 items

tests/test_night.py ..F.....                                            [ 20%]
tests/test_day.py ......F                                               [100%]

=================================== FAILURES ==================================
_______________________________ test_doctor ___________________________________
tests/test_night.py:42: in test_doctor
    assert protect(seat) == 3
E   AssertionError: expected 3, got 2
=========================== short test summary info ===========================
FAILED tests/test_night.py::test_doctor - AssertionError: expected 3, got 2
FAILED tests/test_day.py::test_vote - ValueError: no such seat
======================== 2 failed, 143 passed, 1 skipped ======================
"""


def test_parse_output_reads_pytest():
    failures, counts, parsed = verify.parse_output(PYTEST_OUTPUT)

    assert parsed is True
    assert [f.name for f in failures] == [
        "tests/test_night.py::test_doctor",
        "tests/test_day.py::test_vote",
    ]
    assert failures[0].file == "tests/test_night.py"
    assert failures[0].line == 42
    assert failures[0].message == "AssertionError: expected 3, got 2"
    assert (counts["passed"], counts["failed"], counts["skipped"]) == (143, 2, 1)


def test_output_nobody_can_read_says_so_rather_than_guessing():
    failures, counts, parsed = verify.parse_output("segmentation fault\nmake: *** [test] Error 1")

    assert parsed is False
    assert failures == []
    assert counts == {}


def test_a_count_nobody_read_is_shown_as_n_a_and_never_as_zero():
    """Measured 2026-08-19: 470 green tests reported as '0 passed'."""
    assert verify.count_text(None) == "n/a"
    assert verify.count_text(0) == "0"  # really none is still a number
    assert verify.count_text(470) == "470"
    assert verify.count_note(None) == "test count n/a"
    assert verify.count_note(470) == "470 passed"


def test_summarize_for_agent_is_a_diet():
    """The measured waste: 233 test names into the context after every edit."""
    noisy = PYTEST_OUTPUT + "\n".join(
        "tests/test_bulk.py::test_{0} PASSED".format(i) for i in range(200)
    )
    result = verify.VerifyResult(ok=False)
    failures, counts, parsed = verify.parse_output(noisy)
    result.failures = failures
    result.parsed = parsed
    result.passed, result.failed = counts["passed"], counts["failed"]
    result.commands = [verify.CommandResult(command="pytest -q", exit_code=1, output=noisy)]

    summary = verify.summarize_for_agent(result)

    assert "passed 143" in summary
    assert "test_bulk" not in summary  # the successes are a number, not a list
    assert "tests/test_night.py:42" in summary
    assert "expected 3, got 2" in summary
    assert len(summary) < len(noisy) / 5


def test_render_failure_md_carries_everything_the_retry_needs():
    result = verify.VerifyResult(ok=False, passed=143, failed=2, skipped=1, parsed=True)
    result.failures, _, _ = verify.parse_output(PYTEST_OUTPUT)
    result.commands = [
        verify.CommandResult(
            command="pytest -q", exit_code=1, duration_s=4.0, output=PYTEST_OUTPUT,
            log_path="/logs/01.log",
        )
    ]

    text = verify.render_failure_md(result, slice_id="20260818-doctor", attempt=2, last_commit="abc1234def")

    assert "# FAILURE - 20260818-doctor" in text
    assert "verify attempt 2" in text
    assert "pytest -q" in text and "exit 1" in text
    assert "## Failed tests (2)" in text
    assert "tests/test_night.py:42" in text
    assert "passed 143   failed 2   skipped 1" in text
    assert "full log: /logs/01.log" in text
    assert "last commit: abc1234" in text


# ------------------------------------------------------ temporary file names
#
# Three measured leftovers on three branches: .tsscratch, reindent_tmp.py and
# reindent.py. The convention said not to; the convention is not machinery.


@pytest.mark.parametrize(
    "path",
    [
        "reindent_tmp.py",
        "reindent.py",
        ".tsscratch",
        "desktop/src/x.tsscratch",
        "notes.bak",
        "panel.tsx.orig",
        "debug.py",
        "a/b/wip-notes.md",
        "main.py~",
        "aidev\\temp_notes.py",
    ],
)
def test_a_name_that_reads_as_scratch_work(path):
    assert verify.looks_temporary(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "aidev/verify.py",
        "tests/test_pipeline.py",
        "src/debug/panel.ts",  # a folder named debug is architecture, not leftovers
        "oldest.py",
        "useDebug.ts",
        "templates/index.html",
        "contemporary.md",
        "",
    ],
)
def test_a_name_that_must_not_be_accused(path):
    assert verify.looks_temporary(path) is False


def test_a_temp_file_makes_the_result_dirty():
    result = verify.VerifyResult(ok=True, temp_files=["x_tmp.py"])

    assert result.clean is False
    assert result.failure_count() == 1
    assert result.to_dict()["temp_files"] == ["x_tmp.py"]


def test_render_failure_md_lists_the_temp_files():
    result = verify.VerifyResult(ok=True, temp_files=["reindent_tmp.py", "desktop/src/.tsscratch"])

    text = verify.render_failure_md(result, slice_id="s")

    assert "## Temporary files (2)" in text
    assert "- reindent_tmp.py" in text
    assert "- desktop/src/.tsscratch" in text
    assert "임시 작업 잔재" in text


def test_result_from_report_reads_the_agents_own_words():
    """The fallback path has no command, only what the session wrote about one."""
    result = verify.result_from_report(
        "I ran pytest.\n"
        "FAILED tests/test_night.py::test_doctor - AssertionError: expected 3, got 2\n"
        "1 failed, 143 passed\n"
        "TEST_RESULT: FAIL"
    )

    assert result.ok is False
    assert result.parsed is True
    assert result.failures[0].name == "tests/test_night.py::test_doctor"
    assert result.failures[0].file == "tests/test_night.py"
    assert "expected 3, got 2" in result.failures[0].message
    assert (result.passed, result.failed) == (143, 1)
    assert result.commands[0].exit_code == 1


def test_unreadable_output_falls_back_to_the_tail():
    result = verify.VerifyResult(ok=False)
    result.commands = [
        verify.CommandResult(command="make test", exit_code=2, output="line\n" * 200 + "boom")
    ]

    text = verify.render_failure_md(result, slice_id="s")

    assert "## Output (tail" in text
    assert "boom" in text
    assert "## Failed tests" not in text


# ------------------------------------------------------------------- running


def script(tmp_path, name, body):
    """A python script and the command that runs it, quoted for Windows paths."""
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return '"{0}" "{1}"'.format(sys.executable, path)


def test_first_failing_command_stops_the_rest(tmp_path):
    fail = script(tmp_path, "a.py", "import sys\nprint('lint bad')\nsys.exit(1)\n")
    never = script(tmp_path, "b.py", "open('ran-b.txt','w').write('x')\n")

    result = verify.run_commands([fail, never], tmp_path)

    assert result.ok is False
    assert len(result.commands) == 1
    assert not (tmp_path / "ran-b.txt").exists()


def test_a_command_that_is_not_there_is_not_a_pass(tmp_path):
    result = verify.run_commands(["definitely-not-a-real-binary-xyz --run"], tmp_path)

    assert result.ok is False
    assert result.commands[0].exit_code is None
    assert "not found on PATH" in result.commands[0].output
    assert verify.worst_exit_code(result) != 0


def test_a_silent_pass_reports_no_count_rather_than_zero(tmp_path):
    """A runner that prints nothing has not told us there were no tests."""
    silent = script(tmp_path, "silent.py", "pass\n")

    result = verify.run_commands([silent], tmp_path)

    assert result.ok is True
    assert (result.passed, result.failed, result.skipped) == (None, None, None)

    summary = verify.summarize_for_agent(result)
    assert "passed n/a" in summary
    assert "passed 0" not in summary


def test_a_count_that_was_read_is_still_added_up(tmp_path):
    """The counts are only unknown when nothing said them; two commands still sum."""
    first = script(tmp_path, "one.py", "print('3 passed, 1 skipped')\n")
    second = script(tmp_path, "two.py", "print('4 passed')\n")

    result = verify.run_commands([first, second], tmp_path)

    assert (result.passed, result.skipped) == (7, 1)
    assert result.failed is None  # neither output mentioned a failure


def test_the_full_output_goes_to_a_log_and_not_into_the_summary(tmp_path):
    noisy = script(tmp_path, "c.py", "for i in range(500): print('line', i)\n")
    logs = tmp_path / "logs"

    result = verify.run_commands([noisy], tmp_path, log_dir=logs)

    assert result.ok is True
    written = list(logs.iterdir())
    assert len(written) == 1
    assert "line 499" in written[0].read_text(encoding="utf-8")
    assert "line 499" not in verify.summarize_for_agent(result)


# ------------------------------------------------------------- the whole loop


def verify_script(tmp_path, body, name="verify_action.py"):
    """A command a requirement can declare as its test_commands."""
    return script(tmp_path, name, body)


def counting_script(tmp_path, fail_times):
    """Fails for the first N runs and passes after that, counting in a file."""
    counter = tmp_path / "verify-count.txt"
    return verify_script(
        tmp_path,
        "import sys\n"
        "from pathlib import Path\n"
        "counter = Path(r'{0}')\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        "if n < {1}:\n"
        "    print('=========================== short test summary info ===========================')\n"
        "    for i in range({2}):\n"
        "        print('FAILED tests/test_night.py::test_{{0}} - AssertionError: expected 3, got 2'.format(i))\n"
        "    print('{2} failed, 143 passed')\n"
        "    sys.exit(1)\n"
        "print('148 passed')\n".format(counter, fail_times, "{failures}"),
    )


def failing_script(tmp_path, failures, fail_times=99):
    counter = tmp_path / "verify-count.txt"
    return verify_script(
        tmp_path,
        "import sys\n"
        "from pathlib import Path\n"
        "counter = Path(r'{counter}')\n"
        "n = int(counter.read_text()) if counter.exists() else 0\n"
        "counter.write_text(str(n + 1))\n"
        "if n < {fail_times}:\n"
        "    print('short test summary info')\n"
        "    for i in range({failures}):\n"
        "        print('FAILED tests/test_night.py::test_%d - AssertionError: expected 3, got 2' % i)\n"
        "    print('{failures} failed, 143 passed')\n"
        "    sys.exit(1)\n"
        "print('148 passed')\n".format(
            counter=counter, fail_times=fail_times, failures=failures
        ),
    )


def run(repo, tmp_path, claude_bin, command, *extra):
    requirement(
        repo,
        front="approval: none\nspec_check: off\ntest_commands: {0}".format(command),
    )
    return main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", *extra)
    )


def test_the_pass_path_spawns_no_session_at_all(repo, tmp_path, claude_bin, log, capsys):
    """Done Criteria: PASS 경로에 세션 0개.

    Eight measured test stages in a row reported PASS and cost $0.4 each for
    information nobody used. The engine runs the same command for nothing.
    """
    command = verify_script(tmp_path, "print('148 passed')\n")

    assert run(repo, tmp_path, claude_bin, command) == 0

    calls = invocations(log)
    assert [c["prompt"].splitlines()[0].split()[3] for c in calls] == ["PLAN", "IMPLEMENT"]
    assert len(calls) == 2  # plan and implement. There is no test session.

    state = state_of(repo)
    assert state["status"] == "done"
    assert state["stages"]["test"]["status"] == "done"
    assert state["test_verdict"] == "pass"
    assert state["stages"]["test"]["verify"]["engine"] is True
    assert len(state["stages"]["test"]["verify"]["attempts"]) == 1
    assert state["stages"]["test"]["verify"]["attempts"][0]["ok"] is True

    runs = json.loads((slice_dir(repo) / "runs.json").read_text(encoding="utf-8"))["runs"]
    assert [r["stage"] for r in runs] == ["plan", "implement"]
    # and the stage is still committed, so the branch reads exactly as before
    assert "slice({0}): test".format(state["slice_id"]) in subjects(
        repo, "{0}..{1}".format("HEAD", "slice/" + state["slice_id"])
    )


def test_a_small_failure_is_retried_on_failure_md_alone(repo, tmp_path, claude_bin, log):
    """Two failures: under the threshold, so no diagnosis session is bought."""
    command = failing_script(tmp_path, failures=2, fail_times=1)

    assert run(repo, tmp_path, claude_bin, command) == 0

    text = (slice_dir(repo) / "failure.md").read_text(encoding="utf-8")
    assert "## Failed tests (2)" in text
    assert "tests/test_night.py" in text
    assert "expected 3, got 2" in text

    calls = invocations(log)
    # plan, implement, and the repair implement. No diagnosis: the failure is small.
    assert len(calls) == 3
    assert "DIAGNOSE" not in calls[-1]["prompt"]
    assert "VERIFICATION FAILED" in calls[-1]["prompt"]
    assert "expected 3, got 2" in calls[-1]["prompt"]

    state = state_of(repo)
    assert state["status"] == "done"
    assert [r["grade"] for r in state["repairs"]] == ["small"]
    assert not (slice_dir(repo) / "diagnosis.md").exists()
    assert "slice({0}): repair1/implement".format(state["slice_id"]) in subjects(
        repo, "HEAD..slice/" + state["slice_id"]
    )


def test_a_large_failure_summons_a_diagnosis_session(repo, tmp_path, claude_bin, log):
    """Six failures: over the threshold, so one readonly session is worth it."""
    command = failing_script(tmp_path, failures=6, fail_times=1)

    assert run(repo, tmp_path, claude_bin, command) == 0

    diagnosis = (slice_dir(repo) / "diagnosis.md").read_text(encoding="utf-8")
    assert "# DIAGNOSIS" in diagnosis

    calls = invocations(log)
    assert len(calls) == 4  # plan, implement, diagnose, repair implement
    diagnose = calls[2]
    assert "DIAGNOSE step" in diagnose["prompt"]
    assert "# FAILURE" in diagnose["prompt"]  # the report reaches it verbatim
    blocked = diagnose["argv"][diagnose["argv"].index("--disallowedTools") + 1]
    assert "Bash" in blocked.split(",") and "Edit" in blocked.split(",")

    # and the fix session is given the diagnosis, not only the failure
    assert "DIAGNOSIS" in calls[3]["prompt"]
    assert "off by one" in calls[3]["prompt"]
    assert [r["grade"] for r in state_of(repo)["repairs"]] == ["large"]


def test_past_the_repair_limit_the_documents_are_the_deliverable(
    repo, tmp_path, claude_bin, log, capsys
):
    command = failing_script(tmp_path, failures=6)  # never passes

    assert run(repo, tmp_path, claude_bin, command) == 1

    state = state_of(repo)
    assert state["status"] == "failed"
    assert state["test_verdict"] == "fail"
    assert len(state["repairs"]) == 1  # --max-repairs defaults to 1
    assert (slice_dir(repo) / "failure.md").exists()
    assert (slice_dir(repo) / "diagnosis.md").exists()
    assert "verification failed after 1 repair attempt(s)" in capsys.readouterr().out


def test_max_repairs_zero_never_retries(repo, tmp_path, claude_bin, log):
    command = failing_script(tmp_path, failures=2)

    assert run(repo, tmp_path, claude_bin, command, "--max-repairs", "0") == 1

    assert len(invocations(log)) == 2  # plan and implement, and then it stops
    assert (slice_dir(repo) / "failure.md").exists()
    assert "repairs" not in state_of(repo)


def test_the_verify_logs_are_kept_per_attempt(repo, tmp_path, claude_bin, log):
    command = failing_script(tmp_path, failures=2, fail_times=1)
    assert run(repo, tmp_path, claude_bin, command) == 0

    logs = sorted(p.name for p in (slice_dir(repo) / "verify").iterdir())
    assert len(logs) == 2  # the failing round and the passing one
    assert logs[0].startswith("01-") and logs[1].startswith("02-")


def test_the_summary_shows_the_engine_run(repo, tmp_path, claude_bin, log, capsys):
    command = verify_script(tmp_path, "print('148 passed')\n")
    assert run(repo, tmp_path, claude_bin, command) == 0

    out = capsys.readouterr().out
    assert "Tests     PASS" in out
    assert "Verify" in out and "exit 0" in out
    # the engine's row costs nothing, and says so rather than being blank
    assert "$0.0000" in out


def test_the_summary_shows_the_real_test_count(repo, tmp_path, claude_bin, log, capsys):
    """Done Criteria: 계기판이 실제 개수를 적는다 (fake 러너로 검증)."""
    command = verify_script(tmp_path, "print('470 passed in 161.90s')\n")
    assert run(repo, tmp_path, claude_bin, command) == 0

    out = capsys.readouterr().out
    assert "(470 passed)" in out
    assert "(0 passed)" not in out  # the 2026-08-19 misreport
    assert state_of(repo)["stages"]["test"]["verify"]["attempts"][0]["passed"] == 470


def test_a_pass_with_no_countable_output_says_n_a(repo, tmp_path, claude_bin, log, capsys):
    """The other half: no number to read is 'n/a', never an invented zero."""
    command = verify_script(tmp_path, "pass\n")
    assert run(repo, tmp_path, claude_bin, command) == 0

    out = capsys.readouterr().out
    assert "(test count n/a)" in out
    assert "(0 passed)" not in out
    assert state_of(repo)["stages"]["test"]["verify"]["attempts"][0]["passed"] is None


def test_no_test_commands_means_the_agent_stage_as_before(repo, tmp_path, claude_bin, log):
    """The fallback: nothing declared, so there is nothing for the engine to run."""
    requirement(repo, front="approval: none")
    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 0

    assert len(invocations(log)) == 3  # plan, implement, test - v0.4 exactly
    assert "verify" not in state_of(repo)["stages"]["test"]


# ------------------------------------------------- the temp-file guard, whole
#
# The unit tests above judge a name. These run a slice that really leaves one
# behind, so what is being checked is git's idea of what the slice added and not
# a list a test handed the checker.


def guarded(tmp_path, repo, claude_bin, *extra):
    """Launch a slice whose commands pass, so only the guard can fail it.

    @param tmp_path    the pytest temp dir the script and data dir live under
    @param repo        the user's repository
    @param claude_bin  the stub launcher
    @param extra       further CLI arguments
    """
    command = verify_script(tmp_path, "print('148 passed')\n")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )
    return main(
        argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md", *extra)
    )


def test_a_slice_that_adds_a_temp_file_fails_verification(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Done Criteria: 임시파일 포함 slice가 FAIL + 목록 표시."""
    monkeypatch.setenv("AIDEV_FAKE_TEMPFILE", "reindent_tmp.py")

    assert guarded(tmp_path, repo, claude_bin, "--max-repairs", "0") == 1

    state = state_of(repo)
    assert state["test_verdict"] == "fail"
    text = (slice_dir(repo) / "failure.md").read_text(encoding="utf-8")
    assert "## Temporary files (1)" in text
    assert "- reindent_tmp.py" in text
    attempt = state["stages"]["test"]["verify"]["attempts"][0]
    # the declared commands passed; leaving the file behind is what failed
    assert attempt["commands"][0]["exit_code"] == 0
    assert attempt["temp_files"] == ["reindent_tmp.py"]


def test_the_repair_round_can_delete_a_temp_file(repo, tmp_path, claude_bin, log, monkeypatch):
    monkeypatch.setenv("AIDEV_FAKE_TEMPFILE", "reindent_tmp.py")
    # plan, implement, then the repair - which cleans up instead of scattering
    monkeypatch.setenv("AIDEV_FAKE_TEMPFILE_FIX", "2")

    assert guarded(tmp_path, repo, claude_bin) == 0
    assert state_of(repo)["test_verdict"] == "pass"


def test_no_temp_guard_lets_it_through(repo, tmp_path, claude_bin, log, monkeypatch):
    """The way out of a false positive, because a false positive kills a slice."""
    monkeypatch.setenv("AIDEV_FAKE_TEMPFILE", "reindent_tmp.py")

    assert guarded(tmp_path, repo, claude_bin, "--no-temp-guard") == 0
    assert state_of(repo)["test_verdict"] == "pass"
    assert state_of(repo)["stages"]["test"]["verify"]["attempts"][0]["temp_files"] == []


def test_a_clean_slice_is_not_accused(repo, tmp_path, claude_bin, log):
    """The regression pin: .aidev/ holds this slice's own record and a *.tmp
    brushes past it while write_text_atomic works. Without that exclusion every
    slice in the suite would accuse itself, so this passing is the whole point.
    """
    assert guarded(tmp_path, repo, claude_bin) == 0
    assert state_of(repo)["stages"]["test"]["verify"]["attempts"][0]["temp_files"] == []


# --------------------------------------------- 엔진 verify 2단 검사 (v0.8)
#
# The engine runs these commands itself, so there is nobody to declare a reason
# for what they did. Both looks are therefore absolute, and they are two
# independent looks rather than a before/after comparison.


def writing_script(tmp_path, target, name="verify_writes.py"):
    """A 'test command' that passes and writes a file, counting how often it ran."""
    counter = tmp_path / "verify-runs.txt"
    return verify_script(
        tmp_path,
        "from pathlib import Path\n"
        "counter = Path(r'{0}')\n"
        "counter.write_text(str(int(counter.read_text()) + 1 if counter.exists() else 1))\n"
        "Path(r'{1}').write_text('written by the verification itself\\n')\n"
        "print('148 passed')\n".format(counter, target),
        name=name,
    )


def run_count(tmp_path):
    path = tmp_path / "verify-runs.txt"
    return int(path.read_text(encoding="utf-8")) if path.exists() else 0


def test_a_verify_command_that_writes_an_untracked_file_fails(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Done Criteria: test_commands가 untracked 파일을 만들면 FAIL."""
    worktree = tmp_path / "wt" / "placeholder"
    command = writing_script(tmp_path, "verify-scratch.txt")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1
    state = state_of(repo)
    assert state["status"] == "failed"
    assert "verify가 저장소를 바꿨다" in state["reason"]
    assert "verify-scratch.txt" in state["reason"]
    # the commands did run: it is what they left behind that failed
    assert run_count(tmp_path) == 1
    assert not worktree.exists() or True


def test_a_verify_command_that_rewrites_a_tracked_file_fails(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """A tracked file is no better: the engine may not change the thing it checks."""
    command = writing_script(tmp_path, "README.md")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1
    state = state_of(repo)
    assert "verify가 저장소를 바꿨다" in state["reason"]
    assert "README.md" in state["reason"]


def test_verify_touching_a_declared_file_still_fails(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Declared or not makes no difference: 엔진 실행에는 사유 주체가 없다."""
    command = writing_script(tmp_path, "README.md")
    # the plan declares README.md as a MODIFY target, and it changes nothing here
    monkeypatch.setenv("AIDEV_FAKE_ORDER_EXTRA", "MODIFY|README.md")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1
    assert "verify가 저장소를 바꿨다" in state_of(repo)["reason"]


def test_a_dirty_worktree_fails_verify_before_it_runs(
    repo, tmp_path, claude_bin, log, monkeypatch
):
    """Done Criteria: verify가 파일을 고쳐 실패 -> resume -> 명령 재실행 없이 dirty로 재차 FAIL."""
    command = writing_script(tmp_path, "verify-scratch.txt")
    requirement(
        repo, front="approval: none\nspec_check: off\ntest_commands: {0}".format(command)
    )

    assert main(argv(repo, tmp_path, claude_bin, "--requirement", "tasks/doctor.md")) == 1
    slice_id = state_of(repo)["slice_id"]
    assert run_count(tmp_path) == 1

    assert main(argv(repo, tmp_path, claude_bin, "--resume-slice", slice_id)) == 1
    state = state_of(repo, slice_id)
    assert "verify 시작 전 작업트리가 dirty하다" in state["reason"]
    # the whole point of the first look: the commands were not run a second time
    assert run_count(tmp_path) == 1
