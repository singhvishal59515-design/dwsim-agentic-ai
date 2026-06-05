"""
eo_optimizer.py
───────────────
Equation-oriented (EO) optimization for DWSIM flowsheets — the closest analogue
to Aspen Plus's EO mode.

Background — why this is different from everything else here
-----------------------------------------------------------
DWSIM (like Aspen's default) is SEQUENTIAL-MODULAR: to evaluate one design it
solves the whole flowsheet numerically, returns numbers, then a black-box
optimiser perturbs and repeats. Aspen Plus's *equation-oriented* mode instead
assembles the flowsheet as one large system of algebraic equations and solves
the optimisation + the model SIMULTANEOUSLY with a large-scale NLP solver
(IPOPT / CONOPT). That is faster and more robust on tightly-coupled, constrained
problems — and it is the single biggest capability Aspen has that a black-box
wrapper around DWSIM lacks.

DWSIM does not expose its internal equations to an algebraic modelling layer, so
true EO is impossible directly. The honest, standard way to get EO-style
behaviour over such a simulator (this is exactly what IDAES/ALAMO do) is
SURROGATE-BASED EO:

  1. Sample the decision space (Latin-hypercube DOE) and solve DWSIM at each
     point, recording the objective and every constraint quantity.
  2. Fit smooth ALGEBRAIC surrogates (full quadratic response surfaces — twice
     differentiable, so a gradient NLP solver is happy).
  3. Solve the surrogate as ONE simultaneous NLP: minimise the objective
     surrogate subject to the constraint surrogates and the bounds. Uses IPOPT
     (via Pyomo) when an IPOPT binary is installed; otherwise the identical
     algebraic model is solved with SciPy SLSQP — same EO formulation, different
     engine.
  4. VALIDATE the surrogate optimum with one real DWSIM solve and report the
     surrogate-vs-actual gap, so the result is never trusted blindly.

To get the full Aspen-EO engine, install an IPOPT binary (e.g. `pip install
idaes-pse && idaes get-extensions`); this module will then use it automatically.
"""
from __future__ import annotations

import itertools
import logging
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("eo_optimizer")

# Cross-validated R² below this means the quadratic surrogate doesn't reliably
# predict the flowsheet — the EO optimum should not be trusted blindly.
_CV_R2_TRUST = 0.70


def _register_idaes_solvers() -> None:
    """Importing `idaes` registers its bundled solver bin dir (containing
    ipopt.exe from `idaes get-extensions`) with Pyomo's executable search path.
    Without this, Pyomo cannot find the IDAES-provided IPOPT. Best-effort."""
    try:
        import idaes  # type: ignore  # noqa: F401
    except Exception:
        pass


def _ipopt_executable() -> Optional[str]:
    """Explicit path to the IDAES-bundled ipopt binary, if present — a fallback
    when `import idaes` alone doesn't register it with Pyomo."""
    import os
    for cand in (
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "idaes",
                     "bin", "ipopt.exe"),
        os.path.join(os.path.expanduser("~"), ".idaes", "bin", "ipopt"),
    ):
        if os.path.exists(cand):
            return cand
    return None


def _ipopt_solver():
    """Return an available IPOPT SolverFactory, or None."""
    _register_idaes_solvers()
    try:
        from pyomo.environ import SolverFactory  # type: ignore
        s = SolverFactory("ipopt")
        if s.available(exception_flag=False):
            return s
        exe = _ipopt_executable()
        if exe:
            s = SolverFactory("ipopt", executable=exe)
            if s.available(exception_flag=False):
                return s
    except Exception:
        pass
    return None


def ipopt_available() -> bool:
    """True only when Pyomo AND an actual IPOPT solver binary are present."""
    return _ipopt_solver() is not None


# ── Quadratic response-surface surrogate ───────────────────────────────────

def _quad_terms(n: int):
    """Index pairs for the quadratic monomials x_i·x_j (i ≤ j)."""
    return [(i, j) for i in range(n) for j in range(i, n)]


def _design_matrix(X, n: int):
    """Rows -> [1, x_i…, x_i x_j…]. Smooth, differentiable basis."""
    import numpy as np
    X = np.atleast_2d(X)
    pairs = _quad_terms(n)
    cols = [np.ones(X.shape[0])]
    cols += [X[:, i] for i in range(n)]
    cols += [X[:, i] * X[:, j] for (i, j) in pairs]
    return np.column_stack(cols)


def _fit_quadratic(X, y, n: int):
    import numpy as np
    A = _design_matrix(X, n)
    coef, *_ = np.linalg.lstsq(A, np.asarray(y, dtype=float), rcond=None)
    pred = A @ coef
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2)) or 1e-30
    r2 = 1.0 - ss_res / ss_tot
    return coef, r2


def _cv_r2(X, y, n: int, k: int = 5) -> float:
    """k-fold cross-validated R² of the quadratic surrogate.

    In-sample R² is optimistic (it can look great while the surrogate fails to
    PREDICT). CV R² is the honest trust metric: fit on k-1 folds, score the
    held-out fold. A low CV R² means the surrogate (hence the EO optimum) is
    unreliable for this tightly-coupled / nonlinear problem.
    """
    import numpy as np
    y = np.asarray(y, dtype=float)
    m = len(y)
    n_params = 1 + n + len(_quad_terms(n))
    k = max(2, min(k, m // max(1, n_params)))  # enough train rows per fold
    if k < 2 or m < n_params + 2:
        return float("nan")
    rng = np.random.default_rng(0)
    idx = rng.permutation(m)
    folds = np.array_split(idx, k)
    preds = np.empty(m, dtype=float)
    ok = np.zeros(m, dtype=bool)
    for f in folds:
        train = np.setdiff1d(idx, f)
        if len(train) < n_params:
            continue
        coef, _ = _fit_quadratic(X[train], y[train], n)
        fn = _make_quad_callable(coef, n)
        for j in f:
            preds[j] = fn(X[j])
            ok[j] = True
    if ok.sum() < 2:
        return float("nan")
    yt, yp = y[ok], preds[ok]
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - np.mean(yt)) ** 2)) or 1e-30
    return 1.0 - ss_res / ss_tot


def _make_quad_callable(coef, n: int):
    """Return f(x) evaluating the fitted quadratic at a single point."""
    import numpy as np
    pairs = _quad_terms(n)

    def f(x):
        x = np.asarray(x, dtype=float)
        terms = [1.0] + [x[i] for i in range(n)] + [x[i] * x[j] for (i, j) in pairs]
        return float(np.dot(coef, terms))
    return f


def run_eo_optimization(
    evaluate: Callable[[List[float]], Dict[str, Any]],
    variables: List[Dict[str, Any]],
    constraint_specs: Optional[List[Dict[str, Any]]] = None,
    minimize: bool = True,
    n_samples: int = 0,
    seed: int = 42,
    validate: bool = True,
    max_refine: int = 3,
    refine_rel_tol: float = 1e-2,
) -> Dict[str, Any]:
    """Surrogate-based equation-oriented optimization with ADAPTIVE refinement.

    evaluate(x) -> {"objective": float|None, "constraint_values": [float|None,…]}
        one DWSIM solve, objective + each constraint quantity (matched to
        constraint_specs order).
    constraint_specs : [{operator: ">="|"<="|"==", value: float}, …]

    Adaptive refinement (trust-region / EGO flavour): after solving the
    surrogate NLP we VALIDATE the predicted optimum with a real DWSIM solve and
    add that exact point to the sample pool, then REFIT and re-solve. Because the
    newest sample sits right at the predicted optimum, each round sharpens the
    surrogate exactly where it matters, driving the surrogate-vs-actual gap down
    instead of merely reporting it. Stops when the relative gap ≤ refine_rel_tol
    or after max_refine rounds. Reused samples are never re-solved, so the extra
    cost is one DWSIM solve per round.

    Returns {success, x, objective_surrogate, objective_actual, surrogate_gap,
             solver, r2_objective, r2_constraints, n_samples, feasible,
             n_refinements, refinement_history, converged}.
    """
    import numpy as np

    n = len(variables)
    lo = np.array([float(v["lower"]) for v in variables])
    hi = np.array([float(v["upper"]) for v in variables])
    cspecs = constraint_specs or []

    # ── 1. Latin-hypercube DOE over the decision space ─────────────────────
    n_quad = 1 + n + len(_quad_terms(n))           # params in a full quadratic
    if n_samples <= 0:
        n_samples = max(2 * n_quad, 8 * n + 4)     # comfortably over-determined
    try:
        from scipy.stats.qmc import LatinHypercube
        unit = LatinHypercube(d=n, seed=seed).random(n_samples)
    except Exception:
        rng = np.random.default_rng(seed)
        unit = rng.random((n_samples, n))
    X = lo + unit * (hi - lo)

    # ── 2. Evaluate DWSIM at every sample ──────────────────────────────────
    obj_y, con_y = [], [[] for _ in cspecs]
    X_ok = []
    for row in X:
        r = evaluate([float(v) for v in row])
        o = r.get("objective")
        if o is None or not np.isfinite(o):
            continue
        cvals = r.get("constraint_values") or []
        if any((i >= len(cvals) or cvals[i] is None) for i in range(len(cspecs))):
            continue
        X_ok.append(row)
        obj_y.append(float(o))
        for i in range(len(cspecs)):
            con_y[i].append(float(cvals[i]))
    X_ok = np.atleast_2d(X_ok)
    if X_ok.shape[0] < n_quad:
        return {"success": False,
                "error": f"Only {X_ok.shape[0]} valid samples for a quadratic "
                         f"needing ≥{n_quad}; widen bounds or raise n_samples.",
                "n_samples": int(X.shape[0])}

    sign = 1.0 if minimize else -1.0

    def _is_feasible(cvals):
        for i, spec in enumerate(cspecs):
            if i >= len(cvals) or cvals[i] is None:
                return False
            v = float(cvals[i]); lim = float(spec.get("value", 0.0))
            op = spec.get("operator", ">=")
            tol = 1e-3 * (abs(lim) + 1.0)
            if op == ">=" and v < lim - tol: return False
            if op == "<=" and v > lim + tol: return False
            if op == "==" and abs(v - lim) > tol: return False
        return True

    def _solve_surrogate(obj_coef, con_coef, con_fn, x0):
        """Solve the quadratic surrogate NLP; IPOPT if available else SLSQP."""
        if ipopt_available():
            try:
                xo, used = _solve_with_ipopt(
                    obj_coef, con_coef, cspecs, lo, hi, x0, n, minimize)
                return [float(min(h, max(l, v)))
                        for l, h, v in zip(lo, hi, xo)], used
            except Exception as exc:
                _log.warning("IPOPT solve failed (%s); using SciPy SLSQP.", exc)
        from scipy.optimize import minimize as _min
        cons = []
        for k, spec in enumerate(cspecs):
            op = spec.get("operator", ">="); lim = float(spec.get("value", 0.0))
            g = con_fn[k]
            if op == "<=":
                cons.append({"type": "ineq", "fun": (lambda x, g=g, lim=lim: lim - g(x))})
            elif op == ">=":
                cons.append({"type": "ineq", "fun": (lambda x, g=g, lim=lim: g(x) - lim)})
            else:
                cons.append({"type": "eq", "fun": (lambda x, g=g, lim=lim: g(x) - lim)})
        res = _min(lambda x: sign * _make_quad_callable(obj_coef, n)(x), x0,
                   method="SLSQP", bounds=list(zip(lo, hi)), constraints=cons,
                   options={"maxiter": 300, "ftol": 1e-9})
        return ([float(min(h, max(l, v))) for l, h, v in zip(lo, hi, res.x)],
                "SciPy SLSQP (surrogate NLP)")

    # ── 3–5. Adaptive refinement loop ──────────────────────────────────────
    # Keep the growing sample pool as plain lists so each round just appends the
    # newly-validated optimum (one extra solve) and refits — never re-solving.
    pool_X = [list(map(float, r)) for r in X_ok]
    pool_obj = list(map(float, obj_y))
    pool_con = [list(map(float, c)) for c in con_y]

    best = None                 # best feasible validated result so far
    refinement_history = []
    solver_used = ""
    obj_r2 = 0.0
    con_r2: List[float] = []
    converged = False

    for rnd in range(max_refine + 1):
        Xa = np.atleast_2d(pool_X)
        obj_coef, obj_r2 = _fit_quadratic(Xa, pool_obj, n)
        con_coef, con_r2 = [], []
        for i in range(len(cspecs)):
            c, r2 = _fit_quadratic(Xa, pool_con[i], n)
            con_coef.append(c); con_r2.append(round(r2, 4))
        con_fn = [_make_quad_callable(c, n) for c in con_coef]
        obj_fn = _make_quad_callable(obj_coef, n)

        bi = int(np.argmin(pool_obj) if minimize else np.argmax(pool_obj))
        x_opt, solver_used = _solve_surrogate(obj_coef, con_coef, con_fn, pool_X[bi])
        obj_surrogate = obj_fn(x_opt)

        if not validate:
            best = {"x": x_opt, "obj_surrogate": obj_surrogate,
                    "obj_actual": None, "gap": None, "feasible": None}
            break

        # One real DWSIM solve at the predicted optimum.
        r = evaluate(list(x_opt))
        obj_actual = r.get("objective")
        cvals = [float(v) if v is not None else None
                 for v in (r.get("constraint_values") or [])]
        feasible = _is_feasible(cvals)
        gap = (abs(float(obj_actual) - float(obj_surrogate))
               if obj_actual is not None else None)
        rel_gap = (gap / (abs(float(obj_actual)) + 1e-9)
                   if gap is not None else None)
        refinement_history.append({
            "round": rnd,
            "objective_surrogate": round(float(obj_surrogate), 6),
            "objective_actual": (round(float(obj_actual), 6)
                                 if obj_actual is not None else None),
            "surrogate_gap": round(float(gap), 6) if gap is not None else None,
            "rel_gap": round(float(rel_gap), 6) if rel_gap is not None else None,
            "feasible": feasible,
        })

        # Feed the validated point back into the pool for the next refit.
        if obj_actual is not None and all(
                (i < len(cvals) and cvals[i] is not None)
                for i in range(len(cspecs))):
            pool_X.append([float(v) for v in x_opt])
            pool_obj.append(float(obj_actual))
            for i in range(len(cspecs)):
                pool_con[i].append(float(cvals[i]))

        cand = {"x": x_opt, "obj_surrogate": obj_surrogate,
                "obj_actual": obj_actual, "gap": gap, "feasible": feasible}
        # Track the best FEASIBLE validated point (by true objective).
        if obj_actual is not None and feasible:
            if best is None or not best.get("feasible") or (
                    (obj_actual < best["obj_actual"]) == minimize):
                best = cand
        elif best is None:
            best = cand

        if rel_gap is not None and rel_gap <= refine_rel_tol and feasible:
            converged = True
            break

    if best is None:
        return {"success": False,
                "error": "EO refinement produced no usable point.",
                "n_samples": int(len(pool_X))}

    x_best = best["x"]

    # ── Surrogate-quality guard: honest, CROSS-VALIDATED fit of the objective.
    # In-sample R² overfits; CV R² says whether the surrogate actually predicts.
    # A low value means the EO optimum is untrustworthy for this tightly-coupled
    # problem → recommend a direct (non-surrogate) optimizer.
    import math as _math
    cv = _cv_r2(np.atleast_2d(pool_X), pool_obj, n)
    cv_ok = (not _math.isnan(cv)) and cv >= _CV_R2_TRUST
    trustworthy = bool(cv_ok or (best["gap"] is not None
                                 and best["obj_actual"] is not None
                                 and abs(best["gap"]) <=
                                 1e-2 * (abs(best["obj_actual"]) + 1e-9)))

    note = ("Equation-oriented surrogate NLP with adaptive refinement"
            + (" (converged)" if converged else "")
            + ". Solver: " + solver_used)
    if not trustworthy:
        note += (f". ⚠ Surrogate may be UNRELIABLE here (cross-validated "
                 f"R²={cv:.2f} < {_CV_R2_TRUST}); the flowsheet is likely too "
                 f"nonlinear/coupled for a quadratic surrogate — prefer a direct "
                 f"optimizer (optimize_constrained) or raise n_samples")
    if not ipopt_available():
        note += "; install an IPOPT binary for the full large-scale EO engine"

    return {
        "success": True,
        "x": {f"{v['tag']}.{v['property']}": round(x_best[i], 6)
              for i, v in enumerate(variables)},
        "objective_surrogate": round(float(best["obj_surrogate"]), 6),
        "objective_actual": (round(float(best["obj_actual"]), 6)
                             if best["obj_actual"] is not None else None),
        "surrogate_gap": (round(float(best["gap"]), 6)
                          if best["gap"] is not None else None),
        "feasible": best["feasible"],
        "solver": solver_used,
        "r2_objective": round(float(obj_r2), 4),
        "cv_r2_objective": (round(float(cv), 4) if not _math.isnan(cv) else None),
        "trustworthy": trustworthy,
        "r2_constraints": con_r2,
        "n_samples": int(len(pool_X)),
        "n_refinements": len(refinement_history),
        "refinement_history": refinement_history,
        "converged": converged,
        "minimize": minimize,
        "note": note + ".",
    }


def _solve_with_ipopt(obj_coef, con_coef, cspecs, lo, hi, x0, n, minimize):
    """Express the quadratic surrogate model in Pyomo and solve with IPOPT."""
    from pyomo.environ import (ConcreteModel, Var, Objective, Constraint,
                               minimize as PYO_MIN,
                               maximize as PYO_MAX, value)
    solver = _ipopt_solver()
    if solver is None:
        raise RuntimeError("IPOPT solver not available")
    pairs = _quad_terms(n)

    def _expr(coef, xvars):
        e = coef[0]
        for i in range(n):
            e = e + coef[1 + i] * xvars[i]
        for k, (i, j) in enumerate(pairs):
            e = e + coef[1 + n + k] * xvars[i] * xvars[j]
        return e

    m = ConcreteModel()
    m.I = range(n)
    m.x = Var(m.I, bounds=lambda mm, i: (float(lo[i]), float(hi[i])),
              initialize=lambda mm, i: float(x0[i]))
    xv = [m.x[i] for i in m.I]
    m.obj = Objective(expr=_expr(obj_coef, xv),
                      sense=PYO_MIN if minimize else PYO_MAX)

    m.cons = Constraint(range(len(cspecs)))
    for k, spec in enumerate(cspecs):
        op = spec.get("operator", ">="); lim = float(spec.get("value", 0.0))
        g = _expr(con_coef[k], xv)
        if op == "<=":
            m.cons[k] = (g <= lim)
        elif op == ">=":
            m.cons[k] = (g >= lim)
        else:
            m.cons[k] = (g == lim)

    solver.solve(m, tee=False)
    return [float(value(m.x[i])) for i in m.I], "IPOPT (Pyomo, equation-oriented)"
