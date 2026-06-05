"""
global_sensitivity.py
─────────────────────
Variance-based and elementary-effects GLOBAL sensitivity analysis for DWSIM
flowsheets, via SALib.

Why this exists
---------------
The agent already has `parametric_study` / `parametric_study_2d` — local, one- or
two-variable sweeps (the equivalent of Aspen Plus's Sensitivity block). Those
answer "how does the output move as I sweep THIS variable", but they cannot rank
many variables together or expose INTERACTION effects (where two variables only
matter jointly). Aspen's local sweeps share that limitation.

Global sensitivity goes further:
  • Sobol (variance-based): decomposes output variance into the fraction
    attributable to each input (first-order S1) and to each input including all
    its interactions (total-order ST). ST ≫ S1 reveals interaction-driven inputs.
  • Morris (elementary effects): a cheap screening method — mu_star ranks overall
    influence, sigma flags nonlinear / interacting inputs.

Both tell you WHICH decision variables are worth optimising before paying for an
expensive optimisation run — a capability Aspen Plus does not provide natively.

Import-guarded: callers should check `salib_available()` first.

Cost
----
Sobol needs N·(D+2) evaluations (first/total order); Morris needs r·(D+1). Each
evaluation is one DWSIM solve, so keep N/r modest. Defaults are deliberately
small; raise them for a publication-grade study.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("global_sensitivity")


def salib_available() -> bool:
    try:
        import SALib  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def run_global_sensitivity(
    evaluate: Callable[[List[float]], Optional[float]],
    variables: List[Dict[str, Any]],
    output_name: str = "output",
    method: str = "sobol",
    n_samples: int = 16,
    num_levels: int = 4,
    seed: int = 42,
) -> Dict[str, Any]:
    """Run global sensitivity analysis.

    evaluate(x) -> scalar output (one DWSIM solve) or None on failure.
    variables   -> [{tag, property, lower, upper, unit?}, ...]
    method      -> "sobol" (variance-based) | "morris" (elementary effects)

    Returns a ranking of inputs by influence plus the raw indices.
    """
    import numpy as np

    names = [f"{v['tag']}.{v['property']}" for v in variables]
    bounds = [[float(v["lower"]), float(v["upper"])] for v in variables]
    problem = {"num_vars": len(variables), "names": names, "bounds": bounds}
    method_l = (method or "sobol").strip().lower()

    # ── 1. Sample the input space ──────────────────────────────────────────
    if method_l == "morris":
        from SALib.sample.morris import sample as _sample
        X = _sample(problem, N=int(n_samples), num_levels=int(num_levels),
                    seed=int(seed))
    else:
        method_l = "sobol"
        from SALib.sample import sobol as _sobol_sample
        X = _sobol_sample.sample(problem, int(n_samples),
                                 calc_second_order=False, seed=int(seed))

    # ── 2. Evaluate the model at every sample (the expensive part) ─────────
    Y = np.empty(X.shape[0], dtype=float)
    n_failed = 0
    for i, row in enumerate(X):
        val = evaluate([float(v) for v in row])
        if val is None or not np.isfinite(val):
            Y[i] = np.nan
            n_failed += 1
        else:
            Y[i] = float(val)

    finite = np.isfinite(Y)
    if finite.sum() < max(4, 0.5 * len(Y)):
        return {"success": False,
                "error": f"Too many failed evaluations "
                         f"({n_failed}/{len(Y)}) for a reliable analysis.",
                "n_evaluations": int(len(Y)), "n_failed": int(n_failed)}
    if n_failed:
        # SALib requires finite Y; impute failures with the mean of the rest.
        Y[~finite] = float(np.nanmean(Y))

    # ── 3. Analyse ─────────────────────────────────────────────────────────
    if method_l == "morris":
        from SALib.analyze import morris as _morris
        Si = _morris.analyze(problem, X, Y, num_levels=int(num_levels),
                             print_to_console=False, seed=int(seed))
        ranking = sorted(
            [{"variable": names[j],
              "mu_star": round(float(Si["mu_star"][j]), 6),
              "sigma":   round(float(Si["sigma"][j]), 6)}
             for j in range(len(names))],
            key=lambda d: d["mu_star"], reverse=True)
        method_label = "Morris (elementary effects)"
    else:
        from SALib.analyze import sobol as _sobol
        Si = _sobol.analyze(problem, Y, calc_second_order=False,
                            print_to_console=False, seed=int(seed))
        ranking = sorted(
            [{"variable": names[j],
              "S1": round(float(Si["S1"][j]), 6),
              "ST": round(float(Si["ST"][j]), 6)}
             for j in range(len(names))],
            key=lambda d: d["ST"], reverse=True)
        method_label = "Sobol (variance-based)"

    return {
        "success": True,
        "method": method_label,
        "output": output_name,
        "n_evaluations": int(len(Y)),
        "n_failed": int(n_failed),
        "ranking": ranking,
        "most_influential": [r["variable"] for r in ranking],
    }
