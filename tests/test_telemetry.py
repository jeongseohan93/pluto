from aidev.telemetry import Telemetry

from conftest import TURN_USAGE, assistant, result_event, text_block, tool_result, tool_use


def test_exact_values_come_from_the_result_event(filled_telemetry):
    data = filled_telemetry.to_dict()["exact"]
    assert data["session_id"] == "sess-1"
    assert data["model"] == "claude-opus-5"
    assert data["num_turns"] == 4
    assert data["duration_ms"] == 120_000
    assert data["duration_api_ms"] == 90_000
    assert data["total_cost_usd"] == 1.23
    assert data["exit_code"] == 0
    assert data["is_error"] is False


def test_usage_is_accumulated_live_per_field(filled_telemetry):
    exact = filled_telemetry.to_dict()["exact"]
    # six assistant messages, each carrying TURN_USAGE
    assert exact["usage_messages"] == 6
    assert exact["usage_streamed"] == {key: value * 6 for key, value in TURN_USAGE.items()}
    assert exact["tokens"] == {
        "input": 60,
        "cache_creation": 300,
        "cache_read": 600,
        "output": 120,
        "billed_input": 960,
        "peak_context": 160,
    }


def test_reconciliation_matches_when_result_agrees(filled_telemetry):
    reconciliation = filled_telemetry.to_dict()["exact"]["reconciliation"]
    assert reconciliation["source"] == "result"
    assert reconciliation["matches"] is True
    assert set(reconciliation["delta"].values()) == {0}


def test_reconciliation_reports_a_mismatch():
    telemetry = Telemetry("r1", "p", "implement", "/repo", "p.md")
    telemetry.ingest(assistant(text_block("hi"), request_id="req_1", usage=TURN_USAGE))
    telemetry.ingest(result_event(usage={"input_tokens": 999, "output_tokens": 20}))

    reconciliation = telemetry.reconciliation()
    assert reconciliation["matches"] is False
    assert reconciliation["delta"]["input_tokens"] == 989
    assert reconciliation["delta"]["output_tokens"] == 0
    assert "input_tokens +989" in reconciliation["note"]
    # the result event stays authoritative
    assert telemetry.usage["input_tokens"] == 999
    assert telemetry.usage_streamed["input_tokens"] == 10


def test_reconciliation_without_a_result_event():
    telemetry = Telemetry("r1", "p", "implement", "/repo", "p.md")
    telemetry.ingest(assistant(text_block("hi"), request_id="req_1", usage=TURN_USAGE))
    reconciliation = telemetry.reconciliation()
    assert reconciliation["source"] == "stream"
    assert reconciliation["matches"] is None
    assert telemetry.usage == TURN_USAGE


def test_duplicate_messages_are_dropped_by_request_id():
    telemetry = Telemetry("r1", "p", "implement", "/repo", "p.md")
    event = assistant(
        text_block("hi"), tool_use("t1", "Read", file_path="a.js"), request_id="req_1", usage=TURN_USAGE
    )
    telemetry.ingest(event)
    telemetry.ingest(dict(event))  # same request_id: a retry or a replayed transcript
    telemetry.ingest(tool_result("t1", size=400))

    assert telemetry.duplicate_messages == 1
    assert telemetry.turns_seen == 1
    assert telemetry.usage_messages == 1
    assert telemetry.usage_streamed == dict(TURN_USAGE)
    assert telemetry.tool_counts == {"Read": 1}
    assert telemetry.assistant_text_bytes == 2
    assert telemetry.file_accesses["read::a.js"].count == 1
    assert telemetry.file_accesses["read::a.js"].bytes == 400


def test_deduplication_falls_back_to_message_id_then_counts_unkeyed():
    telemetry = Telemetry("r1", "p", "implement", "/repo", "p.md")
    keyed = {"type": "assistant", "message": {"id": "msg_1", "content": [text_block("a")]}}
    telemetry.ingest(keyed)
    telemetry.ingest(dict(keyed))
    assert telemetry.duplicate_messages == 1

    # no request_id, no message id: we cannot tell, so we must not drop it
    unkeyed = {"type": "assistant", "message": {"content": [text_block("a")]}}
    telemetry.ingest(unkeyed)
    telemetry.ingest(dict(unkeyed))
    assert telemetry.duplicate_messages == 1
    assert telemetry.turns_seen == 3


def test_duplicate_tool_use_ids_are_dropped():
    telemetry = Telemetry("r1", "p", "implement", "/repo", "p.md")
    telemetry.ingest(assistant(tool_use("t1", "Read", file_path="a.js"), request_id="req_1"))
    telemetry.ingest(assistant(tool_use("t1", "Read", file_path="a.js"), request_id="req_2"))
    assert telemetry.duplicate_tool_uses == 1
    assert telemetry.tool_counts == {"Read": 1}


def test_exact_vs_estimated_gap():
    telemetry = Telemetry("r1", "p", "explore", "/repo", "p.md")
    telemetry.ingest(
        assistant(
            tool_use("t1", "Read", file_path="a.js"),
            request_id="req_1",
            usage={"input_tokens": 1000, "cache_read_input_tokens": 39_000, "output_tokens": 100},
        )
    )
    telemetry.ingest(tool_result("t1", size=40_000))  # ~10k estimated tokens

    gap = telemetry.exact_vs_estimated()
    assert gap["comparable"] is True
    assert gap["peak_context_tokens"] == 40_000
    # 40,000 result bytes / 4 plus the 21-byte tool input, both attributable
    assert gap["attributable_tokens"] == 10_005
    assert gap["gap_tokens"] == 29_995
    assert gap["gap_pct"] == 75.0
    assert "system prompt" in gap["note"]


def test_gap_flags_an_overshooting_estimate(filled_telemetry):
    gap = filled_telemetry.to_dict()["estimated"]["exact_vs_estimated"]
    assert gap["gap_tokens"] < 0
    assert "overshot" in gap["note"]


def test_gap_is_not_computable_without_usage():
    telemetry = Telemetry("r1", "p", "explore", "/repo", "p.md")
    telemetry.ingest(assistant(text_block("hi"), request_id="req_1"))
    gap = telemetry.exact_vs_estimated()
    assert gap["comparable"] is False
    assert gap["peak_context_tokens"] == 0


def test_readonly_profile_flags_mutating_calls():
    telemetry = Telemetry("r1", "p", "review", "/repo", "p.md", safety_profile="readonly")
    telemetry.ingest(assistant(tool_use("t1", "Read", file_path="a.js"), request_id="req_1"))
    telemetry.ingest(assistant(tool_use("t2", "Edit", file_path="a.js"), request_id="req_2"))
    violations = telemetry.safety_violations()
    assert len(violations) == 1
    assert violations[0] == {"turn": 2, "tool": "Edit", "target": "a.js"}


def test_default_profile_reports_no_violations(filled_telemetry):
    assert filled_telemetry.to_dict()["observed"]["safety_violations"] == []


def test_observed_tool_and_file_counts(filled_telemetry):
    observed = filled_telemetry.to_dict()["observed"]
    assert observed["tool_counts"] == {"Read": 2, "Bash": 3, "Edit": 1}
    assert observed["tool_calls"] == 6
    assert observed["test_runs"] == 2
    assert observed["error_results"] == 1
    assert observed["turns_seen"] == 6

    accesses = {(a["operation"], a["path"]): a for a in observed["file_accesses"]}
    read = accesses[("read", "src/gameSession.js")]
    assert read["count"] == 2
    assert read["bytes"] == 2000
    edit = accesses[("edit", "src/gameSession.js")]
    assert edit["count"] == 1
    assert edit["bytes"] == 40


def test_result_bytes_are_bucketed_by_category(filled_telemetry):
    observed = filled_telemetry.to_dict()["observed"]
    buckets = observed["result_bytes_by_category"]
    assert buckets["explore"] == 2000
    assert buckets["test"] == 1600
    assert buckets["exec"] == 100
    assert buckets["edit"] == 40
    assert observed["result_bytes_total"] == 3740


def test_tool_calls_get_durations_and_results(filled_telemetry):
    calls = filled_telemetry.tool_calls
    assert all(call.completed for call in calls)
    assert all(call.duration_ms is not None and call.duration_ms >= 0 for call in calls)
    assert calls[0].turn == 1
    assert calls[-1].result_is_error is True


def test_hotspots_and_waste(filled_telemetry):
    estimated = filled_telemetry.to_dict()["estimated"]
    hotspots = estimated["hotspots"]
    assert hotspots["bytes"]["exploration"] == 2000
    assert hotspots["bytes"]["test_output"] == 1600
    # implementation = edit result bytes + edit tool input bytes
    assert hotspots["bytes"]["implementation"] > 40
    assert hotspots["bytes"]["model_output"] > 0
    assert abs(sum(hotspots["share_pct"].values()) - 100.0) < 0.5

    repeated = {entry["path"]: entry for entry in estimated["repeated_reads"]}
    assert repeated["src/gameSession.js"]["count"] == 2
    assert repeated["src/gameSession.js"]["repeat_bytes"] == 1000

    commands = {entry["command"]: entry for entry in estimated["repeated_commands"]}
    assert commands["npm test"]["count"] == 2
    assert commands["npm test"]["repeat_bytes"] == 800
    # (1000 + 800) bytes / 4 chars-per-token
    assert estimated["avoidable_tokens"] == 450
    assert estimated["attributable_tool_context_tokens"] == hotspots["total_tokens"]


def test_snapshot_is_safe_mid_run(stream):
    telemetry = Telemetry("r1", "joker", "implement", "/repo", "p.md", task="doctor")
    snapshot = telemetry.snapshot()
    assert snapshot["status"] == "running"
    assert snapshot["turn"] == 0
    assert snapshot["tool_counts"] == {}

    for event in stream[:5]:
        telemetry.ingest(event)
    snapshot = telemetry.snapshot()
    assert snapshot["session_id"] == "sess-1"
    assert snapshot["turn"] == 2
    assert snapshot["largest_reads"] == [("src/gameSession.js", 2000)]
    assert snapshot["repeated_reads"] == [("src/gameSession.js", 2)]


def test_orphan_tool_result_is_counted_as_other():
    telemetry = Telemetry("r1", "p", "review", "/repo", "p.md")
    telemetry.ingest(
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "ghost", "content": "abcd"}]},
        }
    )
    assert telemetry.category_result_bytes["other"] == 4


def test_malformed_lines_are_tracked():
    telemetry = Telemetry("r1", "p", "plan", "/repo", "p.md")
    telemetry.note_malformed_line()
    telemetry.finish("failed", 1)
    assert telemetry.to_dict()["observed"]["malformed_lines"] == 1
    assert telemetry.to_dict()["status"] == "failed"
    assert telemetry.duration_ms is not None
