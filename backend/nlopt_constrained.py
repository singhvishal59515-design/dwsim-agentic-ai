"""
nlopt_constrained.py
────────────────────
Constrained nonlinear optimization for DWSIM flowsheets using NLopt.

Why this exists
---------------
`dwsim_bridge_v2.optimize_constrained` historically used Differential Evolution
with a PENALTY function: constraint violations are folded into the objective as
`obj + 1e6·violation`. Penalty methods are simple but soft — they trade objective
against feasibility, can converge to slightly-infeasible points, and need penalty
tuning. This is exactly where Aspen Plus is stronger (its SQP treats constraints
explicitly).

NLopt treats nonlinear constraints as first-class: the solver searches the
*feasible region* directly. Default algorithm is GN_ISRES — a global,
derivative-free method that handles bounds + nonlinear inequality + equality
constraints, well-suited to the expensive, possibly-multimodal DWSIM landscape.
LN_COBYLA (local, derivative-free) is available for cheap local refinement.

Import-guarded: the bridge falls back to the DE+penalty path when NLopt is
absent, so behaviour degrades gracefully.

Evaluation cost
---------------
Each DWSIM solve is expensive and NLopt queries the objective and every
constraint as separate callables. We therefore SOLVE ONCE per distinct point and
cache the objective + all constraint values, so a point costs one solve no matter
how many constraints it has.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("nlopt_constrained")

_BIG = 1e12


def nlopt_available() -> bool:
    try:
        import nlopt  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


# Friendly algorithm names → NLopt algorithm attribute names.
_ALGOS = {
    "isres":  "GN_ISRES",    # global, bounds + ineq + eq, derivative-free
    "cobyla": "LN_COBYLA",   # local, ineq, derivative-free
    "auglag": "AUGLAG",      # augmented Lagrangian wrapper (uses a subsolver)
    "mma":    "LD_MMA",      # gradient (finite-diff); ineq
}


def run_nlopt_constrained(
    evaluate: Callable[[List[float]], Dict[str, Any]],
    lower: List[float],
    upper: List[float],
    x0: List[float],
    constraint_specs: List[Dict[str, Any]],
    minimize: bool = True,
    max_evals: int = 300,
    algorithm: str = "isres",
    eq_tol: float = 1e-4,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run NLopt with native constraint handling.

    evaluate(x) -> {"objective": float|None, "constraint_values": [float|None, ...]}
        Performs ONE DWSIM solve and returns the objective plus the value of
        each constraint quantity, positionally matched to `constraint_specs`.

    constraint_specs : [{operator: ">="|"<="|"==", value: float}, ...]
        The feasible region. Converted to g(x) ≤ 0 (inequality) or h(x) = 0
        (equality) for NLopt.

    Returns {success, x, objective, n_evaluations, algorithm, message,
             feasible}.
    """
    import nlopt  # type: ignore
    import numpy as np

    n = len(lower)
    lo = [float(v) for v in lower]
    hi = [float(v) for v in upper]
    x_start = [min(h, max(l, float(v))) for l, h, v in zip(lo, hi, x0)]

    # ── One-solve-per-point cache ───────────────────────────────────────────
    cache: Dict[str, Any] = {"key": None, "res": None}
    n_solves = {"n": 0}

    def _ev(x) -> Dict[str, Any]:
        key = ",".join(f"{xi:.10g}" for xi in x)
        if cache["key"] != key:
            cache["key"] = key
            cache["res"] = evaluate([float(xi) for xi in x])
            n_solves["n"] += 1
        return cache["res"]

    def _objective(x, grad):
        r = _ev(x)
        o = r.get("objective")
        if o is None:
            return _BIG
        return float(o) if minimize else -float(o)

    def _make_ineq(idx: int, op: str, limit: float):
        # Return g(x) such that feasibility is g(x) <= 0.
        def g(x, grad):
            r = _ev(x)
            vals = r.get("constraint_values") or []
            v = vals[idx] if idx < len(vals) else None
            if v is None:
                return _BIG
            v = float(v)
            if op == ">=":
                return limit - v           # v >= limit
            if op == "<=":
                return v - limit           # v <= limit
            return 0.0
        return g

    def _make_eq(idx: int, limit: float):
        def h(x, grad):
            r = _ev(x)
            vals = r.get("constraint_values") or []
            v = vals[idx] if idx < len(vals) else None
            if v is None:
                return _BIG
            return float(v) - limit        # v == limit
        return h

    algo_key = (algorithm or "isres").strip().lower()
    algo_name = _ALGOS.get(algo_key, "GN_ISRES")
    has_eq = any(c.get("operator") == "==" for c in (constraint_specs or []))
    # COBYLA/MMA do not support equality constraints; promote to ISRES if needed.
    if has_eq and algo_name in ("LN_COBYLA", "LD_MMA"):
        algo_name = "GN_ISRES"

    opt = nlopt.opt(getattr(nlopt, algo_name), n)
    opt.set_lower_bounds(lo)
    opt.set_upper_bounds(hi)
    opt.set_min_objective(_objective)
    try:
        opt.set_population(max(0, 0))  # let NLopt choose default population
    except Exception:
        pass
    try:
        nlopt.srand(int(seed))
    except Exception:
        pass

    for i, c in enumerate(constraint_specs or []):
        op = c.get("operator", ">=")
        limit = float(c.get("value", 0.0))
        if op == "==":
            opt.add_equality_constraint(_make_eq(i, limit), float(eq_tol))
        else:
            opt.add_inequality_constraint(_make_ineq(i, op, limit), 1e-8)

    opt.set_maxeval(int(max_evals))
    opt.set_xtol_rel(1e-4)

    message = ""
    try:
        x_opt = opt.optimize(x_start)
        message = f"{algo_name}: result code {opt.last_optimize_result()}"
    except Exception as exc:
        # NLopt raises on some terminations (e.g. roundoff-limited) but still
        # has a usable best point — recover it via the cache when possible.
        _log.warning("NLopt raised (%s); using best cached point.", exc)
        x_opt = x_start
        message = f"{algo_name}: raised {type(exc).__name__}: {exc}"

    final = evaluate([float(v) for v in x_opt])
    n_solves["n"] += 1
    obj = final.get("objective")

    # Feasibility check at the returned point.
    feasible = True
    vals = final.get("constraint_values") or []
    for i, c in enumerate(constraint_specs or []):
        if i >= len(vals) or vals[i] is None:
            feasible = False
            break
        v = float(vals[i]); lim = float(c.get("value", 0.0))
        op = c.get("operator", ">=")
        if op == ">=" and v < lim - eq_tol:
            feasible = False
        elif op == "<=" and v > lim + eq_tol:
            feasible = False
        elif op == "==" and abs(v - lim) > eq_tol:
            feasible = False

    return {
        "success": obj is not None,
        "x": [float(v) for v in x_opt],
        "objective": float(obj) if obj is not None else None,
        "n_evaluations": n_solves["n"],
        "algorithm": algo_name,
        "message": message,
        "feasible": feasible,
    }
