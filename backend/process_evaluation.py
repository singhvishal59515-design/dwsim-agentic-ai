"""
process_evaluation.py
─────────────────────
A deterministic, multi-dimensional *design-quality* rubric for a process
configuration — the engineering-scoring idea from Tian et al., "From Text to
Simulation: A Multi-Agent LLM Workflow for Automated Chemical Process Design"
(arXiv:2601.06776, 2026), adapted to this project.

Why this is additive. The project already has (i) a deterministic SafetyValidator
(physical-plausibility failures) and (ii) an *LLM-as-judge* that scores answers on
four soft criteria. It does NOT have a deterministic, reproducible engineering
rubric that rolls the existing subsystems — TAC economics, safety violations,
recycle/topology completeness, convergence/VF sanity — into ONE comparable number
per design. Tian et al. use exactly such a rubric (their Eq. 1) as the reward
signal that drives their search. We reproduce its *math* verbatim so the engine in
emcts.py has a principled, quota-free objective, and so a suite-level score can be
reported the way their Table 1 does.

The rubric (Tian et al., Eq. 1; weights from Seider, Lewin, Seader et al.,
"Product and Process Design Principles", 2016):

    S_i = w1·Ef + w2·Es + w3·Ps + w4·Tf + w5·Tr
        (Economic, Environmental, Safety, Technical, Topological)
    weights = 0.35, 0.25, 0.15, 0.15, 0.10   (industrial prioritisation)

A non-converged design is penalised rather than discarded — it may still carry a
high-potential dimension worth exploring (this is the property emcts.py exploits):

    S_fail = λ · S ,   λ = 0.30

Everything here is a PURE function of a `facts` dict — no DWSIM, no LLM — so it is
fully unit-testable and reproduces the paper's suite arithmetic exactly. The
caller assembles `facts` from the live bridge (or a mock); each dimension may be
supplied directly in facts["dimensions"] (each in [0, 1]) or DERIVED from raw
signals by the heuristics below. Missing signals score a neutral 0.5 rather than
fabricating a number.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

# ── Rubric constants (Tian et al. 2026; Seider et al. 2016) ───────────────────
DIMENSIONS = ("economic", "environmental", "safety", "technical", "topological")
WEIGHTS: Dict[str, float] = {
    "economic":      0.35,
    "environmental": 0.25,
    "safety":        0.15,
    "technical":     0.15,
    "topological":   0.10,
}
FAIL_PENALTY = 0.30          # λ — multiplier applied to a non-converged design
NEUTRAL = 0.5                # score for a dimension with no available signal

# Safety-violation severity → how much a single violation subtracts from 1.0.
_SEVERITY_COST = {"SILENT": 0.5, "LOUD": 0.3, "WARNING": 0.1}


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


# ── Dimension derivations (each → [0, 1]) ─────────────────────────────────────
# All are deterministic and total; an absent signal yields NEUTRAL, never a guess.

def _economic(facts: Mapping[str, Any]) -> float:
    """Lower total-annualised cost than a baseline ⇒ higher score.

    Signals: facts['tac'] vs facts['tac_baseline'] (cheaper-than-baseline → 1).
    With no baseline, a bare TAC cannot be scored absolutely → NEUTRAL.
    """
    tac = facts.get("tac")
    base = facts.get("tac_baseline")
    if tac is None or base is None:
        return NEUTRAL
    try:
        tac = float(tac); base = float(base)
    except (TypeError, ValueError):
        return NEUTRAL
    if tac <= 0 or base <= 0:
        return NEUTRAL
    # ratio 1.0 at baseline; >1 cheaper-than-baseline saturates to 1, costlier decays.
    return _clamp01(0.5 * base / tac)


def _environmental(facts: Mapping[str, Any]) -> float:
    """Less wasted material/energy ⇒ greener.

    Signal: facts['waste_fraction'] ∈ [0, 1] (purge/emission/loss as a fraction of
    feed). 0 → 1.0, 1 → 0.0. No signal → NEUTRAL.
    """
    wf = facts.get("waste_fraction")
    if wf is None:
        return NEUTRAL
    try:
        return _clamp01(1.0 - float(wf))
    except (TypeError, ValueError):
        return NEUTRAL


def _safety(facts: Mapping[str, Any]) -> float:
    """Start at 1.0; subtract per SafetyValidator violation, weighted by severity.

    Signal: facts['safety_violations'] — a list of dicts with a 'severity' key, or
    a list of severity strings, or an int count (treated as WARNING-severity). An
    empty list means "checked, none found" → 1.0. No key → NEUTRAL.
    """
    if "safety_violations" not in facts:
        return NEUTRAL
    viols = facts["safety_violations"]
    if isinstance(viols, int):
        return _clamp01(1.0 - viols * _SEVERITY_COST["WARNING"])
    score = 1.0
    for v in (viols or []):
        if isinstance(v, Mapping):
            sev = str(v.get("severity", "WARNING")).upper()
        else:
            sev = str(v).upper()
        score -= _SEVERITY_COST.get(sev, _SEVERITY_COST["WARNING"])
    return _clamp01(score)


def _technical(facts: Mapping[str, Any]) -> float:
    """Design maturity, INDEPENDENT of convergence (convergence is handled by λ).

    Signals (all optional, combined multiplicatively from 1.0):
      • streams[i]['vapor_fraction'] outside [0, 1]  → each costs 0.25
      • facts['mass_balance_error']  (fractional)    → 1 − min(1, 10·err)
    No signal → NEUTRAL.
    """
    streams = facts.get("streams")
    mbe = facts.get("mass_balance_error")
    if streams is None and mbe is None:
        return NEUTRAL
    score = 1.0
    for s in (streams or []):
        vf = s.get("vapor_fraction") if isinstance(s, Mapping) else None
        if vf is not None:
            try:
                vf = float(vf)
                if vf < -1e-6 or vf > 1.0 + 1e-6:
                    score -= 0.25
            except (TypeError, ValueError):
                pass
    if mbe is not None:
        try:
            score *= _clamp01(1.0 - min(1.0, 10.0 * abs(float(mbe))))
        except (TypeError, ValueError):
            pass
    return _clamp01(score)


def _topological(facts: Mapping[str, Any]) -> float:
    """Structural completeness: no dangling ports, recycles closed.

    Signals: facts['dangling_ports'] (int) and facts['open_recycles'] (int); each
    occurrence costs 0.2. Alternatively facts['topological'] override. No signal →
    NEUTRAL.
    """
    dangling = facts.get("dangling_ports")
    open_rec = facts.get("open_recycles")
    if dangling is None and open_rec is None:
        return NEUTRAL
    score = 1.0 - 0.2 * (int(dangling or 0) + int(open_rec or 0))
    return _clamp01(score)


_DERIVERS = {
    "economic": _economic, "environmental": _environmental, "safety": _safety,
    "technical": _technical, "topological": _topological,
}


def derive_dimensions(facts: Mapping[str, Any]) -> Dict[str, float]:
    """Resolve all five dimensions to [0, 1] from `facts`.

    An explicit facts['dimensions'][name] (in [0, 1]) overrides the heuristic.
    """
    override = facts.get("dimensions", {}) or {}
    out: Dict[str, float] = {}
    for name in DIMENSIONS:
        if name in override and override[name] is not None:
            try:
                out[name] = _clamp01(float(override[name]))
                continue
            except (TypeError, ValueError):
                pass
        out[name] = _DERIVERS[name](facts)
    return out


# ── Core scoring ──────────────────────────────────────────────────────────────

def weighted_dimension_score(dims: Mapping[str, float],
                             weights: Optional[Mapping[str, float]] = None) -> float:
    """Σ wᵢ·dᵢ over the five dimensions (Tian et al. Eq. 1). Result in [0, 1]."""
    w = weights or WEIGHTS
    return sum(w[name] * _clamp01(float(dims.get(name, NEUTRAL))) for name in DIMENSIONS)


def penalize(weighted: float, converged: bool, fail_penalty: float = FAIL_PENALTY) -> float:
    """S if converged else λ·S — a non-converged design is dimmed, not deleted."""
    return float(weighted) if converged else float(fail_penalty) * float(weighted)


def score_design(facts: Mapping[str, Any],
                 weights: Optional[Mapping[str, float]] = None,
                 fail_penalty: float = FAIL_PENALTY) -> Dict[str, Any]:
    """Score one process configuration. Pure; never raises on a well-formed dict.

    Returns an envelope with the five dimensions (both [0,1] and ×100 scales, the
    paper reports ×100), the weighted score, and the convergence-penalised score
    that search engines should maximise.
    """
    converged = bool(facts.get("converged", True))
    dims = derive_dimensions(facts)
    weighted = weighted_dimension_score(dims, weights)
    pen = penalize(weighted, converged, fail_penalty)
    return {
        "success": True,
        "converged": converged,
        "dimensions": dims,
        "dimensions_100": {k: round(v * 100, 2) for k, v in dims.items()},
        "weighted_score": round(weighted, 6),
        "weighted_score_100": round(weighted * 100, 2),
        "penalized_score": round(pen, 6),
        "penalized_score_100": round(pen * 100, 2),
        "weights": dict(weights or WEIGHTS),
        "fail_penalty": fail_penalty,
    }


# ── Suite-level aggregation (reproduces Tian et al. Table 1 arithmetic) ───────

def simulation_convergence_rate(records: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """SCR — fraction of designs that converged, as a percentage (Tian et al.).

    `records` are dicts each carrying a boolean 'converged'. Returns None for an
    empty set (so callers never assert a rate from zero data — the project's
    standing rule against default convergence numbers).
    """
    if not records:
        return None
    conv = sum(1 for r in records if r.get("converged"))
    return round(conv / len(records) * 100.0, 1)


def aggregate(records: Sequence[Mapping[str, Any]],
              weights: Optional[Mapping[str, float]] = None,
              fail_penalty: float = FAIL_PENALTY) -> Dict[str, Any]:
    """Roll a set of per-design `facts` into a suite score the way the paper does.

    Overall S = mean over designs of the convergence-penalised weighted score
    (×100). This reproduces Tian et al.'s Table 1, where, e.g., dimension means of
    ~73 with an SCR of 23.4% and λ=0.3 give S ≈ 73·(0.234 + 0.3·0.766) ≈ 34.
    """
    if not records:
        return {"success": False, "n": 0, "error": "no records"}
    per = [score_design(r, weights, fail_penalty) for r in records]
    dim_means = {
        name: round(sum(p["dimensions"][name] for p in per) / len(per) * 100, 2)
        for name in DIMENSIONS
    }
    overall = sum(p["penalized_score"] for p in per) / len(per)
    return {
        "success": True,
        "n": len(per),
        "scr_pct": simulation_convergence_rate(records),
        "dimension_means_100": dim_means,
        "overall_score_100": round(overall * 100, 2),
        "weights": dict(weights or WEIGHTS),
        "fail_penalty": fail_penalty,
    }
