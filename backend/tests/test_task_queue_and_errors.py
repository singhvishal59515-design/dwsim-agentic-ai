"""
Unit tests for task_queue and error_utils — pure Python, no DWSIM needed.
"""

from __future__ import annotations
import os
import sys
import time

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── task_queue ────────────────────────────────────────────────────────────

def test_task_queue_runs_fn_and_returns_result():
    from task_queue import TaskQueue
    q = TaskQueue(max_workers=2)
    tid = q.submit("doubler", lambda x: x * 2, 21)
    # poll up to 2 s
    for _ in range(40):
        info = q.get(tid)
        if info and info["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    info = q.get(tid)
    assert info["status"] == "done"
    assert info["result"] == 42


def test_task_queue_captures_exception_as_failed():
    from task_queue import TaskQueue
    q = TaskQueue(max_workers=2)
    def _boom():
        raise ValueError("kaboom")
    tid = q.submit("boom", _boom)
    for _ in range(40):
        info = q.get(tid)
        if info and info["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    info = q.get(tid)
    assert info["status"] == "failed"
    assert "kaboom" in (info["error"] or "")


def test_task_queue_get_missing_returns_none():
    from task_queue import TaskQueue
    q = TaskQueue()
    assert q.get("does_not_exist") is None


def test_task_queue_list_filters_by_status():
    from task_queue import TaskQueue
    q = TaskQueue(max_workers=2)
    q.submit("ok",   lambda: 1)
    q.submit("fail", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    time.sleep(0.5)
    listing = q.list_tasks(status_filter="done")
    assert listing["success"] is True
    assert all(t["status"] == "done" for t in listing["tasks"])


# ─── error_utils ───────────────────────────────────────────────────────────

def test_format_error_shape():
    from error_utils import format_error
    try:
        raise ValueError("bad input")
    except ValueError as e:
        body = format_error(e, module="api.test", error_code="INVALID_INPUT")
    for key in ("success", "error", "error_code", "module",
                "request_id", "exception_type", "trace_brief"):
        assert key in body, f"missing key {key}"
    assert body["success"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert body["exception_type"] == "ValueError"
    assert len(body["request_id"]) == 12


def test_classify_exception_maps_common_types():
    from error_utils import classify_exception
    assert classify_exception(ValueError("bad")) == "INVALID_INPUT"
    assert classify_exception(KeyError("missing")) == "MISSING_KEY"
    assert classify_exception(TypeError("nope")) == "TYPE_MISMATCH"
    assert classify_exception(FileNotFoundError("x")) == "NOT_FOUND"
    assert classify_exception(TimeoutError("slow")) == "TIMEOUT"
    assert classify_exception(NotImplementedError()) == "NOT_IMPLEMENTED"
    assert classify_exception(RuntimeError("did not converge")) == "CONVERGENCE_FAILED"


def test_format_error_extra_merges():
    from error_utils import format_error
    try:
        raise RuntimeError("x")
    except RuntimeError as e:
        body = format_error(e, extra={"stream_tag": "FEED", "step": "set_T"})
    assert body["stream_tag"] == "FEED"
    assert body["step"] == "set_T"
