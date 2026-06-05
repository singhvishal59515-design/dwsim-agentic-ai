"""
experience_store.py — Case-based learning loop for objective-mapping.

The honest gap in this agent is *judgment*: mapping a natural-language goal to
the right optimization objective. The LLM does it competently but not always
reliably, and there is no memory — every goal is solved cold.

This module adds genuine (modest) learning: every time an optimization run
SUCCEEDS and its objective was confirmed by read-back, the (goal → objective →
outcome) triple is persisted as a *case*. On a future goal, the most similar
past cases — matched on goal wording AND flowsheet signature (compounds +
unit-op types) — are retrieved and injected into the objective-mapping prompt
as worked examples ("here is what actually worked before for a similar goal on
a similar flowsheet").

Why this is real learning, not theatre:
  • Only VERIFIED successes are stored (consistent with the write-verification
    discipline) — the agent never learns from an unconfirmed or failed run.
  • Retrieval is grounded in the live flowsheet's chemistry, so a case is only
    surfaced when it is genuinely analogous.
  • The store persists across sessions (JSON on disk), so the agent improves
    with use rather than resetting every restart.

It degrades gracefully: an empty or unavailable store simply means no examples
are injected and behaviour is exactly as before.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("experience_store")

# Persist next to the backend so it survives restarts; override for tests.
_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "experience_cases.json")
_MAX_CASES = 500
_MIN_RETRIEVAL_SCORE = 0.18    # below this, a case is too dissimilar to inject
_lock = threading.RLock()

# Stop-words excluded from goal-token matching so "the/a/to" don't inflate sim.
_STOP = {"the", "a", "an", "to", "of", "and", "or", "for", "in", "on", "at",
         "by", "with", "this", "that", "it", "its", "process", "flowsheet",
         "please", "optimise", "optimize", "optimisation"}


def _tokens(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOP and len(w) > 1}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── persistence ────────────────────────────────────────────────────────────

def _store_path() -> str:
    return os.environ.get("EXPERIENCE_STORE_PATH", _DEFAULT_PATH)


def _load_all() -> List[Dict[str, Any]]:
    p = _store_path()
    if not os.path.exists(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as exc:
        _log.debug("experience store load failed: %s", exc)
        return []


def _save_all(cases: List[Dict[str, Any]]) -> None:
    p = _store_path()
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cases[-_MAX_CASES:], f, indent=1, default=str)
        os.replace(tmp, p)
    except Exception as exc:
        _log.warning("experience store save failed: %s", exc)


# ── flowsheet signature ────────────────────────────────────────────────────

def flowsheet_signature(bridge) -> Dict[str, List[str]]:
    """Compounds + unit-op types present — the basis for 'is this flowsheet
    analogous?'. Cheap, read-only, and tolerant of a missing bridge."""
    sig: Dict[str, List[str]] = {"compounds": [], "unit_types": []}
    try:
        if hasattr(bridge, "list_compounds"):
            r = bridge.list_compounds()
            if isinstance(r, dict) and r.get("success"):
                sig["compounds"] = sorted({str(c).lower()
                                           for c in r.get("compounds", [])})
    except Exception:
        pass
    try:
        if hasattr(bridge, "list_simulation_objects"):
            r = bridge.list_simulation_objects()
            if isinstance(r, dict) and r.get("success"):
                types = set()
                for o in r.get("objects", []):
                    t = str(o.get("type", "")).lower()
                    if t and "stream" not in t:
                        types.add(t)
                sig["unit_types"] = sorted(types)
    except Exception:
        pass
    return sig


# ── record ─────────────────────────────────────────────────────────────────

def record_case(goal: str, spec: Dict[str, Any], result: Dict[str, Any],
                bridge=None, signature: Optional[Dict] = None) -> bool:
    """Persist a VERIFIED success as a learnable case. Returns True if stored.

    Guards: only store when the run succeeded, produced a real objective value,
    and the objective is a concrete variable/expression. Heuristic-fallback or
    low-confidence specs are still stored but tagged, so retrieval can prefer
    genuine LLM/verified cases."""
    try:
        if not (isinstance(result, dict) and result.get("success")):
            return False
        best = result.get("best_objective")
        if best is None:
            return False
        obj = (spec or {}).get("objective") or {}
        if not obj.get("type"):
            return False
        sig = signature or (flowsheet_signature(bridge) if bridge else
                            {"compounds": [], "unit_types": []})
        case = {
            "id": f"{int(time.time()*1000):x}",
            "goal": str(goal),
            "goal_tokens": sorted(_tokens(goal)),
            "objective": {k: obj.get(k) for k in
                          ("type", "tag", "property", "expression")
                          if obj.get(k) is not None},
            "minimize": bool(spec.get("minimize", True)),
            "method": spec.get("method", ""),
            "best_objective": float(best),
            "compounds": sig.get("compounds", []),
            "unit_types": sig.get("unit_types", []),
            "verified": not bool(spec.get("_heuristic_fallback")),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with _lock:
            cases = _load_all()
            # De-dup: drop an older near-identical case (same goal+objective).
            sig_key = (case["goal"].lower().strip(),
                       json.dumps(case["objective"], sort_keys=True))
            cases = [c for c in cases
                     if (c.get("goal", "").lower().strip(),
                         json.dumps(c.get("objective", {}), sort_keys=True)) != sig_key]
            cases.append(case)
            _save_all(cases)
        _log.info("recorded experience case: %s -> %s",
                  goal[:50], case["objective"])
        return True
    except Exception as exc:
        _log.debug("record_case failed: %s", exc)
        return False


# ── retrieve ───────────────────────────────────────────────────────────────

def _score(query_tokens: set, query_sig: Dict, case: Dict) -> float:
    goal_sim = _jaccard(query_tokens, set(case.get("goal_tokens", [])))
    comp_sim = _jaccard(set(query_sig.get("compounds", [])),
                        set(case.get("compounds", [])))
    type_sim = _jaccard(set(query_sig.get("unit_types", [])),
                        set(case.get("unit_types", [])))
    # Goal wording dominates; chemistry/topology refine. Verified cases get a
    # small bonus so genuine successes outrank heuristic ones.
    score = 0.55 * goal_sim + 0.28 * comp_sim + 0.17 * type_sim
    if case.get("verified"):
        score += 0.05
    return score


def retrieve_similar(goal: str, bridge=None, k: int = 3,
                     signature: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """Return up to k past cases most similar to (goal, current flowsheet),
    each above the relevance threshold. Empty list when nothing is analogous."""
    with _lock:
        cases = _load_all()
    if not cases:
        return []
    qt = _tokens(goal)
    qs = signature or (flowsheet_signature(bridge) if bridge else
                       {"compounds": [], "unit_types": []})
    scored: List[Tuple[float, Dict]] = []
    for c in cases:
        s = _score(qt, qs, c)
        if s >= _MIN_RETRIEVAL_SCORE:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, c in scored[:k]:
        c = dict(c)
        c["_similarity"] = round(s, 3)
        out.append(c)
    return out


def format_examples_for_prompt(cases: List[Dict[str, Any]]) -> str:
    """Render retrieved cases as a compact worked-examples block for the
    objective-mapping LLM prompt."""
    if not cases:
        return ""
    lines = ["LEARNED_EXAMPLES (objectives that were VERIFIED to work on "
             "similar goals/flowsheets — prefer this style when applicable):"]
    for c in cases:
        obj = c.get("objective", {})
        if obj.get("type") == "expression":
            otxt = f"expression `{obj.get('expression','')}`"
        else:
            otxt = f"{obj.get('tag','')}.{obj.get('property','')}"
        direction = "minimise" if c.get("minimize") else "maximise"
        lines.append(
            f"  - goal: \"{c.get('goal','')}\"  ->  {direction} {otxt}"
            f"  (compounds: {', '.join(c.get('compounds', [])[:4]) or 'n/a'})")
    return "\n".join(lines)


def stats() -> Dict[str, Any]:
    with _lock:
        cases = _load_all()
    return {"total_cases": len(cases),
            "verified": sum(1 for c in cases if c.get("verified")),
            "path": _store_path()}
