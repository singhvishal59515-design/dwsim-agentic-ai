#!/usr/bin/env python3
"""
Live surrogate-EO approximation-quality audit (no LLM).

Addresses reviewer point 3: "quantify surrogate EO approximation quality (e.g.,
cross-validation R^2 and prediction error at the reported optimum)."

The equation-oriented optimiser is surrogate-based (DOE -> quadratic surrogate ->
NLP -> validate with a real solve). Its honesty hinges on knowing when the
surrogate actually predicts the flowsheet. This script runs the EO on a live
DWSIM water-heater (decision variable = heater outlet T in [40,120] C; objective
= computed heater duty) and reports:

  * in-sample R^2 of the quadratic surrogate,
  * k-fold CROSS-VALIDATED R^2 (the trust metric — in-sample R^2 overfits),
  * the surrogate-vs-actual gap at the reported optimum (a real DWSIM solve at
    the predicted optimum minus the surrogate's prediction there),
  * whether adaptive refinement drove that gap down, and
  * the trustworthy flag.

Run on a machine with DWSIM installed:  python validate_eo_quality_live.py
"""
from __future__ import annotations
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

SPEC = {
    "name": "eo_quality_heater",
    "compounds": ["Water"],
    "property_package": "Peng-Robinson (PR)",
    "objects": [
        {"tag": "Feed",  "type": "MaterialStream"},
        {"tag": "H-101", "type": "Heater"},
        {"tag": "Hot",   "type": "MaterialStream"},
    ],
    "connections": [
        {"from_tag": "Feed",  "to_tag": "H-101"},
        {"from_tag": "H-101", "to_tag": "Hot"},
    ],
    "feed_specs": [
        {"tag": "Feed", "temperature": 25, "temperature_unit": "C",
         "pressure": 2, "pressure_unit": "bar",
         "massflow": 1.0, "massflow_unit": "kg/s",
         "composition": {"Water": 1.0}},
    ],
    "unit_op_specs": [
        {"tag": "H-101", "property_name": "outlet_temperature", "value": 90, "unit": "C"},
    ],
}


def _fv(x):
    try: return float(x)
    except Exception: return float("nan")


def main() -> int:
    from dwsim_bridge_v2 import DWSIMBridgeV2
    from eo_optimizer import run_eo_optimization
    from dwsim_reflection import reflect_get_set

    print("[live] initializing DWSIM…", flush=True)
    b = DWSIMBridgeV2(); b.initialize()
    r = b.build_flowsheet_atomic(SPEC)
    if not (r.get("success") and (r.get("converged") or r.get("solved") or r.get("stream_results"))):
        print(f"[live] ABORT: build/solve failed: {r.get('error') or r.get('build_errors')}")
        return 2

    n_solves = [0]

    def evaluate(x):
        """One real DWSIM solve: set outlet T (C), solve, read duty (kW)."""
        n_solves[0] += 1
        b.set_unit_op_property("H-101", "outlet_temperature", float(x[0]), "C")
        b.save_and_solve()
        duty = _fv(reflect_get_set(b, "H-101", "HeatDuty").get("value"))
        return {"objective": duty, "constraint_values": []}

    res = run_eo_optimization(
        evaluate,
        variables=[{"tag": "H-101", "property": "outlet_temperature",
                    "unit": "C", "lower": 40.0, "upper": 120.0, "initial": 90.0}],
        minimize=True, seed=42, validate=True, max_refine=3)

    if not res.get("success"):
        print(f"[live] EO failed: {res.get('error')}")
        return 1

    r2   = res.get("r2_objective")
    cvr2 = res.get("cv_r2_objective")
    gap  = res.get("surrogate_gap")
    surr = res.get("objective_surrogate")
    act  = res.get("objective_actual")
    nref = res.get("n_refinements")
    trust = res.get("trustworthy", res.get("converged"))
    gap_pct = (abs(gap) / abs(act) * 100.0) if (gap is not None and act) else None

    print(f"[live] EO: in-sample R²={r2} | cross-val R²={cvr2} | "
          f"surrogate gap at optimum={gap} ({gap_pct}%) | refinements={nref}",
          flush=True)

    L = ["# Surrogate-EO Approximation Quality (live DWSIM)", "",
         "The equation-oriented optimiser is surrogate-based, so its honesty "
         "depends on knowing when the surrogate predicts the flowsheet. Run live "
         "on a water-heater (decision variable = outlet T in [40,120] C; objective "
         "= computed heater duty), no LLM.", "",
         "| Metric | Value | Meaning |",
         "|---|--:|---|",
         f"| In-sample R² (objective) | {r2} | quadratic fit on the DOE samples |",
         f"| **Cross-validated R²** | **{cvr2}** | the trust metric (in-sample R² overfits) |",
         f"| Surrogate prediction at optimum | {surr} kW | what the surrogate predicted |",
         f"| Actual DWSIM solve at optimum | {act} kW | a real solve at the predicted optimum |",
         f"| **Surrogate-vs-actual gap** | **{gap}**"
         + (f" ({gap_pct:.3f}%)" if gap_pct is not None else "") + " | prediction error at the reported optimum |",
         f"| Adaptive refinements | {nref} | real solves added at the predicted optimum to sharpen the fit |",
         f"| DWSIM solves total | {n_solves[0]} | DOE + validation + refinement |",
         f"| Trustworthy flag | {trust} | cross-val R² ≥ 0.70 or gap within tolerance |",
         "",
         f"**Reading:** on this flowsheet the surrogate is "
         f"{'reliable' if trust else 'flagged as UNRELIABLE'} — cross-validated "
         f"R² = {cvr2}, and the prediction error at the reported optimum is "
         + (f"{gap_pct:.3f}%" if gap_pct is not None else "reported above") +
         f" after {nref} adaptive refinement(s). The cross-validated R² is the "
         f"honest guard: when it falls below 0.70 (as on the analytic Rosenbrock "
         f"valley in validate_optimization.py, whose curvature defeats a quadratic "
         f"surrogate) the EO optimum is explicitly flagged rather than trusted "
         f"blindly.",
         "", "_Scope: validates the surrogate-quality reporting on a smooth live "
         "objective; the trust flag's discriminating power on a hard (low-R²) "
         "objective is covered analytically (Rosenbrock) in validate_optimization.py._"]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "EO_QUALITY_VALIDATION.md"), "w",
              encoding="utf-8") as f:
        f.write(md)
    print("\n" + md.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
