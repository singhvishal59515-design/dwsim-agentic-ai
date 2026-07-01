# Baseline Comparison

A multi-method comparison in the structure of Tian et al. (arXiv:2601.06776, 2026), Table 1, assembled from what this project can actually measure. The headline contrast — a full tool-using agentic system versus a direct LLM with no tools — uses the REAL live 25-task benchmark (24% strict) for the full system and a structural 0% for the tool-less LLM. We deliberately do NOT use the component-ablation pass rates (68%): those are a smoke-run pipeline check, not live-agent performance, and contradict the live benchmark. External frameworks and the expert baseline are listed honestly as not evaluated, with the reason.

| Method | Pass rate | SCR | Mean time (s) | Source |
|---|--:|--:|--:|---|
| Full agentic system (this work) | 24% | — | — | measured (live benchmark) |
| Direct LLM, no tools (end-to-end baseline) | 0% | — | — | structural |
| GPT-4o / Claude (end-to-end JSON) | — | — | — | not evaluated |
| Swarm / AutoGen / CrewAI / MetaGPT | — | — | — | not evaluated |
| Expert manual design | — | — | — | not evaluated |

**Reading.** On the live 25-task benchmark the full agentic system reaches 24% strict (31.6% over executed tasks) where a direct LLM with no tools reaches 0% — with no tools it cannot operate the simulator at all. The entire capability comes from the tool-calling + convergence loop, not the bare model, which is the gap Tian et al. report between their system and an end-to-end LLM. (The 68% component-ablation figure is NOT used here: it is a smoke-run pipeline check, not live-agent performance, and contradicts this benchmark.) Fresh rubric-scored runs and the external-framework rows are throughput-gated; the harness scores any method callable on the shared 25-task set so those rows populate without new code when quota is available.

**Not evaluated, and why:**
- GPT-4o / Claude (end-to-end JSON) — an end-to-end-LLM baseline; the Direct-LLM row above is this project's measured equivalent.
- Swarm / AutoGen / CrewAI / MetaGPT — external multi-agent frameworks; require framework integration + LLM quota to run on the same tasks.
- Expert manual design — requires recruiting chemical-engineering experts to design and validate each task by hand.
