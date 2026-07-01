"""
thermo_preflight.py
───────────────────
A deterministic pre-design thermodynamic-analysis gate — the auxiliary
"Stage 1: Thermodynamic Analysis" of Tian et al. (arXiv:2601.06776, 2026), which
validates phase-equilibrium / property-package appropriateness BEFORE the main
design workflow so that a wrong property-package start cannot poison every
subsequent iteration.

The project already has the thermodynamic *intelligence* (thermo_models:
classify / recommend / candidate_packages / thermodynamic_intelligence) and a
live binary phase envelope (dwsim_bridge_v2.calculate_phase_envelope for T-x-y).
What was missing is a single gate that — given the compounds, the requested
property package, and the feed conditions — decides whether that package is
theory-appropriate, flags the classic azeotrope error (a cubic equation of state
chosen for a polar low-pressure mixture that forms an azeotrope), and recommends
the fix, so the agent never silently starts from the wrong thermodynamic model.

`preflight_thermo` is a PURE function of (compounds, requested_pp, P, T) — no
DWSIM, no LLM — and is fully unit-tested. `build_flowsheet_atomic` calls it and
attaches the verdict as result["thermo_preflight"] (advisory; it never blocks a
build). The actual T-x-y computation, when wanted, is the existing
calculate_phase_envelope live capability this gate points to.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from thermo_models import (classify, thermodynamic_intelligence,  # noqa: F401
                           resolve_to_dwsim, is_available)

# Activity-coefficient model name fragments (the family that captures azeotropy).
_ACTIVITY = ("NRTL", "UNIQUAC", "WILSON", "UNIFAC")


def _pp_family(dwsim_name: Optional[str]) -> Optional[str]:
    """Map a DWSIM package name to its thermodynamic family."""
    if not dwsim_name:
        return None
    n = dwsim_name.upper()
    if any(k in n for k in _ACTIVITY):
        return "activity_coefficient"
    if "STEAM" in n or "IAPWS" in n:
        return "steam"
    if "ELECTROLYTE" in n:
        return "electrolyte"
    return "equation_of_state"          # cubic / SAFT / GERG / CoolProp / Lee-Kesler


def _needed_family(flags: Dict[str, Any]) -> str:
    """The family a correct VLE description requires for this system."""
    if flags.get("water_only"):
        return "steam"
    if flags.get("electrolyte"):
        return "electrolyte"
    if (flags.get("polar") and not flags.get("hydrocarbon")
            and float(flags.get("pressure_bar", 1.0)) < 10):
        return "activity_coefficient"
    return "equation_of_state"


def preflight_thermo(compounds: List[str], requested_pp: Optional[str] = None,
                     pressure_bar: float = 1.01325, temperature_C: float = 25.0,
                     have_binary_data: bool = False) -> Dict[str, Any]:
    """Decide whether `requested_pp` is theory-appropriate for `compounds` at the
    given conditions, flag azeotrope risk, and recommend the fix. Pure; never
    raises on well-formed input."""
    compounds = [c for c in (compounds or []) if c]
    intel = thermodynamic_intelligence(compounds, pressure_bar, temperature_C,
                                       have_binary_data)
    flags = intel["classification"]
    recommended = intel["recommended_pp"]
    needed = _needed_family(flags)
    n_comp = len(compounds)
    azeotrope_risk = bool(flags.get("polar") and not flags.get("hydrocarbon")
                          and pressure_bar < 10 and n_comp >= 2
                          and not flags.get("water_only"))

    warnings: List[str] = []
    mismatch = False
    suggested: Optional[str] = None
    requested_family: Optional[str] = None
    instantiable: Optional[bool] = None

    if requested_pp:
        res = resolve_to_dwsim(requested_pp)
        canonical = res.get("dwsim_name")
        instantiable = (res.get("matched_on") != "default"
                        and is_available(canonical or ""))
        requested_family = _pp_family(canonical)
        if not instantiable:
            warnings.append(
                f"Requested package '{requested_pp}' is not a DWSIM package "
                f"(would default to '{canonical}'). Specify a valid package — "
                f"recommended: '{recommended}'.")
        if needed == "activity_coefficient" and requested_family == "equation_of_state":
            mismatch = True
            suggested = recommended
            warnings.append(
                f"'{requested_pp}' is a cubic equation of state, but {compounds} "
                f"is a polar, low-pressure mixture likely to form an azeotrope — a "
                f"cubic EOS mis-predicts the vapour-liquid equilibrium here. Use "
                f"'{recommended}' (an activity-coefficient model) and confirm with "
                f"a binary T-x-y check.")
        elif needed == "steam" and requested_family == "equation_of_state":
            warnings.append(
                f"For pure water, '{recommended}' (steam tables) is more accurate "
                f"than '{requested_pp}', though the latter will still solve.")

    if azeotrope_risk and not mismatch:
        warnings.append(
            f"Azeotrope risk: {compounds} is polar and below 10 bar. Run a binary "
            f"T-x-y phase-equilibrium check (calculate_phase_envelope); an "
            f"activity-coefficient model ('{recommended}') is required for "
            f"correct VLE.")

    severity = "mismatch" if mismatch else ("warning" if warnings else "ok")
    if mismatch:
        advice = f"Property-package mismatch — switch to '{suggested}' before building."
    elif azeotrope_risk:
        advice = "Confirm the azeotrope/VLE (T-x-y) before relying on results."
    else:
        advice = f"Thermodynamics appropriate ({requested_pp or recommended})."

    return {
        "success": True,
        "ok": not mismatch,
        "severity": severity,
        "system_flags": flags,
        "requested_pp": requested_pp,
        "requested_family": requested_family,
        "requested_instantiable": instantiable,
        "needed_family": needed,
        "recommended_pp": recommended,
        "suggested_pp": suggested,
        "azeotrope_risk": azeotrope_risk,
        "mismatch": mismatch,
        "warnings": warnings,
        "uncertainty_candidates": intel.get("uncertainty_candidates", []),
        "fidelity_statement": intel.get("fidelity_statement", ""),
        "advice": advice,
    }
