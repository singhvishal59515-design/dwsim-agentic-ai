"""
Tests for the thesis-evidence hardening of the benchmark runner:
honest live/mock mode detection, the per-task Markdown table, and persistence
of a run_all report into benchmark_results.json + eval_log.json.
"""
from __future__ import annotations
import json
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import benchmark_tasks as bt


class _MockBridge:
    def get_simulation_results(self):
        return {"success": True, "stream_results": {}}


class _MockAgent:
    def __init__(self):
        self.on_tool_call = None
        self.bridge = _MockBridge()

    def reset(self):
        pass

    def chat(self, prompt):
        if self.on_tool_call:
            self.on_tool_call("run_simulation", {}, {"success": True})
        return "done"


def test_bridge_mode_flags_mock():
    assert bt._bridge_mode(_MockAgent()) == "mock"
    assert bt._bridge_mode(object()) == "mock"


def test_render_results_table_has_rows_and_total():
    results = [
        {"benchmark_id": "t01", "category": "flash", "complexity": 1,
         "outcome": "SUCCESS", "passed": True, "tool_calls": 2,
         "duration_s": 3.1, "speedup_vs_human": 10.0, "notes": ""},
        {"benchmark_id": "t02", "category": "column", "complexity": 3,
         "outcome": "FAILURE_LOUD", "passed": False, "tool_calls": 0,
         "duration_s": 1.0, "speedup_vs_human": None, "notes": "boom\nx"},
    ]
    md = bt.render_results_table(results)
    assert "| t01 |" in md and "| t02 |" in md
    assert "TOTAL" in md and "1/2" in md      # one of two passed
    assert "\n" not in md.split("boom")[1][:3]  # newline scrubbed from notes


def test_run_all_records_mode_and_persists(tmp_path, monkeypatch):
    # Redirect persistence into a temp dir so the real logs are untouched.
    monkeypatch.setattr(bt, "_HERE", str(tmp_path))
    report = bt.run_all(_MockAgent(), task_ids=[bt.BENCHMARK_TASKS[0].task_id],
                        persist=True)
    assert report["mode"] == "mock"
    assert "ran_at" in report and report["summary"]["total"] == 1

    # Both artifacts written and parseable.
    bj = tmp_path / "benchmark_results.json"
    ev = tmp_path / "eval_log.json"
    assert bj.exists() and ev.exists()
    saved = json.loads(bj.read_text(encoding="utf-8"))
    assert saved["mode"] == "mock" and len(saved["results"]) == 1
    evlog = json.loads(ev.read_text(encoding="utf-8"))
    assert len(evlog["benchmark_results"]) == 1
    assert evlog["benchmark_summary"]["total"] == 1


def test_merge_results_keeps_latest_per_task():
    prior = [{"benchmark_id": "A", "passed": True},
             {"benchmark_id": "B", "passed": False}]
    fresh = [{"benchmark_id": "B", "passed": True}]   # re-ran only B
    merged = {r["benchmark_id"]: r for r in bt._merge_results(prior, fresh)}
    assert set(merged) == {"A", "B"}            # A preserved
    assert merged["B"]["passed"] is True        # B updated to the fresh result


def test_subset_persist_does_not_clobber_full_run(tmp_path, monkeypatch):
    # A full 3-task run, then a 1-task re-check must NOT discard the other two.
    monkeypatch.setattr(bt, "_HERE", str(tmp_path))
    ids = [t.task_id for t in bt.BENCHMARK_TASKS[:3]]
    bt.run_all(_MockAgent(), task_ids=ids, persist=True)
    bt.run_all(_MockAgent(), task_ids=[ids[0]], persist=True)   # subset re-run

    bj = json.loads((tmp_path / "benchmark_results.json").read_text(encoding="utf-8"))
    ev = json.loads((tmp_path / "eval_log.json").read_text(encoding="utf-8"))
    assert len(bj["results"]) == 3, "subset run clobbered the full-run results"
    assert len(ev["benchmark_results"]) == 3
    assert bj["summary"]["total"] == 3
