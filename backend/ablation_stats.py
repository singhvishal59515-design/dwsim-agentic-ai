"""
ablation_stats.py
─────────────────
Statistics for the Phase-4 ablation. Given per-run records tagged with their
ablation `condition`, for each metric it runs:

  1. Kruskal-Wallis H-test across all conditions (non-parametric omnibus —
     does ANY condition differ?). Non-parametric because success is binary and
     tool-counts / wall-times are skewed and small-n.
  2. If the omnibus is significant, pairwise Mann-Whitney U tests with
     Holm-Bonferroni correction (controls family-wise error without the
     conservatism of plain Bonferroni).
  3. Cohen's d effect size for every pair (magnitude, not just significance).

Exact p-values are reported (not just "< 0.05"), as journals require.

Inputs (either):
  --replay   path to a replay_log.jsonl  (metrics derived per turn: success≈
             converged, tool_calls, wall_time_s, llm_calls, safety_violations)
  --records  path to a JSON list of {"condition": str, <metric>: number, ...}

    python ablation_stats.py --replay ~/.dwsim_agent/replay/replay_log.jsonl
    python ablation_stats.py --records results.json --metrics success tool_calls

Designed to be import-tested on synthetic records (the analysis is pure given
the numbers); only `scipy` is required, which the runtime interpreter has.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence

DEFAULT_METRICS = ["success", "tool_calls", "wall_time_s", "llm_calls",
                   "safety_violations"]
# Canonical condition order for stable reporting.
CONDITION_ORDER = ["full", "no_rag", "no_safety", "direct_llm"]


# ─────────────────────────────────────────────────────────────────────────────
# Effect size + multiple-comparison correction (pure python)
# ─────────────────────────────────────────────────────────────────────────────

def cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Cohen's d with pooled SD. None if undefined (too few points / zero var)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    pooled = ((na - 1) * va + (nb - 1) * vb) / (na + nb - 2)
    if pooled <= 0:
        return 0.0 if ma == mb else None
    return (ma - mb) / math.sqrt(pooled)


def holm_bonferroni(pairs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add `p_holm` and `significant` (at 0.05) to each pair dict in-place.
    Holm step-down: sort ascending by raw p, threshold alpha/(m-i)."""
    m = len(pairs)
    order = sorted(range(m), key=lambda i: pairs[i]["p_raw"])
    running_max = 0.0
    for rank, idx in enumerate(order):
        adj = (m - rank) * pairs[idx]["p_raw"]
        running_max = max(running_max, adj)   # enforce monotonicity
        pairs[idx]["p_holm"] = min(1.0, running_max)
    for p in pairs:
        p["significant"] = p["p_holm"] < 0.05
    return pairs


# ─────────────────────────────────────────────────────────────────────────────
# Core analysis
# ─────────────────────────────────────────────────────────────────────────────

def _grouped(records: List[Dict[str, Any]], metric: str
             ) -> "Dict[str, List[float]]":
    groups: Dict[str, List[float]] = {}
    for r in records:
        cond = r.get("condition")
        val = r.get(metric)
        if cond is None or val is None:
            continue
        try:
            groups.setdefault(cond, []).append(float(val))
        except (TypeError, ValueError):
            continue
    return groups


def analyze_metric(records: List[Dict[str, Any]], metric: str,
                   alpha: float = 0.05) -> Dict[str, Any]:
    from scipy import stats  # local import so the module loads without scipy

    groups = _grouped(records, metric)
    conds = [c for c in CONDITION_ORDER if c in groups] + \
            [c for c in groups if c not in CONDITION_ORDER]
    out: Dict[str, Any] = {
        "metric": metric,
        "n_by_condition": {c: len(groups[c]) for c in conds},
        "mean_by_condition": {c: round(sum(groups[c]) / len(groups[c]), 4)
                              for c in conds if groups[c]},
    }
    usable = [c for c in conds if len(groups[c]) >= 1]
    if len(usable) < 2:
        out["error"] = "need >= 2 conditions with data"
        return out

    # Omnibus Kruskal-Wallis (needs >= 2 groups; guard all-identical input).
    try:
        h, p = stats.kruskal(*[groups[c] for c in usable])
        out["kruskal"] = {"H": round(float(h), 4), "p": float(p)}
    except Exception as exc:  # e.g. all values identical
        out["kruskal"] = {"H": None, "p": None, "note": str(exc)}

    # Pairwise Mann-Whitney U + Cohen's d (always computed; significance only
    # claimed when the omnibus is significant, per standard practice).
    pairs: List[Dict[str, Any]] = []
    for a, b in combinations(usable, 2):
        rec = {"a": a, "b": b,
               "mean_a": round(sum(groups[a]) / len(groups[a]), 4),
               "mean_b": round(sum(groups[b]) / len(groups[b]), 4),
               "cohens_d": cohens_d(groups[a], groups[b])}
        try:
            u, pu = stats.mannwhitneyu(groups[a], groups[b],
                                       alternative="two-sided")
            rec["U"] = round(float(u), 2)
            rec["p_raw"] = float(pu)
        except Exception as exc:
            rec["U"] = None
            rec["p_raw"] = 1.0
            rec["note"] = str(exc)
        pairs.append(rec)
    holm_bonferroni(pairs)

    omnibus_p = out.get("kruskal", {}).get("p")
    out["omnibus_significant"] = bool(omnibus_p is not None and omnibus_p < alpha)
    if not out["omnibus_significant"]:
        for p in pairs:
            p["significant"] = False  # don't claim pairwise wins without omnibus
    out["pairwise"] = pairs
    return out


def analyze(records: List[Dict[str, Any]], metrics: Sequence[str],
            alpha: float = 0.05) -> Dict[str, Any]:
    return {
        "n_records": len(records),
        "conditions": sorted({r.get("condition") for r in records
                              if r.get("condition")}),
        "alpha": alpha,
        "metrics": {m: analyze_metric(records, m, alpha) for m in metrics},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Input loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_records_from_replay(path: str) -> List[Dict[str, Any]]:
    """Derive per-turn metric records from a replay_log.jsonl. Only turns that
    carry an ablation `condition` are included."""
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not d.get("condition"):
                continue
            records.append({
                "condition":          d.get("condition"),
                "task_id":            d.get("task_id"),
                "rep":                d.get("rep"),
                "success":            1 if d.get("converged") else 0,
                "tool_calls":         len(d.get("tool_sequence") or []),
                "wall_time_s":        d.get("duration_s"),
                "llm_calls":          d.get("llm_calls"),
                "safety_violations":  len(d.get("sf_violations") or []),
            })
    return records


def load_records(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "records" in data:
        data = data["records"]
    if not isinstance(data, list):
        raise ValueError("records file must be a JSON list (or {records: [...]})")
    return data


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────

def format_report(results: Dict[str, Any]) -> str:
    L = ["# Ablation statistics", "",
         f"Records: {results['n_records']} | Conditions: "
         f"{', '.join(results['conditions'])} | α = {results['alpha']}", ""]
    for metric, m in results["metrics"].items():
        L.append(f"## {metric}")
        if m.get("error"):
            L.append(f"- {m['error']}"); L.append(""); continue
        means = ", ".join(f"{c}={v}" for c, v in m.get("mean_by_condition", {}).items())
        L.append(f"- means: {means}")
        k = m.get("kruskal", {})
        if k.get("p") is not None:
            sig = "significant" if m.get("omnibus_significant") else "n.s."
            L.append(f"- Kruskal-Wallis: H={k['H']}, p={k['p']:.4g} ({sig})")
        else:
            L.append(f"- Kruskal-Wallis: n/a ({k.get('note','')})")
        for p in m.get("pairwise", []):
            d = p.get("cohens_d")
            d_s = f"{d:+.2f}" if isinstance(d, (int, float)) else "n/a"
            star = " *" if p.get("significant") else ""
            ph = p.get("p_holm")
            ph_s = f"{ph:.4g}" if isinstance(ph, (int, float)) else "n/a"
            L.append(f"    {p['a']} vs {p['b']}: "
                     f"p_raw={p.get('p_raw',1):.4g}, p_holm={ph_s}, d={d_s}{star}")
        L.append("")
    L.append("(* = significant after Holm correction, and only when the "
             "omnibus Kruskal-Wallis is significant.)")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ablation statistics")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--replay", help="path to replay_log.jsonl")
    src.add_argument("--records", help="path to JSON list of metric records")
    ap.add_argument("--metrics", nargs="*", default=DEFAULT_METRICS)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--out", default=None, help="write the report to this path")
    args = ap.parse_args()

    records = (load_records_from_replay(args.replay) if args.replay
               else load_records(args.records))
    if not records:
        print("No tagged records found (need turns with an ablation `condition`).")
        return 1
    results = analyze(records, args.metrics, args.alpha)
    report = format_report(results)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n[written] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
