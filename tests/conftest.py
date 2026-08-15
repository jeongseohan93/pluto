import json
from typing import Any, Dict, List

import pytest

from aidev import reporter
from aidev.telemetry import Telemetry


@pytest.fixture(autouse=True)
def _deterministic_glyphs():
    """The CLI picks glyphs from the console encoding; keep tests independent."""
    reporter._unicode_ok = True
    yield
    reporter._unicode_ok = True


TURN_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 50,
    "cache_read_input_tokens": 100,
    "output_tokens": 20,
}


def assistant(
    *blocks: Dict[str, Any],
    session: str = "sess-1",
    request_id: str = None,
    usage: Dict[str, int] = None,
) -> Dict[str, Any]:
    message: Dict[str, Any] = {"content": list(blocks)}
    if usage is not None:
        message["usage"] = dict(usage)
    event = {"type": "assistant", "session_id": session, "message": message}
    if request_id is not None:
        event["request_id"] = request_id
    return event


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def tool_use(tool_id: str, name: str, **tool_input: Any) -> Dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def tool_result(tool_id: str, size: int = 0, is_error: bool = False, session: str = "sess-1"):
    return {
        "type": "user",
        "session_id": session,
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": "x" * size,
                    "is_error": is_error,
                }
            ]
        },
    }


def init_event(session: str = "sess-1") -> Dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "session_id": session,
        "model": "claude-opus-5",
        "tools": ["Read", "Edit", "Bash"],
    }


def result_event(
    session: str = "sess-1",
    turns: int = 4,
    duration_ms: int = 120_000,
    api_ms: int = 90_000,
    cost: float = 1.23,
    usage: Dict[str, int] = None,
    messages: int = 6,
) -> Dict[str, Any]:
    if usage is None:
        # By default the result agrees with the six streamed assistant messages.
        usage = {key: value * messages for key, value in TURN_USAGE.items()}
    return {
        "type": "result",
        "subtype": "success",
        "session_id": session,
        "is_error": False,
        "num_turns": turns,
        "duration_ms": duration_ms,
        "duration_api_ms": api_ms,
        "total_cost_usd": cost,
        "usage": usage,
        "result": "done",
    }


def sample_stream() -> List[Dict[str, Any]]:
    """A small but representative session: 2 reads of one file, a test, an edit."""
    return [
        init_event(),
        assistant(
            text_block("Let me look at the session file."),
            tool_use("t1", "Read", file_path="src/gameSession.js"),
            request_id="req_1",
            usage=TURN_USAGE,
        ),
        tool_result("t1", size=1000),
        assistant(tool_use("t2", "Read", file_path="src/gameSession.js"), request_id="req_2", usage=TURN_USAGE),
        tool_result("t2", size=1000),
        assistant(tool_use("t3", "Bash", command="npm test"), request_id="req_3", usage=TURN_USAGE),
        tool_result("t3", size=800),
        assistant(tool_use("t4", "Bash", command="npm test"), request_id="req_4", usage=TURN_USAGE),
        tool_result("t4", size=800),
        assistant(
            text_block("Now the edit."),
            tool_use("t5", "Edit", file_path="src/gameSession.js", old_string="a", new_string="b"),
            request_id="req_5",
            usage=TURN_USAGE,
        ),
        tool_result("t5", size=40),
        assistant(tool_use("t6", "Bash", command="git status"), request_id="req_6", usage=TURN_USAGE),
        tool_result("t6", size=100, is_error=True),
        result_event(),
    ]


@pytest.fixture
def stream():
    return sample_stream()


@pytest.fixture
def filled_telemetry(stream):
    telemetry = Telemetry(
        run_id="20260813-194500-doctor",
        project="joker",
        phase="implement",
        repo="/repo",
        prompt_path="/repo/tasks/doctor.md",
        task="doctor",
    )
    for event in stream:
        telemetry.ingest(event)
    telemetry.finish("completed", 0)
    return telemetry


@pytest.fixture
def stream_lines(stream):
    return [json.dumps(event, ensure_ascii=False) for event in stream]
