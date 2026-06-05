"""
optimizer.py  -  LLM-Driven Process Optimization Engine
────────────────────────────────────────────────────────
Methods:
  golden_section  — fast single-variable min/max (O(log n) evals)
  grid_refine     — coarse grid → zoom → refine (robust, handles noise)
  nelder_mead     — multi-variable simplex (uses scipy if available,
                    falls back to a pure-Python downhill simplex)

Constraint support:
  Pass constraints=[{"tag": "...", "property": "...", "min": x, "max": y}]
  to any method. Points violating constraints receive an infeasibility penalty.

Early stopping:
  Stagnation is detected when the best objective does not improve by more
  than `stagnation_tol` (default 1e-6) for `stagnation_rounds` consecutive
  iterations (default 5).
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple


# ── Constraint helpers ────────────────────────────────────────────────────────

def _check_constraints(bridge, constraints: List[Dict]) -> float:
    """
    Evaluate constraint violations.  Returns 0.0 when all are satisfied.
    Returns a positive penalty proportional to the total violation otherwise.
    """
    if not constraints:
        return 0.0
    penalty = 0.0
    for c in constraints:
        tag  = c.get("tag", "")
        prop = c.get("property", "")
        if not tag or not prop:
            continue
        try:
            r = bridge.get_stream_properties(tag)
            if not r.get("success"):
                # Try unit-op properties
                r = bridge.get_object_properties(tag)
            props = r.get("properties", {}) or {}
            val = props.get(prop)
            if val is None:
                # Try summary dict (unit-ops)
                summary = props.get("summary", {})
                for k, v in summary.items():
                    if prop.lower() in k.lower():
                        val = v
                        break
            if val is None:
                continue
            fval = float(val)
            lo = c.get("min")
            hi = c.get("max")
            if lo is not None and fval < float(lo):
                penalty += (float(lo) - fval) ** 2
            if hi is not None and fval > float(hi):
                penalty += (fval - float(hi)) ** 2
        except Exception:
            pass
    return penalty


# ── Stagnation tracker ────────────────────────────────────────────────────────

class _StagnationGuard:
    def __init__(self, tol: float = 1e-6, rounds: int = 5):
        self._tol    = tol
        self._rounds = rounds
        self._best   = None
        self._count  = 0

    def update(self, value: Optional[float]) -> bool:
        """Returns True when stagnation is detected (caller should break loop)."""
        if value is None:
            return False
        if self._best is None or abs(value - self._best) > self._tol:
            self._best  = value
            self._count = 0
        else:
            self._count += 1
        return self._count >= self._rounds


# ─────────────────────────────────────────────────────────────────────────────

class DWSIMOptimizer:

    def __init__(self, bridge):
        self.bridge = bridge

    # ── Public API ─────────────────────────────────────────

    def optimize(
        self,
        vary_tag: str,
        vary_property: str,
        vary_unit: str = "",
        observe_tag: str = "",
        observe_property: str = "",
        objective: str = "minimize",
        lower_bound: float = 0,
        upper_bound: float = 1000,
        tolerance: float = 0.01,
        max_iterations: int = 25,
        method: str = "golden_section",
        constraints: Optional[List[Dict]] = None,
        stagnation_tol: float = 1e-6,
        stagnation_rounds: int = 5,
    ) -> dict:
        """
        Single-variable optimization.

        Parameters
        ----------
        constraints : list of dicts, optional
            Each dict: {"tag": "...", "property": "...", "min": x, "max": y}
            Points violating any constraint are penalised.
        stagnation_tol / stagnation_rounds : float / int
            Early stop when best objective barely changes for N consecutive iters.
        """
        _t0 = time.time()
        if not vary_tag or not observe_tag:
            return {"success": False,
                    "error": "Both vary_tag and observe_tag are required."}
        if lower_bound >= upper_bound:
            return {"success": False,
                    "error": f"Invalid bounds: [{lower_bound}, {upper_bound}]"}

        sign = -1.0 if objective.lower().startswith("max") else 1.0
        context = {
            "vary_tag": vary_tag,
            "vary_property": vary_property,
            "vary_unit": vary_unit,
            "observe_tag": observe_tag,
            "observe_property": observe_property,
            "sign": sign,
            "objective": objective,
            "constraints": constraints or [],
        }

        try:
            if method == "grid_refine":
                result = self._grid_refine(
                    context, lower_bound, upper_bound,
                    tolerance, max_iterations, stagnation_tol, stagnation_rounds)
            else:
                result = self._golden_section(
                    context, lower_bound, upper_bound,
                    tolerance, max_iterations, stagnation_tol, stagnation_rounds)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        result["elapsed_seconds"] = round(time.time() - _t0, 2)
        return result

    def multi_optimize(
        self,
        variables: List[Dict],
        observe_tag: str,
        observe_property: str,
        objective: str = "minimize",
        max_iterations: int = 200,
        tolerance: float = 1e-4,
        method: str = "nelder_mead",
        constraints: Optional[List[Dict]] = None,
        stagnation_tol: float = 1e-6,
        stagnation_rounds: int = 10,
    ) -> dict:
        """
        Multi-variable optimization using Nelder-Mead simplex.

        Parameters
        ----------
        variables : list of dicts
            Each dict: {"tag": "...", "property": "...", "unit": "...",
                        "lower": x, "upper": y, "initial": z}
            'initial' is optional; defaults to midpoint of [lower, upper].
        observe_tag : str
        observe_property : str
        objective : "minimize" | "maximize"
        method : "nelder_mead" | "grid_scan"
            "grid_scan" does an n-dimensional coarse scan then refines with nelder_mead.
        constraints : list of constraint dicts (same format as optimize())
        """
        _t0 = time.time()
        if not variables:
            return {"success": False, "error": "variables list is empty"}
        if not observe_tag:
            return {"success": False, "error": "observe_tag is required"}

        for v in variables:
            if v.get("lower", 0) >= v.get("upper", 1):
                return {"success": False,
                        "error": f"Invalid bounds for variable '{v.get('tag')}': "
                                 f"[{v.get('lower')}, {v.get('upper')}]"}

        sign = -1.0 if objective.lower().startswith("max") else 1.0

        x0 = []
        lbs, ubs = [], []
        for v in variables:
            lo, hi = float(v.get("lower", 0)), float(v.get("upper", 1))
            init = float(v.get("initial", (lo + hi) / 2))
            x0.append(max(lo, min(hi, init)))
            lbs.append(lo)
            ubs.append(hi)

        history = []

        def objective_fn(x):
            # Clip to bounds
            xc = [max(lbs[i], min(ubs[i], x[i])) for i in range(len(x))]

            # Set all variables
            for i, v in enumerate(variables):
                r = self.bridge.set_stream_property(
                    v["tag"], v["property"], xc[i], v.get("unit", ""))
                if not r.get("success"):
                    self.bridge.set_unit_op_property(
                        v["tag"], v["property"], str(xc[i]), v.get("unit", ""))

            # Solve
            sim = self.bridge.run_simulation()
            if not sim.get("success"):
                return None

            # Read objective
            results = self.bridge.get_simulation_results()
            if not results.get("success"):
                return None
            stream = results.get("stream_results", {}).get(observe_tag, {})
            val = stream.get(observe_property)
            if val is None:
                op_props = self.bridge.get_object_properties(observe_tag)
                if op_props.get("success"):
                    summary = op_props.get("properties", {}).get("summary", {})
                    for k, v_ in summary.items():
                        if observe_property.lower() in k.lower():
                            val = v_
                            break
            if val is None:
                return None

            # Constraint penalty
            penalty = _check_constraints(self.bridge, constraints or [])

            signed_obj = float(val) * sign + penalty * 1e6
            history.append({
                "x": [round(v, 6) for v in xc],
                "objective": round(float(val), 6),
                "signed": round(signed_obj, 8),
                "feasible": penalty == 0.0,
            })
            # Cap history to avoid unbounded memory growth
            if len(history) > 500:
                history[:] = history[-500:]
            return signed_obj

        try:
            result_x, result_f, n_evals = self._nelder_mead(
                objective_fn, x0, lbs, ubs,
                max_iter=max_iterations,
                tol=tolerance,
                stag_tol=stagnation_tol,
                stag_rounds=stagnation_rounds,
            )
        except Exception as exc:
            return {"success": False, "error": f"Nelder-Mead failed: {exc}"}

        # Read final objective (raw, unsigned)
        feasible_pts = [h for h in history if h.get("feasible", True)]
        best_history = (min(feasible_pts, key=lambda h: h["signed"] * sign)
                        if feasible_pts else None)

        return {
            "success": True,
            "method": "nelder_mead",
            "objective": objective,
            "optimal_values": {
                variables[i]["tag"] + "." + variables[i]["property"]: round(result_x[i], 6)
                for i in range(len(variables))
            },
            "optimal_objective": round(result_f / sign, 6) if result_f is not None else None,
            "observe": f"{observe_tag}.{observe_property}",
            "iterations": len(history),
            "function_evaluations": n_evals,
            "constraints_satisfied": not any(
                not h.get("feasible", True) for h in history[-5:]
            ) if history else True,
            "best_history_point": best_history,
            "history": history[-50:],  # keep last 50 to avoid huge response
            "elapsed_seconds": round(time.time() - _t0, 2),
        }

    def evaluate_point(
        self,
        vary_tag: str,
        vary_property: str,
        vary_unit: str = "",
        value: float = 0,
        observe_tag: str = "",
        observe_property: str = "",
    ) -> dict:
        """Evaluate a single operating point. Useful for LLM-guided exploration."""
        obj = self._evaluate(
            vary_tag, vary_property, vary_unit, value,
            observe_tag, observe_property)

        if obj is None:
            return {
                "success": False,
                "error": f"Simulation failed at {vary_property}={value}",
                "value": value,
            }
        return {
            "success": True,
            "value": value,
            "objective_value": obj,
            "vary": f"{vary_tag}.{vary_property} = {value} {vary_unit}",
            "observe": f"{observe_tag}.{observe_property} = {obj}",
        }

    def multi_objective_optimize(
        self,
        variables:  List[Dict],
        objectives: List[Dict],
        weights:    Optional[List[float]] = None,
        max_iterations: int = 200,
        tolerance:  float = 1e-4,
        constraints: Optional[List[Dict]] = None,
    ) -> dict:
        """
        Weighted multi-objective optimization via Nelder-Mead.

        objectives: list of dicts, each:
            {"tag": "...", "property": "...", "goal": "minimize"|"maximize", "weight": 1.0}
        weights: optional override for objective weights (must match len(objectives)).
        """
        _t0 = time.time()
        if not variables:
            return {"success": False, "error": "variables list is empty"}
        if not objectives:
            return {"success": False, "error": "objectives list is empty"}

        n_obj = len(objectives)
        w = weights if weights and len(weights) == n_obj else [
            o.get("weight", 1.0) for o in objectives
        ]
        w_total = sum(abs(x) for x in w) or 1.0
        w = [x / w_total for x in w]
        signs = [-1.0 if o.get("goal", "minimize").startswith("max") else 1.0
                 for o in objectives]

        x0, lbs, ubs = [], [], []
        for v in variables:
            lo, hi = float(v.get("lower", 0)), float(v.get("upper", 1))
            init = float(v.get("initial", (lo + hi) / 2))
            x0.append(max(lo, min(hi, init)))
            lbs.append(lo)
            ubs.append(hi)

        history = []

        def compound_fn(x):
            xc = [max(lbs[i], min(ubs[i], x[i])) for i in range(len(x))]
            for i, v in enumerate(variables):
                r = self.bridge.set_stream_property(
                    v["tag"], v["property"], xc[i], v.get("unit", ""))
                if not r.get("success"):
                    self.bridge.set_unit_op_property(
                        v["tag"], v["property"], str(xc[i]), v.get("unit", ""))

            sim = self.bridge.run_simulation()
            if not sim.get("success"):
                return None

            results = self.bridge.get_simulation_results()
            obj_vals = []
            for o in objectives:
                val = None
                sr = results.get("stream_results", {}).get(o["tag"], {})
                val = sr.get(o["property"])
                if val is None:
                    op_props = self.bridge.get_object_properties(o["tag"])
                    if op_props.get("success"):
                        for k, v_ in op_props.get("properties", {}).get("summary", {}).items():
                            if o["property"].lower() in k.lower():
                                val = v_
                                break
                obj_vals.append(float(val) if val is not None else None)

            if any(v is None for v in obj_vals):
                return None

            penalty = _check_constraints(self.bridge, constraints or [])
            composite = sum(w[i] * signs[i] * obj_vals[i] for i in range(n_obj))
            composite += penalty * 1e6

            history.append({
                "x": [round(v, 6) for v in xc],
                "objectives": {f"{objectives[i]['tag']}.{objectives[i]['property']}": round(obj_vals[i], 6)
                               for i in range(n_obj)},
                "composite": round(composite, 8),
                "feasible": penalty == 0.0,
            })
            # Cap history to avoid unbounded memory growth
            if len(history) > 500:
                history[:] = history[-500:]
            return composite

        try:
            result_x, result_f, n_evals = self._nelder_mead(
                compound_fn, x0, lbs, ubs,
                max_iter=max_iterations, tol=tolerance,
            )
        except Exception as exc:
            return {"success": False, "error": f"Multi-objective optimization failed: {exc}"}

        return {
            "success": True,
            "method": "weighted_nelder_mead",
            "optimal_values": {
                variables[i]["tag"] + "." + variables[i]["property"]: round(result_x[i], 6)
                for i in range(len(variables))
            },
            "optimal_composite": round(result_f, 6) if result_f is not None else None,
            "objectives": [
                {"tag": o["tag"], "property": o["property"],
                 "goal": o.get("goal", "minimize"), "weight": round(w[i], 4)}
                for i, o in enumerate(objectives)
            ],
            "iterations": len(history),
            "function_evaluations": n_evals,
            "history": history[-50:],
            "elapsed_seconds": round(time.time() - _t0, 2),
        }

    # ── Single-variable Algorithms ─────────────────────────────────────────────

    def _golden_section(self, ctx, a, b, tol, max_iter,
                        stag_tol=1e-6, stag_rounds=5):
        """
        Golden section search for unimodal functions.
        Reduces interval by factor 0.618 each iteration.
        Includes stagnation early-stopping and constraint handling.
        """
        PHI    = (1 + math.sqrt(5)) / 2
        RESPHI = 2 - PHI

        history = []
        sign  = ctx["sign"]
        guard = _StagnationGuard(stag_tol, stag_rounds)

        x1 = a + RESPHI * (b - a)
        x2 = b - RESPHI * (b - a)
        f1 = self._eval_signed(ctx, x1, sign)
        f2 = self._eval_signed(ctx, x2, sign)

        for val, fx in ((x1, f1), (x2, f2)):
            if fx is not None:
                history.append({
                    "iteration": len(history) + 1,
                    "value": round(val, 6),
                    "objective": round(fx / sign, 6),
                })
                if guard.update(fx):
                    break

        for i in range(3, max_iter + 1):
            if abs(b - a) < tol:
                break

            if f1 is None and f2 is None:
                mid = (a + b) / 2
                fm  = self._eval_signed(ctx, mid, sign)
                if fm is not None:
                    history.append({"iteration": i, "value": round(mid, 6),
                                    "objective": round(fm / sign, 6)})
                    if guard.update(fm):
                        break
                a = a + (b - a) * 0.25
                b = b - (b - a) * 0.25
                x1 = a + RESPHI * (b - a)
                x2 = b - RESPHI * (b - a)
                f1 = self._eval_signed(ctx, x1, sign)
                f2 = self._eval_signed(ctx, x2, sign)
                continue

            if f1 is None:
                f1 = f2 + abs(f2) * 0.1
            if f2 is None:
                f2 = f1 + abs(f1) * 0.1

            if f1 < f2:
                b = x2; x2 = x1; f2 = f1
                x1 = a + RESPHI * (b - a)
                f1 = self._eval_signed(ctx, x1, sign)
                if f1 is not None:
                    history.append({"iteration": i, "value": round(x1, 6),
                                    "objective": round(f1 / sign, 6)})
                    if guard.update(f1):
                        break
            else:
                a = x1; x1 = x2; f1 = f2
                x2 = b - RESPHI * (b - a)
                f2 = self._eval_signed(ctx, x2, sign)
                if f2 is not None:
                    history.append({"iteration": i, "value": round(x2, 6),
                                    "objective": round(f2 / sign, 6)})
                    if guard.update(f2):
                        break

        best = self._find_best(history, ctx["objective"])
        optimal_val = (a + b) / 2
        if best:
            optimal_val = best["value"]

        final_obj = self._evaluate(
            ctx["vary_tag"], ctx["vary_property"], ctx["vary_unit"],
            optimal_val, ctx["observe_tag"], ctx["observe_property"])

        return {
            "success": True,
            "method": "golden_section",
            "objective": ctx["objective"],
            "optimal_value": round(optimal_val, 6),
            "optimal_objective": round(final_obj, 6) if final_obj is not None else None,
            "vary": f"{ctx['vary_tag']}.{ctx['vary_property']}",
            "vary_unit": ctx["vary_unit"],
            "observe": f"{ctx['observe_tag']}.{ctx['observe_property']}",
            "iterations": len(history),
            "convergence": abs(b - a),
            "tolerance": tol,
            "bounds": [a, b],
            "history": history,
        }

    def _grid_refine(self, ctx, a, b, tol, max_iter,
                     stag_tol=1e-6, stag_rounds=5):
        """
        Two-phase: coarse grid scan → refine around best with golden section.
        Robust for noisy or multi-modal objectives.
        """
        sign   = ctx["sign"]
        history = []

        n_grid = min(10, max_iter // 2)
        step   = (b - a) / (n_grid - 1) if n_grid > 1 else (b - a)
        grid_results = []

        for i in range(n_grid):
            x   = a + i * step
            obj = self._evaluate(
                ctx["vary_tag"], ctx["vary_property"], ctx["vary_unit"],
                x, ctx["observe_tag"], ctx["observe_property"])
            entry = {"iteration": i + 1, "value": round(x, 6),
                     "objective": round(obj, 6) if obj is not None else None,
                     "phase": "grid"}
            history.append(entry)
            if obj is not None:
                grid_results.append((x, obj))

        if not grid_results:
            return {"success": False,
                    "error": "All grid points failed to converge.",
                    "history": history}

        if ctx["objective"].startswith("max"):
            best_x, best_obj = max(grid_results, key=lambda t: t[1])
        else:
            best_x, best_obj = min(grid_results, key=lambda t: t[1])

        refine_radius = step * 1.5
        ref_a  = max(a, best_x - refine_radius)
        ref_b  = min(b, best_x + refine_radius)
        remaining = max_iter - n_grid

        if remaining > 2 and (ref_b - ref_a) > tol:
            refine_result = self._golden_section(
                ctx, ref_a, ref_b, tol, remaining, stag_tol, stag_rounds)
            for entry in refine_result.get("history", []):
                entry["iteration"] += n_grid
                entry["phase"] = "refine"
                history.append(entry)

            if (refine_result.get("success") and
                    refine_result.get("optimal_objective") is not None):
                return {
                    "success": True,
                    "method": "grid_refine",
                    "objective": ctx["objective"],
                    "optimal_value": refine_result["optimal_value"],
                    "optimal_objective": refine_result["optimal_objective"],
                    "vary": refine_result["vary"],
                    "vary_unit": ctx["vary_unit"],
                    "observe": refine_result["observe"],
                    "iterations": len(history),
                    "grid_best": {"value": round(best_x, 6),
                                  "objective": round(best_obj, 6)},
                    "history": history,
                }

        return {
            "success": True,
            "method": "grid_refine",
            "objective": ctx["objective"],
            "optimal_value": round(best_x, 6),
            "optimal_objective": round(best_obj, 6),
            "vary": f"{ctx['vary_tag']}.{ctx['vary_property']}",
            "vary_unit": ctx["vary_unit"],
            "observe": f"{ctx['observe_tag']}.{ctx['observe_property']}",
            "iterations": len(history),
            "history": history,
        }

    # ── Multi-variable: Nelder-Mead simplex ───────────────────────────────────

    def _nelder_mead(
        self,
        fn,
        x0: List[float],
        lbs: List[float],
        ubs: List[float],
        max_iter: int = 200,
        tol: float = 1e-4,
        stag_tol: float = 1e-6,
        stag_rounds: int = 10,
    ) -> Tuple[List[float], Optional[float], int]:
        """
        Pure-Python Nelder-Mead downhill simplex (uses scipy if available).
        Returns (best_x, best_f, n_evaluations).
        """
        n = len(x0)

        # Try scipy first (better convergence)
        try:
            from scipy.optimize import minimize  # type: ignore
            bounds = list(zip(lbs, ubs))
            res = minimize(
                lambda x: fn(x) if fn(x) is not None else 1e30,
                x0, method="Nelder-Mead",
                options={"maxiter": max_iter, "xatol": tol, "fatol": tol},
                bounds=bounds,
            )
            best_x = [max(lbs[i], min(ubs[i], res.x[i])) for i in range(n)]
            return best_x, res.fun, res.nfev
        except ImportError:
            pass
        except Exception:
            pass

        # Pure-Python simplex fallback
        # α=1, γ=2, ρ=0.5, σ=0.5 (standard coefficients)
        alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
        n_evals = 0
        guard   = _StagnationGuard(stag_tol, stag_rounds)

        def safe_fn(x):
            nonlocal n_evals
            n_evals += 1
            v = fn(x)
            return v if v is not None else 1e30

        # Initialise simplex
        simplex = [list(x0)]
        for i in range(n):
            xi = list(x0)
            step = max(abs(x0[i]) * 0.05, 1e-3 * (ubs[i] - lbs[i]))
            xi[i] = min(ubs[i], x0[i] + step)
            simplex.append(xi)

        f_vals = [safe_fn(s) for s in simplex]

        for iteration in range(max_iter):
            # Sort
            order   = sorted(range(n + 1), key=lambda i: f_vals[i])
            simplex = [simplex[i] for i in order]
            f_vals  = [f_vals[i]  for i in order]

            if guard.update(f_vals[0]):
                break

            # Check convergence
            if max(abs(f_vals[i] - f_vals[0]) for i in range(1, n + 1)) < tol:
                break

            # Centroid (excluding worst)
            centroid = [sum(simplex[i][j] for i in range(n)) / n
                        for j in range(n)]

            # Reflection
            xr = [min(ubs[j], max(lbs[j],
                       centroid[j] + alpha * (centroid[j] - simplex[n][j])))
                  for j in range(n)]
            fr = safe_fn(xr)

            if f_vals[0] <= fr < f_vals[n - 1]:
                simplex[n] = xr
                f_vals[n]  = fr
                continue

            if fr < f_vals[0]:
                # Expansion
                xe = [min(ubs[j], max(lbs[j],
                           centroid[j] + gamma * (xr[j] - centroid[j])))
                      for j in range(n)]
                fe = safe_fn(xe)
                if fe < fr:
                    simplex[n] = xe; f_vals[n] = fe
                else:
                    simplex[n] = xr; f_vals[n] = fr
                continue

            # Contraction
            xc = [min(ubs[j], max(lbs[j],
                       centroid[j] + rho * (simplex[n][j] - centroid[j])))
                  for j in range(n)]
            fc = safe_fn(xc)
            if fc < f_vals[n]:
                simplex[n] = xc; f_vals[n] = fc
                continue

            # Shrink
            for i in range(1, n + 1):
                simplex[i] = [
                    min(ubs[j], max(lbs[j],
                        simplex[0][j] + sigma * (simplex[i][j] - simplex[0][j])))
                    for j in range(n)
                ]
                f_vals[i] = safe_fn(simplex[i])

        best_x = simplex[0]
        best_f = f_vals[0] if f_vals[0] < 1e29 else None
        return best_x, best_f, n_evals

    # ── Gradient-based optimization (SLSQP) ─────────────────────────────────

    def gradient_optimize(
        self,
        variables:      List[Dict],
        observe_tag:    str,
        observe_property: str,
        objective:      str = "minimize",
        max_iterations: int = 100,
        tolerance:      float = 1e-6,
        method:         str = "SLSQP",
        constraints:    Optional[List[Dict]] = None,
        equality_constraints: Optional[List[Dict]] = None,
    ) -> dict:
        """
        Gradient-based multi-variable optimization using scipy.

        Supports SLSQP (Sequential Least Squares Programming) which handles:
          - Bound constraints on all variables
          - Inequality constraints (min/max on any property)
          - Equality constraints: [{tag, property, target}]
          - Scales well to 10-20+ decision variables

        Also supports 'L-BFGS-B' and 'trust-constr' methods via scipy.

        equality_constraints: [{tag: str, property: str, target: float}]
            Forces observed property to equal a target value.
        """
        _t0 = time.time()
        try:
            from scipy.optimize import minimize as sp_minimize
        except ImportError:
            return {"success": False,
                    "error": "scipy is required for gradient_optimize. "
                             "Install with: pip install scipy. "
                             "Alternatively, use multi_optimize (Nelder-Mead, no scipy needed)."}

        if not variables:
            return {"success": False, "error": "variables list is empty"}

        sign = -1.0 if objective.lower().startswith("max") else 1.0

        x0, lbs, ubs = [], [], []
        for v in variables:
            lo, hi = float(v.get("lower", 0)), float(v.get("upper", 1))
            init = float(v.get("initial", (lo + hi) / 2))
            x0.append(max(lo, min(hi, init)))
            lbs.append(lo)
            ubs.append(hi)

        history = []
        n_evals = [0]

        def objective_fn(x):
            n_evals[0] += 1
            xc = [max(lbs[i], min(ubs[i], x[i])) for i in range(len(x))]

            for i, v in enumerate(variables):
                r = self.bridge.set_stream_property(
                    v["tag"], v["property"], xc[i], v.get("unit", ""))
                if not r.get("success"):
                    self.bridge.set_unit_op_property(
                        v["tag"], v["property"], str(xc[i]), v.get("unit", ""))

            sim = self.bridge.run_simulation()
            if not sim.get("success"):
                return 1e30

            results = self.bridge.get_simulation_results()
            if not results.get("success"):
                return 1e30
            stream = results.get("stream_results", {}).get(observe_tag, {})
            val = stream.get(observe_property)
            if val is None:
                op_props = self.bridge.get_object_properties(observe_tag)
                if op_props.get("success"):
                    summary = op_props.get("properties", {}).get("summary", {})
                    for k, v_ in summary.items():
                        if observe_property.lower() in k.lower():
                            val = v_
                            break
            if val is None:
                return 1e30

            obj_val = float(val) * sign
            history.append({
                "x": [round(v, 6) for v in xc],
                "objective": round(float(val), 6),
                "eval": n_evals[0],
            })
            return obj_val

        bounds = list(zip(lbs, ubs))

        # Build scipy constraint dicts
        scipy_constraints = []

        # Inequality constraints: min <= property <= max
        if constraints:
            for c in constraints:
                c_tag  = c.get("tag", "")
                c_prop = c.get("property", "")
                c_min  = c.get("min")
                c_max  = c.get("max")

                if c_min is not None:
                    def make_ineq_min(ct=c_tag, cp=c_prop, cmin=c_min):
                        def ineq_fn(x):
                            # Read-only: objective_fn already ran the simulation
                            # for this point, so just read the property value.
                            r = self.bridge.get_stream_properties(ct)
                            val = r.get("properties", {}).get(cp)
                            if val is None:
                                op = self.bridge.get_object_properties(ct)
                                if op.get("success"):
                                    for k, v_ in op.get("properties", {}).get("summary", {}).items():
                                        if cp.lower() in k.lower():
                                            val = v_; break
                            return float(val) - cmin if val is not None else -1e6
                        return ineq_fn
                    scipy_constraints.append({"type": "ineq", "fun": make_ineq_min()})

                if c_max is not None:
                    def make_ineq_max(ct=c_tag, cp=c_prop, cmax=c_max):
                        def ineq_fn(x):
                            # Read-only: objective_fn already ran the simulation
                            r = self.bridge.get_stream_properties(ct)
                            val = r.get("properties", {}).get(cp)
                            if val is None:
                                op = self.bridge.get_object_properties(ct)
                                if op.get("success"):
                                    for k, v_ in op.get("properties", {}).get("summary", {}).items():
                                        if cp.lower() in k.lower():
                                            val = v_; break
                            return cmax - float(val) if val is not None else -1e6
                        return ineq_fn
                    scipy_constraints.append({"type": "ineq", "fun": make_ineq_max()})

        # Equality constraints
        if equality_constraints:
            for ec in equality_constraints:
                ec_tag    = ec.get("tag", "")
                ec_prop   = ec.get("property", "")
                ec_target = ec.get("target", 0)

                def make_eq(ct=ec_tag, cp=ec_prop, tgt=ec_target):
                    def eq_fn(x):
                        # Read-only: objective_fn already ran the simulation
                        r = self.bridge.get_stream_properties(ct)
                        val = r.get("properties", {}).get(cp)
                        if val is None:
                            op = self.bridge.get_object_properties(ct)
                            if op.get("success"):
                                for k, v_ in op.get("properties", {}).get("summary", {}).items():
                                    if cp.lower() in k.lower():
                                        val = v_; break
                        return float(val) - tgt if val is not None else 1e6
                    return eq_fn
                scipy_constraints.append({"type": "eq", "fun": make_eq()})

        try:
            res = sp_minimize(
                objective_fn, x0,
                method=method,
                bounds=bounds,
                constraints=scipy_constraints if scipy_constraints else (),
                options={
                    "maxiter": max_iterations,
                    "ftol": tolerance,
                    "disp": False,
                },
            )
            best_x = [max(lbs[i], min(ubs[i], res.x[i]))
                       for i in range(len(variables))]

            return {
                "success": True,
                "method": method,
                "objective": objective,
                "optimal_values": {
                    variables[i]["tag"] + "." + variables[i]["property"]: round(best_x[i], 6)
                    for i in range(len(variables))
                },
                "optimal_objective": round(res.fun / sign, 6),
                "observe": f"{observe_tag}.{observe_property}",
                "scipy_success": res.success,
                "scipy_message": res.message,
                "iterations": res.nit,
                "function_evaluations": n_evals[0],
                "history": history[-50:],
                "elapsed_seconds": round(time.time() - _t0, 2),
            }
        except Exception as exc:
            return {"success": False, "error": f"{method} optimization failed: {exc}"}

    # ── Evaluation Helpers ─────────────────────────────────────────────────────

    def _evaluate(self, vary_tag, vary_prop, vary_unit, value,
                  obs_tag, obs_prop):
        """Set parameter → run simulation → read objective. Returns float or None."""
        try:
            r = self.bridge.set_stream_property(
                vary_tag, vary_prop, value, vary_unit)
            if not r.get("success"):
                r = self.bridge.set_unit_op_property(
                    vary_tag, vary_prop, str(value), vary_unit)
                if not r.get("success"):
                    return None

            sim = self.bridge.run_simulation()
            if not sim.get("success"):
                return None

            results = self.bridge.get_simulation_results()
            if not results.get("success"):
                return None

            stream = results.get("stream_results", {}).get(obs_tag, {})
            val    = stream.get(obs_prop)
            if val is None:
                op_props = self.bridge.get_object_properties(obs_tag)
                if op_props.get("success"):
                    summary = op_props.get("properties", {}).get("summary", {})
                    for key, v in summary.items():
                        if obs_prop.lower() in key.lower():
                            val = v
                            break
            return float(val) if val is not None else None
        except Exception:
            return None

    def _eval_signed(self, ctx, value, sign):
        """Evaluate and apply sign for min/max conversion. Applies constraint penalty."""
        raw = self._evaluate(
            ctx["vary_tag"], ctx["vary_property"], ctx["vary_unit"],
            value, ctx["observe_tag"], ctx["observe_property"])
        if raw is None:
            return None
        penalty = _check_constraints(self.bridge, ctx.get("constraints", []))
        return raw * sign + penalty * 1e6

    def _find_best(self, history, objective):
        """Find the best (feasible) point in history."""
        valid = [h for h in history if h.get("objective") is not None]
        if not valid:
            return None
        if objective.startswith("max"):
            return max(valid, key=lambda h: h["objective"])
        return min(valid, key=lambda h: h["objective"])
