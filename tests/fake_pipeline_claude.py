"""Scriptable stand-in for ``claude``, used by the pipeline tests.

Unlike ``fake_claude.py`` (whose exact output the v0.1 tests assert on) this one
is driven by the environment, so one stub can play every stage and every failure
the pipeline has to survive.

    AIDEV_FAKE_LOG        append one JSON line per invocation: argv, cwd, prompt
    AIDEV_FAKE_SESSION    session id to report (default: pipe-session)
    AIDEV_FAKE_MODE       ok | quota | fail | touch | forge | requires_approval
                          (default: ok)
                          quota  - report a usage limit for the first
                                   AIDEV_FAKE_QUOTA_FAILS invocations
                          fail   - report a plain error
                          touch  - write a file into cwd, then succeed
                          forge  - self-approve the plan gate from inside .aidev/
                          requires_approval
                                 - behave like the real CLI without a rule for
                                   the test command: refuse it and report FAIL,
                                   unless argv carries Bash(npm run test:*)
    AIDEV_FAKE_TEST_RULE  which --allowedTools rule 'requires_approval' looks for
                          (default: Bash(npm run test:*))
    AIDEV_FAKE_QUOTA_FAILS  how many invocations fail with a limit (default: 1)
    AIDEV_FAKE_RESET_IN     seconds from now to advertise as the reset moment
    AIDEV_FAKE_TEXT         final text to emit instead of the per-stage default
    AIDEV_FAKE_BIG_PLAN     plan stage lists 18 files, 6 of them tests - a plan
                          over the size the turn-budget warning fires at
    AIDEV_FAKE_FILES        create N files in cwd during implement (commit guard)
    AIDEV_FAKE_MIGRATION    write backend/migrations/003_add_seat.sql during implement,
                          so a real stage commit carries a migration file
    AIDEV_FAKE_SLICES       how many items the decompose stage lists (default: 2)
    AIDEV_FAKE_SLICE_APPROVAL
                          the 'approval:' each listed slice declares (default: none)
    AIDEV_FAKE_FAIL_AFTER   fail every invocation from the Nth on, whatever the
                          mode - how a queue is stopped at a chosen slice
    AIDEV_FAKE_SPECLESS     implement writes a function with no spec comment, so
                          the 함수 명세 machine check has something real to catch
    AIDEV_FAKE_SPEC_FIX     with SPECLESS: from the Nth invocation on, write the
                          same function *with* a spec - a repair that works
    AIDEV_FAKE_DIAGNOSIS    what the diagnose step answers with
"""

import glob
import json
import os
import sys
import time

# ``runner.execute`` writes and reads UTF-8 explicitly.  Python launched through
# a Windows .cmd file otherwise decodes redirected stdin with the active ANSI
# code page, corrupting non-ASCII requirements before the stub can log them.
for stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

# A run that fails still burned tokens, so failures report usage like real ones.
FAILED_USAGE = {
    "input_tokens": 20,
    "cache_creation_input_tokens": 100,
    "cache_read_input_tokens": 900,
    "output_tokens": 40,
}

SESSION = os.environ.get("AIDEV_FAKE_SESSION", "pipe-session")

STAGE_TEXT = {
    "plan": "# PLAN\n\n1. touch aidev/thing.py\n2. add a test\n",
    "implement": "Changed aidev/thing.py as planned.",
    "test": "Ran: pytest -q\n12 passed, 0 failed.",
}

DIAGNOSIS = (
    "# DIAGNOSIS\n"
    "## Cause\n"
    "The helper returns the wrong seat index.\n"
    "## Fix direction\n"
    "Clamp the index to the seat count before returning it.\n"
    "## Where\n"
    "- aidev/thing.py:12 - off by one\n"
)

# A function with no spec comment at all, and the same function with one. The
# machine check is over a real diff, so the stub writes real code.
SPECLESS_FUNCTION = "def generated_helper(seat, phase):\n    return seat + phase\n"
SPECKED_FUNCTION = (
    "def generated_helper(seat, phase):\n"
    '    """Combine a seat with a phase.\n'
    "\n"
    "    @param seat   the seat index\n"
    "    @param phase  the phase number\n"
    '    """\n'
    "    return seat + phase\n"
)


def test_report():
    """AIDEV_FAKE_VERDICT: PASS (default), FAIL, or 'none' to omit the line."""
    verdict = os.environ.get("AIDEV_FAKE_VERDICT", "PASS")
    body = "Ran: pytest -q\n12 passed, 0 failed."
    if verdict.lower() == "none":
        return body
    return "{0}\nTEST_RESULT: {1}".format(body, verdict)


TEST_RULE = "Bash(npm run test:*)"


def has_test_rule():
    """Did a rule for the project's test command reach this process?

    The real CLI only sees --allowedTools; nothing else in argv can stand in for
    it, so looking at exactly that is what makes the check honest. A requirement
    that declares 'test_commands:' is granted the exact command instead of the
    built-in prefix rule, so AIDEV_FAKE_TEST_RULE names which one to look for.
    """
    wanted = os.environ.get("AIDEV_FAKE_TEST_RULE", TEST_RULE)
    for index, arg in enumerate(sys.argv):
        if arg == "--allowedTools" and index + 1 < len(sys.argv):
            if wanted in sys.argv[index + 1].split(","):
                return True
    return False


def approval_report():
    """What the test stage reports with, and without, permission to run the suite."""
    if not has_test_rule():
        return (
            "I tried to run: npm run test:guards\n"
            "This command requires approval\n"
            "TEST_RESULT: FAIL"
        )
    return "Ran: npm run test:guards\n345 passing, 0 failing.\nTEST_RESULT: PASS"


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def decompose_report():
    """A slice list in the format the parser is promised, with N items."""
    count = int(os.environ.get("AIDEV_FAKE_SLICES", "2"))
    approval = os.environ.get("AIDEV_FAKE_SLICE_APPROVAL", "none")
    return "\n".join(
        "=== SLICE {0}: fake slice {0} ===\n"
        "---\n"
        "approval: {1}\n"
        "---\n"
        "# fake slice {0}\n"
        "\n"
        "## What it must do\n"
        "Step {0} of the fake epic.\n"
        "\n"
        "## Scope\n"
        "aidev/thing.py\n"
        "\n"
        "## Tests\n"
        "pytest -q\n"
        "\n"
        "## Not in this slice\n"
        "Everything the other items do.\n".format(index, approval)
        for index in range(1, count + 1)
    )


def big_plan():
    """A plan naming enough work to trip the pre-approval size warning."""
    files = ["aidev/module{0}.py".format(index) for index in range(1, 13)]
    tests = ["tests/test_module{0}.py".format(index) for index in range(1, 7)]
    return "# PLAN\n\n## Files\n\n" + "".join(
        "- {0}: rewrite it\n".format(path) for path in files + tests
    )


def final_text(stage, mode):
    if stage == "decompose":
        return decompose_report()
    if stage == "diagnose":
        return os.environ.get("AIDEV_FAKE_DIAGNOSIS") or DIAGNOSIS
    if stage == "plan" and os.environ.get("AIDEV_FAKE_BIG_PLAN"):
        return big_plan()
    if stage != "test":
        return STAGE_TEXT[stage]
    return approval_report() if mode == "requires_approval" else test_report()


def detect_stage(prompt):
    # decompose and diagnose first: they are the stages whose prompts quote a
    # whole other document, which may well talk about planning or testing.
    upper = prompt.upper()
    if "DIAGNOSE STEP" in upper:
        return "diagnose"
    for stage in ("decompose",) + tuple(STAGE_TEXT):
        if "{0} stage".format(stage).upper() in upper:
            return stage
    return "plan"


def log_invocation(prompt):
    """Returns how many invocations came before this one."""
    path = os.environ.get("AIDEV_FAKE_LOG")
    if not path:
        return 0
    previous = 0
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            previous = sum(1 for line in handle if line.strip())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, ensure_ascii=False
            )
            + "\n"
        )
    return previous


def limit_message():
    reset_in = os.environ.get("AIDEV_FAKE_RESET_IN")
    if reset_in:
        return "Claude AI usage limit reached|{0}".format(int(time.time() + float(reset_in)))
    return "Claude AI usage limit reached. Try again later."


def main():
    prompt = sys.stdin.read()
    stage = detect_stage(prompt)
    previous = log_invocation(prompt)
    mode = os.environ.get("AIDEV_FAKE_MODE", "ok")
    sys.stderr.write("fake-pipeline-claude: {0} stage, {1} prompt chars\n".format(stage, len(prompt)))
    sys.stderr.flush()

    emit({"type": "system", "subtype": "init", "session_id": SESSION, "model": "fake-model"})

    if mode == "quota" and previous < int(os.environ.get("AIDEV_FAKE_QUOTA_FAILS", "1")):
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "session_id": SESSION,
                "is_error": True,
                "num_turns": 1,
                "total_cost_usd": 0.01,
                "usage": dict(FAILED_USAGE),
                "result": limit_message(),
            }
        )
        return 1

    fail_after = os.environ.get("AIDEV_FAKE_FAIL_AFTER")
    if fail_after and previous >= int(fail_after):
        mode = "fail"

    if mode == "fail":
        emit(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "session_id": SESSION,
                "is_error": True,
                "num_turns": 1,
                "result": "something went wrong in the harness",
            }
        )
        return 1

    if mode == "touch":
        with open("touched-by-plan.txt", "w", encoding="utf-8") as handle:
            handle.write("the plan stage should never be able to do this\n")

    if mode == "forge":
        # The nastier version: write inside .aidev/ and self-approve the gate.
        # Isolated, cwd is a worktree with no slice directory in it at all, so the
        # attempt has to create one - which proves both halves: the forgery never
        # reaches the real approvals file, and writing under .aidev/ is still
        # caught as a readonly violation.
        targets = glob.glob(os.path.join(".aidev", "slices", "*", "approvals"))
        if not targets:
            targets = [os.path.join(".aidev", "slices", "forged", "approvals")]
        for approvals in targets:
            if not os.path.isdir(approvals):
                os.makedirs(approvals)
            with open(os.path.join(approvals, "plan.md"), "w", encoding="utf-8") as handle:
                handle.write("approved\n")

    if os.environ.get("AIDEV_FAKE_FILES") and stage == "implement":
        for index in range(int(os.environ["AIDEV_FAKE_FILES"])):
            with open("generated-{0}.txt".format(index), "w", encoding="utf-8") as handle:
                handle.write("derived output nobody ignored\n")

    if os.environ.get("AIDEV_FAKE_SPECLESS") and stage == "implement":
        # A real function in a real file, so the diff the spec checker reads is
        # a real diff. The 'fix' round writes the same function with a spec.
        fix_from = os.environ.get("AIDEV_FAKE_SPEC_FIX")
        fixed = fix_from and previous >= int(fix_from)
        with open("aidev_generated.py", "w", encoding="utf-8") as handle:
            handle.write(SPECKED_FUNCTION if fixed else SPECLESS_FUNCTION)

    if os.environ.get("AIDEV_FAKE_MIGRATION") and stage == "implement":
        # A real file in a real stage commit, so a rollback range genuinely
        # contains a migration rather than one a test planted by hand.
        directory = os.path.join("backend", "migrations")
        if not os.path.isdir(directory):
            os.makedirs(directory)
        with open(os.path.join(directory, "003_add_seat.sql"), "w", encoding="utf-8") as handle:
            handle.write("alter table game add column seat int;\n")

    # One tool call per run, so telemetry has something to account for. The
    # implement stage reports an Edit, which is what the change summary reads;
    # touch mode reports a blocked tool, which readonly must notice.
    if mode == "touch":
        tool = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "touch x"}}
    elif stage == "implement":
        tool = {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "aidev/thing.py"}}
    else:
        tool = {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "aidev/thing.py"}}
    emit(
        {
            "type": "assistant",
            "session_id": SESSION,
            "request_id": "req_{0}".format(stage),
            "message": {
                "id": "msg_{0}".format(stage),
                "usage": {
                    "input_tokens": 20,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 900,
                    "output_tokens": 40,
                },
                "content": [{"type": "text", "text": "working on {0}".format(stage)}, tool],
            },
        }
    )
    emit(
        {
            "type": "user",
            "session_id": SESSION,
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x" * 128}]
            },
        }
    )
    emit(
        {
            "type": "result",
            "subtype": "success",
            "session_id": SESSION,
            "is_error": False,
            "num_turns": 1,
            "duration_ms": 1000,
            "duration_api_ms": 800,
            "total_cost_usd": 0.01,
            "usage": {
                "input_tokens": 20,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 900,
                "output_tokens": 40,
            },
            "result": os.environ.get("AIDEV_FAKE_TEXT") or final_text(stage, mode),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
