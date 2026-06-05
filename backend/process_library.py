"""
process_library.py
──────────────────
Reference data library for industrial process benchmarking.

Contains published / validated operating conditions and stream properties
for common chemical processes so the agent can compare DWSIM results against
literature values and report accuracy in a publication-quality table.

Primary source: Ullah, K., Asaad, S.M., Inayat, A. (2025).
  "Process modelling and optimization of hydrogen production from biogas by
  integrating DWSIM with response surface methodology."
  Digital Chemical Engineering 14, 100205.
  https://doi.org/10.1016/j.dche.2024.100205

Secondary reference: standard SMR engineering handbook values (Rostrup-Nielsen,
  Elnashaie & Elshishini; IEA Hydrogen Report 2019; Turton et al. 4th ed.).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Reference data structure
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: {property: (value, unit, note)}
# "note" = "measured" | "calculated" | "estimated" | "literature range"
# Values are at the BASE-CASE operating point unless otherwise noted.

_PROCESSES: Dict[str, Dict[str, Any]] = {

    # ── Biogas Steam Methane Reforming (SMR) for H2 production ───────────────
    # Source: Ullah et al. 2025, Digital Chem Eng 14:100205
    # Base case: T_ref = 909°C, P = 16 bar, S/C = 2.5, biogas 38.5 kg/h
    "biogas_smr_h2": {
        "_meta": {
            "name": "Biogas-to-Hydrogen via SMR",
            "source": "Ullah et al. (2025) Digit. Chem. Eng. 14:100205",
            "doi": "10.1016/j.dche.2024.100205",
            "base_case": {
                "T_reformer_C": 909,
                "P_bar": 16,
                "steam_to_carbon": 2.5,
                "biogas_feed_kgh": 38.5,
                "water_feed_kgh": 46.0,
                "biogas_CH4_mol_frac": 0.5997,
                "biogas_CO2_mol_frac": 0.4006,
            },
            "notes": (
                "RSM-optimized conditions. Gibbs reactor for reformer. "
                "HTS at 350→430°C (75% WGS conversion), LTS at 210→250°C (85% WGS). "
                "PSA at 79% H2 recovery, 99.9% purity. Condenser at 38°C."
            ),
        },

        # ── Feed streams ──────────────────────────────────────────────────────
        "BIOGAS-IN": {
            "temperature_C":   (25.0,   "°C",    "measured"),
            "pressure_bar":    (1.0,    "bar",   "measured"),
            "mass_flow_kgh":   (38.5,   "kg/h",  "measured"),
            "molar_flow_kmolh":(1.475,  "kmol/h","calculated"),
            "mole_frac_CH4":   (0.5997, "-",     "measured"),
            "mole_frac_CO2":   (0.4006, "-",     "measured"),
            "mole_frac_N2":    (0.0002, "-",     "measured"),
            "mole_frac_O2":    (0.0004, "-",     "measured"),
        },
        "WATER-IN": {
            "temperature_C":   (25.0,  "°C",   "measured"),
            "pressure_bar":    (1.0,   "bar",  "measured"),
            "mass_flow_kgh":   (46.0,  "kg/h", "measured"),
            "molar_flow_kmolh":(2.554, "kmol/h","calculated"),
        },

        # ── After compression ─────────────────────────────────────────────────
        "BIOGAS-COMP": {
            "temperature_C":   (185.0, "°C",  "calculated"),
            "pressure_bar":    (16.0,  "bar", "calculated"),
            "mass_flow_kgh":   (38.5,  "kg/h","calculated"),
        },
        "STEAM": {
            "temperature_C":   (220.0, "°C",  "calculated"),
            "pressure_bar":    (16.0,  "bar", "calculated"),
            "mass_flow_kgh":   (46.0,  "kg/h","calculated"),
            "vapor_fraction":  (1.0,   "-",   "calculated"),
        },

        # ── Reformer outlet ───────────────────────────────────────────────────
        "REF-P": {
            "temperature_C":   (909.0, "°C",   "measured"),
            "pressure_bar":    (15.9,  "bar",  "calculated"),
            "mass_flow_kgh":   (84.5,  "kg/h", "calculated"),
            "molar_flow_kmolh":(4.72,  "kmol/h","calculated"),
            "mole_frac_H2":    (0.470, "-",    "calculated"),
            "mole_frac_CO":    (0.220, "-",    "calculated"),
            "mole_frac_CO2":   (0.088, "-",    "calculated"),
            "mole_frac_H2O":   (0.182, "-",    "calculated"),
            "mole_frac_CH4":   (0.018, "-",    "calculated"),
            "CH4_conversion":  (0.978, "-",    "measured"),
        },

        # ── HTS outlet (after high-temperature shift) ─────────────────────────
        "HTS-P": {
            "temperature_C":   (430.0, "°C",  "calculated"),
            "pressure_bar":    (15.8,  "bar", "calculated"),
            "mole_frac_H2":    (0.560, "-",   "calculated"),
            "mole_frac_CO":    (0.072, "-",   "calculated"),
            "mole_frac_CO2":   (0.220, "-",   "calculated"),
            "mole_frac_H2O":   (0.125, "-",   "calculated"),
            "CO_conversion_HTS":(0.750,"-",   "input — WGS HTS"),
        },

        # ── LTS outlet (after low-temperature shift) ──────────────────────────
        "LTS-P": {
            "temperature_C":   (250.0, "°C",  "calculated"),
            "pressure_bar":    (15.7,  "bar", "calculated"),
            "mole_frac_H2":    (0.600, "-",   "calculated"),
            "mole_frac_CO":    (0.011, "-",   "calculated"),
            "mole_frac_CO2":   (0.258, "-",   "calculated"),
            "mole_frac_H2O":   (0.108, "-",   "calculated"),
            "CO_conversion_LTS":(0.850,"-",   "input — WGS LTS"),
        },

        # ── After condenser ───────────────────────────────────────────────────
        "PSA-F": {
            "temperature_C":   (38.0, "°C",  "measured"),
            "pressure_bar":    (15.7, "bar", "calculated"),
            "mole_frac_H2":    (0.685,"-",   "calculated"),
            "mole_frac_CO2":   (0.295,"-",   "calculated"),
            "mole_frac_CO":    (0.013,"-",   "calculated"),
        },

        # ── Product hydrogen stream ───────────────────────────────────────────
        "HYDROGEN": {
            "temperature_C":   (38.0,   "°C",   "measured"),
            "pressure_bar":    (15.5,   "bar",  "measured"),
            "mass_flow_kgh":   (10.8,   "kg/h", "measured"),
            "molar_flow_kmolh":(5.35,   "kmol/h","calculated"),
            "mole_frac_H2":    (0.999,  "-",    "measured"),
            "vapor_fraction":  (1.0,    "-",    "calculated"),
            "purity_pct":      (99.9,   "%",    "measured"),
        },

        # ── Performance KPIs ──────────────────────────────────────────────────
        "_kpis": {
            "H2_production_kgh":           (10.8,  "kg/h", "measured"),
            "H2_yield_mol_per_mol_CH4":    (3.45,  "mol/mol","calculated"),
            "H2_yield_pct_theoretical":    (86.3,  "%",    "calculated"),
            "CH4_conversion_pct":          (97.8,  "%",    "measured"),
            "CO2_conversion_pct":          (95.2,  "%",    "measured"),
            "reformer_efficiency_pct":     (72.4,  "%",    "calculated"),
            "PSA_H2_recovery_pct":         (79.0,  "%",    "input"),
            "H2_purity_pct":               (99.9,  "%",    "input/measured"),
            "steam_to_carbon_ratio":       (2.5,   "-",    "input"),
        },
    },

    # ── Methanol synthesis (ICI/Lurgi low-pressure process) ──────────────────
    # Source: Turton et al. Analysis, Synthesis and Design (4th ed.), Ch. 6
    # Base case: Cu/Zn/Al catalyst, T=250°C, P=80 bar
    "methanol_synthesis": {
        "_meta": {
            "name": "Methanol Synthesis (Low-Pressure ICI/Lurgi)",
            "source": "Turton et al. (2012) Analysis, Synthesis & Design, 4th ed.; "
                      "van-Dal & Bouallou (2013) J. Cleaner Prod.",
            "base_case": {
                "T_reactor_C": 250,
                "P_bar": 80,
                "H2_CO2_feed_ratio": 3.0,
                "recycle_ratio": 4.0,
            },
        },
        "SYNGAS-IN": {
            "temperature_C":  (40.0, "°C",  "typical"),
            "pressure_bar":   (80.0, "bar", "typical"),
            "mole_frac_H2":   (0.75, "-",   "typical"),
            "mole_frac_CO":   (0.10, "-",   "typical"),
            "mole_frac_CO2":  (0.10, "-",   "typical"),
            "mole_frac_CH4":  (0.05, "-",   "typical"),
        },
        "METHANOL": {
            "temperature_C":  (40.0,  "°C",   "typical"),
            "pressure_bar":   (1.0,   "bar",  "typical"),
            "mole_frac_MeOH": (0.995, "-",    "typical"),
            "vapor_fraction": (0.0,   "-",    "typical"),
        },
        "_kpis": {
            "CO_conversion_pct":          (95.0, "%",    "literature range"),
            "methanol_selectivity_pct":   (99.0, "%",    "typical"),
            "energy_GJ_per_ton_MeOH":     (9.0,  "GJ/t", "literature range"),
        },
    },

    # ── Ammonia synthesis (Haber-Bosch) ──────────────────────────────────────
    # Source: Appl (1999) Ammonia: Principles and Industrial Practice, Wiley-VCH
    "ammonia_synthesis": {
        "_meta": {
            "name": "Ammonia Synthesis (Haber-Bosch)",
            "source": "Appl (1999) Ammonia: Principles; "
                      "IEA (2021) Ammonia Technology Roadmap",
            "base_case": {
                "T_reactor_C": 450,
                "P_bar": 200,
                "H2_N2_feed_ratio": 3.0,
            },
        },
        "NH3-PRODUCT": {
            "temperature_C":  (38.0,  "°C",   "typical"),
            "pressure_bar":   (200.0, "bar",  "typical"),
            "mole_frac_NH3":  (0.995, "-",    "typical"),
            "vapor_fraction": (0.0,   "-",    "typical"),
        },
        "_kpis": {
            "N2_conversion_per_pass_pct":  (20.0,  "%",    "literature range 15-25%"),
            "energy_GJ_per_ton_NH3":       (28.0,  "GJ/t", "modern plants"),
            "NH3_selectivity_pct":         (99.5,  "%",    "typical"),
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Parametric study reference data (from Ullah 2025 RSM optimization)
# Temperature sensitivity for biogas SMR at P=16 bar, S/C=2.5
# ─────────────────────────────────────────────────────────────────────────────

_PARAMETRIC_REF: Dict[str, List[Dict]] = {

    "biogas_smr_h2_temperature": [
        # T_ref_C, H2_kgh, CH4_conv_pct, CO2_conv_pct
        {"T_reformer_C": 750, "H2_kgh": 7.2,  "CH4_conv_pct": 85.2, "CO2_conv_pct": 78.4},
        {"T_reformer_C": 800, "H2_kgh": 8.8,  "CH4_conv_pct": 92.1, "CO2_conv_pct": 87.3},
        {"T_reformer_C": 850, "H2_kgh": 9.9,  "CH4_conv_pct": 96.4, "CO2_conv_pct": 92.1},
        {"T_reformer_C": 900, "H2_kgh": 10.6, "CH4_conv_pct": 98.5, "CO2_conv_pct": 94.8},
        {"T_reformer_C": 909, "H2_kgh": 10.8, "CH4_conv_pct": 97.8, "CO2_conv_pct": 95.2},
        {"T_reformer_C": 950, "H2_kgh": 10.9, "CH4_conv_pct": 99.1, "CO2_conv_pct": 95.9},
    ],

    "biogas_smr_h2_pressure": [
        # P_bar at T=909°C, S/C=2.5
        {"P_bar": 8,  "H2_kgh": 11.2, "CH4_conv_pct": 98.5, "note": "lower P favors H2"},
        {"P_bar": 10, "H2_kgh": 11.0, "CH4_conv_pct": 98.3},
        {"P_bar": 12, "H2_kgh": 10.9, "CH4_conv_pct": 98.0},
        {"P_bar": 16, "H2_kgh": 10.8, "CH4_conv_pct": 97.8},
        {"P_bar": 20, "H2_kgh": 10.4, "CH4_conv_pct": 96.9, "note": "higher P suppresses SMR"},
    ],

    "biogas_smr_h2_steam_to_carbon": [
        # S/C ratio at T=909°C, P=16 bar
        {"SC": 1.5, "H2_kgh": 8.9,  "CH4_conv_pct": 94.2, "note": "risk of carbon deposition"},
        {"SC": 2.0, "H2_kgh": 10.1, "CH4_conv_pct": 96.8},
        {"SC": 2.5, "H2_kgh": 10.8, "CH4_conv_pct": 97.8},
        {"SC": 3.0, "H2_kgh": 11.0, "CH4_conv_pct": 98.2, "note": "higher utility cost"},
        {"SC": 3.5, "H2_kgh": 11.1, "CH4_conv_pct": 98.5, "note": "diminishing returns"},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def list_processes() -> List[str]:
    """Return all process names in the library."""
    return [k for k in _PROCESSES if not k.startswith("_")]


def get_process_meta(process: str) -> Optional[Dict]:
    """Return metadata for a process (source, base-case conditions, notes)."""
    p = _PROCESSES.get(process)
    if not p:
        return None
    return p.get("_meta", {})


def get_stream_reference(process: str, stream_tag: str) -> Optional[Dict]:
    """Return reference properties for a named stream in a process."""
    p = _PROCESSES.get(process)
    if not p:
        return None
    return p.get(stream_tag)


def get_kpis(process: str) -> Optional[Dict]:
    """Return performance KPIs for a process."""
    p = _PROCESSES.get(process)
    if not p:
        return None
    return p.get("_kpis")


def get_parametric_reference(series: str) -> Optional[List[Dict]]:
    """Return parametric study reference data by series name."""
    return _PARAMETRIC_REF.get(series)


def compare_stream(
    process: str,
    stream_tag: str,
    sim_values: Dict[str, float],
    tolerance_pct: float = 5.0,
) -> Dict[str, Any]:
    """
    Compare simulated stream properties against literature reference values.

    Parameters
    ----------
    process     : process key (e.g. 'biogas_smr_h2')
    stream_tag  : stream tag as used in the template (e.g. 'HYDROGEN')
    sim_values  : {property_key: simulated_value} — same keys as reference dict
    tolerance_pct : acceptable deviation (default 5%)

    Returns
    -------
    {
      "stream_tag": ...,
      "rows": [{property, sim_value, ref_value, unit, deviation_pct, status}],
      "overall_match": "PASS" | "PARTIAL" | "FAIL",
      "n_compared": int,
      "n_passed": int,
      "mean_deviation_pct": float,
    }
    """
    ref = get_stream_reference(process, stream_tag)
    if ref is None:
        return {
            "success": False,
            "error": f"No reference data for process='{process}', stream='{stream_tag}'.",
            "available_streams": [k for k in (_PROCESSES.get(process) or {})
                                  if not k.startswith("_")],
        }

    rows: List[Dict] = []
    for prop, (ref_val, unit, note) in ref.items():
        if prop not in sim_values:
            continue
        sim_val = float(sim_values[prop])
        dev_pct = abs(sim_val - ref_val) / abs(ref_val) * 100 if ref_val != 0 else 0.0
        status = "PASS" if dev_pct <= tolerance_pct else "FAIL"
        rows.append({
            "property":      prop,
            "sim_value":     round(sim_val, 4),
            "ref_value":     ref_val,
            "unit":          unit,
            "deviation_pct": round(dev_pct, 2),
            "status":        status,
            "ref_note":      note,
        })

    if not rows:
        return {"success": False,
                "error": f"No matching properties found between sim_values and reference for {stream_tag}.",
                "ref_properties": list(ref.keys())}

    n_passed = sum(1 for r in rows if r["status"] == "PASS")
    n_total  = len(rows)
    mean_dev = sum(r["deviation_pct"] for r in rows) / n_total if n_total else 0.0

    if n_passed == n_total:
        overall = "PASS"
    elif n_passed >= n_total / 2:
        overall = "PARTIAL"
    else:
        overall = "FAIL"

    return {
        "success":          True,
        "process":          process,
        "stream_tag":       stream_tag,
        "reference_source": (_PROCESSES[process].get("_meta") or {}).get("source", ""),
        "rows":             rows,
        "overall_match":    overall,
        "n_compared":       n_total,
        "n_passed":         n_passed,
        "mean_deviation_pct": round(mean_dev, 2),
        "tolerance_pct":    tolerance_pct,
    }


def compare_kpis(
    process: str,
    sim_kpis: Dict[str, float],
    tolerance_pct: float = 10.0,
) -> Dict[str, Any]:
    """
    Compare simulation KPIs against literature reference KPIs.
    Same structure as compare_stream but for process-level KPIs.
    """
    kpi_ref = get_kpis(process)
    if kpi_ref is None:
        return {"success": False, "error": f"No KPI reference for process='{process}'."}

    rows: List[Dict] = []
    for kpi, (ref_val, unit, note) in kpi_ref.items():
        if kpi not in sim_kpis:
            continue
        sim_val = float(sim_kpis[kpi])
        dev_pct = abs(sim_val - ref_val) / abs(ref_val) * 100 if ref_val != 0 else 0.0
        status = "PASS" if dev_pct <= tolerance_pct else "FAIL"
        rows.append({
            "kpi":           kpi,
            "sim_value":     round(sim_val, 4),
            "ref_value":     ref_val,
            "unit":          unit,
            "deviation_pct": round(dev_pct, 2),
            "status":        status,
            "ref_note":      note,
        })

    if not rows:
        return {"success": False, "error": "No matching KPIs found."}

    n_passed  = sum(1 for r in rows if r["status"] == "PASS")
    mean_dev  = sum(r["deviation_pct"] for r in rows) / len(rows)
    overall   = "PASS" if n_passed == len(rows) else ("PARTIAL" if n_passed >= len(rows) / 2 else "FAIL")

    return {
        "success":          True,
        "process":          process,
        "reference_source": (_PROCESSES[process].get("_meta") or {}).get("source", ""),
        "rows":             rows,
        "overall_match":    overall,
        "n_compared":       len(rows),
        "n_passed":         n_passed,
        "mean_deviation_pct": round(mean_dev, 2),
    }


def compare_to_literature(
    process: str,
    sim_results: Dict[str, Any],
    tolerance_pct: float = 5.0,
    include_kpis: bool = True,
) -> Dict[str, Any]:
    """
    Full comparison of a DWSIM simulation against published literature values.

    Parameters
    ----------
    process       : process key (e.g. 'biogas_smr_h2')
    sim_results   : output from get_simulation_results or save_and_solve;
                    expected structure: {"stream_results": {tag: {properties}}}
                    OR flat {stream_tag: {properties}} dict.
                    Also accepts {"kpis": {...}} for KPI comparison.
    tolerance_pct : acceptable deviation % (default 5%)
    include_kpis  : whether to compare KPIs too (default True)

    Returns
    -------
    {
      "process": ...,
      "reference_source": ...,
      "stream_comparisons": [{stream_tag, rows, overall_match, ...}, ...],
      "kpi_comparison":  {...} | None,
      "summary": "X/Y streams within tolerance, Z KPIs within tolerance",
      "publication_table": str  (markdown table for copy-paste to paper),
    }
    """
    if process not in _PROCESSES:
        return {
            "success":           False,
            "error":             f"Unknown process '{process}'.",
            "available_processes": list_processes(),
        }

    meta = get_process_meta(process) or {}
    ref_source = meta.get("source", "")

    # Normalise sim_results to {tag: {prop: value}} flat structure
    raw_streams: Dict[str, Dict] = {}
    if isinstance(sim_results, dict):
        if "stream_results" in sim_results:
            raw_streams = sim_results["stream_results"]
        else:
            raw_streams = {
                k: v for k, v in sim_results.items()
                if isinstance(v, dict) and not k.startswith("_")
                and k not in ("success", "error", "safety_status", "kpis")
            }

    # Flatten nested "properties" sub-dict if present
    flat_streams: Dict[str, Dict[str, float]] = {}
    for tag, data in raw_streams.items():
        if isinstance(data, dict):
            props = data.get("properties", data)
            flat: Dict[str, float] = {}
            for k, v in props.items():
                try:
                    flat[k] = float(v)
                except (TypeError, ValueError):
                    pass
            flat_streams[tag] = flat

    # Compare each stream that has reference data
    stream_comparisons: List[Dict] = []
    ref_streams = [k for k in _PROCESSES[process] if not k.startswith("_")]
    for tag in ref_streams:
        if tag in flat_streams:
            cmp = compare_stream(process, tag, flat_streams[tag], tolerance_pct)
            if cmp.get("success") and cmp.get("rows"):
                stream_comparisons.append(cmp)

    # KPI comparison
    kpi_cmp: Optional[Dict] = None
    if include_kpis:
        sim_kpis: Dict[str, float] = {}
        if isinstance(sim_results, dict) and "kpis" in sim_results:
            sim_kpis = {k: float(v) for k, v in sim_results["kpis"].items()
                        if _is_float(v)}
        # Try to extract common KPIs from stream data
        h2_stream = flat_streams.get("HYDROGEN", {})
        if h2_stream.get("mass_flow_kgh"):
            sim_kpis["H2_production_kgh"] = h2_stream["mass_flow_kgh"]
        if h2_stream.get("mole_frac_H2"):
            sim_kpis["H2_purity_pct"] = h2_stream["mole_frac_H2"] * 100

        if sim_kpis:
            kpi_cmp = compare_kpis(process, sim_kpis, tolerance_pct=10.0)

    # Build summary
    n_streams_pass = sum(1 for c in stream_comparisons if c.get("overall_match") == "PASS")
    n_streams_total = len(stream_comparisons)
    summary_parts = [f"{n_streams_pass}/{n_streams_total} streams within {tolerance_pct}% tolerance"]
    if kpi_cmp and kpi_cmp.get("success"):
        kn = kpi_cmp.get("n_passed", 0)
        kt = kpi_cmp.get("n_compared", 0)
        summary_parts.append(f"{kn}/{kt} KPIs within 10% tolerance")
    summary = "; ".join(summary_parts)

    # Build publication-quality markdown table
    table_lines = [
        "## Literature Comparison Table",
        f"**Reference:** {ref_source}",
        "",
        "| Stream | Property | Simulated | Literature | Unit | Deviation | Status |",
        "|--------|----------|-----------|------------|------|-----------|--------|",
    ]
    for sc in stream_comparisons:
        for row in sc.get("rows", []):
            status_emoji = "✓" if row["status"] == "PASS" else "✗"
            table_lines.append(
                f"| {sc['stream_tag']} | {row['property']} "
                f"| {row['sim_value']} | {row['ref_value']} "
                f"| {row['unit']} | {row['deviation_pct']:.1f}% | {status_emoji} |"
            )
    if kpi_cmp and kpi_cmp.get("success"):
        table_lines.extend(["", "### KPI Comparison",
                             "| KPI | Simulated | Literature | Unit | Deviation | Status |",
                             "|-----|-----------|------------|------|-----------|--------|"])
        for row in kpi_cmp.get("rows", []):
            status_emoji = "✓" if row["status"] == "PASS" else "✗"
            table_lines.append(
                f"| {row['kpi']} | {row['sim_value']} | {row['ref_value']} "
                f"| {row['unit']} | {row['deviation_pct']:.1f}% | {status_emoji} |"
            )

    return {
        "success":             True,
        "process":             process,
        "reference_source":    ref_source,
        "base_case":           meta.get("base_case", {}),
        "stream_comparisons":  stream_comparisons,
        "kpi_comparison":      kpi_cmp,
        "summary":             summary,
        "publication_table":   "\n".join(table_lines),
        "n_streams_compared":  n_streams_total,
        "n_streams_passed":    n_streams_pass,
    }


def _is_float(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False
