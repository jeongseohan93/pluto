"""Minimal interpretation of Claude Code's ``--output-format stream-json`` events.

This module only *normalizes* events. It never aggregates and never writes
anything: aggregation lives in :mod:`aidev.telemetry`, persistence in
:mod:`aidev.storage`. Raw lines are always stored verbatim by the runner, so a
parser bug here can be fixed and replayed later.

Event shapes we care about (everything else is passed through untouched)::

    {"type": "system", "subtype": "init", "session_id": ..., "model": ...}
    {"type": "assistant", "message": {"content": [...], "usage": {...}}}
    {"type": "user", "message": {"content": [{"type": "tool_result", ...}]}}
    {"type": "result", "subtype": "success", "total_cost_usd": ..., ...}
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Rough conversion used for every ESTIMATED number in this project.
CHARS_PER_TOKEN = 4

# Usage fields that make up the context actually sent to the model.
# Anthropic reports cache hits separately from fresh input, so context size is
# the sum of all three - not just ``input_tokens``.
USAGE_INPUT_FIELDS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

EXPLORE_TOOLS = {
    "Read",
    "NotebookRead",
    "Glob",
    "Grep",
    "LS",
    "WebFetch",
    "WebSearch",
}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
EXEC_TOOLS = {"Bash", "BashOutput", "KillBash", "KillShell"}

# Which input key identifies "what the tool acted on".
_TARGET_KEYS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Write": ("file_path",),
    "NotebookRead": ("notebook_path",),
    "NotebookEdit": ("notebook_path",),
    "Bash": ("command",),
    "BashOutput": ("bash_id", "shell_id"),
    "Glob": ("pattern",),
    "Grep": ("pattern",),
    "LS": ("path",),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
    "Task": ("description",),
    "Skill": ("skill",),
}

_TEST_COMMAND_RE = re.compile(
    r"""(?ix)
    \b(
        pytest | tox | unittest
      | jest | vitest | mocha | ava | cypress | playwright
      | rspec | phpunit | ctest
      | (npm|pnpm|yarn|bun) \s+ (run \s+)? test\w*
      | go \s+ test
      | cargo \s+ (test|nextest)
      | (mvn|gradle|gradlew) \s+ \S* test
      | dotnet \s+ test
      | make \s+ \S* test\w*
    )\b
    """
)

_TARGET_MAX = 160


@dataclass
class ToolUse:
    """A ``tool_use`` block emitted inside an assistant message."""

    id: str
    name: str
    input: Dict[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        return tool_target(self.name, self.input)

    @property
    def category(self) -> str:
        return tool_category(self.name, self.input)

    @property
    def input_bytes(self) -> int:
        return content_bytes(self.input)


@dataclass
class ToolResult:
    """A ``tool_result`` block emitted inside a user message."""

    tool_use_id: str
    result_bytes: int
    is_error: bool = False


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse one JSONL line. Returns ``None`` for blanks / non-JSON noise."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def event_type(event: Dict[str, Any]) -> str:
    value = event.get("type")
    return value if isinstance(value, str) else "unknown"


def is_init(event: Dict[str, Any]) -> bool:
    return event_type(event) == "system" and event.get("subtype") == "init"


def is_result(event: Dict[str, Any]) -> bool:
    return event_type(event) == "result"


def session_id(event: Dict[str, Any]) -> Optional[str]:
    value = event.get("session_id")
    return value if isinstance(value, str) else None


def _message_content(event: Dict[str, Any]) -> List[Any]:
    message = event.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, list):
        return content
    if isinstance(content, (str, dict)):
        return [content]
    return []


def iter_tool_uses(event: Dict[str, Any]) -> List[ToolUse]:
    """Tool calls started by this event (assistant messages only)."""
    if event_type(event) != "assistant":
        return []
    uses: List[ToolUse] = []
    for block in _message_content(event):
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        uses.append(
            ToolUse(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or "unknown"),
                input=tool_input if isinstance(tool_input, dict) else {},
            )
        )
    return uses


def iter_tool_results(event: Dict[str, Any]) -> List[ToolResult]:
    """Tool results carried by this event (user messages only)."""
    if event_type(event) != "user":
        return []
    results: List[ToolResult] = []
    for block in _message_content(event):
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        results.append(
            ToolResult(
                tool_use_id=str(block.get("tool_use_id") or ""),
                result_bytes=content_bytes(block.get("content")),
                is_error=bool(block.get("is_error")),
            )
        )
    return results


def assistant_text_bytes(event: Dict[str, Any]) -> int:
    """Bytes of model prose/thinking - tool_use inputs are excluded on purpose.

    Tool inputs are attributed to their own bucket (implementation, mostly), so
    counting them here would double count the same tokens.
    """
    if event_type(event) != "assistant":
        return 0
    total = 0
    for block in _message_content(event):
        if isinstance(block, str):
            total += _utf8_len(block)
        elif isinstance(block, dict) and block.get("type") in ("text", "thinking"):
            for key in ("text", "thinking"):
                value = block.get(key)
                if isinstance(value, str):
                    total += _utf8_len(value)
    return total


def extract_usage(event: Dict[str, Any]) -> Dict[str, int]:
    """Token usage attached to an assistant message or the final result."""
    usage = event.get("usage")
    if not isinstance(usage, dict):
        message = event.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {k: v for k, v in usage.items() if isinstance(v, int)}


def context_tokens(usage: Dict[str, int]) -> int:
    """Context size of one request: fresh input + cache creation + cache read."""
    return sum(int(usage.get(field, 0) or 0) for field in USAGE_INPUT_FIELDS)


def message_key(event: Dict[str, Any]) -> Optional[str]:
    """Stable identity of an assistant message, for deduplication.

    Claude Code can emit the same assistant message more than once (retries,
    resumed sessions, replayed transcripts). ``request_id`` is the strongest
    signal; the message id and the event uuid are fallbacks. ``None`` means
    "cannot tell" - the caller must then count the event rather than drop it.
    """
    for key in ("request_id", "requestId"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return "req:" + value
    message = event.get("message")
    if isinstance(message, dict):
        value = message.get("id")
        if isinstance(value, str) and value:
            return "msg:" + value
    value = event.get("uuid")
    if isinstance(value, str) and value:
        return "uuid:" + value
    return None


def tool_target(name: str, tool_input: Optional[Dict[str, Any]]) -> str:
    """A short, human readable "what did this tool touch" string."""
    tool_input = tool_input or {}
    for key in _TARGET_KEYS.get(name, ()):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _shorten(" ".join(value.split()))
    for key in ("file_path", "path", "pattern", "command", "url", "query"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _shorten(" ".join(value.split()))
    return ""


def tool_category(name: str, tool_input: Optional[Dict[str, Any]] = None) -> str:
    """One of ``explore`` / ``edit`` / ``test`` / ``exec`` / ``other``."""
    if name in EXPLORE_TOOLS:
        return "explore"
    if name in EDIT_TOOLS:
        return "edit"
    if name in EXEC_TOOLS:
        command = (tool_input or {}).get("command")
        if isinstance(command, str) and looks_like_test(command):
            return "test"
        return "exec"
    return "other"


def file_operation(name: str) -> Optional[str]:
    """Map a tool to a file access operation, or ``None`` if it is not one."""
    if name in ("Read", "NotebookRead"):
        return "read"
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "edit"
    if name == "Write":
        return "write"
    return None


def looks_like_test(command: str) -> bool:
    return bool(_TEST_COMMAND_RE.search(command or ""))


def estimate_tokens(nbytes: int) -> int:
    """Bytes -> tokens. Deliberately crude; every consumer labels it ESTIMATED."""
    if nbytes <= 0:
        return 0
    return int(round(nbytes / CHARS_PER_TOKEN))


def content_bytes(content: Any) -> int:
    """UTF-8 size of arbitrary tool input/result content."""
    if content is None:
        return 0
    if isinstance(content, str):
        return _utf8_len(content)
    if isinstance(content, (int, float, bool)):
        return len(str(content))
    if isinstance(content, dict):
        if content.get("type") in ("text", "thinking"):
            total = 0
            for key in ("text", "thinking"):
                value = content.get(key)
                if isinstance(value, str):
                    total += _utf8_len(value)
            if total:
                return total
        source = content.get("source")
        if isinstance(source, dict) and isinstance(source.get("data"), str):
            return len(source["data"])
        return _json_bytes(content)
    if isinstance(content, list):
        return sum(content_bytes(item) for item in content)
    return _utf8_len(str(content))


def _json_bytes(value: Any) -> int:
    try:
        return _utf8_len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return _utf8_len(str(value))


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8", "replace"))


def _shorten(value: str) -> str:
    if len(value) <= _TARGET_MAX:
        return value
    return value[: _TARGET_MAX - 3] + "..."
