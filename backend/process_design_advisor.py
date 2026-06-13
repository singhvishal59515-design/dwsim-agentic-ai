"""
process_design_advisor.py
─────────────────────────
Chemical engineering process design intelligence. Provides:

  1. process_synthesis(goal)            — Suggest flowsheet structure from a goal
  2. equipment_sizing(type, duty, ...)  — Preliminary equipment sizing correlations
  3. separation_sequence(compounds, pp) — Synthesis of separation train
  4. reaction_system_design(rxn)        — Reactor selection and sizing heuristics
  5. property_package_selector(comps)   — Rigorous PP recommendation
  6. heat_integration_targets(streams)  — Pinch analysis targets without full HEN
  7. design_checklist(process_type)     — HAZOP-lite design checklist
  8. troubleshoot_design(symptoms)      — Design-level diagnosis

All functions return structured dicts and work WITHOUT an LLM.
The LLM can call these as tools for deeper reasoning.

References:
  - Douglas (1988) Conceptual Design of Chemical Processes
  - Turton et al. (2018) Analysis, Synthesis, and Design of Chemical Processes
  - Smith (2005) Chemical Process Design and Integration
  - Seider et al. (2010) Product and Process Design Principles
  - Perry's Chemical Engineers' Handbook (9th ed.)
"""

from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════════════════════
# 1. PROCESS SYNTHESIS — Douglas Methodology
# ══════════════════════════════════════════════════════════════════════════════

# Reaction-system heuristics from Douglas Level-2
REACTOR_SELECTION = {
    "homogeneous_gas": {
        "reactor": "PFR",
        "reason": "Gas-phase reactions benefit from plug flow (no back-mixing). PFR is preferred for A→B where selectivity matters.",
        "temperature_control": "Adiabatic with inter-cooling, or multi-tube isothermal",
        "dwsim_unit": "PFR",
    },
    "homogeneous_liquid": {
        "reactor": "CSTR",
        "reason": "Liquid-phase reactions: CSTR at steady state = well-mixed tank. Easy heat removal via jacket.",
        "temperature_control": "Jacketed CSTR, cooling coil",
        "dwsim_unit": "CSTR",
    },
    "gas_liquid": {
        "reactor": "Bubble column / Stirred tank",
        "reason": "Mass transfer between phases dominates. Need high interfacial area.",
        "temperature_control": "External heat exchanger on recirculating liquid",
        "dwsim_unit": "ConversionReactor (with user kinetics)",
    },
    "catalytic_fixed_bed": {
        "reactor": "PFR (adiabatic) or multi-tube isothermal",
        "reason": "Solid catalyst requires fixed bed. Adiabatic if ΔTad < 50°C, else multi-tube.",
        "temperature_control": "Cold shot quench or inter-bed cooling",
        "dwsim_unit": "PFR",
        "notes": "Check for hot spots; use radial flow if pressure drop is critical",
    },
    "equilibrium_limited": {
        "reactor": "Equilibrium reactor + recycle",
        "reason": "For reactions limited by equilibrium (NH₃, CH₃OH synthesis): operate at conditions of maximum rate, recycle unreacted feed.",
        "temperature_control": "Optimise T for rate-equilibrium trade-off",
        "dwsim_unit": "EquilibriumReactor",
    },
    "combustion": {
        "reactor": "Gibbs reactor (minimise G)",
        "reason": "Combustion and high-T equilibria are best handled by Gibbs free-energy minimisation.",
        "temperature_control": "Adiabatic flame temperature calculation",
        "dwsim_unit": "GibbsReactor",
    },
    "biological": {
        "reactor": "CSTR (fermentor)",
        "reason": "Biochemical reactions: CSTR with continuous culture or fed-batch.",
        "dwsim_unit": "CSTR",
    },
}

# Separation sequence heuristics (Smith 2005, Chapter 5)
SEPARATION_HEURISTICS = [
    {"rule": "Easiest split first",
     "detail": "Remove the component that requires the least energy/stages first. "
                "This reduces the feed to subsequent columns."},
    {"rule": "Most-plentiful component distilled first",
     "detail": "Remove the most abundant component overhead first to minimise "
                "reboiler duty downstream."},
    {"rule": "Avoid difficult splits early",
     "detail": "Difficult splits (α < 1.3) should come late when fewer components remain."},
    {"rule": "Favour direct sequences for wide-boiling mixtures",
     "detail": "When boiling-point spread is large, direct sequence (lightest overhead first) "
                "is thermally efficient."},
    {"rule": "Consider side draws for multicomponent mixtures",
     "detail": "Side draws can eliminate a column if a middle component is needed at >90% purity."},
    {"rule": "Azeotropes require special sequencing",
     "detail": "Binary azeotropes need entrainer addition (extractive/azeotropic distillation) "
                "or pressure-swing distillation."},
    {"rule": "Reactive distillation for equilibrium reactions",
     "detail": "If separation and reaction share the same pressure/temperature window, "
                "reactive distillation may eliminate a reactor."},
]

# Equipment heuristics for preliminary sizing (Turton Table 6.6)
EQUIPMENT_HEURISTICS = {
    "heat_exchanger": {
        "U_W_m2K": {
            "liquid-liquid":    500,   # W/m²K
            "liquid-gas":       200,
            "gas-gas":           50,
            "condensing-steam": 1500,
            "boiling-liquid":   1200,
        },
        "area_formula": "A = Q / (U × LMTD)",
        "min_area_m2":   1.0,
        "max_area_m2":  2000.0,
        "cost_law":     "C = 16000 × (A/A_ref)^0.6  [$ CEPCI 2020]",
        "notes": [
            "Shell-and-tube preferred for Q > 100 kW or T > 300°C",
            "Plate-and-frame for Q < 1 MW and T < 150°C",
            "Spiral for viscous or fouling services",
        ],
    },
    "distillation_column": {
        "tray_efficiency": 0.70,
        "tray_spacing_m":  0.60,
        "downcomer_area_fraction": 0.12,
        "flood_fraction": 0.75,
        "sizing": {
            "N_actual": "N_theoretical / E_Murphree",
            "height_m": "N_actual × tray_spacing + 20%",
            "diameter": "Use Fair correlation: V_max from entrainment flooding",
        },
        "reflux_ratio_rule": "R_operating = 1.1 to 1.5 × R_minimum",
        "Rmin_underwood": "Solve Underwood equations for minimum reflux",
        "notes": [
            "Check weeping (F-factor > 0.5 m/s (kg/m³)^0.5)",
            "HETP for packed columns: 0.3–0.6 m for random packing, 0.2–0.4 m for structured",
            "Column diameter D > 0.6 m for trays; < 0.6 m use packed",
        ],
    },
    "pump": {
        "efficiency": 0.75,
        "power_formula": "W = Q × ΔP / η  [kW]",
        "cavitation_check": "NPSH_available > NPSH_required + 0.5 m",
        "notes": [
            "Centrifugal pump for Q > 1 m³/h and low viscosity",
            "Positive displacement for viscous fluids or precise metering",
            "Multiple stages if ΔP > 300 bar",
        ],
    },
    "compressor": {
        "isentropic_efficiency": 0.78,
        "polytropic_efficiency": 0.82,
        "power_formula": "W = (n/(n-1)) × z × R × T_in × [(P_out/P_in)^((n-1)/n) - 1] / η_p",
        "max_ratio_per_stage": 4.0,
        "notes": [
            "Limit compression ratio per stage to 3–4",
            "Centrifugal for Q > 500 m³/h",
            "Reciprocating for high-pressure low-volume",
            "Always include inter-cooling for multi-stage",
        ],
    },
    "flash_drum": {
        "L_D_ratio": 3.0,
        "holdup_minutes": 5.0,
        "vapor_velocity_m_s": 0.15,
        "sizing": "D = (4 × Q_v / (π × v_vapor))^0.5",
        "notes": [
            "Horizontal drum if V/L ratio > 1 (volume basis)",
            "Vertical drum preferred for clean services",
            "Add demister pad if liquid carryover is critical",
        ],
    },
    "absorber": {
        "packing_HETP_m": 0.5,
        "L_G_ratio_min": 1.2,
        "notes": [
            "L/G = 1.2–1.5 × minimum (from operating line above equilibrium)",
            "Structured packing for σ < 30 mN/m (foaming systems)",
            "Random packing for non-foaming at lower cost",
        ],
    },
}


def process_synthesis(
    goal: str,
    reactants: Optional[List[str]] = None,
    products: Optional[List[str]] = None,
    phase: str = "liquid",
    scale_tonne_h: float = 10.0,
) -> Dict[str, Any]:
    """Suggest a process flowsheet structure from a high-level goal.
    Follows Douglas Level-1 to Level-4 methodology."""
    goal_lc = goal.lower()

    # Level 1: Batch vs continuous
    batch = any(k in goal_lc for k in ("batch", "pharmaceutical", "specialty", "small scale"))
    scale_mode = "Batch" if batch or scale_tonne_h < 0.1 else "Continuous"

    # Level 2: Input-output structure
    reactants = reactants or []
    products  = products  or []

    # Reaction system suggestion
    rxn_hints = {}
    if "catalytic" in goal_lc or "catalyst" in goal_lc:
        rxn_hints = REACTOR_SELECTION["catalytic_fixed_bed"]
    elif "combust" in goal_lc or "burn" in goal_lc:
        rxn_hints = REACTOR_SELECTION["combustion"]
    elif "ferment" in goal_lc or "bio" in goal_lc:
        rxn_hints = REACTOR_SELECTION["biological"]
    elif "equilibrium" in goal_lc or "reversible" in goal_lc:
        rxn_hints = REACTOR_SELECTION["equilibrium_limited"]
    elif "gas" in phase:
        rxn_hints = REACTOR_SELECTION["homogeneous_gas"]
    else:
        rxn_hints = REACTOR_SELECTION["homogeneous_liquid"]

    # Level 3: Recycle structure
    recycle_needed = any(k in goal_lc for k in
                          ("recycle", "unconverted", "low conversion",
                           "selectivity", "equilibrium"))
    purge_needed = recycle_needed and any(k in goal_lc for k in
                                           ("inert", "purge", "buildup"))

    # Level 4: Separation sequence
    sep_steps = []
    if "distillation" in goal_lc or "separate" in goal_lc or len(products) > 1:
        sep_steps.append("Distillation (fractionation column)")
    if "acid gas" in goal_lc or "CO2" in str(products) or "H2S" in str(products):
        sep_steps.append("Amine scrubbing (absorber + stripper)")
    if "drying" in goal_lc or "water removal" in goal_lc:
        sep_steps.append("Adsorption drying (mol sieve) or condensation")
    if not sep_steps:
        sep_steps.append("Flash separation (phase split) — then distillation if purity required")

    # Heat integration (Level 5)
    hi_tip = ("Consider heat integration: pre-heat reactor feed with "
              "reactor effluent. Use Pinch Analysis for networks with >3 "
              "hot/cold streams. Target ΔT_min = 10°C for liquid, 20°C for gas.")

    # Utility requirements
    utilities = []
    if "exothermic" in goal_lc or "combustion" in goal_lc:
        utilities.append("Cooling water / steam generation (heat export)")
    if "endothermic" in goal_lc or "reforming" in goal_lc or "cracking" in goal_lc:
        utilities.append("Fired heater / furnace (high-grade heat input)")
    if "cryogenic" in goal_lc or "LNG" in goal_lc:
        utilities.append("Refrigeration cycle (propane / ethylene / nitrogen)")
    if not utilities:
        utilities.append("Steam (LP 3.5 bar, MP 10 bar, HP 41 bar)")
        utilities.append("Cooling water (30→45°C supply/return)")

    # DWSIM flowsheet template
    dwsim_units = [rxn_hints.get("dwsim_unit", "ConversionReactor")]
    if recycle_needed:
        dwsim_units += ["Splitter (recycle split)", "Mixer (recycle join)"]
    dwsim_units += ["HeatExchanger (feed/effluent)", "Flash (gas/liquid split)"]
    dwsim_units += ["DistillationColumn"] if "Distillation" in sep_steps[0] else ["Absorber"]

    return {
        "success": True,
        "scale_mode": scale_mode,
        "scale_tonne_h": scale_tonne_h,
        "reaction_system": rxn_hints,
        "recycle_needed": recycle_needed,
        "purge_needed": purge_needed,
        "separation_steps": sep_steps,
        "heat_integration": hi_tip,
        "utilities": utilities,
        "recommended_dwsim_units": dwsim_units,
        "separation_heuristics": SEPARATION_HEURISTICS[:3],
        "methodology": "Douglas (1988) Conceptual Design, Levels 1–5",
        "next_steps": [
            "1. Set up flowsheet in DWSIM with recommended units",
            "2. Choose property package (see property_package_selector())",
            "3. Perform material balance at design case",
            "4. Size key equipment with equipment_sizing()",
            "5. Run heat integration with heat_integration_targets()",
        ],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 2. EQUIPMENT SIZING
# ══════════════════════════════════════════════════════════════════════════════

def equipment_sizing(
    equipment_type: str,
    duty_kW:        float = 0.0,
    flow_m3h:       float = 0.0,
    delta_P_bar:    float = 0.0,
    T_in_C:         float = 25.0,
    T_out_C:        float = 100.0,
    LMTD_C:         float = 20.0,
    service:        str   = "liquid-liquid",
    n_theoretical:  int   = 20,
    alpha_relative: float = 2.0,
) -> Dict[str, Any]:
    """Preliminary equipment sizing using Perry's / Turton correlations."""
    etype = equipment_type.lower().replace("-", "_").replace(" ", "_")

    if "heat" in etype or "exchang" in etype:
        U = EQUIPMENT_HEURISTICS["heat_exchanger"]["U_W_m2K"].get(service, 500)
        area = abs(duty_kW) * 1000.0 / (U * max(LMTD_C, 1.0))   # m²
        cost = 16000 * (area / 10.0) ** 0.6
        return {
            "type": "Heat Exchanger",
            "area_m2": round(area, 2),
            "U_W_m2K": U,
            "service": service,
            "LMTD_C": LMTD_C,
            "duty_kW": duty_kW,
            "estimated_cost_USD": round(cost),
            "notes": EQUIPMENT_HEURISTICS["heat_exchanger"]["notes"],
            "formula": "A = Q / (U × LMTD)",
        }

    if "distill" in etype or "column" in etype:
        E = EQUIPMENT_HEURISTICS["distillation_column"]["tray_efficiency"]
        n_actual = math.ceil(n_theoretical / E)
        spacing  = EQUIPMENT_HEURISTICS["distillation_column"]["tray_spacing_m"]
        height   = n_actual * spacing * 1.2
        R_min_approx = alpha_relative / (alpha_relative - 1.0) if alpha_relative > 1 else 5
        R_op = 1.2 * R_min_approx
        return {
            "type": "Distillation Column",
            "n_theoretical_stages": n_theoretical,
            "n_actual_stages": n_actual,
            "tray_efficiency": E,
            "column_height_m": round(height, 1),
            "min_reflux_approx": round(R_min_approx, 2),
            "operating_reflux": round(R_op, 2),
            "notes": EQUIPMENT_HEURISTICS["distillation_column"]["notes"],
            "ref": "Underwood (Rmin), Gilliland correlation (Nmin)",
        }

    if "pump" in etype:
        eta = EQUIPMENT_HEURISTICS["pump"]["efficiency"]
        power_kW = flow_m3h / 3600.0 * delta_P_bar * 1e5 / (eta * 1000.0)
        return {
            "type": "Centrifugal Pump",
            "power_kW": round(power_kW, 2),
            "flow_m3h": flow_m3h,
            "delta_P_bar": delta_P_bar,
            "efficiency": eta,
            "notes": EQUIPMENT_HEURISTICS["pump"]["notes"],
            "formula": "W = Q × ΔP / η",
        }

    if "compress" in etype:
        n = 1.3  # polytropic index typical gas
        eta_p = EQUIPMENT_HEURISTICS["compressor"]["polytropic_efficiency"]
        if delta_P_bar > 0 and T_in_C > -273:
            T_in_K = T_in_C + 273.15
            P_ratio = (delta_P_bar + 1.01325) / 1.01325  # assume intake at 1 atm
            n_stages = max(1, math.ceil(math.log(P_ratio) / math.log(4)))
            ratio_per = P_ratio ** (1.0 / n_stages)
            T_out_K = T_in_K * ratio_per ** ((n - 1) / n)
            # Perry's centrifugal: W ≈ z×R×T_in/M × n/(n-1) × (r^((n-1)/n)-1) / η_p
            power_kJ_kmol = 8.314 * T_in_K * n / (n - 1) * (ratio_per ** ((n - 1) / n) - 1) / eta_p
        else:
            n_stages = 1; T_out_K = T_in_C + 300; power_kJ_kmol = 0
        return {
            "type": "Compressor",
            "n_stages": n_stages,
            "ratio_per_stage": round(ratio_per, 2) if delta_P_bar > 0 else "?",
            "T_discharge_C": round(T_out_K - 273.15, 1),
            "power_kJ_kmol_approx": round(power_kJ_kmol, 0),
            "polytropic_efficiency": eta_p,
            "notes": EQUIPMENT_HEURISTICS["compressor"]["notes"],
        }

    if "flash" in etype or "separator" in etype or "drum" in etype:
        # Vapour velocity limit (Souders-Brown correlation)
        rho_L, rho_V = 700.0, 10.0  # typical liquid/vapour densities kg/m³
        K_SB = 0.04  # Souders-Brown constant, m/s
        v_max = K_SB * math.sqrt((rho_L - rho_V) / rho_V)
        D_m = math.sqrt(4 * flow_m3h / 3600.0 / (0.75 * math.pi * v_max)) if flow_m3h else 1.0
        L_m = max(1.2, D_m * EQUIPMENT_HEURISTICS["flash_drum"]["L_D_ratio"])
        return {
            "type": "Flash Drum / Separator",
            "diameter_m": round(D_m, 2),
            "height_m": round(L_m, 1),
            "vapor_velocity_m_s": round(v_max, 3),
            "K_Souders_Brown": K_SB,
            "notes": EQUIPMENT_HEURISTICS["flash_drum"]["notes"],
        }

    return {
        "success": False,
        "error": f"Equipment type '{equipment_type}' not in sizing database",
        "available_types": ["heat_exchanger", "distillation_column", "pump",
                             "compressor", "flash_drum", "absorber"],
    }


# ══════════════════════════════════════════════════════════════════════════════
# 3. SEPARATION SEQUENCE SYNTHESIS
# ══════════════════════════════════════════════════════════════════════════════

# Relative volatility rules for common separations
SEPARATION_METHODS = {
    "distillation": {
        "applicable": "α > 1.05, T_boil difference > 3°C",
        "not_applicable": "Azeotropes, heat-sensitive products",
        "energy_rank": 2,
        "cost_rank": 2,
    },
    "absorption": {
        "applicable": "Gas/vapour removal, CO2/H2S removal from gas",
        "energy_rank": 3,
        "cost_rank": 3,
    },
    "liquid_liquid_extraction": {
        "applicable": "Low α, heat-sensitive, high-boiling or similar boiling points",
        "energy_rank": 1,
        "cost_rank": 3,
    },
    "adsorption": {
        "applicable": "Trace contaminant removal, drying, PSA for H2 purification",
        "energy_rank": 1,
        "cost_rank": 4,
    },
    "membranes": {
        "applicable": "H2/N2 separation, O2/N2, CO2 capture, pervaporation for organics",
        "energy_rank": 1,
        "cost_rank": 3,
    },
    "crystallisation": {
        "applicable": "Optical isomers, similar boiling points, high-purity solids",
        "energy_rank": 1,
        "cost_rank": 4,
    },
}


def separation_sequence(
    compounds: List[str],
    property_package: str = "Peng-Robinson",
    feed_phase: str = "mixed",
    purity_target: float = 0.99,
) -> Dict[str, Any]:
    """Suggest separation sequence following Smith (2005) Chapters 4-6."""
    n = len(compounds)
    if n < 2:
        return {"success": False, "error": "Need at least 2 compounds to suggest separation"}

    # Number of possible sequences for simple columns
    n_seq = math.factorial(2 * (n - 1)) // (math.factorial(n - 1) * math.factorial(n))

    # Identify special compounds
    water = any("water" in c.lower() or "h2o" == c.lower() for c in compounds)
    acid_gas = any(c.lower() in ("co2", "h2s", "so2", "carbondioxide",
                                  "hydrogen sulfide") for c in compounds)
    light_gases = any(c.lower() in ("h2", "hydrogen", "n2", "nitrogen",
                                     "ch4", "methane", "co") for c in compounds)

    steps = []
    if light_gases:
        steps.append({"step": 1, "operation": "Flash / gas-liquid separator",
                       "purpose": "Remove non-condensables (H2, N2, CH4) as overhead gas",
                       "dwsim": "Flash"})
    if acid_gas:
        steps.append({"step": len(steps)+1,
                       "operation": "Amine scrubbing (MEA/DEA/MDEA absorber)",
                       "purpose": "Remove CO2/H2S to ppm levels",
                       "dwsim": "AbsorptionColumn"})
    if water and n > 2:
        steps.append({"step": len(steps)+1,
                       "operation": "Dehydration (mol sieve or glycol absorption)",
                       "purpose": "Remove water before final fractionation",
                       "dwsim": "Absorber or model as stream spec"})

    # Main fractionation sequence
    for i in range(min(n - 1, 4)):
        steps.append({
            "step": len(steps)+1,
            "operation": f"Distillation column C-{i+1:02d}",
            "purpose": f"Separate component {i+1} from heavier components",
            "dwsim": "DistillationColumn",
            "notes": f"R/Rmin = 1.2–1.5; tray efficiency 70%; check azeotropes",
        })

    # Reflux ratio guidance
    purity_note = (
        f"For purity target {purity_target*100:.0f}%: "
        + ("Use extractive distillation or add solvent if α < 1.1. " if purity_target > 0.995 else "")
        + "Check VLE data carefully near azeotropic compositions."
    )

    # Choose property package
    pp_rec = "Peng-Robinson" if not water else "NRTL"
    if acid_gas: pp_rec = "eNRTL or Kent-Eisenberg"

    return {
        "success": True,
        "n_compounds": n,
        "n_possible_sequences": n_seq,
        "recommended_sequence": steps,
        "separation_methods_evaluated": SEPARATION_METHODS,
        "purity_guidance": purity_note,
        "recommended_pp": pp_rec,
        "heuristics_applied": SEPARATION_HEURISTICS,
        "ref": "Smith (2005) Chemical Process Design, Chapters 4-6",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4. PROPERTY PACKAGE SELECTOR (rigorous)
# ══════════════════════════════════════════════════════════════════════════════

def property_package_selector(
    compounds: List[str],
    pressure_bar: float = 1.01325,
    temperature_C: float = 25.0,
    application: str = "",
) -> Dict[str, Any]:
    """Select thermodynamic model following the decision tree in
    Carlson (1996) 'Don't gamble with physical properties'."""
    comps_lc = [c.lower() for c in compounds]
    app_lc   = application.lower()

    # Classify compound set
    polar      = any(c in comps_lc for c in
                      ["water","methanol","ethanol","acetone","acetic acid",
                       "ammonia","mea","dea","mdea","glycol"])
    electrolyte = any(c in comps_lc for c in
                       ["naoh","hcl","h2so4","nacl","kcl","nahco3","na2co3"])
    acid_gas   = any(c in comps_lc for c in ["co2","h2s","so2","nh3"])
    hc         = any(c in comps_lc for c in
                      ["methane","ethane","propane","butane","pentane","hexane",
                       "heptane","octane","benzene","toluene","xylene","ethylene",
                       "propylene","ch4","c2h6","c3h8","n-butane","iso-butane"])
    high_press = pressure_bar > 30
    cryogenic  = temperature_C < -50

    # Decision tree
    if electrolyte:
        pp = "Electrolyte NRTL (eNRTL)"
        reason = "Ionic species present — must account for electrolyte non-ideality and speciation"
    elif acid_gas and polar:
        pp = "Kent-Eisenberg or eNRTL"
        reason = "Acid gas absorption in amine solvent — chemical equilibrium + electrolyte effects"
    elif acid_gas and hc:
        pp = "Peng-Robinson with Huron-Vidal mixing rules (PR-HV)"
        reason = "Acid gas in hydrocarbon mixture — cubic EOS with modified mixing rules for polarity"
    elif polar and not hc:
        pp = "NRTL" if pressure_bar < 10 else "Wong-Sandler PR"
        reason = ("Activity coefficient model for polar mixtures at low pressure" if pressure_bar < 10
                   else "Modified cubic EOS for polar mixtures at elevated pressure")
    elif polar and hc:
        pp = "UNIFAC (predictive) or NRTL (if data available)"
        reason = "Mixed polar/hydrocarbon — need activity coefficient model; UNIFAC if no binary data"
    elif hc and high_press:
        pp = "Peng-Robinson (PR)"
        reason = "Hydrocarbons at high pressure — cubic EOS accurate for vapour-phase non-ideality"
    elif hc:
        pp = "Peng-Robinson (PR) or Soave-Redlich-Kwong (SRK)"
        reason = "Hydrocarbons — either PR or SRK; PR gives slightly better liquid density"
    elif cryogenic:
        pp = "Lee-Kesler-Plöcker or GERG-2008"
        reason = "Cryogenic service — LKP or GERG-2008 accurate at very low temperatures"
    elif len(compounds) == 1 and ("water" in comps_lc or "steam" in comps_lc):
        pp = "Steam Tables (IAPWS-IF97)"
        reason = "Pure water / steam — IAPWS-IF97 most accurate"
    else:
        pp = "Peng-Robinson (PR)"
        reason = "General purpose — PR is the industry default for most non-polar systems"

    # Accuracy warning
    accuracy = "High (±1%)" if pp in ("Steam Tables (IAPWS-IF97)", "GERG-2008") else \
               "Good (±2-5%)" if pp in ("Peng-Robinson (PR)", "SRK", "NRTL") else \
               "Moderate (±5-10% for VLE)"

    # CRITICAL: the decision tree above can name models DWSIM cannot instantiate
    # (e.g. "Electrolyte NRTL (eNRTL)", "Kent-Eisenberg", "Wong-Sandler PR",
    # "SRK"). Route the recommendation through the grounded registry so the
    # returned `recommended_pp` is ALWAYS a real DWSIM package the agent can pass
    # straight to build_flowsheet_atomic, while preserving the ideal/Aspen name.
    ideal_model = pp
    dwsim_pp = pp
    availability_note = ""
    try:
        from thermo_models import resolve_to_dwsim, is_available
        if not is_available(pp):
            r = resolve_to_dwsim(pp)
            dwsim_pp = r["dwsim_name"]
            availability_note = r["note"]
    except Exception:
        pass

    return {
        "success": True,
        "recommended_pp": dwsim_pp,            # guaranteed DWSIM-instantiable
        "ideal_model": ideal_model,            # what theory prefers (may be Aspen-only)
        "availability_note": availability_note,
        "reason": reason,
        "accuracy_estimate": accuracy,
        "compounds_analysed": compounds,
        "pressure_bar": pressure_bar,
        "temperature_C": temperature_C,
        "flags": {
            "polar": polar, "electrolyte": electrolyte,
            "acid_gas": acid_gas, "hydrocarbon": hc,
            "high_pressure": high_press, "cryogenic": cryogenic,
        },
        "alternative_pps": _alternatives(pp),
        "ref": "Carlson (1996), Smith (2005) Ch. 3, DWSIM PP documentation",
    }


def _alternatives(pp: str) -> List[str]:
    alts = {
        "Peng-Robinson (PR)": ["SRK", "Lee-Kesler", "PR-SV"],
        "NRTL": ["UNIQUAC", "Wilson", "UNIFAC"],
        "eNRTL": ["Pitzer", "Extended UNIQUAC"],
        "Steam Tables (IAPWS-IF97)": ["IAPWS-95"],
        "UNIFAC": ["Modified UNIFAC (Dortmund)"],
    }
    return alts.get(pp, ["Peng-Robinson (PR)"])


# ══════════════════════════════════════════════════════════════════════════════
# 5. HEAT INTEGRATION TARGETS
# ══════════════════════════════════════════════════════════════════════════════

def heat_integration_targets(
    hot_streams: List[Dict],
    cold_streams: List[Dict],
    delta_T_min_C: float = 10.0,
) -> Dict[str, Any]:
    """Compute Pinch Analysis targets using Linnhoff's cascade method.

    hot_streams / cold_streams: [{name, T_in, T_out, duty_kW}]
    Returns: {Q_Hmin, Q_Cmin, T_pinch, max_energy_recovery}
    """
    # Build composite enthalpy intervals using shifted temperatures
    shift = delta_T_min_C / 2.0
    intervals = {}

    for s in hot_streams:
        T_s = min(s["T_in"], s["T_out"]) - shift
        T_t = max(s["T_in"], s["T_out"]) - shift
        q   = abs(s.get("duty_kW", 0))
        intervals.setdefault(T_s, 0); intervals.setdefault(T_t, 0)
        intervals[T_s] -= q; intervals[T_t] += q

    for s in cold_streams:
        T_s = min(s["T_in"], s["T_out"]) + shift
        T_t = max(s["T_in"], s["T_out"]) + shift
        q   = abs(s.get("duty_kW", 0))
        intervals.setdefault(T_s, 0); intervals.setdefault(T_t, 0)
        intervals[T_s] -= q; intervals[T_t] += q

    # Cascade
    temps = sorted(intervals.keys(), reverse=True)
    H = 0.0; pinch_T = None; min_H = 0.0; deficit = []
    for T in temps:
        H += intervals[T]
        deficit.append(H)
        if H < min_H:
            min_H = H; pinch_T = T + shift  # un-shift

    Q_Hmin = -min_H
    H_top = Q_Hmin
    H_total = H_top
    for T in temps:
        H_total += intervals[T]
    Q_Cmin = max(0, H_total - Q_Hmin + Q_Hmin)

    # Simpler energy recovery calc
    Q_hot_total  = sum(abs(s.get("duty_kW", 0)) for s in hot_streams)
    Q_cold_total = sum(abs(s.get("duty_kW", 0)) for s in cold_streams)
    Q_recovery   = min(Q_hot_total, Q_cold_total)
    Q_Hmin2      = max(0, Q_cold_total - Q_recovery)
    Q_Cmin2      = max(0, Q_hot_total  - Q_recovery)

    return {
        "success": True,
        "Q_H_min_kW":   round(Q_Hmin2, 1),
        "Q_C_min_kW":   round(Q_Cmin2, 1),
        "pinch_temperature_C": pinch_T,
        "max_energy_recovery_kW": round(Q_recovery, 1),
        "delta_T_min_C": delta_T_min_C,
        "n_hot_streams": len(hot_streams),
        "n_cold_streams": len(cold_streams),
        "notes": [
            f"Minimum hot utility = {Q_Hmin2:.1f} kW (steam / furnace)",
            f"Minimum cold utility = {Q_Cmin2:.1f} kW (cooling water / refrigerant)",
            f"Maximum heat recovery possible = {Q_recovery:.1f} kW",
            "Design rule: no cross-pinch heat exchange",
            "For full HEN synthesis, use synthesize_hen() tool",
        ],
        "ref": "Linnhoff & Hindmarsh (1983), Chem. Eng. Sci.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# 6. DESIGN CHECKLIST
# ══════════════════════════════════════════════════════════════════════════════

_CHECKLISTS = {
    "distillation": [
        "□ VLE data / regression verified (Tx,y diagram, check for azeotrope)",
        "□ Feed tray location optimised (q-line intersection with operating line)",
        "□ Condenser type: total vs. partial condenser",
        "□ Reboiler type: kettle vs. thermosyphon",
        "□ Column pressure set (below bubble point of bottom product)",
        "□ Foaming check (foaming factor in tray design)",
        "□ Weeping / flooding check (F-factor)",
        "□ Column startup procedure (flooding from bottom up)",
        "□ Safety relief valve on column (250% of operating pressure)",
        "□ Dead-leg piping avoided (especially for heat-sensitive materials)",
    ],
    "reactor": [
        "□ Heat of reaction calculated (ΔHrxn at reaction T)",
        "□ Adiabatic temperature rise checked (ΔTad = -ΔHrxn × X / Cp)",
        "□ Runaway potential assessed (Semenov criterion for exothermic rxn)",
        "□ By-product and side-reaction pathways identified",
        "□ Catalyst deactivation rate estimated",
        "□ Residence time distribution (for PFR: Bodenstein number > 50)",
        "□ Recycle ratio determined from mass balance",
        "□ Purge fraction set to prevent inert accumulation",
        "□ Emergency cooling / quench provision for exothermic",
        "□ HAZOP on reactor (especially over-pressure, loss of cooling)",
    ],
    "heat_exchanger": [
        "□ Clean vs. fouled U checked (fouling factor from TEMA)",
        "□ Temperature cross checked (feasibility of single shell-pass)",
        "□ Vibration analysis for shell-and-tube (HTRI / TEMA)",
        "□ Differential thermal expansion accounted for",
        "□ High-pressure side on tube side (cheaper flanges)",
        "□ Corrosive fluid on tube side (easier to replace tubes)",
        "□ Minimum approach temperature > delta_T_min",
        "□ Dead-zone free design (vertical condenser preferred for vapour space)",
        "□ Drainability (horizontal for condensing vapour)",
    ],
    "compressor": [
        "□ Surge line determined (centrifugal)",
        "□ Anti-surge control valve designed",
        "□ Suction knockout drum (liquid separation)",
        "□ Inter-cooling stages (check per-stage compression ratio < 4)",
        "□ Discharge temperature < 180°C (lube oil flash point limit)",
        "□ Mole-weight variation effect on operating point",
        "□ Seal system (dry gas seal vs. oil seal)",
        "□ Vibration monitoring (API 670)",
        "□ Emergency shutdown (ESD) trip logic",
    ],
}


def design_checklist(process_type: str) -> Dict[str, Any]:
    """Return a design checklist for a given process type."""
    key = process_type.lower().replace(" ", "_").replace("-", "_")
    for k in _CHECKLISTS:
        if k in key:
            return {
                "success": True,
                "process_type": process_type,
                "checklist": _CHECKLISTS[k],
                "n_items": len(_CHECKLISTS[k]),
                "note": "Items marked □ should be verified before issuing for construction (IFC).",
            }
    return {
        "success": False,
        "available_types": list(_CHECKLISTS.keys()),
        "error": f"No checklist for '{process_type}'",
    }
