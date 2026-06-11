"""
Tests for the objective-meaningfulness (hollow-objective) advisory gate.

Anchored on the real failure that motivated it (the Liquid-Liquid Extraction
run): "maximise EXTRACTED_PRODUCT.mass_flow" while FEED.mass_flow is a free
variable — the optimum just pegs the feed to its bound. Numerically valid,
engineering-hollow. The gate must flag that, and must NOT flag a genuine
intensive objective (recovery / purity / fraction).
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from objective_quality import assess_objective


def test_lle_mass_flow_objective_flagged_hollow():
    obj = {"type": "variable", "tag": "EXTRACTED_PRODUCT", "property": "mass_flow"}
    variables = [
        {"tag": "FEED", "property": "mass_flow", "lower": 100000, "upper": 150000},
        {"tag": "FEED", "property": "temperature", "lower": 15, "upper": 80},
    ]
    r = assess_objective(obj, variables, minimize=False)
    assert r["meaningful"] is False
    assert r["severity"] == "high"
    assert "extensive_objective_with_throughput_variable" in r["flags"]
    assert "recovery" in r["suggestion"].lower() or "purity" in r["suggestion"].lower()
    # The repair hook: the throughput variable to hold fixed is identified.
    assert r["throughput_vars"] == ["FEED.mass_flow"]
    assert len(r["throughput_var_specs"]) == 1


def test_heatduty_min_identifies_throughput_to_hold_fixed():
    """The exact screenshot case: minimise H-101.HeatDuty with Feed.mass_flow +
    Feed.temperature free. The fix holds mass_flow fixed and optimises
    temperature, so the optimum isn't a trivial feed-scaling."""
    obj = {"type": "variable", "tag": "H-101", "property": "heat_duty"}
    variables = [{"tag": "Feed", "property": "mass_flow", "lower": 1800, "upper": 3600},
                 {"tag": "Feed", "property": "temperature", "lower": 25, "upper": 75}]
    r = assess_objective(obj, variables, minimize=True)
    assert r["severity"] == "high"
    assert r["throughput_vars"] == ["Feed.mass_flow"]
    # Simulate the orchestrator's deterministic repair.
    tp = set(r["throughput_vars"])
    kept = [v for v in variables
            if f"{v['tag']}.{v['property']}" not in tp]
    assert [f"{v['tag']}.{v['property']}" for v in kept] == ["Feed.temperature"]


def test_no_throughput_vars_when_objective_ok():
    obj = {"type": "variable", "tag": "P", "property": "recovery"}
    r = assess_objective(obj, [{"tag": "Feed", "property": "temperature",
                                "lower": 1, "upper": 2}], minimize=False)
    assert r.get("throughput_vars", []) == []


def test_intensive_objective_is_ok():
    obj = {"type": "variable", "tag": "EXTRACTED_PRODUCT", "property": "mole_fraction_acetone"}
    variables = [
        {"tag": "FEED", "property": "mass_flow", "lower": 100000, "upper": 150000},
        {"tag": "SOLVENT", "property": "mass_flow", "lower": 50000, "upper": 120000},
    ]
    r = assess_objective(obj, variables, minimize=False)
    assert r["meaningful"] is True
    assert r["severity"] == "ok"


def test_recovery_objective_ok_even_with_flow_vars():
    obj = {"type": "variable", "tag": "PROD", "property": "acetone_recovery"}
    variables = [{"tag": "FEED", "property": "mass_flow", "lower": 1, "upper": 2}]
    r = assess_objective(obj, variables, minimize=False)
    assert r["meaningful"] is True


def test_objective_equals_decision_variable_flagged():
    obj = {"type": "variable", "tag": "FEED", "property": "temperature"}
    variables = [{"tag": "FEED", "property": "temperature", "lower": 15, "upper": 80}]
    r = assess_objective(obj, variables, minimize=False)
    assert r["meaningful"] is False
    assert r["severity"] == "high"
    assert "objective_is_decision_variable" in r["flags"]


def test_duty_objective_with_intensive_vars_soft_flag():
    # Minimise heater duty with a temperature var — extensive objective but no
    # throughput var, so it's a soft (low) advisory, still 'meaningful'.
    obj = {"type": "variable", "tag": "H-101", "property": "heat_duty"}
    variables = [{"tag": "FEED", "property": "temperature", "lower": 20, "upper": 90}]
    r = assess_objective(obj, variables, minimize=True)
    assert r["severity"] == "low"
    assert r["meaningful"] is True
    assert "extensive_objective" in r["flags"]


def test_expression_objective_assumed_intentional():
    obj = {"type": "expression", "expression": "0.9*purity - 0.1*duty"}
    r = assess_objective(obj, [{"tag": "X", "property": "mass_flow",
                                "lower": 1, "upper": 2}], minimize=False)
    assert r["meaningful"] is True
    assert "expression_objective" in r["flags"]


def test_minimise_extensive_with_throughput_also_hollow():
    # Minimising a product flow by starving the feed is equally hollow.
    obj = {"type": "variable", "tag": "PROD", "property": "molar_flow"}
    variables = [{"tag": "FEED", "property": "molar_flow", "lower": 1, "upper": 100}]
    r = assess_objective(obj, variables, minimize=True)
    assert r["meaningful"] is False
    assert r["severity"] == "high"
