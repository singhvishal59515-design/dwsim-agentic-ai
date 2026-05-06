"""
ablation_paper_table.py
───────────────────────
Reads ablation_log.jsonl and produces the final paper-ready results table.
Aggregates ALL log entries regardless of which invocation produced them,
so partial runs from multiple sessions are combined correctly.

Usage:
    python ablation_paper_table.py
    python ablation_paper_table.py --log ablation_log.jsonl --runs 3
"""
from __future__ import annotations
import json, os, sys, argparse
from collections import defaultdict
from typing import Dict, List, Any

CONFIGS_ORDER = ["A0","A1","A2","A3","A4","A5","A6","A7"]
CONFIG_DESC = {
    "A0": "Full system (baseline)",
    "A1": "No safety validator",
    "A2": "No RAG knowledge base",
    "A3": "No auto-corrector",
    "A4": "No tool compression",
    "A5": "LLM temperature=1.0",
    "A6": "ShortcutColumn forced",
    "A7": "No context trimming",
}
TASKS_ORDER = ["ABL-01","ABL-02","ABL-03","ABL-04","ABL-05"]
TASK_DESC = {
    "ABL-01": "Water heater",
    "ABL-02": "Ethanol distillation",
    "ABL-03": "Air compression",
    "ABL-04": "Methanol reactor",
    "ABL-05": "Cryogenic flash",
}

def load_log(path: str) -> List[Dict[str, Any]]:
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    return entries


def aggregate(entries: List[Dict]) -> Dict:
    """Group entries by (config_id, task_id) and compute stats."""
    groups: Dict[tuple, List] = defaultdict(list)
    for e in entries:
        key = (e.get("config_id","?"), e.get("task_id","?"))
        groups[key].append(e)

    results: Dict[str, Dict] = {}  # config_id -> stats
    per_task: Dict[tuple, Dict] = {}

    for (cfg, task), runs in groups.items():
        n          = len(runs)
        n_pass     = sum(1 for r in runs if r.get("success"))
        n_conv     = sum(1 for r in runs if r.get("converged"))
        avg_dur    = sum(r.get("duration_s",0) for r in runs) / max(n,1)
        avg_tools  = sum(r.get("n_tools",0)    for r in runs) / max(n,1)
        avg_sf     = sum(r.get("safety_violations",0) for r in runs) / max(n,1)
        per_task[(cfg,task)] = {
            "n": n,
            "success_rate": n_pass / max(n,1),
            "conv_rate":    n_conv / max(n,1),
            "avg_dur":      avg_dur,
            "avg_tools":    avg_tools,
            "avg_sf":       avg_sf,
        }

    # Aggregate across tasks per config
    for cfg in CONFIGS_ORDER:
        rows = [per_task[(cfg,t)] for t in TASKS_ORDER if (cfg,t) in per_task]
        if not rows:
            continue
        n_tasks = len(rows)
        results[cfg] = {
            "n_tasks":      n_tasks,
            "success_rate": sum(r["success_rate"] for r in rows) / n_tasks,
            "conv_rate":    sum(r["conv_rate"]    for r in rows) / n_tasks,
            "avg_dur":      sum(r["avg_dur"]      for r in rows) / n_tasks,
            "avg_tools":    sum(r["avg_tools"]    for r in rows) / n_tasks,
            "avg_sf":       sum(r["avg_sf"]       for r in rows) / n_tasks,
            "per_task":     {t: per_task[(cfg,t)] for t in TASKS_ORDER if (cfg,t) in per_task},
        }

    return results, per_task


def print_paper_table(results: Dict, per_task: Dict, n_runs_target: int = 3):
    """Print the LaTeX-ready and console summary tables."""
    baseline = results.get("A0", {})
    base_sr  = baseline.get("success_rate", 0)

    print("\n" + "="*80)
    print("ABLATION STUDY — PAPER TABLE (aggregated from ablation_log.jsonl)")
    print("="*80)

    # ── Main summary table ────────────────────────────────────────────────────
    print(f"\n{'Config':<6} {'Description':<30} {'Tasks':>5} {'Succ%':>6} {'Drop':>6} {'Conv%':>6} {'Tools':>6} {'Time(s)':>8} {'SF':>4}")
    print("-"*80)
    for cfg in CONFIGS_ORDER:
        if cfg not in results:
            print(f"{cfg:<6} {'(no data)':30}")
            continue
        r    = results[cfg]
        sr   = r["success_rate"]
        drop = sr - base_sr if cfg != "A0" else 0
        drop_s = f"{drop*100:+.1f}%" if cfg != "A0" else "—"
        print(f"{cfg:<6} {CONFIG_DESC.get(cfg,'?'):<30} {r['n_tasks']:>5} "
              f"{sr*100:>5.1f}% {drop_s:>6} {r['conv_rate']*100:>5.1f}% "
              f"{r['avg_tools']:>6.1f} {r['avg_dur']:>8.1f} {r['avg_sf']:>4.1f}")

    print()

    # ── Per-task breakdown ────────────────────────────────────────────────────
    print("\nPER-TASK BREAKDOWN")
    print("-"*80)
    header = f"{'Config':<6} " + " ".join(f"{t:<10}" for t in TASKS_ORDER)
    print(header)
    print("-"*80)
    for cfg in CONFIGS_ORDER:
        row = f"{cfg:<6} "
        for task in TASKS_ORDER:
            pt = per_task.get((cfg,task))
            if pt:
                n  = pt["n"]
                sr = pt["success_rate"]
                mark = "✓" if sr >= 0.67 else ("~" if sr >= 0.34 else "✗")
                cell = f"{mark}{sr*100:.0f}%[{n}]"
                row += f"{cell:<10} "
            else:
                row += f"{'—':<10} "
        print(row)

    # ── Coverage check ────────────────────────────────────────────────────────
    print(f"\nCOVERAGE (target: {n_runs_target} runs per pair)")
    total_pairs = len(CONFIGS_ORDER) * len(TASKS_ORDER)
    done_pairs  = sum(1 for (cfg,t), pt in per_task.items() if pt["n"] >= n_runs_target)
    total_runs  = sum(pt["n"] for pt in per_task.values())
    print(f"  Complete pairs : {done_pairs}/{total_pairs}")
    print(f"  Total runs     : {total_runs}")

    missing = [(cfg,t) for cfg in CONFIGS_ORDER for t in TASKS_ORDER
               if per_task.get((cfg,t),{"n":0})["n"] < n_runs_target]
    if missing:
        print(f"  Incomplete pairs ({len(missing)}):")
        for cfg, t in missing:
            n = per_task.get((cfg,t),{"n":0})["n"]
            print(f"    {cfg} {t}: {n}/{n_runs_target} runs")
    else:
        print("  All pairs complete!")

    # ── LaTeX snippet ─────────────────────────────────────────────────────────
    print("\n" + "="*80)
    print("LaTeX TABLE SNIPPET (paste into paper)")
    print("="*80)
    print(r"\begin{table}[ht]")
    print(r"\centering")
    print(r"\caption{Ablation study results. Success rate averaged over 5 tasks × 3 runs each.}")
    print(r"\label{tab:ablation}")
    print(r"\begin{tabular}{llccccc}")
    print(r"\hline")
    print(r"Config & Component Removed & Tasks & Succ\% & $\Delta$Succ & Conv\% & Avg.\ Time (s) \\")
    print(r"\hline")
    for cfg in CONFIGS_ORDER:
        if cfg not in results:
            continue
        r    = results[cfg]
        sr   = r["success_rate"]
        drop = sr - base_sr if cfg != "A0" else 0
        drop_s = f"{drop*100:+.1f}" if cfg != "A0" else "—"
        desc = CONFIG_DESC.get(cfg,'?').replace('&', r'\&')
        print(f"{cfg} & {desc} & {r['n_tasks']} & {sr*100:.1f} & {drop_s} & "
              f"{r['conv_rate']*100:.1f} & {r['avg_dur']:.1f} \\\\")
    print(r"\hline")
    print(r"\end{tabular}")
    print(r"\end{table}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log",  default="ablation_log.jsonl")
    parser.add_argument("--runs", type=int, default=3, help="Target runs per pair")
    args = parser.parse_args()

    if not os.path.exists(args.log):
        print(f"Log file not found: {args.log}")
        sys.exit(1)

    entries = load_log(args.log)
    print(f"Loaded {len(entries)} entries from {args.log}")

    results, per_task = aggregate(entries)
    print_paper_table(results, per_task, args.runs)


if __name__ == "__main__":
    main()
