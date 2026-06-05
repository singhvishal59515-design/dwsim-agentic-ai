"""
dwsim_troubleshooter.py
────────────────────────
Deep DWSIM-specific troubleshooting engine. Diagnoses convergence failures,
configuration errors, and numerical issues using structured expert knowledge.

Functions:
  diagnose(symptoms, flowsheet_state)  — ranked root-cause analysis
  fix_plan(cause_code)                  — step-by-step fix procedure
  convergence_guide(unit_type)          — unit-specific convergence settings
  numerical_settings_advisor(problem)   — solver parameter recommendations
  error_decoder(error_message)          — decode DWSIM error text to meaning

All functions work without an LLM. The agent calls these before giving up.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════════════════════
# DWSIM error pattern → cause → fix database
# ══════════════════════════════════════════════════════════════════════════════

ERROR_PATTERNS = [
    {
        "pattern": ["max iterations", "maximum iterations", "maxiter"],
        "cause": "CONVERGENCE_MAX_ITER",
        "severity": "high",
        "short": "Solver hit iteration limit before converging",
        "explanation": (
            "The recycle convergence loop (Wegstein/Broyden/Direct) exhausted "
            "its iteration budget without the tear stream compositions closing "
            "to the tolerance. Common causes: wrong initial guess, oscillatory "
            "system (negative feedback), or tolerance too tight."
        ),
        "fixes": [
            "1. Increase MaxIterations (Flowsheet Settings → Convergence → Max Iterations; default 100, try 300)",
            "2. Switch convergence method: Direct → Wegstein → Broyden (in order of robustness)",
            "3. Loosen tolerance: 0.0001 → 0.001 (trade accuracy for speed)",
            "4. Improve initial guess: set tear stream conditions to expected converged values",
            "5. Remove recycle and converge once open-loop, then add recycle back",
            "6. Check for feedback instability: plot residual vs iteration — if oscillating, use Wegstein with acceleration factor 0.2",
        ],
        "dwsim_setting": "Flowsheet → Options → Solvers → Convergence Method",
    },
    {
        "pattern": ["flash error", "flash convergence", "phase equilibrium",
                     "vle error", "liquid fraction"],
        "cause": "FLASH_CONVERGENCE",
        "severity": "high",
        "short": "Phase-equilibrium flash calculation failed",
        "explanation": (
            "The flash algorithm (PT, PH, PS, or PV) failed to find a valid "
            "phase split. Common causes: stream conditions outside the "
            "property-package's valid range, wrong flash specification, or "
            "property-package mismatch (using NRTL for hydrocarbons at high P)."
        ),
        "fixes": [
            "1. Check stream T and P are within PP validity range",
            "2. Check the property package suits the compound mixture (use pp_validator)",
            "3. Change flash spec: try T/P instead of H/P (enthalpy-flash is harder)",
            "4. Check compound IDs are correct (DWSIM CAS number vs. common name)",
            "5. Enable 'Use Rachford-Rice for initial estimate' in PP settings",
            "6. For near-critical conditions: reduce T/P step size in parametric",
        ],
        "dwsim_setting": "Stream → Properties → Flash Spec",
    },
    {
        "pattern": ["distillation", "column convergence", "inside-out",
                     "sum-rates", "naphtali", "column did not converge"],
        "cause": "COLUMN_CONVERGENCE",
        "severity": "high",
        "short": "Distillation column solver failed to converge",
        "explanation": (
            "DWSIM tries three column algorithms in order: Inside-Out → "
            "Burningham-Otto Sum-Rates → Naphtali-Sandholm. Failure means all "
            "three were exhausted. Most common: wrong number of stages, "
            "infeasible specs (reflux too close to Rmin), or poor initial T profile."
        ),
        "fixes": [
            "1. Use the 'Initialise Column' tool first (provides realistic T/composition profile)",
            "2. Increase number of stages by 20% (under-specification is safer)",
            "3. Check reflux ratio > 1.05 × Rmin (use Underwood shortcut first)",
            "4. Check distillate/bottoms split: total D+B = Feed × 1.0 (check mass balance)",
            "5. Reduce condenser/reboiler duty spec instead of reflux ratio (easier)",
            "6. Check for azeotrope — column cannot cross azeotrope without entrainer",
            "7. Use partial condenser first, then switch to total once converged",
            "8. Try SRK if PR fails (sometimes PP affects column stability)",
        ],
        "dwsim_setting": "Column → Setup → Solver → Algorithm",
    },
    {
        "pattern": ["property package", "pp error", "missing property",
                     "compound not found", "kij", "binary parameters"],
        "cause": "PROPERTY_PACKAGE_ERROR",
        "severity": "medium",
        "short": "Property package cannot compute required property",
        "explanation": (
            "The selected property package lacks required binary interaction "
            "parameters, or the compound is outside the PP's fitted range. "
            "NRTL/UNIQUAC needs kij for every pair; Cubic EOS kij defaults to 0."
        ),
        "fixes": [
            "1. Check binary interaction parameters: PP → Parameters → show matrix — zeros need regression",
            "2. Use UNIFAC if binary data is unavailable (predictive, no kij needed)",
            "3. For cubic EOS: kij = 0 is acceptable for hydrocarbon pairs",
            "4. Import Aspen or NIST binary parameters via PP → Import",
            "5. Reduce temperature range to PP validity window",
            "6. Switch to a PP that doesn't require kij (UNIFAC, ideal gas)",
        ],
        "dwsim_setting": "Property Package → Binary Interaction Parameters",
    },
    {
        "pattern": ["recycle", "tear stream", "recycle did not converge",
                     "recycle loop", "wegstein", "broyden diverge"],
        "cause": "RECYCLE_CONVERGENCE",
        "severity": "high",
        "short": "Recycle loop tear stream did not close",
        "explanation": (
            "The tear stream (the stream cut to open the recycle loop) was "
            "not matched by the calculated stream after each recycle iteration. "
            "This is DWSIM's most common production issue."
        ),
        "fixes": [
            "1. Set Wegstein method (most robust for smooth functions)",
            "2. For strongly non-linear recycles: Broyden quasi-Newton",
            "3. Set initial tear stream estimate close to expected converged value",
            "4. Reduce tolerance to 0.001 and maxiter to 200",
            "5. For oscillating systems: use Direct method with convergence factor 0.5",
            "6. Check that the flowsheet topology is correct (no duplicate tears)",
            "7. Increase under-relaxation factor (Wegstein q parameter → 0.0 pure Wegstein)",
        ],
        "dwsim_setting": "Flowsheet → Convergence → Recycle Settings",
    },
    {
        "pattern": ["object reference", "null reference", "nullreferenceexception",
                     "object not set"],
        "cause": "NULL_REFERENCE",
        "severity": "medium",
        "short": "A required object or connection is missing",
        "explanation": (
            "An internal .NET null reference — usually means a required "
            "stream/port connection is missing, or the flowsheet object was "
            "not fully initialised."
        ),
        "fixes": [
            "1. Check ALL unit-op inlet/outlet ports are connected",
            "2. Check the property package is set on the flowsheet",
            "3. Check all feed streams have T, P, and composition specified",
            "4. Re-open and re-save the flowsheet (.dwxmz)",
            "5. Delete and re-add the problematic unit op",
            "6. Check DWSIM version — some NullRef bugs are version-specific",
        ],
        "dwsim_setting": "Flowsheet → Verify Connections",
    },
    {
        "pattern": ["temperature exceeds", "pressure exceeds", "out of range",
                     "extrapolation", "valid range"],
        "cause": "OUT_OF_RANGE",
        "severity": "medium",
        "short": "Stream condition outside property package valid range",
        "explanation": (
            "The calculated T or P is outside the range for which the PP was "
            "fitted. Results will be unreliable (extrapolation of correlations)."
        ),
        "fixes": [
            "1. Check that T/P specifications are physically reasonable",
            "2. Switch to a PP valid over the required T/P range",
            "3. For very high T (>600°C): use Shomate/NASA polynomials or ideal gas",
            "4. For high P (>1000 bar): use Cubic Plus Association (CPA) or GERG-2008",
            "5. Check for sign errors in heat duties (direction of duty)",
        ],
    },
    {
        "pattern": ["energy balance", "energy not balanced", "duty",
                     "heat of reaction"],
        "cause": "ENERGY_BALANCE_ERROR",
        "severity": "medium",
        "short": "Energy balance failed or returned implausible result",
        "explanation": (
            "Common in reactors and columns. Heat of reaction data "
            "may be missing (ΔHf° of compounds), or the energy balance "
            "mode is set incorrectly (isothermal vs. adiabatic vs. specified duty)."
        ),
        "fixes": [
            "1. Check ΔHf° for all compounds (especially non-standard compounds)",
            "2. Verify reactor mode: Adiabatic vs. Specified Temperature vs. Specified Duty",
            "3. Check sign convention for heat duty (DWSIM: positive = heat added TO stream)",
            "4. For reactions: add stoichiometry and ΔHrxn in reaction set",
            "5. Cross-check with manual energy balance (Hout - Hin = Q - W)",
        ],
    },
]

# Unit-operation specific convergence settings
UNIT_CONVERGENCE = {
    "DistillationColumn": {
        "algorithms": ["Inside-Out (fast, good first guess)",
                        "Burningham-Otto Sum-Rates (more robust)",
                        "Naphtali-Sandholm (most robust, slowest)"],
        "key_settings": {
            "MaxIterations": "100 → 300 for difficult systems",
            "Tolerance": "0.0001 → 0.001",
            "DampingFactor": "1.0 → 0.5 for oscillating profiles",
            "UseInitialisation": "Always True for complex mixtures",
        },
        "common_failures": [
            "Reflux ratio too close to Rmin (increase R by 20%)",
            "Wrong feed tray location (try +/- 5 stages from estimate)",
            "Incorrect condenser specification (try partial condenser first)",
            "Incorrect number of stages (Gilliland underestimates — add 30%)",
        ],
        "tip": "Always initialise via 'Initialise Column' shortcut tool before first solve.",
    },
    "AbsorptionColumn": {
        "algorithms": ["Inside-Out", "Sum-Rates", "Naphtali-Sandholm"],
        "key_settings": {
            "L_G_ratio": "Start at 1.5× minimum; adjust if not converging",
            "ColumnPressure": "Must be consistent with solvent vapour pressure",
        },
        "common_failures": [
            "L/G ratio below minimum — absorber cannot achieve target",
            "Solvent temperature too high (reduces solubility)",
            "Too few stages (absorption is mass-transfer limited)",
        ],
    },
    "Recycle": {
        "methods": ["Direct", "Wegstein", "Broyden"],
        "method_guidance": {
            "Direct": "Most stable but slow. Use when Wegstein oscillates.",
            "Wegstein": "Best for most recycles. q factor controls acceleration (0=max accel).",
            "Broyden": "Best for strongly nonlinear recycles. Quasi-Newton update.",
        },
        "key_settings": {
            "MaxIterations": "100 → 500",
            "Tolerance": "0.0001 → 0.001",
            "WegsteinQ": "0.0 to 0.9 (0 = most aggressive acceleration)",
        },
        "tip": "Set tear stream initial estimate to expected converged value to halve iteration count.",
    },
    "PFR": {
        "key_settings": {
            "NumericalMethod": "Runge-Kutta 4 or Euler; RK4 more accurate",
            "IntegrationSteps": "100 → 500 for stiff kinetics",
            "ReactionSet": "Must have correct stoichiometry and rate expression",
        },
        "common_failures": [
            "Stiff ODEs (fast + slow reactions): reduce step size",
            "Negative compositions from large step: use RK4 or reduce max step",
            "Missing kinetic data (rate constants)",
        ],
    },
    "CSTR": {
        "key_settings": {
            "ConvergenceMethod": "Successive substitution or Newton",
            "MaxIterations": "100",
        },
        "common_failures": [
            "Multiple steady states (exothermic CSTR): check S-curve",
            "Missing heat removal specification",
        ],
    },
}


def diagnose(
    symptoms: List[str],
    flowsheet_state: Optional[Dict[str, Any]] = None,
    error_message: str = "",
) -> Dict[str, Any]:
    """Rank root causes from observed symptoms and return fix plans."""
    all_text = " ".join(s.lower() for s in symptoms) + " " + error_message.lower()

    matched: List[Dict[str, Any]] = []
    for ep in ERROR_PATTERNS:
        score = sum(1 for pat in ep["pattern"] if pat in all_text)
        if score > 0:
            matched.append({**ep, "_score": score})

    matched.sort(key=lambda x: x["_score"], reverse=True)

    # Additional context from flowsheet state
    context_notes = []
    if flowsheet_state:
        n_recycles = sum(1 for u in flowsheet_state.get("unit_ops", [])
                          if "recycle" in (u.get("type") or "").lower())
        if n_recycles > 0:
            context_notes.append(
                f"Flowsheet has {n_recycles} recycle loop(s) — recycle convergence is most likely cause.")
        n_columns = sum(1 for u in flowsheet_state.get("unit_ops", [])
                         if "column" in (u.get("type") or "").lower() or
                            "distillation" in (u.get("type") or "").lower())
        if n_columns > 0:
            context_notes.append(
                f"Flowsheet has {n_columns} column(s) — column convergence is likely.")

    return {
        "success": True,
        "n_causes_found": len(matched),
        "diagnoses": [
            {
                "rank":        i + 1,
                "cause_code":  d["cause"],
                "severity":    d["severity"],
                "short":       d["short"],
                "explanation": d["explanation"],
                "fixes":       d["fixes"],
                "dwsim_setting": d.get("dwsim_setting", ""),
                "confidence":  min(100, d["_score"] * 25),
            }
            for i, d in enumerate(matched[:5])
        ],
        "context_notes": context_notes,
        "general_tips": [
            "Solve the flowsheet open-loop first (break recycles), then add them back",
            "Use Simulation → Verify Connections to check all ports are wired",
            "Check DWSIM log (View → Log) for detailed error stack",
            "Save as new version before making convergence changes",
        ],
    }


def fix_plan(cause_code: str) -> Dict[str, Any]:
    """Return detailed step-by-step fix for a specific cause code."""
    for ep in ERROR_PATTERNS:
        if ep["cause"] == cause_code.upper():
            return {
                "success":     True,
                "cause_code":  cause_code,
                "short":       ep["short"],
                "explanation": ep["explanation"],
                "steps":       ep["fixes"],
                "dwsim_setting": ep.get("dwsim_setting", ""),
                "n_steps":     len(ep["fixes"]),
            }
    return {
        "success":   False,
        "error":     f"Unknown cause code: {cause_code}",
        "available": [ep["cause"] for ep in ERROR_PATTERNS],
    }


def convergence_guide(unit_type: str) -> Dict[str, Any]:
    """Unit-specific convergence parameter recommendations."""
    key = unit_type.replace(" ", "")
    # Try fuzzy match
    for k, v in UNIT_CONVERGENCE.items():
        if k.lower() in key.lower() or key.lower() in k.lower():
            return {
                "success":   True,
                "unit_type": k,
                "guide":     v,
                "algorithms": v.get("algorithms", []),
                "key_settings": v.get("key_settings", {}),
                "common_failures": v.get("common_failures", []),
                "tip": v.get("tip", ""),
            }
    return {
        "success":   False,
        "error":     f"No convergence guide for '{unit_type}'",
        "available": list(UNIT_CONVERGENCE.keys()),
    }


def numerical_settings_advisor(problem_description: str) -> Dict[str, Any]:
    """Recommend DWSIM numerical solver settings based on the problem."""
    desc = problem_description.lower()
    settings = {}

    if "oscillat" in desc or "diverge" in desc:
        settings["RecycleMethod"]     = "Direct (most damped)"
        settings["WegsteinQ"]         = "0.9 (near-Direct)"
        settings["DampingFactor"]     = "0.3"
        settings["explanation"]       = "Oscillating residuals → increase damping"

    elif "slow" in desc or "too many iter" in desc:
        settings["RecycleMethod"]     = "Broyden (fastest convergence rate)"
        settings["WegsteinQ"]         = "0.0 (maximum Wegstein acceleration)"
        settings["MaxIterations"]     = "50 (if near solution)"
        settings["explanation"]       = "Slow convergence → increase acceleration"

    elif "column" in desc or "distillat" in desc:
        settings["ColumnAlgorithm"]   = "Inside-Out first, then Sum-Rates"
        settings["DampingFactor"]     = "0.5 initially"
        settings["InitialiseFirst"]   = "True"
        settings["explanation"]       = "Column fails → always initialise + damp"

    elif "react" in desc or "kinetic" in desc:
        settings["IntegrationMethod"] = "Runge-Kutta 4"
        settings["IntegrationSteps"]  = "500"
        settings["explanation"]       = "Stiff kinetics → more integration steps + RK4"

    else:
        settings = {
            "RecycleMethod":   "Wegstein",
            "MaxIterations":   "200",
            "Tolerance":       "0.001",
            "DampingFactor":   "0.7",
            "explanation":     "General-purpose robust settings",
        }

    settings["HowToApply"] = (
        "In DWSIM: Flowsheet (or right-click on Recycle/Column) → "
        "Properties → Solver Settings → apply the values above"
    )
    return {"success": True, "recommended_settings": settings}


def error_decoder(error_message: str) -> Dict[str, Any]:
    """Decode a DWSIM error message to a human-readable explanation with fixes."""
    result = diagnose(
        symptoms=[error_message],
        error_message=error_message,
    )
    if result["n_causes_found"] == 0:
        # Generic decode
        return {
            "success": True,
            "decoded": "No specific pattern matched. General advice:",
            "general_steps": [
                "1. Check the DWSIM log (View → Log) for the full stack trace",
                "2. Try saving as a new file and re-opening",
                "3. Delete and re-add the unit op that errored",
                "4. Verify all stream connections are complete",
                "5. Check all compound IDs are valid in the compound database",
            ],
            "raw_message": error_message[:200],
        }
    top = result["diagnoses"][0]
    return {
        "success":   True,
        "decoded":   top["short"],
        "cause":     top["cause_code"],
        "explanation": top["explanation"],
        "top_fixes": top["fixes"][:3],
        "full_diagnosis": result,
    }


def troubleshoot_process(
    process_type: str,
    issue: str,
    flowsheet_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Top-level troubleshooter: given a process type and issue description,
    return both convergence fixes and process-design-level recommendations."""
    from process_design_advisor import design_checklist

    # Convergence diagnosis
    conv = diagnose([issue, process_type], flowsheet_state, issue)

    # Design-level checklist
    cl = design_checklist(process_type)
    checklist_items = cl.get("checklist", []) if cl.get("success") else []

    # Unit-specific settings
    unit_guide = {}
    for k in UNIT_CONVERGENCE:
        if k.lower() in process_type.lower() or process_type.lower() in k.lower():
            unit_guide = convergence_guide(k)
            break

    # Numerical advice
    num = numerical_settings_advisor(issue)

    return {
        "success":        True,
        "issue":          issue,
        "process_type":   process_type,
        "diagnosis":      conv,
        "unit_guide":     unit_guide,
        "numerical_settings": num,
        "design_checklist": checklist_items[:5],
        "summary": (
            f"Diagnosed {conv['n_causes_found']} possible cause(s). "
            + (f"Top cause: {conv['diagnoses'][0]['short']}. "
                if conv['diagnoses'] else "")
            + f"Numerical settings recommendation: {num['recommended_settings'].get('explanation','')}."
        ),
    }
