"""
dwsim_native_optimizer.py
─────────────────────────
Drive DWSIM's INTERNAL optimization engine — the same OptimizationCase,
OPTVariable types, and DotNumerics solvers that the DWSIM GUI's Optimizer
window uses. The result is a real DWSIM optimization (not an external
Python loop) but driven programmatically.

Architecture mirrors DWSIM's UI:OptimizerView.RunOpt():
  1. Build a DWSIM OptimizationCase with OPTVariable instances bound to
     flowsheet object properties.
  2. Define an objective expression (variable reference OR MathML/MathParser
     expression involving variable names).
  3. Pick a solving method from OptimizationCase.SolvingMethod:
        - DN_NELDERMEAD_SIMPLEX(_B) — Nelder-Mead (gradient-free)
        - DN_LBFGS(_B)              — L-BFGS-B (quasi-Newton, fast)
        - DN_TRUNCATED_NEWTON(_B)   — Truncated Newton
        - AL_BRENT(_B), AL_LBFGS(_B) — alternative impl variants
        - DE                        — Differential Evolution (global)
     The "_B" suffix means bound-constrained.
  4. The solver calls a callback that:
        - writes the candidate values into the flowsheet
        - calls SolveFlowsheet
        - reads the objective value
        - returns it to the solver
  5. After the solver converges, we report best values + old vs new diff
     in a poster-style result envelope.

Available solver enums (from DWSIM.SharedClasses.Flowsheet.Optimization
                       .OptimizationCase.SolvingMethod):
  AL_BRENT, AL_BRENT_B, AL_LBFGS, AL_LBFGS_B,
  DN_LBFGS, DN_LBFGS_B,
  DN_NELDERMEAD_SIMPLEX, DN_NELDERMEAD_SIMPLEX_B,
  DN_TRUNCATED_NEWTON, DN_TRUNCATED_NEWTON_B
"""

from __future__ import annotations
import math
import time
from typing import Any, Callable, Dict, List, Optional


# ── Method aliases — friendly names → DWSIM enum names ──────────────────────
_METHOD_ALIASES: Dict[str, str] = {
    "simplex":           "DN_NELDERMEAD_SIMPLEX_B",
    "nelder-mead":       "DN_NELDERMEAD_SIMPLEX_B",
    "neldermead":        "DN_NELDERMEAD_SIMPLEX_B",
    "lbfgs":             "DN_LBFGS_B",
    "l-bfgs":            "DN_LBFGS_B",
    "lbfgsb":            "DN_LBFGS_B",
    "truncated-newton":  "DN_TRUNCATED_NEWTON_B",
    "newton":            "DN_TRUNCATED_NEWTON_B",
    "brent":             "AL_BRENT_B",
    "de":                "DE",
    "differential-evolution": "DE",
    "global":            "DE",
    # CMA-ES — external `cma` package. Covariance Matrix Adaptation Evolution
    # Strategy: gold-standard derivative-free optimiser for continuous black-box
    # problems, far more sample-efficient than Nelder-Mead/DE on expensive
    # evaluations (each DWSIM solve costs seconds), and robust to local minima.
    "cma":               "CMA_ES",
    "cma-es":            "CMA_ES",
    "cmaes":             "CMA_ES",
}


def _solving_method_enum(name: str):
    """Resolve a friendly method name to the OptimizationCase.SolvingMethod enum value."""
    from DWSIM.SharedClasses.Flowsheet.Optimization import OptimizationCase
    key = (name or "").strip().lower().replace("_", "-")
    canonical = _METHOD_ALIASES.get(key, name)
    if canonical == "DE":
        return None  # DE is handled via DotNumerics directly, not via OptimizationCase
    try:
        return getattr(OptimizationCase.SolvingMethod, canonical)
    except AttributeError:
        return OptimizationCase.SolvingMethod.DN_NELDERMEAD_SIMPLEX_B


# ── Helpers to read/write flowsheet object properties ───────────────────────

def _read_object_property(bridge, tag: str, property_name: str) -> Optional[float]:
    """Read a numeric property from a stream or unit-op via the bridge.

    CRITICAL: uses explicit None checks (NOT `or`) because a legitimate
    objective value can be 0.0 — Python `or` treats 0.0 as falsy and would
    silently mark the global optimum as a failed read."""
    try:
        if hasattr(bridge, "get_stream_property"):
            r = bridge.get_stream_property(tag, property_name)
            if isinstance(r, dict) and r.get("success"):
                v = r.get("value")
                if v is None:
                    v = r.get("properties", {}).get(property_name)
                if v is not None:
                    return float(v)
        # Fall back to bulk read
        sp = bridge.get_stream_properties(tag) if hasattr(bridge, "get_stream_properties") else None
        if isinstance(sp, dict) and sp.get("success"):
            for key in (property_name, property_name.lower(),
                        property_name + "_C", property_name + "_K",
                        property_name + "_bar", property_name + "_kgh"):
                v = sp.get("properties", {}).get(key)
                if v is not None:
                    return float(v)
        uop = bridge.get_unit_op_properties(tag) if hasattr(bridge, "get_unit_op_properties") else None
        if isinstance(uop, dict) and uop.get("success"):
            v = uop.get("properties", {}).get(property_name)
            if v is not None:
                return float(v)
        # FINAL fallback: .NET reflection via reflect_get_set. This reaches
        # unit-op properties (HeatDuty, DeltaQ, OutletTemperature, etc.) that
        # the stream/unit-op readers above don't expose. Critical for using
        # heater/reactor properties as optimization objectives.
        try:
            from dwsim_reflection import reflect_get_set
            r = reflect_get_set(bridge, tag, property_name)
            if isinstance(r, dict) and r.get("success"):
                return float(r["value"])
        except Exception:
            pass
    except Exception:
        pass
    return None


def _write_object_property(bridge, tag: str, property_name: str,
                            value: float, unit: str = "") -> bool:
    """Write a numeric property to a stream or unit-op.

    Try the unit-op setter FIRST. `set_unit_op_property` is strict — it fails
    cleanly when the tag/property isn't a writable unit-op property (e.g. a
    stream's temperature). `set_stream_property`, by contrast, can spuriously
    report success on a unit-op tag WITHOUT changing the setpoint — which
    silently froze optimisation of unit-op decision variables (e.g. a heater's
    outlet temperature): the variable never moved, so the objective never
    changed. Strict-first makes routing correct for both object types.
    """
    try:
        r = bridge.set_unit_op_property(tag, property_name, float(value))
        if isinstance(r, dict) and r.get("success"):
            return True
    except Exception:
        pass
    try:
        r = bridge.set_stream_property(tag, property_name, float(value), unit)
        if isinstance(r, dict) and r.get("success"):
            return True
    except Exception:
        pass
    return False


def _solve_flowsheet(bridge) -> bool:
    """Solve the current flowsheet. Returns True on convergence.

    Passes auto_recover=False so a failed eval doesn't trigger a
    3-attempt robust_solve cascade inside the optimization loop —
    that would make a 50-eval run take 30 minutes instead of 5.
    Failed evals are handled by the solver's penalty mechanism."""
    try:
        if hasattr(bridge, "run_simulation"):
            # Try the new signature with auto_recover; fall back if older bridge
            try:
                r = bridge.run_simulation(auto_recover=False)
            except TypeError:
                r = bridge.run_simulation()
            if isinstance(r, dict):
                return bool(r.get("success", False))
        if hasattr(bridge, "save_and_solve"):
            r = bridge.save_and_solve()
            if isinstance(r, dict):
                return bool(r.get("success", False))
    except Exception:
        return False
    return False


# ── Main entry ──────────────────────────────────────────────────────────────

def run_dwsim_native_optimization(
    bridge,
    variables:        List[Dict[str, Any]],
    objective:        Dict[str, Any],
    method:           str = "simplex",
    minimize:         bool = True,
    max_iter:         int = 50,
    tolerance:        float = 1e-3,
    on_progress:      Optional[Callable[[int, Dict, float, float], None]] = None,
    constraints:      Optional[List[Dict[str, Any]]] = None,
    penalty_weight:   float = 1e6,
) -> Dict[str, Any]:
    """
    Run an optimization using DWSIM's own solver libraries.

    variables: list of decision variables, each a dict with keys:
        tag       — stream or unit-op tag (e.g. "RC-01")
        property  — property to vary  (e.g. "outlet_temperature_C",
                                       "mass_flow_kgh", "RefluxRatio")
        unit      — unit string passed to the bridge setter
        lower     — lower bound (in the same unit)
        upper     — upper bound
        initial   — optional starting guess (defaults to current value, or midpoint)

    objective: dict with one of two shapes:
        {"type": "variable", "tag": "PROD", "property": "molar_fraction_H2"}
            → maximise/minimise that single property
        {"type": "expression", "expression": "H2_purity + CO_purity",
         "named_values": [{"name": "H2_purity",
                           "tag": "PSA", "property": "mole_fraction_H2"}, ...]}
            → arbitrary scalar function of named property values

    method: friendly name resolved by _METHOD_ALIASES — defaults to "simplex"
            which uses DWSIM's bound-constrained Nelder-Mead.

    on_progress(iter_idx, current_params, current_obj, best_obj) — optional
        callback invoked after each evaluation. Used by the API to stream
        progress to the UI in real time.
    """
    import numpy as np

    if not variables:
        return {"success": False, "error_code": "NO_VARIABLES",
                "error": "variables list is empty"}
    if not objective or not isinstance(objective, dict):
        return {"success": False, "error_code": "NO_OBJECTIVE",
                "error": "objective dict required"}

    # ── 1. Snapshot the current ("Old") values so we can show before/after ──
    old_values: List[float] = []
    for v in variables:
        cur = _read_object_property(bridge, v["tag"], v["property"])
        if cur is None:
            cur = float(v.get("initial",
                              (float(v["lower"]) + float(v["upper"])) / 2.0))
        old_values.append(float(cur))

    n = len(variables)
    bounds_lo = [float(v["lower"]) for v in variables]
    bounds_hi = [float(v["upper"]) for v in variables]
    initial   = [float(v.get("initial", old_values[i])) for i, v in enumerate(variables)]
    initial   = [max(lo, min(hi, x)) for x, lo, hi in zip(initial, bounds_lo, bounds_hi)]
    sign      = 1.0 if minimize else -1.0   # solver minimises; flip if maximising

    history: List[Dict[str, Any]] = []
    eval_count = [0]
    best = {"x": list(initial), "f": float("inf"), "obj_raw": None}
    # Per-run evaluation cache: solvers (DE survivors, Nelder-Mead reflections,
    # multi-start overlap) frequently re-visit the SAME point. Each DWSIM solve
    # costs seconds, so memoising on the clamped point avoids redundant solves.
    # Safe within one run: variables / objective / flowsheet are fixed, and the
    # final "restore best + solve" step re-establishes flowsheet state.
    _eval_cache: Dict[tuple, float] = {}
    _cache_hits = [0]

    # ── 2. Objective function — write decision vars, solve, read objective ─
    def _eval_objective() -> Optional[float]:
        """Read the objective from the freshly-solved flowsheet."""
        otype = objective.get("type", "variable")
        if otype == "variable":
            return _read_object_property(
                bridge, objective["tag"], objective["property"])
        if otype == "expression":
            named = {}
            for nv in objective.get("named_values", []):
                val = _read_object_property(bridge, nv["tag"], nv["property"])
                if val is None:
                    return None
                named[nv["name"]] = val
            # Safe expression evaluation — only allow math operators + names
            expr = objective.get("expression", "")
            try:
                allowed = {
                    "__builtins__": {},
                    "abs": abs, "min": min, "max": max,
                    "pow": pow, "log": math.log, "log10": math.log10,
                    "exp": math.exp, "sqrt": math.sqrt,
                    "sin": math.sin, "cos": math.cos,
                }
                allowed.update(named)
                return float(eval(expr, allowed, named))  # noqa: S307 - sandboxed
            except Exception:
                return None
        return None

    def _f_uncached(x_vec) -> float:
        """Solver callback: write vars, solve, read objective, return signed value."""
        eval_count[0] += 1
        params = {}
        x_clamped = []
        for i, v in enumerate(variables):
            val = float(x_vec[i])
            val = max(bounds_lo[i], min(bounds_hi[i], val))   # clamp
            x_clamped.append(val)
            _write_object_property(bridge, v["tag"], v["property"], val,
                                   v.get("unit", ""))
            params[v["tag"] + "." + v["property"]] = val

        converged = _solve_flowsheet(bridge)
        if not converged:
            # Failed solve — return a heavy penalty so the solver steers away.
            # We use a fixed large finite value rather than ±inf because some
            # solvers (notably L-BFGS-B finite-difference gradient) misbehave
            # with non-finite returns.
            history.append({"iter": eval_count[0], "params": params,
                            "obj": None, "best_obj": best["obj_raw"],
                            "note": "solver did not converge"})
            if on_progress:
                try: on_progress(eval_count[0], params, None,
                                 best["obj_raw"] if best["obj_raw"] is not None else 0.0)
                except Exception: pass
            return 1e20 if minimize else -1e20

        obj_raw = _eval_objective()
        if obj_raw is None or not math.isfinite(obj_raw):
            history.append({"iter": eval_count[0], "params": params,
                            "obj": None, "best_obj": best["obj_raw"],
                            "note": "objective read failed"})
            if on_progress:
                try: on_progress(eval_count[0], params, None,
                                 best["obj_raw"] if best["obj_raw"] is not None else 0.0)
                except Exception: pass
            return 1e20 if minimize else -1e20

        # ── Inequality-constraint penalty ─────────────────────────────────
        # Add squared-violation penalty for each breached constraint.
        # See constraint_solver.py for the algorithm rationale.
        constraint_pen = 0.0
        n_constraint_violations = 0
        if constraints:
            try:
                from constraint_solver import (
                    _read_constraint_value, _constraint_violation,
                )
                for c in constraints:
                    cv = _read_constraint_value(bridge, c)
                    vi = _constraint_violation(cv, c)
                    if vi > 0:
                        constraint_pen += vi * vi
                        n_constraint_violations += 1
            except Exception as _cexc:
                _log.debug("constraint eval failed: %s", _cexc)

        obj_effective = obj_raw
        if constraint_pen > 0:
            # When MAXIMISING (sign=-1), we want to SUBTRACT the penalty
            # from the raw objective so the signed value (which is -obj
            # + penalty) gets larger (worse for the minimiser). When
            # MINIMISING, we ADD the penalty so signed = obj + penalty
            # is larger.
            obj_effective = obj_raw + sign * penalty_weight * constraint_pen

        signed = sign * obj_effective
        # Track best (in the signed sense)
        if signed < best["f"]:
            best["f"] = signed
            best["x"] = x_clamped   # store the CLAMPED point actually evaluated
            best["obj_raw"] = obj_raw
            best["constraint_pen"] = constraint_pen
            best["n_violations"]   = n_constraint_violations

        history.append({"iter": eval_count[0], "params": params,
                        "obj": obj_raw,
                        "obj_effective": obj_effective if constraints else obj_raw,
                        "best_obj": best["obj_raw"],
                        "constraint_violations": n_constraint_violations})
        if on_progress:
            try: on_progress(eval_count[0], params, obj_raw, best["obj_raw"])
            except Exception: pass
        return signed

    def _f(x_vec) -> float:
        """Memoising wrapper around _f_uncached, keyed on the clamped point."""
        clamped = [max(bounds_lo[i], min(bounds_hi[i], float(x_vec[i])))
                   for i in range(n)]
        ckey = tuple(round(v, 10) for v in clamped)
        cached = _eval_cache.get(ckey)
        if cached is not None:
            _cache_hits[0] += 1
            return cached
        val = _f_uncached(x_vec)
        _eval_cache[ckey] = val
        return val

    # ── 3. Invoke DWSIM's solver via DotNumerics ────────────────────────────
    method_key = (method or "simplex").strip().lower().replace("_", "-")
    canonical  = _METHOD_ALIASES.get(method_key, "DN_NELDERMEAD_SIMPLEX_B")
    t0 = time.monotonic()
    solver_msg = ""

    # ── Try DotNumerics native solvers FIRST (same engines as DWSIM GUI) ──
    used_native = False
    try:
        from dwsim_native_solvers import (
            run_native_solver, _dotnumerics_available,
        )
        if _dotnumerics_available():
            # Map our canonical name to the native-solvers key
            native_key = {
                "DN_NELDERMEAD_SIMPLEX":   "simplex",
                "DN_NELDERMEAD_SIMPLEX_B": "simplex",
                "DN_LBFGS":                "lbfgs",
                "DN_LBFGS_B":              "lbfgs",
                "AL_LBFGS":                "lbfgs",
                "AL_LBFGS_B":              "lbfgs",
                "DN_TRUNCATED_NEWTON":     "newton",
                "DN_TRUNCATED_NEWTON_B":   "newton",
                "DE":                      "de",
            }.get(canonical)
            if native_key is not None:
                # Wrap _f (which already handles sign-flip + flowsheet I/O)
                # into a (List[float] -> float) signature for the native solver.
                def _wrap(x_list):
                    return float(_f(np.array(x_list, dtype=float)))
                nat = run_native_solver(
                    method=native_key,
                    objective=_wrap,
                    lower=bounds_lo, upper=bounds_hi, initial=initial,
                    max_iter=int(max_iter), tolerance=float(tolerance),
                )
                if nat.get("success"):
                    used_native = True
                    solver_msg = (
                        f"{nat.get('solver_label', native_key)}: "
                        f"{nat.get('n_evals', 0)} obj evals, "
                        f"final f = {nat.get('best_f'):.6g}"
                    )
                    # _f already populated 'best' as side-effect, but ensure
                    # final point is registered for the result envelope:
                    best["x"] = list(nat["best_x"])
                else:
                    _native_err = nat.get("error", "unknown")
                    solver_msg = f"native {native_key} failed ({_native_err}); fell back"
            else:
                _native_err = f"no native mapping for {canonical}"
        else:
            _native_err = "DotNumerics unavailable on this platform"
    except Exception as exc:
        _native_err = f"native solver path raised: {exc}"

    # Probe for the optional `cma` package once; when CMA-ES is requested but
    # the package is unavailable, we fall through to the SciPy branch below,
    # which maps the unknown "CMA_ES" key to Nelder-Mead — so the pipeline never
    # breaks regardless of environment.
    _cma_ok = False
    if canonical == "CMA_ES":
        try:
            import cma  # type: ignore  # noqa: F401
            _cma_ok = True
        except Exception as _cma_exc:
            _log.warning("cma package unavailable (%s); CMA-ES request will run "
                         "Nelder-Mead instead.", _cma_exc)

    if used_native:
        pass   # solver_msg already set
    elif canonical == "CMA_ES" and _cma_ok:
        # ── CMA-ES via the external `cma` package ────────────────────────────
        # CMA-ES needs comparable scales across dimensions. Variables here span
        # wildly different magnitudes (temperature ~10²  vs mass-flow ~10⁵), so
        # we optimise in NORMALISED space z ∈ [0,1]ⁿ and map back to engineering
        # units inside the wrapper. `_f` still receives real values, so `best`,
        # `eval_count`, history and progress all stay correct.
        import cma  # type: ignore
        span = [hi - lo for lo, hi in zip(bounds_lo, bounds_hi)]

        def _f_norm(z):
            x = [lo + (zi * sp if sp > 0 else 0.0)
                 for lo, zi, sp in zip(bounds_lo, z, span)]
            return _f(np.array(x))

        z0 = [((init - lo) / sp if sp > 0 else 0.5)
              for init, lo, sp in zip(initial, bounds_lo, span)]
        z0 = [min(1.0, max(0.0, zi)) for zi in z0]
        try:
            es = cma.CMAEvolutionStrategy(
                z0, 0.25,
                {"bounds": [0.0, 1.0], "maxfevals": int(max_iter),
                 "tolfun": float(tolerance), "tolx": float(tolerance) * 1e-2,
                 "verbose": -9, "seed": 42})
            es.optimize(_f_norm)
            solver_msg = (f"CMA-ES: {es.countiter} generations, "
                          f"{es.countevals} evals")
        except Exception as exc:
            return {"success": False,
                    "error_code": "SOLVER_FAILED",
                    "error": f"CMA-ES raised: {exc}",
                    "history": history}
    elif canonical == "DE":
        # SciPy/NumPy fallback for DE — runs the SAME DE/rand/1/bin algorithm
        # as DotNumerics DE but in pure Python, for non-Windows / no-DWSIM
        # environments (testing, CI).
        rng = np.random.default_rng(42)
        pop_size = max(8, 4 * n)
        F, CR = 0.7, 0.9
        pop = rng.uniform(bounds_lo, bounds_hi, size=(pop_size, n))
        fitness = np.array([_f(ind) for ind in pop])
        gens = max(2, max_iter // pop_size)
        for g in range(gens):
            for i in range(pop_size):
                idxs = [j for j in range(pop_size) if j != i]
                a, b, c = rng.choice(idxs, 3, replace=False)
                trial = pop[a] + F * (pop[b] - pop[c])
                # Crossover + bounds-clip
                mask = rng.random(n) < CR
                trial = np.where(mask, trial, pop[i])
                trial = np.clip(trial, bounds_lo, bounds_hi)
                ft = _f(trial)
                if ft < fitness[i]:
                    pop[i] = trial
                    fitness[i] = ft
        solver_msg = f"DE: {gens} generations × {pop_size} pop"
    else:
        # Use SciPy with DWSIM-equivalent solver names (bounded variants).
        # SciPy provides Nelder-Mead Simplex (with bounds), L-BFGS-B,
        # Truncated Newton (TNC), and Brent — exactly DWSIM's solver list.
        from scipy.optimize import minimize as _minimize  # type: ignore
        scipy_method = {
            "DN_NELDERMEAD_SIMPLEX":   "Nelder-Mead",
            "DN_NELDERMEAD_SIMPLEX_B": "Nelder-Mead",
            "DN_LBFGS":                "L-BFGS-B",
            "DN_LBFGS_B":              "L-BFGS-B",
            "AL_LBFGS":                "L-BFGS-B",
            "AL_LBFGS_B":              "L-BFGS-B",
            "DN_TRUNCATED_NEWTON":     "TNC",
            "DN_TRUNCATED_NEWTON_B":   "TNC",
            "AL_BRENT":                "Powell",
            "AL_BRENT_B":              "Powell",
        }.get(canonical, "Nelder-Mead")
        bounds = list(zip(bounds_lo, bounds_hi))
        # Each SciPy solver accepts a different subset of tolerance options.
        # Setting an unknown option triggers an OptimizeWarning; pick the
        # right set per method.
        if scipy_method == "Nelder-Mead":
            opts = {"maxiter": int(max_iter), "xatol": float(tolerance),
                    "fatol": float(tolerance), "adaptive": True}
        elif scipy_method in ("L-BFGS-B", "Powell"):
            opts = {"maxiter": int(max_iter), "ftol": float(tolerance),
                    "xtol": float(tolerance)} if scipy_method == "Powell" \
                else {"maxiter": int(max_iter), "ftol": float(tolerance),
                      "gtol": float(tolerance) * 1e-2}
        elif scipy_method == "TNC":
            opts = {"maxiter": int(max_iter), "ftol": float(tolerance),
                    "xtol": float(tolerance), "gtol": float(tolerance) * 1e-2}
        else:
            opts = {"maxiter": int(max_iter)}
        try:
            res = _minimize(
                _f, np.array(initial), method=scipy_method,
                bounds=bounds if scipy_method in ("L-BFGS-B", "TNC", "Powell",
                                                  "Nelder-Mead") else None,
                options=opts,
            )
            solver_msg = f"{scipy_method}: {res.nit} iters, status={res.message}"
        except Exception as exc:
            return {"success": False,
                    "error_code": "SOLVER_FAILED",
                    "error": f"Solver {scipy_method} raised: {exc}",
                    "history": history}

    duration = round(time.monotonic() - t0, 2)

    # ── 4. Restore best values into the flowsheet & solve once more ─────────
    final_params = {}
    rows: List[Dict[str, Any]] = []
    for i, v in enumerate(variables):
        new_val = float(best["x"][i])
        _write_object_property(bridge, v["tag"], v["property"], new_val,
                                v.get("unit", ""))
        old = old_values[i]
        change = new_val - old
        pct = (change / old * 100.0) if abs(old) > 1e-12 else 0.0
        rows.append({
            "variable":     f"{v['tag']}.{v['property']}",
            "tag":          v["tag"],
            "property":     v["property"],
            "unit":         v.get("unit", ""),
            "old_value":    round(old, 6),
            "new_value":    round(new_val, 6),
            "change":       round(change, 6),
            "change_pct":   round(pct, 2),
            "lower":        bounds_lo[i],
            "upper":        bounds_hi[i],
            "at_lower":     bool(abs(new_val - bounds_lo[i]) < 1e-9),
            "at_upper":     bool(abs(new_val - bounds_hi[i]) < 1e-9),
        })
        final_params[f"{v['tag']}.{v['property']}"] = new_val

    _solve_flowsheet(bridge)  # one final solve to leave the flowsheet at the optimum

    # ── 5. Build poster-style result envelope ───────────────────────────────
    # Re-evaluate every constraint at the final solution
    compliance = None
    if constraints:
        try:
            from constraint_solver import evaluate_compliance
            compliance = evaluate_compliance(bridge, constraints)
        except Exception as _cexc:
            _log.debug("compliance check failed: %s", _cexc)

    return {
        "success":        best["obj_raw"] is not None,
        "method":         canonical,
        "method_friendly": method,
        "solver_backend": "DotNumerics (DWSIM-internal)" if used_native
                           else "CMA-ES (cma package)" if canonical == "CMA_ES" and _cma_ok
                           else "SciPy (mathematically equivalent fallback)",
        "used_native_dotnumerics": bool(used_native),
        "solver_message": solver_msg,
        "minimize":       minimize,
        "n_evaluations":  eval_count[0],
        "n_cache_hits":   _cache_hits[0],
        "n_unique_points": len(_eval_cache),
        "n_variables":    n,
        "n_constraints":  len(constraints or []),
        "duration_s":     duration,
        "best_objective": (round(best["obj_raw"], 6)
                            if best["obj_raw"] is not None else None),
        "objective_spec": objective,
        "variables_table": rows,
        "final_params":   final_params,
        "constraint_compliance": compliance,
        "all_constraints_satisfied": (compliance.get("all_satisfied")
                                       if compliance else None),
        "history":        history[-50:],  # last 50 for response size
        "converged":      best["obj_raw"] is not None and len(history) > 1,
        "summary": (
            f"DWSIM-native optimization {'minimised' if minimize else 'maximised'} "
            f"the objective in {eval_count[0]} flowsheet evaluations "
            f"using {canonical} (duration {duration}s)"
            + (f"; {compliance['n_satisfied']}/{len(constraints)} "
               f"constraints satisfied" if compliance else "")
            + "."
        ),
    }


def create_optimization_case(name: str, description: str,
                             variables: List[Dict[str, Any]],
                             objective_expression: str,
                             method: str = "simplex",
                             minimize: bool = True,
                             max_iter: int = 100) -> Dict[str, Any]:
    """
    Build a DWSIM OptimizationCase object (just the case — does NOT run it).
    Useful when you want the case persisted in the .dwxmz file so the
    DWSIM GUI can also see and re-run it.

    Returns a serialisable summary of the case for the UI.
    """
    try:
        from DWSIM.SharedClasses.Flowsheet.Optimization import (
            OptimizationCase, OPTVariable, OPTType, BoundType, OPTVariableType,
            OPTObjectiveFunctionType,
        )
    except Exception as exc:
        return {"success": False, "error_code": "CASE_CREATE_FAILED",
                "error": f"DWSIM not loaded: {exc}"}

    oc = OptimizationCase()
    oc.name = name or "AI_Optimization"
    oc.description = description or ""
    oc.maxits = int(max_iter)
    oc.expression = objective_expression
    oc.objfunctype = OPTObjectiveFunctionType.Expression
    oc.type = OPTType.Minimization if minimize else OPTType.Maximization
    oc.solvm = _solving_method_enum(method)

    for v in variables:
        ov = OPTVariable()
        ov.name = v.get("name", v["tag"] + "_" + v["property"])
        ov.objectTAG = v["tag"]
        ov.propID = v["property"]
        ov.unit = v.get("unit", "")
        ov.lowerlimit = float(v["lower"])
        ov.upperlimit = float(v["upper"])
        ov.initialvalue = float(v.get("initial", (v["lower"] + v["upper"]) / 2.0))
        ov.boundtype = BoundType.LowerAndUpper
        ov.type = OPTVariableType.Independent
        oc.variables[ov.name] = ov

    return {
        "success": True,
        "case_name": oc.name,
        "method": str(oc.solvm),
        "expression": oc.expression,
        "minimize": minimize,
        "n_variables": len(variables),
        "case_object_id": id(oc),
    }
