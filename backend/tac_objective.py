"""
tac_objective.py
────────────────
Total Annualized Cost (TAC) as an optimization objective — the canonical Aspen
Plus economic-optimization workflow ("minimize the TAC of this column").

    TAC = CRF · CAPEX  +  annual OPEX

where CAPEX is SIZE-DEPENDENT installed capital (Turton-style power-law
correlations C_p = a·S^b per equipment, × a bare-module factor) and OPEX is the
annual utility cost from the unit duties. CRF is the capital-recovery factor
i(1+i)^n / ((1+i)^n − 1). The size-dependence is what creates the trade-off an
optimizer can exploit: bigger equipment (more CAPEX) usually means less utility
(less OPEX), so TAC is convex with an interior minimum — exactly the
heat-recovery / reflux / driving-force trade-offs Aspen users optimise.

`make_tac_objective` adapts this into an objective callable for the project's
optimizers: it reads the current flowsheet's equipment sizes + duties (via a
caller-supplied reader) after each design evaluation and returns the TAC.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def capital_recovery_factor(rate: float = 0.10, years: int = 10) -> float:
    """CRF = i(1+i)^n / ((1+i)^n − 1); the fraction of capital charged per year."""
    if years <= 0:
        return 1.0
    if rate <= 0:
        return 1.0 / years
    f = (1.0 + rate) ** years
    return rate * f / (f - 1.0)


# Turton-style purchased-cost power law per equipment: C_p = a · S^b, with S the
# characteristic SIZE (area m², duty kW, power kW, #stages, volume m³); installed
# (bare-module) cost = C_p · F_BM. Coefficients are representative, overridable.
_CAPEX_CORR = {
    "heatexchanger": {"a": 12000.0, "b": 0.60, "bm": 3.2},
    "hx":            {"a": 12000.0, "b": 0.60, "bm": 3.2},
    "heater":        {"a": 8000.0,  "b": 0.65, "bm": 2.2},
    "cooler":        {"a": 8000.0,  "b": 0.65, "bm": 2.2},
    "column":        {"a": 20000.0, "b": 0.70, "bm": 4.0},
    "distillationcolumn": {"a": 20000.0, "b": 0.70, "bm": 4.0},
    "compressor":    {"a": 25000.0, "b": 0.70, "bm": 2.5},
    "pump":          {"a": 3000.0,  "b": 0.55, "bm": 3.3},
    "reactor":       {"a": 15000.0, "b": 0.60, "bm": 4.0},
    "_default":      {"a": 10000.0, "b": 0.60, "bm": 2.5},
}


def equipment_capex(equipment: List[Dict[str, Any]], capex_scale: float = 1.0,
                    correlations: Optional[Dict] = None) -> Dict[str, Any]:
    """Installed (bare-module) capital from size-dependent correlations.
    equipment = [{"type": str, "size": float}, …] (size in the type's unit)."""
    corr_tab = correlations or _CAPEX_CORR
    items, total = [], 0.0
    for e in equipment:
        corr = corr_tab.get(str(e.get("type", "")).lower(), corr_tab["_default"])
        S = max(1e-9, float(e.get("size", 1.0)))
        c_p = corr["a"] * (S ** corr["b"]) * capex_scale
        c_bm = c_p * corr["bm"]
        items.append({"type": e.get("type"), "size": S,
                      "purchased_usd": round(c_p), "installed_usd": round(c_bm)})
        total += c_bm
    return {"installed_total": total, "items": items}


def utility_opex(duties: List[Dict[str, Any]],
                 heat_price_usd_per_kJ: float = 18e-6,
                 cool_price_usd_per_kJ: float = 5e-6,
                 hours_per_year: float = 8000.0) -> Dict[str, Any]:
    """Annual utility cost from unit duties.
    duties = [{"kind": "heat"|"cool", "duty_kW": float}, …]
    cost = duty_kW × hours × 3600 (→ kJ/yr) × price ($/kJ)."""
    total, breakdown = 0.0, []
    for d in duties:
        kw = abs(float(d.get("duty_kW", 0.0)))
        price = (heat_price_usd_per_kJ if str(d.get("kind", "heat")).startswith("heat")
                 else cool_price_usd_per_kJ)
        cost = kw * hours_per_year * 3600.0 * price
        breakdown.append({"kind": d.get("kind"), "duty_kW": kw, "annual_usd": round(cost)})
        total += cost
    return {"annual_total": total, "breakdown": breakdown}


def total_annualized_cost(equipment: List[Dict[str, Any]],
                          duties: List[Dict[str, Any]],
                          rate: float = 0.10, years: int = 10,
                          hours_per_year: float = 8000.0,
                          capex_scale: float = 1.0,
                          heat_price_usd_per_kJ: float = 18e-6,
                          cool_price_usd_per_kJ: float = 5e-6) -> Dict[str, Any]:
    """TAC = CRF · CAPEX + annual OPEX. Returns the components for transparency."""
    crf = capital_recovery_factor(rate, years)
    cap = equipment_capex(equipment, capex_scale)
    op = utility_opex(duties, heat_price_usd_per_kJ, cool_price_usd_per_kJ, hours_per_year)
    annualized_capex = crf * cap["installed_total"]
    tac = annualized_capex + op["annual_total"]
    return {
        "tac": tac,
        "capex_installed": cap["installed_total"],
        "annualized_capex": annualized_capex,
        "annual_opex": op["annual_total"],
        "crf": crf,
        "equipment": cap["items"],
        "utilities": op["breakdown"],
    }


def make_tac_objective(read_state: Callable[[], Dict[str, Any]],
                       econ_params: Optional[Dict[str, Any]] = None
                       ) -> Callable[[], Dict[str, Any]]:
    """Wrap TAC as a no-argument objective for the optimizer to read AFTER it has
    set the design variables and solved the flowsheet. `read_state()` must return
    {"equipment": [{type, size}, …], "duties": [{kind, duty_kW}, …]}. Returns a
    callable -> {"objective": tac, "tac_breakdown": {...}}; objective is None if
    the state can't be read (so the optimizer treats it as a failed evaluation).
    """
    p = econ_params or {}

    def objective() -> Dict[str, Any]:
        st = read_state() or {}
        eq = st.get("equipment") or []
        du = st.get("duties") or []
        if not eq and not du:
            return {"objective": None, "tac_breakdown": None}
        res = total_annualized_cost(
            eq, du,
            rate=p.get("rate", 0.10), years=p.get("years", 10),
            hours_per_year=p.get("hours_per_year", 8000.0),
            capex_scale=p.get("capex_scale", 1.0),
            heat_price_usd_per_kJ=p.get("heat_price_usd_per_kJ", 18e-6),
            cool_price_usd_per_kJ=p.get("cool_price_usd_per_kJ", 5e-6))
        return {"objective": res["tac"], "tac_breakdown": res}
    return objective
