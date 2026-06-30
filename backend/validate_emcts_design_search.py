#!/usr/bin/env python3
"""
Validation of the E-MCTS design-space search (no LLM, no DWSIM) — the project's
"validate against a known answer" methodology applied to the search engine ported
from Tian et al. (arXiv:2601.06776, 2026).

We isolate the engine's contribution — the dual-layer value that RESCUES a
high-potential configuration which fails to converge — on a small, controlled
design space with a KNOWN global optimum c*. The optimum sits behind a thin
non-converged "ridge": to reach it, the search must step through configurations
that fail to converge but are one refinement away from the answer.

Two searches share the same operators and evaluation budget:
  • Feasible-path greedy — best-first that ABANDONS non-converged nodes (the
    "discard failed branches" policy the paper contrasts against). It cannot
    cross the ridge and stalls on the converged shell, for any budget.
  • E-MCTS — keeps the failed ridge configs attractive via their potential value
    and forces their expansion through the dynamic-revisit rule, reaching c*.

The search operators are answer-agnostic; only evaluate_fn's scores guide the
engine — so recovering c* is a genuine search result, not a lookup.

    python validate_emcts_design_search.py   ->  DESIGN_SEARCH_VALIDATION.md
"""
from __future__ import annotations

import os
import random
from typing import Any, Dict, List, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── Controlled design space: a 5×5 grid with a 2-wide non-converged ridge ─────
K = 5
N = 2
STAR: Tuple[int, ...] = (4, 4)              # known global optimum c*
MAXDIST = N * (K - 1)                        # 8
RIDGE = (1, 2)              # Manhattan distance 1–2 from c* fails to converge
SEEDS: List[Tuple[int, ...]] = [(0, 0)]


def _dist(c: Tuple[int, ...]) -> int:
    return sum(abs(a - b) for a, b in zip(c, STAR))


def evaluate(config: Tuple[int, ...]) -> Dict[str, Any]:
    d = _dist(config)
    return {"score": 1.0 - d / MAXDIST, "converged": not (RIDGE[0] <= d <= RIDGE[1])}


def expand(config: Tuple[int, ...], rng: random.Random) -> List[Tuple[int, ...]]:
    """Answer-agnostic local move set: ±1 on each axis (clamped), shuffled."""
    out: List[Tuple[int, ...]] = []
    for i in range(N):
        for step in (-1, 1):
            nv = config[i] + step
            if 0 <= nv < K:
                out.append(config[:i] + (nv,) + config[i + 1:])
    rng.shuffle(out)
    return out


def feasible_path_greedy(budget: int, seed: int = 0) -> Dict[str, Any]:
    """Best-first over CONVERGED nodes only — non-converged nodes are abandoned."""
    rng = random.Random(seed)
    seen, frontier, evals, best = set(), [], 0, -1.0

    def add(c: Tuple[int, ...]) -> None:
        nonlocal evals, best
        if c in seen:
            return
        seen.add(c); e = evaluate(c); evals += 1
        frontier.append({"cfg": c, "e": e, "expanded": False})
        if e["converged"]:
            best = max(best, e["score"])

    for s in SEEDS:
        add(s)
    while evals < budget:
        cand = [f for f in frontier if f["e"]["converged"] and not f["expanded"]]
        if not cand:
            break
        node = max(cand, key=lambda f: f["e"]["score"]); node["expanded"] = True
        for ch in expand(node["cfg"], rng):
            add(ch)
            if evals >= budget:
                break
    return {"best_converged_score": round(best, 4), "evaluations": evals}


def main() -> int:
    from emcts import EMCTS

    eng = EMCTS(expand, evaluate, SEEDS, children_per_expansion=3,
                max_iter=120, patience=3, seed=7)
    res = eng.search()
    revisits = sum(1 for nd in eng.all_nodes if nd.revisited)
    budget = eng.evaluations

    greedy = feasible_path_greedy(budget=budget, seed=7)

    emcts_score = res["best_score"]
    found_star = emcts_score >= 0.999 and res["best_converged"]
    beats_greedy = emcts_score > greedy["best_converged_score"] + 1e-6
    ok = found_star and beats_greedy

    L = [
        "# E-MCTS Design-Space Search — Validation Against a Known Optimum",
        "",
        "Engine ported from Tian et al. (arXiv:2601.06776, 2026). Validated on a "
        f"controlled {K}×{K} design space with a **known global optimum** "
        f"c* = {STAR}, in which every configuration at Manhattan distance "
        f"{RIDGE[0]}–{RIDGE[1]} from c* is **non-converged** — a thin ridge the "
        "search must cross. Operators are answer-agnostic. No LLM, no DWSIM.",
        "",
        "| Method | Best score | Reached c*? |",
        "|---|--:|:--:|",
        f"| Feasible-path greedy (abandons failed nodes) | "
        f"{greedy['best_converged_score']:.3f} | "
        f"{'yes' if greedy['best_converged_score'] >= 0.999 else 'no'} |",
        f"| **E-MCTS** (dual-layer value + dynamic revisit) | "
        f"**{emcts_score:.3f}** | {'**yes**' if found_star else 'no'} |",
        "",
        f"- E-MCTS optimum: `{res['best_config']}` "
        f"(converged={res['best_converged']}), reached with **{revisits} "
        f"high-potential revisits** across the ridge.",
        f"- Shared evaluation budget: {budget} · nodes: {res['nodes']} · "
        f"terminal: {res['terminal_reason']}.",
        "",
        f"**Result:** {'✅ ' if ok else ''}E-MCTS recovers the known optimum "
        f"(score {emcts_score:.3f}) while the feasible-path greedy baseline stalls "
        f"on the converged shell at {greedy['best_converged_score']:.3f} — it "
        f"cannot step onto the non-converged ridge the optimum sits behind. The "
        f"+{emcts_score - greedy['best_converged_score']:.3f} gap is precisely the "
        "dual-layer-value contribution: a high-potential failed configuration is "
        "rescued and refined into the true optimum instead of being discarded.",
        "",
        "_Scope: a controlled discrete design space with a known answer, isolating "
        "the search algorithm from the simulator and the LLM — the same "
        "methodology used for the optimiser stack. Wiring E-MCTS to live DWSIM via "
        "process_evaluation.score_design is a separate, throughput-gated step._",
    ]
    md = "\n".join(L) + "\n"
    with open(os.path.join(_HERE, "DESIGN_SEARCH_VALIDATION.md"), "w",
              encoding="utf-8") as f:
        f.write(md)
    print(md.encode("ascii", "replace").decode("ascii"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
