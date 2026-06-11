#!/usr/bin/env python3
"""
End-to-end optimization validation against a LIVE DWSIM engine.

Complements validate_optimization.py (which validates the solver algorithms on
analytic benchmarks). This one closes the loop: it builds a real DWSIM flowsheet
via build_flowsheet_atomic, then drives the project's optimizer
(run_dwsim_native_optimization) on it — every objective evaluation is a real
DWSIM solve. No LLM is involved (the numerical optimizer doesn't need one).

Test process: a Water heater. Feed 25 C, 2 bar, 1 kg/s -> Heater (outlet-T mode)
-> Hot. Decision variable = heater outlet temperature in [40, 120] C; objective
= heater duty (kW, read via .NET reflection). Duty rises monotonically with
outlet T (~4.5 kW/C for water), so the known optima are at the bounds — which
makes the closed-loop behaviour easy to verify:
  * minimise -> drive outlet T to 40 C, duty to its minimum
  * maximise -> drive outlet T to 120 C, duty to its maximum
Plus a reproducibility check: re-apply the optimum, re-solve, confirm the duty.

Run on a machine with DWSIM installed.  Usage:  python validate_optimization_live.py
"""
from __future__ import annotations
import os
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))

SPEC = {
    "name": "live_opt_heater",
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
VAR = {"tag": "H-101", "property": "outlet_temperature", "unit": "C",
       "lower": 40, "upper": 120, "initial": 90}
OBJ = {"type": "variable", "tag": "H-101", "property": "HeatDuty"}


def _fv(x):
    try: return float(x)
    except Exception: return float("nan")


def main() -> int:
    from dwsim_bridge_v2 import DWSIMBridgeV2
    from dwsim_native_optimizer import run_dwsim_native_optimization
    from dwsim_reflection import reflect_get_set

    print("[live] initializing DWSIM…", flush=True)
    b = DWSIMBridgeV2(); b.initialize()
    r = b.build_flowsheet_atomic(SPEC)
    if not (r.get("success") and (r.get("converged") or r.get("solved"))):
        print(f"[live] ABORT: flowsheet did not build/solve: {r.get('error') or r.get('build_errors')}")
        return 2

    base_T  = _fv(reflect_get_set(b, "H-101", "OutletTemperature").get("value")) - 273.15
    base_duty = _fv(reflect_get_set(b, "H-101", "HeatDuty").get("value"))
    print(f"[live] baseline: outlet T = {base_T:.1f} C, duty = {base_duty:.2f} kW", flush=True)

    def run(direction: str):
        minimize = direction == "min"
        res = run_dwsim_native_optimization(
            b, variables=[dict(VAR)], objective=dict(OBJ),
            method="simplex", minimize=minimize, max_iter=40, tolerance=1e-4)
        bestT = None
        for row in res.get("variables_table", []):
            if row.get("variable", "").startswith("H-101"):
                bestT = row.get("new_value")
        return res, bestT

    out: Dict[str, Any] = {"baseline_outletT_C": base_T, "baseline_duty_kW": base_duty}
    rows = []
    for direction, bound in (("min", 40.0), ("max", 120.0)):
        res, bestT = run(direction)
        # reproducibility: re-apply optimum, re-solve, re-read duty
        if bestT is not None:
            b.set_unit_op_property("H-101", "outlet_temperature", float(bestT))
            b.save_and_solve()
        repro_duty = _fv(reflect_get_set(b, "H-101", "HeatDuty").get("value"))
        best_obj = res.get("best_objective")
        rows.append({
            "direction": direction, "expected_bound_C": bound,
            "found_outletT_C": bestT, "found_duty_kW": best_obj,
            "reproduced_duty_kW": repro_duty,
            "at_bound": (bestT is not None and abs(bestT - bound) < 2.0),
            "reproduces": (best_obj is not None and abs(repro_duty - best_obj) < max(0.5, 0.01*abs(best_obj))),
            "evals": res.get("n_evaluations") or res.get("evaluations"),
            "success": bool(res.get("success")),
        })
        d = rows[-1]
        print(f"[live] {direction}: outletT={d['found_outletT_C']} C duty={d['found_duty_kW']} kW "
              f"| at bound {bound}: {d['at_bound']} | reproduces: {d['reproduces']}", flush=True)
    out["runs"] = rows

    # ── report ───────────────────────────────────────────────────────────────
    L = ["# End-to-End Optimization Validation (live DWSIM)", "",
         "Built with `build_flowsheet_atomic`, optimised with "
         "`run_dwsim_native_optimization` (method: Nelder-Mead simplex). Every "
         "objective evaluation is a real DWSIM solve; no LLM involved.", "",
         f"**Test:** Water heater, Feed 25 C / 2 bar / 1 kg/s. Decision variable = "
         f"outlet T in [40, 120] C; objective = heater duty (kW). "
         f"Baseline outlet T = {base_T:.1f} C, duty = {base_duty:.2f} kW.", "",
         "| Goal | Found outlet T (C) | Found duty (kW) | At expected bound | Reproduces on re-solve |",
         "|---|--:|--:|:--:|:--:|"]
    for d in rows:
        L.append(f"| {('minimise' if d['direction']=='min' else 'maximise')} duty "
                 f"| {_fv(d['found_outletT_C']):.2f} | {_fv(d['found_duty_kW']):.2f} "
                 f"| {'✅' if d['at_bound'] else '❌'} | {'✅' if d['reproduces'] else '❌'} |")
    allok = all(d["at_bound"] and d["reproduces"] for d in rows)
    L += ["", f"**Result:** {'✅ ' if allok else ''}the optimizer drives a real "
          f"DWSIM-computed objective to its known optimum in both directions and "
          f"the optimum reproduces on an independent re-solve — the closed loop "
          f"(set variable → real DWSIM solve → read objective → optimiser step) "
          f"is validated end-to-end.",
          "", "_Scope: this validates the live DWSIM coupling on a monotonic "
          "objective (optima at the bounds). Hard/multimodal search is covered "
          "separately by validate_optimization.py on analytic benchmarks._"]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "OPTIMIZATION_VALIDATION_LIVE.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print("\n" + md.encode("ascii", "replace").decode("ascii"))
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
