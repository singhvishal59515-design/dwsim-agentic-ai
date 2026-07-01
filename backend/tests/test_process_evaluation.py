"""
Tests for the deterministic multi-dimensional design rubric (Tian et al. 2026,
Eq. 1) in process_evaluation.py.

The rubric is a pure function of a `facts` dict — no DWSIM, no LLM — so its math,
the convergence penalty, the dimension derivations, and the suite aggregation
(which reproduces the paper's Table-1 arithmetic) are fully covered here.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# ── constants & weighted score ───────────────────────────────────────────────

def test_weights_sum_to_one():
    from process_evaluation import WEIGHTS, DIMENSIONS
    assert set(WEIGHTS) == set(DIMENSIONS)
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_weighted_score_bounds():
    from process_evaluation import weighted_dimension_score
    allone = {d: 1.0 for d in ("economic", "environmental", "safety",
                               "technical", "topological")}
    allhalf = {d: 0.5 for d in allone}
    assert abs(weighted_dimension_score(allone) - 1.0) < 1e-9
    assert abs(weighted_dimension_score(allhalf) - 0.5) < 1e-9


def test_weighted_score_respects_weighting():
    from process_evaluation import weighted_dimension_score, WEIGHTS
    # only economic perfect (w=0.35), rest zero → score == economic weight
    dims = {"economic": 1.0, "environmental": 0.0, "safety": 0.0,
            "technical": 0.0, "topological": 0.0}
    assert abs(weighted_dimension_score(dims) - WEIGHTS["economic"]) < 1e-9


# ── convergence penalty (λ) ──────────────────────────────────────────────────

def test_penalty_applied_only_when_failed():
    from process_evaluation import penalize, FAIL_PENALTY
    assert penalize(0.8, converged=True) == 0.8
    assert abs(penalize(0.8, converged=False) - FAIL_PENALTY * 0.8) < 1e-9


def test_score_design_penalizes_nonconverged():
    from process_evaluation import score_design, FAIL_PENALTY
    facts = {"converged": False, "dimensions":
             {"economic": 1, "environmental": 1, "safety": 1,
              "technical": 1, "topological": 1}}
    r = score_design(facts)
    assert r["weighted_score"] == 1.0
    assert abs(r["penalized_score"] - FAIL_PENALTY) < 1e-9
    assert r["converged"] is False


# ── dimension derivations ────────────────────────────────────────────────────

def test_economic_cheaper_than_baseline_scores_higher():
    from process_evaluation import derive_dimensions
    cheap = derive_dimensions({"tac": 50.0, "tac_baseline": 100.0})["economic"]
    dear = derive_dimensions({"tac": 200.0, "tac_baseline": 100.0})["economic"]
    assert cheap > dear


def test_safety_decreases_with_severity():
    from process_evaluation import derive_dimensions
    clean = derive_dimensions({"safety_violations": []})["safety"]
    one_silent = derive_dimensions(
        {"safety_violations": [{"severity": "SILENT"}]})["safety"]
    assert clean == 1.0
    assert one_silent < clean


def test_technical_penalizes_impossible_vapor_fraction():
    from process_evaluation import derive_dimensions
    good = derive_dimensions({"streams": [{"vapor_fraction": 0.4}]})["technical"]
    bad = derive_dimensions({"streams": [{"vapor_fraction": 1.7}]})["technical"]
    assert good == 1.0
    assert bad < good


def test_topological_penalizes_dangling_and_open_recycles():
    from process_evaluation import derive_dimensions
    full = derive_dimensions({"dangling_ports": 0, "open_recycles": 0})["topological"]
    broken = derive_dimensions({"dangling_ports": 2, "open_recycles": 1})["topological"]
    assert full == 1.0
    assert broken < full


def test_missing_signal_is_neutral():
    from process_evaluation import derive_dimensions, NEUTRAL
    dims = derive_dimensions({})  # no signals at all
    assert all(v == NEUTRAL for v in dims.values())


def test_explicit_dimension_overrides_heuristic():
    from process_evaluation import derive_dimensions
    dims = derive_dimensions({"tac": 50, "tac_baseline": 100,
                              "dimensions": {"economic": 0.123}})
    assert dims["economic"] == 0.123


# ── SCR & suite aggregation ──────────────────────────────────────────────────

def test_scr_none_on_empty_and_correct_fraction():
    from process_evaluation import simulation_convergence_rate
    assert simulation_convergence_rate([]) is None
    recs = [{"converged": True}, {"converged": True},
            {"converged": False}, {"converged": False}]
    assert simulation_convergence_rate(recs) == 50.0


def test_aggregate_reproduces_paper_arithmetic():
    """Overall S = mean penalised score = weighted·(SCR + λ·(1−SCR)) when all
    designs share the same dimensions — the closed form behind Tian et al. Table 1
    (dims ~0.73, SCR 23.4%, λ=0.3 → S ≈ 0.34)."""
    from process_evaluation import aggregate, FAIL_PENALTY
    dims = {"economic": 0.73, "environmental": 0.73, "safety": 0.73,
            "technical": 0.73, "topological": 0.73}
    # 1000 designs, 234 converged → SCR 23.4% (matches the paper's GPT-4o row)
    recs = ([{"converged": True, "dimensions": dims}] * 234 +
            [{"converged": False, "dimensions": dims}] * 766)
    agg = aggregate(recs)
    assert agg["scr_pct"] == 23.4
    scr = 0.234
    expected = 0.73 * (scr + FAIL_PENALTY * (1 - scr)) * 100
    assert abs(agg["overall_score_100"] - expected) < 0.5
    # sanity: ~34, far below the ~73 dimension mean, exactly as the paper shows
    assert 33.0 < agg["overall_score_100"] < 35.0
    assert abs(agg["dimension_means_100"]["economic"] - 73.0) < 0.01


def test_score_design_is_deterministic():
    from process_evaluation import score_design
    facts = {"converged": True, "tac": 80, "tac_baseline": 100,
             "safety_violations": [{"severity": "WARNING"}],
             "streams": [{"vapor_fraction": 0.3}],
             "dangling_ports": 0, "open_recycles": 0, "waste_fraction": 0.1}
    assert score_design(facts) == score_design(facts)
