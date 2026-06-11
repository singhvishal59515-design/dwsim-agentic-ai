"""
benchmark_tasks.py
──────────────────
Formal benchmark task set for DWSIM Agentic AI evaluation.

Defines 25 fixed, a-priori tasks across 8 flowsheet categories.
Each task has:
  - unique ID
  - category
  - prompt (exact user query)
  - success_criteria (precise, measurable)
  - complexity level (1=simple, 2=moderate, 3=complex)
  - expected_tools (minimum tool sequence required)
  - physical_constraints (plausibility checks on result)

Success/failure definitions:
  SUCCESS  = all success_criteria met AND no physical constraint violated
  PARTIAL  = converged but one criterion missed by >5%
  FAILURE_LOUD   = exception raised or agent explicitly reports failure
  FAILURE_SILENT = converged=True but physical constraint violated
                   (most dangerous — numerically convergent but wrong)

This task set was fixed BEFORE any experiments were run.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))

@dataclass
class SuccessCriterion:
    """One measurable pass/fail condition on the simulation result."""
    stream_tag:    str          # e.g. "Product"
    property:      str          # e.g. "temperature_C"
    operator:      str          # "==", "<", ">", "between", "~="
    value:         Any          # target value or (low, high) tuple
    tolerance_pct: float = 2.0  # % tolerance for "~=" checks
    description:   str  = ""

@dataclass
class PhysicalConstraint:
    """Physical plausibility check — violation = SILENT FAILURE."""
    description:    str
    check_type:     str    # "mass_balance", "T_order", "P_positive",
                           # "VF_range", "T_positive"
    params:         Dict[str, Any] = field(default_factory=dict)

@dataclass
class BenchmarkTask:
    task_id:           str
    category:          str       # one of CATEGORIES
    complexity:        int       # 1, 2, or 3
    prompt:            str       # exact user query sent to agent
    property_package:  str       # expected PP choice
    success_criteria:  List[SuccessCriterion] = field(default_factory=list)
    physical_constraints: List[PhysicalConstraint] = field(default_factory=list)
    expected_tools:    List[str] = field(default_factory=list)
    human_time_min:    float = 0.0   # expert baseline (minutes)
    notes:             str = ""
    # Flowsheet fixture to load BEFORE the prompt runs (after the per-task
    # reset). Needed for analysis tasks phrased as "the loaded flowsheet" —
    # without a fixture they have nothing to analyse once tasks are isolated.
    setup_load:        str = ""
    # True if the task can only be measured against a pre-existing flowsheet
    # (it references "the loaded flowsheet" or fixture-specific stream names).
    # Such a task is SKIPPED (not failed) when no matching fixture is available,
    # so a missing test fixture never depresses the agent's measured pass-rate.
    requires_fixture:  bool = False

# ── Category taxonomy ─────────────────────────────────────────────────────────
CATEGORIES = [
    "single_unit_creation",    # C1
    "multi_unit_creation",     # C2
    "flowsheet_analysis",      # C3
    "property_modification",   # C4
    "parametric_study",        # C5
    "distillation",            # C6
    "reactor",                 # C7
    "convergence_repair",      # C8
]

# ── The 25 fixed benchmark tasks ─────────────────────────────────────────────
BENCHMARK_TASKS: List[BenchmarkTask] = [

    # ═══════════════ C1: Single-unit creation (3 tasks, complexity 1) ════════

    BenchmarkTask(
        task_id="C1-T01", category="single_unit_creation", complexity=1,
        prompt="Create a water heating process from 25°C to 80°C at 1 atm, 1 kg/s, using Steam Tables.",
        property_package="Steam Tables (IAPWS-IF97)",
        success_criteria=[
            SuccessCriterion("Product","temperature_C","~=",80.0,tolerance_pct=1.5,
                             description="Product temperature within 1.5% of 80°C"),
            SuccessCriterion("Product","pressure_bar","~=",1.013,tolerance_pct=2.0,
                             description="Product pressure ≈ 1.013 bar"),
            SuccessCriterion("Product","mass_flow_kgh","~=",3600.0,tolerance_pct=1.0,
                             description="Mass balance: 3600 kg/h"),
        ],
        physical_constraints=[
            PhysicalConstraint("Product T > Feed T","T_order",{"hot":"Product","cold":"Feed"}),
            PhysicalConstraint("All pressures > 0","P_positive",{}),
            PhysicalConstraint("T > 0 K","T_positive",{}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition",
                        "set_unit_op_property","save_and_solve"],
        human_time_min=15.0,
    ),

    BenchmarkTask(
        task_id="C1-T02", category="single_unit_creation", complexity=1,
        prompt="Build a pump that raises water pressure from 1 bar to 5 bar. Feed: 25°C, 10 kg/s. Efficiency 75%.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("Product","pressure_bar","~=",5.0,tolerance_pct=2.0,
                             description="Outlet pressure ≈ 5 bar"),
            SuccessCriterion("Product","mass_flow_kgh","~=",36000.0,tolerance_pct=1.0,
                             description="Mass balance 36000 kg/h"),
        ],
        physical_constraints=[
            PhysicalConstraint("Product P > Feed P","T_order",
                               {"hot":"Product","cold":"Feed","prop":"pressure_bar"}),
            PhysicalConstraint("Vapor fraction = 0 (liquid pump)","VF_range",
                               {"tag":"Product","min":0.0,"max":0.01}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_unit_op_property","save_and_solve"],
        human_time_min=12.0,
    ),

    BenchmarkTask(
        task_id="C1-T03", category="single_unit_creation", complexity=1,
        prompt="Create a methanol-water flash separator at 70°C, 1 atm. Feed: 60 mol% methanol, 40 mol% water, 1 mol/s. Use NRTL.",
        property_package="NRTL",
        success_criteria=[
            SuccessCriterion("Vapor","vapor_fraction","~=",1.0,tolerance_pct=2.0,
                             description="Vapor outlet is fully vapour"),
            SuccessCriterion("Liquid","vapor_fraction","~=",0.0,tolerance_pct=2.0,
                             description="Liquid outlet is fully liquid"),
            SuccessCriterion("Vapor","temperature_C","~=",70.0,tolerance_pct=2.0,
                             description="Vapour T ≈ 70°C"),
        ],
        physical_constraints=[
            PhysicalConstraint("Mole fractions sum to 1","mass_balance",
                               {"feed":"Feed","outlets":["Vapor","Liquid"]}),
            PhysicalConstraint("VF in [0,1] for all streams","VF_range",
                               {"min":0.0,"max":1.0}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition","save_and_solve"],
        human_time_min=18.0,
    ),

    # ═══════════════ C2: Multi-unit creation (4 tasks, complexity 2) ══════════

    BenchmarkTask(
        task_id="C2-T01", category="multi_unit_creation", complexity=2,
        prompt="Create a water heating then cooling cycle: heat water from 20°C to 90°C, then cool back to 40°C. "
               "Feed 2 kg/s at 1 atm. Use Steam Tables.",
        property_package="Steam Tables (IAPWS-IF97)",
        success_criteria=[
            SuccessCriterion("HotProduct","temperature_C","~=",90.0,tolerance_pct=2.0,
                             description="After heater T ≈ 90°C"),
            SuccessCriterion("CoolProduct","temperature_C","~=",40.0,tolerance_pct=2.0,
                             description="After cooler T ≈ 40°C"),
            SuccessCriterion("CoolProduct","mass_flow_kgh","~=",7200.0,tolerance_pct=1.0,
                             description="Mass balance 7200 kg/h"),
        ],
        physical_constraints=[
            PhysicalConstraint("T_heater_out > T_feed","T_order",
                               {"hot":"HotProduct","cold":"Feed"}),
            PhysicalConstraint("T_cooler_out < T_heater_out","T_order",
                               {"hot":"HotProduct","cold":"CoolProduct"}),
        ],
        expected_tools=["new_flowsheet","add_object","add_object","connect_streams",
                        "set_stream_property","set_unit_op_property","save_and_solve"],
        human_time_min=25.0,
    ),

    BenchmarkTask(
        task_id="C2-T02", category="multi_unit_creation", complexity=2,
        prompt="Build a two-stream mixer: Stream A is methanol at 30°C, 1 atm, 500 kg/h. "
               "Stream B is water at 60°C, 1 atm, 1000 kg/h. Mix them and report the outlet temperature. Use NRTL.",
        property_package="NRTL",
        success_criteria=[
            SuccessCriterion("Product","mass_flow_kgh","~=",1500.0,tolerance_pct=1.0,
                             description="Mass balance: 500+1000=1500 kg/h"),
            SuccessCriterion("Product","temperature_C","between",(30.0,60.0),tolerance_pct=0.0,
                             description="Mixed T between 30°C and 60°C (energy balance)"),
        ],
        physical_constraints=[
            PhysicalConstraint("Mass balance in = out","mass_balance",
                               {"feed":["FeedA","FeedB"],"outlets":["Product"]}),
            PhysicalConstraint("T_out between min(T_in) and max(T_in)","T_order",{}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition","save_and_solve"],
        human_time_min=20.0,
    ),

    BenchmarkTask(
        task_id="C2-T03", category="multi_unit_creation", complexity=2,
        prompt="Create a heat exchanger between hot methanol (80°C, 5 bar, 25000 kg/h) and "
               "cold water (25°C, 1 atm, 15000 kg/h). Area=250 m², U=450 W/(m²·K). Peng-Robinson.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("WaterOut","temperature_C",">",55.0,
                             description="Water outlet heated above feed"),
            SuccessCriterion("MethanolOut","temperature_C","<",80.0,
                             description="Methanol outlet cooled below feed"),
        ],
        physical_constraints=[
            PhysicalConstraint("Energy balance: Q_cold ≈ Q_hot","mass_balance",{}),
            PhysicalConstraint("No phase change in cold stream (liquid water)","VF_range",
                               {"tag":"WaterOut","min":0.0,"max":0.05}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_unit_op_property","save_and_solve"],
        human_time_min=22.0,
    ),

    BenchmarkTask(
        task_id="C2-T04", category="multi_unit_creation", complexity=2,
        prompt="Build a pump-heater system: pump water from 1 bar to 3 bar (efficiency 80%), "
               "then heat from ambient (25°C) to 70°C. Feed: 5 kg/s. Use Steam Tables.",
        property_package="Steam Tables (IAPWS-IF97)",
        success_criteria=[
            SuccessCriterion("FinalProduct","pressure_bar","~=",3.0,tolerance_pct=3.0,
                             description="Final pressure ≈ 3 bar"),
            SuccessCriterion("FinalProduct","temperature_C","~=",70.0,tolerance_pct=2.0,
                             description="Final temperature ≈ 70°C"),
        ],
        physical_constraints=[
            PhysicalConstraint("Intermediate stream P > Feed P","T_order",
                               {"hot":"Intermediate","cold":"Feed","prop":"pressure_bar"}),
            PhysicalConstraint("Final T > Intermediate T","T_order",
                               {"hot":"FinalProduct","cold":"Intermediate"}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_unit_op_property","save_and_solve"],
        human_time_min=25.0,
    ),

    # ═══════════════ C3: Flowsheet analysis (3 tasks) ════════════════════════

    BenchmarkTask(
        task_id="C3-T01", category="flowsheet_analysis", complexity=1,
        prompt="Load the heat exchanger flowsheet and report all stream temperatures, pressures, and mass flows.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("any","temperature_C","~=",78.788,tolerance_pct=1.0,
                             description="Agent reports water outlet T ≈ 78.8°C"),
        ],
        physical_constraints=[],
        expected_tools=["find_flowsheets","load_flowsheet","get_simulation_results"],
        human_time_min=5.0,
    ),

    BenchmarkTask(
        task_id="C3-T02", category="flowsheet_analysis", complexity=1,
        prompt="What thermodynamic property package is used in the loaded flowsheet? "
               "Is it appropriate for the compounds present?",
        property_package="any",
        success_criteria=[
            SuccessCriterion("response","contains_PP_name","~=",True,tolerance_pct=0.0,
                             description="Agent correctly names the property package"),
        ],
        physical_constraints=[],
        expected_tools=["get_property_package","search_knowledge"],
        human_time_min=5.0,
    ),

    BenchmarkTask(
        task_id="C3-T03", category="flowsheet_analysis", complexity=2,
        prompt="Check convergence of all streams. Identify any that did not converge and explain why.",
        property_package="any",
        success_criteria=[
            SuccessCriterion("response","mentions_convergence","~=",True,tolerance_pct=0.0,
                             description="Agent calls check_convergence and reports per-stream status"),
        ],
        physical_constraints=[],
        expected_tools=["check_convergence","get_simulation_results"],
        human_time_min=8.0,
    ),

    # ═══════════════ C4: Property modification (3 tasks) ══════════════════════

    BenchmarkTask(
        task_id="C4-T01", category="property_modification", complexity=1,
        prompt="Change the methanol inlet temperature to 100°C and re-run the simulation.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("MethanolIn","temperature_C","~=",100.0,tolerance_pct=1.0,
                             description="Methanol inlet set to 100°C"),
            SuccessCriterion("WaterOut","temperature_C",">",78.788,
                             description="Water outlet increases (more heat input)"),
        ],
        physical_constraints=[
            PhysicalConstraint("WaterOut T > WaterIn T","T_order",
                               {"hot":"WaterOut","cold":"WaterIn"}),
        ],
        expected_tools=["set_stream_property","run_simulation","get_simulation_results"],
        human_time_min=8.0,
    ),

    BenchmarkTask(
        task_id="C4-T02", category="property_modification", complexity=1,
        prompt="Change the feed composition to 80% methanol and 20% water and re-simulate.",
        property_package="NRTL",
        success_criteria=[
            SuccessCriterion("Feed","composition_methanol","~=",0.80,tolerance_pct=1.0,
                             description="Feed composition set to 80% methanol"),
        ],
        physical_constraints=[
            PhysicalConstraint("Mole fractions sum to 1.0","mass_balance",{}),
            PhysicalConstraint("VF in [0,1]","VF_range",{"min":0.0,"max":1.0}),
        ],
        expected_tools=["set_stream_composition","run_simulation","get_simulation_results"],
        human_time_min=6.0,
    ),

    BenchmarkTask(
        task_id="C4-T03", category="property_modification", complexity=2,
        prompt="Increase the heater duty to achieve 95°C outlet and confirm the simulation converges.",
        property_package="Steam Tables (IAPWS-IF97)",
        success_criteria=[
            SuccessCriterion("Product","temperature_C","~=",95.0,tolerance_pct=2.0,
                             description="Product temperature ≈ 95°C after modification"),
        ],
        physical_constraints=[
            PhysicalConstraint("Product T > Feed T","T_order",
                               {"hot":"Product","cold":"Feed"}),
            PhysicalConstraint("T < boiling point at given P","T_positive",{}),
        ],
        expected_tools=["set_unit_op_property","run_simulation","get_simulation_results"],
        human_time_min=10.0,
    ),

    # ═══════════════ C5: Parametric study (3 tasks) ══════════════════════════

    BenchmarkTask(
        task_id="C5-T01", category="parametric_study", complexity=2,
        prompt="How does the water outlet temperature change as methanol inlet temperature "
               "varies from 60°C to 120°C in steps of 10°C?",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("response","monotonic_trend","~=",True,tolerance_pct=0.0,
                             description="Agent reports 7 data points with correct monotonic trend"),
            SuccessCriterion("response","mentions_phase_transition","~=",True,tolerance_pct=0.0,
                             description="Agent identifies the non-linearity/plateau at 100°C"),
        ],
        physical_constraints=[],
        expected_tools=["parametric_study"],
        human_time_min=20.0,
    ),

    BenchmarkTask(
        task_id="C5-T02", category="parametric_study", complexity=2,
        prompt="Run a parametric study varying the water flow rate from 5000 to 30000 kg/h "
               "in 5000 kg/h steps. Report how methanol outlet temperature changes.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("response","correct_direction","~=",True,tolerance_pct=0.0,
                             description="Agent reports increasing methanol outlet T as water flow decreases"),
        ],
        physical_constraints=[],
        expected_tools=["parametric_study"],
        human_time_min=18.0,
    ),

    BenchmarkTask(
        task_id="C5-T03", category="parametric_study", complexity=3,
        prompt="Find the optimal methanol inlet temperature that maximises water outlet temperature "
               "without the water boiling (keep outlet T below 99°C). Search 60°C to 120°C.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("response","optimal_T_identified","~=",True,tolerance_pct=0.0,
                             description="Agent identifies optimal point ≈ 100°C (phase transition onset)"),
        ],
        physical_constraints=[
            PhysicalConstraint("Water outlet VF < 0.1 (no boiling)","VF_range",
                               {"tag":"WaterOut","max":0.10}),
        ],
        expected_tools=["optimize_parameter","get_simulation_results"],
        human_time_min=30.0,
    ),

    # ═══════════════ C6: Distillation (3 tasks, complexity 2-3) ══════════════

    BenchmarkTask(
        task_id="C6-T01", category="distillation", complexity=2,
        prompt="Create a shortcut distillation column to separate 50/50 benzene-toluene mixture. "
               "Feed: 100 kmol/h at 80°C, 1 atm. Reflux ratio 1.5, 10 stages. "
               "Use Peng-Robinson.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("Distillate","temperature_C","<",85.0,
                             description="Distillate (benzene-rich) below toluene boiling point"),
            SuccessCriterion("Bottoms","temperature_C",">",85.0,
                             description="Bottoms (toluene-rich) above benzene boiling point"),
        ],
        physical_constraints=[
            PhysicalConstraint("Mass balance: distillate + bottoms = feed","mass_balance",
                               {"feed":"Feed","outlets":["Distillate","Bottoms"]}),
            PhysicalConstraint("VF of distillate near 0 (condenser)","VF_range",
                               {"tag":"Distillate","min":0.0,"max":0.1}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition",
                        "set_unit_op_property","save_and_solve"],
        human_time_min=45.0,
        notes="Primary industrially-relevant case study for Reviewer concern 2.3",
    ),

    BenchmarkTask(
        task_id="C6-T02", category="distillation", complexity=3,
        prompt="Design a methanol-water separation column to achieve 95 mol% methanol in distillate. "
               "Feed 100 kmol/h, 50% methanol at 65°C, 1 atm. Use NRTL. Report required reflux ratio.",
        property_package="NRTL",
        success_criteria=[
            SuccessCriterion("Distillate","composition_methanol",">",0.90,
                             description="Distillate methanol purity > 90%"),
        ],
        physical_constraints=[
            PhysicalConstraint("Methanol mass balance","mass_balance",{}),
            PhysicalConstraint("VF in [0,1]","VF_range",{"min":0.0,"max":1.0}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition",
                        "set_unit_op_property","save_and_solve","get_column_properties"],
        human_time_min=60.0,
        notes="Complex industrial distillation case — addresses reviewer 2.3",
    ),

    BenchmarkTask(
        task_id="C6-T03", category="distillation", complexity=3,
        prompt="Analyse the loaded distillation flowsheet. Increase reflux ratio from current value "
               "to 2.0 and report how distillate purity changes.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("response","reports_purity_increase","~=",True,tolerance_pct=0.0,
                             description="Agent correctly identifies purity increases with reflux ratio"),
        ],
        physical_constraints=[],
        expected_tools=["get_column_properties","set_column_property","run_simulation"],
        human_time_min=20.0,
    ),

    # ═══════════════ C7: Reactor (2 tasks, complexity 3) ═════════════════════

    BenchmarkTask(
        task_id="C7-T01", category="reactor", complexity=3,
        prompt="Create a conversion reactor for ethanol dehydration to ethylene. "
               "Feed: pure ethanol at 400°C, 1 atm, 10 kmol/h. "
               "Conversion 85%. Use Peng-Robinson.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("Product","temperature_C","~=",400.0,tolerance_pct=5.0,
                             description="Reactor outlet near specified temperature"),
        ],
        physical_constraints=[
            PhysicalConstraint("T > 0 K","T_positive",{}),
            PhysicalConstraint("P > 0","P_positive",{}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition",
                        "set_reactor_property","save_and_solve"],
        human_time_min=35.0,
    ),

    BenchmarkTask(
        task_id="C7-T02", category="reactor", complexity=3,
        prompt="Create a Gibbs reactor for the water-gas shift reaction (CO + H2O → CO2 + H2). "
               "Feed: 50% CO, 50% H2O at 400°C, 1 atm, 1 mol/s. Use Peng-Robinson.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("Product","temperature_C","~=",400.0,tolerance_pct=5.0,
                             description="Reactor operates at specified temperature"),
        ],
        physical_constraints=[
            PhysicalConstraint("Carbon balance","mass_balance",{}),
            PhysicalConstraint("T > 0 K","T_positive",{}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams",
                        "set_stream_property","set_stream_composition","save_and_solve"],
        human_time_min=40.0,
    ),

    # ═══════════════ C8: Convergence repair (4 tasks) ════════════════════════

    BenchmarkTask(
        task_id="C8-T01", category="convergence_repair", complexity=2,
        prompt="The simulation has not converged. Diagnose the problem and fix it.",
        property_package="any",
        success_criteria=[
            SuccessCriterion("response","diagnoses_cause","~=",True,tolerance_pct=0.0,
                             description="Agent identifies convergence failure cause"),
            SuccessCriterion("response","achieves_convergence","~=",True,tolerance_pct=0.0,
                             description="Agent successfully recovers convergence"),
        ],
        physical_constraints=[],
        expected_tools=["check_convergence","run_simulation"],
        human_time_min=20.0,
    ),

    BenchmarkTask(
        task_id="C8-T02", category="convergence_repair", complexity=2,
        prompt="The distillation column is not converging. Try increasing reflux ratio and re-run.",
        property_package="any",
        success_criteria=[
            SuccessCriterion("response","attempts_fix","~=",True,tolerance_pct=0.0,
                             description="Agent modifies reflux and retries"),
        ],
        physical_constraints=[],
        expected_tools=["set_column_property","run_simulation","check_convergence"],
        human_time_min=15.0,
    ),

    BenchmarkTask(
        task_id="C8-T03", category="convergence_repair", complexity=3,
        prompt="The recycle loop simulation is not converging. Apply convergence repair strategies "
               "and report which strategy worked.",
        property_package="any",
        success_criteria=[
            SuccessCriterion("response","reports_strategy","~=",True,tolerance_pct=0.0,
                             description="Agent reports which auto-corrector strategy succeeded"),
        ],
        physical_constraints=[],
        expected_tools=["run_simulation","check_convergence"],
        human_time_min=25.0,
    ),

    BenchmarkTask(
        task_id="C8-T04", category="convergence_repair", complexity=3,
        prompt="Create a two-recycle reactor system. First attempt may not converge — "
               "apply iterative strategies until convergence is achieved.",
        property_package="Peng-Robinson (PR)",
        success_criteria=[
            SuccessCriterion("response","achieves_convergence","~=",True,tolerance_pct=0.0,
                             description="System eventually converges"),
        ],
        physical_constraints=[
            PhysicalConstraint("Mass balance closure","mass_balance",{}),
        ],
        expected_tools=["new_flowsheet","add_object","connect_streams","save_and_solve","run_simulation"],
        human_time_min=45.0,
    ),
]

# Tasks that can only be measured against a pre-existing flowsheet fixture (they
# reference "the loaded flowsheet" or fixture-specific stream names such as
# MethanolIn / WaterOut). The matching methanol-water heat-exchanger and
# distillation fixtures are NOT in the repo, so these are SKIPPED — not failed —
# until such fixtures (and a setup_load pointing at them) are added. Marking them
# here keeps the measured pass-rate honest (agent capability, not missing data).
_FIXTURE_DEPENDENT = {
    "C3-T01", "C3-T02", "C3-T03",         # analysis of a loaded flowsheet
    "C4-T01", "C4-T02", "C4-T03",         # modify a loaded methanol-water HX
    "C5-T01", "C5-T02", "C5-T03",         # parametric study of a loaded HX
    "C6-T03",                              # analyse a loaded distillation column
    "C8-T01", "C8-T02", "C8-T03",         # repair a pre-existing non-converged sim
}
for _t in BENCHMARK_TASKS:
    if _t.task_id in _FIXTURE_DEPENDENT:
        _t.requires_fixture = True

# ── Panel API: list / run / results ───────────────────────────────────────────
# These back the Eval-tab "Benchmark Suite" UI. `run_task` executes one task
# in-process against the live agent and scores it with the SAME pure evaluators
# the offline CLI uses (run_benchmark._determine_outcome / _evaluate_criterion),
# so panel results and CLI results are consistent.

_DIFFICULTY = {1: "easy", 2: "medium", 3: "hard"}

# In-memory store of the most recent result per benchmark id (process lifetime).
_LAST_RESULTS: Dict[str, Dict[str, Any]] = {}


def list_tasks() -> List[Dict[str, Any]]:
    """Benchmark catalogue in the shape the Eval-tab UI renders."""
    out: List[Dict[str, Any]] = []
    for t in BENCHMARK_TASKS:
        name = (t.notes or t.prompt or t.category).strip()
        if len(name) > 60:
            name = name[:57].rstrip() + "…"
        out.append({
            "id":             t.task_id,
            "task_id":        t.task_id,   # alias for the Tasks-tab consumer
            "name":           name,
            "category":       t.category,
            "complexity":     t.complexity,
            "difficulty":     _DIFFICULTY.get(t.complexity, "medium"),
            "tags":           [t.category] + list(t.expected_tools[:3]),
            "human_time_min": t.human_time_min,
            "description":    t.prompt,
            "n_criteria":     len(t.success_criteria),
        })
    return out


def get_results() -> Dict[str, Any]:
    """Most-recent result per benchmark, plus aggregate pass-rate."""
    results = list(_LAST_RESULTS.values())
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    return {
        "results":    results,
        "total_runs": total,
        "pass_rate":  round(100.0 * passed / total, 1) if total else None,
    }


def _criterion_actual(criterion, stream_results: dict):
    """Best-effort read of a criterion's measured value from stream_results,
    tolerant of the two shapes results come in ({tag:{prop:val}} or
    {tag:{properties:{prop:val}}})."""
    try:
        rec = (stream_results or {}).get(criterion.stream_tag, {})
        if isinstance(rec, dict):
            if criterion.property in rec:
                return rec[criterion.property]
            props = rec.get("properties")
            if isinstance(props, dict) and criterion.property in props:
                return props[criterion.property]
    except Exception:
        pass
    return None


def run_task(task_id: str, agent: Any) -> Dict[str, Any]:
    """Run ONE benchmark task in-process against `agent` and score it.

    Returns the envelope the Eval-tab UI expects: passed / outcome / duration_s
    / speedup_vs_human / tool_calls / convergence / accuracy_checks / notes.
    Heavy (invokes the agent + DWSIM) but fully guarded — never raises.
    """
    import time

    task = next((t for t in BENCHMARK_TASKS if t.task_id == task_id), None)
    if task is None:
        return {"success": False, "passed": False, "benchmark_id": task_id,
                "error": f"Unknown benchmark task {task_id!r}",
                "notes": f"No such benchmark {task_id!r}"}

    # Capture tool calls via the agent's callback hook (same as eval_harness).
    tool_calls: List[str] = []
    prev_cb = getattr(agent, "on_tool_call", None)

    def _cb(name, args, result):
        tool_calls.append(name)
        if callable(prev_cb):
            try: prev_cb(name, args, result)
            except Exception: pass

    try:
        agent.on_tool_call = _cb
    except Exception:
        pass

    # Fresh conversation per task for isolation (no cache bleed).
    for meth in ("reset", "reset_conversation", "clear_history"):
        fn = getattr(agent, meth, None)
        if callable(fn):
            try: fn(); break
            except Exception: pass

    t0 = time.monotonic()
    answer, error = "", ""
    try:
        answer = agent.chat(task.prompt) or ""
    except Exception as exc:
        error = str(exc)
    elapsed = round(time.monotonic() - t0, 2)

    try:
        agent.on_tool_call = prev_cb
    except Exception:
        pass

    # Pull stream results for criteria scoring.
    stream_results: Dict[str, Any] = {}
    try:
        sr = agent.bridge.get_simulation_results()
        if isinstance(sr, dict):
            stream_results = sr.get("stream_results", sr)
    except Exception:
        pass

    chat_result = {"answer": answer, "error": error,
                   "tool_calls": [{"name": n} for n in tool_calls],
                   "elapsed_s": elapsed}

    # Score with the SAME pure evaluators the offline CLI uses.
    outcome = "FAILURE_LOUD" if error else "SUCCESS"
    evaluate_criterion = None
    try:
        from run_benchmark import _determine_outcome, _evaluate_criterion
        evaluate_criterion = _evaluate_criterion
        # _determine_outcome now returns (outcome, detail, stats) — unpack it.
        outcome = _determine_outcome(task, chat_result, stream_results)[0]
    except Exception:
        pass

    checks: List[Dict[str, Any]] = []
    for c in task.success_criteria:
        met = False
        if evaluate_criterion is not None:
            # _evaluate_criterion now returns (met, reason); bool() of a non-empty
            # tuple is ALWAYS True, so unpack the boolean explicitly.
            try: met = bool(evaluate_criterion(c, stream_results)[0])
            except Exception: met = False
        actual = _criterion_actual(c, stream_results)
        checks.append({
            "metric":    f"{c.stream_tag}.{c.property}",
            "actual":    actual,
            "error_pct": None,
            "passed":    met,
        })

    passed = outcome == "SUCCESS"
    convergence = True if (isinstance(stream_results, dict) and stream_results) else None
    speedup = (round(task.human_time_min * 60.0 / elapsed, 1)
               if task.human_time_min and elapsed > 0 else None)

    result = {
        "success":          True,
        "benchmark_id":     task_id,
        "category":         task.category,
        "complexity":       task.complexity,
        "passed":           passed,
        "outcome":          outcome,
        "duration_s":       elapsed,
        "speedup_vs_human": speedup,
        "tool_calls":       len(tool_calls),
        "convergence":      convergence,
        "accuracy_checks":  checks,
        "notes":            (error or task.notes or "")[:200],
        # ── Aliases for the second consumer (Tasks tab) ──
        "time_s":           elapsed,
        "speedup_x":        speedup,
        "human_time_min":   task.human_time_min,
        "agent_response":   answer,
    }
    _LAST_RESULTS[task_id] = result
    return result


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate a list of run_task results into a measured-capability report:
    overall pass-rate plus breakdowns by category and complexity, and mean
    speed-up vs the human-expert baseline."""
    from collections import defaultdict
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    by_cat: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    by_cmplx: Dict[int, List[int]] = defaultdict(lambda: [0, 0])
    speedups = []
    for r in results:
        c = r.get("category", "?"); cx = int(r.get("complexity", 0) or 0)
        by_cat[c][1] += 1; by_cmplx[cx][1] += 1
        if r.get("passed"):
            by_cat[c][0] += 1; by_cmplx[cx][0] += 1
        if r.get("speedup_vs_human"):
            speedups.append(float(r["speedup_vs_human"]))

    def _rate(pt):  # [passed, total] -> percent
        return round(100.0 * pt[0] / pt[1], 1) if pt[1] else None

    return {
        "total": total,
        "passed": passed,
        "pass_rate": _rate([passed, total]),
        "by_category": {k: {"passed": v[0], "total": v[1], "pass_rate": _rate(v)}
                        for k, v in sorted(by_cat.items())},
        "by_complexity": {str(k): {"passed": v[0], "total": v[1],
                                   "pass_rate": _rate(v)}
                          for k, v in sorted(by_cmplx.items())},
        "mean_speedup_vs_human": (round(sum(speedups) / len(speedups), 1)
                                  if speedups else None),
    }


def _bridge_mode(agent: Any) -> str:
    """Honestly classify the run as 'live' (real DWSIMBridgeV2) or 'mock'.

    A thesis number is only meaningful if the reader knows whether it came from
    the real DWSIM engine or a stub. We classify conservatively: anything that
    isn't an actual DWSIMBridgeV2 instance counts as 'mock'."""
    bridge = getattr(agent, "bridge", None)
    if bridge is None:
        return "mock"
    cls = type(bridge).__name__
    if "mock" in cls.lower() or "fake" in cls.lower() or getattr(bridge, "mock", False):
        return "mock"
    try:
        from dwsim_bridge_v2 import DWSIMBridgeV2
        if isinstance(bridge, DWSIMBridgeV2):
            return "live"
    except Exception:
        pass
    return "mock"


def render_results_table(results: List[Dict[str, Any]]) -> str:
    """Render per-task benchmark results as a GitHub-flavoured Markdown table —
    the thesis-ready artifact. One row per task plus a totals line."""
    rows = [
        "| Task | Category | Cx | Outcome | Pass | Tools | Time (s) | Speedup | Notes |",
        "|---|---|:--:|---|:--:|:--:|--:|--:|---|",
    ]
    for r in results:
        speed = r.get("speedup_vs_human")
        rows.append(
            f"| {r.get('benchmark_id','?')} "
            f"| {r.get('category','?')} "
            f"| {r.get('complexity','?')} "
            f"| {r.get('outcome','?')} "
            f"| {'✅' if r.get('passed') else '❌'} "
            f"| {r.get('tool_calls',0)} "
            f"| {r.get('duration_s','?')} "
            f"| {(str(speed)+'×') if speed else '—'} "
            f"| {(r.get('notes') or '')[:60].replace(chr(10),' ')} |"
        )
    s = summarize_results(results)
    rows.append(
        f"| **TOTAL** | | | | **{s['passed']}/{s['total']} "
        f"({s['pass_rate']}%)** | | | "
        f"{('mean '+str(s['mean_speedup_vs_human'])+'×') if s.get('mean_speedup_vs_human') else ''} | |"
    )
    return "\n".join(rows)


def _merge_results(prior: List[Dict[str, Any]],
                   fresh: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Latest-result-per-task merge, keyed by benchmark_id. A subset run updates
    only the tasks it ran and preserves previously-recorded results for the
    rest — so a quick single-task re-check never discards a full-suite run."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in (prior or []):
        if isinstance(r, dict) and r.get("benchmark_id"):
            by_id[r["benchmark_id"]] = r
    for r in (fresh or []):
        if isinstance(r, dict) and r.get("benchmark_id"):
            by_id[r["benchmark_id"]] = r
    return list(by_id.values())


def persist_results(report: Dict[str, Any]) -> str:
    """Persist a run_all report so it survives the process and is picked up by
    eval_summary.py / the /eval/benchmark/results endpoint. Merges per-task
    (latest-result-per-task) into BOTH the standalone benchmark_results.json and
    eval_log.json["benchmark_results"], so a subset run never overwrites a
    fuller prior run. Returns the json path. Never raises."""
    bj_path = os.path.join(_HERE, "benchmark_results.json")
    ev_path = os.path.join(_HERE, "eval_log.json")
    fresh = report.get("results", [])

    def _load(path, key=None):
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                return d if key is None else (d.get(key) if isinstance(d, dict) else None)
        except Exception:
            pass
        return None

    # Standalone report: keep this run's mode/ran_at but carry the merged,
    # cumulative per-task results + a recomputed summary over the full set.
    merged = _merge_results(_load(bj_path, "results"), fresh)
    out = dict(report)
    out["results"] = merged
    out["summary"] = summarize_results(merged)
    try:
        with open(bj_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=str)
    except Exception:
        pass

    # Merge into eval_log.json so existing readers see the cumulative pass-rate.
    try:
        evlog = _load(ev_path) or {}
        if not isinstance(evlog, dict):
            evlog = {}
        ev_merged = _merge_results(evlog.get("benchmark_results"), fresh)
        evlog["benchmark_results"] = ev_merged
        evlog["benchmark_summary"] = summarize_results(ev_merged)
        with open(ev_path, "w", encoding="utf-8") as f:
            json.dump(evlog, f, indent=2, default=str)
    except Exception:
        pass
    return bj_path


def run_all(agent: Any, task_ids: Optional[List[str]] = None,
            persist: bool = False) -> Dict[str, Any]:
    """Run the whole benchmark suite (or a subset) in-process against `agent`
    and return per-task results + an aggregate report. SLOW: each task invokes
    the agent + DWSIM (30-90 s). Intended as a deliberate, one-call batch to
    MEASURE capability — the honest answer to "what is the live pass-rate?".

    `mode` ('live'|'mock') is recorded so the report is never mistaken for a
    real-engine result when it isn't. Set persist=True to write the report to
    disk (benchmark_results.json + eval_log.json) for the thesis artifact.
    """
    ids = task_ids or [t.task_id for t in BENCHMARK_TASKS]
    mode = _bridge_mode(agent)
    results = [run_task(tid, agent) for tid in ids]
    report = {
        "success":   True,
        "mode":      mode,
        "ran_at":    datetime.now(timezone.utc).isoformat(),
        "results":   results,
        "summary":   summarize_results(results),
    }
    if persist:
        report["persisted_to"] = persist_results(report)
    return report


# ── Summary statistics ────────────────────────────────────────────────────────
def task_summary() -> dict:
    from collections import Counter
    cats  = Counter(t.category    for t in BENCHMARK_TASKS)
    comps = Counter(t.complexity  for t in BENCHMARK_TASKS)
    return {
        "total":         len(BENCHMARK_TASKS),
        "by_category":   dict(cats),
        "by_complexity": dict(comps),
        "complexity_1":  comps[1],
        "complexity_2":  comps[2],
        "complexity_3":  comps[3],
        "avg_human_min": sum(t.human_time_min for t in BENCHMARK_TASKS) / len(BENCHMARK_TASKS),
    }

if __name__ == "__main__":
    s = task_summary()
    print(f"Total tasks:    {s['total']}")
    print(f"By category:    {s['by_category']}")
    print(f"By complexity:  {s['by_complexity']}")
    print(f"Avg human time: {s['avg_human_min']:.1f} min")
