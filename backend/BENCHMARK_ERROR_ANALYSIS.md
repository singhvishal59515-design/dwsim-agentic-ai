# Benchmark Error Analysis — 25-Task Live Run

Deterministic per-task failure-mode attribution over the existing run (`benchmark_results.json`); no re-run, no LLM. Of **25** tasks, **6 passed**, **6 never executed** (0 tool calls — provider rate-limited), leaving **19 truly executed**.

- Strict pass rate (all tasks): **24.0%** (6/25)
- Executed pass rate (excludes the 6 inconclusive): **31.6%** (6/19)

## Failure-mode distribution

| Mode | Count | Meaning |
|---|--:|---|
| PASS | 6 | task passed all criteria |
| NOT_EXECUTED | 6 | 0 tool calls — provider rate-limited before execution; inconclusive, not a capability failure |
| PARTIAL_NEAR_MISS | 2 | converged but a success criterion missed — candidate scoring rigidity (correct build, non-canonical tag) |
| EARLY_ABORT | 2 | failed after ≤2 tool calls — likely an early tool error or precondition block |
| EXECUTED_FAILURE | 9 | ran >2 tool calls without passing — genuine capability or scoring wall |

## By category

| Category | pass | not-exec | partial | early-abort | exec-fail |
|---|--:|--:|--:|--:|--:|
| convergence_repair | 1 | 3 | 0 | 0 | 0 |
| distillation | 0 | 2 | 0 | 0 | 1 |
| flowsheet_analysis | 1 | 0 | 0 | 1 | 1 |
| multi_unit_creation | 0 | 0 | 0 | 1 | 3 |
| parametric_study | 1 | 0 | 1 | 0 | 1 |
| property_modification | 1 | 0 | 0 | 0 | 2 |
| reactor | 0 | 1 | 0 | 0 | 1 |
| single_unit_creation | 2 | 0 | 1 | 0 | 0 |

## Per-task attribution

| Task | cat | C | tools | outcome | derived mode |
|---|---|--:|--:|---|---|
| C1-T01 | single_unit_creation | 1 | 2 | SUCCESS | PASS |
| C1-T02 | single_unit_creation | 1 | 3 | SUCCESS | PASS |
| C1-T03 | single_unit_creation | 1 | 16 | PARTIAL | PARTIAL_NEAR_MISS |
| C2-T01 | multi_unit_creation | 2 | 5 | FAILURE_LOUD | EXECUTED_FAILURE |
| C2-T02 | multi_unit_creation | 2 | 1 | FAILURE_LOUD | EARLY_ABORT |
| C2-T03 | multi_unit_creation | 2 | 5 | FAILURE_LOUD | EXECUTED_FAILURE |
| C2-T04 | multi_unit_creation | 2 | 4 | FAILURE_LOUD | EXECUTED_FAILURE |
| C3-T01 | flowsheet_analysis | 1 | 10 | FAILURE_LOUD | EXECUTED_FAILURE |
| C3-T02 | flowsheet_analysis | 1 | 1 | FAILURE_LOUD | EARLY_ABORT |
| C3-T03 | flowsheet_analysis | 2 | 5 | SUCCESS | PASS |
| C4-T01 | property_modification | 1 | 7 | FAILURE_LOUD | EXECUTED_FAILURE |
| C4-T02 | property_modification | 1 | 9 | FAILURE_LOUD | EXECUTED_FAILURE |
| C4-T03 | property_modification | 2 | 10 | SUCCESS | PASS |
| C5-T01 | parametric_study | 2 | 22 | PARTIAL | PARTIAL_NEAR_MISS |
| C5-T02 | parametric_study | 2 | 25 | SUCCESS | PASS |
| C5-T03 | parametric_study | 3 | 5 | FAILURE_LOUD | EXECUTED_FAILURE |
| C6-T01 | distillation | 2 | 0 | FAILURE_LOUD | NOT_EXECUTED |
| C6-T02 | distillation | 3 | 29 | FAILURE_LOUD | EXECUTED_FAILURE |
| C6-T03 | distillation | 3 | 0 | FAILURE_LOUD | NOT_EXECUTED |
| C7-T01 | reactor | 3 | 0 | FAILURE_LOUD | NOT_EXECUTED |
| C7-T02 | reactor | 3 | 20 | FAILURE_LOUD | EXECUTED_FAILURE |
| C8-T01 | convergence_repair | 2 | 13 | SUCCESS | PASS |
| C8-T02 | convergence_repair | 2 | 0 | FAILURE_LOUD | NOT_EXECUTED |
| C8-T03 | convergence_repair | 3 | 0 | FAILURE_LOUD | NOT_EXECUTED |
| C8-T04 | convergence_repair | 3 | 0 | FAILURE_LOUD | NOT_EXECUTED |

## Findings

1. **6 of the 25 tasks never executed** (C6-T01, C6-T03, C7-T01, C8-T02, C8-T03, C8-T04) — 0 tool calls, the provider rate-limited before the agent acted. Counting these as failures understates the pipeline: the honest executed pass rate is 31.6%, not 24.0%.
2. **2 near-miss(es)** (C1-T03, C5-T01) converged but missed a criterion — the scoring-rigidity signature (correct build, output stream named outside the criteria's exact tag set). A tolerance-aware, role-based scoring resolver would likely credit these.
3. The weakest categories are multi-unit creation, distillation and reactors (each 0 passes); single-unit creation is strongest. The failures concentrate in topology-heavy, many-object builds rather than in solving or property reads.
4. **Data-quality caveat:** the per-task `convergence` field reads `True` even for the 6 non-executed tasks (a stale default), so no aggregate solver-convergence number is trustworthy from this run — consistent with the project's standing rule against asserting one.
