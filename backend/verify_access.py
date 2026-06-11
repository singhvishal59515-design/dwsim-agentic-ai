"""
verify_access.py — Minimal verification of agent DWSIM access.

Run this script with a known flowsheet and COMPARE THE OUTPUT
against what DWSIM's own GUI shows on screen.

Each test prints: property name, agent-read value, expected range,
and a PASS/FAIL. You must verify PASS results match the GUI yourself.

Usage:
    python verify_access.py --flowsheet "C:/Users/hp/Documents/water_heating_process.dwxmz"

Output to check against DWSIM GUI:
    1. Product stream Temperature (K) — read from Product stream info panel
    2. Product stream Pressure (Pa) — read from Product stream info panel
    3. Product stream Mass Flow (kg/s) — read from Product stream info panel
    4. Feed stream Temperature (K) — read from Feed stream info panel
    5. H-101 DeltaQ (W) — read from H-101 Settings → Calculated Properties
    6. Energy stream Q EnergyFlow (W) — read from Q stream info panel
    7. ΔT (Product T - Feed T) — compute from GUI values yourself
    8. Q cross-check: ṁ × Cp × ΔT — compute manually

IMPORTANT: record the GUI value next to each agent value.
If agent ≠ GUI within tolerance → the access layer has a bug.
If agent = GUI within tolerance → that specific read is verified.

Do not generalise from passing tests to 'the system works'.
"""

import sys
import os
import json
import argparse

def main():
    parser = argparse.ArgumentParser(description="Verify agent DWSIM access")
    parser.add_argument("--flowsheet", required=True, help="Path to .dwxmz file")
    parser.add_argument("--output", default="verify_results.json")
    args = parser.parse_args()

    if not os.path.exists(args.flowsheet):
        print(f"ERROR: Flowsheet not found: {args.flowsheet}")
        sys.exit(1)

    # Load bridge
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    print(f"Loading: {args.flowsheet}")
    from dwsim_bridge_v2 import DWSIMBridgeV2
    bridge = DWSIMBridgeV2()
    r = bridge.load_flowsheet(args.flowsheet)
    if not r.get("success"):
        print(f"ERROR loading flowsheet: {r.get('error', '?')}")
        sys.exit(1)
    print(f"Loaded: {r.get('object_count')} objects\n")

    from dwsim_reflection import reflect_get_set, inspect_object

    results = []

    def check(label, agent_value, unit, lo, hi, gui_note):
        """Record one verification point."""
        try:
            v = float(agent_value) if agent_value is not None else None
        except (TypeError, ValueError):
            v = None
        in_range = (v is not None and lo <= v <= hi)
        row = {
            "label": label, "agent_value": agent_value, "unit": unit,
            "expected_range": [lo, hi], "in_expected_range": in_range,
            "gui_value_to_fill_in": None,
            "gui_note": gui_note,
        }
        results.append(row)
        flag = "OK" if in_range else "CHECK"
        print(f"  [{flag:5}] {label}")
        print(f"         Agent reads : {agent_value} {unit}")
        print(f"         Expected     : {lo} – {hi} {unit}")
        print(f"         Verify in GUI: {gui_note}")
        print()
        return v

    print("=" * 65)
    print("STEP 1 — Product stream (read Phases[0].Properties.*)")
    print("=" * 65)

    r = reflect_get_set(bridge, "Product",
                         "Phases[0].Properties.temperature")
    prod_T = check("Product Temperature",
                    r.get("value") if r.get("success") else None, "K",
                    300, 400,
                    "Product stream info panel → Temperature")

    r = reflect_get_set(bridge, "Product",
                         "Phases[0].Properties.pressure")
    prod_P = check("Product Pressure",
                    r.get("value") if r.get("success") else None, "Pa",
                    90000, 120000,
                    "Product stream info panel → Pressure")

    r = reflect_get_set(bridge, "Product",
                         "Phases[0].Properties.massflow")
    prod_mf = check("Product Mass Flow",
                     r.get("value") if r.get("success") else None, "kg/s",
                     0.1, 10.0,
                     "Product stream info panel → Mass Flow")

    print("=" * 65)
    print("STEP 2 — Feed stream")
    print("=" * 65)

    r = reflect_get_set(bridge, "Feed",
                         "Phases[0].Properties.temperature")
    feed_T = check("Feed Temperature",
                    r.get("value") if r.get("success") else None, "K",
                    250, 320,
                    "Feed stream info panel → Temperature")

    print("=" * 65)
    print("STEP 3 — Heater H-101 unit operation properties")
    print("  (inspect to find correct property name first)")
    print("=" * 65)

    r_inspect = inspect_object(bridge, "H-101",
                                 filter_prefix="", max_props=100)
    energy_props = [p for p in r_inspect.get("properties", [])
                     if any(k in p["name"].lower()
                             for k in ("q", "duty", "heat", "delta", "energy"))]
    print("  Energy-related properties on H-101:")
    for p in energy_props[:10]:
        print(f"    .{p['name']} = {p['value'][:60]}")
    print()

    # Read DeltaQ (W) and HeatDuty (W)
    for prop in ("DeltaQ", "HeatDuty", "Q", "Duty"):
        r = reflect_get_set(bridge, "H-101", prop)
        if r.get("success"):
            try:
                kw = float(r["value"]) / 1000
                check(f"H-101.{prop}",
                       r["value"], "W (÷1000 = kW)",
                       100000, 500000,
                       f"H-101 Settings → Calculated Properties → {prop} (in W)")
                print(f"  → {prop} in kW: {kw:.3f} kW")
            except Exception:
                pass

    print()
    print("=" * 65)
    print("STEP 4 — Energy stream Q")
    print("=" * 65)

    r_q = inspect_object(bridge, "Q", max_props=50)
    q_props = [p for p in r_q.get("properties", [])
                if any(k in p["name"].lower()
                        for k in ("energy", "flow", "power", "duty", "q"))]
    if q_props:
        print("  Energy-related on Q stream:")
        for p in q_props[:8]:
            print(f"    .{p['name']} = {p['value'][:60]}")
    else:
        print("  No energy properties found on Q stream via reflection.")
        print("  Try: reflect_get_set('Q', 'EnergyFlow')")

    r = reflect_get_set(bridge, "Q", "EnergyFlow")
    if r.get("success"):
        check("Q Energy Stream EnergyFlow",
               r.get("value"), "W",
               100000, 500000,
               "Q energy stream info panel → Energy Flow")

    print()
    print("=" * 65)
    print("STEP 5 — Cross-check: ṁ·Cp·ΔT vs agent DeltaQ")
    print("=" * 65)

    if prod_T and feed_T and prod_mf:
        dT = prod_T - feed_T
        Cp = 4186.0   # J/kg·K (liquid water ~40°C average)
        Q_manual = prod_mf * Cp * dT
        print(f"  Feed T    = {feed_T:.2f} K = {feed_T-273.15:.2f} °C")
        print(f"  Product T = {prod_T:.2f} K = {prod_T-273.15:.2f} °C")
        print(f"  ΔT        = {dT:.2f} K")
        print(f"  Mass flow = {prod_mf:.4f} kg/s")
        print(f"  Cp (water ~{(feed_T+prod_T)/2-273.15:.0f}°C) = {Cp} J/kg·K")
        print(f"  Q = ṁCpΔT = {Q_manual:.2f} W = {Q_manual/1000:.3f} kW")
        print()
        print("  COMPARE THIS against:")
        print("    H-101.DeltaQ from STEP 3 (agent-read)")
        print("    Q stream Energy Flow from STEP 4 (agent-read)")
        print("    Q = 313.87 kW shown in GUI energy stream label")
        print()
        print("  If H-101.DeltaQ ≈ ṁCpΔT → access is correct, GUI label is")
        print("    a different quantity (absolute enthalpy, not duty).")
        print("  If H-101.DeltaQ ≠ ṁCpΔT → access layer has a bug.")
        results.append({
            "label": "Cross-check Q = mCpΔT",
            "Q_manual_W": round(Q_manual, 2),
            "Q_manual_kW": round(Q_manual / 1000, 3),
            "feed_T_K": feed_T,
            "prod_T_K": prod_T,
            "dT_K": dT,
            "massflow_kgs": prod_mf,
        })

    # Save
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({
            "flowsheet": args.flowsheet,
            "object_count": r.get("object_count", "?"),
            "results": results,
            "instruction": (
                "For each row: fill in 'gui_value_to_fill_in' with "
                "what DWSIM GUI shows, then check agent_value == gui_value "
                "within tolerance. That is the only valid verification."
            ),
        }, f, indent=2, default=str)

    print()
    print(f"Raw results saved to: {args.output}")
    print()
    print("=" * 65)
    print("NEXT STEP — fill in the GUI column")
    print("=" * 65)
    print("Open verify_results.json and fill each 'gui_value_to_fill_in'")
    print("with what DWSIM GUI actually shows for that property.")
    print("Where agent = GUI ± tolerance: that read is verified.")
    print("Where agent ≠ GUI: that is a bug to fix before citing the result.")


if __name__ == "__main__":
    main()
