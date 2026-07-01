"""
emcts.py
────────
Enhanced Monte-Carlo Tree Search over discrete process-design CONFIGURATIONS —
the core search algorithm of Tian et al., "From Text to Simulation: A Multi-Agent
LLM Workflow for Automated Chemical Process Design" (arXiv:2601.06776, 2026),
implemented as a generic, engine-agnostic engine for this project.

Why this is additive. The project's existing optimizers (CMA-ES, DE, NSGA-II,
trust-region EO, …) all optimise the CONTINUOUS variables of a GIVEN flowsheet.
None of them SEARCH over the discrete/structural design space — alternative unit
selections, topologies, or parameter regimes — with the simulator in the loop.
E-MCTS does exactly that, and contributes one idea the project lacked: a
*dual-layer value model* that REScues configurations which fail to converge but
score highly on an individual dimension (they may be one parameter tweak away
from the optimum), instead of discarding them as a greedy search would.

Faithful to the paper, each tree node is a COMPLETE configuration (not a single
unit op). The engine is generic: the caller supplies

    expand_fn(config, rng) -> list[child_config]      (≤ children_per_expansion)
    evaluate_fn(config)     -> {"score": float in [0,1],
                                "converged": bool,
                                "dimensions": {name: [0,1], ...}}   # optional

`evaluate_fn` is naturally backed by process_evaluation.score_design on the live
bridge, but for validation it is any pure function with a known optimum — so the
engine is testable with no DWSIM and no LLM (the project's standing methodology).

Algorithm components implemented (paper §"Enhanced MCTS"):
  • Initialization      — root virtual node over the caller's seed configs.
  • Dual-layer value    — V_comb = α(t)·V_imm + (1−α(t))·V_pot, α anneals
                          exploration→exploitation; V_imm is the convergence-
                          penalised immediate score, V_pot the best-dimension /
                          refinement potential.
  • Enhanced UCB        — UCB_enh = V_comb + c(t)·√(ln v_parent / v_i) + Ψ(n),
                          where Ψ adds recent-improvement, variance-sensitivity
                          and depth preference (Eq. 3).
  • Expansion           — each selected node spawns children_per_expansion=3
                          children (the paper's ablated optimum).
  • Dynamic revisit     — a candidate pool of high-potential, not-yet-revisited
                          nodes; on global stagnation, revisit
                          n_rev = argmax (V_pot − V_imm) (Eq. 4) to escape local
                          optima — the failed-config rescue.
  • Terminal conditions — target reached / iteration cap / stagnation with an
                          exhausted pool / visit concentration.
  • Final selection     — 0.7·quality + 0.2·visit-confidence + 0.1·stability.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

FAIL_PENALTY = 0.30          # λ — matches process_evaluation


# ── Node ──────────────────────────────────────────────────────────────────────

@dataclass
class Node:
    config: Any
    parent: Optional["Node"] = None
    children: List["Node"] = field(default_factory=list)
    depth: int = 0
    visits: int = 0
    expanded: bool = False
    revisited: bool = False
    # evaluation
    score: float = 0.0           # raw [0,1] design quality
    converged: bool = True
    dimensions: Dict[str, float] = field(default_factory=dict)

    @property
    def v_imm(self) -> float:
        """Immediate value: convergence-penalised raw score (Tian et al. S_fail)."""
        return self.score if self.converged else FAIL_PENALTY * self.score

    @property
    def v_pot(self) -> float:
        """Potential value: the design's best single dimension — a failed config
        excelling on one axis is flagged as worth refining, not discarded."""
        if self.dimensions:
            return max(self.dimensions.values())
        return self.score

    @property
    def best_descendant_score(self) -> float:
        best = self.score
        for c in self.children:
            best = max(best, c.best_descendant_score)
        return best


# ── Engine ────────────────────────────────────────────────────────────────────

class EMCTS:
    def __init__(
        self,
        expand_fn: Callable[[Any, random.Random], Sequence[Any]],
        evaluate_fn: Callable[[Any], Dict[str, Any]],
        seed_configs: Sequence[Any],
        *,
        children_per_expansion: int = 3,
        max_iter: int = 60,
        target_score: float = 1.0 - 1e-9,
        c_explore: float = 1.0,
        alpha_start: float = 0.3,
        alpha_end: float = 0.9,
        patience: int = 6,
        seed: int = 0,
    ) -> None:
        self.expand_fn = expand_fn
        self.evaluate_fn = evaluate_fn
        self.k = max(1, int(children_per_expansion))
        self.max_iter = max(1, int(max_iter))
        self.target = float(target_score)
        self.c0 = float(c_explore)
        self.alpha0, self.alpha1 = float(alpha_start), float(alpha_end)
        self.patience = max(1, int(patience))
        self.rng = random.Random(seed)

        self.all_nodes: List[Node] = []
        self.root = Node(config=None, depth=0)
        self.root.expanded = True
        self.root.visits = 1
        for cfg in seed_configs:
            self._make_child(self.root, cfg)
        self.evaluations = len(self.all_nodes)
        self.best: Optional[Node] = max(
            (n for n in self.all_nodes), key=lambda n: n.score, default=None)
        self.terminal_reason = ""

    # ── schedules ─────────────────────────────────────────────────────────────
    def _progress(self, it: int) -> float:
        return min(1.0, it / self.max_iter)

    def _alpha(self, it: int) -> float:
        """Exploration→exploitation: weight on immediate value grows over time."""
        return self.alpha0 + (self.alpha1 - self.alpha0) * self._progress(it)

    def _c(self, it: int) -> float:
        """Exploration coefficient decays as the search settles."""
        return self.c0 * (1.0 - 0.5 * self._progress(it))

    # ── node creation / evaluation ────────────────────────────────────────────
    def _make_child(self, parent: Node, cfg: Any) -> Node:
        ev = self.evaluate_fn(cfg) or {}
        node = Node(
            config=cfg, parent=parent, depth=parent.depth + 1,
            score=_clamp01(float(ev.get("score", 0.0))),
            converged=bool(ev.get("converged", True)),
            dimensions={k: _clamp01(float(v)) for k, v in
                        (ev.get("dimensions") or {}).items()},
        )
        parent.children.append(node)
        self.all_nodes.append(node)
        return node

    # ── feature term Ψ (Eq. 3) ────────────────────────────────────────────────
    def _psi(self, n: Node, it: int) -> float:
        # recent improvement: does this node lead anywhere better than itself?
        improvement = max(0.0, n.best_descendant_score - n.score)
        # variance-based sensitivity: spread across the node's dimensions hints at
        # an exploitable gradient.
        if len(n.dimensions) >= 2:
            vals = list(n.dimensions.values())
            mean = sum(vals) / len(vals)
            variance = sum((v - mean) ** 2 for v in vals) / len(vals)
            sensitivity = math.sqrt(variance)
        else:
            sensitivity = 0.0
        # depth preference: favour shallow nodes early (breadth), deeper later.
        depth_pref = (1.0 - self._progress(it)) * (1.0 / (1.0 + n.depth))
        return 0.15 * improvement + 0.10 * sensitivity + 0.05 * depth_pref

    # ── selection (enhanced UCB descent) ──────────────────────────────────────
    def _ucb(self, n: Node, it: int) -> float:
        a = self._alpha(it)
        v_comb = a * n.v_imm + (1.0 - a) * n.v_pot
        parent_visits = n.parent.visits if n.parent else 1
        explore = self._c(it) * math.sqrt(math.log(parent_visits + 1) / (n.visits + 1))
        return v_comb + explore + self._psi(n, it)

    def _select(self, it: int) -> Node:
        """Descend by enhanced UCB to a node that has not yet been expanded."""
        node = self.root
        while node.expanded and node.children:
            node = max(node.children, key=lambda c: self._ucb(c, it))
            if not node.expanded:
                return node
        return node

    # ── dynamic revisit pool (Eq. 4) ──────────────────────────────────────────
    def _revisit_candidate(self) -> Optional[Node]:
        """The expanded-or-leaf node, not yet revisited, with the largest gap
        between potential and realised value — the failed-but-promising rescue."""
        pool = [n for n in self.all_nodes if not n.revisited]
        if not pool:
            return None
        cand = max(pool, key=lambda n: n.v_pot - n.v_imm)
        # only worth a forced revisit if real unrealised potential remains
        if cand.v_pot - cand.v_imm <= 1e-6:
            return None
        return cand

    # ── backprop ──────────────────────────────────────────────────────────────
    def _backprop(self, node: Node) -> None:
        cur: Optional[Node] = node
        while cur is not None:
            cur.visits += 1
            cur = cur.parent

    # ── main loop ─────────────────────────────────────────────────────────────
    def search(self) -> Dict[str, Any]:
        stagnation = 0
        # "Global improvement" = the best *converged* design quality. A
        # non-converged stepping-stone does not count as progress, so a plateau
        # behind a non-converged ridge accumulates stagnation and triggers the
        # rescue — instead of being masked by ever-closer failed configs.
        best_conv = max((n.score for n in self.all_nodes if n.converged),
                        default=float("-inf"))
        for it in range(self.max_iter):
            if self.best and self.best.score >= self.target:
                self.terminal_reason = "target_reached"
                break

            node = self._select(it)

            # On stagnation, force a high-potential revisit to escape a local
            # optimum (Tian et al. Eq. 4). This is an *enhancement*: if no
            # failed-but-promising node exists yet, fall through to normal search
            # rather than terminating — the rescue must not be a stop condition.
            forced = False
            if stagnation >= self.patience:
                rev = self._revisit_candidate()
                if rev is not None:
                    rev.revisited = True
                    node = rev
                    forced = True
                stagnation = 0

            # Expansion. A normally-selected node is unexpanded; a forced-revisit
            # node may already be expanded — re-expand it to deepen a promising
            # failed branch (expand_fn is stochastic, yielding fresh neighbours).
            improved = False
            if (not node.expanded) or forced:
                node.expanded = True
                existing = {c.config for c in node.children}
                for cfg in list(self.expand_fn(node.config, self.rng))[: self.k]:
                    if cfg in existing:
                        continue
                    child = self._make_child(node, cfg)
                    self.evaluations += 1
                    # design-of-record: highest raw quality found (the rescue can
                    # surface a converged optimum hidden behind a failed ridge).
                    if self.best is None or child.score > self.best.score:
                        self.best = child
                    # progress is measured by converged quality only.
                    if child.converged and child.score > best_conv:
                        best_conv = child.score
                        improved = True
            self._backprop(node)

            stagnation = 0 if improved else stagnation + 1
        else:
            self.terminal_reason = self.terminal_reason or "max_iter"

        return self._finalize()

    # ── final selection: 0.7 quality + 0.2 visit-confidence + 0.1 stability ───
    def _finalize(self) -> Dict[str, Any]:
        evaluated = [n for n in self.all_nodes]
        if not evaluated:
            return {"success": False, "error": "no configurations evaluated"}
        max_visits = max(n.visits for n in evaluated) or 1

        def stability(n: Node) -> float:
            if not n.children:
                return 1.0
            scores = [c.score for c in n.children]
            mean = sum(scores) / len(scores)
            var = sum((s - mean) ** 2 for s in scores) / len(scores)
            return 1.0 - min(1.0, math.sqrt(var))

        def final_score(n: Node) -> float:
            quality = n.v_imm
            confidence = n.visits / max_visits
            return 0.7 * quality + 0.2 * confidence + 0.1 * stability(n)

        chosen = max(evaluated, key=final_score)
        # design-of-record: the highest *raw* quality actually found (the rescue
        # means this can differ from the most-visited node).
        best_quality = max(evaluated, key=lambda n: n.score)
        return {
            "success": True,
            "best_config": best_quality.config,
            "best_score": round(best_quality.score, 6),
            "best_converged": best_quality.converged,
            "selected_config": chosen.config,
            "selected_score": round(chosen.score, 6),
            "selected_final_score": round(final_score(chosen), 6),
            "evaluations": self.evaluations,
            "nodes": len(self.all_nodes),
            "terminal_reason": self.terminal_reason,
            "best_dimensions": best_quality.dimensions,
        }


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def run_design_search(expand_fn, evaluate_fn, seed_configs, **kw) -> Dict[str, Any]:
    """Convenience wrapper: build an EMCTS and run it. See EMCTS for kwargs."""
    return EMCTS(expand_fn, evaluate_fn, seed_configs, **kw).search()
