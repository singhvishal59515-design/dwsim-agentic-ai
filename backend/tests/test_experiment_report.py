"""
Tests for experiment_report.py — the unified Tian-et-al.-style Experiment /
Ablation / Results report. The E-MCTS hyperparameter ablation runs live (no LLM);
the data-backed parts are checked against the committed artifacts when present.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_emcts_node_ablation_quality_and_cost_trend():
    from experiment_report import emcts_node_ablation
    rows = emcts_node_ablation(children=(2, 3, 4, 5), seeds=range(8))
    assert [r["children"] for r in rows] == [2, 3, 4, 5]
    # quality at ceiling: every setting reaches the optimum on all seeds
    for r in rows:
        assert r["solved"] == r["n_seeds"]
        assert r["mean_best"] >= 0.999
    # cost (evaluations) is non-decreasing in the branching factor
    evals = [r["mean_evals"] for r in rows]
    assert evals == sorted(evals)


def test_rubric_demo_reproduces_tian_table1():
    from experiment_report import rubric_demo
    d = rubric_demo()
    assert d["tian_table1_scr"] == 23.4
    assert 33.0 < d["tian_table1_overall"] < 35.0   # ≈ 34, their GPT-4o row
    assert abs(sum(d["weights"].values()) - 1.0) < 1e-9


def test_component_ablation_loads_five_conditions():
    from experiment_report import load_component_ablation
    comp = load_component_ablation()
    if comp is None:
        return  # artifact absent in this checkout
    assert len(comp) == 5
    full = next(c for c in comp if "Full" in c["condition"])
    llm_only = next(c for c in comp if "Direct LLM" in c["condition"])
    assert full["pass_rate"] >= llm_only["pass_rate"]   # removing tools never helps
    assert llm_only["pass_rate"] == 0.0


def test_benchmark_analysis_executed_split():
    from experiment_report import load_benchmark_analysis
    a = load_benchmark_analysis()
    if a is None:
        return
    assert a["total"] == 25
    assert a["executed_pass_rate_pct"] >= a["strict_pass_rate_pct"]


def test_markdown_renders_all_sections_and_writes():
    import experiment_report as er
    md = er.to_markdown()
    for section in ("Experimental setup", "A. E-MCTS hyperparameter ablation",
                    "B. Component-attribution ablation",
                    "C. End-to-end benchmark", "D. The five-dimension design rubric"):
        assert section in md
    assert er.main() == 0
    assert os.path.isfile(os.path.join(_B, "EXPERIMENT_RESULTS.md"))
