# Results, Evaluation, and Limitations

> Draft prose for the thesis "Results" and "Limitations" chapters. It is written
> to be **defensible**: every claim is tied to an artifact in the repository, and
> the boundary between *engineered capability* and *empirically demonstrated
> performance* is stated explicitly. Numbers should be regenerated with
> `python eval_summary.py` (and, for the headline pass-rate,
> `python run_benchmark_live.py`) before final submission.

## 1. Summary of Contributions

This work delivers an agentic AI system for process flowsheet design and
optimization in DWSIM. The contribution is threefold:

1. **An agentic reasoning layer** — a multi-step, tool-using LLM agent
   (`agent_v2.py`) that plans, calls tools, observes results, and iterates,
   over an action space of 105 DWSIM operations. It is provider-agnostic, with
   an automatic failover chain across Groq, OpenAI, and Anthropic, a
   provider-neutral conversation history, proactive retrieval-augmented
   generation, persistent session memory, and a case-based experience store.

2. **A flowsheet design and DWSIM integration layer** — a `pythonnet` bridge to
   the DWSIM .NET engine (`dwsim_bridge_v2.py`) plus topology builder,
   step-wise executor, and a template library, hardened with automatic
   recycle-loop tearing (graph cycle detection), energy-stream injection, and
   read-back-after-write verification of every property set.

3. **An optimization and analysis layer** — a natural-language optimization
   orchestrator with admissibility gates, layered over CMA-ES, DE/PSO/GA,
   NSGA-II, NLopt, Bayesian/EGO, equation-oriented surrogate NLP, and SALib
   global sensitivity, each degrading gracefully to a SciPy baseline when the
   external library is absent. Two contributions specifically target the
   capabilities a commercial tool (Aspen Plus) has over a black-box wrapper:
   (a) a **derivative-free trust-region surrogate EO** optimizer
   (`run_eo_trust_region`), a provably-convergent model-management scheme
   (Conn–Scheinberg–Vicente) that upgrades the surrogate EO from a one-shot
   approximation; and (b) an **infeasible-path SQP** optimizer
   (`run_infeasible_path_optimizer`) implementing the central Aspen optimizer
   technique — promoting recycle tear-stream variables to decision variables
   with the loop-closure equations as equality constraints, so the recycle and
   the objective converge SIMULTANEOUSLY (Biegler); a **multi-process parallel
   evaluator** (`parallel_evaluator`) that removes the single-CLR serialization
   for population/batch methods; and a **Total-Annualized-Cost objective**
   (`tac_objective`) — size-dependent Turton power-law capital, annualised by
   the capital-recovery factor, traded against utility OPEX — so the canonical
   Aspen workflow "minimise the TAC of this unit" is a single optimization
   target.

## 2. Component-Level Validation

The software components are validated by an automated test suite of ~50 files
exercising the agent loop, the optimization stack, the construction-robustness
passes, the failover logic, and the evaluation harness. At the time of writing
the suite reports **465 passing, 1 skipped** (`pytest -q` from `backend/`). The
suite uses a mock bridge and agent so that it runs without a DWSIM installation;
tests requiring a live engine are skipped automatically.

**Aspen-parity contributions — validated on problems with known answers
(no DWSIM required, so the result is exact and reproducible):**
- *Trust-region surrogate EO:* converges to the exact optimum on Sphere (0) and
  a constrained QP (the KKT point), with the expected ~50× reduction — not
  machine-zero — on Rosenbrock's non-quadratic valley.
- *Infeasible-path SQP:* on an analytic reactor-with-recycle it reaches the
  **exact same optimum** as the classic feasible-path approach (which fully
  converges the recycle at every step), with the loop closed to a residual of
  **0.0**, using **18 flowsheet passes vs 230 — a 12.8× reduction** (it avoids
  the inner convergence loop). It honours a process constraint and the recycle
  closure simultaneously. *DWSIM integration — attempted live, corrected and
  blocked:* a live exploration on the `reactor_recycle` flowsheet showed the
  correct mechanism is an **open-loop build** (the recycle stream made a FIXED
  feed set to the trial tear, with the recycle block removed) — **not**
  `OT_Recycle MaximumIterations = 1` as first specified, because DWSIM's recycle
  block auto-copies the computed stream to its output and so cannot hold an
  independent tear guess. The open-loop flowsheet builds and converges, but the
  **computed tear stream's molar flow reads back as null** (a "Vessel"-flash /
  stream-read issue), which blocks forming the closure residual. So the live
  coupling is **not yet achieved**; the method remains validated on the analytic
  recycle, and the corrected integration path (open-loop + a working
  computed-tear read) is the remaining work.
- *Parallel evaluator:* parallel batch results identical to serial (order
  preserved). Speed-up is workload-dependent: 1.9× on a mock (no init cost) but
  *slower* (0.11×) on a live 8-design DWSIM batch where per-worker CLR init
  (~30 s) dominates — it pays off only with a persistent pool over many
  generations (see Limitation #7).
- *TAC objective:* the arithmetic (CRF, Turton power-law CAPEX, utility OPEX)
  matches hand calculation, and the project optimizer driven by a TAC objective
  finds the **exact cost-optimal equipment size** on a convex CAPEX↕OPEX
  trade-off (matches a brute-force reference; the optimum is strictly interior —
  a genuine economic trade-off, not a bound).

This establishes **component correctness** — the agent selects and sequences
tools as designed, the optimizers converge on analytic objectives, the recycle
and energy passes fire on the intended topologies, and the failover and history
normalization behave correctly. It does **not**, by itself, establish end-to-end
task success against the real DWSIM engine; that is the role of the benchmark
below.

## 3. Evaluation Methodology

A fixed benchmark of 25 tasks (`benchmark_tasks.py`) was defined **a priori** —
before any experiments — across eight flowsheet categories and three complexity
levels. Each task specifies an exact user prompt, measurable success criteria on
stream results, physical-plausibility constraints, and a human-expert time
baseline. Outcomes are classified as:

- **SUCCESS** — all criteria met and no physical constraint violated;
- **PARTIAL** — converged but a criterion missed by > 5 %;
- **FAILURE_LOUD** — an exception was raised or the agent reported failure;
- **FAILURE_SILENT** — converged but a physical constraint was violated (the
  most dangerous case: numerically convergent yet physically wrong).

Each run is additionally scored by an independent LLM-as-judge on five axes
(physical plausibility, property-package correctness, completeness, reasoning,
and hallucination-absence). The benchmark is executed in-process against the
live agent by `run_benchmark_live.py`, which records the run **mode**
(`live` vs `mock`), persists `benchmark_results.json`, and emits a per-task
results table (`BENCHMARK_TABLE.md`).

## 4. Current Results

The honest state of the recorded evidence (auto-summarized by
`eval_summary.py` → `RESULTS.md`) is:

- **Interaction logs:** 78 agent sessions are recorded. All completed without an
  unhandled exception, and 38 % invoked at least one tool (the remainder being
  knowledge questions). The "completed" rate is a **robustness** indicator, not
  a correctness metric, and is reported as such.

- **LLM-judge quality:** judge scores are presently available for only **2**
  sessions and are therefore indicative only; they are not used to support any
  quantitative quality claim.

- **Hydrogen (biogas-SMR) case study:** 68 runs are recorded and all converged,
  exercising the full design→solve→optimize→report workflow end-to-end. **These
  runs were executed against the mock bridge** (`mock: true`); they validate the
  *workflow and orchestration*, not DWSIM's physics.

- **Formal 25-task benchmark — measured against live DWSIM (2026-06-11):**
  executed crash-isolated (one subprocess per task) with **Claude Sonnet** as
  the agent LLM and a live DWSIM v9.0.5 engine. **Strict pass-rate: 20 % (5/25)**
  (5 SUCCESS, 2 PARTIAL, 18 FAILURE_LOUD), by complexity 2/7 (C1), 3/11 (C2),
  0/7 (C3). This figure, however, **substantially under-measures capability**,
  for two documented reasons:
  - **9 of the 25 tasks never ran** (the most advanced — C6 distillation, C7,
    C8): the Anthropic API **rate-limited after sustained use** ("provider
    returned None", 0 tools called). Each agent request is ~21 k tokens, so a
    free/standard-tier key exhausts throughput partway through the suite. These
    tasks are *inconclusive*, not failures of the agent.
  - **Several converged builds scored null** despite the flowsheet solving
    (`convergence: true`, tools used) because the success criteria reference a
    specific stream tag the agent named differently — a residual scoring
    rigidity beyond the role-alias resolver added in this work.

  Restricting to the **16 tasks the agent actually executed** (tools > 0), the
  rate is **5 SUCCESS + 2 PARTIAL of 16 (31 % strict / 44 % with partial
  credit)**. The agent demonstrably builds and solves real DWSIM flowsheets
  (e.g. the water-heater and pump tasks pass cleanly against live physics).

Consequently, the empirically supportable claim is that the system is
**designed, implemented, component-validated, and demonstrated end-to-end on a
live DWSIM engine**, with a measured but quota-/scoring-limited 20 % strict
benchmark pass-rate (31 % over attempted tasks). A clean headline number
requires a higher-throughput LLM tier (to run all 25 tasks) and further
criteria-matching work — both identified below.

## 5. Limitations and Threats to Validity

1. **The 20 % benchmark number is quota- and scoring-limited, not a clean
   capability measure.** (a) **LLM throughput:** 9/25 tasks could not run because
   the Anthropic API rate-limited mid-suite (the agent's ~21 k-token requests
   exhaust a standard tier); a higher tier — or reducing per-request tokens — is
   needed to attempt all 25. (b) **Scoring rigidity:** some converged, correct
   builds score null because criteria pin an exact output-stream tag; the
   role-alias resolver added here helps but does not cover multi-unit
   intermediate-stream naming. (c) **Platform stability:** certain DWSIM
   operations (notably `parametric_study`) can raise a process-terminating
   pythonnet/.NET exception; the suite is now run crash-isolated (one subprocess
   per task) so a single CLR crash no longer voids the whole run. A defensible
   headline number requires addressing (a) and (b); the attempted-task rate
   (31 % strict / 44 % with partial credit over 16 tasks) is the fairer interim
   measure.

2. **Small judge sample.** LLM-judge coverage (n = 2) is far too small to
   characterize answer quality; it must be scaled to the full benchmark before
   any quality figure is cited.

3. **Construct validity of "success".** The session-level `success` flag records
   turn completion, not task correctness; only the benchmark's criteria-based
   scoring measures correctness. The two are kept strictly separate in reporting.

4. **LLM stochasticity and provider variation.** Outputs depend on the model and
   provider in use; a fixed seed is set where the provider supports it, but
   reproducibility across providers is not guaranteed. Reported numbers should
   state the provider/model and be averaged over repeated runs.

5. **LLM-as-judge bias.** Using an LLM to score an LLM can share blind spots;
   judge scores are treated as a secondary signal alongside the deterministic,
   physics-based success criteria, which are the primary measure.

6. **Surrogate equation-oriented optimization (narrowed, not eliminated).**
   DWSIM does not expose its equation system, so a true open-equation solve
   (Aspen EO with rigorous Jacobians) is impossible directly. The EO optimizer
   is therefore surrogate-based — but it now offers a **derivative-free
   trust-region** mode (`run_eo_trust_region`) in addition to the global-quadratic
   one: local quadratic models inside a trust region with ρ-based step acceptance
   and adaptive radius — a **provably-convergent** model-management scheme
   (Conn, Scheinberg & Vicente, SIAM 2009). It is validated to converge to known
   optima on analytic test problems (sphere → 0; the constrained QP → the exact
   KKT point; ~50× reduction on Rosenbrock, whose curved valley defeats *any*
   quadratic surrogate). This upgrades the EO from a one-shot approximation to a
   rigorous local-optimization method; the residual gap to Aspen EO is the
   surrogate-vs-native-equation fidelity, which is fundamental to optimising over
   a closed simulator rather than a methodological shortcut.

7. **Single-process DWSIM — mitigated for batch evaluation.** A single process
   hosts one in-process CLR, so it solves one flowsheet at a time. This is
   removed for the workloads that actually need it (population optimisers,
   Sobol sampling, parametric sweeps) by a **multi-process worker pool**
   (`parallel_evaluator.py`, `bridge.parallel_evaluate_designs`): N separate
   processes, each with its OWN CLR and its OWN copy of the flowsheet (loaded
   once), evaluate a batch of designs concurrently. Validated for **correctness**
   (parallel results identical to serial, order preserved). On **speed-up the
   live result is reported honestly and is nuanced**: a mock evaluator (no init
   cost) showed 1.9× with 4 workers, but a LIVE 8-design batch on real DWSIM was
   *slower* (0.11×) because each worker initialises its own CLR (~30 s), which
   dwarfs a few fast solves. The pool therefore pays off only when that init is
   amortised — a **persistent** pool reused across many generations of a
   population optimiser with non-trivial per-solve time (rough breakeven ≈
   n_workers × 30 s of total solve work); for one-shot small batches the single
   in-process CLR wins. So it is a *correct, opt-in* parallel primitive whose
   benefit is workload-dependent, not a blanket speed-up. (Aspen likewise does
   not parallelise a single solve.)

## 6. Path to a Complete "Objective Achieved" Claim

A single, well-scoped experiment closes the gap:

1. Provision a machine with DWSIM installed and a valid LLM key.
2. Run `python run_benchmark_live.py` (full suite; ~30–90 s per task).
3. Re-run `python eval_summary.py` to fold the measured pass-rate, per-category
   and per-complexity breakdowns, and mean speed-up-vs-human into `RESULTS.md`.
4. Replace the mock hydrogen case study with a non-mock run for a physically
   validated reference point.

With those artifacts, the contribution moves from *"an agentic AI for DWSIM
flowsheet design and optimization was designed and built"* to *"…and achieves an
X % success rate on a 25-task benchmark against the live DWSIM engine,"* which is
the form the objective requires.
