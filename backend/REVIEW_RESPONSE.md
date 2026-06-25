# Response to Reviewer — disposition and new artifacts

This document maps each reviewer suggestion to an action, and supplies the
quota-free additions the review asked for (a non-ideal model-form-uncertainty
result, a failure-mode/recovery table, a threats-to-validity table, and a
computational-requirements + reproducibility note). Items that require LLM
throughput to *measure* are marked accordingly and are honestly out of reach
without a higher-throughput tier — they need data collection, not more code.

## 1. Disposition table

| # | Reviewer suggestion | Status | Where |
|---|---|---|---|
| 2d | Model-form uncertainty: show how package choice shifts a real result | **DONE (live)** | §2 below; `MULTIMODEL_NONIDEAL_VALIDATION.md` |
| 3a | Failure-modes & recovery table | **DONE** | §3 below |
| 6c | Threats-to-validity table (result → evidence → gap) | **DONE** | §4 below |
| 5c | Computational requirements (tokens/turn, tier, hardware) | **DONE** | §5 below |
| 5d | Reproducibility incl. mock-mode (no paid LLM key) | **DONE** | §5 below |
| 6b | Graceful degradation example when optional libs absent | **DONE** | §5 below |
| 2b | Relax/augment scoring (semantic stream matching) | **PARTIAL** — role-alias resolver exists (`_resolve_stream`/`_STREAM_ALIASES`); fuller fuzzy matching is a scoring change best validated on a re-run | code present, extension quota-gated |
| 1 | Update related work vs 2025–26 native DWSIM AI + multi-agent papers | **AUTHOR** — differentiation argument in §6; specific citations must be added/verified by the authors (not fabricated here) | §6 below |
| 4 | Deepen discussion: education / industry / safety | **AUTHOR (draft provided)** | §6 below |
| 5a/4 | Full typeset equations (1)–(4)/(8) | **DONE in the .docx** (`gen_paper_docx.py` renders all display equations); the PDF needs the same | `DWSIM_Agentic_AI_paper.docx` |
| 2a | Expand benchmark to 40–50 tasks / 2nd suite | **QUOTA-GATED** — needs LLM throughput to *run*; specs can be added but are inert without execution |
| 2e | Token/cost/wall-clock per task | **QUOTA-GATED** — per-call payload measured (~22k tok, §5); per-task totals need a run |
| 2f | Complete ablation with p-values / effect sizes | **QUOTA-GATED** — pipeline wired + verified (`ablation_runner.py`); needs throughput |
| 2c | Live infeasible-path coupling | **OPEN (engineering)** — honestly still pending |
| 2 (cross) | Pass-rate variance across providers | **QUOTA-GATED** |

## 2. New result — non-ideal thermodynamic model-form uncertainty (live)

The water-heater demo was *robust* (0.07 % spread) because liquid water is
well-behaved. Re-running the same one-command analysis on a **strongly non-ideal**
system (methanol/water 50/50 mol, heated to 75 °C at 1 atm — a two-phase state)
under four activity-coefficient packages makes the point the reviewer asked for:

| Output | NRTL | UNIQUAC | Wilson | Mod. UNIFAC (Dmd) | spread % |
|---|--:|--:|--:|--:|--:|
| Hot vapour fraction | 0.279 | **0.000** | 0.294 | 0.266 | **140 %** |
| Hot density (kg/m³) | 3.14 | 840.8 | 2.98 | 3.28 | 394 % |

The engineering result — *is there a vapour phase, and how much* — **depends
entirely on the package**: UNIQUAC predicts a single liquid phase at 75 °C where
NRTL/Wilson/UNIFAC predict ~28 % vaporisation. The platform surfaces this in one
command and flags the result as MODEL-DEPENDENT, rather than reporting one
unqualified number. This *quantifies* the fidelity exposure a commercial tool
leaves implicit, and is the honest complement to the robust water case.
(Reproduce: `python validate_multimodel_nonideal_live.py`, no LLM.)

## 3. Failure modes and recovery (from the implementation)

| Failure class | Detection | Recovery / behaviour |
|---|---|---|
| Solver non-convergence | post-solve `check_convergence` (`all_converged` over streams **and** unit ops) | `robust_solve` cascade (auto-recover); optimiser applies a finite penalty and steers away; loud report (never a silent convergent-but-wrong result) |
| Invalid spec (NaN/inf, sub-physical) | `set_stream_property` physical-validation gate (T,P,flow ≥ 0; VF∈[0,1]) | rejected with `code:"INVALID_VALUE"` before touching the solver |
| Write that did not persist | read-back-after-write (re-read in SI, compare) | `verified:false` + warning ("property may be calculated, not specifiable") |
| Topology error (dup tags, unknown port) | `build_flowsheet_atomic` pre-flight validation | actionable error before DWSIM runs |
| Stale read after edit | dirty-state tracking | `needs_resolve:true` + "values are STALE" warning on the read |
| Repeated identical tool error | circuit breaker (3 consecutive same (tool,error)) | breaks the loop, returns a real error instead of looping to the iteration cap |
| Hollow / ill-posed objective | admissibility gate | rejected before any expensive solve |
| Hallucinated number | quality heuristic (numeric claim with no backing tool call) + dual-path audit | guarded/flagged; ≤0.13 % on the audited property path |
| Provider rate-limit/quota/parse | failover chain (provider-neutral history) | switches provider; if all exhausted, a structured provider-failure message |
| CLR process crash | crash-isolated subprocess per task | one task's crash cannot void the run |

## 4. Threats to validity (result → supporting evidence → remaining gap)

| Claimed result | Supporting artifact | Remaining gap |
|---|---|---|
| Solvers reach known optima (5/5; front 2.1e-5; Sobol 1.1e-3) | `validate_optimization.py` (analytic, exact) | none for the analytic claim |
| Infeasible-path 12.8× fewer passes | analytic reactor-with-recycle | live *coupling* still pending (stated) |
| Live closed-loop to duty bounds + reproduces | `validate_optimization_live.py` | monotonic objective (optima at bounds) |
| Capstone interior optimum = √(P₁P₂) | `validate_compression_live.py` (3 ways) | single low-dimensional unit |
| Dual-path ≤0.13 % (no hallucination) | `accuracy.py` `compare()` | property-reporting path only |
| Model-form uncertainty is real | `validate_multimodel_(nonideal_)live.py` | qualitative, not yet tied to an optimum shift |
| Benchmark 24 %/32 %-executed | `benchmark_results.json` | small suite; quota- & scoring-limited; **per-task convergence now a real flag (harness fixed), awaits re-run** |
| Component correctness | 564 passing tests (mock) | mocks can't judge LLM answer *quality* |

## 5. Computational requirements, reproducibility, graceful degradation

**Per-call payload (measured):** ~22k tokens/turn (system prompt ~8k; 107-tool
schema ~21k, phase-gated to ~20–70 tools/turn). The benchmark's binding
constraint is LLM throughput, not the pipeline: low-TPM tiers (e.g. 12k TPM)
413 mid-suite. **Recommended tier:** a model with ≥30k TPM (Claude Sonnet,
GPT-4o-class) for an uninterrupted 25-task run.

**Hardware / platform:** Windows + DWSIM v9.0.5 + .NET runtime via pythonnet;
one in-process CLR per process (the multi-process pool restores batch parallelism
at a ~30 s/worker init cost). This Windows/.NET dependency is a stated
limitation.

**Mock-mode reproducibility (no paid key, no DWSIM):** the full component suite
runs against a mock bridge/agent —
`& "Python39\python.exe" -m pytest tests -q` → **564 passed, 1 skipped** (the
live-DWSIM tests auto-skip). This validates the software end-to-end without any
external service.

**Graceful degradation (concrete):** the optimiser stack is import-guarded —
e.g. NSGA-II uses `pymoo` when present and otherwise the run reports the
dependency is unavailable rather than crashing; the surrogate EO uses
IPOPT-via-Pyomo when available and **falls back to SciPy SLSQP on the identical
NLP** when not; global sensitivity uses SALib when present. Every accelerator
(`pymoo`, `NLopt`, `SALib`, `Pyomo+IPOPT`, `CMA-ES`) degrades to a SciPy/analytic
baseline.

## 6. Author-to-finalise (drafts, not fabricated citations)

**Related work (1):** the differentiation argument the authors should add —
*native simulator assistants and recent multi-agent flowsheet-generation systems
excel at conversational Q&A, script help, and topology drafting; this platform's
distinct contribution is end-to-end agentic control plus a **mathematically
validated, commercial-grade optimisation and sensitivity stack** (infeasible-path
SQP, trust-region surrogate EO, NSGA-II, global Sobol, TAC, model-form
uncertainty) over an open engine, validated against known optima.* The specific
2025–26 citations (native DWSIM LLM assistant/MCP, sketch-to-simulation,
MCTS/multi-agent generators) **must be added and verified by the authors** — they
are not invented here.

**Discussion (4):** suggested additions — (a) *education*: conversational control
lowers the set-up barrier but the authors should caution that it must scaffold,
not shortcut, conceptual learning; (b) *industry*: expert oversight remains
required; position as a co-pilot with export/interop, not a replacement; (c)
*safety*: a natural-language interface lowers the barrier for non-experts, so
hard constraints are enforced deterministically by the SafetyValidator
(pre/post-solve physical checks) independent of the LLM, and a wrong
LLM-proposed optimum cannot bypass them — but users must not treat the agent as a
black-box oracle.
