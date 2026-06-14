"""
ablation_report.py
==================
Human-readable ablation report: descriptive tables, Kruskal-Wallis,
pairwise Mann-Whitney U (Holm-corrected), Cohen's d, category breakdown, CSV.

Consumes the JSONL logs written by ablation_runner.py (one record per task:
condition A/B/C/D, success 1/0/-1, tool_calls, wall_time_s,
error_recovery_events, category).

Run AFTER ablation_runner.py completes:
    python ablation_runner.py --reps 3
    python ablation_report.py

(For the programmatic stats object — used by tests and the paper pipeline — see
ablation_stats.py, which exposes analyze()/format_report() over the same data.)

Author: Vishal Bhadauriya, M.Tech Chemical Engineering, HBTU Kanpur
Vendored into the backend and wired to ablation_runner.py's log directory.
"""

import json
import numpy as np
from pathlib import Path
from collections import defaultdict
from itertools import combinations

try:
    from scipy import stats
    SCIPY_AVAILABLE = True
except ImportError:
    print("WARNING: scipy not found. Run: pip install scipy")
    SCIPY_AVAILABLE = False

# Resolve next to this file so it matches ablation_runner.DEFAULT_LOG_DIR
# regardless of the current working directory.
LOG_DIR = Path(__file__).resolve().parent / "ablation_logs"
CONDITIONS = {
    "A": "Full System",
    "B": "No-RAG",
    "C": "No-SafetyValidator",
    "D": "Direct LLM",
}


def load_results():
    """Load all JSONL logs and organize by condition."""
    data = defaultdict(lambda: defaultdict(list))  # data[condition][metric]

    for log_file in LOG_DIR.glob("*.jsonl"):
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    cond = entry["condition"]
                    if entry.get("success") == -1:
                        continue  # not-applicable / skipped task
                    data[cond]["success"].append(entry["success"])
                    data[cond]["tool_calls"].append(entry["tool_calls"])
                    data[cond]["wall_time_s"].append(entry["wall_time_s"])
                    data[cond]["error_recovery_events"].append(
                        entry.get("error_recovery_events", 0))
                except Exception as e:
                    print(f"Parse error in {log_file}: {e}")

    return data


def cohen_d(a, b):
    """Cohen's d effect size between two groups."""
    a, b = np.array(a), np.array(b)
    if len(a) < 2 or len(b) < 2:
        return 0.0
    pooled_std = np.sqrt((np.std(a, ddof=1) ** 2 + np.std(b, ddof=1) ** 2) / 2)
    if pooled_std == 0:
        return 0.0
    return (np.mean(a) - np.mean(b)) / pooled_std


def interpret_cohens_d(d):
    d = abs(d)
    if d < 0.2:
        return "negligible"
    elif d < 0.5:
        return "small"
    elif d < 0.8:
        return "medium"
    else:
        return "large"


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def run_analysis():
    data = load_results()

    if not data:
        print("No log files found in", LOG_DIR)
        print("   Run ablation_runner.py first.")
        return

    # TABLE 1: Descriptive Statistics
    print_section("TABLE 1: Descriptive Statistics")
    metrics = ["success", "tool_calls", "wall_time_s", "error_recovery_events"]
    metric_labels = {
        "success": "Task Success Rate",
        "tool_calls": "Tool Calls / Task",
        "wall_time_s": "Wall Time (s)",
        "error_recovery_events": "Error Recovery Events",
    }

    for metric in metrics:
        print(f"\n  {metric_labels[metric]}:")
        print(f"  {'Condition':<20} {'N':>5} {'Mean':>10} {'Std':>10} {'Min':>8} {'Max':>8}")
        print(f"  {'-'*65}")
        for cond in ["A", "B", "C", "D"]:
            vals = data[cond][metric]
            if vals:
                arr = np.array(vals)
                if metric == "success":
                    print(f"  {cond} - {CONDITIONS[cond]:<15} {len(arr):>5} "
                          f"{np.mean(arr)*100:>9.1f}% {np.std(arr)*100:>9.1f}% "
                          f"{np.min(arr)*100:>7.0f}% {np.max(arr)*100:>7.0f}%")
                else:
                    print(f"  {cond} - {CONDITIONS[cond]:<15} {len(arr):>5} "
                          f"{np.mean(arr):>10.3f} {np.std(arr):>10.3f} "
                          f"{np.min(arr):>8.3f} {np.max(arr):>8.3f}")

    if not SCIPY_AVAILABLE:
        print("\nscipy not available — skipping statistical tests")
        return

    # TABLE 2: Kruskal-Wallis Test
    print_section("TABLE 2: Kruskal-Wallis Test (omnibus)")
    for metric in ["success", "tool_calls", "wall_time_s"]:
        groups = [data[c][metric] for c in ["A", "B", "C", "D"] if data[c][metric]]
        if len(groups) >= 2 and all(len(g) >= 1 for g in groups):
            try:
                H, p = stats.kruskal(*groups)
                sig = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else "ns"))
                print(f"  {metric_labels[metric]:<30} H={H:.4f}  p={p:.4f}  {sig}")
            except Exception as e:
                print(f"  {metric_labels[metric]:<30} n/a ({e})")

    # TABLE 3: Pairwise Mann-Whitney U (Holm-corrected)
    print_section("TABLE 3: Pairwise Mann-Whitney U Tests (Holm-corrected)")
    cond_pairs = list(combinations(["A", "B", "C", "D"], 2))

    for metric in ["success", "tool_calls"]:
        print(f"\n  Metric: {metric_labels[metric]}")
        print(f"  {'Pair':<15} {'U-stat':>10} {'p (raw)':>10} {'p (Holm)':>10} {'Cohen d':>10} {'Effect':>12}")
        print(f"  {'-'*65}")

        raw_results = []
        for c1, c2 in cond_pairs:
            g1, g2 = data[c1][metric], data[c2][metric]
            if g1 and g2:
                try:
                    U, p_raw = stats.mannwhitneyu(g1, g2, alternative="two-sided")
                except Exception:
                    U, p_raw = float("nan"), 1.0
                d = cohen_d(g1, g2)
                raw_results.append((c1, c2, U, p_raw, d))

        raw_results.sort(key=lambda x: x[3])
        n_tests = len(raw_results)
        for rank, (c1, c2, U, p_raw, d) in enumerate(raw_results):
            p_holm = min(p_raw * (n_tests - rank), 1.0)
            sig = "***" if p_holm < 0.001 else ("**" if p_holm < 0.01 else ("*" if p_holm < 0.05 else "ns"))
            effect = interpret_cohens_d(d)
            print(f"  {c1} vs {c2}        {U:>10.1f} {p_raw:>10.4f} {p_holm:>10.4f} {d:>10.3f} {effect:>12} {sig}")

    # TABLE 4: Cohen's d — Full System (A) vs others
    print_section("TABLE 4: Cohen's d — Full System (A) vs All Others")
    print(f"  {'Comparison':<25} {'Success d':>12} {'Tool calls d':>14} {'Interpretation'}")
    print(f"  {'-'*65}")
    for cond in ["B", "C", "D"]:
        d_success = cohen_d(data["A"]["success"], data[cond]["success"])
        d_tools = cohen_d(data["A"]["tool_calls"], data[cond]["tool_calls"])
        print(f"  A vs {cond} ({CONDITIONS[cond]:<18}) {d_success:>12.3f} "
              f"{d_tools:>14.3f} {interpret_cohens_d(d_success)}")

    # Category breakdown
    print_section("BY CATEGORY — Success Rate per Condition")
    category_data = defaultdict(lambda: defaultdict(list))
    for log_file in LOG_DIR.glob("*.jsonl"):
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("success") == -1:
                        continue
                    category_data[entry.get("category", "unknown")][entry["condition"]].append(entry["success"])
                except Exception:
                    pass

    cats = sorted(category_data.keys())
    print(f"\n  {'Category':<22}", end="")
    for cond in ["A", "B", "C", "D"]:
        print(f"  {cond}({CONDITIONS[cond][:8]})", end="")
    print()
    print(f"  {'-'*70}")
    for cat in cats:
        print(f"  {cat:<22}", end="")
        for cond in ["A", "B", "C", "D"]:
            vals = category_data[cat][cond]
            rate = f"{np.mean(vals)*100:.0f}%" if vals else "N/A"
            print(f"  {rate:>14}", end="")
        print()

    # CSV export
    csv_path = LOG_DIR / "ablation_results_summary.csv"
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("condition,description,n,success_mean,success_std,tool_calls_mean,"
                "tool_calls_std,wall_time_mean,wall_time_std\n")
        for cond in ["A", "B", "C", "D"]:
            s = np.array(data[cond]["success"]) if data[cond]["success"] else np.array([0])
            t = np.array(data[cond]["tool_calls"]) if data[cond]["tool_calls"] else np.array([0])
            w = np.array(data[cond]["wall_time_s"]) if data[cond]["wall_time_s"] else np.array([0])
            f.write(f"{cond},{CONDITIONS[cond]},{len(s)},{np.mean(s):.4f},"
                    f"{np.std(s):.4f},{np.mean(t):.4f},{np.std(t):.4f},"
                    f"{np.mean(w):.4f},{np.std(w):.4f}\n")

    print(f"\n  CSV exported: {csv_path}")
    print("\nAnalysis complete. Use ablation_results_summary.csv for paper figures.")


if __name__ == "__main__":
    run_analysis()
