"""
Phase-4 scaffolding tests: ablation statistics pipeline.

Validates the building blocks (Cohen's d, Holm correction) and the end-to-end
analyze() on synthetic data with a known structure (one condition clearly worse,
one metric with no real difference), plus the replay-JSONL loader.
"""
from __future__ import annotations
import json
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_cohens_d_sign_and_magnitude():
    from ablation_stats import cohens_d
    a = [10, 11, 9, 10, 10]
    b = [5, 6, 4, 5, 5]
    d = cohens_d(a, b)
    assert d is not None and d > 2.0          # large positive effect
    assert cohens_d(a, a) == 0.0              # identical groups → 0
    assert cohens_d([1], [2]) is None         # too few points


def test_holm_orders_and_thresholds():
    from ablation_stats import holm_bonferroni
    pairs = [{"p_raw": 0.001}, {"p_raw": 0.04}, {"p_raw": 0.5}]
    holm_bonferroni(pairs)
    # smallest p gets multiplied by m=3, etc.; monotone non-decreasing
    holms = [p["p_holm"] for p in pairs]
    assert abs(holms[0] - 0.003) < 1e-9
    assert holms[1] >= holms[0]
    assert pairs[0]["significant"] is True
    assert pairs[2]["significant"] is False


def _synthetic_records():
    # success: full/no_rag/no_safety good, direct_llm bad → omnibus significant.
    # tool_calls: the three tool-using conditions cluster low, direct_llm high
    # and tie-free (distinct values → exact Mann-Whitney, unambiguous after Holm
    # across all 6 pairs). wall_time: no real difference between conditions.
    n = 8
    succ = {"full": 1, "no_rag": 1, "no_safety": 1, "direct_llm": 0}
    base = {"full": 3, "no_rag": 4, "no_safety": 5, "direct_llm": 30}
    recs = []
    for cond in ("full", "no_rag", "no_safety", "direct_llm"):
        for i in range(n):
            recs.append({
                "condition": cond,
                "success": succ[cond],
                "tool_calls": base[cond] + i,            # distinct within group
                "wall_time_s": 10.0 + i * 0.01,          # same across conditions
            })
    return recs


def test_analyze_flags_real_difference_and_ignores_noise():
    from ablation_stats import analyze
    res = analyze(_synthetic_records(),
                  metrics=["success", "tool_calls", "wall_time_s"])
    assert res["conditions"] == ["direct_llm", "full", "no_rag", "no_safety"]

    # tool_calls: direct_llm is clearly different → omnibus significant, and the
    # full-vs-direct_llm pair survives Holm.
    tc = res["metrics"]["tool_calls"]
    assert tc["omnibus_significant"] is True
    fd = next(p for p in tc["pairwise"]
              if {p["a"], p["b"]} == {"full", "direct_llm"})
    assert fd["significant"] is True
    assert abs(fd["cohens_d"]) > 2.0

    # wall_time_s: no real difference → omnibus not significant.
    wt = res["metrics"]["wall_time_s"]
    assert wt["omnibus_significant"] is False


def test_load_records_from_replay(tmp_path):
    from ablation_stats import load_records_from_replay
    p = tmp_path / "replay.jsonl"
    rows = [
        {"condition": "full", "converged": True, "tool_sequence": ["a", "b"],
         "duration_s": 3.2, "llm_calls": 2, "sf_violations": []},
        {"condition": None, "converged": True, "tool_sequence": ["x"],
         "duration_s": 1.0, "llm_calls": 1, "sf_violations": []},  # untagged → skipped
        {"condition": "direct_llm", "converged": False, "tool_sequence": [],
         "duration_s": 0.5, "llm_calls": 1, "sf_violations": [{"code": "SF-1"}]},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    recs = load_records_from_replay(str(p))
    assert len(recs) == 2                       # untagged row dropped
    full = next(r for r in recs if r["condition"] == "full")
    assert full["success"] == 1 and full["tool_calls"] == 2
    direct = next(r for r in recs if r["condition"] == "direct_llm")
    assert direct["success"] == 0 and direct["safety_violations"] == 1


def test_frozen_tasks_match_code():
    # The committed task specs must match the in-code BENCHMARK_TASKS.
    import freeze_tasks
    assert freeze_tasks.check() == 0
