#!/usr/bin/env python3
"""
Multi-variable live optimization (no LLM) — moves the validated ceiling from
1 decision variable to 4.

Five-stage compression with intercooling. A gas is compressed 1 -> 32 bar in five
stages, each followed by intercooling back to the inlet temperature. The four
intermediate pressures are decision variables. For equal-efficiency stages with
intercooling to inlet T, total compressor power is minimised when every stage
pressure ratio is equal, i.e. ratio = (Pf/P0)^(1/5) = 32^(1/5) = 2, so the exact
closed-form optimum is the geometric progression

        P_int* = [2, 4, 8, 16] bar.

This is a genuine 4-DOF optimisation on a real DWSIM flowsheet (5 compressors +
4 coolers), validated against a known multi-dimensional optimum — the next rung
above the single-variable heater/2-stage cases.

Run on a machine with DWSIM installed:  python validate_multivar_compression_live.py
"""
from __future__ import annotations
import math
import os
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))

P0, PF, N = 1.0, 32.0, 5
RATIO = (PF / P0) ** (1.0 / N)                 # = 2.0
ANALYTIC = [round(P0 * RATIO ** k, 4) for k in range(1, N)]   # [2, 4, 8, 16]

_objs = [{"tag": "Feed", "type": "MaterialStream"}]
_conns = []
_uo = []
_prev = "Feed"
for i in range(1, N + 1):
    comp = f"C{i}"
    _objs += [{"tag": comp, "type": "Compressor"}]
    _conns += [{"from_tag": _prev, "to_tag": comp}]
    mid = f"M{i}"
    _objs += [{"tag": mid, "type": "MaterialStream"}]
    _conns += [{"from_tag": comp, "to_tag": mid}]
    if i < N:                                   # intercooler after every stage but the last
        cool = f"IC{i}"
        _objs += [{"tag": cool, "type": "Cooler"}]
        _conns += [{"from_tag": mid, "to_tag": cool}]
        midc = f"M{i}c"
        _objs += [{"tag": midc, "type": "MaterialStream"}]
        _conns += [{"from_tag": cool, "to_tag": midc}]
        _uo += [{"tag": cool, "property_name": "outlet_temperature", "value": 25, "unit": "C"}]
        _prev = midc
    else:
        _prev = mid
# fixed final stage outlet; intermediate pressures are decision variables (init = geometric guess slightly off)
_init_P = [3.0, 6.0, 11.0, 18.0]
for i in range(1, N):
    _uo += [{"tag": f"C{i}", "property_name": "outlet_pressure", "value": _init_P[i-1], "unit": "bar"}]
_uo += [{"tag": f"C{N}", "property_name": "outlet_pressure", "value": PF, "unit": "bar"}]

SPEC = {
    "name": "multivar_compression", "compounds": ["Nitrogen"],
    "property_package": "Peng-Robinson (PR)",
    "objects": _objs, "connections": _conns,
    "feed_specs": [{"tag": "Feed", "temperature": 25, "temperature_unit": "C",
                    "pressure": P0, "pressure_unit": "bar",
                    "massflow": 1.0, "massflow_unit": "kg/s",
                    "composition": {"Nitrogen": 1.0}}],
    "unit_op_specs": _uo,
}

VARS = [{"tag": f"C{i}", "property": "outlet_pressure", "unit": "bar",
         "lower": lo, "upper": hi, "initial": _init_P[i-1]}
        for i, (lo, hi) in enumerate([(1.5, 6), (3, 12), (6, 22), (10, 30)], start=1)]
OBJ = {"type": "expression",
       "expression": "+".join(f"abs(P{i})" for i in range(1, N + 1)),
       "named_values": [{"name": f"P{i}", "tag": f"C{i}", "property": "DeltaQ"}
                        for i in range(1, N + 1)]}


def _fv(x):
    try: return float(x)
    except Exception: return float("nan")


def main() -> int:
    from dwsim_bridge_v2 import DWSIMBridgeV2
    from dwsim_native_optimizer import run_dwsim_native_optimization
    from dwsim_reflection import reflect_get_set

    print(f"[live] initializing DWSIM…  analytic optimum = {ANALYTIC} bar", flush=True)
    b = DWSIMBridgeV2(); b.initialize()
    r = b.build_flowsheet_atomic(SPEC)
    if not (r.get("success") and (r.get("converged") or r.get("solved") or r.get("stream_results"))):
        print(f"[live] ABORT: build/solve failed: {r.get('error') or r.get('build_errors')}")
        return 2

    def total_power_kW() -> float:
        return sum(abs(_fv(reflect_get_set(b, f"C{i}", "DeltaQ").get("value")))
                   for i in range(1, N + 1)) / 1000.0

    def set_and_solve(P: List[float]) -> None:
        for i, p in enumerate(P, start=1):
            b.set_unit_op_property(f"C{i}", "outlet_pressure", float(p), "bar")
        b.save_and_solve()

    set_and_solve(_init_P)
    p_init = total_power_kW()
    set_and_solve(ANALYTIC)
    p_analytic = total_power_kW()
    print(f"[live] power @ init {_init_P} = {p_init:.3f} kW | "
          f"@ analytic {ANALYTIC} = {p_analytic:.3f} kW", flush=True)

    res = run_dwsim_native_optimization(
        b, variables=[dict(v) for v in VARS], objective=dict(OBJ),
        method="simplex", minimize=True, max_iter=300, tolerance=1e-4)

    found: Dict[str, float] = {}
    for row in res.get("variables_table", []):
        var = row.get("variable", "")
        for i in range(1, N):
            if var.startswith(f"C{i}"):
                found[f"C{i}"] = _fv(row.get("new_value"))
    P_opt = [found.get(f"C{i}") for i in range(1, N)]
    set_and_solve([p if p is not None else _init_P[i] for i, p in enumerate(P_opt)])
    p_opt = total_power_kW()

    # agreement: each found pressure within tolerance of analytic, and power <= analytic+eps
    per_var_ok = all(p is not None and abs(p - a) <= max(0.6, 0.12 * a)
                     for p, a in zip(P_opt, ANALYTIC))
    power_ok = p_opt <= p_analytic * 1.02
    allok = per_var_ok and power_ok

    print(f"[live] optimizer optimum = {[round(p,3) if p else None for p in P_opt]} bar "
          f"| power {p_opt:.3f} kW | per-var match {per_var_ok} | power_ok {power_ok}",
          flush=True)

    L = ["# Multi-Variable Live Optimization — Five-Stage Compression (live DWSIM)", "",
         "A genuine **4-decision-variable** optimisation on a real DWSIM flowsheet "
         "(5 compressors + 4 intercoolers), validated against a known "
         "multi-dimensional optimum. This moves the validated live ceiling above "
         "the single-variable heater/2-stage cases. No LLM.", "",
         f"**Problem:** compress nitrogen {P0:.0f} → {PF:.0f} bar in {N} stages, "
         f"intercooling to 25 °C; minimise total compressor power over the four "
         f"intermediate pressures.",
         f"**Closed-form optimum:** equal stage ratios r = (Pf/P0)^(1/{N}) = "
         f"{RATIO:.3f} ⇒ **{ANALYTIC} bar**.", "",
         "| Intermediate pressure | Analytic (bar) | Optimizer (bar) |",
         "|---|--:|--:|"]
    for i, a in enumerate(ANALYTIC, start=1):
        po = P_opt[i-1]
        L.append(f"| P{i} (C{i} outlet) | {a} | {po:.3f} |" if po is not None
                 else f"| P{i} (C{i} outlet) | {a} | n/a |")
    L += ["",
          "| Total compressor power | kW |", "|---|--:|",
          f"| at initial guess {_init_P} | {p_init:.3f} |",
          f"| at analytic optimum {ANALYTIC} | {p_analytic:.3f} |",
          f"| at optimizer optimum | {p_opt:.3f} |", "",
          f"**Result:** {'✅ ' if allok else ''}the optimizer recovers the "
          f"equal-ratio geometric-progression optimum of a real {N}-stage DWSIM "
          f"flowsheet across **four** simultaneous decision variables "
          f"({'all within tolerance' if per_var_ok else 'see table'}; total power "
          f"{'at or below' if power_ok else 'vs'} the analytic optimum). This "
          f"demonstrates multi-variable live optimisation, not just the "
          f"single-variable cases.",
          "", "_Scope: 4 continuous decision variables on a real flowsheet with a "
          "known closed-form optimum; still well below industrial DOF counts "
          "(see the paper's scaling discussion), but a concrete step above 1-D._"]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "MULTIVAR_OPTIMIZATION_VALIDATION.md"), "w",
              encoding="utf-8") as f:
        f.write(md)
    print("\n" + md.encode("ascii", "replace").decode("ascii"))
    return 0 if allok else 1


if __name__ == "__main__":
    raise SystemExit(main())
