"""
kinetics_db.py — Curated reaction kinetics database.

Each entry: rate equation, kinetic parameters, validity range, reference.
Used by the agent when configuring CSTR/PFR/Gibbs reactors.

Rate forms supported:
  • Power-law:        r = k * Π[Ci^ni]
  • LHHW:             r = k * (driving force) / (1 + Σ Ki·Ci)^n  (Langmuir-Hinshelwood)
  • Equilibrium-corr: r = k * (forward - reverse / Keq)

Arrhenius:  k = A * exp(-Ea/RT)
"""

from typing import Dict, List, Any
import math
import difflib


REACTION_KINETICS: List[Dict[str, Any]] = [

    # ─── Steam reforming ──────────────────────────────────────────────────────
    {
        "id": "smr_main",
        "name": "Steam Methane Reforming",
        "reaction": "CH4 + H2O ⇌ CO + 3 H2",
        "reactants": {"Methane": 1, "Water": 1},
        "products":  {"Carbon monoxide": 1, "Hydrogen": 3},
        "delta_h_kj_mol": 206.0,
        "equilibrium": True,
        "rate_form": "LHHW",
        "rate_equation": "r = (k1/p_H2^2.5) * (p_CH4*p_H2O - p_H2^3*p_CO/Keq) / DEN^2",
        "kinetics": {
            "A":      4.225e15,        # mol bar^0.5 / kg-cat/h
            "Ea_J":   240100,
            "n":      "LHHW",
            "adsorption": {"K_CO": 8.23e-5, "K_H2": 6.12e-9, "K_CH4": 6.65e-4, "K_H2O": 1.77e5},
        },
        "validity": {"T_K": [700, 1200], "P_bar": [10, 40]},
        "catalyst": "Ni/Al2O3",
        "reference": "Xu & Froment, AIChE J. 35 (1989) 88",
    },
    {
        "id": "wgs_high_temp",
        "name": "Water-Gas Shift (High Temperature)",
        "reaction": "CO + H2O ⇌ CO2 + H2",
        "reactants": {"Carbon monoxide": 1, "Water": 1},
        "products":  {"Carbon dioxide": 1, "Hydrogen": 1},
        "delta_h_kj_mol": -41.2,
        "equilibrium": True,
        "rate_form": "power_law_eq",
        "kinetics": {"A": 1.78e13, "Ea_J": 88000, "orders": {"CO": 1.0, "H2O": 0.5}},
        "validity": {"T_K": [573, 723], "P_bar": [1, 50]},
        "catalyst": "Fe2O3/Cr2O3",
        "reference": "Singh & Saraf, Ind. Eng. Chem. Process Des. Dev. 16 (1977)",
    },

    # ─── Methanol synthesis ───────────────────────────────────────────────────
    {
        "id": "methanol_synthesis_co",
        "name": "Methanol from CO",
        "reaction": "CO + 2 H2 ⇌ CH3OH",
        "reactants": {"Carbon monoxide": 1, "Hydrogen": 2},
        "products":  {"Methanol": 1},
        "delta_h_kj_mol": -90.7,
        "equilibrium": True,
        "rate_form": "LHHW",
        "kinetics": {"A": 4.89e7, "Ea_J": 113000},
        "validity": {"T_K": [490, 550], "P_bar": [50, 100]},
        "catalyst": "Cu/ZnO/Al2O3",
        "reference": "Graaf et al., Chem. Eng. Sci. 43 (1988) 3185",
    },
    {
        "id": "methanol_synthesis_co2",
        "name": "Methanol from CO2",
        "reaction": "CO2 + 3 H2 ⇌ CH3OH + H2O",
        "reactants": {"Carbon dioxide": 1, "Hydrogen": 3},
        "products":  {"Methanol": 1, "Water": 1},
        "delta_h_kj_mol": -49.4,
        "equilibrium": True,
        "rate_form": "LHHW",
        "kinetics": {"A": 1.09e5, "Ea_J": 87000},
        "validity": {"T_K": [490, 550], "P_bar": [50, 100]},
        "catalyst": "Cu/ZnO/Al2O3",
        "reference": "Graaf et al., Chem. Eng. Sci. 43 (1988) 3185",
    },

    # ─── Ammonia synthesis ────────────────────────────────────────────────────
    {
        "id": "ammonia_synthesis",
        "name": "Haber-Bosch Ammonia Synthesis",
        "reaction": "N2 + 3 H2 ⇌ 2 NH3",
        "reactants": {"Nitrogen": 1, "Hydrogen": 3},
        "products":  {"Ammonia": 2},
        "delta_h_kj_mol": -91.8,
        "equilibrium": True,
        "rate_form": "Temkin-Pyzhev",
        "rate_equation": "r = k1 * Keq^2 * p_N2 * (p_H2^3/p_NH3^2)^alpha - k2 * (p_NH3^2/p_H2^3)^(1-alpha)",
        "kinetics": {"A_fwd": 8.85e14, "Ea_fwd_J": 170000, "alpha": 0.5},
        "validity": {"T_K": [673, 823], "P_bar": [100, 300]},
        "catalyst": "Promoted iron (Fe-K2O-CaO-Al2O3)",
        "reference": "Temkin & Pyzhev, Acta Physicochim. URSS 12 (1940) 327",
    },

    # ─── Fischer-Tropsch ──────────────────────────────────────────────────────
    {
        "id": "fischer_tropsch_co",
        "name": "Fischer-Tropsch CO Consumption",
        "reaction": "CO + 2 H2 → -CH2- + H2O   (chain growth via ASF)",
        "reactants": {"Carbon monoxide": 1, "Hydrogen": 2},
        "products":  {},  # ASF distribution — not a simple stoichiometry
        "delta_h_kj_mol": -165.0,
        "equilibrium": False,
        "rate_form": "Yates-Satterfield",
        "kinetics": {"A": 8.85e-4, "Ea_J": 90000},
        "validity": {"T_K": [473, 573], "P_bar": [10, 30]},
        "catalyst": "Cobalt or Iron",
        "reference": "Yates & Satterfield, Energy Fuels 5 (1991) 168",
        "note": "Use ASF chain growth probability alpha = 0.85–0.95 for cobalt",
    },

    # ─── Cracking ─────────────────────────────────────────────────────────────
    {
        "id": "ethane_cracking",
        "name": "Ethane Steam Cracking (to Ethylene)",
        "reaction": "C2H6 → C2H4 + H2",
        "reactants": {"Ethane": 1},
        "products":  {"Ethene": 1, "Hydrogen": 1},
        "delta_h_kj_mol": 137.0,
        "equilibrium": True,
        "rate_form": "first_order",
        "kinetics": {"A": 4.65e13, "Ea_J": 273000, "orders": {"Ethane": 1.0}},
        "validity": {"T_K": [1023, 1173], "P_bar": [1.5, 3.0]},
        "catalyst": "Thermal (pyrolysis tubes)",
        "reference": "Sundaram & Froment, Chem. Eng. Sci. 32 (1977) 601",
    },
    {
        "id": "propane_cracking",
        "name": "Propane Steam Cracking",
        "reaction": "C3H8 → C2H4 + CH4",
        "reactants": {"Propane": 1},
        "products":  {"Ethene": 1, "Methane": 1},
        "delta_h_kj_mol": 81.0,
        "equilibrium": False,
        "rate_form": "first_order",
        "kinetics": {"A": 5.89e10, "Ea_J": 211000, "orders": {"Propane": 1.0}},
        "validity": {"T_K": [973, 1173], "P_bar": [1.5, 3.0]},
        "catalyst": "Thermal",
        "reference": "Sundaram & Froment, Chem. Eng. Sci. 32 (1977) 609",
    },

    # ─── MTBE / Etherification ────────────────────────────────────────────────
    {
        "id": "mtbe_synthesis",
        "name": "MTBE Synthesis",
        "reaction": "iC4H8 + CH3OH ⇌ MTBE",
        "reactants": {"Isobutene": 1, "Methanol": 1},
        "products":  {"MTBE": 1},
        "delta_h_kj_mol": -37.0,
        "equilibrium": True,
        "rate_form": "LHHW",
        "kinetics": {"A": 3.67e12, "Ea_J": 86000},
        "validity": {"T_K": [333, 363], "P_bar": [8, 15]},
        "catalyst": "Amberlyst-15 (sulfonic acid resin)",
        "reference": "Rehfinger & Hoffmann, Chem. Eng. Sci. 45 (1990) 1605",
    },

    # ─── Claus reaction ───────────────────────────────────────────────────────
    {
        "id": "claus_main",
        "name": "Claus Reaction (Catalytic)",
        "reaction": "2 H2S + SO2 ⇌ 3 S + 2 H2O",
        "reactants": {"Hydrogen sulfide": 2, "Sulfur dioxide": 1},
        "products":  {"Sulfur": 3, "Water": 2},
        "delta_h_kj_mol": -107.0,
        "equilibrium": True,
        "rate_form": "power_law_eq",
        "kinetics": {"A": 1.5e8, "Ea_J": 75000, "orders": {"Hydrogen sulfide": 1.0, "Sulfur dioxide": 0.5}},
        "validity": {"T_K": [493, 633], "P_bar": [1.0, 2.0]},
        "catalyst": "Activated alumina or titania",
        "reference": "Goar & Sames, Sulfur Recovery 2nd ed. (1988)",
    },

    # ─── Combustion (general) ─────────────────────────────────────────────────
    {
        "id": "methane_combustion",
        "name": "Methane Combustion",
        "reaction": "CH4 + 2 O2 → CO2 + 2 H2O",
        "reactants": {"Methane": 1, "Oxygen": 2},
        "products":  {"Carbon dioxide": 1, "Water": 2},
        "delta_h_kj_mol": -890.0,
        "equilibrium": False,
        "rate_form": "Westbrook-Dryer_global",
        "kinetics": {"A": 1.3e8, "Ea_J": 202600, "orders": {"Methane": -0.3, "Oxygen": 1.3}},
        "validity": {"T_K": [1100, 2300], "P_bar": [0.5, 50]},
        "catalyst": "None (gas-phase)",
        "reference": "Westbrook & Dryer, Combust. Sci. Tech. 27 (1981) 31",
    },
]


def list_reactions(catalyst: str = "", reactant: str = "") -> Dict[str, Any]:
    """List available reactions, optionally filtered."""
    filtered = REACTION_KINETICS
    if catalyst:
        filtered = [r for r in filtered if catalyst.lower() in r.get("catalyst", "").lower()]
    if reactant:
        filtered = [r for r in filtered if any(reactant.lower() in k.lower() for k in r.get("reactants", {}))]
    return {
        "success": True,
        "count": len(filtered),
        "reactions": [
            {
                "id":          r["id"],
                "name":        r["name"],
                "reaction":    r["reaction"],
                "catalyst":    r.get("catalyst", ""),
                "T_range_K":   r.get("validity", {}).get("T_K"),
                "P_range_bar": r.get("validity", {}).get("P_bar"),
            }
            for r in filtered
        ],
    }


def get_reaction(reaction_id: str) -> Dict[str, Any]:
    """Return full kinetic spec for a reaction."""
    for r in REACTION_KINETICS:
        if r["id"] == reaction_id:
            return {"success": True, "reaction": r}
    # Fuzzy fallback
    ids = [r["id"] for r in REACTION_KINETICS]
    suggestions = difflib.get_close_matches(reaction_id, ids, n=3, cutoff=0.45)
    return {
        "success": False,
        "error": f"Reaction '{reaction_id}' not found",
        "suggestions": suggestions,
        "all_ids": ids,
    }


def suggest_kinetics(reactants: List[str], T_K: float = 0, P_bar: float = 0) -> Dict[str, Any]:
    """Suggest matching reactions for a list of reactants and operating conditions."""
    reactants_lower = {r.lower() for r in reactants}
    matches = []
    for r in REACTION_KINETICS:
        r_reactants = {k.lower() for k in r.get("reactants", {})}
        overlap = r_reactants & reactants_lower
        if len(overlap) >= max(1, len(r_reactants) - 1):
            score = len(overlap) / max(1, len(r_reactants))
            # Penalize if T/P out of range
            valid_T = r.get("validity", {}).get("T_K", [0, 99999])
            valid_P = r.get("validity", {}).get("P_bar", [0, 99999])
            in_T = T_K == 0 or (valid_T[0] <= T_K <= valid_T[1])
            in_P = P_bar == 0 or (valid_P[0] <= P_bar <= valid_P[1])
            if not in_T: score *= 0.5
            if not in_P: score *= 0.7
            matches.append({"id": r["id"], "name": r["name"], "match_score": round(score, 2),
                            "in_T_range": in_T, "in_P_range": in_P})
    matches.sort(key=lambda m: -m["match_score"])
    return {"success": True, "count": len(matches), "matches": matches[:10]}


def evaluate_rate_arrhenius(reaction_id: str, T_K: float) -> Dict[str, Any]:
    """Quick Arrhenius rate constant evaluation at a given temperature."""
    r = get_reaction(reaction_id)
    if not r.get("success"):
        return r
    rxn = r["reaction"]
    kin = rxn.get("kinetics", {})
    A  = kin.get("A") or kin.get("A_fwd")
    Ea = kin.get("Ea_J") or kin.get("Ea_fwd_J")
    if A is None or Ea is None:
        return {"success": False, "error": "Reaction has no simple Arrhenius parameters"}
    R = 8.314
    k = A * math.exp(-Ea / (R * T_K))
    return {
        "success": True,
        "reaction_id": reaction_id,
        "T_K": T_K,
        "k": k,
        "A": A,
        "Ea_J_mol": Ea,
        "note": "Units of k depend on rate-form. See full reaction spec for context.",
    }
