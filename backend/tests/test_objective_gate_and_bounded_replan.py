"""
Tests for the two judgment-enhancing mechanisms added on top of the
single-shot replanner:

  • Objective-confidence gate  — catches a *readable but wrong* objective and
    swaps it to a better-matching observable BEFORE wasting a full run.
  • Bounded multi-step replanning — `replan_on_failure(history=...)` reasons
    cumulatively and never re-issues a plan it already tried.

No DWSIM needed; uses a mock bridge + mock LLM.
"""
from __future__ import annotations
import json, os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _Bridge:
    """Water-only flowsheet: Feed stream + H-101 heater (HeatDuty readable)."""
    def list_compounds(self):
        return {"success": True, "compounds": ["Water"]}
    def list_simulation_objects(self):
        return {"success": True, "objects": [
            {"tag": "Feed", "type": "MaterialStream", "category": "stream"},
            {"tag": "Product", "type": "MaterialStream", "category": "stream"},
            {"tag": "H-101", "type": "Heater", "category": "unit_op"},
        ]}
    def get_stream_property(self, tag, prop):
        vals = {
            ("Feed", "temperature"): 283.15,
            ("Product", "temperature"): 343.15,
            ("H-101", "HeatDuty"): 293.0,
        }
        v = vals.get((tag, prop))
        return {"success": v is not None, "value": v}
    def get_stream_properties(self, tag):
        base = {"Feed": {"temperature_C": 10.0, "mass_flow_kgh": 3600.0,
                         "pressure_bar": 1.0},
                "Product": {"temperature_C": 70.0, "mass_flow_kgh": 3600.0,
                            "pressure_bar": 1.0}}
        return {"success": tag in base, "properties": base.get(tag, {})}
    def get_unit_op_properties(self, tag):
        return {"success": True, "properties": {}}


# ── Objective-confidence gate ─────────────────────────────────────────────

def test_score_flags_composition_objective_for_energy_goal():
    from optimization_orchestrator import _score_objective_match
    score, reason = _score_objective_match(
        "minimise total energy consumption",
        {"type": "variable", "tag": "Product", "property": "mole_fraction_H2"})
    assert score < 0.5, reason


def test_score_accepts_matching_objective():
    from optimization_orchestrator import _score_objective_match
    score, _ = _score_objective_match(
        "minimise heater energy duty",
        {"type": "variable", "tag": "H-101", "property": "HeatDuty"})
    assert score >= 0.5


def test_score_trusts_objective_when_goal_has_no_keyword():
    from optimization_orchestrator import _score_objective_match
    score, _ = _score_objective_match(
        "make it better",
        {"type": "variable", "tag": "H-101", "property": "HeatDuty"})
    assert score >= 0.5   # neutral — must not intervene on a vague goal


def test_gate_swaps_wrong_objective_to_heatduty():
    """Goal is energy; objective wrongly set to a composition → gate corrects
    it deterministically to the readable HeatDuty observable."""
    from optimization_orchestrator import _objective_confidence_gate
    spec = {
        "success": True,
        "objective": {"type": "variable", "tag": "Product",
                      "property": "mole_fraction_Water"},
        "variables": [{"tag": "Feed", "property": "temperature",
                       "unit": "C", "lower": 5, "upper": 50}],
        "method": "simplex", "minimize": True,
    }
    new_spec, info = _objective_confidence_gate(
        _Bridge(), "minimise energy consumption", spec, llm=None,
        emit=lambda *a: None)
    assert info.get("corrected") is True
    assert new_spec["objective"]["property"] == "HeatDuty"


def test_gate_leaves_good_objective_untouched():
    from optimization_orchestrator import _objective_confidence_gate
    spec = {
        "success": True,
        "objective": {"type": "variable", "tag": "H-101", "property": "HeatDuty"},
        "variables": [{"tag": "Feed", "property": "temperature",
                       "unit": "C", "lower": 5, "upper": 50}],
        "method": "simplex", "minimize": True,
    }
    new_spec, info = _objective_confidence_gate(
        _Bridge(), "minimise heater duty", spec, llm=None, emit=lambda *a: None)
    assert not info.get("corrected")
    assert new_spec["objective"]["property"] == "HeatDuty"


def test_gate_skips_already_replanned_spec():
    from optimization_orchestrator import _objective_confidence_gate
    spec = {"success": True, "_replanned": True,
            "objective": {"type": "variable", "tag": "Product",
                          "property": "mole_fraction_H2"},
            "variables": [], "method": "simplex", "minimize": True}
    new_spec, info = _objective_confidence_gate(
        _Bridge(), "minimise energy", spec, llm=None, emit=lambda *a: None)
    assert info.get("skipped") is True
    assert new_spec is spec   # unchanged


# ── Bounded multi-step replanning ─────────────────────────────────────────

def test_replan_history_passed_and_avoids_repeat():
    """The LLM replanner must receive the ALREADY_TRIED block so it can avoid
    repeating a failed plan."""
    from adaptive_replanner import replan_on_failure
    seen = {}
    class _LLM:
        def chat(self, messages, tools=None, system_prompt=""):
            seen["user"] = messages[-1]["content"]
            return {"content": json.dumps({
                "diagnosis": "tight bounds",
                "feasible": True,
                "new_plan": {
                    "objective": {"type": "variable", "tag": "H-101",
                                  "property": "HeatDuty"},
                    "variables": [{"tag": "Feed", "property": "temperature",
                                   "unit": "C", "lower": -20, "upper": 90}],
                    "method": "de", "minimize": True,
                    "rationale": "widen + global"}})}
    history = [{"objective": {"tag": "H-101", "property": "HeatDuty"},
                "method": "simplex", "error": "did not converge"}]
    r = replan_on_failure(
        _Bridge(), _LLM(), "minimise heater duty",
        {"objective": {"type": "variable", "tag": "H-101", "property": "HeatDuty"},
         "variables": [], "method": "simplex"},
        {"error_code": "RUN_FAILED", "error": "did not converge"},
        history=history)
    assert r["replanned"] is True
    assert "ALREADY_TRIED" in seen["user"]
    assert "simplex" in seen["user"]


def test_heuristic_progressive_widening_and_method_rotation():
    """Each heuristic replan, given more history, widens further and rotates to
    a solver method it has not tried yet."""
    from adaptive_replanner import replan_on_failure
    base_spec = {
        "objective": {"type": "variable", "tag": "H-101", "property": "HeatDuty"},
        "variables": [{"tag": "Feed", "property": "temperature",
                       "unit": "C", "lower": 9, "upper": 11}],
        "method": "simplex", "minimize": True}
    # First retry: no history → method 'de', ±50%
    r1 = replan_on_failure(_Bridge(), None, "minimise heater duty", base_spec,
                           {"error_code": "RUN_FAILED",
                            "result": {"best_objective": 5.0}}, history=[])
    assert r1["replanned"] is True
    assert r1["new_spec"]["method"] == "de"
    v1 = r1["new_spec"]["variables"][0]
    assert v1["lower"] < 9 and v1["upper"] > 11

    # Second retry: 'de' already tried → must rotate to a different method
    hist = [{"objective": base_spec["objective"], "method": "de",
             "error": "max iterations"}]
    r2 = replan_on_failure(_Bridge(), None, "minimise heater duty", base_spec,
                           {"error_code": "RUN_FAILED",
                            "result": {"best_objective": 5.0}}, history=hist)
    assert r2["new_spec"]["method"] != "de"
    v2 = r2["new_spec"]["variables"][0]
    # Wider than the first attempt (progressive widening)
    assert v2["lower"] <= v1["lower"] and v2["upper"] >= v1["upper"]
