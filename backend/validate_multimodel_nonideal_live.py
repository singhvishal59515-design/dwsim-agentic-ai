#!/usr/bin/env python3
"""
Live non-ideal multi-model uncertainty demo (no LLM).

Addresses the reviewer ask: "show how the property-package choice quantitatively
shifts a real result." The water-heater demo (validate_multimodel_live.py) came
out ROBUST (0.07 %) because liquid water is well-behaved. Methanol-water near its
azeotrope is the opposite: the activity-coefficient models genuinely disagree on
the vapour-liquid split, so the SAME flowsheet gives materially different answers
depending on the package — exactly the model-form uncertainty the capability is
meant to surface.

Flowsheet: methanol/water 50/50 (mol) feed, heated to 75 C at 1 atm (a two-phase
state between the bubble and dew points), solved under NRTL, UNIQUAC, Wilson and
Modified UNIFAC (Dortmund). The reported per-output spread (notably the heated
stream's vapour fraction) is the decision-relevant quantity.

Run on a machine with DWSIM installed:  python validate_multimodel_nonideal_live.py
"""
from __future__ import annotations
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

SPEC = {
    "name": "mmu_meoh_water_flash",
    "compounds": ["Methanol", "Water"],
    "property_package": "NRTL",            # overridden per model
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
         "molarflow": 1.0, "molarflow_unit": "mol/s",
         "composition": {"Methanol": 0.5, "Water": 0.5}},
    ],
    "unit_op_specs": [
        {"tag": "H-101", "property_name": "outlet_temperature", "value": 75, "unit": "C"},
    ],
}

PACKAGES = ["NRTL", "UNIQUAC", "Wilson", "Modified UNIFAC (Dortmund)"]
OBSERVE = ["temperature_C", "pressure_bar", "vapor_fraction", "density_kg_m3"]


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

    summ = res["summary"]; obs = res["observations"]
    print(f"[live] models solved: {summ['models']}", flush=True)
    print(f"[live] {summ['interpretation']}", flush=True)

    L = ["# Non-Ideal Multi-Model Thermodynamic Uncertainty (live DWSIM)", "",
         "The SAME methanol/water flowsheet (50/50 mol feed, heated to 75 C at "
         "1 atm — a two-phase state) solved under four activity-coefficient "
         "packages. Unlike pure water, a strongly non-ideal mixture near its "
         "azeotrope makes the package choice materially change the answer. No LLM.",
         "",
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
          "**Why this matters:** for this non-ideal separation the vapour "
          "fraction (the engineering result) differs by the spread above purely "
          "from the thermodynamic-model choice. The platform surfaces that in one "
          "command, so a result that depends on the package is flagged rather "
          "than reported as a single unqualified number — directly quantifying "
          "the fidelity gap a commercial tool leaves implicit.",
          "", f"_model_status: {res.get('model_status')}_"]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "MULTIMODEL_NONIDEAL_VALIDATION.md"), "w",
              encoding="utf-8") as f:
        f.write(md)
    print("\n" + md.encode("ascii", "replace").decode("ascii"))
    return 0 if summ.get("n_observations") else 1


if __name__ == "__main__":
    raise SystemExit(main())
