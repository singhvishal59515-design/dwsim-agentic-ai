"""
hydrogen_case_study.py
======================
Replicates hydrogen production results from:
  Ullah, Asaad & Inayat (2025)
  "Process modelling and optimization of hydrogen production from biogas
   by integrating DWSIM with response surface methodology"
  Digital Chemical Engineering 14, 100205

Usage:
  python hydrogen_case_study.py --mode quick    # ~20 min (quick sweep)
  python hydrogen_case_study.py --mode full     # ~60 min (all pts)
  python hydrogen_case_study.py --mode base     # base + optimal only
  python hydrogen_case_study.py --mock          # analytical mock (no DWSIM)
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND_DIR)

# ─── Paper reference values ──────────────────────────────────────────────────
PAPER_REFERENCE = {
    "source": "Ullah, K., Asaad, S.M., Inayat, A. (2025). Digital Chem Eng 14, 100205",
    "doi": "https://doi.org/10.1016/j.dche.2024.100205",
    "template": "biogas_smr_h2",
    "baseline": {
        "reformer_temp_C":  909.0,
        "pressure_bar":     16.0,
        "biogas_flow_kgh":  38.5,
        "steam_flow_kgh":   46.0,
    },
    "optimal": {
        "reformer_temp_C":  954.0,
        "pressure_bar":     12.5,
        "biogas_flow_kgh":  57.0,
        "steam_flow_kgh":   33.97,
        "h2_yield_mol_pct": 64.87,
    },
    "qualitative_trends": {
        "temperature": "H2 yield increases with reformer T (endothermic SMR)",
        "pressure":    "H2 yield decreases with pressure (moles increase)",
        "biogas_flow": "H2 yield increases with biogas feed (more CH4)",
        "steam_flow":  "H2 has optimum near S/C ratio 3; drops with excess steam",
    },
}

# ─── Sensitivity sweep ranges ────────────────────────────────────────────────
SENSITIVITY_QUICK = {
    "temperature": list(range(700, 1001, 50)),   # 7 pts
    "pressure":    list(range(8, 25, 2)),          # 9 pts
    "biogas_flow": list(range(20, 81, 10)),        # 7 pts
    "steam_flow":  list(range(20, 71, 10)),        # 6 pts
}
SENSITIVITY_FULL = {
    "temperature": list(range(700, 1001, 10)),    # 31 pts
    "pressure":    list(range(8, 25, 1)),          # 17 pts
    "biogas_flow": [f * 5 for f in range(4, 17)], # 13 pts
    "steam_flow":  [f * 5 for f in range(4, 15)], # 11 pts
}


# ─── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class SimPoint:
    reformer_temp_C:    float
    pressure_bar:       float
    biogas_flow_kgh:    float
    steam_flow_kgh:     float
    h2_mole_fraction:   Optional[float]
    h2_yield_mol_pct:   Optional[float]
    converged:          bool
    duration_s:         float
    error:              Optional[str] = None
    mole_fractions:     Optional[Dict[str, float]] = None
    mock:               bool = False


# ─── Main class ──────────────────────────────────────────────────────────────
class HydrogenCaseStudy:
    """
    Replicates Ullah et al. (2025) via the biogas_smr_h2 template.
    Uses DWSIMBridgeV2 directly — no LLM loop.
    """

    def __init__(self, bridge=None, results_path: str = ""):
        self._bridge = bridge
        self._results_path = results_path or os.path.join(
            _BACKEND_DIR, "hydrogen_results.jsonl")
        self._template_path: Optional[str] = None
        self._mock_mode = False

    # ── Bridge init ──────────────────────────────────────────────────────────
    def _get_bridge(self):
        if self._bridge is not None:
            return self._bridge
        try:
            from dwsim_bridge_v2 import DWSIMBridgeV2
            b = DWSIMBridgeV2()
            r = b.initialize()
            if not r["success"]:
                raise RuntimeError(r.get("error", "init failed"))
            self._bridge = b
            return b
        except Exception as exc:
            print(f"[H2Study] DWSIM unavailable: {exc} → MOCK mode")
            self._mock_mode = True
            return None

    # ── Template build ───────────────────────────────────────────────────────
    def build(self, variant: str = "biogas_smr_h2") -> bool:
        """Build the SMR flowsheet from template and save to disk."""
        if self._mock_mode:
            return True
        try:
            from flowsheet_templates import get_template
            from flowsheet_builder import build_flowsheet
            topology = get_template(variant)
            if topology is None:
                raise ValueError(f"Template '{variant}' not found")
            docs = os.path.expanduser("~/Documents")
            save_path = os.path.join(docs, "h2_case_study.dwxmz")
            bridge = self._get_bridge()
            if bridge is None:          # DWSIM unavailable → already in mock mode
                return True
            result = build_flowsheet(bridge._mgr, topology, save_path)
            if not result["success"]:
                print(f"[H2Study] Build failed: {result.get('warnings', [])[:3]}")
                return False
            self._template_path = save_path
            print(f"[H2Study] Template built → {save_path}")
            for w in (result.get("warnings") or [])[:3]:
                print(f"  [WARN] {w}")
            return True
        except Exception as exc:
            print(f"[H2Study] Build exception: {exc}")
            return False

    # ── Condition setter ─────────────────────────────────────────────────────
    def _configure(self, reformer_temp_C: float, pressure_bar: float,
                   biogas_flow_kgh: float, steam_flow_kgh: float) -> bool:
        """Reload template and set operating conditions."""
        bridge = self._get_bridge()
        if bridge is None or self._template_path is None:
            return self._mock_mode

        r = bridge.load_flowsheet(self._template_path)
        if not r["success"]:
            print(f"[H2Study] Reload failed: {r.get('error')}")
            return False

        # Reformer temperature: both feed heaters (H-101 for biogas, B-101 for steam)
        bridge.set_unit_op_property("H-101",    "OutletTemperature",
                                    reformer_temp_C + 273.15)
        bridge.set_unit_op_property("B-101",    "OutletTemperature",
                                    reformer_temp_C + 273.15)
        # System pressure: compressor + feed pump
        bridge.set_unit_op_property("COMP-101", "OutletPressure", pressure_bar * 1e5)
        bridge.set_unit_op_property("P-101",    "OutletPressure", pressure_bar * 1e5)
        # Feed flows
        bridge.set_stream_property("BIOGAS-IN", "MassFlow", biogas_flow_kgh, "kg/h")
        bridge.set_stream_property("WATER-IN",  "MassFlow", steam_flow_kgh,  "kg/h")
        return True

    # ── Solve and read ───────────────────────────────────────────────────────
    def _solve_and_read(self) -> Tuple[Optional[float], Optional[Dict], bool]:
        """Run simulation; return (h2_mole_fraction, mole_fracs, converged)."""
        bridge = self._get_bridge()
        r = bridge.run_simulation()
        if not r.get("success"):
            return None, None, False
        sp = bridge.get_stream_properties("HYDROGEN")
        if not sp.get("success"):
            return None, None, True
        mf = sp["properties"].get("mole_fractions", {})
        h2 = mf.get("Hydrogen") or mf.get("hydrogen") or mf.get("H2")
        return h2, mf, True

    # ── Mock H2 yield ────────────────────────────────────────────────────────
    def _mock_h2(self, T: float, P: float, F: float, S: float) -> float:
        """Analytical approximation matching paper trends for mock mode."""
        T0, P0, F0, S0 = 909, 16, 38.5, 46
        base = 0.6487
        Tf = 1 + 0.0008 * (T - T0)
        Pf = 1 - 0.004 * (P - P0)
        Ff = 1 + 0.04 * (F - F0) / F0
        sc = S / max(F * 0.6, 1)
        Sf = 1 - 0.015 * (sc - 3.0) ** 2
        return max(0.05, min(0.95, base * Tf * Pf * Ff * Sf))

    # ── Single point runner ──────────────────────────────────────────────────
    def _run_point(self, T: float, P: float, F: float, S: float,
                   label: str = "") -> SimPoint:
        t0 = time.time()
        print(f"[H2Study] {label or 'point'}: T={T}°C P={P}bar biogas={F}kg/h steam={S}kg/h")

        if self._mock_mode:
            h2 = self._mock_h2(T, P, F, S)
            pt = SimPoint(T, P, F, S, h2, round(h2 * 100, 3), True,
                          round(time.time() - t0, 2),
                          mole_fractions={"Hydrogen": h2}, mock=True)
        else:
            try:
                ok = self._configure(T, P, F, S)
                if not ok:
                    raise RuntimeError("Configuration failed")
                h2, mf, conv = self._solve_and_read()
                pt = SimPoint(T, P, F, S, h2,
                              round(h2 * 100, 3) if h2 is not None else None,
                              conv, round(time.time() - t0, 2),
                              mole_fractions=mf)
            except Exception as exc:
                pt = SimPoint(T, P, F, S, None, None, False,
                              round(time.time() - t0, 2), error=str(exc))

        pct = f"{pt.h2_yield_mol_pct:.2f}%" if pt.h2_yield_mol_pct else "N/A"
        print(f"  -> H2={pct}  converged={pt.converged}  ({pt.duration_s:.1f}s)")
        self._log(pt)
        return pt

    def _log(self, pt: SimPoint) -> None:
        try:
            with open(self._results_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({**asdict(pt),
                                    "ts": datetime.utcnow().isoformat()}) + "\n")
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────────────────
    def run_base_case(self) -> SimPoint:
        bc = PAPER_REFERENCE["baseline"]
        return self._run_point(bc["reformer_temp_C"], bc["pressure_bar"],
                               bc["biogas_flow_kgh"], bc["steam_flow_kgh"],
                               label="base case")

    def run_optimal_case(self) -> SimPoint:
        opt = PAPER_REFERENCE["optimal"]
        return self._run_point(opt["reformer_temp_C"], opt["pressure_bar"],
                               opt["biogas_flow_kgh"], opt["steam_flow_kgh"],
                               label="optimal case")

    def run_sensitivity(self, parameter: str,
                        values: List[float]) -> List[SimPoint]:
        """Vary one parameter, hold others at paper baseline."""
        bc = PAPER_REFERENCE["baseline"]
        T0, P0, F0, S0 = (bc["reformer_temp_C"], bc["pressure_bar"],
                           bc["biogas_flow_kgh"], bc["steam_flow_kgh"])
        results = []
        for v in values:
            if parameter == "temperature":
                pt = self._run_point(v, P0, F0, S0, f"T-sensitivity T={v}°C")
            elif parameter == "pressure":
                pt = self._run_point(T0, v, F0, S0, f"P-sensitivity P={v}bar")
            elif parameter == "biogas_flow":
                pt = self._run_point(T0, P0, v, S0, f"F-sensitivity F={v}kg/h")
            elif parameter == "steam_flow":
                pt = self._run_point(T0, P0, F0, v, f"S-sensitivity S={v}kg/h")
            else:
                raise ValueError(f"Unknown parameter '{parameter}'. "
                                 "Use: temperature|pressure|biogas_flow|steam_flow")
            results.append(pt)
        return results

    # ── Report generation ─────────────────────────────────────────────────────
    def generate_report(self,
                        base_case: Optional[SimPoint],
                        optimal_case: Optional[SimPoint],
                        sensitivity: Dict[str, List[SimPoint]],
                        mode: str = "quick") -> Dict[str, Any]:
        paper_opt = PAPER_REFERENCE["optimal"]

        # Quantitative comparison at optimal
        cmp: Dict[str, Any] = {}
        if optimal_case and optimal_case.h2_yield_mol_pct is not None:
            sim_h2    = optimal_case.h2_yield_mol_pct
            paper_h2  = paper_opt["h2_yield_mol_pct"]
            abs_err   = abs(sim_h2 - paper_h2)
            rel_err   = abs_err / paper_h2 * 100
            cmp = {
                "paper_h2_pct":    paper_h2,
                "sim_h2_pct":      round(sim_h2, 3),
                "absolute_error":  round(abs_err, 3),
                "relative_error_pct": round(rel_err, 2),
                "within_5pct":     rel_err < 5.0,
                "within_10pct":    rel_err < 10.0,
                "status": "PASS" if rel_err < 10.0 else "OUTSIDE_10PCT",
            }
        else:
            cmp = {"status": "NO_DATA"}

        # Qualitative trends
        def monotone_ok(pts: List[SimPoint], increasing: bool) -> bool:
            vals = [p.h2_yield_mol_pct for p in pts if p.h2_yield_mol_pct]
            if len(vals) < 2:
                return False
            viols = sum(1 for a, b in zip(vals, vals[1:])
                        if (increasing and b < a) or (not increasing and b > a))
            return viols <= max(2, len(vals) // 5)

        trends: Dict[str, bool] = {}
        if "temperature" in sensitivity:
            trends["T_increase_yields_more_H2"] = monotone_ok(
                sensitivity["temperature"], True)
        if "pressure" in sensitivity:
            trends["P_increase_yields_less_H2"] = monotone_ok(
                sensitivity["pressure"], False)
        if "biogas_flow" in sensitivity:
            trends["biogas_increase_yields_more_H2"] = monotone_ok(
                sensitivity["biogas_flow"], True)
        if "steam_flow" in sensitivity:
            vals = [p.h2_yield_mol_pct for p in sensitivity["steam_flow"]
                    if p.h2_yield_mol_pct]
            if vals:
                mi = vals.index(max(vals))
                trends["steam_has_optimum"] = 0 < mi < len(vals) - 1
            else:
                trends["steam_has_optimum"] = False

        n_pass = sum(1 for v in trends.values() if v)
        mock_tag = " [MOCK]" if self._mock_mode else ""
        summary = (
            f"=== H2 Case Study Report ({mode}{mock_tag}) ===\n"
            f"Sim H2: {cmp.get('sim_h2_pct','N/A')}%  "
            f"Paper: {paper_opt['h2_yield_mol_pct']}%  "
            f"Status: {cmp.get('status')}\n"
            f"Trends: {n_pass}/{len(trends)} verified"
        )
        print("\n" + summary)

        return {
            "timestamp":        datetime.utcnow().isoformat(),
            "template":         "biogas_smr_h2",
            "mode":             mode,
            "mock_mode":        self._mock_mode,
            "comparison":       cmp,
            "trends_verified":  trends,
            "summary":          summary,
            "paper_reference":  PAPER_REFERENCE,
            "base_case":        asdict(base_case)  if base_case  else None,
            "optimal_case":     asdict(optimal_case) if optimal_case else None,
            "sensitivity":      {k: [asdict(p) for p in v]
                                 for k, v in sensitivity.items()},
        }

    # ── Full orchestrator ─────────────────────────────────────────────────────
    def run_full(self, mode: str = "quick") -> Dict[str, Any]:
        print("\n" + "=" * 65)
        print("HYDROGEN CASE STUDY — Ullah et al. (2025) Replication")
        print("=" * 65)

        sweeps = SENSITIVITY_QUICK if mode == "quick" else SENSITIVITY_FULL

        if not self._mock_mode:
            print("\n[1/6] Building SMR flowsheet from template…")
            ok = self.build()
            if not ok:
                print("  → Build failed. Switching to MOCK mode.")
                self._mock_mode = True

        print("\n[2/6] Base case…")
        base = self.run_base_case()

        print("\n[3/6] Optimal case…")
        opt = self.run_optimal_case()

        sensitivity: Dict[str, List[SimPoint]] = {}
        if mode in ("quick", "full"):
            step_labels = ["4", "4", "5", "5"]
            for step, (param, vals) in zip(step_labels, sweeps.items()):
                print(f"\n[{step}/6] {param} sensitivity ({len(vals)} pts)...")
                sensitivity[param] = self.run_sensitivity(param, vals)

        report = self.generate_report(base, opt, sensitivity, mode)

        # Persist report — use json.dumps with ensure_ascii=True so the
        # written bytes are pure ASCII, safe on any Windows code page.
        rpath = os.path.join(_BACKEND_DIR, "hydrogen_report.json")
        try:
            report_str = json.dumps(report, indent=2, ensure_ascii=True)
            with open(rpath, "w", encoding="utf-8") as f:
                f.write(report_str)
            print(f"\nReport -> {rpath}")
        except Exception as exc:
            print(f"  [WARN] Could not save report: {exc}")

        return report


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="Replicate H2 production results from Ullah et al. (2025)")
    p.add_argument("--mode",    choices=["base", "quick", "full"], default="quick")
    p.add_argument("--mock",    action="store_true",
                   help="Analytical mock — no DWSIM required")
    p.add_argument("--results", default="",
                   help="Path for JSONL results log")
    args = p.parse_args()

    study = HydrogenCaseStudy(results_path=args.results)
    if args.mock:
        study._mock_mode = True
    study.run_full(mode=args.mode)


if __name__ == "__main__":
    main()
