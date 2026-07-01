# Baseline Comparison

A multi-method comparison in the structure of Tian et al. (arXiv:2601.06776, 2026), Table 1, assembled from what this project can actually measure. The headline contrast — a full tool-using agentic system versus a direct LLM with no tools — is real, from the project's ablation run; external frameworks and the expert baseline are listed honestly as not evaluated, with the reason.

| Method | Pass rate | SCR | Mean time (s) | Source |
|---|--:|--:|--:|---|
| Full agentic system (this work) | 68% | — | — | ablation run |
| Direct LLM, no tools (end-to-end baseline) | 0% | — | — | ablation run |
| GPT-4o / Claude (end-to-end JSON) | — | — | — | not evaluated |
| Swarm / AutoGen / CrewAI / MetaGPT | — | — | — | not evaluated |
| Expert manual design | — | — | — | not evaluated |

**Reading.** The full agentic system reaches a 68% pass rate where the direct LLM with no tools reaches 0% — the entire capability comes from the tool-calling + convergence loop, not the bare model, which is exactly the gap Tian et al. report between their system and an end-to-end LLM. Fresh rubric-scored runs and the external-framework rows are throughput-gated; the harness scores any method callable on the shared 25-task set so those rows populate without new code when quota is available.

**Not evaluated, and why:**
- GPT-4o / Claude (end-to-end JSON) — an end-to-end-LLM baseline; the Direct-LLM row above is this project's measured equivalent.
- Swarm / AutoGen / CrewAI / MetaGPT — external multi-agent frameworks; require framework integration + LLM quota to run on the same tasks.
- Expert manual design — requires recruiting chemical-engineering experts to design and validate each task by hand.
