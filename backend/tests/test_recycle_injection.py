"""
Regression: complex (recycle) flowsheet construction must auto-tear untorn
feedback loops with an OT_Recycle block, or DWSIM's sequential-modular solver
can never converge them.

Mirrors the reactor_recycle template but with the recycle stream connected
DIRECTLY back to the mixer (no recycle block) — the typical LLM free-build
mistake. The analyzer must detect the loop and the builder/executor must splice
in a recycle block.
"""
from __future__ import annotations
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

pytest.importorskip("networkx")

import recycle_analyzer as ra


def _untorn_topology():
    streams = [
        {"tag": "FreshFeed", "type": "MaterialStream"},
        {"tag": "Mixed", "type": "MaterialStream"},
        {"tag": "ReactorOut", "type": "MaterialStream"},
        {"tag": "Product", "type": "MaterialStream"},
        {"tag": "UnconvFeed", "type": "MaterialStream"},
    ]
    unit_ops = [
        {"tag": "MIX-01", "type": "Mixer"},
        {"tag": "R-101", "type": "ConversionReactor"},
        {"tag": "FLASH", "type": "Vessel"},
    ]
    connections = [
        {"from": "FreshFeed", "to": "MIX-01", "from_port": 0, "to_port": 0},
        {"from": "MIX-01", "to": "Mixed", "from_port": 0, "to_port": 0},
        {"from": "Mixed", "to": "R-101", "from_port": 0, "to_port": 0},
        {"from": "R-101", "to": "ReactorOut", "from_port": 0, "to_port": 0},
        {"from": "ReactorOut", "to": "FLASH", "from_port": 0, "to_port": 0},
        {"from": "FLASH", "to": "Product", "from_port": 0, "to_port": 0},
        {"from": "FLASH", "to": "UnconvFeed", "from_port": 1, "to_port": 0},
        {"from": "UnconvFeed", "to": "MIX-01", "from_port": 0, "to_port": 1},  # untorn
    ]
    return streams, unit_ops, connections


# ── Analyzer ───────────────────────────────────────────────────────────────

def test_detects_untorn_loop():
    s, u, c = _untorn_topology()
    info = ra.find_recycle_loops(s, u, c)
    assert info["available"] is True
    assert len(info["cycles"]) == 1
    assert len(info["untorn"]) == 1
    assert info["recycle_units"] == []


def test_plan_tears_at_mixer_join():
    s, u, c = _untorn_topology()
    plans = ra.plan_recycle_insertions(s, u, c)
    assert len(plans) == 1
    p = plans[0]
    # Preferred tear is the stream feeding the loop-closing Mixer, port preserved.
    assert p["tear_stream"] == "UnconvFeed"
    assert p["consumer"] == "MIX-01"
    assert p["consumer_port"] == 1
    assert p["rec_tag"].startswith("REC-")
    assert p["new_stream_tag"] == "UnconvFeed_rec"


def test_existing_recycle_block_is_noop():
    s, u, c = _untorn_topology()
    # Properly torn version: insert a Recycle block in the loop.
    u = u + [{"tag": "REC-01", "type": "Recycle"}]
    s = s + [{"tag": "Recycle", "type": "MaterialStream"}]
    c = [x for x in c if not (x["from"] == "UnconvFeed" and x["to"] == "MIX-01")]
    c += [
        {"from": "UnconvFeed", "to": "REC-01", "from_port": 0, "to_port": 0},
        {"from": "REC-01", "to": "Recycle", "from_port": 0, "to_port": 0},
        {"from": "Recycle", "to": "MIX-01", "from_port": 0, "to_port": 1},
    ]
    info = ra.find_recycle_loops(s, u, c)
    assert info["untorn"] == []
    assert ra.plan_recycle_insertions(s, u, c) == []


def test_acyclic_topology_no_plan():
    s = [{"tag": "Feed", "type": "MaterialStream"},
         {"tag": "Out", "type": "MaterialStream"}]
    u = [{"tag": "H-1", "type": "Heater"}]
    c = [{"from": "Feed", "to": "H-1"}, {"from": "H-1", "to": "Out"}]
    assert ra.plan_recycle_insertions(s, u, c) == []


# ── Builder injection ──────────────────────────────────────────────────────

def test_builder_injects_recycle_block():
    from flowsheet_builder import _inject_recycle_blocks
    s, u, c = _untorn_topology()
    warnings = []
    s, u, c = _inject_recycle_blocks(s, u, c, warnings)

    rec_units = [x for x in u if x["type"] == "Recycle"]
    assert len(rec_units) == 1
    rec_tag = rec_units[0]["tag"]
    assert rec_units[0]["max_iterations"] == 50

    # Direct untorn edge is gone.
    assert not any(x.get("from") == "UnconvFeed" and x.get("to") == "MIX-01"
                   for x in c)
    # Rerouted through the block, mixer port preserved.
    assert any(x["from"] == "UnconvFeed" and x["to"] == rec_tag for x in c)
    assert any(x["from"] == rec_tag and x["to"] == "UnconvFeed_rec" for x in c)
    assert any(x["from"] == "UnconvFeed_rec" and x["to"] == "MIX-01"
               and x["to_port"] == 1 for x in c)
    assert any("Auto-inserted recycle block" in w for w in warnings)


def test_tear_stream_is_seeded_from_feed():
    """The inserted tear stream must inherit a physical initial guess
    (T/P/composition) from a seed source so the recycle iteration converges."""
    from flowsheet_builder import _inject_recycle_blocks
    s, u, c = _untorn_topology()
    # Give the fresh feed conditions to seed from.
    for st in s:
        if st["tag"] == "FreshFeed":
            st.update({"T": 80.0, "T_unit": "C", "P": 5.0, "P_unit": "bar",
                       "compositions": {"Methanol": 1.0}})
    s2, u2, c2 = _inject_recycle_blocks(s, u, c, [])
    rec_stream = next(x for x in s2 if x["tag"] == "UnconvFeed_rec")
    # Tear stream (UnconvFeed) had no spec → seed comes from the feed.
    assert rec_stream.get("T") == 80.0
    assert rec_stream.get("compositions") == {"Methanol": 1.0}


def test_seed_prefers_tear_stream_own_spec():
    s, u, c = _untorn_topology()
    for st in s:
        if st["tag"] == "UnconvFeed":
            st.update({"T": 120.0, "T_unit": "C", "compositions": {"Methanol": 0.3}})
    plans = ra.plan_recycle_insertions(s, u, c)
    assert plans[0]["seed_from"] == "UnconvFeed"


def test_builder_noop_when_already_torn():
    from flowsheet_builder import _inject_recycle_blocks
    s = [{"tag": "Feed", "type": "MaterialStream"},
         {"tag": "Out", "type": "MaterialStream"}]
    u = [{"tag": "H-1", "type": "Heater"}]
    c = [{"from": "Feed", "to": "H-1"}, {"from": "H-1", "to": "Out"}]
    warnings = []
    s2, u2, c2 = _inject_recycle_blocks(s, u, c, warnings)
    assert not any(x["type"] == "Recycle" for x in u2)
    assert warnings == []


# ── Executor injection ─────────────────────────────────────────────────────

class _MockBridge:
    def __init__(self):
        self.objects = []      # (tag, type)
        self.connections = []  # (from, to, from_port, to_port)

    def new_flowsheet(self, name, compounds, property_package):
        return {"success": True, "name": name}

    def add_object(self, tag, type):
        self.objects.append((tag, type))
        return {"success": True}

    def connect_streams(self, from_tag, to_tag, from_port=0, to_port=0):
        self.connections.append((from_tag, to_tag, from_port, to_port))
        return {"success": True}

    def set_stream_property(self, **k):
        return {"success": True}

    def set_unit_op_property(self, **k):
        return {"success": True}


def test_executor_creates_recycle_block():
    from flowsheet_executor import execute_build_plan
    s, u, c = _untorn_topology()
    plan = {"name": "rx", "compounds": ["Methanol"],
            "property_package": "Peng-Robinson (PR)",
            "streams": s, "unit_ops": u, "connections": c}
    br = _MockBridge()
    res = execute_build_plan(plan, br, solve=False)
    assert res["success"], res
    rec = [t for (t, ty) in br.objects if ty == "Recycle"]
    assert len(rec) == 1, br.objects
    # The recycle block is wired into the loop-closing mixer.
    assert any(to == "MIX-01" and frm.endswith("_rec")
               for (frm, to, _, _) in br.connections), br.connections
