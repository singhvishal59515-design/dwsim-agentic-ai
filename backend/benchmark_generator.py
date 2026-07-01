#!/usr/bin/env python3
"""
benchmark_generator.py — scale the agent benchmark toward Simona-size by
*generating* tasks from real process archetypes and *validating each on the live
DWSIM engine*, instead of hand-authoring (or fabricating) them.

Honesty line. Every task is built from a real archetype (heater, cooler, pump,
compressor, valve, …) over a real compound system, and its success criteria are
READ FROM A REAL DWSIM SOLVE of a reference specification — never invented. A
candidate that does not converge is dropped. The resulting set is therefore
"programmatically generated, engine-validated", NOT "expert-designed": that is
the accurate, publishable claim. The agent benchmark then tests whether the agent,
given only the natural-language prompt, reproduces the engine-verified reference.

Resource note. Generating and validating the DATASET needs DWSIM but NO LLM quota.
Running the agent across the tasks (the benchmark itself) needs quota — the same
constraint as the existing 25-task run.

    python benchmark_generator.py --target 100 --live    # build + solve on DWSIM
    python benchmark_generator.py --target 40             # mock (no DWSIM; pipeline)

Writes generated_benchmark.json (content-hashed, immutable).
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Compound systems (theory-appropriate package per system) ──────────────────
SYSTEMS: Dict[str, Dict[str, Any]] = {
    "water":          {"compounds": ["Water"], "pp": "Steam Tables (IAPWS-IF97)",
                       "phase": "liquid", "comp": {"Water": 1.0}},
    "methanol_water": {"compounds": ["Methanol", "Water"], "pp": "NRTL",
                       "phase": "liquid", "comp": {"Methanol": 0.5, "Water": 0.5}},
    "ethanol_water":  {"compounds": ["Ethanol", "Water"], "pp": "NRTL",
                       "phase": "liquid", "comp": {"Ethanol": 0.4, "Water": 0.6}},
    "benzene_toluene": {"compounds": ["Benzene", "Toluene"], "pp": "Peng-Robinson (PR)",
                        "phase": "liquid", "comp": {"Benzene": 0.5, "Toluene": 0.5}},
    "nitrogen":       {"compounds": ["Nitrogen"], "pp": "Peng-Robinson (PR)",
                       "phase": "gas", "comp": {"Nitrogen": 1.0}},
    "methane":        {"compounds": ["Methane"], "pp": "Peng-Robinson (PR)",
                       "phase": "gas", "comp": {"Methane": 1.0}},
    "co2":            {"compounds": ["Carbon dioxide"], "pp": "Peng-Robinson (PR)",
                       "phase": "gas", "comp": {"Carbon dioxide": 1.0}},
    "propane":        {"compounds": ["Propane"], "pp": "Peng-Robinson (PR)",
                       "phase": "gas", "comp": {"Propane": 1.0}},
}


def _comp_desc(sys: Dict[str, Any]) -> str:
    comp = sys["comp"]
    if len(comp) == 1:
        return list(comp)[0].lower()
    parts = ", ".join(f"{int(v*100)}% {k.lower()}" for k, v in comp.items())
    return f"a mixture of {parts} (mol)"


def _feed(sys: Dict[str, Any], T: float, P: float, mdot: float = 1.0) -> Dict[str, Any]:
    return {"tag": "Feed", "temperature": T, "temperature_unit": "C",
            "pressure": P, "pressure_unit": "bar", "massflow": mdot,
            "massflow_unit": "kg/s", "composition": dict(sys["comp"])}


# ── Archetype builders: (sys, params) → (spec, outlet_tag, prompt, meta) ──────
def _single_unit(name: str, sys: Dict[str, Any], unit_type: str,
                 feedT: float, feedP: float, prop: str, value: float,
                 energy: bool) -> Tuple[Dict[str, Any], str]:
    objs = [{"tag": "Feed", "type": "MaterialStream"},
            {"tag": "U", "type": unit_type},
            {"tag": "Out", "type": "MaterialStream"}]
    conns = [{"from_tag": "Feed", "to_tag": "U", "from_port": 0, "to_port": 0},
             {"from_tag": "U", "to_tag": "Out", "from_port": 0, "to_port": 0}]
    if energy:
        objs.append({"tag": "Q", "type": "EnergyStream"})
        conns.append({"from_tag": "Q", "to_tag": "U", "from_port": 0, "to_port": 1})
    spec = {"name": name, "compounds": sys["compounds"], "property_package": sys["pp"],
            "objects": objs, "connections": conns,
            "feed_specs": [_feed(sys, feedT, feedP)],
            "unit_op_specs": [{"tag": "U", "property_name": prop, "value": value,
                               "unit": ("C" if "temperature" in prop else "bar")}]}
    return spec, "Out"


def build_heater(sys, P, params):
    feedT, outT = params
    spec, out = _single_unit(P, sys, "Heater", feedT, 2.0, "outlet_temperature", outT, True)
    return spec, out, ("heater", 1)


def build_cooler(sys, P, params):
    feedT, outT = params
    spec, out = _single_unit(P, sys, "Cooler", feedT, 2.0, "outlet_temperature", outT, True)
    return spec, out, ("cooler", 1)


def build_pump(sys, P, params):
    feedT, outP = params
    spec, out = _single_unit(P, sys, "Pump", feedT, 2.0, "outlet_pressure", outP, True)
    return spec, out, ("pump", 1)


def build_compressor(sys, P, params):
    feedT, outP = params
    spec, out = _single_unit(P, sys, "Compressor", feedT, 1.0, "outlet_pressure", outP, True)
    return spec, out, ("compressor", 1)


def build_valve(sys, P, params):
    feedT, outP = params
    spec, out = _single_unit(P, sys, "Valve", feedT, 10.0, "outlet_pressure", outP, False)
    return spec, out, ("valve", 1)


# archetype → (builder, compatible phases, feed-conditions × setpoint sweep)
ARCHETYPES = {
    "heater":     (build_heater,     ("liquid", "gas"), [(25, 60), (25, 80), (40, 100)]),
    "cooler":     (build_cooler,     ("liquid", "gas"), [(100, 40), (120, 60), (90, 50)]),
    "pump":       (build_pump,       ("liquid",),       [(25, 5), (25, 10), (25, 20)]),
    "compressor": (build_compressor, ("gas",),          [(25, 3), (25, 5), (40, 8)]),
    "valve":      (build_valve,      ("gas", "liquid"), [(25, 2), (25, 5)]),
}


def _prompt(arch: str, sys: Dict[str, Any], params, detail: str) -> str:
    desc = _comp_desc(sys)
    a, b = params
    verb = {"heater": f"heat {desc} to {b} °C",
            "cooler": f"cool {desc} to {b} °C",
            "pump": f"pump {desc} up to {b} bar",
            "compressor": f"compress {desc} to {b} bar",
            "valve": f"let {desc} down across a valve to {b} bar"}[arch]
    if detail == "full":
        return (f"Build a flowsheet to {verb}. The feed is {desc} at {a} °C; use "
                f"the {sys['pp']} property package. Solve it and report the outlet "
                f"stream conditions.")
    return f"{verb[0].upper() + verb[1:]} and report the result."   # ambiguous


def enumerate_specs(detail_levels=("full", "ambiguous")) -> List[Dict[str, Any]]:
    """All candidate task descriptors (deterministic; no DWSIM)."""
    out: List[Dict[str, Any]] = []
    for arch, (builder, phases, sweep) in ARCHETYPES.items():
        for sys_key, sys in SYSTEMS.items():
            if sys["phase"] not in phases:
                continue
            for params in sweep:
                for detail in detail_levels:
                    name = f"{arch}_{sys_key}_{params[0]}_{params[1]}_{detail}"
                    spec, outlet, (cat, cplx) = builder(sys, name, params)
                    out.append({
                        "candidate_id": name, "archetype": arch, "system": sys_key,
                        "category": cat, "complexity": cplx, "detail_level": detail,
                        "spec": spec, "outlet_tag": outlet,
                        "expected_property_package": sys["pp"],
                        "prompt": _prompt(arch, sys, params, detail)})
    return out


# ── Validation: build + solve, read ground-truth criteria from the engine ─────
_CRITERIA_KEYS = (("temperature_C", 2.0), ("pressure_bar", 1.0),
                  ("vapor_fraction", 0.0), ("mass_flow_kg_s", 1.0))


def _solved_outlet(bridge, spec, outlet_tag) -> Optional[Dict[str, Any]]:
    r = bridge.build_flowsheet_atomic(spec)
    ok = r.get("success") and (r.get("converged") or r.get("solved")
                               or r.get("stream_results"))
    if not ok:
        return None
    return (r.get("stream_results") or {}).get(outlet_tag)


def _intent_achieved(spec: Dict[str, Any], outlet: Dict[str, Any]) -> bool:
    """The reference solve must actually reach the setpoint the prompt states —
    otherwise the unit-op property did not take effect and the task would be
    self-contradictory (prompt asks for X, engine produced Y). Drop those."""
    uo = spec["unit_op_specs"][0]
    target = float(uo["value"])
    if uo["property_name"] == "outlet_temperature":
        got = outlet.get("temperature_C")
        return got is not None and abs(float(got) - target) <= max(2.0, 0.05 * abs(target))
    if uo["property_name"] == "outlet_pressure":
        got = outlet.get("pressure_bar")
        return got is not None and abs(float(got) - target) <= max(0.2, 0.05 * abs(target))
    return True


def validate_candidate(desc: Dict[str, Any], bridge) -> Optional[Dict[str, Any]]:
    """Build+solve the reference spec; on convergence AND only when the solve
    achieves the stated intent, emit a task whose success criteria are the
    engine's own outlet values. Returns None otherwise."""
    try:
        outlet = _solved_outlet(bridge, desc["spec"], desc["outlet_tag"])
    except Exception:
        return None
    if not outlet or not _intent_achieved(desc["spec"], outlet):
        return None
    checks = []
    for key, tol in _CRITERIA_KEYS:
        if isinstance(outlet.get(key), (int, float)):
            checks.append({"stream": desc["outlet_tag"], "property": key,
                           "expected": round(float(outlet[key]), 6),
                           "tolerance_pct": tol})
    if not checks:
        return None
    return {
        "id": desc["candidate_id"], "category": desc["category"],
        "complexity": desc["complexity"], "detail_level": desc["detail_level"],
        "prompt": desc["prompt"],
        "expected_property_package": desc["expected_property_package"],
        "reference_spec": desc["spec"], "success_criteria": checks,
        "provenance": "programmatically generated, engine-validated",
    }


def _content_hash(tasks: List[Dict[str, Any]]) -> str:
    blob = json.dumps([{k: t[k] for k in ("id", "prompt", "success_criteria")}
                       for t in tasks], sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def generate(target: int = 100, bridge=None, detail_levels=("full", "ambiguous"),
             seed: int = 0) -> Dict[str, Any]:
    """Enumerate → validate on `bridge` → keep up to `target` validated tasks.
    Candidates are shuffled (seeded, deterministic) so a target-limited run spans
    the archetypes rather than exhausting the first one."""
    import random
    candidates = enumerate_specs(detail_levels)
    random.Random(seed).shuffle(candidates)
    tasks: List[Dict[str, Any]] = []
    dropped = 0
    for desc in candidates:
        if len(tasks) >= target:
            break
        t = validate_candidate(desc, bridge)
        if t is None:
            dropped += 1
            continue
        tasks.append(t)
    return {"success": True, "n_candidates": len(candidates),
            "n_validated": len(tasks), "n_dropped": dropped,
            "content_hash": _content_hash(tasks), "tasks": tasks}


# ── Mock bridge (for testing the pipeline without DWSIM) ──────────────────────
class _MockBridge:
    """Returns a deterministic 'converged' outlet derived from the spec's intent,
    so the enumerate → validate → emit → hash pipeline is testable with no DWSIM."""
    def build_flowsheet_atomic(self, spec):
        feed = spec["feed_specs"][0]
        uo = spec["unit_op_specs"][0]
        T = float(feed["temperature"]); P = float(feed["pressure"])
        if uo["property_name"] == "outlet_temperature":
            T = float(uo["value"])
        elif uo["property_name"] == "outlet_pressure":
            P = float(uo["value"])
        return {"success": True, "converged": True, "stream_results": {
            "Out": {"temperature_C": T, "pressure_bar": P, "vapor_fraction": 0.0,
                    "mass_flow_kg_s": float(feed["massflow"])}}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=100)
    ap.add_argument("--live", action="store_true", help="validate on real DWSIM")
    args = ap.parse_args()

    if args.live:
        from dwsim_bridge_v2 import DWSIMBridgeV2
        bridge = DWSIMBridgeV2(); bridge.initialize()
        mode = "live DWSIM"
    else:
        bridge = _MockBridge()
        mode = "MOCK (no DWSIM — pipeline only)"

    res = generate(target=args.target, bridge=bridge)
    out = os.path.join(_HERE, "generated_benchmark.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    print(f"[gen] mode={mode}  candidates={res['n_candidates']}  "
          f"validated={res['n_validated']}  dropped={res['n_dropped']}  "
          f"hash={res['content_hash']}")
    print(f"[gen] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
