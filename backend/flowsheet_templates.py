"""
flowsheet_templates.py
──────────────────────
Curated library of starter flowsheet topologies for create_flowsheet.

Each template is a valid topology dict that can be passed straight into
DWSIMBridgeV2.create_flowsheet(). Templates are intentionally minimal —
users are expected to override compound lists, conditions, and save paths
with create_from_template(name, overrides={...}).

Categories:
  • simple       — single unit op demonstrating a specific block
  • separation   — flash, distillation, absorption
  • reaction     — CSTR, PFR, Gibbs, equilibrium
  • heat         — heater-cooler, shell-and-tube, HEN
  • pressure     — pump + compressor + valve network
  • recycle      — reactor with recycle loop (uses OT_Recycle)
  • renewables   — fuel cell, electrolyzer
"""

from copy import deepcopy
from typing import Any, Dict, List, Optional


# Key shorthand helpers ─────────────────────────────────────────────────────
def _s(tag, **kw):
    """Material-stream shorthand."""
    d = {"tag": tag, "type": "MaterialStream"}
    d.update(kw)
    return d


def _e(tag):
    """Energy-stream shorthand."""
    return {"tag": tag, "type": "EnergyStream"}


def _u(tag, type_, **kw):
    """Unit-op shorthand."""
    d = {"tag": tag, "type": type_}
    d.update(kw)
    return d


def _c(frm, to, fp=0, tp=0):
    return {"from": frm, "to": to, "from_port": fp, "to_port": tp}


# Template definitions ──────────────────────────────────────────────────────
TEMPLATES: Dict[str, Dict[str, Any]] = {

    # ── SEPARATION ───────────────────────────────────────────────────────────
    "flash_separation": {
        "category": "separation",
        "description": "Two-phase flash drum separating a methanol/water feed at 80 °C.",
        "topology": {
            "name": "flash_separation",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Water", "Methanol"],
            "streams": [
                _s("Feed", T=80, T_unit="C", P=2, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Water": 0.6, "Methanol": 0.4}),
                _s("Vapor"),
                _s("Liquid"),
            ],
            "unit_ops": [_u("FLASH-01", "Vessel")],
            "connections": [
                _c("Feed",  "FLASH-01", 0, 0),
                _c("FLASH-01", "Vapor",  0, 0),
                _c("FLASH-01", "Liquid", 1, 0),
            ],
        },
    },

    "shortcut_distillation": {
        "category": "separation",
        "description": "Fenske–Underwood–Gilliland shortcut column for benzene/toluene.",
        "topology": {
            "name": "shortcut_distillation",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Benzene", "Toluene"],
            "streams": [
                _s("Feed", T=90, T_unit="C", P=1.1, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Benzene": 0.5, "Toluene": 0.5}),
                _s("Distillate"),
                _s("Bottoms"),
                _e("Qc"), _e("Qr"),
            ],
            "unit_ops": [
                _u("T-101", "ShortcutColumn",
                   reflux_ratio=1.5, number_of_stages=20),
            ],
            "connections": [
                _c("Feed", "T-101", 0, 0),
                _c("T-101", "Distillate", 0, 0),
                _c("T-101", "Bottoms",   1, 0),
                _c("Qc",   "T-101",      0, 1),
                _c("Qr",   "T-101",      0, 2),
            ],
        },
    },

    "absorber": {
        "category": "separation",
        "description": "Packed absorber removing CO2 from a gas stream with MEA solvent.",
        "topology": {
            "name": "co2_absorber",
            "property_package": "NRTL",
            "compounds": ["Water", "Carbon dioxide", "Nitrogen"],
            "streams": [
                _s("GasFeed",  T=40, T_unit="C", P=2, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Nitrogen": 0.85, "Carbon dioxide": 0.15,
                                 "Water": 0.0}),
                _s("Solvent", T=40, T_unit="C", P=2, P_unit="bar",
                   molar_flow=200, flow_unit="kmol/h",
                   compositions={"Water": 1.0, "Carbon dioxide": 0.0,
                                 "Nitrogen": 0.0}),
                _s("CleanGas"),
                _s("RichSolvent"),
            ],
            "unit_ops": [
                _u("ABS-01", "AbsorptionColumn", number_of_stages=10),
            ],
            "connections": [
                _c("GasFeed", "ABS-01", 0, 0),
                _c("Solvent", "ABS-01", 0, 1),
                _c("ABS-01",  "CleanGas",    0, 0),
                _c("ABS-01",  "RichSolvent", 1, 0),
            ],
        },
    },

    # ── HEAT TRANSFER ────────────────────────────────────────────────────────
    "heater_cooler": {
        "category": "heat",
        "description": "Heat a water stream from 25 °C to 90 °C.",
        "topology": {
            "name": "simple_heater",
            "property_package": "Steam Tables (IAPWS-IF97)",
            "compounds": ["Water"],
            "streams": [
                _s("Feed", T=25, T_unit="C", P=2, P_unit="bar",
                   mass_flow=1.0, mass_flow_unit="kg/s",
                   compositions={"Water": 1.0}),
                _s("Hot"),
                _e("Q"),
            ],
            "unit_ops": [
                _u("H-101", "Heater", outlet_T=90, outlet_T_unit="C",
                   pressure_drop=0),
            ],
            "connections": [
                _c("Feed", "H-101", 0, 0),
                _c("H-101", "Hot", 0, 0),
                _c("Q",    "H-101", 0, 1),
            ],
        },
    },

    "heat_exchanger": {
        "category": "heat",
        "description": "Counter-current shell-and-tube exchanger cooling a hot process stream with cold water.",
        "topology": {
            "name": "shell_tube_he",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Water", "Ethanol"],
            "streams": [
                _s("HotIn",  T=120, T_unit="C", P=3, P_unit="bar",
                   molar_flow=50, flow_unit="kmol/h",
                   compositions={"Ethanol": 1.0, "Water": 0.0}),
                _s("ColdIn", T=20,  T_unit="C", P=3, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Water": 1.0, "Ethanol": 0.0}),
                _s("HotOut"), _s("ColdOut"),
            ],
            "unit_ops": [_u("E-101", "HeatExchanger")],
            "connections": [
                _c("HotIn",  "E-101", 0, 0),
                _c("ColdIn", "E-101", 0, 1),
                _c("E-101",  "HotOut",  0, 0),
                _c("E-101",  "ColdOut", 1, 0),
            ],
        },
    },

    # ── PRESSURE CHANGERS ───────────────────────────────────────────────────
    "pump_valve": {
        "category": "pressure",
        "description": "Pump pressurizes water, then a valve drops pressure back — verifies pressure changers.",
        "topology": {
            "name": "pump_valve",
            "property_package": "Steam Tables (IAPWS-IF97)",
            "compounds": ["Water"],
            "streams": [
                _s("In", T=25, T_unit="C", P=1, P_unit="bar",
                   mass_flow=1.0, mass_flow_unit="kg/s",
                   compositions={"Water": 1.0}),
                _s("HighP"), _s("Out"),
                _e("W"),
            ],
            "unit_ops": [
                _u("P-101", "Pump", outlet_P=5, outlet_P_unit="bar", efficiency=0.75),
                _u("V-101", "Valve", outlet_P=1, outlet_P_unit="bar"),
            ],
            "connections": [
                _c("In",    "P-101", 0, 0),
                _c("P-101", "HighP", 0, 0),
                _c("W",     "P-101", 0, 1),
                _c("HighP", "V-101", 0, 0),
                _c("V-101", "Out",   0, 0),
            ],
        },
    },

    # ── REACTION ─────────────────────────────────────────────────────────────
    "conversion_reactor": {
        "category": "reaction",
        "description": "Conversion reactor placeholder: A → B, 80 % conversion (stoichiometry set by user).",
        "topology": {
            "name": "conversion_reactor",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Methanol", "Water"],
            "streams": [
                _s("Feed", T=150, T_unit="C", P=5, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Methanol": 1.0, "Water": 0.0}),
                _s("Product"),
                _e("Q"),
            ],
            "unit_ops": [
                _u("R-101", "ConversionReactor", conversion=0.8),
            ],
            "connections": [
                _c("Feed", "R-101", 0, 0),
                _c("R-101", "Product", 0, 0),
                _c("Q", "R-101", 0, 1),
            ],
        },
    },

    "gibbs_reactor": {
        "category": "reaction",
        "description": "Gibbs equilibrium reactor for water-gas shift.",
        "topology": {
            "name": "gibbs_wgs",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Carbon monoxide", "Water", "Carbon dioxide", "Hydrogen"],
            "streams": [
                _s("Feed", T=300, T_unit="C", P=20, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Carbon monoxide": 0.33, "Water": 0.33,
                                 "Carbon dioxide": 0.17, "Hydrogen": 0.17}),
                _s("Product"),
                _e("Q"),
            ],
            "unit_ops": [_u("R-GIBBS", "GibbsReactor")],
            "connections": [
                _c("Feed",    "R-GIBBS", 0, 0),
                _c("R-GIBBS", "Product", 0, 0),
                _c("Q",       "R-GIBBS", 0, 1),
            ],
        },
    },

    # ── RECYCLE LOOP ─────────────────────────────────────────────────────────
    "reactor_recycle": {
        "category": "recycle",
        "description": "Conversion reactor followed by flash + unconverted-feed recycle loop (OT_Recycle).",
        "topology": {
            "name": "reactor_recycle_loop",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Methanol", "Water"],
            "streams": [
                _s("FreshFeed", T=80, T_unit="C", P=5, P_unit="bar",
                   molar_flow=100, flow_unit="kmol/h",
                   compositions={"Methanol": 1.0, "Water": 0.0}),
                _s("Mixed"), _s("ReactorOut"),
                _s("Product"), _s("UnconvFeed"), _s("Recycle"),
                _e("Qr"),
            ],
            "unit_ops": [
                _u("MIX-01", "Mixer"),
                _u("R-101",  "ConversionReactor", conversion=0.6),
                _u("FLASH",  "Vessel"),
                _u("REC-01", "Recycle", max_iterations=20, tolerance=0.001),
            ],
            "connections": [
                _c("FreshFeed",  "MIX-01",      0, 0),
                _c("Recycle",    "MIX-01",      0, 1),
                _c("MIX-01",     "Mixed",       0, 0),
                _c("Mixed",      "R-101",       0, 0),
                _c("Qr",         "R-101",       0, 1),
                _c("R-101",      "ReactorOut",  0, 0),
                _c("ReactorOut", "FLASH",       0, 0),
                _c("FLASH",      "Product",     0, 0),
                _c("FLASH",      "UnconvFeed",  1, 0),
                _c("UnconvFeed", "REC-01",      0, 0),
                _c("REC-01",     "Recycle",     0, 0),
            ],
        },
    },

    # ── MIXING ───────────────────────────────────────────────────────────────
    "stream_blender": {
        "category": "simple",
        "description": "Mixer blending three feeds into a common outlet.",
        "topology": {
            "name": "three_way_blender",
            "property_package": "NRTL",
            "compounds": ["Water", "Ethanol", "Methanol"],
            "streams": [
                _s("A", T=25, T_unit="C", P=1, P_unit="bar",
                   molar_flow=50, flow_unit="kmol/h",
                   compositions={"Water": 1.0, "Ethanol": 0.0, "Methanol": 0.0}),
                _s("B", T=25, T_unit="C", P=1, P_unit="bar",
                   molar_flow=30, flow_unit="kmol/h",
                   compositions={"Water": 0.0, "Ethanol": 1.0, "Methanol": 0.0}),
                _s("C", T=25, T_unit="C", P=1, P_unit="bar",
                   molar_flow=20, flow_unit="kmol/h",
                   compositions={"Water": 0.0, "Ethanol": 0.0, "Methanol": 1.0}),
                _s("Blended"),
            ],
            "unit_ops": [_u("MIX-01", "Mixer")],
            "connections": [
                _c("A", "MIX-01", 0, 0),
                _c("B", "MIX-01", 0, 1),
                _c("C", "MIX-01", 0, 2),
                _c("MIX-01", "Blended", 0, 0),
            ],
        },
    },

    # ── RENEWABLES ───────────────────────────────────────────────────────────
    "water_electrolyzer": {
        "category": "renewables",
        "description": "Water electrolyzer producing hydrogen and oxygen.",
        "topology": {
            "name": "water_electrolyzer",
            "property_package": "Peng-Robinson (PR)",
            "compounds": ["Water", "Hydrogen", "Oxygen"],
            "streams": [
                _s("WaterIn", T=60, T_unit="C", P=1, P_unit="bar",
                   molar_flow=10, flow_unit="kmol/h",
                   compositions={"Water": 1.0, "Hydrogen": 0.0, "Oxygen": 0.0}),
                _s("H2Out"), _s("O2Out"),
                _e("W"),
            ],
            "unit_ops": [_u("EL-01", "WaterElectrolyzer")],
            "connections": [
                # WaterElectrolyzer: energy connector is at index 0,
                # material (water) inlet is at index 1.
                _c("W",       "EL-01", 0, 0),
                _c("WaterIn", "EL-01", 0, 1),
                _c("EL-01",   "H2Out", 0, 0),
                _c("EL-01",   "O2Out", 1, 0),
            ],
        },
    },
}


# Public API ────────────────────────────────────────────────────────────────
def list_templates() -> List[Dict[str, str]]:
    """Return [{name, category, description}, ...] for all templates."""
    return [
        {"name": name,
         "category": tpl.get("category", "other"),
         "description": tpl.get("description", "")}
        for name, tpl in sorted(TEMPLATES.items())
    ]


def get_template(name: str) -> Optional[Dict[str, Any]]:
    """Return a deep copy of the topology for a named template, or None."""
    tpl = TEMPLATES.get(name)
    if not tpl:
        return None
    return deepcopy(tpl["topology"])


def render_template(name: str,
                    overrides: Optional[Dict[str, Any]] = None
                    ) -> Optional[Dict[str, Any]]:
    """Return a topology dict with user overrides applied (shallow merge on
    top-level keys + per-stream / per-unit-op merges by tag)."""
    topology = get_template(name)
    if topology is None:
        return None
    if not overrides:
        return topology

    top_level = ("name", "save_path", "property_package", "run_simulation")
    for key in top_level:
        if key in overrides:
            topology[key] = overrides[key]

    if "compounds" in overrides:
        topology["compounds"] = list(overrides["compounds"])

    # Merge stream overrides by tag.
    stream_overrides = overrides.get("streams") or {}
    if isinstance(stream_overrides, dict):
        for s in topology.get("streams", []):
            patch = stream_overrides.get(s["tag"])
            if patch:
                s.update(patch)

    # Merge unit-op overrides by tag.
    uo_overrides = overrides.get("unit_ops") or {}
    if isinstance(uo_overrides, dict):
        for u in topology.get("unit_ops", []):
            patch = uo_overrides.get(u["tag"])
            if patch:
                u.update(patch)

    return topology
