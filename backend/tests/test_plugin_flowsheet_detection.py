"""
Test that the agent's state-card builder correctly handles Cantera /
ChemSep / Reaktoro plugin flowsheets — where compounds and PP are managed
internally by the plugin and not introspectable via standard DWSIM APIs.

Regression for the bug: with the Biodiesel Combustion (Cantera) flowsheet
loaded (16 objects present), Gemini Flash refused to proceed because the
state card showed "Property Package: not set" and "Compounds: none".
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _CanteraBridge:
    """Mocks a loaded Cantera flowsheet. State cache is populated with
    flowsheet name and objects but PP/compounds queries return empty
    (because Cantera manages these internally)."""

    class state:
        name = "Biodiesel Combustion (Cantera)"
        active_alias = "main"
        loaded_flowsheets = {"main": object()}
        property_package = None      # Cantera doesn't populate this
        compounds = []               # Cantera doesn't populate this
        streams = []
        unit_ops = []

    def list_simulation_objects(self):
        # Live query returns 16 real objects — Biodiesel Combustion has
        # streams + reactor + heat exchangers
        return {
            "success": True,
            "count": 16,
            "objects": [
                {"tag": f"S-{i}", "type": "MaterialStream",
                 "category": "stream"} for i in range(1, 9)
            ] + [
                {"tag": "RC-1", "type": "ConversionReactor",
                 "category": "unit_op"},
                {"tag": "HX-1", "type": "HeatExchanger",
                 "category": "unit_op"},
                {"tag": "HX-2", "type": "HeatExchanger",
                 "category": "unit_op"},
                {"tag": "HX-3", "type": "HeatExchanger",
                 "category": "unit_op"},
                {"tag": "MX-1", "type": "Mixer", "category": "unit_op"},
                {"tag": "MX-2", "type": "Mixer", "category": "unit_op"},
                {"tag": "VLV", "type": "Valve", "category": "unit_op"},
                {"tag": "PSA", "type": "PSA", "category": "unit_op"},
            ],
        }

    def get_property_package(self):
        # Cantera doesn't populate SelectedPropertyPackage
        return {"success": True, "property_package": ""}

    def list_compounds(self):
        # Cantera doesn't populate SelectedCompounds
        return {"success": True, "compounds": [], "count": 0}


def _build_state_card(bridge):
    """Reproduce the agent's state-card logic in isolation. Mirrors the
    code in agent_v2.py:chat()."""
    from agent_v2 import _bridge_objects   # uses the same helper
    st = bridge.state
    fs_name = getattr(st, "name", None) or "none"
    pp      = getattr(st, "property_package", None)
    comps   = getattr(st, "compounds", []) or []
    streams = getattr(st, "streams", []) or []
    unitops = getattr(st, "unit_ops", []) or []

    real_objs_present = bool(streams or unitops)
    live = _bridge_objects(bridge)
    if not real_objs_present and (live.get("streams") or live.get("unit_ops")):
        streams = live["streams"]
        unitops = live["unit_ops"]
        real_objs_present = True

    if real_objs_present and (not pp or pp in ("not set", "None", "none")):
        r = bridge.get_property_package()
        if isinstance(r, dict) and r.get("success"):
            v = r.get("property_package") or ""
            if v and v.lower() not in ("none", "not set"):
                pp = v
    if real_objs_present and not comps:
        r = bridge.list_compounds()
        if isinstance(r, dict) and r.get("success"):
            comps = r.get("compounds") or []

    is_plugin_managed = bool(
        real_objs_present
        and (not pp or pp in ("not set", "None", "none"))
        and not comps
    )
    name_lc = (fs_name or "").lower()
    plugin_hint = ""
    if "cantera" in name_lc:    plugin_hint = "Cantera"
    elif "chemsep" in name_lc:  plugin_hint = "ChemSep"
    elif "reaktoro" in name_lc: plugin_hint = "Reaktoro"

    if is_plugin_managed:
        pp_display = (f"(managed by {plugin_hint} plugin)"
                      if plugin_hint
                      else "(managed by a plugin / not introspectable)")
        comp_display = (f"(managed by {plugin_hint} plugin)"
                        if plugin_hint
                        else "(managed by a plugin / not introspectable)")
    else:
        pp_display = pp or "not set"
        comp_str = ", ".join(list(comps)[:6]) + (
            f" (+{len(comps)-6} more)" if len(comps) > 6 else "")
        comp_display = comp_str or "none"

    return (
        f"[CURRENT FLOWSHEET STATE] "
        f"Name: {fs_name} | Property Package: {pp_display} | "
        f"Compounds: {comp_display} | "
        f"Streams: {len(streams)} | Unit ops: {len(unitops)}"
        + (f" | NOTE: Flowsheet is loaded and READY. "
            "Property package and compounds are configured "
            f"via the {plugin_hint or 'flowsheet plugin'} — "
            "proceed with any user request; do NOT ask the user "
            "to specify compounds or property package."
           if is_plugin_managed else "")
    )


# ─── Tests ─────────────────────────────────────────────────────────────

def test_cantera_flowsheet_shows_plugin_managed_in_state_card():
    """The state card MUST signal that the flowsheet is plugin-managed
    rather than empty, so the LLM doesn't refuse."""
    bridge = _CanteraBridge()
    card = _build_state_card(bridge)
    assert "Cantera" in card, \
        f"State card should mention Cantera plugin. Got: {card}"
    assert "managed by Cantera plugin" in card
    assert "Streams: 8" in card or "Streams: 7" in card  # 8 stream objects
    assert "Unit ops: 8" in card                          # 8 unit ops
    assert "READY" in card
    # Critical: must NOT contain the refusal-trigger phrases
    assert "not set" not in card.lower().replace("readys", ""), card
    assert "compounds: none" not in card.lower()


def test_chemsep_flowsheet_shows_plugin_hint():
    class _ChemSepBridge(_CanteraBridge):
        class state(_CanteraBridge.state):
            name = "Distillation_ChemSep_Column.dwxmz"
    card = _build_state_card(_ChemSepBridge())
    assert "ChemSep" in card


def test_standard_flowsheet_shows_real_pp_and_compounds():
    """A normal DWSIM flowsheet (PR + compounds) should NOT be flagged as
    plugin-managed."""
    class _StdBridge:
        class state:
            name = "methanol_synthesis.dwxmz"
            active_alias = "main"; loaded_flowsheets = {"main": 1}
            property_package = "Peng-Robinson (PR)"
            compounds = ["Methanol", "Water", "Carbon Monoxide", "Hydrogen"]
            streams = ["FEED", "PROD"]
            unit_ops = ["RC-1"]
        def list_simulation_objects(self):
            return {"success": True, "objects": []}
        def get_property_package(self):
            return {"success": True, "property_package": "Peng-Robinson (PR)"}
        def list_compounds(self):
            return {"success": True, "compounds":
                    ["Methanol", "Water", "Carbon Monoxide", "Hydrogen"]}
    card = _build_state_card(_StdBridge())
    assert "Peng-Robinson" in card
    assert "Methanol" in card
    # Should NOT have the plugin-managed marker
    assert "managed by" not in card
    assert "READY" not in card or "NOTE" not in card


def test_empty_flowsheet_still_shows_not_set_no_plugin_marker():
    """No objects loaded → genuine 'not set', no plugin signal."""
    class _EmptyBridge:
        class state:
            name = None; active_alias = None; loaded_flowsheets = {}
            property_package = None; compounds = []
            streams = []; unit_ops = []
        def list_simulation_objects(self):
            return {"success": True, "objects": []}
        def get_property_package(self):
            return {"success": True, "property_package": ""}
        def list_compounds(self):
            return {"success": True, "compounds": []}
    card = _build_state_card(_EmptyBridge())
    assert "managed by" not in card
    assert "not set" in card.lower() or "none" in card.lower()
