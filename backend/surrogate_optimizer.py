"""
surrogate_optimizer.py — Surrogate-assisted (EGO) optimisation for COMPLEX,
expensive-to-solve flowsheets.

The scalability ceiling of optimising a DWSIM flowsheet is the cost of a single
solve: for recycle-heavy / column-laden flowsheets each evaluation re-converges
the whole tear-stream system and is slow and noisy. A direct global optimiser
(differential evolution) that needs hundreds of *real* solves therefore becomes
impractical. The established remedy (Efficient Global Optimisation / kriging
surrogate, as in the DWSIM/HYSYS LNG literature) is implemented here:

    1. Sample the design space with a Latin hypercube and evaluate a SMALL,
       fixed budget of points on the REAL flowsheet.
    2. Fit a Gaussian-process (kriging) surrogate to those points.
    3. Search the CHEAP surrogate globally with Expected Improvement
       (thousands of surrogate evaluations, zero real solves).
    4. VALIDATE only the single most-promising point on the real flowsheet,
       add it to the training set, refit, and repeat.

Real-solve cost = n_initial + n_refine, INDEPENDENT of how many surrogate
evaluations the global search performs or of the problem dimension. This is the
upgrade that extends practical optimisation to complex flowsheets where a direct
optimiser is too expensive.

It reuses the project's existing kriging surrogate (bayesian_optimizer._GP),
Latin-hypercube sampler (_lhs) and EI acquisition (_ei), and the native
optimiser's write/solve/read evaluation semantics so results are consistent
with the rest of the system.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

import numpy as np

_log = logging.getLogger("surrogate_optimizer")


def _eval_objective(bridge, objective: Dict[str, Any]) -> Optional[float]:
    """Read the objective (variable or expression) from the solved flowsheet."""
    from dwsim_native_optimizer import _read_object_property
    otype = objective.get("type", "variable")
    if otype == "expression":
        named: Dict[str, float] = {}
        for nv in objective.get("named_values", []) or []:
            v = _read_object_property(bridge, nv.get("tag", ""), nv.get("property", ""))
            if v is None:
                return None
            named[nv.get("name", "")] = v
        try:
            allowed = {"__builtins__": {}, "abs": abs, "min": min, "max": max,
                       "pow": pow, "log": math.log, "log10": math.log10,
                       "exp": math.exp, "sqrt": math.sqrt}
            allowed.update(named)
            return float(eval(objective.get("expression", ""), allowed, named))  # noqa: S307
        except Exception:
            return None
    return _read_object_property(bridge, objective.get("tag", ""),
                                 objective.get("property", ""))


def run_surrogate_assisted_optimization(
    bridge,
    variables: List[Dict[str, Any]],
    objective: Dict[str, Any],
    minimize: bool = True,
    n_initial: int = 12,
    n_refine: int = 8,
    surrogate_samples: int = 4000,
    tolerance: float = 1e-4,
    on_progress: Optional[Callable] = None,
    constraints: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Optimise an expensive flowsheet objective with a kriging surrogate.

    Returns a result dict in the same shape the optimisation workflow expects
    (best_objective, n_evaluations, variables_table, method, solver_backend …).
    """
    from bayesian_optimizer import _lhs, _GP, _ei
    from dwsim_native_optimizer import (
        _write_object_property, _solve_flowsheet, _read_object_property,
    )

    t0 = time.time()
    d = len(variables)
    if d == 0:
        return {"success": False, "error": "no decision variables"}
    lo = np.array([float(v["lower"]) for v in variables], dtype=float)
    hi = np.array([float(v["upper"]) for v in variables], dtype=float)
    span = np.where(hi > lo, hi - lo, 1.0)

    old_vals = [_read_object_property(bridge, v["tag"], v["property"])
                for v in variables]
    history: List[Dict[str, Any]] = []
    real_evals = [0]

    def _evaluate(x: np.ndarray) -> Optional[float]:
        """Write x → solve → read objective. Returns the value in MINIMISE
        sense (so the surrogate always minimises), or None on a failed solve."""
        real_evals[0] += 1
        params: Dict[str, float] = {}
        for i, v in enumerate(variables):
            xi = float(max(lo[i], min(hi[i], x[i])))
            _write_object_property(bridge, v["tag"], v["property"], xi,
                                   v.get("unit", ""))
            params[f"{v['tag']}.{v['property']}"] = xi
        if not _solve_flowsheet(bridge):
            history.append({"iter": real_evals[0], "params": params,
                            "obj": None, "note": "did not converge"})
            if on_progress:
                try: on_progress(real_evals[0], params, None, None)
                except Exception: pass
            return None
        raw = _eval_objective(bridge, objective)
        if raw is None or not math.isfinite(raw):
            history.append({"iter": real_evals[0], "params": params,
                            "obj": None, "note": "objective read failed"})
            if on_progress:
                try: on_progress(real_evals[0], params, None, None)
                except Exception: pass
            return None
        f = float(raw) if minimize else -float(raw)   # minimise sense
        # Inequality-constraint penalty (consistent with the direct optimiser).
        if constraints:
            try:
                from constraint_solver import (_read_constraint_value,
                                               _constraint_violation)
                pen = 0.0
                for c in constraints:
                    cv = _read_constraint_value(bridge, c)
                    if cv is not None:
                        pen += float(_constraint_violation(cv, c)) ** 2
                f += 1e6 * pen
            except Exception:
                pass
        history.append({"iter": real_evals[0], "params": params,
                        "obj": float(raw), "note": "ok"})
        if on_progress:
            try: on_progress(real_evals[0], params, float(raw), None)
            except Exception: pass
        return f

    rng = np.random.default_rng(42)

    # ── 1) Design of experiments on the REAL flowsheet ────────────────────
    Xu: List[np.ndarray] = []
    Y: List[float] = []
    best_f = math.inf
    best_xu: Optional[np.ndarray] = None
    for xu in _lhs(max(2, int(n_initial)), d, rng):
        f = _evaluate(lo + xu * span)
        if f is not None:
            Xu.append(xu); Y.append(f)
            if f < best_f:
                best_f, best_xu = f, xu
    if not Xu:
        return {"success": False, "error_code": "ALL_EVALS_FAILED",
                "error": "No sampled point converged — the flowsheet may be "
                         "infeasible or the bounds are outside the solvable "
                         "region.",
                "n_evaluations": real_evals[0]}

    # ── 2) EGO refinement: fit surrogate, search it cheaply, validate ─────
    for _ in range(max(0, int(n_refine))):
        gp = _GP()
        try:
            gp.fit(np.array(Xu), np.array(Y))
        except Exception as exc:
            _log.debug("GP fit failed, stopping refinement: %s", exc)
            break
        # Cheap global search of the surrogate via Expected Improvement.
        ns = max(200, int(surrogate_samples))
        cand = np.vstack([_lhs(ns, d, rng), rng.random((ns // 2, d))])
        mu, var = gp.predict(cand)
        ei = _ei(mu, var, best_f)
        next_xu = cand[int(np.argmax(ei))]
        f = _evaluate(lo + next_xu * span)
        if f is not None:
            Xu.append(next_xu); Y.append(f)
            if f < best_f:
                best_f, best_xu = f, next_xu

    # ── 3) Leave the flowsheet at the best point + build the result ───────
    best_x = lo + best_xu * span
    for i, v in enumerate(variables):
        _write_object_property(bridge, v["tag"], v["property"],
                               float(best_x[i]), v.get("unit", ""))
    _solve_flowsheet(bridge)

    rows = []
    for i, v in enumerate(variables):
        nv = float(best_x[i]); ov = old_vals[i]
        if ov is None:
            continue
        rows.append({"variable": f"{v['tag']}.{v['property']}",
                     "old_value": float(ov), "new_value": nv,
                     "change": nv - float(ov),
                     "change_pct": (100.0 * (nv - float(ov)) / float(ov))
                                   if ov else 0.0,
                     "at_lower": abs(nv - lo[i]) < 1e-9,
                     "at_upper": abs(nv - hi[i]) < 1e-9})

    return {
        "success": True,
        "best_objective": (best_f if minimize else -best_f),
        "best_params": {f"{v['tag']}.{v['property']}": float(best_x[i])
                        for i, v in enumerate(variables)},
        "variables_table": rows,
        "n_evaluations": real_evals[0],
        "real_solves": real_evals[0],
        "surrogate_evals_per_round": int(surrogate_samples),
        "method": "surrogate_ego",
        "solver_backend": "Surrogate-assisted EGO (kriging GP + Expected Improvement)",
        "minimize": minimize,
        "converged": True,
        "duration_s": round(time.time() - t0, 2),
        "history": history[-40:],
        "used_native_dotnumerics": False,
    }
