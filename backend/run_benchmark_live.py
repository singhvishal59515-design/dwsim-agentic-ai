#!/usr/bin/env python3
"""
Live benchmark runner — the thesis evidence generator.

Builds the REAL agent (real DWSIMBridgeV2 + configured LLM), runs the fixed
25-task benchmark suite against it, scores each task, and writes:

  * benchmark_results.json          — full machine-readable report
  * BENCHMARK_TABLE.md              — per-task Markdown table + headline pass-rate
  * eval_log.json["benchmark_results"] — so eval_summary.py / the UI pick it up

It refuses to fabricate numbers: if the real agent cannot be built (no DWSIM
install, no LLM key) it prints a clear diagnosis and exits non-zero rather than
silently scoring a mock. The report records mode='live' vs 'mock' either way.

Usage:
    python run_benchmark_live.py                 # full suite
    python run_benchmark_live.py t01 t02 t07     # a subset of task ids

Prereqs for a meaningful 'live' run: a DWSIM install reachable via pythonnet
(set DWSIM_DLL_FOLDER if not auto-detected) and a valid LLM key in the
environment (e.g. GROQ_API_KEY / ANTHROPIC_API_KEY) per .env.example.
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _build_agent():
    """Build the real agent via the same factory the API server uses."""
    import api
    return api._get_agent()


def main(argv: list) -> int:
    task_ids = argv or None
    import benchmark_tasks as bt

    print("[run_benchmark_live] building the real agent (DWSIM + LLM)…")
    try:
        agent = _build_agent()
    except Exception as exc:
        print(f"\n[run_benchmark_live] ABORT: could not build a live agent: {exc}")
        print("  This usually means DWSIM isn't installed/reachable or no LLM key "
              "is set.\n  Fix the environment (see .env.example) and re-run. "
              "Refusing to emit mock numbers as if they were live.")
        return 2

    mode = bt._bridge_mode(agent)
    n = len(task_ids) if task_ids else len(bt.BENCHMARK_TASKS)
    print(f"[run_benchmark_live] mode={mode.upper()}  running {n} task(s)… "
          f"(30-90s each — be patient)")
    if mode != "live":
        print("  WARNING: the bridge is NOT a live DWSIMBridgeV2 — results will be "
              "labelled mode='mock' and must NOT be cited as live-engine evidence.")

    report = bt.run_all(agent, task_ids=task_ids, persist=True)
    summary = report["summary"]

    table = bt.render_results_table(report["results"])
    md = (
        f"# Benchmark Results ({mode})\n\n"
        f"Ran at: {report['ran_at']}  ·  mode: **{mode}**  ·  "
        f"pass-rate: **{summary['passed']}/{summary['total']} "
        f"({summary['pass_rate']}%)**\n\n"
        f"{table}\n\n"
        f"## By category\n\n" + _kv_table(summary.get("by_category", {})) +
        f"\n\n## By complexity\n\n" + _kv_table(summary.get("by_complexity", {}))
    )
    md_path = os.path.join(_HERE, "BENCHMARK_TABLE.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md + "\n")

    print("\n" + table.encode("ascii", "replace").decode("ascii"))
    print(f"\n[run_benchmark_live] mode={mode}  pass-rate="
          f"{summary['passed']}/{summary['total']} ({summary['pass_rate']}%)")
    print(f"[run_benchmark_live] wrote {md_path}")
    print(f"[run_benchmark_live] wrote {report.get('persisted_to')}")
    print("[run_benchmark_live] now re-run:  python eval_summary.py")
    return 0


def _kv_table(d: dict) -> str:
    if not d:
        return "_none_"
    rows = ["| Bucket | Passed | Total | Pass-rate |", "|---|--:|--:|--:|"]
    for k, v in d.items():
        rows.append(f"| {k} | {v.get('passed')} | {v.get('total')} | "
                    f"{v.get('pass_rate')}% |")
    return "\n".join(rows)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
