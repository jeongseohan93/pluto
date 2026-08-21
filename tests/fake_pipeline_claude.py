"""Scriptable stand-in for ``claude``, used by the pipeline tests.

Unlike ``fake_claude.py`` (whose exact output the v0.1 tests assert on) this one
is driven by the environment, so one stub can play every stage and every failure
the pipeline has to survive.

    AIDEV_FAKE_LOG        append one JSON line per invocation: argv, cwd, prompt
    AIDEV_FAKE_SESSION    session id to report (default: pipe-session)
    AIDEV_FAKE_MODE       ok | quota | turns | fail | touch | forge |
                          requires_approval  (default: ok)
                          quota  - report a usage limit for the first
                                   AIDEV_FAKE_QUOTA_FAILS invocations
                          turns  - run out of turns (error_max_turns) on the
                                   first AIDEV_FAKE_TURN_FAILS invocations of
                                   AIDEV_FAKE_TURN_STAGE, leaving real tool
                                   traffic behind for the engine to judge
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
    AIDEV_FAKE_TEMPFILE     implement leaves these files (comma separated) in cwd,
                          default reindent_tmp.py - one of the three measured
                          leftovers, so the temp-file guard has real scratch work
    AIDEV_FAKE_TEMPFILE_FIX with TEMPFILE: from the Nth invocation on, delete them
                          again instead of writing them - a repair that works
    AIDEV_FAKE_TEST_REPORT  what the test stage's report says above its verdict
                          line - AIDEV_FAKE_TEXT would replace every stage's
                          text, and the fallback report only concerns this one
    AIDEV_FAKE_DIAGNOSIS    what the diagnose step answers with
    AIDEV_FAKE_TURN_FAILS   how many invocations of the turn stage die at the
                          turn limit (default: 1)
    AIDEV_FAKE_TURN_STAGE   which stage 'turns' mode kills (default: implement)
    AIDEV_FAKE_SICK         that death leaves a pathological pattern - one file
                          edited six times and one test command run three
                          times, every result an error - instead of the healthy
                          one (three different files edited, nothing repeated)
    AIDEV_FAKE_OBSERVATION  what the observe step answers with

    v0.8 작업 지시서. The plan stage appends a '## 작업 지시서' table naming
    exactly the files this invocation will really create, derived from the very
    switches above - one place decides both, so the table and the work can never
    drift apart.

    AIDEV_FAKE_NO_WORK_ORDER  the plan emits no 작업 지시서 section at all
    AIDEV_FAKE_ORDER        the table's data rows, verbatim, newline separated -
                          for the malformed / escaping / ambiguous scenarios
    AIDEV_FAKE_ORDER_EXTRA  extra rows appended to the generated table,
                          'VERB|path|symbol' entries separated by ';'
    AIDEV_FAKE_REASONS      implement and test append a '## 범위 밖 수정 사유'
                          section: 'path|reason' entries separated by ';',
                          'empty' for a section whose table has no rows
    AIDEV_FAKE_EDIT         existing files (comma separated) implement appends to,
                          declared as MODIFY in the table
    AIDEV_FAKE_UNEDIT       files implement takes that same line back out of - a
                          revert, so the path leaves the final diff entirely
    AIDEV_FAKE_UNDECLARED_EDIT
                          keep the EDIT/UNEDIT rows out of the table, so the edit
                          is an unplanned one the reason table has to declare
    AIDEV_FAKE_DELETE       existing files (comma separated) implement deletes
    AIDEV_FAKE_WRITE        files implement creates and the table never mentions
    AIDEV_FAKE_TEST_WRITE   a file the agent test stage writes

    Any path in AIDEV_FAKE_WRITE / _EDIT / _UNEDIT / _DELETE / _TEST_WRITE /
    _TEMPFILE / _ORDER_EXTRA may contain '{slice}', which becomes the worktree's
    own directory name - the slice id a test cannot know in advance.
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

OBSERVATION = (
    "# OBSERVATION\n"
    "## 반복 중인 행동\n"
    "aidev/stuck.py 한 파일만 여섯 번 고치고 같은 테스트를 세 번 돌렸다.\n"
    "## 막힌 지점 - 추정 원인\n"
    "같은 실패를 같은 방법으로 다시 고치고 있다.\n"
    "## 제안\n"
    "suggestion: switch-approach\n"
    "그 파일을 더 고치지 말고, 실패하는 테스트가 무엇을 요구하는지부터 읽어라.\n"
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
    """AIDEV_FAKE_VERDICT: PASS (default), FAIL, or 'none' to omit the line.

    AIDEV_FAKE_TEST_REPORT replaces the body above the verdict line, which is
    what the fallback path writes its failure.md out of: the session's own
    sentences are the only evidence that path ever has.
    """
    verdict = os.environ.get("AIDEV_FAKE_VERDICT", "PASS")
    body = os.environ.get("AIDEV_FAKE_TEST_REPORT") or "Ran: pytest -q\n12 passed, 0 failed."
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


# The one line AIDEV_FAKE_EDIT appends and AIDEV_FAKE_UNEDIT takes back out, so a
# revert really is byte-for-byte the file git already has.
EDIT_MARKER = "# touched by the fake implement\n"


def write_file(path, text):
    """Write a file, making the directories it needs - real work in the real tree."""
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def here(path):
    """A path a test named, with '{slice}' filled in from the worktree's own name.

    A test cannot know the slice id before the slice exists, and the worktree is
    named after it - so '.aidev/history/{slice}/evil.py' is expressible.
    """
    return str(path).replace("{slice}", os.path.basename(os.getcwd()))


def csv_env(name):
    """A comma separated environment list, emptied of blanks and filled in."""
    return [here(part.strip()) for part in (os.environ.get(name) or "").split(",") if part.strip()]


def verb_for(path):
    """CREATE unless the file is already there - which is what a real plan would say.

    It matters in an epic chain: slice 2 branches off slice 1's tip, so the file
    slice 1 created is already present and declaring CREATE for it would be a
    conflict the engine is right to refuse.
    """
    return "MODIFY" if os.path.exists(path) else "CREATE"


def work_order_rows():
    """Exactly the files this invocation really touches, as 작업 지시서 rows.

    Derived from the same switches that do the touching, a few lines below, so
    the declared scope and the actual work come from one decision rather than
    two that have to be kept in step by hand. AIDEV_FAKE_WRITE is the deliberate
    exception: those files are created and never declared.
    """
    rows = []
    if os.environ.get("AIDEV_FAKE_BIG_PLAN"):
        # Exactly the 18 paths the prose names, so the plan-scale count is
        # measured over one list rather than the union of two.
        for index in range(1, 13):
            rows.append(("CREATE", "aidev/module{0}.py".format(index), "", "rewrite it"))
        for index in range(1, 7):
            rows.append(("CREATE", "tests/test_module{0}.py".format(index), "", "rewrite it"))
    else:
        rows.append((verb_for("aidev/thing.py"), "aidev/thing.py", "", "the file the fake plan names"))
    for index in range(int(os.environ.get("AIDEV_FAKE_FILES") or 0)):
        name = "generated-{0}.txt".format(index)
        rows.append((verb_for(name), name, "", "derived output"))
    if os.environ.get("AIDEV_FAKE_SPECLESS"):
        rows.append((verb_for("aidev_generated.py"), "aidev_generated.py", "", "the helper"))
    for name in csv_env("AIDEV_FAKE_TEMPFILE"):
        rows.append((verb_for(name), name, "", "scratch work nobody meant to keep"))
    if os.environ.get("AIDEV_FAKE_MIGRATION"):
        name = "backend/migrations/003_add_seat.sql"
        rows.append((verb_for(name), name, "", "a seat column"))
    for name in csv_env("AIDEV_FAKE_EDIT") + csv_env("AIDEV_FAKE_UNEDIT"):
        if not os.environ.get("AIDEV_FAKE_UNDECLARED_EDIT"):
            rows.append(("MODIFY", name, "", "an edit the plan owns"))
    for name in csv_env("AIDEV_FAKE_TEST_WRITE"):
        rows.append((verb_for(name), name, "", "written by the test stage"))
    for part in (os.environ.get("AIDEV_FAKE_ORDER_EXTRA") or "").split(";"):
        fields = [here(field.strip()) for field in part.split("|")]
        if len(fields) >= 2 and fields[0] and fields[1]:
            rows.append((fields[0], fields[1], fields[2] if len(fields) > 2 else "", "an extra row"))
    return rows


def work_order_table():
    """The '## 작업 지시서' section the plan stage appends, or '' when told not to."""
    if os.environ.get("AIDEV_FAKE_NO_WORK_ORDER"):
        return ""
    body = os.environ.get("AIDEV_FAKE_ORDER")
    if body is None:
        body = "\n".join("| {0} | {1} | {2} | {3} |".format(*row) for row in work_order_rows())
    return (
        "\n## 작업 지시서\n\n"
        "| 동사 | 대상 경로 | symbol | 책임 |\n"
        "| --- | --- | --- | --- |\n" + body + "\n"
    )


def reasons_table():
    """The '## 범위 밖 수정 사유' section implement and test append, or ''."""
    spec = os.environ.get("AIDEV_FAKE_REASONS")
    if spec is None:
        return ""
    header = "\n\n## 범위 밖 수정 사유\n\n| 경로 | 사유 |\n| --- | --- |\n"
    if spec.strip().lower() == "empty":
        return header
    rows = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        path, _, reason = part.partition("|")
        rows.append("| {0} | {1} |".format(path.strip(), reason.strip() or "부득이한 수정"))
    return header + "\n".join(rows) + "\n"


def result_text(stage, mode):
    """What this invocation reports, with the v0.8 sections attached to it.

    AIDEV_FAKE_TEXT replaces the body and not the sections: a test that pins the
    plan's prose still gets a plan carrying a work order, which is what the
    engine now requires of every plan.
    """
    text = os.environ.get("AIDEV_FAKE_TEXT") or final_text(stage, mode)
    if stage == "plan":
        return text + work_order_table()
    if stage in ("implement", "test"):
        return text + reasons_table()
    return text


def final_text(stage, mode):
    if stage == "decompose":
        return decompose_report()
    if stage == "diagnose":
        return os.environ.get("AIDEV_FAKE_DIAGNOSIS") or DIAGNOSIS
    if stage == "observe":
        return os.environ.get("AIDEV_FAKE_OBSERVATION") or OBSERVATION
    if stage == "plan" and os.environ.get("AIDEV_FAKE_BIG_PLAN"):
        return big_plan()
    if stage != "test":
        return STAGE_TEXT[stage]
    return approval_report() if mode == "requires_approval" else test_report()


def detect_stage(prompt):
    # observe, decompose and diagnose first: they are the stages whose prompts
    # quote whole other documents, which may well talk about planning or testing.
    upper = prompt.upper()
    if "OBSERVE STEP" in upper:
        return "observe"
    if "DIAGNOSE STEP" in upper:
        return "diagnose"
    for stage in ("decompose",) + tuple(STAGE_TEXT):
        if "{0} stage".format(stage).upper() in upper:
            return stage
    return "plan"


def log_invocation(prompt, stage):
    """How many invocations came before this one: in total, and of this same stage.

    The per-stage count is what lets 'turns' mode kill one stage's first N
    attempts without counting the other stages that ran in between.
    """
    path = os.environ.get("AIDEV_FAKE_LOG")
    if not path:
        return 0, 0
    previous = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            previous = [json.loads(line) for line in handle if line.strip()]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, ensure_ascii=False
            )
            + "\n"
        )
    same = sum(1 for entry in previous if detect_stage(entry.get("prompt") or "") == stage)
    return len(previous), same


def argv_value(flag, fallback=None):
    """What the CLI was actually given for one flag - the stub reports its own budget."""
    for index, arg in enumerate(sys.argv):
        if arg == flag and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return fallback


def turn_death_events():
    """The tool traffic a turn-limited session leaves behind, healthy or pathological.

    Healthy is three different files edited and nothing read twice - a session
    that was working through a list when the budget ran out. Pathological is one
    file edited six times and one command run three times with every result an
    error, which is the shape the engine's thresholds are written against.
    """
    calls = []
    if os.environ.get("AIDEV_FAKE_SICK"):
        for index in range(6):
            calls.append(
                ({"file_path": "aidev/stuck.py"}, "Edit", False),
            )
        for index in range(3):
            calls.append(({"command": "python -m pytest -q"}, "Bash", True))
    else:
        for name in ("aidev_first.py", "aidev_second.py", "aidev_third.py"):
            calls.append(({"file_path": name}, "Edit", False))
    return calls


def limit_message():
    reset_in = os.environ.get("AIDEV_FAKE_RESET_IN")
    if reset_in:
        return "Claude AI usage limit reached|{0}".format(int(time.time() + float(reset_in)))
    return "Claude AI usage limit reached. Try again later."


def emit_turn_death(stage):
    """Spend the whole budget on real tool traffic, then die at the turn limit.

    ``num_turns`` is whatever ``--max-turns`` this invocation was given, so the
    engine's estimate is made from the budget it actually handed out - and a
    test can prove an extension reached the CLI by reading the same number back.
    """
    turns = int(argv_value("--max-turns", "80"))
    for index, (tool_input, tool, is_error) in enumerate(turn_death_events()):
        call_id = "t{0}".format(index)
        emit(
            {
                "type": "assistant",
                "session_id": SESSION,
                "request_id": "req_{0}_{1}".format(stage, index),
                "message": {
                    "id": "msg_{0}_{1}".format(stage, index),
                    "usage": dict(FAILED_USAGE),
                    "content": [
                        {"type": "text", "text": "still on {0}".format(stage)},
                        {"type": "tool_use", "id": call_id, "name": tool, "input": tool_input},
                    ],
                },
            }
        )
        emit(
            {
                "type": "user",
                "session_id": SESSION,
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "content": "x" * 64,
                            "is_error": is_error,
                        }
                    ]
                },
            }
        )
    emit(
        {
            "type": "result",
            "subtype": "error_max_turns",
            "session_id": SESSION,
            "is_error": True,
            "num_turns": turns,
            "duration_ms": 1000,
            "total_cost_usd": 0.02,
            "usage": dict(FAILED_USAGE),
            "result": "error_max_turns: reached the maximum number of turns",
        }
    )


def main():
    prompt = sys.stdin.read()
    stage = detect_stage(prompt)
    previous, previous_stage = log_invocation(prompt, stage)
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

    if (
        mode == "turns"
        and stage == os.environ.get("AIDEV_FAKE_TURN_STAGE", "implement")
        and previous_stage < int(os.environ.get("AIDEV_FAKE_TURN_FAILS", "1"))
    ):
        emit_turn_death(stage)
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

    if os.environ.get("AIDEV_FAKE_TEMPFILE") and stage == "implement":
        # Real files with the real names, because the guard reads git's idea of
        # what this slice added and not a list a test handed it.
        fix_from = os.environ.get("AIDEV_FAKE_TEMPFILE_FIX")
        cleaning = fix_from and previous >= int(fix_from)
        for name in os.environ["AIDEV_FAKE_TEMPFILE"].split(","):
            name = name.strip()
            if not name:
                continue
            if cleaning:
                if os.path.exists(name):
                    os.remove(name)
                continue
            with open(name, "w", encoding="utf-8") as handle:
                handle.write("scratch work nobody meant to keep\n")

    if stage == "implement":
        # Existing files, changed and removed for real: the scope check reads
        # git's idea of the final diff, not a list a test handed it.
        for name in csv_env("AIDEV_FAKE_EDIT"):
            with open(name, "a", encoding="utf-8") as handle:
                handle.write(EDIT_MARKER)
        for name in csv_env("AIDEV_FAKE_UNEDIT"):
            if os.path.exists(name):
                with open(name, "r", encoding="utf-8") as handle:
                    kept = [line for line in handle.readlines() if line != EDIT_MARKER]
                with open(name, "w", encoding="utf-8", newline="") as handle:
                    handle.writelines(kept)
        for name in csv_env("AIDEV_FAKE_DELETE"):
            if os.path.exists(name):
                os.remove(name)
        for name in csv_env("AIDEV_FAKE_WRITE"):
            write_file(name, "a file the 지시서 never mentioned\n")

    if stage == "test":
        for name in csv_env("AIDEV_FAKE_TEST_WRITE"):
            write_file(name, "written by the agent test stage\n")

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
            "result": result_text(stage, mode),
        }
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
