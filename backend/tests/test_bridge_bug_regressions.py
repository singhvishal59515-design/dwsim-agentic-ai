"""
Phase-2 regression suite for the bridge bugs found by self-testing the live
DWSIM MCP session (12 June 2026). Each bug was reproduced before fixing:

  BUG-1  list_objects() bucketed an ENERGY stream into streams[] because the
         splitter tested `"stream" in category` first and "stream" is a
         substring of "energystream" — so it never reached the energy guard.
         (The roadmap's "GUID-prefix mis-typing" diagnosis was wrong: the
         bridge already categorises via the .NET class name; the real defect
         was substring ordering in the splitter.)
  BUG-2  _check_convergence_internal iterated only self.state.streams, so an
         unconverged unit op (Calculated == False) never surfaced and
         all_converged could be True with a heater/column that had not solved.
  BUG-3  /health read the cached current_property_package/state attribute,
         which is never populated on load, so it always reported "None" even
         with a package set. Fixed to read live via get_property_package().

These run without DWSIM by invoking the real methods with a minimal fake
`self`, so the fix is what is under test (not a re-implementation).
"""
from __future__ import annotations
import os
import sys
import types

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# ── BUG-1: list_objects() energy-stream bucketing ────────────────────────────

def _call_list_objects(objects):
    from dwsim_bridge_v2 import DWSIMBridgeV2
    fake = types.SimpleNamespace(
        list_simulation_objects=lambda: {"success": True, "objects": objects,
                                         "count": len(objects)})
    return DWSIMBridgeV2.list_objects(fake)


def test_bug1_energy_stream_not_bucketed_as_stream():
    # Solved-phase shape: _categorise() returns "EnergyStream" / type "EnergyStream".
    objects = [
        {"tag": "Q", "type": "EnergyStream", "category": "EnergyStream"},
        {"tag": "Feed", "type": "MaterialStream", "category": "MaterialStream"},
        {"tag": "H-101", "type": "Heater", "category": "UnitOperation"},
    ]
    r = _call_list_objects(objects)
    stream_tags = [o["tag"] for o in r["streams"]]
    unitop_tags = [o["tag"] for o in r["unit_ops"]]
    assert "Q" not in stream_tags, f"energy stream leaked into streams: {stream_tags}"
    assert "Q" not in unitop_tags, "energy stream must be skipped, not a unit op"
    assert "Feed" in stream_tags
    assert "H-101" in unitop_tags


def test_bug1_energy_stream_skipped_in_build_phase():
    # Build phase categorises everything in state.streams as "stream"; the only
    # discriminator is the type string. The fix must still skip it.
    objects = [
        {"tag": "E1", "type": "EnergyStream", "category": "stream"},
        {"tag": "S1", "type": "MaterialStream", "category": "stream"},
    ]
    r = _call_list_objects(objects)
    assert [o["tag"] for o in r["streams"]] == ["S1"]


def test_bug1_material_stream_still_classified_as_stream():
    objects = [{"tag": "Q", "type": "MaterialStream", "category": "MaterialStream"}]
    r = _call_list_objects(objects)
    assert [o["tag"] for o in r["streams"]] == ["Q"]


def test_bug1_unknown_object_falls_through_to_unit_ops():
    objects = [{"tag": "X", "type": "Recycle", "category": "Other"}]
    r = _call_list_objects(objects)
    assert [o["tag"] for o in r["unit_ops"]] == ["X"]


# ── BUG-2: convergence_check must include unit operations ────────────────────

def _fake_convergence_self(streams, unit_ops, stream_props, unit_objs):
    """unit_objs maps tag -> object (with .Calculated) or None (not found)."""
    from dwsim_bridge_v2 import DWSIMBridgeV2
    state = types.SimpleNamespace(streams=streams, unit_ops=unit_ops)

    def get_stream_properties(tag):
        p = stream_props.get(tag)
        if p is None:
            return {"success": False}
        return {"success": True, "properties": p}

    fake = types.SimpleNamespace(
        state=state,
        get_stream_properties=get_stream_properties,
        _find_object=lambda tag: unit_objs.get(tag),
    )
    return DWSIMBridgeV2._check_convergence_internal(fake)


def _good_stream():
    return {"temperature_K": 350.0, "pressure_Pa": 1e5, "mass_flow_kg_s": 1.0,
            "molar_flow_mol_s": 55.5, "vapor_fraction": 0.0}


def test_bug2_unconverged_unitop_flips_all_converged_false():
    r = _fake_convergence_self(
        streams=["Feed"], unit_ops=["H-101"],
        stream_props={"Feed": _good_stream()},
        unit_objs={"H-101": types.SimpleNamespace(Calculated=False)})
    assert r["all_converged"] is False
    nc_tags = [e["tag"] for e in r["not_converged"]]
    assert "H-101" in nc_tags, f"unconverged unit op not reported: {r}"
    assert any(u["tag"] == "H-101" for u in r["unit_ops"])


def test_bug2_converged_unitop_passes():
    r = _fake_convergence_self(
        streams=["Feed"], unit_ops=["H-101"],
        stream_props={"Feed": _good_stream()},
        unit_objs={"H-101": types.SimpleNamespace(Calculated=True)})
    assert r["all_converged"] is True
    assert "H-101" in r["converged"]


def test_bug2_missing_unitop_marked_inaccessible():
    r = _fake_convergence_self(
        streams=[], unit_ops=["H-101"],
        stream_props={},
        unit_objs={"H-101": None})
    assert r["all_converged"] is False
    assert "H-101" in r["inaccessible"]


def test_bug2_streams_still_validated():
    # Guard: the unit-op addition must not bypass the existing stream checks.
    bad = _good_stream(); bad["temperature_K"] = -5.0  # unphysical
    r = _fake_convergence_self(
        streams=["Feed"], unit_ops=["H-101"],
        stream_props={"Feed": bad},
        unit_objs={"H-101": types.SimpleNamespace(Calculated=True)})
    assert r["all_converged"] is False
    assert any(e["tag"] == "Feed" for e in r["not_converged"])


# ── BUG-3: /health reads property package live, not from stale cache ─────────

def _patched_health(monkeypatch, fake_bridge):
    import api
    monkeypatch.setattr(api, "_bridge", fake_bridge, raising=False)
    return api.health()


def test_bug3_health_uses_live_property_package(monkeypatch):
    fake = types.SimpleNamespace(
        current_property_package=None,
        state=types.SimpleNamespace(name="simple_heater", property_package=None),
        get_property_package=lambda: {"success": True,
                                      "property_package": "Peng-Robinson (PR)"},
    )
    r = _patched_health(monkeypatch, fake)
    assert r["property_package"] == "Peng-Robinson (PR)", r


def test_bug3_health_falls_back_to_cache_when_live_unknown(monkeypatch):
    fake = types.SimpleNamespace(
        current_property_package="NRTL",
        state=types.SimpleNamespace(name="fs", property_package=None),
        get_property_package=lambda: {"success": True, "property_package": "Unknown"},
    )
    r = _patched_health(monkeypatch, fake)
    assert r["property_package"] == "NRTL", r


def test_bug3_health_survives_bridge_without_reader(monkeypatch):
    # A bridge that lacks get_property_package must not crash /health.
    fake = types.SimpleNamespace(
        current_property_package="SRK",
        state=types.SimpleNamespace(name="fs", property_package=None),
    )
    r = _patched_health(monkeypatch, fake)
    assert r["status"] == "ok"
    assert r["property_package"] == "SRK"


# ── #7: category-aware "stream not found" suggestions ────────────────────────

def test_p7_stream_not_found_excludes_unit_ops():
    from dwsim_bridge_v2 import DWSIMBridgeV2
    fake = types.SimpleNamespace(
        _find_object=lambda tag: None,
        _known_objects_split=lambda: {"known_streams": ["Feed", "Hot"],
                                      "known_unit_ops": ["H-101"]},
    )
    r = DWSIMBridgeV2.get_stream_properties(fake, "Nope")
    assert r["success"] is False
    assert "Feed" in r["error"] and "Hot" in r["error"]
    # The unit op is listed separately, not as a stream suggestion.
    assert "H-101" in r["error"]
    # Specifically: the stream-suggestion clause must not contain the unit op.
    stream_clause = r["error"].split("(Unit operations")[0]
    assert "H-101" not in stream_clause


def test_p7_split_known_objects_helper():
    from bridge_patches_v4 import split_known_objects
    lo = {"streams": [{"tag": "S1"}], "unit_ops": [{"tag": "U1"}]}
    out = split_known_objects(lo)
    assert out == {"known_streams": ["S1"], "known_unit_ops": ["U1"]}


# ── #5: property-name resolver (case-insensitive + did-you-mean) ─────────────

def test_p5_property_resolver_canonicalises_aliases():
    from bridge_patches_v4 import PropertyNames
    assert PropertyNames.resolve("temperature")[0] == "Temperature"
    assert PropertyNames.resolve("TEMP")[0] == "Temperature"
    assert PropertyNames.resolve("mass_flow")[0] == "MassFlow"
    assert PropertyNames.resolve("duty")[0] == "EnergyFlow"


def test_p5_property_resolver_suggests_on_unknown():
    from bridge_patches_v4 import PropertyNames
    canon, msg = PropertyNames.resolve("temperatur")  # typo
    assert canon is None
    assert "Temperature" in msg


# ── #2: read-before-write verification ───────────────────────────────────────

def test_p2_readbeforewrite_reports_old_new_verified():
    from bridge_patches_v4 import ReadBeforeWrite
    store = {("S1", "T"): 300.0}
    rbw = ReadBeforeWrite(
        getter=lambda t, p: store.get((t, p)),
        setter=lambda t, p, v: store.__setitem__((t, p), v))
    rec = rbw.set_verified("S1", "T", 350.0)
    assert rec["old_value"] == 300.0
    assert rec["new_value"] == 350.0
    assert rec["verified"] is True
    assert rec["success"] is True


def test_p2_readbeforewrite_flags_calculated_property():
    from bridge_patches_v4 import ReadBeforeWrite
    # setter is a no-op (property is calculated, not specifiable)
    rbw = ReadBeforeWrite(getter=lambda t, p: 300.0, setter=lambda t, p, v: None)
    rec = rbw.set_verified("S1", "T", 350.0)
    assert rec["verified"] is False
    assert rec["success"] is False
    assert "FAILED" in rec["error"]


# ── #6: dirty-state stale-value tracking ─────────────────────────────────────

def test_p6_dirtystate_mark_clear_stamp():
    from bridge_patches_v4 import DirtyState
    d = DirtyState()
    clean = d.stamp({"success": True})
    assert clean["needs_resolve"] is False
    assert "warning" not in clean
    d.mark("set H-101.outlet_temperature=80C")
    dirty = d.stamp({"success": True})
    assert dirty["needs_resolve"] is True
    assert "STALE" in dirty["warning"]
    d.clear()
    assert d.stamp({"success": True})["needs_resolve"] is False


def test_p6_bridge_stamp_helper_is_safe_without_dirtystate():
    from dwsim_bridge_v2 import DWSIMBridgeV2
    fake = types.SimpleNamespace(_dirty=None)
    out = DWSIMBridgeV2._stamp_dirty(fake, {"success": True})
    assert out == {"success": True}  # no crash, no stamp when disabled
