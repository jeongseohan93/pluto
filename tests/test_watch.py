"""aidev watch: a read-only follower for a run happening in another terminal."""

import json
import os
import sys
import time
from pathlib import Path

import pytest

from aidev import cli, reporter, storage
from aidev.cli import main, resolve_watch_target
from aidev.storage import (
    RunStore,
    find_running_run,
    read_json_tolerant,
    read_live,
    write_json_atomic,
)

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """The watch loop must never actually block the test suite."""
    monkeypatch.setattr(cli, "_sleep", lambda seconds: None)


def write_live(run_dir: Path, **fields) -> Path:
    payload = {
        "run_id": run_dir.name,
        "project": "joker",
        "task": "doctor",
        "phase": "implement",
        "status": "running",
        "usage_state": "provisional",
        "turn": 3,
        "elapsed_s": 168.0,
        "session_id": "sess-1",
        "cost_usd": None,
        "tokens": {
            "input": 40,
            "cache_creation": 200,
            "cache_read": 1000,
            "output": 30,
            "billed_input": 1240,
            "peak_context": 1240,
        },
        "tool_counts": {"Read": 19, "Bash": 7, "Edit": 4},
        "largest_reads": [("src/gameSession.js", 42_000)],
        "repeated_reads": [("src/gameSession.js", 5)],
        "attribution": {
            "tokens": {"exploration": 8000, "implementation": 2000},
            "share_pct": {"exploration": 80.0, "implementation": 20.0},
            "total_tokens": 10_000,
        },
        "last_tool": "Read src/gameSession.js",
        "updated_at": time.time(),
    }
    payload.update(fields)
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(run_dir / "live.json", payload)
    return run_dir / "live.json"


# ------------------------------------------------------------- atomic writing

def test_atomic_write_leaves_no_temp_file_and_replaces_content(tmp_path):
    path = tmp_path / "live.json"
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"a": 2})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}
    assert list(tmp_path.iterdir()) == [path]  # no stray live.json.tmp


def test_run_store_write_live_is_throttled_then_forced(tmp_path):
    store = RunStore(tmp_path / "runs", "run-1")
    assert store.write_live({"status": "running", "turn": 1}, now=100.0) is True
    assert store.write_live({"status": "running", "turn": 2}, now=100.1) is False
    assert store.write_live({"status": "running", "turn": 3}, now=100.4) is True
    # a forced write always lands, however soon it comes
    assert store.write_live({"status": "completed", "turn": 4}, force=True, now=100.41) is True

    live = read_live(store.dir)
    assert live["turn"] == 4
    assert live["status"] == "completed"
    assert live["schema"] == 1
    assert live["pid"] == os.getpid()
    assert live["run_dir"] == str(store.dir)
    assert isinstance(live["updated_at"], float)


def test_reading_survives_every_broken_file(tmp_path):
    assert read_json_tolerant(tmp_path / "missing.json") is None

    truncated = tmp_path / "half.json"
    truncated.write_text('{"run_id": "abc", "tok', encoding="utf-8")
    assert read_json_tolerant(truncated) is None

    not_an_object = tmp_path / "list.json"
    not_an_object.write_text("[1, 2, 3]", encoding="utf-8")
    assert read_json_tolerant(not_an_object) is None

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    assert read_json_tolerant(empty) is None

    # a directory where a file is expected must not raise either
    (tmp_path / "adir.json").mkdir()
    assert read_json_tolerant(tmp_path / "adir.json") is None


# ------------------------------------------------------------ target discovery

def test_find_running_run_prefers_the_newest_running(tmp_path):
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="completed")
    write_live(runs / "20260102-000000-b", status="running")
    write_live(runs / "20260103-000000-c", status="failed")

    assert find_running_run(runs).name == "20260102-000000-b"
    assert resolve_watch_target(runs, "last").name == "20260102-000000-b"
    assert resolve_watch_target(runs, None).name == "20260102-000000-b"
    # an explicit id always wins over the running-run search
    assert resolve_watch_target(runs, "20260103").name == "20260103-000000-c"


def test_without_a_running_run_watch_falls_back_to_the_newest(tmp_path):
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="completed")
    write_live(runs / "20260102-000000-b", status="completed")

    assert find_running_run(runs) is None
    assert resolve_watch_target(runs, "last").name == "20260102-000000-b"
    assert resolve_watch_target(runs, "nope") is None
    assert resolve_watch_target(tmp_path / "empty", "last") is None


# ------------------------------------------------------------------ staleness
#
# Measured 2026-08-18: `aidev watch` attached to a run that had died two days
# earlier. A killed runner leaves live.json saying 'running' forever, so the
# status alone cannot answer "is anybody still working on this".


def write_run_json(run_dir: Path, repo: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(run_dir / "run.json", {"config": {"repo": str(repo)}})


def test_last_skips_a_run_nobody_has_updated(tmp_path):
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="completed")
    two_days_ago = time.time() - 2 * 24 * 3600
    write_live(runs / "20260102-000000-b", status="running", updated_at=two_days_ago)

    assert find_running_run(runs) is None
    # the finished run is worth more than one that only claims to be running
    assert resolve_watch_target(runs, "last").name == "20260101-000000-a"
    # and naming it is still allowed: an explicit id always wins
    assert resolve_watch_target(runs, "20260102").name == "20260102-000000-b"


def test_last_still_prefers_a_live_running_run(tmp_path):
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="running", updated_at=time.time() - 5)
    write_live(runs / "20260102-000000-b", status="completed")

    assert find_running_run(runs).name == "20260101-000000-a"
    assert resolve_watch_target(runs, "last").name == "20260101-000000-a"


def test_a_run_that_cannot_be_dated_is_not_called_dead(tmp_path):
    """Not knowing how old it is, is not the same as knowing it is dead."""
    runs = tmp_path / "runs"
    run_dir = runs / "20260101-000000-a"
    write_live(run_dir, status="running", updated_at="not a number")

    assert storage.live_age({"updated_at": None}) is None
    # the file's own mtime stands in, and it was written just now
    assert storage.is_stale(read_live(run_dir), run_dir) is False
    assert find_running_run(runs).name == "20260101-000000-a"


def test_only_stale_running_runs_are_still_offered(tmp_path):
    """Better a stale run with a warning than 'no run to watch' when there is one."""
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="running", updated_at=1000.0)

    assert find_running_run(runs) is None
    assert resolve_watch_target(runs, "last").name == "20260101-000000-a"


# ----------------------------------------------------------------- repo scope


def test_watch_scopes_last_to_a_repo(tmp_path):
    runs = tmp_path / "runs"
    mine, theirs = tmp_path / "jokertest", tmp_path / "someone-else"
    write_live(runs / "20260101-000000-a", status="running")
    write_run_json(runs / "20260101-000000-a", theirs)
    write_live(runs / "20260102-000000-b", status="running")
    write_run_json(runs / "20260102-000000-b", mine)

    assert resolve_watch_target(runs, "last", repo=mine).name == "20260102-000000-b"
    assert resolve_watch_target(runs, "last", repo=theirs).name == "20260101-000000-a"
    # unscoped is what it always was: the newest running run, whoever owns it
    assert resolve_watch_target(runs, "last").name == "20260102-000000-b"


def test_a_slice_worktree_run_counts_as_the_repos_own(tmp_path):
    """Pipeline stages record the worktree they ran in, not the user's checkout."""
    runs = tmp_path / "runs"
    repo = tmp_path / "jokertest"
    worktree = tmp_path / "jokertest-slices" / "20260818-doctor"
    write_live(runs / "20260101-000000-a", status="running")
    write_run_json(runs / "20260101-000000-a", worktree)

    assert resolve_watch_target(runs, "last", repo=repo).name == "20260101-000000-a"
    assert resolve_watch_target(runs, "last", repo=tmp_path / "elsewhere") is None


def test_a_run_that_never_said_where_it_ran_is_out_of_scope(tmp_path):
    """A run that cannot prove it belongs here is the one that got attached to."""
    runs = tmp_path / "runs"
    write_live(runs / "20260101-000000-a", status="running")

    assert storage.run_repo(runs / "20260101-000000-a") is None
    assert resolve_watch_target(runs, "last", repo=tmp_path / "jokertest") is None
    assert resolve_watch_target(runs, "last").name == "20260101-000000-a"


def test_run_repo_falls_back_to_telemetry(tmp_path):
    run_dir = tmp_path / "runs" / "20260101-000000-a"
    run_dir.mkdir(parents=True)
    write_json_atomic(run_dir / "telemetry.json", {"repo": "/w/jokertest"})

    assert storage.run_repo(run_dir) == "/w/jokertest"
    write_json_atomic(run_dir / "run.json", {"config": {"repo": "/w/other"}})
    assert storage.run_repo(run_dir) == "/w/other"  # run.json is written first and wins


# --------------------------------------------------------------------- frames

def test_watch_frame_marks_live_numbers_provisional(tmp_path):
    run_dir = tmp_path / "20260813-201113-doctor"
    write_live(run_dir)
    frame = "\n".join(reporter.render_watch(read_live(run_dir)))

    assert "AI DEV WATCH   20260813-201113-doctor" in frame
    assert "Tokens  EXACT, PROVISIONAL" in frame
    assert "1,000" in frame  # cache read, separated from the other fields
    assert "Turn      3" in frame
    assert "Elapsed   02:48" in frame
    assert "IMPLEMENT" in frame
    assert "doctor" in frame
    assert "Read src/gameSession.js" in frame
    assert "Read" in frame and "19" in frame
    assert "Repeated reads" in frame
    assert "Attribution (estimated)" in frame
    assert "Ctrl+C stops watching only" in frame


def test_watch_frame_switches_to_final_exact(tmp_path):
    run_dir = tmp_path / "run-final"
    write_live(run_dir, status="completed", usage_state="final", cost_usd=1.5)
    frame = "\n".join(reporter.render_watch(read_live(run_dir)))

    assert "Tokens  FINAL EXACT" in frame
    assert "PROVISIONAL" not in frame
    assert "$1.5000" in frame
    assert "Ctrl+C" not in frame  # the run is over; nothing to keep alive


def test_watch_frame_reports_staleness(tmp_path):
    run_dir = tmp_path / "run-stale"
    write_live(run_dir, updated_at=1000.0)
    live = read_live(run_dir)

    fresh = "\n".join(reporter.render_watch(live, now=1000.5))
    assert "updated  0.5s ago" in fresh

    stale = "\n".join(reporter.render_watch(live, now=1042.0))
    assert "STALE: no update for 42s" in stale

    unknown = "\n".join(reporter.render_watch({"run_id": "r", "status": "running"}))
    assert "updated  unknown" in unknown


# ----------------------------------------------------------------- watch loop

def test_watch_once_on_a_running_run(tmp_path, capsys):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "20260813-201113-doctor"
    write_live(run_dir)

    assert main(["--data-dir", str(data_dir), "watch", "--once"]) == 0
    out = capsys.readouterr().out
    assert "AI DEV WATCH" in out
    assert "PROVISIONAL" in out


def test_watch_survives_garbage_and_keeps_the_last_good_frame(tmp_path, monkeypatch, capsys):
    """A read landing mid-replace returns None; the watcher must not die."""
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-1"
    write_live(run_dir)
    good = read_live(run_dir)

    reads = [good, None, None, dict(good, turn=9), None, dict(good, status="completed", usage_state="final")]
    calls = {"n": 0}

    def fake_read_live(_run_dir):
        index = calls["n"]
        calls["n"] += 1
        return reads[index] if index < len(reads) else reads[-1]

    monkeypatch.setattr(cli, "read_live", fake_read_live)
    monkeypatch.setattr(cli, "read_telemetry", lambda _d: None)

    assert main(["--data-dir", str(data_dir), "watch", "run-1"]) == 0
    out = capsys.readouterr().out
    assert calls["n"] == len(reads)  # every read consumed, including the None ones
    assert "Turn      9" in out  # the frame after the two failed reads
    assert "run finished (completed)" in out


def test_watch_exits_after_the_run_completes_and_prints_the_final_report(
    tmp_path, filled_telemetry, capsys
):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-1"
    write_live(run_dir, status="completed", usage_state="final")
    write_json_atomic(run_dir / "telemetry.json", filled_telemetry.to_dict())

    assert main(["--data-dir", str(data_dir), "watch", "run-1"]) == 0
    out = capsys.readouterr().out
    assert "AI DEV WATCH" in out
    assert "TOKEN / CONTEXT HOTSPOTS" in out  # the final report follows the last frame
    assert "FINAL EXACT" in out


def test_watch_on_an_old_run_without_live_json(tmp_path, filled_telemetry, capsys):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-old"
    run_dir.mkdir(parents=True)
    write_json_atomic(run_dir / "telemetry.json", filled_telemetry.to_dict())

    assert main(["--data-dir", str(data_dir), "watch", "run-old"]) == 0
    assert "TOKEN / CONTEXT HOTSPOTS" in capsys.readouterr().out


def test_watch_waits_when_there_is_nothing_to_show_yet(tmp_path, capsys):
    data_dir = tmp_path / "data"
    (data_dir / "runs" / "run-empty").mkdir(parents=True)

    assert main(["--data-dir", str(data_dir), "watch", "run-empty", "--once"]) == 0
    assert "waiting for" in capsys.readouterr().out


def test_watch_without_any_run(tmp_path, capsys):
    assert main(["--data-dir", str(tmp_path / "data"), "watch"]) == 2
    assert "no run to watch" in capsys.readouterr().err


def test_watch_does_not_follow_a_dead_run_forever(tmp_path, monkeypatch, capsys):
    """The other half of the 2026-08-18 defect: attaching to it never ended."""
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-dead"
    write_live(run_dir, status="running", updated_at=time.time() - 2 * 24 * 3600)

    def never(_seconds):
        raise AssertionError("a run with no runner behind it must not be polled")

    monkeypatch.setattr(cli, "_sleep", never)
    assert main(["--data-dir", str(data_dir), "watch", "run-dead"]) == 0

    out = capsys.readouterr().out
    assert "STALE" in out and "no runner" in out
    assert "run finished" not in out  # it did not finish; nobody is there


def test_ctrl_c_stops_only_the_watcher(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-1"
    write_live(run_dir)

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep", interrupt)
    assert main(["--data-dir", str(data_dir), "watch", "run-1"]) == 130

    captured = capsys.readouterr()
    assert "The run itself is unaffected" in captured.err
    # the watcher never writes into the run directory
    assert sorted(p.name for p in run_dir.iterdir()) == ["live.json"]


def test_watch_follows_the_run_that_starts_after_this_one(tmp_path, monkeypatch, capsys):
    """A slice is many runs. Watching 'last' must follow the pipeline, not one stage."""
    data_dir = tmp_path / "data"
    runs = data_dir / "runs"
    first = runs / "20260819-100000-a"
    second = runs / "20260819-100100-b"
    write_live(first, status="completed", usage_state="final")

    steps = {"n": 0}

    def fake_sleep(_seconds):
        steps["n"] += 1
        if steps["n"] == 1:
            write_live(second, status="running")  # the next stage starts, a beat later
        else:
            write_live(second, status="completed", usage_state="final")

    monkeypatch.setattr(cli, "_sleep", fake_sleep)
    monkeypatch.setattr(cli, "read_telemetry", lambda _d: None)

    assert main(["--data-dir", str(data_dir), "watch"]) == 0

    out = capsys.readouterr().out
    assert "AI DEV WATCH   20260819-100000-a" in out
    assert "AI DEV WATCH   20260819-100100-b" in out  # it handed over
    assert out.rindex("20260819-100100-b") > out.rindex("20260819-100000-a")


def test_a_finished_run_with_no_successor_still_ends(tmp_path, monkeypatch, capsys):
    """The handoff is a few polls, not a new way to hang."""
    data_dir = tmp_path / "data"
    write_live(data_dir / "runs" / "20260819-100000-a", status="completed", usage_state="final")
    slept = []
    monkeypatch.setattr(cli, "_sleep", lambda seconds: slept.append(seconds) or None)
    monkeypatch.setattr(cli, "read_telemetry", lambda _d: None)

    assert main(["--data-dir", str(data_dir), "watch"]) == 0
    assert len(slept) == cli.HANDOFF_POLLS - 1  # bounded, and then it reports
    assert "run finished (completed)" in capsys.readouterr().out


def test_an_explicit_run_id_is_never_handed_over(tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    runs = data_dir / "runs"
    write_live(runs / "20260819-100000-a", status="completed", usage_state="final")
    write_live(runs / "20260819-100100-b", status="running")

    def never(_seconds):
        raise AssertionError("the run the user named is the run to watch")

    monkeypatch.setattr(cli, "_sleep", never)
    monkeypatch.setattr(cli, "read_telemetry", lambda _d: None)

    assert main(["--data-dir", str(data_dir), "watch", "20260819-100000-a"]) == 0
    assert "20260819-100100-b" not in capsys.readouterr().out


def test_next_run_only_offers_a_newer_live_run(tmp_path):
    runs = tmp_path / "runs"
    current = runs / "20260819-100000-a"
    write_live(current, status="running")
    assert cli.next_run(runs, current) is None  # itself is not its successor

    write_live(runs / "20260818-090000-old", status="running")
    assert cli.next_run(runs, current) is None  # older, however alive it looks

    write_live(runs / "20260819-110000-c", status="running", updated_at=time.time() - 2 * 24 * 3600)
    assert cli.next_run(runs, current) is None  # newer, but nobody is updating it

    successor = runs / "20260819-120000-d"
    write_live(successor, status="running")
    assert cli.next_run(runs, current) == successor


def test_watch_interval_is_clamped(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "run-1"
    write_live(run_dir)
    slept = []
    monkeypatch.setattr(cli, "_sleep", lambda seconds: slept.append(seconds) or None)

    reads = [read_live(run_dir), dict(read_live(run_dir), status="completed")]
    monkeypatch.setattr(cli, "read_live", lambda _d: reads[min(len(slept), 1)])
    monkeypatch.setattr(cli, "read_telemetry", lambda _d: None)

    main(["--data-dir", str(data_dir), "watch", "run-1", "--interval", "5"])
    assert slept == [0.5]

    slept.clear()
    main(["--data-dir", str(data_dir), "watch", "run-1", "--interval", "0.01"])
    assert slept == [0.3]


# ------------------------------------------------------------------ runner side

def test_run_publishes_live_json(tmp_path, capsys):
    """The runner is the only writer, and it leaves a FINAL live.json behind."""
    repo = tmp_path / "jokertest"
    (repo / "tasks").mkdir(parents=True)
    (repo / "tasks" / "doctor.md").write_text("do it", encoding="utf-8")

    if os.name == "nt":
        launcher = tmp_path / "claude.cmd"
        launcher.write_text('@echo off\r\n"{0}" "{1}" %*\r\n'.format(sys.executable, FAKE_CLAUDE))
    else:
        launcher = tmp_path / "claude.sh"
        launcher.write_text('#!/bin/sh\nexec "{0}" "{1}" "$@"\n'.format(sys.executable, FAKE_CLAUDE))
        launcher.chmod(0o755)

    data_dir = tmp_path / "data"
    assert (
        main(
            [
                "--data-dir", str(data_dir),
                "run",
                "--repo", str(repo),
                "--prompt", "tasks/doctor.md",
                "--claude-bin", str(launcher),
                "--no-live",
            ]
        )
        == 0
    )
    capsys.readouterr()

    run_dir = next((data_dir / "runs").iterdir())
    live = read_live(run_dir)
    assert live["status"] == "completed"
    assert live["usage_state"] == "final"
    assert live["tokens"]["cache_read"] == 2400
    assert live["attribution"]["total_tokens"] > 0
    assert not (run_dir / "live.json.tmp").exists()

    # and watching it afterwards renders the finished run
    assert main(["--data-dir", str(data_dir), "watch", run_dir.name]) == 0
    out = capsys.readouterr().out
    assert "FINAL EXACT" in out
    assert "TOKEN / CONTEXT HOTSPOTS" in out
