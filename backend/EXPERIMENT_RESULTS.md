# Experiment, Ablation & Results

A unified evaluation report in the structure of Tian et al. (arXiv:2601.06776, 2026), assembled from this project's real artifacts. Parts computable without LLM quota are computed and shown with live numbers; parts needing live agent throughput are labelled, not fabricated.

## Experimental setup

Three questions, each with the appropriate experiment: (i) are the search and optimisation algorithms correct? — validated against known optima with no engine and no LLM; (ii) which architectural components are load-bearing? — a controlled component-attribution ablation; (iii) does the agent complete realistic tasks end-to-end? — an a-priori 25-task live benchmark. Designs are scored by the five-dimension rubric of Part D (Tian et al. Eq. 1), and convergence by the Simulation Convergence Rate (SCR = converged / total).

## A. E-MCTS hyperparameter ablation (children per expansion)

Tian et al.'s Table 3 sweeps the E-MCTS branching factor and finds quality (SCR) nearly flat while cost (time, tokens) rises — they adopt 3. We reproduce that experiment on the project's E-MCTS engine, on the validated rescue problem (a known optimum behind a 2-wide non-converged ridge). No LLM.

| Children/expansion | Mean best score | Reached optimum | Mean evaluations (cost) | Mean rescue revisits |
|--:|--:|:--:|--:|--:|
| 2 | 1.000 | 8/8 | 85.2 | 4.8 |
| 3 | 1.000 | 8/8 | 125.1 | 2.0 |
| 4 | 1.000 | 8/8 | 206.2 | 2.0 |
| 5 | 1.000 | 8/8 | 206.2 | 2.0 |

**Reading.** Every setting reaches the optimum (quality at ceiling), so the differentiator is cost: evaluations climb steeply with the branching factor while the dual-layer rescue does the actual ridge-crossing. Two children are cheapest but lean hardest on revisits; 3 balances exploration breadth against cost — the same trade-off, and the same choice, as Tian et al. The flat-quality / rising-cost shape matches their Table 3 (SCR 79.8→80.6 vs time 736→1197 s).

## B. Component-attribution ablation

Each condition removes one capability from the full system and re-runs the a-priori task set (Tian et al. ablate the Task-Understanding agent and E-MCTS; we ablate retrieval, the safety validator, the reflection/diagnostic tools, and — most severely — all tools). Pass-rates are measured; the inferential statistics (Kruskal–Wallis → Mann–Whitney U + Holm → Cohen's d) are wired and throughput-gated.

| Condition | Pass rate | Passed / run |
|---|--:|--:|
| Full System | 68% | 17 / 25 |
| No RAG | 68% | 17 / 25 |
| No Safety Validator | 68% | 17 / 25 |
| No Reflection Tools | 50% | 11 / 22 |
| Direct LLM (No Tools) | 0% | 0 / 10 |

**Reading.** Removing all tools collapses the pass-rate to 0% and removing the reflection tools drops it to 50%, while removing retrieval grounding or the safety validator leaves it unchanged on this task set — evidence that the tool-calling action space and the reflection tools are load-bearing, while grounding and safety act as guardrails whose value is qualitative (avoiding unsafe/unsupported answers) rather than pass-rate-changing here. This mirrors Tian et al.'s finding that removing E-MCTS and the Task-Understanding agent each degrade the run.

Two further in-context-learning conditions are wired (Tian et al. Table 4): **no_cot** strips the chain-of-thought reasoning block and **no_fewshot** strips the worked examples from the system prompt (toggled by `DWSIM_ABLATION_CONDITION`; verified to remove exactly those sections). Their pass-rate deltas are throughput-gated like the rest of the agent ablation.

## C. End-to-end benchmark and per-task error analysis

The a-priori 25-task benchmark ran against the live engine. Strict pass rate **24.0%** (6/25); excluding the **6** tasks that never executed (0 tool calls — provider rate-limited, inconclusive rather than failed), the executed pass rate is **31.6%** (6/19).

| Failure mode | Count |
|---|--:|
| PASS | 6 |
| NOT_EXECUTED | 6 |
| PARTIAL_NEAR_MISS | 2 |
| EARLY_ABORT | 2 |
| EXECUTED_FAILURE | 9 |

Like Tian et al.'s qualitative analysis (which traces specific failures — a flash drum emitting one outlet, a missing recycle loop), the per-task attribution localises *why* each task failed; the full table is in BENCHMARK_ERROR_ANALYSIS.md. The headline is quota- and scoring-limited, not a clean capability ceiling.

## D. The five-dimension design rubric (Tian et al. Eq. 1)

Designs are scored on five weighted dimensions — economic 0.35, environmental 0.25, safety 0.15, technical 0.15, topological 0.1 (Seider et al. 2016) — with a non-convergence penalty λ = 0.3. A worked converged design scores 85.6/100 (dimensions {'economic': 62.5, 'environmental': 95.0, 'safety': 100.0, 'technical': 100.0, 'topological': 100.0}).

**Cross-check.** Reproducing Tian et al.'s Table-1 arithmetic — uniform dimensions of 73 with an SCR of 23.4% and λ = 0.3 — yields an overall score of 33.9/100, matching their reported ≈34 for the GPT-4o row, which confirms the rubric implementation. Scoring the live agent's designs on all five dimensions across every condition (their Table 1) is the one piece that needs live throughput.

## E. Multi-method baseline comparison

A Tian-Table-1-style comparison of the full agentic system against a direct LLM with no tools (the project's measured end-to-end-LLM equivalent): 68% vs 0% pass rate — the capability comes from the tool-calling + convergence loop, not the bare model. External multi-agent frameworks (Swarm, AutoGen, CrewAI, MetaGPT) and the expert-manual baseline are listed honestly as not evaluated, each with its reason; the harness scores any method callable on the shared task set, so those rows populate without new code when quota is available. Full table in BASELINE_COMPARISON.md.

---
_Related artifacts: BASELINE_COMPARISON.md, BENCHMARK_ERROR_ANALYSIS.md, DESIGN_SEARCH_VALIDATION.md, DISTILLATION_TAC_CASE_STUDY.md, ABLATION_PROTOCOL.md._
