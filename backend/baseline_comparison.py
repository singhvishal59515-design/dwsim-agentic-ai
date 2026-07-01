"""
baseline_comparison.py
──────────────────────
Multi-method baseline comparison in the style of Tian et al.'s Table 1
(arXiv:2601.06776, 2026) — the one piece of their evaluation structure the
project lacked. Tian compare their system against end-to-end LLMs (GPT-4o,
Claude), external multi-agent frameworks (Swarm, AutoGen, CrewAI, MetaGPT), and
expert manual design, on one rubric (five dimensions + overall S + SCR + time).

This harness assembles the same kind of table honestly, from what the project can
actually run:

  • REAL rows measured on the project's live 25-task benchmark: the full agentic
    system at its real pass rate (24% strict) vs a direct LLM with no tools, which
    is structurally 0% (it cannot call solve or read a stream) — the "Ours vs
    end-to-end LLM" contrast in Tian's Table 1. NOTE: we do NOT use the
    component-ablation pass rates (68%); those are a smoke-run pipeline check
    (sub-second per task, no LLM/solver executed) and contradict the live
    benchmark, so treating them as measured performance would be wrong.
  • A generic LIVE runner that scores any set of method callables on the shared
    25-task set, using process_evaluation (the 5-dimension rubric, Tian Eq. 1) +
    the SCR + wall time. Fully unit-tested with mock methods; populating it with
    fresh agent runs is throughput-gated, not fabricated.
  • Honest NOT-EVALUATED placeholders for the external frameworks and the expert
    baseline, each with the reason it is out of reach here (framework
    integration, human experts, LLM quota) — so the comparison mirrors Tian's
    structure without pretending to numbers we did not measure.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

_HERE = os.path.dirname(os.path.abspath(__file__))


# ── method-level result row ──────────────────────────────────────────────────
@dataclass
class MethodResult:
    name: str
    source: str                       # "ablation_run" | "live" | "not_evaluated"
    n_tasks: Optional[int] = None
    pass_rate_pct: Optional[float] = None
    scr_pct: Optional[float] = None   # simulation convergence rate
    mean_time_s: Optional[float] = None
    dims_100: Optional[Dict[str, float]] = None   # 5-dim rubric means (×100)
    note: str = ""


# ── per-task result for the live runner ──────────────────────────────────────
@dataclass
class RunResult:
    task_id: str
    passed: bool
    converged: bool
    tool_calls: int
    time_s: float
    design_facts: Optional[Dict[str, Any]] = None   # for rubric scoring


# ── REAL rows from the project's ablation run ────────────────────────────────
# The ablation's Full-System and Direct-LLM conditions ARE the two methods Tian's
# Table 1 contrasts ("Ours" vs an end-to-end LLM), so we surface them as methods.
_ABLATION_TO_METHOD = {
    "Full System": "Full agentic system (this work)",
    "Direct LLM (No Tools)": "Direct LLM, no tools (end-to-end baseline)",
}


def real_method_rows(path: Optional[str] = None) -> List[MethodResult]:
    """The two methods this project can HONESTLY report: the full agentic system
    at its real live-benchmark pass rate, and a direct LLM with no tools which is
    structurally unable to operate the simulator (0%).

    We deliberately do NOT use the component-ablation pass rates here: those are a
    smoke-run pipeline check (sub-second per task — no LLM or solver executed) and
    contradict the live benchmark (they report the full system at 68% where the
    live benchmark measures 24%). Using them would misrepresent performance."""
    path = path or os.path.join(_HERE, "benchmark_results.json")
    strict = executed = None
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            from benchmark_error_analysis import analyze
            a = analyze(data.get("results", data))
            strict, executed = a["strict_pass_rate_pct"], a["executed_pass_rate_pct"]
        except Exception:
            pass
    note = (f"measured on the live 25-task benchmark: {strict}% strict, "
            f"{executed}% over executed tasks" if strict is not None
            else "measured on the live 25-task benchmark")
    return [
        MethodResult(name="Full agentic system (this work)", source="measured",
                     n_tasks=25, pass_rate_pct=strict, note=note),
        MethodResult(name="Direct LLM, no tools (end-to-end baseline)",
                     source="structural", pass_rate_pct=0.0,
                     note="structural — with no tools it cannot call solve or "
                          "read a stream, so nothing executes"),
    ]


# ── honest placeholders for baselines out of reach here ──────────────────────
def external_baseline_placeholders() -> List[MethodResult]:
    reasons = {
        "GPT-4o / Claude (end-to-end JSON)":
            "an end-to-end-LLM baseline; the Direct-LLM row above is this "
            "project's measured equivalent",
        "Swarm / AutoGen / CrewAI / MetaGPT":
            "external multi-agent frameworks; require framework integration + "
            "LLM quota to run on the same tasks",
        "Expert manual design":
            "requires recruiting chemical-engineering experts to design and "
            "validate each task by hand",
    }
    return [MethodResult(name=n, source="not_evaluated", note=r)
            for n, r in reasons.items()]


# ── generic LIVE runner (scored on the 5-dim rubric + SCR + time) ────────────
def run_live_comparison(
    methods: Dict[str, Callable[[Dict[str, Any]], RunResult]],
    tasks: Sequence[Dict[str, Any]],
) -> List[MethodResult]:
    """Run each named method over the shared task set and aggregate. Each method
    is a callable task -> RunResult. Scoring reuses process_evaluation so the
    rubric and SCR are identical to the rest of the evaluation."""
    from process_evaluation import aggregate, simulation_convergence_rate
    out: List[MethodResult] = []
    for name, fn in methods.items():
        runs = [fn(t) for t in tasks]
        n = len(runs) or 1
        passed = sum(1 for r in runs if r.passed)
        recs = [{"converged": r.converged, **(r.design_facts or {})} for r in runs]
        dims = None
        if any(r.design_facts for r in runs):
            agg = aggregate(recs)
            dims = agg.get("dimension_means_100")
        out.append(MethodResult(
            name=name, source="live", n_tasks=len(runs),
            pass_rate_pct=round(passed / n * 100, 1),
            scr_pct=simulation_convergence_rate(recs),
            mean_time_s=round(sum(r.time_s for r in runs) / n, 2),
            dims_100=dims, note="fresh live run"))
    return out


# ── assemble + render ────────────────────────────────────────────────────────
def compare(live: Optional[List[MethodResult]] = None) -> List[MethodResult]:
    rows: List[MethodResult] = []
    rows.extend(live or [])
    rows.extend(real_method_rows())
    rows.extend(external_baseline_placeholders())
    return rows


def to_markdown(rows: List[MethodResult]) -> str:
    L: List[str] = []
    w = L.append
    w("# Baseline Comparison")
    w("")
    w("A multi-method comparison in the structure of Tian et al. "
      "(arXiv:2601.06776, 2026), Table 1, assembled from what this project can "
      "actually measure. The headline contrast — a full tool-using agentic system "
      "versus a direct LLM with no tools — uses the REAL live 25-task benchmark "
      "(24% strict) for the full system and a structural 0% for the tool-less LLM. "
      "We deliberately do NOT use the component-ablation pass rates (68%): those "
      "are a smoke-run pipeline check, not live-agent performance, and contradict "
      "the live benchmark. External frameworks and the expert baseline are listed "
      "honestly as not evaluated, with the reason.")
    w("")
    w("| Method | Pass rate | SCR | Mean time (s) | Source |")
    w("|---|--:|--:|--:|---|")
    def fmt(v, suffix=""):
        return f"{v:g}{suffix}" if isinstance(v, (int, float)) else "—"
    for r in rows:
        src = {"measured": "measured (live benchmark)", "structural": "structural",
               "live": "live run", "not_evaluated": "not evaluated",
               "ablation_run": "ablation run"}.get(r.source, r.source)
        w(f"| {r.name} | {fmt(r.pass_rate_pct, '%')} | {fmt(r.scr_pct, '%')} "
          f"| {fmt(r.mean_time_s)} | {src} |")
    w("")
    # 5-dimension sub-table only if any live method was rubric-scored
    scored = [r for r in rows if r.dims_100]
    if scored:
        from process_evaluation import DIMENSIONS
        w("Five-dimension rubric means (×100; Tian et al. Eq. 1), where measured:")
        w("")
        w("| Method | " + " | ".join(d.capitalize() for d in DIMENSIONS) + " |")
        w("|---|" + "--:|" * len(DIMENSIONS))
        for r in scored:
            w(f"| {r.name} | " +
              " | ".join(f"{r.dims_100.get(d, float('nan')):.1f}" for d in DIMENSIONS) +
              " |")
        w("")
    w("**Reading.** On the live 25-task benchmark the full agentic system reaches "
      "24% strict (31.6% over executed tasks) where a direct LLM with no tools "
      "reaches 0% — with no tools it cannot operate the simulator at all. The "
      "entire capability comes from the tool-calling + convergence loop, not the "
      "bare model, which is the gap Tian et al. report between their system and an "
      "end-to-end LLM. (The 68% component-ablation figure is NOT used here: it is a "
      "smoke-run pipeline check, not live-agent performance, and contradicts this "
      "benchmark.) Fresh rubric-scored runs and the external-framework rows are "
      "throughput-gated; the harness scores any method callable on the shared "
      "25-task set so those rows populate without new code when quota is available.")
    w("")
    w("**Not evaluated, and why:**")
    for r in rows:
        if r.source == "not_evaluated":
            w(f"- {r.name} — {r.note}.")
    return "\n".join(L) + "\n"


def main() -> int:
    rows = compare()
    md = to_markdown(rows)
    out = os.path.join(_HERE, "BASELINE_COMPARISON.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(md.encode("ascii", "replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
