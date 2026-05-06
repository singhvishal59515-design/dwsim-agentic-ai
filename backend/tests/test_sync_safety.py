"""Sync-safety tests: mtime tracking, .lock files, stale reclaim, conflict detection."""
import os
import time


def test_load_captures_mtime(bridge, sample_flowsheet):
    r = bridge.load_flowsheet(sample_flowsheet)
    assert r["success"]
    assert r["mtime"] > 0
    assert abs(bridge.state.loaded_mtime
               - os.path.getmtime(sample_flowsheet)) < 0.5


def test_lock_blocks_load(bridge, sample_flowsheet):
    lock = sample_flowsheet + ".lock"
    with open(lock, "w", encoding="utf-8") as f:
        f.write("test-holder")
    try:
        r = bridge.load_flowsheet(sample_flowsheet)
        assert not r["success"]
        assert r.get("code") == "LOCKED"
        assert "locked" in r["error"].lower()
    finally:
        if os.path.exists(lock):
            os.remove(lock)


def test_external_mtime_drift_blocks_save(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    # Artificially advance mtime.
    future = bridge.state.loaded_mtime + 10.0
    os.utime(sample_flowsheet, (future, future))
    r = bridge.save_flowsheet()
    assert not r["success"]
    assert r.get("conflict") is True
    assert r.get("code") == "EXTERNAL_EDIT"


def test_force_save_overrides_mtime_conflict(bridge, sample_flowsheet):
    bridge.load_flowsheet(sample_flowsheet)
    future = bridge.state.loaded_mtime + 10.0
    os.utime(sample_flowsheet, (future, future))
    r = bridge.save_flowsheet(force=True)
    assert r["success"]
    assert r["mtime"] > 0
    # Lock file is cleaned up.
    assert not os.path.exists(sample_flowsheet + ".lock")


def test_stale_lock_old_mtime_reclaimed(bridge, sample_flowsheet):
    lock = sample_flowsheet + ".lock"
    with open(lock, "w", encoding="utf-8") as f:
        f.write("AI-bridge pid=999999999")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    r = bridge.load_flowsheet(sample_flowsheet)
    assert r["success"], r.get("error")
    assert not os.path.exists(lock)


def test_stale_lock_dead_pid_reclaimed(bridge, sample_flowsheet):
    lock = sample_flowsheet + ".lock"
    with open(lock, "w", encoding="utf-8") as f:
        f.write("AI-bridge pid=999999999")
    r = bridge.load_flowsheet(sample_flowsheet)
    assert r["success"], r.get("error")


def test_live_pid_lock_still_blocks(bridge, sample_flowsheet):
    lock = sample_flowsheet + ".lock"
    with open(lock, "w", encoding="utf-8") as f:
        f.write(f"test pid={os.getpid()}")
    try:
        r = bridge.load_flowsheet(sample_flowsheet)
        assert not r["success"]
        assert r.get("code") == "LOCKED"
    finally:
        if os.path.exists(lock):
            os.remove(lock)
