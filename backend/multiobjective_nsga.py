"""
multiobjective_nsga.py
──────────────────────
True multi-objective optimization via NSGA-II (pymoo) for DWSIM flowsheets.

Why this exists
---------------
`dwsim_bridge_v2.optimize_multiobjective` historically used *weighted-sum
scalarization*: it sweeps a weight vector and solves a single-objective problem
per weight. Weighted-sum is simple but has a well-known fatal flaw — it can only
recover points on the CONVEX part of a Pareto front. Any concave (non-convex)
region is invisible to it, no matter how the weights are chosen.

NSGA-II (Non-dominated Sorting Genetic Algorithm II, Deb et al. 2002) evolves a
whole population toward the Pareto front simultaneously and recovers non-convex
fronts in a single run, with no manual weights. It is the de-facto standard for
process trade-off studies (purity vs energy, yield vs cost, …).

This module is import-guarded: callers fall back to the weighted-sum method when
`pymoo` is unavailable, so behaviour degrades gracefully.

Each evaluation is an expensive DWSIM solve, so the default budget is modest
(population × generations ≈ a few hundred solves at most).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("multiobjective_nsga")


def pymoo_available() -> bool:
    try:
        import pymoo  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def run_nsga2(
    eval_objectives: Callable[[List[float]], Optional[List[float]]],
    variables: List[Dict[str, Any]],
    objectives: List[Dict[str, Any]],
    pop_size: int = 0,
    n_gen: int = 15,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run NSGA-II over a black-box flowsheet evaluation.

    eval_objectives(x) -> list of RAW objective values (in engineering units),
        one per objective, in the same order as `objectives`; or None when the
        solve/read failed (penalised internally).

    `objectives` entries use {tag, property, unit?, minimize?} — `minimize`
    defaults to True. NSGA-II minimises internally; maximise objectives are
    negated for the search and reported back in real units.

    Returns the SAME envelope shape as the weighted-sum path:
        {success, pareto_front:[{point_index, optimal_variables,
         objective_values, nondominated}], n_points, objectives, variables,
         method, n_evaluations}
    """
    import numpy as np  # local import; numpy is always present
    from pymoo.core.problem import ElementwiseProblem      # type: ignore
    from pymoo.algorithms.moo.nsga2 import NSGA2           # type: ignore
    from pymoo.optimize import minimize as pymoo_minimize  # type: ignore

    n = len(variables)
    m = len(objectives)
    lo = np.array([float(v["lower"]) for v in variables], dtype=float)
    hi = np.array([float(v["upper"]) for v in variables], dtype=float)
    signs = [1.0 if o.get("minimize", True) else -1.0 for o in objectives]

    eval_count = {"n": 0}
    _BIG = 1e9

    class _DWSIMProblem(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=n, n_obj=m, xl=lo, xu=hi)

        def _evaluate(self, x, out, *args, **kwargs):
            eval_count["n"] += 1
            raw = eval_objectives([float(v) for v in x])
            if raw is None or len(raw) != m or any(v is None for v in raw):
                out["F"] = [_BIG] * m
                return
            # Apply sign so every objective is MINIMISED by NSGA-II.
            out["F"] = [s * float(v) for s, v in zip(signs, raw)]

    if pop_size <= 0:
        pop_size = max(12, 4 * m, 2 * n)

    algorithm = NSGA2(pop_size=int(pop_size))
    res = pymoo_minimize(
        _DWSIMProblem(), algorithm, ("n_gen", int(n_gen)),
        seed=int(seed), verbose=False, save_history=False)

    # res.X / res.F hold the non-dominated set. Normalise to 2-D.
    X = np.atleast_2d(res.X) if res.X is not None else np.empty((0, n))
    F = np.atleast_2d(res.F) if res.F is not None else np.empty((0, m))

    pareto_front: List[Dict[str, Any]] = []
    for i in range(X.shape[0]):
        xi = X[i]
        # Convert back to real (un-signed) objective values for reporting.
        obj_vals = {
            f"{o['tag']}.{o['property']}": round(signs[j] * float(F[i][j]), 6)
            for j, o in enumerate(objectives)
        }
        pareto_front.append({
            "point_index": i + 1,
            "optimal_variables": {
                f"{v['tag']}.{v['property']}": round(float(xi[j]), 6)
                for j, v in enumerate(variables)},
            "objective_values": obj_vals,
            "nondominated": True,
        })

    return {
        "success": len(pareto_front) > 0,
        "pareto_front": pareto_front,
        "n_points": len(pareto_front),
        "objectives": [f"{o['tag']}.{o['property']}" for o in objectives],
        "variables": [f"{v['tag']}.{v['property']}" for v in variables],
        "method": "NSGA-II (pymoo)",
        "n_evaluations": eval_count["n"],
    }
