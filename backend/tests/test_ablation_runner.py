"""
Proves the ablation pipeline is FULLY WORKING end-to-end without LLM quota:
a mock agent → ablation_runner → ablation_logs/*.jsonl → ablation_report loader.

This is the no-quota guarantee: the mechanics (condition env, per-task scoring
via run_task, schema, error-recovery counting, log format) are correct, so a
real run only needs throughput, not more code.
"""
from __future__ import annotations
import json
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _FakeBridge:
    def get_simulation_results(self):
        return {"stream_results": {}}

    def reset(self):
        pass


class _FakeAgent:
    """Minimal stand-in matching what benchmark_tasks.run_task touches."""
    def __init__(self):
        self.bridge = _FakeBridge()
        self.on_tool_call = None

    def reset(self):
        pass

    def chat(self, prompt):
        # Simulate a tool trajectory with one failure that is then recovered,
        # so error_recovery_events must come out as 1.
        cb = self.on_tool_call
        if callable(cb):
            cb("new_flowsheet", {}, {"success": True})
            cb("set_stream_property", {}, {"success": False})
            cb("set_stream_property", {}, {"success": True})
        return "build complete; flowsheet converged"


def _read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def test_runner_emits_user_schema(tmp_path):
    import ablation_runner as R
    try:
        files = R.run_condition("A", _FakeAgent(), reps=1,
                                task_ids=["C1-T01", "C1-T02"],
                                log_dir=str(tmp_path))
        assert files and os.path.exists(files[0])
        rows = _read_jsonl(files[0])
        assert len(rows) == 2
        required = {"condition", "task_id", "category", "success",
                    "tool_calls", "wall_time_s", "error_recovery_events"}
        for r in rows:
            assert required <= set(r), r
            assert r["condition"] == "A"
            assert r["success"] in (0, 1, -1)
            assert r["tool_calls"] == 3
            assert r["error_recovery_events"] == 1   # one failure → later success
    finally:
        R._cleanup_env()


def test_condition_sets_the_right_env(tmp_path):
    import ablation_runner as R
    try:
        R.run_condition("D", _FakeAgent(), reps=1, task_ids=["C1-T01"],
                        log_dir=str(tmp_path))
        # condition D maps to the direct_llm toggle in ablation_config
        assert os.environ.get("DWSIM_ABLATION_CONDITION") == "direct_llm"
        from ablation_config import ablation
        assert ablation.direct_llm is True
    finally:
        R._cleanup_env()


def test_report_loader_consumes_runner_output(tmp_path, monkeypatch):
    import ablation_runner as R
    import ablation_report as REP
    try:
        # Two conditions so the report has groups to compare.
        R.run_condition("A", _FakeAgent(), reps=2, task_ids=["C1-T01", "C1-T02"],
                        log_dir=str(tmp_path))
        R.run_condition("D", _FakeAgent(), reps=2, task_ids=["C1-T01", "C1-T02"],
                        log_dir=str(tmp_path))
        monkeypatch.setattr(REP, "LOG_DIR", tmp_path)
        data = REP.load_results()
        assert set(data.keys()) >= {"A", "D"}
        # 2 tasks × 2 reps = 4 success entries per condition (none skipped here)
        assert len(data["A"]["success"]) == 4
        assert len(data["A"]["tool_calls"]) == 4
        assert all(tc == 3 for tc in data["A"]["tool_calls"])
    finally:
        R._cleanup_env()


def test_skip_records_minus_one(tmp_path, monkeypatch):
    # A task that requires a fixture with none available must log success = -1
    # (so it is excluded from the analysis, never depressing a condition).
    import ablation_runner as R
    import benchmark_tasks as BT

    # Find or synthesise a fixture-requiring task id.
    fixture_task = next((t for t in BT.BENCHMARK_TASKS
                         if getattr(t, "requires_fixture", False)), None)
    if fixture_task is None:
        import pytest
        pytest.skip("no fixture-requiring task in the suite")
    try:
        files = R.run_condition("A", _FakeAgent(), reps=1,
                                task_ids=[fixture_task.task_id],
                                log_dir=str(tmp_path))
        rows = _read_jsonl(files[0])
        assert rows[0]["success"] == -1
        assert rows[0]["skip_reason"]
    finally:
        R._cleanup_env()
