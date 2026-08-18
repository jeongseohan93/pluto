"""progress.md: what a stage had done when it ran out of turns.

Measured 2026-08-16/17: a stage that died at its turn limit took its whole
understanding with it, and the resume began by rediscovering the repository from
scratch. The requirement asked for the engine to *signal* the session at 90% of
its budget so the session writes the file itself - but a headless ``claude -p``
has no channel back into a running session. So the engine writes it instead,
from what telemetry already observed: which files were edited, which commands
were run, and what the model last said. That needs no cooperation from the
session at all, which is also why it survives a session that died mid-sentence.

The file is not deleted after it is injected. It is the human's report too.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# The fraction of a stage's turn budget at which a snapshot is taken. Late
# enough that the work is worth recording, early enough to still have turns.
PROGRESS_TURN_RATIO = 0.9

MAX_LISTED = 15
LAST_WORDS_LIMIT = 1200

REASON_BUDGET = "turn-budget {0}%".format(int(PROGRESS_TURN_RATIO * 100))
REASON_INCOMPLETE = "stage ended incomplete"


def should_snapshot(turn: int, budget: int, already: bool = False) -> bool:
    """Has this stage reached the fraction of its budget worth recording at?

    @param turn     turns used so far, as telemetry counts them
    @param budget   this stage's --max-turns
    @param already  whether a snapshot was already written for this stage
    """
    if already or not budget or budget <= 0:
        return False
    return int(turn) >= max(1, int(math.ceil(budget * PROGRESS_TURN_RATIO)))


def touched_paths(telemetry: Optional[Dict[str, Any]]) -> List[str]:
    """Files this stage wrote or edited, in the order telemetry first saw them.

    @param telemetry  a telemetry snapshot or its stored dict
    """
    paths: List[str] = []
    observed = (telemetry or {}).get("observed") or {}
    for access in observed.get("file_accesses") or []:
        if not isinstance(access, dict) or access.get("operation") not in ("edit", "write"):
            continue
        path = access.get("path")
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def ran_commands(telemetry: Optional[Dict[str, Any]]) -> List[str]:
    """Commands this stage ran, deduplicated - the other half of "what was done".

    @param telemetry  a telemetry snapshot or its stored dict
    """
    found: List[str] = []
    observed = (telemetry or {}).get("observed") or {}
    for call in observed.get("calls") or []:
        if not isinstance(call, dict) or call.get("category") not in ("test", "exec"):
            continue
        target = call.get("target") or call.get("name")
        if isinstance(target, str) and target and target not in found:
            found.append(target)
    return found


def remaining_paths(plan_paths: Sequence[str], done: Sequence[str]) -> List[str]:
    """Paths the plan named that nothing has edited yet.

    @param plan_paths  every path plan.md mentions
    @param done        paths this stage already wrote
    """
    edited = {str(path).replace("\\", "/").lower().lstrip("./") for path in done}
    left: List[str] = []
    for path in plan_paths:
        key = str(path).replace("\\", "/").lower().lstrip("./")
        if key in edited or any(key in seen or seen.endswith(key) for seen in edited):
            continue
        if path not in left:
            left.append(path)
    return left


def render(
    slice_id: str,
    stage: str,
    attempt: int,
    telemetry: Optional[Dict[str, Any]] = None,
    plan_paths: Sequence[str] = (),
    last_text: str = "",
    reason: str = REASON_INCOMPLETE,
    turn: int = 0,
    budget: int = 0,
    repo: str = "",
) -> str:
    """The whole file: what was done, what is left, what the session last said.

    @param slice_id    which slice this belongs to
    @param stage       the stage that was running
    @param attempt     which attempt of it
    @param telemetry   the live telemetry snapshot, where "done" comes from
    @param plan_paths  paths plan.md names, where "remaining" comes from
    @param last_text   the last thing the session said, clipped
    @param reason      why this was written - a budget threshold, or a dead stage
    @param turn        turns used at the moment of writing
    @param budget      this stage's turn budget
    @param repo        the user's repository, for the resume command
    @flow  header -> Done (files, commands) -> Remaining -> Last words -> Resume
    주요 내부 변수: done(편집된 파일), lines(누적 출력)
    """
    done = touched_paths(telemetry)
    commands = ran_commands(telemetry)
    lines = [
        "# PROGRESS - {0} · {1}".format(slice_id or "(slice)", stage),
        datetime.now().astimezone().isoformat(timespec="seconds"),
        "",
        "attempt {0}   turn {1}/{2}   ({3})".format(attempt, turn, budget or "?", reason),
        "",
        "## Done",
    ]
    if done:
        for path in done[:MAX_LISTED]:
            lines.append("- edited {0}".format(path))
        if len(done) > MAX_LISTED:
            lines.append("- ... {0} more file(s)".format(len(done) - MAX_LISTED))
    else:
        lines.append("- (no file was edited)")
    for command in commands[:MAX_LISTED]:
        lines.append("- ran {0}".format(command))

    left = remaining_paths(plan_paths, done)
    lines += ["", "## Remaining"]
    if left:
        lines.append("Named by the plan and not edited yet:")
        for path in left[:MAX_LISTED]:
            lines.append("- {0}".format(path))
        if len(left) > MAX_LISTED:
            lines.append("- ... {0} more".format(len(left) - MAX_LISTED))
    else:
        lines.append("- (nothing the plan names is untouched - check the plan itself)")

    lines += ["", "## Last words", ""]
    words = (last_text or "").strip()
    lines.append(
        (words[:LAST_WORDS_LIMIT] + ("..." if len(words) > LAST_WORDS_LIMIT else ""))
        if words
        else "(the session said nothing before it stopped)"
    )

    lines += ["", "## Resume", ""]
    lines.append(
        "    aidev pipeline --repo {0} --resume-slice {1}".format(repo or "<repo>", slice_id)
    )
    return "\n".join(lines) + "\n"


def read(path: Path) -> str:
    """The file's text, or empty when it is not there. Never raises.

    @param path  where progress.md lives
    """
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
