"""The verify engine: a pass costs no session, a failure produces documents.

Measured 2026-08-16/17: eight test stages in a row reported PASS, at $0.4 each,
for information nobody used. The fixtures come from test_pipeline unchanged, the
way test_epic already reuses them - the single --requirement path is what these
tests must not disturb.
"""

import json
import sys

from aidev import verify
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
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
