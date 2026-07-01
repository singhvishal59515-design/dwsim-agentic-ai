"""
Tests for baseline_comparison.py — the Tian-Table-1-style multi-method comparison.
The real ablation-sourced rows, the generic live runner (with mock methods scored
on the 5-dimension rubric + SCR), and the rendering are covered without LLM quota.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_real_rows_use_live_benchmark_not_the_smoke_run():
    from baseline_comparison import real_method_rows
    rows = real_method_rows()
    names = {r.name for r in rows}
    assert any("Full agentic system" in n for n in names)
    assert any("Direct LLM" in n for n in names)
    full = next(r for r in rows if "Full agentic" in r.name)
    direct = next(r for r in rows if "Direct LLM" in r.name)
    # the full-system row must be the REAL live-benchmark rate (24), never the
    # smoke-run ablation figure (68)
    assert full.pass_rate_pct != 68.0
    assert full.source == "measured"
    assert direct.pass_rate_pct == 0.0
    assert direct.source == "structural"


def test_external_placeholders_are_marked_not_evaluated():
    from baseline_comparison import external_baseline_placeholders
    ph = external_baseline_placeholders()
    assert len(ph) >= 3
    assert all(p.source == "not_evaluated" and p.note for p in ph)
    assert all(p.pass_rate_pct is None for p in ph)


def test_live_runner_scores_rubric_and_scr():
    from baseline_comparison import run_live_comparison, RunResult

    good_dims = {"economic": 0.9, "environmental": 0.9, "safety": 0.9,
                 "technical": 0.9, "topological": 0.9}

    def strong(task):
        return RunResult(task_id=task["id"], passed=True, converged=True,
                         tool_calls=5, time_s=40.0,
                         design_facts={"dimensions": good_dims})

    def weak(task):
        return RunResult(task_id=task["id"], passed=False, converged=False,
                         tool_calls=0, time_s=2.0)

    tasks = [{"id": f"T{i}"} for i in range(4)]
    rows = run_live_comparison({"Strong": strong, "Weak": weak}, tasks)
    by = {r.name: r for r in rows}
    assert by["Strong"].pass_rate_pct == 100.0
    assert by["Strong"].scr_pct == 100.0
    assert by["Strong"].dims_100 is not None          # rubric scored
    assert by["Strong"].mean_time_s == 40.0
    assert by["Weak"].pass_rate_pct == 0.0
    assert by["Weak"].scr_pct == 0.0
    assert by["Weak"].dims_100 is None                # no design facts -> no rubric


def test_markdown_renders_and_writes():
    import baseline_comparison as bc
    rows = bc.compare()
    md = bc.to_markdown(rows)
    assert "Baseline Comparison" in md
    assert "Not evaluated, and why" in md
    assert bc.main() == 0
    assert os.path.isfile(os.path.join(_B, "BASELINE_COMPARISON.md"))
