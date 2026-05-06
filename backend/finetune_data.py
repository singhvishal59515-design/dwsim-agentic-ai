"""
finetune_data.py  —  DWSIM Finetuning Data Pipeline
─────────────────────────────────────────────────────
Following Chip Huyen AI Engineering Ch.7 (When to Finetune) and
Ch.8 (Dataset Engineering):
  "High-quality data beats large quantities of low-quality data."
  "Filter aggressively: only include sessions where you KNOW the answer is correct."

This pipeline:
1. Reads replay_log.jsonl (turn-by-turn agent sessions)
2. Filters to high-quality turns (converged=True, no SF violations, tool sequence valid)
3. Exports as OpenAI finetuning JSONL format (messages array per turn)
4. Reports dataset statistics and quality metrics

Output format (OpenAI chat completion finetuning):
  {"messages": [
      {"role": "system", "content": "<system prompt>"},
      {"role": "user",   "content": "<user query>"},
      {"role": "assistant", "content": "<agent final answer>"}
  ]}

Usage:
  python finetune_data.py --output finetune_dataset.jsonl
  python finetune_data.py --stats   # just show dataset statistics
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Quality filters (book Ch.8: "Filter aggressively") ───────────────────────

# Minimum turn quality criteria for inclusion in finetuning dataset
MIN_QUALITY = {
    "must_converge":         True,    # simulation must have converged
    "max_sf_violations":     0,       # zero safety failures allowed
    "min_tool_calls":        2,       # at least 2 tool calls (non-trivial)
    "max_tool_calls":        30,      # cap on over-complicated sessions
    "min_answer_length":     50,      # final answer must be substantive (chars)
    "required_tools_any":    [        # at least one of these must have been called
        "save_and_solve", "run_simulation", "get_simulation_results"
    ],
    "exclude_error_keywords": [       # reject answers containing these
        "I cannot", "I am unable", "not possible", "don't know",
        "no flowsheet", "failed to", "error occurred",
    ],
}


@dataclass
class TurnQuality:
    turn_id:       str
    session_id:    str
    passed:        bool
    reasons:       List[str]   # why it passed or failed
    quality_score: float       # 0.0-1.0


def _load_replay_log(log_dir: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load all turns from replay_log.jsonl."""
    if log_dir is None:
        log_dir = os.environ.get("DWSIM_REPLAY_LOG_DIR", _DIR)
    log_file = os.path.join(log_dir, "replay_turns.jsonl")
    if not os.path.exists(log_file):
        return []
    turns = []
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    turns.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return turns


def _score_turn(turn: Dict[str, Any]) -> TurnQuality:
    """
    Score a single turn for finetuning quality.
    Returns TurnQuality with pass/fail and detailed reasons.
    """
    reasons: List[str] = []
    score = 1.0
    passed = True

    turn_id    = turn.get("turn_id", "unknown")
    session_id = turn.get("session_id", "unknown")
    converged  = turn.get("converged", False)
    sf_viols   = turn.get("sf_violations", [])
    tool_calls = turn.get("tool_calls", [])
    answer     = turn.get("final_answer", "") or ""
    user_msg   = turn.get("user_prompt", "") or ""

    # Must have converged
    if MIN_QUALITY["must_converge"] and not converged:
        passed = False
        reasons.append("FAIL: simulation did not converge")
        score -= 0.4

    # Zero SF violations
    n_viols = len(sf_viols) if isinstance(sf_viols, list) else 0
    if n_viols > MIN_QUALITY["max_sf_violations"]:
        passed = False
        reasons.append(f"FAIL: {n_viols} safety violations detected")
        score -= 0.3

    # Tool call count
    n_tools = len(tool_calls)
    if n_tools < MIN_QUALITY["min_tool_calls"]:
        passed = False
        reasons.append(f"FAIL: only {n_tools} tool calls (need >={MIN_QUALITY['min_tool_calls']})")
        score -= 0.2
    if n_tools > MIN_QUALITY["max_tool_calls"]:
        passed = False
        reasons.append(f"FAIL: {n_tools} tool calls exceeds max {MIN_QUALITY['max_tool_calls']}")
        score -= 0.1

    # Required tool presence
    tool_names = {tc.get("tool_name", "") for tc in tool_calls if isinstance(tc, dict)}
    has_required = any(t in tool_names for t in MIN_QUALITY["required_tools_any"])
    if not has_required:
        passed = False
        reasons.append("FAIL: no simulation tool called (save_and_solve/run_simulation)")
        score -= 0.3

    # Answer quality
    if len(answer) < MIN_QUALITY["min_answer_length"]:
        passed = False
        reasons.append(f"FAIL: answer too short ({len(answer)} chars)")
        score -= 0.2

    for kw in MIN_QUALITY["exclude_error_keywords"]:
        if kw.lower() in answer.lower():
            passed = False
            reasons.append(f"FAIL: answer contains rejection phrase '{kw}'")
            score -= 0.3
            break

    # Missing user prompt
    if not user_msg.strip():
        passed = False
        reasons.append("FAIL: empty user prompt")
        score -= 0.5

    if passed:
        reasons.append("PASS: meets all quality criteria")

    return TurnQuality(
        turn_id       = turn_id,
        session_id    = session_id,
        passed        = passed,
        reasons       = reasons,
        quality_score = max(0.0, min(1.0, score)),
    )


def _turn_to_messages(turn: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a replay turn to OpenAI finetuning message format.
    Uses the system prompt from the turn's sys_hash field if available,
    otherwise uses a compact version of the base system prompt.
    """
    user_prompt  = turn.get("user_prompt", "")
    final_answer = turn.get("final_answer", "")

    # Reconstruct tool call sequence as assistant reasoning context
    tool_calls = turn.get("tool_calls", [])
    tool_summary = ""
    if tool_calls:
        successful_tools = [
            tc.get("tool_name", "") for tc in tool_calls
            if isinstance(tc, dict) and tc.get("success", True)
        ]
        if successful_tools:
            tool_summary = f"\n[Tools used: {', '.join(successful_tools[:8])}]"

    # System message: compact DWSIM expert identity
    system_content = (
        "You are an expert DWSIM chemical process simulation assistant. "
        "You use DWSIM tools to build, configure, and analyze process simulations. "
        "Always verify compound properties (Tc, Pc, omega) before creating flowsheets. "
        "Choose property packages correctly: PR for hydrocarbons, NRTL/UNIQUAC for polar mixtures. "
        "Report results in both SI and engineering units."
    )

    return {
        "messages": [
            {"role": "system",    "content": system_content},
            {"role": "user",      "content": user_prompt},
            {"role": "assistant", "content": final_answer + tool_summary},
        ]
    }


def build_dataset(
    log_dir:    Optional[str] = None,
    output:     Optional[str] = None,
    min_score:  float         = 0.7,
    max_turns:  int           = 2000,
    stats_only: bool          = False,
) -> Dict[str, Any]:
    """
    Build the finetuning dataset from replay logs.

    Returns statistics dict. If output is provided and stats_only=False,
    writes the JSONL finetuning file.
    """
    turns = _load_replay_log(log_dir)
    if not turns:
        return {
            "total_turns": 0,
            "passed": 0,
            "rejected": 0,
            "output_file": None,
            "message": "No replay turns found. Run simulations to collect data.",
        }

    # Score all turns
    scored: List[Tuple[Dict, TurnQuality]] = []
    for turn in turns:
        quality = _score_turn(turn)
        scored.append((turn, quality))

    passed_turns  = [(t, q) for t, q in scored if q.passed and q.quality_score >= min_score]
    rejected_turns = [(t, q) for t, q in scored if not (q.passed and q.quality_score >= min_score)]

    # Sort by quality score (best first) and cap
    passed_turns.sort(key=lambda x: -x[1].quality_score)
    selected = passed_turns[:max_turns]

    # Rejection reason summary
    rejection_reasons: Dict[str, int] = {}
    for _, q in rejected_turns:
        for r in q.reasons:
            if r.startswith("FAIL:"):
                reason_key = r.split(":")[1].strip()[:50]
                rejection_reasons[reason_key] = rejection_reasons.get(reason_key, 0) + 1

    stats = {
        "total_turns":       len(turns),
        "passed":            len(passed_turns),
        "selected":          len(selected),
        "rejected":          len(rejected_turns),
        "pass_rate_pct":     round(len(passed_turns) / max(len(turns), 1) * 100, 1),
        "avg_quality_score": round(
            sum(q.quality_score for _, q in selected) / max(len(selected), 1), 3
        ),
        "rejection_reasons": dict(sorted(rejection_reasons.items(), key=lambda x: -x[1])[:10]),
        "output_file":       None,
        "message":           f"Dataset ready: {len(selected)} high-quality turns selected.",
    }

    if stats_only or not selected:
        return stats

    # Write JSONL finetuning file
    out_path = output or os.path.join(_DIR, "finetune_dataset.jsonl")
    with open(out_path, "w", encoding="utf-8") as f:
        for turn, quality in selected:
            record = _turn_to_messages(turn)
            record["_quality_score"] = quality.quality_score
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    stats["output_file"] = out_path
    stats["message"] = (
        f"Wrote {len(selected)} finetuning examples to {out_path}. "
        f"Ready for: openai api fine_tuning.jobs.create --training-file {out_path}"
    )
    return stats


def estimate_finetuning_cost(n_examples: int, avg_tokens_per_example: int = 800) -> Dict:
    """
    Estimate OpenAI finetuning cost for a dataset.
    Based on OpenAI pricing: $8/1M training tokens (gpt-4o-mini), $25/1M (gpt-4o).
    """
    total_tokens = n_examples * avg_tokens_per_example
    return {
        "n_examples":          n_examples,
        "avg_tokens_example":  avg_tokens_per_example,
        "total_tokens":        total_tokens,
        "cost_gpt4o_mini_usd": round(total_tokens / 1_000_000 * 8.0, 2),
        "cost_gpt4o_usd":      round(total_tokens / 1_000_000 * 25.0, 2),
        "recommended_epochs":  3,
        "note": (
            "Minimum 10 examples required. "
            f"With {n_examples} examples at 3 epochs: "
            f"${round(total_tokens * 3 / 1_000_000 * 8.0, 2)} (gpt-4o-mini). "
            "Chip Huyen Ch.7: start with gpt-4o-mini finetuning before full gpt-4o."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DWSIM Finetuning Data Pipeline")
    parser.add_argument("--output",  default=None, help="Output JSONL file path")
    parser.add_argument("--logdir",  default=None, help="Replay log directory")
    parser.add_argument("--stats",   action="store_true", help="Show stats only, no file write")
    parser.add_argument("--minscore",type=float, default=0.7, help="Min quality score (0-1)")
    parser.add_argument("--cost",    action="store_true", help="Estimate finetuning cost")
    args = parser.parse_args()

    result = build_dataset(
        log_dir    = args.logdir,
        output     = args.output,
        min_score  = args.minscore,
        stats_only = args.stats,
    )

    print(json.dumps(result, indent=2))

    if args.cost and result.get("selected", 0) > 0:
        cost = estimate_finetuning_cost(result["selected"])
        print("\nFinetuning cost estimate:")
        print(json.dumps(cost, indent=2))
