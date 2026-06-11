"""
Tests for adaptive_replanner — genuine LLM-in-the-loop reasoning on failure.
No DWSIM needed; uses mock bridge + mock LLM.
"""
from __future__ import annotations
import json, os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _Bridge:
    def list_compounds(self):
        return {"success": True, "compounds": ["Water"]}
    def list_simulation_objects(self):
        return {"success": True, "objects": [
            {"tag": "Feed", "type": "MaterialStream", "category": "stream"},
            {"tag": "H-101", "type": "Heater", "category": "unit_op"},
        ]}
    def get_stream_property(self, tag, prop):
        vals = {("Feed", "temperature"): 283.15, ("H-101", "HeatDuty"): 293.0}
        v = vals.get((tag, prop))
        return {"success": v is not None, "value": v}
    def get_stream_properties(self, tag):
        if tag == "Feed":
            return {"success": True, "properties": {
                "temperature_C": 10.0, "mass_flow_kgh": 3600.0,
                "pressure_bar": 1.0}}
        return {"success": True, "properties": {}}
    def get_unit_op_properties(self, tag):
        return {"success": True, "properties": {}}


def test_llm_declares_infeasible():
    from adaptive_replanner import replan_on_failure
    class _LLM:
        def chat(self, messages, tools=None, system_prompt=""):
            return {"content": json.dumps({
                "diagnosis": "no H2 in flowsheet",
                "feasible": False,
                "infeasible_reason": "Flowsheet contains only Water."})}
    r = replan_on_failure(
        _Bridge(), _LLM(), "maximise H2 purity",
        {"objective": {"type": "variable", "tag": "P", "property": "mole_fraction_H2"},
         "variables": []},
        {"error_code": "OBJECTIVE_NOT_MEASURABLE"})
    assert r["replanned"] is False
    assert r["feasible"] is False
    assert "Water" in r["infeasible_reason"]


def test_llm_proposes_new_plan():
    from adaptive_replanner import replan_on_failure
    class _LLM:
        def chat(self, messages, tools=None, system_prompt=""):
            return {"content": json.dumps({
                "diagnosis": "objective unreadable; use heater duty",
                "feasible": True,
                "new_plan": {
                    "objective": {"type": "variable", "tag": "H-101",
                                  "property": "HeatDuty"},
                    "variables": [{"tag": "Feed", "property": "temperature",
                                   "unit": "C", "lower": 5, "upper": 50}],
                    "method": "simplex", "minimize": True,
                    "rationale": "HeatDuty is measurable."}})}
    r = replan_on_failure(
        _Bridge(), _LLM(), "minimise energy",
        {"objective": {"type": "variable", "tag": "X", "property": "bad"},
         "variables": []},
        {"error_code": "OBJECTIVE_NOT_MEASURABLE"})
    assert r["replanned"] is True
    assert r["new_spec"]["objective"]["property"] == "HeatDuty"
    assert r["_via"] == "llm"


def test_heuristic_replan_without_llm():
    """When no LLM, the heuristic must still produce an alternative plan."""
    from adaptive_replanner import replan_on_failure
    r = replan_on_failure(
        _Bridge(), None, "minimise heater duty",
        {"objective": {"type": "variable", "tag": "P", "property": "mole_fraction_H2"},
         "variables": [{"tag": "Feed", "property": "temperature",
                        "unit": "C", "lower": 9, "upper": 11}]},
        {"error_code": "OBJECTIVE_NOT_MEASURABLE", "best_objective": None})
    assert r["replanned"] is True
    assert r["_via"] == "heuristic"
    # Should switch to a readable observable (heater duty present)
    assert r["new_spec"]["objective"]["type"] == "variable"


def test_replan_context_only_includes_readable_observables():
    from adaptive_replanner import _build_flowsheet_context
    ctx = _build_flowsheet_context(_Bridge())
    assert "Water" in ctx["compounds"]
    # Feed.temperature_C is readable; a nonexistent compound fraction is not
    assert any("Feed" in o for o in ctx["observables"])
