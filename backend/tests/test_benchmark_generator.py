"""
Tests for benchmark_generator.py — generate-from-archetypes + engine-validate.

Enumeration, intent-consistency, criteria extraction, dropping, content-hashing
and determinism are covered with a MockBridge (no DWSIM). The live build+solve
path is exercised by running the script with --live.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_enumeration_covers_all_archetypes_and_scales():
    from benchmark_generator import enumerate_specs, ARCHETYPES
    cands = enumerate_specs()
    assert len(cands) >= 100                       # scales well past the 25-task set
    archs = {c["archetype"] for c in cands}
    assert archs == set(ARCHETYPES)                # every archetype represented
    c = cands[0]
    for k in ("spec", "prompt", "outlet_tag", "category", "complexity",
              "detail_level", "expected_property_package"):
        assert k in c


def test_full_vs_ambiguous_prompts_differ():
    from benchmark_generator import enumerate_specs
    cands = {c["candidate_id"]: c for c in enumerate_specs()}
    full = next(c for c in cands.values() if c["detail_level"] == "full")
    # the matching ambiguous variant omits the property package from the prompt
    assert full["expected_property_package"] in full["prompt"]
    amb_id = full["candidate_id"].replace("_full", "_ambiguous")
    assert cands[amb_id]["expected_property_package"] not in cands[amb_id]["prompt"]


def test_validate_reads_criteria_from_the_solve():
    from benchmark_generator import enumerate_specs, validate_candidate, _MockBridge
    heater = next(c for c in enumerate_specs() if c["archetype"] == "heater")
    setpoint = float(heater["spec"]["unit_op_specs"][0]["value"])
    task = validate_candidate(heater, _MockBridge())
    assert task is not None
    temp = next(c for c in task["success_criteria"] if c["property"] == "temperature_C")
    assert abs(temp["expected"] - setpoint) < 1e-6     # criterion == engine outlet
    assert task["provenance"] == "programmatically generated, engine-validated"


def test_non_converged_candidate_is_dropped():
    from benchmark_generator import enumerate_specs, validate_candidate
    class _Fail:
        def build_flowsheet_atomic(self, spec):
            return {"success": False, "error": "did not converge"}
    heater = next(c for c in enumerate_specs() if c["archetype"] == "heater")
    assert validate_candidate(heater, _Fail()) is None


def test_intent_not_achieved_is_dropped():
    from benchmark_generator import enumerate_specs, validate_candidate
    # a bridge that ignores the setpoint (returns the feed unchanged) → drop
    class _Ignore:
        def build_flowsheet_atomic(self, spec):
            f = spec["feed_specs"][0]
            return {"success": True, "converged": True, "stream_results": {
                "Out": {"temperature_C": float(f["temperature"]),
                        "pressure_bar": float(f["pressure"]),
                        "vapor_fraction": 0.0, "mass_flow_kg_s": 1.0}}}
    heater = next(c for c in enumerate_specs() if c["archetype"] == "heater")
    # heater feed 25 °C, setpoint 60/80/100 → outlet stuck at 25 ≠ intent → None
    assert validate_candidate(heater, _Ignore()) is None


def test_generate_is_deterministic_and_hashed():
    from benchmark_generator import generate, _MockBridge
    a = generate(target=30, bridge=_MockBridge())
    b = generate(target=30, bridge=_MockBridge())
    assert a["n_validated"] == 30
    assert a["content_hash"] == b["content_hash"]      # deterministic
    assert len(a["content_hash"]) == 16
    assert all(t["success_criteria"] for t in a["tasks"])
