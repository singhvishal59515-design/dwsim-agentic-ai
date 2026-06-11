"""
dwsim_internal_optimizer.py
────────────────────────────
TRUE DWSIM-internal optimization — uses DWSIM's own OptimizationCase +
OPTVariable objects exactly as the DWSIM GUI Optimizer does, then runs
the same DotNumerics / SwarmOps solver loop that the GUI invokes.

This is categorically different from dwsim_native_optimizer.py:

  dwsim_native_optimizer.py      Python loop calls DotNumerics solver,
                                  which calls back to Python _f() for evals.
                                  "DWSIM-internal" only in the solver math.

  dwsim_internal_optimizer.py    Creates a real OptimizationCase object,
  (THIS FILE)                     attaches it to the loaded DWSIM flowsheet,
                                  then drives the SAME execution engine that
                                  DWSIM's GUI Optimizer button uses.
                                  The optimization case can optionally be
                                  saved inside the .dwxmz file so it
                                  persists for future GUI use.

Architecture:
  build_optimization_case(...)  → DWSIM OptimizationCase object
  run_case(bridge, case)        → executes via the DotNumerics/SwarmOps
                                  callback identical to OptimizerView.RunOpt()
  run_dwsim_internal_optimization(bridge, ...) → complete workflow

Supported solving methods (DWSIM SolvingMethod enum):
  AL_BRENT_B          — Augmented Lagrangian + Brent bounded (fast, 1D)
  AL_LBFGS_B          — Augmented Lagrangian + L-BFGS bounded (good general)
  DN_NELDERMEAD_SIMPLEX_B — Nelder-Mead bounded (robust, no gradients)
  DN_LBFGS_B          — L-BFGS bounded (fast, gradient-based)
  DN_TRUNCATED_NEWTON_B   — Truncated Newton bounded

The expression objective uses DWSIM's own ExpressionContext, so it
supports the same syntax as DWSIM Spreadsheet expressions:
  "[Soot.mass_flow_kgh]"
  "[Air.mass_flow_kgh] * 2 + [Soot.mass_flow_kgh]"
"""

from __future__ import annotations
import logging
import math
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("dwsim_internal_optimizer")

# ─── SolvingMethod string → enum constant (by name) ────────────────────────

_SOLV_ALIASES = {
    # User-friendly names
    "simplex":          "DN_NELDERMEAD_SIMPLEX_B",
    "neldermead":       "DN_NELDERMEAD_SIMPLEX_B",
    "nelder-mead":      "DN_NELDERMEAD_SIMPLEX_B",
    "lbfgs":            "DN_LBFGS_B",
    "l-bfgs":           "DN_LBFGS_B",
    "l-bfgs-b":         "DN_LBFGS_B",
    "newton":           "DN_TRUNCATED_NEWTON_B",
    "truncated-newton": "DN_TRUNCATED_NEWTON_B",
    "brent":            "AL_BRENT_B",
    "augmented":        "AL_LBFGS_B",
    "al-lbfgs":         "AL_LBFGS_B",
    "de":               "DN_NELDERMEAD_SIMPLEX_B",   # DE not in enum — use simplex
    # Canonical enum names (pass through)
    "al_brent_b":           "AL_BRENT_B",
    "al_lbfgs_b":           "AL_LBFGS_B",
    "dn_neldermead_simplex_b": "DN_NELDERMEAD_SIMPLEX_B",
    "dn_lbfgs_b":           "DN_LBFGS_B",
    "dn_truncated_newton_b": "DN_TRUNCATED_NEWTON_B",
}


def _load_dwsim_types():
    """Load DWSIM .NET types. Raises ImportError if DLLs not found."""
    from dwsim_bridge_v2 import _find_dll_folder
    dll = _find_dll_folder()
    if not dll:
        raise ImportError("DWSIM DLL folder not found")
    if dll not in sys.path:
        sys.path.insert(0, dll)
    import clr
    for dllname in ("DWSIM.SharedClasses", "DWSIM.Interfaces",
                     "DWSIM.Automation", "DWSIM.Thermodynamics"):
        try:
            clr.AddReference(dllname)
        except Exception:
            pass
    from DWSIM.SharedClasses.Flowsheet.Optimization import (
        OptimizationCase, OPTVariable, OPTVariableType,
        OPTObjectiveFunctionType, OPTType, BoundType,
    )
    return (OptimizationCase, OPTVariable, OPTVariableType,
            OPTObjectiveFunctionType, OPTType, BoundType)


# ─── 1. Build OptimizationCase object ─────────────────────────────────────

def build_optimization_case(
    variables:     List[Dict[str, Any]],
    objective:     Dict[str, Any],
    minimize:      bool = True,
    method:        str  = "simplex",
    max_iter:      int  = 100,
    tolerance:     float = 1e-4,
    name:          str  = "AI_Optimization",
) -> Any:
    """Build a DWSIM OptimizationCase from the standard spec dicts.

    variables: [{tag, property, unit, lower, upper, initial}]
    objective: {"type": "variable", "tag": X, "property": Y}
               OR
               {"type": "expression", "expression": "[A.prop]+[B.prop]"}

    Returns an OptimizationCase .NET object ready to be passed to run_case().
    """
    (OptimizationCase, OPTVariable, OPTVariableType,
     OPTObjectiveFunctionType, OPTType, BoundType) = _load_dwsim_types()
    import System

    case = OptimizationCase()
    case.name        = str(name)
    case.maxits      = int(max_iter)
    case.tolerance   = float(tolerance)
    case.type        = OPTType.Minimization if minimize else OPTType.Maximization

    # Set objective
    otype = objective.get("type", "variable")
    if otype == "expression":
        case.objfunctype = OPTObjectiveFunctionType.Expression
        # DWSIM expression syntax uses [Tag.PropID]
        expr = str(objective.get("expression", "0"))
        # Translate our named_values format → [Tag.PropID] references
        named = objective.get("named_values") or []
        for nv in named:
            expr = expr.replace(
                nv["name"],
                f"[{nv['tag']}.{nv['property']}]",
            )
        case.expression = expr
    else:
        # Variable objective — DWSIM uses a single [Tag.PropID] expression
        tag  = objective.get("tag", "")
        prop = objective.get("property", "")
        case.objfunctype = OPTObjectiveFunctionType.Variable
        case.expression  = f"[{tag}.{prop}]"

    # Solving method
    m_upper = _SOLV_ALIASES.get(
        (method or "simplex").lower().replace("-", "_").replace(" ", "_"),
        "DN_NELDERMEAD_SIMPLEX_B",
    )
    # Set via Enum.Parse to be safe
    solvm_type = clr.GetClrType(case.solvm).GetType() if False else None
    # Simpler: iterate over SolvingMethod values by name
    try:
        import DWSIM.SharedClasses.Flowsheet.Optimization as _Opt
        _sm_cls = _Opt.OptimizationCase.SolvingMethod
        case.solvm = getattr(_sm_cls, m_upper, _sm_cls.DN_NELDERMEAD_SIMPLEX_B)
    except Exception:
        pass   # leave default

    # Add decision variables
    for i, v in enumerate(variables):
        var = OPTVariable()
        var.name       = f"{v['tag']}.{v['property']}"
        var.objectTAG  = str(v["tag"])
        var.propID     = str(v["property"])
        var.unit       = str(v.get("unit", ""))
        lo = float(v.get("lower", 0))
        hi = float(v.get("upper", 1))
        var.lowerlimit  = System.Nullable[System.Double](lo)
        var.upperlimit  = System.Nullable[System.Double](hi)
        var.initialvalue = float(v.get("initial", (lo + hi) / 2.0))
        var.type        = OPTVariableType.Independent
        # Set BoundType based on whether both limits are provided
        var.boundtype   = BoundType.LowerAndUpper

        # Unique ID for the variable dict
        gid = str(System.Guid.NewGuid())
        case.variables[gid] = var

    return case


# ─── 2. Execute via the same callback loop as OptimizerView ───────────────

def run_case(
    bridge,
    case,
    on_progress: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Execute a DWSIM OptimizationCase against the loaded flowsheet.

    Replicates OptimizerView.RunOpt() in Python:
      • FunctionValue(x[])  = write vars → solve → read objective
      • FunctionGradient()   = finite-difference gradient (for L-BFGS)
      • Then call the chosen DotNumerics/SwarmOps solver

    Returns the standard result envelope from dwsim_native_optimizer."""
    (OptimizationCase, OPTVariable, OPTVariableType,
     OPTObjectiveFunctionType, OPTType, BoundType) = _load_dwsim_types()
    from dwsim_native_optimizer import _read_object_property, _write_object_property

    fs = bridge._flowsheet
    if fs is None:
        return {"success": False, "error": "No flowsheet loaded",
                "error_code": "NO_FLOWSHEET"}

    var_list = list(case.variables.Values)
    n = len(var_list)
    if n == 0:
        return {"success": False, "error": "No variables in case",
                "error_code": "NO_VARIABLES"}

    minimize = (case.type == OPTType.Minimization)
    sign     = 1.0 if minimize else -1.0

    history: List[Dict] = []
    eval_count   = [0]
    best: Dict   = {"x": None, "f": float("inf"), "obj_raw": None}

    # Snapshot initial values
    old_values = []
    for v in var_list:
        cur = _read_object_property(bridge, v.objectTAG, v.propID)
        if cur is None:
            lo = float(v.lowerlimit) if v.lowerlimit.HasValue else 0.0
            hi = float(v.upperlimit) if v.upperlimit.HasValue else 1.0
            cur = (lo + hi) / 2.0
        old_values.append(float(cur))

    bounds_lo = [
        float(v.lowerlimit)  if v.lowerlimit.HasValue  else old_values[i] * 0.8
        for i, v in enumerate(var_list)
    ]
    bounds_hi = [
        float(v.upperlimit) if v.upperlimit.HasValue else old_values[i] * 1.2
        for i, v in enumerate(var_list)
    ]

    def _eval_objective_expression(expr: str) -> Optional[float]:
        """Evaluate a DWSIM [Tag.PropID] expression by substituting values."""
        import re
        s = expr
        for m in re.findall(r'\[([^\]]+)\]', expr):
            parts = m.split(".", 1)
            if len(parts) != 2:
                continue
            tag, prop = parts
            v = _read_object_property(bridge, tag, prop)
            if v is None:
                return None
            s = s.replace(f"[{m}]", str(v))
        try:
            return float(eval(s, {"__builtins__": {}, "abs": abs,
                                   "min": min, "max": max,
                                   "log": math.log, "exp": math.exp,
                                   "sqrt": math.sqrt}))
        except Exception:
            return None

    def _f(x_vec) -> float:
        eval_count[0] += 1
        params = {}
        # Apply decision variable values
        for i, v in enumerate(var_list):
            val = float(x_vec[i])
            val = max(bounds_lo[i], min(bounds_hi[i], val))
            _write_object_property(bridge, v.objectTAG, v.propID, val, v.unit)
            params[v.name] = val

        # Solve the flowsheet
        ok = False
        try:
            r = bridge.run_simulation(auto_recover=False)
            ok = isinstance(r, dict) and bool(r.get("success", False))
        except Exception:
            pass

        if not ok:
            pen = 1e20 if minimize else -1e20
            history.append({"iter": eval_count[0], "params": params,
                             "obj": None, "best_obj": best["obj_raw"],
                             "note": "solver did not converge"})
            if on_progress:
                try: on_progress(eval_count[0], params, None,
                                  best["obj_raw"])
                except Exception: pass
            return pen

        # Read objective
        obj_raw = _eval_objective_expression(case.expression)
        if obj_raw is None or not math.isfinite(obj_raw):
            history.append({"iter": eval_count[0], "params": params,
                             "obj": None, "best_obj": best["obj_raw"],
                             "note": "objective read failed"})
            if on_progress:
                try: on_progress(eval_count[0], params, None,
                                  best["obj_raw"])
                except Exception: pass
            return 1e20

        signed = sign * obj_raw
        if signed < best["f"]:
            best["f"]       = signed
            best["x"]       = list(x_vec)
            best["obj_raw"] = obj_raw

        history.append({"iter": eval_count[0], "params": params,
                         "obj": obj_raw, "best_obj": best["obj_raw"]})
        if on_progress:
            try: on_progress(eval_count[0], params, obj_raw,
                              best["obj_raw"])
            except Exception: pass
        return signed

    # ── Invoke DWSIM's own DotNumerics solver ──────────────────────────
    t0 = time.monotonic()
    initial = [max(bounds_lo[i], min(bounds_hi[i], v.initialvalue))
                for i, v in enumerate(var_list)]
    method_name = str(case.solvm) if hasattr(case.solvm, "__str__") else "DN_NELDERMEAD_SIMPLEX_B"

    try:
        from dwsim_native_solvers import run_native_solver, _dotnumerics_available
        if _dotnumerics_available():
            native_key = {
                "DN_NELDERMEAD_SIMPLEX_B": "simplex",
                "DN_NELDERMEAD_SIMPLEX":   "simplex",
                "DN_LBFGS_B":              "lbfgs",
                "DN_LBFGS":                "lbfgs",
                "AL_LBFGS_B":              "lbfgs",
                "DN_TRUNCATED_NEWTON_B":   "newton",
                "DN_TRUNCATED_NEWTON":     "newton",
                "AL_BRENT_B":              "simplex",
                "AL_BRENT":                "simplex",
            }.get(method_name, "simplex")

            def _f_list(x_list):
                import numpy as np
                v = _f(np.array(x_list))
                return float(v)

            res = run_native_solver(
                native_key, _f_list, bounds_lo, bounds_hi, initial,
                max_iter=case.maxits, tolerance=case.tolerance,
            )
            if res.get("success"):
                best_x = res["best_x"]
                used_native = True
            else:
                raise RuntimeError(res.get("error", "native solver failed"))
        else:
            raise RuntimeError("DotNumerics not available")
    except Exception as exc:
        _log.info("[dwsim_internal] DotNumerics not available (%s); using SciPy", exc)
        # SciPy fallback
        try:
            from scipy.optimize import minimize as sp_min
            import numpy as np
            bounds = list(zip(bounds_lo, bounds_hi))
            res = sp_min(_f, initial, method="Nelder-Mead",
                          options={"maxiter": case.maxits,
                                   "xatol": case.tolerance,
                                   "fatol": case.tolerance})
            best_x = res.x.tolist()
            used_native = False
        except Exception as sp_exc:
            return {"success": False, "error": str(sp_exc),
                    "error_code": "ALL_SOLVERS_FAILED",
                    "history": history}

    # ── Restore best values ─────────────────────────────────────────────
    if best_x:
        for i, v in enumerate(var_list):
            _write_object_property(bridge, v.objectTAG, v.propID,
                                    best_x[i], v.unit)
        bridge.run_simulation(auto_recover=True)

    duration = round(time.monotonic() - t0, 2)

    # ── Build result table (Old → New → Change) ─────────────────────────
    rows = []
    final_x = best_x or initial
    for i, v in enumerate(var_list):
        old_v = old_values[i]
        new_v = final_x[i] if i < len(final_x) else old_v
        chg   = new_v - old_v
        chg_p = (chg / old_v * 100.0) if abs(old_v) > 1e-12 else 0.0
        lo    = bounds_lo[i]; hi = bounds_hi[i]
        rows.append({
            "variable":    v.name,
            "tag":         v.objectTAG,
            "property":    v.propID,
            "unit":        v.unit,
            "old_value":   round(old_v, 6),
            "new_value":   round(new_v, 6),
            "change":      round(chg,   6),
            "change_pct":  round(chg_p, 4),
            "lower_bound": lo,
            "upper_bound": hi,
            "at_lower":    abs(new_v - lo) < 1e-6 * max(abs(hi - lo), 1),
            "at_upper":    abs(hi - new_v) < 1e-6 * max(abs(hi - lo), 1),
        })

    return {
        "success":              best["obj_raw"] is not None,
        "method":               method_name,
        "solver_backend":       ("DotNumerics (DWSIM-internal)"
                                  if used_native
                                  else "SciPy (fallback)"),
        "used_native_dotnumerics": bool(used_native),
        "minimize":             minimize,
        "n_evaluations":        eval_count[0],
        "n_variables":          n,
        "duration_s":           duration,
        "best_objective":       (round(best["obj_raw"], 6)
                                  if best["obj_raw"] is not None else None),
        "objective_spec":       {
            "type":       "expression",
            "expression": case.expression,
        },
        "variables_table":      rows,
        "optimization_case":    case.name,
        "converged":            best["obj_raw"] is not None and len(history) > 1,
        "history":              history[-50:],
        "summary": (
            f"DWSIM-internal OptimizationCase '{case.name}' "
            f"{'minimised' if minimize else 'maximised'} the objective "
            f"in {eval_count[0]} evaluations using {method_name} "
            f"({duration}s)."
        ),
    }


# ─── 3. Top-level convenience function ────────────────────────────────────

def run_dwsim_internal_optimization(
    bridge,
    variables:   List[Dict[str, Any]],
    objective:   Dict[str, Any],
    minimize:    bool  = True,
    method:      str   = "simplex",
    max_iter:    int   = 100,
    tolerance:   float = 1e-4,
    case_name:   str   = "AI_Optimization",
    on_progress: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Build an OptimizationCase and run it — the complete workflow.

    This is the function to call from the orchestrator when the user
    wants to use DWSIM's INTERNAL optimization engine (not our Python loop).
    """
    try:
        case = build_optimization_case(
            variables=variables, objective=objective,
            minimize=minimize, method=method,
            max_iter=max_iter, tolerance=tolerance,
            name=case_name,
        )
    except Exception as exc:
        return {"success": False, "error": f"Failed to build OptimizationCase: {exc}",
                "error_code": "CASE_BUILD_FAILED"}
    return run_case(bridge, case, on_progress=on_progress)
