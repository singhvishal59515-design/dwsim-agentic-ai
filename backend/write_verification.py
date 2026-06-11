"""
write_verification.py — Eliminate silent failures.

The single most dangerous class of bug in an autonomous simulator agent is
a tool that reports success=True while DWSIM's state did NOT change. This
module provides read-back verification: after any state-changing write, it
reads the property back and confirms it actually took the intended value.

If the write did not take effect, the operation is reported as a FAILURE
(verified=False), not a success. This makes the agent able to *distrust its
own success=True* and treat unverified writes as failures — exactly the
discipline required for unattended operation.

Usage:
    from write_verification import verify_property_write
    r = verify_property_write(bridge, "Feed", "temperature_C", 45.0, "C")
    # r = {verified: True, read_back: 45.0, ...} or
    #     {verified: False, read_back: 10.0, expected: 45.0, drift: 35.0, ...}

Design notes:
  • Verification reads via the SAME reflection path the agent uses, so it
    reflects DWSIM's actual live state, not a cache.
  • Unit-aware: a write of 45 °C is verified against the read-back in the
    same unit (handles K↔C↔bar↔Pa conversions for the common cases).
  • Tolerance is relative (default 1%) with an absolute floor, because
    floating-point + solver round-off mean exact equality is wrong.
  • If the property genuinely cannot be read back (some derived outputs),
    the result is 'unverifiable' (not a hard failure) so we don't block on
    properties that are legitimately read-only post-write.
"""

from __future__ import annotations
import math
from typing import Any, Dict, Optional, Tuple

# Properties that are inputs we expect to read straight back after a write.
# (Outlet temperatures on heaters are a spec input → readable; phase
#  compositions are computed → may not round-trip exactly.)
_DEFAULT_REL_TOL = 0.01      # 1% relative
_DEFAULT_ABS_FLOOR = 1e-6    # absolute floor for near-zero values


def _to_kelvin(value: float, unit: str) -> float:
    u = (unit or "").lower().strip()
    if u in ("c", "celsius", "°c", "degc"):
        return value + 273.15
    if u in ("f", "fahrenheit", "°f"):
        return (value - 32.0) * 5.0 / 9.0 + 273.15
    return value  # already K (or unknown → assume base)


def _normalise_for_compare(prop: str, value: float, unit: str) -> Tuple[float, str]:
    """Normalise a (value, unit) pair to a canonical unit for comparison so a
    write of 45°C compares correctly to a read-back of 318.15 K."""
    p = prop.lower()
    u = (unit or "").lower()
    if "temperature" in p or p.endswith(("_c", "_k")) and "temp" in p:
        return _to_kelvin(value, unit), "K"
    if "temperature" in p:
        return _to_kelvin(value, unit), "K"
    if "pressure" in p:
        if u in ("bar",):       return value * 1e5, "Pa"
        if u in ("kpa",):       return value * 1e3, "Pa"
        if u in ("atm",):       return value * 101325.0, "Pa"
        return value, "Pa"
    if "mass_flow" in p or "massflow" in p:
        if u in ("kg/h", "kgh", "kg_h"):  return value / 3600.0, "kg/s"
        return value, "kg/s"
    return value, u or "raw"


def _read_property(bridge, tag: str, prop: str) -> Optional[float]:
    """Read a numeric property back from DWSIM via the reflection bridge,
    falling back to the bridge's own readers. Returns None if unreadable."""
    # 1. Reflection (reaches unit-op + nested phase properties)
    try:
        from dwsim_reflection import reflect_get_set
        # Map common property aliases to the reflection path DWSIM exposes
        path = _reflection_path_for(tag, prop)
        r = reflect_get_set(bridge, tag, path)
        if isinstance(r, dict) and r.get("success"):
            return float(r["value"])
    except Exception:
        pass
    # 2. Bridge stream reader (returns a dict of derived props)
    try:
        sp = bridge.get_stream_properties(tag)
        if isinstance(sp, dict) and sp.get("success"):
            props = sp.get("properties", {})
            for key in (prop, prop.lower(), prop + "_C", prop + "_K",
                        prop + "_bar", prop + "_kgh", prop + "_kg_s"):
                if key in props and props[key] is not None:
                    return float(props[key])
    except Exception:
        pass
    return None


def _reflection_path_for(tag: str, prop: str) -> str:
    """Translate a friendly property name to the DWSIM reflection path.

    Unit-op SPEC properties (a pump's OutletPressure, a heater's
    OutletTemperature, …) must be read from the unit-op's OWN property — NOT the
    stream-phase path. Previously every name containing 'pressure' (including
    'OutletPressure') was mapped to Phases[0].Properties.pressure, so a pump's
    pressure spec verified against a stream phase that doesn't exist on the unit.
    """
    pn = prop.lower().replace("_", "").replace(" ", "")
    _UNITOP_SPEC = {
        "outletpressure": "Pout", "pout": "Pout", "pressureout": "Pout",
        "outlettemperature": "OutletTemperature", "outlettemp": "OutletTemperature",
        "deltap": "DeltaP", "pressuredrop": "DeltaP",
        "deltat": "DeltaT", "heatduty": "DeltaQ", "duty": "DeltaQ", "deltaq": "DeltaQ",
    }
    if pn in _UNITOP_SPEC:
        return _UNITOP_SPEC[pn]
    p = prop.lower()
    if "temperature" in p:
        return "Phases[0].Properties.temperature"   # Kelvin
    if "pressure" in p:
        return "Phases[0].Properties.pressure"       # Pa
    if "mass_flow" in p or "massflow" in p:
        return "Phases[0].Properties.massflow"       # kg/s
    # Unit-op duties / outlet specs read directly
    return prop


def verify_property_write(
    bridge,
    tag: str,
    prop: str,
    expected_value: float,
    unit: str = "",
    rel_tol: float = _DEFAULT_REL_TOL,
) -> Dict[str, Any]:
    """Read a property back and confirm it took the expected value.

    Returns:
      {verified, status, read_back, expected, drift, ...}
      status ∈ {'verified', 'mismatch', 'unverifiable'}
    """
    exp_norm, canon_unit = _normalise_for_compare(prop, float(expected_value), unit)
    read = _read_property(bridge, tag, prop)
    if read is None:
        return {
            "verified": None,          # unknown — not a hard fail
            "status": "unverifiable",
            "tag": tag, "property": prop,
            "expected": expected_value, "unit": unit,
            "note": "Property could not be read back to verify "
                    "(may be a derived/read-only output).",
        }
    # Efficiency is stored by DWSIM in PERCENT (e.g. 75.0) but agents naturally
    # write the FRACTION (0.75). Normalise both to [0,1] so 0.75 verifies against
    # a 75.0 read-back instead of false-failing. (Same class of representation
    # mismatch as the pump-pressure read path.)
    if "efficiency" in prop.lower():
        en = exp_norm / 100.0 if exp_norm > 1.5 else exp_norm
        rn = read / 100.0 if read > 1.5 else read
        verified = abs(en - rn) <= max(abs(en) * rel_tol, 1e-3)
        return {
            "verified": verified,
            "status": "verified" if verified else "mismatch",
            "tag": tag, "property": prop, "expected": expected_value,
            "unit": unit, "expected_canonical": round(en, 6),
            "read_back": round(read, 6), "drift": round(abs(en - rn), 6),
            "tolerance": round(max(abs(en) * rel_tol, 1e-3), 6),
            "note": ("Write confirmed against live DWSIM state (efficiency "
                     "normalised fraction↔percent)." if verified else
                     "WRITE DID NOT TAKE EFFECT — efficiency mismatch."),
        }
    # Read-back may be in canonical unit (K/Pa/kg-s) already
    read_norm, _ = _normalise_for_compare(prop, read, canon_unit) \
        if canon_unit not in ("raw",) else (read, canon_unit)
    # If the read came from reflection it's already canonical (K/Pa/kg-s);
    # compare directly in canonical space.
    drift = abs(read - exp_norm)
    tol = max(abs(exp_norm) * rel_tol, _DEFAULT_ABS_FLOOR)
    # Try both: read might already be canonical (reflection) OR friendly
    ok_canonical = drift <= tol
    drift_friendly = abs(read - float(expected_value))
    tol_friendly = max(abs(float(expected_value)) * rel_tol, _DEFAULT_ABS_FLOOR)
    ok_friendly = drift_friendly <= tol_friendly
    verified = ok_canonical or ok_friendly
    return {
        "verified": verified,
        "status": "verified" if verified else "mismatch",
        "tag": tag, "property": prop,
        "expected": expected_value, "unit": unit,
        "expected_canonical": round(exp_norm, 6),
        "read_back": round(read, 6),
        "drift": round(min(drift, drift_friendly), 6),
        "tolerance": round(min(tol, tol_friendly), 6),
        "note": ("Write confirmed against live DWSIM state."
                 if verified else
                 "WRITE DID NOT TAKE EFFECT — DWSIM state does not match "
                 "the intended value. Treating as a failed write."),
    }


def verified_set_stream_property(
    bridge, tag: str, prop: str, value: float, unit: str = "",
) -> Dict[str, Any]:
    """Set a stream property AND verify it took effect. Returns the bridge
    set-result augmented with a 'verification' block; success is downgraded
    to False if the verification shows a mismatch."""
    set_r = bridge.set_stream_property(tag, prop, value, unit)
    if not isinstance(set_r, dict) or not set_r.get("success"):
        return set_r  # set itself failed — already reported
    v = verify_property_write(bridge, tag, prop, value, unit)
    out = dict(set_r)
    out["verification"] = v
    if v["status"] == "mismatch":
        out["success"] = False
        out["error"] = v["note"]
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_set_unit_op_property(
    bridge, tag: str, prop: str, value: float, unit: str = "",
) -> Dict[str, Any]:
    """Set a unit-op property AND verify it took effect."""
    set_r = bridge.set_unit_op_property(tag, prop, value, unit)
    if not isinstance(set_r, dict) or not set_r.get("success"):
        return set_r
    # Determine the unit for verification: prefer the caller-supplied unit, else
    # infer from the property-name suffix.
    if not unit:
        pl = prop.lower()
        if pl.endswith("_c") or "temperature_c" in pl:  unit = "C"
        elif pl.endswith("_k"):                          unit = "K"
        elif "bar" in pl:                                unit = "bar"
    v = verify_property_write(bridge, tag, prop, value, unit)
    out = dict(set_r)
    out["verification"] = v
    if v["status"] == "mismatch":
        out["success"] = False
        out["error"] = v["note"]
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_add_object(bridge, tag: str, obj_type: str) -> Dict[str, Any]:
    """Add an object AND verify it actually exists in the flowsheet afterward.
    Catches the silent failure where add_object reports success but the object
    was not created."""
    add_r = bridge.add_object(tag=tag, type=obj_type)
    if not isinstance(add_r, dict) or not add_r.get("success"):
        return add_r
    # Verify by re-querying the object list
    exists = False
    try:
        r = bridge.list_simulation_objects()
        if isinstance(r, dict) and r.get("success"):
            for o in r.get("objects", []):
                if str(o.get("tag", "")).lower() == tag.lower():
                    exists = True
                    break
    except Exception:
        exists = None  # can't verify → don't hard-fail
    out = dict(add_r)
    out["verification"] = {"verified": exists,
                            "status": ("verified" if exists else
                                       "mismatch" if exists is False else
                                       "unverifiable")}
    if exists is False:
        out["success"] = False
        out["error"] = (f"add_object reported success but object '{tag}' is "
                        f"not present in the flowsheet — write not verified.")
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_set_stream_composition(bridge, tag: str,
                                     composition: Dict[str, float]) -> Dict[str, Any]:
    """Set a stream composition AND verify the mole fractions took effect."""
    set_r = bridge.set_stream_composition(tag, composition)
    if not isinstance(set_r, dict) or not set_r.get("success"):
        return set_r
    # Read back mole fractions and compare
    verified = None
    detail = ""
    try:
        sp = bridge.get_stream_properties(tag)
        if isinstance(sp, dict) and sp.get("success"):
            mf = sp.get("properties", {}).get("mole_fractions", {})
            if isinstance(mf, dict) and mf:
                # Normalise the requested composition
                total = sum(float(v) for v in composition.values()) or 1.0
                ok = True
                for comp, want in composition.items():
                    want_norm = float(want) / total
                    got = float(mf.get(comp, 0.0))
                    if abs(got - want_norm) > 0.02:   # 2% tolerance
                        ok = False
                        detail = f"{comp}: wanted {want_norm:.3f}, got {got:.3f}"
                        break
                verified = ok
    except Exception:
        verified = None
    out = dict(set_r)
    out["verification"] = {"verified": verified,
                            "status": ("verified" if verified else
                                       "mismatch" if verified is False else
                                       "unverifiable"),
                            "detail": detail}
    if verified is False:
        out["success"] = False
        out["error"] = (f"Composition write not verified — read-back mismatch "
                        f"({detail}).")
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


# ─────────────────────────────────────────────────────────────────────────
#  Generic read-back helpers + wrappers for the REMAINING state-changing
#  tools (columns, reactors, energy streams, deletes, column specs, binary
#  interaction parameters, flash specs, heat-exchanger config, disconnects).
#
#  Goal: NO state-changing tool returns a bare success=True. Either it is
#  verified against live DWSIM state (and a mismatch is downgraded to a hard
#  failure), or it returns an explicit status='unverifiable' so the caller
#  knows the effect was not confirmed. This closes the "silent failure" gap
#  across the whole mutation surface, not just the first four tools.
# ─────────────────────────────────────────────────────────────────────────

def _canon_key(s: str) -> str:
    return str(s).lower().replace("_", "").replace(" ", "")


def _read_via_getter(bridge, getter_name: str, tag: str,
                     prop: str) -> Optional[float]:
    """Call bridge.<getter_name>(tag) and pull `prop` back as a float, matching
    keys case/underscore-insensitively. Returns None if unreadable."""
    try:
        fn = getattr(bridge, getter_name, None)
        if fn is None:
            return None
        r = fn(tag)
        if not (isinstance(r, dict) and r.get("success")):
            return None
        props = r.get("properties")
        if not isinstance(props, dict):
            props = {k: v for k, v in r.items()
                     if k not in ("success", "error")}
        want = _canon_key(prop)
        for k, v in props.items():
            if _canon_key(k) == want and v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
    except Exception:
        return None
    return None


def _finish_numeric(set_r: Dict[str, Any], read: Optional[float],
                    expected: float, prop: str = "",
                    rel_tol: float = _DEFAULT_REL_TOL) -> Dict[str, Any]:
    """Attach a verification verdict to a numeric write; downgrade to failure
    on a confirmed mismatch; leave success intact but flagged when the value
    genuinely cannot be read back."""
    out = dict(set_r)
    if read is None:
        out["verification"] = {
            "verified": None, "status": "unverifiable", "property": prop,
            "expected": expected,
            "note": "Write could not be read back to confirm — effect on DWSIM "
                    "state is unconfirmed."}
        return out
    drift = abs(read - float(expected))
    tol = max(abs(float(expected)) * rel_tol, _DEFAULT_ABS_FLOOR)
    ok = drift <= tol
    out["verification"] = {
        "verified": ok, "status": "verified" if ok else "mismatch",
        "property": prop, "expected": expected,
        "read_back": round(read, 6), "drift": round(drift, 6),
        "tolerance": round(tol, 6),
        "note": ("Write confirmed against live DWSIM state." if ok else
                 "WRITE DID NOT TAKE EFFECT — DWSIM state does not match the "
                 "intended value. Treating as a failed write.")}
    if not ok:
        out["success"] = False
        out["error"] = (f"Write not verified: wrote {expected} to "
                        f"{prop or 'property'}, read back {round(read, 6)}.")
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_set_column_property(bridge, tag: str, prop: str,
                                 value: float) -> Dict[str, Any]:
    set_r = bridge.set_column_property(tag, prop, value)
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    read = _read_via_getter(bridge, "get_column_properties", tag, prop)
    return _finish_numeric(set_r, read, value, prop)


def verified_set_reactor_property(bridge, tag: str, prop: str,
                                  value: float) -> Dict[str, Any]:
    set_r = bridge.set_reactor_property(tag, prop, value)
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    read = _read_via_getter(bridge, "get_reactor_properties", tag, prop)
    return _finish_numeric(set_r, read, value, prop)


def verified_set_energy_stream(bridge, tag: str, duty_W: float) -> Dict[str, Any]:
    set_r = bridge.set_energy_stream(tag, float(duty_W))
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    read = None
    try:
        r = bridge.get_energy_stream(tag)
        if isinstance(r, dict) and r.get("success"):
            src = r.get("properties") if isinstance(r.get("properties"), dict) else r
            for k in ("duty_W", "duty", "power_W", "power", "heat_duty_W",
                      "value"):
                if src.get(k) is not None:
                    read = float(src[k]); break
    except Exception:
        read = None
    return _finish_numeric(set_r, read, float(duty_W), "duty_W")


def verified_set_column_specs(bridge, tag: str, **kwargs) -> Dict[str, Any]:
    """Set one or more column specs and verify each numeric one read-back."""
    set_r = bridge.set_column_specs(tag, **kwargs)
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    checks = {}
    worst = None
    for key, val in kwargs.items():
        try:
            fv = float(val)
        except (TypeError, ValueError):
            continue
        read = _read_via_getter(bridge, "get_column_properties", tag, key)
        if read is None:
            checks[key] = {"status": "unverifiable"}
            continue
        drift = abs(read - fv)
        tol = max(abs(fv) * _DEFAULT_REL_TOL, _DEFAULT_ABS_FLOOR)
        ok = drift <= tol
        checks[key] = {"status": "verified" if ok else "mismatch",
                       "read_back": round(read, 6), "expected": fv}
        if not ok:
            worst = key
    out = dict(set_r)
    out["verification"] = {"per_spec": checks,
                           "status": "mismatch" if worst else "verified"}
    if worst:
        out["success"] = False
        out["error"] = (f"Column spec '{worst}' not verified "
                        f"({checks[worst]}).")
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_set_binary_interaction_parameters(bridge, compound_1: str,
                                               compound_2: str,
                                               **params) -> Dict[str, Any]:
    set_r = bridge.set_binary_interaction_parameters(compound_1, compound_2,
                                                     **params)
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    read_ok = None
    detail = ""
    try:
        r = bridge.get_binary_interaction_parameters(compound_1, compound_2)
        if isinstance(r, dict) and r.get("success"):
            src = r.get("parameters") if isinstance(r.get("parameters"), dict) else r
            read_ok = True
            for k, v in params.items():
                try:
                    want = float(v)
                except (TypeError, ValueError):
                    continue
                got = src.get(k)
                if got is None or abs(float(got) - want) > max(abs(want) * 0.02, 1e-6):
                    read_ok = False
                    detail = f"{k}: wanted {want}, got {got}"
                    break
    except Exception:
        read_ok = None
    out = dict(set_r)
    out["verification"] = {"verified": read_ok,
                           "status": ("verified" if read_ok else
                                      "mismatch" if read_ok is False else
                                      "unverifiable"), "detail": detail}
    if read_ok is False:
        out["success"] = False
        out["error"] = f"Binary interaction parameter not verified ({detail})."
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_delete_object(bridge, tag: str) -> Dict[str, Any]:
    """Delete an object AND verify it is actually gone from the flowsheet."""
    del_r = bridge.delete_object(tag)
    if not (isinstance(del_r, dict) and del_r.get("success")):
        return del_r
    gone = None
    try:
        r = bridge.list_simulation_objects()
        if isinstance(r, dict) and r.get("success"):
            gone = all(str(o.get("tag", "")).lower() != tag.lower()
                       for o in r.get("objects", []))
    except Exception:
        gone = None
    out = dict(del_r)
    out["verification"] = {"verified": gone,
                           "status": ("verified" if gone else
                                      "mismatch" if gone is False else
                                      "unverifiable")}
    if gone is False:
        out["success"] = False
        out["error"] = (f"delete_object reported success but '{tag}' is still "
                        f"present in the flowsheet — delete not verified.")
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out


def verified_generic(bridge, set_callable, *, describe: str,
                     verify_callable=None) -> Dict[str, Any]:
    """Run a state-changing call that we cannot numerically read back, but
    still attach an explicit verification verdict so the agent never sees a
    bare success=True on a mutation.

    verify_callable() — optional; returns True/False/None for verified/
    mismatch/unverifiable. When absent, the result is 'unverifiable' with a
    clear note that the effect was not independently confirmed."""
    set_r = set_callable()
    if not (isinstance(set_r, dict) and set_r.get("success")):
        return set_r
    verdict = None
    if verify_callable is not None:
        try:
            verdict = verify_callable()
        except Exception:
            verdict = None
    out = dict(set_r)
    out["verification"] = {
        "verified": verdict,
        "status": ("verified" if verdict else
                   "mismatch" if verdict is False else "unverifiable"),
        "note": (f"{describe}: effect not independently read back; "
                 "treat as unconfirmed." if verdict is None else describe)}
    if verdict is False:
        out["success"] = False
        out["error"] = f"{describe}: write not verified."
        out["error_code"] = "WRITE_NOT_VERIFIED"
    return out
