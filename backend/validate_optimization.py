#!/usr/bin/env python3
"""
Empirical validation of the optimization engine.

The project's optimizers evaluate an objective by setting decision variables and
reading a result back through the bridge. The numerical solvers are independent
of DWSIM and the LLM, so we can validate them rigorously and reproducibly the
standard way the optimization literature does: run them on benchmark problems
with KNOWN global optima / analytical answers and measure how close they get.

Three modalities are covered, matching the project's claims:
  1. Single-objective global search  — 5 standard functions, known min = 0
  2. Multi-objective (NSGA-II)        — known Pareto front, check spread + accuracy
  3. Global sensitivity (Sobol)       — Ishigami function, known analytical indices

This validates ALGORITHM CORRECTNESS. It is complementary to (not a substitute
for) an end-to-end run against a live DWSIM flowsheet, which additionally
exercises the bridge coupling and process physics.

Usage:  python validate_optimization.py     # prints results + writes OPTIMIZATION_VALIDATION.md
"""
from __future__ import annotations
import math
import os
from typing import Any, Callable, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── Mock bridge: turns an analytic function into a "flowsheet objective" ──────
class _AnalyticBridge:
    """Reproduces the project's test pattern: decision variables are written via
    set_*; the objective stream OBJ.value returns f(current variables)."""
    def __init__(self, fn: Callable[[Dict], float]):
        self.props: Dict = {}
        self._fn = fn

    def get_stream_property(self, tag, prop):
        if tag == "OBJ" and prop == "value":
            return {"success": True, "value": self._fn(self.props)}
        v = self.props.get((tag, prop))
        return {"success": v is not None, "value": v}

    def get_stream_properties(self, tag):
        return {"success": True,
                "properties": {k[1]: v for k, v in self.props.items() if k[0] == tag}}
    get_unit_op_properties = get_stream_properties

    def set_stream_property(self, tag, prop, value, unit=""):
        self.props[(tag, prop)] = float(value); return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self.props[(tag, prop)] = float(value); return {"success": True}

    def run_simulation(self):  return {"success": True}
    def save_and_solve(self):  return {"success": True}


def _xy(p):  # read the two decision variables from the bridge props
    return p.get(("X1", "v"), 0.0), p.get(("X2", "v"), 0.0)


# ── Standard 2-D benchmark functions (global minimum = 0) ─────────────────────
BENCHMARKS = [
    # name, f(x,y), known optimum (x*,y*), bounds, multimodal?
    ("Sphere",     lambda x, y: x*x + y*y,                                  (0, 0),   (-5, 5),  False),
    ("Booth",      lambda x, y: (x + 2*y - 7)**2 + (2*x + y - 5)**2,        (1, 3),   (-10, 10),False),
    ("Rosenbrock", lambda x, y: (1 - x)**2 + 100*(y - x*x)**2,             (1, 1),   (-2, 3),  False),
    ("Rastrigin",  lambda x, y: 20 + (x*x - 10*math.cos(2*math.pi*x))
                                    + (y*y - 10*math.cos(2*math.pi*y)),     (0, 0),   (-5.12, 5.12), True),
    ("Ackley",     lambda x, y: (-20*math.exp(-0.2*math.sqrt(0.5*(x*x+y*y)))
                                 - math.exp(0.5*(math.cos(2*math.pi*x)+math.cos(2*math.pi*y)))
                                 + math.e + 20),                            (0, 0),   (-5, 5),  True),
]
METHODS = ["cma", "de", "simplex"]
# Convergence threshold on the (known = 0) objective. Multimodal functions are
# harder for local methods — that contrast is itself part of the validation.
PASS_EPS = 0.1


def _run_single_objective() -> List[Dict[str, Any]]:
    from dwsim_native_optimizer import run_dwsim_native_optimization
    rows = []
    for name, f, (xs, ys), (lo, hi), multimodal in BENCHMARKS:
        for method in METHODS:
            br = _AnalyticBridge(lambda p, f=f: (lambda xy: f(*xy))(_xy(p)))
            # deliberately start away from the optimum
            br.props[("X1", "v")] = lo + 0.7 * (hi - lo)
            br.props[("X2", "v")] = lo + 0.3 * (hi - lo)
            try:
                res = run_dwsim_native_optimization(
                    br,
                    variables=[
                        {"tag": "X1", "property": "v", "unit": "", "lower": lo, "upper": hi,
                         "initial": br.props[("X1", "v")]},
                        {"tag": "X2", "property": "v", "unit": "", "lower": lo, "upper": hi,
                         "initial": br.props[("X2", "v")]},
                    ],
                    objective={"type": "variable", "tag": "OBJ", "property": "value"},
                    method=method, minimize=True, max_iter=400, tolerance=1e-8,
                )
                best = res.get("best_objective")
                by = {r["variable"]: r["new_value"] for r in res.get("variables_table", [])}
                dist = (math.hypot(by.get("X1.v", 1e9) - xs, by.get("X2.v", 1e9) - ys)
                        if by else float("nan"))
                ok = res.get("success") and best is not None and best < PASS_EPS
            except Exception as exc:
                best, dist, ok = None, None, False
                res = {"error": str(exc)}
            rows.append({"function": name, "multimodal": multimodal, "method": method,
                         "best": best, "dist_to_opt": dist, "pass": bool(ok),
                         "backend": res.get("solver_backend", res.get("error", "?"))})
    return rows


def _run_multiobjective() -> Dict[str, Any]:
    """NSGA-II on a known non-convex front: f1=x, f2=1-sqrt(x), x in [0,1].
    The true front is f2 = 1 - sqrt(f1); validate spread + on-front accuracy."""
    try:
        from multiobjective_nsga import run_nsga2, pymoo_available
        if not pymoo_available():
            return {"available": False}
        out = run_nsga2(lambda x: [x[0], 1.0 - x[0] ** 0.5],
                        [{"tag": "X", "property": "v", "unit": "", "lower": 0.0, "upper": 1.0}],
                        [{"tag": "F1", "property": "val", "minimize": True},
                         {"tag": "F2", "property": "val", "minimize": True}],
                        pop_size=40, n_gen=40, seed=1)
        pts = out.get("pareto_front", [])
        xs = sorted(p["optimal_variables"]["X.v"] for p in pts)
        # max deviation of returned points from the analytic front
        dev = max((abs(p["objective_values"]["F2.val"] - (1 - p["objective_values"]["F1.val"] ** 0.5))
                   for p in pts), default=float("nan"))
        return {"available": True, "n_points": len(pts),
                "x_min": xs[0] if xs else None, "x_max": xs[-1] if xs else None,
                "front_max_dev": dev,
                "spread_ok": bool(xs and xs[0] < 0.2 and xs[-1] > 0.8),
                "accuracy_ok": dev < 1e-2}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _run_sensitivity() -> Dict[str, Any]:
    """Sobol indices on Ishigami (a=7, b=0.1) vs the textbook analytical values."""
    try:
        from global_sensitivity import run_global_sensitivity, salib_available
        if not salib_available():
            return {"available": False}
        PI = math.pi
        def ishigami(x, a=7.0, b=0.1):
            return math.sin(x[0]) + a*math.sin(x[1])**2 + b*(x[2]**4)*math.sin(x[0])
        out = run_global_sensitivity(
            ishigami,
            [{"tag": "X1", "property": "v", "lower": -PI, "upper": PI},
             {"tag": "X2", "property": "v", "lower": -PI, "upper": PI},
             {"tag": "X3", "property": "v", "lower": -PI, "upper": PI}],
            output_name="f", method="sobol", n_samples=1024, seed=1)
        # Analytical Ishigami indices (a=7, b=0.1)
        ref_S1 = {"X1.v": 0.314, "X2.v": 0.442, "X3.v": 0.0}
        ref_ST = {"X1.v": 0.558, "X2.v": 0.442, "X3.v": 0.244}
        idx = {r["variable"]: r for r in out.get("ranking", [])}
        rows = []
        for v in ("X1.v", "X2.v", "X3.v"):
            s1 = idx.get(v, {}).get("S1"); st = idx.get(v, {}).get("ST")
            rows.append({"var": v, "S1": s1, "S1_ref": ref_S1[v],
                         "ST": st, "ST_ref": ref_ST[v],
                         "S1_err": (abs(s1 - ref_S1[v]) if s1 is not None else None),
                         "ST_err": (abs(st - ref_ST[v]) if st is not None else None)})
        max_err = max((r["ST_err"] for r in rows if r["ST_err"] is not None), default=None)
        return {"available": True, "rows": rows, "max_ST_err": max_err,
                "accuracy_ok": (max_err is not None and max_err < 0.1)}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def _fmt(v, nd=4):
    return "—" if v is None else (f"{v:.{nd}g}" if isinstance(v, float) else str(v))


def main() -> None:
    so = _run_single_objective()
    mo = _run_multiobjective()
    se = _run_sensitivity()
    L: List[str] = []
    def w(s=""): L.append(s)

    w("# Optimization Engine — Empirical Validation")
    w()
    w("Generated by `validate_optimization.py`. Solvers run on benchmark problems "
      "with **known optima**; this measures algorithm correctness independent of "
      "DWSIM and the LLM. Global minimum of every single-objective function is 0.")
    w()
    w("## 1. Single-objective global search (known min = 0)")
    w()
    w("| Function | Multimodal | Method | Best objective | Dist→optimum | Pass (<0.1) |")
    w("|---|:--:|---|--:|--:|:--:|")
    passes = 0
    for r in so:
        passes += 1 if r["pass"] else 0
        w(f"| {r['function']} | {'yes' if r['multimodal'] else 'no'} | {r['method']} "
          f"| {_fmt(r['best'])} | {_fmt(r['dist_to_opt'])} | "
          f"{'✅' if r['pass'] else '❌'} |")
    w(f"\n**{passes}/{len(so)}** (method, function) cases reached the global optimum "
      f"within {PASS_EPS}. Local methods (simplex) are expected to miss on "
      f"multimodal functions (Rastrigin/Ackley) — global methods (CMA-ES, DE) "
      f"are what should succeed there.")
    # Per-function: solved by at least one shipped solver? (The orchestrator
    # cascades global→local in real use, so this reflects deployed behaviour.)
    by_fn: Dict[str, List[str]] = {}
    for r in so:
        by_fn.setdefault(r["function"], [])
        if r["pass"]:
            by_fn[r["function"]].append(r["method"])
    solved = sum(1 for fn in by_fn if by_fn[fn])
    w()
    w(f"**Per-function (best-of-suite): {solved}/{len(by_fn)} benchmarks solved** "
      f"by at least one shipped solver:")
    w()
    w("| Function | Solved by |")
    w("|---|---|")
    for fn in [b[0] for b in BENCHMARKS]:
        ms = by_fn.get(fn, [])
        w(f"| {fn} | {', '.join(ms) if ms else '— none —'} |")
    w()
    w("## 2. Multi-objective Pareto front (NSGA-II, non-convex)")
    w()
    if mo.get("available"):
        w(f"- Front points: **{mo['n_points']}**")
        w(f"- Spread in x: [{_fmt(mo['x_min'])}, {_fmt(mo['x_max'])}] "
          f"→ spread {'✅' if mo['spread_ok'] else '❌'} (endpoints reached)")
        w(f"- Max deviation from analytic front f2=1−√f1: **{_fmt(mo['front_max_dev'])}** "
          f"→ accuracy {'✅' if mo['accuracy_ok'] else '❌'} (<0.01)")
    else:
        w(f"_NSGA-II unavailable: {mo.get('error', 'pymoo not installed')}_")
    w()
    w("## 3. Global sensitivity — Sobol indices vs analytical (Ishigami)")
    w()
    if se.get("available"):
        w("| Variable | S1 (got) | S1 (ref) | ST (got) | ST (ref) | ST error |")
        w("|---|--:|--:|--:|--:|--:|")
        for r in se["rows"]:
            w(f"| {r['var']} | {_fmt(r['S1'],3)} | {r['S1_ref']} | {_fmt(r['ST'],3)} "
              f"| {r['ST_ref']} | {_fmt(r['ST_err'],3)} |")
        w(f"\nMax total-order error **{_fmt(se['max_ST_err'],3)}** → "
          f"{'✅ within 0.1' if se['accuracy_ok'] else '❌'} of the textbook values.")
    else:
        w(f"_Sensitivity unavailable: {se.get('error', 'SALib not installed')}_")
    w()
    w("## Verdict")
    w()
    w(f"- Single-objective global search: **{passes}/{len(so)}** cases hit the known optimum.")
    w(f"- Multi-objective: {'recovers a well-spread, accurate Pareto front' if mo.get('available') and mo.get('spread_ok') and mo.get('accuracy_ok') else 'see above'}.")
    w(f"- Sensitivity: {'recovers analytical Sobol indices' if se.get('available') and se.get('accuracy_ok') else 'see above'}.")
    w()
    w("**Scope:** this validates the solver algorithms on standard problems. "
      "End-to-end optimization on a live DWSIM flowsheet (bridge coupling + "
      "process physics) is a separate test — run the agent/optimiser against a "
      "loaded flowsheet to cover it.")

    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "OPTIMIZATION_VALIDATION.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md.encode("ascii", "replace").decode("ascii"))


if __name__ == "__main__":
    main()
