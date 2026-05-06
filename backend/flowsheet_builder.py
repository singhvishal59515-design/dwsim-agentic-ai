"""
flowsheet_builder.py
────────────────────
Autonomous Flowsheet Generation for DWSIM.

Converts a JSON topology spec into a fully-wired DWSIM flowsheet:
  1. Validates topology
  2. Creates blank IFlowsheet
  3. Adds compounds + property package
  4. Adds material streams and unit operations
  5. Connects objects via port indices
  6. Sets initial conditions on streams/unit-ops
  7. Calls AutoLayout for clean visual layout
  8. Saves and runs simulation
  9. Returns convergence status

Usage (from bridge):
    from flowsheet_builder import build_flowsheet
    result = build_flowsheet(mgr, topology_dict)
"""

import io
import os
import re
import uuid
from contextlib import redirect_stdout, redirect_stderr
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# ObjectType enum name mapping
# (from probe: DWSIM.Interfaces.Enums.GraphicObjects.ObjectType)
# ─────────────────────────────────────────────────────────────────────────────

OBJECT_TYPE_MAP: Dict[str, str] = {
    # ── Material / energy streams ────────────────────────────────────────────
    "materialstream":           "MaterialStream",
    "material stream":          "MaterialStream",
    "material_stream":          "MaterialStream",
    "stream":                   "MaterialStream",
    "energystream":             "EnergyStream",
    "energy stream":            "EnergyStream",
    "energy_stream":            "EnergyStream",
    "heat stream":              "EnergyStream",
    "work stream":              "EnergyStream",

    # ── Heat transfer ────────────────────────────────────────────────────────
    "heater":                   "Heater",
    "cooler":                   "Cooler",
    "heatercooler":             "HeaterCooler",
    "heater cooler":            "HeaterCooler",
    "heater/cooler":            "HeaterCooler",
    "heatexchanger":            "HeatExchanger",
    "heat exchanger":           "HeatExchanger",
    "heat_exchanger":           "HeatExchanger",
    "he":                       "HeatExchanger",
    "hx":                       "HeatExchanger",
    "shell and tube":           "HeatExchanger",
    "aircooler":                "AirCooler2",
    "air cooler":               "AirCooler2",
    "air_cooler":               "AirCooler2",
    "aircooler2":               "AirCooler2",

    # ── Pressure changers ────────────────────────────────────────────────────
    "pump":                     "Pump",
    "compressor":               "Compressor",
    "expander":                 "Expander",
    "turbine":                  "Expander",
    "turboexpander":            "Expander",
    "compressorexpander":       "CompressorExpander",
    "valve":                    "Valve",
    "control valve":            "Valve",
    "throttling valve":         "Valve",
    "orifice":                  "OrificePlate",
    "orifice plate":            "OrificePlate",
    "orificeplate":             "OrificePlate",

    # ── Mixers / splitters ──────────────────────────────────────────────────
    # NOTE: NodeIn / NodeOut are the proprietary DWSIM mixer/splitter names;
    # Mixer / Splitter are the CAPE-OPEN compatible variants. Both work.
    "mixer":                    "Mixer",
    "stream mixer":             "Mixer",
    "stream_mixer":             "Mixer",
    "nodein":                   "NodeIn",
    "node in":                  "NodeIn",
    "splitter":                 "Splitter",
    "stream splitter":          "Splitter",
    "stream_splitter":          "Splitter",
    "flow splitter":            "Splitter",
    "nodeout":                  "NodeOut",
    "node out":                 "NodeOut",
    "nodeen":                   "NodeEn",
    "energymixer":              "EnergyMixer",
    "energy mixer":             "EnergyMixer",
    "energy_mixer":             "EnergyMixer",

    # ── Separators / Flash drums ────────────────────────────────────────────
    # "Vessel" is our preferred mapping for flash drums (TPVessel has a
    # NullReferenceException bug in AddObjectToSurface on some builds).
    "separator":                "Vessel",
    "flash":                    "Vessel",
    "flash drum":               "Vessel",
    "flash tank":               "Vessel",
    "flash separator":          "Vessel",
    "flash vessel":             "Vessel",
    "flashdrum":                "Vessel",
    "flash_drum":               "Vessel",
    "flash_tank":               "Vessel",
    "flash_separator":          "Vessel",
    "flash_vessel":             "Vessel",
    "ko drum":                  "Vessel",
    "knockout drum":            "Vessel",
    "lv separator":             "Vessel",
    "lv_separator":             "Vessel",
    "gas-liquid separator":     "Vessel",
    "gas_liquid_separator":     "Vessel",
    "equilibrium separator":    "Vessel",
    "equilibrium_separator":    "Vessel",
    "two phase separator":      "Vessel",
    "three phase separator":    "Vessel",
    "tpvessel":                 "TPVessel",
    "vessel":                   "Vessel",
    "tank":                     "Tank",
    "storage tank":             "Tank",
    "buffer tank":              "Tank",

    # ── Columns ──────────────────────────────────────────────────────────────
    "distillationcolumn":       "DistillationColumn",
    "distillation column":      "DistillationColumn",
    "distillation_column":      "DistillationColumn",
    "column":                   "DistillationColumn",
    "rigorous column":          "DistillationColumn",
    "absorptioncolumn":         "AbsorptionColumn",
    "absorption column":        "AbsorptionColumn",
    "absorption_column":        "AbsorptionColumn",
    "absorber":                 "AbsorptionColumn",
    "stripper":                 "AbsorptionColumn",
    "shortcutcolumn":           "ShortcutColumn",
    "shortcut column":          "ShortcutColumn",
    "shortcut_column":          "ShortcutColumn",
    "fenske-underwood-gilliland": "ShortcutColumn",
    "refluxedabsorber":         "RefluxedAbsorber",
    "refluxed absorber":        "RefluxedAbsorber",
    "reboiledabsorber":         "ReboiledAbsorber",
    "reboiled absorber":        "ReboiledAbsorber",

    # ── Reactors ─────────────────────────────────────────────────────────────
    "cstr":                     "RCT_CSTR",
    "continuous stirred tank reactor": "RCT_CSTR",
    "pfr":                      "RCT_PFR",
    "plug flow reactor":        "RCT_PFR",
    "tubular reactor":          "RCT_PFR",
    "gibbsreactor":             "RCT_Gibbs",
    "gibbs reactor":            "RCT_Gibbs",
    "gibbs_reactor":            "RCT_Gibbs",
    "gibbs":                    "RCT_Gibbs",
    "gibbsreaktoro":            "RCT_GibbsReaktoro",
    "gibbs reaktoro":           "RCT_GibbsReaktoro",
    "electrolyte reactor":      "RCT_GibbsReaktoro",
    "conversionreactor":        "RCT_Conversion",
    "conversion reactor":       "RCT_Conversion",
    "equilibriumreactor":       "RCT_Equilibrium",
    "equilibrium reactor":      "RCT_Equilibrium",

    # ── Piping ───────────────────────────────────────────────────────────────
    "pipe":                     "Pipe",
    "pipe segment":             "Pipe",
    "pipesegment":              "Pipe",

    # ── Solids operations ───────────────────────────────────────────────────
    "filter":                   "Filter",
    "cake filter":              "Filter",
    "solidseparator":           "SolidSeparator",
    "solid separator":          "SolidSeparator",
    "solidops":                 "SolidOps",
    "solid ops":                "SolidOps",

    # ── Logical / convergence blocks (essential for recycle loops) ──────────
    "recycle":                  "OT_Recycle",
    "ot_recycle":               "OT_Recycle",
    "energy recycle":           "OT_EnergyRecycle",
    "ot_energyrecycle":         "OT_EnergyRecycle",
    "adjust":                   "OT_Adjust",
    "ot_adjust":                "OT_Adjust",
    "design spec":              "OT_Adjust",
    "specification":            "OT_Spec",
    "spec":                     "OT_Spec",
    "ot_spec":                  "OT_Spec",

    # ── Controllers ──────────────────────────────────────────────────────────
    "pid":                      "Controller_PID",
    "pid controller":           "Controller_PID",
    "controller_pid":           "Controller_PID",
    "controller":               "Controller_PID",
    "python controller":        "Controller_Python",
    "controller_python":        "Controller_Python",

    # ── Energy conversion (renewables / fuel cells) ─────────────────────────
    "hydroelectricturbine":     "HydroelectricTurbine",
    "hydroelectric turbine":    "HydroelectricTurbine",
    "solarpanel":               "SolarPanel",
    "solar panel":              "SolarPanel",
    "windturbine":              "WindTurbine",
    "wind turbine":             "WindTurbine",
    "pemfuelcell":              "PEMFuelCell",
    "pem fuel cell":            "PEMFuelCell",
    "fuel cell":                "PEMFuelCell",
    "waterelectrolyzer":        "WaterElectrolyzer",
    "water electrolyzer":       "WaterElectrolyzer",
    "electrolyzer":             "WaterElectrolyzer",

    # ── Extensibility (custom / CAPE-OPEN / external) ───────────────────────
    "customuo":                 "CustomUO",
    "custom uo":                "CustomUO",
    "python script uo":         "CustomUO",
    "capeopen":                 "CapeOpenUO",
    "cape-open":                "CapeOpenUO",
    "cape open":                "CapeOpenUO",
    "capeopenuo":               "CapeOpenUO",
    "exceluo":                  "ExcelUO",
    "excel uo":                 "ExcelUO",
    "flowsheetuo":              "FlowsheetUO",
    "sub-flowsheet":            "FlowsheetUO",
    "external":                 "External",

    # ── Misc ─────────────────────────────────────────────────────────────────
    "componentseparator":       "ComponentSeparator",
    "component separator":      "ComponentSeparator",
    "compound separator":       "ComponentSeparator",
    "switch":                   "Switch",
    "input":                    "Input",

    # ── Indicators / gauges (usually cosmetic) ──────────────────────────────
    "analog gauge":             "AnalogGauge",
    "digital gauge":            "DigitalGauge",
    "level gauge":              "LevelGauge",
}


# Every concrete ObjectType value DWSIM supports (from
# DWSIM.Interfaces.Enums.GraphicObjects.ObjectType). Used for error messages
# so users get a valid-name list instead of guessing.
SUPPORTED_OBJECT_TYPES: List[str] = [
    "AbsorptionColumn", "AirCooler2", "CapeOpenUO", "ComponentSeparator",
    "Compressor", "CompressorExpander", "Controller_PID", "Controller_Python",
    "Cooler", "CustomUO", "DistillationColumn", "EnergyMixer", "EnergyStream",
    "ExcelUO", "Expander", "External", "Filter", "FlowsheetUO",
    "HeatExchanger", "Heater", "HeaterCooler", "HydroelectricTurbine",
    "MaterialStream", "Mixer", "NodeEn", "NodeIn", "NodeOut",
    "OT_Adjust", "OT_EnergyRecycle", "OT_Recycle", "OT_Spec",
    "OrificePlate", "PEMFuelCell", "Pipe", "Pump",
    "RCT_CSTR", "RCT_Conversion", "RCT_Equilibrium", "RCT_Gibbs",
    "RCT_GibbsReaktoro", "RCT_PFR",
    "ReboiledAbsorber", "RefluxedAbsorber", "ShortcutColumn", "SolarPanel",
    "SolidOps", "SolidSeparator", "Splitter", "Switch",
    "TPVessel", "Tank", "Valve", "Vessel", "WaterElectrolyzer", "WindTurbine",
]


def _resolve_type(type_str: str) -> Optional[str]:
    """Map user type string to ObjectType enum name.
    Tries the alias map first, then a case-insensitive direct match against
    the canonical enum names. Returns None if nothing matches.
    """
    key = (type_str or "").lower().strip()
    if not key:
        return None
    hit = OBJECT_TYPE_MAP.get(key)
    if hit:
        return hit
    # Direct case-insensitive match on the canonical enum name.
    for canonical in SUPPORTED_OBJECT_TYPES:
        if canonical.lower() == key:
            return canonical
    return None


def _suggest_types(type_str: str, limit: int = 5) -> List[str]:
    """Return the closest-matching canonical type names for an unknown input."""
    import difflib
    key = (type_str or "").lower().strip()
    alias_hits = [OBJECT_TYPE_MAP[k] for k in OBJECT_TYPE_MAP
                  if key and key in k]
    # Deduplicate preserving order.
    seen = set()
    alias_hits = [x for x in alias_hits if not (x in seen or seen.add(x))]
    if len(alias_hits) >= limit:
        return alias_hits[:limit]
    # Fall back to difflib over the canonical set.
    fuzzy = difflib.get_close_matches(
        key, [n.lower() for n in SUPPORTED_OBJECT_TYPES],
        n=limit, cutoff=0.4)
    by_lower = {n.lower(): n for n in SUPPORTED_OBJECT_TYPES}
    for name in fuzzy:
        canonical = by_lower[name]
        if canonical not in alias_hits:
            alias_hits.append(canonical)
    return alias_hits[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Property setters for streams and unit ops
# ─────────────────────────────────────────────────────────────────────────────

def _get_phase0_props(obj):
    """
    Get Phase[0].Properties from an ISimulationObject.
    Uses multiple strategies because the interface only exposes ISimulationObject,
    not the concrete MaterialStream class, so Phases may not be directly accessible.
    """
    # Strategy 1: direct attribute access (works on concrete class)
    for key in (0, "0"):
        try:
            phase = obj.Phases[key]
            if phase is not None:
                return getattr(phase, "Properties", phase)
        except Exception:
            pass

    # Strategy 2: reflection — pythonnet may box the interface; try GetType().GetProperty
    try:
        phases_prop = obj.GetType().GetProperty("Phases")
        if phases_prop:
            phases = phases_prop.GetValue(obj)
            if phases is not None:
                for key in (0, "0"):
                    try:
                        phase = phases[key]
                        if phase is not None:
                            return getattr(phase, "Properties", phase)
                    except Exception:
                        pass
                # Try .Values iteration
                try:
                    vals = list(phases.Values)
                    if vals:
                        phase = vals[0]
                        return getattr(phase, "Properties", phase)
                except Exception:
                    pass
    except Exception:
        pass

    return None


def _get_phase0_compounds(obj):
    """Get Phase[0].Compounds dictionary from an ISimulationObject."""
    phase_props = _get_phase0_props(obj)
    if phase_props is None:
        return None

    # Phase[0].Compounds may be on the phase itself, not on Properties
    for key in (0, "0"):
        try:
            phase = obj.Phases[key]
            if phase is not None:
                cmpds = getattr(phase, "Compounds", None)
                if cmpds is not None:
                    return cmpds
        except Exception:
            pass

    # Reflection fallback
    try:
        phases_prop = obj.GetType().GetProperty("Phases")
        if phases_prop:
            phases = phases_prop.GetValue(obj)
            if phases is not None:
                vals = list(phases.Values)
                if vals:
                    phase = vals[0]
                    cmpds = getattr(phase, "Compounds", None)
                    if cmpds is None:
                        cp = phase.GetType().GetProperty("Compounds")
                        if cp:
                            cmpds = cp.GetValue(phase)
                    return cmpds
    except Exception:
        pass
    return None


def _set_phase_prop(obj, attr_name: str, value: float) -> bool:
    """Set a property on Phase[0].Properties via multiple strategies."""
    pp = _get_phase0_props(obj)
    if pp is None:
        return False
    # Direct setattr
    try:
        setattr(pp, attr_name, value)
        return True
    except Exception:
        pass
    # Reflection
    try:
        prop = pp.GetType().GetProperty(attr_name)
        if prop and prop.CanWrite:
            prop.SetValue(pp, value)
            return True
    except Exception:
        pass
    return False


def _set_stream_initial_conditions(obj, spec: Dict, compounds: List[str]) -> List[str]:
    """
    Set T, P, flow, and composition on a newly created material stream.
    Returns list of warnings.
    """
    warnings = []

    # Temperature
    T_val = spec.get("T") or spec.get("temperature")
    T_unit = spec.get("T_unit", "K")
    if T_val is not None:
        T_K = float(T_val) + 273.15 if T_unit.upper() in ("C", "CELSIUS") else float(T_val)
        if not _set_phase_prop(obj, "temperature", T_K):
            warnings.append(f"Could not set temperature on {spec.get('tag','stream')}")

    # Pressure
    P_val = spec.get("P") or spec.get("pressure")
    P_unit = spec.get("P_unit", "Pa")
    if P_val is not None:
        conv = {"bar": 1e5, "kpa": 1e3, "atm": 101325.0, "psi": 6894.757, "mpa": 1e6}
        P_Pa = float(P_val) * conv.get(P_unit.lower(), 1.0)
        if not _set_phase_prop(obj, "pressure", P_Pa):
            warnings.append(f"Could not set pressure on {spec.get('tag','stream')}")

    # Molar flow — use explicit None check so flow=0 isn't silently skipped
    F_val = next(
        (spec[k] for k in ("molar_flow", "F", "flow") if k in spec and spec[k] is not None),
        None,
    )
    F_unit = spec.get("flow_unit", "mol/s")
    if F_val is not None:
        conv = {"mol/h": 1/3600, "kmol/h": 1000/3600, "mol/min": 1/60, "mol/s": 1.0}
        F_SI = float(F_val) * conv.get(F_unit.lower(), 1.0)
        if not _set_phase_prop(obj, "molarflow", F_SI):
            if not _set_phase_prop(obj, "massflow", F_SI):
                warnings.append(f"Could not set flow on {spec.get('tag','stream')}")

    # Mass flow (alternative)
    mf_val = spec.get("mass_flow")
    if mf_val is not None and F_val is None:
        mf_conv = {"kg/h": 1/3600, "t/h": 1000/3600, "kg/s": 1.0}
        mf_unit = spec.get("mass_flow_unit", "kg/s")
        mf_SI = float(mf_val) * mf_conv.get(mf_unit.lower(), 1.0)
        if not _set_phase_prop(obj, "massflow", mf_SI):
            warnings.append(f"Could not set mass flow on {spec.get('tag','stream')}")

    # Compositions (mole fractions)
    comps = spec.get("compositions") or spec.get("composition") or {}
    if comps:
        total = sum(comps.values())
        if abs(total - 1.0) > 0.01:
            warnings.append(f"Compositions sum to {total:.4f}, not 1.0 — normalising")
            comps = {k: v/total for k, v in comps.items()}
        phase_cmpds = _get_phase0_compounds(obj)
        if phase_cmpds is not None:
            for comp_name, mf in comps.items():
                matched_key = _fuzzy_match_compound(comp_name, compounds)
                if matched_key:
                    try:
                        c_obj = phase_cmpds[matched_key]
                        for mf_attr in ("MoleFraction", "molefraction"):
                            try:
                                setattr(c_obj, mf_attr, float(mf))
                                break
                            except Exception:
                                pass
                    except Exception:
                        warnings.append(f"Compound {comp_name!r} not found in stream phases")
                else:
                    warnings.append(f"Compound {comp_name!r} not in DB compounds list")
        else:
            warnings.append(f"Could not access Phase[0].Compounds on {spec.get('tag','stream')}")

    # Vapor fraction
    vf = spec.get("vapor_fraction") or spec.get("VF")
    if vf is not None and T_val is None:
        if not _set_phase_prop(obj, "vaporfraction", float(vf)):
            warnings.append(f"Could not set vapor fraction on {spec.get('tag','stream')}")

    return warnings


def _set_unitop_initial_conditions(obj, spec: Dict, type_name: str) -> List[str]:
    """
    Set initial operating conditions on a unit operation.
    Returns list of warnings.
    """
    warnings = []
    _sink = io.StringIO()

    # Generic property setter using setattr with common DWSIM property names
    prop_map = {
        # key in spec -> DWSIM property name
        "outlet_T":         ("OutletTemperature",   lambda v, u: float(v) + 273.15 if u in ("C","c") else float(v)),
        "temperature":      ("OutletTemperature",   lambda v, u: float(v) + 273.15 if u in ("C","c") else float(v)),
        "outlet_P":         ("OutletPressure",      lambda v, u: float(v) * 1e5 if u == "bar" else float(v)),
        "pressure_drop":    ("DeltaP",              lambda v, _: float(v) * 1e5 if _ == "bar" else float(v)),
        "duty":             ("DeltaQ",              lambda v, _: float(v)),
        "efficiency":       ("AdiabaticEfficiency", lambda v, _: float(v)),
        "head":             ("DeltaP",              lambda v, _: float(v)),
        "reflux_ratio":     ("RefluxRatio",         lambda v, _: float(v)),
        "number_of_stages": ("NumberOfStages",      lambda v, _: int(v)),
        "stages":           ("NumberOfStages",      lambda v, _: int(v)),
        "condenser_duty":   ("CondenserDuty",       lambda v, _: float(v)),
        "reboiler_duty":    ("ReboilerDuty",        lambda v, _: float(v)),
        "volume":           ("Volume",              lambda v, _: float(v)),
        "length":           ("Length",              lambda v, _: float(v)),
        "diameter":         ("Diameter",            lambda v, _: float(v)),
        "conversion":       ("Conversion",          lambda v, _: float(v)),
        "split_ratio":      ("SplitRatio",          lambda v, _: float(v)),
        # ── Logical blocks ────────────────────────────────────────────────
        "max_iterations":   ("MaximumIterations",   lambda v, _: int(v)),
        "tolerance":        ("Tolerance",           lambda v, _: float(v)),
        "acceleration":     ("AccelerationMethod",  lambda v, _: str(v)),
        # ── PID controller ────────────────────────────────────────────────
        "setpoint":         ("SetPoint",            lambda v, _: float(v)),
        "kp":               ("Kp",                  lambda v, _: float(v)),
        "ki":               ("Ki",                  lambda v, _: float(v)),
        "kd":               ("Kd",                  lambda v, _: float(v)),
    }

    for spec_key, (dwsim_attr, conv_fn) in prop_map.items():
        if spec_key not in spec:
            continue
        val = spec[spec_key]
        unit = spec.get(f"{spec_key}_unit", "")
        try:
            converted = conv_fn(val, unit)
            with redirect_stdout(_sink), redirect_stderr(_sink):
                try:
                    setattr(obj, dwsim_attr, converted)
                except Exception:
                    # Try via reflection
                    prop = obj.GetType().GetProperty(dwsim_attr)
                    if prop and prop.CanWrite:
                        prop.SetValue(obj, converted)
        except Exception as e:
            warnings.append(f"Could not set {spec_key} on {spec.get('tag','?')}: {e}")

    # For Heater/Cooler: use reflection to set CalcMode enum and Nullable<Double>
    # properties properly (setattr on ISimulationObject doesn't work for these).
    if type_name in ("Heater", "Cooler"):
        import System  # type: ignore

        # 1) Set CalcMode to OutletTemperature if outlet_T or temperature is specified
        if "outlet_T" in spec or "temperature" in spec:
            _spec_set = False
            for attr in ("CalcMode", "SpecType", "CalculationMode"):
                try:
                    prop = obj.GetType().GetProperty(attr)
                    if prop and prop.CanWrite and prop.PropertyType.IsEnum:
                        ot_val = System.Enum.Parse(prop.PropertyType, "OutletTemperature")
                        with redirect_stdout(_sink), redirect_stderr(_sink):
                            prop.SetValue(obj, ot_val)
                        _spec_set = True
                        break
                except Exception:
                    pass
            if not _spec_set:
                warnings.append(
                    f"Could not set CalcMode=OutletTemperature on {spec.get('tag','?')}")

        # 2) Re-set OutletTemperature via reflection with Nullable<Double> wrapping
        #    (the generic setattr above may fail silently on Nullable properties)
        T_spec = spec.get("outlet_T") or spec.get("temperature")
        if T_spec is not None:
            T_unit = spec.get("outlet_T_unit", "") or spec.get("temperature_unit", "")
            T_K = float(T_spec) + 273.15 if T_unit.lower() in ("c", "celsius") else float(T_spec)
            try:
                ot_prop = obj.GetType().GetProperty("OutletTemperature")
                if ot_prop and ot_prop.CanWrite:
                    ot_prop.SetValue(obj, System.Nullable[System.Double](T_K))
            except Exception as e:
                warnings.append(f"Reflection OutletTemperature failed: {e}")

        # 3) Re-set DeltaP via reflection with Nullable<Double>
        dp_val = spec.get("pressure_drop")
        if dp_val is not None:
            dp_unit = spec.get("pressure_drop_unit", "")
            dp_Pa = float(dp_val) * 1e5 if dp_unit == "bar" else float(dp_val)
            try:
                dp_prop = obj.GetType().GetProperty("DeltaP")
                if dp_prop and dp_prop.CanWrite:
                    dp_prop.SetValue(obj, System.Nullable[System.Double](dp_Pa))
            except Exception:
                pass

    # ── Pump / Compressor / Expander: set CalcMode + outlet pressure ─────────
    if type_name in ("Pump", "Compressor", "Expander", "CompressorExpander"):
        import System  # type: ignore

        op_val  = spec.get("outlet_P")
        op_unit = spec.get("outlet_P_unit", "")
        dp_val  = spec.get("pressure_drop")
        dp_unit = spec.get("pressure_drop_unit", "")

        if op_val is not None:
            op_Pa = float(op_val) * 1e5 if op_unit.lower() == "bar" else float(op_val)
            # 1) Set CalcMode to outlet-pressure variant
            for mode_name in ("OutletPressure", "Outlet_Pressure",
                              "PressureOut", "P_Outlet"):
                for attr in ("CalcMode", "CalculationMode", "SpecType"):
                    try:
                        prop = obj.GetType().GetProperty(attr)
                        if prop and prop.CanWrite and prop.PropertyType.IsEnum:
                            ev = System.Enum.Parse(prop.PropertyType, mode_name)
                            with redirect_stdout(_sink), redirect_stderr(_sink):
                                prop.SetValue(obj, ev)
                            break
                    except Exception:
                        continue
                else:
                    continue
                break
            # 2) Set OutletPressure (try several property names)
            for pname in ("OutletPressure", "Pout", "PressureOut", "P_Outlet"):
                try:
                    prop = obj.GetType().GetProperty(pname)
                    if prop and prop.CanWrite:
                        prop.SetValue(obj, System.Nullable[System.Double](op_Pa))
                        break
                except Exception:
                    pass

        elif dp_val is not None:
            # DeltaP mode (pressure increase for pump; pressure drop for valve/expander)
            dp_Pa = float(dp_val) * 1e5 if dp_unit.lower() == "bar" else float(dp_val)
            for mode_name in ("PressureIncrease", "DeltaP", "Pressure_Increase"):
                for attr in ("CalcMode", "CalculationMode", "SpecType"):
                    try:
                        prop = obj.GetType().GetProperty(attr)
                        if prop and prop.CanWrite and prop.PropertyType.IsEnum:
                            ev = System.Enum.Parse(prop.PropertyType, mode_name)
                            with redirect_stdout(_sink), redirect_stderr(_sink):
                                prop.SetValue(obj, ev)
                            break
                    except Exception:
                        continue
                else:
                    continue
                break
            for pname in ("DeltaP", "PressureIncrease", "DeltaPressure"):
                try:
                    prop = obj.GetType().GetProperty(pname)
                    if prop and prop.CanWrite:
                        prop.SetValue(obj, System.Nullable[System.Double](dp_Pa))
                        break
                except Exception:
                    pass

    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Compound / PP fuzzy matching
# ─────────────────────────────────────────────────────────────────────────────

def _fuzzy_match_compound(name: str, available: List[str]) -> Optional[str]:
    """Return the best matching compound name from available list."""
    nl = name.lower().strip()
    # Exact match
    for a in available:
        if a.lower() == nl:
            return a
    # Partial match
    for a in available:
        if nl in a.lower() or a.lower() in nl:
            return a
    return None


def _fuzzy_match_pp(name: str, available: List[str]) -> Optional[str]:
    """Return best matching property package key."""
    nl = name.lower().strip()
    # Exact
    for a in available:
        if a.lower() == nl:
            return a
    # Common aliases
    aliases = {
        "pr":       "peng-robinson (pr)",
        "peng robinson": "peng-robinson (pr)",
        "srk":      "soave-redlich-kwong (srk)",
        "nrtl":     "nrtl",
        "unifac":   "unifac",
        "raoult":   "raoult's law",
        "raoults":  "raoult's law",
        "steam":    "steam tables (iapws-if97)",
        "iapws":    "steam tables (iapws-if97)",
        "coolprop": "coolprop",
        "pc-saft":  "pc-saft",
        "pcsaft":   "pc-saft (with association support) (.net code)",
        "gerg":     "gerg-2008",
    }
    alias_match = aliases.get(nl)
    if alias_match:
        for a in available:
            if alias_match in a.lower():
                return a
    # Partial
    for a in available:
        if nl in a.lower():
            return a
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Layout: assign x,y positions in a left-to-right DAG layout
# ─────────────────────────────────────────────────────────────────────────────

def _compute_layout(streams: List[Dict], unit_ops: List[Dict],
                    connections: List[Dict]) -> Dict[str, Tuple[int, int]]:
    """
    Assign (x, y) pixel positions for each object tag using a simple
    topological layout:
      - Column (depth) = longest path from any source node
      - Row (within column) = enumerated order
    Returns {tag: (x, y)} dict.
    """
    # Build adjacency: from_tag -> to_tag
    adj: Dict[str, List[str]] = {}
    in_degree: Dict[str, int] = {}
    all_tags = [s["tag"] for s in streams] + [u["tag"] for u in unit_ops]

    for tag in all_tags:
        adj[tag] = []
        in_degree[tag] = 0

    for conn in connections:
        frm = conn.get("from", "")
        to  = conn.get("to", "")
        if frm in adj and to in all_tags:
            adj[frm].append(to)
            in_degree[to] = in_degree.get(to, 0) + 1

    # BFS topological sort — compute column depths
    from collections import deque
    depths: Dict[str, int] = {t: 0 for t in all_tags}
    visited: set = set()
    queue = deque([t for t in all_tags if in_degree.get(t, 0) == 0])
    while queue:
        tag = queue.popleft()
        if tag in visited:
            continue
        visited.add(tag)
        for nxt in adj.get(tag, []):
            depths[nxt] = max(depths.get(nxt, 0), depths[tag] + 1)
            queue.append(nxt)

    # Group by depth
    cols: Dict[int, List[str]] = {}
    for tag, d in depths.items():
        cols.setdefault(d, []).append(tag)

    # Assign pixel coordinates
    X_START, X_STEP = 100, 250
    Y_START, Y_STEP = 150, 120
    positions: Dict[str, Tuple[int, int]] = {}
    for col_idx, col_tags in sorted(cols.items()):
        x = X_START + col_idx * X_STEP
        for row_idx, tag in enumerate(col_tags):
            y = Y_START + row_idx * Y_STEP
            positions[tag] = (x, y)

    return positions


# ─────────────────────────────────────────────────────────────────────────────
# Topology validator
# ─────────────────────────────────────────────────────────────────────────────

def validate_topology(topology: Dict) -> List[str]:
    """
    Validate topology dict. Returns list of errors (empty = valid).
    """
    errors = []
    required_keys = ["compounds", "property_package", "streams", "connections"]
    for k in required_keys:
        if k not in topology:
            errors.append(f"Missing required key: '{k}'")

    compounds = topology.get("compounds", [])
    if not compounds:
        errors.append("'compounds' must be a non-empty list")

    streams = topology.get("streams", [])
    unit_ops = topology.get("unit_ops", [])
    all_tags = set()

    for s in streams:
        if "tag" not in s:
            errors.append(f"Stream missing 'tag': {s}")
            continue
        tag = s["tag"]
        if tag in all_tags:
            errors.append(f"Duplicate tag: {tag!r}")
        all_tags.add(tag)
        t = s.get("type", "MaterialStream")
        if _resolve_type(t) is None:
            hints = _suggest_types(t)
            hint_txt = f" — did you mean {hints}?" if hints else ""
            errors.append(f"Unknown stream type {t!r} for tag {tag!r}{hint_txt}")
        comps = s.get("compositions") or {}
        if comps:
            total = sum(comps.values())
            if abs(total - 1.0) > 0.02:
                errors.append(f"Stream {tag!r} compositions sum to {total:.4f}, not 1.0")

    for u in unit_ops:
        if "tag" not in u:
            errors.append(f"Unit op missing 'tag': {u}")
            continue
        tag = u["tag"]
        if tag in all_tags:
            errors.append(f"Duplicate tag: {tag!r}")
        all_tags.add(tag)
        t = u.get("type", "")
        if not t:
            errors.append(f"Unit op {tag!r} missing 'type'")
        elif _resolve_type(t) is None:
            hints = _suggest_types(t)
            hint_txt = f" — did you mean {hints}?" if hints else ""
            errors.append(f"Unknown unit op type {t!r} for tag {tag!r}{hint_txt}")

    for conn in topology.get("connections", []):
        frm = conn.get("from")
        to  = conn.get("to")
        if not frm:
            errors.append(f"Connection missing 'from': {conn}")
        elif frm not in all_tags:
            errors.append(f"Connection 'from' tag {frm!r} not defined")
        if not to:
            errors.append(f"Connection missing 'to': {conn}")
        elif to not in all_tags:
            errors.append(f"Connection 'to' tag {to!r} not defined")

    return errors


# ─────────────────────────────────────────────────────────────────────────────
# Main builder function
# ─────────────────────────────────────────────────────────────────────────────

def build_flowsheet(mgr, topology: Dict) -> Dict[str, Any]:
    """
    Build a DWSIM flowsheet from a topology dict.

    topology keys:
      name (str, optional)          -- flowsheet name
      save_path (str, optional)     -- where to save (.dwxmz); defaults to Documents/name.dwxmz
      property_package (str)        -- e.g. "Peng-Robinson (PR)", "NRTL"
      compounds (list[str])         -- e.g. ["Water", "Methanol"]
      streams (list[dict])          -- see below
      unit_ops (list[dict])         -- see below
      connections (list[dict])      -- {from, to, from_port (opt), to_port (opt)}
      run_simulation (bool)         -- default True

    stream dict keys:
      tag (str)                     -- display name
      type (str)                    -- "MaterialStream" or "EnergyStream"
      T (float), T_unit (str)       -- temperature (K or C)
      P (float), P_unit (str)       -- pressure (Pa, bar, kPa, atm, psi)
      molar_flow (float), flow_unit -- mol/s, mol/h, kmol/h
      mass_flow (float)             -- kg/s (alternative to molar_flow)
      compositions (dict)           -- {compound: mole_fraction}

    unit_op dict keys:
      tag (str)                     -- display name
      type (str)                    -- e.g. "Heater", "HeatExchanger", "DistillationColumn"
      (+ type-specific properties)
    """
    import System  # type: ignore
    _sink = io.StringIO()

    # ── 0. Validate ─────────────────────────────────────────────────────────
    errors = validate_topology(topology)
    if errors:
        return {"success": False, "error": "Topology validation failed",
                "validation_errors": errors}

    warnings: List[str] = []
    name = topology.get("name", "generated_flowsheet")
    safe_name = re.sub(r"[^\w\-_]", "_", name)

    save_path = topology.get("save_path")
    if not save_path:
        docs = os.path.expanduser("~/Documents")
        save_path = os.path.join(docs, f"{safe_name}.dwxmz")

    # ── 1. Create blank flowsheet ────────────────────────────────────────────
    try:
        with redirect_stdout(_sink), redirect_stderr(_sink):
            fs = mgr.CreateFlowsheet()
    except Exception as e:
        return {"success": False, "error": f"CreateFlowsheet failed: {e}"}

    # ── 2. Add compounds ─────────────────────────────────────────────────────
    avail_comps = [c.Key for c in mgr.AvailableCompounds]
    added_compounds: List[str] = []

    import difflib
    avail_lower = {c.lower(): c for c in avail_comps}
    for comp_name in topology["compounds"]:
        matched = _fuzzy_match_compound(comp_name, avail_comps)
        if matched is None:
            close = difflib.get_close_matches(
                comp_name.lower(), list(avail_lower.keys()), n=3, cutoff=0.6)
            suggestions = [avail_lower[c] for c in close]
            msg = f"Compound {comp_name!r} not in DWSIM database — skipped"
            if suggestions:
                msg += f" (did you mean: {suggestions}?)"
            warnings.append(msg)
            continue
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                fs.AddCompound(matched)
            added_compounds.append(matched)
        except Exception as e:
            warnings.append(f"AddCompound({matched!r}) failed: {e}")

    if not added_compounds:
        return {"success": False,
                "error": "No compounds could be added — check compound names",
                "warnings": warnings}

    # ── 3. Add property package ──────────────────────────────────────────────
    avail_pps = [p.Key for p in mgr.AvailablePropertyPackages]
    pp_name = topology["property_package"]
    matched_pp = _fuzzy_match_pp(pp_name, avail_pps)

    if matched_pp is None:
        warnings.append(f"Property package {pp_name!r} not found — using Peng-Robinson (PR)")
        matched_pp = next((p for p in avail_pps if "peng-robinson (pr)" in p.lower()), avail_pps[0])

    try:
        with redirect_stdout(_sink), redirect_stderr(_sink):
            fs.CreateAndAddPropertyPackage(matched_pp)
    except Exception as e:
        warnings.append(f"CreateAndAddPropertyPackage({matched_pp!r}) failed: {e}")

    # ── 4. Get ObjectType enum + System.Int32 for AddObject calls ────────────
    obj_type_enum_type = None
    try:
        add_methods = [m for m in fs.GetType().GetMethods() if m.Name == "AddObject"]
        for mi in add_methods:
            params = mi.GetParameters()
            if len(list(params)) >= 4:
                obj_type_enum_type = list(params)[0].ParameterType
                break
    except Exception as e:
        warnings.append(f"Could not get ObjectType enum type: {e}")

    def _get_enum_val(type_name_str: str):
        """Return the .NET ObjectType enum value for a given enum name string."""
        if obj_type_enum_type is None:
            return None
        try:
            return System.Enum.Parse(obj_type_enum_type, type_name_str)
        except Exception:
            return None

    def _add_object(enum_name: str, tag: str, x: int, y: int):
        """Add an object to the flowsheet. Returns sim object or None."""
        enum_val = _get_enum_val(enum_name)
        if enum_val is None:
            warnings.append(f"Cannot resolve ObjectType.{enum_name}")
            return None
        for attempt in [
            (enum_val, System.Int32(x), System.Int32(y), tag),
            (enum_val, System.Int32(x), System.Int32(y)),
        ]:
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    obj = fs.AddObject(*attempt)
                if obj is not None:
                    return obj
            except Exception:
                continue
        # Fallback: AddFlowsheetObject with full type name
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                obj = fs.AddFlowsheetObject(enum_name, tag)
            return obj
        except Exception as e:
            warnings.append(f"AddObject/AddFlowsheetObject({enum_name!r}, {tag!r}) failed: {e}")
            return None

    # ── 5. Compute layout positions ──────────────────────────────────────────
    streams  = topology.get("streams", [])
    unit_ops = topology.get("unit_ops", [])
    connections = topology.get("connections", [])
    positions = _compute_layout(streams, unit_ops, connections)

    # ── 6. Create streams ────────────────────────────────────────────────────
    sim_objects: Dict[str, Any] = {}  # tag -> sim object

    for stream_spec in streams:
        tag = stream_spec["tag"]
        type_str = stream_spec.get("type", "MaterialStream")
        enum_name = _resolve_type(type_str) or "MaterialStream"
        x, y = positions.get(tag, (100, 100))

        obj = _add_object(enum_name, tag, x, y)
        if obj is None:
            warnings.append(f"Failed to create stream {tag!r}")
            continue
        sim_objects[tag] = obj

        # Set tag on GraphicObject
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                obj.GraphicObject.Tag = tag
        except Exception:
            pass

        # Set initial conditions
        if enum_name == "MaterialStream":
            w = _set_stream_initial_conditions(obj, stream_spec, added_compounds)
            warnings.extend(w)

    # ── 7. Create unit operations ────────────────────────────────────────────
    for uo_spec in unit_ops:
        tag = uo_spec["tag"]
        type_str = uo_spec.get("type", "")
        enum_name = _resolve_type(type_str)
        if enum_name is None:
            warnings.append(f"Unknown unit op type {type_str!r} for {tag!r} — skipped")
            continue
        x, y = positions.get(tag, (300, 100))

        obj = _add_object(enum_name, tag, x, y)
        if obj is None:
            warnings.append(f"Failed to create unit op {tag!r}")
            continue
        sim_objects[tag] = obj

        # Set tag
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                obj.GraphicObject.Tag = tag
        except Exception:
            pass

        # Set operating conditions
        w = _set_unitop_initial_conditions(obj, uo_spec, enum_name)
        warnings.extend(w)

    # ── 8. Connect objects ───────────────────────────────────────────────────
    connection_results = []
    for conn in connections:
        frm_tag  = conn.get("from", "")
        to_tag   = conn.get("to", "")
        frm_port = int(conn.get("from_port", 0))
        to_port  = int(conn.get("to_port", 0))

        frm_obj = sim_objects.get(frm_tag)
        to_obj  = sim_objects.get(to_tag)

        if frm_obj is None:
            warnings.append(f"Connection from {frm_tag!r}: object not created")
            continue
        if to_obj is None:
            warnings.append(f"Connection to {to_tag!r}: object not created")
            continue

        try:
            frm_go = frm_obj.GraphicObject
            to_go  = to_obj.GraphicObject
        except Exception as e:
            warnings.append(f"Cannot get GraphicObject for {frm_tag} -> {to_tag}: {e}")
            continue

        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                fs.ConnectObjects(frm_go, to_go,
                                  System.Int32(frm_port),
                                  System.Int32(to_port))
            connection_results.append({"from": frm_tag, "to": to_tag, "ok": True})
        except Exception as e:
            warnings.append(f"ConnectObjects({frm_tag} -> {to_tag}): {str(e)[:80]}")
            connection_results.append({"from": frm_tag, "to": to_tag, "ok": False,
                                       "error": str(e)[:80]})

    # ── 9. Layout ────────────────────────────────────────────────────────────
    for layout_method in ("NaturalLayout", "AutoLayout"):
        fn = getattr(fs, layout_method, None)
        if fn:
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    fn()
                break
            except Exception:
                pass

    # ── 10. Save ─────────────────────────────────────────────────────────────
    saved = False
    try:
        _save_dir = os.path.dirname(save_path)
        if _save_dir:
            os.makedirs(_save_dir, exist_ok=True)
        with redirect_stdout(_sink), redirect_stderr(_sink):
            mgr.SaveFlowsheet(fs, save_path, False)
        saved = True
    except Exception as e:
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                mgr.SaveFlowsheet2(fs, save_path)
            saved = True
        except Exception as e2:
            warnings.append(f"Save failed: {e2}")

    # ── 11. Run simulation ───────────────────────────────────────────────────
    converged = None
    solve_errors = []
    if topology.get("run_simulation", True):
        for calc_method in ("CalculateFlowsheet2", "CalculateFlowsheet",
                            "CalculateFlowsheet3"):
            fn = getattr(mgr, calc_method, None)
            if fn is None:
                continue
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    fn(fs)
                converged = True
                break
            except Exception as e:
                solve_errors.append(f"{calc_method}: {str(e)[:100]}")

        if converged is None:
            converged = False
            warnings.append("Simulation could not be solved: " + "; ".join(solve_errors[:2]))

    return {
        "success": True,
        "flowsheet_path": save_path if saved else None,
        "saved": saved,
        "converged": converged,
        "compounds_added": added_compounds,
        "property_package": matched_pp,
        "objects_created": list(sim_objects.keys()),
        "connections": connection_results,
        "warnings": warnings,
        "_fs": fs,   # internal — bridge stores this for further operations
    }


# ─────────────────────────────────────────────────────────────────────────────
# Topology JSON schema (for LLM guidance)
# ─────────────────────────────────────────────────────────────────────────────

TOPOLOGY_SCHEMA = {
    "type": "object",
    "required": ["compounds", "property_package", "streams", "connections"],
    "properties": {
        "name": {
            "type": "string",
            "description": "Flowsheet name (used as filename if save_path not given)"
        },
        "save_path": {
            "type": "string",
            "description": "Absolute path for saving, e.g. C:/Users/hp/Documents/my_sim.dwxmz"
        },
        "property_package": {
            "type": "string",
            "description": (
                "Thermodynamic model. Accepted values: "
                "'Peng-Robinson (PR)', 'Soave-Redlich-Kwong (SRK)', 'NRTL', "
                "'UNIFAC', \"Raoult's Law\", 'Steam Tables (IAPWS-IF97)', "
                "'CoolProp', 'GERG-2008', 'Wilson', 'UNIQUAC', 'PC-SAFT'"
            )
        },
        "compounds": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of compound names from DWSIM database, e.g. ['Water', 'Methanol']"
        },
        "streams": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tag"],
                "properties": {
                    "tag":          {"type": "string", "description": "Display name, e.g. 'Feed'"},
                    "type":         {"type": "string", "description": "MaterialStream or EnergyStream"},
                    "T":            {"type": "number", "description": "Temperature value"},
                    "T_unit":       {"type": "string", "description": "K or C"},
                    "P":            {"type": "number", "description": "Pressure value"},
                    "P_unit":       {"type": "string", "description": "Pa, bar, kPa, atm, psi"},
                    "molar_flow":   {"type": "number", "description": "Molar flow rate"},
                    "flow_unit":    {"type": "string", "description": "mol/s, mol/h, kmol/h"},
                    "mass_flow":    {"type": "number", "description": "Mass flow rate in kg/s"},
                    "vapor_fraction": {"type": "number", "description": "0=liquid, 1=vapor"},
                    "compositions": {
                        "type": "object",
                        "description": "Mole fractions, must sum to 1.0. e.g. {'Water': 0.6, 'Methanol': 0.4}",
                        "additionalProperties": {"type": "number"}
                    }
                }
            }
        },
        "unit_ops": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["tag", "type"],
                "properties": {
                    "tag":              {"type": "string"},
                    "type":             {
                        "type": "string",
                        "description": (
                            "Unit op type. Accepted: Heater, Cooler, HeatExchanger, "
                            "Pump, Compressor, Expander, Valve, Mixer, Splitter, "
                            "DistillationColumn, AbsorptionColumn, ShortcutColumn, "
                            "CSTR, PFR, GibbsReactor, ConversionReactor, "
                            "EquilibriumReactor, Separator, Pipe, CompoundSeparator"
                        )
                    },
                    "outlet_T":         {"type": "number", "description": "Outlet temperature (K)"},
                    "pressure_drop":    {"type": "number", "description": "Pressure drop (Pa)"},
                    "duty":             {"type": "number", "description": "Heat duty (W)"},
                    "efficiency":       {"type": "number", "description": "Adiabatic efficiency (0-1)"},
                    "reflux_ratio":     {"type": "number"},
                    "stages":           {"type": "integer"},
                    "number_of_stages": {"type": "integer"},
                    "volume":           {"type": "number", "description": "Reactor volume (m3)"},
                    "conversion":       {"type": "number", "description": "Conversion (0-1)"},
                }
            }
        },
        "connections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["from", "to"],
                "properties": {
                    "from":      {"type": "string", "description": "Source object tag"},
                    "to":        {"type": "string", "description": "Destination object tag"},
                    "from_port": {"type": "integer", "description": "Output port index (default 0)"},
                    "to_port":   {"type": "integer", "description": "Input port index (default 0)"},
                }
            }
        },
        "run_simulation": {
            "type": "boolean",
            "description": "Whether to run simulation after building (default true)"
        }
    }
}
