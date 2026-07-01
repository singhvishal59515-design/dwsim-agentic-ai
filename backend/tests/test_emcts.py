"""
Tests for the Enhanced MCTS design-space search engine (emcts.py), ported from
Tian et al. (arXiv:2601.06776, 2026).

The engine is generic over the configuration type via expand/evaluate callbacks,
so it is validated here on small synthetic landscapes with KNOWN optima — no
DWSIM, no LLM. Covers: node value semantics (the dual-layer rescue), enhanced-UCB
search recovering a known optimum, the failed-but-promising rescue beating a
greedy baseline, budget/expansion bounds, determinism, and graceful empties.
"""
from __future__ import annotations
import os
import random
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# ── Node value semantics ─────────────────────────────────────────────────────

def test_node_immediate_value_applies_fail_penalty():
    from emcts import Node, FAIL_PENALTY
    conv = Node(config=None, score=0.8, converged=True)
    fail = Node(config=None, score=0.8, converged=False)
    assert conv.v_imm == 0.8
    assert abs(fail.v_imm - FAIL_PENALTY * 0.8) < 1e-9


def test_node_potential_is_best_dimension():
    from emcts import Node
    n = Node(config=None, score=0.2, converged=False,
             dimensions={"a": 0.1, "b": 0.95, "c": 0.3})
    # a failed config that excels on one axis still has high potential
    assert n.v_pot == 0.95
    assert n.v_pot > n.v_imm


# ── known-optimum recovery (no ridge) ────────────────────────────────────────

def _grid_problem(star, k):
    n = len(star)
    maxd = n * (k - 1)

    def evaluate(cfg):
        d = sum(abs(a - b) for a, b in zip(cfg, star))
        return {"score": 1.0 - d / maxd, "converged": True,
                "dimensions": {f"s{i}": 1.0 - abs(cfg[i] - star[i]) / (k - 1)
                               for i in range(n)}}

    def expand(cfg, rng):
        out, seen = [], set()
        for _ in range(10):
            i = rng.randrange(n); step = rng.choice((-1, 1))
            nv = cfg[i] + step
            if 0 <= nv < k:
                ch = cfg[:i] + (nv,) + cfg[i + 1:]
                if ch not in seen:
                    seen.add(ch); out.append(ch)
        return out
    return evaluate, expand


def test_recovers_known_optimum():
    from emcts import run_design_search
    star, k = (4, 4), 5
    ev, ex = _grid_problem(star, k)
    res = run_design_search(ex, ev, [(0, 0), (0, 4), (4, 0)],
                            max_iter=120, seed=3)
    assert res["success"] is True
    assert res["best_score"] >= 0.95          # reaches the optimum (or one step away)


def test_search_is_reproducible():
    from emcts import run_design_search
    ev, ex = _grid_problem((4, 4), 5)
    a = run_design_search(ex, ev, [(0, 0), (0, 4), (4, 0)], max_iter=80, seed=11)
    b = run_design_search(ex, ev, [(0, 0), (0, 4), (4, 0)], max_iter=80, seed=11)
    assert a["best_config"] == b["best_config"]
    assert a["best_score"] == b["best_score"]


# ── the dual-layer rescue: beats greedy through a non-converged ridge ─────────

def _ridge_problem():
    """Compact 5×5 grid; the optimum c*=(4,4) sits behind a 2-wide non-converged
    ridge (Manhattan distance 1–2). v_pot falls back to the raw (unpenalised)
    score, so a failed ridge config's potential is its true worth-if-converged."""
    star, k, n = (4, 4), 5, 2
    maxd = n * (k - 1)

    def evaluate(cfg):
        d = sum(abs(a - b) for a, b in zip(cfg, star))
        return {"score": 1.0 - d / maxd, "converged": not (1 <= d <= 2)}

    def expand(cfg, rng):
        out = []
        for i in range(n):
            for st in (-1, 1):
                nv = cfg[i] + st
                if 0 <= nv < k:
                    out.append(cfg[:i] + (nv,) + cfg[i + 1:])
        rng.shuffle(out)
        return out
    return evaluate, expand


def test_rescues_optimum_behind_nonconverged_ridge():
    from emcts import EMCTS
    ev, ex = _ridge_problem()
    eng = EMCTS(ex, ev, [(0, 0)], children_per_expansion=3,
                max_iter=120, patience=3, seed=7)
    res = eng.search()
    # reached the converged global optimum behind the 2-wide non-converged ridge
    assert res["best_score"] >= 0.999
    assert res["best_converged"] is True
    # …and got there by rescuing high-potential failed configs, not by luck
    assert sum(1 for nd in eng.all_nodes if nd.revisited) >= 1


def test_rescue_beats_feasible_path_greedy():
    """A greedy search that abandons non-converged nodes (the policy the paper
    contrasts against) cannot cross the ridge and stalls on the converged shell;
    E-MCTS strictly beats it."""
    from emcts import EMCTS, FAIL_PENALTY
    ev, ex = _ridge_problem()
    eng = EMCTS(ex, ev, [(0, 0)], children_per_expansion=3,
                max_iter=120, patience=3, seed=7)
    res = eng.search()
    budget = eng.evaluations

    # feasible-path greedy: best-first over converged nodes only.
    seen, frontier, evals, best = set(), [], 0, -1.0
    rng = random.Random(7)

    def add(c):
        nonlocal evals, best
        if c in seen:
            return
        seen.add(c); e = ev(c); evals += 1
        frontier.append({"cfg": c, "e": e, "expanded": False})
        if e["converged"]:
            best = max(best, e["score"])
    add((0, 0))
    while evals < budget:
        cand = [f for f in frontier if f["e"]["converged"] and not f["expanded"]]
        if not cand:
            break
        node = max(cand, key=lambda f: f["e"]["score"]); node["expanded"] = True
        for ch in ex(node["cfg"], rng):
            add(ch)
            if evals >= budget:
                break
    assert res["best_score"] > best + 1e-6


# ── budget / expansion / robustness ──────────────────────────────────────────

def test_children_per_expansion_is_bounded():
    from emcts import EMCTS
    ev, ex = _grid_problem((4, 4), 5)
    eng = EMCTS(ex, ev, [(0, 0)], children_per_expansion=3, max_iter=20, seed=1)
    eng.search()
    # every expanded non-root node has at most k children
    for node in eng.all_nodes:
        assert len(node.children) <= 3


def test_evaluation_budget_is_finite():
    from emcts import EMCTS
    ev, ex = _grid_problem((4, 4), 5)
    eng = EMCTS(ex, ev, [(0, 0), (0, 4)], children_per_expansion=3,
                max_iter=10, seed=1)
    eng.search()
    # bounded by seeds + max_iter expansions × children
    assert eng.evaluations <= 2 + 10 * 3 + 1


def test_empty_seed_set_is_graceful():
    from emcts import run_design_search
    ev, ex = _grid_problem((4, 4), 5)
    res = run_design_search(ex, ev, [], max_iter=10, seed=1)
    assert res.get("success") is False
