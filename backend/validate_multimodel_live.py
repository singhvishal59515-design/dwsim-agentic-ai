#!/usr/bin/env python3
"""
Live validation of multi_model_uncertainty against a real DWSIM engine (no LLM).

Builds the same water-heater flowsheet under three property packages
(Peng-Robinson, SRK, Steam Tables/IAPWS-IF97) and reports the spread of every
stream output. Water is a deliberately telling case: the cubic EOS (PR/SRK) are
poor for liquid-water density while Steam Tables is accurate, so the density
spread is large and concrete — the capability tells the user "for water, the
thermo package matters; use Steam Tables", which is exactly the honest,
measured counter to a commercial tool's validated-thermo advantage.

Run on a machine with DWSIM installed:  python validate_multimodel_live.py
"""
from __future__ import annotations
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

SPEC = {
    "name": "mmu_water_heater",
    "compounds": ["Water"],
    "property_package": "Peng-Robinson (PR)",   # overridden per model
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
         "pressure": 1, "pressure_unit": "atm",
         "massflow": 1.0, "massflow_unit": "kg/s",
         "composition": {"Water": 1.0}},
    ],
    "unit_op_specs": [
        {"tag": "H-101", "property_name": "outlet_temperature", "value": 80, "unit": "C"},
    ],
}

PACKAGES = ["Peng-Robinson (PR)", "Soave-Redlich-Kwong (SRK)",
            "Steam Tables (IAPWS-IF97)"]
OBSERVE = ["temperature_C", "pressure_bar", "mass_flow_kgh",
           "vapor_fraction", "density_kg_m3"]


def main() -> int:
    from dwsim_bridge_v2 import DWSIMBridgeV2

    print("[live] initializing DWSIM…", flush=True)
    b = DWSIMBridgeV2(); b.initialize()

    res = b.multi_model_uncertainty(SPEC, property_packages=PACKAGES,
                                    observe_props=OBSERVE)
    if not res.get("success"):
        print(f"[live] FAILED: {res.get('error')}")
        print(f"       model_status: {res.get('model_status')}")
        return 2

    summ = res["summary"]
    obs  = res["observations"]
    print(f"[live] models solved: {summ['models']}", flush=True)
    print(f"[live] {summ['interpretation']}", flush=True)

    L = ["# Multi-Model Thermodynamic Uncertainty (live DWSIM)", "",
         "The SAME water-heater flowsheet (Feed 25 °C / 1 atm / 1 kg/s, heated to "
         "80 °C) solved under three property packages; every stream output is "
         "compared across models. No LLM is involved.", "",
         f"**Models solved:** {', '.join(summ['models'])}  ",
         f"**Most model-dependent output:** {summ['most_sensitive']} "
         f"({summ['max_rel_spread_pct']:.2f} % spread)  ",
         f"**Verdict:** {'ROBUST' if summ['robust'] else 'MODEL-DEPENDENT'}", "",
         "| Output | " + " | ".join(summ["models"]) + " | spread % |",
         "|---|" + "|".join("--:" for _ in summ["models"]) + "|--:|"]
    for label, o in obs.items():
        row = [label]
        for m in summ["models"]:
            v = o["by_model"].get(m)
            row.append(f"{v:.4g}" if isinstance(v, (int, float)) else "—")
        rel = o["rel_spread_pct"]
        row.append(f"{rel:.2f}" if rel is not None else "n/a")
        L.append("| " + " | ".join(row) + " |")
    L += ["", f"_{summ['interpretation']}_", "",
          "**Why this matters vs a commercial simulator:** a validated-thermo "
          "tool gives one number; this gives the number AND how much it depends "
          "on the model — surfacing, in one call, when a result is only as good "
          "as the package choice (here, liquid-water density under cubic EOS).",
          "", f"_model_status: {res.get('model_status')}_"]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "MULTIMODEL_VALIDATION.md"), "w",
              encoding="utf-8") as f:
        f.write(md)
    print("\n" + md.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
