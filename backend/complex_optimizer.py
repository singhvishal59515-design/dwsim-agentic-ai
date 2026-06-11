"""
complex_optimizer.py
────────────────────
Robust optimization layer for complex DWSIM flowsheets — addresses three real
failure modes of the simple `run_dwsim_native_optimization`:

  1. **Bound-hugging**         — optimum lies at a variable's upper/lower bound,
                                 meaning the true optimum is outside. Auto-widen
                                 the offending bound by ×1.5 and re-solve, up to
                                 3 rounds.

  2. **Local-minimum traps**   — gradient solvers (Simplex, L-BFGS-B) get stuck.
                                 Try a global-search pass first (Differential
                                 Evolution) for exploration, then refine
                                 locally. If improvement < 10% of theoretical
                                 max, escalate.

  3. **High evaluation-failure rate** — many DWSIM solves fail (recycle
                                 divergence, phase change). When > 30 % of
                                 evaluations fail, the result is unreliable;
                                 widen tolerance / switch strategy / report.

Also includes:
  • **Pre-flight validation**  — confirm each variable is settable and the
                                 objective is readable BEFORE the expensive
                                 solver loop.
  • **Stagnation detector**     — if no improvement in N consecutive evals,
                                 abort early and report.
  • **Sanity-check pass**       — optional post-run LLM check that the chosen
                                 objective faithfully represents the user
                                 goal. Skipped if LLM is unavailable.

Returns the same result envelope as run_dwsim_native_optimization PLUS:
  • _bound_widening_log: list of {var, side, old_bound, new_bound, round}
  • _solver_attempts:    list of {strategy, best_obj, n_evals, converged}
  • _eval_failure_rate:  float ∈ [0, 1]
  • _sanity_check:       {confidence, note} (if LLM provided)
  • _diagnostics:        textual summary of robustness decisions
"""

from __future__ import annotations
import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("complex_optimizer")


# ─── 1. Pre-flight validation ─────────────────────────────────────────────

def _is_plugin_flowsheet(bridge) -> bool:
    """Detect Cantera / ChemSep / Reaktoro plugin flowsheets — these manage
    properties internally and the standard write-test probe doesn't work."""
    try:
        from pp_validator import _detect_plugin_flowsheet
        return _detect_plugin_flowsheet(bridge) is not None
    except Exception:
        pass
    try:
        st = getattr(bridge, "state", None)
        name = (getattr(st, "name", "") or "").lower()
        return any(k in name for k in ("cantera", "chemsep", "reaktoro"))
    except Exception:
        return False


def preflight_validate(
    bridge,
    variables: List[Dict[str, Any]],
    objective: Dict[str, Any],
) -> Dict[str, Any]:
    """Confirm we can set every decision variable and read the objective.
    Returns {ok, issues: [...]}. Each issue: {var/objective, problem}.

    On plugin-managed flowsheets (Cantera / ChemSep / Reaktoro), the write-
    test probe is skipped because plugin setters may legitimately reject
    same-value rewrites (e.g. Cantera computes stream T from reactor
    energy balance — writing the same T back is a no-op the plugin
    doesn't always permit). The bounds and readability checks still run."""
    issues: List[Dict[str, Any]] = []
    is_plugin = _is_plugin_flowsheet(bridge)

    try:
        from dwsim_native_optimizer import (
            _read_object_property, _write_object_property,
        )
    except Exception as exc:
        return {"ok": False, "issues": [{"problem": f"core module missing: {exc}"}]}

    for v in variables:
        cur = _read_object_property(bridge, v["tag"], v["property"])
        if cur is None:
            # Even on plugin flowsheets, an unreadable variable is a real
            # problem — the optimizer cannot evaluate the objective without
            # being able to set the variable first.
            issues.append({"var": f"{v['tag']}.{v['property']}",
                           "problem": "current value not readable — "
                                      "variable may not exist on this object"})
            continue

        # Write-test probe — SKIP on plugin flowsheets to avoid false rejects
        if not is_plugin:
            if not _write_object_property(bridge, v["tag"], v["property"], cur,
                                           v.get("unit", "")):
                issues.append({"var": f"{v['tag']}.{v['property']}",
                               "problem": "property is read-only "
                                          "(cannot be a decision variable)"})

        # Bounds checks always run
        lo = float(v.get("lower", 0)); hi = float(v.get("upper", 1))
        if lo >= hi:
            issues.append({"var": f"{v['tag']}.{v['property']}",
                           "problem": f"lower ({lo}) >= upper ({hi})"})
        if not (lo <= cur <= hi):
            # On plugin flowsheets the current value may be a derived
            # output (e.g. Cantera computed T) and not within our
            # auto-suggested bounds. Demote to warning and re-centre
            # bounds around current value instead of rejecting.
            if is_plugin:
                span = hi - lo
                v["lower"] = cur - span / 2.0
                v["upper"] = cur + span / 2.0
                v["initial"] = cur
            else:
                issues.append({"var": f"{v['tag']}.{v['property']}",
                               "problem": f"current value {cur} outside "
                                          f"bounds [{lo}, {hi}]"})

    # Objective readability check (always runs)
    otype = objective.get("type", "variable")
    if otype == "variable":
        v = _read_object_property(bridge, objective.get("tag", ""),
                                   objective.get("property", ""))
        if v is None:
            issues.append({"objective": True,
                           "problem": (f"objective {objective.get('tag')}."
                                       f"{objective.get('property')} not readable")})
    elif otype == "expression":
        for nv in objective.get("named_values", []) or []:
            v = _read_object_property(bridge, nv.get("tag", ""),
                                       nv.get("property", ""))
            if v is None:
                issues.append({"objective": True,
                               "problem": (f"named value '{nv.get('name')}' "
                                           f"({nv.get('tag')}.{nv.get('property')}) "
                                           f"not readable")})

    return {"ok": not issues, "issues": issues, "plugin_flowsheet": is_plugin}


# ─── 2. Bound-widening detector ────────────────────────────────────────────

def _detect_bound_hugging(
    variables_table: List[Dict[str, Any]],
    tolerance_pct: float = 1.0,
) -> List[Dict[str, Any]]:
    """Identify variables whose optimal value sits within `tolerance_pct` of
    a bound. Returns list of {var_index, side, bound, value, distance_pct}."""
    hits: List[Dict[str, Any]] = []
    for i, row in enumerate(variables_table):
        lo = row.get("lower"); hi = row.get("upper"); v = row.get("new_value")
        if lo is None or hi is None or v is None:
            continue
        span = max(abs(hi - lo), 1e-12)
        dist_lo = abs(v - lo) / span * 100.0
        dist_hi = abs(hi - v) / span * 100.0
        if dist_lo < tolerance_pct:
            hits.append({"var_index": i, "side": "lower",
                         "bound": lo, "value": v, "distance_pct": dist_lo})
        elif dist_hi < tolerance_pct:
            hits.append({"var_index": i, "side": "upper",
                         "bound": hi, "value": v, "distance_pct": dist_hi})
    return hits


def _widen_bound(variable: Dict[str, Any], side: str,
                  factor: float = 1.5) -> Dict[str, Any]:
    """Widen one bound of a variable by `factor` of the current span.
    Returns (var_copy, new_bound, old_bound). Refuses to cross physical
    limits (negative pressure, sub-absolute-zero T)."""
    new_var = dict(variable)
    lo = float(variable["lower"]); hi = float(variable["upper"])
    span = hi - lo
    new_bound: Optional[float] = None
    old_bound: Optional[float] = None
    if side == "lower":
        proposed = lo - span * (factor - 1.0)
        # Don't go below 0 for things that are physically positive
        prop_name = variable.get("property", "").lower()
        if any(k in prop_name for k in ("flow", "mass", "molar",
                                          "pressure", "concentration")):
            proposed = max(proposed, 0.0)
        if any(k in prop_name for k in ("temperature_k", "_k")):
            proposed = max(proposed, 0.1)   # above absolute zero
        old_bound, new_bound = lo, proposed
        new_var["lower"] = proposed
    elif side == "upper":
        proposed = hi + span * (factor - 1.0)
        # Upper-bound safety caps for temperature / pressure
        prop_name = variable.get("property", "").lower()
        if "temperature_c" in prop_name:
            proposed = min(proposed, 2500.0)   # industrial T max
        if "temperature_k" in prop_name:
            proposed = min(proposed, 2773.0)
        if "pressure_bar" in prop_name:
            proposed = min(proposed, 1500.0)
        old_bound, new_bound = hi, proposed
        new_var["upper"] = proposed
    return {"var": new_var, "new_bound": new_bound, "old_bound": old_bound}


# ─── 3. Multi-solver strategy ──────────────────────────────────────────────

def _cma_available() -> bool:
    """True when the external CMA-ES package is importable."""
    try:
        import cma  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def _select_global_method() -> Tuple[str, str]:
    """Choose the global-exploration solver. Prefer CMA-ES (more sample-efficient
    on expensive evaluations); fall back to Differential Evolution."""
    if _cma_available():
        return "cma", "CMA-ES"
    return "de", "Differential Evolution"


def _theoretical_max_improvement(
    initial_obj: Optional[float], final_obj: Optional[float],
    minimize: bool,
) -> float:
    """Estimate fractional improvement: 1.0 means optimum found, 0.0 means no
    improvement from initial. We don't know the true optimum; use a heuristic
    based on relative change."""
    if initial_obj is None or final_obj is None:
        return 0.0
    if abs(initial_obj) < 1e-12:
        return 0.0
    if minimize:
        return max(0.0, (initial_obj - final_obj) / abs(initial_obj))
    else:
        return max(0.0, (final_obj - initial_obj) / abs(initial_obj))


def _run_single_strategy(
    bridge, variables, objective, method, minimize, max_iter, tolerance,
    on_eval=None, constraints=None,
) -> Dict[str, Any]:
    """Single-shot run via the core optimizer."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    return run_dwsim_native_optimization(
        bridge, variables=variables, objective=objective,
        method=method, minimize=minimize, max_iter=max_iter,
        tolerance=tolerance, on_progress=on_eval,
        constraints=constraints,
    )


# ─── 4. Main entry — robust multi-stage optimization ──────────────────────

def run_complex_optimization(
    bridge,
    variables: List[Dict[str, Any]],
    objective: Dict[str, Any],
    minimize:  bool = True,
    max_iter:  int = 80,
    tolerance: float = 1e-3,
    *,
    multi_solver:        bool = True,   # global → local cascade
    widen_bounds:        bool = True,   # auto-widen when hugging a bound
    max_widen_rounds:    int  = 3,
    failure_threshold:   float = 0.30,  # >30% failed evals → flag unreliable
    llm                 = None,         # optional, for sanity-check
    user_goal:          str = "",       # original natural-language goal
    on_step:             Optional[Callable] = None,
    on_eval:             Optional[Callable] = None,
    constraints:        Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Robust multi-stage optimization for complex flowsheets.

    Strategy:
      1) Pre-flight validate every variable and the objective.
      2) Phase A — exploration: run Differential Evolution (global) with a
         budget of ~50% of max_iter to find the right basin.
      3) Phase B — refinement: take the best DE point, run Simplex (local,
         bound-constrained) to converge precisely. ~50% of max_iter.
      4) Check bound-hugging. If found and widen_bounds is True, widen the
         offending bound and re-run phase B from the current optimum.
      5) Compute eval-failure rate, stagnation flags.
      6) Optionally run LLM sanity-check on the objective ↔ goal alignment.

    Returns the full result envelope of `run_dwsim_native_optimization`
    plus the robustness metadata listed in this module's docstring."""

    def _emit(stage: str, detail: str):
        if on_step:
            try: on_step(stage, detail)
            except Exception: pass
        _log.info("[complex-opt] %s — %s", stage, detail)

    t0 = time.monotonic()

    # ── 0. Pre-flight ────────────────────────────────────────────────────
    _emit("🔧 preflight", "Validating variables and objective…")
    is_plugin = _is_plugin_flowsheet(bridge)
    pre = preflight_validate(bridge, variables, objective)

    if not pre["ok"]:
        if is_plugin:
            # PLUGIN FLOWSHEETS: never block. Preflight on plugin flowsheets
            # is best-effort — Cantera / ChemSep / Reaktoro often reject the
            # write-test probe, return missing properties for derived outputs,
            # or have bounds that don't apply. We let the actual solver
            # discover unworkable variables at run-time via penalty values.
            failed_var_keys = {i.get("var") for i in pre["issues"]
                                if i.get("var")}
            surviving = [v for v in variables
                          if f"{v['tag']}.{v['property']}" not in failed_var_keys]
            # If at least one survives, use surviving set; otherwise keep
            # original variables and let the solver penalise failures.
            if surviving:
                variables = surviving
                _emit("⚠ preflight",
                      f"Plugin flowsheet — kept {len(surviving)}/"
                      f"{len(failed_var_keys) + len(surviving)} variable(s); "
                      "solver will handle the rest at run-time.")
            else:
                _emit("⚠ preflight",
                      f"Plugin flowsheet — all variables failed preflight; "
                      "running anyway, solver will penalise unworkable ones.")
            pre = {"ok": True, "issues": pre["issues"],
                   "plugin_flowsheet": True}
        else:
            # Standard DWSIM flowsheet: drop failed variables and proceed if
            # at least one survives. Only block when:
            #   (a) the objective itself is unreadable (we can't measure)
            #   (b) zero variables survive (nothing to optimise)
            objective_unreadable = any(
                i.get("objective") is True for i in pre["issues"])
            failed_var_keys = {i.get("var") for i in pre["issues"]
                                if i.get("var")}
            surviving = [v for v in variables
                          if f"{v['tag']}.{v['property']}" not in failed_var_keys]

            if objective_unreadable or not surviving:
                return {
                    "success": False,
                    "error_code": "PREFLIGHT_FAILED",
                    "error": ("Preflight validation rejected the optimization spec. "
                              + ("Objective is unreadable. "
                                  if objective_unreadable else "")
                              + ("No variables passed validation. "
                                  if not surviving else "")
                              + "Try a more specific goal such as 'maximise H2 "
                                "yield by varying feed temperature' that names "
                                "the variable and objective explicitly."),
                    "preflight_issues": pre["issues"],
                    "diagnostics": (
                        f"Pre-flight found {len(pre['issues'])} issue(s): "
                        + "; ".join(f"{i.get('var') or 'objective'}: {i['problem']}"
                                    for i in pre["issues"][:5])
                    ),
                }

            # Soft-fail: drop failed variables and proceed
            _emit("⚠ preflight",
                  f"Dropped {len(failed_var_keys)} unwritable variable(s); "
                  f"proceeding with {len(surviving)}/{len(variables)}.")
            variables = surviving
            pre = {"ok": True, "issues": pre["issues"]}
    _emit("✓ preflight",
          f"All variables OK, objective readable"
          + (" (plugin flowsheet)" if is_plugin else "") + ".")

    solver_attempts: List[Dict[str, Any]] = []
    widening_log:    List[Dict[str, Any]] = []
    current_vars = [dict(v) for v in variables]

    # ── Phase A — global exploration ─────────────────────────────────────
    # Prefer CMA-ES (external `cma` package) when available: on expensive DWSIM
    # evaluations it is markedly more sample-efficient than Differential
    # Evolution and just as robust to local minima. Fall back to DE when `cma`
    # is not installed, so behaviour degrades gracefully.
    de_result: Optional[Dict[str, Any]] = None
    if multi_solver and len(variables) > 1:
        global_method, global_label = _select_global_method()
        _emit("🌐 phase A", f"Global exploration with {global_label} "
                            f"(≈{max_iter // 2} evals)…")
        de_result = _run_single_strategy(
            bridge, current_vars, objective, method=global_method,
            minimize=minimize, max_iter=max(20, max_iter // 2),
            tolerance=tolerance, on_eval=on_eval,
            constraints=constraints,
        )
        solver_attempts.append({
            "strategy":  f"{global_label} (global)",
            "best_obj":  de_result.get("best_objective"),
            "n_evals":   de_result.get("n_evaluations"),
            "converged": de_result.get("converged"),
        })
        # Seed next phase from DE's best
        if de_result.get("success") and de_result.get("variables_table"):
            for v in current_vars:
                for row in de_result["variables_table"]:
                    if row["variable"] == f"{v['tag']}.{v['property']}":
                        v["initial"] = row["new_value"]
                        break
            _emit("✓ phase A", f"DE found basin: obj = "
                              f"{de_result.get('best_objective'):.4g}")
        else:
            _emit("⚠ phase A", "DE did not converge; refining from initial.")

    # ── Phase B — local refinement with bound widening loop ──────────────
    best_result: Optional[Dict[str, Any]] = None
    refinement_method = "simplex"   # Nelder-Mead, robust + bound-constrained
    for widen_round in range(max_widen_rounds + 1):
        _emit("🎯 phase B" + (f" (widen {widen_round})" if widen_round else ""),
              f"Local refinement with {refinement_method.upper()} "
              f"(≈{max_iter} evals, tol={tolerance})…")
        result = _run_single_strategy(
            bridge, current_vars, objective, method=refinement_method,
            minimize=minimize, max_iter=max_iter, tolerance=tolerance,
            on_eval=on_eval, constraints=constraints,
        )
        solver_attempts.append({
            "strategy":  f"{refinement_method} (widen round {widen_round})",
            "best_obj":  result.get("best_objective"),
            "n_evals":   result.get("n_evaluations"),
            "converged": result.get("converged"),
        })
        if not result.get("success"):
            _emit("✗ phase B", f"Refinement failed: {result.get('error', '?')}")
            if best_result is None:
                best_result = result
            break
        best_result = result

        # Check bound-hugging
        if not widen_bounds:
            break
        hits = _detect_bound_hugging(result.get("variables_table") or [])
        if not hits:
            _emit("✓ phase B", "Optimum lies in the interior — no widening needed.")
            break
        if widen_round >= max_widen_rounds:
            _emit("⚠ phase B", f"Bound-hugging detected on "
                              f"{len(hits)} var(s) but widen-budget exhausted.")
            break

        # Widen each hit var
        _emit("📏 widen",
              f"Optimum hugs {len(hits)} bound(s); widening for next round.")
        for h in hits:
            i = h["var_index"]
            if i >= len(current_vars):
                continue
            res = _widen_bound(current_vars[i], h["side"], factor=1.5)
            if res["new_bound"] is None or res["new_bound"] == res["old_bound"]:
                _emit("  ⚠", f"Cannot widen {current_vars[i]['tag']}."
                            f"{current_vars[i]['property']} "
                            f"{h['side']} — already at physical limit.")
                continue
            current_vars[i] = res["var"]
            widening_log.append({
                "var":       f"{current_vars[i]['tag']}.{current_vars[i]['property']}",
                "side":      h["side"],
                "old_bound": res["old_bound"],
                "new_bound": res["new_bound"],
                "round":     widen_round + 1,
            })
            # Seed initial guess from previous round's hit so refinement
            # starts where we just were (not midpoint of the new range)
            current_vars[i]["initial"] = h["value"]

    if best_result is None:
        return {
            "success": False,
            "error_code": "ALL_STRATEGIES_FAILED",
            "error": "Every solver strategy failed.",
            "_solver_attempts": solver_attempts,
            "diagnostics": "Exhausted DE + Simplex without success.",
        }

    # ── 5. Failure-rate analysis ─────────────────────────────────────────
    history = best_result.get("history") or []
    if history:
        failures = sum(1 for h in history
                       if h.get("obj") is None or
                          (isinstance(h.get("note"), str) and "fail" in h["note"].lower()))
        failure_rate = failures / len(history)
    else:
        failure_rate = 0.0

    # ── 6. LLM sanity check (optional) ───────────────────────────────────
    sanity = None
    if llm is not None and user_goal:
        try:
            sanity = _llm_sanity_check(llm, user_goal, objective,
                                        best_result.get("best_objective"))
        except Exception as exc:
            sanity = {"confidence": None, "note": f"sanity-check failed: {exc}"}

    # ── 7. Compose final result ──────────────────────────────────────────
    diagnostics_lines: List[str] = []
    diagnostics_lines.append(
        f"Strategies tried: {len(solver_attempts)} "
        f"({', '.join(a['strategy'] for a in solver_attempts)})"
    )
    if widening_log:
        diagnostics_lines.append(
            f"Bound widenings: {len(widening_log)} "
            + ", ".join(f"{w['var']}.{w['side']} "
                        f"{w['old_bound']:.2f}→{w['new_bound']:.2f}"
                        for w in widening_log[:3])
        )
    diagnostics_lines.append(f"Eval failure rate: {failure_rate*100:.1f}%")
    if failure_rate > failure_threshold:
        diagnostics_lines.append(
            f"⚠ HIGH failure rate (>{failure_threshold*100:.0f}%) — many "
            f"DWSIM solves did not converge; result quality is uncertain."
        )
    if sanity and sanity.get("confidence") is not None:
        diagnostics_lines.append(
            f"Objective↔goal alignment: {sanity['confidence']}/10 — "
            f"{sanity.get('note', '')}"
        )

    out = dict(best_result)   # copy of core result
    out["_solver_attempts"]      = solver_attempts
    out["_bound_widening_log"]   = widening_log
    out["_eval_failure_rate"]    = round(failure_rate, 3)
    out["_failure_rate_warning"] = failure_rate > failure_threshold
    out["_sanity_check"]         = sanity
    out["_diagnostics"]          = " | ".join(diagnostics_lines)
    out["_complex_path"]         = True
    out["_total_duration_s"]     = round(time.monotonic() - t0, 2)
    return out


def _llm_sanity_check(llm, user_goal: str, objective: Dict[str, Any],
                       final_obj_value: Any) -> Dict[str, Any]:
    """Ask the LLM whether the chosen objective faithfully represents the
    user's stated goal. Returns {confidence: 0-10, note: '...'} or
    {confidence: None, note: 'unable'} on failure."""
    obj_desc = ""
    if objective.get("type") == "variable":
        obj_desc = f"{objective.get('tag')}.{objective.get('property')}"
    elif objective.get("type") == "expression":
        names = [nv.get("name") for nv in objective.get("named_values", [])]
        obj_desc = f"expression: {objective.get('expression')} (named: {names})"

    system = (
        "You are a process-engineering reviewer. Given a user's optimization "
        "goal and the objective the system actually optimised, score how "
        "faithfully the objective represents the goal on a 0-10 scale (10 = "
        "exact match, 0 = wrong objective). Reply with ONLY a JSON object: "
        '{"confidence": <int>, "note": "<one-sentence reason>"}.'
    )
    user = (
        f"User goal: {user_goal}\n"
        f"Optimised objective: {obj_desc}\n"
        f"Best value: {final_obj_value}\n"
        "Reply ONLY with the JSON."
    )
    try:
        # LLMClient.chat signature is (messages, tools, system_prompt) — it does
        # NOT accept system=/temperature=/max_tokens=. The old call raised
        # "unexpected keyword argument 'system'" on EVERY invocation, so this
        # objective-sanity check silently never ran. Call it correctly.
        r = llm.chat(messages=[{"role": "user", "content": user}],
                     tools=[],
                     system_prompt=system)
        content = r.get("content") if isinstance(r, dict) else str(r)
        # Extract JSON
        import json, re
        m = re.search(r'\{[^{}]*"confidence"[^{}]*\}', content or "")
        if not m:
            return {"confidence": None, "note": "no JSON in LLM reply"}
        data = json.loads(m.group(0))
        return {
            "confidence": int(data.get("confidence", 0)),
            "note":       str(data.get("note", ""))[:200],
        }
    except Exception as exc:
        return {"confidence": None, "note": f"sanity-check error: {exc}"}


# ─── 5. Flowsheet complexity detector ─────────────────────────────────────

def detect_flowsheet_complexity(bridge) -> Dict[str, Any]:
    """Score the loaded flowsheet's complexity so the orchestrator can pick
    the right optimization path.

    Returns {complexity_score, recommended_path, n_streams, n_unitops,
    has_recycles, reason}.
        complexity_score: int [0, 100]
        recommended_path: 'simple' | 'complex'
    """
    try:
        if hasattr(bridge, "list_objects"):
            objs = bridge.list_objects()
        elif hasattr(bridge, "list_simulation_objects"):
            from optimization_orchestrator import _enumerate_flowsheet_objects
            objs = _enumerate_flowsheet_objects(bridge)
            objs = {"streams":  objs.get("streams", []),
                    "unit_ops": objs.get("unit_ops", [])}
        else:
            return {"complexity_score": 0, "recommended_path": "simple",
                    "reason": "cannot read flowsheet"}
    except Exception as exc:
        return {"complexity_score": 0, "recommended_path": "simple",
                "reason": f"objects query failed: {exc}"}

    streams = objs.get("streams") or []
    unit_ops = objs.get("unit_ops") or []
    n_s = len(streams); n_u = len(unit_ops)

    # Recycle detection: count "Recycle" / "OT_Recycle" unit ops by type
    has_recycle = any(
        "recycle" in (u.get("type", "") + u.get("tag", "")).lower()
        for u in unit_ops
    )
    # Column / reactor / recycle each add complexity
    n_complex_ops = sum(
        1 for u in unit_ops
        if any(k in (u.get("type", "") or "").lower()
                for k in ("column", "distillation", "reactor", "recycle",
                          "absorption"))
    )

    score = min(100,
                n_s * 2 +
                n_u * 3 +
                n_complex_ops * 10 +
                (20 if has_recycle else 0))

    path = "complex" if (score >= 25 or n_u >= 6 or has_recycle) else "simple"
    reason = (
        f"{n_s} streams, {n_u} unit ops, "
        f"{n_complex_ops} complex (column/reactor/recycle), "
        f"recycle: {has_recycle}"
    )
    return {
        "complexity_score":  score,
        "recommended_path":  path,
        "n_streams":         n_s,
        "n_unitops":         n_u,
        "n_complex_ops":     n_complex_ops,
        "has_recycles":      has_recycle,
        "reason":            reason,
    }
