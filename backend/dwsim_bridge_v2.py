"""
dwsim_bridge_v2.py
──────────────────
Enhanced bridge with 5 accuracy improvements over v1:

  ACC-1  set_stream_composition  — set mole fractions on any feed stream
  ACC-2  check_convergence       — verify every stream converged after solve
  ACC-3  get_property_package    — read thermo model (PR, SRK, NRTL, etc.)
  ACC-4  validate_feed_specs     — warn if T/P/flow missing before solve
  ACC-5  optimize_parameter      — SciPy bounded minimise/maximise of any
                                   stream property over a stream/unit-op param

All improvements from v1 are retained:
  IMP-2  set_stream_property uses Phase[0].Properties API
  IMP-3  FlowsheetState tracks loaded streams/unit-ops for context injection
  IMP-4  parametric_study tool
  IMP-5  get_unit_op_summary clean human-readable unit-op properties
  IMP-6  Multi-flowsheet: load_flowsheet stores multiple, switch switches
"""

import io
import os
import sys
import time
import traceback
from contextlib import redirect_stdout, redirect_stderr
from typing import Any, Dict, List, Optional, Tuple

try:
    from suppress_dotnet_output import suppress_dotnet_console
except ImportError:
    from contextlib import nullcontext
    def suppress_dotnet_console():
        return nullcontext()

_CANDIDATE_FOLDERS = [
    os.path.expanduser(r"~\AppData\Local\DWSIM\DWSIM"),
    os.path.expanduser(r"~\AppData\Local\DWSIM\dwsim"),
    os.path.expanduser(r"~\AppData\Local\DWSIM"),
    os.path.expanduser(r"~\AppData\Local\dwsim"),
    os.path.expanduser(r"~\AppData\Local\Programs\DWSIM"),
    r"C:\Program Files\DWSIM",
    r"C:\Program Files\DWSIM\DWSIM",
]


def _find_dll_folder(extra: Optional[List[str]] = None) -> Optional[str]:
    for c in (extra or []) + _CANDIDATE_FOLDERS:
        if c and os.path.isdir(c):
            if (os.path.exists(os.path.join(c, "DWSIM.Automation.dll")) and
                    os.path.exists(os.path.join(c, "DWSIM.Interfaces.dll"))):
                return c
    for root in [os.path.expanduser(r"~\AppData\Local"),
                 os.path.expanduser(r"~\AppData\Roaming")]:
        if not os.path.exists(root):
            continue
        for dirpath, _, filenames in os.walk(root):
            if ("DWSIM.Automation.dll" in filenames and
                    "DWSIM.Interfaces.dll" in filenames):
                return dirpath
    return None


# ─────────────────────────────────────────────────────────────────────────────
# IMP-3: Flowsheet state tracker
# ─────────────────────────────────────────────────────────────────────────────

# Stale-lock TTL: 5 minutes. A .lock older than this is assumed abandoned
# (e.g. bridge crashed mid-save) and safe to silently reclaim.
_LOCK_STALE_SECONDS = 300

# Keep this many rolling backups per flowsheet. Older ones are pruned.
_BACKUP_KEEP = 5


def _backup_before_write(path: str) -> Optional[str]:
    """Copy `path` to `path.bak.YYYYMMDD_HHMMSS` before overwrite.
    Prunes the oldest when more than _BACKUP_KEEP exist. Returns the
    backup path (or None if source missing or copy failed)."""
    import shutil
    if not os.path.exists(path):
        return None
    try:
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = f"{path}.bak.{ts}"
        shutil.copy2(path, backup)
    except Exception as exc:
        try:
            import logging
            logging.getLogger("dwsim_bridge").warning(
                "backup of %s failed: %s", path, exc)
        except Exception:
            pass
        return None
    try:
        import glob as _g
        existing = sorted(_g.glob(path + ".bak.*"))
        for old in existing[:-_BACKUP_KEEP]:
            try: os.remove(old)
            except Exception: pass
    except Exception:
        pass
    return backup


def list_backups(path: str) -> List[Dict[str, Any]]:
    """Return backup metadata for a given flowsheet path, newest first."""
    import glob as _g
    out = []
    for p in sorted(_g.glob(path + ".bak.*"), reverse=True):
        try:
            st = os.stat(p)
            out.append({
                "path": p,
                "name": os.path.basename(p),
                "size_bytes": st.st_size,
                "mtime": st.st_mtime,
            })
        except Exception:
            pass
    return out


def restore_backup(backup_path: str, target: str) -> Dict[str, Any]:
    """Copy a backup over the live flowsheet. Used by the restore endpoint."""
    import shutil
    if not os.path.exists(backup_path):
        return {"success": False, "error": f"backup not found: {backup_path}"}
    try:
        shutil.copy2(backup_path, target)
        return {"success": True, "restored_to": target,
                "mtime": os.path.getmtime(target)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _classify_load_error(detail: str, path: str):
    """Map a raw LoadFlowsheet exception string to (code, hint, suggestions).

    Returns tuple: (code: str, hint: str, suggestions: list[str])
    """
    d = (detail or "").lower()
    bn = os.path.basename(path or "")

    if "property package" in d or "propertypackage" in d:
        return ("PP_MISSING",
                "flowsheet references a property package whose DLL is not "
                "loaded in the Automation3 runtime",
                [
                    "Open the file once in the DWSIM desktop app to prime "
                    "the property-package cache, then retry.",
                    "If the PP is PR/LK, this is a known runtime quirk — "
                    "see /diagnostics for details.",
                    "Check that DWSIM.Thermodynamics.dll version matches the "
                    "version that created this file.",
                ])

    if "pr/lk" in d or ("pr" in d and "lk" in d and "equation" in d):
        return ("PR_LK_INIT",
                "the PR/LK property package needs DWSIM to initialise its "
                "internal type registry",
                [
                    "Open the file in DWSIM desktop once, then retry the "
                    "agent load.",
                    "Alternatively, re-save the flowsheet with a different "
                    "PP (e.g. Peng-Robinson) from DWSIM desktop.",
                ])

    if "compound" in d and ("missing" in d or "not found" in d or "unknown" in d):
        return ("COMPOUND_MISSING",
                "a compound referenced in the flowsheet is absent from the "
                "loaded DWSIM compound database",
                [
                    "Verify the compound name — use /compounds/search for the "
                    "exact DWSIM spelling.",
                    "If the compound is a user-added one, ensure it was "
                    "re-imported into this DWSIM install.",
                ])

    if "version" in d and (".dwxmz" in d or ".dwxm" in d or "schema" in d):
        return ("VERSION_MISMATCH",
                "file was saved by a DWSIM version newer than the loaded DLLs",
                [
                    "Upgrade the bundled DWSIM DLLs, or re-save the file "
                    "from an older DWSIM desktop version.",
                    "See /diagnostics → dwsim.dll_versions for the loaded "
                    "DLL versions.",
                ])

    if "zip" in d or "crc" in d or "archive" in d or "corrupt" in d:
        return ("CORRUPT_ARCHIVE",
                "the .dwxmz archive appears to be corrupt or truncated",
                [
                    f"Check file size is reasonable: {bn}",
                    "Try re-downloading or restoring from a backup "
                    "(.bak.* files alongside the original).",
                ])

    if "unauthorized" in d or "access is denied" in d or "permission" in d:
        return ("PERMISSION_DENIED",
                "Windows blocked reading the file",
                [
                    "Check you have read access to the path.",
                    "Close any other process holding the file open.",
                ])

    if "no method matches" in d:
        return ("AUTOMATION_MISMATCH",
                "Automation DLL signature did not match any LoadFlowsheet "
                "overload",
                [
                    "Mixed-version DWSIM DLLs can cause this — run "
                    "/diagnostics and check dwsim.dll_versions for drift.",
                    "Clean install of DWSIM and re-point DWSIM_DLL_FOLDER.",
                ])

    return ("LOAD_FAILED", "", [
        "Check /diagnostics for runtime issues.",
        "Try opening the file in DWSIM desktop to see the raw error.",
    ])


def _is_stale_lock(lock_path: str) -> bool:
    """True when the lock should be ignored (dead PID or old timestamp)."""
    try:
        if not os.path.exists(lock_path):
            return False
        age = time.time() - os.path.getmtime(lock_path)
        if age > _LOCK_STALE_SECONDS:
            return True
        # Parse 'pid=NNNN' out of the holder string; if that PID is gone, stale.
        with open(lock_path, "r", encoding="utf-8") as f:
            body = f.read()
        import re
        m = re.search(r"pid=(\d+)", body)
        if m:
            pid = int(m.group(1))
            try:
                if os.name == "nt":
                    import ctypes
                    PROCESS_QUERY_LIMITED = 0x1000
                    h = ctypes.windll.kernel32.OpenProcess(
                        PROCESS_QUERY_LIMITED, False, pid)
                    if not h:
                        return True  # no such process
                    ctypes.windll.kernel32.CloseHandle(h)
                else:
                    os.kill(pid, 0)
            except (OSError, ProcessLookupError):
                return True
    except Exception:
        pass
    return False


class FlowsheetState:
    """Tracks current flowsheet topology for LLM context injection."""

    def __init__(self):
        self.name:         str = ""
        self.path:         str = ""
        self.streams:      List[str] = []
        self.unit_ops:     List[str] = []
        self.all_tags:     List[str] = []
        self.object_types: Dict[str, str] = {}
        # ACC-3: property package info
        self.property_package: str = ""
        # Sync-safety: mtime observed at load/save time — used to detect
        # external edits (watcher compares on-disk mtime to this).
        self.loaded_mtime: float = 0.0

    def update(self, path: str, objects: List[Dict]) -> None:
        self.path     = path
        self.name     = os.path.basename(path)
        self.streams  = [o["tag"] for o in objects if o.get("category") == "MaterialStream"]
        self.unit_ops = [o["tag"] for o in objects if o.get("category") == "UnitOperation"]
        self.all_tags = [o["tag"] for o in objects]
        self.object_types = {o["tag"]: o.get("type", "") for o in objects}

    def context_summary(self) -> str:
        if not self.name:
            return "No flowsheet currently loaded."
        lines = [
            f"CURRENT FLOWSHEET: {self.name}",
            f"  Material streams ({len(self.streams)}): {', '.join(self.streams)}",
            f"  Unit operations  ({len(self.unit_ops)}): {', '.join(self.unit_ops) or 'none'}",
        ]
        if self.property_package:
            lines.append(f"  Property package: {self.property_package}")
        return "\n".join(lines)

    def clear(self):
        self.__init__()


# ─────────────────────────────────────────────────────────────────────────────
# .NET reflection helpers
# ─────────────────────────────────────────────────────────────────────────────

def _reflect_get(obj, prop_name: str):
    try:
        prop = obj.GetType().GetProperty(prop_name)
        if prop is not None:
            return prop.GetValue(obj)
    except Exception:
        pass
    return None


def _reflect_index(obj, index):
    try:
        return obj[index]
    except Exception:
        pass
    try:
        get_item = obj.GetType().GetMethod("get_Item")
        if get_item:
            return get_item.Invoke(obj, [index])
    except Exception:
        pass
    return None


def _reflect_values(obj):
    try:
        vp = obj.GetType().GetProperty("Values")
        if vp:
            return vp.GetValue(obj)
    except Exception:
        pass
    return None


def _unwrap_nullable(val) -> Optional[float]:
    if val is None:
        return None
    try:
        if hasattr(val, "HasValue"):
            return float(val.Value) if val.HasValue else None
        if hasattr(val, "GetValueOrDefault"):
            return float(val.GetValueOrDefault())
        return float(val)
    except Exception:
        return None


def _get_phase_props(obj, phase_index: int):
    try:
        phases = obj.Phases
        for key in (phase_index, str(phase_index)):
            try:
                phase = phases[key]
                if phase is not None:
                    return getattr(phase, "Properties", phase)
            except Exception:
                pass
        try:
            vals = list(phases.Values)
            if len(vals) > phase_index:
                return getattr(vals[phase_index], "Properties", vals[phase_index])
        except Exception:
            pass
    except AttributeError:
        pass

    phases_r = _reflect_get(obj, "Phases")
    if phases_r is None:
        return None
    for key in (phase_index, str(phase_index)):
        phase = _reflect_index(phases_r, key)
        if phase is not None:
            props = _reflect_get(phase, "Properties")
            return props if props is not None else phase
    vals = _reflect_values(phases_r)
    if vals is not None:
        try:
            phase_list = list(vals)
            if len(phase_list) > phase_index:
                phase = phase_list[phase_index]
                props = _reflect_get(phase, "Properties")
                return props if props is not None else phase
        except Exception:
            pass
    return None


def _read_prop(obj, phase_index: int, direct: tuple,
               phase_attrs: tuple, allow_zero: bool = False) -> Optional[float]:
    def accept(v):
        return v is not None and (allow_zero or v != 0.0)

    for attr in direct:
        try:
            v = _unwrap_nullable(getattr(obj, attr))
            if accept(v):
                return round(v, 8)
        except Exception:
            pass

    pp = _get_phase_props(obj, phase_index)
    if pp is not None:
        for attr in phase_attrs:
            for getter in (lambda a: getattr(pp, a),
                           lambda a: _reflect_get(pp, a)):
                try:
                    v = _unwrap_nullable(getter(attr))
                    if accept(v):
                        return round(v, 8)
                except Exception:
                    pass
    return None


def _read_compositions(obj) -> Dict[str, float]:
    return _read_compositions_by(obj, ("MoleFraction", "molefraction"))


def _read_compositions_mass(obj) -> Dict[str, float]:
    return _read_compositions_by(obj, ("MassFraction", "massfraction"))


def _read_compositions_by(obj, attrs: tuple) -> Dict[str, float]:
    comps: Dict[str, float] = {}
    phase = None
    try:
        for key in (0, "0"):
            try:
                phase = obj.Phases[key]; break
            except Exception:
                pass
        if phase is None:
            phases_r = _reflect_get(obj, "Phases")
            if phases_r:
                phase = _reflect_index(phases_r, 0)
                if phase is None:
                    vals = _reflect_values(phases_r)
                    if vals:
                        try: phase = list(vals)[0]
                        except Exception: pass
    except Exception:
        pass
    if phase is None:
        return comps
    try:
        cmpds = getattr(phase, "Compounds", None) or _reflect_get(phase, "Compounds")
        if cmpds is None:
            return comps
        for name in cmpds.Keys:
            comp = cmpds[name]
            for attr in attrs:
                try:
                    v = _unwrap_nullable(
                        getattr(comp, attr, None) or _reflect_get(comp, attr))
                    if v is not None:
                        comps[str(name)] = round(v, 8); break
                except Exception:
                    pass
    except Exception:
        pass
    return comps


def _resolve_tag(obj, fallback: str = "") -> str:
    for path in (
        ("GraphicObject", "Tag"),
        ("GraphicObject", "Name"),
        ("Tag",), ("Nome",), ("Name",),
    ):
        try:
            val = obj
            for attr in path:
                val = getattr(val, attr)
            s = str(val).strip()
            if s and s != str(fallback).strip():
                return s
        except Exception:
            pass
    return str(fallback)


def _categorise(typename: str) -> str:
    t = typename.lower()
    if "materialstream" in t: return "MaterialStream"
    if "energystream"   in t: return "EnergyStream"
    if any(x in t for x in ("heatexchanger", "heater", "cooler", "reactor",
                              "column", "separator", "mixer", "splitter",
                              "pump", "compressor", "valve", "expander",
                              "vessel", "tank", "pipe", "filter",
                              "orifice", "customuo", "exceluo", "flowsheetuo",
                              "nodein", "nodeout", "absorber")):
        return "UnitOperation"
    return "Other"


def _reflect_set_flag(obj, prop_name: str, value: bool) -> bool:
    """Set a boolean .NET property via reflection."""
    try:
        p = obj.GetType().GetProperty(prop_name)
        if p and p.CanWrite:
            p.SetValue(obj, value)
            return True
    except Exception:
        pass
    return False


def _reflect_set_typed(tp, obj, value) -> bool:
    """
    Set a .NET property via reflection, boxing Python primitives to the
    correct .NET type.

    Strategy (ordered by reliability):
    1. setattr on the object directly — pythonnet handles boxing automatically
    2. Typed .NET constructor via System module (loaded after clr init)
    3. Fallback cascade: float → int → str
    """
    prop_name = tp.Name

    # Strategy 1: direct setattr — pythonnet resolves boxing automatically
    for coerce in (float, int, str, lambda v: v):
        try:
            setattr(obj, prop_name, coerce(value))
            return True
        except Exception:
            continue

    # Strategy 2: System module typed constructors (requires clr to be loaded)
    try:
        import System
        pt_name = tp.PropertyType.FullName if tp.PropertyType else ""
        _CTORS = {
            "System.Int32":   System.Int32,
            "System.Int64":   System.Int64,
            "System.Double":  System.Double,
            "System.Single":  System.Single,
            "System.Boolean": System.Boolean,
        }
        ctor = _CTORS.get(pt_name)
        if ctor:
            tp.SetValue(obj, ctor(value))
            return True
    except Exception:
        pass

    # Strategy 3: raw reflection cascade
    for coerce in (float, int, str):
        try:
            tp.SetValue(obj, coerce(value))
            return True
        except Exception:
            continue
    return False


def _convert_to_si(prop: str, value: float,
                   unit: str) -> Tuple[float, str, Optional[str]]:
    p = prop.lower().replace(" ", "_").replace("-", "_")
    u = unit.lower().strip()
    if "temp" in p:
        return (value + 273.15 if u == "c" else value), "K", "Temperature"
    if "press" in p:
        conv = {"bar": 1e5, "kpa": 1e3, "atm": 101325.0,
                "psi": 6894.757, "mpa": 1e6, "mmhg": 133.322}
        return value * conv.get(u, 1.0), "Pa", "Pressure"
    if "molar" in p or "mole_flow" in p:
        conv = {"mol/h": 1/3600, "kmol/h": 1000/3600, "mol/min": 1/60}
        return value * conv.get(u, 1.0), "mol/s", "MolarFlow"
    if "mass" in p and "flow" in p:
        conv = {"kg/h": 1/3600, "t/h": 1000/3600, "lb/h": 0.000125998}
        return value * conv.get(u, 1.0), "kg/s", "MassFlow"
    if "vapor" in p or "quality" in p or "vf" in p:
        return float(value), "-", "VaporFraction"
    return value, unit, None


# ─────────────────────────────────────────────────────────────────────────────
# IMP-5: Unit-op human-readable summary helpers
# ─────────────────────────────────────────────────────────────────────────────

_UNIT_OP_PROPS: Dict[str, List[Tuple[str, str, float]]] = {
    "heatexchanger": [
        ("Area",               "Area (m²)",       1.0),
        ("OverallCoefficient", "U (W/m²·K)",      1.0),
        ("HeatExchanged",      "Duty (kW)",        1e-3),
        ("LMTD",               "LMTD (K)",         1.0),
        ("DeltaP",             "Shell ΔP (Pa)",    1.0),
        ("DeltaP2",            "Tube ΔP (Pa)",     1.0),
        ("CalculationMode",    "Calc mode",        None),
    ],
    "heater": [
        ("DeltaT",             "ΔT (K)",           1.0),
        ("DutySpec",           "Duty spec (W)",    1.0),
        ("OutletTemperature",  "Outlet T (K)",     1.0),
        ("DeltaP",             "ΔP (Pa)",          1.0),
        ("Efficiency",         "Efficiency",       1.0),
    ],
    "cooler": [
        ("DeltaT",             "ΔT (K)",           1.0),
        ("DutySpec",           "Duty spec (W)",    1.0),
        ("OutletTemperature",  "Outlet T (K)",     1.0),
        ("DeltaP",             "ΔP (Pa)",          1.0),
    ],
    "pump": [
        ("OutletPressure",     "Outlet P (Pa)",    1.0),
        ("DeltaP",             "ΔP (Pa)",          1.0),
        ("Efficiency",         "Efficiency",       1.0),
        ("DeltaQ",             "Power (W)",        1.0),
    ],
    "compressor": [
        ("OutletPressure",     "Outlet P (Pa)",    1.0),
        ("PressureRatio",      "Pressure ratio",   1.0),
        ("Efficiency",         "Isentropic η",     1.0),
        ("DeltaQ",             "Power (W)",        1.0),
    ],
    "valve": [
        ("OutletPressure",     "Outlet P (Pa)",    1.0),
        ("DeltaP",             "ΔP (Pa)",          1.0),
    ],
    "reactor": [
        ("ReactorType",        "Reactor type",     None),
        ("Conversion",         "Conversion",       1.0),
        ("OutletTemperature",  "Outlet T (K)",     1.0),
        ("DeltaP",             "ΔP (Pa)",          1.0),
    ],
    "separator": [
        ("DeltaP",             "ΔP (Pa)",          1.0),
        ("SeparationFactor",   "Sep. factor",      1.0),
    ],
    "mixer": [
        ("PressureSpec",       "Pressure spec",    None),
        ("OutletTemperature",  "Outlet T (K)",     1.0),
        ("OutletPressure",     "Outlet P (Pa)",    1.0),
    ],
    "column": [
        ("NumberOfStages",     "Stages",           None),
        ("RefluxRatio",        "Reflux ratio",     1.0),
        ("ReboilerDuty",       "Reboiler duty (W)",1.0),
        ("CondenserDuty",      "Condenser duty (W)",1.0),
    ],
}


def _get_unit_op_summary(obj) -> Dict[str, Any]:
    try:
        typename = obj.GetType().Name.lower()
    except Exception:
        typename = ""

    profile = []
    for key, props in _UNIT_OP_PROPS.items():
        if key in typename:
            profile = props
            break

    # Always tack on these generic fields so every unit op shows *something*
    # when it's been calculated. Ordered after the profile so typed fields win.
    _GENERIC_FALLBACK = [
        ("OutletTemperature", "Outlet T (K)",    1.0),
        ("OutletPressure",    "Outlet P (Pa)",   1.0),
        ("DeltaT",            "ΔT (K)",          1.0),
        ("DeltaP",            "ΔP (Pa)",         1.0),
        ("DeltaQ",            "Duty (W)",        1.0),
        ("Efficiency",        "Efficiency",      1.0),
        ("CalculationMode",   "Calc mode",       None),
    ]
    seen_attrs = {p[0] for p in profile}
    profile = list(profile) + [p for p in _GENERIC_FALLBACK
                                if p[0] not in seen_attrs]

    result: Dict[str, Any] = {}
    for attr, display, scale in profile:
        for getter in (lambda a: getattr(obj, a),
                       lambda a: _reflect_get(obj, a)):
            try:
                raw = getter(attr)
                if raw is None:
                    continue
                if scale is not None:
                    v = _unwrap_nullable(raw)
                    # Keep zero values — a calculated ΔP of 0 Pa is valid info.
                    if v is not None:
                        result[display] = round(v * scale, 6)
                else:
                    s = str(raw).strip()
                    if s and s.lower() not in ("none", "null"):
                        result[display] = s
                break
            except Exception:
                pass

    # Reflection fallback: if nothing meaningful came back, scan readable
    # numeric / string properties with useful name prefixes. This catches
    # Mixer, CustomUO, ComponentSeparator etc. that don't match any profile.
    if not result:
        _PREFIXES = ("Outlet", "Delta", "Duty", "Energy", "Power",
                     "Efficiency", "Conversion", "Pressure", "Temperature",
                     "Error", "Residual", "LMTD", "Spec", "Ratio",
                     "Area", "Volume", "Stages", "Mode")
        _SKIP_EXACT = {"Flowsheet", "FlowSheet", "FlowsheetObject",
                       "SpecVarType"}
        try:
            for tp in obj.GetType().GetProperties():
                if not tp.CanRead:
                    continue
                name = tp.Name
                if name in _SKIP_EXACT:
                    continue
                if not any(name.startswith(p) for p in _PREFIXES):
                    continue
                try:
                    raw = tp.GetValue(obj, None)
                except Exception:
                    continue
                if raw is None:
                    continue
                v = _unwrap_nullable(raw)
                if isinstance(v, (int, float)):
                    # Skip obviously-uninitialised sentinels
                    if v in (-1e20, 1e20) or (isinstance(v, float) and
                                              (v != v)):  # NaN
                        continue
                    result[name] = round(float(v), 6)
                else:
                    s = str(raw).strip()
                    if s and s.lower() not in ("none", "null",
                                               "system.collections"):
                        result[name] = s[:60]
                if len(result) >= 8:
                    break
        except Exception:
            pass

    # ── Distillation stage temperature profile (for SF-08d) ──────────────────
    # Try to extract per-stage temperatures from column objects
    typename_for_stages = ""
    try:
        typename_for_stages = obj.GetType().Name.lower()
    except Exception:
        pass

    if any(k in typename_for_stages for k in
           ("distillationcolumn", "absorptioncolumn", "reboiledabsorber", "refluxedabsorber")):
        stage_temps = []
        try:
            # DWSIM stores column profiles in Stages collection or TempProfile
            for profile_attr in ("Stages", "TempProfile", "TemperatureProfile"):
                p = obj.GetType().GetProperty(profile_attr)
                if p is None:
                    continue
                stages = p.GetValue(obj)
                if stages is None:
                    continue
                try:
                    # Stages is an IEnumerable — iterate and read Temperature
                    for stage in stages:
                        try:
                            t_prop = stage.GetType().GetProperty("Temperature")
                            if t_prop:
                                t_k = _unwrap_nullable(t_prop.GetValue(stage))
                                if t_k is not None and float(t_k) > 0:
                                    stage_temps.append(round(float(t_k) - 273.15, 2))  # → °C
                        except Exception:
                            pass
                    if stage_temps:
                        break
                except Exception:
                    pass
            # Alternative: TempProfile as a list/array
            if not stage_temps:
                for attr in ("TempProfile", "TemperatureProfile"):
                    try:
                        arr = getattr(obj, attr, None)
                        if arr is not None:
                            stage_temps = [round(float(v) - 273.15, 2) for v in arr
                                           if v is not None and float(v) > 0]
                            if stage_temps:
                                break
                    except Exception:
                        pass
        except Exception:
            pass
        if stage_temps:
            result["stage_temperatures"] = stage_temps

    # ── Compressor/Pump efficiency (for SF-08b) ───────────────────────────────
    if any(k in typename_for_stages for k in ("compressor", "pump", "expander", "turbine")):
        for eff_attr in ("AdiabaticEfficiency", "IsentropicEfficiency", "Efficiency",
                         "adiabatic_efficiency"):
            try:
                p = obj.GetType().GetProperty(eff_attr)
                if p:
                    v = _unwrap_nullable(p.GetValue(obj))
                    if v is not None and 0 < float(v) <= 1.0:
                        result["adiabatic_efficiency"] = round(float(v), 4)
                        break
            except Exception:
                pass
        if "adiabatic_efficiency" not in result:
            try:
                v = getattr(obj, "AdiabaticEfficiency", None) or \
                    getattr(obj, "Efficiency", None)
                v = _unwrap_nullable(v)
                if v is not None and 0 < float(v) <= 1.0:
                    result["adiabatic_efficiency"] = round(float(v), 4)
            except Exception:
                pass

    return result


# ─────────────────────────────────────────────────────────────────────────────

class DWSIMBridgeV2:

    def __init__(self, dll_folder: Optional[str] = None):
        self.dll_folder = dll_folder or _find_dll_folder()
        self._mgr        = None
        self._ready      = False
        self._MaterialStream = None

        self.state = FlowsheetState()

        # IMP-6: multi-flowsheet support
        self._flowsheets:   Dict[str, Dict] = {}
        self._active_alias: Optional[str]   = None
        # Tracks whether the active flowsheet is mid-build (new_flowsheet called
        # but save_and_solve not yet run).  Used by the idempotency guard so
        # a *solved* flowsheet does not block a fresh new_flowsheet call.
        self._building: bool = False

    # ── shortcuts to active flowsheet ─────────────────────────────────────────

    def _purge_stale_flowsheets(self, keep_alias: Optional[str] = None) -> None:
        """
        Drop all loaded flowsheets from the bridge's registry (except keep_alias).

        Why this is needed:
          self._mgr (the DWSIM AutomationManager) is a shared .NET singleton.
          Every LoadFlowsheet / CreateFlowsheet call registers a new sim object
          inside the manager's internal list.  Old sims that are removed from
          self._flowsheets but NOT explicitly closed keep their .NET references
          alive, which causes:
            • tag-name conflicts  (Feed / Product / Q already exist in stale sim)
            • solver-state bleed  (old convergence data biases new solve)
            • memory accumulation (each create_flowsheet leaks a .NET object)

          Calling this before create_flowsheet / create_from_template ensures
          the manager starts in a clean state.
        """
        _sink = io.StringIO()
        stale_aliases = [a for a in list(self._flowsheets) if a != keep_alias]
        for alias in stale_aliases:
            entry = self._flowsheets.pop(alias, None)
            if entry is None:
                continue
            fs = entry.get("fs")
            if fs is None:
                continue
            # Try to close/unload the flowsheet from the manager so the .NET
            # GC can collect it and free the tag registry.
            for close_method in ("CloseFlowsheet", "RemoveFlowsheet",
                                 "UnloadFlowsheet"):
                fn = getattr(self._mgr, close_method, None)
                if fn is None:
                    continue
                try:
                    with redirect_stdout(_sink), redirect_stderr(_sink):
                        fn(fs)
                    break
                except Exception:
                    pass
            # Regardless of whether close succeeded, drop the Python reference
            # so CPython's GC can decrement the COM refcount.
            del fs

        if not self._flowsheets:
            # Full reset: clear active alias and state
            self._active_alias = None
            self.state = FlowsheetState()

    @property
    def _flowsheet(self):
        if self._active_alias and self._active_alias in self._flowsheets:
            return self._flowsheets[self._active_alias]["fs"]
        return None

    @property
    def _flowsheet_path(self) -> Optional[str]:
        if self._active_alias and self._active_alias in self._flowsheets:
            return self._flowsheets[self._active_alias]["path"]
        return None

    # ── init ──────────────────────────────────────────────────────────────────

    def initialize(self) -> Dict[str, Any]:
        if self._ready:
            return {"success": True, "message": "Already initialised"}
        if not self.dll_folder:
            return {"success": False,
                    "error": "DWSIM DLL folder not found. "
                             "Install from https://dwsim.org/downloads/"}
        try:
            import clr  # type: ignore
        except ImportError:
            return {"success": False,
                    "error": "pythonnet not installed: pip install pythonnet"}

        if self.dll_folder not in sys.path:
            sys.path.insert(0, self.dll_folder)

        _sink = io.StringIO()
        try:
            with suppress_dotnet_console(), \
                 redirect_stdout(_sink), redirect_stderr(_sink):
                clr.AddReference("DWSIM.Automation")   # type: ignore
                clr.AddReference("DWSIM.Interfaces")   # type: ignore
        except Exception as exc:
            return {"success": False, "error": f"clr.AddReference failed: {exc}"}

        Automation = None
        for cls_name in ("Automation3", "Automation2", "Automation"):
            try:
                import importlib
                mod = importlib.import_module("DWSIM.Automation")
                Automation = getattr(mod, cls_name)
                break
            except Exception:
                pass
        if Automation is None:
            return {"success": False, "error": "Cannot import Automation class"}

        try:
            with suppress_dotnet_console(), \
                 redirect_stdout(_sink), redirect_stderr(_sink):
                self._mgr = Automation()
        except Exception as exc:
            return {"success": False, "error": f"Automation() failed: {exc}",
                    "traceback": traceback.format_exc()}

        try:
            with suppress_dotnet_console(), \
                 redirect_stdout(_sink), redirect_stderr(_sink):
                clr.AddReference("DWSIM.Thermodynamics")  # type: ignore
            import importlib
            smod = importlib.import_module("DWSIM.Thermodynamics.Streams")
            self._MaterialStream = getattr(smod, "MaterialStream", None)
        except Exception:
            self._MaterialStream = None

        self._ready = True

        # ── Version Detection ─────────────────────────────────────────────────
        dwsim_version = "unknown"
        api_warnings  = []
        try:
            # Try to read assembly version from the Automation object
            asm = type(self._mgr).Assembly if hasattr(type(self._mgr), "Assembly") \
                  else getattr(self._mgr, "GetType", lambda: None)()
            if asm is not None:
                v = getattr(asm, "GetName", lambda: None)()
                if v:
                    dwsim_version = str(getattr(v, "Version", "unknown"))
            # Alternative: read from a dll file
            if dwsim_version == "unknown":
                for dll in ("DWSIM.dll", "DWSIM.Automation.dll"):
                    dll_path = os.path.join(self.dll_folder, dll)
                    if os.path.isfile(dll_path):
                        try:
                            import System.Reflection  # type: ignore
                            asm2 = System.Reflection.Assembly.LoadFrom(dll_path)
                            dwsim_version = str(asm2.GetName().Version)
                            break
                        except Exception:
                            pass
        except Exception:
            pass

        # Warn if DWSIM version is older than 8.0 (API may be missing methods)
        try:
            major = int(dwsim_version.split(".")[0])
            if major < 8:
                api_warnings.append(
                    f"DWSIM v{dwsim_version} detected — bridge tested on v8+. "
                    "Some API methods may be missing. Consider upgrading DWSIM."
                )
        except Exception:
            pass

        self._dwsim_version = dwsim_version
        result_msg = f"DWSIM v{dwsim_version} initialised from {self.dll_folder}"
        if api_warnings:
            result_msg += " | WARNINGS: " + "; ".join(api_warnings)
        print(f"[DWSIMBridge] {result_msg}")
        return {
            "success":        True,
            "message":        result_msg,
            "dwsim_version":  dwsim_version,
            "api_warnings":   api_warnings,
        }

    # ── flowsheet file ops ────────────────────────────────────────────────────

    def find_flowsheets(self) -> Dict[str, Any]:
        roots = [
            os.path.expanduser("~/Documents"),
            os.path.expanduser("~/Desktop"),
            os.path.expanduser("~/Downloads"),
            os.path.expanduser(r"~\AppData\Local\DWSIM"),
        ]
        found, seen = [], set()
        for root in roots + [os.path.expanduser("~")]:
            if not root or not os.path.exists(root):
                continue
            for dirpath, _, filenames in os.walk(root):
                for f in filenames:
                    if f.lower().endswith((".dwxmz", ".dwxm")):
                        full = os.path.join(dirpath, f)
                        if full not in seen:
                            seen.add(full)
                            found.append(full)
                if len(found) > 50:
                    break
        return {"success": True, "count": len(found), "flowsheets": found}

    def load_flowsheet(self, path: str,
                       alias: Optional[str] = None) -> Dict[str, Any]:
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r
        if not os.path.exists(path):
            return {"success": False, "error": f"File not found: {path}"}

        lock_path = path + ".lock"
        stale = _is_stale_lock(lock_path)
        if os.path.exists(lock_path) and not stale:
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    holder = f.read().strip()
            except Exception:
                holder = "?"
            return {"success": False,
                    "code": "LOCKED",
                    "error": f"File locked by '{holder}'. "
                             f"Wait or delete {os.path.basename(lock_path)}.",
                    "locked_by": holder}
        if stale:
            try: os.remove(lock_path)
            except Exception: pass

        fs = None
        best_error: Optional[str] = None
        for method in ("LoadFlowsheet", "LoadFlowsheet2"):
            if not hasattr(self._mgr, method):
                continue
            for args in [(path,), (path, None)]:
                try:
                    fs = getattr(self._mgr, method)(*args)
                    if fs is not None:
                        break
                except Exception as exc:
                    msg = f"{type(exc).__name__}: {str(exc)[:220]}"
                    # Prefer real DWSIM errors over pythonnet overload noise.
                    if best_error is None or "No method matches" in best_error:
                        best_error = msg
            if fs is not None:
                break

        if fs is None:
            detail = best_error or "LoadFlowsheet returned None"
            code, hint, suggestions = _classify_load_error(detail, path)
            return {"success": False, "code": code,
                    "error": detail + (f" — {hint}" if hint else ""),
                    "suggestions": suggestions,
                    "path": path}

        _sink = io.StringIO()
        try:
            with suppress_dotnet_console(), \
                 redirect_stdout(_sink), redirect_stderr(_sink):
                if hasattr(self._mgr, "CalculateFlowsheet2"):
                    self._mgr.CalculateFlowsheet2(fs)
                elif hasattr(self._mgr, "SolveFlowsheet"):
                    self._mgr.SolveFlowsheet(fs)
        except Exception:
            pass

        fs_alias = alias or os.path.splitext(os.path.basename(path))[0]
        tag_cache: Dict[str, str] = {}
        coll = self._get_collection_for(fs)
        if coll:
            for guid, obj in self._iter_collection(coll):
                tag_cache[str(guid)] = _resolve_tag(obj, str(guid))

        fs_state = FlowsheetState()
        objects = []
        if coll:
            for guid, obj in self._iter_collection(coll):
                tag = tag_cache.get(str(guid), str(guid))
                try: typename = obj.GetType().Name
                except Exception: typename = "Unknown"
                objects.append({"tag": tag, "guid": str(guid),
                                 "type": typename,
                                 "category": _categorise(typename)})
        fs_state.update(path, objects)
        try:
            fs_state.loaded_mtime = os.path.getmtime(path)
        except Exception:
            fs_state.loaded_mtime = 0.0

        # ACC-3: read property package from flowsheet
        pkg_name = self._read_property_package(fs)
        fs_state.property_package = pkg_name

        self._flowsheets[fs_alias] = {
            "fs":        fs,
            "path":      path,
            "state":     fs_state,
            "tag_cache": tag_cache,
        }
        self._active_alias = fs_alias
        self.state = fs_state
        self._building = False   # loading an existing file is not a build-in-progress

        # ACC-4: validate feed specs
        warnings = self._validate_feed_specs_internal()

        return {
            "success":      True,
            "message":      f"Loaded and solved: {os.path.basename(path)}",
            "alias":        fs_alias,
            "path":         path,
            "object_count": len(tag_cache),
            "streams":      fs_state.streams,
            "unit_ops":     fs_state.unit_ops,
            "property_package": pkg_name,
            "feed_warnings":    warnings or None,
            "mtime":            fs_state.loaded_mtime,
        }

    def save_flowsheet(self, path: Optional[str] = None,
                       force: bool = False) -> Dict[str, Any]:
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        save_path = path or self._flowsheet_path
        if not save_path:
            return {"success": False, "error": "No save path provided"}

        # External-edit conflict check: refuse if on-disk mtime has
        # advanced past our last-loaded mtime unless force=True.
        cached_mtime = getattr(self.state, "loaded_mtime", 0.0) or 0.0
        if not force and os.path.exists(save_path) and cached_mtime > 0:
            try:
                on_disk_mtime = os.path.getmtime(save_path)
            except Exception:
                on_disk_mtime = 0.0
            if on_disk_mtime - cached_mtime > 1.0:  # 1s tolerance
                return {"success": False,
                        "conflict": True,
                        "code": "EXTERNAL_EDIT",
                        "error": "File modified externally since load. "
                                 "Reload or pass force=True to overwrite.",
                        "cached_mtime": cached_mtime,
                        "on_disk_mtime": on_disk_mtime}

        # Advisory lock: write .lock with our identity, then remove after.
        lock_path = save_path + ".lock"
        lock_ours = False
        if os.path.exists(lock_path) and not _is_stale_lock(lock_path):
            try:
                with open(lock_path, "r", encoding="utf-8") as f:
                    holder = f.read().strip()
            except Exception:
                holder = "?"
            return {"success": False,
                    "code": "LOCKED",
                    "error": f"File locked by '{holder}'. "
                             f"Wait or delete {os.path.basename(lock_path)}.",
                    "locked_by": holder}
        if os.path.exists(lock_path):
            try: os.remove(lock_path)
            except Exception: pass
        try:
            with open(lock_path, "w", encoding="utf-8") as f:
                f.write(f"AI-bridge pid={os.getpid()}")
            lock_ours = True
        except Exception:
            pass

        # Snapshot existing file before overwrite.
        backup = _backup_before_write(save_path)

        try:
            for method in ("SaveFlowsheet", "SaveFlowsheet2"):
                if hasattr(self._mgr, method):
                    try:
                        getattr(self._mgr, method)(self._flowsheet,
                                                   save_path, True)
                        try:
                            new_mtime = os.path.getmtime(save_path)
                            if self.state is not None:
                                self.state.loaded_mtime = new_mtime
                        except Exception:
                            new_mtime = 0.0
                        return {"success": True,
                                "saved_to": save_path,
                                "mtime": new_mtime,
                                "backup": backup}
                    except Exception:
                        pass
            return {"success": False,
                    "error": "No working SaveFlowsheet method",
                    "code": "SAVE_METHOD_MISSING"}
        finally:
            if lock_ours:
                try:
                    os.remove(lock_path)
                except Exception:
                    pass

    def switch_flowsheet(self, alias: str) -> Dict[str, Any]:
        if alias not in self._flowsheets:
            return {"success": False,
                    "error": f"Alias '{alias}' not found.",
                    "available": list(self._flowsheets.keys())}
        self._active_alias = alias
        self.state = self._flowsheets[alias]["state"]
        return {
            "success":   True,
            "message":   f"Switched to '{alias}'",
            "streams":   self.state.streams,
            "unit_ops":  self.state.unit_ops,
        }

    def list_loaded_flowsheets(self) -> Dict[str, Any]:
        return {
            "success": True,
            "active":  self._active_alias,
            "loaded":  {alias: entry["path"]
                        for alias, entry in self._flowsheets.items()},
        }

    # ── object enumeration ────────────────────────────────────────────────────

    def list_simulation_objects(self) -> Dict[str, Any]:
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}

        # During build phase (new_flowsheet called, save_and_solve not yet run),
        # objects are staged in self.state but the DWSIM engine collection may
        # not reflect them yet.  Return staged state so the agent knows the true
        # count and does NOT re-add objects thinking they were lost.
        if self._building:
            objects = []
            for tag in self.state.streams:
                objects.append({"tag": tag, "category": "stream",
                                 "type": self.state.object_types.get(tag, "MaterialStream")})
            for tag in self.state.unit_ops:
                objects.append({"tag": tag, "category": "unit_op",
                                 "type": self.state.object_types.get(tag, "UnitOperation")})
            return {
                "success": True,
                "count":   len(objects),
                "objects": objects,
                "_note":   (
                    "Flowsheet is in BUILD phase — objects staged but not yet solved. "
                    "Call save_and_solve when all objects, connections, and properties are set."
                ),
            }

        coll = self._get_collection()
        if coll is None:
            return {"success": False, "error": "Cannot access SimulationObjects"}
        objects = []
        tag_cache = self._active_tag_cache()
        for guid, obj in self._iter_collection(coll):
            try:
                tag      = tag_cache.get(str(guid)) or _resolve_tag(obj, guid)
                typename = obj.GetType().Name
                objects.append({"tag": tag, "guid": str(guid),
                                 "type": typename,
                                 "category": _categorise(typename)})
            except Exception as exc:
                objects.append({"guid": str(guid), "error": str(exc)})
        return {"success": True, "count": len(objects), "objects": objects}

    # ── property reading ──────────────────────────────────────────────────────

    def get_stream_properties(self, tag: str) -> Dict[str, Any]:
        obj = self._find_object(tag)
        if obj is None:
            known = list(self._active_tag_cache().values())[:20]
            return {"success": False,
                    "error": f"Stream '{tag}' not found. Known: {known}"}

        props: Dict[str, Any] = {"tag": tag}
        try:
            props["object_type"] = obj.GetType().Name
        except Exception:
            props["object_type"] = "unknown"

        props["temperature_K"]    = _read_prop(obj, 0,
            direct=("Temperature",),
            phase_attrs=("temperature", "Temperature"))
        props["pressure_Pa"]      = _read_prop(obj, 0,
            direct=("Pressure",),
            phase_attrs=("pressure", "Pressure"))
        props["molar_flow_mol_s"] = _read_prop(obj, 0,
            direct=("MolarFlow", "molarflow"),
            phase_attrs=("molarflow", "MolarFlow"))
        props["mass_flow_kg_s"]   = _read_prop(obj, 0,
            direct=("MassFlow", "massflow"),
            phase_attrs=("massflow", "MassFlow"))
        props["enthalpy_kJ_kg"]   = _read_prop(obj, 0,
            direct=(),
            phase_attrs=("enthalpy", "Enthalpy"))
        props["vapor_fraction"]   = _read_prop(obj, 0,
            direct=("VaporFraction",),
            phase_attrs=("vaporfraction", "VaporFraction"),
            allow_zero=True)
        props["volumetric_flow_m3_s"] = _read_prop(obj, 0,
            direct=("VolumetricFlow", "volumetricflow"),
            phase_attrs=("volumetric_flow", "volumetricflow", "VolumetricFlow"))
        props["density_kg_m3"]    = _read_prop(obj, 0,
            direct=(),
            phase_attrs=("density", "Density"))

        props = {k: v for k, v in props.items() if v is not None}

        if "temperature_K" in props:
            props["temperature_C"]    = round(props["temperature_K"] - 273.15, 3)
        if "pressure_Pa" in props:
            props["pressure_bar"]     = round(props["pressure_Pa"] / 1e5, 5)
            props["pressure_kPa"]     = round(props["pressure_Pa"] / 1e3, 4)
        if "molar_flow_mol_s" in props:
            props["molar_flow_kmolh"] = round(props["molar_flow_mol_s"] * 3.6, 5)
        if "mass_flow_kg_s" in props:
            props["mass_flow_kgh"]    = round(props["mass_flow_kg_s"] * 3600, 4)
        if "volumetric_flow_m3_s" in props:
            props["volumetric_flow_m3_h"] = round(
                props["volumetric_flow_m3_s"] * 3600, 6)

        comps = _read_compositions(obj)
        if comps:
            props["mole_fractions"] = comps
        mass_comps = _read_compositions_mass(obj)
        if mass_comps:
            props["mass_fractions"] = mass_comps

        return {"success": True, "properties": props}

    def get_object_properties(self, tag: str) -> Dict[str, Any]:
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        info: Dict[str, Any] = {}
        try:
            info["dotnet_type"] = obj.GetType().FullName
        except Exception:
            info["dotnet_type"] = "unknown"

        typename = info["dotnet_type"].lower()
        if "materialstream" in typename:
            return self.get_stream_properties(tag)

        info["summary"] = _get_unit_op_summary(obj)
        try:
            info["all_attributes"] = sorted(
                [a for a in dir(obj) if not a.startswith("_")])[:80]
        except Exception:
            info["all_attributes"] = []

        return {"success": True, "tag": tag, "properties": info}

    # ── property writing ──────────────────────────────────────────────────────

    def set_stream_property(self, tag: str, property_name: str,
                            value: float, unit: str = "") -> Dict[str, Any]:
        """
        Set a stream inlet spec.

        CRITICAL FIX (v2.1):
        - Mark Calculated=False FIRST (so solver re-reads Phase[0] on next solve)
        - Use reflection .SetValue() on Phase[0].Properties, NOT setattr()
          (setattr silently creates a Python shadow attribute without writing
           to the underlying .NET Nullable<Double> backing field)
        """
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False,
                    "error": f"Stream '{tag}' not found. "
                             f"Known: {list(self._active_tag_cache().values())[:20]}"}

        # Capture old value for undo support BEFORE we write the new one
        old_value: Any = None
        try:
            old_props = self.get_stream_properties(tag)
            _PROP_TO_KEY = {
                "temperature": "temperature_C",
                "pressure":    "pressure_bar",
                "massflow":    "mass_flow_kgh",
                "molarflow":   "molar_flow_kmolh",
                "vaporfraction": "vapor_fraction",
            }
            lookup_key = _PROP_TO_KEY.get(property_name.lower().replace(" ", "").replace("_", ""))
            if lookup_key and old_props.get(lookup_key) is not None:
                old_value = old_props[lookup_key]
        except Exception:
            pass

        si_value, si_unit, dot_attr = _convert_to_si(property_name, value, unit)
        if dot_attr is None:
            return {"success": False,
                    "error": f"Unknown property '{property_name}'"}

        # Physical validation — reject impossible values before touching the solver.
        # SI basis: T in K, P in Pa, flows in kg/s or mol/s, VF dimensionless [0,1].
        try:
            sv = float(si_value)
        except (TypeError, ValueError):
            return {"success": False, "code": "INVALID_VALUE",
                    "error": f"{property_name}={value!r} is not numeric."}
        import math as _math
        if not _math.isfinite(sv):
            return {"success": False, "code": "INVALID_VALUE",
                    "error": f"{property_name}={value}{unit} is NaN/inf; refusing to set."}
        _PHYS_MIN = {"Temperature": 0.0, "Pressure": 0.0,
                     "MassFlow": 0.0, "MolarFlow": 0.0,
                     "VaporFraction": 0.0}
        _PHYS_MAX = {"VaporFraction": 1.0}
        if dot_attr in _PHYS_MIN and sv < _PHYS_MIN[dot_attr]:
            return {"success": False, "code": "INVALID_VALUE",
                    "error": f"{property_name}={value}{unit} ({sv:g} {si_unit}) "
                             f"is below physical minimum ({_PHYS_MIN[dot_attr]} {si_unit}); "
                             f"refusing to set."}
        if dot_attr in _PHYS_MAX and sv > _PHYS_MAX[dot_attr]:
            return {"success": False, "code": "INVALID_VALUE",
                    "error": f"{property_name}={value}{unit} ({sv:g} {si_unit}) "
                             f"exceeds physical maximum ({_PHYS_MAX[dot_attr]} {si_unit}); "
                             f"refusing to set."}

        # PROP_MS codes — the CORRECT DWSIM Automation API
        _PROP_CODE = {
            "Temperature":   "PROP_MS_0",   # K
            "Pressure":      "PROP_MS_1",   # Pa
            "MassFlow":      "PROP_MS_2",   # kg/s
            "MolarFlow":     "PROP_MS_3",   # mol/s
            "VaporFraction": "PROP_MS_6",   # 0-1
        }

        # Step 1: mark NOT calculated BEFORE writing — solver will re-read Phase[0]
        _reflect_set_flag(obj, "Calculated", False)
        _reflect_set_flag(obj, "IsDirty",    True)

        set_ok = False
        tried  = []

        # Step 2 (PRIMARY): SetPropertyValue with PROP_MS codes — guaranteed to work
        prop_code = _PROP_CODE.get(dot_attr)
        if prop_code:
            try:
                obj.SetPropertyValue(prop_code, float(si_value))
                set_ok = True
                tried.append(f"SetPropertyValue({prop_code})")
            except Exception:
                pass

        # Step 3: Named setter methods (SetTemperature, SetPressure, etc.)
        if not set_ok:
            setter_name = f"Set{dot_attr}"
            setter = getattr(obj, setter_name, None)
            if callable(setter):
                try:
                    setter(float(si_value))
                    set_ok = True
                    tried.append(f"{setter_name}()")
                except Exception:
                    pass

        # Step 4: reflection on Phase[0].Properties
        if not set_ok:
            try:
                phases_prop = obj.GetType().GetProperty("Phases")
                if phases_prop:
                    phases = phases_prop.GetValue(obj)
                    for key in (0, "0"):
                        try:
                            phase0 = phases[key]
                            if phase0 is None:
                                continue
                            pp_prop = phase0.GetType().GetProperty("Properties")
                            if pp_prop is None:
                                continue
                            pp = pp_prop.GetValue(phase0)
                            if pp is None:
                                continue
                            for candidate in (dot_attr, dot_attr.lower()):
                                tp = pp.GetType().GetProperty(candidate)
                                if tp and tp.CanWrite:
                                    tp.SetValue(pp, float(si_value))
                                    set_ok = True
                                    tried.append(f"Phase0.Properties.{candidate}")
                                    break
                            if set_ok:
                                break
                        except Exception:
                            pass
            except Exception:
                pass

        # Step 5: last resort — setattr
        if not set_ok:
            pp = _get_phase_props(obj, 0)
            if pp is not None:
                for candidate in (dot_attr, dot_attr.lower()):
                    try:
                        setattr(pp, candidate, si_value)
                        set_ok = True
                        tried.append(f"setattr.Phase0.{candidate}")
                        break
                    except Exception:
                        pass

        if not set_ok:
            return {"success": False,
                    "error": f"Could not set '{dot_attr}' on '{tag}'",
                    "tried": tried}

        return {"success": True,
                "message":   f"Set {property_name}={value}{unit} on '{tag}'",
                "old_value": old_value,
                "new_value": value,
                "methods":   tried[:3]}

    def set_unit_op_property(self, tag: str, property_name: str,
                             value: Any) -> Dict[str, Any]:
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}
        # LLM sometimes passes numeric values as strings — coerce to float/int
        if isinstance(value, str):
            try:
                value = float(value)
            except (ValueError, TypeError):
                pass

        # ── Heater/Cooler: OutletTemperature requires CalcMode set first ───────
        key_lower = property_name.lower().replace("_", "").replace(" ", "")
        if key_lower == "outlettemperature":
            try:
                typename = obj.GetType().Name
            except Exception:
                typename = ""
            if any(t in typename for t in ("Heater", "Cooler")):
                import System  # type: ignore
                _sink = io.StringIO()
                _calc_set = False
                for attr in ("CalcMode", "SpecType", "CalculationMode"):
                    try:
                        prop = obj.GetType().GetProperty(attr)
                        if prop and prop.CanWrite and prop.PropertyType.IsEnum:
                            ev = System.Enum.Parse(prop.PropertyType, "OutletTemperature")
                            with redirect_stdout(_sink), redirect_stderr(_sink):
                                prop.SetValue(obj, ev)
                            _calc_set = True
                            break
                    except Exception:
                        continue
                # Set OutletTemperature via Nullable<Double> reflection
                try:
                    ot_prop = obj.GetType().GetProperty("OutletTemperature")
                    if ot_prop and ot_prop.CanWrite:
                        ot_prop.SetValue(obj, System.Nullable[System.Double](float(value)))
                        return {"success": True,
                                "message": f"Set CalcMode=OutletTemperature + OutletTemperature={value} on '{tag}'"}
                except Exception as e:
                    pass  # Fall through to generic setter below

        # ── OutletPressure on Pump/Compressor/Expander ────────────────────────
        if key_lower == "outletpressure":
            try:
                typename = obj.GetType().Name
            except Exception:
                typename = ""
            if any(t in typename for t in ("Pump", "Compressor", "Expander")):
                import System  # type: ignore
                _sink = io.StringIO()
                for mode_name in ("OutletPressure", "Outlet_Pressure", "PressureOut"):
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
                try:
                    op_prop = obj.GetType().GetProperty("OutletPressure")
                    if op_prop and op_prop.CanWrite:
                        op_prop.SetValue(obj, System.Nullable[System.Double](float(value)))
                        return {"success": True,
                                "message": f"Set CalcMode=OutletPressure + OutletPressure={value} on '{tag}'"}
                except Exception:
                    pass  # Fall through to generic setter below

        key_clean = property_name.lower().replace("_", "").replace(" ", "")
        # Try reflection first (handles .NET type boxing correctly)
        for tp in obj.GetType().GetProperties():
            if tp.Name.lower().replace("_", "") == key_clean and tp.CanWrite:
                if _reflect_set_typed(tp, obj, value):
                    return {"success": True,
                            "message": f"Set {tp.Name} = {value} on '{tag}'"}
        # Fallback: setattr (works for some pythonnet-exposed properties)
        target = next(
            (a for a in dir(obj) if a.lower().replace("_", "") == key_clean),
            property_name
        )
        coerced: Any = value
        for cast in (float, int):
            try:
                coerced = cast(value); break
            except (ValueError, TypeError):
                pass
        try:
            setattr(obj, target, coerced)
            return {"success": True, "message": f"Set {target} = {coerced} on '{tag}'"}
        except Exception as exc:
            return {"success": False, "error": str(exc), "tried": target}

    # ── ACC-1: set stream composition ─────────────────────────────────────────

    def set_stream_composition(self, tag: str,
                               compositions: Dict[str, float]) -> Dict[str, Any]:
        """
        ACC-1: Set mole fractions on a feed stream.

        FIXED: Primary path uses stream.InputComposition (Dict[String,Double])
        which is the authoritative DWSIM input spec store for compositions.
        Falls back to Phase[0].Compounds via reflection.
        """
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Stream '{tag}' not found."}

        if not compositions:
            return {"success": False, "code": "INVALID_VALUE",
                    "error": "Composition dict is empty — provide at least one "
                             "{compound: fraction} pair."}

        import math as _math
        for name, frac in compositions.items():
            try:
                f = float(frac)
            except (TypeError, ValueError):
                return {"success": False, "code": "INVALID_VALUE",
                        "error": f"Fraction for '{name}' is not numeric: {frac!r}"}
            if not _math.isfinite(f):
                return {"success": False, "code": "INVALID_VALUE",
                        "error": f"Fraction for '{name}' is NaN/inf: {frac!r}"}
            if f < 0.0:
                return {"success": False, "code": "INVALID_VALUE",
                        "error": f"Fraction for '{name}' is negative ({f}); "
                                 f"mole fractions must be in [0, 1]."}
            if f > 1.0:
                return {"success": False, "code": "INVALID_VALUE",
                        "error": f"Fraction for '{name}' exceeds 1.0 ({f}); "
                                 f"mole fractions must be in [0, 1]."}

        total = sum(float(v) for v in compositions.values())
        if abs(total - 1.0) > 0.01:
            return {"success": False, "code": "INVALID_VALUE",
                    "error": f"Mole fractions sum to {total:.4f}, must sum to 1.0 "
                             f"(tolerance 0.01)."}

        # Mark not-calculated first
        _reflect_set_flag(obj, "Calculated", False)
        _reflect_set_flag(obj, "IsDirty",    True)

        set_count = 0
        errors    = []

        # Path 1: InputComposition dict (confirmed DWSIM authoritative store)
        try:
            ic_prop = obj.GetType().GetProperty("InputComposition")
            if ic_prop:
                ic = ic_prop.GetValue(obj)
                if ic is not None:
                    for comp_name, frac in compositions.items():
                        # Try exact then case-insensitive key
                        matched_key = None
                        try:
                            if ic.ContainsKey(comp_name):
                                matched_key = comp_name
                            else:
                                for k in ic.Keys:
                                    if str(k).lower() == comp_name.lower():
                                        matched_key = str(k); break
                        except Exception:
                            pass

                        if matched_key:
                            try:
                                ic[matched_key] = float(frac)
                                set_count += 1
                            except Exception as e:
                                # Try reflection-based set
                                try:
                                    set_method = ic.GetType().GetMethod("set_Item")
                                    if set_method:
                                        set_method.Invoke(ic, [matched_key, float(frac)])
                                        set_count += 1
                                except Exception:
                                    errors.append(f"InputComposition[{comp_name}]: {e}")
                        else:
                            errors.append(f"Component '{comp_name}' not in InputComposition")
        except Exception as e:
            errors.append(f"InputComposition path: {e}")

        # Path 2: Phase[0].Compounds via reflection (backup)
        if set_count == 0:
            try:
                phases_p = obj.GetType().GetProperty("Phases")
                if phases_p:
                    phases = phases_p.GetValue(obj)
                    phase0 = phases[0]
                    if phase0:
                        cmpds_p = phase0.GetType().GetProperty("Compounds")
                        if cmpds_p:
                            cmpds = cmpds_p.GetValue(phase0)
                            if cmpds:
                                for comp_name, frac in compositions.items():
                                    comp = None
                                    try:
                                        comp = cmpds[comp_name]
                                    except Exception:
                                        for k in cmpds.Keys:
                                            if str(k).lower() == comp_name.lower():
                                                try: comp = cmpds[k]; break
                                                except Exception: pass
                                    if comp is None:
                                        errors.append(f"Component '{comp_name}' not found")
                                        continue
                                    for pname in ("MoleFraction", "molefraction"):
                                        tp = comp.GetType().GetProperty(pname)
                                        if tp and tp.CanWrite:
                                            tp.SetValue(comp, float(frac))
                                            set_count += 1
                                            break
            except Exception as e:
                errors.append(f"Phase0.Compounds path: {e}")

        if set_count == 0:
            return {"success": False,
                    "error":   "Could not set any mole fractions",
                    "details": errors[:5]}

        return {
            "success":      True,
            "message":      f"Set {set_count} component mole fractions on '{tag}'",
            "compositions": compositions,
            "errors":       errors or None,
        }



    # ── simulation execution ──────────────────────────────────────────────────

    def run_simulation(self) -> Dict[str, Any]:
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        errors, solved = [], False
        for method in ("CalculateFlowsheet2", "SolveFlowsheet", "RunAll"):
            if hasattr(self._mgr, method):
                try:
                    getattr(self._mgr, method)(self._flowsheet)
                    solved = True; break
                except Exception as exc:
                    errors.append(f"{method}: {exc}")
        if not solved:
            for method in ("Solve", "Calculate", "Run"):
                if hasattr(self._flowsheet, method):
                    try:
                        getattr(self._flowsheet, method)()
                        solved = True; break
                    except Exception as exc:
                        errors.append(f"fs.{method}: {exc}")
        if not solved:
            return {"success": False, "error": "No working solve method",
                    "attempts": errors}
        self._rebuild_active_cache()

        conv_errors = []
        for attr in ("Errors", "CalculationErrors", "Messages"):
            try:
                for e in getattr(self._flowsheet, attr):
                    m = str(e).strip()
                    if m: conv_errors.append(m)
            except Exception:
                pass

        # ACC-2: check convergence on every stream
        convergence = self._check_convergence_internal()

        # ── Safety Validation (post-solve) ───────────────────────────────────
        safety_warnings = []
        try:
            from safety_validator import SafetyValidator
            sr = self.get_simulation_results()
            sv = SafetyValidator()
            failures = sv.check(sr.get("stream_results", {}))
            if failures:
                safety_warnings = [
                    {"code": f.code, "severity": f.severity,
                     "description": f.description, "evidence": f.evidence}
                    for f in failures
                ]
        except Exception:
            pass

        result = {
            "success":            True,
            "message":            "Simulation completed",
            "convergence_errors": conv_errors or None,
            "convergence_check":  convergence,
        }
        if safety_warnings:
            result["safety_warnings"] = safety_warnings
            result["safety_status"]   = "VIOLATIONS_DETECTED"
        else:
            result["safety_status"]   = "PASSED"
        return result

    def get_simulation_results(self) -> Dict[str, Any]:
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        coll = self._get_collection()
        if coll is None:
            return {"success": False, "error": "Cannot access SimulationObjects"}
        results: Dict[str, Any] = {}
        tag_cache = self._active_tag_cache()
        for guid, obj in self._iter_collection(coll):
            try:
                if "materialstream" not in obj.GetType().Name.lower():
                    continue
            except Exception:
                continue
            tag = tag_cache.get(str(guid)) or str(guid)
            r = self.get_stream_properties(tag)
            if r["success"]:
                results[tag] = r["properties"]
        return {"success": True,
                "stream_count":   len(results),
                "stream_results": results}

    # ── ACC-2: convergence check ──────────────────────────────────────────────

    def check_convergence(self) -> Dict[str, Any]:
        """ACC-2: Verify every stream converged after the last solve."""
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        result = self._check_convergence_internal()
        return {"success": True, **result}

    def _check_convergence_internal(self) -> Dict[str, Any]:
        converged, not_converged, missing = [], [], []
        for tag in self.state.streams:
            r = self.get_stream_properties(tag)
            if not r["success"]:
                missing.append(tag)
                continue
            p = r["properties"]
            # A stream is considered converged if T, P, and at least one flow are set
            has_T = "temperature_K" in p
            has_P = "pressure_Pa" in p
            has_F = "molar_flow_mol_s" in p or "mass_flow_kg_s" in p
            if has_T and has_P and has_F:
                converged.append(tag)
            else:
                missing_props = []
                if not has_T: missing_props.append("T")
                if not has_P: missing_props.append("P")
                if not has_F: missing_props.append("flow")
                not_converged.append({"tag": tag, "missing": missing_props})

        all_ok = len(not_converged) == 0 and len(missing) == 0
        return {
            "all_converged":   all_ok,
            "converged":       converged,
            "not_converged":   not_converged,
            "inaccessible":    missing,
        }

    # ── ACC-3: property package ───────────────────────────────────────────────

    def get_property_package(self) -> Dict[str, Any]:
        """ACC-3: Read the thermodynamic property package from the flowsheet."""
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        pkg = self._read_property_package(self._flowsheet)
        self.state.property_package = pkg
        return {
            "success":          True,
            "property_package": pkg,
            "description":      _PP_DESCRIPTIONS.get(pkg.upper(), pkg),
        }

    def _read_property_package(self, fs) -> str:
        """Try various DWSIM API paths to read the property package name."""
        # Path 1: fs.SelectedPropertyPackage
        for attr in ("SelectedPropertyPackage", "PropertyPackage",
                     "ThermodynamicsPackage"):
            try:
                val = getattr(fs, attr)
                if val is not None:
                    name = str(val)
                    # It might be an object with a .Name property
                    try: name = str(val.Name)
                    except Exception: pass
                    if name and name not in ("None", ""):
                        return name
            except Exception:
                pass

        # Path 2: iterate property packages collection
        for coll_attr in ("PropertyPackages", "ThermodynamicsPackages"):
            try:
                coll = getattr(fs, coll_attr)
                if coll is None:
                    continue
                try:
                    names = []
                    for k in coll.Keys:
                        pp = coll[k]
                        try: names.append(str(pp.Name))
                        except Exception: names.append(str(k))
                    if names:
                        return ", ".join(names)
                except Exception:
                    pass
                # Try as list
                try:
                    items = list(coll)
                    if items:
                        try: return str(items[0].Name)
                        except Exception: return str(items[0])
                except Exception:
                    pass
            except Exception:
                pass

        return "Unknown"

    # ── ACC-4: feed validation ────────────────────────────────────────────────

    def validate_feed_specs(self) -> Dict[str, Any]:
        """ACC-4: Warn if any feed stream is missing T, P, or flow spec."""
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        warnings = self._validate_feed_specs_internal()
        return {
            "success":  True,
            "warnings": warnings,
            "all_ok":   len(warnings) == 0,
        }

    def _validate_feed_specs_internal(self) -> List[str]:
        warnings = []
        for tag in self.state.streams:
            r = self.get_stream_properties(tag)
            if not r["success"]:
                continue
            p = r["properties"]
            missing = []
            if "temperature_K" not in p: missing.append("Temperature")
            if "pressure_Pa" not in p:   missing.append("Pressure")
            if ("molar_flow_mol_s" not in p and
                    "mass_flow_kg_s" not in p):
                missing.append("Flow rate")
            if missing:
                warnings.append(
                    f"Stream '{tag}' is missing: {', '.join(missing)}")
        return warnings

    # ═══════════════════════════════════════════════════════════════════════════
    # INDUSTRIAL BRIDGE UPGRADES — Added for production-grade flowsheet support
    # ═══════════════════════════════════════════════════════════════════════════

    def robust_solve(
        self,
        max_attempts: int = 3,
        strategy: str = "standard",
    ) -> Dict[str, Any]:
        """
        Enhanced save_and_solve with adaptive convergence strategies.
        Escalates through strategies on failure for industrial flowsheets.

        strategy:
          'standard'  – single save+reload+solve (same as save_and_solve)
          'robust'    – 3 attempts: reload between each, escalating
          'aggressive'– 5 attempts: reload + reinitialise streams between each

        Returns the last result with all attempt details in '_attempts'.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}

        attempts_log = []
        n = max_attempts if strategy in ("robust", "aggressive") else 1

        for attempt in range(1, n + 1):
            try:
                # On attempt 2+, reload from disk to reset internal DWSIM state
                if attempt > 1 and self._flowsheet_path:
                    self.load_flowsheet(self._flowsheet_path,
                                        alias=self._active_alias)

                result = self.save_and_solve()
                attempts_log.append({
                    "attempt": attempt,
                    "success": result.get("success"),
                    "converged": result.get("converged"),
                })

                if result.get("success") and result.get("converged", True):
                    result["_attempts"] = attempts_log
                    result["_strategy"] = strategy
                    result["_attempts_used"] = attempt
                    return result

                # On aggressive mode attempt 2+: perturb stream temperatures
                if strategy == "aggressive" and attempt < n:
                    self._perturb_feeds_for_convergence()

            except Exception as exc:
                attempts_log.append({"attempt": attempt, "error": str(exc)})

        # All attempts exhausted — return last result with log
        last = self.save_and_solve()
        last["_attempts"] = attempts_log
        last["_strategy"] = strategy
        last["_attempts_used"] = n
        last["_hint"] = (
            f"Flowsheet did not converge after {n} attempts with strategy='{strategy}'. "
            "Try: (1) check tear stream specs, (2) reduce recycle ratio, "
            "(3) initialize distillation column temperature profile manually, "
            "(4) use a simpler property package for initial convergence."
        )
        return last

    def _perturb_feeds_for_convergence(self) -> None:
        """
        Slightly perturb feed stream temperatures to escape local non-convergence.
        Used internally by robust_solve aggressive strategy.
        Perturbation: ±5 K on all feed streams (streams with no inlet connections).
        """
        import random
        if self._flowsheet is None:
            return
        coll = self._get_collection()
        if coll is None:
            return
        try:
            for guid, obj in self._iter_collection(coll):
                try:
                    if "materialstream" not in obj.GetType().Name.lower():
                        continue
                    # Only perturb feed streams (GraphicObject.InputConnectors all empty)
                    go = getattr(obj, "GraphicObject", None)
                    if go is None:
                        continue
                    connectors = getattr(go, "InputConnectors", [])
                    is_feed = all(not getattr(c, "IsAttached", False)
                                  for c in connectors)
                    if not is_feed:
                        continue
                    ph = getattr(obj, "Phases", None)
                    if ph is None:
                        continue
                    props = getattr(ph[0], "Properties", None)
                    if props is None:
                        continue
                    t = getattr(props, "temperature", None)
                    if t is not None and float(t) > 0:
                        props.temperature = float(t) + random.uniform(-5, 5)
                except Exception:
                    pass
        except Exception:
            pass

    def initialize_distillation(
        self,
        column_tag: str,
        T_top_C: Optional[float] = None,
        T_bot_C: Optional[float] = None,
        algorithm: str = "auto",
        reflux_ratio: Optional[float] = None,
        max_attempts: int = 4,
    ) -> Dict[str, Any]:
        """
        Initialize and converge a rigorous distillation column for industrial use.

        Sets a linear temperature profile from top to bottom before solving,
        then escalates through DWSIM's convergence algorithms on failure:
          IO  → Burningham-Otto → Sum-Rates → reduced-reflux retry

        column_tag  : tag of the DistillationColumn or AbsorptionColumn object
        T_top_C     : estimated top temperature in °C (condenser region)
        T_bot_C     : estimated bottom temperature in °C (reboiler region)
        algorithm   : 'auto' | 'IO' | 'BO' | 'SR'
        reflux_ratio: if provided, sets reflux ratio before each attempt
        max_attempts: max convergence attempts (default 4)

        Returns convergence status, algorithm used, and stream results.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}

        coll = self._get_collection()
        if coll is None:
            return {"success": False, "error": "Cannot access flowsheet objects"}

        # Locate the column object
        col_obj = None
        tag_cache = self._active_tag_cache()
        for guid, obj in self._iter_collection(coll):
            tag = tag_cache.get(str(guid), "")
            if tag == column_tag:
                col_obj = obj
                break

        if col_obj is None:
            return {"success": False,
                    "error": f"Column tag '{column_tag}' not found in flowsheet"}

        # DWSIM algorithm codes: 0=IO, 1=BO, 2=SR
        _ALGO_MAP = {"IO": 0, "BO": 1, "SR": 2,
                     "inside-out": 0, "burningham-otto": 1, "sum-rates": 2}
        _ALGO_NAME = {0: "Inside-Out (IO)", 1: "Burningham-Otto (BO)",
                      2: "Sum-Rates (SR)"}

        if algorithm == "auto":
            algo_sequence = [0, 1, 2]  # escalate on failure
        else:
            start = _ALGO_MAP.get(algorithm.lower(), 0)
            algo_sequence = [start] + [a for a in [0, 1, 2] if a != start]

        attempts_log = []
        result = {"success": False, "error": "No attempts made"}

        for attempt_idx, algo_code in enumerate(algo_sequence[:max_attempts]):
            try:
                # Set convergence algorithm
                for attr in ("ConvergenceMethod", "SolvingScheme",
                             "SolverType", "Algorithm"):
                    try:
                        setattr(col_obj, attr, algo_code)
                    except Exception:
                        pass

                # Set temperature profile if provided
                if T_top_C is not None and T_bot_C is not None:
                    for attr in ("TopTemperature", "CondenserTemperature",
                                 "TemperatureTop"):
                        try:
                            setattr(col_obj, attr, T_top_C + 273.15)
                            break
                        except Exception:
                            pass
                    for attr in ("BottomTemperature", "ReboilerTemperature",
                                 "TemperatureBottom"):
                        try:
                            setattr(col_obj, attr, T_bot_C + 273.15)
                            break
                        except Exception:
                            pass

                # Set reflux ratio if provided (reduce on retries)
                if reflux_ratio is not None:
                    rr = reflux_ratio * (1.0 - 0.1 * attempt_idx)
                    rr = max(rr, 1.05)  # never below 5% above minimum
                    for attr in ("RefluxRatio", "L_D_Ratio", "Reflux"):
                        try:
                            setattr(col_obj, attr, rr)
                            break
                        except Exception:
                            pass

                # Reload from disk to ensure clean state
                if self._flowsheet_path and attempt_idx > 0:
                    self.load_flowsheet(self._flowsheet_path,
                                        alias=self._active_alias)

                # Solve
                result = self.save_and_solve()
                algo_name = _ALGO_NAME.get(algo_code, str(algo_code))
                attempts_log.append({
                    "attempt": attempt_idx + 1,
                    "algorithm": algo_name,
                    "success": result.get("success"),
                    "converged": result.get("converged"),
                })

                if result.get("success"):
                    result["_algorithm_used"] = algo_name
                    result["_attempts"] = attempts_log
                    result["_column_tag"] = column_tag
                    return result

            except Exception as exc:
                attempts_log.append({
                    "attempt": attempt_idx + 1,
                    "algorithm": _ALGO_NAME.get(algo_code, "?"),
                    "error": str(exc),
                })

        result["_attempts"] = attempts_log
        result["_column_tag"] = column_tag
        result["_hint"] = (
            f"Column '{column_tag}' failed to converge with all algorithms "
            f"({', '.join(_ALGO_NAME[a] for a in algo_sequence[:max_attempts])}). "
            "Suggestions: (1) provide T_top_C and T_bot_C closer to actual values, "
            "(2) start with a lower reflux ratio (1.2-1.5 × minimum), "
            "(3) check feed stage position (feed near middle for binary systems), "
            "(4) verify NRTL/UNIQUAC BIPs are set for polar pairs."
        )
        return result

    def optimize_constrained(
        self,
        variables: List[Dict],
        observe_tag: str,
        observe_property: str,
        constraints: Optional[List[Dict]] = None,
        minimize: bool = True,
        max_iter: int = 100,
        population_size: int = 15,
        seed: int = 42,
        on_progress=None,
    ) -> Dict[str, Any]:
        """
        Multi-variable optimization with inequality constraints.
        Critical for industrial applications with product spec requirements.

        variables : [{tag, property, unit, lower, upper}, ...]
        observe_tag / observe_property : objective to minimize/maximize
        constraints : [{tag, property, unit, operator, value}, ...]
            operator: '>=' | '<=' | '==' (approximate)
            example: [{"tag":"Product","property":"mole_fraction_water",
                       "unit":"", "operator":"<=", "value":0.005}]
        minimize  : True = minimize objective
        max_iter  : differential evolution max iterations
        seed      : reproducibility

        Returns optimal variables, objective value, constraint satisfaction.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not variables:
            return {"success": False, "error": "variables list is empty"}

        try:
            from scipy.optimize import differential_evolution
        except ImportError:
            return {"success": False,
                    "error": "scipy not installed: pip install scipy"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        bounds     = [(float(v["lower"]), float(v["upper"])) for v in variables]
        history    = []
        eval_count = [0]
        PENALTY    = 1e6   # large penalty for constraint violation

        def _set_vars(x_vec):
            for v, xi in zip(variables, x_vec):
                r = self.set_stream_property(
                    v["tag"], v["property"], float(xi), v.get("unit", ""))
                if not r["success"]:
                    self.set_unit_op_property(
                        v["tag"], v["property"], float(xi))

        def _get_obs():
            obs_r = self.get_stream_properties(observe_tag)
            if obs_r.get("success"):
                val = obs_r["properties"].get(observe_property)
                if val is not None:
                    return float(val)
            # Try as unit op property
            uo_r = self.get_object_properties(observe_tag)
            if uo_r.get("success"):
                val = uo_r.get("properties", {}).get(observe_property)
                if val is not None:
                    return float(val)
            return None

        def _check_constraints():
            penalty = 0.0
            satisfied = []
            if not constraints:
                return 0.0, []
            for c in constraints:
                r = self.get_stream_properties(c["tag"])
                if not r.get("success"):
                    r = self.get_object_properties(c["tag"])
                val = r.get("properties", {}).get(c["property"])
                if val is None:
                    penalty += PENALTY
                    satisfied.append({"constraint": c, "value": None,
                                      "satisfied": False})
                    continue
                val_f   = float(val)
                limit   = float(c["value"])
                op      = c.get("operator", ">=")
                if op == ">=":
                    viol = max(0.0, limit - val_f)
                elif op == "<=":
                    viol = max(0.0, val_f - limit)
                elif op == "==":
                    viol = abs(val_f - limit)
                else:
                    viol = 0.0
                penalty  += viol * PENALTY
                satisfied.append({"constraint": c, "value": round(val_f, 6),
                                  "satisfied": viol < 1e-6})
            return penalty, satisfied

        def objective(x_vec):
            eval_count[0] += 1
            if base_path:
                self.load_flowsheet(base_path, alias=base_alias)
            _set_vars(x_vec)
            run_r = self.run_simulation()
            if not run_r.get("success"):
                return PENALTY

            obj_val = _get_obs()
            if obj_val is None:
                return PENALTY

            penalty, _ = _check_constraints()
            fval = (obj_val if minimize else -obj_val) + penalty

            entry = {
                "eval": eval_count[0],
                "variables": {v["tag"]+"."+v["property"]: round(float(xi), 4)
                              for v, xi in zip(variables, x_vec)},
                "objective": round(obj_val, 6),
                "penalty": round(penalty, 2),
            }
            history.append(entry)

            if on_progress:
                try:
                    on_progress(eval_count[0], x_vec, obj_val,
                                min(h["objective"] for h in history)
                                if minimize else
                                max(h["objective"] for h in history))
                except Exception:
                    pass
            return fval

        result = differential_evolution(
            objective, bounds,
            maxiter=max_iter,
            popsize=population_size,
            seed=seed,
            tol=1e-5,
            mutation=(0.5, 1.5),
            recombination=0.9,
        )

        # Final evaluation at optimum
        if base_path:
            self.load_flowsheet(base_path, alias=base_alias)
        _set_vars(result.x)
        self.run_simulation()
        final_obj   = _get_obs()
        _, cons_sat = _check_constraints()
        stream_res  = self.get_simulation_results().get("stream_results", {})

        opt_vars = {
            f"{v['tag']}.{v['property']}": {
                "value": round(float(xi), 6),
                "unit":  v.get("unit", ""),
            }
            for v, xi in zip(variables, result.x)
        }

        all_satisfied = all(c.get("satisfied", False) for c in cons_sat)

        return {
            "success":              True,
            "optimal_variables":    opt_vars,
            "optimal_objective":    round(final_obj, 6) if final_obj else None,
            "minimize":             minimize,
            "constraints":          cons_sat,
            "all_constraints_satisfied": all_satisfied,
            "evaluations":          eval_count[0],
            "scipy_success":        result.success,
            "stream_results":       stream_res,
            "history":              history[-20:],  # last 20 for context
        }

    def optimize_multiobjective(
        self,
        variables: List[Dict],
        objectives: List[Dict],
        n_points: int = 10,
        max_iter_per_point: int = 50,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """
        Multi-objective optimization via weighted sum scalarization.
        Generates a Pareto front approximation for industrial trade-off analysis.

        objectives : [{tag, property, unit, minimize, weight_start, weight_end}, ...]
            Each objective is weighted from weight_start to weight_end across n_points.
            Example: [
              {"tag":"HYDROGEN","property":"mole_fraction_h2","minimize":False,
               "weight_start":0.9,"weight_end":0.1},  # maximize H2 purity
              {"tag":"Q-REF","property":"energy_kW","minimize":True,
               "weight_start":0.1,"weight_end":0.9},  # minimize energy
            ]
        n_points    : number of Pareto front points (default 10)
        seed        : reproducibility

        Returns pareto_front list of {weights, variables, objective_values}.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not objectives or len(objectives) < 2:
            return {"success": False,
                    "error": "At least 2 objectives required for multi-objective optimization"}

        pareto_front = []
        base_path  = self._flowsheet_path
        base_alias = self._active_alias

        try:
            from scipy.optimize import differential_evolution
        except ImportError:
            return {"success": False,
                    "error": "scipy not installed: pip install scipy"}

        bounds = [(float(v["lower"]), float(v["upper"])) for v in variables]

        def _get_obj_val(obj_spec: Dict) -> Optional[float]:
            r = self.get_stream_properties(obj_spec["tag"])
            if not r.get("success"):
                r = self.get_object_properties(obj_spec["tag"])
            return r.get("properties", {}).get(obj_spec["property"])

        for i in range(n_points):
            alpha = i / max(n_points - 1, 1)  # 0 → 1

            # Interpolate weights for each objective
            weights = []
            for obj_spec in objectives:
                w0 = float(obj_spec.get("weight_start", 1.0))
                w1 = float(obj_spec.get("weight_end",   0.0))
                weights.append(w0 + alpha * (w1 - w0))

            # Normalize weights to sum to 1
            total = sum(abs(w) for w in weights) or 1.0
            weights = [w / total for w in weights]

            def scalarized(x_vec, _w=weights):
                if base_path:
                    self.load_flowsheet(base_path, alias=base_alias)
                for v, xi in zip(variables, x_vec):
                    r = self.set_stream_property(
                        v["tag"], v["property"], float(xi), v.get("unit",""))
                    if not r["success"]:
                        self.set_unit_op_property(
                            v["tag"], v["property"], float(xi))
                run_r = self.run_simulation()
                if not run_r.get("success"):
                    return 1e9
                total_obj = 0.0
                for wj, obj_spec in zip(_w, objectives):
                    val = _get_obj_val(obj_spec)
                    if val is None:
                        return 1e9
                    sign = 1.0 if obj_spec.get("minimize", True) else -1.0
                    total_obj += wj * sign * float(val)
                return total_obj

            res = differential_evolution(
                scalarized, bounds,
                maxiter=max_iter_per_point,
                seed=seed + i,
                tol=1e-4,
                popsize=10,
            )

            # Evaluate objectives at optimum
            if base_path:
                self.load_flowsheet(base_path, alias=base_alias)
            for v, xi in zip(variables, res.x):
                r = self.set_stream_property(
                    v["tag"], v["property"], float(xi), v.get("unit",""))
                if not r["success"]:
                    self.set_unit_op_property(
                        v["tag"], v["property"], float(xi))
            self.run_simulation()

            obj_vals = {}
            for obj_spec in objectives:
                val = _get_obj_val(obj_spec)
                key = f"{obj_spec['tag']}.{obj_spec['property']}"
                obj_vals[key] = round(float(val), 6) if val is not None else None

            pareto_front.append({
                "point_index":       i + 1,
                "weights":           {f"{o['tag']}.{o['property']}": round(w, 4)
                                      for o, w in zip(objectives, weights)},
                "optimal_variables": {f"{v['tag']}.{v['property']}":
                                      round(float(xi), 6)
                                      for v, xi in zip(variables, res.x)},
                "objective_values":  obj_vals,
                "scipy_success":     res.success,
            })

        return {
            "success":      True,
            "pareto_front": pareto_front,
            "n_points":     len(pareto_front),
            "objectives":   [f"{o['tag']}.{o['property']}" for o in objectives],
            "variables":    [f"{v['tag']}.{v['property']}" for v in variables],
        }

    def parametric_study_2d(
        self,
        vary1_tag: str,
        vary1_property: str,
        vary1_unit: str,
        vary1_values: List[float],
        vary2_tag: str,
        vary2_property: str,
        vary2_unit: str,
        vary2_values: List[float],
        observe_tag: str,
        observe_property: str,
        on_progress=None,
    ) -> Dict[str, Any]:
        """
        Two-variable parametric study generating a response surface matrix.
        Equivalent to RSM Central Composite Design data generation.

        vary1 / vary2   : input variables (tag, property, unit, list of values)
        observe         : output to record at each combination
        on_progress     : optional callback(i, j, n1, n2, val) for SSE streaming

        Returns:
          matrix: list of {vary1, vary2, observe} dicts (n1 × n2 combinations)
          summary: min/max/argmin/argmax across the surface
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not vary1_values or not vary2_values:
            return {"success": False, "error": "Both vary1_values and vary2_values required"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        matrix     = []
        n1, n2     = len(vary1_values), len(vary2_values)
        total      = n1 * n2
        count      = 0

        for i, v1 in enumerate(vary1_values):
            for j, v2 in enumerate(vary2_values):
                count += 1
                try:
                    # Reload clean state
                    if base_path:
                        self.load_flowsheet(base_path, alias=base_alias)

                    # Set variable 1
                    r1 = self.set_stream_property(
                        vary1_tag, vary1_property, float(v1), vary1_unit)
                    if not r1["success"]:
                        self.set_unit_op_property(
                            vary1_tag, vary1_property, float(v1))

                    # Set variable 2
                    r2 = self.set_stream_property(
                        vary2_tag, vary2_property, float(v2), vary2_unit)
                    if not r2["success"]:
                        self.set_unit_op_property(
                            vary2_tag, vary2_property, float(v2))

                    # Solve
                    run_r = self.run_simulation()
                    obs_val = None
                    if run_r.get("success"):
                        obs_r = self.get_stream_properties(observe_tag)
                        if obs_r.get("success"):
                            obs_val = obs_r["properties"].get(observe_property)
                        if obs_val is None:
                            uo_r = self.get_object_properties(observe_tag)
                            if uo_r.get("success"):
                                obs_val = (uo_r.get("properties", {})
                                           .get(observe_property))

                    row = {
                        vary1_property: round(float(v1), 6),
                        vary2_property: round(float(v2), 6),
                        observe_property: round(float(obs_val), 6)
                                          if obs_val is not None else None,
                        "converged": run_r.get("success", False),
                        "point_index": count,
                    }
                    matrix.append(row)

                    if on_progress:
                        try:
                            on_progress(i, j, n1, n2, obs_val)
                        except Exception:
                            pass

                except Exception as exc:
                    matrix.append({
                        vary1_property: float(v1),
                        vary2_property: float(v2),
                        observe_property: None,
                        "converged": False,
                        "error": str(exc),
                        "point_index": count,
                    })

        # Summary statistics
        valid_vals = [r[observe_property] for r in matrix
                      if r.get(observe_property) is not None]
        summary = {}
        if valid_vals:
            best_row = (min if True else max)(
                [r for r in matrix if r.get(observe_property) is not None],
                key=lambda r: r[observe_property])
            worst_row = max(
                [r for r in matrix if r.get(observe_property) is not None],
                key=lambda r: r[observe_property])
            summary = {
                "min_value":    round(min(valid_vals), 6),
                "max_value":    round(max(valid_vals), 6),
                "min_at":       {vary1_property: best_row[vary1_property],
                                  vary2_property: best_row[vary2_property]},
                "max_at":       {vary1_property: worst_row[vary1_property],
                                  vary2_property: worst_row[vary2_property]},
                "success_rate": f"{len(valid_vals)}/{total}",
            }

        return {
            "success":        True,
            "matrix":         matrix,
            "n_points":       total,
            "vary1":          {"tag": vary1_tag, "property": vary1_property,
                                "values": vary1_values},
            "vary2":          {"tag": vary2_tag, "property": vary2_property,
                                "values": vary2_values},
            "observe":        {"tag": observe_tag, "property": observe_property},
            "summary":        summary,
        }

    # ── ACC-5: optimize parameter ─────────────────────────────────────────────

    def optimize_parameter(
        self,
        vary_tag:          str,
        vary_property:     str,
        vary_unit:         str,
        lower_bound:       float,
        upper_bound:       float,
        observe_tag:       str,
        observe_property:  str,
        minimize:          bool = True,
        tolerance:         float = 1e-4,
        max_iterations:    int = 50,
    ) -> Dict[str, Any]:
        """
        ACC-5: Use SciPy bounded scalar minimisation to find the value of
        vary_tag.vary_property (in the given unit, within [lower_bound, upper_bound])
        that minimises (or maximises) observe_tag.observe_property.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not self._flowsheet_path:
            return {"success": False, "error": "No flowsheet path for reload"}

        try:
            from scipy.optimize import minimize_scalar  # type: ignore
        except ImportError:
            return {"success": False,
                    "error": "scipy not installed: pip install scipy"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        call_count = [0]
        history    = []

        def objective(x):
            call_count[0] += 1
            load_r = self.load_flowsheet(base_path, alias=base_alias)
            if not load_r["success"]:
                return float("inf")

            set_r = self.set_stream_property(vary_tag, vary_property, x, vary_unit)
            if not set_r["success"]:
                set_r = self.set_unit_op_property(vary_tag, vary_property, x)
            if not set_r["success"]:
                return float("inf")

            run_r = self.run_simulation()
            if not run_r["success"]:
                return float("inf")

            obs_r = self.get_stream_properties(observe_tag)
            if not obs_r["success"]:
                return float("inf")

            val = obs_r["properties"].get(observe_property)
            if val is None:
                return float("inf")

            fval = float(val)
            history.append({"x": x, "y": fval})
            return fval if minimize else -fval

        result = minimize_scalar(
            objective,
            bounds=(lower_bound, upper_bound),
            method="bounded",
            options={"xatol": tolerance, "maxiter": max_iterations},
        )

        optimal_x   = result.x
        optimal_val = result.fun if minimize else -result.fun

        return {
            "success":          True,
            "optimal_input":    {
                "tag":      vary_tag,
                "property": vary_property,
                "unit":     vary_unit,
                "value":    round(optimal_x, 6),
            },
            "optimal_output":   {
                "tag":      observe_tag,
                "property": observe_property,
                "value":    round(optimal_val, 6),
            },
            "minimize":         minimize,
            "iterations":       call_count[0],
            "converged":        result.success if hasattr(result, "success") else True,
            "history":          history,
        }

    # ── pinch analysis ────────────────────────────────────────────────────────

    def pinch_analysis(self, min_approach_temp_C: float = 10.0) -> Dict[str, Any]:
        """
        Perform Pinch Analysis (Linnhoff method) on the loaded flowsheet.

        Algorithm:
          1. Classify each unit op as heater (hot utility) or cooler (cold utility)
          2. Build hot composite curve: stream temperatures + duties
          3. Build cold composite curve
          4. Shift cold curve right by ΔTmin → find pinch point (overlap minimum)
          5. Calculate minimum heating utility (QHmin) and cooling utility (QCmin)

        Returns:
          pinch_temp_C, QH_min_kW, QC_min_kW, current_QH_kW, current_QC_kW,
          potential_savings_kW, heat_recovery_pct, hot_streams, cold_streams
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}

        try:
            from dwsim_bridge_v2 import _get_unit_op_summary
            from safety_validator import SafetyValidator

            coll = self._get_collection()
            if coll is None:
                return {"success": False, "error": "Cannot access flowsheet collection"}

            tag_cache = self._active_tag_cache()
            hot_streams: List[Dict] = []    # need cooling (heaters that dump heat out)
            cold_streams: List[Dict] = []   # need heating (coolers that absorb heat)

            # Gather process streams from unit op summaries
            for guid, obj in self._iter_collection(coll):
                try:
                    typename = obj.GetType().Name
                    if "Stream" in typename:
                        continue
                    tag     = tag_cache.get(str(guid), "")
                    summary = _get_unit_op_summary(obj, tag)
                    duty    = summary.get("duty_kW") or summary.get("heat_duty_kW")
                    if duty is None:
                        continue
                    duty = float(duty)

                    # Get connected stream temperatures
                    t_in  = summary.get("inlet_temperature_C")
                    t_out = summary.get("outlet_temperature_C")
                    if t_in is None:
                        t_in = summary.get("temperature_in_C")
                    if t_out is None:
                        t_out = summary.get("temperature_out_C")

                    # If temps not in summary, estimate from stream results
                    if t_in is None or t_out is None:
                        sr = self.get_simulation_results()
                        streams_data = sr.get("stream_results", {})
                        for stag, sprops in streams_data.items():
                            if duty > 0 and t_in is None:
                                t_in  = sprops.get("temperature_C")
                            if duty > 0 and t_out is None:
                                t_out = sprops.get("temperature_C")
                            if t_in is not None and t_out is not None:
                                break

                    if t_in is None or t_out is None:
                        continue

                    t_in, t_out = float(t_in), float(t_out)

                    if duty > 0:  # Heater — process stream needs heating → COLD stream
                        cold_streams.append({
                            "tag": tag, "T_in_C": t_in, "T_out_C": t_out,
                            "duty_kW": abs(duty),
                        })
                    elif duty < 0:  # Cooler — process stream needs cooling → HOT stream
                        hot_streams.append({
                            "tag": tag, "T_in_C": t_in, "T_out_C": t_out,
                            "duty_kW": abs(duty),
                        })
                except Exception:
                    continue

            if not hot_streams and not cold_streams:
                return {
                    "success":  True,
                    "message":  "No heat exchange units found in flowsheet. "
                                "Add heaters/coolers to enable pinch analysis.",
                    "hot_streams":  [],
                    "cold_streams": [],
                }

            # Current utility loads
            current_QH_kW = sum(s["duty_kW"] for s in cold_streams)
            current_QC_kW = sum(s["duty_kW"] for s in hot_streams)

            # Pinch calculation — Problem Table Algorithm (simplified)
            dT = float(min_approach_temp_C)

            # Temperature intervals: all supply/target temperatures (hot shifted down by ΔTmin/2)
            hot_temps  = sorted({s["T_in_C"] for s in hot_streams}  |
                                 {s["T_out_C"] for s in hot_streams}, reverse=True)
            cold_temps = sorted({s["T_in_C"] for s in cold_streams} |
                                 {s["T_out_C"] for s in cold_streams}, reverse=True)
            all_temps = sorted(
                set(hot_temps) | {t + dT for t in cold_temps}, reverse=True
            )

            # Heat cascade
            surplus = 0.0
            min_surplus = float("inf")
            pinch_temp = None
            for i in range(len(all_temps) - 1):
                T_hi, T_lo = all_temps[i], all_temps[i + 1]
                dH_hot  = sum(s["duty_kW"] * (T_hi - T_lo) /
                               max(abs(s["T_in_C"] - s["T_out_C"]), 0.1)
                               for s in hot_streams
                               if max(s["T_in_C"], s["T_out_C"]) >= T_hi and
                                  min(s["T_in_C"], s["T_out_C"]) <= T_lo)
                dH_cold = sum(s["duty_kW"] * (T_hi - T_lo) /
                               max(abs(s["T_in_C"] - s["T_out_C"]), 0.1)
                               for s in cold_streams
                               if max(s["T_in_C"], s["T_out_C"]) >= T_hi - dT and
                                  min(s["T_in_C"], s["T_out_C"]) <= T_lo - dT)
                surplus += dH_hot - dH_cold
                if surplus < min_surplus:
                    min_surplus = surplus
                    pinch_temp  = (T_hi + T_lo) / 2

            # QH_min = amount needed to make cascade feasible (push min surplus to 0)
            QH_min_kW = max(0.0, -min_surplus)
            QC_min_kW = max(0.0, QH_min_kW + current_QH_kW - current_QC_kW)

            potential_savings = max(0.0, current_QH_kW - QH_min_kW)
            pct = potential_savings / current_QH_kW * 100 if current_QH_kW > 0 else 0.0

            return {
                "success":            True,
                "min_approach_temp_C": dT,
                "pinch_temp_C":       round(pinch_temp, 1) if pinch_temp else None,
                "QH_current_kW":      round(current_QH_kW, 1),
                "QC_current_kW":      round(current_QC_kW, 1),
                "QH_min_kW":          round(QH_min_kW, 1),
                "QC_min_kW":          round(QC_min_kW, 1),
                "potential_savings_kW": round(potential_savings, 1),
                "heat_recovery_pct":  round(pct, 1),
                "hot_streams":        hot_streams,
                "cold_streams":       cold_streams,
                "interpretation": (
                    f"With ΔTmin={dT}°C, minimum hot utility = {QH_min_kW:.1f} kW "
                    f"vs current {current_QH_kW:.1f} kW. "
                    f"Potential savings: {potential_savings:.1f} kW ({pct:.0f}%) "
                    f"through internal heat recovery."
                    + (f" Pinch point at ~{pinch_temp:.1f}°C." if pinch_temp else "")
                ),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    def optimize_multivar(
        self,
        variables: List[Dict],          # [{tag, property, unit, lower, upper}, ...]
        observe_tag:       str,
        observe_property:  str,
        minimize:          bool = True,
        max_iterations:    int = 100,
        population_size:   int = 8,
        tolerance:         float = 1e-3,
    ) -> Dict[str, Any]:
        """
        Multi-variable optimisation using SciPy differential_evolution.

        variables: list of {tag, property, unit, lower_bound, upper_bound}
        Example: optimise reflux ratio + feed stage for minimum reboiler duty.

        Note: DWSIM bridge is single-threaded; workers=1 is enforced.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not self._flowsheet_path:
            return {"success": False, "error": "No flowsheet path for reload"}
        if not variables:
            return {"success": False, "error": "variables list is empty"}
        try:
            from scipy.optimize import differential_evolution  # type: ignore
        except ImportError:
            return {"success": False, "error": "scipy not installed: pip install scipy"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        call_count = [0]
        history    = []

        def objective(x: List[float]) -> float:
            call_count[0] += 1
            load_r = self.load_flowsheet(base_path, alias=base_alias)
            if not load_r["success"]:
                return float("inf")
            for xi, var in zip(x, variables):
                set_r = self.set_stream_property(
                    var["tag"], var["property"], xi, var.get("unit", ""))
                if not set_r["success"]:
                    set_r = self.set_unit_op_property(
                        var["tag"], var["property"], xi)
                if not set_r["success"]:
                    return float("inf")
            run_r = self.run_simulation()
            if not run_r["success"]:
                return float("inf")
            obs_r = self.get_stream_properties(observe_tag)
            if not obs_r["success"]:
                return float("inf")
            val = obs_r["properties"].get(observe_property)
            if val is None:
                return float("inf")
            fval = float(val)
            history.append({"x": list(x), "y": fval})
            return fval if minimize else -fval

        bounds = [(v["lower_bound"], v["upper_bound"]) for v in variables]
        result = differential_evolution(
            objective,
            bounds,
            maxiter=max_iterations,
            popsize=population_size,
            tol=tolerance,
            seed=42,
            workers=1,   # single-threaded — DWSIM bridge not thread-safe
        )

        optimal_val = result.fun if minimize else -result.fun
        optimal_vars = [
            {
                "tag":      v["tag"],
                "property": v["property"],
                "unit":     v.get("unit", ""),
                "optimal":  round(xi, 6),
                "lower":    v["lower_bound"],
                "upper":    v["upper_bound"],
            }
            for v, xi in zip(variables, result.x)
        ]

        return {
            "success":        True,
            "optimal_inputs": optimal_vars,
            "optimal_output": {
                "tag":      observe_tag,
                "property": observe_property,
                "value":    round(optimal_val, 6),
            },
            "minimize":     minimize,
            "converged":    bool(result.success),
            "iterations":   call_count[0],
            "message":      result.message,
            "history":      history[-20:],  # last 20 evaluations
        }

    def bayesian_optimize(
        self,
        variables:    List[Dict],   # [{tag, property, unit, lower, upper}, ...]
        observe_tag:  str,
        observe_property: str,
        minimize:     bool  = True,
        n_initial:    int   = 5,
        max_iter:     int   = 20,
        xi:           float = 0.01,
        seed:         int   = 42,
        save_plot:    str   = "",
        on_progress=None,     # callable(iter, params, value, best) for SSE streaming
    ) -> Dict[str, Any]:
        """
        Bayesian Optimisation of a DWSIM simulation using a GP surrogate.

        Each evaluation: set variable values → save & solve → read objective.
        Uses Expected Improvement acquisition with LHS warm-up.

        variables   : list of dicts:
                        tag           – stream or unit op tag
                        property      – property name (e.g. 'MassFlow')
                        unit          – unit string (e.g. 'kg/h')
                        lower / upper – search bounds (floats)
        observe_tag         : tag of stream/unit op to observe
        observe_property    : property to observe as objective
        minimize            : True = minimise objective; False = maximise
        n_initial           : LHS warm-up evaluations (default 5)
        max_iter            : BO iterations after warm-up (default 20)
        xi                  : EI exploration bonus (default 0.01)
        seed                : reproducibility seed (default 42)
        save_plot           : filepath for PNG convergence plot; '' = skip
        """
        from bayesian_optimizer import BayesianOptimizer

        if not variables:
            return {"success": False, "error": "variables list is empty"}
        if not observe_tag or not observe_property:
            return {"success": False, "error": "observe_tag and observe_property required"}

        # Build bounds dict {name: (lo, hi)}
        bounds: Dict[str, tuple] = {}
        var_meta: List[Dict] = []
        for v in variables:
            tag  = str(v.get("tag", ""))
            prop = str(v.get("property", ""))
            unit = str(v.get("unit", ""))
            lo   = float(v.get("lower", 0.0))
            hi   = float(v.get("upper", 1.0))
            name = f"{tag}.{prop}"
            bounds[name] = (lo, hi)
            var_meta.append({"tag": tag, "property": prop, "unit": unit,
                              "name": name, "lower": lo, "upper": hi})

        eval_count = [0]
        progress_log: List[Dict] = []

        def objective(params: Dict[str, float]):
            """Single evaluation: set params → solve → read objective."""
            eval_count[0] += 1
            # Apply variable values
            for vm in var_meta:
                val = params[vm["name"]]
                r   = self.set_stream_property(vm["tag"], vm["property"], val, vm["unit"])
                if not r.get("success"):
                    # Try unit op property if stream fails
                    self.set_unit_op_property(vm["tag"], vm["property"], val)

            # Save and solve
            solve_r = self.save_and_solve()
            if not solve_r.get("success") or not solve_r.get("converged"):
                return None  # failed — BO penalises this region

            # Read objective
            try:
                obj_val = None
                # Try stream first
                sp = self.get_stream_properties(observe_tag)
                if sp.get("success"):
                    obj_val = sp.get("properties", {}).get(observe_property)
                # Fallback: unit op
                if obj_val is None:
                    uo = self.get_unit_op_properties(observe_tag)
                    if uo.get("success"):
                        obj_val = uo.get("properties", {}).get(observe_property)
                if obj_val is None:
                    return None
                return float(obj_val)
            except Exception:
                return None

        def on_progress(it, params, val, best):
            progress_log.append({
                "iteration": it, "value": val, "best_so_far": best,
                "params": {k: round(v, 6) for k, v in params.items()},
            })

        try:
            opt = BayesianOptimizer(
                bounds      = bounds,
                n_initial   = n_initial,
                max_iter    = max_iter,
                minimize    = minimize,
                xi          = xi,
                seed        = seed,
                save_plot   = save_plot,
                on_progress = on_progress,
            )
            result = opt.run(objective)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        # Restore best parameters
        for vm in var_meta:
            bval = result.best_params[vm["name"]]
            self.set_stream_property(vm["tag"], vm["property"], bval, vm["unit"])
        self.save_and_solve()

        return {
            "success":          True,
            "best_params":      {vm["tag"]+"."+vm["property"]: result.best_params[vm["name"]]
                                  for vm in var_meta},
            "best_value":       round(result.best_value, 6),
            "observe":          f"{observe_tag}.{observe_property}",
            "minimize":         minimize,
            "n_evals":          result.n_evals,
            "n_initial":        result.n_initial,
            "max_iter":         max_iter,
            "converged":        result.converged,
            "duration_s":       result.duration_s,
            "convergence_plot": result.convergence_plot,
            "history":          result.history[-30:],
            "variables":        [
                {"name": vm["name"], "lower": vm["lower"], "upper": vm["upper"],
                 "best": result.best_params[vm["name"]]}
                for vm in var_meta
            ],
        }

    def monte_carlo_study(
        self,
        vary_params:      List[Dict],   # [{tag, property, unit, distribution, param1, param2}, ...]
        observe_tag:      str,
        observe_property: str,
        n_samples:        int = 100,
        on_progress:      Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Monte Carlo uncertainty propagation.

        vary_params: list of {
          tag, property, unit,
          distribution: "normal" | "uniform" | "triangular",
          mean (normal), std (normal),
          low (uniform/triangular), high (uniform/triangular), mode (triangular)
        }

        Returns: {mean, std, p5, p25, p50, p75, p95, samples, histogram_bins}

        Note: DWSIM bridge is single-threaded — n_samples runs sequentially.
        For n_samples=100 and a 3s solve, expect ~5 minutes. Use n_samples=30
        for quick results, n_samples=200+ for journal-quality CIs.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not self._flowsheet_path:
            return {"success": False, "error": "No flowsheet path for reload"}
        if not vary_params:
            return {"success": False, "error": "vary_params list is empty"}

        import random as _random
        import math   as _math

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        samples_out: List[float] = []
        samples_in:  List[Dict]  = []
        errors = []

        # Generate all sample inputs first (deterministic draw order for seed reproducibility)
        _random.seed(42)

        def _draw(vp: Dict) -> float:
            dist = vp.get("distribution", "normal").lower()
            if dist == "normal":
                return _random.gauss(float(vp.get("mean", 0)), float(vp.get("std", 1)))
            elif dist == "uniform":
                return _random.uniform(float(vp.get("low", 0)), float(vp.get("high", 1)))
            elif dist == "triangular":
                return _random.triangular(
                    float(vp.get("low", 0)),
                    float(vp.get("high", 1)),
                    float(vp.get("mode", 0.5)),
                )
            return float(vp.get("mean", vp.get("low", 0)))

        input_matrix = [[_draw(vp) for vp in vary_params] for _ in range(n_samples)]

        for i, inputs in enumerate(input_matrix, start=1):
            if callable(on_progress):
                try:
                    on_progress(i, n_samples, inputs, None)
                except Exception:
                    pass

            load_r = self.load_flowsheet(base_path, alias=base_alias)
            if not load_r["success"]:
                errors.append(f"Run {i}: reload failed")
                continue

            # Apply drawn values
            ok = True
            drawn = {}
            for vp, xi in zip(vary_params, inputs):
                drawn[f"{vp['tag']}.{vp['property']}"] = round(xi, 6)
                r = self.set_stream_property(vp["tag"], vp["property"], xi, vp.get("unit", ""))
                if not r["success"]:
                    r = self.set_unit_op_property(vp["tag"], vp["property"], xi)
                if not r["success"]:
                    errors.append(f"Run {i}: set {vp['property']}={xi} failed")
                    ok = False
                    break
            if not ok:
                continue

            run_r = self.run_simulation()
            if not run_r["success"]:
                errors.append(f"Run {i}: solve failed")
                continue

            obs_r = self.get_stream_properties(observe_tag)
            if not obs_r["success"]:
                continue

            val = obs_r["properties"].get(observe_property)
            if val is None:
                continue

            samples_out.append(float(val))
            samples_in.append(drawn)

            if callable(on_progress):
                try:
                    on_progress(i, n_samples, inputs, float(val))
                except Exception:
                    pass

        if not samples_out:
            return {"success": False, "error": "No successful samples",
                    "sample_errors": errors[:10]}

        s = sorted(samples_out)
        n = len(s)
        mean = sum(s) / n
        std  = _math.sqrt(sum((x - mean) ** 2 for x in s) / max(n - 1, 1))

        def _pct(p: float) -> float:
            idx = (p / 100) * (n - 1)
            lo  = int(idx)
            hi  = min(lo + 1, n - 1)
            return s[lo] + (idx - lo) * (s[hi] - s[lo])

        # Histogram (10 bins)
        vmin, vmax = s[0], s[-1]
        bin_w = (vmax - vmin) / 10 if vmax > vmin else 1.0
        hist_bins = []
        for b in range(10):
            lo = vmin + b * bin_w
            hi = lo + bin_w
            count = sum(1 for x in s if lo <= x < hi)
            hist_bins.append({"lo": round(lo, 4), "hi": round(hi, 4), "count": count})

        return {
            "success":         True,
            "n_successful":    n,
            "n_requested":     n_samples,
            "observe_tag":     observe_tag,
            "observe_property": observe_property,
            "mean":     round(mean, 6),
            "std":      round(std,  6),
            "cv_pct":   round(std / abs(mean) * 100, 2) if mean != 0 else None,
            "p5":       round(_pct(5),  6),
            "p25":      round(_pct(25), 6),
            "p50":      round(_pct(50), 6),
            "p75":      round(_pct(75), 6),
            "p95":      round(_pct(95), 6),
            "min":      round(s[0],   6),
            "max":      round(s[-1],  6),
            "samples":  samples_out[:200],   # cap for API response size
            "inputs":   samples_in[:200],
            "histogram": hist_bins,
            "errors":   errors[:20] or None,
            "ci_95":    [round(_pct(2.5), 6), round(_pct(97.5), 6)],
        }

    # ── parametric study (IMP-4) ──────────────────────────────────────────────

    def parametric_study(
        self,
        vary_tag:         str,
        vary_property:    str,
        vary_unit:        str,
        values:           List[float],
        observe_tag:      str,
        observe_property: str,
        on_progress:      Optional[Any] = None,   # callable(i, n, v, obs_val)
    ) -> Dict[str, Any]:
        """
        Run a one-at-a-time parametric study.

        Note: DWSIM's .NET bridge is NOT thread-safe (COM single-apartment model),
        so simulations run sequentially. on_progress(i, n, v, obs_val) is called
        after each point so callers can stream live progress to the UI.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not self._flowsheet_path:
            return {"success": False,
                    "error": "No flowsheet path — cannot reload for parametric study"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        n_total    = len(values)
        table  = []
        errors = []

        for i, v in enumerate(values, start=1):
            # --- progress callback (enables SSE streaming per data point) ---
            if callable(on_progress):
                try:
                    on_progress(i, n_total, v, None)
                except Exception:
                    pass

            load_r = self.load_flowsheet(base_path, alias=base_alias)
            if not load_r["success"]:
                errors.append(f"Reload failed at {vary_property}={v}: {load_r['error']}")
                continue

            set_r = self.set_stream_property(vary_tag, vary_property, v, vary_unit)
            if not set_r["success"]:
                set_r = self.set_unit_op_property(vary_tag, vary_property, v)
            if not set_r["success"]:
                errors.append(f"Set failed at {vary_property}={v}: {set_r['error']}")
                continue

            run_r = self.run_simulation()
            if not run_r["success"]:
                errors.append(f"Solve failed at {vary_property}={v}: {run_r['error']}")
                continue

            obs_r = self.get_stream_properties(observe_tag)
            if not obs_r["success"]:
                errors.append(f"Read failed for '{observe_tag}' at {v}")
                continue

            obs_val = obs_r["properties"].get(observe_property)
            table.append({
                f"{vary_tag}.{vary_property} [{vary_unit}]": v,
                f"{observe_tag}.{observe_property}": obs_val,
            })

            # --- progress callback with result ---
            if callable(on_progress):
                try:
                    on_progress(i, n_total, v, obs_val)
                except Exception:
                    pass

        return {
            "success":          True,
            "vary_tag":         vary_tag,
            "vary_property":    vary_property,
            "observe_tag":      observe_tag,
            "observe_property": observe_property,
            "points":           len(table),
            "table":            table,
            "results":          [{"input": list(row.values())[0],
                                  "observed": list(row.values())[1]}
                                 for row in table],
            "errors":           errors or None,
        }

    # ── Bayesian Optimisation ─────────────────────────────────────────────────

    def bayesian_optimize(
        self,
        variables:        List[Dict],   # [{tag, property, unit, lower_bound, upper_bound}]
        observe_tag:      str,
        observe_property: str,
        minimize:         bool  = True,
        n_initial:        int   = 5,    # Latin-Hypercube exploration evaluations
        max_iter:         int   = 20,   # GP-BO exploitation iterations
        tolerance:        float = 1e-4, # early-stop tolerance
        seed:             int   = 42,
        on_progress:      Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Bayesian Optimisation of DWSIM operating conditions.

        Uses a Gaussian Process surrogate with Expected Improvement acquisition
        to find optimal operating conditions in n_initial + max_iter simulations
        (typically 10–25 total) vs 100+ for differential_evolution.

        Best for:
          • 1–5 decision variables
          • Expensive simulations (each DWSIM call >10 s)
          • Smooth objective landscapes

        Parameters
        ──────────
        variables        : list of dicts, each with keys:
                           tag, property, unit, lower_bound, upper_bound
        observe_tag      : tag of the stream whose property to optimise
        observe_property : property key, e.g. 'temperature_C', 'molar_flow_mols'
        minimize         : True to minimise (default), False to maximise
        n_initial        : Latin-Hypercube initial evaluations (exploration)
        max_iter         : BO iterations (exploitation). Total = n_initial + max_iter.
        tolerance        : early-stop if improvement < tolerance for 5 consecutive iters
        seed             : random seed for reproducibility
        on_progress      : optional callback(iter, total, x_vals, y_val, best_y)

        Returns
        ───────
        Dict with keys:
          success, optimal_inputs, optimal_output, minimize,
          converged, n_evaluations, surrogate_r2, convergence_curve,
          history, message
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        if not self._flowsheet_path:
            return {"success": False, "error": "No flowsheet path — required for reload"}
        if not variables:
            return {"success": False, "error": "variables list is empty"}
        if len(variables) > 10:
            return {"success": False,
                    "error": "Too many variables (max 10). Use optimize_multivar for >10."}

        try:
            from bayesian_optimizer import BayesianOptimizer  # local module
        except ImportError:
            return {"success": False,
                    "error": "bayesian_optimizer.py not found in backend directory"}

        base_path  = self._flowsheet_path
        base_alias = self._active_alias
        call_count = [0]

        # ── objective function ────────────────────────────────────────────────
        def objective(x_actual):
            call_count[0] += 1
            # Reload flowsheet fresh for each evaluation
            load_r = self.load_flowsheet(base_path, alias=base_alias)
            if not load_r.get("success"):
                return float("inf")

            # Apply decision variable values
            for xi, var in zip(x_actual, variables):
                tag  = var["tag"]
                prop = var["property"]
                unit = var.get("unit", "")

                set_r = self.set_stream_property(tag, prop, float(xi), unit)
                if not set_r.get("success"):
                    set_r = self.set_unit_op_property(tag, prop, float(xi))
                if not set_r.get("success"):
                    return float("inf")

            # Solve
            run_r = self.run_simulation()
            if not run_r.get("success"):
                return float("inf")

            # Read objective
            obs_r = self.get_stream_properties(observe_tag)
            if not obs_r.get("success"):
                # Try unit op fallback
                obs_r = self.get_object_properties(observe_tag)
            if not obs_r.get("success"):
                return float("inf")

            # Search in both properties and nested dicts
            props = obs_r.get("properties") or obs_r
            val   = props.get(observe_property)
            if val is None:
                # Try dot-notation fallback
                for k, v in props.items():
                    if isinstance(v, dict):
                        val = v.get(observe_property)
                        if val is not None:
                            break
            if val is None:
                return float("inf")

            return float(val)

        # ── progress wrapper ─────────────────────────────────────────────────
        total_evals = n_initial + max_iter

        def _progress(it, total, x_vals, y_val, best_y):
            tag_vals = [
                {"tag": v["tag"], "property": v["property"],
                 "unit": v.get("unit", ""), "value": round(xv, 6)}
                for v, xv in zip(variables, x_vals)
            ]
            status = "INIT" if it <= n_initial else "BO"
            print(f"  [BO {status} {it:3d}/{total}] "
                  f"y={y_val:.6g}  best={best_y:.6g}  "
                  f"vars={[round(xv, 4) for xv in x_vals]}")
            if callable(on_progress):
                try:
                    on_progress(it, total, tag_vals, y_val, best_y)
                except Exception:
                    pass

        # ── run BO ───────────────────────────────────────────────────────────
        bounds = [(float(v["lower_bound"]), float(v["upper_bound"])) for v in variables]

        opt = BayesianOptimizer(
            bounds      = bounds,
            n_initial   = n_initial,
            max_iter    = max_iter,
            minimize    = minimize,
            tolerance   = tolerance,
            seed        = seed,
            on_progress = _progress,
        )

        try:
            result = opt.optimize(objective)
        except Exception as exc:
            return {"success": False, "error": f"BO failed: {exc}"}

        # ── format output (matches optimize_multivar interface) ───────────────
        optimal_vars = [
            {
                "tag":      v["tag"],
                "property": v["property"],
                "unit":     v.get("unit", ""),
                "optimal":  round(float(xi), 6),
                "lower":    v["lower_bound"],
                "upper":    v["upper_bound"],
            }
            for v, xi in zip(variables, result.best_x)
        ]

        return {
            "success":          True,
            "optimal_inputs":   optimal_vars,
            "optimal_output":   {
                "tag":      observe_tag,
                "property": observe_property,
                "value":    round(result.best_y, 6),
            },
            "minimize":         minimize,
            "converged":        result.converged,
            "n_evaluations":    result.n_evaluations,
            "surrogate_r2":     result.surrogate_r2,
            "convergence_curve": [round(v, 6) for v in result.convergence],
            "history":          [
                {"x": [round(xi, 6) for xi in hx], "y": round(hy, 6)}
                for hx, hy in zip(result.history_x, result.history_y)
            ][-30:],   # last 30 for API response size
            "message": (
                f"Bayesian optimisation {'converged' if result.converged else 'completed'} "
                f"in {result.n_evaluations} evaluations "
                f"(GP surrogate R²={result.surrogate_r2}). "
                f"{'Min' if minimize else 'Max'} {observe_property}="
                f"{round(result.best_y, 4)}."
            ),
        }

    # ── v3: distillation column ───────────────────────────────────────────────


    def get_column_properties(self, tag: str) -> Dict[str, Any]:
        """Read distillation/absorption column properties."""
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Column '{tag}' not found"}
        info: Dict[str, Any] = {}
        # Numeric properties
        for attr, label in [
            ("NumberOfStages",  "stages"),
            ("RefluxRatio",     "reflux_ratio"),
            ("ReboilerDuty",    "reboiler_duty_W"),
            ("CondenserDuty",   "condenser_duty_W"),
            ("BottomsFlowRate", "bottoms_flow_mol_s"),
            ("DistillateFlowRate", "distillate_flow_mol_s"),
            ("CondenserPressure",  "condenser_pressure_Pa"),
            ("ReboilerPressure",   "reboiler_pressure_Pa"),
            ("FeedStage",       "feed_stage"),
        ]:
            for tp in obj.GetType().GetProperties():
                if tp.Name == attr:
                    try:
                        v = _unwrap_nullable(tp.GetValue(obj))
                        if v is not None:
                            info[label] = round(float(v), 6)
                    except Exception:
                        pass
                    break
        # Column type / spec
        for attr in ("ColumnType", "CondenserType", "OperationMode"):
            try:
                p = obj.GetType().GetProperty(attr)
                if p:
                    info[attr.lower()] = str(p.GetValue(obj))
            except Exception:
                pass
        return {"success": True, "tag": tag, "column_properties": info}

    def set_column_property(self, tag: str, property_name: str,
                            value: Any) -> Dict[str, Any]:
        """Set a distillation column property (reflux ratio, stages, duties, etc.)."""
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Column '{tag}' not found"}
        key = property_name.lower().replace("_", "").replace(" ", "")
        for tp in obj.GetType().GetProperties():
            if tp.Name.lower().replace("_", "") == key and tp.CanWrite:
                if _reflect_set_typed(tp, obj, value):
                    return {"success": True,
                            "message": f"Set {tp.Name}={value} on '{tag}'"}
                return {"success": False,
                        "error": f"Could not coerce '{value}' for {tp.Name}"}
        return {"success": False,
                "error": f"Property '{property_name}' not found on '{tag}'"}

    # ── v3: reactors ─────────────────────────────────────────────────────────

    def get_reactor_properties(self, tag: str) -> Dict[str, Any]:
        """Read reactor properties for all DWSIM reactor types."""
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Reactor '{tag}' not found"}
        typename = ""
        try: typename = obj.GetType().Name.lower()
        except Exception: pass

        info: Dict[str, Any] = {"reactor_type": typename}

        # Universal properties
        for attr, label in [
            ("Pressure",          "pressure_Pa"),
            ("DeltaP",            "delta_pressure_Pa"),
            ("OutletTemperature", "outlet_temperature_K"),
            ("Volume",            "volume_m3"),
            ("Length",            "length_m"),
            ("Diameter",          "diameter_m"),
        ]:
            for tp in obj.GetType().GetProperties():
                if tp.Name == attr:
                    try:
                        v = _unwrap_nullable(tp.GetValue(obj))
                        if v is not None and v != 0.0:
                            info[label] = round(float(v), 6)
                    except Exception:
                        pass
                    break

        # Conversion reactor: per-reaction conversions
        if "conversion" in typename:
            try:
                rc_p = obj.GetType().GetProperty("Reactions")
                if rc_p:
                    reactions = rc_p.GetValue(obj)
                    conv_list = []
                    for r in reactions:
                        try:
                            conv_p = r.GetType().GetProperty("Conversion")
                            conv = _unwrap_nullable(conv_p.GetValue(r)) if conv_p else None
                            name_p = r.GetType().GetProperty("Name")
                            name = str(name_p.GetValue(r)) if name_p else "?"
                            conv_list.append({"reaction": name, "conversion": conv})
                        except Exception:
                            pass
                    if conv_list:
                        info["reactions"] = conv_list
            except Exception:
                pass

        # CSTR: residence time
        if "cstr" in typename:
            for attr in ("ResidenceTime", "TauN"):
                for tp in obj.GetType().GetProperties():
                    if tp.Name.lower() == attr.lower():
                        try:
                            v = _unwrap_nullable(tp.GetValue(obj))
                            if v is not None:
                                info["residence_time_s"] = round(float(v), 4)
                        except Exception:
                            pass

        # Equilibrium: equilibrium temperature
        if "equilibrium" in typename or "gibbs" in typename:
            for attr in ("Temperature", "EquilibriumTemperature"):
                for tp in obj.GetType().GetProperties():
                    if tp.Name.lower() in (attr.lower(), "equilibriumtemperature"):
                        try:
                            v = _unwrap_nullable(tp.GetValue(obj))
                            if v is not None and v > 0:
                                info["equilibrium_temperature_K"] = round(float(v), 3)
                        except Exception:
                            pass

        return {"success": True, "tag": tag, "reactor_properties": info}

    def set_reactor_property(self, tag: str, property_name: str,
                             value: Any) -> Dict[str, Any]:
        """Set a reactor property (conversion, volume, temperature, etc.)."""
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Reactor '{tag}' not found"}
        key = property_name.lower().replace("_", "").replace(" ", "")
        for tp in obj.GetType().GetProperties():
            if tp.Name.lower().replace("_", "") == key and tp.CanWrite:
                if _reflect_set_typed(tp, obj, value):
                    return {"success": True,
                            "message": f"Set {tp.Name}={value} on '{tag}'"}
                return {"success": False,
                        "error": f"Could not coerce '{value}' for {tp.Name}"}
        return {"success": False,
                "error": f"Property '{property_name}' not found on '{tag}'"}

    # ── v3: dynamic detection ─────────────────────────────────────────────────

    def detect_simulation_mode(self) -> Dict[str, Any]:
        """Detect if the loaded flowsheet is steady-state or dynamic."""
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        is_dynamic = False
        indicators = []

        # Check IsDynamicFlowsheet flag
        for attr in ("IsDynamicFlowsheet", "DynamicMode", "IsDynamic"):
            try:
                p = self._flowsheet.GetType().GetProperty(attr)
                if p:
                    v = p.GetValue(self._flowsheet)
                    if v is True or str(v).lower() == "true":
                        is_dynamic = True
                        indicators.append(attr)
            except Exception:
                pass

        # Check for PID controllers, tanks in object collection
        if not is_dynamic:
            coll = self._get_collection()
            if coll:
                for guid, obj in self._iter_collection(coll):
                    try:
                        tn = obj.GetType().Name.lower()
                        if any(x in tn for x in ("pidcontroller", "tank",
                                                   "dynamicpipe", "controlvalve")):
                            is_dynamic = True
                            indicators.append(f"object:{obj.GetType().Name}")
                            break
                    except Exception:
                        pass

        return {
            "success":    True,
            "is_dynamic": is_dynamic,
            "mode":       "Dynamic" if is_dynamic else "Steady-State",
            "indicators": indicators,
            "note":       ("This is a dynamic flowsheet. Static analysis of "
                           "streams and unit operations is available, but "
                           "time-domain simulation requires the DWSIM GUI.")
                          if is_dynamic else
                          "Steady-state flowsheet — full automation supported.",
        }

    # ── v3: plugin/custom unit op detection ──────────────────────────────────

    def get_plugin_info(self, tag: str) -> Dict[str, Any]:
        """
        Identify and describe plugin/custom unit operations
        (Cantera, Reaktoro, Excel UO, Script UO, FOSSEE custom ops).
        Returns what properties are accessible and what requires external engines.
        """
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        typename = ""
        fullname = ""
        try:
            typename = obj.GetType().Name.lower()
            fullname = obj.GetType().FullName.lower()
        except Exception:
            pass

        plugin_type = "unknown"
        accessible  = []
        requires    = []

        if "cantera" in fullname or "cantera" in typename:
            plugin_type = "Cantera"
            requires    = ["Cantera engine (not available via Python automation)"]
            accessible  = ["inlet/outlet stream properties"]

        elif "reaktoro" in fullname or "reaktoro" in typename:
            plugin_type = "Reaktoro"
            requires    = ["Reaktoro library"]
            accessible  = ["inlet/outlet stream properties"]

        elif "excel" in typename or "exceluo" in typename:
            plugin_type = "Excel UO"
            requires    = ["Microsoft Excel installation and COM connection"]
            accessible  = ["unit op parameters via get_object_properties"]

        elif "script" in typename or "pythonuo" in typename or "ironpython" in typename:
            plugin_type = "Script UO"
            requires    = ["Script source code is embedded in flowsheet"]
            accessible  = ["inlet/outlet stream properties", "script parameters"]

        elif "nested" in typename or "subflowsheet" in typename:
            plugin_type = "Nested Flowsheet"
            requires    = ["sub-flowsheet file is accessible"]
            accessible  = ["inlet/outlet stream properties"]

        else:
            # Generic custom/FOSSEE UO — read all numeric properties via reflection
            plugin_type = "Custom UO"
            props = {}
            try:
                for tp in obj.GetType().GetProperties():
                    if tp.Name.startswith("_"): continue
                    try:
                        v = _unwrap_nullable(tp.GetValue(obj))
                        if v is not None and v != 0.0:
                            props[tp.Name] = round(float(v), 6)
                    except Exception:
                        pass
            except Exception:
                pass
            accessible = list(props.keys())[:30]
            return {
                "success":     True,
                "tag":         tag,
                "plugin_type": plugin_type,
                "readable_properties": props,
                "note": "Custom UO — all numeric properties read via reflection",
            }

        return {
            "success":     True,
            "tag":         tag,
            "plugin_type": plugin_type,
            "accessible":  accessible,
            "requires":    requires,
            "note":        f"{plugin_type} detected. External engine required for calculation.",
        }

    # ── autonomous flowsheet generation ──────────────────────────────────────

    def get_available_compounds(self, search: str = "") -> Dict[str, Any]:
        """Return compounds from DWSIM database, optionally filtered by search string."""
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r
        try:
            all_comps = [c.Key for c in self._mgr.AvailableCompounds]
            if search:
                sl = search.lower()
                filtered = [c for c in all_comps if sl in c.lower()]
            else:
                filtered = all_comps
            return {
                "success": True,
                "count":   len(filtered),
                "compounds": filtered[:200],   # cap at 200 to avoid huge responses
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def initialize_recycle(
        self,
        recycle_tag:    str,
        T_guess_C:      float,
        P_guess_bar:    float,
        composition:    Dict[str, float],
        solver:         str = "Wegstein",
    ) -> Dict[str, Any]:
        """
        Seed a recycle stream with initial guess values to help the DWSIM solver
        converge. Recycle loops are the #1 convergence failure cause.

        Steps:
          1. Set temperature, pressure, and composition on the recycle stream
          2. Switch the recycle stream's flash spec to TP (T-P flash)
          3. Set convergence algorithm to Wegstein or Broyden on the recycle block
          4. Run simulation once to test convergence

        Args:
            recycle_tag:  tag of the recycle stream (MaterialStream)
            T_guess_C:    initial temperature guess in °C
            P_guess_bar:  initial pressure guess in bar
            composition:  mole fraction dict {compound: fraction}
            solver:       "Wegstein" (default) or "Broyden"
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        steps_done = []
        try:
            # Step 1: Set T, P on recycle stream
            T_K = T_guess_C + 273.15
            P_Pa = P_guess_bar * 1e5
            r = self.set_stream_property(recycle_tag, "temperature", T_K, "K")
            if r["success"]:
                steps_done.append(f"Set T={T_guess_C}°C on '{recycle_tag}'")
            r = self.set_stream_property(recycle_tag, "pressure", P_Pa, "Pa")
            if r["success"]:
                steps_done.append(f"Set P={P_guess_bar} bar on '{recycle_tag}'")

            # Step 2: Set composition
            if composition:
                r = self.set_stream_composition(recycle_tag, composition)
                if r.get("success"):
                    steps_done.append(f"Set composition on '{recycle_tag}'")

            # Step 3: Switch flash spec to TP
            try:
                r = self.set_stream_flash_spec(recycle_tag, "TP")
                if r.get("success"):
                    steps_done.append("Flash spec → TP")
            except Exception:
                pass

            # Step 4: Set convergence solver on OT_Recycle block if present
            try:
                obj = self._find_object(recycle_tag)
                if obj is not None:
                    # Try to set solver type via reflection
                    for solver_attr in ("ConvergenceMethod", "SolverType", "Method"):
                        p = obj.GetType().GetProperty(solver_attr)
                        if p and p.CanWrite:
                            try:
                                import System  # type: ignore
                                # Try setting as enum string
                                enum_type = p.PropertyType
                                val = System.Enum.Parse(enum_type, solver, True)
                                p.SetValue(obj, val)
                                steps_done.append(f"Solver → {solver}")
                                break
                            except Exception:
                                try:
                                    p.SetValue(obj, solver)
                                    steps_done.append(f"Solver → {solver}")
                                    break
                                except Exception:
                                    pass
            except Exception:
                pass

            return {
                "success":    True,
                "message":    (
                    f"Recycle stream '{recycle_tag}' seeded with initial guess. "
                    f"Steps: {'; '.join(steps_done)}. "
                    "Call save_and_solve to attempt convergence. "
                    "If it fails, try adjusting T/P guess closer to expected outlet conditions."
                ),
                "steps_done": steps_done,
                "recycle_tag": recycle_tag,
                "T_guess_C":   T_guess_C,
                "P_guess_bar": P_guess_bar,
            }
        except Exception as e:
            return {"success": False, "error": str(e), "steps_done": steps_done}

    def get_compound_properties(self, name: str) -> Dict[str, Any]:
        """
        Return critical thermodynamic constants for a single compound from the
        DWSIM database: Tc, Pc, ω (acentric factor), Tb, MW, ΔHf°, ΔGf°.
        Used for model selection sanity checks and journal reporting.
        """
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r
        try:
            # DWSIM AvailableCompounds is a dict-like collection keyed by name
            cdb = self._mgr.AvailableCompounds
            comp = None
            name_lo = name.lower().strip()
            # Exact match first, then case-insensitive
            for entry in cdb:
                if entry.Key.lower() == name_lo:
                    comp = entry.Value
                    break
            if comp is None:
                # Partial match fallback
                for entry in cdb:
                    if name_lo in entry.Key.lower():
                        comp = entry.Value
                        break
            if comp is None:
                return {"success": False,
                        "error": f"Compound '{name}' not found in DWSIM database. "
                                 "Use get_available_compounds to search."}

            def _safe(attr: str, default=None):
                try:
                    v = getattr(comp, attr, default)
                    if v is None:
                        return default
                    return float(v) if isinstance(v, (int, float)) else str(v)
                except Exception:
                    return default

            props = {
                "name":               _safe("Name") or name,
                "formula":            _safe("Formula"),
                "molar_weight_kg_kmol": _safe("Molar_Weight"),
                "Tc_K":               _safe("Critical_Temperature"),
                "Pc_Pa":              _safe("Critical_Pressure"),
                "Pc_bar":             round(_safe("Critical_Pressure", 0) / 1e5, 2)
                                      if _safe("Critical_Pressure") else None,
                "Vc_m3_kmol":         _safe("Critical_Volume"),
                "acentric_factor":    _safe("Acentric_Factor"),
                "Tb_K":               _safe("Normal_Boiling_Point"),
                "Tb_C":               round(_safe("Normal_Boiling_Point", 273.15) - 273.15, 2)
                                      if _safe("Normal_Boiling_Point") else None,
                "Tf_K":               _safe("TemperatureOfFusion"),
                "Hf_kJ_mol":          _safe("IG_Enthalpy_of_Formation_25C"),   # kJ/mol
                "Gf_kJ_mol":          _safe("IG_Gibbs_Energy_of_Formation_25C"),
                "dipole_moment_debye": _safe("Dipole_Moment"),
                "CAS_number":         _safe("CAS_Number"),
                "phase_at_STP":       _safe("Phase"),
            }
            # Remove None values for cleaner output
            props = {k: v for k, v in props.items() if v is not None}
            return {"success": True, "compound": props}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── AI step-by-step flowsheet building primitives ────────────────────────

    def new_flowsheet(self, name: str, compounds: List[str],
                      property_package: str,
                      save_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a blank DWSIM flowsheet with compounds and a property package.
        Call this first when building a flowsheet step-by-step with the AI.
        After this, use add_object / connect_streams / set_stream_property /
        set_unit_op_property / run_simulation to complete the flowsheet.
        """
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r

        from flowsheet_builder import _fuzzy_match_compound, _fuzzy_match_pp
        import difflib, re, System  # type: ignore
        _sink = io.StringIO()

        # ── Idempotency guard ─────────────────────────────────────────────────
        # If new_flowsheet was called but save_and_solve hasn't run yet, the
        # flowsheet is still being built — DON'T purge + recreate, just return
        # "already initialized" so the LLM stops calling new_flowsheet in a loop.
        # A flowsheet that has already been solved (_building=False) is NOT
        # guarded so the user can freely ask for a new one in the next turn.
        if self._building and self._active_alias and self._active_alias in self._flowsheets:
            existing = self._flowsheets[self._active_alias]
            n_streams = len(self.state.streams)
            n_ops     = len(self.state.unit_ops)
            return {
                "success": True,
                "message": (
                    f"Flowsheet '{self.state.name}' is already initialized "
                    f"({n_streams} stream(s), {n_ops} unit-op(s) added so far). "
                    "Do NOT call new_flowsheet again — it is ready. "
                    "Next step: call add_object for each stream and unit op, "
                    "then connect_streams, then set_stream_property, "
                    "then set_unit_op_property, then save_and_solve."
                ),
                "compounds_added":  [],
                "property_package": self.state.property_package,
                "save_path":        existing.get("path", ""),
                "skipped":          [],
                "_already_initialized": True,
            }

        # Purge stale flowsheets so the DWSIM manager starts clean
        self._purge_stale_flowsheets()

        # 1. Create blank flowsheet
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                fs = self._mgr.CreateFlowsheet()
        except Exception as e:
            return {"success": False, "error": f"CreateFlowsheet failed: {e}"}

        # 2. Add compounds
        avail_comps = [c.Key for c in self._mgr.AvailableCompounds]
        avail_lower = {c.lower(): c for c in avail_comps}
        added, skipped = [], []
        for comp_name in compounds:
            matched = _fuzzy_match_compound(comp_name, avail_comps)
            if matched is None:
                close = difflib.get_close_matches(
                    comp_name.lower(), list(avail_lower.keys()), n=3, cutoff=0.6)
                suggestions = [avail_lower[c] for c in close]
                skipped.append(f"{comp_name!r} not found — did you mean {suggestions}?")
                continue
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    fs.AddCompound(matched)
                added.append(matched)
            except Exception as e:
                skipped.append(f"{comp_name!r}: {e}")

        if not added:
            return {"success": False,
                    "error": "No compounds could be added",
                    "skipped": skipped}

        # 3. Add property package
        avail_pps = [p.Key for p in self._mgr.AvailablePropertyPackages]
        matched_pp = _fuzzy_match_pp(property_package, avail_pps)
        if matched_pp is None:
            matched_pp = next(
                (p for p in avail_pps if "peng-robinson" in p.lower()),
                avail_pps[0] if avail_pps else "")
        try:
            with redirect_stdout(_sink), redirect_stderr(_sink):
                fs.CreateAndAddPropertyPackage(matched_pp)
        except Exception:
            pass  # non-fatal — solver may still work

        # 4. Determine save path
        safe_name = re.sub(r"[^\w\-_]", "_", name or "flowsheet")
        if not save_path:
            docs = os.path.expanduser("~/Documents")
            save_path = os.path.join(docs, f"{safe_name}.dwxmz")

        # 5. Register in bridge (no solve yet — user adds objects next)
        fs_state = FlowsheetState()
        fs_state.name = name or safe_name
        fs_state.path = save_path
        fs_state.property_package = matched_pp

        fs_alias = safe_name
        self._flowsheets[fs_alias] = {
            "fs":        fs,
            "path":      save_path,
            "state":     fs_state,
            "tag_cache": {},
        }
        self._active_alias = fs_alias
        self.state = fs_state
        self._building = True   # mark build-in-progress; cleared by save_and_solve

        return {
            "success":          True,
            "message":          f"Blank flowsheet '{name}' ready — add objects next",
            "compounds_added":  added,
            "property_package": matched_pp,
            "save_path":        save_path,
            "skipped":          skipped,
        }

    def add_object(self, tag: str, type: str) -> Dict[str, Any]:
        """
        Add one stream or unit operation to the currently active flowsheet.
        Call new_flowsheet first, then add_object for each stream and unit op,
        then connect_streams, then set_stream_property / set_unit_op_property,
        then run_simulation.

        type examples: MaterialStream, EnergyStream, Heater, Cooler,
          HeatExchanger, Pump, Compressor, Expander, Valve, Mixer, Splitter,
          Separator, DistillationColumn, AbsorptionColumn, ShortcutColumn,
          CSTR, PFR, GibbsReactor, ConversionReactor, EquilibriumReactor, Pipe
        """
        fs = self._flowsheet
        if fs is None:
            return {"success": False,
                    "error": "No active flowsheet — call new_flowsheet first"}

        from flowsheet_builder import _resolve_type
        import System  # type: ignore
        _sink = io.StringIO()

        enum_name = _resolve_type(type)
        if enum_name is None:
            return {"success": False,
                    "error": f"Unknown object type '{type}'. "
                             f"Valid examples: MaterialStream, EnergyStream, Heater, "
                             f"Pump, HeatExchanger, Separator, DistillationColumn"}

        # Get ObjectType enum via reflection
        obj_type_enum_type = None
        try:
            for mi in fs.GetType().GetMethods():
                if mi.Name == "AddObject":
                    params = list(mi.GetParameters())
                    if len(params) >= 4:
                        obj_type_enum_type = params[0].ParameterType
                        break
        except Exception:
            pass

        obj = None
        if obj_type_enum_type is not None:
            try:
                ev = System.Enum.Parse(obj_type_enum_type, enum_name)
                for args in [
                    (ev, System.Int32(100), System.Int32(100), tag),
                    (ev, System.Int32(100), System.Int32(100)),
                ]:
                    try:
                        with redirect_stdout(_sink), redirect_stderr(_sink):
                            obj = fs.AddObject(*args)
                        if obj is not None:
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        if obj is None:
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    obj = fs.AddFlowsheetObject(enum_name, tag)
            except Exception as e:
                return {"success": False,
                        "error": f"DWSIM AddObject({enum_name}, {tag!r}) failed: {e}"}

        if obj is None:
            return {"success": False,
                    "error": f"AddObject returned None for type '{type}'"}

        # Set display tag
        try:
            obj.GraphicObject.Tag = tag
        except Exception:
            pass

        # Update bridge tag cache
        entry = self._flowsheets.get(self._active_alias, {})
        entry.setdefault("tag_cache", {})
        try:
            guid = str(obj.GraphicObject.Name)
            entry["tag_cache"][guid] = tag
        except Exception:
            pass

        # Update state
        try:
            typename = obj.GetType().Name
        except Exception:
            typename = enum_name
        category = _categorise(typename)
        if category == "stream":
            if tag not in self.state.streams:
                self.state.streams.append(tag)
        else:
            if tag not in self.state.unit_ops:
                self.state.unit_ops.append(tag)

        n_streams = len(self.state.streams)
        n_ops     = len(self.state.unit_ops)
        return {
            "success":       True,
            "tag":           tag,
            "type":          enum_name,
            "category":      category,
            "staged_streams": n_streams,
            "staged_unit_ops": n_ops,
            "message": (
                f"Added {enum_name} '{tag}'. "
                f"Staged so far: {n_streams} stream(s), {n_ops} unit op(s). "
                f"Do NOT call list_simulation_objects to verify — "
                f"objects are invisible to it until save_and_solve is called."
            ),
        }

    # ── Pre-solve SF checker ─────────────────────────────────────────────────

    def _pre_solve_sf_check(self) -> list:
        """
        Catch SF-02, SF-06, SF-07 BEFORE the solver runs, turning all three
        from silent/post-solve failures into loud pre-solve errors.

        SF-02: unit op with no material inlet (reversed port connection)
        SF-06: DeltaP > feed pressure → negative outlet P
        SF-07: Heater OutletT < feed T → physically impossible

        Returns a list of dicts (empty = no violations found).
        """
        violations = []
        if not self._flowsheet:
            return violations

        topology = getattr(self, "_last_topology_connections", [])
        if not topology:
            return violations   # no connection info → skip (validator catches post-solve)

        # ── SF-02: reversed port topology check ──────────────────────────────
        try:
            from safety_validator import SafetyValidator
            _topo_dict = {
                "connections": topology,
                "unit_ops": [
                    {"tag": t, "type": self.state.object_types.get(t, "")}
                    for t in self.state.unit_ops
                ],
            }
            sv = SafetyValidator()
            sf02_viols = sv.pre_solve_sf02_check(
                _topo_dict, self.state.object_types
            )
            violations.extend(sf02_viols)
        except Exception:
            pass  # SF-02 check must never break the solve path

        # Build quick-lookup: tag → object
        def _obj(tag):
            return self._find_object(tag)

        def _get_float_prop(obj, *names):
            """Try multiple attribute names; return float or None."""
            for n in names:
                try:
                    v = getattr(obj, n, None)
                    if v is not None:
                        return float(v)
                except Exception:
                    pass
            return None

        for conn in topology:
            from_tag = conn.get("from") or conn.get("from_tag", "")
            to_tag   = conn.get("to")   or conn.get("to_tag",   "")
            if not from_tag or not to_tag:
                continue

            from_obj = _obj(from_tag)
            to_obj   = _obj(to_tag)
            if from_obj is None or to_obj is None:
                continue

            from_type = self.state.object_types.get(from_tag, "")
            to_type   = self.state.object_types.get(to_tag,   "")

            # ── SF-06: DeltaP > feed pressure ────────────────────────────────
            # Unit op → outlet stream: check DeltaP on unit op vs its inlet P
            for uo_tag, uo_type in [(from_tag, from_type), (to_tag, to_type)]:
                if uo_type.lower() not in ("heater", "cooler", "valve", "pipe"):
                    continue
                uo_obj = _obj(uo_tag)
                if uo_obj is None:
                    continue
                delta_p = _get_float_prop(uo_obj, "DeltaP", "PressureDrop", "Delta_P")
                if delta_p is None or delta_p <= 0:
                    continue
                # Find inlet stream to this unit op
                inlets = [c["from"] for c in topology
                          if c.get("to") == uo_tag or c.get("to_tag") == uo_tag]
                for in_tag in inlets:
                    in_obj = _obj(in_tag)
                    if in_obj is None:
                        continue
                    # Get inlet pressure from Phase[0]
                    feed_p = None
                    try:
                        ph = in_obj.Phases[0].Properties
                        feed_p = float(ph.pressure or 0)
                    except Exception:
                        pass
                    if feed_p and delta_p > feed_p:
                        violations.append({
                            "code": "SF-06",
                            "severity": "LOUD",
                            "description": (
                                f"SF-06 PREVENTED: DeltaP on '{uo_tag}' "
                                f"({delta_p/1e5:.2f} bar) exceeds feed pressure "
                                f"({feed_p/1e5:.2f} bar). "
                                f"Outlet would be {(feed_p-delta_p)/1e5:.2f} bar (negative)."
                            ),
                            "fix": (
                                f"set_unit_op_property('{uo_tag}', 'DeltaP', "
                                f"{max(0, feed_p * 0.1):.0f})  "
                                f"# reduce DeltaP below feed pressure"
                            ),
                        })

            # ── SF-07: Heater OutletT < feed T ───────────────────────────────
            if from_type.lower() in ("heater",) and to_type.lower() not in ("heater",):
                # from_tag is a heater; to_tag is output stream
                uo_obj = from_obj
                out_t = _get_float_prop(uo_obj, "OutletTemperature")
                if out_t is None:
                    continue
                # Find inlet stream to this heater
                inlets = [c["from"] for c in topology
                          if c.get("to") == from_tag or c.get("to_tag") == from_tag]
                for in_tag in inlets:
                    in_obj = _obj(in_tag)
                    if in_obj is None:
                        continue
                    try:
                        feed_t = float(in_obj.Phases[0].Properties.temperature or 0)
                    except Exception:
                        continue
                    if feed_t > 0 and out_t < feed_t:
                        violations.append({
                            "code": "SF-07",
                            "severity": "LOUD",
                            "description": (
                                f"SF-07 PREVENTED: Heater '{from_tag}' "
                                f"OutletTemperature ({out_t:.2f} K) < "
                                f"feed temperature ({feed_t:.2f} K). "
                                f"A heater cannot cool a stream."
                            ),
                            "fix": (
                                f"set_unit_op_property('{from_tag}', 'OutletTemperature', "
                                f"{feed_t + 20:.2f})  # must be > feed T ({feed_t:.2f} K)"
                            ),
                        })
        return violations

    def save_and_solve(self) -> Dict[str, Any]:
        """
        Save the active flowsheet to disk and run the DWSIM solver.
        Call this after all objects are connected and properties are set.
        Returns converged status and stream results.
        """
        fs = self._flowsheet
        if fs is None:
            return {"success": False, "error": "No active flowsheet"}

        _sink = io.StringIO()
        save_path = self._flowsheet_path or os.path.join(
            os.path.expanduser("~/Documents"), "flowsheet.dwxmz")

        # AutoLayout
        for method in ("NaturalLayout", "AutoLayout"):
            fn = getattr(fs, method, None)
            if fn:
                try:
                    with redirect_stdout(_sink), redirect_stderr(_sink):
                        fn()
                    break
                except Exception:
                    pass

        # Save
        saved = False
        _dir = os.path.dirname(save_path)
        if _dir:
            os.makedirs(_dir, exist_ok=True)
        for method in ("SaveFlowsheet", "SaveFlowsheet2"):
            fn = getattr(self._mgr, method, None)
            if fn is None:
                continue
            try:
                with redirect_stdout(_sink), redirect_stderr(_sink):
                    fn(fs, save_path, False)
                saved = True
                break
            except Exception:
                pass

        # ── Pre-solve silent-failure prevention (SF-02, SF-06, SF-07) ───────
        # Run before the expensive solve so we can surface problems loudly
        # instead of returning a convergent-but-wrong result.
        pre_warnings = self._pre_solve_sf_check()
        if pre_warnings:
            return {
                "success": False,
                "error": "Pre-solve validation failed — simulation not run.",
                "pre_solve_failures": pre_warnings,
                "safety_status": "PRE_SOLVE_VIOLATION",
                "hint": (
                    "Fix the listed SF violations BEFORE calling save_and_solve again. "
                    "These failures would have produced a convergent but physically wrong result."
                ),
            }

        # Solve by reloading from disk (ensures bridge state is consistent)
        if saved:
            load_result = self.load_flowsheet(save_path)
            if load_result.get("success"):
                # Build phase is done — clear the flag so a future new_flowsheet
                # call (different user request) creates a fresh flowsheet rather
                # than hitting the "already initialized" idempotency guard.
                self._building = False

                sr = self.get_simulation_results()
                stream_results = sr.get("stream_results", {})

                # ── Safety Validation + SF-05 auto-correction (post-solve) ───
                safety_warnings = []
                sf05_corrections = 0
                try:
                    from safety_validator import SafetyValidator
                    _topology = {
                        "connections": getattr(self, "_last_topology_connections", []),
                        "unit_ops":    [{"tag": t, "type": self.state.object_types.get(t, "")}
                                        for t in self.state.unit_ops],
                    }
                    sv = SafetyValidator()
                    # check_with_duties: SF-05 auto-correction + energy balance
                    # SF-EB01 check using live unit-op duty data
                    _duties: Dict[str, float] = {}
                    _details: Dict[str, Dict] = {}
                    try:
                        _coll = self._get_collection()
                        if _coll is not None:
                            _tc = self._active_tag_cache()
                            for _guid, _obj in self._iter_collection(_coll):
                                try:
                                    if "Stream" in _obj.GetType().Name:
                                        continue
                                    _tag = _tc.get(str(_guid), "")
                                    if not _tag:
                                        continue
                                    _s = _get_unit_op_summary(_obj)
                                    _d = _s.get("duty_kW") or _s.get("heat_duty_kW") or \
                                         _s.get("Duty (W)") or _s.get("DeltaQ (W)")
                                    if _d is not None:
                                        # Convert W→kW if value seems to be in Watts
                                        _dv = float(_d)
                                        if abs(_dv) > 1e6:  # likely Watts not kW
                                            _dv /= 1000.0
                                        _duties[_tag] = _dv
                                    # Collect full summary for SF-08b/c/d
                                    _details[_tag] = {
                                        "type": _obj.GetType().Name,
                                        **{k: v for k, v in _s.items()},
                                    }
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    failures, sf05_corrections = sv.check_with_duties(
                        stream_results, _topology,
                        _duties or None,
                        _details or None,
                    )
                    if failures:
                        safety_warnings = [
                            {"code": f.code, "severity": f.severity,
                             "description": f.description, "evidence": f.evidence,
                             "stream": f.stream_tag,
                             "auto_fixed": f.auto_fixed}
                            for f in failures
                        ]
                except Exception:
                    pass   # validator must never break simulation

                result = {
                    "success":          True,
                    "saved_to":         save_path,
                    "converged":        load_result.get("success", False),
                    "stream_results":   stream_results,
                    "streams":          self.state.streams,
                    "unit_ops":         self.state.unit_ops,
                }
                if safety_warnings:
                    result["safety_warnings"] = safety_warnings
                    result["safety_status"]   = "VIOLATIONS_DETECTED"
                else:
                    result["safety_status"] = "PASSED"
                if sf05_corrections:
                    result["sf05_auto_corrections"] = sf05_corrections
                    result["sf05_note"] = (
                        f"{sf05_corrections} stream(s) had VF corrected from numerical "
                        "noise to exact 0.0 or 1.0 (SF-05 auto-correction). "
                        "Original values stored in stream._sf05_original_vf."
                    )
                return result
            else:
                return {"success": False,
                        "error": f"Save succeeded but reload failed: {load_result.get('error')}",
                        "saved_to": save_path}
        else:
            # Fallback: try to solve in-memory without saving
            for method in ("CalculateFlowsheet2", "CalculateFlowsheet"):
                fn = getattr(self._mgr, method, None)
                if fn:
                    try:
                        with redirect_stdout(_sink), redirect_stderr(_sink):
                            fn(fs)
                        self._building = False   # build phase done
                        sr = self.get_simulation_results()
                        return {
                            "success":        True,
                            "saved_to":       None,
                            "converged":      True,
                            "stream_results": sr.get("stream_results", {}),
                        }
                    except Exception:
                        pass
            return {"success": False, "error": "Save and solve both failed"}

    def get_available_property_packages(self) -> Dict[str, Any]:
        """Return all thermodynamic property packages available in DWSIM."""
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r
        try:
            pps = [p.Key for p in self._mgr.AvailablePropertyPackages]
            return {"success": True, "count": len(pps), "property_packages": pps}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def generate_report(self, report_spec: dict) -> Dict[str, Any]:
        """
        Generate a formatted PDF research report from a parametric study result.

        report_spec keys:
          title (str)            -- Report title
          study_data (dict)      -- Output from parametric_study()
          report_text (dict)     -- LLM-drafted text (all six sections):
                                    {abstract, introduction, methodology,
                                     results, discussion, conclusion}
          output_dir (str, opt)  -- Save directory (default ~/Documents/dwsim_reports/)
          output_pdf (str, opt)  -- Full override path for PDF

        The flowsheet metadata (name, property package, streams, unit ops) is
        automatically injected from the current bridge state.
        """
        try:
            from report_generator import generate_report as _gen
        except ImportError as e:
            return {"success": False, "error": f"report_generator import failed: {e}"}

        # Inject current flowsheet metadata automatically
        report_spec = dict(report_spec)  # don't mutate caller's dict
        report_spec.setdefault("flowsheet_meta", {
            "flowsheet":        self.state.name or "Unknown",
            "property_package": self.state.property_package or "Unknown",
            "streams":          self.state.streams,
            "unit_ops":         self.state.unit_ops,
        })

        return _gen(report_spec)

    def create_flowsheet(self, topology: dict) -> Dict[str, Any]:
        """
        Autonomously build a DWSIM flowsheet from a JSON topology spec.
        See flowsheet_builder.py for full topology schema.

        Workflow: CreateFlowsheet -> AddCompounds -> AddPropertyPackage ->
                  AddObjects -> ConnectObjects -> AutoLayout -> Save -> Solve.
        """
        if not self._ready:
            r = self.initialize()
            if not r["success"]:
                return r

        try:
            from flowsheet_builder import build_flowsheet
        except ImportError as e:
            return {"success": False, "error": f"flowsheet_builder import failed: {e}"}

        # ── Clear stale flowsheets from the DWSIM manager BEFORE building ────
        # Old .NET flowsheet objects share the manager's tag registry.
        # If we don't purge them, tag-name conflicts and solver-state bleed
        # corrupt the new flowsheet.  (See _purge_stale_flowsheets docstring.)
        self._purge_stale_flowsheets()

        # Skip solving inside the builder — load_flowsheet will solve after
        # re-loading from disk.  This avoids a double-solve (builder + load).
        topology_nosave = dict(topology)
        topology_nosave["run_simulation"] = False
        result = build_flowsheet(self._mgr, topology_nosave)

        # If successful and flowsheet was saved, register it in the bridge
        # so subsequent tools (run_simulation, get_stream_properties, etc.) work
        if result.get("success") and result.get("flowsheet_path"):
            path = result["flowsheet_path"]
            result.pop("_fs", None)  # remove internal handle before reload
            try:
                load_result = self.load_flowsheet(path)
                if load_result.get("success"):
                    result["loaded_into_bridge"] = True
                    result["saved_to"] = path  # normalise key for callers
                    # Attach stream results so callers don't need a second call
                    try:
                        sr = self.get_simulation_results()
                        if sr.get("success"):
                            result["stream_results"] = sr.get("stream_results", {})
                            result["converged"] = bool(result["stream_results"])
                    except Exception:
                        pass
                else:
                    result["loaded_into_bridge"] = False
                    result.setdefault("warnings", []).append(
                        f"Flowsheet saved but could not be loaded into bridge: "
                        f"{load_result.get('error', '?')}")
            except Exception as e:
                result["loaded_into_bridge"] = False
                result.setdefault("warnings", []).append(
                    f"Post-save load failed: {e}")
        else:
            result.pop("_fs", None)

        result.pop("_fs", None)  # safety cleanup
        return result

    # ── v5: phase-specific results ────────────────────────────────────────────

    def get_phase_results(self, stream_tag: str,
                          phase: str = "vapor") -> Dict[str, Any]:
        """
        Read phase-specific thermodynamic properties for a material stream.

        phase: 'vapor' | 'liquid' | 'liquid1' | 'liquid2' | 'solid' | 'overall'
        Returns T, P, H, S, molar/mass fractions, and mole fractions per phase.
        """
        _PHASE_MAP = {
            "overall": 0, "mixture": 0,
            "liquid": 1, "overallliquid": 1,
            "vapor": 2, "vapour": 2,
            "liquid1": 3,
            "liquid2": 4,
            "liquid3": 5,
            "aqueous": 6,
            "solid": 7,
        }
        phase_idx = _PHASE_MAP.get(phase.lower().strip(), 2)

        obj = self._find_object(stream_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Stream '{stream_tag}' not found"}

        pp = _get_phase_props(obj, phase_idx)
        if pp is None:
            return {"success": False,
                    "error": f"Phase '{phase}' (index {phase_idx}) not available "
                              f"on stream '{stream_tag}'"}

        result: Dict[str, Any] = {
            "stream": stream_tag,
            "phase":  phase,
            "phase_index": phase_idx,
        }

        # Scalar properties
        for prop_key, attrs in [
            ("temperature_K",     ("temperature", "Temperature")),
            ("pressure_Pa",       ("pressure", "Pressure")),
            ("enthalpy_kJ_kg",    ("enthalpy", "Enthalpy")),
            ("entropy_kJ_kgK",    ("entropy", "Entropy")),
            ("molar_flow_mol_s",  ("molarflow", "MolarFlow")),
            ("mass_flow_kg_s",    ("massflow", "MassFlow")),
            ("molar_fraction",    ("molefraction", "MoleFraction")),
            ("mass_fraction",     ("massfraction", "MassFraction")),
            ("vapor_fraction",    ("vaporfraction", "VaporFraction")),
            ("density_kg_m3",     ("density", "Density")),
            ("viscosity_Pa_s",    ("viscosity", "Viscosity")),
        ]:
            for attr in attrs:
                for getter in (lambda a: getattr(pp, a),
                               lambda a: _reflect_get(pp, a)):
                    try:
                        v = _unwrap_nullable(getter(attr))
                        if v is not None:
                            result[prop_key] = round(v, 8)
                            break
                    except Exception:
                        pass
                if prop_key in result:
                    break

        # Unit conversions
        if "temperature_K" in result:
            result["temperature_C"] = round(result["temperature_K"] - 273.15, 3)
        if "pressure_Pa" in result:
            result["pressure_bar"] = round(result["pressure_Pa"] / 1e5, 5)
        if "molar_flow_mol_s" in result:
            result["molar_flow_kmolh"] = round(result["molar_flow_mol_s"] * 3.6, 5)
        if "mass_flow_kg_s" in result:
            result["mass_flow_kgh"] = round(result["mass_flow_kg_s"] * 3600, 4)

        # Compositions for this phase
        comps: Dict[str, float] = {}
        try:
            cmpds = getattr(pp, "Compounds", None) or _reflect_get(pp, "Compounds")
            if cmpds is None:
                # Try parent phase object
                phase_obj = None
                try:
                    phases = obj.Phases
                    for key in (phase_idx, str(phase_idx)):
                        try:
                            phase_obj = phases[key]; break
                        except Exception:
                            pass
                except Exception:
                    pass
                if phase_obj is not None:
                    cmpds = (getattr(phase_obj, "Compounds", None) or
                             _reflect_get(phase_obj, "Compounds"))
            if cmpds is not None:
                for name in cmpds.Keys:
                    comp = cmpds[name]
                    for attr in ("MoleFraction", "molefraction"):
                        try:
                            v = _unwrap_nullable(
                                getattr(comp, attr, None) or
                                _reflect_get(comp, attr))
                            if v is not None:
                                comps[str(name)] = round(v, 8)
                                break
                        except Exception:
                            pass
        except Exception:
            pass
        if comps:
            result["mole_fractions"] = comps

        return {"success": True, "phase_properties": result}

    # ── v6: transport / physical properties ──────────────────────────────────

    def get_transport_properties(self, stream_tag: str,
                                 phase: str = "overall") -> Dict[str, Any]:
        """
        Read transport and physical properties (density, viscosity, heat
        capacity, thermal conductivity, surface tension) for a phase.

        phase: 'overall' | 'vapor' | 'liquid' | 'liquid1' | 'liquid2' | 'solid'
        """
        _PHASE_MAP = {
            "overall": 0, "mixture": 0,
            "vapor": 2, "vapour": 2,
            "liquid": 1, "overallliquid": 1, "liquid1": 3,
            "liquid2": 4, "aqueous": 5, "solid": 6,
        }
        phase_idx = _PHASE_MAP.get(phase.lower().strip(), 0)

        obj = self._find_object(stream_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Stream '{stream_tag}' not found"}

        pp = _get_phase_props(obj, phase_idx)
        if pp is None:
            return {"success": False,
                    "error": f"Phase '{phase}' (index {phase_idx}) not "
                             f"available on '{stream_tag}'"}

        result: Dict[str, Any] = {
            "stream":      stream_tag,
            "phase":       phase,
            "phase_index": phase_idx,
        }

        for key, attrs in [
            ("density_kg_m3",            ("density", "Density")),
            ("viscosity_Pa_s",           ("viscosity", "Viscosity")),
            ("kinematic_viscosity_m2_s", ("kinematic_viscosity",
                                          "kinematicViscosity")),
            ("heat_capacity_cp_kJ_kgK",  ("heatCapacityCp", "heatcapacitycp")),
            ("heat_capacity_cv_kJ_kgK",  ("heatCapacityCv", "heatcapacitycv")),
            ("thermal_conductivity_W_mK", ("thermalConductivity",
                                           "thermalconductivity")),
            ("surface_tension_N_m",      ("surfaceTension", "surfacetension")),
            ("molecular_weight",         ("molecularWeight", "molecularweight")),
            ("compressibility_factor",   ("compressibilityFactor",
                                          "compressibilityfactor")),
            ("volumetric_flow_m3_s",     ("volumetric_flow", "volumetricflow",
                                          "VolumetricFlow")),
            ("vapor_fraction",           ("volumetricFraction", "vaporfraction",
                                          "VaporFraction")),
        ]:
            for attr in attrs:
                try:
                    v = _unwrap_nullable(
                        getattr(pp, attr, None) or _reflect_get(pp, attr))
                    if v is not None:
                        result[key] = round(v, 10)
                        break
                except Exception:
                    pass

        if "heat_capacity_cp_kJ_kgK" in result and \
           "heat_capacity_cv_kJ_kgK" in result and \
           result["heat_capacity_cv_kJ_kgK"]:
            result["cp_cv_ratio"] = round(
                result["heat_capacity_cp_kJ_kgK"] /
                result["heat_capacity_cv_kJ_kgK"], 5)

        return {"success": True, "transport_properties": result}

    # ── v6: phase envelope ───────────────────────────────────────────────────

    def calculate_phase_envelope(self, stream_tag: str = "",
                                 envelope_type: str = "PT",
                                 max_points: int = 50,
                                 quality: float = 0.0,
                                 fixed_P_Pa: float = 101325.0,
                                 fixed_T_K: float = 298.15,
                                 step_count: int = 40) -> Dict[str, Any]:
        """
        Compute a phase envelope using the stream's property package.

        envelope_type:
          'PT'  — pressure–temperature loop (bubble + dew curves)
          'Txy' — T–xy at fixed P (binary, returns x, y1, y2 arrays)
          'Pxy' — P–xy at fixed T (binary, returns x, P_bubble, P_dew arrays)

        fixed_P_Pa    — pressure for Txy mode (default 1 atm)
        fixed_T_K     — temperature for Pxy mode (default 25 °C)
        step_count    — resolution along x axis (binary modes, default 40)

        Returns bubble/dew curves as parallel arrays.
        """
        import System  # type: ignore

        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}

        obj = self._find_object(stream_tag) if stream_tag else None
        if stream_tag and obj is None:
            return {"success": False,
                    "error": f"Stream '{stream_tag}' not found"}

        pkg = None
        try:
            coll = getattr(self._flowsheet, "PropertyPackages", None)
            if coll is not None:
                try:
                    keys = list(coll.Keys)
                    if keys:
                        pkg = coll[keys[0]]
                except Exception:
                    items = list(coll)
                    if items:
                        pkg = items[0]
        except Exception:
            pass
        if pkg is None and obj is not None:
            for attr in ("PropertyPackage", "propertypackage"):
                try:
                    pkg = getattr(obj, attr, None) or _reflect_get(obj, attr)
                    if pkg is not None:
                        break
                except Exception:
                    pass
        if pkg is None:
            return {"success": False,
                    "error": "No property package available"}

        env_type = envelope_type.upper().strip()

        try:
            if env_type == "PT":
                try:
                    from DWSIM.Thermodynamics.PropertyPackages import \
                        PhaseEnvelopeOptions  # type: ignore
                    opts = PhaseEnvelopeOptions()
                    for attr, val in [
                        ("BubbleCurveMaximumPoints", int(max_points)),
                        ("DewCurveMaximumPoints", int(max_points)),
                        ("QualityLine", bool(quality and quality > 0)),
                        ("QualityValue", float(quality or 0.0)),
                    ]:
                        try:
                            setattr(opts, attr, val)
                        except Exception:
                            pass
                except Exception:
                    opts = None

                env = None
                last_exc = None
                # Try direct call first
                for args in ((opts, None), (opts,), (None, None), (None,)):
                    try:
                        if hasattr(pkg, "DW_ReturnPhaseEnvelope"):
                            env = pkg.DW_ReturnPhaseEnvelope(*args)
                            if env is not None:
                                break
                    except Exception as exc:
                        last_exc = exc
                # Reflection fallback: IPropertyPackage hides the method
                if env is None:
                    try:
                        methods = pkg.GetType().GetMethods()
                        target = None
                        for m in methods:
                            if m.Name == "DW_ReturnPhaseEnvelope":
                                target = m
                                break
                        if target is not None:
                            n_params = target.GetParameters().Length
                            from System import Array, Object  # type: ignore
                            arg_array = Array[Object](n_params)
                            if n_params >= 1:
                                arg_array[0] = opts
                            env = target.Invoke(pkg, arg_array)
                    except Exception as exc:
                        last_exc = exc
                if env is None:
                    return {"success": False,
                            "error": f"DW_ReturnPhaseEnvelope failed: {last_exc}"}

                def _list(arr):
                    if arr is None:
                        return []
                    try:
                        return [float(x) for x in arr if x is not None]
                    except Exception:
                        return []

                # DW_ReturnPhaseEnvelope returns Object[] with layout:
                #   [0]=bubble_T [1]=bubble_P [2]=bubble_H [3]=bubble_S [4]=bubble_V
                #   [5]=dew_T    [6]=dew_P    [7]=dew_H    [8]=dew_S    [9]=dew_V
                # Pure-fluid packages (Steam Tables) populate only the dew side.
                bubble = {"T_K": [], "P_Pa": []}
                dew    = {"T_K": [], "P_Pa": []}
                try:
                    env_len = getattr(env, "Length", None)
                    if env_len is None:
                        items = list(env)
                    else:
                        items = [env[i] for i in range(int(env_len))]
                    if len(items) >= 2:
                        bubble["T_K"]  = _list(items[0])
                        bubble["P_Pa"] = _list(items[1])
                    if len(items) >= 7:
                        dew["T_K"]  = _list(items[5])
                        dew["P_Pa"] = _list(items[6])
                except Exception as exc:
                    return {"success": False,
                            "error": f"Parsing envelope failed: {exc}"}

                # Strip degenerate single-point curves (placeholder zeros / -1)
                def _clean(curve):
                    t, p = curve["T_K"], curve["P_Pa"]
                    if len(t) <= 1:
                        return {"T_K": [], "P_Pa": []}
                    return {"T_K": t, "P_Pa": p}

                bubble = _clean(bubble)
                dew    = _clean(dew)

                return {
                    "success":       True,
                    "envelope_type": "PT",
                    "bubble_curve":  bubble,
                    "dew_curve":     dew,
                    "points":        {"bubble": len(bubble["T_K"]),
                                      "dew":    len(dew["T_K"])},
                }

            if env_type in ("TXY", "PXY"):
                # Attach stream to PP so it sees the compounds
                if obj is not None:
                    try:
                        pkg.CurrentMaterialStream = obj
                    except Exception:
                        pass
                try:
                    cmps = list(self._flowsheet.SelectedCompounds.Keys)
                except Exception:
                    cmps = []
                if len(cmps) < 2:
                    return {"success": False,
                            "error": "Binary envelope needs >= 2 compounds"}

                from System import Array, Object  # type: ignore
                params = Array[Object](13)
                params[0] = "T-x-y" if env_type == "TXY" else "P-x-y"
                params[1] = float(fixed_P_Pa)   # used for T-x-y
                params[2] = float(fixed_T_K)    # used for P-x-y
                params[3] = True   # VLE
                params[4] = False  # LLE
                params[5] = False  # SLE
                params[6] = False  # Critical
                params[7] = False  # SolidSolution
                params[8] = None
                params[9] = None
                params[10] = int(step_count)
                params[11] = 0.0   # MinX
                params[12] = 1.0   # MaxX

                methods = [m for m in pkg.GetType().GetMethods()
                           if m.Name == "DW_ReturnBinaryEnvelope"]
                if not methods:
                    return {"success": False,
                            "error": "DW_ReturnBinaryEnvelope not found"}
                m = methods[0]
                args = Array[Object](2)
                args[0] = params
                args[1] = None
                env = m.Invoke(pkg, args)

                def _list(arr):
                    try:
                        return [float(x) for x in arr if x is not None]
                    except Exception:
                        return []

                length = getattr(env, "Length", 0)
                items = [env[i] for i in range(int(length))]

                if env_type == "TXY":
                    # Layout: [px, py1, py2, ...]
                    result = {
                        "success":       True,
                        "envelope_type": "Txy",
                        "fixed_P_Pa":    float(fixed_P_Pa),
                        "compound_1":    cmps[0],
                        "compound_2":    cmps[1],
                        "x_compound_1":  _list(items[0]) if len(items) > 0 else [],
                        "T_bubble_K":    _list(items[1]) if len(items) > 1 else [],
                        "T_dew_K":       _list(items[2]) if len(items) > 2 else [],
                    }
                else:
                    result = {
                        "success":       True,
                        "envelope_type": "Pxy",
                        "fixed_T_K":     float(fixed_T_K),
                        "compound_1":    cmps[0],
                        "compound_2":    cmps[1],
                        "x_compound_1":  _list(items[0]) if len(items) > 0 else [],
                        "P_bubble_Pa":   _list(items[1]) if len(items) > 1 else [],
                        "P_dew_Pa":      _list(items[2]) if len(items) > 2 else [],
                    }
                result["points"] = {
                    "bubble": len(result.get("T_bubble_K") or
                                  result.get("P_bubble_Pa") or []),
                    "dew":    len(result.get("T_dew_K") or
                                  result.get("P_dew_Pa") or []),
                }
                return result

            return {"success": False,
                    "error": f"envelope_type '{envelope_type}' not "
                             f"supported (use 'PT', 'Txy', or 'Pxy')"}

        except Exception as exc:
            return {"success": False,
                    "error": f"Phase envelope failed: {exc}",
                    "trace":  traceback.format_exc(limit=5)}

    # ── v6: flash spec control ───────────────────────────────────────────────

    def set_stream_flash_spec(self, stream_tag: str,
                              spec: str = "TP") -> Dict[str, Any]:
        """
        Set the flash calculation mode (SpecType) on a material stream.

        spec values (case-insensitive):
          'TP' | 'T_P' | 'Temperature_and_Pressure'
          'PH' | 'P_H' | 'Pressure_and_Enthalpy'
          'PS' | 'P_S' | 'Pressure_and_Entropy'
          'PVF'| 'P_VF'| 'Pressure_and_VaporFraction'
          'TVF'| 'T_VF'| 'Temperature_and_VaporFraction'
        """
        import System  # type: ignore

        obj = self._find_object(stream_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Stream '{stream_tag}' not found"}

        _SPEC_MAP = {
            "tp":  "Temperature_and_Pressure",
            "t_p": "Temperature_and_Pressure",
            "temperature_and_pressure": "Temperature_and_Pressure",
            "ph":  "Pressure_and_Enthalpy",
            "p_h": "Pressure_and_Enthalpy",
            "pressure_and_enthalpy": "Pressure_and_Enthalpy",
            "ps":  "Pressure_and_Entropy",
            "p_s": "Pressure_and_Entropy",
            "pressure_and_entropy": "Pressure_and_Entropy",
            "pvf":  "Pressure_and_VaporFraction",
            "p_vf": "Pressure_and_VaporFraction",
            "pressure_and_vaporfraction": "Pressure_and_VaporFraction",
            "tvf":  "Temperature_and_VaporFraction",
            "t_vf": "Temperature_and_VaporFraction",
            "temperature_and_vaporfraction": "Temperature_and_VaporFraction",
        }
        enum_name = _SPEC_MAP.get(spec.lower().replace(" ", "").strip())
        if enum_name is None:
            return {"success": False,
                    "error": f"Unknown spec '{spec}'. "
                             f"Valid: {sorted(set(_SPEC_MAP.values()))}"}

        try:
            prop = obj.GetType().GetProperty("SpecType")
            if prop is None or not prop.CanWrite:
                return {"success": False,
                        "error": "SpecType property not writable"}
            enum_val = System.Enum.Parse(prop.PropertyType, enum_name)
            prop.SetValue(obj, enum_val)
        except Exception as exc:
            return {"success": False,
                    "error": f"Setting SpecType failed: {exc}"}

        _reflect_set_flag(obj, "Calculated", False)
        _reflect_set_flag(obj, "IsDirty",    True)

        return {"success": True,
                "message": f"Set flash spec {enum_name} on '{stream_tag}'",
                "spec":    enum_name}

    # ── v7: binary interaction parameters ────────────────────────────────────

    def _get_active_pp(self):
        if self._flowsheet is None:
            return None
        try:
            coll = getattr(self._flowsheet, "PropertyPackages", None)
            if coll is None:
                return None
            keys = list(coll.Keys)
            if not keys:
                return None
            return coll[keys[0]]
        except Exception:
            return None

    def _pp_field(self, pkg, name):
        """Reflection helper — read a non-public field off a PP instance."""
        try:
            import System.Reflection as SR  # type: ignore
            bf = (SR.BindingFlags.Public | SR.BindingFlags.NonPublic |
                  SR.BindingFlags.Instance)
            curr = pkg.GetType()
            while curr is not None and curr.Name != "Object":
                f = curr.GetField(name, bf)
                if f is not None:
                    return f, f.GetValue(pkg)
                curr = curr.BaseType
        except Exception:
            pass
        return None, None

    def get_binary_interaction_parameters(self,
                                          compound_1: str = "",
                                          compound_2: str = "") -> Dict[str, Any]:
        """
        Read binary interaction parameters from the active property package.

        • Peng-Robinson / SRK  — reads KijMatrix  (returns kij)
        • NRTL / UNIQUAC       — reads InteractionParameters (A12, A21, B12,
                                 B21, C12, C21, alpha12)

        If compound_1/compound_2 omitted, dumps the full matrix for the
        currently-selected compounds.
        """
        pkg = self._get_active_pp()
        if pkg is None:
            return {"success": False, "error": "No property package loaded"}

        pkg_name = pkg.GetType().Name
        try:
            cmps = list(self._flowsheet.SelectedCompounds.Keys)
        except Exception:
            cmps = []

        # --- Cubic EOS (PR / SRK) -------------------------------------------
        if "PengRobinson" in pkg_name or "SRK" in pkg_name or \
           "PRSV" in pkg_name:
            _f, kij = self._pp_field(pkg, "KijMatrix")
            if kij is None:
                return {"success": False,
                        "error": f"KijMatrix not found on {pkg_name}"}
            n_rows = kij.GetLength(0)
            n_cols = kij.GetLength(1)
            matrix = [[float(kij.GetValue(i, j)) for j in range(n_cols)]
                      for i in range(n_rows)]

            if compound_1 and compound_2:
                try:
                    i = cmps.index(compound_1)
                    j = cmps.index(compound_2)
                except ValueError:
                    return {"success": False,
                            "error": f"Compounds not found in {cmps}"}
                return {"success":    True,
                        "model":      "kij",
                        "package":    pkg_name,
                        "compound_1": compound_1,
                        "compound_2": compound_2,
                        "kij":        matrix[i][j]}

            return {"success":   True,
                    "model":     "kij",
                    "package":   pkg_name,
                    "compounds": cmps,
                    "matrix":    matrix}

        # --- Activity coefficient (NRTL / UNIQUAC) --------------------------
        inner_name = "nrtl" if "NRTL" in pkg_name else (
            "uniquac" if "UNIQUAC" in pkg_name else None)
        if inner_name is None:
            return {"success": False,
                    "error": f"BIP not implemented for {pkg_name}"}

        _f, inner = self._pp_field(pkg, inner_name)
        if inner is None:
            return {"success": False,
                    "error": f"Inner '{inner_name}' field missing on {pkg_name}"}

        try:
            ip_store = inner.InteractionParameters
        except Exception as exc:
            return {"success": False,
                    "error": f"InteractionParameters access failed: {exc}"}

        def _ipdata_to_dict(d):
            out = {}
            for attr in ("A12", "A21", "B12", "B21", "C12", "C21",
                         "alpha12", "Name1", "Name2", "comment"):
                try:
                    v = getattr(d, attr, None)
                    if v is not None:
                        out[attr] = (float(v)
                                     if attr not in ("Name1", "Name2", "comment")
                                     else str(v))
                except Exception:
                    pass
            return out

        if compound_1 and compound_2:
            for a, b in ((compound_1, compound_2), (compound_2, compound_1)):
                try:
                    if a in ip_store and b in ip_store[a]:
                        return {"success":    True,
                                "model":      inner_name.upper(),
                                "package":    pkg_name,
                                "compound_1": a,
                                "compound_2": b,
                                "params":     _ipdata_to_dict(ip_store[a][b])}
                except Exception:
                    pass
            return {"success": False,
                    "error": f"No {inner_name.upper()} pair for "
                             f"'{compound_1}' / '{compound_2}'"}

        pairs: Dict[str, Dict[str, Any]] = {}
        try:
            for k in list(ip_store.Keys):
                inner_d = ip_store[k]
                for kk in list(inner_d.Keys):
                    pairs[f"{k}/{kk}"] = _ipdata_to_dict(inner_d[kk])
        except Exception:
            pass
        return {"success":   True,
                "model":     inner_name.upper(),
                "package":   pkg_name,
                "compounds": cmps,
                "pairs":     pairs}

    def set_binary_interaction_parameters(self,
                                          compound_1: str,
                                          compound_2: str,
                                          **params) -> Dict[str, Any]:
        """
        Write binary interaction parameters to the active property package.

        For cubic EOS (PR/SRK) pass params={'kij': 0.123}.
        For NRTL/UNIQUAC pass any of:
          A12, A21, B12, B21, C12, C21, alpha12   (NRTL alpha12 only)
        Unspecified fields are left untouched.
        """
        pkg = self._get_active_pp()
        if pkg is None:
            return {"success": False, "error": "No property package loaded"}

        pkg_name = pkg.GetType().Name
        try:
            cmps = list(self._flowsheet.SelectedCompounds.Keys)
        except Exception:
            cmps = []

        if "PengRobinson" in pkg_name or "SRK" in pkg_name or "PRSV" in pkg_name:
            kij = params.get("kij")
            if kij is None:
                return {"success": False,
                        "error": "Cubic EOS needs kij=<value>"}
            _f, matrix = self._pp_field(pkg, "KijMatrix")
            if matrix is None:
                return {"success": False, "error": "KijMatrix missing"}
            try:
                i = cmps.index(compound_1)
                j = cmps.index(compound_2)
            except ValueError:
                return {"success": False,
                        "error": f"Compounds must be one of {cmps}"}
            matrix.SetValue(float(kij), i, j)
            matrix.SetValue(float(kij), j, i)  # symmetric
            return {"success": True,
                    "message": f"Set kij({compound_1},{compound_2})={kij}",
                    "package": pkg_name}

        inner_name = "nrtl" if "NRTL" in pkg_name else (
            "uniquac" if "UNIQUAC" in pkg_name else None)
        if inner_name is None:
            return {"success": False,
                    "error": f"BIP writing not implemented for {pkg_name}"}

        _f, inner = self._pp_field(pkg, inner_name)
        if inner is None:
            return {"success": False,
                    "error": f"Inner '{inner_name}' missing"}
        try:
            ip_store = inner.InteractionParameters
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        # Locate or create entry
        a, b = compound_1, compound_2
        entry = None
        for ka, kb in ((a, b), (b, a)):
            try:
                if ka in ip_store and kb in ip_store[ka]:
                    entry = ip_store[ka][kb]
                    break
            except Exception:
                pass
        created = False
        if entry is None:
            try:
                import clr  # type: ignore
                if inner_name == "nrtl":
                    from DWSIM.Thermodynamics.PropertyPackages.Auxiliary \
                        import NRTL_IPData  # type: ignore
                    entry = NRTL_IPData()
                else:
                    from DWSIM.Thermodynamics.PropertyPackages.Auxiliary \
                        import UNIQUAC_IPData  # type: ignore
                    entry = UNIQUAC_IPData()
                # Register in dict
                if a not in ip_store:
                    from System.Collections.Generic import Dictionary  # type: ignore
                    ip_store[a] = Dictionary[str, type(entry)]()
                ip_store[a][b] = entry
                created = True
            except Exception as exc:
                return {"success": False,
                        "error": f"Could not create IP data: {exc}"}

        updated: Dict[str, float] = {}
        for key in ("A12", "A21", "B12", "B21", "C12", "C21", "alpha12"):
            if key in params:
                if key == "alpha12" and inner_name != "nrtl":
                    continue
                try:
                    setattr(entry, key, float(params[key]))
                    updated[key] = float(params[key])
                except Exception as exc:
                    return {"success": False,
                            "error": f"Setting {key}: {exc}"}

        return {"success": True,
                "message": f"Updated {inner_name.upper()} params for "
                           f"{compound_1}/{compound_2}",
                "package": pkg_name,
                "created": created,
                "updated": updated}

    # ── v7: heat exchanger design modes ──────────────────────────────────────

    def configure_heat_exchanger(self,
                                 hx_tag: str,
                                 mode: str = "",
                                 area_m2: Optional[float] = None,
                                 overall_U_W_m2K: Optional[float] = None,
                                 hot_outlet_T_K: Optional[float] = None,
                                 cold_outlet_T_K: Optional[float] = None,
                                 hot_dp_Pa: Optional[float] = None,
                                 cold_dp_Pa: Optional[float] = None,
                                 duty_W: Optional[float] = None,
                                 flow_direction: str = "",
                                 lmtd_correction_F: Optional[float] = None,
                                 defined_temperature: str = "",
                                 ) -> Dict[str, Any]:
        """
        Configure a HeatExchanger unit op's design/rating mode and parameters.

        mode values (case-insensitive):
          'CalcTempHotOut'      — specify U*A + cold-out T, solve hot-out T
          'CalcTempColdOut'     — specify U*A + hot-out T, solve cold-out T
          'CalcBothTemp'        — specify duty, solve both outlet T's
          'CalcBothTemp_UA'     — specify U*A, solve both outlet T's
          'CalcArea'            — specify both outlet T's, solve area
          'PinchPoint'          — pinch-point mode
          'ThermalEfficiency'   — specify efficiency + hot/cold-out
          'OutletVaporFraction1'/'OutletVaporFraction2'
          'ShellandTube_Rating' / 'ShellandTube_CalcFoulingFactor'

        flow_direction: 'counter' | 'cocurrent'
        defined_temperature: 'hot' | 'cold'  (for CalcBothTemp mode)
        """
        import System  # type: ignore

        obj = self._find_object(hx_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Unit op '{hx_tag}' not found"}
        if "HeatExchanger" not in obj.GetType().Name:
            return {"success": False,
                    "error": f"'{hx_tag}' is {obj.GetType().Name}, "
                             f"not a HeatExchanger"}

        t = obj.GetType()
        applied: List[str] = []
        warnings: List[str] = []

        def _set_nullable(pname: str, val: Optional[float], label: str):
            if val is None:
                return
            try:
                p = t.GetProperty(pname)
                if p and p.CanWrite:
                    p.SetValue(obj, System.Nullable[System.Double](float(val)))
                    applied.append(f"{label}={val}")
                else:
                    warnings.append(f"{pname} not writable")
            except Exception as exc:
                warnings.append(f"{pname}: {exc}")

        def _set_enum(pname: str, enum_val: str, label: str):
            try:
                p = t.GetProperty(pname)
                if p and p.CanWrite and p.PropertyType.IsEnum:
                    ev = System.Enum.Parse(p.PropertyType, enum_val)
                    p.SetValue(obj, ev)
                    applied.append(f"{label}={enum_val}")
                else:
                    warnings.append(f"{pname} not settable")
            except Exception as exc:
                warnings.append(f"{pname}: {exc}")

        if mode:
            _set_enum("CalculationMode", mode, "mode")

        if flow_direction:
            fd = flow_direction.strip().lower()
            val = "CounterCurrent" if fd.startswith("counter") else (
                  "CoCurrent" if fd.startswith("co") else "")
            if val:
                _set_enum("FlowDir", val, "flow_direction")
            else:
                warnings.append(f"bad flow_direction '{flow_direction}'")

        if defined_temperature:
            dt = defined_temperature.strip().lower()
            val = "Hot_Fluid" if dt.startswith("hot") else (
                  "Cold_Fluid" if dt.startswith("cold") else "")
            if val:
                _set_enum("DefinedTemperature", val, "defined_temperature")

        _set_nullable("Area",                      area_m2,        "area_m2")
        _set_nullable("OverallCoefficient",        overall_U_W_m2K, "U")
        _set_nullable("HotSideOutletTemperature",  hot_outlet_T_K, "T_hot_out")
        _set_nullable("ColdSideOutletTemperature", cold_outlet_T_K, "T_cold_out")
        _set_nullable("HotSidePressureDrop",       hot_dp_Pa,      "dp_hot")
        _set_nullable("ColdSidePressureDrop",      cold_dp_Pa,     "dp_cold")
        _set_nullable("HeatDuty",                  duty_W,         "duty")
        _set_nullable("LMTD_F",                    lmtd_correction_F, "LMTD_F")

        _reflect_set_flag(obj, "Calculated", False)
        _reflect_set_flag(obj, "IsDirty",    True)

        result = {"success": True,
                  "message": f"Configured HX '{hx_tag}'",
                  "applied": applied}
        if warnings:
            result["warnings"] = warnings
        return result

    # ── v5: energy streams ────────────────────────────────────────────────────

    def get_energy_stream(self, stream_tag: str) -> Dict[str, Any]:
        """Read the duty of an energy stream in multiple units."""
        obj = self._find_object(stream_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Energy stream '{stream_tag}' not found"}

        duty_W: Optional[float] = None
        for attr in ("EnergyFlow", "energyflow", "Duty", "duty", "Power",
                     "HeatDuty", "Q"):
            for getter in (lambda a: getattr(obj, a),
                           lambda a: _reflect_get(obj, a)):
                try:
                    v = _unwrap_nullable(getter(attr))
                    if v is not None:
                        duty_W = v; break
                except Exception:
                    pass
            if duty_W is not None:
                break

        if duty_W is None:
            # try phase 0 Properties
            pp = _get_phase_props(obj, 0)
            if pp is not None:
                for attr in ("EnergyFlow", "energyflow", "Duty"):
                    v = _unwrap_nullable(_reflect_get(pp, attr))
                    if v is not None:
                        duty_W = v; break

        if duty_W is None:
            return {"success": False,
                    "error": f"Could not read duty from energy stream '{stream_tag}'"}

        return {
            "success": True,
            "stream":  stream_tag,
            "duty_W":  round(duty_W, 4),
            "duty_kW": round(duty_W / 1e3, 6),
            "duty_kJ_h": round(duty_W * 3.6, 4),
            "duty_kcal_h": round(duty_W * 3.6 / 4.184, 4),
        }

    def set_energy_stream(self, stream_tag: str,
                          duty_W: float) -> Dict[str, Any]:
        """Set the duty of an energy stream (value in Watts)."""
        obj = self._find_object(stream_tag)
        if obj is None:
            return {"success": False,
                    "error": f"Energy stream '{stream_tag}' not found"}

        set_ok = False
        tried: List[str] = []
        for attr in ("EnergyFlow", "energyflow", "Duty", "duty", "Power",
                     "HeatDuty", "Q"):
            # Reflection-based typed set
            tp = obj.GetType().GetProperty(attr)
            if tp and tp.CanWrite:
                try:
                    tp.SetValue(obj, float(duty_W))
                    set_ok = True
                    tried.append(f"reflection:{attr}")
                    break
                except Exception:
                    pass
            # setattr fallback
            try:
                setattr(obj, attr, float(duty_W))
                set_ok = True
                tried.append(f"setattr:{attr}")
                break
            except Exception:
                pass

        if not set_ok:
            return {"success": False,
                    "error": f"Could not set duty on '{stream_tag}'",
                    "tried": tried}

        return {
            "success": True,
            "message": f"Set duty = {duty_W} W on '{stream_tag}'",
            "duty_kW": round(duty_W / 1e3, 6),
        }

    # ── v5: delete / disconnect ───────────────────────────────────────────────

    def delete_object(self, tag: str) -> Dict[str, Any]:
        """Remove a stream or unit operation from the active flowsheet."""
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        obj = self._find_object(tag)
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        deleted = False

        # Strategy 1: flowsheet.DeleteSimulationObject(guid)
        coll = self._get_collection()
        obj_guid: Optional[str] = None
        if coll:
            tag_cache = self._active_tag_cache()
            for guid, human in tag_cache.items():
                if human.strip() == tag.strip():
                    obj_guid = guid
                    break
        if obj_guid:
            for method in ("DeleteSimulationObject", "RemoveSimulationObject",
                           "DeleteObject", "RemoveObject"):
                if hasattr(self._flowsheet, method):
                    try:
                        getattr(self._flowsheet, method)(obj_guid)
                        deleted = True
                        break
                    except Exception:
                        pass

        # Strategy 2: collection Remove
        if not deleted and coll and obj_guid:
            for method in ("Remove", "remove"):
                if hasattr(coll, method):
                    try:
                        getattr(coll, method)(obj_guid)
                        deleted = True
                        break
                    except Exception:
                        pass

        # Strategy 3: manager-level delete
        if not deleted:
            for method in ("DeleteSimulationObject", "RemoveSimulationObject"):
                if hasattr(self._mgr, method):
                    try:
                        getattr(self._mgr, method)(self._flowsheet, obj)
                        deleted = True
                        break
                    except Exception:
                        pass

        if not deleted:
            return {"success": False,
                    "error": f"Could not delete '{tag}' — no working delete API found"}

        # Remove from tag cache and state
        if obj_guid and self._active_alias in self._flowsheets:
            self._flowsheets[self._active_alias]["tag_cache"].pop(obj_guid, None)
        self._rebuild_active_cache()

        return {"success": True,
                "message": f"Deleted '{tag}' from flowsheet"}

    def disconnect_streams(self, uo_tag: str,
                           stream_tag: str) -> Dict[str, Any]:
        """
        Sever the connection between a unit operation and a stream
        without deleting either object.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        uo_obj  = self._find_object(uo_tag)
        str_obj = self._find_object(stream_tag)
        if uo_obj is None:
            return {"success": False, "error": f"Unit op '{uo_tag}' not found"}
        if str_obj is None:
            return {"success": False, "error": f"Stream '{stream_tag}' not found"}

        disconnected = False

        # Strategy 1: DisconnectObject / DisconnectStream on flowsheet
        for method in ("DisconnectObject", "DisconnectStream",
                       "Disconnect", "RemoveConnection"):
            if hasattr(self._flowsheet, method):
                for args in [(uo_obj, str_obj), (str_obj, uo_obj),
                             (uo_tag, stream_tag), (stream_tag, uo_tag)]:
                    try:
                        getattr(self._flowsheet, method)(*args)
                        disconnected = True
                        break
                    except Exception:
                        pass
            if disconnected:
                break

        # Strategy 2: Graphic connector manipulation
        if not disconnected:
            for obj in (uo_obj, str_obj):
                try:
                    go = getattr(obj, "GraphicObject", None)
                    if go is None:
                        continue
                    conns = (getattr(go, "InputConnectors", None) or [] +
                             getattr(go, "OutputConnectors", None) or [])
                    for conn in conns:
                        try:
                            attached_id = getattr(conn, "AttachedToObjID", None)
                            other_tag   = self._active_tag_cache().get(
                                str(attached_id), str(attached_id))
                            if (other_tag == uo_tag or
                                    other_tag == stream_tag):
                                # clear attachment
                                conn_type = type(conn)
                                for attr in ("IsAttached", "AttachedToObjID"):
                                    tp = conn_type.GetProperty(attr)
                                    if tp and tp.CanWrite:
                                        tp.SetValue(conn,
                                            False if attr == "IsAttached" else "")
                                disconnected = True
                        except Exception:
                            pass
                except Exception:
                    pass

        if not disconnected:
            return {
                "success": False,
                "error":   f"Could not disconnect '{uo_tag}' ↔ '{stream_tag}' "
                           f"— no working disconnect API found. "
                           f"Consider deleting and re-adding streams instead.",
            }

        return {"success": True,
                "message": f"Disconnected '{stream_tag}' from '{uo_tag}'"}

    def connect_streams(self, from_tag: str, to_tag: str,
                        from_port: int = 0,
                        to_port: int = 0) -> Dict[str, Any]:
        """Wire a stream or unit-op output to another object's input.

        Uses DWSIM's fs.ConnectObjects(from_go, to_go, from_port, to_port).
        Validates both endpoints exist and the connection actually takes
        effect by re-reading the connector state afterward.
        """
        if self._flowsheet is None:
            return {"success": False, "code": "NO_FLOWSHEET",
                    "error": "No flowsheet loaded"}
        frm_obj = self._find_object(from_tag)
        to_obj  = self._find_object(to_tag)
        if frm_obj is None:
            return {"success": False, "code": "OBJECT_NOT_FOUND",
                    "error": f"from: '{from_tag}' not found"}
        if to_obj is None:
            return {"success": False, "code": "OBJECT_NOT_FOUND",
                    "error": f"to: '{to_tag}' not found"}
        try:
            frm_go = frm_obj.GraphicObject
            to_go  = to_obj.GraphicObject
            if frm_go is None or to_go is None:
                return {"success": False, "code": "NO_GRAPHIC",
                        "error": "object has no GraphicObject (not layouted?)"}
        except Exception as exc:
            return {"success": False, "code": "NO_GRAPHIC",
                    "error": f"GraphicObject access failed: {exc}"}

        try:
            import System
            _sink = io.StringIO()
            with suppress_dotnet_console(), \
                 redirect_stdout(_sink), redirect_stderr(_sink):
                self._flowsheet.ConnectObjects(
                    frm_go, to_go,
                    System.Int32(int(from_port)),
                    System.Int32(int(to_port)))
        except Exception as exc:
            return {"success": False, "code": "CONNECT_FAILED",
                    "error": f"ConnectObjects({from_tag}→{to_tag}): "
                             f"{str(exc)[:160]}"}

        # Verify the connection actually took effect.
        verified = False
        try:
            for conn in (getattr(frm_go, "OutputConnectors", None) or []):
                if getattr(conn, "IsAttached", False):
                    ac = getattr(conn, "AttachedConnector", None)
                    if ac and getattr(ac, "AttachedTo", None) is to_go:
                        verified = True
                        break
        except Exception:
            pass

        return {"success": True,
                "from": from_tag, "to": to_tag,
                "from_port": from_port, "to_port": to_port,
                "verified": verified,
                "message": f"Connected {from_tag}[{from_port}] → "
                           f"{to_tag}[{to_port}]"}

    def validate_topology(self) -> Dict[str, Any]:
        """Graph-level sanity check: find dangling streams and unconnected ports.

        Returns issues as a list so the agent can report or auto-repair.
        """
        if self._flowsheet is None:
            return {"success": False, "code": "NO_FLOWSHEET",
                    "error": "No flowsheet loaded"}
        coll = self._get_collection()
        if coll is None:
            return {"success": False, "error": "no collection"}

        dangling_streams = []  # streams with no upstream or downstream unit
        unconnected_ports = []  # unit ops with open required ports
        tags = self._active_tag_cache()

        for guid, obj in self._iter_collection(coll):
            tag = tags.get(str(guid), str(guid)[:8])
            try:
                typename = obj.GetType().Name
            except Exception:
                continue
            is_stream = "MaterialStream" in typename or "EnergyStream" in typename
            try:
                go = obj.GraphicObject
            except Exception:
                continue
            if go is None:
                continue
            in_connected  = any(getattr(c, "IsAttached", False)
                                for c in (getattr(go, "InputConnectors", None)
                                          or []))
            out_connected = any(getattr(c, "IsAttached", False)
                                for c in (getattr(go, "OutputConnectors", None)
                                          or []))
            if is_stream and not (in_connected or out_connected):
                dangling_streams.append(tag)
            elif not is_stream:
                # Unit op — check if it has no connections at all
                if not in_connected and not out_connected:
                    unconnected_ports.append({"tag": tag, "type": typename,
                                              "issue": "no connections"})

        issues = []
        for s in dangling_streams:
            issues.append({"severity": "error", "tag": s,
                           "message": f"Stream '{s}' is dangling — "
                                      f"not connected to any unit operation"})
        for u in unconnected_ports:
            issues.append({"severity": "warning", "tag": u["tag"],
                           "message": f"Unit op '{u['tag']}' ({u['type']}) "
                                      f"has no connections"})

        return {"success": True,
                "issue_count": len(issues),
                "issues": issues,
                "dangling_streams": dangling_streams,
                "is_valid": len(issues) == 0}

    # ── v5: reaction setup ────────────────────────────────────────────────────

    def setup_reaction(
        self,
        reactor_tag:   str,
        reactions:     List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Configure reactions on a conversion or kinetic reactor.

        Each reaction dict:
          name         (str)   — reaction label
          type         (str)   — 'conversion' | 'kinetic' | 'equilibrium'
          base_compound (str)  — limiting reactant (for conversion type)
          conversion   (float) — fractional conversion 0-1 (conversion type)
          stoichiometry (dict) — {compound: coefficient} (negative = reactant)

        Returns list of configured reaction names.
        """
        if self._flowsheet is None:
            return {"success": False, "error": "No flowsheet loaded"}
        obj = self._find_object(reactor_tag)
        if obj is None:
            return {"success": False, "error": f"Reactor '{reactor_tag}' not found"}

        configured: List[str] = []
        errors:     List[str] = []

        for rxn_spec in reactions:
            rxn_name = rxn_spec.get("name", f"R{len(configured)+1}")
            rxn_type = rxn_spec.get("type", "conversion").lower()

            try:
                # Try to get/create the Reactions collection on the reactor
                rxn_coll = None
                for attr in ("Reactions", "ReactionSet", "ReactionsSetID"):
                    rxn_coll = (getattr(obj, attr, None) or
                                _reflect_get(obj, attr))
                    if rxn_coll is not None:
                        break

                if rxn_type == "conversion":
                    conv = float(rxn_spec.get("conversion", 0.0))
                    base = rxn_spec.get("base_compound", "")

                    # Try direct property set on reactor (ConversionReactor)
                    set_ok = False
                    for prop, val in [("Conversion", conv),
                                      ("BaseReactant", base)]:
                        tp = obj.GetType().GetProperty(prop)
                        if tp and tp.CanWrite:
                            try:
                                tp.SetValue(obj, val if isinstance(val, str)
                                            else float(val))
                                set_ok = True
                            except Exception:
                                pass
                    if set_ok:
                        configured.append(rxn_name)
                        continue

                    # Try adding to Reactions list if available
                    if rxn_coll is not None:
                        try:
                            # Try reflection to create a new Reaction object
                            asm = obj.GetType().Assembly
                            rxn_types = [t for t in asm.GetTypes()
                                         if "reaction" in t.Name.lower()
                                         and "conversion" in t.Name.lower()]
                            if rxn_types:
                                new_rxn = rxn_types[0]()
                                for prop, val in [
                                    ("Name",       rxn_name),
                                    ("Conversion", conv),
                                    ("BaseReactant", base),
                                ]:
                                    tp = new_rxn.GetType().GetProperty(prop)
                                    if tp and tp.CanWrite:
                                        try:
                                            tp.SetValue(new_rxn,
                                                val if isinstance(val, str)
                                                else float(val))
                                        except Exception:
                                            pass
                                rxn_coll.Add(new_rxn)
                                configured.append(rxn_name)
                                continue
                        except Exception as e:
                            errors.append(f"{rxn_name}: {e}")

                elif rxn_type in ("kinetic", "equilibrium"):
                    # For kinetic/equilibrium — set stoichiometry via reflection
                    stoich = rxn_spec.get("stoichiometry", {})
                    if rxn_coll is not None:
                        try:
                            for comp, coeff in stoich.items():
                                # Try to find compound in reactor's stoichiometry dict
                                for attr in ("StoichiometricCoefficients",
                                             "Stoichiometry"):
                                    sd = _reflect_get(obj, attr)
                                    if sd is not None:
                                        try:
                                            sd[comp] = float(coeff)
                                        except Exception:
                                            pass
                            configured.append(rxn_name)
                        except Exception as e:
                            errors.append(f"{rxn_name}: {e}")
                    else:
                        errors.append(f"{rxn_name}: no Reactions collection on reactor")
                else:
                    errors.append(f"{rxn_name}: unknown type '{rxn_type}'")

            except Exception as e:
                errors.append(f"{rxn_name}: {e}")

        if not configured:
            return {
                "success": False,
                "error":   "Could not configure any reactions",
                "details": errors,
                "note":    "Reaction setup via automation is limited in DWSIM. "
                           "For complex kinetics, configure reactions in the GUI, "
                           "save the flowsheet, then load it here.",
            }

        return {
            "success":     True,
            "configured":  configured,
            "errors":      errors or None,
            "note": ("Configured conversion/stoichiometry. "
                     "Call run_simulation to apply."),
        }

    # ── v5: column batch spec ─────────────────────────────────────────────────

    def set_column_specs(
        self,
        column_tag:      str,
        n_stages:        Optional[int]   = None,
        reflux_ratio:    Optional[float] = None,
        feed_stage:      Optional[int]   = None,
        condenser_type:  Optional[str]   = None,
        condenser_duty_W: Optional[float] = None,
        reboiler_duty_W:  Optional[float] = None,
        distillate_rate_mol_s: Optional[float] = None,
        bottoms_rate_mol_s:    Optional[float] = None,
        condenser_pressure_Pa: Optional[float] = None,
        reboiler_pressure_Pa:  Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Batch-set multiple distillation column specs in a single call.
        Calls set_column_property for each provided (non-None) argument.
        Returns a summary of what was set and any errors.
        """
        specs = {
            "NumberOfStages":      n_stages,
            "RefluxRatio":         reflux_ratio,
            "FeedStage":           feed_stage,
            "CondenserType":       condenser_type,
            "CondenserDuty":       condenser_duty_W,
            "ReboilerDuty":        reboiler_duty_W,
            "DistillateFlowRate":  distillate_rate_mol_s,
            "BottomsFlowRate":     bottoms_rate_mol_s,
            "CondenserPressure":   condenser_pressure_Pa,
            "ReboilerPressure":    reboiler_pressure_Pa,
        }
        applied: List[str] = []
        failed:  List[str] = []
        for prop, val in specs.items():
            if val is None:
                continue
            r = self.set_column_property(column_tag, prop, val)
            if r.get("success"):
                applied.append(f"{prop}={val}")
            else:
                failed.append(f"{prop}: {r.get('error', '?')}")

        if not applied:
            return {"success": False,
                    "error": "No specs were set",
                    "details": failed}

        return {
            "success": True,
            "column":  column_tag,
            "applied": applied,
            "failed":  failed or None,
            "note":    "Call run_simulation to apply changes.",
        }

    # ── private helpers ───────────────────────────────────────────────────────

    def _active_tag_cache(self) -> Dict[str, str]:
        if self._active_alias and self._active_alias in self._flowsheets:
            return self._flowsheets[self._active_alias].get("tag_cache", {})
        return {}

    def _rebuild_active_cache(self) -> None:
        if not self._active_alias:
            return
        coll = self._get_collection()
        if coll is None:
            return
        cache = {}
        for guid, obj in self._iter_collection(coll):
            cache[str(guid)] = _resolve_tag(obj, str(guid))
        if self._active_alias in self._flowsheets:
            self._flowsheets[self._active_alias]["tag_cache"] = cache

    def _get_collection_for(self, fs):
        if fs is None:
            return None
        for attr in ("SimulationObjects", "Objects", "GetSimulationObjects"):
            try:
                c = getattr(fs, attr)
                if callable(c): c = c()
                if c is not None: return c
            except Exception:
                pass
        return None

    def _get_collection(self):
        return self._get_collection_for(self._flowsheet)

    def _iter_collection(self, coll):
        try:
            for k in list(coll.Keys):
                try: yield k, coll[k]
                except Exception: pass
            return
        except Exception:
            pass
        try:
            for item in coll:
                yield _resolve_tag(item, "?"), item
        except Exception:
            pass

    def _find_object(self, tag: str):
        if self._flowsheet is None:
            return None
        coll = self._get_collection()
        if coll is None:
            return None
        tag_s     = tag.strip()
        tag_cache = self._active_tag_cache()

        for guid, human in tag_cache.items():
            if human.strip() == tag_s:
                try: return coll[guid]
                except Exception: pass
        try:
            obj = coll[tag]
            if obj is not None: return obj
        except Exception:
            pass
        for guid, obj in self._iter_collection(coll):
            if _resolve_tag(obj, guid).strip() == tag_s:
                return obj
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ACC-3: property package description map
# ─────────────────────────────────────────────────────────────────────────────

_PP_DESCRIPTIONS: Dict[str, str] = {
    "PENG-ROBINSON":    "Peng-Robinson cubic EOS — good for hydrocarbons & gases",
    "PR":               "Peng-Robinson cubic EOS",
    "SRK":              "Soave-Redlich-Kwong cubic EOS — good for non-polar fluids",
    "SOAVE-REDLICH-KWONG": "Soave-Redlich-Kwong cubic EOS",
    "NRTL":             "Non-Random Two-Liquid — activity coefficient model for liquid mixtures",
    "UNIQUAC":          "UNIQUAC activity coefficient model",
    "WILSON":           "Wilson activity coefficient model",
    "STEAM TABLES":     "IAPWS-IF97 steam/water tables",
    "IAPWS-IF97":       "IAPWS-IF97 steam/water tables",
    "CoolProp":         "CoolProp multi-fluid property library",
    "REFPROP":          "NIST REFPROP high-accuracy fluid properties",
    "IDEAL":            "Ideal gas / Raoult's law — for dilute/ideal mixtures only",
}

# ─────────────────────────────────────────────────────────────────────────────
# Flowsheet diagram parser (reads .dwxmz XML directly — no .NET required)
# ─────────────────────────────────────────────────────────────────────────────

import zipfile
import re as _re

_SHAPE_MAP = {
    "MaterialStreamGraphic":      "stream_material",
    "EnergyStreamGraphic":        "stream_energy",
    "HeaterGraphic":              "heater",
    "CoolerGraphic":              "cooler",
    "HeatExchangerGraphic":       "heat_exchanger",
    "MixerGraphic":               "mixer",
    "SplitterGraphic":            "splitter",
    "PumpGraphic":                "pump",
    "CompressorGraphic":          "compressor",
    "ExpanderGraphic":            "expander",
    "ValveGraphic":               "valve",
    "ShortcutColumnGraphic":      "column",
    "DistillationColumnGraphic":  "column",
    "AbsorptionColumnGraphic":    "column",
    "RefluxedAbsorberGraphic":    "column",
    "ReboiledAbsorberGraphic":    "column",
    "ConversionReactorGraphic":   "reactor",
    "PFRGraphic":                 "reactor_pfr",
    "CSTRGraphic":                "reactor_cstr",
    "EquilibriumReactorGraphic":  "reactor",
    "GibbsReactorGraphic":        "reactor",
    "FlashSeparatorGraphic":      "separator",
    "VesselGraphic":              "vessel",
    "ComponentSeparatorGraphic":  "separator",
    "FilterGraphic":              "separator",
    "OrificeGraphic":             "valve",
    "PipeSegmentGraphic":         "pipe",
    "RecycleGraphic":             "recycle",
    "AdjustGraphic":              "adjust",
    "SpecificationGraphic":       "spec",
    "CustomUOGraphic":            "custom",
    "MasterTableGraphic":         "table",
    "RectangleGraphic":           "rect_annotation",
    "TextGraphic":                "text_annotation",
}

# Skip these shape types entirely (annotations / decorators)
_SKIP_SHAPES = {"table", "rect_annotation"}


def _gx(block: str, tag: str) -> str:
    m = _re.search(rf"<{tag}>(.*?)</{tag}>", block, _re.DOTALL)
    return m.group(1).strip() if m else ""


def _argb_to_css(color: str) -> str:
    """Convert DWSIM #AARRGGBB → CSS #RRGGBB (discard alpha)."""
    c = color.strip()
    if c.startswith("#") and len(c) == 9:
        return "#" + c[3:]
    if c.startswith("#") and len(c) == 7:
        return c
    return "#888888"


def parse_flowsheet_diagram(dwxmz_path: str) -> dict:
    """
    Parse a .dwxmz file and return nodes + edges for DWSIM-accurate SVG rendering.
    Extracts actual colors, sizes, positions, rotation, and connector topology.
    """
    try:
        with zipfile.ZipFile(dwxmz_path, "r") as z:
            xml_files = [n for n in z.namelist() if n.endswith(".xml")]
            if not xml_files:
                return {"success": False, "error": "No XML found in file"}
            with z.open(xml_files[0]) as f:
                content = f.read().decode("utf-8-sig", errors="ignore")
    except Exception as e:
        return {"success": False, "error": str(e)}

    gfx_blocks = _re.findall(r"<GraphicObject>.*?</GraphicObject>", content, _re.DOTALL)

    nodes = []
    edges = []

    for block in gfx_blocks:
        type_full  = _gx(block, "Type")
        short_type = type_full.split(".")[-1] if type_full else ""
        shape      = _SHAPE_MAP.get(short_type, "unknown")

        if shape in _SKIP_SHAPES:
            continue
        if shape == "unknown" and not _gx(block, "Tag"):
            continue

        obj_id    = _gx(block, "Name")
        tag       = _gx(block, "Tag") or obj_id
        desc      = _gx(block, "Description")
        calc      = _gx(block, "Calculated").lower() == "true"
        active    = _gx(block, "Active").lower() != "false"
        x         = float(_gx(block, "X") or 0)
        y         = float(_gx(block, "Y") or 0)
        w         = float(_gx(block, "Width") or 20)
        h         = float(_gx(block, "Height") or 20)
        rotation  = float(_gx(block, "Rotation") or 0)
        flipped_h = _gx(block, "FlippedH").lower() == "true"
        flipped_v = _gx(block, "FlippedV").lower() == "true"

        # Text annotations — extract text content
        text_content = ""
        if shape == "text_annotation":
            text_content = _gx(block, "Text") or tag

        # Colors — DWSIM stores #AARRGGBB
        fill_color   = _argb_to_css(_gx(block, "FillColor")   or "#ffd3d3d3")
        line_color   = _argb_to_css(_gx(block, "LineColor")   or "#ff000000")
        grad_color1  = _argb_to_css(_gx(block, "GradientColor1") or "#ffd3d3d3")
        grad_color2  = _argb_to_css(_gx(block, "GradientColor2") or "#ffffffff")
        gradient     = _gx(block, "GradientMode").lower() == "true"
        line_width   = float(_gx(block, "LineWidth") or 1)
        font_size    = float(_gx(block, "FontSize") or 10)
        draw_label   = _gx(block, "DrawLabel").lower() != "false"

        node = dict(
            id=obj_id, tag=tag, type=short_type, shape=shape,
            x=x, y=y, w=w, h=h,
            calculated=calc, active=active,
            description=desc,
            rotation=rotation, flipped_h=flipped_h, flipped_v=flipped_v,
            fill=fill_color, stroke=line_color,
            grad1=grad_color1, grad2=grad_color2,
            gradient=gradient, line_width=line_width,
            font_size=font_size, draw_label=draw_label,
            text=text_content,
        )
        nodes.append(node)

        # Output connectors → edges (material and energy)
        out_conns = _re.findall(
            r'<Connector\s[^>]*IsAttached="true"[^>]*ConnType="Con(?:Out|En)"'
            r'[^>]*AttachedToObjID="([^"]+)"[^>]*AttachedToEnergyConn="([^"]+)"',
            block
        )
        for dest_id, energy_str in out_conns:
            edges.append({
                "from": obj_id,
                "to":   dest_id,
                "energy": energy_str.lower() == "true",
            })

    # Remove edges where source or destination node was skipped
    valid_ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["from"] in valid_ids and e["to"] in valid_ids]

    # Canvas bounds
    if nodes:
        xs = [n["x"] for n in nodes]
        ys = [n["y"] for n in nodes]
        bounds = {
            "min_x": min(xs),       "min_y": min(ys),
            "max_x": max(n["x"] + n["w"] for n in nodes) + 40,
            "max_y": max(n["y"] + n["h"] for n in nodes) + 40,
        }
    else:
        bounds = {"min_x": 0, "min_y": 0, "max_x": 800, "max_y": 600}

    return {"success": True, "nodes": nodes, "edges": edges, "bounds": bounds}


# Backwards-compatible alias
DWSIMBridge = DWSIMBridgeV2
