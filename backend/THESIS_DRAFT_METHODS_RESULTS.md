# Methods and Results (draft)

> Draft chapter synthesised from the committed validation artifacts
> (`RESULTS.md`, `OPTIMIZATION_VALIDATION.md`, `LIVE_ASPEN_VALIDATION.md`,
> `benchmark_results.json`, `ASPEN_CAPABILITY_MATRIX.md`,
> `THESIS_RESULTS_AND_LIMITATIONS.md`). Every figure is reproducible from the
> repository. Written to be defensible: the boundary between *engineered
> capability*, *method validated against a known answer*, and *demonstrated on
> the live DWSIM engine* is stated explicitly throughout.

## 1. Methods

### 1.1 System architecture

The system is an agentic AI for process-flowsheet design and optimization in
DWSIM, organised in three layers:

1. **Agentic reasoning layer** (`agent_v2.py`): a multi-step, tool-using LLM
   agent over a 105-tool action space. It plans, calls a tool, observes the
   result, and iterates (up to 20 iterations per turn). Supporting subsystems:
   a provider-agnostic LLM client with an automatic failover chain
   (`llm_client.py`), a provider-neutral conversation history with on-the-fly
   normalisation, proactive retrieval-augmented generation, persistent session
   memory, and a case-based experience store.
2. **DWSIM integration layer** (`dwsim_bridge_v2.py`): a `pythonnet` bridge to
   the DWSIM .NET engine, with a topology builder
   (`build_flowsheet_atomic`), recycle auto-tearing (graph cycle detection),
   energy-stream injection, and read-back-after-write verification of every
   property set.
3. **Optimization and analysis layer**: a natural-language optimization
   orchestrator with admissibility gates, over a stack of solvers (CMA-ES,
   DE/PSO/GA, NSGA-II, NLopt, Bayesian/EGO, equation-oriented surrogate NLP,
   SALib global sensitivity), each degrading gracefully to a SciPy baseline.

### 1.2 The agent loop

Each user turn runs a reason→act→observe loop. A persistent "state card" of the
active flowsheet is injected so the model never reconstructs state from
compressed history. Dynamic tool selection filters the 105-tool catalogue to a
phase-relevant subset (~20–70 tools) to control prompt size and improve
tool-selection accuracy. Output is guarded by a synchronous quality heuristic
(numerical claims without a backing tool call, ignored convergence errors,
load-mismatch confabulation) and scored asynchronously by an LLM-as-judge.

### 1.3 Optimization methodology and the Aspen-parity contributions

On top of the standard solver stack, four contributions specifically target
capabilities a commercial tool (Aspen Plus) has over a black-box wrapper around
a sequential-modular simulator. Each is engine-agnostic (it operates on an
`evaluate`/`one_pass` callback), which makes it validatable on problems with
known answers independent of DWSIM and the LLM.

- **Trust-region surrogate equation-oriented optimization**
  (`run_eo_trust_region`). DWSIM does not expose its equation system, so true
  open-equation optimization is impossible; the surrogate approach is standard
  (DOE → algebraic model → NLP → validate). This work upgrades it from a
  one-shot/global-quadratic fit to a **derivative-free trust-region** scheme:
  local quadratic models inside a trust region of radius Δ, minimised within the
  region, with the trust-region acceptance ratio ρ = (actual reduction)/
  (predicted reduction) deciding step acceptance and whether Δ grows or shrinks.
  Shrinking on rejection guarantees eventual progress — a provably-convergent
  model-management scheme (Conn, Scheinberg & Vicente, *Introduction to
  Derivative-Free Optimization*, SIAM 2009).

- **Infeasible-path SQP** (`run_infeasible_path_optimization`). The central
  Aspen optimizer technique (Biegler). Rather than fully converging each recycle
  loop before every objective evaluation (feasible path), the recycle
  tear-stream variables are promoted to decision variables and the loop-closure
  equations become equality constraints, so an SQP solver converges the recycle
  and the objective **simultaneously** — one flowsheet pass per evaluation, no
  inner convergence loop.

- **Multi-process parallel evaluator** (`parallel_evaluator.py`). DWSIM hosts one
  CLR per process. N worker processes, each with its own CLR and a copy of the
  flowsheet (loaded once via a pool initialiser), evaluate a batch of designs
  concurrently — the batch primitive for population optimisers and sweeps.

- **Total-Annualized-Cost objective** (`tac_objective.py`). The canonical Aspen
  economic-optimization target. TAC = CRF·CAPEX + annual OPEX, with
  size-dependent Turton power-law installed capital (C = a·Sᵇ·F_BM) traded
  against utility OPEX. The size dependence is what creates the convex trade-off
  an optimizer exploits ("minimise the TAC of this unit").

### 1.4 Evaluation methodology

Three complementary levels of evidence are used, in increasing realism and
decreasing coverage:

1. **Component tests** — an automated suite (mock bridge/agent, no DWSIM
   required) over the agent loop, optimization stack, construction-robustness
   passes, failover, and evaluation harness.
2. **Method validation against known answers** — each optimizer/contribution run
   on analytic problems with closed-form optima (sphere, Rosenbrock, constrained
   QP, Ishigami, an analytic reactor-with-recycle), so correctness is exact and
   reproducible without DWSIM or an LLM.
3. **Live-engine demonstration** — selected contributions exercised against a
   live DWSIM v9.0.5 build, and a fixed 25-task benchmark (defined a priori)
   run agent-end-to-end with success criteria, physical-plausibility checks, and
   an LLM-as-judge.

## 2. Results

### 2.1 Component-level correctness

`pytest -q` from `backend/`: **492 passed, 5 skipped** (497 collected; the
skipped tests require a live DWSIM and skip automatically). This establishes
component correctness — tool selection/sequencing, optimizer convergence on
analytic objectives, the recycle/energy passes, failover, and history
normalisation — but not, by itself, end-to-end task success.

### 2.2 Optimizer-algorithm validation (analytic, exact)

| Modality | Result |
|---|---|
| Single-objective global search (5 standard functions, min = 0) | **5/5 functions solved** by at least one shipped solver (10/15 method×function cases within 0.1); local methods miss multimodal functions as expected, global methods cover them |
| Multi-objective (NSGA-II, non-convex front) | 40-point front, full spread, **max deviation 2.1×10⁻⁵** from the analytic front |
| Global sensitivity (Sobol on Ishigami) | recovered indices match the textbook analytical values to **max total-order error 0.0011** |

### 2.3 Aspen-parity contributions

| Contribution | Validated against known answer | Live-DWSIM status |
|---|---|---|
| **Trust-region EO** | Sphere → exact 0; constrained QP → exact KKT point; ~50× reduction on Rosenbrock (its curved valley defeats any quadratic surrogate) | **Live ✅** — minimised real heater duty to the 40 °C optimum (67.58 kW) in 9 evals |
| **Infeasible-path SQP** | analytic reactor-with-recycle: **exact same optimum** as feasible-path, recycle closed to residual 0.0, **18 passes vs 230 = 12.8× fewer** | Optimizer mechanics confirmed live (closure → 0 in 2 passes); a fully-coupled live recycle is still needed (the test flowsheet's open-loop form did not couple — documented) |
| **Parallel evaluator** | correctness: parallel == serial, order preserved | Live ✅ for correctness; **speed-up is workload-dependent** — 1.9× on a mock, but a live 8-design batch was ~9× *slower* because per-worker CLR init (~30 s) dominates. Pays off only with a persistent pool over many generations |
| **TAC objective** | CRF and TAC arithmetic match hand calculation; the optimizer finds the **exact interior cost optimum** on a convex CAPEX↕OPEX trade-off | **Live ✅** — computed from the real solved duty (293.9 kW → $267,533/yr) |

### 2.4 Live 25-task benchmark

Run crash-isolated (one subprocess per task) with Claude Sonnet as the agent LLM
against a live DWSIM engine. Each agent request is ~22k tokens, so the LLM tier
rate-limits part-way through the suite and **no single run completes all 25
tasks** (whichever run first gets done). Two runs with complementary orderings
(easy-first, advanced-first) were combined per task by scoring each task from the
run where the agent actually executed it (tools > 0) — selecting a real attempt
over a rate-limited no-op, not a higher score.

**Combined result: 24 % strict (6/25)** — 6 SUCCESS, 2 PARTIAL; by complexity
**28.6 % / 36.4 % / 0 %**. A complexity-3/advanced task (C8-T01) is among the
successes. **19 of 25 tasks were actually executed** across the two runs; **6
never ran in either** (persistent rate-limiting on the most advanced) and are
*inconclusive*, not failures. Over the **19 executed tasks: 32 % strict / 42 %
with partial credit**. A residual scoring rigidity remains (some converged,
correct builds score null when the output stream is named outside the role-alias
set). The agent demonstrably builds and solves real DWSIM flowsheets (the
water-heater and pump tasks pass cleanly against live physics).

## 3. Discussion and Limitations

The defensible claim is that the system is **designed, implemented,
component-validated, and demonstrated end-to-end on a live DWSIM engine**, with
**Aspen-level optimization methodology** (the four contributions are at
commercial level and validated against known optima; three are also shown live).
It is **not** "Aspen-level simulation": two gaps are *fundamental*, not closable
by code, because the system is built on DWSIM rather than replacing it —
(a) **model/thermo fidelity** (DWSIM is not validated against decades of
industrial data the way Aspen is), and (b) **true native equation-oriented
optimization** (DWSIM does not expose its equations; the EO here is surrogate).

Honest threats to validity:
- The 24 % benchmark (32 % over executed tasks) is quota- and scoring-limited, not a clean capability
  number; a higher-throughput LLM tier and further criteria-matching are needed
  for a complete headline figure.
- The parallel speed-up is workload-dependent (CLR-init bound), corrected from an
  earlier mock-based 1.9× claim.
- The infeasible-path live coupling is not yet achieved (optimizer mechanics
  confirmed; a properly-coupling live recycle flowsheet is the remaining work).
- LLM stochasticity and provider variation: numbers should state the
  provider/model and average over repeated runs; LLM-as-judge shares blind
  spots with the agent and is used only as a secondary signal alongside the
  deterministic physics-based criteria.

## 4. Path to completion

A higher-throughput LLM tier closes the remaining evidentiary gaps without new
code: (1) run the full 25-task benchmark (9 tasks currently unrun) for a clean
pass-rate with mean ± spread over repeated runs; (2) one head-to-head case study
(e.g. a Luyben-style column TAC optimization or the Williams-Otto reactor)
solved live and compared to the published optimum — the strongest single piece
of evidence; (3) a coupling-correct live recycle for the infeasible-path
end-to-end demonstration.
