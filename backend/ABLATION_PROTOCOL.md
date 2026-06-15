# Ablation Protocol (Phase 3 — run hardening)

This documents the **reproducibility controls** that make the Phase-4 ablation
study defensible. Phase 3 hardens *how* the agent runs so that every condition
is configured identically and every turn is logged with enough metadata to
group and analyse. The four conditions and the statistics are Phase 4.

## Controls held identical across all conditions

| Control | Value | Where enforced |
|---|---|---|
| Provider + model | ONE, locked (no cross-provider failover) | `ablation_config.lock_provider` → `_llm_chat_with_retry` skips `_FAILOVER_CHAIN` |
| Temperature | **0.0 on every attempt** (no retry-temperature diversity) | `ablation_config.retry_temperatures()` → `_try_one_provider` |
| LLM retry budget | fixed: `_LLM_MAX_ATTEMPTS` attempts within `_LLM_RETRY_BUDGET_S` | `agent_v2._try_one_provider` |
| Tool-error circuit breaker | break after 3 consecutive identical (tool, error) pairs | `agent_v2.chat` |
| Max ReAct iterations | `max_iterations` (default 20) | `agent_v2.chat` |
| Seed | recorded per turn | `replay_log` |

A single environment variable enters ablation mode and turns on the provider
lock and temperature-0 determinism automatically:

```
DWSIM_ABLATION_CONDITION = full | no_rag | no_safety | direct_llm
DWSIM_ABLATION_TASK      = C1-T01            # the task id under test
DWSIM_ABLATION_REP       = 1                 # repetition index (>= 3 reps/condition)
```

Optional, independent of a condition:

```
DWSIM_DETERMINISTIC = 1   # force temperature 0 on all attempts
DWSIM_LOCK_PROVIDER = 1   # disable cross-provider failover
```

These are read live from the environment on every turn (`ablation_config.py`),
so the harness can set them per task/rep without restarting the process.

## Transcript log (full ReAct trace, JSONL)

Every turn appends one self-contained `ReplayTurn` record to a JSONL file
(`replay_log.py`), location overridable with `DWSIM_REPLAY_LOG_DIR`
(default `~/.dwsim_agent/replay/replay_log.jsonl`). Each record carries:

- identity: `turn_id`, `session_id`, `turn_index`, `timestamp`
- **ablation tags: `condition`, `task_id`, `rep`** (new in Phase 3)
- model metadata: `provider`, `model`, `temperature`, `seed`,
  `prompt_hash`, `system_prompt_hash` (drift detection)
- full conversation `messages` snapshot
- per-tool-call trace: `tool_name`, `arguments`, `result`, `duration_ms`,
  `success`, `error`; plus the ordered `tool_sequence`
- outcomes: `final_answer`, `converged`, `stream_snapshot`, `sf_violations`
- timing: `duration_s`, `llm_calls`

Records are append-only (never overwritten). The same prompt + seed +
temperature 0 + locked model reproduces the tool sequence.

### Metrics derivable per task (for the Phase-4 stats)

success (binary), tool-call count (`len(tool_sequence)`), wall time
(`duration_s`), LLM round-trips (`llm_calls`), error-recovery events
(tool_calls where `success == False` followed by a later success),
safety violations caught (`sf_violations`).

## Running the study (now fully wired)

The runner drives the **real agent** over the frozen 25 tasks under each
condition, applying the toggles via `ablation_config`, scoring each task with the
benchmark success criteria (`benchmark_tasks.run_task`), and writing one JSONL
record per task to `ablation_logs/`.

```powershell
# Mechanics smoke test (no real study) — proves the pipeline end to end:
python ablation_runner.py --smoke

# Full study (needs LLM throughput): 4 conditions × 25 tasks × 3 reps
python ablation_runner.py --reps 3
#   A = Full System   B = No-RAG   C = No-SafetyValidator   D = Direct LLM

# Analyse:
python ablation_report.py     # tables, Kruskal-Wallis, Mann-Whitney+Holm, Cohen's d, CSV
python ablation_stats.py --records <(...)   # programmatic stats object (tests/paper)
```

Per-task JSONL record (`ablation_logs/cond_<X>_rep<n>.jsonl`):
`{condition, task_id, category, complexity, rep, success(1/0/-1), tool_calls,
wall_time_s, error_recovery_events, outcome}`. `success = -1` marks a
not-applicable task (e.g. a missing fixture); the analysis excludes it so it
never depresses a condition's score.

The pipeline is verified without quota by `tests/test_ablation_runner.py`
(a mock agent → runner → logs → report loader round-trip, including the
error-recovery count and the condition→toggle mapping). A real run therefore
needs only LLM throughput, not more code.

### Condition → toggle mapping
`A→full`, `B→no_rag`, `C→no_safety`, `D→direct_llm` (set automatically by the
runner via `DWSIM_ABLATION_CONDITION`; each implies determinism + provider lock).
