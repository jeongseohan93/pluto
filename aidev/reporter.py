"""Terminal output: a live panel while running, a hotspot report when done.

No web dashboard in v0.1 - deliberately. Everything here renders from plain
dicts (``Telemetry.snapshot()`` / ``Telemetry.to_dict()``) so ``aidev report``
can redraw an old run straight from ``telemetry.json``.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional, Sequence

from .telemetry import HOTSPOT_BUCKETS

_PATH_WIDTH = 30

# Windows consoles are often cp949/cp1252; degrade the box drawing instead of
# crashing on UnicodeEncodeError.
_GLYPHS = {"rule": "━", "bar": "█", "empty": "·", "times": "×", "ellipsis": "…"}
_ASCII_GLYPHS = {"rule": "=", "bar": "#", "empty": ".", "times": "x", "ellipsis": "..."}
_unicode_ok = True


def configure_output(stream=None) -> None:
    """Pick unicode or ASCII glyphs based on what ``stream`` can encode."""
    global _unicode_ok
    stream = stream or sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(_GLYPHS.values()).encode(encoding)
        _unicode_ok = True
    except (LookupError, UnicodeEncodeError):
        _unicode_ok = False
    # Last resort: never let a stray glyph kill a finished run.
    for handle in (sys.stdout, sys.stderr):
        reconfigure = getattr(handle, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def glyph(name: str) -> str:
    return (_GLYPHS if _unicode_ok else _ASCII_GLYPHS)[name]


def rule() -> str:
    return glyph("rule") * 42


# --------------------------------------------------------------------- format

def format_bytes(nbytes: Optional[int]) -> str:
    n = float(nbytes or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return "{0:.0f} {1}".format(n, unit)
            return "{0:.1f} {1}".format(n, unit) if n < 10 else "{0:.0f} {1}".format(n, unit)
        n /= 1024.0
    return "{0:.0f} B".format(n)


def format_tokens(tokens: Optional[int]) -> str:
    n = int(tokens or 0)
    if n < 1000:
        return "{0}".format(n)
    if n < 100_000:
        return "{0:.1f}k".format(n / 1000.0)
    return "{0:.0f}k".format(n / 1000.0)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--:--"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "{0:d}:{1:02d}:{2:02d}".format(hours, minutes, secs)
    return "{0:02d}:{1:02d}".format(minutes, secs)


def format_cost(cost: Optional[float]) -> str:
    return "-" if cost is None else "${0:.4f}".format(cost)


def shorten_path(path: str, width: int = _PATH_WIDTH) -> str:
    path = (path or "").replace("\\", "/")
    if len(path) <= width:
        return path
    marker = glyph("ellipsis")
    tail = path[-max(1, width - len(marker)) :]
    return marker + tail


# ----------------------------------------------------------------- live panel

def render_live(snapshot: Dict[str, Any]) -> List[str]:
    lines = ["AI DEV RUNNER", rule(), ""]
    session = snapshot.get("session_id") or "-"
    rows = [
        ("Project", snapshot.get("project") or "-"),
        ("Task", snapshot.get("task") or "-"),
        ("Phase", (snapshot.get("phase") or "-").upper()),
        ("Status", (snapshot.get("status") or "-").upper()),
        ("", ""),
        ("Session", session[:12] + (glyph("ellipsis") if len(session) > 12 else "")),
        ("Turn", str(snapshot.get("turn", 0))),
        ("Elapsed", format_duration(snapshot.get("elapsed_s"))),
        ("Cost", format_cost(snapshot.get("cost_usd"))),
    ]
    for label, value in rows:
        lines.append("" if not label else "{0:<10}{1}".format(label, value))

    tokens = snapshot.get("tokens") or {}
    final = snapshot.get("usage_state") == "final"
    lines += ["", "Tokens  {0}".format("FINAL EXACT" if final else "EXACT, PROVISIONAL")]
    for label, key in (
        ("in", "input"),
        ("cache w", "cache_creation"),
        ("cache r", "cache_read"),
        ("out", "output"),
        ("peak ctx", "peak_context"),
    ):
        lines.append(" {0:<12}{1:>10}".format(label, "{0:,}".format(int(tokens.get(key) or 0))))

    tool_counts = snapshot.get("tool_counts") or {}
    if tool_counts:
        lines += ["", "Tools"]
        for tool, count in list(tool_counts.items())[:8]:
            lines.append(" {0:<12}{1:>4}".format(tool, count))

    largest = snapshot.get("largest_reads") or []
    if largest:
        lines += ["", "Largest reads"]
        for path, nbytes in largest:
            lines.append(" {0:<32}{1:>9}".format(shorten_path(path, 32), format_bytes(nbytes)))

    repeated = snapshot.get("repeated_reads") or []
    if repeated:
        lines += ["", "Repeated reads"]
        for path, count in repeated:
            lines.append(" {0:<32}{1:>9}".format(shorten_path(path, 32), glyph("times") + str(count)))

    attribution = snapshot.get("attribution") or {}
    tokens_by_bucket = attribution.get("tokens") or {}
    if any(tokens_by_bucket.values()):
        shares = attribution.get("share_pct") or {}
        ranked = sorted(HOTSPOT_BUCKETS, key=lambda kv: -tokens_by_bucket.get(kv[0], 0))
        lines += ["", "Attribution (estimated)"]
        for key, label in ranked[:3]:
            if not tokens_by_bucket.get(key):
                continue
            lines.append(
                " {0:<20}{1:>4.0f}%{2:>9}".format(
                    label, shares.get(key, 0.0), "~" + format_tokens(tokens_by_bucket.get(key))
                )
            )
        lines.append(
            " {0:<20}{1:>13}".format(
                "attributable", "~" + format_tokens(attribution.get("total_tokens"))
            )
        )

    last_tool = snapshot.get("last_tool")
    if last_tool and snapshot.get("status") == "running":
        lines += ["", "> " + shorten_path(last_tool, 60)]
    return lines


# --------------------------------------------------------------------- watch

STALE_AFTER_S = 10.0


def render_watch(payload: Dict[str, Any], now: Optional[float] = None) -> List[str]:
    """One watcher frame: the live panel plus where it came from and how fresh."""
    now = time.time() if now is None else now
    run_id = payload.get("run_id") or "-"
    status = (payload.get("status") or "-").upper()
    lines = ["AI DEV WATCH   {0}".format(run_id), rule()]
    lines += render_live(payload)[2:]  # drop the panel's own title and rule

    updated_at = payload.get("updated_at")
    age = None if not isinstance(updated_at, (int, float)) else max(0.0, now - updated_at)
    lines += ["", rule()]
    if age is None:
        lines.append("updated  unknown")
    elif status == "RUNNING" and age > STALE_AFTER_S:
        lines.append(
            "STALE: no update for {0:.0f}s - the runner may have stopped".format(age)
        )
    else:
        lines.append("updated  {0:.1f}s ago".format(age))
    if status == "RUNNING":
        lines.append("Ctrl+C stops watching only - the run keeps going")
    return lines


class LiveReporter:
    """Redraws the panel in place on a TTY; falls back to log lines otherwise."""

    def __init__(
        self,
        stream=None,
        enabled: bool = True,
        interval: float = 0.4,
    ) -> None:
        self.stream = stream or sys.stdout
        self.interval = interval
        self._lines_drawn = 0
        self._last_draw = 0.0
        self._tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.enabled = enabled
        if self.enabled and self._tty:
            _enable_ansi()

    def update(self, snapshot: Dict[str, Any], force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < self.interval:
            return
        self._last_draw = now
        if self._tty:
            self._draw(render_live(snapshot))
        else:
            self._log(snapshot)

    def frame(self, lines: Sequence[str], force: bool = False) -> None:
        """Draw a pre-rendered panel. Used by ``aidev watch``."""
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and (now - self._last_draw) < self.interval:
            return
        self._last_draw = now
        if self._tty:
            self._draw(lines)
        else:
            self.stream.write("\n".join(lines) + "\n")
            self.stream.flush()

    def clear(self) -> None:
        """Forget the drawn panel so the next output starts on a fresh line."""
        if self.enabled and self._tty and self._lines_drawn:
            self.stream.write("\n")
            self.stream.flush()
        self._lines_drawn = 0

    def finish(self, snapshot: Dict[str, Any]) -> None:
        if not self.enabled:
            return
        if self._tty:
            self._draw(render_live(snapshot))
            self._lines_drawn = 0
        self.stream.write("\n")
        self.stream.flush()

    def _draw(self, lines: Sequence[str]) -> None:
        out = []
        if self._lines_drawn:
            out.append("\x1b[{0}A".format(self._lines_drawn))
        for line in lines:
            out.append("\x1b[2K" + line + "\n")
        # Clear leftovers if the panel shrank.
        extra = self._lines_drawn - len(lines)
        for _ in range(max(0, extra)):
            out.append("\x1b[2K\n")
        if extra > 0:
            out.append("\x1b[{0}A".format(extra))
        self.stream.write("".join(out))
        self.stream.flush()
        self._lines_drawn = len(lines)

    def _log(self, snapshot: Dict[str, Any]) -> None:
        tools = " ".join(
            "{0}={1}".format(t, c) for t, c in list((snapshot.get("tool_counts") or {}).items())[:6]
        )
        tokens = snapshot.get("tokens") or {}
        self.stream.write(
            "[{0}] turn {1} | in {2:,} cache-r {3:,} out {4:,} | {5} | {6}\n".format(
                format_duration(snapshot.get("elapsed_s")),
                snapshot.get("turn", 0),
                int(tokens.get("input") or 0),
                int(tokens.get("cache_read") or 0),
                int(tokens.get("output") or 0),
                tools or "no tools yet",
                shorten_path(snapshot.get("last_tool") or "-", 50),
            )
        )
        self.stream.flush()


# --------------------------------------------------------------- final report

def render_final(data: Dict[str, Any]) -> str:
    exact = data.get("exact", {})
    observed = data.get("observed", {})
    estimated = data.get("estimated", {})
    hotspots = estimated.get("hotspots", {})
    tokens = hotspots.get("tokens", {})
    shares = hotspots.get("share_pct", {})

    lines: List[str] = ["", "RUN SUMMARY", rule(), ""]
    duration_ms = exact.get("duration_ms")
    api_ms = exact.get("duration_api_ms")
    duration = format_duration(duration_ms / 1000.0 if duration_ms else None)
    if api_ms:
        duration += " (api {0})".format(format_duration(api_ms / 1000.0))
    for label, value in (
        ("Run", data.get("run_id")),
        ("Project", data.get("project")),
        ("Task", data.get("task")),
        ("Phase", (data.get("phase") or "-").upper()),
        ("Status", (data.get("status") or "-").upper()),
        ("Session", exact.get("session_id") or "-"),
        ("Model", exact.get("model") or "-"),
        ("Turns", exact.get("num_turns") if exact.get("num_turns") is not None else observed.get("turns_seen")),
        ("Duration", duration),
        ("Cost", format_cost(exact.get("total_cost_usd"))),
        ("Exit", exact.get("exit_code")),
        ("Safety", data.get("safety_profile") or "default"),
    ):
        lines.append("{0:<10}{1}".format(label, "-" if value is None else value))

    lines += ["", "Tokens (EXACT)", ""]
    lines += _token_rows(exact)

    violations = observed.get("safety_violations") or []
    if violations:
        lines += ["", "SAFETY VIOLATION: readonly profile, mutating calls observed", ""]
        for entry in violations[:5]:
            lines.append(
                " turn {0}  {1}  {2}".format(
                    entry.get("turn"), entry.get("tool"), shorten_path(entry.get("target") or "", 50)
                )
            )

    tool_counts = observed.get("tool_counts") or {}
    if tool_counts:
        lines += ["", "Tools", ""]
        for tool, count in sorted(tool_counts.items(), key=lambda kv: -kv[1]):
            lines.append(" {0:<14}{1:>5}".format(tool, count))
        lines.append(
            " {0:<14}{1:>5}".format(
                "result bytes", format_bytes(observed.get("result_bytes_total"))
            )
        )

    lines += ["", "TOKEN / CONTEXT HOTSPOTS", rule(), ""]
    ranked = sorted(HOTSPOT_BUCKETS, key=lambda kv: -tokens.get(kv[0], 0))
    for index, (key, label) in enumerate(ranked, start=1):
        share = shares.get(key, 0.0)
        lines.append(
            "{0}. {1:<22}{2:>5.0f}%  {3:>8}  {4}".format(
                index,
                label,
                share,
                "~" + format_tokens(tokens.get(key, 0)),
                _bar(share),
            )
        )
    lines.append("")
    lines.append(
        "{0:<34}{1:>8}".format(
            "Estimated attributable tool context",
            "~" + format_tokens(estimated.get("attributable_tool_context_tokens")),
        )
    )

    lines += _gap_rows(estimated.get("exact_vs_estimated") or {})

    repeated_reads = estimated.get("repeated_reads") or []
    repeated_commands = estimated.get("repeated_commands") or []
    if repeated_reads or repeated_commands:
        lines += ["", "Suspected waste", ""]
        for entry in repeated_reads[:5]:
            lines.append(" {0}".format(shorten_path(entry["path"], 60)))
            lines.append(
                "   read {0} {1}   {2} returned   (~{3} repeated)".format(
                    glyph("times"),
                    entry["count"],
                    format_bytes(entry["bytes"]),
                    format_tokens(_tokens(entry["repeat_bytes"])),
                )
            )
        for entry in repeated_commands[:5]:
            lines.append(" {0}".format(shorten_path(entry["command"], 60)))
            lines.append(
                "   run {0} {1}   {2} output   (~{3} repeated)".format(
                    glyph("times"),
                    entry["count"],
                    format_bytes(entry["bytes"]),
                    format_tokens(_tokens(entry["repeat_bytes"])),
                )
            )

    lines += [
        "",
        "Estimated avoidable context",
        "  ~{0} tokens".format(format_tokens(estimated.get("avoidable_tokens"))),
        "",
    ]
    malformed = observed.get("malformed_lines") or 0
    if malformed:
        lines.append("note: {0} unparsed stdout line(s) - see events.jsonl".format(malformed))
    duplicates = observed.get("duplicate_messages") or 0
    if duplicates:
        lines.append(
            "note: {0} duplicate assistant message(s) dropped by request_id".format(duplicates)
        )
    reconciliation = exact.get("reconciliation") or {}
    if reconciliation.get("matches") is False:
        lines.append("note: usage reconciliation - {0}".format(reconciliation.get("note")))
    lines.append(
        "EXACT: session/turns/duration/cost/usage   OBSERVED: tool+file counts   "
        "ESTIMATED: token buckets (~{0} chars/token)".format(
            estimated.get("chars_per_token", 4)
        )
    )
    return "\n".join(lines)


def _token_rows(exact: Dict[str, Any]) -> List[str]:
    tokens = exact.get("tokens") or {}
    reconciliation = exact.get("reconciliation") or {}
    rows = [
        ("input", tokens.get("input")),
        ("cache creation", tokens.get("cache_creation")),
        ("cache read", tokens.get("cache_read")),
        ("output", tokens.get("output")),
        ("billed input", tokens.get("billed_input")),
        ("peak context", tokens.get("peak_context")),
    ]
    lines = [" {0:<16}{1:>12}".format(label, "{0:,}".format(int(value or 0))) for label, value in rows]
    source = reconciliation.get("source") or "none"
    matches = reconciliation.get("matches")
    verdict = {True: "stream matched", False: "stream differs", None: "stream only"}[matches]
    lines.append(" {0:<16}{1:>12}".format("source", "{0} / {1}".format(source, verdict)))
    return lines


def _gap_rows(gap: Dict[str, Any]) -> List[str]:
    if not gap or not gap.get("comparable"):
        return [
            "",
            "EXACT vs ESTIMATED",
            "",
            " no exact usage in the stream - gap cannot be computed",
        ]
    gap_tokens = gap.get("gap_tokens") or 0
    gap_pct = gap.get("gap_pct")
    lines = [
        "",
        "EXACT vs ESTIMATED",
        "",
        " {0:<28}{1:>9}".format(
            "Peak context (exact)", format_tokens(gap.get("peak_context_tokens"))
        ),
        " {0:<28}{1:>9}".format(
            "Attributable tools (est.)", "~" + format_tokens(gap.get("attributable_tokens"))
        ),
        " {0:<28}{1:>9}{2}".format(
            "Unattributed gap" if gap_tokens >= 0 else "Estimate overshoot",
            ("" if gap_tokens >= 0 else "-") + format_tokens(abs(gap_tokens)),
            "" if gap_pct is None else "   ({0:.0f}%)".format(gap_pct),
        ),
        " {0:<28}{1:>9}".format(
            "Billed input (cumulative)", format_tokens(gap.get("billed_input_tokens"))
        ),
        "",
        " note: {0}".format(gap.get("note") or ""),
    ]
    return lines


def render_run_list(rows: Sequence[Any]) -> str:
    lines = [
        "{0:<28}{1:<12}{2:<11}{3:>6}{4:>10}{5:>10}".format(
            "RUN", "PROJECT", "PHASE", "TURNS", "COST", "STATUS"
        ),
        "-" * 77,
    ]
    for row in rows:
        lines.append(
            "{0:<28}{1:<12}{2:<11}{3:>6}{4:>10}{5:>10}".format(
                str(row["id"])[:27],
                str(row["project"] or "-")[:11],
                str(row["phase"] or "-")[:10],
                row["turns"] if row["turns"] is not None else "-",
                format_cost(row["cost_usd"]),
                str(row["status"] or "-")[:9],
            )
        )
    if len(lines) == 2:
        lines.append("(no runs yet)")
    return "\n".join(lines)


def render_phase_stats(rows: Sequence[Any], limit: int) -> str:
    total = sum((row["tokens"] or 0) for row in rows) or 1
    lines = ["", "PHASE BREAKDOWN (last {0} runs)".format(limit), rule(), ""]
    for row in rows:
        share = 100.0 * (row["tokens"] or 0) / total
        lines.append(
            "{0:<12}{1:>5.0f}%  {2:>8}  {3:>9}  {4:>3} runs  {5}".format(
                str(row["phase"] or "-").upper(),
                share,
                "~" + format_tokens(row["tokens"]),
                format_cost(row["cost_usd"]),
                row["runs"],
                _bar(share),
            )
        )
    if not rows:
        lines.append("(no runs yet)")
    return "\n".join(lines) + "\n"


def _bar(share: float, width: int = 20) -> str:
    filled = int(round(width * max(0.0, min(100.0, share)) / 100.0))
    return glyph("bar") * filled + glyph("empty") * (width - filled)


def _tokens(nbytes: int) -> int:
    from .events import estimate_tokens

    return estimate_tokens(nbytes)


def _enable_ansi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # pragma: no cover - cosmetic only
        pass
