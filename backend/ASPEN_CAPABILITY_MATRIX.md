# Capability Matrix vs Aspen Plus

Operationalises the claim "Aspen-level process-flowsheet optimization" feature by
feature, rather than asserting it abstractly. Three columns: the Aspen Plus
capability, this system's equivalent (with the module that implements it), and
an **honest caveat**. Validation status is stated explicitly — "validated"
means against a known answer (analytic optimum / hand calculation), reproducibly;
"live-pending" means the method is validated but its DWSIM coupling has not yet
been exercised on a real flowsheet (gated on LLM/engine throughput).

| Aspen Plus capability | This system's equivalent | Honest caveat |
|---|---|---|
| **Design Spec** (vary a variable to meet a target) | Constraint handling via `constraint_solver.py`; `optimize_constrained` (NLopt SLSQP) with equality targets | Equivalent in function; not exposed as a one-click "Design Spec" block in a GUI |
| **Sensitivity analysis** | `parametric_study` / `parametric_study_2d`; **global** sensitivity via SALib Sobol/Morris (`global_sensitivity`) | Global sensitivity (Sobol) is arguably *beyond* Aspen's local sensitivity blocks; validated on Ishigami |
| **Optimization (feasible-path SQP)** | `optimize_constrained` (NLopt SLSQP/ISRES), `complex_optimizer` multi-solver cascade, CMA-ES/DE/PSO/GA | Comparable; broader modern/global algorithm set than Aspen's built-in optimizer |
| **Infeasible-path optimization** (simultaneous tear + optimize) | `run_infeasible_path_optimizer` — tear variables as decision vars, closure as equality constraints (Biegler) | **Validated** (exact optimum, 12.8× fewer passes); DWSIM coupling (`OT_Recycle` single-pass) **live-pending** |
| **Equation-oriented (EO) mode** | Surrogate EO: global-quadratic (`run_eo_optimization`) and **trust-region** (`run_eo_trust_region`, provably convergent) | **Surrogate**, not native open-equation — DWSIM doesn't expose its equations. This is the one fundamental EO gap |
| **Multi-objective optimization** | NSGA-II Pareto fronts (`multiobjective_nsga`, pymoo) | Aspen has no native multi-objective; **project ahead**. Validated (front to 2e-5) |
| **APEA / economic evaluation** | `economics.py` (Turton bare-module CAPEX, tiered-utility OPEX, NPV/payback) + **TAC objective** (`tac_objective`) | "Minimise TAC" is a single optimization target; validated to the exact cost optimum. Costs are correlation-based, not vendor-quote |
| **Aspen Energy Analyzer** (pinch / heat integration) | Pinch analysis (`/flowsheet/pinch`, industrial features) | Targeting present; not as fully featured as a dedicated HEN-synthesis tool |
| **Parallel / large-scale throughput** | Multi-process worker pool (`parallel_evaluator`, `bridge.parallel_evaluate_designs`) — N CLRs evaluate a batch concurrently | Correctness validated (parallel == serial). Speed-up is workload-dependent: per-worker CLR init (~30 s) means a live small batch is *slower*; benefit needs a persistent pool over many generations |
| **Property methods / data regression** | DWSIM property packages; compound DB (`property_db`), BIP setting | Bounded by DWSIM's property data coverage — narrower than Aspen's databanks |
| **Natural-language / autonomous workflow** | The agent: NL goal → spec → gates → solve → verify → report, with hollow-objective detection and adaptive replanning | Aspen has **none** of this; the project's distinguishing contribution |
| **Model/thermo fidelity** | DWSIM engine | **The ceiling.** DWSIM's thermo is not validated against decades of industrial data the way Aspen's is — fundamental, not closable by code |

## Defensible reading

- **Where the system matches or exceeds Aspen:** modern/global + multi-objective
  optimization breadth, global sensitivity, the infeasible-path and trust-region
  contributions, and — uniquely — natural-language autonomous operation.
- **Where Aspen remains decisively ahead, fundamentally:** model/thermo fidelity
  and true native equation-oriented optimization. Both stem from building *on*
  DWSIM rather than replacing it; no amount of optimizer engineering closes them.
- **Therefore the honest thesis claim is** *"Aspen-level optimization
  methodology over an open simulator"* — the algorithms, the infeasible-path and
  EO schemes, and the economic objective are at commercial level and validated
  against known optima — **not** *"Aspen-level simulation,"* which the fidelity
  ceiling forbids.

## Outstanding to fully substantiate

1. Live-validate the infeasible-path, trust-region EO, parallel, and TAC
   contributions on real DWSIM flowsheets (currently validated on analytic
   problems with known answers).
2. Complete the 25-task live benchmark (9 tasks currently unrun due to LLM
   rate-limiting) for a clean headline pass-rate.
3. One head-to-head case study (e.g. a Luyben column TAC optimization or the
   Williams-Otto reactor) solved live and compared to the published/Aspen
   optimum — the strongest single piece of evidence for the claim.
