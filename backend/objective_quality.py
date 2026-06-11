"""
objective_quality.py
─────────────────────
Advisory "is this objective engineering-meaningful?" gate.

The existing gates catch a BROKEN objective (unreadable, or insensitive to every
variable). They do NOT catch a TRIVIAL / HOLLOW objective — one that optimises
perfectly but means nothing. The canonical example (the LLE run): "maximise
EXTRACTED_PRODUCT.mass_flow" by varying FEED.mass_flow. It "succeeds" by simply
pegging the feed to its upper bound — more mass in → more mass out. The number
goes up; the engineering says nothing.

This module flags that pattern and suggests an INTENSIVE objective (purity,
recovery, yield, mole/mass fraction) instead. It is ADVISORY — it never blocks a
run, because a user may legitimately want a throughput study. It just makes the
hollow case loud instead of silent.

Pure / no DWSIM — fully unit-testable.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# Properties that scale with throughput (extensive) — maximising/minimising one
# is trivially achieved by scaling a feed.
_EXTENSIVE = (
    "mass_flow", "massflow", "molar_flow", "molarflow", "mole_flow", "moleflow",
    "volume_flow", "volumetric_flow", "vol_flow", "volflow",
    "duty", "heat_duty", "heatduty", "power", "energy", "work", "heat_load",
)
_EXTENSIVE_SUFFIX = ("_kgh", "_kgs", "_kmolh", "_mols", "_kw", "_mw", "_w")

# Intensive / normalised properties — these ARE meaningful optimisation targets.
_INTENSIVE = (
    "fraction", "purity", "recovery", "yield", "conversion", "selectivity",
    "mole_frac", "mass_frac", "molefrac", "massfrac", "ppm", "ppb",
    "concentration", "efficiency", "ratio",
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_extensive(prop: str) -> bool:
    p = (prop or "").lower()
    n = _norm(prop)
    if any(_norm(k) in n for k in _EXTENSIVE):
        return True
    if p.endswith(_EXTENSIVE_SUFFIX):
        return True
    # bare "flow" (but not "flow_fraction" etc., handled by intensive check first)
    return "flow" in n and not _is_intensive(prop)


def _is_intensive(prop: str) -> bool:
    n = _norm(prop)
    return any(_norm(k) in n for k in _INTENSIVE)


def assess_objective(objective: Dict[str, Any],
                     variables: List[Dict[str, Any]],
                     minimize: bool) -> Dict[str, Any]:
    """Assess whether an objective is likely engineering-meaningful.

    Returns {assessed, meaningful, severity('ok'|'low'|'high'), warning,
             suggestion, flags}.
    """
    out = {"assessed": True, "meaningful": True, "severity": "ok",
           "warning": "", "suggestion": "", "flags": []}

    if not objective or objective.get("type") not in ("variable", None) and \
            objective.get("type") != "variable":
        # Expression objectives are user-crafted composites — assume intentional.
        if objective.get("type") == "expression":
            out["flags"].append("expression_objective")
            return out

    obj_prop = objective.get("property", "")
    obj_tag = objective.get("tag", "")
    obj_name = f"{obj_tag}.{obj_prop}" if obj_tag else obj_prop
    direction = "minimise" if minimize else "maximise"

    obj_extensive = _is_extensive(obj_prop)
    obj_intensive = _is_intensive(obj_prop)

    ext_var_specs = [v for v in (variables or [])
                     if _is_extensive(v.get("property", ""))]
    ext_vars = [f"{v.get('tag','')}.{v.get('property','')}" for v in ext_var_specs]

    # ── Flag 1: objective IS one of the decision variables ──────────────────
    for v in (variables or []):
        if (v.get("tag") == obj_tag and v.get("property") == obj_prop):
            out.update({
                "meaningful": False, "severity": "high",
                "warning": (f"The objective {obj_name} is also a decision "
                            f"variable — {direction} it just drives that "
                            f"variable to its bound. The result is tautological."),
                "suggestion": ("Optimise a downstream OUTCOME (a product purity, "
                               "recovery or yield) that depends on this variable, "
                               "not the variable itself."),
            })
            out["flags"].append("objective_is_decision_variable")
            return out

    # ── Flag 2: extensive objective + extensive decision variable ───────────
    if obj_extensive and ext_vars:
        out.update({
            "meaningful": False, "severity": "high",
            "warning": (
                f"{direction.capitalize()[:-1]}ing the extensive quantity "
                f"{obj_name} while a throughput variable "
                f"({', '.join(ext_vars)}) is free is likely HOLLOW: the optimum "
                f"just scales the feed (more in → more out / less in → less out) "
                f"and pegs that variable to its bound."),
            "suggestion": (
                f"Prefer an INTENSIVE objective in {obj_tag or 'the product'} — "
                f"e.g. a component recovery, purity, mole/mass fraction or yield "
                f"— or hold throughput fixed and optimise a ratio (product per "
                f"unit feed/energy)."),
        })
        out["flags"].append("extensive_objective_with_throughput_variable")
        # The throughput variables that make this objective hollow. The
        # orchestrator can hold these fixed so the optimiser finds a real optimum
        # instead of trivially scaling the feed.
        out["throughput_vars"] = ext_vars
        out["throughput_var_specs"] = ext_var_specs
        return out

    # ── Flag 3 (soft): extensive objective, no intensive signal ─────────────
    if obj_extensive and not obj_intensive:
        out.update({
            "meaningful": True, "severity": "low",
            "warning": (f"{obj_name} is an extensive (throughput-scaling) "
                        f"quantity. Make sure that is genuinely the goal and not "
                        f"a stand-in for efficiency/purity."),
            "suggestion": ("If you care about separation/quality, optimise a "
                           "recovery, purity or yield instead."),
        })
        out["flags"].append("extensive_objective")
        return out

    return out
