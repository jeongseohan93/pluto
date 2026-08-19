"""왜 그 단계가 죽었는지, 정직하게 죽었는지, 얼마를 더 주면 끝나는지.

Measured 2026-08-18/19: two usage-limit deaths inside 24 hours, and every
turn-limit death diagnosed by hand - read the log, decide whether to raise the
budget, launch it again. 멈춤 자체가 비용이다. This module is the reading and
the arithmetic of that decision, and nothing else.

Deliberately pure, exactly like ``progress.py``: it opens no file, spends no
session and never touches ``state.json``. Everything it needs is already in the
telemetry dict the run left behind, so every threshold below can be tested
without a repository, a worktree or a session. ``pipeline`` imports this; this
imports nothing of ``pipeline``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# What killed a stage. Anything that is not one of the first two is the failure
# path v0.6 already had, unchanged.
CAUSE_QUOTA = "usage_limit"
CAUSE_TURNS = "turns"
CAUSE_OTHER = "other"

# The result subtype ``claude -p`` ends with when it hits --max-turns, plus the
# looser spellings of the same death that reach us through stderr instead.
TURN_MARKERS: Tuple[str, ...] = ("error_max_turns", "max_turns", "maximum number of turns")

# 병리 임계값. Every one of them is counted inside a single session: the
# question is not "is this file edited a lot" but "did this session spend its
# whole budget in one place".
EDIT_REPEAT_MAX = 5  # the same file edited this many times, and
EDIT_REPEAT_UNITS = 2  # this few files touched in total = circling one spot
READ_REPEAT_MAX = 4  # one file read this many times over
READ_REPEAT_TOTAL = 12  # or this many re-reads in total (first reads excluded)
COMMAND_REPEAT_MAX = 3  # the same command run this often = the same failure, again
ERROR_RESULT_MAX = 10  # tool calls that came back as errors

# 견적. The requirement's formula: Remaining × 실측 소화 속도(턴/파일) + 여유 20%.
SAFETY_MARGIN = 0.2
MIN_EXTENSION = 20  # below this an extension does not buy a whole session
BLIND_EXTENSION = 40  # when Remaining cannot be counted at all (no plan yet)
DEFAULT_TURN_CAP = 300  # a stage's budget may never be raised past this

# 안전핀: how many automatic actions each mode allows per slice. conservative is
# the default because an extension costs one session and an Observer costs two.
EXTENSION_LIMITS: Dict[str, int] = {"off": 0, "conservative": 1, "aggressive": 2}
OBSERVER_LIMITS: Dict[str, int] = {"off": 0, "conservative": 0, "aggressive": 1}

# The three things an outside eye is allowed to conclude. 'call-human' is the
# one that stops the pipeline instead of buying another retry.
SUGGESTIONS: Tuple[str, ...] = ("switch-approach", "rescope", "call-human")
DEFAULT_SUGGESTION = "switch-approach"
CALL_HUMAN = "call-human"

_SUGGESTION_RE = re.compile(r"^\s*suggestion\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)

# How much of the session's own history the Observer is shown. The events file
# itself is never quoted - it reaches hundreds of megabytes - so this is read
# off the telemetry summary the run already wrote.
ACTIVITY_ROWS = 20


def is_turn_death(subtype: Optional[str], text: str) -> bool:
    """Did this stage die at its turn limit, as either the CLI or the log tells it?

    Both sources are read on purpose. ``subtype`` is what telemetry recorded
    from the result event; ``text`` is what ``failure_text`` scraped off the end
    of events.jsonl and stderr. Telemetry is derived from that same stream, so
    agreeing is cheap and disagreeing is what keeps a misparse from being fatal.

    @param subtype  ``exact.result_subtype`` from the run's telemetry, if any
    @param text     the tail of the run - the terminal result event and stderr
    @flow  subtype is the exact marker -> yes ; else look for any marker in the text
    """
    if str(subtype or "").strip().lower() in ("error_max_turns", "max_turns"):
        return True
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in TURN_MARKERS)


def classify(subtype: Optional[str], text: str, quota: bool) -> str:
    """The cause of death, in the three kinds the engine can act on differently.

    ``quota`` is passed in rather than re-derived: ``pipeline.looks_like_quota``
    owns that list of markers, and holding a second copy here is how the two
    would drift.

    @param subtype  ``exact.result_subtype`` from the run's telemetry, if any
    @param text     the tail of the run - the terminal result event and stderr
    @param quota    what ``looks_like_quota`` said about the same text
    @flow  a limit wins over everything -> turn death -> anything else
    """
    if quota:
        return CAUSE_QUOTA
    if is_turn_death(subtype, text):
        return CAUSE_TURNS
    return CAUSE_OTHER


@dataclass
class Verdict:
    """건강한 소진인가 병리인가, and the counts the judgement was made from."""

    healthy: bool = True
    units: int = 0  # distinct files this session actually moved
    turns: int = 0  # turns it spent doing that
    reasons: List[str] = field(default_factory=list)
    edits: List[Tuple[str, int]] = field(default_factory=list)
    rereads: List[Tuple[str, int]] = field(default_factory=list)
    commands: List[Tuple[str, int]] = field(default_factory=list)
    errors: int = 0

    @property
    def pace(self) -> float:
        """Turns per file, measured on this session - the only speed we actually know."""
        return float(self.turns) / float(max(1, self.units))


def _accesses(telemetry: Optional[Dict[str, Any]], operations: Tuple[str, ...]) -> List[Tuple[str, int]]:
    """``(path, count)`` for the file accesses of these operations, busiest first.

    @param telemetry   a telemetry dict, or None
    @param operations  which operations count - edit/write, or read
    """
    found: List[Tuple[str, int]] = []
    observed = (telemetry or {}).get("observed") or {}
    for access in observed.get("file_accesses") or []:
        if not isinstance(access, dict) or access.get("operation") not in operations:
            continue
        path = access.get("path")
        if isinstance(path, str) and path:
            found.append((path, int(access.get("count") or 0)))
    found.sort(key=lambda item: (-item[1], item[0]))
    return found


def _repeats(telemetry: Optional[Dict[str, Any]], key: str, name: str) -> List[Tuple[str, int]]:
    """``(what, count)`` out of one of telemetry's ESTIMATED repeat tables.

    @param telemetry  a telemetry dict, or None
    @param key        the table name under ``estimated``
    @param name       which field of a row names the thing repeated
    """
    found: List[Tuple[str, int]] = []
    estimated = (telemetry or {}).get("estimated") or {}
    for row in estimated.get(key) or []:
        if not isinstance(row, dict):
            continue
        what = row.get(name)
        if isinstance(what, str) and what:
            found.append((what, int(row.get("count") or 0)))
    found.sort(key=lambda item: (-item[1], item[0]))
    return found


def verdict(telemetry: Optional[Dict[str, Any]]) -> Verdict:
    """Judge how a session spent the turns it ran out of: honestly, or in a circle.

    The judgement is two-valued because the requirement defined it that way, and
    an ambiguous session is called healthy rather than pathological: under the
    default mode a pathological verdict buys nothing at all, and under
    ``aggressive`` it buys a session. 애매를 병리로 밀면 둘 다 손해다.

    @param telemetry  the dead run's telemetry dict
    @flow  count edits / re-reads / repeated commands / errors -> units
           -> each 병리 조건 appends a reason -> healthy is "no reason at all"
    주요 내부 변수: edits(편집된 파일), rereads(재독), commands(반복 명령), reasons(병리 근거)
    """
    exact = (telemetry or {}).get("exact") or {}
    observed = (telemetry or {}).get("observed") or {}
    edits = _accesses(telemetry, ("edit", "write"))
    reads = _accesses(telemetry, ("read",))
    rereads = _repeats(telemetry, "repeated_reads", "path")
    commands = _repeats(telemetry, "repeated_commands", "command")
    errors = int(observed.get("error_results") or 0)
    # A stage that edits is measured by what it edited. plan and decompose never
    # edit anything, so for them the unit of work is a file read.
    units = len(edits) or len(reads)

    reasons: List[str] = []
    if units == 0:
        reasons.append("아무 파일도 만지지 않았다")
    if edits and edits[0][1] >= EDIT_REPEAT_MAX and len(edits) <= EDIT_REPEAT_UNITS:
        reasons.append(
            "같은 파일을 {0}번 고쳤다 ({1}) - 손댄 파일은 {2}개뿐".format(
                edits[0][1], edits[0][0], len(edits)
            )
        )
    reread_total = sum(max(0, count - 1) for _, count in rereads)
    if rereads and rereads[0][1] >= READ_REPEAT_MAX:
        reasons.append("같은 파일을 {0}번 다시 읽었다 ({1})".format(rereads[0][1], rereads[0][0]))
    elif reread_total >= READ_REPEAT_TOTAL:
        reasons.append("재독이 {0}회 - 읽은 것을 또 읽고 있다".format(reread_total))
    if commands and commands[0][1] >= COMMAND_REPEAT_MAX:
        reasons.append(
            "같은 명령을 {0}번 돌렸다 ({1})".format(commands[0][1], commands[0][0])
        )
    if errors >= ERROR_RESULT_MAX:
        reasons.append("도구 에러가 {0}회 누적됐다".format(errors))

    return Verdict(
        healthy=not reasons,
        units=units,
        turns=int(exact.get("num_turns") or 0),
        reasons=reasons,
        edits=edits,
        rereads=rereads,
        commands=commands,
        errors=errors,
    )


def extension_turns(remaining: int, v: Verdict, budget: int, cap: int = DEFAULT_TURN_CAP) -> int:
    """How many turns to add: Remaining × 실측 소화 속도 + 여유 20%, capped.

    Returning 0 is the answer that stops the automation - the stage is already at
    the absolute ceiling, and no arithmetic below may talk its way past it.

    @param remaining  how many files the plan still names as untouched
    @param v          the verdict, which carries the measured pace
    @param budget     this stage's current turn budget
    @param cap        the absolute ceiling a budget may be raised to
    @flow  at the cap -> 0 ; Remaining known -> pace estimate ; unknown -> the flat
           blind figure ; then floor at MIN_EXTENSION and clip to what the cap leaves
    주요 내부 변수: want(견적), room(상한까지 남은 여유)
    """
    room = int(cap) - int(budget)
    if room <= 0:
        return 0
    if int(remaining) >= 1:
        # Rounded before the ceiling: 4 files at 10 turns each plus 20% is 48,
        # and binary floating point makes that 48.000000000000007 - which a bare
        # ceil() would charge a whole extra turn for.
        want = int(math.ceil(round(int(remaining) * v.pace * (1.0 + SAFETY_MARGIN), 6)))
    else:
        # 못 재겠다를 지어내지 않는다: a plan stage has no plan.md to count, and a
        # stage that touched everything it named still died holding something.
        want = BLIND_EXTENSION
    return min(max(want, MIN_EXTENSION), room)


def render_activity(telemetry: Optional[Dict[str, Any]], limit: int = ACTIVITY_ROWS) -> str:
    """What the session actually did, as the markdown block the Observer is handed.

    Built only from the telemetry summary. events.jsonl itself is never quoted:
    it is the one file in this tool that has no upper bound on its size.

    @param telemetry  the dead run's telemetry dict
    @param limit      how many of each list to show, including the last tool calls
    @flow  header counts -> edits -> re-reads -> repeated commands -> the last calls
    주요 내부 변수: v(판정에 쓰인 것과 같은 집계), lines(누적 출력)
    """
    v = verdict(telemetry)
    observed = (telemetry or {}).get("observed") or {}
    counts = observed.get("tool_counts") or {}
    lines = [
        "turns {0}   tools {1}   errors {2}".format(
            v.turns, int(observed.get("tool_calls") or 0), v.errors
        ),
        "tool counts: {0}".format(
            ", ".join(
                "{0} {1}".format(name, counts[name])
                for name in sorted(counts, key=lambda k: -int(counts[k] or 0))
            )
            or "(none)"
        ),
        "",
        "## Files edited ({0})".format(len(v.edits)),
    ]
    for path, count in v.edits[:limit] or [("(none)", 0)]:
        lines.append("- {0}  x{1}".format(path, count))
    lines += ["", "## Files read more than once ({0})".format(len(v.rereads))]
    for path, count in v.rereads[:limit] or [("(none)", 0)]:
        lines.append("- {0}  x{1}".format(path, count))
    lines += ["", "## Commands run more than once ({0})".format(len(v.commands))]
    for command, count in v.commands[:limit] or [("(none)", 0)]:
        lines.append("- {0}  x{1}".format(command, count))

    calls = [c for c in (observed.get("calls") or []) if isinstance(c, dict)]
    lines += ["", "## Last {0} tool calls".format(min(limit, len(calls)))]
    for call in calls[-limit:] or [{}]:
        lines.append(
            "- turn {0}  {1}  {2}{3}".format(
                call.get("turn", "?"),
                call.get("tool", "(none)"),
                call.get("target", ""),
                "  [error]" if call.get("result_is_error") else "",
            )
        )
    return "\n".join(lines)


def parse_suggestion(text: str) -> str:
    """Which of the three the Observer chose, defaulting to the harmless one.

    An answer nobody can read must not be able to stop a slice, so anything that
    is not one of ``SUGGESTIONS`` reads as ``switch-approach`` - the suggestion
    whose consequence is one more attempt.

    @param text  observation.md as the session wrote it
    @flow  every 'suggestion:' line -> first known word wins -> else the default
    """
    for match in _SUGGESTION_RE.finditer(text or ""):
        value = match.group(1).strip().strip(".").lower()
        for known in SUGGESTIONS:
            if value == known:
                return known
    return DEFAULT_SUGGESTION
