"""
Tests for the Eval-tab benchmark panel backend (benchmark_tasks.list_tasks /
run_task / get_results) — the feature that was previously broken end-to-end
(UI called /eval/benchmark* which didn't exist; backend imported list_tasks /
run_task which didn't exist either).

Uses a mock agent so no DWSIM / LLM is required.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import benchmark_tasks as bt


def test_list_tasks_shape():
    tasks = bt.list_tasks()
    assert len(tasks) == len(bt.BENCHMARK_TASKS) >= 1
    t = tasks[0]
    for key in ("id", "name", "category", "complexity", "difficulty",
                "tags", "human_time_min", "description", "n_criteria"):
        assert key in t, key
    assert t["difficulty"] in ("easy", "medium", "hard")
    assert isinstance(t["tags"], list)
    # Both consumers: Eval-tab reads `id`, Tasks-tab reads `task_id`.
    assert t["id"] == t["task_id"]


class _MockAgent:
    """Minimal agent: records the prompt, lets a callback fire, returns canned
    stream results so success criteria can be scored."""
    def __init__(self, stream_results=None, raise_on_chat=False):
        self.on_tool_call = None
        self._sr = stream_results or {}
        self._raise = raise_on_chat
        self.reset_called = False

        class _Bridge:
            def __init__(self, sr): self._sr = sr
            def get_simulation_results(self):
                return {"success": True, "stream_results": self._sr}
        self.bridge = _Bridge(self._sr)

    def reset(self):
        self.reset_called = True

    def chat(self, prompt):
        # Simulate one tool call so tool_calls is non-zero.
        if self.on_tool_call:
            self.on_tool_call("run_simulation", {}, {"success": True})
        if self._raise:
            raise RuntimeError("boom")
        return "Done. The flowsheet converged."


def test_run_task_unknown_id_is_graceful():
    out = bt.run_task("NOPE-T99", agent=_MockAgent())
    assert out["success"] is False
    assert out["passed"] is False
    assert out["benchmark_id"] == "NOPE-T99"


def test_run_task_returns_ui_envelope_and_caches():
    bt._LAST_RESULTS.clear()
    tid = bt.BENCHMARK_TASKS[0].task_id
    agent = _MockAgent(stream_results={})
    out = bt.run_task(tid, agent=agent)

    assert agent.reset_called is True            # isolation reset happened
    assert out["success"] is True
    assert out["benchmark_id"] == tid
    for key in ("passed", "outcome", "duration_s", "speedup_vs_human",
                "tool_calls", "convergence", "accuracy_checks", "notes",
                # aliases consumed by the second (Tasks-tab) UI:
                "time_s", "speedup_x", "human_time_min", "agent_response"):
        assert key in out, key
    assert out["tool_calls"] >= 1                # callback fired
    assert out["duration_s"] >= 0
    # Result is cached and surfaced by get_results().
    res = bt.get_results()
    assert res["total_runs"] == 1
    assert res["results"][0]["benchmark_id"] == tid
    assert res["pass_rate"] is not None


def test_run_task_agent_exception_is_caught():
    tid = bt.BENCHMARK_TASKS[0].task_id
    out = bt.run_task(tid, agent=_MockAgent(raise_on_chat=True))
    assert out["success"] is True          # the runner itself didn't crash
    assert out["passed"] is False
    assert out["outcome"] == "FAILURE_LOUD"
    assert "boom" in out["notes"]


def test_run_all_and_summarize():
    bt._LAST_RESULTS.clear()
    ids = [t.task_id for t in bt.BENCHMARK_TASKS[:3]]
    out = bt.run_all(_MockAgent(), task_ids=ids)
    assert out["success"] is True
    assert len(out["results"]) == 3
    s = out["summary"]
    assert s["total"] == 3
    assert 0 <= (s["pass_rate"] or 0) <= 100
    # Breakdowns are present and well-formed.
    assert s["by_category"] and s["by_complexity"]
    for v in s["by_category"].values():
        assert v["passed"] <= v["total"]


def test_run_task_includes_category_and_complexity():
    tid = bt.BENCHMARK_TASKS[0].task_id
    out = bt.run_task(tid, agent=_MockAgent())
    assert "category" in out and "complexity" in out


def test_get_results_empty_initially():
    bt._LAST_RESULTS.clear()
    res = bt.get_results()
    assert res["total_runs"] == 0
    assert res["results"] == []
    assert res["pass_rate"] is None
