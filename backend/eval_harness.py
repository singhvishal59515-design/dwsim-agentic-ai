"""
eval_harness.py
───────────────
Lightweight category-based eval harness — blueprint Phase 4.

Implements:
  • EvalTask with categories (template_base, template_variant, adversarial,
    out_of_corpus, regression).
  • Composable per-axis scorers (correctness, topology, efficiency, answer).
  • Run-once / run-suite entry points.
  • Diff-vs-baseline report that highlights regressions explicitly.

This is a *thin* harness — the heavy machinery (Anthropic LLM loop, DWSIM
runtime) is reused from agent_v2. The harness's job is to define what
"passing" means, score it consistently, and surface regressions cleanly.

Usage:
    from eval_harness import EvalTask, run_suite, write_report

    tasks = load_tasks_from_yaml("eval/tasks/*.yaml")
    results = run_suite(tasks, agent_config={"model": "gpt-4o-mini"})
    write_report(results, baseline_path="eval/baselines/2026-05-13.json",
                 out_path="eval/reports/latest.md")
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Task / result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = ("template_base", "template_variant", "adversarial",
              "out_of_corpus", "regression")


@dataclass
class StreamExpectation:
    """Acceptance criteria for one stream after solve."""
    T_K_range:            Optional[Tuple[float, float]] = None
    P_Pa_range:           Optional[Tuple[float, float]] = None
    mass_flow_kg_s_range: Optional[Tuple[float, float]] = None
    composition_at_least: Dict[str, float] = field(default_factory=dict)
    composition_at_most:  Dict[str, float] = field(default_factory=dict)
    phase:                Optional[str] = None      # "vapor" | "liquid"


@dataclass
class Expectations:
    must_converge:         bool = True
    must_verify_pass:      bool = True
    must_include_units:    List[str] = field(default_factory=list)
    must_include_recycle:  bool = False
    streams:               Dict[str, StreamExpectation] = field(default_factory=dict)
    answer_must_mention:   List[str] = field(default_factory=list)
    answer_must_not_claim: List[str] = field(default_factory=list)
    max_steps:             int = 20
    max_cost_usd:          float = 1.00


@dataclass
class EvalTask:
    id:                   str
    prompt:               str
    category:             str               # one of CATEGORIES
    expected_template:    Optional[str] = None
    expectations:         Expectations = field(default_factory=Expectations)
    tags:                 List[str] = field(default_factory=list)

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, got {self.category!r}")


@dataclass
class EvalResult:
    task_id:     str
    success:     bool
    scores:      Dict[str, float]
    failures:    List[str]
    steps_used:  int
    cost_usd:    float
    template_used: Optional[str] = None
    elapsed_s:   float = 0.0
    final_envelope: Dict[str, Any] = field(default_factory=dict)
    final_answer:   str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Scorers — each returns (score 0..1, list[str] of specific failures)
# ─────────────────────────────────────────────────────────────────────────────

def score_convergence(task: EvalTask,
                      envelope: Dict[str, Any],
                      answer: str,
                      transcript: List[Dict]
                      ) -> Tuple[float, List[str]]:
    if not task.expectations.must_converge:
        return 1.0, []
    if envelope.get("converged") is False:
        return 0.0, ["did not converge"]
    # Treat "no solve happened" as a fail for tasks that demand convergence
    if not envelope:
        return 0.0, ["no solve envelope captured"]
    return 1.0, []


def score_intent_verification(task: EvalTask,
                              envelope: Dict[str, Any],
                              answer: str,
                              transcript: List[Dict]
                              ) -> Tuple[float, List[str]]:
    """Honour the intent_verification block produced by declare_intent + solve."""
    if not task.expectations.must_verify_pass:
        return 1.0, []
    iv = envelope.get("intent_verification")
    if not iv:
        return 1.0, []   # no intent declared by the LLM; can't fail
    if iv.get("passed"):
        return 1.0, []
    fails = [f["target"] for f in iv.get("findings", []) if f.get("severity") == "error"]
    return 0.0, [f"intent target failed: {x}" for x in fails]


def score_streams(task: EvalTask,
                  envelope: Dict[str, Any],
                  answer: str,
                  transcript: List[Dict]
                  ) -> Tuple[float, List[str]]:
    fails: List[str] = []
    expectations = task.expectations.streams
    if not expectations:
        return 1.0, []
    streams = (envelope.get("stream_results") or envelope.get("streams") or {})
    for tag, exp in expectations.items():
        s = streams.get(tag) or {}
        if not s:
            fails.append(f"stream {tag} missing from results")
            continue
        if exp.T_K_range:
            lo, hi = exp.T_K_range
            v = s.get("temperature_K") or s.get("T_K")
            if v is None or not (lo <= float(v) <= hi):
                fails.append(f"{tag}.T_K={v} not in {exp.T_K_range}")
        if exp.P_Pa_range:
            lo, hi = exp.P_Pa_range
            v = s.get("pressure_Pa") or s.get("P_Pa")
            if v is None or not (lo <= float(v) <= hi):
                fails.append(f"{tag}.P_Pa={v} not in {exp.P_Pa_range}")
        # Composition checks
        comp = (s.get("composition") or s.get("mole_fractions") or {})
        for c, lo in exp.composition_at_least.items():
            actual = comp.get(c) or s.get(f"mole_frac_{c}")
            if actual is None or float(actual) < lo:
                fails.append(f"{tag}.{c}={actual} < {lo}")
        for c, hi in exp.composition_at_most.items():
            actual = comp.get(c) or s.get(f"mole_frac_{c}")
            if actual is not None and float(actual) > hi:
                fails.append(f"{tag}.{c}={actual} > {hi}")
        if exp.phase and s.get("phase") != exp.phase:
            fails.append(f"{tag}.phase={s.get('phase')} != {exp.phase}")

    score = 1.0 - (len(fails) / max(len(expectations), 1))
    return max(0.0, score), fails


def score_topology(task: EvalTask,
                   envelope: Dict[str, Any],
                   answer: str,
                   transcript: List[Dict]
                   ) -> Tuple[float, List[str]]:
    fails: List[str] = []
    req_units    = task.expectations.must_include_units
    req_recycle  = task.expectations.must_include_recycle
    if not req_units and not req_recycle:
        return 1.0, []

    # Pull unit kinds from the envelope (preferred) or the transcript
    unit_kinds = set()
    for tag, u in (envelope.get("unit_ops") or {}).items():
        if isinstance(u, dict):
            unit_kinds.add(u.get("type") or u.get("kind") or "")
    # Fallback: scan transcript for add_object kinds
    if not unit_kinds:
        for ev in transcript:
            if ev.get("kind") == "tool_call" and ev.get("name") == "add_object":
                args = ev.get("arguments") or {}
                if args.get("type"):
                    unit_kinds.add(args["type"])

    for need in req_units:
        if need not in unit_kinds:
            fails.append(f"missing required unit kind: {need}")

    if req_recycle:
        has_recycle = bool(envelope.get("recycles")) or \
            any("Recycle" in str(k) for k in unit_kinds)
        if not has_recycle:
            fails.append("required recycle not present")

    n_req = len(req_units) + (1 if req_recycle else 0)
    return max(0.0, 1.0 - len(fails) / max(n_req, 1)), fails


def score_answer(task: EvalTask,
                 envelope: Dict[str, Any],
                 answer: str,
                 transcript: List[Dict]
                 ) -> Tuple[float, List[str]]:
    fails: List[str] = []
    lower = (answer or "").lower()
    for term in task.expectations.answer_must_mention:
        if term.lower() not in lower:
            fails.append(f"answer missing required mention: {term!r}")
    for term in task.expectations.answer_must_not_claim:
        if term.lower() in lower:
            fails.append(f"answer made forbidden claim: {term!r}")
    n_checks = (len(task.expectations.answer_must_mention)
                + len(task.expectations.answer_must_not_claim))
    if n_checks == 0:
        return 1.0, []
    return max(0.0, 1.0 - len(fails) / n_checks), fails


def score_efficiency(task: EvalTask,
                     envelope: Dict[str, Any],
                     answer: str,
                     transcript: List[Dict],
                     steps_used: int = 0,
                     cost_usd: float = 0.0,
                     ) -> Tuple[float, List[str]]:
    fails: List[str] = []
    # Hard cap: only fail at 2x the budget. 1x-2x reports a soft warning.
    if steps_used > task.expectations.max_steps * 2:
        fails.append(f"steps {steps_used} > 2× budget {task.expectations.max_steps}")
    if cost_usd > task.expectations.max_cost_usd * 2:
        fails.append(f"cost ${cost_usd:.2f} > 2× budget ${task.expectations.max_cost_usd}")
    return (1.0 if not fails else 0.0), fails


SCORERS: Dict[str, Callable] = {
    "convergence":       score_convergence,
    "intent_verification": score_intent_verification,
    "streams":           score_streams,
    "topology":          score_topology,
    "answer":            score_answer,
    "efficiency":        score_efficiency,
}


# ─────────────────────────────────────────────────────────────────────────────
# Single-task and suite runners
# ─────────────────────────────────────────────────────────────────────────────

def run_task(task: EvalTask,
             agent_factory: Callable[[], Any],
             ) -> EvalResult:
    """Run one task end-to-end and score it.

    `agent_factory()` must return a fresh DWSIMAgentV2 — fresh per task,
    blueprint §"Isolated agent sessions" (no cache bleed)."""
    started = time.monotonic()
    agent = agent_factory()
    transcript: List[Dict] = []
    cost_usd = 0.0
    steps_used = 0
    answer = ""
    envelope: Dict[str, Any] = {}

    def on_tool_call(tool_name, args, result):
        transcript.append({"kind": "tool_call", "name": tool_name,
                           "arguments": args, "result_snippet": str(result)[:200]})
        # Capture the last solve envelope so scorers can inspect it
        nonlocal envelope
        if tool_name in ("save_and_solve", "run_simulation",
                         "build_flowsheet_atomic", "robust_solve"):
            if isinstance(result, dict) and result.get("success", False):
                envelope.update(result)

    # Wire the callback
    try:
        agent.on_tool_call = on_tool_call
    except Exception:
        pass

    try:
        answer = agent.chat(task.prompt) or ""
    except Exception as exc:
        return EvalResult(
            task_id=task.id, success=False,
            scores={}, failures=[f"agent raised: {exc}"],
            steps_used=0, cost_usd=0.0,
            elapsed_s=time.monotonic() - started,
        )

    # Best-effort cost/step tally from agent internals
    try:
        steps_used = len([ev for ev in transcript if ev["kind"] == "tool_call"])
    except Exception:
        pass

    # Score
    scores: Dict[str, float] = {}
    failures: List[str] = []
    for name, fn in SCORERS.items():
        try:
            if name == "efficiency":
                s, f = fn(task, envelope, answer, transcript,
                          steps_used=steps_used, cost_usd=cost_usd)
            else:
                s, f = fn(task, envelope, answer, transcript)
            scores[name] = s
            failures.extend(f)
        except Exception as exc:
            scores[name] = 0.0
            failures.append(f"scorer {name} raised: {exc}")

    # Hard gates: convergence + intent_verification + topology must pass
    hard_gates = ["convergence", "intent_verification", "topology"]
    success = all(scores.get(g, 0.0) >= 0.99 for g in hard_gates)

    return EvalResult(
        task_id=task.id, success=success,
        scores=scores, failures=failures,
        steps_used=steps_used, cost_usd=cost_usd,
        elapsed_s=time.monotonic() - started,
        final_envelope=envelope,
        final_answer=answer,
    )


def run_suite(tasks: List[EvalTask],
              agent_factory: Callable[[], Any],
              repetitions: int = 1,
              ) -> List[EvalResult]:
    """Run all tasks once (or N times) and return aggregated results.

    Blueprint §"Run-to-run variance is real" — repetitions ≥ 3 recommended
    for honest reporting, but 1 is acceptable for fast iteration."""
    all_results: List[EvalResult] = []
    for rep in range(repetitions):
        for task in tasks:
            r = run_task(task, agent_factory)
            r.task_id = f"{r.task_id}#rep{rep+1}" if repetitions > 1 else r.task_id
            all_results.append(r)
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
# Reporting — pass/fail diff vs frozen baseline
# ─────────────────────────────────────────────────────────────────────────────

def summarise(results: List[EvalResult]) -> Dict[str, Any]:
    """Compact summary suitable for CI gates."""
    total = len(results)
    passes = sum(1 for r in results if r.success)
    mean_cost = (sum(r.cost_usd for r in results) / total) if total else 0.0
    mean_steps = (sum(r.steps_used for r in results) / total) if total else 0.0
    by_task = {r.task_id: r.success for r in results}
    return {
        "total":        total,
        "passes":       passes,
        "pass_rate":    passes / total if total else 0.0,
        "mean_cost":    mean_cost,
        "mean_steps":   mean_steps,
        "results":      by_task,
    }


def diff_vs_baseline(current: List[EvalResult],
                     baseline_path: Optional[str]
                     ) -> Dict[str, Any]:
    """Blueprint §"Surface regressions explicitly" — was-passing-now-failing."""
    base: Dict[str, bool] = {}
    if baseline_path and os.path.exists(baseline_path):
        try:
            with open(baseline_path, "r", encoding="utf-8") as fh:
                base = json.load(fh).get("results", {})
        except Exception:
            base = {}

    regressions: List[str] = []
    new_passes:  List[str] = []
    for r in current:
        was = base.get(r.task_id)
        now = r.success
        if was is True and not now:
            regressions.append(r.task_id)
        elif was is False and now:
            new_passes.append(r.task_id)

    return {
        "regressions": regressions,
        "new_passes":  new_passes,
        "n_regressions": len(regressions),
    }


def write_report(results: List[EvalResult],
                 baseline_path: Optional[str] = None,
                 out_path: str = "eval_report.md") -> str:
    """Write a markdown report; return the path."""
    summary = summarise(results)
    diff = diff_vs_baseline(results, baseline_path)

    lines: List[str] = [
        f"# Eval Report ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        "",
        f"**Overall:** {summary['passes']}/{summary['total']} passes "
        f"({summary['pass_rate']*100:.1f}%)",
        f"**Mean cost/task:** ${summary['mean_cost']:.3f}",
        f"**Mean steps/task:** {summary['mean_steps']:.1f}",
        "",
    ]

    if diff["regressions"]:
        lines.append("## ⚠ REGRESSIONS (was passing, now failing)")
        for t in diff["regressions"]:
            r = next((x for x in results if x.task_id == t), None)
            if r:
                lines.append(f"- **{t}** — {'; '.join(r.failures[:3]) or 'unknown'}")
        lines.append("")

    if diff["new_passes"]:
        lines.append("## ✓ NEW PASSES (was failing, now passing)")
        for t in diff["new_passes"]:
            lines.append(f"- {t}")
        lines.append("")

    # Per-task table
    lines.append("## Per-task results")
    lines.append("| Task | Status | Steps | Cost | Top failure |")
    lines.append("|------|--------|-------|------|-------------|")
    for r in results:
        status = "✓" if r.success else "✗"
        top_fail = r.failures[0] if r.failures else ""
        lines.append(f"| {r.task_id} | {status} | {r.steps_used} | "
                     f"${r.cost_usd:.3f} | {top_fail[:80]} |")

    content = "\n".join(lines)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return out_path


def save_baseline(results: List[EvalResult], path: str) -> None:
    """Freeze a baseline run — blueprint §"Lock the baseline explicitly"."""
    summary = summarise(results)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# Starter task set — 5 tasks covering all 5 categories
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_TASKS: List[EvalTask] = [
    EvalTask(
        id="water_heater_base",
        prompt="Build a water heater: feed water at 25°C, 1 bar, 1 kg/s. "
               "Heat to 80°C. Use Steam Tables. Report outlet conditions.",
        category="template_base",
        expectations=Expectations(
            must_converge=True,
            must_include_units=["Heater"],
            answer_must_mention=["80", "kg/s"],
            max_steps=8,
            max_cost_usd=0.10,
        ),
        tags=["basic", "heater"],
    ),
    EvalTask(
        id="biogas_h2_base",
        prompt="Build the biogas-to-hydrogen SMR flowsheet from the "
               "Ullah 2025 paper using the available template, then solve "
               "and report H2 production rate.",
        category="template_base",
        expected_template="biogas_smr_h2_gibbs",
        expectations=Expectations(
            must_converge=True,
            must_include_units=["GibbsReactor", "ConversionReactor",
                                "CompoundSeparator"],
            answer_must_mention=["hydrogen", "kg/h"],
            answer_must_not_claim=["100% conversion"],
            max_steps=15,
            max_cost_usd=0.50,
        ),
        tags=["h2", "smr", "ullah_2025"],
    ),
    EvalTask(
        id="biogas_h2_pressure_variant",
        prompt="Build a biogas-to-hydrogen SMR flowsheet but at 10 bar "
               "instead of the usual 16 bar. Report how the lower pressure "
               "affects hydrogen yield.",
        category="template_variant",
        expected_template="biogas_smr_h2_gibbs",
        expectations=Expectations(
            must_converge=True,
            must_include_units=["GibbsReactor"],
            answer_must_mention=["pressure", "hydrogen", "10"],
            max_steps=20,
            max_cost_usd=0.60,
        ),
        tags=["h2", "smr", "variant"],
    ),
    EvalTask(
        id="ammonia_underspec_adversarial",
        prompt="I want to make ammonia. Help.",
        category="adversarial",
        expectations=Expectations(
            must_converge=False,    # may not even reach solve
            answer_must_mention=["assumed", "feed"],  # must explicitly note assumptions
            max_steps=15,
            max_cost_usd=0.40,
        ),
        tags=["underspec", "ammonia"],
    ),
    EvalTask(
        id="fluidized_bed_out_of_corpus",
        prompt="Build a fluidized bed reactor for catalytic cracking of vacuum gas oil.",
        category="out_of_corpus",
        expectations=Expectations(
            must_converge=False,
            # Agent should explicitly state the scope limitation
            answer_must_mention=["not", "support"],
            max_steps=8,
            max_cost_usd=0.20,
        ),
        tags=["refusal", "out_of_scope"],
    ),
]


def load_default_tasks() -> List[EvalTask]:
    """Return the starter task set. Extend by appending to DEFAULT_TASKS or
    by writing your own list and passing to run_suite()."""
    return list(DEFAULT_TASKS)
