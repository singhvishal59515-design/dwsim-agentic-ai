"""
Regression tests for the state-card / fast-path live-fallback fix.

Reproduces the bug: a flowsheet is loaded via the UI Load button (not the
agent's own tools), so self.bridge.state is empty even though
list_simulation_objects() and get_property_package() return real data.
Without the fallback, the LLM was told "Property Package: not set,
Compounds: none" and incorrectly refused to proceed.
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _LoadedButCacheEmpty:
    """Mock bridge that simulates "loaded via UI button" — state cache is
    empty but the live methods return real data."""

    class state:
        # Everything empty — simulates an externally-loaded flowsheet
        # whose state we never tracked
        name = None
        active_alias = None
        loaded_flowsheets = {}
        property_package = None
        compounds = []
        streams = []
        unit_ops = []

    def list_simulation_objects(self):
        # Live query returns real objects
        return {
            "success": True,
            "count": 16,
            "objects": [
                {"tag": "Biodiesel", "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "Air",       "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "Combustor", "type": "GibbsReactor",
                 "category": "unit_op"},
                {"tag": "HX-1",      "type": "Heater",
                 "category": "unit_op"},
            ],
        }

    def get_property_package(self):
        return {"success": True, "property_package": "Peng-Robinson (PR)"}

    def list_compounds(self):
        return {"success": True,
                "compounds": ["Biodiesel", "Air", "O2", "N2", "CO2", "H2O"],
                "count": 6}


def test_bridge_objects_helper_finds_via_live_query():
    """The agent's _bridge_objects helper must split a flat objects[] list
    into streams + unit_ops."""
    from agent_v2 import _bridge_objects
    out = _bridge_objects(_LoadedButCacheEmpty())
    assert len(out["streams"]) == 2
    assert len(out["unit_ops"]) == 2
    assert {s["tag"] for s in out["streams"]} == {"Biodiesel", "Air"}
    assert {u["tag"] for u in out["unit_ops"]} == {"Combustor", "HX-1"}


def test_fast_path_uses_live_query_when_state_cache_is_empty():
    """The optimization fast-path must trigger for 'do optimisation' even
    when the bridge state cache is empty (because the flowsheet was loaded
    via UI Load button rather than the agent's tools)."""
    from agent_v2 import DWSIMAgentV2

    agent = DWSIMAgentV2.__new__(DWSIMAgentV2)
    agent.bridge = _LoadedButCacheEmpty()
    # Should trigger because the live bridge has ≥2 objects
    assert agent._should_fast_path_optimization("do optimisation"), \
        "Fast-path should trigger when live bridge has objects, even if " \
        "state cache is empty"
    assert agent._should_fast_path_optimization("maximise yield"), \
        "Fast-path should trigger for clear optimization phrases"


def test_bridge_list_objects_alias_splits_objects_correctly():
    """The new bridge.list_objects() alias must split the flat objects[]
    returned by list_simulation_objects() into {streams, unit_ops}."""
    from dwsim_bridge_v2 import DWSIMBridgeV2
    # Use the real DWSIMBridgeV2 class but skip __init__ (avoids needing
    # DWSIM DLLs); install our mock's list_simulation_objects in its place.
    bridge = DWSIMBridgeV2.__new__(DWSIMBridgeV2)
    bridge.list_simulation_objects = _LoadedButCacheEmpty().list_simulation_objects
    out = bridge.list_objects()
    assert out["success"]
    assert len(out["streams"]) == 2
    assert len(out["unit_ops"]) == 2
    assert {s["tag"] for s in out["streams"]} == {"Biodiesel", "Air"}
    assert {u["tag"] for u in out["unit_ops"]} == {"Combustor", "HX-1"}


def test_get_property_package_returns_real_pp():
    """Sanity check the live PP query for the mock."""
    bridge = _LoadedButCacheEmpty()
    r = bridge.get_property_package()
    assert r["success"]
    assert "Peng-Robinson" in r["property_package"]


def test_list_compounds_returns_real_compounds():
    """Sanity check the live compounds query for the mock."""
    bridge = _LoadedButCacheEmpty()
    r = bridge.list_compounds()
    assert r["success"]
    assert r["count"] == 6
    assert "CO2" in r["compounds"]
