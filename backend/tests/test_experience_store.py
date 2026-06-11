"""
Tests for the case-based learning loop (experience_store).

Verifies the agent (a) only learns from VERIFIED successes, (b) retrieves a
past case for a similar goal on a similar flowsheet, (c) does NOT surface a
case for an unrelated flowsheet, and (d) feeds it into the objective-mapping
prompt. Uses a temp store file via EXPERIENCE_STORE_PATH; no DWSIM needed.
"""
from __future__ import annotations
import os, sys, tempfile
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def _fresh_store(tmp_path_factory=None):
    import experience_store as es
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.remove(path)
    os.environ["EXPERIENCE_STORE_PATH"] = path
    return es, path


class _WaterBridge:
    def list_compounds(self):
        return {"success": True, "compounds": ["Water"]}
    def list_simulation_objects(self):
        return {"success": True, "objects": [
            {"tag": "Feed", "type": "MaterialStream"},
            {"tag": "H-101", "type": "Heater"}]}


class _SyngasBridge:
    def list_compounds(self):
        return {"success": True, "compounds": ["Hydrogen", "CarbonMonoxide"]}
    def list_simulation_objects(self):
        return {"success": True, "objects": [
            {"tag": "Syn", "type": "MaterialStream"},
            {"tag": "R-1", "type": "Reactor_Gibbs"}]}


def test_only_verified_successes_are_recorded():
    es, path = _fresh_store()
    try:
        spec = {"objective": {"type": "variable", "tag": "H-101",
                              "property": "HeatDuty"}, "minimize": True,
                "method": "simplex"}
        # Failed run → not recorded
        assert es.record_case("minimise heater duty", spec,
                              {"success": False}, _WaterBridge()) is False
        # Success but no objective value → not recorded
        assert es.record_case("minimise heater duty", spec,
                              {"success": True, "best_objective": None},
                              _WaterBridge()) is False
        # Verified success → recorded
        assert es.record_case("minimise heater duty", spec,
                              {"success": True, "best_objective": 125.6},
                              _WaterBridge()) is True
        assert es.stats()["total_cases"] == 1
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_retrieves_similar_and_rejects_unrelated():
    es, path = _fresh_store()
    try:
        spec = {"objective": {"type": "variable", "tag": "H-101",
                              "property": "HeatDuty"}, "minimize": True,
                "method": "simplex"}
        es.record_case("minimise the heater energy duty", spec,
                       {"success": True, "best_objective": 125.6},
                       _WaterBridge())

        # Similar goal on a similar (water+heater) flowsheet → retrieved
        hits = es.retrieve_similar("reduce heater duty energy", _WaterBridge(), k=3)
        assert len(hits) == 1
        assert hits[0]["objective"]["property"] == "HeatDuty"
        assert hits[0]["_similarity"] > 0.18

        # Unrelated goal + different chemistry/topology → not surfaced
        none = es.retrieve_similar("maximise hydrogen purity in syngas",
                                   _SyngasBridge(), k=3)
        assert none == []
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_examples_block_renders_for_prompt():
    es, path = _fresh_store()
    try:
        spec = {"objective": {"type": "variable", "tag": "H-101",
                              "property": "HeatDuty"}, "minimize": True}
        es.record_case("minimise heater duty", spec,
                       {"success": True, "best_objective": 125.6}, _WaterBridge())
        hits = es.retrieve_similar("lower the heater duty", _WaterBridge())
        block = es.format_examples_for_prompt(hits)
        assert "LEARNED_EXAMPLES" in block
        assert "minimise" in block and "HeatDuty" in block
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_dedup_keeps_latest_for_same_goal_objective():
    es, path = _fresh_store()
    try:
        spec = {"objective": {"type": "variable", "tag": "H-101",
                              "property": "HeatDuty"}, "minimize": True}
        es.record_case("minimise heater duty", spec,
                       {"success": True, "best_objective": 200.0}, _WaterBridge())
        es.record_case("minimise heater duty", spec,
                       {"success": True, "best_objective": 125.6}, _WaterBridge())
        assert es.stats()["total_cases"] == 1   # de-duped
        hit = es.retrieve_similar("minimise heater duty", _WaterBridge())[0]
        assert hit["best_objective"] == 125.6    # latest kept
    finally:
        if os.path.exists(path):
            os.remove(path)
