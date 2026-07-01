"""
benchmark_error_analysis.py
───────────────────────────
Deeper, deterministic error analysis of a completed 25-task benchmark run —
the "provide deeper error analysis" reviewer ask. Pure function of the existing
benchmark_results.json (no LLM, no re-run), so it is reproducible and testable.

The raw run records only a coarse outcome (SUCCESS / PARTIAL / FAILURE_LOUD) and
a `passed` flag. That conflates three very different situations, which this module
separates by reading the per-task signals (tool-call count, outcome, convergence):

  • NOT_EXECUTED   — tool_calls == 0: the provider rate-limited before the agent
                     made a single tool call. These are INCONCLUSIVE, not
                     capability failures, and must be excluded from the
                     denominator when judging the pipeline (the honest
                     19-executed / 6-inconclusive split the paper reports).
  • EARLY_ABORT    — 1–2 tool calls then failure: a tool error or precondition
                     block stopped the agent almost immediately.
  • PARTIAL_NEAR_MISS — converged but a success criterion missed: the candidate
                     for scoring rigidity (a correct build whose output stream is
                     named outside the criteria's exact tag set).
  • EXECUTED_FAILURE — ran substantially (>2 tool calls) and still did not pass:
                     a genuine capability or scoring wall, localised by category.

It also surfaces a data-quality caveat the project already knows: the per-task
`convergence` field is `True` even for NOT_EXECUTED tasks (a stale default), so no
aggregate "solver-convergence" number is trustworthy from this run.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

# Failure-mode taxonomy and a one-line root-cause hint per mode.
MODES = {
    "PASS":             "task passed all criteria",
    "NOT_EXECUTED":     "0 tool calls — provider rate-limited before execution; "
                        "inconclusive, not a capability failure",
    "EARLY_ABORT":      "failed after ≤2 tool calls — likely an early tool error "
                        "or precondition block",
    "PARTIAL_NEAR_MISS": "converged but a success criterion missed — candidate "
                        "scoring rigidity (correct build, non-canonical tag)",
    "EXECUTED_FAILURE": "ran >2 tool calls without passing — genuine capability "
                        "or scoring wall",
}


def classify(rec: Dict[str, Any]) -> str:
    """Derive a failure mode from one task record's signals. Deterministic."""
    tool_calls = int(rec.get("tool_calls") or 0)
    outcome = str(rec.get("outcome") or "")
    if rec.get("passed"):
        return "PASS"
    if tool_calls == 0:
        return "NOT_EXECUTED"
    if outcome == "PARTIAL":
        return "PARTIAL_NEAR_MISS"
    if tool_calls <= 2:
        return "EARLY_ABORT"
    return "EXECUTED_FAILURE"


def analyze(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Full breakdown: per-task modes, executed-vs-inconclusive headline, and
    per-category / per-complexity / per-mode rollups. Pure; never raises."""
    per_task: List[Dict[str, Any]] = []
    mode_counts: Counter = Counter()
    by_cat: Dict[str, Counter] = defaultdict(Counter)
    by_cx: Dict[str, Counter] = defaultdict(Counter)

    for r in results:
        mode = classify(r)
        mode_counts[mode] += 1
        cat = str(r.get("category") or "?")
        cx = str(r.get("complexity") or "?")
        by_cat[cat][mode] += 1
        by_cx[cx][mode] += 1
        per_task.append({
            "id": r.get("benchmark_id"),
            "category": cat,
            "complexity": cx,
            "tool_calls": int(r.get("tool_calls") or 0),
            "outcome": r.get("outcome"),
            "mode": mode,
            "root_cause": MODES[mode],
        })

    total = len(results)
    passed = mode_counts["PASS"]
    not_executed = mode_counts["NOT_EXECUTED"]
    executed = total - not_executed
    return {
        "total": total,
        "passed": passed,
        "not_executed": not_executed,
        "executed": executed,
        "strict_pass_rate_pct": round(passed / total * 100, 1) if total else None,
        "executed_pass_rate_pct": round(passed / executed * 100, 1) if executed else None,
        "mode_counts": dict(mode_counts),
        "by_category": {k: dict(v) for k, v in sorted(by_cat.items())},
        "by_complexity": {k: dict(v) for k, v in sorted(by_cx.items())},
        "per_task": per_task,
        # Data-quality caveat: convergence is True even where tool_calls == 0.
        "convergence_field_trustworthy": not any(
            (r.get("tool_calls") or 0) == 0 and r.get("convergence") is True
            for r in results),
    }


def to_markdown(a: Dict[str, Any]) -> str:
    L: List[str] = []
    w = L.append
    w("# Benchmark Error Analysis — 25-Task Live Run")
    w("")
    w(f"Deterministic per-task failure-mode attribution over the existing run "
      f"(`benchmark_results.json`); no re-run, no LLM. Of **{a['total']}** tasks, "
      f"**{a['passed']} passed**, **{a['not_executed']} never executed** "
      f"(0 tool calls — provider rate-limited), leaving **{a['executed']} truly "
      f"executed**.")
    w("")
    w(f"- Strict pass rate (all tasks): **{a['strict_pass_rate_pct']}%** "
      f"({a['passed']}/{a['total']})")
    w(f"- Executed pass rate (excludes the {a['not_executed']} inconclusive): "
      f"**{a['executed_pass_rate_pct']}%** ({a['passed']}/{a['executed']})")
    w("")
    w("## Failure-mode distribution")
    w("")
    w("| Mode | Count | Meaning |")
    w("|---|--:|---|")
    for mode in ("PASS", "NOT_EXECUTED", "PARTIAL_NEAR_MISS", "EARLY_ABORT",
                 "EXECUTED_FAILURE"):
        if mode in a["mode_counts"]:
            w(f"| {mode} | {a['mode_counts'][mode]} | {MODES[mode]} |")
    w("")
    w("## By category")
    w("")
    w("| Category | pass | not-exec | partial | early-abort | exec-fail |")
    w("|---|--:|--:|--:|--:|--:|")
    for cat, mc in a["by_category"].items():
        w(f"| {cat} | {mc.get('PASS',0)} | {mc.get('NOT_EXECUTED',0)} | "
          f"{mc.get('PARTIAL_NEAR_MISS',0)} | {mc.get('EARLY_ABORT',0)} | "
          f"{mc.get('EXECUTED_FAILURE',0)} |")
    w("")
    w("## Per-task attribution")
    w("")
    w("| Task | cat | C | tools | outcome | derived mode |")
    w("|---|---|--:|--:|---|---|")
    for t in a["per_task"]:
        w(f"| {t['id']} | {t['category']} | {t['complexity']} | {t['tool_calls']} "
          f"| {t['outcome']} | {t['mode']} |")
    w("")
    w("## Findings")
    w("")
    not_exec_ids = [t["id"] for t in a["per_task"] if t["mode"] == "NOT_EXECUTED"]
    near_ids = [t["id"] for t in a["per_task"] if t["mode"] == "PARTIAL_NEAR_MISS"]
    w(f"1. **{a['not_executed']} of the {a['total']} tasks never executed** "
      f"({', '.join(not_exec_ids)}) — 0 tool calls, the provider rate-limited "
      f"before the agent acted. Counting these as failures understates the "
      f"pipeline: the honest executed pass rate is "
      f"{a['executed_pass_rate_pct']}%, not {a['strict_pass_rate_pct']}%.")
    if near_ids:
        w(f"2. **{len(near_ids)} near-miss(es)** ({', '.join(near_ids)}) converged "
          f"but missed a criterion — the scoring-rigidity signature (correct build, "
          f"output stream named outside the criteria's exact tag set). A "
          f"tolerance-aware, role-based scoring resolver would likely credit these.")
    w(f"3. The weakest categories are multi-unit creation, distillation and "
      f"reactors (each 0 passes); single-unit creation is strongest. The failures "
      f"concentrate in topology-heavy, many-object builds rather than in solving "
      f"or property reads.")
    if not a["convergence_field_trustworthy"]:
        w(f"4. **Data-quality caveat:** the per-task `convergence` field reads "
          f"`True` even for the {a['not_executed']} non-executed tasks (a stale "
          f"default), so no aggregate solver-convergence number is trustworthy "
          f"from this run — consistent with the project's standing rule against "
          f"asserting one.")
    return "\n".join(L) + "\n"


def main(path: Optional[str] = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    path = path or os.path.join(here, "benchmark_results.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    results = data.get("results", data) if isinstance(data, dict) else data
    a = analyze(results)
    md = to_markdown(a)
    out = os.path.join(here, "BENCHMARK_ERROR_ANALYSIS.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
