"""
benchmark_report.py
───────────────────
Generate a comparison report from benchmark runs: Agent vs Manual DWSIM baseline.

Produces three artefacts from the existing eval_log.json + benchmarks.json:
  • summary   — pass rate, mean speedup, convergence rate, grouped by difficulty
  • per-case  — one row per benchmark with agent_time_s / human_time_s / speedup / passed
  • markdown  — a readable report string suitable for display in the UI or export

Intentionally pure-python: no pandas / matplotlib / reportlab dependency so it
stays callable from the API without blowing import time.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional

from evaluation import get_benchmark_suite, get_eval_log


def _latest_result_per_case(results: List[Dict]) -> Dict[str, Dict]:
    """Keep only the newest benchmark result for each benchmark_id."""
    latest: Dict[str, Dict] = {}
    for r in results:
        bid = r.get("benchmark_id")
        if not bid:
            continue
        if bid not in latest or r.get("timestamp_iso", "") > latest[bid].get("timestamp_iso", ""):
            latest[bid] = r
    return latest


def build_report(*, only_latest: bool = True) -> Dict[str, Any]:
    suite    = get_benchmark_suite()
    log      = get_eval_log()
    metrics  = log.get_benchmark_metrics()
    results  = metrics.get("results") or []
    cases    = {c["id"]: c for c in suite.list_all()}

    # reverse back to chronological so latest-wins works
    results = list(reversed(results))
    picked  = _latest_result_per_case(results) if only_latest else {
        f"{r['benchmark_id']}::{i}": r for i, r in enumerate(results)
    }

    rows: List[Dict[str, Any]] = []
    for bid, r in picked.items():
        # bid may be prefixed when only_latest=False — strip
        real_bid = r.get("benchmark_id", bid)
        case = cases.get(real_bid, {})
        human_min = case.get("human_time_min")
        human_s   = (human_min * 60) if human_min else None
        agent_s   = r.get("duration_s")
        speedup   = (round(human_s / agent_s, 1)
                     if (human_s and agent_s and agent_s > 0) else None)
        rows.append({
            "benchmark_id":     real_bid,
            "name":             case.get("name", "?"),
            "difficulty":       case.get("difficulty", "?"),
            "tags":             case.get("tags", []),
            "passed":           bool(r.get("passed")),
            "convergence":      r.get("convergence"),
            "agent_time_s":     agent_s,
            "human_time_s":     human_s,
            "human_time_min":   human_min,
            "speedup_vs_human": speedup,
            "accuracy_checks":  r.get("accuracy_checks", []),
            "notes":            r.get("notes", ""),
            "session_id":       r.get("session_id", ""),
        })
    rows.sort(key=lambda x: (x["difficulty"], x["benchmark_id"]))

    # Aggregates
    passed  = sum(1 for r in rows if r["passed"])
    n       = len(rows)
    pass_rate = round(passed / n * 100, 1) if n else None

    speedups = [r["speedup_vs_human"] for r in rows if r["speedup_vs_human"]]
    mean_speedup   = round(statistics.mean(speedups), 1)   if speedups else None
    median_speedup = round(statistics.median(speedups), 1) if speedups else None

    conv_rows = [r for r in rows if r["convergence"] is not None]
    conv_rate = round(
        sum(1 for r in conv_rows if r["convergence"]) / len(conv_rows) * 100, 1
    ) if conv_rows else None

    agent_total_s = sum(r["agent_time_s"] for r in rows if r["agent_time_s"])
    human_total_s = sum(r["human_time_s"] for r in rows if r["human_time_s"])

    # Pass rate grouped by difficulty
    by_difficulty: Dict[str, Dict[str, Any]] = {}
    for diff in ("easy", "medium", "hard"):
        group = [r for r in rows if r["difficulty"] == diff]
        if not group:
            continue
        g_pass = sum(1 for r in group if r["passed"])
        g_speedups = [r["speedup_vs_human"] for r in group if r["speedup_vs_human"]]
        by_difficulty[diff] = {
            "count":          len(group),
            "passed":         g_pass,
            "pass_rate_pct":  round(g_pass / len(group) * 100, 1),
            "mean_speedup":   round(statistics.mean(g_speedups), 1) if g_speedups else None,
            "mean_agent_s":   round(statistics.mean([r["agent_time_s"] for r in group if r["agent_time_s"]]), 1)
                              if any(r["agent_time_s"] for r in group) else None,
            "mean_human_min": round(statistics.mean([r["human_time_min"] for r in group if r["human_time_min"]]), 1)
                              if any(r["human_time_min"] for r in group) else None,
        }

    summary = {
        "total_cases":    len(cases),
        "runs_counted":   n,
        "passed":         passed,
        "pass_rate_pct":  pass_rate,
        "mean_speedup_vs_human":   mean_speedup,
        "median_speedup_vs_human": median_speedup,
        "convergence_rate_pct":    conv_rate,
        "agent_total_s":  round(agent_total_s, 1) if agent_total_s else 0.0,
        "human_total_s":  round(human_total_s, 1) if human_total_s else 0.0,
        "human_total_min": round(human_total_s / 60, 1) if human_total_s else 0.0,
        "by_difficulty":  by_difficulty,
        "untested_cases": [cid for cid in cases if cid not in picked],
    }

    return {
        "summary":  summary,
        "per_case": rows,
        "markdown": _render_markdown(summary, rows),
    }


def _fmt_secs(s: Optional[float]) -> str:
    if s is None:
        return "—"
    if s < 60:
        return f"{s:.1f}s"
    return f"{s/60:.1f}m"


def _render_markdown(summary: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# DWSIM Agent — Benchmark Report")
    lines.append("")
    lines.append(f"**Cases defined:** {summary['total_cases']}   "
                 f"**Runs counted:** {summary['runs_counted']}   "
                 f"**Passed:** {summary['passed']} "
                 f"({summary['pass_rate_pct']}%)")
    lines.append("")
    lines.append("## Agent vs Manual DWSIM")
    lines.append("")
    lines.append(f"- Mean speedup:   **{summary['mean_speedup_vs_human'] or '—'}×**")
    lines.append(f"- Median speedup: **{summary['median_speedup_vs_human'] or '—'}×**")
    lines.append(f"- Agent total time: {_fmt_secs(summary['agent_total_s'])}")
    lines.append(f"- Human baseline:   {_fmt_secs(summary['human_total_s'])}")
    lines.append(f"- Convergence rate: {summary['convergence_rate_pct'] or '—'}%")
    lines.append("")
    if summary["by_difficulty"]:
        lines.append("### By difficulty")
        lines.append("")
        lines.append("| Difficulty | Cases | Passed | Pass rate | Mean speedup | Mean agent | Mean human |")
        lines.append("|------------|-------|--------|-----------|--------------|------------|------------|")
        for diff, d in summary["by_difficulty"].items():
            lines.append(
                f"| {diff} | {d['count']} | {d['passed']} | "
                f"{d['pass_rate_pct']}% | "
                f"{d['mean_speedup'] or '—'}× | "
                f"{_fmt_secs(d['mean_agent_s'])} | "
                f"{(str(d['mean_human_min']) + ' min') if d['mean_human_min'] else '—'} |"
            )
        lines.append("")

    if rows:
        lines.append("## Per-case results")
        lines.append("")
        lines.append("| ID | Name | Difficulty | Passed | Agent | Human | Speedup |")
        lines.append("|----|------|------------|--------|-------|-------|---------|")
        for r in rows:
            mark = "✅" if r["passed"] else "❌"
            lines.append(
                f"| {r['benchmark_id']} | {r['name']} | {r['difficulty']} | "
                f"{mark} | {_fmt_secs(r['agent_time_s'])} | "
                f"{(str(r['human_time_min']) + ' min') if r['human_time_min'] else '—'} | "
                f"{(str(r['speedup_vs_human']) + '×') if r['speedup_vs_human'] else '—'} |"
            )
        lines.append("")

    if summary["untested_cases"]:
        lines.append("## Not yet run")
        lines.append("")
        for cid in summary["untested_cases"]:
            lines.append(f"- {cid}")
        lines.append("")
    return "\n".join(lines)
