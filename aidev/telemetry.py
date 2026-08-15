"""Aggregation of stream-json events into EXACT / OBSERVED / ESTIMATED numbers.

The three tiers are kept apart on purpose and never mixed in the output:

EXACT      reported by Claude Code itself - session id, turns, duration, cost,
           and token usage (input / cache creation / cache read / output),
           accumulated live per assistant message and reconciled against the
           final ``result`` event.
OBSERVED   counted from the event stream (tool calls, files, result bytes)
ESTIMATED  derived with a bytes->tokens heuristic (context hotspots, waste)

Assistant messages are deduplicated by ``request_id`` before anything is
counted, so a retried or replayed message never inflates usage or tool counts.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from . import events as ev

PHASES = ("plan", "explore", "implement", "review", "test", "repair", "decompose")

# Hotspot buckets shown by the final report, in display order.
HOTSPOT_BUCKETS = (
    ("exploration", "Code exploration"),
    ("implementation", "Implementation"),
    ("test_output", "Test output"),
    ("model_output", "Model output"),
    ("other", "Other"),
)

# Tools that can change the repository. Used by the readonly safety profile to
# check, after the fact, that nothing mutating actually ran.
MUTATING_TOOLS = frozenset(
    {
        "Bash",
        "BashOutput",
        "KillBash",
        "KillShell",
        "Edit",
        "MultiEdit",
        "Write",
        "NotebookEdit",
        "Task",
        "SlashCommand",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ToolCallRecord:
    """One tool_use, later completed by its matching tool_result."""

    id: str
    turn: int
    tool: str
    target: str
    category: str
    input_bytes: int
    started_at: float
    result_bytes: int = 0
    result_is_error: bool = False
    duration_ms: Optional[int] = None
    completed: bool = False

    def to_row(self) -> Dict[str, Any]:
        return {
            "turn": self.turn,
            "tool": self.tool,
            "target": self.target,
            "category": self.category,
            "input_bytes": self.input_bytes,
            "result_bytes": self.result_bytes,
            "result_is_error": self.result_is_error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class FileAccess:
    path: str
    operation: str
    count: int = 0
    bytes: int = 0


class Telemetry:
    """Ingests events one at a time; safe to snapshot at any moment."""

    def __init__(
        self,
        run_id: str,
        project: str,
        phase: str,
        repo: str,
        prompt_path: str,
        task: str = "",
        safety_profile: str = "default",
        disallowed_tools: str = "",
        started_at: Optional[float] = None,
    ) -> None:
        self.run_id = run_id
        self.project = project
        self.phase = phase
        self.repo = repo
        self.prompt_path = prompt_path
        self.task = task or run_id
        self.safety_profile = safety_profile
        self.disallowed_tools = disallowed_tools

        self.started_monotonic = time.monotonic()
        self.started_at = started_at or time.time()
        self.started_at_iso = utc_now_iso()
        self.finished_at_iso: Optional[str] = None
        self.status = "running"

        # --- EXACT (from system/init, assistant usage, and the result event) ---
        self.session_id: Optional[str] = None
        self.model: Optional[str] = None
        self.num_turns: Optional[int] = None
        self.duration_ms: Optional[int] = None
        self.duration_api_ms: Optional[int] = None
        self.total_cost_usd: Optional[float] = None
        self.result_subtype: Optional[str] = None
        self.is_error: Optional[bool] = None
        self.exit_code: Optional[int] = None
        self.usage_streamed: Dict[str, int] = {}
        self.usage_final: Dict[str, int] = {}
        self.peak_context_tokens = 0
        self.usage_messages = 0

        # --- OBSERVED ---
        self.event_count = 0
        self.malformed_lines = 0
        self.turns_seen = 0
        self.duplicate_messages = 0
        self.duplicate_tool_uses = 0
        self.tool_counts: Dict[str, int] = {}
        self.tool_calls: List[ToolCallRecord] = []
        self._pending: Dict[str, ToolCallRecord] = {}
        self._seen_message_keys: Set[str] = set()
        self._seen_tool_use_ids: Set[str] = set()
        self.file_accesses: Dict[str, FileAccess] = {}
        self.assistant_text_bytes = 0
        self.category_result_bytes: Dict[str, int] = {}
        self.category_input_bytes: Dict[str, int] = {}
        self.test_runs = 0
        self.error_results = 0
        self.last_tool: Optional[str] = None

    # ------------------------------------------------------------------ ingest

    def note_malformed_line(self) -> None:
        self.malformed_lines += 1

    def ingest(self, event: Dict[str, Any], now: Optional[float] = None) -> None:
        now = now if now is not None else time.monotonic()
        self.event_count += 1

        sid = ev.session_id(event)
        if sid and not self.session_id:
            self.session_id = sid

        kind = ev.event_type(event)
        if kind == "system":
            model = event.get("model")
            if isinstance(model, str):
                self.model = model
        elif kind == "assistant":
            self._ingest_assistant(event, now)
        elif kind == "user":
            self._ingest_user(event, now)
        elif kind == "result":
            self._ingest_result(event)

    def _ingest_assistant(self, event: Dict[str, Any], now: float) -> None:
        key = ev.message_key(event)
        if key is not None:
            if key in self._seen_message_keys:
                self.duplicate_messages += 1
                return  # already counted: usage, turn and tools all skipped
            self._seen_message_keys.add(key)

        self.turns_seen += 1
        self.assistant_text_bytes += ev.assistant_text_bytes(event)

        usage = ev.extract_usage(event)
        if usage:
            self.usage_messages += 1
            for name, value in usage.items():
                self.usage_streamed[name] = self.usage_streamed.get(name, 0) + value
            self.peak_context_tokens = max(
                self.peak_context_tokens, ev.context_tokens(usage)
            )

        for use in ev.iter_tool_uses(event):
            if use.id and use.id in self._seen_tool_use_ids:
                self.duplicate_tool_uses += 1
                continue
            if use.id:
                self._seen_tool_use_ids.add(use.id)
            record = ToolCallRecord(
                id=use.id,
                turn=self.turns_seen,
                tool=use.name,
                target=use.target,
                category=use.category,
                input_bytes=use.input_bytes,
                started_at=now,
            )
            self.tool_calls.append(record)
            if record.id:
                self._pending[record.id] = record
            self.tool_counts[record.tool] = self.tool_counts.get(record.tool, 0) + 1
            self.category_input_bytes[record.category] = (
                self.category_input_bytes.get(record.category, 0) + record.input_bytes
            )
            if record.category == "test":
                self.test_runs += 1
            self.last_tool = "{0} {1}".format(record.tool, record.target).strip()

            operation = ev.file_operation(record.tool)
            if operation and record.target:
                self._touch_file(record.target, operation, None)

    def _ingest_user(self, event: Dict[str, Any], now: float) -> None:
        for result in ev.iter_tool_results(event):
            record = self._pending.pop(result.tool_use_id, None)
            if record is None:
                # Result without a known call: still count the bytes as "other".
                self.category_result_bytes["other"] = (
                    self.category_result_bytes.get("other", 0) + result.result_bytes
                )
                if result.is_error:
                    self.error_results += 1
                continue
            record.result_bytes = result.result_bytes
            record.result_is_error = result.is_error
            record.duration_ms = int(round((now - record.started_at) * 1000))
            record.completed = True
            self.category_result_bytes[record.category] = (
                self.category_result_bytes.get(record.category, 0) + result.result_bytes
            )
            if result.is_error:
                self.error_results += 1
            operation = ev.file_operation(record.tool)
            if operation and record.target:
                self._touch_file(record.target, operation, result.result_bytes)

    def _ingest_result(self, event: Dict[str, Any]) -> None:
        subtype = event.get("subtype")
        if isinstance(subtype, str):
            self.result_subtype = subtype
        for attr, key in (
            ("num_turns", "num_turns"),
            ("duration_ms", "duration_ms"),
            ("duration_api_ms", "duration_api_ms"),
        ):
            value = event.get(key)
            if isinstance(value, int):
                setattr(self, attr, value)
        cost = event.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            self.total_cost_usd = float(cost)
        if "is_error" in event:
            self.is_error = bool(event.get("is_error"))
        usage = ev.extract_usage(event)
        if usage:
            self.usage_final = dict(usage)

    def _touch_file(self, path: str, operation: str, nbytes: Optional[int]) -> None:
        """``nbytes is None`` means "the call started"; a number means "it returned"."""
        key = "{0}::{1}".format(operation, path)
        access = self.file_accesses.get(key)
        if access is None:
            access = FileAccess(path=path, operation=operation)
            self.file_accesses[key] = access
        if nbytes is None:
            access.count += 1
        else:
            access.bytes += nbytes

    # ------------------------------------------------------------------ finish

    def finish(self, status: str, exit_code: Optional[int]) -> None:
        self.status = status
        self.exit_code = exit_code
        self.finished_at_iso = utc_now_iso()
        if self.duration_ms is None:
            self.duration_ms = int(round((time.monotonic() - self.started_monotonic) * 1000))

    # ------------------------------------------------------------------- usage

    @property
    def usage(self) -> Dict[str, int]:
        """Authoritative usage: the result event when present, else the stream."""
        return dict(self.usage_final or self.usage_streamed)

    def reconciliation(self) -> Dict[str, Any]:
        """Compare live-accumulated usage against the final result event."""
        streamed = self.usage_streamed
        final = self.usage_final
        if not final:
            return {
                "source": "stream" if streamed else "none",
                "delta": {},
                "matches": None,
                "note": "no usage on the result event; live accumulation is authoritative",
            }
        keys = sorted(set(streamed) | set(final))
        delta = {k: final.get(k, 0) - streamed.get(k, 0) for k in keys}
        mismatched = {k: v for k, v in delta.items() if v}
        return {
            "source": "result",
            "delta": delta,
            "matches": not mismatched,
            "note": (
                "live accumulation matches the result event"
                if not mismatched
                else "result event differs from live accumulation: {0}".format(
                    ", ".join("{0} {1:+d}".format(k, v) for k, v in mismatched.items())
                )
            ),
        }

    def billed_input_tokens(self) -> int:
        """Cumulative context billed across every turn (re-sends included)."""
        return ev.context_tokens(self.usage)

    def output_tokens(self) -> int:
        return int(self.usage.get("output_tokens", 0) or 0)

    def token_summary(self) -> Dict[str, int]:
        usage = self.usage
        return {
            "input": int(usage.get("input_tokens", 0) or 0),
            "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
            "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
            "output": int(usage.get("output_tokens", 0) or 0),
            "billed_input": self.billed_input_tokens(),
            "peak_context": self.peak_context_tokens,
        }

    # ---------------------------------------------------------------- readouts

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_monotonic

    @property
    def turn(self) -> int:
        return self.num_turns if self.num_turns is not None else self.turns_seen

    def reads_by_file(self) -> List[FileAccess]:
        reads = [a for a in self.file_accesses.values() if a.operation == "read"]
        reads.sort(key=lambda a: (-a.bytes, -a.count, a.path))
        return reads

    def repeated_reads(self) -> List[FileAccess]:
        return [a for a in self.reads_by_file() if a.count > 1]

    def repeated_commands(self) -> List[Dict[str, Any]]:
        """Bash/test commands that ran more than once, with their total output."""
        grouped: Dict[str, Dict[str, Any]] = {}
        for call in self.tool_calls:
            if call.category not in ("test", "exec") or not call.target:
                continue
            entry = grouped.setdefault(
                call.target,
                {
                    "command": call.target,
                    "category": call.category,
                    "count": 0,
                    "bytes": 0,
                    "repeat_bytes": 0,
                },
            )
            if entry["count"] > 0:
                entry["repeat_bytes"] += call.result_bytes
            entry["count"] += 1
            entry["bytes"] += call.result_bytes
        repeats = [e for e in grouped.values() if e["count"] > 1]
        repeats.sort(key=lambda e: (-e["repeat_bytes"], -e["count"]))
        return repeats

    def repeated_read_waste(self) -> List[Dict[str, Any]]:
        """Per file: bytes returned by every read after the first one."""
        first_seen = set()
        wasted: Dict[str, Dict[str, Any]] = {}
        for call in self.tool_calls:
            if ev.file_operation(call.tool) != "read" or not call.target:
                continue
            entry = wasted.setdefault(
                call.target, {"path": call.target, "count": 0, "bytes": 0, "repeat_bytes": 0}
            )
            if call.target in first_seen:
                entry["repeat_bytes"] += call.result_bytes
            else:
                first_seen.add(call.target)
            entry["count"] += 1
            entry["bytes"] += call.result_bytes
        repeats = [e for e in wasted.values() if e["count"] > 1]
        repeats.sort(key=lambda e: (-e["repeat_bytes"], -e["count"]))
        return repeats

    def hotspots(self) -> Dict[str, Any]:
        """ESTIMATED context attributable to tool traffic and model output."""
        result_bytes = self.category_result_bytes
        input_bytes = self.category_input_bytes
        buckets = {
            "exploration": result_bytes.get("explore", 0),
            "implementation": result_bytes.get("edit", 0) + input_bytes.get("edit", 0),
            "test_output": result_bytes.get("test", 0),
            "model_output": self.assistant_text_bytes,
            "other": (
                result_bytes.get("exec", 0)
                + result_bytes.get("other", 0)
                + input_bytes.get("explore", 0)
                + input_bytes.get("test", 0)
                + input_bytes.get("exec", 0)
                + input_bytes.get("other", 0)
            ),
        }
        tokens = {k: ev.estimate_tokens(v) for k, v in buckets.items()}
        total = sum(tokens.values())
        shares = {
            k: (round(100.0 * v / total, 1) if total else 0.0) for k, v in tokens.items()
        }
        return {"bytes": buckets, "tokens": tokens, "total_tokens": total, "share_pct": shares}

    def exact_vs_estimated(self) -> Dict[str, Any]:
        """How much of the real context our tool accounting explains.

        Both sides are one-shot context sizes: ``peak_context_tokens`` is the
        largest single request Claude actually paid for, and the attributable
        estimate is what we can trace to tool traffic. The remainder is system
        prompt, tool schemas, CLAUDE.md, the prompt itself and conversation
        scaffolding - real context we cannot attribute from the event stream.
        """
        attributable = self.hotspots()["total_tokens"]
        peak = self.peak_context_tokens
        gap = peak - attributable
        return {
            "peak_context_tokens": peak,
            "attributable_tokens": attributable,
            "gap_tokens": gap,
            "gap_pct": round(100.0 * gap / peak, 1) if peak else None,
            "comparable": peak > 0,
            "billed_input_tokens": self.billed_input_tokens(),
            "output_tokens": self.output_tokens(),
            "note": (
                "gap = system prompt, tool schemas, CLAUDE.md, conversation scaffolding"
                if gap >= 0
                else "estimate exceeds the exact peak: the bytes->tokens heuristic overshot"
            ),
        }

    def avoidable_tokens(self) -> int:
        wasted_bytes = sum(e["repeat_bytes"] for e in self.repeated_read_waste())
        wasted_bytes += sum(e["repeat_bytes"] for e in self.repeated_commands())
        return ev.estimate_tokens(wasted_bytes)

    def safety_violations(self) -> List[Dict[str, Any]]:
        """Mutating calls that got through despite the readonly profile."""
        if self.safety_profile != "readonly":
            return []
        return [
            {"turn": c.turn, "tool": c.tool, "target": c.target}
            for c in self.tool_calls
            if c.tool in MUTATING_TOOLS
        ]

    def snapshot(self) -> Dict[str, Any]:
        """Live view for the terminal panel and for ``live.json``.

        ``usage_state`` is the honesty flag watchers key off: until the result
        event lands, every token number here is PROVISIONAL - it is whatever the
        assistant messages have reported so far.
        """
        reads = self.reads_by_file()
        hotspots = self.hotspots()
        return {
            "usage_state": "final" if self.usage_final else "provisional",
            "attribution": {
                "tokens": hotspots["tokens"],
                "share_pct": hotspots["share_pct"],
                "total_tokens": hotspots["total_tokens"],
            },
            "run_id": self.run_id,
            "project": self.project,
            "task": self.task,
            "phase": self.phase,
            "status": self.status,
            "safety_profile": self.safety_profile,
            "session_id": self.session_id,
            "turn": self.turn,
            "elapsed_s": self.elapsed_s,
            "cost_usd": self.total_cost_usd,
            "tokens": self.token_summary(),
            "tool_counts": dict(sorted(self.tool_counts.items(), key=lambda kv: -kv[1])),
            "largest_reads": [(a.path, a.bytes) for a in reads[:5]],
            "repeated_reads": [(a.path, a.count) for a in self.repeated_reads()[:5]],
            "last_tool": self.last_tool,
            "events": self.event_count,
            "errors": self.error_results,
            "duplicates": self.duplicate_messages,
            "malformed_lines": self.malformed_lines,
            "safety_violations": len(self.safety_violations()),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Everything, tier by tier - this is what telemetry.json holds."""
        hotspots = self.hotspots()
        return {
            "run_id": self.run_id,
            "project": self.project,
            "task": self.task,
            "phase": self.phase,
            "repo": self.repo,
            "prompt_path": self.prompt_path,
            "status": self.status,
            "safety_profile": self.safety_profile,
            "disallowed_tools": self.disallowed_tools,
            "started_at": self.started_at_iso,
            "finished_at": self.finished_at_iso,
            "exact": {
                "session_id": self.session_id,
                "model": self.model,
                "num_turns": self.num_turns,
                "duration_ms": self.duration_ms,
                "duration_api_ms": self.duration_api_ms,
                "total_cost_usd": self.total_cost_usd,
                "exit_code": self.exit_code,
                "result_subtype": self.result_subtype,
                "is_error": self.is_error,
                "usage": self.usage,
                "usage_streamed": self.usage_streamed,
                "usage_final": self.usage_final,
                "usage_messages": self.usage_messages,
                "reconciliation": self.reconciliation(),
                "tokens": self.token_summary(),
            },
            "observed": {
                "events": self.event_count,
                "malformed_lines": self.malformed_lines,
                "duplicate_messages": self.duplicate_messages,
                "duplicate_tool_uses": self.duplicate_tool_uses,
                "turns_seen": self.turns_seen,
                "tool_counts": self.tool_counts,
                "tool_calls": len(self.tool_calls),
                "test_runs": self.test_runs,
                "error_results": self.error_results,
                "assistant_text_bytes": self.assistant_text_bytes,
                "result_bytes_by_category": self.category_result_bytes,
                "input_bytes_by_category": self.category_input_bytes,
                "result_bytes_total": sum(self.category_result_bytes.values()),
                "safety_violations": self.safety_violations(),
                "file_accesses": [asdict(a) for a in self.file_accesses.values()],
                "calls": [c.to_row() for c in self.tool_calls],
            },
            "estimated": {
                "chars_per_token": ev.CHARS_PER_TOKEN,
                "hotspots": hotspots,
                "attributable_tool_context_tokens": hotspots["total_tokens"],
                "exact_vs_estimated": self.exact_vs_estimated(),
                "repeated_reads": self.repeated_read_waste(),
                "repeated_commands": self.repeated_commands(),
                "avoidable_tokens": self.avoidable_tokens(),
            },
        }
