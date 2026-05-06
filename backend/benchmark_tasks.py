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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
