import io

from aidev import reporter
from aidev.telemetry import Telemetry


def test_format_helpers():
    assert reporter.format_bytes(0) == "0 B"
    assert reporter.format_bytes(1536) == "1.5 KB"
    assert reporter.format_bytes(42_000) == "41 KB"
    assert reporter.format_tokens(999) == "999"
    assert reporter.format_tokens(18_500) == "18.5k"
    assert reporter.format_duration(168) == "02:48"
    assert reporter.format_duration(3671) == "1:01:11"
    assert reporter.format_duration(None) == "--:--"
    assert reporter.format_cost(None) == "-"
    assert reporter.format_cost(1.5) == "$1.5000"
    assert reporter.shorten_path("a/b.js", 30) == "a/b.js"
    assert len(reporter.shorten_path("x" * 80, 30)) == 30


def test_render_live_panel(filled_telemetry):
    panel = "\n".join(reporter.render_live(filled_telemetry.snapshot()))
    assert "AI DEV RUNNER" in panel
    assert "Project   joker" in panel
    assert "IMPLEMENT" in panel
    assert "Read" in panel and "Bash" in panel
    assert "Largest reads" in panel
    assert "Repeated reads" in panel
    assert reporter.glyph("times") + "2" in panel


def test_render_final_report(filled_telemetry):
    text = reporter.render_final(filled_telemetry.to_dict())
    assert "RUN SUMMARY" in text
    assert "sess-1" in text
    assert "$1.2300" in text
    assert "TOKEN / CONTEXT HOTSPOTS" in text
    assert "Code exploration" in text
    assert "Test output" in text
    assert "Suspected waste" in text
    assert "read {0} 2".format(reporter.glyph("times")) in text
    assert "run {0} 2".format(reporter.glyph("times")) in text
    assert "Estimated avoidable context" in text
    assert "~450 tokens" in text


def test_live_panel_shows_exact_token_totals(filled_telemetry):
    panel = "\n".join(reporter.render_live(filled_telemetry.snapshot()))
    assert "cache r" in panel
    assert "600" in panel  # accumulated cache read
    assert "peak ctx" in panel
    # the result event has landed in this fixture, so usage is no longer live
    assert "Tokens  FINAL EXACT" in panel


def test_live_panel_marks_usage_provisional_until_the_result_event(stream):
    telemetry = Telemetry("r1", "joker", "implement", "/repo", "p.md", task="doctor")
    for event in stream[:-1]:  # everything except the result event
        telemetry.ingest(event)
    panel = "\n".join(reporter.render_live(telemetry.snapshot()))
    assert "Tokens  EXACT, PROVISIONAL" in panel
    assert "FINAL" not in panel

    telemetry.ingest(stream[-1])
    panel = "\n".join(reporter.render_live(telemetry.snapshot()))
    assert "Tokens  FINAL EXACT" in panel


def test_live_panel_shows_attribution(filled_telemetry):
    panel = "\n".join(reporter.render_live(filled_telemetry.snapshot()))
    assert "Attribution (estimated)" in panel
    assert "Code exploration" in panel
    assert "attributable" in panel


def test_final_report_renames_and_gap(filled_telemetry):
    text = reporter.render_final(filled_telemetry.to_dict())
    assert "Model output" in text
    assert "Review" not in text.split("TOKEN / CONTEXT HOTSPOTS")[1]
    assert "Estimated attributable tool context" in text
    assert "Estimated context total" not in text

    assert "Tokens (EXACT)" in text
    assert "cache creation" in text
    assert "peak context" in text
    assert "result / stream matched" in text

    assert "EXACT vs ESTIMATED" in text
    assert "Peak context (exact)" in text
    assert "Attributable tools (est.)" in text
    assert "Estimate overshoot" in text  # the fixture's estimate exceeds its peak


def test_final_report_shows_a_positive_gap():
    data = {
        "run_id": "r1",
        "exact": {"tokens": {"peak_context": 40_000}, "reconciliation": {"source": "result", "matches": True}},
        "observed": {},
        "estimated": {
            "hotspots": {"tokens": {}, "share_pct": {}},
            "attributable_tool_context_tokens": 10_000,
            "exact_vs_estimated": {
                "comparable": True,
                "peak_context_tokens": 40_000,
                "attributable_tokens": 10_000,
                "gap_tokens": 30_000,
                "gap_pct": 75.0,
                "billed_input_tokens": 400_000,
                "note": "gap = system prompt, tool schemas, CLAUDE.md, conversation scaffolding",
            },
        },
    }
    text = reporter.render_final(data)
    assert "Unattributed gap" in text
    assert "(75%)" in text
    assert "Billed input (cumulative)" in text


def test_final_report_without_usage_says_gap_is_uncomputable():
    data = {"run_id": "r1", "exact": {}, "observed": {}, "estimated": {"exact_vs_estimated": {"comparable": False}}}
    text = reporter.render_final(data)
    assert "gap cannot be computed" in text
    assert "none / stream only" in text


def test_final_report_flags_safety_violations_and_duplicates():
    data = {
        "run_id": "r1",
        "safety_profile": "readonly",
        "exact": {"reconciliation": {"source": "result", "matches": False, "note": "input_tokens +5"}},
        "observed": {
            "duplicate_messages": 2,
            "safety_violations": [{"turn": 3, "tool": "Edit", "target": "a.js"}],
        },
        "estimated": {},
    }
    text = reporter.render_final(data)
    assert "Safety    readonly" in text
    assert "SAFETY VIOLATION" in text
    assert "turn 3  Edit  a.js" in text
    assert "2 duplicate assistant message(s) dropped by request_id" in text
    assert "usage reconciliation - input_tokens +5" in text


def test_live_reporter_non_tty_logs_lines(filled_telemetry):
    stream = io.StringIO()
    live = reporter.LiveReporter(stream=stream, interval=0.0)
    live.update(filled_telemetry.snapshot(), force=True)
    live.finish(filled_telemetry.snapshot())
    output = stream.getvalue()
    assert "turn 4" in output
    assert "Read=2" in output
    assert "in 60 cache-r 600 out 120" in output


def test_live_reporter_can_be_disabled(filled_telemetry):
    stream = io.StringIO()
    live = reporter.LiveReporter(stream=stream, enabled=False)
    live.update(filled_telemetry.snapshot(), force=True)
    live.finish(filled_telemetry.snapshot())
    assert stream.getvalue() == ""


def test_ascii_fallback_for_legacy_consoles():
    class Cp949Stream:
        encoding = "cp949"

    reporter.configure_output(Cp949Stream())
    try:
        assert reporter.glyph("times") == "x"
        assert reporter.rule().startswith("=")
        shortened = reporter.shorten_path("x" * 80, 30)
        assert shortened.startswith("...") and len(shortened) == 30
    finally:
        reporter._unicode_ok = True
