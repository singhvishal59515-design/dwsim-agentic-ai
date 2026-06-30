# E-MCTS Design-Space Search — Validation Against a Known Optimum

Engine ported from Tian et al. (arXiv:2601.06776, 2026). Validated on a controlled 5×5 design space with a **known global optimum** c* = (4, 4), in which every configuration at Manhattan distance 1–2 from c* is **non-converged** — a thin ridge the search must cross. Operators are answer-agnostic. No LLM, no DWSIM.

| Method | Best score | Reached c*? |
|---|--:|:--:|
| Feasible-path greedy (abandons failed nodes) | 0.625 | no |
| **E-MCTS** (dual-layer value + dynamic revisit) | **1.000** | **yes** |

- E-MCTS optimum: `(4, 4)` (converged=True), reached with **2 high-potential revisits** across the ridge.
- Shared evaluation budget: 116 · nodes: 116 · terminal: target_reached.

**Result:** ✅ E-MCTS recovers the known optimum (score 1.000) while the feasible-path greedy baseline stalls on the converged shell at 0.625 — it cannot step onto the non-converged ridge the optimum sits behind. The +0.375 gap is precisely the dual-layer-value contribution: a high-potential failed configuration is rescued and refined into the true optimum instead of being discarded.

_Scope: a controlled discrete design space with a known answer, isolating the search algorithm from the simulator and the LLM — the same methodology used for the optimiser stack. Wiring E-MCTS to live DWSIM via process_evaluation.score_design is a separate, throughput-gated step._
