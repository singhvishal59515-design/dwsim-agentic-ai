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
   external library is absent.

## 2. Component-Level Validation

The software components are validated by an automated test suite of ~50 files
exercising the agent loop, the optimization stack, the construction-robustness
passes, the failover logic, and the evaluation harness. At the time of writing
the suite reports **465 passing, 1 skipped** (`pytest -q` from `backend/`). The
suite uses a mock bridge and agent so that it runs without a DWSIM installation;
tests requiring a live engine are skipped automatically.

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

6. **Surrogate equation-oriented optimization.** DWSIM does not expose its
   equation system, so the EO optimizer is surrogate-based (DOE → quadratic
   model → IPOPT → validate), not a true open-equation solve; this is a
   methodological approximation, not native EO.

7. **Single-process DWSIM.** DWSIM runs in one in-process CLR, so solves are
   serialized and cannot be parallelized, bounding throughput.

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
