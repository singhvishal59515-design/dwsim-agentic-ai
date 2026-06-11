"""
infeasible_path_optimizer.py
────────────────────────────
Infeasible-path (simultaneous tear + optimize) SQP — the central Aspen Plus
optimizer technique, brought to a sequential-modular DWSIM wrapper.

The problem it solves
---------------------
A flowsheet with a recycle has a TEAR stream (the loop is cut there). The normal
"feasible-path" approach fully CONVERGES the recycle (iterating the tear stream
to a fixed point — typically 5–20 inner passes) BEFORE every single objective
evaluation the optimizer asks for. So the cost is

    (outer optimization steps) × (inner recycle-convergence passes).

Infeasible-path (Biegler) instead promotes the tear-stream variables to DECISION
VARIABLES and adds the recycle CLOSURE equations as EQUALITY CONSTRAINTS:

    minimize   f(design, tear)
    subject to tear_out(design, tear) − tear = 0   (recycle closes)
               g(design, tear) {≤,≥,=} limit        (process constraints)
               bounds

An SQP solver then drives the objective AND the recycle residuals to convergence
SIMULTANEOUSLY — one flowsheet pass per evaluation, no inner loop. On
recycle-heavy flowsheets this cuts passes (hence wall-clock) by the inner-loop
factor, and it is more robust because the recycle never has to be fully
converged at infeasible intermediate designs.

This module is engine-agnostic: it takes a `one_pass(design_x, tear_x)` callable
that does ONE forward pass (set design + guessed tear, solve WITHOUT recycle
iteration, read the objective, the RECOMPUTED tear, and any constraint
quantities). That makes the method unit-testable on an analytic recycle with a
known optimum; `bridge.optimize_infeasible_path` is the DWSIM specialisation.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

OnePass = Callable[[Sequence[float], Sequence[float]], Dict[str, Any]]


def run_infeasible_path_optimization(
    one_pass: OnePass,
    design_variables: List[Dict[str, Any]],
    tear_variables: List[Dict[str, Any]],
    constraint_specs: Optional[List[Dict[str, Any]]] = None,
    minimize: bool = True,
    x0_design: Optional[List[float]] = None,
    x0_tear: Optional[List[float]] = None,
    max_iter: int = 300,
    tol: float = 1e-8,
) -> Dict[str, Any]:
    """Simultaneous tear + optimize via SQP (SciPy SLSQP).

    one_pass(design_x, tear_x) -> {
        "objective": float|None,
        "computed_tear": [float, …]   # the tear stream RECOMPUTED after one pass
                                        # (aligned to tear_variables)
        "constraint_values": [float, …]  # aligned to constraint_specs (optional)
    }
    design_variables / tear_variables : [{tag, property, unit?, lower, upper}, …]
    constraint_specs : [{tag, property, operator, value}, …]

    Returns the design + tear optimum, the closure residuals at the solution
    (must be ~0), the objective, feasibility, and the pass count — which, vs a
    feasible-path run on the same problem, is the headline speed-up.
    """
    import numpy as np
    from scipy.optimize import minimize as _min

    nd, nt = len(design_variables), len(tear_variables)
    allv = list(design_variables) + list(tear_variables)
    lo = np.array([float(v["lower"]) for v in allv])
    hi = np.array([float(v["upper"]) for v in allv])
    sign = 1.0 if minimize else -1.0
    cspecs = constraint_specs or []

    passes = {"n": 0}
    _cache: Dict[tuple, Dict[str, Any]] = {}

    def _pass(z):
        key = tuple(round(float(v), 10) for v in z)
        if key not in _cache:
            passes["n"] += 1
            _cache[key] = one_pass(list(z[:nd]), list(z[nd:])) or {}
        return _cache[key]

    def _obj(z):
        o = _pass(z).get("objective")
        return sign * float(o) if (o is not None and np.isfinite(o)) else 1e12

    cons: List[Dict[str, Any]] = []
    # Recycle closure: computed_tear[i] - tear_decision[i] = 0.
    for i in range(nt):
        def _clo(z, i=i):
            ct = _pass(z).get("computed_tear") or []
            if i >= len(ct) or ct[i] is None:
                return 1e6
            return float(ct[i]) - float(z[nd + i])
        cons.append({"type": "eq", "fun": _clo})
    # Process constraints on the surrogate of the one-pass result.
    for k, spec in enumerate(cspecs):
        op = spec.get("operator", ">="); lim = float(spec.get("value", 0.0))
        def _cv(z, k=k):
            cv = _pass(z).get("constraint_values") or []
            return (float(cv[k]) if k < len(cv) and cv[k] is not None else 0.0)
        if op == "<=":
            cons.append({"type": "ineq", "fun": (lambda z, _cv=_cv, lim=lim: lim - _cv(z))})
        elif op == ">=":
            cons.append({"type": "ineq", "fun": (lambda z, _cv=_cv, lim=lim: _cv(z) - lim)})
        else:
            cons.append({"type": "eq", "fun": (lambda z, _cv=_cv, lim=lim: _cv(z) - lim)})

    def _mid(vs):
        return [0.5 * (float(v["lower"]) + float(v["upper"])) for v in vs]
    z0 = np.array((x0_design or _mid(design_variables))
                  + (x0_tear or _mid(tear_variables)), dtype=float).clip(lo, hi)

    res = _min(_obj, z0, method="SLSQP", bounds=list(zip(lo, hi)),
               constraints=cons, options={"maxiter": max_iter, "ftol": tol})
    z = np.clip(res.x, lo, hi)

    final = _pass(z)
    ct = final.get("computed_tear") or []
    residuals = [round(float(ct[i]) - float(z[nd + i]), 8)
                 if i < len(ct) and ct[i] is not None else None
                 for i in range(nt)]
    max_res = max((abs(r) for r in residuals if r is not None), default=None)
    closed = (max_res is not None and max_res <= 1e-4 * (1 + abs(float(z[nd] if nt else 0))))

    def _feasible():
        cv = final.get("constraint_values") or []
        for k, spec in enumerate(cspecs):
            if k >= len(cv) or cv[k] is None:
                return False
            v = float(cv[k]); lim = float(spec.get("value", 0.0))
            op = spec.get("operator", ">="); t = 1e-3 * (abs(lim) + 1.0)
            if op == ">=" and v < lim - t: return False
            if op == "<=" and v > lim + t: return False
            if op == "==" and abs(v - lim) > t: return False
        return True

    return {
        "success": bool(res.success or closed),
        "method": "infeasible-path SQP (simultaneous tear + optimize)",
        "design": {f"{v['tag']}.{v['property']}": round(float(z[i]), 6)
                   for i, v in enumerate(design_variables)},
        "tear": {f"{v['tag']}.{v['property']}": round(float(z[nd + i]), 6)
                 for i, v in enumerate(tear_variables)},
        "objective": (round(float(final.get("objective")), 6)
                      if final.get("objective") is not None else None),
        "closure_residuals": residuals,
        "max_closure_residual": (round(float(max_res), 8) if max_res is not None else None),
        "recycle_closed": bool(closed),
        "feasible": _feasible() if cspecs else True,
        "n_passes": passes["n"],
        "minimize": minimize,
        "solver_message": str(res.message),
        "note": ("Infeasible-path SQP: tear-stream variables optimised jointly "
                 "with the design; recycle closure imposed as equality "
                 "constraints — one pass per evaluation, no inner convergence "
                 "loop (Biegler simultaneous tear + optimize)."),
    }
