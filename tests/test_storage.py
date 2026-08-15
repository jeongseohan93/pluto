import json
import sqlite3
from datetime import datetime

from aidev.storage import (
    Database,
    RunStore,
    list_run_dirs,
    make_run_id,
    resolve_run_dir,
    slugify,
)


def test_make_run_id():
    run_id = make_run_id("doctor night action", now=datetime(2026, 8, 13, 19, 45, 0))
    assert run_id == "20260813-194500-doctor-night-action"
    assert slugify("!!!") == "run"


def test_run_store_writes_all_artifacts(tmp_path, filled_telemetry):
    with RunStore(tmp_path / "runs", "run-x") as store:
        store.write_prompt("do the thing")
        store.write_raw_event('{"type": "system"}\n')
        store.write_raw_event('{"type": "result"}')
        store.write_stderr("warning: something\n")
        store.write_telemetry(filled_telemetry.to_dict())
        store.write_run_json({"run_id": "run-x", "command": ["claude", "-p"]})

    run_dir = tmp_path / "runs" / "run-x"
    assert (run_dir / "prompt.md").read_text(encoding="utf-8") == "do the thing"
    assert (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() == [
        '{"type": "system"}',
        '{"type": "result"}',
    ]
    assert "warning" in (run_dir / "stderr.log").read_text(encoding="utf-8")
    assert json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["run_id"] == "run-x"
    assert store.read_telemetry()["exact"]["session_id"] == "sess-1"


def test_database_roundtrip(tmp_path, filled_telemetry):
    data = filled_telemetry.to_dict()
    with Database(tmp_path / "aidev.db") as db:
        db.save_run(data)
        db.save_run(data)  # idempotent: re-saving must not duplicate rows

        runs = db.recent_runs()
        assert len(runs) == 1
        row = runs[0]
        assert row["id"] == data["run_id"]
        assert row["project"] == "joker"
        assert row["phase"] == "implement"
        assert row["session_id"] == "sess-1"
        assert row["turns"] == 4
        assert row["cost_usd"] == 1.23
        assert row["exit_code"] == 0

        tool_rows = db.conn.execute("SELECT * FROM tool_calls").fetchall()
        assert len(tool_rows) == 6
        assert {r["tool"] for r in tool_rows} == {"Read", "Bash", "Edit"}

        file_rows = db.conn.execute("SELECT * FROM file_accesses").fetchall()
        assert {(r["operation"], r["count"]) for r in file_rows} == {("read", 2), ("edit", 1)}

        phase_rows = db.phase_totals()
        assert len(phase_rows) == 1
        assert phase_rows[0]["phase"] == "implement"
        assert phase_rows[0]["runs"] == 1
        assert phase_rows[0]["tokens"] == data["estimated"]["attributable_tool_context_tokens"]


def test_database_stores_exact_tokens_and_gap(tmp_path, filled_telemetry):
    data = filled_telemetry.to_dict()
    with Database(tmp_path / "aidev.db") as db:
        db.save_run(data)
        row = db.recent_runs()[0]
        assert row["safety_profile"] == "default"
        assert row["input_tokens"] == 60
        assert row["cache_creation_tokens"] == 300
        assert row["cache_read_tokens"] == 600
        assert row["output_tokens"] == 120
        assert row["peak_context_tokens"] == 160
        assert row["attributable_tokens"] == data["estimated"]["attributable_tool_context_tokens"]
        assert row["gap_tokens"] == data["estimated"]["exact_vs_estimated"]["gap_tokens"]


def test_database_migrates_an_older_schema(tmp_path, filled_telemetry):
    """A db created before the token columns existed must keep working."""
    path = tmp_path / "aidev.db"
    legacy = sqlite3.connect(str(path))
    legacy.execute("CREATE TABLE runs (id TEXT PRIMARY KEY, project TEXT)")
    legacy.commit()
    legacy.close()

    with Database(path) as db:
        db.save_run(filled_telemetry.to_dict())
        row = db.recent_runs()[0]
        assert row["peak_context_tokens"] == 160
        assert row["safety_profile"] == "default"


def test_resolve_run_dir(tmp_path):
    runs = tmp_path / "runs"
    for name in ("20260101-000000-a", "20260102-000000-b"):
        (runs / name).mkdir(parents=True)

    assert resolve_run_dir(runs, "last").name == "20260102-000000-b"
    assert resolve_run_dir(runs, "20260101-000000-a").name == "20260101-000000-a"
    assert resolve_run_dir(runs, "20260101").name == "20260101-000000-a"
    assert resolve_run_dir(runs, "nope") is None
    assert len(list_run_dirs(runs)) == 2
    assert list_run_dirs(tmp_path / "missing") == []
