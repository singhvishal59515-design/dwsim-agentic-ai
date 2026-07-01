"""
Tests for benchmark_error_analysis.py — the deterministic failure-mode attribution
over a completed benchmark run. Pure function of the result records, so fully
covered without an LLM or a re-run.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_classify_each_mode():
    from benchmark_error_analysis import classify
    assert classify({"passed": True, "tool_calls": 5}) == "PASS"
    assert classify({"passed": False, "tool_calls": 0}) == "NOT_EXECUTED"
    assert classify({"passed": False, "tool_calls": 10,
                     "outcome": "PARTIAL"}) == "PARTIAL_NEAR_MISS"
    assert classify({"passed": False, "tool_calls": 1,
                     "outcome": "FAILURE_LOUD"}) == "EARLY_ABORT"
    assert classify({"passed": False, "tool_calls": 9,
                     "outcome": "FAILURE_LOUD"}) == "EXECUTED_FAILURE"


def test_not_executed_excluded_from_executed_rate():
    from benchmark_error_analysis import analyze
    results = [
        {"benchmark_id": "A", "passed": True, "tool_calls": 3, "outcome": "SUCCESS"},
        {"benchmark_id": "B", "passed": False, "tool_calls": 0,
         "outcome": "FAILURE_LOUD"},   # never ran
        {"benchmark_id": "C", "passed": False, "tool_calls": 0,
         "outcome": "FAILURE_LOUD"},   # never ran
        {"benchmark_id": "D", "passed": False, "tool_calls": 8,
         "outcome": "FAILURE_LOUD"},
    ]
    a = analyze(results)
    assert a["total"] == 4
    assert a["not_executed"] == 2
    assert a["executed"] == 2
    assert a["passed"] == 1
    assert a["strict_pass_rate_pct"] == 25.0          # 1/4
    assert a["executed_pass_rate_pct"] == 50.0        # 1/2 (excludes the 2 non-exec)


def test_convergence_caveat_detected():
    from benchmark_error_analysis import analyze
    # a non-executed task still flagged converged → field is untrustworthy
    results = [{"benchmark_id": "X", "passed": False, "tool_calls": 0,
                "convergence": True, "outcome": "FAILURE_LOUD"}]
    assert analyze(results)["convergence_field_trustworthy"] is False
    # clean run (executed, converged) → trustworthy
    results2 = [{"benchmark_id": "Y", "passed": True, "tool_calls": 4,
                 "convergence": True, "outcome": "SUCCESS"}]
    assert analyze(results2)["convergence_field_trustworthy"] is True


def test_real_run_reproduces_paper_split():
    """On the committed 25-task run, the analysis must reproduce the paper's
    headline: 6/25 strict, 19 executed, 6 inconclusive."""
    import json
    p = os.path.join(_B, "benchmark_results.json")
    if not os.path.isfile(p):
        return  # artifact not present in this checkout
    from benchmark_error_analysis import analyze
    data = json.load(open(p, encoding="utf-8"))
    a = analyze(data["results"])
    assert a["total"] == 25
    assert a["passed"] == 6
    assert a["not_executed"] == 6
    assert a["executed"] == 19
    assert a["strict_pass_rate_pct"] == 24.0
    assert a["executed_pass_rate_pct"] == 31.6


def test_markdown_renders():
    from benchmark_error_analysis import analyze, to_markdown
    md = to_markdown(analyze([
        {"benchmark_id": "A", "passed": True, "tool_calls": 3, "outcome": "SUCCESS",
         "category": "x", "complexity": 1},
        {"benchmark_id": "B", "passed": False, "tool_calls": 0,
         "outcome": "FAILURE_LOUD", "category": "y", "complexity": 2},
    ]))
    assert "Benchmark Error Analysis" in md
    assert "NOT_EXECUTED" in md
