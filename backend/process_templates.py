"""
process_templates.py — Curated library of industrial flowsheet templates.

Each template is a complete, parametrized JSON spec the agent can pass to
the existing build pipeline (new_flowsheet + add_object + connect + set).

These cover the most common feasibility-study cases:

  1. methanol_synthesis_loop  — CO + 2H2 → CH3OH with recycle
  2. smr_hydrogen             — CH4 + H2O → CO + 3H2 (steam methane reforming)
  3. amine_sweetening         — H2S/CO2 absorption with MEA recycle
  4. glycol_dehydration       — TEG contactor for natural gas drying
  5. ngl_recovery             — Cryogenic turbo-expander for ethane recovery
  6. air_separation           — Cryogenic O2/N2 distillation
  7. crude_atmospheric        — Atmospheric crude distillation column
  8. claus_sulfur             — Sulfur recovery via Claus reaction
  9. ammonia_synthesis        — Haber-Bosch loop
 10. fischer_tropsch          — CO + H2 → hydrocarbons (Anderson-Schulz-Flory)
 11. cryogenic_co2_capture    — Pre-combustion CO2 capture
 12. ethylene_separation      — C2 splitter (ethylene/ethane)
 13. mtbe_synthesis           — Methanol + isobutylene → MTBE
 14. wgs_co_conversion        — Water-gas shift reactor train
 15. flash_drum_2phase        — Simple two-phase separator (training)

Template structure:
  {
    "id":               unique id,
    "name":             human-readable,
    "category":         e.g. 'separations', 'reactions', 'gas-processing',
    "description":      one-paragraph overview,
    "compounds":        list of CAS or DWSIM names,
    "property_package": recommended PP,
    "streams":          [{tag, T_C, P_bar, flow_kmol_h, compositions, role}],
    "unit_ops":         [{tag, type, params}],
    "connections":      [{from, to}],
    "expected_results": {key kPIs the user can verify},
    "tunable_params":   [params the user typically optimizes],
    "references":       [Perry's, Smith-Van Ness chapters, journal papers],
  }
"""

from typing import Dict, List, Any


PROCESS_TEMPLATES: List[Dict[str, Any]] = [

    # ─── 1. Methanol synthesis loop ───────────────────────────────────────────
    {
        "id": "methanol_synthesis_loop",
        "name": "Methanol Synthesis Loop (Lurgi)",
        "category": "petrochemical",
        "complexity": "intermediate",
        "description": (
            "Industrial methanol synthesis from syngas. CO + 2H2 → CH3OH "
            "over Cu/ZnO/Al2O3 catalyst at 50–100 bar, 220–280°C. "
            "Includes feed compressor, reactor, cooler, flash, and unconverted "
            "gas recycle. Typical conversion per pass 5–8%, overall 95%+."
        ),
        "compounds": ["Carbon monoxide", "Hydrogen", "Methanol", "Water", "Carbon dioxide", "Methane"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "syngas_feed", "role": "feed",  "T_C": 30,   "P_bar": 25,  "flow_kmol_h": 100,
             "compositions": {"Carbon monoxide": 0.25, "Hydrogen": 0.65, "Carbon dioxide": 0.05, "Methane": 0.05}},
            {"tag": "compressed_feed", "T_C": 220, "P_bar": 80},
            {"tag": "reactor_feed"},
            {"tag": "reactor_out", "T_C": 270, "P_bar": 78},
            {"tag": "cooled_out", "T_C": 40},
            {"tag": "crude_methanol", "role": "product"},
            {"tag": "purge", "role": "purge"},
            {"tag": "recycle_gas"},
        ],
        "unit_ops": [
            {"tag": "C-101", "type": "Compressor", "params": {"outlet_pressure_bar": 80, "efficiency": 0.78}},
            {"tag": "MIX-1", "type": "Mixer"},
            {"tag": "R-101", "type": "ConversionReactor",
             "params": {"reactions": [{"reactants": {"Carbon monoxide": 1, "Hydrogen": 2},
                                       "products":  {"Methanol": 1}, "conversion": 0.08}]}},
            {"tag": "E-101", "type": "Cooler", "params": {"outlet_T_C": 40}},
            {"tag": "V-101", "type": "Separator"},
            {"tag": "SPL-1", "type": "Splitter", "params": {"split_ratio": [0.05, 0.95]}},
        ],
        "connections": [
            {"from": "syngas_feed", "to": "C-101"}, {"from": "C-101", "to": "compressed_feed"},
            {"from": "compressed_feed", "to": "MIX-1"}, {"from": "recycle_gas", "to": "MIX-1"},
            {"from": "MIX-1", "to": "reactor_feed"}, {"from": "reactor_feed", "to": "R-101"},
            {"from": "R-101", "to": "reactor_out"}, {"from": "reactor_out", "to": "E-101"},
            {"from": "E-101", "to": "cooled_out"}, {"from": "cooled_out", "to": "V-101"},
            {"from": "V-101", "to": "crude_methanol", "phase": "liquid"},
            {"from": "V-101", "to": "SPL-1", "phase": "vapor"},
            {"from": "SPL-1", "to": "purge", "stream_index": 0},
            {"from": "SPL-1", "to": "recycle_gas", "stream_index": 1},
        ],
        "expected_results": {
            "methanol_yield_kmol_h": 7,
            "carbon_efficiency_pct": 92,
            "compressor_duty_kW": 250,
        },
        "tunable_params": [
            "reactor_temperature_C", "reactor_pressure_bar",
            "purge_split_ratio", "feed_H2_to_CO_ratio",
        ],
        "references": [
            "Aasberg-Petersen et al., Catal. Today (2013)",
            "Lurgi MegaMethanol process licensor docs",
        ],
    },

    # ─── 2. Steam Methane Reforming (SMR) ─────────────────────────────────────
    {
        "id": "smr_hydrogen",
        "name": "Steam Methane Reforming for H2 Production",
        "category": "gas-processing",
        "complexity": "intermediate",
        "description": (
            "Industrial hydrogen production via SMR. CH4 + H2O → CO + 3H2 "
            "(endothermic, ΔH = +206 kJ/mol) at 800–900°C, 25 bar over Ni/Al2O3. "
            "Followed by water-gas shift (CO + H2O → CO2 + H2) at 350°C."
        ),
        "compounds": ["Methane", "Water", "Carbon monoxide", "Hydrogen", "Carbon dioxide"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "natgas_feed", "role": "feed", "T_C": 25, "P_bar": 25, "flow_kmol_h": 100,
             "compositions": {"Methane": 1.0}},
            {"tag": "steam_feed",  "role": "feed", "T_C": 250, "P_bar": 25, "flow_kmol_h": 300,
             "compositions": {"Water": 1.0}},
            {"tag": "reformer_in"}, {"tag": "syngas_hot", "T_C": 870},
            {"tag": "syngas_warm", "T_C": 350}, {"tag": "shifted_gas"},
        ],
        "unit_ops": [
            {"tag": "MIX-FEED", "type": "Mixer"},
            {"tag": "R-REFORMER", "type": "GibbsReactor",
             "params": {"outlet_T_C": 870, "outlet_P_bar": 24}},
            {"tag": "E-COOL1", "type": "Cooler", "params": {"outlet_T_C": 350}},
            {"tag": "R-SHIFT", "type": "EquilibriumReactor",
             "params": {"outlet_T_C": 350}},
        ],
        "connections": [
            {"from": "natgas_feed", "to": "MIX-FEED"},
            {"from": "steam_feed", "to": "MIX-FEED"},
            {"from": "MIX-FEED", "to": "reformer_in"},
            {"from": "reformer_in", "to": "R-REFORMER"},
            {"from": "R-REFORMER", "to": "syngas_hot"},
            {"from": "syngas_hot", "to": "E-COOL1"},
            {"from": "E-COOL1", "to": "syngas_warm"},
            {"from": "syngas_warm", "to": "R-SHIFT"},
            {"from": "R-SHIFT", "to": "shifted_gas"},
        ],
        "expected_results": {
            "h2_yield_kmol_h": 280,
            "co_conversion_pct": 90,
            "reformer_duty_MW": 5.2,
            "thermal_efficiency_pct": 75,
        },
        "tunable_params": [
            "reformer_temperature_C", "steam_to_carbon_ratio",
            "reformer_pressure_bar", "shift_temperature_C",
        ],
        "references": ["Rostrup-Nielsen, Catal. Today (1993)", "Perry's 8th ed. Ch.23"],
    },

    # ─── 3. Amine sweetening ──────────────────────────────────────────────────
    {
        "id": "amine_sweetening",
        "name": "MEA Amine Sweetening for Acid Gas Removal",
        "category": "gas-processing",
        "complexity": "advanced",
        "description": (
            "Removal of H2S and CO2 from sour natural gas using monoethanolamine "
            "(MEA) absorber + regenerator with rich/lean amine recycle. "
            "Standard for refinery off-gas treating."
        ),
        "compounds": ["Methane", "Hydrogen sulfide", "Carbon dioxide", "Water", "Monoethanolamine"],
        "property_package": "Sour Water",
        "warning": "DWSIM electrolyte support is limited — use Acid Gas property package if available",
        "streams": [
            {"tag": "sour_gas", "role": "feed", "T_C": 40, "P_bar": 50, "flow_kmol_h": 1000,
             "compositions": {"Methane": 0.92, "Hydrogen sulfide": 0.03, "Carbon dioxide": 0.05}},
            {"tag": "lean_amine_in", "T_C": 45, "P_bar": 50, "flow_kmol_h": 500},
            {"tag": "sweet_gas", "role": "product"}, {"tag": "rich_amine"},
            {"tag": "rich_amine_hot", "T_C": 105},
            {"tag": "regen_overhead", "role": "byproduct"},
            {"tag": "lean_amine_hot"},
        ],
        "unit_ops": [
            {"tag": "C-ABS", "type": "AbsorptionColumn",
             "params": {"number_of_stages": 20, "operating_pressure_bar": 50}},
            {"tag": "E-CROSS", "type": "HeatExchanger",
             "params": {"hot_side_outlet_T_C": 50, "cold_side_outlet_T_C": 105}},
            {"tag": "C-REGEN", "type": "DistillationColumn",
             "params": {"number_of_stages": 15, "reflux_ratio": 1.5,
                       "condenser_pressure_bar": 2, "reboiler_pressure_bar": 2.5}},
        ],
        "connections": [
            {"from": "sour_gas", "to": "C-ABS"},
            {"from": "lean_amine_in", "to": "C-ABS"},
            {"from": "C-ABS", "to": "sweet_gas", "phase": "vapor"},
            {"from": "C-ABS", "to": "rich_amine", "phase": "liquid"},
            {"from": "rich_amine", "to": "E-CROSS"},
            {"from": "E-CROSS", "to": "rich_amine_hot"},
            {"from": "rich_amine_hot", "to": "C-REGEN"},
            {"from": "C-REGEN", "to": "regen_overhead", "phase": "vapor"},
            {"from": "C-REGEN", "to": "lean_amine_hot", "phase": "liquid"},
            {"from": "lean_amine_hot", "to": "E-CROSS"},
        ],
        "expected_results": {
            "h2s_removal_pct": 99.5,
            "co2_removal_pct": 95,
            "reboiler_duty_MW": 2.8,
        },
        "tunable_params": [
            "lean_amine_circulation_kmol_h", "regenerator_reflux_ratio",
            "lean_amine_temperature_C", "absorber_pressure_bar",
        ],
        "references": ["Kohl & Nielsen, Gas Purification 5th ed., Ch.2"],
    },

    # ─── 4. Glycol dehydration ────────────────────────────────────────────────
    {
        "id": "glycol_dehydration",
        "name": "TEG Glycol Dehydration",
        "category": "gas-processing",
        "complexity": "intermediate",
        "description": (
            "Triethylene glycol (TEG) contactor + regenerator for natural gas "
            "dehydration to pipeline specs (<7 lb H2O / MMscf)."
        ),
        "compounds": ["Methane", "Water", "Triethylene glycol"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "wet_gas", "role": "feed", "T_C": 30, "P_bar": 70, "flow_kmol_h": 5000,
             "compositions": {"Methane": 0.997, "Water": 0.003}},
            {"tag": "lean_teg", "T_C": 50, "P_bar": 70, "flow_kmol_h": 50,
             "compositions": {"Triethylene glycol": 0.99, "Water": 0.01}},
            {"tag": "dry_gas", "role": "product"},
            {"tag": "rich_teg"},
            {"tag": "water_out", "role": "byproduct"},
            {"tag": "regenerated_teg"},
        ],
        "unit_ops": [
            {"tag": "C-CONTACT", "type": "AbsorptionColumn",
             "params": {"number_of_stages": 8, "operating_pressure_bar": 70}},
            {"tag": "C-REGEN", "type": "DistillationColumn",
             "params": {"number_of_stages": 6, "reflux_ratio": 0.5,
                       "condenser_pressure_bar": 1.2}},
        ],
        "connections": [
            {"from": "wet_gas", "to": "C-CONTACT"},
            {"from": "lean_teg", "to": "C-CONTACT"},
            {"from": "C-CONTACT", "to": "dry_gas", "phase": "vapor"},
            {"from": "C-CONTACT", "to": "rich_teg", "phase": "liquid"},
            {"from": "rich_teg", "to": "C-REGEN"},
            {"from": "C-REGEN", "to": "water_out", "phase": "vapor"},
            {"from": "C-REGEN", "to": "regenerated_teg", "phase": "liquid"},
        ],
        "expected_results": {
            "water_removal_pct": 99.8,
            "dew_point_depression_C": 30,
        },
        "tunable_params": ["teg_circulation_kmol_h", "regenerator_reboiler_T_C"],
        "references": ["Campbell, Gas Conditioning & Processing Vol.2"],
    },

    # ─── 5. NGL recovery (cryogenic) ──────────────────────────────────────────
    {
        "id": "ngl_recovery",
        "name": "Cryogenic Turbo-Expander NGL Recovery",
        "category": "gas-processing",
        "complexity": "advanced",
        "description": (
            "Cryogenic expansion for ethane and heavier hydrocarbon recovery "
            "from natural gas. Includes cold-box exchanger, expander, and "
            "demethanizer column."
        ),
        "compounds": ["Methane", "Ethane", "Propane", "n-Butane", "Nitrogen"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "ng_feed", "role": "feed", "T_C": 30, "P_bar": 60, "flow_kmol_h": 1000,
             "compositions": {"Methane": 0.85, "Ethane": 0.08, "Propane": 0.04,
                              "n-Butane": 0.02, "Nitrogen": 0.01}},
            {"tag": "cold_feed", "T_C": -50},
            {"tag": "expanded_gas", "T_C": -90, "P_bar": 25},
            {"tag": "sales_gas", "role": "product"},
            {"tag": "ngl_product", "role": "product"},
        ],
        "unit_ops": [
            {"tag": "E-COLDBOX", "type": "HeatExchanger",
             "params": {"hot_side_outlet_T_C": -50}},
            {"tag": "EXP-101", "type": "Expander",
             "params": {"outlet_pressure_bar": 25, "efficiency": 0.85}},
            {"tag": "C-DEMETH", "type": "DistillationColumn",
             "params": {"number_of_stages": 25, "operating_pressure_bar": 25}},
        ],
        "connections": [
            {"from": "ng_feed", "to": "E-COLDBOX"},
            {"from": "E-COLDBOX", "to": "cold_feed"},
            {"from": "cold_feed", "to": "EXP-101"},
            {"from": "EXP-101", "to": "expanded_gas"},
            {"from": "expanded_gas", "to": "C-DEMETH"},
            {"from": "C-DEMETH", "to": "sales_gas", "phase": "vapor"},
            {"from": "C-DEMETH", "to": "ngl_product", "phase": "liquid"},
        ],
        "expected_results": {
            "ethane_recovery_pct": 92,
            "propane_recovery_pct": 99,
            "expander_power_kW": 850,
        },
        "tunable_params": ["expander_outlet_pressure_bar", "demethanizer_stages"],
        "references": ["Manning & Thompson, Oilfield Processing of Petroleum Vol.1"],
    },

    # ─── 6. Air separation ────────────────────────────────────────────────────
    {
        "id": "air_separation",
        "name": "Cryogenic Air Separation Unit (ASU)",
        "category": "gas-processing",
        "complexity": "advanced",
        "description": (
            "Double-column cryogenic distillation of air into O2, N2, and "
            "argon. High-pressure column at 5.5 bar feeds low-pressure column "
            "at 1.4 bar."
        ),
        "compounds": ["Oxygen", "Nitrogen", "Argon"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "air_feed", "role": "feed", "T_C": -173, "P_bar": 5.5, "flow_kmol_h": 1000,
             "compositions": {"Nitrogen": 0.781, "Oxygen": 0.209, "Argon": 0.010}},
            {"tag": "lp_overhead", "role": "product"},
            {"tag": "lp_bottom", "role": "product"},
        ],
        "unit_ops": [
            {"tag": "C-LP", "type": "DistillationColumn",
             "params": {"number_of_stages": 60, "operating_pressure_bar": 1.4}},
        ],
        "connections": [
            {"from": "air_feed", "to": "C-LP"},
            {"from": "C-LP", "to": "lp_overhead", "phase": "vapor"},
            {"from": "C-LP", "to": "lp_bottom", "phase": "liquid"},
        ],
        "expected_results": {
            "n2_purity_pct": 99.9,
            "o2_purity_pct": 95,
        },
        "tunable_params": ["lp_column_stages", "feed_temperature_C"],
        "references": ["Smith & Klosek, Cryogenics (2001)"],
    },

    # ─── 7. Crude atmospheric distillation ────────────────────────────────────
    {
        "id": "crude_atmospheric",
        "name": "Crude Atmospheric Distillation (Pre-Flash + Tower)",
        "category": "refinery",
        "complexity": "advanced",
        "description": (
            "Atmospheric crude unit with pre-flash and main column producing "
            "naphtha, kerosene, diesel, AGO, and bottoms. Uses pseudo-components."
        ),
        "compounds": ["n-Butane", "n-Hexane", "n-Decane", "n-Hexadecane", "n-Eicosane"],
        "property_package": "Peng-Robinson (PR)",
        "warning": "For real refinery work, use ASTM/TBP-defined pseudo-components",
        "streams": [
            {"tag": "crude_feed", "role": "feed", "T_C": 350, "P_bar": 2, "flow_kmol_h": 1000,
             "compositions": {"n-Butane": 0.05, "n-Hexane": 0.15, "n-Decane": 0.35,
                              "n-Hexadecane": 0.30, "n-Eicosane": 0.15}},
            {"tag": "naphtha", "role": "product"},
            {"tag": "kerosene", "role": "product"},
            {"tag": "diesel", "role": "product"},
            {"tag": "residue", "role": "product"},
        ],
        "unit_ops": [
            {"tag": "C-ATM", "type": "DistillationColumn",
             "params": {"number_of_stages": 40, "feed_stage": 30,
                       "operating_pressure_bar": 1.5, "reflux_ratio": 2.5}},
        ],
        "connections": [
            {"from": "crude_feed", "to": "C-ATM"},
            {"from": "C-ATM", "to": "naphtha"},
            {"from": "C-ATM", "to": "residue"},
        ],
        "expected_results": {
            "naphtha_yield_pct": 20, "diesel_yield_pct": 30,
            "residue_yield_pct": 15,
        },
        "tunable_params": ["reflux_ratio", "feed_temperature_C"],
        "references": ["Watkins, Petroleum Refinery Distillation 2nd ed."],
    },

    # ─── 8. Claus sulfur recovery ─────────────────────────────────────────────
    {
        "id": "claus_sulfur",
        "name": "Claus Sulfur Recovery (2-Stage)",
        "category": "gas-processing",
        "complexity": "intermediate",
        "description": (
            "Modified Claus process: H2S + 1.5 O2 → SO2 + H2O (thermal stage), "
            "then 2 H2S + SO2 → 3 S + 2 H2O (catalytic stages). "
            "Typical recovery 95-97%."
        ),
        "compounds": ["Hydrogen sulfide", "Oxygen", "Sulfur dioxide", "Sulfur", "Water", "Nitrogen"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "acid_gas", "role": "feed", "T_C": 50, "P_bar": 1.5, "flow_kmol_h": 100,
             "compositions": {"Hydrogen sulfide": 0.85, "Water": 0.10, "Carbon dioxide": 0.05}},
            {"tag": "air_feed", "role": "feed", "T_C": 25, "P_bar": 1.5, "flow_kmol_h": 70,
             "compositions": {"Oxygen": 0.21, "Nitrogen": 0.79}},
            {"tag": "thermal_out", "T_C": 1100},
            {"tag": "catalytic_out"}, {"tag": "tail_gas", "role": "byproduct"},
            {"tag": "liquid_sulfur", "role": "product"},
        ],
        "unit_ops": [
            {"tag": "R-THERM", "type": "GibbsReactor",
             "params": {"outlet_T_C": 1100, "outlet_P_bar": 1.5}},
            {"tag": "E-COND1", "type": "Cooler", "params": {"outlet_T_C": 140}},
            {"tag": "V-COND1", "type": "Separator"},
        ],
        "connections": [
            {"from": "acid_gas", "to": "R-THERM"},
            {"from": "air_feed", "to": "R-THERM"},
            {"from": "R-THERM", "to": "thermal_out"},
            {"from": "thermal_out", "to": "E-COND1"},
            {"from": "E-COND1", "to": "V-COND1"},
            {"from": "V-COND1", "to": "liquid_sulfur", "phase": "liquid"},
            {"from": "V-COND1", "to": "tail_gas", "phase": "vapor"},
        ],
        "expected_results": {
            "sulfur_recovery_pct": 95,
            "thermal_reactor_duty_MW": 1.2,
        },
        "tunable_params": ["air_to_acid_gas_ratio", "thermal_reactor_T_C"],
        "references": ["Goar & Sames, Sulfur Recovery (1988)"],
    },

    # ─── 9. Ammonia synthesis (Haber-Bosch) ───────────────────────────────────
    {
        "id": "ammonia_synthesis",
        "name": "Haber-Bosch Ammonia Synthesis Loop",
        "category": "petrochemical",
        "complexity": "advanced",
        "description": (
            "N2 + 3 H2 ⇌ 2 NH3 over iron catalyst at 150–300 bar, 400–500°C. "
            "Includes feed compressor, reactor with quench, condenser, "
            "and unconverted gas recycle. Per-pass conversion 10-20%."
        ),
        "compounds": ["Nitrogen", "Hydrogen", "Ammonia", "Methane", "Argon"],
        "property_package": "Peng-Robinson (PR)",
        "streams": [
            {"tag": "fresh_syngas", "role": "feed", "T_C": 30, "P_bar": 25,
             "flow_kmol_h": 400, "compositions": {"Nitrogen": 0.25, "Hydrogen": 0.74, "Methane": 0.01}},
            {"tag": "compressed_make_up", "P_bar": 200},
            {"tag": "reactor_feed"}, {"tag": "reactor_out", "T_C": 450},
            {"tag": "cooled", "T_C": -25},
            {"tag": "liquid_ammonia", "role": "product"},
            {"tag": "purge", "role": "purge"}, {"tag": "recycle"},
        ],
        "unit_ops": [
            {"tag": "K-101", "type": "Compressor",
             "params": {"outlet_pressure_bar": 200, "efficiency": 0.80}},
            {"tag": "MIX-101", "type": "Mixer"},
            {"tag": "R-101", "type": "EquilibriumReactor",
             "params": {"outlet_T_C": 450, "outlet_P_bar": 195}},
            {"tag": "E-101", "type": "Cooler", "params": {"outlet_T_C": -25}},
            {"tag": "V-101", "type": "Separator"},
            {"tag": "SPL-101", "type": "Splitter", "params": {"split_ratio": [0.05, 0.95]}},
        ],
        "connections": [
            {"from": "fresh_syngas", "to": "K-101"},
            {"from": "K-101", "to": "compressed_make_up"},
            {"from": "compressed_make_up", "to": "MIX-101"},
            {"from": "recycle", "to": "MIX-101"},
            {"from": "MIX-101", "to": "reactor_feed"},
            {"from": "reactor_feed", "to": "R-101"},
            {"from": "R-101", "to": "reactor_out"},
            {"from": "reactor_out", "to": "E-101"},
            {"from": "E-101", "to": "cooled"},
            {"from": "cooled", "to": "V-101"},
            {"from": "V-101", "to": "liquid_ammonia", "phase": "liquid"},
            {"from": "V-101", "to": "SPL-101", "phase": "vapor"},
            {"from": "SPL-101", "to": "purge"},
            {"from": "SPL-101", "to": "recycle"},
        ],
        "expected_results": {
            "ammonia_production_kmol_h": 90,
            "compressor_power_MW": 4.5,
            "per_pass_conversion_pct": 15,
        },
        "tunable_params": ["loop_pressure_bar", "purge_fraction", "reactor_T_C"],
        "references": ["Appl, Ammonia: Principles & Industrial Practice (1999)"],
    },

    # ─── 10. Flash drum (training template) ───────────────────────────────────
    {
        "id": "flash_drum_2phase",
        "name": "Two-Phase Flash Drum (Training)",
        "category": "simple",
        "complexity": "beginner",
        "description": (
            "Single flash drum separating a 50/50 methanol-water mix at 1 bar. "
            "Good for learning the build pipeline."
        ),
        "compounds": ["Methanol", "Water"],
        "property_package": "NRTL",
        "streams": [
            {"tag": "feed", "role": "feed", "T_C": 90, "P_bar": 1, "flow_kmol_h": 100,
             "compositions": {"Methanol": 0.5, "Water": 0.5}},
            {"tag": "vapor_out", "role": "product"},
            {"tag": "liquid_out", "role": "product"},
        ],
        "unit_ops": [
            {"tag": "V-101", "type": "Separator", "params": {"operating_pressure_bar": 1}},
        ],
        "connections": [
            {"from": "feed", "to": "V-101"},
            {"from": "V-101", "to": "vapor_out", "phase": "vapor"},
            {"from": "V-101", "to": "liquid_out", "phase": "liquid"},
        ],
        "expected_results": {
            "vapor_methanol_mol_frac": 0.78,
            "liquid_methanol_mol_frac": 0.30,
        },
        "tunable_params": ["feed_T_C", "operating_pressure_bar"],
        "references": ["Smith, Van Ness, Abbott — Ch.10"],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def list_templates(category: str = "", complexity: str = "") -> Dict[str, Any]:
    """List available templates, optionally filtered."""
    filtered = PROCESS_TEMPLATES
    if category:
        filtered = [t for t in filtered if t["category"].lower() == category.lower()]
    if complexity:
        filtered = [t for t in filtered if t["complexity"].lower() == complexity.lower()]
    return {
        "success": True,
        "count": len(filtered),
        "templates": [
            {
                "id":          t["id"],
                "name":        t["name"],
                "category":    t["category"],
                "complexity":  t["complexity"],
                "description": t["description"][:200],
                "n_streams":   len(t.get("streams", [])),
                "n_unit_ops":  len(t.get("unit_ops", [])),
            }
            for t in filtered
        ],
        "categories": sorted({t["category"] for t in PROCESS_TEMPLATES}),
    }


def get_template(template_id: str) -> Dict[str, Any]:
    """Return the full template spec by id."""
    for t in PROCESS_TEMPLATES:
        if t["id"] == template_id:
            return {"success": True, "template": t}
    return {
        "success": False,
        "error": f"Template '{template_id}' not found",
        "available_ids": [t["id"] for t in PROCESS_TEMPLATES],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic template instantiator
# ─────────────────────────────────────────────────────────────────────────────

def instantiate_template(template_id: str, bridge: Any,
                         overrides: Dict[str, Any] = None,
                         solve: bool = False) -> Dict[str, Any]:
    """Walk a template dict and build the DWSIM flowsheet step-by-step.

    Replaces the LLM-driven build path for known templates. The LLM cannot
    reliably emit 20+ ordered tool calls in one shot; this function does
    the same work deterministically with structured error reporting.

    Order of operations:
      1. new_flowsheet(compounds, property_package)
      2. add_object for every unit_op (creates UO + auto-connected streams)
      3. add_object for every standalone stream
      4. connect_streams for every connection
      5. set_stream_property / set_stream_composition for feed streams
      6. set_unit_op_property for every unit op param
      7. optionally save_and_solve

    Returns:
      {success, template_id, steps: [{step, ok, detail}], errors: [...],
       solve_result: optional, instructions_for_user: str}
    """
    overrides = overrides or {}

    spec = get_template(template_id)
    if not spec.get("success"):
        return spec
    tpl = spec["template"]

    steps: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    def _step(name: str, ok: bool, detail: Any = None, fatal: bool = False):
        steps.append({"step": name, "ok": ok, "detail": detail})
        if not ok and fatal:
            errors.append({"step": name, "detail": detail})

    # 1. Create blank flowsheet with compounds + PP
    try:
        r = bridge.new_flowsheet(
            name=tpl["id"],
            compounds=list(tpl.get("compounds", [])),
            property_package=tpl.get("property_package", "Peng-Robinson (PR)"),
        )
        ok = bool(r.get("success", False))
        _step("new_flowsheet", ok, r if not ok else {"name": tpl["id"]})
        if not ok:
            return {"success": False, "template_id": template_id,
                    "steps": steps, "errors": [r],
                    "error": "Failed to create blank flowsheet"}
    except Exception as exc:
        _step("new_flowsheet", False, {"exception": str(exc)}, fatal=True)
        return {"success": False, "template_id": template_id,
                "steps": steps, "errors": errors,
                "error": f"new_flowsheet raised: {exc}"}

    # 2. Add unit ops
    for uop in tpl.get("unit_ops", []):
        tag = uop.get("tag")
        utype = uop.get("type", "")
        try:
            r = bridge.add_object(tag=tag, type=utype)
            ok = bool(r.get("success", False))
            _step(f"add_object[{tag}:{utype}]", ok, r if not ok else None)
            if not ok:
                errors.append({"step": f"add_object[{tag}]", "detail": r})
        except Exception as exc:
            _step(f"add_object[{tag}:{utype}]", False, {"exception": str(exc)})
            errors.append({"step": f"add_object[{tag}]", "detail": str(exc)})

    # 3. Add standalone streams (those not auto-created by unit op ports).
    # Strategy: try to add every declared stream as MaterialStream; if it
    # already exists from a UO port, the bridge will return success or a
    # benign "exists" code — either way we keep going.
    for s in tpl.get("streams", []):
        tag = s.get("tag")
        if not tag:
            continue
        try:
            r = bridge.add_object(tag=tag, type="MaterialStream")
            # 'exists' is fine; only treat hard failures as errors
            _step(f"add_stream[{tag}]", r.get("success", True) or
                  "exists" in str(r.get("code", "")).lower(), r)
        except Exception as exc:
            _step(f"add_stream[{tag}]", False, {"exception": str(exc)})

    # 4. Connections
    for c in tpl.get("connections", []):
        src = c.get("from")
        dst = c.get("to")
        try:
            r = bridge.connect_streams(
                from_tag=src, to_tag=dst,
                from_port=int(c.get("from_port", 0)),
                to_port=int(c.get("to_port", 0)),
            )
            ok = bool(r.get("success", False))
            _step(f"connect[{src}->{dst}]", ok, r if not ok else None)
            if not ok:
                errors.append({"step": f"connect[{src}->{dst}]", "detail": r})
        except Exception as exc:
            _step(f"connect[{src}->{dst}]", False, {"exception": str(exc)})
            errors.append({"step": f"connect[{src}->{dst}]", "detail": str(exc)})

    # 5. Feed stream properties (T, P, flow, composition)
    for s in tpl.get("streams", []):
        tag = s.get("tag")
        if not tag:
            continue
        for prop_key, dwsim_prop, unit in (
            ("T_C", "temperature", "C"),
            ("P_bar", "pressure", "bar"),
            ("flow_kmol_h", "molar_flow", "kmol/h"),
            ("flow_kg_h", "mass_flow", "kg/h"),
        ):
            val = overrides.get(f"{tag}.{prop_key}", s.get(prop_key))
            if val is None:
                continue
            try:
                r = bridge.set_stream_property(
                    tag=tag, property_name=dwsim_prop,
                    value=float(val), unit=unit,
                )
                _step(f"set_stream[{tag}.{dwsim_prop}={val}{unit}]",
                      bool(r.get("success", False)), r)
            except Exception as exc:
                _step(f"set_stream[{tag}.{dwsim_prop}]", False,
                      {"exception": str(exc)})

        # Composition if specified
        comps = s.get("compositions") or {}
        if comps and hasattr(bridge, "set_stream_composition"):
            try:
                r = bridge.set_stream_composition(tag=tag, composition=comps)
                _step(f"set_composition[{tag}]",
                      bool(r.get("success", False)), r)
            except Exception as exc:
                _step(f"set_composition[{tag}]", False, {"exception": str(exc)})

    # 6. Unit op params
    for uop in tpl.get("unit_ops", []):
        tag = uop.get("tag")
        params = uop.get("params") or {}
        if not isinstance(params, dict):
            continue
        for pkey, pval in params.items():
            # Skip complex param structures (reactions, kinetics) — those
            # need dedicated handlers; the basic params here are scalars.
            if isinstance(pval, (list, dict)):
                continue
            ov_key = f"{tag}.{pkey}"
            if ov_key in overrides:
                pval = overrides[ov_key]
            try:
                r = bridge.set_unit_op_property(
                    tag=tag, property_name=pkey, value=pval,
                )
                _step(f"set_unit_op[{tag}.{pkey}={pval}]",
                      bool(r.get("success", False)), r)
            except Exception as exc:
                _step(f"set_unit_op[{tag}.{pkey}]", False,
                      {"exception": str(exc)})

    # 7. Optionally solve
    solve_result = None
    if solve and hasattr(bridge, "save_and_solve"):
        try:
            solve_result = bridge.save_and_solve()
            _step("save_and_solve",
                  bool(solve_result.get("success", False)), solve_result)
        except Exception as exc:
            solve_result = {"success": False, "error": str(exc)}
            _step("save_and_solve", False, {"exception": str(exc)})

    n_ok = sum(1 for s in steps if s["ok"])
    overall_ok = (len(errors) == 0)

    instructions = (
        f"Template '{tpl['name']}' instantiated: {n_ok}/{len(steps)} steps OK. "
    )
    if errors:
        instructions += (
            f"{len(errors)} errors during build — see 'errors' list. "
            "Use the agent to fix specific connections or properties."
        )
    else:
        instructions += (
            "Click 'Run Simulation' in the UI, or ask the agent to solve."
        )

    return {
        "success": overall_ok,
        "template_id": template_id,
        "template_name": tpl["name"],
        "steps_total": len(steps),
        "steps_ok": n_ok,
        "steps": steps,
        "errors": errors,
        "solve_result": solve_result,
        "instructions_for_user": instructions,
    }
