"""사망 판독의 산수. No session, no repository, no state.json - just the numbers.

This is the half of 자동 복구 that can be pinned down exactly, which is why it
lives in its own module: a threshold nobody can test without launching a slice
is a threshold nobody will ever change.
"""

import pytest

from aidev import recovery


def telemetry(
    edits=(),
    reads=(),
    rereads=(),
    commands=(),
    errors=0,
    turns=80,
    subtype="error_max_turns",
    calls=(),
):
    """A telemetry dict shaped exactly like the one a run writes, with only what is judged."""
    accesses = [{"path": path, "operation": "edit", "count": count} for path, count in edits]
    accesses += [{"path": path, "operation": "read", "count": count} for path, count in reads]
    return {
        "exact": {"num_turns": turns, "result_subtype": subtype},
        "observed": {
            "file_accesses": accesses,
            "error_results": errors,
            "tool_counts": {"Edit": len(edits), "Read": len(reads)},
            "tool_calls": len(accesses),
            "calls": list(calls),
        },
        "estimated": {
            "repeated_reads": [{"path": path, "count": count} for path, count in rereads],
            "repeated_commands": [
                {"command": command, "count": count} for command, count in commands
            ],
        },
    }


HEALTHY = (("a.py", 1), ("b.py", 1), ("c.py", 1))


# --------------------------------------------------------------- the classifier


@pytest.mark.parametrize(
    "subtype, text, expected",
    [
        ("error_max_turns", "", True),
        ("ERROR_MAX_TURNS", "", True),
        (None, "error_max_turns: reached the maximum number of turns", True),
        (None, "Claude reached the maximum number of turns", True),
        ("success", "", False),
        ("error_during_execution", "Claude AI usage limit reached|1755302400", False),
        (None, "TypeError: undefined is not a function", False),
    ],
)
def test_a_turn_death_is_read_from_either_the_subtype_or_the_log(subtype, text, expected):
    """Both sources are read: telemetry is derived from the log, so they check each other."""
    assert recovery.is_turn_death(subtype, text) is expected


def test_the_three_causes_and_which_one_wins():
    quota_text = "Claude AI usage limit reached|1755302400"
    # a limit beats a turn death: the wait is the cheaper answer and it is right
    assert recovery.classify("error_max_turns", quota_text, True) == recovery.CAUSE_QUOTA
    assert recovery.classify("error_max_turns", "", False) == recovery.CAUSE_TURNS
    assert recovery.classify("error_during_execution", "boom", False) == recovery.CAUSE_OTHER


# ------------------------------------------------------------------ the verdict


def test_a_session_that_moved_through_files_is_healthy():
    v = recovery.verdict(telemetry(edits=HEALTHY, turns=80))
    assert v.healthy
    assert v.reasons == []
    assert (v.units, v.turns) == (3, 80)


def test_a_readonly_stage_is_measured_in_files_read():
    """plan and decompose edit nothing; their unit of work is a file they opened."""
    v = recovery.verdict(telemetry(reads=(("a.py", 1), ("b.py", 1))))
    assert v.healthy and v.units == 2


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({}, "아무 파일도"),
        ({"edits": (("stuck.py", 6),)}, "같은 파일을 6번 고쳤다"),
        ({"edits": HEALTHY, "rereads": (("wide.py", 4),)}, "4번 다시 읽었다"),
        (
            {"edits": HEALTHY, "rereads": tuple(("f{0}.py".format(i), 3) for i in range(6))},
            "재독이 12회",
        ),
        ({"edits": HEALTHY, "commands": (("pytest -q", 3),)}, "같은 명령을 3번"),
        ({"edits": HEALTHY, "errors": 10}, "도구 에러가 10회"),
    ],
)
def test_every_병리_pattern_is_named_in_the_verdict(kwargs, fragment):
    v = recovery.verdict(telemetry(**kwargs))
    assert not v.healthy
    assert any(fragment in reason for reason in v.reasons), v.reasons


def test_the_thresholds_are_thresholds_not_hints():
    """One under each limit is still an honest session: 애매를 병리로 밀지 않는다."""
    assert recovery.verdict(telemetry(edits=(("stuck.py", 4),))).healthy
    assert recovery.verdict(telemetry(edits=HEALTHY, rereads=(("wide.py", 3),))).healthy
    assert recovery.verdict(telemetry(edits=HEALTHY, commands=(("pytest", 2),))).healthy
    assert recovery.verdict(telemetry(edits=HEALTHY, errors=9)).healthy
    # a busy file is not a circle when the session was moving through many files
    busy = (("stuck.py", 9), ("b.py", 1), ("c.py", 1))
    assert recovery.verdict(telemetry(edits=busy)).healthy


def test_an_empty_telemetry_is_pathological_rather_than_a_crash():
    v = recovery.verdict({})
    assert not v.healthy and v.units == 0 and v.turns == 0
    assert recovery.verdict(None).units == 0


# ------------------------------------------------------------------- the estimate


def test_the_estimate_is_remaining_times_the_measured_pace_plus_20_percent():
    v = recovery.verdict(telemetry(edits=tuple(("f{0}.py".format(i), 1) for i in range(8))))
    assert v.units == 8 and v.pace == 10.0  # 80 turns over 8 files
    assert recovery.extension_turns(4, v, 80, 300) == 48  # ceil(4 * 10 * 1.2)


def test_an_uncountable_remaining_gets_the_flat_blind_figure():
    """plan.md may not exist yet, or may name nothing left - 견적을 지어내지 않는다."""
    v = recovery.verdict(telemetry(edits=HEALTHY))
    assert recovery.extension_turns(0, v, 80, 300) == recovery.BLIND_EXTENSION


def test_a_tiny_estimate_is_raised_to_something_worth_a_session():
    v = recovery.verdict(telemetry(edits=HEALTHY, turns=3))
    assert recovery.extension_turns(1, v, 80, 300) == recovery.MIN_EXTENSION


def test_the_cap_clips_the_estimate_and_then_stops_it_entirely():
    v = recovery.verdict(telemetry(edits=(("a.py", 1),), turns=80))  # pace 80, a huge want
    assert recovery.extension_turns(15, v, 290, 300) == 10  # only what the cap leaves
    assert recovery.extension_turns(15, v, 300, 300) == 0
    assert recovery.extension_turns(15, v, 400, 300) == 0


# ------------------------------------------------------- what the observer sees


def test_the_activity_block_carries_every_number_the_judgement_used():
    data = telemetry(
        edits=(("stuck.py", 6),),
        rereads=(("wide.py", 4),),
        commands=(("python -m pytest -q", 3),),
        errors=3,
        calls=[
            {"turn": 1, "tool": "Edit", "target": "stuck.py", "result_is_error": False},
            {"turn": 2, "tool": "Bash", "target": "python -m pytest -q", "result_is_error": True},
        ],
    )
    text = recovery.render_activity(data)
    for fragment in ("stuck.py", "wide.py", "python -m pytest -q", "turns 80", "[error]"):
        assert fragment in text


def test_the_activity_block_survives_a_run_that_recorded_nothing():
    text = recovery.render_activity({})
    assert "(none)" in text and "turns 0" in text


# ------------------------------------------------------------- the suggestion


@pytest.mark.parametrize("word", recovery.SUGGESTIONS)
def test_each_suggestion_is_read_back(word):
    assert recovery.parse_suggestion("## 제안\nsuggestion: {0}\ndo that\n".format(word)) == word


def test_an_unreadable_suggestion_never_stops_a_slice():
    """The default is the one whose consequence is another attempt, not a stopped pipeline."""
    assert recovery.parse_suggestion("") == recovery.DEFAULT_SUGGESTION
    assert recovery.parse_suggestion("# OBSERVATION\nno idea\n") == recovery.DEFAULT_SUGGESTION
    assert recovery.parse_suggestion("suggestion: 아무거나") == recovery.DEFAULT_SUGGESTION
    # the template line the prompt itself carries must not read as an answer
    echoed = "suggestion: <one of: switch-approach, rescope, call-human>\nsuggestion: rescope"
    assert recovery.parse_suggestion(echoed) == "rescope"
