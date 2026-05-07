"""
knowledge_base.py  -  Chemical Engineering RAG Knowledge Base
─────────────────────────────────────────────────────────────
Curated chemical engineering knowledge with TF-IDF retrieval.
No external dependencies (pure Python).

The agent calls search_knowledge("query") to find relevant
engineering principles, design heuristics, and troubleshooting
tips before making decisions.

Sources cited:
  - Smith, Van Ness & Abbott: Intro to Chemical Engineering Thermodynamics
  - Perry's Chemical Engineers' Handbook (9th ed.)
  - Seider, Seader, Lewin: Product and Process Design Principles
  - Turton et al.: Analysis, Synthesis and Design of Chemical Processes
  - Coulson & Richardson: Chemical Engineering Design (Vol. 6)
  - McCabe, Smith & Harriott: Unit Operations of Chemical Engineering
  - Fogler: Elements of Chemical Reaction Engineering
  - Moran & Shapiro: Fundamentals of Engineering Thermodynamics
  - IEA Hydrogen Reports; IRENA Renewable Power Reports
  - DWSIM Documentation & Forums
"""

import math
import os
import re
from collections import Counter
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════
# KNOWLEDGE CHUNKS
# ═══════════════════════════════════════════════════════════

KNOWLEDGE_CHUNKS: List[Dict] = [

    # ── Thermodynamic Property Packages ────────────────────

    {
        "id": "thermo_pr",
        "title": "Peng-Robinson (PR) Equation of State",
        "text": (
            "The Peng-Robinson EOS is the most widely used cubic equation of state "
            "for hydrocarbon systems. It handles vapor-liquid equilibrium well for "
            "non-polar and slightly polar compounds. Best suited for: natural gas, "
            "refinery processes, petrochemical systems. Parameters: Tc, Pc, and "
            "acentric factor (omega). Limitations: poor for highly polar compounds "
            "(water, alcohols, acids) and electrolyte solutions. Accuracy degrades "
            "at very high pressures (>100 bar) or near the critical point. "
            "Use Peng-Robinson with classical mixing rules for mixtures of "
            "hydrocarbons, N2, CO2, H2S. For hydrocarbon-water systems, consider "
            "PR with Huron-Vidal mixing rules or switch to CPA."
        ),
        "tags": ["Peng-Robinson", "PR", "EOS", "equation of state", "hydrocarbons",
                 "thermodynamics", "property package", "VLE"],
        "source": "Smith, Van Ness & Abbott, Ch. 3; Perry's 9th ed., Sec. 4",
    },
    {
        "id": "thermo_srk",
        "title": "Soave-Redlich-Kwong (SRK) Equation of State",
        "text": (
            "SRK is a cubic EOS similar to Peng-Robinson, with slightly different "
            "alpha function and volume translation. Preferred for gas processing, "
            "natural gas systems, and light hydrocarbons (C1-C10). Often used in "
            "refinery and gas plant simulations. Gives comparable accuracy to PR "
            "for VLE but can differ in liquid density predictions. SRK typically "
            "overestimates liquid molar volumes by 5-15%. For better liquid density, "
            "use volume-translated SRK. In DWSIM, SRK is available as "
            "'Soave-Redlich-Kwong (SRK)'. Choose between PR and SRK based on "
            "which has better experimental data fit for your specific system."
        ),
        "tags": ["SRK", "Soave-Redlich-Kwong", "EOS", "gas processing",
                 "hydrocarbons", "property package"],
        "source": "Perry's 9th ed., Sec. 4; Soave (1972) Chem. Eng. Sci.",
    },
    {
        "id": "thermo_nrtl",
        "title": "NRTL Activity Coefficient Model",
        "text": (
            "NRTL (Non-Random Two-Liquid) is an activity coefficient model for "
            "liquid phase non-ideality. Essential for: polar/non-ideal liquid "
            "mixtures, organic-water systems, alcohol-water, ester-acid systems. "
            "Handles both VLE and LLE (liquid-liquid equilibrium). Requires binary "
            "interaction parameters (BIPs) fitted to experimental data. DWSIM has "
            "a built-in BIP database for many common pairs. For missing BIPs, use "
            "UNIFAC to estimate. NRTL uses 3 parameters per binary pair: tau12, "
            "tau21, alpha12. The non-randomness parameter alpha is typically 0.2-0.47. "
            "Common value: alpha=0.3 for most systems. For VLE calculations with "
            "NRTL, the vapor phase is typically assumed ideal or uses a simple EOS "
            "correction. Best choice for distillation of polar mixtures."
        ),
        "tags": ["NRTL", "activity coefficient", "polar", "non-ideal",
                 "liquid-liquid", "VLE", "LLE", "distillation", "water-organic"],
        "source": "Renon & Prausnitz (1968); Perry's 9th ed., Sec. 4",
    },
    {
        "id": "thermo_uniquac",
        "title": "UNIQUAC Model",
        "text": (
            "UNIQUAC (Universal Quasi-Chemical) is an activity coefficient model "
            "that accounts for molecular size and shape differences using structural "
            "parameters r (volume) and q (surface area). Better than NRTL for "
            "systems with large molecular size differences (e.g., polymer-solvent). "
            "Handles VLE and LLE. Preferred for: mixtures of molecules with "
            "very different sizes, liquid-liquid extraction systems, and when "
            "NRTL parameters are unavailable. UNIQUAC forms the basis for the "
            "UNIFAC group contribution method. In DWSIM, select 'UNIQUAC' for "
            "liquid-liquid equilibrium calculations or systems with significant "
            "size asymmetry. Requires 2 binary interaction parameters per pair "
            "(fewer than NRTL's 3), making it more suitable when limited experimental "
            "data is available."
        ),
        "tags": ["UNIQUAC", "activity coefficient", "LLE", "liquid-liquid",
                 "polymer", "extraction", "size asymmetry"],
        "source": "Abrams & Prausnitz (1975); Perry's 9th ed., Sec. 4",
    },
    {
        "id": "thermo_steam",
        "title": "Steam Tables (IAPWS-IF97)",
        "text": (
            "The IAPWS-IF97 steam tables provide highly accurate thermodynamic "
            "properties for pure water and steam across all industrially relevant "
            "conditions. Use ONLY for pure water/steam systems: boilers, condensers, "
            "turbines, steam heating/cooling. Covers: 0-800C, 0-100 MPa. "
            "Provides: enthalpy, entropy, specific volume, Cp, viscosity, thermal "
            "conductivity. Do NOT use for water mixtures with other components "
            "- use NRTL, UNIQUAC, or CPA instead. In DWSIM, select "
            "'Steam Tables (IAPWS-IF97)' for pure water heating, cooling, or "
            "phase change calculations. These tables are the most accurate option "
            "for pure water systems, far more precise than any equation of state."
        ),
        "tags": ["steam tables", "IAPWS", "water", "steam", "boiler",
                 "pure water", "phase change"],
        "source": "IAPWS-IF97; Wagner & Kretzschmar (2008)",
    },
    {
        "id": "thermo_selection",
        "title": "Property Package Selection Guide",
        "text": (
            "Quick selection guide for thermodynamic models in DWSIM: "
            "1) Hydrocarbons (non-polar): Use Peng-Robinson or SRK. "
            "2) Polar organics + water (VLE): Use NRTL with BIPs. "
            "3) Polar organics + water (LLE): Use NRTL or UNIQUAC. "
            "4) Pure water/steam: Use Steam Tables (IAPWS-IF97). "
            "5) Ideal gas mixtures at low P: Use Raoult's Law or ideal. "
            "6) Electrolyte solutions: Use NRTL-Electrolyte or e-NRTL. "
            "7) Polymer solutions: Use UNIQUAC or Flory-Huggins. "
            "8) High-pressure systems: Use PR or SRK with appropriate mixing rules. "
            "9) Cryogenic systems: Use PR with kij tuning. "
            "10) Unknown system: Start with Peng-Robinson; if results are poor "
            "for polar components, switch to NRTL. Always validate against "
            "experimental data when available. Check DWSIM's BIP database "
            "before choosing NRTL/UNIQUAC to ensure parameters are available."
        ),
        "tags": ["selection guide", "property package", "thermodynamics",
                 "which model", "choosing", "recommendation"],
        "source": "Seider, Seader & Lewin, Ch. 2; Turton et al., Ch. 4",
    },

    # ── Heat Exchangers ────────────────────────────────────

    {
        "id": "hx_design",
        "title": "Heat Exchanger Design Principles",
        "text": (
            "Heat exchanger design is governed by Q = U * A * LMTD, where "
            "Q is duty (kW), U is overall heat transfer coefficient (W/m2-K), "
            "A is heat transfer area (m2), and LMTD is log-mean temperature "
            "difference. Typical U values: water-water 800-1500, gas-gas 10-50, "
            "condensing steam-water 1000-4000, organic liquid-water 300-700 W/m2-K. "
            "Design rules: minimum approach temperature (pinch) 10-20C for liquid-liquid, "
            "5-10C for refrigeration. Shell-and-tube is most common. "
            "Counter-current flow gives higher LMTD than co-current. "
            "Fouling factors: clean water 0.0002, cooling tower water 0.0004, "
            "light hydrocarbons 0.0002, heavy oil 0.0009 m2-K/W. "
            "In DWSIM, use the Heater/Cooler for simple duty specification or "
            "Shell-and-Tube Heat Exchanger for detailed design with U and A."
        ),
        "tags": ["heat exchanger", "HX", "LMTD", "duty", "area",
                 "overall coefficient", "U value", "design", "fouling"],
        "source": "Perry's 9th ed., Sec. 11; Coulson & Richardson Vol. 6",
    },
    {
        "id": "hx_pinch",
        "title": "Pinch Analysis and Heat Integration",
        "text": (
            "Pinch analysis identifies the minimum energy requirement for a process. "
            "The pinch point is the temperature where hot and cold composite curves "
            "are closest. Rules: never transfer heat across the pinch, never use "
            "external cooling above the pinch, never use external heating below. "
            "Minimum approach temperature (delta-T-min): 10-20C for most processes, "
            "3-5C for cryogenic, 20-40C for high-temperature furnaces. "
            "Heat integration can reduce energy costs by 20-40% in typical chemical "
            "plants. In DWSIM, model heat integration by connecting multiple heat "
            "exchangers: use hot process streams to preheat cold feed streams. "
            "Start by identifying all hot streams (need cooling) and cold streams "
            "(need heating), then match them based on temperature levels."
        ),
        "tags": ["pinch analysis", "heat integration", "energy", "minimum approach",
                 "composite curve", "delta T min", "energy saving"],
        "source": "Linnhoff et al.: User Guide on Process Integration; Smith (2005)",
    },

    # ── Reactors ───────────────────────────────────────────

    {
        "id": "reactor_types",
        "title": "Reactor Type Selection",
        "text": (
            "Reactor selection depends on reaction kinetics, phases, and throughput. "
            "Conversion reactor: specify fractional conversion directly, useful for "
            "preliminary design when kinetics are unknown. "
            "Equilibrium reactor: calculates equilibrium composition based on "
            "thermodynamics (minimizes Gibbs energy). Good for fast, reversible reactions. "
            "Gibbs reactor: rigorous Gibbs energy minimization, handles multiple "
            "reactions and phases simultaneously. Best for combustion, reforming. "
            "CSTR: continuous stirred-tank, well-mixed, operates at exit conditions. "
            "Good for liquid-phase reactions. Design equation: V = F_A0 * X / (-r_A). "
            "PFR: plug flow, no axial mixing, concentration varies along length. "
            "Good for gas-phase reactions. Higher conversion than CSTR for same volume "
            "(for positive-order kinetics). "
            "In DWSIM: use Conversion Reactor for simple problems, Gibbs Reactor "
            "for combustion/equilibrium, CSTR/PFR for kinetic studies."
        ),
        "tags": ["reactor", "CSTR", "PFR", "conversion", "equilibrium",
                 "Gibbs", "reactor selection", "kinetics", "design"],
        "source": "Fogler: Elements of Chemical Reaction Engineering, 6th ed.",
    },
    {
        "id": "reactor_conversion",
        "title": "Reactor Conversion and Yield",
        "text": (
            "Conversion X = (moles reacted) / (moles fed) of limiting reactant. "
            "Selectivity S = (moles desired product) / (moles of all products). "
            "Yield Y = X * S = (moles desired product) / (moles fed). "
            "For series reactions A->B->C: maximize intermediate B by optimizing "
            "residence time (PFR better than CSTR). For parallel reactions: "
            "selectivity depends on activation energies and concentrations. "
            "Higher temperature favors the reaction with higher activation energy. "
            "Higher concentration favors the reaction with higher order. "
            "Adiabatic temperature rise: delta_T = (-delta_H_rxn * X * F_A0) / "
            "(sum(F_i * Cp_i)). For exothermic reactions, check for thermal runaway. "
            "In DWSIM, set conversion directly in Conversion Reactor or let "
            "Gibbs/Equilibrium reactor calculate it from thermodynamics."
        ),
        "tags": ["conversion", "selectivity", "yield", "reaction", "kinetics",
                 "adiabatic", "temperature rise", "residence time"],
        "source": "Fogler, Ch. 5-8; Levenspiel: Chemical Reaction Engineering",
    },

    # ── Distillation ───────────────────────────────────────

    {
        "id": "distill_design",
        "title": "Distillation Column Design",
        "text": (
            "Distillation separates components by relative volatility (alpha). "
            "Rule of thumb: alpha > 1.2 for practical distillation. "
            "Minimum stages: Fenske equation N_min = ln(separation) / ln(alpha). "
            "Minimum reflux: Underwood equation. Actual reflux typically 1.1-1.5 * R_min. "
            "Actual stages = N_min / efficiency. Tray efficiency 50-80% for most systems. "
            "Feed stage: Kirkbride equation or trial-and-error. Feed should enter "
            "near the stage with matching composition. "
            "Column pressure: operate at lowest practical pressure to maximize "
            "relative volatility (but ensure condenser temperature > cooling water). "
            "In DWSIM, the Distillation Column requires: number of stages, "
            "feed stage location, condenser/reboiler specs, reflux ratio or "
            "distillate rate. Start with shortcut column for initial estimates, "
            "then use rigorous column for detailed design."
        ),
        "tags": ["distillation", "column", "reflux ratio", "stages", "Fenske",
                 "relative volatility", "separation", "design"],
        "source": "Seider, Seader & Lewin, Ch. 8; Perry's 9th ed., Sec. 13",
    },

    # ── Flash Separation ───────────────────────────────────

    {
        "id": "flash_design",
        "title": "Flash Separation (VLE Flash)",
        "text": (
            "Flash separation exploits vapor-liquid equilibrium by reducing "
            "pressure or changing temperature. Types: isothermal flash (specify T), "
            "adiabatic flash (isenthalpic), and specified vapor fraction flash. "
            "Rachford-Rice equation solves for vapor fraction: "
            "sum(zi*(Ki-1)/(1+V*(Ki-1))) = 0, where Ki = yi/xi. "
            "Flash drums typically operate at 50-80% of feed pressure for good "
            "separation. Liquid residence time: 5-10 minutes. Vapor velocity "
            "limited by entrainment: u_max = K * sqrt((rho_L - rho_V)/rho_V), "
            "where K = 0.04-0.07 m/s typically. "
            "In DWSIM, the Separator (flash drum) calculates VLE automatically. "
            "Connect a feed stream, and two outlet streams (vapor and liquid). "
            "Specify feed conditions (T, P, composition) and the separator will "
            "calculate the equilibrium split."
        ),
        "tags": ["flash", "separator", "VLE", "vapor-liquid", "flash drum",
                 "Rachford-Rice", "phase split", "equilibrium"],
        "source": "Smith, Van Ness & Abbott, Ch. 10; Perry's 9th ed., Sec. 14",
    },

    # ── Pumps & Compressors ────────────────────────────────

    {
        "id": "pump_design",
        "title": "Pump Design and Selection",
        "text": (
            "Centrifugal pumps: most common, for flows 1-5000 m3/h, heads up to "
            "150 m. Positive displacement pumps: for high viscosity, low flow, "
            "high pressure. Pump power: P = Q * delta_P / efficiency. "
            "Typical efficiencies: centrifugal 60-80%, positive displacement 80-95%. "
            "NPSH (Net Positive Suction Head): ensure NPSH_available > NPSH_required "
            "to avoid cavitation. Rule of thumb: 1-2 m margin. "
            "Suction pressure must exceed vapor pressure of the liquid. "
            "In DWSIM, specify either outlet pressure or pressure increase (delta P). "
            "Adiabatic efficiency is the key parameter (typically 0.65-0.80). "
            "The pump calculates outlet temperature, power consumption, and duty."
        ),
        "tags": ["pump", "centrifugal", "NPSH", "cavitation", "head",
                 "efficiency", "pressure increase", "power"],
        "source": "Perry's 9th ed., Sec. 10; Coulson & Richardson Vol. 6",
    },
    {
        "id": "compressor_design",
        "title": "Compressor Design",
        "text": (
            "Compressors increase gas pressure. Types: centrifugal (high flow, "
            "moderate ratio), axial (very high flow), reciprocating (high ratio, "
            "low flow), screw (moderate). Maximum compression ratio per stage: "
            "3-4 for centrifugal, 5-6 for reciprocating. Above this, use multi-stage "
            "with intercooling. Ideal (isentropic) work: W_s = Cp*T1*((P2/P1)^((k-1)/k) - 1), "
            "where k = Cp/Cv. Adiabatic outlet temperature: T2 = T1*(P2/P1)^((k-1)/k). "
            "Polytropic efficiency: 70-85% for centrifugal. "
            "In DWSIM, specify outlet pressure and adiabatic efficiency. "
            "For multi-stage, use multiple compressors with coolers between stages. "
            "Check that discharge temperature doesn't exceed material limits (typically < 200C)."
        ),
        "tags": ["compressor", "compression", "multi-stage", "intercooling",
                 "isentropic", "polytropic", "efficiency", "gas"],
        "source": "Perry's 9th ed., Sec. 10; Turton et al., Ch. 11",
    },

    # ── Valves & Pipes ─────────────────────────────────────

    {
        "id": "valve_design",
        "title": "Valve Sizing and Pressure Drop",
        "text": (
            "Control valves regulate flow by varying pressure drop. "
            "Cv = Q * sqrt(SG / delta_P) for liquids (Q in gpm, delta_P in psi). "
            "Rule of thumb: valve delta_P should be 30-50% of total system pressure drop "
            "for good controllability. Valve characteristics: linear, equal-percentage, "
            "quick-opening. Equal-percentage most common for process control. "
            "In DWSIM, the Valve model calculates the isenthalpic flash at outlet "
            "pressure. Specify outlet pressure or pressure drop. "
            "Joule-Thomson cooling occurs when gas expands through a valve "
            "(important for natural gas systems). For liquids, flash vaporization "
            "may occur if outlet P < bubble point (cavitation risk). "
            "Ensure valve downstream pressure exceeds fluid vapor pressure."
        ),
        "tags": ["valve", "Cv", "pressure drop", "control valve",
                 "Joule-Thomson", "flash", "throttling"],
        "source": "Perry's 9th ed., Sec. 8; ISA Handbook of Control Valves",
    },

    # ── Process Safety ─────────────────────────────────────

    {
        "id": "safety_basics",
        "title": "Process Safety Fundamentals",
        "text": (
            "Key safety considerations in chemical process design: "
            "1) Flammability: check flash point, autoignition temperature, "
            "flammability limits (LEL/UEL) of all components. "
            "2) Toxicity: review MSDS/SDS, exposure limits (TLV-TWA, PEL). "
            "3) Thermal stability: runaway reaction potential for exothermic systems. "
            "4) Pressure relief: size relief valves for all credible overpressure "
            "scenarios (fire, blocked outlet, runaway reaction). "
            "5) Inherent safety: minimize inventory, substitute less hazardous "
            "materials, moderate conditions (lower T, P), simplify. "
            "6) HAZOP: systematic review of deviations (more, less, no, reverse, "
            "other) from design intent for each process parameter. "
            "In DWSIM simulations, always check that operating conditions are "
            "within safe material limits and that relief paths are sized correctly."
        ),
        "tags": ["safety", "HAZOP", "relief valve", "flammability",
                 "toxicity", "runaway", "inherent safety"],
        "source": "Crowl & Louvar: Chemical Process Safety; CCPS Guidelines",
    },

    # ── Convergence & Troubleshooting ──────────────────────

    {
        "id": "converge_tips",
        "title": "Simulation Convergence Troubleshooting",
        "text": (
            "Common convergence issues in DWSIM and how to fix them: "
            "1) Flash calculation fails: check that T and P are physically consistent "
            "(not above critical point for light gases, not below triple point). "
            "2) Column doesn't converge: try increasing number of stages, adjusting "
            "reflux ratio, or providing better initial estimates. Reduce number of "
            "stages to simplify, verify condenser/reboiler specs are feasible. "
            "3) Recycle loop doesn't converge: provide good initial guesses for "
            "tear streams (composition, T, P, flow rate close to expected values). "
            "Reduce damping factor. Use direct substitution for simple recycles. "
            "4) Negative temperatures or pressures: check unit conversions "
            "(K vs C, Pa vs bar). In DWSIM, internal units are SI (K, Pa, mol/s). "
            "5) Property package mismatch: ensure all species have parameters "
            "in the selected model. Check BIP availability for NRTL/UNIQUAC."
        ),
        "tags": ["convergence", "troubleshooting", "debug", "flash fails",
                 "column convergence", "recycle", "error", "DWSIM tips"],
        "source": "DWSIM Documentation; Process Simulation Best Practices",
    },
    {
        "id": "dwsim_tips",
        "title": "DWSIM-Specific Tips and Best Practices",
        "text": (
            "DWSIM workflow best practices: "
            "1) Always fully specify feed streams: temperature, pressure, flow rate, "
            "AND composition (mole fractions must sum to 1.0). "
            "2) Internal units are SI: temperature in K, pressure in Pa, "
            "molar flow in mol/s, mass flow in kg/s. Set properties using the "
            "appropriate unit parameter to avoid conversion errors. "
            "3) Run simulation after every parameter change. "
            "4) For multi-component systems, use the DWSIM compound database "
            "to search for exact compound names (case-sensitive). "
            "5) Energy streams must connect to unit operations that require energy "
            "(heaters, coolers, pumps, compressors). "
            "6) For recycle loops, place a Recycle block and provide initial "
            "estimates close to expected values. "
            "7) Save flowsheets frequently. Use 'save_flowsheet' after successful runs. "
            "8) Check convergence after every simulation - unconverged streams "
            "give invalid property values."
        ),
        "tags": ["DWSIM", "best practices", "tips", "workflow", "units",
                 "specification", "feed", "simulation"],
        "source": "DWSIM Documentation v8",
    },

    # ── Mass & Energy Balances ─────────────────────────────

    {
        "id": "mass_balance",
        "title": "Mass and Energy Balance Principles",
        "text": (
            "Conservation laws are the foundation of process simulation: "
            "Mass balance: input - output + generation - consumption = accumulation. "
            "At steady state, accumulation = 0. For non-reactive systems, "
            "total mass in = total mass out. For reactive systems, elements are "
            "conserved (not molecular species). "
            "Energy balance: Q - W_s = delta_H (enthalpy change). For adiabatic "
            "systems, Q = 0. For isobaric systems, delta_H = sum(F_i * Cp_i * delta_T) + "
            "sum(F_i * delta_H_vap) for phase changes. "
            "Always verify mass and energy balances after simulation to ensure "
            "consistency. Typical closure tolerance: <0.01% for mass, <0.1% for energy. "
            "In DWSIM, compare total inlet and outlet mass flows to verify closure."
        ),
        "tags": ["mass balance", "energy balance", "conservation",
                 "steady state", "enthalpy", "verification"],
        "source": "Felder & Rousseau: Elementary Principles of Chemical Processes",
    },

    # ── Mixing & Splitting ─────────────────────────────────

    {
        "id": "mixer_splitter",
        "title": "Mixers and Splitters",
        "text": (
            "Mixer: combines multiple input streams into one outlet stream. "
            "Outlet enthalpy is the sum of inlet enthalpies (energy balance). "
            "All input streams must be at the same pressure (or very close). "
            "If pressure differs, add valves upstream to equalize. "
            "Splitter: divides one inlet into multiple outlets with the SAME "
            "composition, temperature, and pressure. Only the flow rates differ. "
            "Specify split ratios (fractions summing to 1.0). "
            "Component splitter: separates specific components to different outlets "
            "(e.g., membrane separation). Specify separation factors per component. "
            "In DWSIM: Mixer requires all inputs at same pressure. Splitter needs "
            "split ratio specification. Use Component Separator for component-specific "
            "separation (e.g., removing water from organic stream)."
        ),
        "tags": ["mixer", "splitter", "mixing", "splitting", "component separator",
                 "split ratio", "pressure equalization"],
        "source": "Perry's 9th ed.; Seider, Seader & Lewin",
    },

    # ── Process Economics ──────────────────────────────────

    {
        "id": "cost_estimation",
        "title": "Cost Estimation Rules of Thumb",
        "text": (
            "Quick capital cost estimation for chemical equipment (2024 USD): "
            "Heat exchangers: $500-2000/m2 of area. Shell-and-tube typical. "
            "Pumps: $5,000-50,000 depending on size and material. "
            "Compressors: $5,000-10,000/kW of shaft power. "
            "Distillation columns: $50,000 base + $5,000/tray. "
            "Reactors: $10,000-100,000 depending on volume and pressure rating. "
            "Storage tanks: $500-1,500/m3 for atmospheric. "
            "Scaling: C2 = C1 * (S2/S1)^n, where n = 0.6 typically (six-tenths rule). "
            "Installed cost = purchased cost * installation factor (typically 3-5x). "
            "Annual operating cost: utilities + raw materials + labor + maintenance. "
            "Utility costs: steam $10-15/ton, cooling water $0.05-0.15/m3, "
            "electricity $0.05-0.15/kWh. "
            "Use these estimates for preliminary economic evaluation."
        ),
        "tags": ["cost", "economics", "capital", "operating", "estimation",
                 "six-tenths rule", "installed cost", "utilities"],
        "source": "Turton et al., Ch. 5; Peters & Timmerhaus",
    },

    # ── Phase Behavior ─────────────────────────────────────

    {
        "id": "phase_behavior",
        "title": "Phase Behavior and Phase Diagrams",
        "text": (
            "Understanding phase behavior is critical for process design: "
            "Bubble point: temperature/pressure where first vapor bubble forms. "
            "Dew point: temperature/pressure where first liquid drop forms. "
            "Critical point: above this T and P, no distinct liquid/vapor phases. "
            "For mixtures, phase envelopes show the two-phase region. "
            "Cricondenbar: maximum pressure on phase envelope. "
            "Cricondentherm: maximum temperature on phase envelope. "
            "Retrograde condensation: liquid forms when pressure decreases (common "
            "in natural gas systems near cricondenbar). "
            "In DWSIM, always check vapor fraction after flash calculations. "
            "Vapor fraction 0 = all liquid, 1 = all vapor, 0-1 = two-phase. "
            "Avoid operating near the critical point where small changes in T or P "
            "cause large density changes (poor controllability)."
        ),
        "tags": ["phase", "bubble point", "dew point", "critical point",
                 "phase envelope", "retrograde", "two-phase", "vapor fraction"],
        "source": "Smith, Van Ness & Abbott, Ch. 10-12",
    },

    # ── Environment ────────────────────────────────────────

    {
        "id": "environmental",
        "title": "Environmental Considerations in Process Design",
        "text": (
            "Green engineering principles for chemical process design: "
            "1) Minimize waste at source (atom economy, selectivity). "
            "2) Recover and recycle solvents and catalysts. "
            "3) Use water as solvent when possible (avoid VOCs). "
            "4) Minimize energy consumption through heat integration. "
            "5) Design for lowest feasible operating pressure and temperature. "
            "6) Consider CO2 emissions from fuel combustion and steam generation. "
            "7) Wastewater treatment requirements depend on BOD, COD, pH, "
            "heavy metals, and specific pollutant concentrations. "
            "8) Air emissions: VOCs, NOx, SOx, particulates must meet local "
            "regulations (EPA, EU directives). "
            "In DWSIM, track component flows in all outlet streams to estimate "
            "emissions. Use mass balance to calculate waste generation rates."
        ),
        "tags": ["environment", "green", "emissions", "waste", "sustainability",
                 "VOC", "wastewater", "CO2"],
        "source": "Allen & Shonnard: Green Engineering; EPA Guidelines",
    },

    # ── Process Control ────────────────────────────────────

    {
        "id": "process_control",
        "title": "Process Control Basics for Simulation",
        "text": (
            "Key control concepts relevant to simulation: "
            "1) Degrees of freedom = number of variables - number of equations. "
            "Must be zero for a well-defined simulation. "
            "2) Control variables: choose one manipulated variable for each controlled "
            "variable. Common pairs: temperature-heat duty, pressure-valve position, "
            "level-outlet flow, composition-reflux ratio. "
            "3) Adjust/Spec blocks in DWSIM: 'Adjust' manipulates one variable "
            "to achieve a target value for another. 'Specification' enforces "
            "a relationship between variables. "
            "4) Design specifications: use Adjust blocks to find operating "
            "conditions that meet product specifications (e.g., 'adjust feed "
            "temperature until product purity = 99.5%'). "
            "5) Sensitivity analysis (parametric study): vary one parameter and "
            "observe how outputs change - essential for understanding control behavior."
        ),
        "tags": ["control", "degrees of freedom", "adjust", "specification",
                 "manipulated variable", "controlled variable", "design spec"],
        "source": "Seborg et al.: Process Dynamics and Control; Seider et al.",
    },

    # ── Fluid Properties ───────────────────────────────────

    {
        "id": "fluid_props",
        "title": "Important Fluid Properties",
        "text": (
            "Key properties for process calculations: "
            "Viscosity: affects pressure drop, heat transfer, mixing. Liquids: "
            "0.2-1000 cP (water 1 cP at 20C). Gases: 0.01-0.03 cP. "
            "Thermal conductivity: affects heat transfer coefficient. "
            "Water 0.6 W/m-K, gases 0.01-0.05, organics 0.1-0.2. "
            "Heat capacity (Cp): water 4.18 kJ/kg-K (highest of common liquids). "
            "Gases Cp depends on molecular complexity and temperature. "
            "Surface tension: affects droplet/bubble size, entrainment, foaming. "
            "Vapor pressure: critical for flash calculations, NPSH, safety. "
            "Antoine equation: log10(P) = A - B/(C+T). "
            "In DWSIM, all these properties are calculated from the selected "
            "property package. Check that calculated values are reasonable by "
            "comparing with literature data."
        ),
        "tags": ["viscosity", "thermal conductivity", "heat capacity", "Cp",
                 "vapor pressure", "Antoine", "surface tension", "properties"],
        "source": "Perry's 9th ed., Sec. 2; Reid, Prausnitz & Poling",
    },

    # ── Combustion ─────────────────────────────────────────

    {
        "id": "combustion",
        "title": "Combustion and Furnace Design",
        "text": (
            "Combustion reactions with air: CxHy + (x+y/4)O2 -> xCO2 + (y/2)H2O. "
            "Stoichiometric air = theoretical air for complete combustion. "
            "Excess air: typically 10-20% for gas fuels, 15-30% for liquid, "
            "20-50% for solid fuels. More excess air = more complete combustion "
            "but lower flame temperature and higher flue gas losses. "
            "Adiabatic flame temperature: 1800-2000C for natural gas in air. "
            "Furnace efficiency: 80-92% for well-designed systems. "
            "Flue gas composition: CO2, H2O, N2, excess O2 (and SOx if sulfur present). "
            "In DWSIM, model combustion with Gibbs reactor for equilibrium "
            "composition or Conversion reactor with specified conversion. "
            "Include N2 in the air feed (79 mol% N2, 21 mol% O2)."
        ),
        "tags": ["combustion", "furnace", "flame temperature", "excess air",
                 "flue gas", "stoichiometric", "natural gas", "fuel"],
        "source": "Perry's 9th ed., Sec. 24; Turns: Intro to Combustion",
    },

    # ── Absorption ─────────────────────────────────────────

    {
        "id": "absorption_design",
        "title": "Gas Absorption Column Design",
        "text": (
            "Absorption removes a gas component using a liquid solvent. "
            "Key design parameters: L/G ratio (liquid to gas molar flow ratio), "
            "number of theoretical stages (NTS), and column diameter. "
            "Minimum L/G: from operating line tangent to equilibrium curve. "
            "Actual L/G: typically 1.2-1.5 times minimum. "
            "Henry's law: p_i = H * x_i for dilute systems (governs equilibrium). "
            "Common absorbents: water (for NH3, HCl), amine solutions (for CO2, H2S), "
            "physical solvents (for CO2 at high pressure). "
            "Column types: packed columns (random or structured packing) for "
            "most absorption. Tray columns for very high liquid rates. "
            "In DWSIM, use the Absorption Column with specified number of stages. "
            "Feed gas enters at bottom, solvent enters at top (counter-current)."
        ),
        "tags": ["absorption", "gas absorption", "Henry's law", "L/G ratio",
                 "packed column", "amine", "CO2 capture", "solvent"],
        "source": "Perry's 9th ed., Sec. 14; Seider, Seader & Lewin, Ch. 8",
    },

    # ── Optimization ───────────────────────────────────────

    {
        "id": "optimization_principles",
        "title": "Process Optimization Strategies",
        "text": (
            "Common optimization objectives in chemical processes: "
            "1) Minimize energy consumption (heat duty, compressor power). "
            "2) Maximize product yield or selectivity. "
            "3) Minimize capital cost (smaller equipment). "
            "4) Maximize profit (revenue - costs). "
            "Single-variable optimization: golden section search for unimodal "
            "objectives, grid search for multi-modal. "
            "Multi-variable: Nelder-Mead simplex (gradient-free), SQP for "
            "constrained problems. "
            "For DWSIM simulations: use parametric study to map the design space, "
            "then use optimize_process for precise optimization. "
            "Common trade-offs: higher reflux ratio = better separation but more "
            "energy; more stages = better separation but higher capital cost; "
            "higher conversion = more product but larger reactor. "
            "Always check that the optimal point satisfies all constraints "
            "(temperature limits, pressure ratings, material compatibility)."
        ),
        "tags": ["optimization", "golden section", "minimize", "maximize",
                 "trade-off", "parametric study", "design space", "objective"],
        "source": "Edgar, Himmelblau & Lasdon: Optimization of Chemical Processes",
    },

    # ── Biogas & Anaerobic Digestion ───────────────────────

    {
        "id": "biogas_composition",
        "title": "Biogas Composition and Properties",
        "text": (
            "Biogas is produced by anaerobic digestion (AD) of organic matter. "
            "Typical composition: CH4 55-70 mol%, CO2 30-45 mol%, H2O 1-5%, "
            "trace H2S 0.1-3%, N2 0-3%, NH3 traces. "
            "Lower heating value (LHV): 20-25 MJ/Nm3 (vs 34 MJ/Nm3 for natural gas). "
            "Methane number (anti-knock): 130-140 (better than petrol). "
            "Water saturated at digester temperature (~37-55°C, mesophilic or "
            "thermophilic). Must remove H2O before compression/upgrading. "
            "H2S is corrosive and toxic — remove to <250 ppm for engines, "
            "<4 ppm for biomethane grid injection. "
            "In DWSIM, model biogas as a CH4/CO2/H2S/H2O mixture using "
            "Peng-Robinson EOS. Use exact DWSIM compound names: "
            "'Methane', 'Carbon dioxide', 'Hydrogen sulfide', 'Water'."
        ),
        "tags": ["biogas", "anaerobic digestion", "methane", "CO2", "H2S",
                 "composition", "biomethane", "LHV"],
        "source": "IEA Bioenergy Task 37; Murphy et al.: Biogas Handbook",
    },
    {
        "id": "biogas_upgrading",
        "title": "Biogas Upgrading to Biomethane",
        "text": (
            "Biogas upgrading removes CO2 (and H2S) to produce biomethane "
            "(>96% CH4) suitable for grid injection or vehicle fuel. "
            "Main technologies: "
            "1) Water scrubbing (WS): CO2 absorbed in water at 6-10 bar, "
            "20°C. Simple, methane slip 1-2%, energy use ~0.3 kWh/Nm3. "
            "2) Pressure swing adsorption (PSA): CO2 adsorbed on zeolite/activated "
            "carbon. 4-7 bar, methane purity 96-99%. Energy ~0.25 kWh/Nm3. "
            "3) Amine scrubbing (chemical absorption): MEA or MDEA absorbs CO2 "
            "selectively. Highest purity (>99% CH4), higher capital cost. "
            "4) Membrane separation: selective CO2 permeation. "
            "Simple operation, lower purity unless multi-stage. "
            "In DWSIM, model water scrubbing as absorption column (CO2-water, "
            "Henry's law). PSA requires dynamic or simplified steady-state model. "
            "Property package: Peng-Robinson for CO2/CH4 at elevated pressure."
        ),
        "tags": ["biogas upgrading", "biomethane", "PSA", "water scrubbing",
                 "amine scrubbing", "CO2 removal", "membrane", "grid injection"],
        "source": "IRENA: Biogas to Biomethane; Urban et al. (2019)",
    },
    {
        "id": "anaerobic_digestion",
        "title": "Anaerobic Digestion Process",
        "text": (
            "Anaerobic digestion (AD) converts organic substrates to biogas "
            "through four stages: hydrolysis → acidogenesis → acetogenesis → "
            "methanogenesis. Key operating parameters: "
            "Temperature: mesophilic 35-40°C (most common), thermophilic 50-55°C "
            "(faster but less stable). "
            "pH: optimal 6.8-7.5. pH <6.5 inhibits methanogens. "
            "Hydraulic retention time (HRT): 15-30 days for slurries, "
            "3-7 days for some industrial wastes. "
            "C/N ratio: 20-30:1 optimal. Too low N = ammonia inhibition. "
            "Biogas yield: 250-400 Nm3/tonne VS (volatile solids). "
            "Substrates: manure 200-500 Nm3/tonne, food waste 400-700, "
            "energy crops 500-700. "
            "In DWSIM, model AD as a conversion reactor with known biogas yield, "
            "or as a Gibbs reactor for equilibrium composition. "
            "Include digestate (effluent) as a separate outlet stream."
        ),
        "tags": ["anaerobic digestion", "biogas", "methanogens", "HRT",
                 "mesophilic", "thermophilic", "substrate", "digestate"],
        "source": "Metcalf & Eddy: Wastewater Engineering; IEA Bioenergy",
    },

    # ── Hydrogen Production ────────────────────────────────

    {
        "id": "hydrogen_smr",
        "title": "Steam Methane Reforming (SMR) for Hydrogen",
        "text": (
            "Steam methane reforming is the dominant industrial H2 production route. "
            "Reactions: "
            "CH4 + H2O ⇌ CO + 3H2  (reforming, endothermic, ΔH = +206 kJ/mol) "
            "CO + H2O ⇌ CO2 + H2   (water-gas shift, exothermic, ΔH = -41 kJ/mol) "
            "Operating conditions: 800-900°C, 15-30 bar, steam:carbon ratio 3-4. "
            "Catalyst: Ni on Al2O3. Efficiency: 65-80% (LHV basis). "
            "CO2 emissions: ~10 kg CO2/kg H2 (grey hydrogen). "
            "CCS integration → blue hydrogen (<2 kg CO2/kg H2). "
            "H2 purity after PSA: >99.999%. "
            "In DWSIM: model reformer as Gibbs reactor with CH4, H2O, CO, CO2, H2. "
            "Use Peng-Robinson EOS. HTS at 350-400°C, LTS at 200-230°C. "
            "PSA for final purification."
        ),
        "tags": ["SMR", "steam methane reforming", "hydrogen", "H2", "water-gas shift",
                 "reforming", "grey hydrogen", "blue hydrogen", "Gibbs reactor"],
        "source": "Aasberg-Petersen et al. (2011); IEA: The Future of Hydrogen",
    },
    {
        "id": "hydrogen_electrolysis",
        "title": "Water Electrolysis for Green Hydrogen",
        "text": (
            "Water electrolysis splits water into H2 and O2 using electricity. "
            "Overall reaction: H2O → H2 + ½O2  (ΔH = +286 kJ/mol, ΔG = +237 kJ/mol). "
            "Technologies: "
            "1) Alkaline (ALK): 70-80% efficiency, lowest cost, 60-80°C, KOH electrolyte. "
            "2) PEM (Proton Exchange Membrane): 75-85% efficiency, fast response, "
            "pure water feed, higher cost. Best for coupling with renewables. "
            "3) Solid oxide (SOEC): 85-95% efficiency at 700-900°C. "
            "Uses steam. Highest efficiency but immature technology. "
            "Energy consumption: 50-55 kWh/kg H2 (ALK/PEM), 35-40 kWh/kg H2 (SOEC). "
            "Green hydrogen: electricity from wind/solar → zero direct CO2 emissions. "
            "In DWSIM, model electrolyzer as component separator with known "
            "H2O conversion and O2 by-product, plus energy stream for power input. "
            "Property package: Peng-Robinson or Steam Tables for water/steam feed."
        ),
        "tags": ["electrolysis", "green hydrogen", "PEM", "alkaline", "SOEC",
                 "water splitting", "H2", "O2", "renewable energy"],
        "source": "IRENA: Green Hydrogen Cost Reduction; Schmidt et al. (2017)",
    },
    {
        "id": "hydrogen_biogas_reforming",
        "title": "Hydrogen from Biogas Reforming",
        "text": (
            "Biogas can be reformed to produce hydrogen — a renewable H2 pathway. "
            "Dry reforming: CH4 + CO2 → 2CO + 2H2  (ΔH = +247 kJ/mol, 800-900°C). "
            "This uses CO2 from the biogas itself, making it CO2-negative if "
            "CCS is applied. Ni catalyst, prone to coking at low S/C ratios. "
            "Steam reforming of biogas: similar to SMR but feed is CH4/CO2 mixture. "
            "The CO2 in biogas reduces S/C ratio — extra steam needed to prevent coking. "
            "Typical S/C ratio: 3.5-4.5. Pre-desulfurization essential (H2S poisons Ni catalyst). "
            "Combined reforming: biogas + steam fed to reformer. "
            "H2 yield: 2.5-3.0 Nm3 H2/Nm3 biogas (for 60% CH4 biogas). "
            "In DWSIM: model as Gibbs reactor with CH4, CO2, H2O, CO, H2. "
            "Remove H2S before feed using ZnO bed (modeled as component separator)."
        ),
        "tags": ["biogas reforming", "dry reforming", "hydrogen", "H2",
                 "renewable hydrogen", "CO2 utilization", "Ni catalyst", "coking"],
        "source": "Kolbitsch et al. (2008); Sunde et al. (2011)",
    },
    {
        "id": "methanation",
        "title": "Methanation (Power-to-Gas)",
        "text": (
            "Methanation converts CO2 + H2 to synthetic natural gas (SNG): "
            "CO2 + 4H2 → CH4 + 2H2O  (Sabatier reaction, ΔH = -165 kJ/mol, exothermic). "
            "Also: CO + 3H2 → CH4 + H2O  (CO methanation). "
            "Operating conditions: 250-550°C, 1-80 bar, Ni or Ru catalyst. "
            "Equilibrium strongly favors CH4 at low T and high P. "
            "Temperature management critical due to high exothermicity — use "
            "multiple stages with intercooling or recycle of product gas for dilution. "
            "CH4 content in SNG: >95% after water removal. "
            "Power-to-Gas concept: excess renewable electricity → electrolysis → H2 "
            "→ methanation with CO2 from biogas/air → SNG → gas grid storage. "
            "In DWSIM: model as Gibbs reactor or conversion reactor. "
            "Include cooler after reactor for heat recovery. "
            "Water separator (flash) to remove H2O from product. "
            "Use Peng-Robinson EOS for CO2/H2/CH4/H2O system."
        ),
        "tags": ["methanation", "Sabatier", "power-to-gas", "SNG", "synthetic natural gas",
                 "CO2 utilization", "hydrogen storage", "Ni catalyst"],
        "source": "Götz et al. (2016) Renewable Energy; Sterner (2009) PhD",
    },

    # ── Carbon Capture ─────────────────────────────────────

    {
        "id": "ccs_basics",
        "title": "Carbon Capture and Storage (CCS) Basics",
        "text": (
            "CCS captures CO2 from industrial processes for storage or utilization. "
            "Capture technologies: "
            "1) Post-combustion: absorb CO2 from flue gas with amine (MEA, MDEA). "
            "   Flue gas typically 10-15% CO2 at 1 bar. Energy penalty: 3-4 GJ/tonne CO2. "
            "2) Pre-combustion: convert fuel to H2/CO2 before combustion, "
            "   separate CO2 at high partial pressure (more efficient). "
            "3) Oxyfuel: combust with pure O2, flue gas is CO2/H2O only (easy separation). "
            "Amine scrubbing process: absorber (40-60°C) → rich amine → stripper "
            "(120-140°C) → lean amine recycle. "
            "CO2 compression for transport: 110-150 bar (dense phase / supercritical). "
            "In DWSIM, model amine absorber as absorption column with CO2/N2/H2O feed "
            "and MEA/water solvent. Use NRTL or amine-specific package. "
            "Stripper modeled as distillation column with reboiler."
        ),
        "tags": ["CCS", "carbon capture", "CO2", "amine scrubbing", "MEA", "MDEA",
                 "post-combustion", "pre-combustion", "oxyfuel", "flue gas"],
        "source": "IPCC Special Report on CCS; Rochelle (2009) Science",
    },

    # ── Biomass & Gasification ─────────────────────────────

    {
        "id": "biomass_gasification",
        "title": "Biomass Gasification",
        "text": (
            "Biomass gasification converts solid biomass to syngas (CO + H2 + CO2 + CH4 + N2) "
            "at high temperature with limited oxygen. "
            "Key reactions: "
            "C + O2 → CO2  (combustion, exothermic) "
            "C + CO2 → 2CO  (Boudouard, endothermic) "
            "C + H2O → CO + H2  (steam gasification, endothermic) "
            "CO + H2O → CO2 + H2  (water-gas shift) "
            "Gasification agents: air (cheap, N2 dilution), O2 (expensive, clean syngas), "
            "steam (hydrogen-rich syngas). "
            "Temperature: 700-1000°C. Equivalence ratio (ER): 0.2-0.4. "
            "Syngas LHV: 4-6 MJ/Nm3 (air-blown), 10-16 (O2 or steam). "
            "Tar is a major problem — requires cleaning (cracking, scrubbing). "
            "In DWSIM: model gasifier as Gibbs reactor with C (graphite), H2O, O2, N2 "
            "as reactants. Adjust ER to control temperature. "
            "Include cyclone and scrubber downstream for solids/tar removal."
        ),
        "tags": ["gasification", "biomass", "syngas", "Boudouard", "equivalence ratio",
                 "tar", "Gibbs reactor", "steam gasification"],
        "source": "Basu: Biomass Gasification, Pyrolysis and Torrefaction (3rd ed.)",
    },

    # ── DWSIM Advanced Troubleshooting ────────────────────

    {
        "id": "dwsim_recycle_fix",
        "title": "Recycle Loop Convergence in DWSIM",
        "text": (
            "Recycle loops are the most common convergence challenge in flowsheet simulation. "
            "Root causes of failure and fixes: "
            "1) Bad initial guess on tear stream: set realistic T, P, composition and "
            "   flow rate on the recycle stream before running. Use expected steady-state "
            "   values from a mass balance estimate. "
            "2) Too aggressive convergence tolerance: relax tolerance from 0.0001 to 0.001. "
            "3) Too few iterations: increase max iterations in Recycle block (default 20, "
            "   try 50-100 for difficult systems). "
            "4) Wrong tear stream: DWSIM auto-selects tear streams. For multi-recycle "
            "   flowsheets, manually assign. "
            "5) Oscillating solution: enable Wegstein or Broyden acceleration method. "
            "   Wegstein accelerates convergence for slowly changing systems. "
            "6) Phase change in recycle: if recycle stream changes phase between iterations, "
            "   set vapor fraction specification to guide convergence. "
            "OT_Recycle block in DWSIM: connect inlet from downstream, outlet to upstream "
            "mixer. Set tolerance (relative) and max iterations."
        ),
        "tags": ["recycle", "convergence", "tear stream", "Wegstein", "OT_Recycle",
                 "DWSIM", "troubleshooting", "oscillation", "initial guess"],
        "source": "DWSIM Documentation; Seader & Henley: Separation Process Principles",
    },
    {
        "id": "dwsim_column_init",
        "title": "Distillation Column Initialization in DWSIM",
        "text": (
            "Distillation columns often fail to converge without good initialization. "
            "Steps for successful convergence: "
            "1) Start with shortcut column (Fenske-Underwood-Gilliland) to get "
            "   N_min, R_min, and approximate stage/reflux. "
            "2) Use shortcut results as initial estimate for rigorous column. "
            "3) For rigorous column, set: number of stages, feed stage, "
            "   condenser type (total/partial), reflux ratio, and distillate flow. "
            "4) Over-specify initially (e.g., fix both reflux AND distillate) "
            "   then switch to degree-of-freedom consistent spec. "
            "5) If column diverges: reduce number of stages by 50%, converge, "
            "   then increase stages. Or start with reflux ratio = 2*R_min. "
            "6) Condenser temperature too low: check operating pressure allows "
            "   condensation with available cooling utility. "
            "7) Reboiler temperature too high: reduce operating pressure. "
            "ShortcutColumn in DWSIM: good first step. Provides N, R, Qc, Qr estimates."
        ),
        "tags": ["distillation", "column", "initialization", "shortcut column",
                 "convergence", "reflux ratio", "DWSIM", "rigorous column"],
        "source": "DWSIM Documentation; Kister: Distillation Design",
    },
    {
        "id": "dwsim_unit_errors",
        "title": "Common DWSIM Unit and Specification Errors",
        "text": (
            "Frequent errors when setting up DWSIM simulations and their fixes: "
            "1) Temperature: DWSIM internal unit is Kelvin. If setting 25°C, "
            "   pass value=25 with unit='C' (the API converts). Never pass 25 "
            "   as a 'K' temperature — that's near absolute zero. "
            "2) Pressure: internal unit is Pascal. 1 bar = 1e5 Pa, 1 atm = 101325 Pa. "
            "3) Molar flow: mol/s internally. 1 kmol/h = 0.2778 mol/s. "
            "4) Mass flow: kg/s internally. 1 kg/h = 0.000278 kg/s. "
            "5) Composition sum: mole fractions must sum exactly to 1.0. "
            "   Rounding errors (e.g., 0.33+0.33+0.33=0.99) cause flash failure. "
            "6) Outlet T for heater: must be in Kelvin (OutletTemperature property). "
            "   90°C = 363.15 K. "
            "7) Energy stream connection: energy streams connect to unit op port 1 "
            "   (to_port=1 for heater, cooler, pump, reactor). Port 0 is material. "
            "8) Compound name case: DWSIM compound names are case-sensitive and "
            "   specific. 'Water' ≠ 'water'. Use get_available_compounds to search."
        ),
        "tags": ["DWSIM", "units", "error", "Kelvin", "Pascal", "mole fraction",
                 "specification", "temperature conversion", "common mistake"],
        "source": "DWSIM Documentation v8; Community Forum FAQs",
    },
    {
        "id": "dwsim_property_pkg_errors",
        "title": "Property Package Mismatch and Errors in DWSIM",
        "text": (
            "Property package errors are a major source of simulation failures. "
            "Common problems and solutions: "
            "1) 'Binary interaction parameters not found': NRTL/UNIQUAC selected but "
            "   BIPs missing for a pair. Switch to PR if non-polar, or use UNIFAC for estimates. "
            "2) Flash convergence failure for polar+nonpolar mix: using PR for "
            "   alcohol+water system. Switch to NRTL. "
            "3) Pure water system with PR: gives less accurate results than "
            "   Steam Tables. Always use IAPWS-IF97 for pure water. "
            "4) Negative Cp values: wrong PP for temperature range. "
            "5) Gas phase at high pressure collapses: EOS near critical point. "
            "   Try SRK with volume correction or use CoolProp. "
            "6) Electrolyte system with NRTL: needs e-NRTL (electrolyte NRTL) package. "
            "   Standard NRTL cannot handle dissociation. "
            "7) CO2+CH4 system (biogas): use Peng-Robinson. Good accuracy up to 100 bar. "
            "   CO2/CH4 kij = 0.089 (key binary interaction parameter)."
        ),
        "tags": ["property package", "NRTL", "BIP", "flash failure", "DWSIM",
                 "error", "mismatch", "troubleshooting", "electrolyte"],
        "source": "DWSIM Documentation; Gmehling et al.: Chemical Thermodynamics",
    },

    # ── Reaction Engineering Heuristics ───────────────────

    {
        "id": "reactor_heuristics",
        "title": "Reactor Design Heuristics and Rules of Thumb",
        "text": (
            "Practical reactor design heuristics for process engineers: "
            "1) Exothermic reactions: always design for heat removal first. "
            "   Maximum adiabatic temperature rise ΔT_ad < 50°C for safety. "
            "2) Endothermic reactions: heat supply controls the reaction rate. "
            "3) For liquid phase reactions with unknown kinetics, start with CSTR. "
            "4) For gas phase reactions, PFR generally preferred (no back-mixing). "
            "5) Single-pass conversion should not exceed 90% for most reactions "
            "   (diminishing returns, recycle economics better). "
            "6) Residence time for CSTRs: 0.5-4 hours typical for liquid phase. "
            "7) PFR length/diameter ratio: 10:1 to 100:1. "
            "8) Catalyst bed pressure drop: should not exceed 10-15% of inlet pressure. "
            "9) For equilibrium-limited reactions: remove product as formed "
            "   (reactive distillation, membrane reactor). "
            "10) Recycle ratio = recycle flow / fresh feed. High recycle (>5:1) "
            "    is economically feasible only if separation is cheap."
        ),
        "tags": ["reactor", "heuristics", "rules of thumb", "CSTR", "PFR",
                 "conversion", "recycle", "adiabatic", "residence time"],
        "source": "Douglas: Conceptual Design of Chemical Processes; Turton et al.",
    },

    # ── Utilities ──────────────────────────────────────────

    {
        "id": "utilities_steam",
        "title": "Steam Utility Systems",
        "text": (
            "Industrial steam is classified by pressure level: "
            "High pressure (HP): 40-80 bar, 400-500°C (power generation, high-T reactions). "
            "Medium pressure (MP): 10-20 bar, 200-300°C (most process heating). "
            "Low pressure (LP): 3-5 bar, 133-151°C (reboilers, tracing, low-T heating). "
            "Steam cost: $5-15/tonne depending on fuel cost and efficiency. "
            "Steam generation efficiency: 85-92% for modern boilers. "
            "Condensate recovery: return condensate to boiler (saves energy + treated water). "
            "Steam traps: separate condensate from steam; failure wastes steam. "
            "In DWSIM, model steam heating using Heater/Cooler with specified duty, "
            "or use an explicit hot steam stream in a heat exchanger. "
            "Use Steam Tables for the steam-side property package."
        ),
        "tags": ["steam", "utility", "HP steam", "MP steam", "LP steam",
                 "boiler", "condensate", "steam trap", "heating"],
        "source": "Perry's 9th ed., Sec. 9; Spirax Sarco Steam Engineering Tutorials",
    },
    {
        "id": "utilities_cooling",
        "title": "Cooling Utilities: Cooling Water and Refrigeration",
        "text": (
            "Cooling water (CW): most common cooling utility. Supply 25-35°C, "
            "return 40-45°C (max ΔT = 10°C to limit fouling and scale). "
            "Cooling water cost: $0.05-0.20/GJ. Minimum approach to CW: 10°C. "
            "Air cooling: supply at ambient air temperature, 10-20°C approach. "
            "Good for remote sites or where water is scarce. "
            "Refrigeration: required when cooling below 15-20°C. "
            "Common refrigerants: propylene (down to -45°C), ethylene (-100°C), "
            "propane (-40°C), ammonia (-33°C). "
            "Coefficient of performance (COP): 1-5 depending on temperature level. "
            "Refrigeration cost increases sharply below -40°C (multistage needed). "
            "In DWSIM, model condenser with cold utility by specifying outlet "
            "temperature or vapor fraction. Use a cooler block for simple cooling, "
            "heat exchanger for detailed design with utility stream."
        ),
        "tags": ["cooling water", "refrigeration", "utility", "COP",
                 "refrigerant", "approach temperature", "air cooling"],
        "source": "Turton et al., Ch. 8; Smith: Chemical Process Design",
    },

    # ── Perry's Handbook ───────────────────────────────────

    {
        "id": "perry_pipe_flow",
        "title": "Pipe Flow and Pressure Drop (Perry's Ch. 6)",
        "text": (
            "Pressure drop for incompressible flow in pipes (Darcy-Weisbach): "
            "ΔP = f * (L/D) * (ρu²/2), where f is Darcy friction factor, "
            "L pipe length, D diameter, ρ density, u velocity. "
            "Friction factor: laminar Re<2100: f=64/Re. "
            "Turbulent: Colebrook-White equation or Moody chart. "
            "Churchill equation (explicit): works for all Re. "
            "Economic pipe velocity: liquids 1-3 m/s, gases 15-30 m/s, "
            "steam 20-40 m/s. Slurries 1.5-3 m/s (above settling velocity). "
            "Equivalent lengths for fittings: gate valve fully open = 13D, "
            "globe valve = 350D, 90° elbow = 30D, tee (flow-through) = 20D. "
            "For compressible gas flow, use isothermal or adiabatic equations "
            "when Mach > 0.1. Choked flow at Mach = 1. "
            "DWSIM Pipe Segment: specify diameter, length, fittings, inclination. "
            "Reports outlet T, P, velocity, and pressure profile."
        ),
        "tags": ["pipe", "pressure drop", "Darcy-Weisbach", "friction factor",
                 "velocity", "fittings", "Moody", "pipe flow", "Perry's"],
        "source": "Perry's Chemical Engineers' Handbook, 9th ed., Sec. 6",
    },
    {
        "id": "perry_mass_transfer",
        "title": "Mass Transfer Fundamentals (Perry's Ch. 5)",
        "text": (
            "Mass transfer drives separation processes. Key concepts: "
            "Fick's law (molecular diffusion): J = -D * dC/dz. "
            "Diffusivity in gases: ~1e-5 m²/s. In liquids: ~1e-9 m²/s. "
            "Mass transfer coefficient k: flux = k * (C_bulk - C_interface). "
            "Film theory: k = D / δ (film thickness δ). "
            "Penetration theory: k = 2*sqrt(D/(π*t_e)). "
            "HTU (Height of Transfer Unit) = L / (K_y * a): lower = better packing. "
            "NTU (Number of Transfer Units): integral of (dy / (y* - y)). "
            "Height of packing = HTU × NTU. "
            "For gas absorption: NOG = ln[(1-A) * y1/y2 + A] / (1-A), "
            "where A = L/(mG) is absorption factor, m = Henry's constant slope. "
            "Typical HTU for packed columns: 0.3-1.5 m. "
            "In DWSIM, absorption/stripping columns use equilibrium stages. "
            "Convert real packing height using overall efficiency or HETP."
        ),
        "tags": ["mass transfer", "Fick's law", "diffusivity", "HTU", "NTU",
                 "absorption factor", "HETP", "film theory", "Perry's"],
        "source": "Perry's Chemical Engineers' Handbook, 9th ed., Sec. 5",
    },
    {
        "id": "perry_heat_conduction",
        "title": "Heat Transfer by Conduction and Convection (Perry's Ch. 11)",
        "text": (
            "Conduction: Q = k * A * ΔT / L (Fourier's law). "
            "Thermal conductivities: metals 10-400 W/m-K, liquids 0.1-0.6, "
            "gases 0.01-0.05, insulation 0.02-0.05 W/m-K. "
            "Convection: Q = h * A * (T_wall - T_fluid), h = heat transfer coefficient. "
            "Dittus-Boelter (turbulent pipe flow): Nu = 0.023 Re^0.8 Pr^n "
            "(n=0.4 heating, n=0.3 cooling). Valid Re>10000, 0.6<Pr<160. "
            "Sieder-Tate correction for high viscosity: multiply by (μ/μ_wall)^0.14. "
            "Natural convection: Nu = C*(Gr*Pr)^n from Churchill correlations. "
            "Boiling: nucleate boiling h = 5000-50000 W/m²-K. "
            "Condensing: Nusselt correlation for film condensation. "
            "Overall HTC: 1/U = 1/h_i + t_wall/k_wall + 1/h_o + R_fi + R_fo "
            "(sum of resistances). Fouling resistance R_f dominates in dirty service."
        ),
        "tags": ["heat transfer", "conduction", "convection", "Dittus-Boelter",
                 "Nusselt", "overall HTC", "fouling", "boiling", "condensing", "Perry's"],
        "source": "Perry's Chemical Engineers' Handbook, 9th ed., Sec. 11",
    },

    # ── Coulson & Richardson ───────────────────────────────

    {
        "id": "cr_vessel_sizing",
        "title": "Vessel and Separator Sizing (Coulson & Richardson Vol. 6)",
        "text": (
            "Flash drum / separator sizing rules (Coulson & Richardson): "
            "Vapor velocity: u_v = K_v * sqrt((ρ_L - ρ_V) / ρ_V). "
            "K_v = 0.04-0.07 m/s for vertical vessels (use 0.04 for conservative). "
            "Diameter: D = sqrt(4 * Q_v / (π * u_v)). "
            "Liquid residence time: 5-10 minutes (sizing for liquid sump). "
            "L/D ratio: 1.5-3 for horizontal, 2-4 for vertical drums. "
            "Demister pad: reduces droplet entrainment, placed above liquid. "
            "Pressure vessel design: shell thickness t = P*r / (S*E - 0.6P), "
            "where S = allowable stress, E = weld efficiency, r = radius. "
            "Design pressure: 10% above operating pressure (min 3 bar gauge above). "
            "Design temperature: 15°C above maximum operating temperature. "
            "Material selection: carbon steel up to 425°C, stainless steel for "
            "corrosive, Hastelloy for severe corrosion."
        ),
        "tags": ["vessel sizing", "separator", "flash drum", "diameter", "L/D",
                 "pressure vessel", "shell thickness", "demister", "Coulson Richardson"],
        "source": "Coulson & Richardson Vol. 6 (Sinnott), 4th ed., Ch. 13",
    },
    {
        "id": "cr_packed_column",
        "title": "Packed Column Design (Coulson & Richardson Vol. 6)",
        "text": (
            "Packed columns used for absorption, stripping, and distillation. "
            "Packing types: random (Raschig rings, Pall rings, IMTP), "
            "structured (Sulzer MellapakMR 250Y, Flexipac). "
            "Structured packing: lower pressure drop, higher capacity, "
            "HETP 20-60 cm vs 30-90 cm for random packing. "
            "Flooding velocity from GPDC (Generalized Pressure Drop Correlation): "
            "operate at 70-80% of flooding. "
            "Pressure drop per metre: 20-80 Pa/m (low pressure drop) to "
            "200-400 Pa/m (near flooding). "
            "Minimum wetting rate: 0.04-0.08 m³/m²-h for most packings. "
            "Column diameter: from flooding velocity and 70-80% design. "
            "HETP increases with: low liquid rate, high viscosity, poor distribution. "
            "Liquid distributor required every 5-8 m of packing. "
            "In DWSIM, absorption column uses theoretical stages. "
            "Convert HETP to stages: N_stages = packing height / HETP."
        ),
        "tags": ["packed column", "HETP", "flooding", "packing", "Raschig",
                 "structured packing", "GPDC", "pressure drop", "Coulson Richardson"],
        "source": "Coulson & Richardson Vol. 6, Ch. 11; Kister: Distillation Design",
    },
    {
        "id": "cr_equipment_selection",
        "title": "Equipment Selection Heuristics (Sinnott & Towler)",
        "text": (
            "Key equipment selection rules from Sinnott & Towler: "
            "Separations: "
            "- Relative volatility >1.05: distillation feasible. "
            "- α < 1.1 or azeotrope: consider extractive distillation, azeotropic "
            "  distillation, liquid-liquid extraction, or adsorption. "
            "- Solids present: use cyclones, filters, centrifuges. "
            "- Dissolved solids: crystallization if supersaturation achievable. "
            "Heat transfer: "
            "- Q < 1 MW, simple: plate heat exchanger. "
            "- Q > 1 MW or high P: shell-and-tube. "
            "- Very high T or corrosive: spiral heat exchanger. "
            "- Gas-gas: direct-fired heater or regenerative exchanger. "
            "Reactors: "
            "- Single liquid phase, known kinetics: CSTR or PFR. "
            "- Gas-liquid: bubble column, stirred tank, trickle bed. "
            "- Gas-solid catalyst: fixed bed (PFR), fluidized bed. "
            "- High temperature (>500°C): tubular reactor with flue gas heating."
        ),
        "tags": ["equipment selection", "heuristics", "distillation", "relative volatility",
                 "heat exchanger", "reactor", "Sinnott", "separation", "azeotrope"],
        "source": "Sinnott & Towler: Chemical Engineering Design, 6th ed., Ch. 5",
    },

    # ── McCabe-Smith-Harriott ──────────────────────────────

    {
        "id": "msh_distill_stages",
        "title": "McCabe-Thiele Method for Distillation Stages",
        "text": (
            "The McCabe-Thiele graphical method determines theoretical stages for "
            "binary distillation (McCabe, Smith & Harriott). "
            "Operating lines: "
            "Rectifying: y = (R/(R+1))*x + xD/(R+1) "
            "Stripping: y = (L'/V')*x - (B/V')*xB "
            "q-line: slope = q/(q-1), intercept at x = zF on 45° line. "
            "q = 1 (saturated liquid feed), q = 0 (saturated vapor), "
            "q > 1 (subcooled liquid), q < 0 (superheated vapor). "
            "Stages: step between equilibrium curve and operating lines, "
            "starting from distillate or bottoms composition. "
            "Feed stage: switch operating lines at q-line intersection. "
            "Minimum reflux: operating line tangent to equilibrium curve. "
            "For curved equilibrium: Rm = (xD - y*F) / (y*F - xF). "
            "Actual stages = theoretical stages / tray efficiency (50-80%). "
            "In DWSIM, use shortcut column for Fenske-Underwood-Gilliland estimates, "
            "which uses the same fundamental relationships."
        ),
        "tags": ["McCabe-Thiele", "distillation stages", "operating line",
                 "rectifying", "stripping", "q-line", "reflux ratio", "McCabe Smith"],
        "source": "McCabe, Smith & Harriott: Unit Operations of Chemical Engineering, 8th ed.",
    },
    {
        "id": "msh_leaching",
        "title": "Leaching and Solid-Liquid Extraction (McCabe-Smith)",
        "text": (
            "Leaching extracts soluble components from solid using solvent. "
            "Also called solid-liquid extraction. "
            "Key parameters: "
            "Underflow (solids + retained solution) vs overflow (extract). "
            "Wash ratio = wash solvent / solids flow rate. "
            "Recovery = f(wash stages, wash ratio). "
            "For constant underflow: N stages for fraction remaining = (1/(R+1))^N "
            "where R = overflow/underflow liquid ratio. "
            "Countercurrent washing is most efficient. "
            "Applications: sugar from sugar cane, oil from seeds, "
            "metals from ores (hydrometallurgy), pharmaceuticals from plants. "
            "Equipment: batch tanks, continuous belt filters, CCD (countercurrent decantation). "
            "Temperature increases solubility and diffusivity → faster extraction. "
            "In DWSIM, model leaching as a series of component separators "
            "with specified recovery factors per component."
        ),
        "tags": ["leaching", "solid-liquid extraction", "washing", "countercurrent",
                 "underflow", "recovery", "hydrometallurgy", "McCabe Smith"],
        "source": "McCabe, Smith & Harriott, 8th ed., Ch. 18",
    },
    {
        "id": "msh_crystallization",
        "title": "Crystallization Design (McCabe-Smith)",
        "text": (
            "Crystallization separates dissolved solids by controlled precipitation. "
            "Driving force: supersaturation = C - C* (actual - saturation concentration). "
            "Nucleation rate: primary (spontaneous) and secondary (from existing crystals). "
            "Crystal growth rate G = dL/dt, depends on supersaturation and temperature. "
            "Population balance: n(L) = n0 * exp(-L/(G*τ)), τ = residence time. "
            "Mean crystal size L_avg = 3.67 * G * τ (MSMPR crystallizer). "
            "Yield = (C_initial - C*) / C_initial (for cooling crystallization). "
            "Crystallizer types: "
            "- Draft tube baffle (DTB): good crystal size control, continuous. "
            "- Forced circulation: for moderate solubility, scale-prone materials. "
            "- Batch cooling: for specialty chemicals. "
            "Key control: maintain moderate supersaturation (metastable zone). "
            "In DWSIM, crystallization modeled with conversion + solid stream. "
            "Use component separator with temperature-dependent recovery factor."
        ),
        "tags": ["crystallization", "supersaturation", "nucleation", "crystal growth",
                 "MSMPR", "yield", "population balance", "McCabe Smith"],
        "source": "McCabe, Smith & Harriott, 8th ed., Ch. 17",
    },

    # ── Fogler — Reaction Engineering ──────────────────────

    {
        "id": "fogler_pfr_sizing",
        "title": "PFR Sizing and Design Equation (Fogler)",
        "text": (
            "Plug flow reactor (PFR) design equation: "
            "dF_A/dV = r_A  →  V = F_A0 * ∫(0 to X) dX / (-r_A) "
            "For power law kinetics: -r_A = k * C_A^n "
            "Liquid phase (constant volumetric flow): C_A = C_A0*(1-X). "
            "Gas phase (variable density): C_A = C_A0*(1-X)/(1+εX) * (P/P0) * (T0/T). "
            "ε = δ * y_A0, where δ = change in moles per mole A reacted. "
            "Damköhler number: Da = τ * k * C_A0^(n-1). "
            "For first order: X = 1 - exp(-Da). "
            "Space time τ = C_A0 * V / F_A0 = V / v0. "
            "PFR always more efficient than CSTR for positive-order kinetics "
            "(same volume, higher conversion). "
            "For exothermic PFR: energy balance dT/dV = (-r_A)*(-ΔH_rx) / (sum Fi*Cpi). "
            "Hot-spot temperature: maximum T in reactor, must not exceed safe limit. "
            "In DWSIM PFR: specify volume, catalyst loading, kinetic law. "
            "Check temperature profile along reactor length."
        ),
        "tags": ["PFR", "plug flow reactor", "design equation", "Damköhler",
                 "space time", "power law", "gas phase", "hot spot", "Fogler"],
        "source": "Fogler: Elements of Chemical Reaction Engineering, 6th ed., Ch. 2-3",
    },
    {
        "id": "fogler_cstr",
        "title": "CSTR Analysis and Multiple Steady States (Fogler)",
        "text": (
            "CSTR mole balance: V = F_A0 * X / (-r_A) evaluated at exit conditions. "
            "For first order liquid phase: V = v0 * C_A0 * X / (k * C_A0 * (1-X)) "
            "→ Damköhler: Da = k*τ = X/(1-X), so X = Da/(1+Da). "
            "Multiple CSTRs in series: X_n approaches PFR performance as n→∞. "
            "For n CSTRs: C_An = C_A0 / (1+Da)^n. "
            "Heat effects in CSTR: energy balance gives heat generation curve G(T) "
            "and heat removal line R(T). Intersections = steady states. "
            "Multiple steady states possible for exothermic reactions: "
            "lower SS (quench), middle SS (unstable), upper SS (ignited). "
            "Hysteresis: system may jump between SS depending on startup. "
            "Optimal CSTR temperature for exothermic equilibrium: balance "
            "between rate (increases with T) and equilibrium (decreases with T). "
            "DWSIM CSTR: specify volume and kinetic parameters. "
            "Set inlet T and verify outlet T from energy balance."
        ),
        "tags": ["CSTR", "continuous stirred tank", "multiple steady states",
                 "energy balance", "heat generation", "Damköhler", "series", "Fogler"],
        "source": "Fogler: Elements of Chemical Reaction Engineering, 6th ed., Ch. 5, 11-12",
    },
    {
        "id": "fogler_catalysis",
        "title": "Catalysis and Heterogeneous Reactions (Fogler)",
        "text": (
            "Heterogeneous catalysis: reaction occurs on solid catalyst surface. "
            "Langmuir-Hinshelwood mechanism: adsorption → surface reaction → desorption. "
            "Rate: -r'_A = k * C_A / (1 + K_A * C_A) (single reactant, single site). "
            "Rate law parameters from Arrhenius: k = A * exp(-E_a / RT). "
            "Typical activation energies: 40-200 kJ/mol for catalytic reactions. "
            "Effectiveness factor η = actual rate / rate without diffusion limitations. "
            "Thiele modulus φ = R*sqrt(k/D_e): η → 1 for φ<1 (kinetic control), "
            "η ≈ 1/φ for φ>>1 (diffusion limited). "
            "Catalyst deactivation: sintering (high T), coking (carbon deposition), "
            "poisoning (S, Pb, Cl block active sites). "
            "Fixed-bed reactor: pressure drop from Ergun equation. "
            "Catalyst loading: kg catalyst per m³ bed or kg/kg_feed. "
            "In DWSIM: use conversion reactor with temperature-dependent conversion "
            "OR PFR with Langmuir-Hinshelwood kinetics if available."
        ),
        "tags": ["catalysis", "Langmuir-Hinshelwood", "effectiveness factor",
                 "Thiele modulus", "deactivation", "fixed bed", "Ergun", "Fogler"],
        "source": "Fogler: Elements of Chemical Reaction Engineering, 6th ed., Ch. 10, 15",
    },

    # ── Smith Van Ness Abbott — Thermodynamics ─────────────

    {
        "id": "sva_vle_fundamentals",
        "title": "VLE Calculations: Bubble and Dew Points (Smith Van Ness)",
        "text": (
            "Vapor-liquid equilibrium condition: f_i^V = f_i^L for each component. "
            "Modified Raoult's law (low-pressure VLE): y_i * P = x_i * γ_i * P_sat_i. "
            "Bubble point P: P = sum(x_i * γ_i * P_sat_i). "
            "Dew point P: 1/P = sum(y_i / (γ_i * P_sat_i)). "
            "K-value (distribution coefficient): K_i = y_i/x_i = γ_i * P_sat_i / (φ_i * P). "
            "Bubble point T (iterative): guess T → compute P_sat_i(T), γ_i(x) → "
            "check sum(K_i * x_i) = 1. "
            "Flash calculation (Rachford-Rice): find V such that "
            "sum(z_i*(K_i-1)/(1+V*(K_i-1))) = 0, then x_i = z_i/(1+V*(K_i-1)). "
            "Azeotrope: x_i = y_i for all components. "
            "Maximum pressure azeotrope: γ_i > 1 (positive deviation, NRTL/UNIQUAC). "
            "Minimum pressure azeotrope: γ_i < 1 (negative deviation). "
            "In DWSIM, VLE is calculated by the property package automatically. "
            "Check vapor fraction after any flash block."
        ),
        "tags": ["VLE", "bubble point", "dew point", "K-value", "Rachford-Rice",
                 "Raoult's law", "flash", "azeotrope", "activity coefficient", "Smith Van Ness"],
        "source": "Smith, Van Ness & Abbott: Intro to ChE Thermodynamics, 8th ed., Ch. 10-12",
    },
    {
        "id": "sva_enthalpy_calculations",
        "title": "Enthalpy and Energy Calculations (Smith Van Ness)",
        "text": (
            "Enthalpy change for process streams: "
            "ΔH = ΔH_sensible + ΔH_phase + ΔH_mixing. "
            "Sensible heat: ΔH_s = integral(Cp dT). "
            "Cp for ideal gas: A + BT + CT² + DT⁻² (polynomial, from NIST/Perry's). "
            "Heat of vaporization: Watson correlation: ΔH_vap(T) = ΔH_vap(T_ref) * "
            "((1-T_r)/(1-T_r_ref))^0.38. "
            "Heat of reaction: ΔH_rx(T) = ΔH_rx(298K) + integral(ΔCp dT). "
            "Standard heats of formation (kJ/mol, 25°C): "
            "H2O(l) = -285.8, CO2(g) = -393.5, CH4(g) = -74.8, "
            "CO(g) = -110.5, H2(g) = 0, C3H8(g) = -103.8. "
            "Hess's law: ΔH_rxn = sum(ΔH_f products) - sum(ΔH_f reactants). "
            "In DWSIM, enthalpy is computed by the property package from "
            "EOS or reference state data. "
            "Energy balance check: compare Q computed by DWSIM with hand calculation "
            "using ΔH = m * Cp * ΔT for sensible heating."
        ),
        "tags": ["enthalpy", "energy balance", "Cp", "heat of vaporization",
                 "heat of reaction", "Watson correlation", "formation enthalpy", "Smith Van Ness"],
        "source": "Smith, Van Ness & Abbott, 8th ed., Ch. 4-5; NIST WebBook",
    },
    {
        "id": "sva_gibbs_equilibrium",
        "title": "Chemical Reaction Equilibrium and Gibbs Minimization (Smith Van Ness)",
        "text": (
            "At equilibrium, Gibbs free energy is minimized: dG = 0 at constant T, P. "
            "Equilibrium constant: Ka = exp(-ΔG°_rxn / RT). "
            "ΔG°_rxn = sum(ν_i * ΔG°_f,i) (stoichiometric sum of formation energies). "
            "Relationship between Ka and T (van't Hoff): d(lnKa)/dT = ΔH°_rxn / RT². "
            "Exothermic reaction: Ka decreases with T (lower equilibrium conversion at high T). "
            "Endothermic reaction: Ka increases with T (higher conversion at high T). "
            "Pressure effect: if Δn_gas > 0, high P reduces conversion. "
            "If Δn_gas < 0 (e.g., ammonia synthesis), high P increases conversion. "
            "Inerts dilution: increases conversion for positive Δn reactions. "
            "Gibbs reactor in DWSIM: minimizes total Gibbs free energy directly — "
            "no need to specify reactions or Ka. "
            "Best for: combustion, reforming, WGS, ammonia synthesis, methanation. "
            "Requires accurate ΔG_f data in the property package."
        ),
        "tags": ["Gibbs", "equilibrium", "Ka", "van't Hoff", "Gibbs energy",
                 "exothermic", "endothermic", "pressure effect", "inerts", "Smith Van Ness"],
        "source": "Smith, Van Ness & Abbott, 8th ed., Ch. 13-14",
    },

    # ── DWSIM Advanced Reference ───────────────────────────

    {
        "id": "dwsim_connection_ports",
        "title": "DWSIM Object Connection Port Reference",
        "text": (
            "DWSIM ConnectObjects(from_obj, to_obj, from_port, to_port) port rules: "
            "MaterialStream: always from_port=0, to_port=0 (stream has one port each side). "
            "EnergyStream: from_port=0, to_port=0 (one port each side). "
            "Heater/Cooler: material in to_port=0, material out from_port=0, "
            "energy in to_port=1 (connect EnergyStream → Heater/Cooler). "
            "Pump/Compressor: material in to_port=0, material out from_port=0, "
            "energy/work to_port=1. "
            "Mixer: material inlets to_port=0,1,2... (one per feed), outlet from_port=0. "
            "Splitter: material inlet to_port=0, outlets from_port=0,1,2... "
            "Separator/Vessel: feed to_port=0, vapor from_port=0, liquid from_port=1. "
            "HeatExchanger (two-stream): hot in to_port=0, cold in to_port=1, "
            "hot out from_port=0, cold out from_port=1. "
            "Reactor (Conversion/CSTR/PFR/Gibbs): feed to_port=0, product from_port=0, "
            "energy to_port=1. "
            "ShortcutColumn: feed to_port=0, distillate from_port=0, bottoms from_port=1, "
            "condenser duty Qc: EnergyStream to_port=1, reboiler duty Qr: to_port=2. "
            "OT_Recycle: inlet to_port=0, outlet from_port=0 (connects recycle loop)."
        ),
        "tags": ["DWSIM", "ports", "connection", "ConnectObjects", "to_port", "from_port",
                 "heater", "mixer", "separator", "heat exchanger", "reactor", "recycle"],
        "source": "DWSIM Automation API Documentation v8; DWSIM Source Code",
    },
    {
        "id": "dwsim_solve_sequence",
        "title": "DWSIM Flowsheet Solve Sequence and Solver Settings",
        "text": (
            "DWSIM uses sequential modular simulation: solves unit ops one at a time "
            "following the material flow path. "
            "Solver sequence: upstream objects solved first, results passed downstream. "
            "For recycle loops, tear stream initialized → sequential solve → "
            "recycle block checks convergence → iterate until convergence. "
            "Convergence methods in DWSIM: "
            "- Direct Substitution: simplest, slow for complex recycles. "
            "- Wegstein Acceleration: faster than direct substitution, default. "
            "- Broyden's Method: quasi-Newton, best for stiff recycles. "
            "Flash specification options: "
            "- TP flash (temperature + pressure specified): most common. "
            "- PH flash (pressure + enthalpy): for adiabatic units. "
            "- PS flash: pressure + entropy (for isentropic units). "
            "- PVF flash: pressure + vapor fraction. "
            "- TVF flash: temperature + vapor fraction. "
            "Solver tolerance: default 0.0001 (relative). "
            "Max iterations: default 50. Increase for complex systems. "
            "In DWSIM API, solver runs after save_flowsheet or explicitly via RunSimulation."
        ),
        "tags": ["DWSIM", "solver", "sequential modular", "Wegstein", "Broyden",
                 "flash spec", "TP flash", "PH flash", "convergence method", "iteration"],
        "source": "DWSIM Documentation v8; Westerberg et al.: Process Flowsheeting",
    },
    {
        "id": "dwsim_compound_database",
        "title": "DWSIM Compound Database and Naming",
        "text": (
            "DWSIM's compound database contains thousands of chemicals with "
            "thermodynamic and physical property data. Key naming rules: "
            "Common compounds (exact DWSIM names): "
            "'Water', 'Methanol', 'Ethanol', 'Acetone', 'Benzene', 'Toluene', "
            "'Methane', 'Ethane', 'Propane', 'Butane', 'Nitrogen', 'Oxygen', "
            "'Carbon dioxide', 'Hydrogen', 'Ammonia', 'Hydrogen sulfide', "
            "'Carbon monoxide', 'Ethylene', 'Propylene', 'n-Hexane', 'n-Heptane', "
            "'Cyclohexane', 'Chloroform', 'Acetic acid', 'Diethyl ether'. "
            "Common naming traps: "
            "- 'CO2' does NOT work — use 'Carbon dioxide'. "
            "- 'H2O' does NOT work — use 'Water'. "
            "- 'H2S' does NOT work — use 'Hydrogen sulfide'. "
            "- 'MEA' does NOT work — use 'Monoethanolamine' or search. "
            "If unsure of exact name, call get_available_compounds with a search term: "
            "get_available_compounds({'search': 'ethanol'}) returns matching names. "
            "The bridge uses fuzzy matching to correct near-matches automatically."
        ),
        "tags": ["DWSIM", "compound", "database", "naming", "compound names",
                 "Water", "Methane", "Carbon dioxide", "fuzzy matching"],
        "source": "DWSIM Compound Database v8; DWSIM Forum",
    },
    {
        "id": "dwsim_property_package_list",
        "title": "Full List of Property Packages Available in DWSIM",
        "text": (
            "DWSIM built-in property packages (exact names for API calls): "
            "Equations of State: "
            "'Peng-Robinson (PR)', 'Soave-Redlich-Kwong (SRK)', "
            "'Peng-Robinson-Stryjek-Vera (PRSV)', 'Lee-Kesler-Plöcker (LKP)', "
            "'Benedict-Webb-Rubin-Starling (BWRS)'. "
            "Activity Coefficient: 'NRTL', 'UNIQUAC', 'Modified UNIFAC (Dortmund)', "
            "'Regular Solution'. "
            "Special: 'Steam Tables (IAPWS-IF97)' (pure water only), "
            "'CoolProp' (refrigerants, cryogenics), "
            "'Raoult's Law' (ideal VLE, low pressure only), "
            "'Ideal' (ideal gas + ideal liquid), 'MSRK' (modified SRK). "
            "Selection guide summary: "
            "Natural gas / hydrocarbons → PR or SRK. "
            "Polar mixtures (alcohol/water, ketone/water) → NRTL. "
            "Pure water/steam → Steam Tables. "
            "Refrigerants, cryogenics → CoolProp. "
            "Unknown at low pressure → Raoult's Law as starting point. "
            "Missing BIPs for NRTL → Modified UNIFAC for estimation."
        ),
        "tags": ["property package", "DWSIM", "Peng-Robinson", "NRTL", "SRK",
                 "CoolProp", "UNIFAC", "steam tables", "list", "available"],
        "source": "DWSIM Documentation v8: Thermodynamics Section",
    },

    # ── Thermodynamics ───────────────────────────────────────

    {
        "id": "thermo_fugacity",
        "title": "Fugacity and Chemical Potential in Phase Equilibrium",
        "text": (
            "At phase equilibrium, fugacity of each component is equal in all phases: "
            "f_i^L = f_i^V for VLE. Fugacity is the 'corrected pressure' accounting "
            "for non-ideal behavior: f = y * phi * P (vapor phase), f = x * gamma * P_sat (liquid). "
            "The fugacity coefficient phi comes from an EOS (PR, SRK). "
            "The activity coefficient gamma comes from Gibbs excess energy models (NRTL, UNIQUAC). "
            "For ideal gas: phi = 1. For ideal liquid: gamma = 1 (Raoult's law). "
            "Modified Raoult's law: P_total = sum(x_i * gamma_i * P_sat_i). "
            "K-values (VLE ratio): K_i = y_i/x_i = gamma_i * P_sat_i / (phi_i * P). "
            "Bubble point: sum(K_i * x_i) = 1. Dew point: sum(y_i / K_i) = 1. "
            "DWSIM calculates these automatically inside the flash algorithms."
        ),
        "tags": ["fugacity", "VLE", "K-value", "bubble point", "dew point",
                 "phase equilibrium", "thermodynamics", "Raoult"],
        "source": "Smith, Van Ness & Abbott, Ch. 12-14",
    },

    {
        "id": "thermo_excess_gibbs",
        "title": "Excess Gibbs Energy and Liquid-Liquid Equilibrium",
        "text": (
            "Liquid-liquid equilibrium (LLE) occurs when excess Gibbs energy exceeds "
            "the ideal mixing contribution, causing phase splitting. For partial miscibility: "
            "G^E/RT > 0 with a maximum; spinodal and binodal curves define the immiscible region. "
            "NRTL and UNIQUAC can model LLE when parameters are regressed from LLE data. "
            "UNIFAC (Dortmund version) can predict LLE without data. "
            "Tie lines connect coexisting liquid phases. "
            "For LLE simulation in DWSIM: use 3-phase flash with appropriate property package, "
            "ensure the LLE parameters are loaded. "
            "Temperature sensitivity: LLE phase boundary shifts strongly with T; "
            "lower T generally increases immiscibility for organic-water systems. "
            "Common LLE systems: butanol-water, MIBK-water, liquid-liquid extraction solvents."
        ),
        "tags": ["LLE", "liquid-liquid equilibrium", "excess Gibbs", "NRTL", "UNIQUAC",
                 "phase splitting", "immiscibility", "extraction"],
        "source": "Smith, Van Ness & Abbott, Ch. 14; Perry's Ch. 4",
    },

    {
        "id": "thermo_cpa",
        "title": "CPA Equation of State for Associating Fluids",
        "text": (
            "CPA (Cubic Plus Association) EOS extends PR or SRK with an association "
            "term (Wertheim theory) to handle hydrogen-bonding fluids: water, alcohols, glycols, acids. "
            "Suitable for: natural gas with water and MEG, acid gas systems, biodiesel. "
            "CPA accurately models: water-hydrocarbon mutual solubility, hydrate formation conditions, "
            "alcohol distribution in two-phase systems. "
            "Parameters: EOS parameters (a0, b, c1) + association parameters (epsilon_A, beta_A). "
            "Available in DWSIM via external property package or CoolProp. "
            "For pure water in steam/power cycles: always use Steam Tables (IAPWS-IF97). "
            "For water in mixed organic systems: CPA > NRTL in accuracy when T range is wide."
        ),
        "tags": ["CPA", "associating fluids", "water", "alcohols", "hydrogen bonding",
                 "MEG", "glycol", "hydrate", "EOS"],
        "source": "Kontogeorgis & Folas: Thermodynamic Models for Industrial Applications",
    },

    {
        "id": "thermo_enthalpy_departure",
        "title": "Enthalpy and Entropy Departures from Ideal Gas",
        "text": (
            "Residual (departure) functions quantify deviation from ideal gas: "
            "H - H^ig = integral from 0 to P of [V - T(dV/dT)_P] dP. "
            "For cubic EOS (PR/SRK), departure functions have analytical expressions. "
            "Enthalpy calculation route: H = H^ig(T) + H_departure(T,P). "
            "Heat of vaporization from Clausius-Clapeyron: d(ln P_sat)/d(1/T) = -ΔH_vap/R. "
            "Watson equation correlates ΔH_vap with T: ΔH_vap2/ΔH_vap1 = ((1-Tr2)/(1-Tr1))^0.38. "
            "In process simulation, enthalpy streams drive energy balance: "
            "Q = sum(H_out) - sum(H_in) for each unit op. "
            "Entropy departures are used for isentropic efficiency calculations in "
            "compressors and turbines: eta_s = (H2s - H1)/(H2 - H1)."
        ),
        "tags": ["enthalpy", "entropy", "departure function", "residual", "ideal gas",
                 "heat of vaporization", "Watson", "isentropic efficiency"],
        "source": "Smith, Van Ness & Abbott, Ch. 6; Perry's Ch. 4",
    },

    # ── Distillation and Separations ─────────────────────────

    {
        "id": "distill_rigorous",
        "title": "Rigorous Distillation Column Simulation (MESH Equations)",
        "text": (
            "Rigorous column simulation solves MESH equations: Material balance, "
            "Equilibrium, Summation, and Heat balance on each stage. "
            "Degrees of freedom for a distillation column: specify feed, P-profile, "
            "reflux ratio (or condenser duty), and one product composition or flow. "
            "DWSIM's rigorous column (DistillationColumn) uses inside-out algorithm. "
            "Initialization is critical: poor initialization causes divergence. "
            "Recommended initialization: (1) set reflux = 1.5 * minimum reflux, "
            "(2) estimate product splits from shortcut first, "
            "(3) linear T-profile from bubble to dew point T. "
            "Convergence issues: (a) check for azeotropes — they limit separation, "
            "(b) check K-values are reasonable (not 0 or infinity), "
            "(c) reduce pressure tolerance, (d) try different initial T-profile. "
            "Tray efficiency typically 0.60–0.85 for distillation columns."
        ),
        "tags": ["distillation", "MESH", "rigorous", "DistillationColumn", "DWSIM",
                 "convergence", "reflux", "initialization", "stages"],
        "source": "Seider, Seader & Lewin, Ch. 9; Perry's Ch. 13",
    },

    {
        "id": "distill_azeotrope",
        "title": "Azeotropic Distillation and Maximum/Minimum Boiling Azeotropes",
        "text": (
            "An azeotrope is a mixture with VLE composition equal to liquid composition; "
            "the relative volatility alpha = 1 at the azeotrope. "
            "Maximum boiling azeotropes (negative deviations from Raoult): HCl-water, HNO3-water, acetone-chloroform. "
            "Minimum boiling azeotropes (positive deviations): ethanol-water (95.6% ethanol), "
            "isopropanol-water, n-propanol-water. "
            "Cannot cross azeotrope by simple distillation. Options: "
            "(1) Pressure-swing distillation (azeotrope composition shifts with P), "
            "(2) Extractive distillation (add solvent to change relative volatility), "
            "(3) Azeotropic distillation (add entrainer that forms new azeotrope), "
            "(4) Pervaporation membranes for ethanol dehydration. "
            "For ethanol-water: dehydration beyond 95.6% requires molecular sieves, "
            "extractive distillation with ethylene glycol, or pressure-swing."
        ),
        "tags": ["azeotrope", "azeotropic distillation", "ethanol", "pressure swing",
                 "extractive distillation", "entrainer", "VLE", "relative volatility"],
        "source": "Perry's Ch. 13; McCabe Smith, Ch. 17",
    },

    {
        "id": "distill_column_internals",
        "title": "Column Internals: Trays vs Packings",
        "text": (
            "Trayed columns: sieve trays (cheapest, most common), valve trays (turndown ratio 4:1), "
            "bubble-cap trays (best turndown, no weeping). "
            "Tray spacing: typically 600 mm (large diameter columns) to 450 mm (smaller). "
            "Packed columns: random packing (Raschig rings, Pall rings, IMTP) or "
            "structured packing (Mellapak, Koch-Sulzer). "
            "HETP (Height Equivalent to Theoretical Plate): "
            "Structured packing: 250-500 mm. Random packing: 0.5-1.0 m. "
            "Packed columns preferred for: diameter < 1 m, corrosive systems, "
            "vacuum distillation (low pressure drop), foaming systems. "
            "Tray columns preferred for: high liquid flow rates, side draws, "
            "column diameter > 1.5 m, liquid-liquid extraction. "
            "Flooding velocity (trays): check Souders-Brown coefficient Cs = u_f * sqrt(rho_V/(rho_L - rho_V)). "
            "Design at 70-80% of flood velocity."
        ),
        "tags": ["column internals", "trays", "packing", "HETP", "flooding",
                 "Souders-Brown", "structured packing", "sieve tray", "valve tray"],
        "source": "Coulson & Richardson Vol. 6, Ch. 11; Perry's Ch. 14",
    },

    {
        "id": "extraction_design",
        "title": "Liquid-Liquid Extraction (LLE) Design Principles",
        "text": (
            "LLE separates components based on differential solubility in two immiscible liquid phases. "
            "Solvent selection criteria: high distribution coefficient K_D = (conc. in solvent)/(conc. in feed), "
            "high selectivity beta = K_D(solute)/K_D(diluent), low miscibility with feed, "
            "easy recovery, low toxicity. "
            "Number of stages from Kremser equation: N = ln[(y_n - m*x_0)/(y_1 - m*x_0)] / ln(A) "
            "where A = L/(m*G) is the absorption/extraction factor. "
            "Equipment: mixer-settlers (scale-up easy), pulsed columns, rotating disc contactors. "
            "In DWSIM, model LLE with: 3-phase flash drums or use the solvent extraction block. "
            "Common applications: phenol from water, antibiotics from fermentation broth, "
            "acid/base extraction in pharmaceuticals, BTEX from wastewater."
        ),
        "tags": ["liquid-liquid extraction", "LLE", "solvent", "distribution coefficient",
                 "selectivity", "Kremser", "mixer settler", "extraction"],
        "source": "Perry's Ch. 15; Coulson & Richardson Vol. 2",
    },

    {
        "id": "adsorption_design",
        "title": "Adsorption and Pressure Swing Adsorption (PSA) Principles",
        "text": (
            "Adsorption: selective retention of species on solid surface. "
            "Common adsorbents: activated carbon (organics, VOCs), zeolites (molecular sieves), "
            "silica gel (drying), alumina (drying), ion exchange resins. "
            "Isotherms: Langmuir (monolayer, homogeneous surface), Freundlich (heterogeneous). "
            "Langmuir: q = q_m * K * C / (1 + K * C) where q_m = max capacity, K = affinity. "
            "BET isotherm for multilayer adsorption used for surface area measurement. "
            "PSA process cycle: adsorption at high P → depressurize → purge → re-pressurize. "
            "PSA for H2 purification: achieves 99.999% purity from reformer off-gas. "
            "TSA (temperature swing): for strongly adsorbed species, deep drying. "
            "DWSIM: model PSA as sequential unit ops with compressors and flash vessels; "
            "no native PSA block available — use custom scripts or time-step simulation."
        ),
        "tags": ["adsorption", "PSA", "pressure swing", "zeolite", "activated carbon",
                 "Langmuir", "Freundlich", "hydrogen purification", "drying"],
        "source": "Perry's Ch. 16; Yang: Gas Separation by Adsorption Processes",
    },

    {
        "id": "membrane_separation",
        "title": "Membrane Separation Processes",
        "text": (
            "Membrane separations use selective permeation through a semi-permeable barrier. "
            "Types: reverse osmosis (RO, pressure-driven, dissolved solids removal), "
            "nanofiltration (NF, divalent ions), ultrafiltration (UF, macromolecules), "
            "microfiltration (MF, particles), gas permeation (H2, CO2 separation), "
            "pervaporation (ethanol dehydration). "
            "Driving force: pressure (RO, gas permeation), concentration (dialysis), "
            "temperature (membrane distillation). "
            "Gas permeation flux: J_i = P_i * (p_feed - p_permeate) where P_i is permeability. "
            "Selectivity: alpha = P_i/P_j. H2/CO2 selectivity: ~50 for polymeric membranes. "
            "Advantages: low energy (no phase change), continuous operation, modular. "
            "Disadvantages: fouling, limited selectivity, high membrane cost. "
            "In DWSIM: model as custom script or component separator with specified split fractions."
        ),
        "tags": ["membrane", "RO", "reverse osmosis", "pervaporation", "gas permeation",
                 "ultrafiltration", "H2 separation", "CO2 separation", "permeability"],
        "source": "Perry's Ch. 22; Baker: Membrane Technology and Applications",
    },

    # ── Heat Transfer ────────────────────────────────────────

    {
        "id": "ht_overall_coeff",
        "title": "Overall Heat Transfer Coefficient and Fouling Factors",
        "text": (
            "Overall heat transfer coefficient U for a heat exchanger: "
            "1/U = 1/h_o + R_o + R_wall + R_i + 1/h_i (per unit outer area). "
            "where h_i, h_o = film coefficients (W/m²K), R = fouling resistance (m²K/W). "
            "Typical U values: "
            "Shell-and-tube, water-water: 800-1500 W/m²K. "
            "Shell-and-tube, gas-liquid: 50-300 W/m²K. "
            "Shell-and-tube, condensing steam: 1000-3000 W/m²K. "
            "Plate heat exchanger, water-water: 2000-5000 W/m²K. "
            "Air cooler: 30-50 W/m²K. "
            "TEMA fouling factors: cooling water 0.0002 m²K/W, river water 0.0003, "
            "organic liquids 0.0002, crude oil 0.0005-0.002. "
            "Log Mean Temperature Difference (LMTD): ΔT_lm = (ΔT1 - ΔT2)/ln(ΔT1/ΔT2). "
            "LMTD correction factor F for multi-pass exchangers (F < 0.75 = redesign needed)."
        ),
        "tags": ["heat transfer", "overall coefficient", "U value", "fouling", "LMTD",
                 "shell-and-tube", "film coefficient", "TEMA"],
        "source": "Perry's Ch. 11; Coulson & Richardson Vol. 6, Ch. 12",
    },

    {
        "id": "ht_condensers_reboilers",
        "title": "Condenser and Reboiler Design",
        "text": (
            "Condensers: Total condenser (all vapor condensed, one product), "
            "partial condenser (vapor + liquid products, acts as equilibrium stage). "
            "Cooling medium: cooling water (typical ΔT = 10-25°C), air cooling (approach T ≈ 15°C). "
            "Condenser duty = reflux ratio * D * ΔH_vap + D * C_p * ΔT_subcool. "
            "Reboilers: Kettle reboiler (large vapor space, good for wide boiling range), "
            "thermosyphon (once-through or recirculating, low ΔT, common for narrow boiling), "
            "forced circulation (viscous liquids, heavy fouling). "
            "Reboiler duty = condenser duty + feed thermal condition * ΔH_vap - product enthalpies. "
            "Maximum heat flux for kettle reboiler (nucleate boiling): "
            "q_max ≈ 40 kW/m² (aqueous), 25 kW/m² (organic). "
            "DWSIM ShortcutColumn computes both duties automatically given reflux ratio."
        ),
        "tags": ["condenser", "reboiler", "kettle", "thermosyphon", "distillation",
                 "cooling water", "heat duty", "reflux"],
        "source": "Perry's Ch. 11; Seider Ch. 12",
    },

    {
        "id": "ht_fired_heater",
        "title": "Fired Heaters (Furnaces) — Design and Operation",
        "text": (
            "Fired heaters supply heat via combustion of fuel (natural gas, fuel oil). "
            "Zones: radiant section (high T, radiation dominant), convection section (flue gas recovery). "
            "Thermal efficiency: typically 85-92% (LHV basis). Excess air: 10-15% for natural gas. "
            "Average radiant flux: 30-50 kW/m² for refinery heaters. "
            "Tube material: carbon steel up to 450°C, Cr-Mo alloys up to 650°C. "
            "Tube skin temperature = process temperature + ΔT_film; keep below metallurgical limit. "
            "Coking risk for crude oil heaters: velocity > 1.5 m/s, ΔT < 20°C (film). "
            "NOx emission control: low-NOx burners, flue gas recirculation, staged combustion. "
            "In DWSIM: model as Heater unit op with Q specified from combustion calculation, "
            "or use ConversionReactor to model combustion alongside heat transfer."
        ),
        "tags": ["fired heater", "furnace", "combustion", "radiant", "convection",
                 "efficiency", "flue gas", "NOx", "coking", "tube temperature"],
        "source": "API 560; Baukal: The John Zink Hamworthy Combustion Handbook",
    },

    # ── Reactor Design ───────────────────────────────────────

    {
        "id": "reactor_pfr_cstr_comparison",
        "title": "PFR vs CSTR Performance Comparison",
        "text": (
            "For a first-order reaction A → B at same conversion X: "
            "CSTR volume > PFR volume because CSTR operates at lowest (exit) concentration. "
            "V_CSTR/V_PFR = [(1-X)^(-1) - 1] / (-ln(1-X)) for first order. "
            "At X = 0.9: V_CSTR/V_PFR ≈ 2.9. At X = 0.99: ratio ≈ 4.5. "
            "For autocatalytic reactions: CSTR then PFR gives minimum total volume. "
            "For nth order (n > 0): PFR always outperforms CSTR at same conditions. "
            "Multiple CSTRs in series approach PFR performance. "
            "Temperature effects: higher T increases rate but can reduce selectivity. "
            "Optimal T policy for complex reactions: profile T along reactor axis (PFR). "
            "In DWSIM: use ConversionReactor (specify X) or GibbsReactor (equilibrium) "
            "for fast simulations; use PlugFlowReactor for detailed PFR sizing."
        ),
        "tags": ["PFR", "CSTR", "reactor design", "conversion", "volume", "performance",
                 "first order", "Levenspiel"],
        "source": "Fogler: Elements of Chemical Reaction Engineering, Ch. 5",
    },

    {
        "id": "reactor_selectivity",
        "title": "Reaction Selectivity and Yield Optimization",
        "text": (
            "Selectivity S_D = moles desired product / moles key reactant consumed. "
            "Yield Y_D = moles desired product / moles reactant fed. "
            "Point selectivity (instantaneous): s = r_D / r_U (desired vs undesired rate). "
            "For A + B → D (desired) and A → U (undesired): "
            "If r_D = k1*C_A^a1 * C_B^b1 and r_U = k2*C_A^a2: "
            "If a1 > a2: high C_A favors D → use PFR (high C_A throughout). "
            "If a1 < a2: low C_A favors D → use CSTR (dilution). "
            "Temperature: if E_D > E_U → higher T favors desired (most common). "
            "Semibatch reactors: add one reactant slowly to control concentration. "
            "In DWSIM: model as ConversionReactor with specified conversion, "
            "then use ComponentSeparator to represent selectivity split. "
            "Track selectivity in Python post-processing: moles_D / (moles_feed - moles_A_out)."
        ),
        "tags": ["selectivity", "yield", "reaction", "optimization", "CSTR", "PFR",
                 "semibatch", "temperature effect"],
        "source": "Fogler Ch. 6; Levenspiel: Chemical Reaction Engineering",
    },

    {
        "id": "reactor_heat_management",
        "title": "Reactor Heat Management: Adiabatic, Isothermal, Non-Isothermal",
        "text": (
            "Adiabatic reactors: Q = 0; T rise = (-ΔH_rxn * X * F_A0) / (sum(F_i * Cp_i)). "
            "Adiabatic temperature rise ΔT_ad for exothermic reactions can exceed 500°C for "
            "highly exothermic reactions — hot spots and runaway risk. "
            "Isothermal operation: requires heat exchanger (jacket, tubes inside reactor). "
            "Shell-and-tube reactors (multitubular fixed bed): small tube diameter (25-50mm) "
            "for high surface/volume ratio, coolant on shell side. "
            "Quench cooling: inject cold feed between adiabatic stages — used in SO2 oxidation, "
            "methanol synthesis, ammonia synthesis. "
            "Heat exchanger reactor (HEX reactor): reaction + heat exchange in same unit. "
            "Runaway prevention: (1) limit adiabatic temperature rise < 20°C for strongly exothermic, "
            "(2) install safety relief, (3) design cooling capacity > peak rate. "
            "In DWSIM: use Heater/Cooler after adiabatic reactor block to model heat exchange."
        ),
        "tags": ["reactor", "heat management", "adiabatic", "isothermal", "temperature rise",
                 "runaway", "quench", "multitubular", "fixed bed"],
        "source": "Fogler Ch. 8; Perry's Ch. 7",
    },

    {
        "id": "reactor_equilibrium",
        "title": "Chemical Equilibrium and Le Chatelier's Principle",
        "text": (
            "Equilibrium constant Ka = exp(-ΔG°_rxn / (R*T)) relates to Gibbs energy. "
            "Ka depends only on T; increases with T for endothermic reactions (Le Chatelier). "
            "Temperature: endothermic → higher T gives higher conversion. "
            "Exothermic → lower T gives higher equilibrium, but kinetics slower. "
            "Compromise temperature (e.g., NH3 synthesis: 400-500°C). "
            "Pressure: increasing P favors side with fewer moles of gas. "
            "NH3: N2 + 3H2 → 2NH3 (Δn = -2): high P favors NH3. "
            "SO3: SO2 + ½O2 → SO3 (Δn = -0.5): high P helps slightly. "
            "Inerts: reducing inert partial pressure shifts equilibrium toward more moles. "
            "In DWSIM: GibbsReactor minimizes Gibbs energy directly (no need to specify Ka). "
            "For multiple simultaneous reactions: Gibbs minimization handles all simultaneously."
        ),
        "tags": ["equilibrium", "Le Chatelier", "Gibbs", "Ka", "temperature", "pressure",
                 "ammonia synthesis", "GibbsReactor", "ΔG"],
        "source": "Smith Van Ness Abbott, Ch. 13; Fogler Ch. 9",
    },

    # ── Process Control ──────────────────────────────────────

    {
        "id": "process_control_pid",
        "title": "PID Controllers: Tuning and Control Loop Design",
        "text": (
            "PID controller: u(t) = Kc*[e + (1/Ti)*∫e dt + Td*(de/dt)]. "
            "Kc = proportional gain, Ti = integral time (minutes), Td = derivative time. "
            "Ziegler-Nichols tuning (closed loop): find ultimate gain Ku and period Pu. "
            "PID: Kc = 0.6*Ku, Ti = 0.5*Pu, Td = 0.125*Pu. "
            "Cohen-Coon (open loop): use process reaction curve; read dead time theta, "
            "time constant tau, and steady-state gain K. "
            "Common control loops in distillation: "
            "reflux → distillate purity, "
            "reboiler duty → bottoms purity or boilup ratio, "
            "condenser duty → top pressure, "
            "level controls: condenser drum (distillate flow), reboiler (bottoms flow). "
            "For temperature control: cascade preferred (fast inner loop = manipulated variable). "
            "Feed-forward control: compensate for measurable disturbances (feed flow, composition). "
            "Anti-windup: essential when controller output saturates (integrator windup prevention)."
        ),
        "tags": ["PID", "control", "Ziegler-Nichols", "Cohen-Coon", "tuning",
                 "distillation control", "level control", "cascade", "feed-forward"],
        "source": "Seborg, Edgar, Mellichamp: Process Dynamics and Control, Ch. 8-12",
    },

    {
        "id": "process_control_degrees_freedom",
        "title": "Degrees of Freedom Analysis for Process Flowsheets",
        "text": (
            "Degrees of freedom (DOF) = number of unknowns - number of equations. "
            "DOF = 0: system fully specified (can solve). "
            "DOF > 0: under-specified (need more specifications). "
            "DOF < 0: over-specified (inconsistent). "
            "For each stream: NC + 2 variables (T, P, NC compositions). "
            "Each unit op provides equations (mass balance, energy balance, equilibrium). "
            "For a distillation column with N stages, NC components, F feeds: "
            "variables = N*(NC+2) + condensers/reboilers; equations = N*NC + N (MB + EB + equil). "
            "Specifications needed = DOF of the column. "
            "Typical column specifications: feed flow/T/P/composition, reflux ratio, "
            "distillate rate or bottoms purity, condenser pressure. "
            "In DWSIM: each unit op has required inputs listed in its Properties dialog. "
            "Missing required specification → simulation will not converge."
        ),
        "tags": ["degrees of freedom", "DOF", "specification", "flowsheet", "distillation",
                 "mass balance", "simulation", "convergence"],
        "source": "Seider, Seader & Lewin, Ch. 4; Perry's Ch. 8",
    },

    {
        "id": "process_safety_hazop",
        "title": "HAZOP Study and Process Safety Fundamentals",
        "text": (
            "HAZOP (Hazard and Operability Study): systematic technique to identify "
            "process hazards using guide words: MORE, LESS, NO, REVERSE, AS WELL AS, OTHER THAN. "
            "Applied to each node (line, vessel) with each parameter (flow, temperature, pressure, level). "
            "Example: REVERSE FLOW → backflow check valve required. "
            "MORE PRESSURE → relief valve sizing per API 520/521. "
            "Relief valve sizing: Orifice area A = W / (C*K_d*P_1*sqrt(M/(T*Z))) "
            "where W = flow rate, C = gas constant function, K_d = discharge coefficient. "
            "Safety instrumented systems (SIS): SIL 1-4 requirements per IEC 61511. "
            "Two-phase flow relief: use Omega method or DIERS methodology. "
            "Flammability limits: LEL-UEL; methane 5-15%, hydrogen 4-75%. "
            "For DWSIM: add pressure safety valves as Valve unit ops set at relief pressure; "
            "document overpressure scenarios in simulation notes."
        ),
        "tags": ["HAZOP", "safety", "relief valve", "SIS", "SIL", "flammability",
                 "overpressure", "API 520", "process safety"],
        "source": "API 520/521; CCPS Guidelines for Chemical Process Safety",
    },

    # ── Mass Transfer ────────────────────────────────────────

    {
        "id": "mass_transfer_film",
        "title": "Film Theory and Mass Transfer Coefficients",
        "text": (
            "Two-film theory: resistance to mass transfer in both vapor and liquid phases. "
            "Overall coefficient: 1/K_y = 1/k_y + m/k_x "
            "where m = local slope of equilibrium curve, k_y, k_x = individual film coefficients. "
            "Height of a Transfer Unit (HTU): H_OG = G / (K_y * a * S) "
            "where G = vapor flow, a = interfacial area per volume. "
            "Number of Transfer Units: N_OG = integral of dy/(y* - y) from y1 to y2. "
            "Packing height = N_OG * H_OG. "
            "Typical H_OG: 0.3-0.6 m for absorption, 0.5-1.5 m for stripping. "
            "Enhancement factors for reactive absorption (CO2-MEA): "
            "E = sqrt(Ha) for fast reaction where Ha = Hatta number = sqrt(k2*D_A*C_B)/k_L. "
            "In DWSIM: absorption columns use stage efficiency or HETP; "
            "no direct HTU/NTU column block — specify equivalent stages from N_OG/HETP ratio."
        ),
        "tags": ["film theory", "mass transfer", "HTU", "NTU", "absorption", "stripping",
                 "Hatta number", "enhancement factor", "MEA", "CO2"],
        "source": "Perry's Ch. 5; Treybal: Mass Transfer Operations",
    },

    {
        "id": "drying_principles",
        "title": "Drying Operations: Rate, Equipment, and Psychrometrics",
        "text": (
            "Drying removes moisture from solids or liquids. "
            "Drying rate curve: constant rate period (surface evaporation) → falling rate (internal diffusion). "
            "Critical moisture content Xc separates the two periods. "
            "Dryer types: spray dryer (liquid feeds, fine particles), rotary dryer (granular solids), "
            "fluidized bed dryer (uniform particles), drum dryer (slurries), freeze dryer (pharmaceuticals). "
            "Psychrometric chart: humidity, dew point, wet-bulb temperature, enthalpy. "
            "Humidity ratio: H = m_water / m_dry_air = 0.622 * P_v/(P - P_v). "
            "Heat duty: Q = m_dry * [Cs * (T2 - T1) + λ * (X1 - X2)] "
            "where Cs = humid heat = 1.005 + 1.88*H. "
            "Energy efficiency: use exhaust air heat recovery; "
            "heat pump dryers achieve COP 3-5 vs conventional ~1. "
            "In DWSIM: model spray dryer as flash separator + heater combination."
        ),
        "tags": ["drying", "spray dryer", "rotary dryer", "psychrometrics", "humidity",
                 "moisture", "fluidized bed", "heat duty", "drying rate"],
        "source": "Perry's Ch. 12; McCabe Smith, Ch. 24",
    },

    # ── Fluid Mechanics ──────────────────────────────────────

    {
        "id": "fluid_pipe_network",
        "title": "Pipe Network Design and Pressure Drop Calculation",
        "text": (
            "Darcy-Weisbach equation: ΔP = f * (L/D) * (rho*v²/2) + rho*g*ΔZ + ΔP_fittings. "
            "Friction factor f: Moody chart (or Colebrook equation): "
            "1/sqrt(f) = -2*log10(eps/(3.7*D) + 2.51/(Re*sqrt(f))). "
            "Laminar (Re < 2100): f = 64/Re. "
            "Turbulent (Re > 4000): use Moody/Colebrook. "
            "Equivalent length for fittings: elbow = 30D, gate valve (open) = 8D, "
            "globe valve = 340D, check valve = 50-100D. "
            "Pipe sizing heuristics: "
            "Liquids: velocity 1-3 m/s (process), suction < 1 m/s. "
            "Gases: velocity 15-30 m/s. "
            "Steam: velocity 30-50 m/s. "
            "Economic optimum pipe diameter: D_opt = 0.133 * Q^0.40 * rho^0.131 (inches, gpm). "
            "In DWSIM: use Pipe unit op; specify L, D, roughness, fittings K-values."
        ),
        "tags": ["pipe", "pressure drop", "Darcy-Weisbach", "friction factor", "Moody",
                 "Colebrook", "velocity", "pipe sizing", "network"],
        "source": "Perry's Ch. 6; Crane Technical Paper 410",
    },

    {
        "id": "fluid_two_phase_flow",
        "title": "Two-Phase Flow Patterns and Pressure Drop",
        "text": (
            "Two-phase flow regimes in horizontal pipes: "
            "stratified (low velocities), slug/plug, annular (high gas velocity), dispersed bubble. "
            "Baker chart or Mandhane map for flow pattern prediction. "
            "Vertical flow: bubble, slug, churn, annular, mist. "
            "Lockhart-Martinelli parameter X = sqrt(ΔP_L/ΔP_G): "
            "Chisholm correlation for two-phase multiplier: phi_L² = 1 + C/X + 1/X². "
            "C values: 20 (turbulent-turbulent), 12 (laminar-turbulent), 10 (T-L), 5 (L-L). "
            "Void fraction (Zivi): epsilon = 1/(1 + ((1-x)/x)*(rho_G/rho_L)^(2/3)). "
            "Critical (choked) flow: sonic velocity in gas-liquid mixtures, "
            "use homogeneous or drift-flux models. "
            "In DWSIM: Pipe block handles two-phase flow using built-in correlations; "
            "check flow regime output to verify annular or slug conditions."
        ),
        "tags": ["two-phase flow", "flow pattern", "slug flow", "Lockhart-Martinelli",
                 "void fraction", "pressure drop", "annular flow"],
        "source": "Perry's Ch. 6; Brennen: Fundamentals of Multiphase Flow",
    },

    {
        "id": "pump_npsh",
        "title": "Pump NPSH, Cavitation Prevention, and Selection",
        "text": (
            "NPSH_available (NPSHA) = (P_suction - P_vapor) / (rho*g) + v_s²/(2g). "
            "Must have NPSHA > NPSHR (required, from pump curve) + 1 m safety margin. "
            "Cavitation: vapor bubbles form and collapse → erosion, noise, vibration, flow loss. "
            "Prevention: (1) lower pump elevation, (2) raise suction vessel pressure, "
            "(3) cool suction liquid, (4) increase suction line diameter. "
            "Pump selection: centrifugal (high flow, moderate P), "
            "positive displacement (low flow, high P, viscous fluids). "
            "Specific speed Ns = N*sqrt(Q) / H^0.75 (rpm, gpm, ft): "
            "Ns < 500: radial flow. 500-4000: mixed flow. > 4000: axial. "
            "Affinity laws: Q ∝ N, H ∝ N², P ∝ N³. "
            "Efficiency: best efficiency point (BEP) at 70-85% for centrifugal. "
            "In DWSIM: Pump requires outlet pressure and efficiency; NPSH not auto-checked."
        ),
        "tags": ["pump", "NPSH", "cavitation", "centrifugal", "positive displacement",
                 "specific speed", "affinity laws", "efficiency"],
        "source": "Perry's Ch. 10; Karassik: Pump Handbook",
    },

    # ── Environmental and Sustainability ─────────────────────

    {
        "id": "lca_basics",
        "title": "Life Cycle Assessment (LCA) for Chemical Processes",
        "text": (
            "LCA evaluates environmental impacts from cradle to grave (or gate). "
            "Four phases per ISO 14040/14044: "
            "(1) Goal and scope definition — functional unit, system boundaries. "
            "(2) Life cycle inventory (LCI) — energy, material, emission data. "
            "(3) Life cycle impact assessment (LCIA) — characterize impacts "
            "(climate change, eutrophication, acidification, toxicity). "
            "(4) Interpretation — hotspot analysis, sensitivity. "
            "Impact categories: GWP (CO2-eq), AP (SO2-eq), EP (PO4-eq), "
            "ODP, human toxicity, ecotoxicity. "
            "Global Warming Potential (GWP): CO2 = 1, CH4 = 28 (100-yr), N2O = 265. "
            "Common databases: ecoinvent, GaBi, US LCI. "
            "Comparative LCA: compare routes (e.g., bio-ethanol vs fossil ethanol). "
            "In DWSIM: connect to external LCA tools by exporting stream compositions "
            "and energy duties, then multiply by emission factors."
        ),
        "tags": ["LCA", "life cycle", "GWP", "CO2", "environmental", "ecoinvent",
                 "impact assessment", "sustainability", "methane", "N2O"],
        "source": "ISO 14040/14044; Bare et al., J. Ind. Ecol. (2003)",
    },

    {
        "id": "co2_capture_solvents",
        "title": "Post-Combustion CO2 Capture with Chemical Solvents",
        "text": (
            "MEA (monoethanolamine) is the benchmark solvent: 30 wt% solution, "
            "captures CO2 via carbamate formation. Reaction: 2 MEA + CO2 → MEA-carbamate + MEAH+. "
            "Regeneration energy: 3.5-4.0 GJ/t-CO2 (thermal reboiler duty). "
            "Absorber: 15-25 theoretical stages. T = 40-60°C. "
            "Stripper: 3-8 stages. T = 100-130°C. Pressure: 1.5-2 bar. "
            "Solvent degradation: oxidative (O2) and thermal; inhibitors extend life. "
            "Advanced solvents: MDEA (lower energy), piperazine (faster kinetics), "
            "amino acid salts (low volatility). "
            "CO2 compression: captured CO2 at 1.5 bar → pipeline quality at 100+ bar. "
            "Compression energy: ~0.3 GJ/t-CO2 for 5-stage compression. "
            "In DWSIM: model absorber as AbsorptionColumn with MEA-water-CO2-N2 system "
            "using NRTL or modified Kent-Eisenberg for electrolyte thermodynamics."
        ),
        "tags": ["CO2 capture", "MEA", "post-combustion", "CCS", "absorber", "stripper",
                 "amine", "MDEA", "piperazine", "solvent", "regeneration energy"],
        "source": "Metz et al.: IPCC Special Report on CCS; Rochelle, Science 2009",
    },

    {
        "id": "wastewater_treatment",
        "title": "Industrial Wastewater Treatment Processes",
        "text": (
            "Wastewater treatment train for industrial effluent: "
            "(1) Preliminary: screening, grit removal. "
            "(2) Primary: sedimentation, gravity separation (API separator for oil). "
            "(3) Secondary (biological): activated sludge (BOD removal 90-95%), "
            "trickling filters, sequencing batch reactors (SBR). "
            "(4) Tertiary: nutrient removal (N, P), filtration, disinfection. "
            "Key parameters: BOD (biological oxygen demand), COD, TSS, pH, T. "
            "BOD5 test: 5-day oxygen consumption at 20°C. "
            "Activated sludge: F/M ratio 0.05-0.15 kg BOD/(kg MLSS·day). "
            "SRT (sludge retention time): 5-15 days for carbonaceous removal, "
            "15-25 days for nitrification. "
            "Stripping: air or steam stripping for ammonia, phenol, VOCs. "
            "Zero liquid discharge (ZLD): MVR evaporation + crystallization. "
            "In DWSIM: model biological treatment as conversion reactor (BOD → CO2 + H2O)."
        ),
        "tags": ["wastewater", "biological treatment", "BOD", "COD", "activated sludge",
                 "stripping", "ZLD", "effluent", "wastewater treatment"],
        "source": "Metcalf & Eddy: Wastewater Engineering (5th ed.)",
    },

    # ── Electrolysis and Green Hydrogen ──────────────────────

    {
        "id": "electrolysis_thermodynamics",
        "title": "Water Electrolysis Thermodynamics and Efficiency",
        "text": (
            "Water splitting: H2O → H2 + ½O2. "
            "ΔG° = 237.1 kJ/mol at 25°C, 1 bar. "
            "Thermoneutral voltage: V_tn = ΔH/(n*F) = 285.8/(2*96485) = 1.481 V. "
            "Reversible voltage: V_rev = ΔG/(n*F) = 1.229 V at 25°C. "
            "Overpotential losses: activation (Butler-Volmer), ohmic (iR), concentration. "
            "Cell voltage: V_cell = V_rev + η_anode + η_cathode + i*R_ohmic. "
            "Faraday efficiency: η_F = n_H2_actual / n_H2_theoretical (typically 0.98-0.99). "
            "Energy efficiency: η = V_tn / V_cell (typically 60-80% for alkaline). "
            "H2 production: n_H2 = I * t / (2 * F) mol, or at 1A current: 5.18 mL/s H2. "
            "Technologies: alkaline (V_cell 1.8-2.4V), PEM (1.6-2.2V, dynamic response), "
            "SOEC (700-900°C, η up to 90%). "
            "Specific energy: alkaline 50-60 kWh/kg-H2, PEM 55-70 kWh/kg-H2. "
            "In DWSIM: model WaterElectrolyzer or use GibbsReactor at 80°C with power input "
            "set as unit op property."
        ),
        "tags": ["electrolysis", "water splitting", "hydrogen", "Faraday", "PEM",
                 "alkaline", "SOEC", "energy efficiency", "overpotential", "green hydrogen"],
        "source": "Bockris: Modern Electrochemistry; IEA Hydrogen Report 2023",
    },

    {
        "id": "green_hydrogen_economics",
        "title": "Green Hydrogen Production Costs and Competitiveness",
        "text": (
            "Green hydrogen cost breakdown (2023): "
            "Electrolyzer CAPEX: ~700-1200 $/kW for PEM, ~500-900 $/kW alkaline. "
            "Stack lifetime: 60,000-100,000 hours. Replacement cost: ~40% of CAPEX. "
            "Electricity cost dominates (~70-80% of green H2 cost). "
            "At 50 $/MWh electricity: green H2 ≈ 4-6 $/kg. "
            "At 20 $/MWh (curtailed renewables): green H2 ≈ 2-3 $/kg. "
            "Blue hydrogen (SMR + CCS): 1.5-2.5 $/kg. "
            "Grey hydrogen (SMR no CCS): 0.8-1.5 $/kg. "
            "Breakeven conditions for green H2: electricity < 30 $/MWh + high carbon price. "
            "2030 targets: IEA Green Hydrogen at 2 $/kg requires: electrolyzer cost < 300 $/kW "
            "and electricity cost < 20 $/MWh. "
            "Capacity factor: higher utilization reduces fixed cost per kg; "
            "optimal electrolyzer sizing vs. renewable intermittency is key design variable."
        ),
        "tags": ["green hydrogen", "cost", "electrolyzer", "CAPEX", "electricity cost",
                 "PEM", "alkaline", "blue hydrogen", "competitiveness", "LCOH"],
        "source": "IEA Global Hydrogen Review 2023; IRENA Hydrogen Report 2022",
    },

    # ── Process Economics ────────────────────────────────────

    {
        "id": "capex_lang_factor",
        "title": "Plant Cost Estimation: Lang Factor and CAPCOST Method",
        "text": (
            "Lang factor method: Total Fixed Capital Cost (TFCC) = f_Lang * sum(Purchased Equipment Cost). "
            "f_Lang: 3.1 (solid processing), 4.7 (solid-fluid processing), 5.5 (fluid processing). "
            "Purchased equipment cost from Marshall & Swift index or Peters & Timmerhaus correlations. "
            "Equipment cost correlation: C_E = C_E,ref * (S/S_ref)^n "
            "where n = cost exponent (0.6 for vessels, 0.8 for compressors). "
            "Six-tenths rule: doubling capacity → cost increases by 2^0.6 = 1.52x. "
            "CAPCOST method (Turton et al.): "
            "C_BM = C_P^0 * F_BM where F_BM = bare module factor = f(pressure, material). "
            "Equipment-specific correlations in Turton Table A-1. "
            "CEPCI (Chemical Engineering Plant Cost Index): update historical costs "
            "to current year: C_new = C_old * (CEPCI_new/CEPCI_old). "
            "CEPCI 2001 = 394, 2010 = 550, 2020 = 596, 2023 ≈ 800. "
            "Contingency and contractor fee: +15-20% of TDC (total direct cost)."
        ),
        "tags": ["CAPEX", "Lang factor", "cost estimation", "CEPCI", "equipment cost",
                 "six-tenths rule", "CAPCOST", "Turton", "plant cost"],
        "source": "Turton et al.: Analysis Synthesis Design, App. A; Peters & Timmerhaus",
    },

    {
        "id": "opex_economics",
        "title": "Operating Cost (OPEX) Analysis and Profitability Metrics",
        "text": (
            "Annual OPEX components: "
            "Raw materials (largest for commodity chemicals, 60-80% of OPEX), "
            "utilities (steam, electricity, cooling water, fuel), "
            "labor (2-10% of OPEX for automated plants), "
            "maintenance (3-5% of CAPEX/yr), "
            "overhead (20-30% of labor + maintenance). "
            "Profitability metrics: "
            "NPV (Net Present Value) = sum of (Cash Flow_t / (1+i)^t) - CAPEX_0. "
            "Positive NPV → project adds value. "
            "IRR (Internal Rate of Return): i where NPV = 0. "
            "Payback period: CAPEX / (Annual Net Profit). "
            "Typical hurdle rates: 10-15% for refinery debottlenecking, "
            "15-20% for new greenfield chemical plant. "
            "ROI = Net Annual Profit / Total Capital Investment. "
            "Manufacturing cost = OPEX / production rate. "
            "Variable cost = raw materials + utilities (scales with production). "
            "Fixed cost = labor + maintenance + overhead (independent of production)."
        ),
        "tags": ["OPEX", "operating cost", "NPV", "IRR", "payback", "profitability",
                 "raw materials", "utilities", "ROI", "economics"],
        "source": "Turton et al., Ch. 8-9; Peters & Timmerhaus, Ch. 9",
    },

    # ── Advanced Topics ──────────────────────────────────────

    {
        "id": "pinch_analysis",
        "title": "Pinch Analysis and Heat Exchanger Network Optimization",
        "text": (
            "Pinch analysis systematically minimizes energy consumption in process HENs. "
            "Composite curves: hot composite (all hot streams) vs cold composite; "
            "the closest approach T = ΔT_min (pinch temperature). "
            "Minimum utility targets: Q_H,min (hot utility above pinch), Q_C,min (cold below pinch). "
            "Pinch rules: (1) no heat transfer across pinch, (2) no hot utility below pinch, "
            "(3) no cold utility above pinch — violation increases utility consumption. "
            "ΔT_min selection: 10-20°C for process-process (liquid), 20-30°C for gas-liquid. "
            "HEN design: maximum energy recovery (MER) — match hot and cold streams at pinch. "
            "Grand Composite Curve: shows pocket regions where process can self-integrate "
            "before needing external utilities. "
            "Software: HINT, Aspen Energy Analyzer, SuperTarget. "
            "In DWSIM: manually design HEN from pinch targets; no built-in pinch analysis."
        ),
        "tags": ["pinch analysis", "heat exchanger network", "HEN", "composite curve",
                 "minimum utility", "ΔT_min", "energy integration", "Grand Composite"],
        "source": "Linnhoff: Pinch Analysis for the Design of Energy-Efficient Processes",
    },

    {
        "id": "exergy_analysis",
        "title": "Exergy Analysis and Thermodynamic Efficiency",
        "text": (
            "Exergy = maximum useful work obtainable as system reaches equilibrium with environment. "
            "Exergy = (H - H0) - T0*(S - S0) for closed system (T0 = dead state temperature ~298 K). "
            "Exergy balance: Ex_in = Ex_out + Ex_destroyed (irreversibility = T0 * S_gen). "
            "Exergy efficiency: eta_ex = Ex_product / Ex_in = 1 - (T0*S_gen)/Ex_in. "
            "Exergy destruction in unit ops: "
            "HX: Ex_destroyed = T0 * Q * (1/T_cold - 1/T_hot). Minimized by reducing ΔT. "
            "Mixing: always irreversible (entropy generation from composition equalization). "
            "Reaction: Ex_destroyed = T0 * S_gen from reaction kinetics + mixing. "
            "Exergy analysis helps identify where losses are largest → guide for improvement. "
            "In DWSIM: calculate exergy manually using stream H and S values from results; "
            "Python script: Ex = H - H_ref - T_ref*(S - S_ref) per stream."
        ),
        "tags": ["exergy", "availability", "thermodynamic efficiency", "irreversibility",
                 "entropy generation", "dead state", "exergy destruction"],
        "source": "Moran & Shapiro: Fundamentals of Engineering Thermodynamics, Ch. 7",
    },

    {
        "id": "cfd_basics",
        "title": "CFD Fundamentals for Chemical Engineers",
        "text": (
            "CFD (Computational Fluid Dynamics) solves Navier-Stokes equations numerically. "
            "Conservation equations: mass (continuity), momentum (NS), energy, species. "
            "Turbulence models: k-epsilon (industrial default), k-omega (near-wall flows), "
            "LES (large eddy simulation, expensive), DNS (direct numerical simulation, research). "
            "Mesh types: structured (hexahedral, more accurate), unstructured (tetrahedral, complex geometries). "
            "y+ criterion for wall treatment: y+ < 1 for low-Re models, y+ 30-300 for wall functions. "
            "Grid independence study: refine until solution changes < 1-2%. "
            "Applications in ChE: reactor mixing (CSTR dead zones), heat exchanger maldistribution, "
            "cyclone efficiency, spray drying, fluidized beds. "
            "Software: OpenFOAM (free), ANSYS Fluent/CFX, STAR-CCM+, COMSOL Multiphysics. "
            "Coupling with DWSIM: boundary conditions from DWSIM stream properties → CFD → "
            "validate mixing assumptions. Not natively integrated."
        ),
        "tags": ["CFD", "Navier-Stokes", "turbulence", "k-epsilon", "mesh",
                 "OpenFOAM", "ANSYS Fluent", "mixing", "reactor", "fluidized bed"],
        "source": "Versteeg & Malalasekera: Introduction to CFD (2nd ed.)",
    },

    {
        "id": "polymer_processing",
        "title": "Polymer Reaction Engineering and Processing",
        "text": (
            "Addition polymerization: initiation, propagation, termination. "
            "Chain length distribution: Schulz-Flory for step-growth (PDI = 2), "
            "Poisson for living polymerization (PDI → 1). "
            "Reactor types: bulk polymerization (high conversion, viscosity issue), "
            "solution polymerization (dilution reduces viscosity, solvent recovery needed), "
            "suspension polymerization (droplets in water, PS, PVC), "
            "emulsion polymerization (surfactant, latex products). "
            "Glass transition Tg: below Tg → glassy, above → rubbery. "
            "Fox equation for copolymer Tg: 1/Tg = w1/Tg1 + w2/Tg2. "
            "Molecular weight control: chain transfer agents, inhibitors, temperature. "
            "In DWSIM: polymerization not natively modeled; "
            "use ConversionReactor for monomer conversion, set product properties manually."
        ),
        "tags": ["polymer", "polymerization", "chain growth", "PDI", "Tg", "glass transition",
                 "emulsion", "suspension", "molecular weight"],
        "source": "Odian: Principles of Polymerization (4th ed.); Perry's Ch. 24",
    },

    {
        "id": "bioprocess_fermentation",
        "title": "Bioprocess Engineering: Fermentation and Bioreactor Design",
        "text": (
            "Bioreactor types: stirred tank (STR, most common), bubble column, airlift, "
            "packed bed (immobilized cells), membrane bioreactor. "
            "Monod kinetics: mu = mu_max * C_S / (K_S + C_S) "
            "where mu = specific growth rate (h^-1), K_S = half-saturation constant. "
            "Yield coefficients: Y_X/S = biomass produced / substrate consumed. "
            "Oxygen transfer: OTR = k_L * a * (C* - C_L) [mol/L/h]. "
            "k_L * a (volumetric mass transfer): 50-500 h^-1 for well-mixed STR. "
            "Aeration: sparger, agitator (Rushton turbine for O2 transfer). "
            "Sterilization: SIP (steam-in-place), filter sterilization of air. "
            "Scale-up rules: constant P/V (power per volume), constant k_L*a, "
            "constant tip speed, or constant Re — each gives different scale behavior. "
            "DWSIM: model as ConversionReactor with biological substrate-to-product conversion."
        ),
        "tags": ["fermentation", "bioreactor", "Monod", "k_La", "oxygen transfer",
                 "biomass", "yield", "scale-up", "bioprocess", "STR"],
        "source": "Shuler & Kargi: Bioprocess Engineering (2nd ed.); Perry's Ch. 27",
    },

    {
        "id": "crystallization_design",
        "title": "Crystallization Design: Supersaturation and Crystal Size Distribution",
        "text": (
            "Crystallization driving force: supersaturation S = C/C_sat or Δ = C - C_sat. "
            "Primary nucleation: homogeneous (spontaneous) or heterogeneous (on surfaces). "
            "Secondary nucleation: from existing crystals (dominant in industrial crystallizers). "
            "Crystal growth rate G = k_g * (C - C_sat)^g where g = 1-2. "
            "Population balance equation (PBE) gives CSD (crystal size distribution). "
            "Equipment: MSMPR (mixed suspension mixed product removal, continuous), "
            "draft tube baffle (DTB), forced circulation crystallizer, evaporative crystallizer. "
            "Yield: Y = F * (x_feed - x_sat_T2) / (1 - R * x_sat_T2) "
            "where R = solvent lost by evaporation. "
            "Washing and filtration: crystal cake washing removes mother liquor. "
            "Agglomeration: reduce by controlling S < S_crit, add habit modifier. "
            "In DWSIM: model as flash drum + solid separator; set crystallization yield manually."
        ),
        "tags": ["crystallization", "supersaturation", "nucleation", "crystal growth",
                 "CSD", "MSMPR", "yield", "population balance", "draft tube"],
        "source": "Mullin: Crystallization (4th ed.); McCabe Smith, Ch. 27",
    },

    {
        "id": "dwsim_sensitivity_analysis",
        "title": "DWSIM Sensitivity Analysis and Parametric Study",
        "text": (
            "DWSIM supports sensitivity analysis (parametric studies) via: "
            "(1) Python scripting: loop over parameter values, re-solve, extract results. "
            "(2) Optimization tool: built-in optimizer for min/max objective functions. "
            "(3) Excel integration: Excel add-in or Python openpyxl for result export. "
            "Typical parametric study workflow: "
            "for param in np.linspace(p_min, p_max, n_points): "
            "    set_unit_op_property(tag, prop, param) "
            "    run_simulation() "
            "    results.append(get_stream_properties(stream_tag)). "
            "In the AI agent: use get_stream_properties after run_simulation to "
            "interrogate results; loop by calling run_simulation multiple times. "
            "Key variables for distillation study: reflux ratio vs reboiler duty, "
            "feed stage vs separation. "
            "Report format: table of [param_value, T_product, P_product, composition] "
            "for thesis documentation."
        ),
        "tags": ["sensitivity analysis", "parametric study", "DWSIM", "Python scripting",
                 "optimization", "loop", "run_simulation", "reflux ratio"],
        "source": "DWSIM Documentation: Scripting and Automation",
    },

    {
        "id": "dwsim_convergence_advanced",
        "title": "Advanced DWSIM Convergence Troubleshooting",
        "text": (
            "Common DWSIM convergence failures and fixes: "
            "(1) 'Flash calculation did not converge': wrong property package for system. "
            "Fix: switch from PR to NRTL for polar mixtures; check compound list. "
            "(2) 'Recycle did not converge after N iterations': loose tolerance or wrong initial guess. "
            "Fix: set recycle tolerance to 1e-4, increase max iterations to 50, "
            "provide good initial estimate for recycle stream T/P/composition. "
            "(3) 'Column not converging': poor initialization. "
            "Fix: run shortcut first → use results to initialize rigorous column. "
            "Set reflux = 2 * minimum reflux as starting point. "
            "(4) 'Solver returned NaN': stream has zero molar flow or negative T/P. "
            "Fix: check feed stream specs; all molar flows must be > 0. "
            "(5) Gibbs reactor not converging for inert-heavy feed: "
            "Fix: set temperature higher (> 200°C) and reduce inerts. "
            "General tip: solve subsections of flowsheet progressively before linking all streams."
        ),
        "tags": ["DWSIM", "convergence", "troubleshooting", "flash", "recycle", "column",
                 "NaN", "Gibbs reactor", "initialization", "debugging"],
        "source": "DWSIM Community Forum; Internal testing",
    },

    {
        "id": "simulation_validation",
        "title": "Process Simulation Validation and Model Verification",
        "text": (
            "Validation hierarchy: (1) unit operation level — compare to published data or bench tests, "
            "(2) section level — compare section mass/energy balances, "
            "(3) plant level — compare to actual plant data (DCS historian). "
            "Cross-checks for mass balance: "
            "In = Out for each component across any system boundary. "
            "Energy balance: Net heat + Work = ΔEnthalpy of streams. "
            "Common errors to check: "
            "- Composition normalization: ensure sum(xi) = 1 for all streams. "
            "- Basis consistency: all flows in same units (kmol/h or kg/h). "
            "- Reaction stoichiometry: atom balance must close. "
            "Benchmarking: compare DWSIM results to Aspen or HYSYS for same flowsheet. "
            "Acceptance criteria: < 1% error on major flows, < 5°C on temperatures. "
            "Uncertainty: EOS uncertainty ±5-10% on VLE; propagates to product purity. "
            "Document assumptions: ideal stages, constant efficiency, no heat losses."
        ),
        "tags": ["validation", "verification", "mass balance", "energy balance",
                 "cross-check", "Aspen", "HYSYS", "accuracy", "benchmarking"],
        "source": "Seider, Seader & Lewin, Ch. 2; AIChE Guidelines for Process Simulation",
    },

    {
        "id": "natural_gas_processing",
        "title": "Natural Gas Processing: Sweetening, Dehydration, NGL Extraction",
        "text": (
            "Raw natural gas composition: 70-90% CH4, 5-15% C2+, CO2, H2S, N2, H2O. "
            "Gas sweetening (acid gas removal): amine absorption (DEA, MDEA) removes H2S and CO2. "
            "H2S spec for sales gas: < 4 ppm vol. CO2 < 2-3%. "
            "Dehydration: glycol (TEG) absorption — water dew point to -10 to -40°C. "
            "TEG circulation rate: 20-50 L TEG / kg H2O removed. "
            "NGL (natural gas liquids) recovery: "
            "Joule-Thomson expansion (> 70% C3+ recovery), "
            "turboexpander (> 95% C3+ recovery at -90°C), "
            "lean oil absorption (older technology). "
            "LPG fractionation: deethanizer → depropanizer → debutanizer. "
            "LNG liquefaction: coolants N2, mixed refrigerant (APCI C3-MR process). "
            "In DWSIM: use PR EOS for gas processing; "
            "MDEA absorption column + TEG dehydration as separate flowsheets."
        ),
        "tags": ["natural gas", "sweetening", "amine", "MDEA", "DEA", "dehydration",
                 "TEG", "NGL", "LPG", "LNG", "turboexpander"],
        "source": "Campbell: Gas Conditioning and Processing (9th ed.); Perry's Ch. 27",
    },

    {
        "id": "refinery_processes",
        "title": "Key Refinery Processes Overview",
        "text": (
            "Crude distillation unit (CDU): separates crude into fractions at atmospheric pressure. "
            "Products: naphtha (30-175°C), kerosene (175-250°C), gasoil (250-350°C), residue (>350°C). "
            "Vacuum distillation (VDU): processes CDU residue at 5-20 mbar. "
            "Fluid Catalytic Cracking (FCC): converts heavy gasoil to gasoline, LPG at 520°C, zeolite catalyst. "
            "Hydrocracking: heavy fractions + H2 at 380-420°C, 100-200 bar → gasoline/kerosene. "
            "Catalytic reforming: naphtha → reformate (high octane); produces H2 as byproduct. "
            "Hydrotreating: removes S, N, metals from feeds; protects downstream catalysts. "
            "Alkylation: isobutane + olefins → alkylate (high octane, low RVP). "
            "In DWSIM: crude distillation modeled as multiple flash stages + shortcut columns; "
            "use PR EOS for hydrocarbon-only streams, NRTL-PR for water-containing streams."
        ),
        "tags": ["refinery", "crude distillation", "FCC", "hydrocracking", "reforming",
                 "hydrotreating", "alkylation", "vacuum distillation", "petroleum"],
        "source": "Gary & Handwerk: Petroleum Refining (5th ed.); Perry's Ch. 27",
    },

    {
        "id": "ammonia_synthesis_process",
        "title": "Haber-Bosch Ammonia Synthesis Process",
        "text": (
            "Haber-Bosch process: N2 + 3H2 → 2NH3. ΔH = -92 kJ/mol (exothermic). "
            "Equilibrium-limited; lower T and higher P favor conversion but kinetics are slow at low T. "
            "Industrial conditions: 350-550°C, 150-300 bar, Fe-based catalyst (KOH promoted). "
            "Single-pass conversion: 15-25%; recycling unconverted N2+H2 achieves overall 97%+ efficiency. "
            "Synthesis loop: compressor → reactor (multi-bed adiabatic, quench cooled) → "
            "condenser (NH3 condensed at -20 to 0°C) → liquid NH3 separator → recycle. "
            "Purge stream needed to remove inerts (Ar, CH4) from loop. "
            "Feed: stoichiometric H2:N2 = 3:1 (with slight excess N2 in practice). "
            "Energy integration: reactor outlet gas (hot) preheats reactor inlet gas. "
            "In DWSIM: model as GibbsReactor at 450°C, 200 bar → flash (NH3 condensation) → "
            "Recycle block → compressor for loop pressure."
        ),
        "tags": ["ammonia synthesis", "Haber-Bosch", "nitrogen", "hydrogen", "catalyst",
                 "equilibrium", "recycle loop", "high pressure", "synthesis loop"],
        "source": "Appl: Ammonia: Principles and Industrial Practice; Ullmann's Encyclopedia",
    },

    {
        "id": "methanol_synthesis",
        "title": "Methanol Synthesis from Syngas",
        "text": (
            "Methanol synthesis: CO + 2H2 → CH3OH. ΔH = -90.5 kJ/mol. "
            "Also: CO2 + 3H2 → CH3OH + H2O. ΔH = -49.5 kJ/mol. "
            "Catalyst: Cu/ZnO/Al2O3. Active at 200-300°C, 50-100 bar. "
            "Equilibrium conversion per pass: 15-25%. High recycle ratio (5:1). "
            "Industrial reactor: Lurgi multi-tube (isothermal, boiling water cooling), "
            "ICI quench cooled (adiabatic beds with cold syngas quench). "
            "Purge: 5-10% of recycle to remove CH4, N2, Ar. "
            "Distillation: crude MeOH (92-94%) → light ends column (removes DME) → "
            "methanol column (99.9% pure). "
            "Stoichiometric number: SN = (H2 - CO2) / (CO + CO2) = 2.03-2.10 optimal. "
            "In DWSIM: GibbsReactor at 250°C, 80 bar → flash (methanol condensation) → "
            "recycle loop using OT_Recycle block."
        ),
        "tags": ["methanol", "syngas", "CO", "hydrogen", "Cu/ZnO catalyst", "Lurgi",
                 "recycle", "purge", "synthesis", "distillation"],
        "source": "Ullmann's Encyclopedia: Methanol; Spath & Dayton NREL Report",
    },

    {
        "id": "ethanol_production",
        "title": "Bioethanol Production from Lignocellulosic Biomass",
        "text": (
            "First-generation (1G) ethanol: from sugar cane or corn starch → fermentation → distillation. "
            "Second-generation (2G) ethanol from lignocellulose: "
            "Pretreatment (dilute acid, steam explosion) → enzymatic hydrolysis → fermentation → distillation. "
            "Sugars: hexoses (glucose, C6) fermented by S. cerevisiae; "
            "pentoses (xylose, C5) require engineered organisms. "
            "Theoretical yield: 0.51 g EtOH / g glucose (EMP pathway). "
            "Industrial yield: 0.45-0.48 g/g (90-95% theoretical). "
            "Fermentation: pH 4.5-5.5, T 30-37°C, yeast loading 1-5 g/L. "
            "Distillation: beer column (12% EtOH → 95%) + rectifier + dehydration (mol sieve). "
            "Mass balance: 1 tonne dry biomass → ~270 kg ethanol. "
            "In DWSIM: ConversionReactor (glucose → EtOH + CO2), "
            "then ShortcutColumn for distillation."
        ),
        "tags": ["ethanol", "bioethanol", "fermentation", "lignocellulose", "distillation",
                 "S. cerevisiae", "dehydration", "molecular sieve", "biomass"],
        "source": "Wyman: Handbook on Bioethanol; Mosier et al., Bioresource Technology 2005",
    },

    # ── CPA Equation of State (extended) ────────────────────
    {
        "id": "thermo_cpa_extended",
        "title": "Cubic Plus Association (CPA) Equation of State",
        "text": (
            "CPA (Cubic Plus Association) EOS extends SRK with an explicit "
            "association term for hydrogen-bonding compounds (alcohols, water, "
            "glycols, amines, acids). Essential for accurate VLE in: "
            "water-hydrocarbon systems (oil/gas dehydration), glycol dehydration "
            "(TEG-water-methane), methanol injection (hydrate inhibition), "
            "fatty acid and biodiesel systems. "
            "When to prefer CPA over PR/SRK: any system containing water + "
            "non-polar hydrocarbons at elevated pressures, or alcohols/glycols "
            "at any conditions. CPA uses SRK as the physical term plus a "
            "Wertheim perturbation term for association. "
            "In DWSIM, select 'CPA' from the property package list. "
            "Binary interaction parameters (kij) are pre-fitted for common "
            "water-hydrocarbon pairs. For new systems, regress kij from "
            "experimental VLE data. "
            "CPA is computationally 3-5x slower than PR/SRK due to the "
            "iterative association solve — expect longer simulation times."
        ),
        "tags": ["CPA", "cubic plus association", "water", "hydrogen bonding",
                 "glycol", "TEG", "alcohol", "hydrate", "EOS", "association",
                 "property package", "Wertheim"],
        "source": "Kontogeorgis & Folas: Thermodynamic Models for Industrial Applications, Wiley 2010",
    },

    # ── Process Intensification ────────────────────────────
    {
        "id": "process_intensification",
        "title": "Process Intensification Principles",
        "text": (
            "Process intensification (PI) combines multiple functions (reaction, "
            "separation, heat exchange) into a single unit to reduce equipment "
            "count, energy use, and capital cost. Key PI technologies: "
            "(1) Reactive distillation: simultaneous reaction + separation in "
            "one column. Applicable when reaction and separation temperatures "
            "overlap. Classic examples: MTBE, ethyl acetate, methyl acetate. "
            "Advantages: overcomes equilibrium limitation, removes product "
            "immediately. Model in DWSIM with Reactive Distillation Column. "
            "(2) Dividing wall columns (DWC): thermodynamically equivalent to "
            "two columns but uses one shell. Saves 20-35% energy for ternary "
            "separations. Not directly supported in DWSIM — approximate with "
            "two coupled columns. "
            "(3) Heat-integrated distillation columns (HIDiC): internal heat "
            "pump; stripping section heats rectifying section. Saves up to "
            "50% energy but requires pressure differential. "
            "(4) Microreactors: excellent heat/mass transfer due to high "
            "surface-to-volume ratio. Best for highly exothermic or fast "
            "reactions. Not directly in DWSIM — use PFR with adjusted "
            "heat transfer coefficients. "
            "Design heuristics: PI is most valuable when capital cost "
            "dominates (high-value products), safety constraints require "
            "small inventories, or energy costs are the dominant OPEX."
        ),
        "tags": ["process intensification", "reactive distillation", "dividing wall column",
                 "DWC", "HIDiC", "microreactor", "heat pump", "MTBE", "intensification"],
        "source": "Stankiewicz & Moulijn: Chemical Engineering Progress, 2000; Sundmacher & Kienle (eds.)",
    },

    # ── UNIQUAC vs NRTL selection ──────────────────────────
    {
        "id": "thermo_nrtl_vs_uniquac_selection",
        "title": "Choosing Between NRTL and UNIQUAC for Polar Mixtures",
        "text": (
            "Both NRTL and UNIQUAC are local-composition activity coefficient "
            "models suitable for polar, non-electrolyte systems. Key differences: "
            "NRTL (Non-Random Two-Liquid): "
            "  - 2 interaction parameters per binary pair (τ12, τ21) + non-randomness α "
            "  - α usually fixed at 0.2 (hydrocarbons), 0.3 (polar), 0.47 (LLE) "
            "  - Excellent for VLE and LLE including partially miscible systems "
            "  - Recommended when LLE data available for regression "
            "  - Better for aqueous-organic systems with liquid-liquid splitting "
            "UNIQUAC (Universal Quasi-Chemical): "
            "  - 2 interaction parameters per binary + molecular size/shape (r, q) "
            "  - r and q from group volumes (Bondi method) — available in databases "
            "  - Better for polymer-solvent systems and mixtures with large size differences "
            "  - More physically meaningful parameters — extrapolates better "
            "  - Preferred when size/shape differences are large (solvent + polymer) "
            "UNIFAC (predictive): "
            "  - Group contribution method — no binary parameter regression needed "
            "  - Use when NO experimental data available "
            "  - Less accurate than NRTL/UNIQUAC with fitted parameters "
            "  - Available in DWSIM as 'UNIFAC' and 'Modified UNIFAC (Dortmund)' "
            "Decision guide: Use NRTL for aqueous+polar systems with data; "
            "UNIQUAC when molecular sizes differ greatly; UNIFAC when no data."
        ),
        "tags": ["NRTL", "UNIQUAC", "UNIFAC", "activity coefficient", "local composition",
                 "polar", "VLE", "LLE", "liquid-liquid equilibrium", "binary parameters",
                 "property package selection"],
        "source": "Prausnitz, Lichtenthaler & Azevedo: Molecular Thermodynamics, 3rd ed.; Gmehling et al.",
    },

    # ── Pressure Drop Engineering ──────────────────────────
    {
        "id": "pressure_drop_engineering",
        "title": "Pressure Drop: Pipes, Packed Beds, and Columns",
        "text": (
            "Pressure drop is critical for pump/compressor sizing and column "
            "hydraulics. Key correlations: "
            "Pipe flow (Darcy-Weisbach): ΔP = f × (L/D) × (ρv²/2) "
            "  - f = friction factor; Moody chart or Churchill correlation "
            "  - Turbulent: f ≈ 0.316 Re^(-0.25) (Blasius, Re < 100,000) "
            "  - Design velocity: liquids 1-3 m/s; gases 10-30 m/s "
            "Packed beds (Ergun equation): "
            "  ΔP/L = 150μv(1-ε)²/(dp²ε³) + 1.75ρv²(1-ε)/(dpε³) "
            "  - dp = particle diameter; ε = void fraction (~0.4 for random packing) "
            "  - First term: viscous losses (low Re); second: inertial (high Re) "
            "Distillation columns: "
            "  - Tray columns: 5-10 mbar/tray for sieve trays; target <70% flooding "
            "  - Packed columns: 2-4 mbar/m at 70% flooding; use structured packing "
            "    for vacuum or low-pressure drop requirements "
            "  - Flooding check: F-factor = v√ρ_vapor < 2.5 Pa^0.5 (typical) "
            "In DWSIM: set DeltaP on pipes using Pipe Segment with Darcy friction. "
            "For columns, set pressure profile in Column Specifications. "
            "Rule of thumb: total column ΔP < 10% of bottom pressure."
        ),
        "tags": ["pressure drop", "Darcy-Weisbach", "Ergun", "packed bed", "pipe flow",
                 "friction factor", "flooding", "tray column", "packed column",
                 "hydraulics", "pump sizing"],
        "source": "Perry's 9th ed., Sec. 6; Billet: Packed Towers, VCH 1995",
    },

    # ── Sensitivity Analysis vs Parametric Study ───────────
    {
        "id": "sensitivity_vs_parametric",
        "title": "Sensitivity Analysis vs. Parametric Study in Process Simulation",
        "text": (
            "Sensitivity analysis and parametric studies are complementary "
            "simulation techniques with different purposes: "
            "Parametric study (one-at-a-time, OAT): "
            "  - Vary ONE parameter over a range while keeping others fixed "
            "  - Output: trend curve showing how one output changes with one input "
            "  - Use for: equipment sizing curves, temperature vs. conversion, "
            "    reflux ratio vs. purity, pressure vs. energy consumption "
            "  - In DWSIM agent: use parametric_study tool "
            "  - Limitation: misses interaction effects between variables "
            "Global sensitivity analysis (Sobol, Morris): "
            "  - Vary all parameters simultaneously; quantifies interaction effects "
            "  - Computationally expensive (N×(2k+2) simulations for Sobol, k=params) "
            "  - Not built into DWSIM agent — requires external Python script "
            "Design of Experiments (DoE): "
            "  - Factorial or response-surface designs for multi-variable optimisation "
            "  - 2^k full factorial: evaluates all combinations of k factors at 2 levels "
            "  - Central Composite Design (CCD): fits second-order response surface "
            "  - Use when optimising >2 variables simultaneously "
            "Uncertainty analysis (Monte Carlo): "
            "  - Propagate feed composition uncertainty → output variance "
            "  - Essential for reporting confidence intervals in published results "
            "For journal papers: report both OAT parametric results AND identify "
            "whether variable interactions are significant."
        ),
        "tags": ["sensitivity analysis", "parametric study", "DOE", "design of experiments",
                 "Monte Carlo", "Sobol", "response surface", "factorial", "uncertainty",
                 "one-at-a-time", "optimization"],
        "source": "Saltelli et al.: Global Sensitivity Analysis, Wiley 2008; Box, Hunter & Hunter",
    },

    # ── Bayesian Optimisation Guide ────────────────────────
    {
        "id": "bayesian_optimization_dwsim",
        "title": "Bayesian Optimisation for DWSIM: When and How to Use It",
        "text": (
            "Bayesian Optimisation (BO) is the preferred method for optimising "
            "DWSIM operating conditions when simulations are expensive (>10 s each) "
            "and you have 1–5 decision variables. "
            "How BO works: "
            "(1) Latin Hypercube Sampling (LHS) evaluates n_initial diverse points "
            "to explore the design space broadly. "
            "(2) A Gaussian Process (GP) surrogate model is fit to the observed data — "
            "it predicts the mean AND uncertainty of the objective at any unsampled point. "
            "(3) Expected Improvement (EI) acquisition identifies the next point that "
            "is most likely to improve on the current best (balances exploration/exploitation). "
            "(4) Evaluate the objective there, update the GP, repeat max_iter times. "
            "Total evaluations: n_initial + max_iter (default 5 + 20 = 25). "
            "Compared to alternatives: "
            "optimize_parameter (1D scalar, SciPy bounded): use for single-variable problems. "
            "bayesian_optimize (1–5 vars, GP-BO): BEST for expensive simulations. "
            "optimize_multivar (1–10 vars, differential_evolution): needs 100+ evaluations, "
            "but more robust for highly discontinuous or multi-modal landscapes. "
            "parametric_study (1D grid sweep): for visualization, NOT optimization. "
            "Decision guide: "
            "- 1 variable: use optimize_parameter. "
            "- 2–5 variables, smooth landscape: use bayesian_optimize (25 evaluations). "
            "- 2–10 variables, noisy/multimodal: use optimize_multivar (100+ evaluations). "
            "- Need to visualize trends: use parametric_study. "
            "BO convergence indicator: surrogate_r2 > 0.7 means the GP has a good fit. "
            "If R² is low, increase n_initial (more exploration needed). "
            "The convergence_curve in the output shows best objective per evaluation — "
            "a flat tail means convergence; downward steps mean the optimizer is still improving."
        ),
        "tags": ["Bayesian optimization", "Gaussian process", "Expected Improvement",
                 "Latin Hypercube", "surrogate model", "DWSIM optimization",
                 "optimize", "minimize", "maximize", "operating conditions",
                 "expensive simulation", "black-box optimization"],
        "source": "Shahriari et al.: Taking the Human Out of the Loop, IEEE 2016; Jones et al.: Efficient Global Optimization, JOGO 1998",
    },

    # ══════════════════════════════════════════════════════════
    # COMPOUND THERMODYNAMIC PROPERTIES
    # Source: DIPPR 801 (2023), Perry's 9th ed., NIST WebBook
    # Format per chunk: Tc, Pc, Vc, ω, NBP, Antoine (A,B,C in
    # °C / mmHg unless noted), NRTL BIPs with water where known,
    # liquid density at 25°C, ΔHvap at NBP, Cp liquid at 25°C.
    # ══════════════════════════════════════════════════════════

    {
        "id": "props_water",
        "title": "Water (H2O) — Critical Properties, Antoine, Thermodynamic Data",
        "text": (
            "Water (H2O, CAS 7732-18-5, MW=18.015 g/mol). "
            "Critical: Tc=647.1 K (373.95°C), Pc=220.6 bar, Vc=56.0 cm³/mol, ω=0.3449. "
            "Normal boiling point (NBP): 100.0°C at 1 atm. "
            "Antoine coefficients (°C, mmHg, valid 60–150°C): A=8.10765, B=1750.286, C=235.000. "
            "Antoine (°C, bar, valid 0–60°C): A=5.40221, B=1838.675, C=262.255. "
            "Liquid density at 25°C: 997.0 kg/m³. "
            "ΔHvap at NBP: 40.65 kJ/mol (2257 kJ/kg). "
            "Cp liquid at 25°C: 75.3 J/(mol·K) = 4.18 kJ/(kg·K). "
            "Cp vapor (steam) at 100°C: 33.6 J/(mol·K). "
            "DWSIM property package: use Steam Tables (IAPWS-IF97) for pure water/steam. "
            "For water-organic mixtures: NRTL or UNIQUAC with binary interaction parameters. "
            "Henry's law constant for O2 in water: KH=769 L·bar/mol at 25°C (O2 sparingly soluble). "
            "Dielectric constant at 25°C: 78.4. Strong hydrogen bonding — do NOT use PR/SRK for pure water."
        ),
        "tags": ["water", "H2O", "steam", "critical properties", "Antoine", "IAPWS", "steam tables",
                 "NBP", "boiling point", "density", "heat capacity", "enthalpy vaporization",
                 "property package", "Henry law"],
        "source": "DIPPR 801; IAPWS-IF97; NIST WebBook; Perry's 9th ed.",
    },
    {
        "id": "props_ethanol",
        "title": "Ethanol (C2H5OH) — Critical Properties, Antoine, NRTL BIP with Water",
        "text": (
            "Ethanol (C2H5OH, CAS 64-17-5, MW=46.068 g/mol). "
            "Critical: Tc=513.9 K (240.75°C), Pc=61.37 bar, Vc=167 cm³/mol, ω=0.6452. "
            "Normal boiling point: 78.37°C at 1 atm. "
            "Antoine (°C, mmHg, valid 20–93°C): A=8.11220, B=1592.864, C=226.184. "
            "Liquid density at 25°C: 785.1 kg/m³. "
            "ΔHvap at NBP: 38.56 kJ/mol (838 kJ/kg). "
            "Cp liquid at 25°C: 111.5 J/(mol·K). "
            "Azeotrope with water: 89.4 mol% ethanol (95.57 wt%) at 78.15°C, 1 atm — "
            "minimum-boiling homogeneous azeotrope. Cannot exceed 95.6 wt% by simple distillation. "
            "NRTL BIPs with water (Renon 1968, fit to VLE data): "
            "  τ12(EtOH→H2O)=3.4578, τ21(H2O→EtOH)=-0.8009, α12=0.2994. "
            "UNIQUAC BIPs with water: u12-u22=435.47 K, u21-u11=-116.36 K. "
            "For distillation: use NRTL or UNIQUAC. PR/SRK severely underestimates "
            "non-ideality — DO NOT use cubic EOS for ethanol-water VLE."
        ),
        "tags": ["ethanol", "C2H5OH", "ethyl alcohol", "critical properties", "Antoine",
                 "NRTL", "BIP", "binary interaction", "azeotrope", "water-ethanol",
                 "distillation", "alcohol", "VLE", "UNIQUAC"],
        "source": "DIPPR 801; Renon & Prausnitz 1968; Gmehling et al. DECHEMA VLE Data",
    },
    {
        "id": "props_methanol",
        "title": "Methanol (CH3OH) — Critical Properties, Antoine, NRTL BIP with Water",
        "text": (
            "Methanol (CH3OH, CAS 67-56-1, MW=32.042 g/mol). "
            "Critical: Tc=512.6 K (239.45°C), Pc=80.97 bar, Vc=118 cm³/mol, ω=0.5625. "
            "Normal boiling point: 64.70°C at 1 atm. "
            "Antoine (°C, mmHg, valid 15–84°C): A=7.87863, B=1473.110, C=230.000. "
            "Liquid density at 25°C: 791.0 kg/m³. "
            "ΔHvap at NBP: 35.27 kJ/mol (1101 kJ/kg). "
            "Cp liquid at 25°C: 80.9 J/(mol·K). "
            "Methanol-water system: no azeotrope (completely miscible, near-ideal). "
            "NRTL BIPs with water: τ12(MeOH→H2O)=2.9996, τ21(H2O→MeOH)=-0.6904, α12=0.2471. "
            "UNIQUAC BIPs with water: u12-u22=239.42 K, u21-u11=-107.98 K. "
            "Relative volatility MeOH/water near 1.5–2.0 (easy separation). "
            "Toxic — minimum 200 ppm air. Flash point: 11°C (flammable liquid). "
            "Use NRTL or UNIQUAC for methanol-water VLE simulations."
        ),
        "tags": ["methanol", "CH3OH", "methyl alcohol", "critical properties", "Antoine",
                 "NRTL", "BIP", "binary interaction", "water-methanol",
                 "distillation", "alcohol", "VLE", "UNIQUAC"],
        "source": "DIPPR 801; DECHEMA VLE Data Collection; Perry's 9th ed.",
    },
    {
        "id": "props_co2",
        "title": "Carbon Dioxide (CO2) — Critical Properties, Antoine, EOS Guidance",
        "text": (
            "Carbon dioxide (CO2, CAS 124-38-9, MW=44.010 g/mol). "
            "Critical: Tc=304.13 K (30.98°C), Pc=73.77 bar, Vc=94.07 cm³/mol, ω=0.2239. "
            "Triple point: 216.55 K (-56.60°C), 5.185 bar. Sublimation at 1 atm: -78.45°C. "
            "No normal boiling point at 1 atm (sublimes) — liquid only above 5.185 bar. "
            "Antoine for saturation pressure (K, bar, valid 216–304 K): "
            "  log10(P) = 6.81228 - 1301.679/(T - 3.494). "
            "CO2 as a supercritical fluid above 31°C, 73.8 bar. "
            "Cp gas at 25°C, 1 bar: 37.1 J/(mol·K). "
            "Henry's law constant in water at 25°C: KH=29.41 L·bar/mol. "
            "Solubility in water: 1.45 g/L at 25°C, 1 atm. "
            "DWSIM property package for CO2-rich streams: Peng-Robinson EOS (excellent fit). "
            "PR kij for CO2-CH4: 0.105; CO2-N2: -0.017; CO2-H2S: 0.070; CO2-H2O: 0.190. "
            "For CO2 capture solvents (MEA, DEA): use electrolyte models (eNRTL) or Kent-Eisenberg. "
            "CO2 liquefaction: compress to >73.8 bar and cool below 31°C."
        ),
        "tags": ["CO2", "carbon dioxide", "critical properties", "Antoine", "supercritical",
                 "PR", "Peng-Robinson", "kij", "binary interaction", "Henry law",
                 "CCS", "carbon capture", "CO2 solubility", "sublimation"],
        "source": "DIPPR 801; NIST WebBook; Poling, Prausnitz & O'Connell (2001)",
    },
    {
        "id": "props_methane",
        "title": "Methane (CH4) — Critical Properties, Antoine, Natural Gas EOS",
        "text": (
            "Methane (CH4, CAS 74-82-8, MW=16.043 g/mol). "
            "Critical: Tc=190.56 K (-82.59°C), Pc=45.99 bar, Vc=98.6 cm³/mol, ω=0.0115. "
            "Normal boiling point: -161.52°C (111.63 K). "
            "Antoine (K, bar, valid 90–190 K): A=6.61184, B=389.93, C=-6.888. "
            "Cp gas at 25°C: 35.7 J/(mol·K). Cp liquid at -161°C: 54.0 J/(mol·K). "
            "ΔHvap at NBP: 8.17 kJ/mol. "
            "DWSIM property package: Peng-Robinson or SRK (both excellent for natural gas). "
            "PR kij for CH4-C2H6: 0.000; CH4-C3H8: 0.010; CH4-N2: 0.025; CH4-CO2: 0.105. "
            "Main natural gas component (typically 70–95 mol%). "
            "LNG storage: -162°C at 1 atm. LNG density: ~424 kg/m³. "
            "Lower Heating Value (LHV): 50.1 MJ/kg. Higher Heating Value (HHV): 55.5 MJ/kg. "
            "Wobbe Index: 52.9 MJ/Nm³ (pipeline quality reference). "
            "Flammability limits in air: 5–15 vol%. GWP₂₀: 86 (CO2-equivalent)."
        ),
        "tags": ["methane", "CH4", "natural gas", "LNG", "critical properties", "Antoine",
                 "PR", "SRK", "Peng-Robinson", "kij", "binary interaction", "cryogenic"],
        "source": "DIPPR 801; Poling et al. (2001); Gas Processors Association GPA 2145",
    },
    {
        "id": "props_ethylene",
        "title": "Ethylene (C2H4) — Critical Properties, Antoine, Polymerization Notes",
        "text": (
            "Ethylene (ethene, C2H4, CAS 74-85-1, MW=28.054 g/mol). "
            "Critical: Tc=282.34 K (9.19°C), Pc=50.41 bar, Vc=131 cm³/mol, ω=0.0866. "
            "Normal boiling point: -103.71°C (169.44 K). "
            "Antoine (K, bar, valid 150–280 K): A=6.74756, B=585.00, C=-18.16. "
            "Cp gas at 25°C: 42.9 J/(mol·K). ΔHvap at NBP: 13.55 kJ/mol. "
            "DWSIM property package: Peng-Robinson (standard for olefin systems). "
            "PR kij for C2H4-C2H6: 0.010; C2H4-H2: 0.000; C2H4-N2: 0.060. "
            "Ethylene is the world's most produced organic chemical (polyethylene, EO, etc.). "
            "High-pressure polymerization: 1000–3000 bar, 150–300°C (LDPE). "
            "Ethylene oxide (EO) synthesis: 250°C, 20–30 bar, Ag catalyst, selectivity ~80%. "
            "Flammability limits: 2.7–36 vol% in air. Boiling point rise with ethane: "
            "C2H4/C2H6 relative volatility ~1.5 at -40°C — cryogenic distillation required."
        ),
        "tags": ["ethylene", "C2H4", "ethene", "olefin", "critical properties", "Antoine",
                 "PR", "Peng-Robinson", "polyethylene", "ethylene oxide", "cryogenic"],
        "source": "DIPPR 801; SRI PEP; Perry's 9th ed.",
    },
    {
        "id": "props_propane",
        "title": "Propane (C3H8) — Critical Properties, Antoine, LPG Data",
        "text": (
            "Propane (C3H8, CAS 74-98-6, MW=44.097 g/mol). "
            "Critical: Tc=369.83 K (96.68°C), Pc=42.48 bar, Vc=200 cm³/mol, ω=0.1521. "
            "Normal boiling point: -42.09°C (231.06 K). "
            "Antoine (°C, mmHg, valid -40 to 0°C): A=6.82973, B=813.200, C=248.00. "
            "Liquid density at 25°C: 493.0 kg/m³ (stored as liquid at ~8.5 bar at 20°C). "
            "ΔHvap at NBP: 18.77 kJ/mol. Cp liquid at 25°C: 96.7 J/(mol·K). "
            "LPG composition: typically 60–70% propane + 30–40% butane. "
            "DWSIM property package: Peng-Robinson or SRK. "
            "PR kij for C3H8-CH4: 0.010; C3H8-N2: 0.080; C3H8-CO2: 0.130. "
            "Vapor pressure at 20°C: 8.4 bar. Flammability: 2.1–9.5 vol% in air. "
            "LHV: 46.4 MJ/kg. Refrigeration: common in industrial refrigerant cycles (R290)."
        ),
        "tags": ["propane", "C3H8", "LPG", "liquefied petroleum gas", "critical properties",
                 "Antoine", "PR", "SRK", "vapor pressure", "refrigerant", "R290"],
        "source": "DIPPR 801; GPA 2145; Perry's 9th ed.",
    },
    {
        "id": "props_hydrogen",
        "title": "Hydrogen (H2) — Critical Properties, Antoine, Storage and EOS Notes",
        "text": (
            "Hydrogen (H2, CAS 1333-74-0, MW=2.016 g/mol). "
            "Critical: Tc=33.19 K (-239.96°C), Pc=13.13 bar, Vc=64.1 cm³/mol, ω=-0.2160 "
            "(negative acentric factor — quantum gas). "
            "Normal boiling point: -252.88°C (20.27 K). "
            "Antoine (K, bar, valid 15–33 K): A=3.54314, B=99.395, C=7.726. "
            "Cp gas at 25°C: 28.82 J/(mol·K). ΔHvap at NBP: 0.904 kJ/mol. "
            "Gas-phase H2: treat as ideal gas below 200 bar; use PR or SRK above that. "
            "CAUTION: PR/SRK poorly suited for H2 at low T; use quantum-corrected EOS or Lee-Kesler. "
            "PR kij for H2-N2: 0.103; H2-CH4: 0.000; H2-CO: 0.091; H2-CO2: -0.136. "
            "H2 storage: compressed gas at 350–700 bar (tanks), liquid at -253°C (cryo), "
            "metal hydrides (1–2 wt%), or LOHC (dibenzyltoluene, 6.2 wt%). "
            "LHV: 120 MJ/kg (3× gasoline). HHV: 142 MJ/kg. "
            "Electrolysis energy requirement: ~55 kWh/kg H2 (system level, PEM). "
            "Flammability: 4–75 vol% in air. Detonation: 18–59 vol%."
        ),
        "tags": ["hydrogen", "H2", "critical properties", "Antoine", "cryogenic", "LH2",
                 "electrolysis", "green hydrogen", "storage", "PR", "SRK", "EOS",
                 "fuel cell", "SMR", "kij"],
        "source": "DIPPR 801; NIST WebBook; IEA Hydrogen 2023",
    },
    {
        "id": "props_nitrogen",
        "title": "Nitrogen (N2) — Critical Properties, Air Separation, Inert Blanket",
        "text": (
            "Nitrogen (N2, CAS 7727-37-9, MW=28.013 g/mol). "
            "Critical: Tc=126.19 K (-146.96°C), Pc=33.96 bar, Vc=89.2 cm³/mol, ω=0.0377. "
            "Normal boiling point: -195.80°C (77.35 K). "
            "Antoine (K, bar, valid 65–126 K): A=6.49457, B=255.68, C=-6.60. "
            "Cp gas at 25°C: 29.12 J/(mol·K). Air: 78.09 mol% N2, 20.95 mol% O2, 0.93% Ar. "
            "Boiling point of air: -194.4°C (O2 richer in liquid phase). "
            "PR kij for N2-O2: -0.011; N2-Ar: -0.010; N2-CH4: 0.025; N2-CO2: -0.017. "
            "Air separation unit (ASU): cryogenic distillation at -170°C to -195°C. "
            "Pressure swing adsorption (PSA): produces 95–99.5% N2, lower purity than cryo. "
            "N2 inert blanket: used in reactors, tanks, pipelines to prevent O2 contact. "
            "N2 fertilizer: converted to NH3 via Haber-Bosch (N2 + 3H2 → 2NH3, 400–500°C, 150–300 bar, Fe catalyst). "
            "DWSIM: use Peng-Robinson for N2-hydrocarbon systems."
        ),
        "tags": ["nitrogen", "N2", "air", "critical properties", "Antoine", "air separation",
                 "ASU", "cryogenic", "inert", "Haber-Bosch", "ammonia", "PR", "SRK"],
        "source": "DIPPR 801; Perry's 9th ed.; Timmerhaus & Reed: Cryogenic Engineering",
    },
    {
        "id": "props_oxygen",
        "title": "Oxygen (O2) — Critical Properties, Antoine, Combustion Data",
        "text": (
            "Oxygen (O2, CAS 7782-44-7, MW=31.999 g/mol). "
            "Critical: Tc=154.58 K (-118.57°C), Pc=50.43 bar, Vc=73.4 cm³/mol, ω=0.0222. "
            "Normal boiling point: -182.95°C (90.20 K). "
            "Antoine (K, bar, valid 70–155 K): A=6.69144, B=319.01, C=-6.45. "
            "Cp gas at 25°C: 29.38 J/(mol·K). Liquid density at -183°C: 1142 kg/m³. "
            "Henry's law constant in water at 25°C: KH=769 L·bar/mol (low solubility). "
            "Dissolved oxygen in water at saturation (25°C, 1 atm): 8.24 mg/L. "
            "O2 purity for combustion: >99.5% (VSA/PSA) or 99.9%+ (cryogenic ASU). "
            "Combustion stoichiometry: CH4 + 2O2 → CO2 + 2H2O. "
            "Oxy-fuel combustion: uses pure O2 instead of air → N2-free flue gas → "
            "easier CO2 capture (CCS application). "
            "HAZARD: strong oxidizer — never mix with hydrocarbons. Adiabatic flame temperature "
            "with CH4: ~2800°C (vs 1950°C with air). "
            "DWSIM property package: Peng-Robinson for O2-N2-Ar (air separation)."
        ),
        "tags": ["oxygen", "O2", "critical properties", "Antoine", "combustion", "oxy-fuel",
                 "air separation", "Henry law", "dissolved oxygen", "CCS", "cryogenic"],
        "source": "DIPPR 801; NIST WebBook; Perry's 9th ed.",
    },
    {
        "id": "props_acetone",
        "title": "Acetone (C3H6O) — Critical Properties, Antoine, NRTL BIP with Water",
        "text": (
            "Acetone (propanone, C3H6O, CAS 67-64-1, MW=58.079 g/mol). "
            "Critical: Tc=508.2 K (235.05°C), Pc=47.01 bar, Vc=209 cm³/mol, ω=0.3065. "
            "Normal boiling point: 56.05°C at 1 atm. "
            "Antoine (°C, mmHg, valid -26 to 77°C): A=7.11714, B=1210.595, C=229.664. "
            "Liquid density at 25°C: 784.6 kg/m³. "
            "ΔHvap at NBP: 30.99 kJ/mol. Cp liquid at 25°C: 124.7 J/(mol·K). "
            "NRTL BIPs with water: τ12(acetone→H2O)=2.0938, τ21(H2O→acetone)=0.7078, α=0.5343. "
            "Acetone-water: fully miscible, no azeotrope, easy distillation. "
            "NRTL BIPs with methanol: τ12=0.1886, τ21=0.4827, α=0.3001. "
            "Flash point: -20°C (highly flammable). UEL/LEL: 13/2.5 vol% in air. "
            "DWSIM: NRTL for acetone-water-alcohol ternary systems."
        ),
        "tags": ["acetone", "propanone", "C3H6O", "ketone", "critical properties", "Antoine",
                 "NRTL", "BIP", "binary interaction", "water-acetone", "VLE"],
        "source": "DIPPR 801; DECHEMA VLE Data; Gmehling et al.",
    },
    {
        "id": "props_benzene",
        "title": "Benzene (C6H6) — Critical Properties, Antoine, PR Parameters",
        "text": (
            "Benzene (C6H6, CAS 71-43-2, MW=78.114 g/mol). "
            "Critical: Tc=562.05 K (288.90°C), Pc=48.95 bar, Vc=259 cm³/mol, ω=0.2103. "
            "Normal boiling point: 80.09°C at 1 atm. Melting point: 5.53°C. "
            "Antoine (°C, mmHg, valid 8–80°C): A=6.90565, B=1211.033, C=220.790. "
            "Liquid density at 25°C: 873.8 kg/m³. "
            "ΔHvap at NBP: 30.72 kJ/mol. Cp liquid at 25°C: 136.0 J/(mol·K). "
            "DWSIM property package: Peng-Robinson for benzene-toluene-xylene (BTX) systems. "
            "PR kij for benzene-toluene: 0.000; benzene-xylene: 0.000 (similar molecules). "
            "Benzene-water: partially miscible, two liquid phases (LLE). "
            "NRTL BIPs with water: τ12=4.1178, τ21=4.6652, α=0.2000. "
            "Carcinogen (IARC Group 1) — workplace limit 1 ppm (OSHA). "
            "Benzene-toluene relative volatility: ~2.5 at 1 atm — good separation by distillation."
        ),
        "tags": ["benzene", "C6H6", "BTX", "aromatic", "critical properties", "Antoine",
                 "PR", "Peng-Robinson", "kij", "toluene", "xylene", "LLE"],
        "source": "DIPPR 801; Gmehling et al.; Perry's 9th ed.",
    },
    {
        "id": "props_toluene",
        "title": "Toluene (C7H8) — Critical Properties, Antoine, BTX Separation",
        "text": (
            "Toluene (methylbenzene, C7H8, CAS 108-88-3, MW=92.141 g/mol). "
            "Critical: Tc=591.80 K (318.65°C), Pc=41.06 bar, Vc=316 cm³/mol, ω=0.2638. "
            "Normal boiling point: 110.63°C at 1 atm. Melting point: -94.95°C. "
            "Antoine (°C, mmHg, valid 6–137°C): A=6.95087, B=1342.310, C=219.187. "
            "Liquid density at 25°C: 862.3 kg/m³. "
            "ΔHvap at NBP: 33.18 kJ/mol. Cp liquid at 25°C: 157.3 J/(mol·K). "
            "DWSIM property package: Peng-Robinson for BTX systems. "
            "Toluene-benzene distillation: relative volatility ~2.3 at 1 atm, 5–10 theoretical stages. "
            "Toluene-water: partially miscible (LLE). Toluene azeotrope: none with water "
            "at 1 atm (heterogeneous LLE instead). "
            "Toluene dealkylation to benzene: C7H8 + H2 → C6H6 + CH4, 600–700°C, 35–70 bar. "
            "Solvent: dissolves many organic compounds, moderate toxicity."
        ),
        "tags": ["toluene", "C7H8", "methylbenzene", "BTX", "aromatic", "critical properties",
                 "Antoine", "PR", "Peng-Robinson", "benzene", "xylene", "distillation"],
        "source": "DIPPR 801; Perry's 9th ed.; Seider et al.",
    },
    {
        "id": "props_h2s",
        "title": "Hydrogen Sulfide (H2S) — Critical Properties, Antoine, Acid Gas EOS",
        "text": (
            "Hydrogen sulfide (H2S, CAS 7783-06-4, MW=34.082 g/mol). "
            "Critical: Tc=373.53 K (100.38°C), Pc=89.63 bar, Vc=98.5 cm³/mol, ω=0.0942. "
            "Normal boiling point: -60.33°C (212.82 K). "
            "Antoine (°C, mmHg, valid -100 to -60°C): A=7.87414, B=1016.670, C=237.100. "
            "Liquid density at -60°C: 946 kg/m³. ΔHvap at NBP: 18.67 kJ/mol. "
            "Cp gas at 25°C: 34.2 J/(mol·K). "
            "DWSIM property package: Peng-Robinson or SRK for acid gas systems (H2S, CO2, CH4). "
            "PR kij: H2S-CH4=0.080; H2S-CO2=0.096; H2S-H2O=0.190. "
            "Amine treating to remove H2S: monoethanolamine (MEA) or MDEA. "
            "H2S partial pressure determines H2S pick-up in amines. "
            "Claus process: H2S → SO2 → elemental S (recovery >99.9%). "
            "TLV-TWA (ACGIH): 1 ppm. Immediately Dangerous to Life or Health (IDLH): 50 ppm. "
            "Sour gas: >5.7 mg H2S/m³ (4 ppm). Highly flammable and toxic."
        ),
        "tags": ["H2S", "hydrogen sulfide", "acid gas", "sour gas", "critical properties",
                 "Antoine", "PR", "SRK", "Claus", "amine", "MEA", "MDEA", "kij"],
        "source": "DIPPR 801; Kidnay & Parrish: Fundamentals of Natural Gas Processing",
    },
    {
        "id": "props_ammonia",
        "title": "Ammonia (NH3) — Critical Properties, Antoine, Haber-Bosch Process",
        "text": (
            "Ammonia (NH3, CAS 7664-41-7, MW=17.031 g/mol). "
            "Critical: Tc=405.65 K (132.50°C), Pc=113.33 bar, Vc=72.5 cm³/mol, ω=0.2526. "
            "Normal boiling point: -33.35°C (239.80 K). "
            "Antoine (°C, mmHg, valid -83 to -33°C): A=7.36050, B=926.132, C=240.17. "
            "Liquid density at -33°C: 682.0 kg/m³ (storage condition). "
            "ΔHvap at NBP: 23.33 kJ/mol. Cp liquid at 25°C: 80.8 J/(mol·K). "
            "Haber-Bosch synthesis: N2 + 3H2 → 2NH3, ΔH=-92 kJ/mol (exothermic). "
            "Conditions: 400–500°C, 150–300 bar, promoted Fe catalyst (KOH + Al2O3 promoters). "
            "Single-pass conversion: 15–25% (limited by equilibrium). Recycle loop required. "
            "Equilibrium favored by low T and high P — kinetics limit low-T operation. "
            "Refrigerant R717: excellent thermodynamic properties for industrial cooling. "
            "DWSIM property package: for NH3 systems, use Peng-Robinson or SRK with "
            "special care near critical point. NH3-water: use NRTL (strong non-ideal). "
            "TLV-TWA: 25 ppm. IDLH: 300 ppm. Flammability: 15–28 vol% in air."
        ),
        "tags": ["ammonia", "NH3", "critical properties", "Antoine", "Haber-Bosch",
                 "synthesis", "refrigerant", "R717", "PR", "SRK", "NRTL",
                 "nitrogen fixation", "fertilizer"],
        "source": "DIPPR 801; Appl: Ammonia, Wiley-VCH 1999; Perry's 9th ed.",
    },
    {
        "id": "props_acetic_acid",
        "title": "Acetic Acid (CH3COOH) — Critical Properties, Antoine, Dimerization",
        "text": (
            "Acetic acid (ethanoic acid, CH3COOH, CAS 64-19-7, MW=60.052 g/mol). "
            "Critical: Tc=591.95 K (318.80°C), Pc=57.86 bar, Vc=171 cm³/mol, ω=0.4665. "
            "Normal boiling point: 117.90°C. Melting point: 16.64°C (glacial acetic acid freezes). "
            "Antoine (°C, mmHg, valid 17–118°C): A=7.80307, B=1651.200, C=225.000. "
            "Liquid density at 25°C: 1044 kg/m³. ΔHvap at NBP: 39.65 kJ/mol. "
            "CRITICAL MODELING NOTE: Acetic acid dimerizes strongly in vapor phase via hydrogen bonding. "
            "Apparent MW in vapor ~120 g/mol at low temperatures. "
            "Standard cubic EOS (PR, SRK) cannot model dimerization — severely wrong predictions. "
            "Correct approach: use chemical theory model or Hayden-O'Connell (HOC) correlation "
            "for vapor phase association, combined with NRTL for liquid phase. "
            "In DWSIM: use 'Hayden-O'Connell (Nothnagel)' vapor model with NRTL. "
            "Acetic acid-water: no azeotrope, but highly non-ideal. "
            "NRTL BIPs with water: τ12=0.3514, τ21=1.8920, α=0.4538."
        ),
        "tags": ["acetic acid", "CH3COOH", "carboxylic acid", "dimerization", "association",
                 "critical properties", "Antoine", "NRTL", "HOC", "Hayden-O'Connell",
                 "vapor association", "non-ideal"],
        "source": "DIPPR 801; Nothnagel et al. (1973); Perry's 9th ed.",
    },
    {
        "id": "props_cyclohexane",
        "title": "Cyclohexane (C6H12) — Critical Properties, Antoine, Solvent Properties",
        "text": (
            "Cyclohexane (C6H12, CAS 110-82-7, MW=84.161 g/mol). "
            "Critical: Tc=553.64 K (280.49°C), Pc=40.75 bar, Vc=308 cm³/mol, ω=0.2108. "
            "Normal boiling point: 80.74°C. Melting point: 6.47°C. "
            "Antoine (°C, mmHg, valid 20–81°C): A=6.84498, B=1203.526, C=222.863. "
            "Liquid density at 25°C: 774.0 kg/m³. ΔHvap at NBP: 29.97 kJ/mol. "
            "DWSIM property package: Peng-Robinson (non-polar cycloalkane). "
            "Cyclohexane-benzene: nearly ideal (Raoult's law); relative volatility ~1.02 → "
            "extractive distillation or liquid-liquid extraction needed. "
            "Cyclohexane production: benzene hydrogenation (Ni catalyst, 200°C, 20–50 bar). "
            "Nylon-6 route: cyclohexane → cyclohexanone/cyclohexanol → caprolactam. "
            "Good nonpolar solvent; used in wax extraction, polymer processing."
        ),
        "tags": ["cyclohexane", "C6H12", "cycloalkane", "critical properties", "Antoine",
                 "PR", "Peng-Robinson", "benzene", "solvent", "nylon"],
        "source": "DIPPR 801; Perry's 9th ed.",
    },
    {
        "id": "props_diethylether",
        "title": "Diethyl Ether (C4H10O) — Critical Properties, Antoine, Safety",
        "text": (
            "Diethyl ether (ethoxyethane, C4H10O, CAS 60-29-7, MW=74.123 g/mol). "
            "Critical: Tc=466.70 K (193.55°C), Pc=36.40 bar, Vc=280 cm³/mol, ω=0.2810. "
            "Normal boiling point: 34.55°C. "
            "Antoine (°C, mmHg, valid -40 to 35°C): A=6.92374, B=1064.070, C=228.800. "
            "Liquid density at 25°C: 713.5 kg/m³. ΔHvap at NBP: 26.52 kJ/mol. "
            "High vapor pressure (584 mmHg at 20°C) — significant evaporative losses. "
            "Flash point: -45°C. LEL: 1.9 vol%. Peroxide formation risk on storage. "
            "NRTL BIPs with water: τ12=2.2485, τ21=0.6018, α=0.2001. "
            "Partially miscible with water: mutual solubility at 25°C — "
            "  organic phase: 98.7% ether; aqueous phase: 6.9% ether. "
            "Used in extraction of polar products from aqueous streams (low selectivity). "
            "Use NRTL + Hayden-O'Connell for ether-water VLE in DWSIM."
        ),
        "tags": ["diethyl ether", "ether", "C4H10O", "solvent", "critical properties",
                 "Antoine", "NRTL", "BIP", "extraction", "LLE", "flash point"],
        "source": "DIPPR 801; DECHEMA LLE Data; Perry's 9th ed.",
    },
    {
        "id": "props_mea",
        "title": "Monoethanolamine (MEA, C2H7NO) — Amine Solvent for CO2/H2S Capture",
        "text": (
            "Monoethanolamine (MEA, C2H7NO, CAS 141-43-5, MW=61.083 g/mol). "
            "Critical: Tc=678.2 K (405.05°C), Pc=67.11 bar, ω=0.7560. "
            "Normal boiling point: 170.5°C. Melting point: 10.3°C. "
            "Antoine (°C, mmHg, valid 72–170°C): A=8.28842, B=2232.500, C=198.600. "
            "Liquid density at 25°C: 1018 kg/m³. ΔHvap at NBP: 56.43 kJ/mol. "
            "INDUSTRIAL USE: 15–30 wt% aqueous MEA absorbs CO2 and H2S from gas streams. "
            "CO2 loading capacity: ~0.5 mol CO2/mol MEA (operating range 0.1–0.45). "
            "Reaction: CO2 + 2MEA → MEACOO⁻ + MEAH⁺ (carbamate formation, fast). "
            "Heat of absorption: ~84 kJ/mol CO2 (high regeneration energy). "
            "Regeneration: 110–130°C, stripping column. Solvent degradation: oxidative + thermal. "
            "DWSIM modeling: use Kent-Eisenberg model or eNRTL (electrolyte NRTL). "
            "Standard NRTL/PR cannot model electrolyte reactions properly — use specialized models. "
            "Alternative solvents: MDEA (less reactive, lower regen energy), piperazine (fast kinetics)."
        ),
        "tags": ["MEA", "monoethanolamine", "amine", "CO2 capture", "H2S removal",
                 "acid gas", "CCS", "Kent-Eisenberg", "eNRTL", "electrolyte",
                 "absorption", "regeneration", "solvent"],
        "source": "DIPPR 801; Rochelle (2009) Science; Kohl & Nielsen: Gas Purification",
    },
    {
        "id": "props_glycerol",
        "title": "Glycerol (C3H8O3) — Critical Properties, Antoine, Biodiesel Byproduct",
        "text": (
            "Glycerol (glycerin, C3H8O3, CAS 56-81-5, MW=92.094 g/mol). "
            "Critical: Tc=850 K (576.85°C, extrapolated — decomposes before critical point), "
            "Pc=75.0 bar (estimated), ω=1.485. "
            "Normal boiling point: 290°C (decomposes above 260°C at 1 atm). "
            "Antoine (°C, mmHg, valid 165–206°C): A=8.38920, B=2711.000, C=185.000. "
            "Liquid density at 25°C: 1261 kg/m³. Viscosity at 25°C: 934 mPa·s (very viscous). "
            "Hygroscopic — absorbs moisture from air. Miscible with water and methanol. "
            "Biodiesel byproduct: transesterification of triglycerides produces 1 mol glycerol "
            "per 3 mol FAME (fatty acid methyl ester). Raw crude glycerol: 50–80% glycerol. "
            "Purification: vacuum distillation or ion exchange. "
            "Uses: pharmaceuticals, cosmetics, food (E422), propylene glycol synthesis. "
            "DWSIM: use UNIQUAC or NRTL for glycerol-water-methanol ternary (biodiesel wash)."
        ),
        "tags": ["glycerol", "glycerin", "C3H8O3", "biodiesel", "transesterification",
                 "FAME", "critical properties", "Antoine", "NRTL", "UNIQUAC",
                 "viscous", "hygroscopic"],
        "source": "DIPPR 801; Pinto et al. (2005); Perry's 9th ed.",
    },

    # ── Missing thermodynamic models ──────────────────────────

    {
        "id": "thermo_wilson",
        "title": "Wilson Equation — Activity Coefficient Model for Miscible Systems",
        "text": (
            "The Wilson equation is an activity coefficient model for completely miscible "
            "liquid mixtures. Advantages over NRTL: "
            "  - Only 2 parameters per binary pair (vs 3 for NRTL) "
            "  - Thermodynamically consistent "
            "  - Excellent accuracy for alcohol-water, ether-water, ketone-water systems. "
            "Limitation: CANNOT predict liquid-liquid phase splitting (LLE). "
            "If your system forms two liquid phases, use NRTL or UNIQUAC instead. "
            "Parameters: Λ12 and Λ21 (dimensionless, related to molar volume ratio × Boltzmann factor). "
            "Wilson parameters are temperature-dependent: Λij = (Vj/Vi) × exp(-(aij/RT)). "
            "Parameter estimation: fit aij and aji to VLE experimental data. "
            "Wilson equation for activity coefficient of component 1 in binary: "
            "  ln(γ1) = -ln(x1 + Λ12·x2) + x2[(Λ12/(x1+Λ12·x2)) - (Λ21/(x2+Λ21·x1))] "
            "Example — ethanol-water Wilson parameters (J/mol): a12=-169.0, a21=986.5. "
            "In DWSIM: available as 'Wilson' property package. "
            "When to use: miscible polar systems, alcohol-hydrocarbon at moderate conditions. "
            "NEVER use Wilson for: partially miscible systems, polymer solutions."
        ),
        "tags": ["Wilson", "activity coefficient", "miscible", "VLE", "alcohol-water",
                 "polar", "property package", "NRTL", "UNIQUAC", "liquid-liquid",
                 "binary parameters", "thermodynamics"],
        "source": "Wilson (1964) JACS; Walas: Phase Equilibria in Chemical Engineering",
    },
    {
        "id": "thermo_rachford_rice",
        "title": "Rachford-Rice Flash Algorithm — Vapor-Liquid Equilibrium Calculation",
        "text": (
            "The Rachford-Rice equation is the standard algorithm for isothermal flash "
            "(PT flash) calculations in process simulation. "
            "Problem: given feed z_i, T, P → find vapor fraction β and phase compositions. "
            "Step 1: Compute K-values (equilibrium ratios) Ki = yi/xi. "
            "  Initial guess: Ki = (Pc_i/P) × exp(5.373(1+ωi)(1 - Tc_i/T))  [Wilson's equation]. "
            "Step 2: Solve Rachford-Rice equation for vapor fraction β: "
            "  f(β) = Σ_i [z_i(Ki-1) / (1 + β(Ki-1))] = 0 "
            "  f(β) is monotonically decreasing — use bisection or Newton-Raphson. "
            "  Bracket: β ∈ (β_min, β_max) where β_min = 1/(1-Ki_max), β_max = 1/(1-Ki_min). "
            "Step 3: Calculate compositions: "
            "  xi = z_i / (1 + β(Ki-1));  yi = Ki × xi "
            "Step 4: Update K-values using EOS (fugacity coefficient ratio): "
            "  Ki = φi_L / φi_V (from PR or SRK fugacity coefficients). "
            "Step 5: Check convergence: Σ(yi - xi)² < 1e-10. If not, go to Step 2. "
            "Successive substitution: simple but slow (15–30 iterations). "
            "Accelerated: Wegstein or dominant eigenvalue method for faster convergence. "
            "Three-phase flash (VLLE): requires Rachford-Rice for two liquid phases simultaneously. "
            "DWSIM handles flash internally — this understanding helps debug convergence failures."
        ),
        "tags": ["Rachford-Rice", "flash", "VLE", "vapor-liquid equilibrium", "isothermal flash",
                 "PT flash", "K-value", "Wilson", "convergence", "algorithm",
                 "vapor fraction", "fugacity", "phase equilibrium"],
        "source": "Rachford & Rice (1952) Trans. AIME; Michelsen & Mollerup: Thermodynamic Models",
    },
    {
        "id": "thermo_henry_law",
        "title": "Henry's Law — Gas Solubility in Liquids",
        "text": (
            "Henry's Law: at dilute concentrations, the partial pressure of a dissolved gas "
            "is proportional to its mole fraction in solution: p_i = KH_i × x_i. "
            "KH is Henry's constant (atm or bar) — increases with temperature (less soluble at higher T). "
            "Henry's law constants at 25°C in water (bar·m³/mol): "
            "  O2: 769  | N2: 1600 | H2: 1228 | CO2: 29.4 | H2S: 5.5 | CH4: 400 "
            "  SO2: 0.81 | HCl: ~0.0001 (very soluble) | NH3: 0.057 (very soluble). "
            "Low KH = high solubility (SO2, NH3, HCl, CO2 relatively soluble). "
            "High KH = low solubility (O2, N2, H2 sparingly soluble). "
            "Temperature dependence: ln(KH) = -ΔHsol/R × (1/T) + constant. "
            "  For CO2: KH increases from 15 L·bar/mol at 15°C to 57 L·bar/mol at 45°C. "
            "Applications: "
            "  - Absorption column design: driving force = p_i - KH×x_i. "
            "  - Oxygen transfer in bioreactors: use KLa to model O2 dissolution. "
            "  - CO2 absorption into water: pH 6–7 (carbonic acid equilibrium). "
            "  - Stripping: operate at high T or low P to reduce dissolved gases. "
            "In DWSIM: for dissolved gases at low concentrations, use 'Henry's Law' "
            "component property rather than full EOS — much more accurate for supercritical gases."
        ),
        "tags": ["Henry's law", "gas solubility", "dissolved gas", "absorption", "stripping",
                 "O2", "CO2", "N2", "H2S", "NH3", "KH", "Henry constant",
                 "bioreactor", "oxygen transfer", "supercritical"],
        "source": "Sander (2015) Atm. Chem. Phys.; Perry's 9th ed. Sec. 2; NIST WebBook",
    },
    {
        "id": "thermo_dippr_correlations",
        "title": "DIPPR Thermodynamic Property Correlations — Temperature-Dependent Properties",
        "text": (
            "DIPPR 801 database provides temperature-dependent property correlations for 2000+ compounds. "
            "Key equation forms used in process simulation: "
            "DIPPR 100 (polynomial): Cp_liquid = A + B·T + C·T² + D·T³ + E·T⁴ (J/mol/K, T in K). "
            "  Example: water Cp_liq — A=276370, B=-2090.1, C=8.125, D=-0.01412, E=0.00000952. "
            "DIPPR 101 (Antoine-like): ln(P_sat) = A + B/T + C·ln(T) + D·T^E (P in Pa, T in K). "
            "  More accurate than 3-parameter Antoine over wide T range. "
            "  Example: water — A=73.649, B=-7258.2, C=-7.3037, D=4.17e-6, E=2. "
            "DIPPR 105 (Rackett): ρ_liquid = A / B^(1 + (1-T/Tc)^n)  (mol/m³). "
            "  Liquid density — very accurate near normal conditions. "
            "  Example: ethanol — A=1484.8, B=0.27469, Tc=513.9 K, n=0.23178. "
            "DIPPR 106 (Watson): ΔHvap = A × (1-Tr)^(B + C·Tr + D·Tr² + E·Tr³). "
            "  Enthalpy of vaporization vs reduced temperature Tr = T/Tc. "
            "DIPPR 107 (PPDS): Cp_gas = A × (B/T/sinh(B/T))² + C × (D/T/cosh(D/T))². "
            "  Ideal gas heat capacity — accurate from low T to 1500 K. "
            "DIPPR 119: solid/liquid heat capacity with melting point transition. "
            "DWSIM automatically uses DIPPR correlations for any compound in its database. "
            "For custom compounds: enter Tc, Pc, ω and DIPPR correlation coefficients manually."
        ),
        "tags": ["DIPPR", "thermodynamic correlations", "temperature dependent", "heat capacity",
                 "Cp", "liquid density", "Rackett", "Watson", "Antoine", "vapor pressure",
                 "enthalpy vaporization", "ideal gas", "property estimation"],
        "source": "DIPPR 801 (2023); Smith, Van Ness & Abbott App. B; Poling et al. (2001)",
    },
    {
        "id": "thermo_pr_kij_table",
        "title": "Peng-Robinson kij Binary Interaction Parameters — Common Gas Pairs",
        "text": (
            "Peng-Robinson (PR) kij values correct the standard mixing rules for "
            "unlike-molecule interactions. kij = kji (symmetric). "
            "kij > 0: positive deviation (repulsive interaction), common for dissimilar pairs. "
            "kij < 0: negative deviation (attractive), rare. kij = 0: ideal mixing. "
            "Recommended kij values (literature-fitted to VLE data): "
            "  CH4-CO2:    kij = 0.105     CH4-N2:     kij = 0.025 "
            "  CH4-H2S:    kij = 0.080     CH4-C2H6:   kij = 0.000 "
            "  CH4-C3H8:   kij = 0.010     CH4-H2:     kij = 0.000 "
            "  CO2-N2:     kij = -0.017    CO2-H2S:    kij = 0.070 "
            "  CO2-H2O:    kij = 0.190     CO2-C2H6:   kij = 0.130 "
            "  CO2-C3H8:   kij = 0.135     H2S-N2:     kij = 0.170 "
            "  N2-O2:      kij = -0.011    N2-H2:      kij = 0.103 "
            "  C2H6-C3H8: kij = 0.000     H2-CO:      kij = 0.091 "
            "  Benzene-toluene: kij = 0.000   C6H6-H2O: kij = 0.324 "
            "How to set in DWSIM: Property Package → Edit → Binary Interaction Parameters table. "
            "Default kij=0 is only valid for similar hydrocarbons (Ci-Cj, same family). "
            "ALWAYS set kij ≠ 0 for CO2, H2S, H2O in hydrocarbon mixtures."
        ),
        "tags": ["kij", "binary interaction parameter", "Peng-Robinson", "PR", "SRK",
                 "mixing rules", "CH4", "CO2", "N2", "H2S", "H2O", "H2",
                 "VLE", "EOS", "gas mixture", "natural gas"],
        "source": "Poling, Prausnitz & O'Connell (2001); Kidnay & Parrish; GPA 2145",
    },
    {
        "id": "thermo_nrtl_bip_table",
        "title": "NRTL Binary Interaction Parameter Table — Common Polar Pairs",
        "text": (
            "NRTL model parameters τ12, τ21, α for common binary pairs. "
            "Convention: τ12 is the interaction of component 2 on component 1's environment. "
            "Parameters valid for atmospheric pressure VLE (liquid phase non-ideality). "
            "Units: τ dimensionless (or in K if using temperature-dependent form a+b/T). "
            "Selected NRTL BIPs from DECHEMA VLE Data Collection: "
            "  Ethanol(1)-Water(2):    τ12=3.4578, τ21=-0.8009, α=0.2994  (T=25°C ref) "
            "  Methanol(1)-Water(2):   τ12=2.9996, τ21=-0.6904, α=0.2471 "
            "  Acetone(1)-Water(2):    τ12=2.0938, τ21=0.7078,  α=0.5343 "
            "  IPA(1)-Water(2):        τ12=3.3399, τ21=-0.3974, α=0.2981 "
            "  THF(1)-Water(2):        τ12=2.8258, τ21=1.2090,  α=0.2000 "
            "  Acetonitrile(1)-H2O(2): τ12=2.1484, τ21=1.3219,  α=0.2983 "
            "  Acetic acid(1)-H2O(2):  τ12=0.3514, τ21=1.8920,  α=0.4538 "
            "  Benzene(1)-Water(2):    τ12=4.1178, τ21=4.6652,  α=0.2000  (LLE system) "
            "  n-Hexane(1)-Ethanol(2): τ12=2.8020, τ21=0.9460,  α=0.4715 "
            "  MEK(1)-Water(2):        τ12=2.5232, τ21=1.0804,  α=0.4139 "
            "Temperature-dependent form: τij = aij + bij/T (T in K). "
            "If DWSIM does not have BIPs for your pair, use UNIFAC to estimate "
            "or fit to experimental bubble/dew point data from NIST or DECHEMA."
        ),
        "tags": ["NRTL", "BIP", "binary interaction parameter", "tau", "alpha",
                 "ethanol", "methanol", "acetone", "IPA", "water", "VLE",
                 "activity coefficient", "DECHEMA", "polar", "non-ideal"],
        "source": "Gmehling & Onken: DECHEMA VLE Data Collection; Renon & Prausnitz 1968",
    },
    {
        "id": "thermo_azeotrope_database",
        "title": "Azeotrope Data — Common Binary Systems",
        "text": (
            "Azeotropes are mixtures where vapor and liquid have identical composition "
            "at a specific T and P — cannot be separated by simple distillation. "
            "MINIMUM-BOILING azeotropes (most common, positive deviation from Raoult): "
            "  Ethanol-Water:       89.4 mol% EtOH,  78.15°C, 1 atm "
            "  IPA-Water:           67.5 mol% IPA,   80.37°C, 1 atm "
            "  n-Propanol-Water:    71.7 mol% PrOH,  87.72°C, 1 atm "
            "  Ethyl acetate-EtOH:  46.0 mol% EtOH,  71.81°C, 1 atm "
            "  Chloroform-Acetone:  35.0 mol% CHCl3, 64.37°C, 1 atm  (negative deviation) "
            "  THF-Water:           82.0 mol% THF,   63.40°C, 1 atm "
            "  Acetonitrile-Water:  67.0 mol% MeCN,  76.50°C, 1 atm "
            "  Benzene-Cyclohexane: 52.5 mol% Benz,  77.50°C, 1 atm "
            "  HCl-Water:           11.1 mol% HCl,   108.6°C, 1 atm (max boiling) "
            "MAXIMUM-BOILING azeotropes (negative deviation, less common): "
            "  HNO3-Water: 38.3 wt% HNO3, 120.7°C, 1 atm "
            "  H2SO4-Water: 98.3 wt% H2SO4, 337°C, 1 atm "
            "  HCl-Water: 20.2 wt% HCl, 108.6°C, 1 atm. "
            "HETEROGENEOUS azeotropes (two liquid phases in equilibrium with vapor): "
            "  n-Butanol-Water: 74.9 mol% BuOH, 93.0°C (LLE below 92.7°C). "
            "  Water-benzene: 70.6 mol% water, 69.3°C (basis of steam distillation). "
            "Pressure sensitivity: increasing P shifts azeotrope composition "
            "  (pressure-swing distillation exploits this). "
            "In DWSIM: azeotrope compositions appear when x_i = y_i in flash results."
        ),
        "tags": ["azeotrope", "minimum boiling", "maximum boiling", "heterogeneous azeotrope",
                 "ethanol-water", "IPA-water", "distillation", "VLE",
                 "pressure swing", "separation", "eutectic", "azeotropic composition"],
        "source": "Gmehling et al.: Azeotropic Data (Wiley-VCH); Perry's 9th ed. Sec. 13",
    },
    {
        "id": "thermo_acentric_factors",
        "title": "Acentric Factor (ω) — Values for Common Compounds",
        "text": (
            "The acentric factor ω characterizes the non-sphericity of a molecule. "
            "Defined by Pitzer: ω = -log10(P_sat/Pc) at T/Tc=0.7 - 1.000. "
            "ω ≈ 0: simple spherical molecules (noble gases, CH4). "
            "ω ≈ 0.1–0.3: light nonpolar hydrocarbons. "
            "ω > 0.5: polar or associating molecules (alcohols, acids). "
            "Acentric factors for common compounds: "
            "  Noble gases: He=−0.385, Ne=−0.041, Ar=0.000 "
            "  Simple gases: H2=−0.216, N2=0.038, O2=0.022, CO=0.045, CO2=0.224 "
            "  Light hydrocarbons: CH4=0.012, C2H6=0.099, C3H8=0.152, n-C4=0.200, "
            "    n-C5=0.252, n-C6=0.301, n-C7=0.349, n-C8=0.394 "
            "  Aromatics: benzene=0.210, toluene=0.264, o-xylene=0.312 "
            "  Alcohols: methanol=0.563, ethanol=0.645, 1-propanol=0.630, "
            "    1-butanol=0.593, glycerol=1.485 "
            "  Acids: acetic acid=0.467, propionic acid=0.536 "
            "  Other: acetone=0.307, THF=0.225, DMF=0.374, water=0.345, "
            "    ammonia=0.253, H2S=0.094, SO2=0.245 "
            "Role in EOS: α(T) = [1 + m(1-√Tr)]², m = 0.37464 + 1.54226ω − 0.26992ω² (PR). "
            "Higher ω → stronger temperature dependence of vapor pressure → steeper VLE curves."
        ),
        "tags": ["acentric factor", "omega", "Pitzer", "EOS", "Peng-Robinson", "SRK",
                 "CH4", "C2H6", "benzene", "alcohols", "water", "CO2", "N2",
                 "property estimation", "vapor pressure", "PR", "thermodynamics"],
        "source": "Poling, Prausnitz & O'Connell (2001) Appendix A; DIPPR 801",
    },

    # ══════════════════════════════════════════════════════════
    # REACTION THERMODYNAMICS
    # ══════════════════════════════════════════════════════════

    {
        "id": "rxn_combustion",
        "title": "Combustion Reactions — Heat of Reaction and Stoichiometry",
        "text": (
            "Standard heats of combustion (ΔHcomb at 25°C, 1 atm, products H2O liquid): "
            "  CH4  + 2O2  → CO2 + 2H2O     ΔH = -890.4 kJ/mol  (LHV gas = 50.1 MJ/kg) "
            "  C2H6 + 3.5O2 → 2CO2 + 3H2O   ΔH = -1559.8 kJ/mol "
            "  C3H8 + 5O2  → 3CO2 + 4H2O    ΔH = -2220.0 kJ/mol "
            "  H2   + 0.5O2 → H2O            ΔH = -285.8 kJ/mol  (HHV basis) "
            "  CO   + 0.5O2 → CO2            ΔH = -283.0 kJ/mol "
            "  C    + O2   → CO2             ΔH = -393.5 kJ/mol "
            "Lower Heating Value (LHV) vs Higher Heating Value (HHV): "
            "  HHV includes condensation of water vapor → HHV > LHV. "
            "  Difference = nH2O × ΔHvap(H2O) = nH2O × 44.0 kJ/mol. "
            "  For CH4: HHV = 890.4 kJ/mol, LHV = 890.4 - 2×44.0 = 802.4 kJ/mol. "
            "Adiabatic flame temperature (AFT) with air (21% O2): "
            "  CH4/air: ~1950°C;  H2/air: ~2100°C;  C3H8/air: ~1995°C. "
            "  With pure O2 (oxy-fuel): AFT increases by 600–900°C. "
            "Excess air impact: 10% excess air reduces AFT by ~50°C; "
            "  50% excess air reduces AFT by ~250°C. "
            "In DWSIM reactor: use GibbsReactor for equilibrium, ConversionReactor "
            "for known conversion, KineticReactor for Arrhenius rate expressions. "
            "Always check: heat duty = -ΔH × molar flow (positive = exothermic → cooling needed)."
        ),
        "tags": ["combustion", "heat of reaction", "LHV", "HHV", "adiabatic flame temperature",
                 "CH4", "H2", "CO", "stoichiometry", "enthalpy", "exothermic",
                 "oxy-fuel", "excess air", "heating value"],
        "source": "NIST-JANAF Thermochemical Tables; Perry's 9th ed. Sec. 27",
    },
    {
        "id": "rxn_steam_reforming",
        "title": "Steam Methane Reforming (SMR) — Reaction Thermodynamics and Conditions",
        "text": (
            "Steam methane reforming (SMR) is the primary industrial H2 production route. "
            "Primary reforming reactions: "
            "  (1) CH4 + H2O → CO + 3H2     ΔH298 = +206.1 kJ/mol  (endothermic) "
            "  (2) CO  + H2O → CO2 + H2     ΔH298 = -41.2 kJ/mol   (water-gas shift, WGS) "
            "  (3) CH4 + 2H2O → CO2 + 4H2   ΔH298 = +165.0 kJ/mol  (net reforming) "
            "Equilibrium constants (Kp in bar units): "
            "  Reaction 1: ln(Kp1) = -26830/T + 30.11  (T in K; Kp1 = 1.0 at ~900 K) "
            "  Reaction 2: ln(Kp2) = 4400/T - 4.063   (WGS reaches equilibrium at ~700 K) "
            "Industrial SMR conditions: "
            "  Temperature: 800–900°C (tube outlet), fired to ~1050°C tube skin. "
            "  Pressure: 20–35 bar. Steam-to-carbon ratio (S/C): 2.5–4.0 mol/mol. "
            "  Catalyst: Ni on Al2O3 support, active 400–900°C. "
            "  CH4 conversion: 65–75% per pass (equilibrium limited). "
            "Pre-reforming: adiabatic reactor at 400–550°C converts higher hydrocarbons "
            "  (C2+) to CH4 before main reformer — protects catalyst. "
            "In DWSIM: model with GibbsReactor (equilibrium) at 850°C, 25 bar, "
            "  or Conversion Reactor with 70% CH4 conversion for simplified model. "
            "Include CO shift conversion section after reformer for higher H2 purity."
        ),
        "tags": ["steam reforming", "SMR", "hydrogen production", "CH4", "H2",
                 "water gas shift", "WGS", "endothermic", "equilibrium", "Ni catalyst",
                 "Gibbs reactor", "synthesis gas", "syngas", "blue hydrogen"],
        "source": "Aasberg-Petersen et al. (2011) J. Nat. Gas Sci. Eng.; Rostrup-Nielsen (2002)",
    },
    {
        "id": "rxn_water_gas_shift",
        "title": "Water-Gas Shift (WGS) Reaction — Thermodynamics and Reactor Design",
        "text": (
            "Water-Gas Shift (WGS): CO + H2O(g) → CO2 + H2   ΔH298 = -41.2 kJ/mol (exothermic). "
            "Equilibrium constant: Keq = exp(4577.8/T - 4.33)  (T in K). "
            "  At 300°C: Keq ≈ 13.0  (favorable). "
            "  At 400°C: Keq ≈ 4.0   (less favorable). "
            "  At 500°C: Keq ≈ 1.6   (near neutral). "
            "  At 700°C: Keq ≈ 0.4   (reverse shift). "
            "Industrial WGS stages: "
            "  High-temperature shift (HTS): 350–450°C, Fe2O3-Cr2O3 catalyst, "
            "    CO reduced from ~12% to ~3%. "
            "  Low-temperature shift (LTS): 190–250°C, Cu-ZnO-Al2O3 catalyst, "
            "    CO reduced to <0.3%. "
            "  DWSIM: use two ConversionReactors in series (HTS: 80% conv; LTS: 90% conv) "
            "    or GibbsReactor per stage at respective temperatures. "
            "CO2 removal after WGS: PSA (Pressure Swing Adsorption), "
            "  MEA/MDEA absorption, or membrane separation. "
            "Reverse WGS (rWGS): CO2 + H2 → CO + H2O at >700°C — "
            "  used in CO2 utilization and syngas production from CO2."
        ),
        "tags": ["water gas shift", "WGS", "CO", "H2", "CO2", "H2O", "equilibrium",
                 "HTS", "LTS", "syngas", "hydrogen purification", "exothermic",
                 "Fe catalyst", "Cu catalyst", "PSA"],
        "source": "Callaghan (2006) PhD Thesis WPI; Moe (1962) Chem. Eng. Prog.",
    },
    {
        "id": "rxn_ammonia_synthesis",
        "title": "Haber-Bosch Ammonia Synthesis — Reaction Thermodynamics and Kinetics",
        "text": (
            "Ammonia synthesis: N2 + 3H2 → 2NH3   ΔH298 = -92.4 kJ/mol (exothermic). "
            "Equilibrium mole fraction of NH3 (Kp in bar, H2/N2 = 3:1 stoichiometry): "
            "  ln(Kp) = -10.6 + 5900/T  (T in K, P in bar). "
            "  Kp at 400°C = 0.00659 bar^-1;  at 450°C = 0.00317 bar^-1. "
            "  At 450°C, 150 bar: yNH3(eq) ≈ 25-28 mol%. "
            "  At 450°C, 300 bar: yNH3(eq) ≈ 40-45 mol%. "
            "Industrial Haber-Bosch conditions: "
            "  Temperature: 400–500°C (kinetics vs equilibrium trade-off). "
            "  Pressure: 150–300 bar (high P favors NH3, but high compression cost). "
            "  Catalyst: alpha-Fe with K2O (promoter), Al2O3, CaO promoters. "
            "  Single-pass conversion: 15–25% (equilibrium limited at operating T). "
            "  Recycle ratio: 3–5:1 (recycle unreacted N2+H2 after NH3 condensation). "
            "Temkin-Pyzhev kinetic equation: "
            "  r = k × (fN2 × fH2^1.5 / fNH3) - k_rev × (fNH3 / fH2^1.5) "
            "  Activation energy (forward): ~170 kJ/mol; pre-exponential: 1.0e11 mol/(g_cat·s). "
            "In DWSIM: GibbsReactor at 450°C, 200 bar with recycle loop. "
            "Separation: refrigeration to -20°C to condense NH3 (Psat ~ 8.6 bar at -20°C). "
            "Energy: ~28-32 GJ/t NH3 (modern plants); theoretical minimum ~20 GJ/t."
        ),
        "tags": ["ammonia", "Haber-Bosch", "N2", "H2", "NH3", "equilibrium", "kinetics",
                 "Fe catalyst", "exothermic", "recycle", "high pressure", "Keq",
                 "Temkin-Pyzhev", "fertilizer", "nitrogen fixation"],
        "source": "Appl (1999) Ammonia Wiley-VCH; Jennings (1991) Catalytic Ammonia Synthesis",
    },
    {
        "id": "rxn_methanol_synthesis",
        "title": "Methanol Synthesis — Reaction Thermodynamics and Cu/ZnO Catalyst",
        "text": (
            "Methanol synthesis from syngas — two parallel reactions: "
            "  (1) CO  + 2H2 → CH3OH    ΔH298 = -90.5 kJ/mol  (exothermic) "
            "  (2) CO2 + 3H2 → CH3OH + H2O  ΔH298 = -49.5 kJ/mol  (exothermic) "
            "  (3) CO  + H2O → CO2 + H2  ΔH298 = -41.2 kJ/mol  (WGS, reverse also occurs) "
            "Modern plants use CO2-containing syngas — reaction (2) is dominant. "
            "Equilibrium: high P and low T favor methanol. "
            "  At 250°C, 50 bar: equilibrium methanol yield ~55-60 mol% (CO2/H2 feed). "
            "  Keq(1) = exp(-21.30 + 9143.6/T)  (T in K, P in bar). "
            "Industrial conditions: "
            "  Temperature: 200–280°C. Pressure: 50–100 bar. "
            "  Catalyst: Cu/ZnO/Al2O3 (ICI low-pressure catalyst), 230–250°C optimum. "
            "  Single-pass conversion: 15–25% (recycle loop used). "
            "  Space velocity: 4000–10000 h^-1 GHSV. "
            "  Catalyst lifetime: 2–5 years (deactivation by sintering, S poisoning). "
            "Stoichiometric module: M = (H2 - CO2)/(CO + CO2); optimum M = 2.0–2.1. "
            "In DWSIM: ConversionReactor at 250°C, 80 bar with 20% CO conversion, "
            "  or GibbsReactor. Distillation separates crude methanol (water removal). "
            "Green methanol: CO2 + 3H2 from renewable electricity → CO2 utilization."
        ),
        "tags": ["methanol synthesis", "CO", "CO2", "H2", "CH3OH", "Cu/ZnO", "catalyst",
                 "syngas", "exothermic", "equilibrium", "green methanol", "Keq",
                 "low pressure", "recycle", "stoichiometric module"],
        "source": "Spath & Dayton (2003) NREL; Bozzano & Manenti (2016) Prog. Energy Combust.",
    },
    {
        "id": "rxn_ethylene_oxide",
        "title": "Ethylene Oxide (EO) Synthesis — Ag Catalyst, Selectivity, and Safety",
        "text": (
            "Ethylene oxidation to ethylene oxide (EO): "
            "  (1) C2H4 + 0.5O2 → C2H4O (EO)   ΔH298 = -105 kJ/mol  (desired) "
            "  (2) C2H4 + 3O2  → 2CO2 + 2H2O   ΔH298 = -1323 kJ/mol (combustion, undesired) "
            "Industrial conditions: "
            "  Temperature: 220–280°C. Pressure: 10–30 bar. "
            "  Catalyst: silver on alpha-Al2O3, Cs/Re promoters. "
            "  O2 feed: pure O2 (oxygen process) or air (air process). "
            "  Selectivity to EO: 80–90% (modern Cs-promoted catalysts). "
            "  CO2 recycle: CO2 (from combustion) recirculated to suppress combustion reaction. "
            "  Chloride moderators (1–2 ppm C2H5Cl or C2H4Cl2) improve selectivity. "
            "Reaction temperature sensitivity: "
            "  Every 1°C increase raises EO rate by ~3% but combustion rate by ~5% → "
            "  temperature runaway risk above 290°C. "
            "  Multi-tubular fixed-bed reactor with boiling water cooling (steam generation). "
            "EO is further reacted to ethylene glycol (MEG): "
            "  C2H4O + H2O → HOCH2CH2OH   ΔH = -80 kJ/mol. "
            "In DWSIM: ConversionReactor at 250°C, 20 bar, 85% selectivity to EO. "
            "SAFETY: EO is explosive (3–100 vol% in air), toxic, carcinogenic."
        ),
        "tags": ["ethylene oxide", "EO", "ethylene", "Ag catalyst", "silver", "selectivity",
                 "ethylene glycol", "MEG", "exothermic", "runaway", "fixed bed reactor",
                 "safety", "chloride moderator", "combustion"],
        "source": "Rebsdat & Mayer (2012) Ullmann's; Kirk-Othmer Enc. Chem. Tech.",
    },
    {
        "id": "rxn_esterification",
        "title": "Esterification and Transesterification — Reaction Equilibrium and Kinetics",
        "text": (
            "Esterification: ROH + R'COOH ⇌ R'COOR + H2O  (reversible, acid-catalyzed). "
            "Equilibrium constant Keq ≈ 1–10 (not strongly favored — need to drive forward). "
            "Methods to shift equilibrium toward ester: "
            "  1. Excess alcohol (2–10:1 alcohol:acid molar ratio). "
            "  2. Remove water continuously (reactive distillation, pervaporation). "
            "  3. Use Dean-Stark trap for small batches. "
            "Common example — ethyl acetate from ethanol + acetic acid: "
            "  C2H5OH + CH3COOH ⇌ CH3COOC2H5 + H2O   Keq ≈ 4.0 at 25°C. "
            "  ΔH298 ≈ -3 kJ/mol (near-thermoneutral). "
            "  Arrhenius: k_fwd = 4.76e-4 × exp(-59500/RT) L/(mol·s), H+ catalyst. "
            "Transesterification (biodiesel): "
            "  Triglyceride + 3 CH3OH → 3 FAME + Glycerol  (NaOH or KOH catalyst). "
            "  ΔH ≈ -10 kJ/mol (slightly exothermic). "
            "  Conditions: 60–65°C, methanol:oil 6:1 molar, 0.5–1% NaOH catalyst. "
            "  Conversion >98% in 1–2 hours batch or continuous plug-flow reactor. "
            "  Glycerol byproduct (10 wt% of biodiesel) must be separated by gravity or centrifuge. "
            "In DWSIM: ConversionReactor with 98% conversion for transesterification; "
            "  for esterification with recycle, use Gibbs or set conversion from Keq."
        ),
        "tags": ["esterification", "transesterification", "FAME", "biodiesel", "ethyl acetate",
                 "equilibrium", "Keq", "reactive distillation", "glycerol", "NaOH",
                 "acid catalyst", "kinetics", "Arrhenius"],
        "source": "Fogler (2016) Elements of CRE Ch. 5; Freedman et al. (1986) JAOCS",
    },
    {
        "id": "rxn_fermentation",
        "title": "Ethanol Fermentation — Biochemistry, Yield, and Reactor Design",
        "text": (
            "Alcoholic fermentation (yeast — Saccharomyces cerevisiae): "
            "  C6H12O6 → 2 C2H5OH + 2 CO2   ΔH = -235 kJ/mol glucose (exothermic). "
            "  Theoretical yield: 0.511 g ethanol / g glucose (Gay-Lussac). "
            "  Practical yield: 90–95% of theoretical = 0.46–0.49 g EtOH / g glucose. "
            "Monod kinetics: μ = μmax × S / (Ks + S) × (1 - P/Pm)^n "
            "  μmax = 0.4 h^-1; Ks = 0.22 g/L; Pm = 87 g/L (product inhibition); n = 0.5. "
            "Conditions: T = 30–35°C; pH = 4.5–5.5; [glucose] = 100–200 g/L. "
            "  Ethanol inhibition above 80 g/L — limits batch concentration. "
            "Bioreactor types: "
            "  Batch: 48–72h, 80–100 g/L ethanol. Simplest, most common. "
            "  Fed-batch: glucose fed continuously to avoid inhibition. "
            "  Continuous CSTR: steady state, lower productivity than batch. "
            "  Vacuum fermentation: ethanol stripped continuously, higher concentrations. "
            "CO2 production: 0.489 kg CO2 / kg ethanol produced (must vent). "
            "Heat generation: ~1.2 kW per m³ fermenter (cooling water required). "
            "In DWSIM: model as ConversionReactor at 32°C, 98% glucose conversion, "
            "  specify molar stoichiometry C6H12O6 → 2 EtOH + 2 CO2."
        ),
        "tags": ["fermentation", "ethanol", "yeast", "glucose", "C6H12O6",
                 "Monod kinetics", "bioreactor", "batch", "CSTR", "fed-batch",
                 "product inhibition", "bioethanol", "CO2", "exothermic"],
        "source": "Shuler & Kargi (2002) Bioprocess Engineering; Doran (2013) Bioprocess Principles",
    },

    # ══════════════════════════════════════════════════════════
    # LIQUID-LIQUID EXTRACTION (LLE)
    # ══════════════════════════════════════════════════════════

    {
        "id": "lle_fundamentals",
        "title": "Liquid-Liquid Extraction (LLE) — Fundamentals and Solvent Selection",
        "text": (
            "Liquid-liquid extraction (LLE) separates components between two immiscible "
            "liquid phases. Used when distillation is impractical (azeotropes, heat-sensitive, "
            "similar volatility, dilute aqueous solutions). "
            "Key concepts: "
            "  Distribution coefficient: Kd = (solute concentration in solvent) / (solute in raffinate). "
            "  Selectivity: β12 = Kd1 / Kd2 (target over impurity). "
            "  Single-stage extraction: y = Kd × x / (1 + Kd × S/F) where S/F = solvent-to-feed ratio. "
            "  N theoretical stages: E = Kd × S/F (extraction factor); recovery = E^N / (1 + E^N). "
            "Solvent selection criteria (Robbins' rules): "
            "  1. High distribution coefficient (Kd >> 1). "
            "  2. High selectivity (β >> 1 for target vs impurities). "
            "  3. Large density difference (>50 kg/m³) for phase separation. "
            "  4. Low mutual solubility with raffinate phase. "
            "  5. Easy regeneration (stripping, back-extraction, distillation). "
            "  6. Low viscosity, low toxicity, low cost. "
            "Common solvent-solute-water systems: "
            "  Acetic acid / water → ethyl acetate (β = 2.7), ethyl ether (β = 4.0). "
            "  Phenol / water → butyl acetate (β = 8.5), diisopropyl ether (β = 10). "
            "  Caprolactam / water → benzene (β = 2.0), toluene (β = 1.8). "
            "  Furfural / water → methyl isobutyl ketone (MIBK, β = 3.2). "
            "In DWSIM: use Liquid-Liquid Extractor unit op with NRTL property package. "
            "Ensure both liquid phases form (LLE — not VLE). Check NRTL alpha parameter: "
            "for alpha < 0.2 and high tau, two liquid phases likely."
        ),
        "tags": ["LLE", "liquid-liquid extraction", "distribution coefficient", "selectivity",
                 "solvent", "raffinate", "extract", "NRTL", "two liquid phases",
                 "acetic acid", "phenol", "furfural", "extraction factor"],
        "source": "Seider et al.: Product & Process Design Principles Ch. 8; Perry's 9th ed. Sec. 15",
    },
    {
        "id": "lle_data_systems",
        "title": "LLE Tie-Line Data — Common Binary and Ternary Systems",
        "text": (
            "Liquid-liquid equilibrium (LLE) tie-line compositions at 25°C, 1 atm: "
            "n-Butanol / Water system: "
            "  Organic phase: 79.9 mol% n-BuOH, 20.1 mol% H2O. "
            "  Aqueous phase: 1.5 mol% n-BuOH, 98.5 mol% H2O. "
            "  Plait point: ~40 mol% n-BuOH, 60°C. "
            "Benzene / Water system: "
            "  Organic phase: 99.95 mol% benzene. "
            "  Aqueous phase: 0.04 mol% benzene (solubility 1.79 g/L). "
            "n-Hexane / Ethanol / Water ternary (Kd at 25°C): "
            "  Ethanol Kd (hexane/water) ≈ 0.10-0.20 (ethanol prefers water). "
            "  Use extractive distillation, not LLE, for ethanol-water separation. "
            "Acetic acid / Ethyl acetate / Water (30°C): "
            "  Organic phase: 61% EtOAc, 22% AcOH, 17% H2O. "
            "  Aqueous phase: 6% EtOAc, 28% AcOH, 66% H2O. "
            "  Kd(AcOH) ≈ 0.79 in this system — multiple stages needed. "
            "Furfural / Water (25°C): solubility = 8.3 g/100 mL (partially miscible). "
            "Phenol / Water (65.8°C, upper critical solution temperature): "
            "  Below 65.8°C: two phases;  above 65.8°C: fully miscible. "
            "NRTL alpha values < 0.2 with large |tau| → two liquid phases in DWSIM. "
            "Verification: run flash at desired T,P — if two liquid phases appear, LLE is active. "
            "Use DECHEMA LLE Data Collection or NIST for experimental tie-line data."
        ),
        "tags": ["LLE", "tie-line", "two liquid phases", "n-butanol", "benzene", "phenol",
                 "furfural", "acetic acid", "ethyl acetate", "solubility", "NRTL",
                 "UCST", "partial miscibility", "DECHEMA"],
        "source": "Sorensen & Arlt: DECHEMA LLE Data Collection (1979); Treybal (1963)",
    },
    {
        "id": "lle_equipment_design",
        "title": "Liquid-Liquid Extraction Equipment — Columns, Mixer-Settlers, Sizing",
        "text": (
            "LLE equipment types: "
            "Mixer-Settler: "
            "  - Stages: 1–20 theoretical stages, each = mixing tank + gravity settler. "
            "  - High throughput, easy scale-up, low head requirement. "
            "  - Stage efficiency: 70–95% (near-theoretical). "
            "  - Settling time: 5–30 min (function of interfacial tension and density diff). "
            "Pulsed sieve-plate column: "
            "  - HETS (Height Equivalent to Theoretical Stage): 0.3–1.0 m. "
            "  - Pulse frequency: 1–3 Hz; amplitude: 10–25 mm. "
            "  - Capacity: 10–80 m³/(m²·h) based on column cross-section. "
            "Rotary disc contactor (RDC): "
            "  - HETS: 0.2–0.5 m. Good for viscous systems. "
            "Centrifugal extractor (Podbielniak): "
            "  - Very short residence time (seconds) — ideal for thermally labile compounds. "
            "  - Used in antibiotics, vitamins extraction. "
            "Sizing rules of thumb: "
            "  Flooding velocity: ud = 0.7 × [(ρH - ρL)/ρL]^0.5 × d^0.5 m/s. "
            "  Minimum density difference for gravity settling: 50 kg/m³. "
            "  Interfacial tension > 5 mN/m required for clean phase separation. "
            "  Diameter: D = sqrt(4Q/π/ud), where Q = total volumetric flow. "
            "Raffinate washing: add water wash stage after extraction to recover solvent. "
            "In DWSIM: Liquid-Liquid Extractor with N stages → run with NRTL. "
            "Post-extraction: recover solvent by distillation or back-extraction."
        ),
        "tags": ["LLE equipment", "mixer-settler", "pulsed column", "RDC", "HETS",
                 "flooding", "extractor", "sizing", "interfacial tension", "settling",
                 "capacity", "DWSIM", "extraction column"],
        "source": "Treybal: Mass Transfer Operations 3rd ed.; Perry's 9th ed. Sec. 15",
    },

    # ══════════════════════════════════════════════════════════
    # ADVANCED THERMODYNAMIC MODELS
    # ══════════════════════════════════════════════════════════

    {
        "id": "thermo_uniquac_detail",
        "title": "UNIQUAC Model — Parameters, Structure, and When to Use",
        "text": (
            "UNIQUAC (Universal Quasi-Chemical) activity coefficient model. "
            "Parameters per binary pair: u12-u22 and u21-u11 (in K, energy units). "
            "τij = exp(-(uij-ujj)/T)  where T is in Kelvin. "
            "UNIQUAC uses molecular size (r) and surface area (q) parameters in addition to BIPs: "
            "  Example r and q values: "
            "    Water: r=0.920,  q=1.400 "
            "    Methanol: r=1.431, q=1.432 "
            "    Ethanol: r=2.106,  q=1.972 "
            "    Acetone: r=2.574,  q=2.336 "
            "    Benzene: r=3.187,  q=2.400 "
            "    n-Hexane: r=4.500, q=3.856 "
            "Advantages over NRTL: "
            "  - Only 2 energy parameters per pair (vs 3 for NRTL). "
            "  - Better extrapolation to multicomponent mixtures. "
            "  - Can model both VLE and LLE with same parameters. "
            "  - More physically meaningful (based on molecular geometry). "
            "When to prefer UNIQUAC over NRTL: "
            "  - Multicomponent systems (3+ components) where interaction matrix is large. "
            "  - Polymer solutions (extended UNIQUAC-FV). "
            "  - When only limited experimental data available (less overfitting risk). "
            "UNIFAC: group-contribution method to estimate UNIQUAC parameters without "
            "  experimental data. Less accurate than fitted BIPs but usable for screening. "
            "In DWSIM: select 'UNIQUAC' property package. Enter u12-u22 (K) in BIP table."
        ),
        "tags": ["UNIQUAC", "activity coefficient", "BIP", "binary interaction", "u12", "u21",
                 "r parameter", "q parameter", "VLE", "LLE", "UNIFAC", "polymer",
                 "multicomponent", "NRTL comparison"],
        "source": "Abrams & Prausnitz (1975) AIChE J.; Fredenslund, Jones & Prausnitz (1975)",
    },
    {
        "id": "thermo_nrtl_temperature_dependent",
        "title": "Temperature-Dependent NRTL — a+b/T Form for Distillation Accuracy",
        "text": (
            "Standard NRTL uses fixed τ12 and τ21 fitted at a reference temperature (usually 25°C). "
            "For distillation columns operating at 60–200°C, fixed-τ parameters "
            "can introduce errors of 5–15% in VLE predictions. "
            "Temperature-dependent NRTL: τij(T) = aij + bij/T  (T in Kelvin). "
            "The aij term dominates at high T; bij/T dominates at low T. "
            "At T=298K: τij = aij + bij/298 — this equals the standard fixed-τ value. "
            "Available T-dependent parameters from DECHEMA and Aspen databank: "
            "  Ethanol / Water: τ12(T) = 3.000 - 712.32/T;  τ21(T) = -0.563 + 385.16/T "
            "  Methanol / Water: τ12(T) = 0.799 + 281.48/T; τ21(T) = -1.027 + 544.64/T "
            "  Acetone / Water:  τ12(T) = 1.435 + 190.45/T; τ21(T) = -0.369 + 396.22/T "
            "When to use T-dependent NRTL: "
            "  - Column feed enters at 25°C but bottoms exits at 100°C → 75°C range. "
            "  - High-purity separation requires accurate VLE throughout column. "
            "  - When fixed-τ model shows inflections in x-y diagram at off-reference T. "
            "In DWSIM: if available, set NRTL BIPs with temperature-dependent coefficients "
            "  in the interaction parameter table (some DWSIM versions support this). "
            "  Otherwise, re-fit τ values at the average column temperature."
        ),
        "tags": ["NRTL", "temperature dependent", "tau", "BIP", "distillation", "VLE",
                 "a+b/T", "ethanol-water", "methanol-water", "acetone-water",
                 "DECHEMA", "Aspen", "accuracy", "column design"],
        "source": "Gmehling et al. DECHEMA VLE Data; Aspen Plus V14 databank",
    },

    # ══════════════════════════════════════════════════════════
    # PROCESS ENGINEERING PROCEDURES
    # ══════════════════════════════════════════════════════════

    {
        "id": "proc_distill_init",
        "title": "Distillation Column Initialization — Systematic Convergence Procedure",
        "text": (
            "Distillation columns are the hardest DWSIM unit op to converge. "
            "Use this systematic procedure to initialize reliably: "
            "STEP 1 — Run shortcut column (Fenske-Underwood-Gilliland) first. "
            "  Provides: N_min (minimum theoretical stages), R_min (minimum reflux). "
            "  Set shortcut: light key (LK) and heavy key (HK) component recoveries "
            "  (e.g., 99% LK in distillate, 1% in bottoms). "
            "  Shortcut result: N_actual = 1.5–2.0 × N_min; R_actual = 1.2–1.5 × R_min. "
            "STEP 2 — Estimate stage temperatures before setting profile. "
            "  Top stage T ≈ bubble point of distillate at column pressure. "
            "  Bottom stage T ≈ bubble point of bottoms at column pressure. "
            "  Use compute_vapor_pressure tool to estimate bubble points from Antoine. "
            "  Set linear temperature profile between top and bottom estimates. "
            "STEP 3 — Set column specifications in DWSIM: "
            "  - Number of stages: N_actual from shortcut. "
            "  - Feed stage: N/2 to N/3 from top (adjust if feed is liquid or vapor). "
            "  - Reflux ratio: 1.3 × R_min as starting point. "
            "  - Distillate rate: set to recover LK. "
            "  - Condenser: Total or Partial depending on product state. "
            "  - Reboiler: always Partial for liquid bottoms. "
            "STEP 4 — Convergence troubleshooting if column fails: "
            "  - Increase max iterations to 100+ (DWSIM default is often 50). "
            "  - Tighten damping factor: try 0.7–0.8 instead of default 1.0. "
            "  - Switch convergence method: try Boston-Britt if Naphtali-Sandholm fails. "
            "  - For high-purity separations (99.9%+): relax purity first, then tighten. "
            "  - Check for missing NRTL BIPs — use lookup_binary_parameters tool. "
            "STEP 5 — Azeotropic systems: "
            "  If LK/HK have relative volatility < 1.05 anywhere in the column, "
            "  simple distillation cannot achieve the separation. "
            "  Options: extractive distillation (add solvent), pressure-swing distillation, "
            "  or azeotropic distillation with entrainer. "
            "  Check azeotrope data with search_knowledge('azeotrope') first."
        ),
        "tags": ["distillation", "column initialization", "convergence", "Fenske", "Underwood",
                 "Gilliland", "shortcut column", "reflux ratio", "theoretical stages",
                 "bubble point", "temperature profile", "DWSIM", "azeotrope",
                 "feed stage", "Boston-Britt", "Naphtali-Sandholm"],
        "source": "Seider et al.: Product & Process Design Principles Ch. 11; Perry's 9th ed. Sec. 13",
    },
    {
        "id": "proc_lle_init",
        "title": "Liquid-Liquid Extractor Initialization in DWSIM — Convergence Procedure",
        "text": (
            "DWSIM's Liquid-Liquid Extractor is difficult to initialize. "
            "Follow this procedure to achieve convergence: "
            "STEP 1 — Verify two liquid phases actually exist before building the column. "
            "  Run a simple flash (FlashSpec=TP) on the feed mixture at column conditions. "
            "  Check vapor fraction: should be 0 (all liquid). "
            "  Check phase count: DWSIM should report 2 liquid phases if LLE is active. "
            "  If only 1 liquid phase: the system may be above the critical solution "
            "  temperature — reduce temperature or check property package. "
            "STEP 2 — Property package selection is critical for LLE. "
            "  MUST use NRTL or UNIQUAC with LLE-fitted BIPs (not VLE-fitted). "
            "  PR/SRK EOS cannot predict LLE — will show only one liquid phase. "
            "  Use lookup_binary_parameters with model='nrtl' to get DECHEMA BIPs. "
            "  Verify NRTL alpha < 0.2 for strongly immiscible systems. "
            "STEP 3 — Set initial composition estimates. "
            "  DWSIM extractor needs initial phase compositions to start iteration. "
            "  Set organic phase initial composition based on solvent solubility data. "
            "  Set aqueous phase initial composition ≈ feed composition minus solvent. "
            "STEP 4 — Column setup. "
            "  Start with 1–3 theoretical stages (mixer-settler equivalent). "
            "  Solvent-to-feed (S/F) ratio: start with Kd × S/F ≈ 1.5 (extraction factor ~1.5). "
            "  Heavy phase (typically water) enters at top; light phase (organic) enters at bottom. "
            "STEP 5 — If convergence fails: "
            "  - Increase damping factor (0.5–0.7). "
            "  - Reduce number of stages to 1 and confirm it converges, then increase. "
            "  - Verify two-phase split occurs by running standalone flash first. "
            "  - Check that solvent and feed streams are connected to correct ports."
        ),
        "tags": ["LLE", "liquid-liquid extractor", "initialization", "convergence", "DWSIM",
                 "NRTL", "UNIQUAC", "two liquid phases", "LLE BIPs", "extraction factor",
                 "solvent-to-feed", "phase split", "mixer-settler", "heavy phase"],
        "source": "Perry's 9th ed. Sec. 15; DWSIM Documentation; Treybal: Mass Transfer Operations",
    },
    {
        "id": "proc_pressure_profile",
        "title": "Pressure Profile Design — Consistent Flowsheet Pressure Schedule",
        "text": (
            "Pressure inconsistency is a leading cause of DWSIM convergence failures. "
            "Follow these rules to build a consistent pressure schedule: "
            "RULE 1 — Work from high pressure to low pressure along the flow direction. "
            "  Typical plant: High-pressure reaction (20–300 bar) → letdown → "
            "  separation (5–20 bar) → purification (1–3 bar) → storage (1 atm). "
            "RULE 2 — Every pressure drop must be accounted for explicitly. "
            "  Control valve: set DeltaP in bar (typical process valve: 0.5–5 bar). "
            "  Heat exchanger: typical shell-side DeltaP = 0.3–0.7 bar. "
            "  Pipe segment: calculate from Darcy-Weisbach or set 0.1–0.5 bar/100m. "
            "  Distillation column: 0.05–0.10 bar per theoretical stage (tray column). "
            "  Packed column: 0.005–0.020 bar per meter of packing. "
            "RULE 3 — Pumps and compressors must supply enough ΔP. "
            "  Pump outlet P ≥ highest pressure destination + all pipe/HX losses. "
            "  Compressor ratio per stage: max 3.5–4.0 (avoid above this without intercooling). "
            "  For multi-stage compression: use equal pressure ratios per stage. "
            "RULE 4 — Feed stream pressures must match unit op inlet specifications. "
            "  If a stream enters a distillation column at 5 bar, set column P = 5 bar. "
            "  Mismatched pressure causes flash discontinuities and convergence failure. "
            "RULE 5 — Recycle loops need pressure balance. "
            "  Total pressure drop around the recycle loop must equal zero at steady state. "
            "  If not, add a pump/compressor to make up the pressure loss. "
            "  Use initialize_recycle before solving any flowsheet with a recycle loop. "
            "In DWSIM: set stream pressure using set_stream_property with property_name='pressure' "
            "and unit='bar'. Always verify with get_stream_properties after solve."
        ),
        "tags": ["pressure profile", "pressure drop", "convergence", "pump", "compressor",
                 "valve", "heat exchanger", "distillation", "packed column", "Darcy-Weisbach",
                 "recycle", "pressure schedule", "multi-stage compression", "DWSIM"],
        "source": "Seider et al.: Product & Process Design Principles; Perry's 9th ed. Sec. 6",
    },
    {
        "id": "proc_property_pkg_selection",
        "title": "Property Package Selection Guide — Decision Tree for DWSIM Simulations",
        "text": (
            "Choosing the wrong property package is the #1 cause of wrong simulation results. "
            "Use this decision tree BEFORE creating any flowsheet: "
            "1. Pure water or water/steam only? → Steam Tables (IAPWS-IF97). "
            "   Most accurate for T range 0–800°C. "
            "2. Hydrocarbons only (C1–C12, no polar compounds)? "
            "   → Peng-Robinson (PR) or SRK. "
            "   PR preferred for C7+ and gas condensate; SRK for light gas. "
            "   Set kij ≠ 0 for CO2, H2S, N2, H2 in the mixture. "
            "3. Polar organics + water (alcohols, ketones, esters, acids)? "
            "   → NRTL or UNIQUAC with fitted BIPs. "
            "   Call lookup_binary_parameters to get DECHEMA BIPs. "
            "   Never use PR/SRK — ethanol-water, acetone-water VLE will be wrong. "
            "4. Electrolytes (acids, bases, salts in water)? "
            "   → eNRTL (electrolyte NRTL) or Pitzer model. "
            "   Standard NRTL/UNIQUAC cannot model ionization. "
            "5. Refrigerants (R134a, R22, R32, ammonia)? "
            "   → CoolProp (most accurate) or PR/SRK with refrigerant parameters. "
            "   REFPROP if available. "
            "6. Polymers or high-molecular-weight compounds? "
            "   → UNIQUAC-FV (free volume) or SAFT equation of state. "
            "7. Near-critical or supercritical conditions? "
            "   → Use PR or SRK — cubic EOS works near critical point. "
            "   Check: is T > 0.9×Tc? Use lookup_compound_properties to verify Tc. "
            "8. Mixed: hydrocarbons + CO2 + H2S (sour gas, natural gas)? "
            "   → PR with H2S-CH4, CO2-CH4 kij from the kij table in knowledge base. "
            "In DWSIM: set property package in new_flowsheet 'property_package' parameter. "
            "After setting, verify with get_property_package tool."
        ),
        "tags": ["property package", "EOS", "NRTL", "UNIQUAC", "Peng-Robinson", "SRK",
                 "Steam Tables", "CoolProp", "eNRTL", "refrigerant", "electrolyte",
                 "decision tree", "selection guide", "kij", "polar", "hydrocarbons"],
        "source": "Smith, Van Ness & Abbott Ch. 3; Perry's 9th ed. Sec. 4; Seider et al. Ch. 3",
    },

    # ══════════════════════════════════════════════════════════
    # EQUIPMENT SIZING
    # ══════════════════════════════════════════════════════════

    {
        "id": "sizing_distill_column",
        "title": "Distillation Column Sizing — Diameter, Tray Spacing, and Packed Height",
        "text": (
            "After determining theoretical stages N and reflux ratio R, physical column sizing: "
            "COLUMN DIAMETER from Souders-Brown equation: "
            "  Flooding velocity: u_f = C_SB × sqrt((rho_L - rho_V) / rho_V)  [m/s] "
            "  C_SB (Souders-Brown factor): 0.06-0.12 m/s for tray columns (use 0.08 as default). "
            "  Volumetric vapor flow at top: Q_V = V_molar × MW_avg / rho_V  [m³/s] "
            "  Cross-sectional area: A = Q_V / (0.8 × u_f)  [m²]  (80% flood design) "
            "  Column diameter: D = sqrt(4A/pi)  [m] "
            "Example: 1000 kmol/h vapor, rho_V=2 kg/m³, rho_L=800 kg/m³, MW=30 g/mol: "
            "  Q_V = (1000/3600) × 30/1000 / 2 = 4.17 m³/s "
            "  u_f = 0.08 × sqrt((800-2)/2) = 1.59 m/s "
            "  A = 4.17 / (0.8 × 1.59) = 3.28 m²  → D = 2.04 m (use 2.1 m standard) "
            "TRAY COLUMN HEIGHT: "
            "  Actual stages = N_theoretical / tray_efficiency (Murphree efficiency 0.6-0.8 typical). "
            "  Tray spacing: 0.45-0.60 m (0.50 m standard). "
            "  Column height = actual_stages × tray_spacing + 3-5 m (bottom sump + top disengagement). "
            "PACKED COLUMN: "
            "  HETP (Height Equivalent to Theoretical Plate): 0.3-0.6 m for structured packing "
            "    (Mellapak 250Y, Koch-Glitsch), 0.4-0.8 m for random packing (25mm Raschig rings). "
            "  Packed height = N_theoretical × HETP. "
            "  Pressure drop: 0.5-2.0 mbar/m at 70% flood (structured), 3-8 mbar/m (random). "
            "  F-factor check: F = u_V × sqrt(rho_V) < 2.5 Pa^0.5 (avoid flooding). "
            "In DWSIM: report D, H, tray count after shortcut → rigorous column simulation."
        ),
        "tags": ["column sizing", "Souders-Brown", "flooding velocity", "diameter", "tray",
                 "packed column", "HETP", "Murphree efficiency", "tray spacing",
                 "distillation design", "structured packing"],
        "source": "Perry's 9th ed. Sec. 14; Fair (1961) Petro/Chem. Eng.; Billet (1995)",
    },
    {
        "id": "sizing_heat_exchanger",
        "title": "Heat Exchanger Sizing — LMTD, U Value, and Area Calculation",
        "text": (
            "Shell & tube heat exchanger sizing from process duty: "
            "STEP 1 — Calculate LMTD (Log Mean Temperature Difference): "
            "  Counter-current (preferred): "
            "    ΔT1 = T_hot_in - T_cold_out;  ΔT2 = T_hot_out - T_cold_in "
            "    LMTD = (ΔT1 - ΔT2) / ln(ΔT1/ΔT2) "
            "  Apply correction factor F (0.8-1.0 for multi-pass): LMTD_corrected = F × LMTD "
            "STEP 2 — Overall heat transfer coefficient U (W/m²/K): "
            "  Liquid-liquid:        U = 300-800  W/m²/K "
            "  Gas-liquid:           U = 20-300   W/m²/K "
            "  Condensing steam/liquid: U = 1000-6000 W/m²/K "
            "  Vaporizing liquid/steam: U = 500-2500 W/m²/K "
            "  Typical shell & tube:  U = 300-1000 W/m²/K (liquids both sides) "
            "STEP 3 — Required area: A = Q / (U × LMTD_corrected) [m²] "
            "Example: Q=1 MW, T_hot: 150→80°C, T_cold: 20→60°C counter-current: "
            "  ΔT1=150-60=90°C, ΔT2=80-20=60°C, LMTD=73.9°C "
            "  U=500 W/m²/K → A = 1,000,000 / (500×73.9) = 27.1 m² "
            "FOULING ALLOWANCE: Add 20-25% to area for fouling (Rf = 0.0002 m²K/W typical). "
            "TUBE SIZING: 19.05mm OD (3/4 in) or 25.4mm OD (1 in) standard. "
            "  Tube velocity: 1-3 m/s (liquid), 10-30 m/s (gas) to control fouling. "
            "In DWSIM: set U, A, and LMTD target in HeatExchanger parameters. "
            "Verify: duty from stream ΔH must match set Q within 5%."
        ),
        "tags": ["heat exchanger sizing", "LMTD", "U value", "overall heat transfer",
                 "shell tube", "fouling", "area calculation", "HX design",
                 "condensing", "vaporizing", "counter-current"],
        "source": "Kern (1950) Process Heat Transfer; Perry's 9th ed. Sec. 11; Seider et al.",
    },
    {
        "id": "sizing_pump_compressor",
        "title": "Pump and Compressor Sizing — Power, Head, and NPSH",
        "text": (
            "PUMP SIZING: "
            "Hydraulic power: P_hyd = rho × g × Q × H  [W] "
            "  rho = liquid density [kg/m³], g=9.81 m/s², Q=volumetric flow [m³/s], H=head [m] "
            "Shaft power: P_shaft = P_hyd / eta_pump (pump efficiency eta = 0.6-0.85 typical) "
            "Conversion: 1 bar pressure rise = 10.2 m head (water), = 102 m head (gas at rho=1 kg/m³). "
            "For slurries or viscous liquids: use Hydraulic Institute correction curves. "
            "NPSH (Net Positive Suction Head): "
            "  NPSH_available = (P_inlet - P_vapor) / (rho × g) + V²/2g + Z_inlet "
            "  Must exceed NPSH_required (from pump curve, typically 2-6 m). "
            "  Cavitation occurs when NPSH_available < NPSH_required. "
            "  Risk: hot liquids near boiling point (ethanol at 70°C, steam condensate). "
            "COMPRESSOR SIZING: "
            "Isentropic work per stage: W_s = (gamma/(gamma-1)) × (P1/rho1) × ((P2/P1)^((gamma-1)/gamma) - 1) "
            "  gamma = Cp/Cv ratio: 1.4 (diatomic gases N2, air), 1.3 (CO2), 1.66 (Ar). "
            "Max compression ratio per stage: P2/P1 ≤ 3.5-4.0 (practical limit, avoid excessive T_out). "
            "Outlet temperature: T2 = T1 × (P2/P1)^((gamma-1)/gamma / eta_is). "
            "For CH4 at T1=25°C, P1=1 bar → P2=10 bar: T2_is = 298×10^(0.4/1.4) = 573K (300°C). "
            "Intercooling required above 200°C outlet — use multiple stages with intercoolers. "
            "Power: P = m_dot × W_s / eta_is (isentropic efficiency 0.75-0.85 centrifugal). "
            "In DWSIM: set outlet P and efficiency; read calculated power and outlet T."
        ),
        "tags": ["pump sizing", "NPSH", "cavitation", "compressor sizing", "isentropic",
                 "compression ratio", "intercooling", "hydraulic power", "pump head",
                 "centrifugal", "adiabatic", "efficiency"],
        "source": "Walas (1990) Chemical Process Equipment; Perry's 9th ed. Sec. 10; Seider et al.",
    },

    # ══════════════════════════════════════════════════════════
    # MULTICOMPONENT DISTILLATION
    # ══════════════════════════════════════════════════════════

    {
        "id": "distill_multicomponent_lkhk",
        "title": "Multicomponent Distillation — Key Component Selection and Distribution",
        "text": (
            "In multicomponent distillation (3+ components), identification of key components "
            "is the most critical step before shortcut calculations. "
            "DEFINITIONS: "
            "  Light Key (LK): the most volatile component that appears significantly in BOTH "
            "    distillate AND bottoms (i.e., the LK is 'split' between products). "
            "  Heavy Key (HK): the least volatile component that appears significantly in BOTH "
            "    distillate AND bottoms. "
            "  Light Non-Key (LNK): more volatile than LK — goes essentially entirely to distillate. "
            "  Heavy Non-Key (HNK): less volatile than HK — goes essentially entirely to bottoms. "
            "LK/HK SELECTION RULES: "
            "  1. Rank components by relative volatility α_i = K_i/K_HK at average column conditions. "
            "     K_i = vapor pressure_i / total pressure (Raoult's law approximation for first guess). "
            "  2. LK = component with α just above 1.0 (just above HK in volatility). "
            "  3. HK = component with α = 1.0 (reference). "
            "  4. Check: LK should be the heaviest component in distillate spec, "
            "     HK should be the lightest component in bottoms spec. "
            "EXAMPLE — Methanol/Ethanol/n-Propanol separation (1 atm): "
            "  Boiling points: MeOH 64.7°C, EtOH 78.4°C, n-PrOH 97.2°C "
            "  α at 80°C: MeOH≈2.1, EtOH≈1.0 (HK), n-PrOH≈0.52 "
            "  LK=EtOH, HK=n-PrOH (if goal is EtOH in distillate and n-PrOH in bottoms). "
            "  MeOH is LNK → goes with distillate. "
            "DISTRIBUTED COMPONENTS: When a component's α is between LK and HK, "
            "  it distributes between products. The Kremser equation or full rigorous simulation "
            "  is needed to determine the split. "
            "MULTICOMPONENT SHORTCUT PROCEDURE: "
            "  1. Select LK and HK as above. "
            "  2. Apply Fenske equation for N_min using LK and HK recoveries. "
            "  3. Apply Underwood equation to find minimum reflux R_min. "
            "     Sum over all components: Σ[α_i × z_i / (α_i - θ)] = 1 - q "
            "     where θ is between αLK and αHK, q = feed thermal condition. "
            "  4. Apply Gilliland correlation for actual N. "
            "In DWSIM ShortcutColumn: set LK, HK, and their recovery fractions in distillate."
        ),
        "tags": ["multicomponent distillation", "light key", "heavy key", "LK", "HK",
                 "relative volatility", "Fenske", "Underwood", "Gilliland", "shortcut",
                 "distributed components", "key component selection", "non-key"],
        "source": "Seider et al. Ch. 11; Treybal: Mass Transfer Operations; Perry's 9th ed.",
    },
    {
        "id": "distill_hen_synthesis",
        "title": "Heat Exchanger Network (HEN) Synthesis After Pinch Analysis",
        "text": (
            "After pinch analysis identifies the pinch temperature and minimum utilities, "
            "implement the Heat Exchanger Network (HEN) following these steps: "
            "STEP 1 — Identify hot streams (need cooling) and cold streams (need heating). "
            "  Above pinch: use hot utility to heat cold streams; never transfer heat across pinch. "
            "  Below pinch: use cold utility to cool hot streams; never transfer heat across pinch. "
            "STEP 2 — Apply pinch rules: "
            "  Rule 1: No heat transfer across the pinch (violates minimum utility target). "
            "  Rule 2: No hot utility below the pinch. "
            "  Rule 3: No cold utility above the pinch. "
            "  Violations of these rules increase energy consumption by exactly the amount transferred. "
            "STEP 3 — Match streams using tick-off heuristic: "
            "  Above pinch: start at pinch. Match hot stream to cold stream where feasible. "
            "    Feasibility: C_P_hot ≤ C_P_cold (heat capacity flowrate) to avoid temperature cross. "
            "  Below pinch: start at pinch. Match cold stream to hot stream. "
            "    Feasibility: C_P_cold ≤ C_P_hot. "
            "STEP 4 — Calculate exchanger areas using LMTD for each match. "
            "STEP 5 — Implement in DWSIM: "
            "  For each heat exchanger match: "
            "  (a) Add a HeatExchanger unit op. "
            "  (b) Connect hot stream inlet/outlet and cold stream inlet/outlet. "
            "  (c) Set duty from pinch analysis match (set Q, not T_out). "
            "  (d) Set minimum approach temperature ΔT_min in HX specifications. "
            "  (e) Verify no temperature crossover: T_hot_out > T_cold_in + ΔT_min. "
            "ENERGY SAVINGS FORMULA: "
            "  Q_HEN_savings = Q_hot_utility_before - Q_hot_utility_after "
            "  For a 10 MW process: typically 20-40% energy savings achievable via HEN. "
            "COMMON MISTAKE: Adding heat exchangers between streams that cross the pinch "
            "  INCREASES total utility consumption — always check which side of pinch each stream is on."
        ),
        "tags": ["HEN synthesis", "heat exchanger network", "pinch analysis", "pinch rules",
                 "tick-off heuristic", "hot utility", "cold utility", "heat integration",
                 "stream matching", "energy saving", "LMTD", "approach temperature"],
        "source": "Linnhoff et al. (1982) User Guide on Process Integration; Kemp (2007) Pinch Analysis",
    },
    {
        "id": "sizing_vessel_reactor",
        "title": "Vessel and Reactor Sizing — Volume, L/D Ratio, and Residence Time",
        "text": (
            "REACTOR/VESSEL SIZING from residence time and throughput: "
            "CSTR volume: V = F_0 × X × tau = Q × C_A0 × X / (-r_A)  [m³] "
            "  Where tau = residence time [h], Q = volumetric flow [m³/h], X = conversion. "
            "  Typical tau: chemical reactions 0.5-4 h; fermenters 12-72 h; crystallizers 1-8 h. "
            "PFR volume: V = F_A0 × ∫(dX / -r_A) — integrate rate equation from 0 to X. "
            "  For first-order reaction: V/Q = -ln(1-X) / k. "
            "  k at operating T from Arrhenius: k = k_0 × exp(-Ea/RT). "
            "L/D RATIO (Length to Diameter): "
            "  Horizontal vessels (liquid-liquid, flash drums): L/D = 3-5. "
            "  Vertical vessels (vapor-liquid separators): L/D = 1-2 (wider for disengagement). "
            "  Tubular reactors (PFR): L/D = 20-100 (long and narrow for plug flow). "
            "  CSTRs: L/D ≈ 1 (height ≈ diameter for good mixing). "
            "FLASH DRUM SIZING: "
            "  Vapor velocity: u_V = K × sqrt((rho_L - rho_V) / rho_V) "
            "  K = 0.04-0.06 m/s (Souders-Brown for flash drum). "
            "  Liquid residence time: 5-10 min in liquid sump (for control). "
            "  Diameter from vapor velocity, height from liquid residence time. "
            "MATERIAL OF CONSTRUCTION: "
            "  Carbon steel: T < 400°C, no H2S/HCl, pH 6-10. "
            "  304/316 SS: corrosive chemicals, T < 600°C. "
            "  Hastelloy C: HCl, Cl₂, very corrosive; T < 650°C. "
            "In DWSIM: vessel sizing is not automated — calculate V, D, H manually, "
            "  then specify in equipment cost as a custom vessel."
        ),
        "tags": ["vessel sizing", "CSTR sizing", "PFR sizing", "residence time", "L/D ratio",
                 "flash drum", "reactor volume", "Souders-Brown", "material of construction",
                 "Arrhenius", "conversion", "mixing"],
        "source": "Fogler (2016) Elements of CRE; Seider et al. Ch. 12; Perry's 9th ed. Sec. 6",
    },

    # ── Pipe hydraulics & pressure drop ────────────────────────
    {
        "id": "pipe_hydraulics_pressure_drop",
        "title": "Pipe Hydraulics: Darcy-Weisbach, Friction Factors, Two-Phase Flow",
        "text": (
            "Pipe pressure drop is calculated by the Darcy-Weisbach equation: "
            "ΔP = f·(L/D)·(ρv²/2), where f is the Darcy friction factor, L the pipe "
            "length (m), D the inside diameter (m), ρ the fluid density (kg/m³), "
            "v the velocity (m/s). Note: Fanning friction factor f_F = f/4. Always "
            "confirm which convention DWSIM is using.\n\n"
            "Friction factor selection by Reynolds number Re = ρvD/μ:\n"
            "  Laminar (Re < 2100): f = 64/Re (independent of roughness)\n"
            "  Transitional (2100 < Re < 4000): unstable, avoid in design\n"
            "  Turbulent (Re > 4000): Colebrook equation\n"
            "    1/√f = -2·log10[(ε/D)/3.7 + 2.51/(Re·√f)]\n"
            "  ε = pipe roughness: commercial steel 0.046 mm, drawn tubing 0.0015 mm,\n"
            "  cast iron 0.26 mm, riveted steel 0.9-9 mm.\n\n"
            "Swamee-Jain explicit approximation (no iteration, good to ±1%):\n"
            "  f = 0.25 / [log10((ε/D)/3.7 + 5.74/Re^0.9)]²\n\n"
            "Sizing rules of thumb (use for first-pass design):\n"
            "  Liquid (water-like)         : 1-3 m/s  (lower for slurries: 1-2 m/s)\n"
            "  Gas at atmospheric P        : 10-30 m/s\n"
            "  Gas at high P (>10 bar)     : 5-15 m/s (lower v at higher ρ)\n"
            "  Steam (low pressure)         : 30-60 m/s\n"
            "  Steam (high pressure)        : 30-50 m/s\n"
            "  Two-phase (vapor-liquid)    : avoid >15 m/s mixture velocity\n\n"
            "TWO-PHASE FLOW (Lockhart-Martinelli method):\n"
            "  X² = (ΔP/L)_liq / (ΔP/L)_vap\n"
            "  Two-phase multiplier φ_L² = 1 + C/X + 1/X² (turbulent-turbulent: C=20)\n"
            "  ΔP_TP = φ_L² · ΔP_liq\n"
            "  Flow regimes: bubble, plug, slug, annular, mist (Baker chart).\n"
            "  Rule: keep mixture mass flux G < 2000-3000 kg/m²/s in process pipes.\n\n"
            "DWSIM Pipe unit op specifies: length, diameter, roughness, elevation, "
            "and outputs ΔP. After solving, verify outlet P > 0 (SF-06) and that "
            "the velocity falls within the rule-of-thumb range for the fluid type."
        ),
        "tags": ["pipe", "hydraulics", "pressure drop", "Darcy-Weisbach", "Fanning",
                 "friction factor", "Colebrook", "Reynolds", "Moody", "roughness",
                 "two-phase flow", "Lockhart-Martinelli", "pipe sizing", "velocity",
                 "flow regime", "pipe diameter", "head loss"],
        "source": "Perry's 9th ed., Sec. 6 (Fluid Dynamics); Crane TP-410; "
                  "Coulson & Richardson Vol. 1, Ch. 3",
    },

    # ── Distillation: convergence algorithm selection ─────────────
    {
        "id": "distill_algorithm_selection",
        "title": "Distillation Column Convergence: Inside-Out, Burningham-Otto, Sum-Rates",
        "text": (
            "DWSIM provides 3 main column solvers; choose based on system characteristics. "
            "If the default algorithm fails to converge, try a different one BEFORE adjusting "
            "specs.\n\n"
            "1. INSIDE-OUT (IO) — DWSIM default. Decouples thermodynamics from MESH.\n"
            "   Best for: ideal/near-ideal mixtures, narrow-boiling separations,\n"
            "     hydrocarbon distillation, refinery columns, demethanizers.\n"
            "   Avoid for: highly non-ideal mixtures, wide-boiling absorbers, reactive\n"
            "     distillation. Convergence: O(N²) per outer loop, fast (5-15 iterations).\n\n"
            "2. MODIFIED BURNINGHAM-OTTO (BO) — Simultaneous correction.\n"
            "   Best for: wide-boiling-range absorbers/strippers, cryogenic columns,\n"
            "     multicomponent absorbers with light-key recovery > 95%.\n"
            "   Drawback: slower (10-25 iterations). More robust than IO when liquid\n"
            "     and vapor profiles differ by orders of magnitude across the column.\n\n"
            "3. SUM-RATES (SR) — Newton-based simultaneous solver.\n"
            "   Best for: highly non-ideal mixtures (azeotropic, three-phase),\n"
            "     reactive distillation, columns with side draws and multiple feeds.\n"
            "   Drawback: requires good initialization; can diverge if profile is far\n"
            "     from feasibility. Use after IO or BO has produced a partial profile.\n\n"
            "DECISION TREE — when IO fails to converge:\n"
            "  Symptom 'Bubble point not found on stage X' →\n"
            "    Stage X temperature is far outside Antoine range; switch to BO.\n"
            "  Symptom 'Mass balance not satisfied' →\n"
            "    Likely a side-draw or non-ideal κ; switch to SR.\n"
            "  Symptom 'Maximum iterations reached, residual oscillating' →\n"
            "    Reflux ratio too close to minimum (R_min); increase R by 20% and retry.\n"
            "  Symptom 'Negative liquid flow on stage' →\n"
            "    Feed stage too low or condenser duty insufficient; check feed enthalpy\n"
            "    and review thermal condition q.\n\n"
            "INITIALIZATION — strongly affects convergence:\n"
            "  • Start with reflux ratio R = 1.3 × R_min (Underwood) and N = 1.5 × N_min "
            "    (Fenske-Gilliland).\n"
            "  • Provide bubble-point temperature profile (top: dew point of distillate; "
            "    bottom: bubble point of bottoms; linear interpolation otherwise).\n"
            "  • For azeotropes (e.g. ethanol-water), use NRTL with VLE BIPs validated against\n"
            "    experimental data (DECHEMA consistency test).\n"
            "  • Cold-start trick: run with relaxed tolerance (1e-3), then tighten to 1e-6.\n\n"
            "MESH equation tolerances in DWSIM:\n"
            "  Default: 1e-5 for material balance, 1e-4 for energy balance.\n"
            "  For pre-design exploration: relax to 1e-3 (faster, less robust).\n"
            "  For final validation: tighten to 1e-7 (slower, more accurate)."
        ),
        "tags": ["distillation convergence", "Inside-Out", "IO algorithm",
                 "Burningham-Otto", "Sum-Rates", "MESH equations",
                 "column not converging", "rigorous distillation",
                 "absorber convergence", "azeotropic distillation",
                 "reactive distillation", "wide-boiling", "tolerance",
                 "DWSIM column solver", "convergence troubleshooting"],
        "source": "Seader, Henley & Roper (2011) Separation Process Principles, "
                  "Ch. 10-11; Holland (1981) Fundamentals of Multicomponent Distillation; "
                  "DWSIM v9 documentation Sec. 4.3",
    },

    # ── Electrolyte thermodynamics: MEA / amine gas treating ──────
    {
        "id": "electrolyte_mea_acid_gas",
        "title": "Electrolyte Thermodynamics: MEA / Amine Acid Gas Treating",
        "text": (
            "Aqueous amine solutions (MEA, DEA, MDEA, piperazine) absorb acid gases "
            "(CO₂, H₂S) via reversible chemical reaction — NOT by Henry's-law physical "
            "absorption alone. Standard PR/SRK/NRTL property packages are INCORRECT for "
            "these systems and will under-predict CO₂ loading by 50-200%.\n\n"
            "CORRECT MODELS FOR ELECTROLYTE SYSTEMS:\n"
            "  • Electrolyte-NRTL (e-NRTL, Chen-Britt 1982) — DWSIM via custom prop package\n"
            "    or Reaktoro plugin. Handles long-range Pitzer-Debye-Hückel ion-ion forces\n"
            "    plus short-range NRTL local composition.\n"
            "  • Pitzer ion-interaction model — for high-ionic-strength brines.\n"
            "  • Kent-Eisenberg — simplified equilibrium model for amine + CO₂ + H₂S,\n"
            "    accurate for screening at low loading (<0.4 mol/mol).\n"
            "  • Deshmukh-Mather — extension of Kent-Eisenberg with activity coefficients,\n"
            "    accurate to ~0.5 mol/mol loading.\n\n"
            "KEY REACTIONS (MEA + CO₂):\n"
            "  Carbamate formation:  2 RNH₂ + CO₂  ⇌  RNHCOO⁻ + RNH₃⁺   (fast, 1°/2° amines)\n"
            "  Bicarbonate (slow):   RNH₂ + CO₂ + H₂O ⇌ RNH₃⁺ + HCO₃⁻\n"
            "  Stoichiometric limit: 0.5 mol CO₂ / mol MEA (for primary amines, 1° carbamate)\n"
            "  MDEA (3°) cannot form carbamate: 1.0 mol CO₂ / mol MDEA (bicarbonate route).\n\n"
            "TYPICAL OPERATING WINDOW (30 wt% MEA, post-combustion CO₂ capture):\n"
            "  Absorber: T = 40-60 °C, P = 1-2 bar, lean loading 0.20-0.25 mol/mol,\n"
            "            rich loading 0.45-0.50 mol/mol\n"
            "  Stripper: T = 110-125 °C (steam reboiler), P = 1.5-2.0 bar\n"
            "  Reboiler duty: 3.5-4.2 GJ/ton CO₂ (energy penalty for regeneration)\n"
            "  CO₂ removal: typically 85-90% (industrial standard)\n"
            "  Solvent circulation: L/G mass ratio 3-7 in absorber\n\n"
            "DWSIM IMPLEMENTATION OPTIONS:\n"
            "  (a) Use DWSIM's built-in Sour Gas (Amines) Property Package if available\n"
            "      (PR + Kent-Eisenberg). Acceptable for screening.\n"
            "  (b) Use Reaktoro plugin (in DWSIM Plugins menu) for full e-NRTL.\n"
            "  (c) For preliminary design only: ChemSep or Aspen if available.\n"
            "  (d) AVOID: NRTL with default BIPs — will give wrong CO₂ loading and\n"
            "      undersize absorber/stripper by 30-50%.\n\n"
            "WARNING TO USER: When user requests MEA/amine + CO₂/H₂S simulation in DWSIM,\n"
            "ALWAYS verify property package selection. If only PR/NRTL/SRK are available,\n"
            "warn that absolute loading and reboiler duty will be approximate. Recommend\n"
            "validation against published data (e.g. Jou et al. 1994; Aronu et al. 2011)."
        ),
        "tags": ["electrolyte", "MEA", "amine", "DEA", "MDEA", "CO2 absorption",
                 "carbon capture", "CCS", "acid gas", "H2S removal", "sour gas",
                 "e-NRTL", "Kent-Eisenberg", "Pitzer", "carbamate", "loading",
                 "reboiler duty", "regeneration energy", "absorber stripper",
                 "Deshmukh-Mather", "Reaktoro"],
        "source": "Kohl & Nielsen (1997) Gas Purification 5th ed., Ch. 2; "
                  "Chen & Britt (1982) AIChE J.; Jou, Mather & Otto (1994) Can. J. Chem. Eng.; "
                  "DWSIM Plugins documentation (Reaktoro)",
    },

]


# ═══════════════════════════════════════════════════════════
# TF-IDF RETRIEVAL ENGINE (zero external dependencies)
# ═══════════════════════════════════════════════════════════

class KnowledgeBase:
    """
    Chemical engineering knowledge base with hybrid search.

    Search strategy (best available wins):
      1. Semantic search  — sentence-transformers + cosine similarity
         (install: pip install sentence-transformers)
         Handles paraphrases, synonyms, and cross-language variants.
      2. BM25 fallback     — pure Python, no extra deps.
         Works well for domain-specific vocabulary queries.
    """

    def __init__(self):
        self.chunks = KNOWLEDGE_CHUNKS
        self._idf: Dict[str, float] = {}
        self._doc_tfs: List[Counter] = []
        self._doc_lens: List[int] = []
        self._avg_dl: float = 0.0
        self._build_index()

        # ── Optional: semantic search via sentence-transformers ───────────────
        # Not enabled by default — TF-IDF is sufficient for this project's
        # domain-specific vocabulary and the LLM compensates for any misses.
        # To enable: pip install sentence-transformers  (pulls in PyTorch ~500MB)
        self._smodel   = None
        self._semb     = None
        self._use_sem  = False
        self._sem_lock = __import__('threading').Lock()

    # ── Embedding cache ───────────────────────────────────────────────────────
    # Embeddings are expensive to compute (~50 s for 109 chunks on first run).
    # We save them to a .npy file keyed by a hash of the chunk contents so the
    # cache invalidates automatically whenever KNOWLEDGE_CHUNKS changes.
    _CACHE_DIR  = os.path.join(os.path.dirname(__file__), "__pycache__")
    _MODEL_NAME = "all-MiniLM-L6-v2"

    def _chunks_hash(self) -> str:
        """Stable hash of all chunk ids + text. Cache key."""
        import hashlib
        raw = "".join(c["id"] + c["text"][:100] for c in self.chunks)
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _cache_path(self) -> str:
        return os.path.join(
            self._CACHE_DIR,
            f"kb_embeddings_{self._chunks_hash()}.npy",
        )

    def _try_load_semantic(self) -> None:
        """
        Load sentence-transformers if available; silently fall back to TF-IDF.
        Embeddings are cached to disk so subsequent server starts take ~1-2 s
        instead of ~50 s.
        """
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            import numpy as np                                       # type: ignore

            cache = self._cache_path()
            os.makedirs(self._CACHE_DIR, exist_ok=True)

            self._smodel = SentenceTransformer(self._MODEL_NAME)

            if os.path.exists(cache):
                # Fast path: load pre-computed normalised embeddings (~0.1 s)
                self._semb = np.load(cache)
            else:
                # Slow path: encode all chunks (~10-50 s on first run), then cache
                corpus = [c["title"] + ". " + c["text"][:400] for c in self.chunks]
                raw = self._smodel.encode(corpus, convert_to_numpy=True,
                                          show_progress_bar=False)
                norms = np.linalg.norm(raw, axis=1, keepdims=True)
                self._semb = raw / (norms + 1e-9)
                # np.save appends .npy if missing — use a tmp name that already
                # ends in .npy so the rename works correctly on all platforms.
                tmp = cache[:-4] + "_tmp.npy"   # e.g. kb_embeddings_abc_tmp.npy
                np.save(tmp, self._semb)
                os.replace(tmp, cache)

            with self._sem_lock:
                self._use_sem = True
        except ImportError:
            pass   # sentence-transformers not installed → TF-IDF fallback
        except Exception:
            with self._sem_lock:
                self._use_sem = False   # any error → TF-IDF fallback

    def _semantic_scores(self, query: str) -> List[float]:
        """Return cosine similarity scores for each chunk (highest = most relevant)."""
        import numpy as np  # already confirmed available if _use_sem is True
        q_emb = self._smodel.encode([query], convert_to_numpy=True,
                                     show_progress_bar=False)[0]
        q_emb = q_emb / (np.linalg.norm(q_emb) + 1e-9)
        return (self._semb @ q_emb).tolist()

    # ── Query result cache (Chip Huyen Ch.10: reduce latency via exact caching) ──
    # BM25 is deterministic — same query always returns same result.
    # Cache up to 256 recent queries in an LRU dict (thread-safe via lock).
    _CACHE_MAX = 256
    _query_cache: Dict[str, dict] = {}
    _cache_lock  = __import__('threading').Lock()

    @classmethod
    def _cache_get(cls, key: str):
        with cls._cache_lock:
            return cls._query_cache.get(key)

    @classmethod
    def _cache_set(cls, key: str, value: dict) -> None:
        with cls._cache_lock:
            if len(cls._query_cache) >= cls._CACHE_MAX:
                oldest = next(iter(cls._query_cache))
                del cls._query_cache[oldest]
            cls._query_cache[key] = value

    def _semantic_cache_lookup(self, query: str, top_k: int) -> Optional[dict]:
        """
        Semantic cache (Chip Huyen Ch.10: 'semantic caching — similar queries return
        cached results'). Uses BM25 token overlap between the new query and all cached
        query keys — no embedding model required.

        A cached result is reused if token overlap (Jaccard similarity) > 0.8,
        meaning the queries share >80% of their tokens. This correctly matches:
          - "NRTL BIP ethanol water" == "NRTL binary interaction ethanol water" (NO — different tokens)
          - "Peng Robinson CO2 methane kij" == "PR kij CO2 CH4" (NO — different tokens)
          - "NRTL binary parameters ethanol water" == "NRTL binary interaction parameters ethanol water" (YES)
        Exact-cache misses fall through to BM25 computation.
        """
        query_tokens = set(self._tokenize(query.strip().lower()))
        if len(query_tokens) < 3:
            return None  # too short for reliable similarity

        JACCARD_THRESHOLD = 0.80

        with self._cache_lock:
            for cached_key, cached_result in self._query_cache.items():
                # cached_key format: "query_text|top_k"
                parts = cached_key.rsplit("|", 1)
                if len(parts) != 2:
                    continue
                cached_query, cached_topk = parts[0], parts[1]
                if int(cached_topk) != top_k:
                    continue
                cached_tokens = set(self._tokenize(cached_query))
                if not cached_tokens:
                    continue
                # Jaccard similarity = |intersection| / |union|
                intersection = len(query_tokens & cached_tokens)
                union        = len(query_tokens | cached_tokens)
                jaccard = intersection / union if union > 0 else 0.0
                if jaccard >= JACCARD_THRESHOLD:
                    # Return cached result with semantic cache flag
                    return {**cached_result, "_cached": True,
                            "_semantic_cache": True,
                            "_jaccard_similarity": round(jaccard, 3),
                            "_matched_query": cached_query}
        return None

    def search(self, query: str, top_k: int = 5) -> dict:
        """
        Search the knowledge base. Returns matching chunks ranked by relevance.

        Retrieval strategy (Chip Huyen AI Engineering Ch. 6):
          1. BM25 sparse retrieval — best for exact domain-specific terms
             (NRTL, Rachford-Rice, kij) where semantic search adds noise.
          2. Retrieve 2× top_k candidates, then filter by MIN_SCORE threshold
             (re-ranking step: score quality gate replaces cut-off by rank alone).
          3. Results cached by (query, top_k) for identical repeat queries — zero
             latency on proactive RAG injection when same query fires multiple turns.
        Falls back to semantic search if sentence-transformers is installed.
        """
        if not query or not query.strip():
            return {"success": True, "query": query,
                    "result_count": 0, "results": [],
                    "retrieval_method": "none"}

        top_k = min(int(top_k), len(self.chunks))

        # ── Exact cache lookup ────────────────────────────────────────────────
        cache_key = f"{query.strip().lower()}|{top_k}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return {**cached, "_cached": True}

        # ── Semantic cache lookup (Jaccard token-overlap) ─────────────────────
        # Book Ch.10: "semantic caching — if similar query in cache, return it."
        # No embedding needed: BM25 tokenizer gives good semantic overlap proxy
        # for chemical engineering vocabulary (domain-specific exact terms).
        sem_cached = self._semantic_cache_lookup(query.strip().lower(), top_k)
        if sem_cached is not None:
            return sem_cached

        with self._sem_lock:
            use_sem = self._use_sem

        # ── Retrieve 2× candidates for better score-based re-ranking ─────────
        # Book insight: retrieve more than needed, then apply quality threshold.
        # This avoids cutting off a highly relevant chunk ranked at position top_k+1
        # just because a slightly less relevant chunk ranked higher.
        retrieve_n = min(top_k * 2, len(self.chunks))

        if use_sem:
            raw_scores = self._semantic_scores(query)
            indexed = sorted(enumerate(raw_scores), key=lambda x: -x[1])
            method = "semantic"
        else:
            raw_scores = self._bm25_scores(query)
            indexed = sorted(enumerate(raw_scores), key=lambda x: -x[1])
            method = "bm25"

        # ── Score threshold (quality gate instead of pure rank cut-off) ───────
        # BM25: min_score 0.5 — anything below contributes almost no signal.
        # Semantic: min_score 0.05 — cosine can be negative (anti-relevant).
        MIN_SCORE = 0.5 if method == "bm25" else 0.05
        results = []
        for idx, score in indexed[:retrieve_n]:
            if score <= MIN_SCORE:
                continue
            if len(results) >= top_k:
                break
            chunk = self.chunks[idx].copy()
            chunk["relevance_score"] = round(float(score), 4)
            results.append(chunk)

        result = {
            "success":          True,
            "query":            query,
            "result_count":     len(results),
            "results":          results,
            "retrieval_method": method,
        }

        # Cache the result for future identical queries
        self._cache_set(cache_key, result)
        return result

    def _bm25_scores(self, query: str) -> List[float]:
        """BM25 scoring — scores are non-negative, comparable across queries."""
        K1 = 1.5   # term saturation parameter
        B  = 0.75  # length normalization parameter
        query_tokens = self._tokenize(query)
        scores = []
        for i, doc_tf in enumerate(self._doc_tfs):
            dl   = self._doc_lens[i]
            norm = 1 - B + B * (dl / (self._avg_dl or 1))
            score = 0.0
            for qt in query_tokens:
                tf  = doc_tf.get(qt, 0)
                idf = self._idf.get(qt, 0.0)
                score += idf * (tf * (K1 + 1)) / (tf + K1 * norm)
            # Tag boost: exact tag match adds 1.0 (but won't exceed ~5 for 5 query terms)
            for qt in query_tokens:
                if qt in [t.lower() for t in self.chunks[i].get("tags", [])]:
                    score += 1.0
            scores.append(score)
        return scores

    def _tfidf_scores(self, query: str) -> List[float]:  # legacy — superseded by _bm25_scores
        """TF-IDF cosine-like scoring for each chunk."""
        query_tokens = self._tokenize(query)
        scores = []
        for i, doc_tf in enumerate(self._doc_tfs):
            doc_len = sum(doc_tf.values()) or 1
            score = 0.0
            for qt in query_tokens:
                score += (doc_tf.get(qt, 0) / doc_len) * self._idf.get(qt, 0)
            # Boost for exact tag matches
            for qt in query_tokens:
                if qt in [t.lower() for t in self.chunks[i].get("tags", [])]:
                    score += 0.5
            scores.append(score)
        return scores

    def list_topics(self) -> dict:
        """List all available knowledge topics."""
        topics = [{"id": c["id"], "title": c["title"]}
                  for c in self.chunks]
        return {"success": True, "topics": topics, "count": len(topics)}

    # ── Index Building ─────────────────────────────────────

    def _build_index(self):
        N = len(self.chunks)
        df: Counter = Counter()
        self._doc_tfs = []

        for chunk in self.chunks:
            text = (chunk["title"] + " " + chunk["text"] + " " +
                    " ".join(chunk.get("tags", [])))
            tokens = self._tokenize(text)
            tf = Counter(tokens)
            self._doc_tfs.append(tf)
            for t in set(tokens):
                df[t] += 1

        self._doc_lens = [sum(tf.values()) for tf in self._doc_tfs]
        self._avg_dl   = sum(self._doc_lens) / (len(self._doc_lens) or 1)

        self._idf = {}
        for term, count in df.items():
            idf_val = math.log((N - count + 0.5) / (count + 0.5) + 1)
            self._idf[term] = max(idf_val, 0.0)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Simple tokenization with lowering and stopword removal."""
        tokens = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_-]{1,}\b', text.lower())
        STOPS = {
            "the", "is", "in", "at", "of", "and", "or", "to", "a", "an",
            "for", "with", "on", "by", "this", "that", "from", "are", "be",
            "as", "was", "were", "it", "its", "can", "has", "have", "had",
            "will", "would", "should", "may", "which", "when", "where",
            "than", "each", "all", "more", "most", "use", "used", "using",
        }
        return [t for t in tokens if t not in STOPS]
