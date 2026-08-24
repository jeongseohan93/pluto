"""프로세스 신원: lock이 누구를 적어두는지, 죽은 pid를 어떻게 인수하는지, --stop이 언제 거부하는지.

The point of every test here is a refusal. A pid on its own is not an identity,
so the interesting cases are the ones where the pipeline is *asked* to act and
declines: a number that has been handed to another process, a lock written by an
older version, a run that shares its process group with someone's shell.

Fixtures come from test_pipeline unchanged, the way test_epic does it: --stop is
a new command on the same parser and the same records, not a second machinery.
"""

import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aidev import pipeline, procid
from aidev.cli import main

from test_pipeline import (  # noqa: F401  (pytest fixtures, used by name)
    argv,
    claude_bin,
    git_repo,
    log,
    repo,
)


SLICE_ID = "20260822-identity"


def record(repo_path, slice_id=SLICE_ID):
    """A slice record with its directory on disk, which is all a lock needs."""
    rec = pipeline.SliceRecord(pipeline.slices_root(repo_path), slice_id)
    rec.dir.mkdir(parents=True, exist_ok=True)
    return rec


def lock_of(repo_path, slice_id=SLICE_ID):
    return pipeline.SliceLock(record(repo_path, slice_id))


def sleeper(own_group=True):
    """A live child that does nothing for half a minute, optionally in a group of its own."""
    kwargs = {}
    if own_group and os.name != "nt":
        kwargs["start_new_session"] = True
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)


def reap(child):
    try:
        child.kill()
    except OSError:
        pass
    try:
        child.wait(timeout=10)
    except Exception:
        pass


def dead_pid():
    """A pid that has certainly finished and been accounted for."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# ------------------------------------------------------------------ 신원 기록


def test_this_process_can_read_its_own_start_time():
    first = procid.process_start(os.getpid())
    assert first is not None
    assert procid.process_start(os.getpid()) == first


def test_a_pid_that_cannot_exist_is_not_alive():
    assert procid.process_alive(0) is False
    assert procid.process_alive(-1) is False
    assert procid.process_start(0) is None


def test_a_reaped_child_is_not_alive():
    assert procid.process_alive(dead_pid()) is False


def test_a_lock_records_the_identity_of_the_process_that_took_it(repo):
    lock = lock_of(repo)
    with lock:
        written = json.loads(lock.path.read_text(encoding="utf-8"))

    assert written["pid"] == os.getpid()
    assert written["start"] == procid.process_start(os.getpid())
    assert written["label"] == "slice {0}".format(SLICE_ID)
    assert written["repo"] == procid.normalise_repo(repo)
    if os.name == "nt":
        assert written["pgid"] is None
    else:
        assert written["pgid"] == os.getpgid(os.getpid())
    assert not lock.path.exists()  # released on the way out


def test_an_unreadable_lock_is_not_a_crash(repo, tmp_path):
    assert procid.decode("pid 12\nsince yesterday\n") is None
    assert procid.decode("") is None
    assert procid.decode('{"schema": 99, "pid": 12}') is None
    assert procid.inspect(tmp_path / "nothing" / ".lock").status == procid.ABSENT


# ------------------------------------------------------------------ 인수와 거부


def test_the_same_lock_is_refused_while_its_owner_lives(repo):
    lock = lock_of(repo)
    with lock:
        assert procid.inspect(lock.path, lock.identity).status == procid.LIVE
        with pytest.raises(pipeline.PipelineError) as caught:
            with lock_of(repo):
                pass
    assert "already running" in str(caught.value)


def test_a_reused_pid_is_taken_over(repo, capsys):
    lock = lock_of(repo)
    # Same pid as us, born at a different moment: exactly what a recycled number
    # looks like, and the only part of it that can be staged in a test.
    impostor = dataclasses.replace(lock.identity, start="0", since="2026-01-01T00:00:00+09:00")
    lock.path.write_text(procid.encode(impostor), encoding="utf-8")

    assert procid.inspect(lock.path, lock.identity).status == procid.STALE
    with lock_of(repo) as taken:
        written = json.loads(taken.path.read_text(encoding="utf-8"))

    assert written["start"] == procid.process_start(os.getpid())
    assert "taking over" in capsys.readouterr().out


def test_a_dead_owner_is_taken_over(repo, capsys):
    lock = lock_of(repo)
    gone = dataclasses.replace(lock.identity, pid=dead_pid(), start="0", pgid=None)
    lock.path.write_text(procid.encode(gone), encoding="utf-8")

    assert procid.inspect(lock.path, lock.identity).status == procid.STALE
    with lock_of(repo):
        pass
    assert "taking over" in capsys.readouterr().out


def test_a_lock_for_another_record_is_never_taken_over(repo):
    lock = lock_of(repo)
    other = dataclasses.replace(lock.identity, pid=dead_pid(), label="slice 20260101-other")
    lock.path.write_text(procid.encode(other), encoding="utf-8")

    assert procid.inspect(lock.path, lock.identity).status == procid.FOREIGN
    with pytest.raises(pipeline.PipelineError):
        with lock_of(repo):
            pass
    assert procid.decode(lock.path.read_text(encoding="utf-8")).label == "slice 20260101-other"


def test_a_legacy_lock_is_never_taken_over(repo):
    lock = lock_of(repo)
    legacy = "pid 1\nsince 2026-01-01T00:00:00+09:00\n"
    lock.path.write_text(legacy, encoding="utf-8")

    assert procid.inspect(lock.path, lock.identity).status == procid.LEGACY
    with pytest.raises(pipeline.PipelineError) as caught:
        with lock_of(repo):
            pass

    message = str(caught.value)
    assert "older format" in message
    assert str(lock.path) in message
    assert lock.path.read_text(encoding="utf-8") == legacy  # untouched


def test_an_empty_lock_is_not_taken_over(repo):
    lock = lock_of(repo)
    lock.path.write_text("", encoding="utf-8")

    assert procid.inspect(lock.path, lock.identity).status == procid.UNKNOWN
    with pytest.raises(pipeline.PipelineError):
        with lock_of(repo):
            pass


# ------------------------------------------------------------------ --stop


@pytest.fixture
def kills(monkeypatch):
    """Every termination this process would have performed, recorded instead of done."""
    seen = []
    monkeypatch.setattr(procid, "_killpg", lambda pgid, sig: seen.append(("killpg", pgid, sig)))
    monkeypatch.setattr(
        procid,
        "_taskkill",
        lambda pid: seen.append(("taskkill", pid)) or subprocess.CompletedProcess([], 0, "", ""),
    )
    return seen


def hold(repo_path, child, pgid=None):
    """Write a lock that truthfully names a live child, as that child would have written it."""
    lock = lock_of(repo_path)
    identity = dataclasses.replace(
        lock.identity,
        pid=child.pid,
        start=procid.process_start(child.pid),
        pgid=child.pid if pgid is None and os.name != "nt" else pgid,
    )
    lock.path.write_text(procid.encode(identity), encoding="utf-8")
    return lock


def test_stop_says_nothing_is_running_without_a_lock(repo, tmp_path, claude_bin, kills, capsys):
    record(repo)
    assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 0
    assert "not running" in capsys.readouterr().out
    assert kills == []


def test_stop_terminates_on_a_full_identity_match(repo, tmp_path, claude_bin, kills, capsys):
    child = sleeper()
    try:
        lock = hold(repo, child)
        assert procid.inspect(lock.path, lock.identity).status == procid.LIVE
        assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 0
    finally:
        reap(child)

    if os.name == "nt":
        assert kills == [("taskkill", child.pid)]
    else:
        import signal

        assert kills == [("killpg", child.pid, signal.SIGTERM)]
    assert "stopped slice" in capsys.readouterr().out
    assert lock.path.exists()  # the dying process removes it, not us


def test_stop_refuses_a_reused_pid(repo, tmp_path, claude_bin, kills, capsys):
    child = sleeper()
    try:
        lock = hold(repo, child)
        recorded = procid.decode(lock.path.read_text(encoding="utf-8"))
        lock.path.write_text(
            procid.encode(dataclasses.replace(recorded, start="0")), encoding="utf-8"
        )

        assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 0
        assert kills == []
        out = capsys.readouterr().out
        assert "not running" in out
        assert "different moment" in out

        # ...and the same lock is the next run's to take over
        with lock_of(repo):
            pass
    finally:
        reap(child)


def test_stop_refuses_a_legacy_lock(repo, tmp_path, claude_bin, kills, capsys):
    lock = lock_of(repo)
    legacy = "pid {0}\nsince 2026-01-01T00:00:00+09:00\n".format(os.getpid())
    lock.path.write_text(legacy, encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 2
    assert kills == []
    assert "older format" in capsys.readouterr().err
    assert lock.path.read_text(encoding="utf-8") == legacy


def test_stop_refuses_a_lock_it_cannot_judge(repo, tmp_path, claude_bin, kills, capsys):
    lock = lock_of(repo)
    # A live pid recorded without a creation time: the one thing that tells it
    # apart from a stranger who inherited the number is missing.
    blind = dataclasses.replace(lock.identity, start=None)
    lock.path.write_text(procid.encode(blind), encoding="utf-8")

    assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 2
    assert kills == []
    assert "never recorded a creation time" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
def test_stop_refuses_a_process_that_does_not_lead_its_group(
    repo, tmp_path, claude_bin, kills, capsys
):
    child = sleeper(own_group=False)
    try:
        # Started in our group, not its own: signalling that group would reach
        # this test runner and whatever shell launched it.
        hold(repo, child, pgid=os.getpgid(child.pid))
        assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 2
    finally:
        reap(child)

    assert kills == []
    assert "process group of its own" in capsys.readouterr().err


def test_stop_cannot_be_combined_with_another_command(repo, tmp_path, claude_bin, capsys):
    record(repo)
    assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID, "--merge", SLICE_ID)) == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_stop_really_ends_a_detached_child(repo, tmp_path, claude_bin, capsys):
    child = sleeper()
    try:
        hold(repo, child)
        assert main(argv(repo, tmp_path, claude_bin, "--stop", SLICE_ID)) == 0
        deadline = time.time() + 10
        while time.time() < deadline and child.poll() is None:
            time.sleep(0.1)
        assert child.poll() is not None, "the child outlived --stop"
    finally:
        reap(child)
