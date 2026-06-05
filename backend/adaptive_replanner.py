"""
adaptive_replanner.py — Genuine LLM-in-the-loop replanning on failure.

The critical analysis identified the core ceiling: the system has hard-coded
recovery cascades, not genuine reasoning. A real autonomous planner observes
that an approach failed, REASONS about why, and forms a *different* plan.

This module closes that gap for the optimisation path. When a run fails, it
gives the LLM:
  • the original natural-language goal,
  • what was tried (variables, objective, method, bounds),
  • the concrete failure (error, diagnosis, eval-failure rate, which
    constraints/objective were unreadable),
  • the actual flowsheet context (compounds, objects, readable observables),

and asks it to REASON about the cause and propose a *materially different*
plan: a different objective, different variables, widened/narrowed bounds, a
different solver, or an explicit "this goal is infeasible because …".

The output is a structured plan the workflow retries ONCE. This is bounded
autonomy — one reasoning-driven replan, not an unbounded agent loop — which
keeps it testable and prevents runaway cost.

If the LLM is unavailable, a deterministic heuristic replanner applies the
most common fixes (widen bounds, switch to a readable objective, change
method) so the mechanism still degrades gracefully.
"""

from __future__ import annotations
import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("adaptive_replanner")


_REPLAN_SYSTEM_PROMPT = """\
You are a chemical-process-optimisation troubleshooter. An optimisation run
FAILED. Reason about WHY it failed from the evidence, then propose a single,
materially DIFFERENT plan that is likely to succeed.

You will receive:
  GOAL              — the engineer's original natural-language goal
  FAILED_PLAN       — the variables / objective / method that were tried
  FAILURE_EVIDENCE  — the error, diagnosis, eval-failure rate
  FLOWSHEET_CONTEXT — compounds present + readable observables + variables

Output ONLY a JSON object:
{
  "diagnosis": "<one sentence: the most likely reason it failed>",
  "feasible": <bool — false ONLY if the goal is impossible on this flowsheet>,
  "infeasible_reason": "<if not feasible, explain in one sentence>",
  "new_plan": {
     "objective": {"type":"variable","tag":"...","property":"..."}
                  OR {"type":"expression","expression":"...","named_values":[...]},
     "variables": [{"tag":"...","property":"...","unit":"...","lower":N,"upper":N}],
     "method": "simplex" | "lbfgs" | "de",
     "minimize": <bool>,
     "rationale": "<why THIS plan differs from the failed one and should work>"
  }
}

Rules:
  • Use ONLY tags/compounds/observables that appear in FLOWSHEET_CONTEXT.
  • The new objective MUST be in the readable-observables list (the previous
    objective likely failed because it was unreadable — pick a measurable one).
  • If the previous variables hugged their bounds, widen them; if the search
    was too wide and diverged, narrow them.
  • If the goal is genuinely impossible (e.g. 'maximise H2' on a flowsheet with
    no H2), set feasible=false and explain — do NOT invent a fake objective.
  • Output ONLY the JSON. No prose, no markdown fences.
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    depth = 0; start = -1
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(t[start:i+1])
                except json.JSONDecodeError:
                    continue
    return None


def _build_flowsheet_context(bridge) -> Dict[str, Any]:
    """Collect compounds + readable observables + candidate variables."""
    ctx: Dict[str, Any] = {"compounds": [], "observables": [], "variables": []}
    try:
        if hasattr(bridge, "list_compounds"):
            r = bridge.list_compounds()
            if isinstance(r, dict) and r.get("success"):
                ctx["compounds"] = list(r.get("compounds", []))
    except Exception:
        pass
    try:
        from optimization_orchestrator import (
            suggest_decision_variables, _enumerate_flowsheet_objects,
        )
        from dwsim_native_optimizer import _read_object_property
        sv = suggest_decision_variables(bridge, max_n=8)
        ctx["variables"] = [
            {"tag": v["tag"], "property": v["property"], "unit": v["unit"],
             "lower": v["lower"], "upper": v["upper"]}
            for v in sv
        ]
        # Readable observables: only include those that actually read non-None
        objs = _enumerate_flowsheet_objects(bridge)
        for s in objs.get("streams", [])[:10]:
            tag = s.get("tag", "")
            if not tag:
                continue
            for prop in ("temperature_C", "mass_flow_kgh", "pressure_bar",
                         "molar_flow_mols"):
                if _read_object_property(bridge, tag, prop) is not None:
                    ctx["observables"].append(f"{tag}.{prop}")
            for c in ctx["compounds"][:4]:
                p = f"mole_fraction_{c}"
                if _read_object_property(bridge, tag, p) is not None:
                    ctx["observables"].append(f"{tag}.{p}")
        for u in objs.get("unit_ops", [])[:6]:
            tag = u.get("tag", "")
            if tag and _read_object_property(bridge, tag, "HeatDuty") is not None:
                ctx["observables"].append(f"{tag}.HeatDuty")
    except Exception as exc:
        _log.debug("context build failed: %s", exc)
    return ctx


def replan_on_failure(
    bridge,
    llm,
    goal: str,
    failed_spec: Dict[str, Any],
    failure_result: Dict[str, Any],
    history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Reason about a failed optimisation and propose a different plan.

    `history` is the list of previously-tried-and-failed plans (objective /
    method / error / diagnosis) from earlier replan iterations. It lets the
    replanner reason about the *sequence* of failures and avoid repeating an
    approach it already tried.

    Returns:
      {replanned, feasible, diagnosis, new_spec, rationale, _via}
      replanned=False if no better plan could be formed.
    """
    history = history or []
    ctx = _build_flowsheet_context(bridge)

    # Summarise the failure
    failure_evidence = {
        "error": failure_result.get("error", ""),
        "error_code": failure_result.get("error_code", ""),
        "eval_failure_rate": (failure_result.get("result") or {}).get(
            "_eval_failure_rate"),
        "best_objective": (failure_result.get("result") or {}).get(
            "best_objective"),
        "preflight_issues": failure_result.get("preflight_issues"),
        "available_compounds": failure_result.get("available_compounds"),
    }

    # ── LLM path ──────────────────────────────────────────────────────────
    if llm is not None:
        history_block = ""
        if history:
            tried = [
                {"objective": h.get("objective"), "method": h.get("method"),
                 "why_it_failed": h.get("error", "") or h.get("diagnosis", "")}
                for h in history
            ]
            history_block = (
                f"ALREADY_TRIED (do NOT repeat these — they failed):\n"
                f"{json.dumps(tried, default=str)[:1200]}\n\n"
            )
        user = (
            f"GOAL: {goal}\n\n"
            f"FAILED_PLAN:\n{json.dumps(failed_spec, default=str)[:1500]}\n\n"
            f"FAILURE_EVIDENCE:\n{json.dumps(failure_evidence, default=str)[:1500]}\n\n"
            f"{history_block}"
            f"FLOWSHEET_CONTEXT:\n"
            f"  compounds: {ctx['compounds']}\n"
            f"  readable_observables: {ctx['observables'][:30]}\n"
            f"  candidate_variables: {json.dumps(ctx['variables'][:8], default=str)}\n\n"
            "Reason about the failure and output the JSON plan."
        )
        try:
            resp = llm.chat(
                messages=[{"role": "user", "content": user}],
                system_prompt=_REPLAN_SYSTEM_PROMPT,
                tools=[],
            )
            content = resp.get("content") if isinstance(resp, dict) else str(resp)
            parsed = _extract_json(content or "")
            if parsed:
                if parsed.get("feasible") is False:
                    return {
                        "replanned": False,
                        "feasible": False,
                        "diagnosis": parsed.get("diagnosis", ""),
                        "infeasible_reason": parsed.get("infeasible_reason", ""),
                        "_via": "llm",
                    }
                np = parsed.get("new_plan", {})
                if np.get("objective") and np.get("variables"):
                    return {
                        "replanned": True,
                        "feasible": True,
                        "diagnosis": parsed.get("diagnosis", ""),
                        "new_spec": {
                            "success": True,
                            "objective": np["objective"],
                            "variables": np["variables"],
                            "method": np.get("method", "simplex"),
                            "minimize": bool(np.get("minimize", True)),
                            "_replanned": True,
                        },
                        "rationale": np.get("rationale", ""),
                        "_via": "llm",
                    }
        except Exception as exc:
            _log.warning("LLM replan failed: %s", exc)

    # ── Deterministic heuristic replan (LLM unavailable) ──────────────────
    return _heuristic_replan(goal, failed_spec, failure_evidence, ctx, history)


def _heuristic_replan(goal, failed_spec, evidence, ctx, history=None) -> Dict[str, Any]:
    """Apply the most common fixes when the LLM can't reason for us:
      1. If the objective was unreadable → switch to a readable observable.
      2. If variables hugged bounds → widen them.
      3. Otherwise switch solver method."""
    observables = ctx.get("observables", [])
    variables = failed_spec.get("variables") or ctx.get("variables", [])

    # Fix 1: unreadable objective → first readable observable
    old_obj = failed_spec.get("objective", {})
    obj_unreadable = (
        evidence.get("error_code") in ("OBJECTIVE_NOT_MEASURABLE",
                                        "PREFLIGHT_FAILED")
        or evidence.get("best_objective") is None
    )
    if obj_unreadable and observables:
        # Prefer an energy/duty observable for "minimise" goals
        goal_lc = goal.lower()
        chosen = None
        if any(k in goal_lc for k in ("energy", "duty", "heat", "consumption")):
            chosen = next((o for o in observables if "heatduty" in o.lower()), None)
        if chosen is None:
            chosen = observables[0]
        tag, prop = chosen.split(".", 1)
        is_min = any(k in goal_lc for k in ("min", "reduce", "decrease", "lower"))
        return {
            "replanned": True,
            "feasible": True,
            "diagnosis": "Original objective was unreadable on this flowsheet; "
                         "switched to a measurable observable.",
            "new_spec": {
                "success": True,
                "objective": {"type": "variable", "tag": tag, "property": prop},
                "variables": variables,
                "method": "simplex",
                "minimize": is_min,
                "_replanned": True,
            },
            "rationale": f"Objective changed to readable '{chosen}'.",
            "_via": "heuristic",
        }

    # Fix 2: widen bounds (variables likely hugged them).
    # On repeat attempts, don't re-issue an identical plan: rotate the solver
    # method and widen progressively further so each retry is materially
    # different from the ones in `history`.
    if variables:
        tried_methods = {(h.get("method") or "").lower()
                         for h in (history or [])}
        # Progressive widening: more failed attempts → wider search.
        n_prev = len(history or [])
        factor = 0.5 * (n_prev + 1)
        widened = []
        for v in variables:
            lo = float(v.get("lower", 0)); hi = float(v.get("upper", 1))
            span = hi - lo
            widened.append({**v,
                            "lower": round(lo - span * factor, 6),
                            "upper": round(hi + span * factor, 6)})
        # Pick a method we haven't tried yet, in escalating globality.
        method = next((m for m in ("de", "simplex", "lbfgs")
                       if m not in tried_methods), "de")
        return {
            "replanned": True,
            "feasible": True,
            "diagnosis": ("Search likely constrained by tight bounds; widened "
                          + (f"(attempt {n_prev + 1})." if n_prev else ".")),
            "new_spec": {
                "success": True,
                "objective": old_obj,
                "variables": widened,
                "method": method,
                "minimize": failed_spec.get("minimize", True),
                "_replanned": True,
            },
            "rationale": (f"Bounds widened ±{int(factor*100)}% and solver set to "
                          f"'{method}'."),
            "_via": "heuristic",
        }

    return {"replanned": False, "feasible": None,
            "diagnosis": "No alternative plan could be formed.",
            "_via": "heuristic"}
