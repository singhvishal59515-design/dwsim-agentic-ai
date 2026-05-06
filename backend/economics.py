"""
economics.py  —  Process Economic Optimizer
────────────────────────────────────────────
Estimates CAPEX, OPEX, energy cost, and profitability metrics
from a DWSIM simulation result.

CAPEX:  Turton/CAPCOST Bare-Module method (Turton et al., 4th ed., Ch. 7)
OPEX:   Utilities from simulation duties + raw material flows × prices
ROI:    NPV, IRR, payback period calculations

All monetary values in USD.  Flows in kg/h from DWSIM stream results.
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple


# ── Equipment CAPEX correlations (2023 USD, Turton + CEPCI updated) ──────────
# Base = purchased equipment cost (bare equipment, not installed).
# Installed (bare-module) cost = base × BM_FACTOR.
# Lang factor applied on top to get total capital investment.

EQUIPMENT_BASE_COST: Dict[str, Dict[str, Any]] = {
    "Heater":                   {"base": 55_000,  "bm": 3.2, "desc": "Heater/Furnace"},
    "Cooler":                   {"base": 55_000,  "bm": 3.2, "desc": "Heat Exchanger (cooler)"},
    "HeatExchanger":            {"base": 160_000, "bm": 3.2, "desc": "Shell & Tube Heat Exchanger"},
    "Pump":                     {"base": 25_000,  "bm": 3.3, "desc": "Centrifugal Pump"},
    "Compressor":               {"base": 380_000, "bm": 2.8, "desc": "Compressor/Blower"},
    "Valve":                    {"base": 15_000,  "bm": 1.5, "desc": "Control Valve"},
    "Vessel":                   {"base": 85_000,  "bm": 4.2, "desc": "Flash Drum / Vessel"},
    "Tank":                     {"base": 50_000,  "bm": 4.0, "desc": "Storage Tank"},
    "ShortcutColumn":           {"base": 650_000, "bm": 4.7, "desc": "Distillation Column (shortcut)"},
    "DistillationColumn":       {"base": 900_000, "bm": 4.7, "desc": "Distillation Column (rigorous)"},
    "AbsorptionColumn":         {"base": 550_000, "bm": 4.7, "desc": "Absorption Column"},
    "ReboiledAbsorber":         {"base": 700_000, "bm": 4.7, "desc": "Reboiled Absorber"},
    "RefluxedAbsorber":         {"base": 700_000, "bm": 4.7, "desc": "Refluxed Absorber"},
    "ConversionReactor":        {"base": 270_000, "bm": 4.0, "desc": "Conversion Reactor"},
    "GibbsReactor":             {"base": 380_000, "bm": 4.0, "desc": "Gibbs Equilibrium Reactor"},
    "PlugFlowReactor":          {"base": 330_000, "bm": 4.0, "desc": "Plug Flow Reactor"},
    "ContinuousStirredTankReactor": {"base": 310_000, "bm": 4.0, "desc": "CSTR"},
    "Mixer":                    {"base": 35_000,  "bm": 2.0, "desc": "Stream Mixer"},
    "Splitter":                 {"base": 25_000,  "bm": 2.0, "desc": "Stream Splitter"},
    "Pipe":                     {"base": 20_000,  "bm": 2.5, "desc": "Pipe Segment"},
    "WaterElectrolyzer":        {"base": 700_000, "bm": 2.5, "desc": "Water Electrolyzer (PEM/Alkaline)"},
    "PEMFuelCell":              {"base": 500_000, "bm": 2.5, "desc": "PEM Fuel Cell"},
    "SolarPanel":               {"base": 200_000, "bm": 1.5, "desc": "Solar Panel Array"},
    "WindTurbine":              {"base": 1_000_000,"bm":1.5, "desc": "Wind Turbine"},
    "Recycle":                  {"base":  5_000,  "bm": 1.0, "desc": "Recycle Controller (no hardware)"},
    "ComponentSeparator":       {"base": 120_000, "bm": 3.5, "desc": "Component Separator"},
    "SolidSeparator":           {"base": 150_000, "bm": 3.0, "desc": "Solid Separator"},
    "Filter":                   {"base": 100_000, "bm": 2.8, "desc": "Filter"},
    # Fallback
    "_default":                 {"base": 120_000, "bm": 3.5, "desc": "Generic Equipment"},
}

# Lang factor for total installed cost / total capital investment
# (includes piping, instrumentation, electrical, civil, insulation)
LANG_FLUID       = 4.7   # fluid processing
LANG_SOLID_FLUID = 4.0   # mixed solid/fluid
LANG_SOLID       = 3.1   # solid processing

# Contingency and contractor fee fraction
CONTINGENCY_FRAC = 0.15  # 15% of Total Direct Cost


# ── Utility cost defaults ─────────────────────────────────────────────────────
DEFAULT_PARAMS = {
    "annual_hours":           8000,      # h/yr (typical continuous plant)
    "product_price_per_kg":   1.0,       # $/kg
    "feed_price_per_kg":      0.30,      # $/kg
    "electricity_per_kWh":    0.08,      # $/kWh
    # Temperature-tiered steam pricing ($/GJ) — used when outlet_temps provided
    "steam_lp_per_GJ":        10.0,      # LP steam  <160°C  (~4 bar)
    "steam_mp_per_GJ":        15.0,      # MP steam  160-220°C (~10 bar)
    "steam_hp_per_GJ":        22.0,      # HP steam  220-280°C (~40 bar)
    "steam_furnace_per_GJ":   35.0,      # Direct-fired >280°C
    "steam_per_GJ":           18.0,      # Flat rate fallback (no temp data)
    "cooling_water_per_GJ":    0.25,     # $/GJ  (cooling water, <45°C)
    "refrigeration_per_GJ":   12.0,      # $/GJ  (chilled water <15°C)
    "cryo_per_GJ":            50.0,      # $/GJ  (cryogenic <-20°C)
    "project_life_years":     15,        # years
    "discount_rate":          0.12,      # 12% WACC
    "labor_per_year":         400_000,   # $/yr (operators + supervision)
    "lang_factor":            LANG_FLUID,
    "contingency_frac":       CONTINGENCY_FRAC,
    "product_stream_tags":    [],        # [] = use largest outlet by kg/h
    "feed_stream_tags":       [],        # [] = use largest inlet by kg/h
    "capex_scale":            1.0,       # multiplicative scale for CAPEX
}

# Temperature thresholds for utility tier selection (outlet temp in °C)
_STEAM_TIER_C = [
    (160,  "steam_lp_per_GJ",      "LP Steam (<160°C)"),
    (220,  "steam_mp_per_GJ",      "MP Steam (160-220°C)"),
    (280,  "steam_hp_per_GJ",      "HP Steam (220-280°C)"),
    (9999, "steam_furnace_per_GJ", "Direct-fired (>280°C)"),
]
_COOL_TIER_C = [
    (15,  "refrigeration_per_GJ", "Chilled Water (<15°C)"),
    (-20, "cryo_per_GJ",          "Cryogenic (<-20°C)"),   # checked below 15
]


def _steam_price(outlet_temp_C: Optional[float], params: Dict) -> Tuple[float, str]:
    """Return ($/GJ, tier_label) for a heating duty given the outlet temperature."""
    if outlet_temp_C is None:
        return params.get("steam_per_GJ", 18.0), "Steam (generic)"
    for threshold, key, label in _STEAM_TIER_C:
        if outlet_temp_C <= threshold:
            return params.get(key, DEFAULT_PARAMS[key]), label
    return params.get("steam_furnace_per_GJ", 35.0), "Direct-fired (>280°C)"


def _cool_price(outlet_temp_C: Optional[float], params: Dict) -> Tuple[float, str]:
    """Return ($/GJ, tier_label) for a cooling duty given the outlet temperature."""
    if outlet_temp_C is None:
        return params.get("cooling_water_per_GJ", 0.25), "Cooling Water (generic)"
    if outlet_temp_C < -20:
        return params.get("cryo_per_GJ", 50.0), "Cryogenic (<-20°C)"
    if outlet_temp_C < 15:
        return params.get("refrigeration_per_GJ", 12.0), "Chilled Water (<15°C)"
    return params.get("cooling_water_per_GJ", 0.25), "Cooling Water"


# ── CAPEX engine ──────────────────────────────────────────────────────────────

def _clean_type(typename: str) -> str:
    """Strip DWSIM namespace prefix, return short class name."""
    return typename.rsplit(".", 1)[-1]


def estimate_capex(
    objects: List[Dict],
    params: Dict,
) -> Dict[str, Any]:
    """
    Estimate CAPEX from a list of simulation objects using the Bare-Module
    method (Turton et al., "Analysis, Synthesis and Design of Chemical
    Processes", 4th ed., Chapter 7).

    Methodology (correctly separated):
      Purchased Equipment Cost (C_p)   = base cost × capex_scale
      Bare-Module Cost (C_BM)          = C_p × F_BM   (F_BM = BM factor)
      Total Bare-Module Cost (TBM)     = ΣC_BM
      Contingency + Fee (C_cont)       = TBM × contingency_frac
      Total Module Cost (C_TM)         = TBM + C_cont
      Grass-roots capital (C_GR)       = C_TM × 1.18  (Turton Eq 7.13)

    Note: The Lang factor method and BM method are ALTERNATIVE approaches.
    This implementation uses the BM method. The lang_factor param is stored
    for reference / override of TCC but NOT applied on top of BM factors
    (that would double-count installation costs).
    """
    items = []
    purchased_sum = 0.0
    bm_sum        = 0.0

    for obj in objects:
        category = obj.get("category", "").lower()
        typename  = _clean_type(obj.get("type", ""))
        tag       = obj.get("tag", typename)

        # Skip streams and pure logic objects (no hardware cost)
        if category == "stream" or "stream" in typename.lower():
            continue

        is_default = typename not in EQUIPMENT_BASE_COST
        entry      = EQUIPMENT_BASE_COST.get(typename, EQUIPMENT_BASE_COST["_default"])
        scale      = params.get("capex_scale", 1.0)
        c_p        = entry["base"] * scale          # purchased equipment cost
        c_bm       = c_p * entry["bm"]              # bare-module cost (installed)

        items.append({
            "tag":               tag,
            "type":              typename,
            "description":       entry["desc"],
            "purchased_usd":     round(c_p),
            "bm_usd":            round(c_bm),
            "cost_estimated":    is_default,         # True = unknown type, used $120k default
        })
        purchased_sum += c_p
        bm_sum        += c_bm

    contingency_frac = params.get("contingency_frac", CONTINGENCY_FRAC)
    contingency      = bm_sum * contingency_frac     # contractor fee + contingency
    c_tm             = bm_sum + contingency           # Total Module Cost
    # Grass-roots capital (Turton Eq 7.13): C_GR = C_TM × 1.18
    # (auxiliary facilities: site development, utility infrastructure, off-sites)
    c_gr             = c_tm * 1.18

    return {
        "equipment_items":    items,
        "purchased_total":    round(purchased_sum),   # ΣC_p
        "bare_module_total":  round(bm_sum),          # ΣC_BM
        "installed_total":    round(bm_sum),          # alias kept for UI compat
        "contingency":        round(contingency),
        "total_module_cost":  round(c_tm),            # C_TM (= FCI for revamp)
        "grassroots_capital": round(c_gr),            # C_GR (= FCI for new plant)
        "tcc":                round(c_gr),            # primary TCC reported to UI
        "lang_factor":        params.get("lang_factor", LANG_FLUID),  # stored, not applied
        "method":             "Bare-Module (Turton et al., 4th ed.)",
    }


# ── OPEX engine ───────────────────────────────────────────────────────────────

def _get_duty_kW(unit_op_duties: Dict[str, float]) -> float:
    """Sum all heating/cooling duties in kW (absolute value)."""
    return sum(abs(v) for v in unit_op_duties.values())


def estimate_opex(
    stream_results: Dict[str, Dict],
    unit_op_duties: Dict[str, float],
    params: Dict,
    unit_op_outlet_temps: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Estimate annual OPEX from simulation results.

    stream_results        : {tag: {mass_flow_kgh, ...}}
    unit_op_duties        : {tag: duty_kW}  (+ve=heating, -ve=cooling)
    unit_op_outlet_temps  : {tag: outlet_temp_C}  — enables temperature-tiered
                            utility pricing (LP/MP/HP steam, chilled water, cryo).
                            If None, falls back to flat steam_per_GJ rate.
    """
    hours = params.get("annual_hours", 8000)
    outlet_temps = unit_op_outlet_temps or {}

    # ── Feed cost ─────────────────────────────────────────────────────────────
    feed_tags  = params.get("feed_stream_tags", [])
    feed_price = params.get("feed_price_per_kg", 0.30)
    feed_cost  = 0.0
    feed_flows_kg_yr: Dict[str, float] = {}

    for tag, sr in stream_results.items():
        is_feed = (tag in feed_tags) if feed_tags else _looks_like_feed(tag, stream_results)
        if is_feed:
            mf = sr.get("mass_flow_kgh", 0.0) or 0.0
            flow_kg_yr = mf * hours
            feed_flows_kg_yr[tag] = flow_kg_yr
            feed_cost += flow_kg_yr * feed_price

    # ── Utility costs — temperature-tiered ───────────────────────────────────
    # Mechanical tags (pumps/compressors) → electricity, not steam
    _MECH_KW = ("pump", "compressor", "comp", "blower", "fan", "expander", "turbine")
    mech_tags = {t for t in unit_op_duties if any(kw in t.lower() for kw in _MECH_KW)}

    steam_cost   = 0.0
    cooling_cost = 0.0
    elec_kWh_yr  = 0.0
    utility_breakdown: List[Dict] = []

    heat_duty_kW = 0.0
    cool_duty_kW = 0.0

    for tag, duty_kw in unit_op_duties.items():
        if tag in mech_tags:
            # Mechanical work → electricity
            if duty_kw > 0:
                kwh = duty_kw * hours
                elec_kWh_yr += kwh
            continue

        if duty_kw > 0:
            # Heating utility — tier by outlet temperature
            gj_yr = duty_kw * hours * 3600 / 1e9
            price, tier_label = _steam_price(outlet_temps.get(tag), params)
            cost = gj_yr * price
            steam_cost   += cost
            heat_duty_kW += duty_kw
            utility_breakdown.append({
                "tag": tag, "type": "heating", "duty_kW": round(duty_kw, 1),
                "GJ_yr": round(gj_yr, 1), "tier": tier_label,
                "cost_usd": round(cost),
            })
        elif duty_kw < 0:
            # Cooling utility — tier by outlet temperature
            abs_kw = abs(duty_kw)
            gj_yr = abs_kw * hours * 3600 / 1e9
            price, tier_label = _cool_price(outlet_temps.get(tag), params)
            cost = gj_yr * price
            cooling_cost += cost
            cool_duty_kW += abs_kw
            utility_breakdown.append({
                "tag": tag, "type": "cooling", "duty_kW": round(abs_kw, 1),
                "GJ_yr": round(gj_yr, 1), "tier": tier_label,
                "cost_usd": round(cost),
            })

    # Mechanical fallback: if no pump/compressor tags identified, estimate
    # electricity as 10% of total heat duty. This is a rough heuristic with
    # no thermodynamic basis — it will underestimate compressor-heavy processes
    # significantly. Flagged in output so users know it is an approximation.
    _elec_estimated = False
    if not mech_tags and not elec_kWh_yr:
        elec_kWh_yr = heat_duty_kW * hours * 0.10
        _elec_estimated = True

    electricity_cost = elec_kWh_yr * params.get("electricity_per_kWh", 0.08)

    heat_GJ_yr = heat_duty_kW * hours * 3600 / 1e9
    cool_GJ_yr = cool_duty_kW * hours * 3600 / 1e9

    utilities = {
        "steam_heating":  round(steam_cost),
        "cooling":        round(cooling_cost),
        "electricity":    round(electricity_cost),
        "breakdown":      utility_breakdown,
    }
    utilities_total = steam_cost + cooling_cost + electricity_cost

    # ── Labor ─────────────────────────────────────────────────────────────────
    labor_cost = params.get("labor_per_year", 400_000)

    # ── Total OPEX (maintenance added by run_economic_analysis) ───────────────
    total_opex = feed_cost + utilities_total + labor_cost

    return {
        "raw_material":    round(feed_cost),
        "feed_flows_kg_yr": {k: round(v) for k, v in feed_flows_kg_yr.items()},
        "utilities":       utilities,
        "utilities_total": round(utilities_total),
        "labor":           round(labor_cost),
        "total_opex":      round(total_opex),
        "heat_duty_kW":    round(heat_duty_kW, 1),
        "cool_duty_kW":    round(cool_duty_kW, 1),
        "heat_GJ_yr":      round(heat_GJ_yr, 1),
        "cool_GJ_yr":      round(cool_GJ_yr, 1),
        "elec_kWh_yr":          round(elec_kWh_yr, 0),
        "elec_cost_estimated":  _elec_estimated,   # True = 10% heuristic, not from pump/compressor data
        "tiered_pricing":       bool(outlet_temps),
    }


def _looks_like_feed(tag: str, stream_results: Dict) -> bool:
    """
    Heuristic: treat a stream as 'feed' if its tag contains feed-like keywords
    or if it has the highest flow rate and a simpler composition.
    Falls back to picking the top-flow stream.
    """
    tag_lo = tag.lower()
    feed_words = ("feed", "in", "fresh", "raw", "inlet", "water_in", "waterin")
    if any(w in tag_lo for w in feed_words):
        return True
    return False


# ── Revenue engine ────────────────────────────────────────────────────────────

def estimate_revenue(
    stream_results: Dict[str, Dict],
    params: Dict,
) -> Dict[str, Any]:
    """Estimate annual revenue from product streams."""
    hours = params.get("annual_hours", 8000)
    product_tags = params.get("product_stream_tags", [])
    product_price = params.get("product_price_per_kg", 1.0)

    revenue = 0.0
    product_flows = {}

    for tag, sr in stream_results.items():
        is_product = (tag in product_tags) if product_tags else _looks_like_product(tag)
        if is_product:
            mf = sr.get("mass_flow_kgh", 0.0) or 0.0
            flow_kg_yr = mf * hours
            product_flows[tag] = flow_kg_yr
            revenue += flow_kg_yr * product_price

    # If no product identified, try all non-feed output streams
    if not product_flows and stream_results:
        for tag, sr in stream_results.items():
            if not _looks_like_feed(tag, stream_results):
                mf = sr.get("mass_flow_kgh", 0.0) or 0.0
                flow_kg_yr = mf * hours
                product_flows[tag] = flow_kg_yr
                revenue += flow_kg_yr * product_price

    return {
        "product_flows_kg_yr": {k: round(v) for k, v in product_flows.items()},
        "product_price_per_kg": product_price,
        "annual_revenue":      round(revenue),
    }


def _looks_like_product(tag: str) -> bool:
    tag_lo = tag.lower()
    product_words = ("product", "out", "distillate", "bottoms", "vapor", "liquid",
                     "h2out", "o2out", "clean", "purified", "rich", "top", "btm")
    return any(w in tag_lo for w in product_words)


# ── Financial metrics ─────────────────────────────────────────────────────────

def calculate_npv(capex: float, annual_profit: float, rate: float, years: int) -> float:
    """Calculate Net Present Value."""
    if rate <= 0:
        return annual_profit * years - capex
    pv_annuity = annual_profit * (1 - (1 + rate) ** -years) / rate
    return pv_annuity - capex


def calculate_irr(capex: float, annual_profit: float, years: int) -> Optional[float]:
    """Calculate Internal Rate of Return via bisection."""
    if annual_profit <= 0 or capex <= 0:
        return None
    # Simple payback < project life is needed for positive IRR
    if annual_profit * years <= capex:
        return None
    lo, hi = 0.001, 5.0
    for _ in range(60):
        mid = (lo + hi) / 2
        npv = calculate_npv(capex, annual_profit, mid, years)
        if abs(npv) < 1.0:
            return round(mid, 4)
        if npv > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 4)


def cash_flow_table(capex: float, annual_profit: float,
                    rate: float, years: int) -> List[Dict]:
    """Annual cash flow + cumulative NPV table."""
    rows = [{"year": 0, "cash_flow": -capex, "pv": -capex, "cumulative_npv": -capex}]
    cumulative = -capex
    for t in range(1, years + 1):
        pv = annual_profit / (1 + rate) ** t
        cumulative += pv
        rows.append({
            "year": t,
            "cash_flow": round(annual_profit),
            "pv": round(pv),
            "cumulative_npv": round(cumulative),
        })
    return rows


# ── Main entry point ──────────────────────────────────────────────────────────

def run_economic_analysis(
    objects: List[Dict],
    stream_results: Dict[str, Dict],
    unit_op_duties: Dict[str, float],
    user_params: Dict,
    unit_op_outlet_temps: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Full economic analysis.

    Parameters
    ----------
    objects         : from bridge.list_simulation_objects()
    stream_results  : from bridge.get_simulation_results()['stream_results']
    unit_op_duties  : {tag: duty_kW} — positive = heat in, negative = heat out
    user_params     : user inputs (see DEFAULT_PARAMS for keys)

    Returns complete economic summary dict.
    """
    params = {**DEFAULT_PARAMS, **user_params}

    # --- CAPEX ----------------------------------------------------------------
    capex_result = estimate_capex(objects, params)
    tcc = capex_result["tcc"]

    # Maintenance = 4% of TCC/yr
    maintenance_per_yr = tcc * 0.04

    # --- OPEX -----------------------------------------------------------------
    opex_result = estimate_opex(stream_results, unit_op_duties, params,
                                unit_op_outlet_temps=unit_op_outlet_temps)
    total_opex = opex_result["total_opex"] + maintenance_per_yr

    # --- Revenue --------------------------------------------------------------
    rev_result = estimate_revenue(stream_results, params)
    annual_revenue = rev_result["annual_revenue"]

    # --- Profit ---------------------------------------------------------------
    annual_profit = annual_revenue - total_opex
    gross_margin  = (annual_profit / annual_revenue) if annual_revenue > 0 else 0.0

    # --- Financial metrics ----------------------------------------------------
    years = int(params.get("project_life_years", 15))
    rate  = float(params.get("discount_rate", 0.12))
    payback = (tcc / annual_profit) if annual_profit > 0 else float("inf")
    npv     = calculate_npv(tcc, annual_profit, rate, years)
    irr     = calculate_irr(tcc, annual_profit, years)
    roi     = annual_profit / tcc if tcc > 0 else 0.0
    cf_table = cash_flow_table(tcc, annual_profit, rate, years)

    return {
        "success": True,
        "capex": {
            **capex_result,
            "maintenance_per_yr": round(maintenance_per_yr),
        },
        "opex": {
            **opex_result,
            "maintenance":  round(maintenance_per_yr),
            "total_opex":   round(total_opex),
        },
        "revenue": rev_result,
        "profit": {
            "annual_revenue":      annual_revenue,
            "total_opex_per_yr":   round(total_opex),
            "annual_profit":       round(annual_profit),
            "gross_margin_pct":    round(gross_margin * 100, 1),
        },
        "metrics": {
            "roi_pct":        round(roi * 100, 1),
            "payback_years":  round(payback, 2) if payback < 999 else None,
            "npv_usd":        round(npv),
            "irr_pct":        round(irr * 100, 1) if irr else None,
            "project_life":   years,
            "discount_rate_pct": round(rate * 100, 1),
        },
        "cash_flow_table": cf_table,
        "params_used": {k: v for k, v in params.items()
                        if k not in ("product_stream_tags", "feed_stream_tags")},
    }
