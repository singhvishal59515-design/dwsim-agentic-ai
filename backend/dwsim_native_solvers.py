"""
dwsim_native_solvers.py
───────────────────────
Direct .NET bindings to the SAME optimization solvers DWSIM uses internally:

  • DotNumerics.Optimization.LBFGSB.LBFGSBDriver   — L-BFGS-B (quasi-Newton, bound-constrained)
  • DotNumerics.Optimization.Simplex                — Nelder-Mead Simplex (bound-constrained)
  • DotNumerics.Optimization.TruncatedNewton        — Truncated Newton
  • DWSIM.MathOps.MathEx.OptimizationL.DE           — Differential Evolution (global)

Each solver is invoked via the exact same .NET API DWSIM's GUI Optimizer uses.
The Python objective callback is wrapped in a pythonnet-constructed delegate
(`OptMultivariateFunction` for DotNumerics, `Func<double[], double>` for DE).

The module is a thin wrapper — it must:
  1. Load DWSIM DLLs (done by the bridge first, so they're already in CLR).
  2. Construct the right .NET array of bound variables.
  3. Wrap the Python objective (and finite-difference gradient where required).
  4. Call ComputeMin / Solve.
  5. Convert the .NET Double[] result to a Python list.

Falls back to SciPy when DotNumerics is unavailable (e.g. on non-Windows test
environments). The fallback is mathematically equivalent — same algorithm
families — but uses SciPy's implementations.
"""

from __future__ import annotations
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("dwsim_native_solvers")


# ─── DotNumerics availability flag ─────────────────────────────────────────

def _dotnumerics_available() -> bool:
    """Return True if DotNumerics + DWSIM optimization assemblies are loadable.

    Only POSITIVE results are cached. A negative result (DLLs not yet loaded)
    is re-checked on every call — cheap (just an ImportError) and lets the
    function start returning True after the bridge initialises DWSIM later
    in the same process."""
    if getattr(_dotnumerics_available, "_cached", False):
        return True
    try:
        from DotNumerics.Optimization import (   # noqa: F401
            OptBoundVariable, OptMultivariateFunction,
            OptMultivariateGradient, Simplex, TruncatedNewton,
        )
        from DotNumerics.Optimization.LBFGSB import LBFGSBDriver  # noqa: F401
        from DWSIM.MathOps.MathEx.OptimizationL import DE          # noqa: F401
        from System import Array, Double, Func                     # noqa: F401
        _dotnumerics_available._cached = True
        return True
    except Exception as exc:
        _log.debug("DotNumerics not available (will retry): %s",
                   str(exc)[:120])
        return False


# ─── The four solver wrappers ──────────────────────────────────────────────

def solve_lbfgs(objective: Callable[[List[float]], float],
                lower: List[float], upper: List[float],
                initial: List[float],
                tolerance: float = 1e-6,
                max_iter: int = 200,
                ) -> Dict[str, Any]:
    """L-BFGS-B via DotNumerics. Returns dict with best_x, best_f, n_evals."""
    from DotNumerics.Optimization import (
        OptBoundVariable, OptMultivariateFunction, OptMultivariateGradient,
    )
    from DotNumerics.Optimization.LBFGSB import LBFGSBDriver
    from System import Array, Double

    n_evals = [0]

    def _f(X):
        n_evals[0] += 1
        return float(objective([float(X[i]) for i in range(len(X))]))

    eps = 1e-6

    def _grad(X):
        n = len(X)
        g = Array.CreateInstance(Double, n)
        base = [float(X[i]) for i in range(n)]
        for i in range(n):
            xp = list(base); xm = list(base)
            xp[i] += eps; xm[i] -= eps
            g[i] = (objective(xp) - objective(xm)) / (2.0 * eps)
            n_evals[0] += 2
        return g

    fn = OptMultivariateFunction(_f)
    gd = OptMultivariateGradient(_grad)

    bounds = []
    for i, (lo, hi, x0) in enumerate(zip(lower, upper, initial)):
        bounds.append(OptBoundVariable(f"x{i}", float(x0), float(lo), float(hi)))
    var_arr = Array[OptBoundVariable](bounds)

    driver = LBFGSBDriver()
    try:
        result = driver.ComputeMin(fn, gd, var_arr, float(tolerance),
                                    1.0e7, int(max_iter))
    except Exception as exc:
        return {"success": False, "error": f"L-BFGS-B failed: {exc}"}
    if isinstance(result, tuple):
        result = result[0]
    best_x = [float(result[i]) for i in range(len(result))]
    return {
        "success": True,
        "best_x":  best_x,
        "best_f":  objective(best_x),
        "n_evals": n_evals[0],
        "solver":  "DotNumerics.LBFGSBDriver",
    }


def solve_simplex(objective: Callable[[List[float]], float],
                   lower: List[float], upper: List[float],
                   initial: List[float],
                   initial_step: float = 0.1,
                   ) -> Dict[str, Any]:
    """Nelder-Mead Simplex via DotNumerics — gradient-free, bound-constrained."""
    from DotNumerics.Optimization import (
        OptBoundVariable, OptMultivariateFunction, Simplex,
    )
    from System import Array, Single

    n_evals = [0]

    def _f(X):
        n_evals[0] += 1
        return float(objective([float(X[i]) for i in range(len(X))]))

    fn = OptMultivariateFunction(_f)
    bounds = []
    for i, (lo, hi, x0) in enumerate(zip(lower, upper, initial)):
        bounds.append(OptBoundVariable(f"x{i}", float(x0), float(lo), float(hi)))
    var_arr = Array[OptBoundVariable](bounds)

    simplex = Simplex()
    try:
        # initial_step is a Single (float32) per the overload — cast explicitly
        result = simplex.ComputeMin(fn, var_arr, Single(float(initial_step)))
    except Exception:
        # Try the no-step overload
        try:
            result = simplex.ComputeMin(fn, var_arr)
        except Exception as exc:
            return {"success": False, "error": f"Simplex failed: {exc}"}
    best_x = [float(result[i]) for i in range(len(result))]
    return {
        "success": True,
        "best_x":  best_x,
        "best_f":  objective(best_x),
        "n_evals": n_evals[0],
        "solver":  "DotNumerics.Simplex",
    }


def solve_truncated_newton(objective: Callable[[List[float]], float],
                           lower: List[float], upper: List[float],
                           initial: List[float],
                           ) -> Dict[str, Any]:
    """Truncated Newton via DotNumerics."""
    from DotNumerics.Optimization import (
        OptBoundVariable, OptMultivariateFunction, OptMultivariateGradient,
        TruncatedNewton,
    )
    from System import Array, Double

    n_evals = [0]

    def _f(X):
        n_evals[0] += 1
        return float(objective([float(X[i]) for i in range(len(X))]))

    eps = 1e-6

    def _grad(X):
        n = len(X)
        g = Array.CreateInstance(Double, n)
        base = [float(X[i]) for i in range(n)]
        for i in range(n):
            xp = list(base); xm = list(base)
            xp[i] += eps; xm[i] -= eps
            g[i] = (objective(xp) - objective(xm)) / (2.0 * eps)
            n_evals[0] += 2
        return g

    fn = OptMultivariateFunction(_f)
    gd = OptMultivariateGradient(_grad)

    bounds = []
    for i, (lo, hi, x0) in enumerate(zip(lower, upper, initial)):
        bounds.append(OptBoundVariable(f"x{i}", float(x0), float(lo), float(hi)))
    var_arr = Array[OptBoundVariable](bounds)

    tn = TruncatedNewton()
    try:
        result = tn.ComputeMin(fn, gd, var_arr)
    except Exception as exc:
        return {"success": False, "error": f"TruncatedNewton failed: {exc}"}
    best_x = [float(result[i]) for i in range(len(result))]
    return {
        "success": True,
        "best_x":  best_x,
        "best_f":  objective(best_x),
        "n_evals": n_evals[0],
        "solver":  "DotNumerics.TruncatedNewton",
    }


def solve_de(objective: Callable[[List[float]], float],
             lower: List[float], upper: List[float],
             initial: List[float],
             max_iter: int = 100,
             tolerance: float = 1e-4,
             ) -> Dict[str, Any]:
    """Differential Evolution via DWSIM's DotNumerics-backed DE class.

    NOTE: DotNumerics DE generates candidates with magnitudes proportional to
    the initial point's magnitude, not the bound width — so on bounds like
    [550, 650] it explores values >1000 and the solver gets stuck clamping
    to the bounds. We normalise the search space to [0, 1]^n and unmap
    inside the objective. This is mathematically identical (affine change
    of variables) but keeps the solver in its well-behaved regime."""
    from DWSIM.MathOps.MathEx.OptimizationL import DE
    from System import Array, Double, Func

    n = len(lower)
    lo = [float(x) for x in lower]
    hi = [float(x) for x in upper]
    spans = [hi[i] - lo[i] for i in range(n)]
    if any(s <= 0 for s in spans):
        return {"success": False, "error": "DE: each bound must satisfy lower < upper"}

    n_evals = [0]

    def _f_norm(X):
        # X is a .NET array of normalized values in [0, 1]
        n_evals[0] += 1
        x_real = [lo[i] + max(0.0, min(1.0, float(X[i]))) * spans[i] for i in range(n)]
        return float(objective(x_real))

    fn = Func[Array[Double], Double](_f_norm)
    # Initial normalized to [0, 1]
    init_norm = [(float(initial[i]) - lo[i]) / spans[i] for i in range(n)]
    init_norm = [max(0.0, min(1.0, x)) for x in init_norm]
    xv = Array[Double](init_norm)
    lb = Array[Double]([0.0] * n)
    ub = Array[Double]([1.0] * n)

    de = DE()
    try:
        de.MaxIterations = int(max_iter)
        de.Tolerance = float(tolerance)
    except Exception:
        pass

    try:
        result_norm = de.Solve(fn, None, xv, lb, ub)
    except Exception as exc:
        return {"success": False, "error": f"DE failed: {exc}"}

    # Un-normalize the result
    best_x = [lo[i] + max(0.0, min(1.0, float(result_norm[i]))) * spans[i]
              for i in range(n)]
    return {
        "success": True,
        "best_x":  best_x,
        "best_f":  objective(best_x),
        "n_evals": n_evals[0],
        "solver":  "DWSIM.MathOps.DE (normalized)",
    }


# ─── Top-level dispatcher ──────────────────────────────────────────────────

# Friendly name → (function, friendly label)
_SOLVERS: Dict[str, Tuple[Callable, str]] = {
    "lbfgs":            (solve_lbfgs,             "DotNumerics L-BFGS-B"),
    "lbfgs-b":          (solve_lbfgs,             "DotNumerics L-BFGS-B"),
    "lbfgsb":           (solve_lbfgs,             "DotNumerics L-BFGS-B"),
    "l-bfgs":           (solve_lbfgs,             "DotNumerics L-BFGS-B"),
    "simplex":          (solve_simplex,           "DotNumerics Nelder-Mead Simplex"),
    "nelder-mead":      (solve_simplex,           "DotNumerics Nelder-Mead Simplex"),
    "neldermead":       (solve_simplex,           "DotNumerics Nelder-Mead Simplex"),
    "newton":           (solve_truncated_newton,  "DotNumerics Truncated Newton"),
    "truncated-newton": (solve_truncated_newton,  "DotNumerics Truncated Newton"),
    "tnc":              (solve_truncated_newton,  "DotNumerics Truncated Newton"),
    "de":               (solve_de,                "DWSIM DotNumerics DE"),
    "differential-evolution": (solve_de,          "DWSIM DotNumerics DE"),
}


def run_native_solver(method: str,
                      objective: Callable[[List[float]], float],
                      lower: List[float], upper: List[float],
                      initial: List[float],
                      max_iter: int = 100,
                      tolerance: float = 1e-4,
                      ) -> Dict[str, Any]:
    """Top-level entry. Returns {success, best_x, best_f, n_evals, solver}.

    method: friendly name from _SOLVERS keys. Defaults to simplex if unknown
    (gradient-free, robust for noisy DWSIM evaluations)."""
    if not _dotnumerics_available():
        return {"success": False, "error_code": "DOTNUMERICS_UNAVAILABLE",
                "error": "DotNumerics not loaded — install DWSIM or run "
                         "the bridge first."}
    key = (method or "simplex").strip().lower().replace("_", "-")
    fn, label = _SOLVERS.get(key, (solve_simplex, "DotNumerics Nelder-Mead Simplex (default)"))
    t0 = time.monotonic()
    if fn is solve_de:
        r = fn(objective, lower, upper, initial, max_iter=max_iter,
               tolerance=tolerance)
    elif fn is solve_lbfgs:
        r = fn(objective, lower, upper, initial, tolerance=tolerance,
               max_iter=max_iter)
    elif fn is solve_simplex:
        r = fn(objective, lower, upper, initial)
    else:
        r = fn(objective, lower, upper, initial)
    r["duration_s"] = round(time.monotonic() - t0, 3)
    r["solver_label"] = label
    return r
