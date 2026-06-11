"""
run_benchmark.py  —  Automated 25-task benchmark runner
════════════════════════════════════════════════════════
Runs all benchmark tasks against the live server and writes results to CSV.
The server must be running at http://localhost:8080.

Usage:
    python run_benchmark.py                    # all 25 tasks, 3 runs each
    python run_benchmark.py --task heat_01     # single task
    python run_benchmark.py --runs 1           # 1 run per task (quick)
    python run_benchmark.py --api-key SECRET   # if server has auth enabled

Output:
    benchmark_results/results_<timestamp>.csv
    benchmark_results/summary_<timestamp>.json

Outcome codes (from benchmark_tasks.py):
    SUCCESS        — all SuccessCriterion met within tolerance
    PARTIAL        — converged, one criterion missed by 5-20%
    FAILURE_LOUD   — exception raised or agent reported inability
    FAILURE_SILENT — converged=True but PhysicalConstraint violated
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, List, Optional

# Windows consoles default to cp1252, which has no glyphs for the ✓/✗/~/⚠ status
# symbols — printing them raised UnicodeEncodeError and aborted the whole run.
# Force UTF-8 (replace on any unmappable char) so the runner never dies on I/O.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_URL  = os.getenv("BENCHMARK_SERVER", "http://localhost:8080")
API_KEY     = os.getenv("API_SECRET_KEY", "")
TIMEOUT_S   = 300   # 5-minute timeout per task (complex flowsheets can be slow)
RUNS_DEFAULT = 3

# ── Import task definitions ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from benchmark_tasks import (BENCHMARK_TASKS, BenchmarkTask, SuccessCriterion,
                             _resolve_stream)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _post(path: str, body: dict, timeout: float = TIMEOUT_S) -> dict:
    url  = SERVER_URL + path
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}",
                "outcome": "FAILURE_LOUD"}
    except Exception as e:
        return {"success": False, "error": str(e), "outcome": "FAILURE_LOUD"}


def _get(path: str) -> dict:
    url = SERVER_URL + path
    req = urllib.request.Request(url, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _health_check() -> bool:
    r = _get("/health")
    return r.get("status") == "ok"


def _reset_chat():
    _post("/chat/reset", {})


def _reset_flowsheet():
    """Purge to an empty flowsheet so each task is independent. Without this,
    one task's flowsheet (tags, streams, solver state) bleeds into the next and
    later tasks get measured against the WRONG flowsheet."""
    r = _post("/flowsheet/new", {})
    return bool(r.get("success"))


# ─────────────────────────────────────────────────────────────────────────────
# SSE chat helper — reads the full streamed response
# ─────────────────────────────────────────────────────────────────────────────

def _run_chat(message: str) -> dict:
    """
    POST to /chat/stream and consume the SSE until 'done' or 'error'.
    Returns {"answer": str, "tool_calls": [...], "error": str|None,
             "elapsed_s": float}.
    """
    url  = SERVER_URL + "/chat/stream"
    data = json.dumps({"message": message}).encode()
    req  = urllib.request.Request(url, data=data, headers=_headers(), method="POST")

    t0 = time.monotonic()
    tool_calls: List[dict] = []
    answer = ""
    error  = None

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            buf = b""
            while True:
                chunk = r.read(1024)
                if not chunk:
                    break
                buf += chunk
                while b"\n\n" in buf:
                    line_block, buf = buf.split(b"\n\n", 1)
                    for line in line_block.split(b"\n"):
                        line = line.decode(errors="replace")
                        if not line.startswith("data: "):
                            continue
                        try:
                            evt = json.loads(line[6:])
                        except Exception:
                            continue
                        if evt.get("type") == "done":
                            answer = evt.get("data", "")
                        elif evt.get("type") == "error":
                            error  = evt.get("data", "unknown error")
                        elif evt.get("type") == "tool_call":
                            tool_calls.append(evt.get("data", {}))
    except Exception as exc:
        error = str(exc)

    return {
        "answer":     answer,
        "tool_calls": tool_calls,
        "error":      error,
        "elapsed_s":  round(time.monotonic() - t0, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Outcome evaluation
# ─────────────────────────────────────────────────────────────────────────────

def _get_stream_results() -> dict:
    """Fetch current simulation results for SuccessCriterion evaluation."""
    r = _get("/flowsheet/results")
    return r if r.get("success") else {}


def _stream_value(stream: dict, prop: str):
    """Read a criterion property from a /flowsheet/results stream record,
    mapping the criteria's friendly C/bar/kg-h names onto the SI keys the API
    actually returns (temperature_K, pressure_Pa, mass_flow_kg_s, …). Without
    this every quantitative criterion read None and the whole suite scored 0%."""
    if prop in stream:
        return stream[prop]

    def g(k):
        v = stream.get(k)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    p = prop.lower()
    T = g("temperature_K"); P = g("pressure_Pa")
    mf = g("mass_flow_kg_s"); nf = g("molar_flow_mol_s")
    table = {
        "temperature_c": (None if T is None else T - 273.15),
        "temperature":   T, "temperature_k": T,
        "pressure_bar":  (None if P is None else P / 1e5),
        "pressure_atm":  (None if P is None else P / 101325.0),
        "pressure_kpa":  (None if P is None else P / 1e3),
        "pressure":      P, "pressure_pa": P,
        "mass_flow_kgh": (None if mf is None else mf * 3600.0),
        "mass_flow_kg_h":(None if mf is None else mf * 3600.0),
        "mass_flow":     mf, "mass_flow_kg_s": mf,
        "molar_flow_kmolh": (None if nf is None else nf * 3.6),
        "molar_flow":    nf, "molar_flow_mol_s": nf,
    }
    if p in table:
        return table[p]
    # composition / mole-fraction style: try a few shapes
    comp = stream.get("composition") or stream.get("compositions") or {}
    if isinstance(comp, dict):
        for key in (prop, prop.split("_")[-1]):
            if key in comp:
                return comp[key]
    return None


def _evaluate_criterion(criterion: SuccessCriterion, stream_results: dict):
    """
    Check a single SuccessCriterion against the live simulation results.
    Returns (met: bool, reason: str). The reason makes a FAILURE_LOUD
    interpretable — distinguishing a genuine physics miss from a stream-naming
    mismatch or empty results, which otherwise look identical in the CSV.
    """
    want = f"{criterion.stream_tag}.{criterion.property} {criterion.operator} {criterion.value}"

    # Results arrive as the full /flowsheet/results dict ({stream_results: {...}})
    # OR an already-tag-keyed mapping (in-process path). Handle both.
    streams = (stream_results.get("stream_results")
               or stream_results.get("streams")
               or stream_results)
    if not isinstance(streams, dict):
        return False, f"{want}: no stream results returned"
    # Resolve by tag, tolerant of casing and role-equivalent names (the agent
    # may name the product stream "Outlet" etc.) — fair without false passes.
    s = _resolve_stream(streams, criterion.stream_tag)
    if not isinstance(s, dict):
        avail = ",".join(sorted(k for k in streams.keys() if isinstance(k, str))[:8])
        return False, f"{want}: stream '{criterion.stream_tag}' not found (have: {avail or 'none'})"

    val = _stream_value(s, criterion.property)
    if val is None:
        return False, f"{want}: property '{criterion.property}' unreadable"

    try:
        val    = float(val)
        target = float(criterion.value)
    except (TypeError, ValueError):
        return False, f"{want}: non-numeric (got {val!r})"

    tol = criterion.tolerance_pct / 100.0

    op = criterion.operator
    if op in ("==", "~="):
        met = abs(val - target) <= abs(target) * tol if target != 0 else abs(val) <= tol
    elif op == ">":
        met = val > target * (1 - tol)
    elif op == "<":
        met = val < target * (1 + tol)
    elif op == ">=":
        met = val >= target * (1 - tol)
    elif op == "<=":
        met = val <= target * (1 + tol)
    else:
        return False, f"{want}: unknown operator '{op}'"

    return met, f"{want}: got {val:.4g} ({'ok' if met else 'MISS'})"


# Known DWSIM property-package names, for the 'contains_PP_name' answer check.
_PP_NAMES = ("steam table", "iapws", "nrtl", "peng-robinson", "peng robinson",
             "pr ", "srk", "soave", "uniquac", "unifac", "raoult", "wilson",
             "lee-kesler", "chao-seader", "grayson", "cpa", "pc-saft", "saft")

# Keyword heuristics for the qualitative 'response' criteria. These are NOT
# physics checks — they grade the analysis text. Kept deliberately conservative
# and reported SEPARATELY from quantitative criteria so the headline pass-rate
# (DWSIM-verified physics) is never inflated by fuzzy keyword matching.
_RESPONSE_KEYWORDS = {
    "contains_pp_name":        _PP_NAMES,
    "mentions_convergence":    ("converg", "solved", "solution found"),
    "achieves_convergence":    ("converg", "solved successfully", "now solves"),
    "mentions_phase_transition": ("phase", "boil", "bubble point", "dew point",
                                  "vapor", "vaporis", "vaporiz", "condens"),
    "monotonic_trend":         ("increas", "decreas", "monotonic", "rises", "falls"),
    "correct_direction":       ("increas", "decreas", "higher", "lower", "rises", "falls"),
    "optimal_t_identified":    ("optim", "optimum", "optimal"),
    "reports_purity_increase": ("purity", "purer", "more pure"),
    "diagnoses_cause":         ("because", "cause", "due to", "reason", "caused by",
                                "root cause"),
    "attempts_fix":            ("fix", "adjust", "chang", "correct", "increas",
                                "decreas", "modif", "set "),
    "reports_strategy":        ("strateg", "approach", "method", "step", "first",
                                "then", "tear", "initial guess"),
}


def _evaluate_response_criterion(criterion: SuccessCriterion, answer: str):
    """Qualitative answer-content check. Returns (met, reason)."""
    prop = (criterion.property or "").lower()
    a = (answer or "").lower()
    kws = _RESPONSE_KEYWORDS.get(prop)
    if not kws:
        return False, f"response.{criterion.property}: no keyword rule (unscored)"
    hit = next((k for k in kws if k in a), None)
    # 'optimal_T_identified' additionally wants a number present.
    if prop == "optimal_t_identified" and hit:
        import re as _re
        if not _re.search(r"\d", a):
            hit = None
    if hit:
        return True, f"response.{criterion.property}: matched '{hit.strip()}'"
    return False, f"response.{criterion.property}: no keyword match (MISS)"


def _evaluate_any_criterion(criterion: SuccessCriterion, stream_results: dict):
    """'any' sentinel: criterion met if ANY stream satisfies it. (met, reason)."""
    streams = (stream_results.get("stream_results")
               or stream_results.get("streams")
               or stream_results)
    if not isinstance(streams, dict) or not streams:
        return False, f"any.{criterion.property}: no stream results"
    for tag, s in streams.items():
        if not isinstance(s, dict):
            continue
        probe = SuccessCriterion(stream_tag=tag, property=criterion.property,
                                 operator=criterion.operator, value=criterion.value,
                                 tolerance_pct=criterion.tolerance_pct)
        met, _ = _evaluate_criterion(probe, {tag: s})
        if met:
            return True, f"any.{criterion.property}: satisfied by '{tag}'"
    return False, f"any.{criterion.property}: no stream matched {criterion.operator} {criterion.value}"


def _is_qualitative(criterion: SuccessCriterion) -> bool:
    return criterion.stream_tag == "response"


def _determine_outcome(
    task: BenchmarkTask,
    chat_result: dict,
    stream_results: dict,
):
    """
    Map a completed task run to an outcome code.
    Returns (outcome, detail) where detail explains WHY — essential for telling
    a real agent failure apart from a stream-naming mismatch or transport error.
    SUCCESS / PARTIAL / FAILURE_LOUD / FAILURE_SILENT
    """
    _empty = {"quant_met": 0, "quant_tot": 0, "qual_met": 0, "qual_tot": 0}
    if chat_result.get("error"):
        return "FAILURE_LOUD", f"transport/agent error: {chat_result['error']}", _empty

    answer = (chat_result.get("answer") or "").lower()
    for phrase in ["i cannot", "i am unable", "failed to", "error occurred",
                   "could not complete", "unable to build"]:
        if phrase in answer:
            return "FAILURE_LOUD", f"agent reported inability ('{phrase}')", _empty

    # Evaluate SuccessCriterion objects. Route by kind: stream-value (physics,
    # checked against live DWSIM), 'any' (any stream), or 'response' (qualitative
    # answer-content, keyword-scored). Track quantitative vs qualitative counts
    # so the caller can report a DWSIM-verified pass-rate that fuzzy keyword
    # scoring can never inflate.
    answer_raw = chat_result.get("answer") or ""
    reasons = []
    criteria_results = []
    quant_met = quant_tot = 0
    qual_met = qual_tot = 0
    for criterion in task.success_criteria:
        if criterion.stream_tag == "response":
            met, reason = _evaluate_response_criterion(criterion, answer_raw)
            qual_tot += 1
            qual_met += int(met)
        elif criterion.stream_tag == "any":
            met, reason = _evaluate_any_criterion(criterion, stream_results)
            quant_tot += 1
            quant_met += int(met)
        else:
            met, reason = _evaluate_criterion(criterion, stream_results)
            quant_tot += 1
            quant_met += int(met)
        criteria_results.append(met)
        reasons.append(reason)
    detail = (f"[quant {quant_met}/{quant_tot} qual {qual_met}/{qual_tot}] "
              + " ; ".join(reasons))
    stats = {"quant_met": quant_met, "quant_tot": quant_tot,
             "qual_met": qual_met, "qual_tot": qual_tot}

    if not criteria_results:
        # No quantitative criteria — treat as SUCCESS if no error
        return "SUCCESS", "no criteria defined (answer-only task)", stats

    n_met = sum(criteria_results)
    n_tot = len(criteria_results)

    if n_met == n_tot:
        return "SUCCESS", detail, stats
    elif n_met >= n_tot - 1 and n_tot > 1:
        return "PARTIAL", detail, stats     # one criterion missed
    else:
        return "FAILURE_LOUD", detail, stats


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def _skip_record(task: BenchmarkTask, run_number: int, reason: str) -> dict:
    """A task that cannot be measured (missing/mismatched fixture). Counted
    separately from SUCCESS/FAILURE so it never distorts the agent pass-rate."""
    return {
        "task_id": task.task_id, "category": task.category,
        "complexity": task.complexity, "run": run_number,
        "outcome": "SKIPPED", "elapsed_s": 0.0, "n_tools": 0,
        "tools_used": "", "error": "", "quant_met": 0, "quant_tot": 0,
        "qual_met": 0, "qual_tot": 0, "detail": reason,
        "answer_words": 0, "timestamp": datetime.utcnow().isoformat(),
    }


def run_task(task: BenchmarkTask, run_number: int, verbose: bool = True) -> dict:
    """Run one benchmark task and return a result record."""
    if verbose:
        print(f"  Run {run_number}: {task.task_id} | {task.prompt[:60]}...")

    # Independence: purge the flowsheet AND clear chat before each run. Resetting
    # only the chat (the old behaviour) left the previous task's flowsheet live,
    # so later tasks were scored against the wrong flowsheet — the single biggest
    # cause of the suite's artificially low score.
    fs_reset = _reset_flowsheet()
    _reset_chat()
    if verbose and not fs_reset:
        print("    (warning: /flowsheet/new did not confirm reset)")

    # Fixture preload: analysis/modification tasks phrased as "the loaded
    # flowsheet" have nothing to analyse once tasks are isolated. Load the
    # declared fixture. If the fixture is MISSING (load fails) or MISMATCHED
    # (loads, but the task's required named streams aren't in it), the task is
    # unmeasurable through no fault of the agent — mark it SKIPPED and exclude
    # it from the pass-rate, rather than scoring the agent against missing data.
    setup = getattr(task, "setup_load", "") or ""
    requires = getattr(task, "requires_fixture", False)
    if requires and not setup:
        # Needs a pre-existing flowsheet but no fixture is wired — unmeasurable.
        reason = ("requires a pre-loaded flowsheet fixture not present in repo "
                  "(references 'the loaded flowsheet' / fixture-specific streams)")
        if verbose:
            print(f"    ⊘ SKIPPED — {reason}")
        return _skip_record(task, run_number, reason)
    if setup:
        lr = _post("/flowsheet/load", {"path": setup})
        skip_reason = None
        if not lr.get("success"):
            skip_reason = f"fixture '{setup}' could not be loaded ({lr.get('error')})"
        else:
            # Named streams the task's quantitative criteria require (excluding
            # the 'any'/'response' sentinels).
            need = {c.stream_tag for c in task.success_criteria
                    if c.stream_tag not in ("any", "response")}
            if need:
                sr = _get_stream_results()
                have = set((sr.get("stream_results") or sr.get("streams") or {}).keys())
                missing = need - have
                if missing:
                    skip_reason = (f"fixture '{setup}' lacks required stream(s): "
                                   f"{','.join(sorted(missing))} (have: "
                                   f"{','.join(sorted(have)) or 'none'})")
        if skip_reason:
            if verbose:
                print(f"    ⊘ SKIPPED — {skip_reason}")
            return _skip_record(task, run_number, skip_reason)

    time.sleep(1.0)        # small pause to let server settle

    chat_result          = _run_chat(task.prompt)
    stream_results       = _get_stream_results()
    outcome, detail, st  = _determine_outcome(task, chat_result, stream_results)

    n_tools = len(chat_result.get("tool_calls", []))
    tool_names = [tc.get("name", "") for tc in chat_result.get("tool_calls", [])]

    record = {
        "task_id":       task.task_id,
        "category":      task.category,
        "complexity":    task.complexity,
        "run":           run_number,
        "outcome":       outcome,
        "elapsed_s":     chat_result["elapsed_s"],
        "n_tools":       n_tools,
        "tools_used":    "|".join(tool_names),
        "error":         chat_result.get("error") or "",
        "quant_met":     st["quant_met"],
        "quant_tot":     st["quant_tot"],
        "qual_met":      st["qual_met"],
        "qual_tot":      st["qual_tot"],
        "detail":        detail,
        "answer_words":  len((chat_result.get("answer") or "").split()),
        "timestamp":     datetime.utcnow().isoformat(),
    }

    status_symbol = {"SUCCESS": "✓", "PARTIAL": "~", "FAILURE_LOUD": "✗",
                     "FAILURE_SILENT": "⚠", "SKIPPED": "⊘"}.get(outcome, "?")
    if verbose:
        print(f"    {status_symbol} {outcome} | {chat_result['elapsed_s']:.1f}s | {n_tools} tools")

    return record


def run_all(
    tasks:    List[BenchmarkTask],
    runs:     int,
    out_dir:  str,
    verbose:  bool = True,
) -> None:
    ts    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path  = os.path.join(out_dir, f"results_{ts}.csv")
    json_path = os.path.join(out_dir, f"summary_{ts}.json")

    os.makedirs(out_dir, exist_ok=True)

    all_records: List[dict] = []
    outcome_counts: Dict[str, int] = {}

    total = len(tasks) * runs
    done  = 0

    for task in tasks:
        if verbose:
            print(f"\n[{task.task_id}]  cat={task.category}  complexity={task.complexity}")

        for run_n in range(1, runs + 1):
            record = run_task(task, run_n, verbose=verbose)
            all_records.append(record)
            outcome_counts[record["outcome"]] = outcome_counts.get(record["outcome"], 0) + 1
            done += 1

            # Write CSV incrementally so partial results survive crashes
            write_header = not os.path.exists(csv_path)
            with open(csv_path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=list(record.keys()))
                if write_header:
                    writer.writeheader()
                writer.writerow(record)

    # ── Summary ───────────────────────────────────────────────────────────────
    # SKIPPED tasks (missing/mismatched fixtures) are excluded from the pass-rate
    # denominator — they measure the test data, not the agent.
    n_skipped = outcome_counts.get("SKIPPED", 0)
    scored    = [r for r in all_records if r["outcome"] != "SKIPPED"]
    n_success = outcome_counts.get("SUCCESS", 0) + outcome_counts.get("PARTIAL", 0)
    n_total   = len(scored)
    rate      = n_success / n_total * 100 if n_total else 0.0

    avg_time  = sum(r["elapsed_s"] for r in scored) / n_total if n_total else 0.0
    avg_tools = sum(r["n_tools"]   for r in scored) / n_total if n_total else 0.0

    # DWSIM-verified physics pass-rate: fraction of QUANTITATIVE criteria met,
    # checked against live simulation results. This is the defensible headline
    # number — it cannot be inflated by the qualitative keyword scoring.
    q_met = sum(r.get("quant_met", 0) for r in all_records)
    q_tot = sum(r.get("quant_tot", 0) for r in all_records)
    ql_met = sum(r.get("qual_met", 0) for r in all_records)
    ql_tot = sum(r.get("qual_tot", 0) for r in all_records)

    summary = {
        "timestamp":      ts,
        "n_tasks":        len(tasks),
        "n_runs":         runs,
        "n_total_runs":   len(all_records),
        "n_scored":       n_total,
        "n_skipped":      n_skipped,
        "outcome_counts": outcome_counts,
        "success_rate_pct": round(rate, 2),
        "quant_criteria_pass_pct": round(q_met / q_tot * 100, 2) if q_tot else None,
        "quant_criteria": f"{q_met}/{q_tot}",
        "qual_criteria_pass_pct": round(ql_met / ql_tot * 100, 2) if ql_tot else None,
        "qual_criteria": f"{ql_met}/{ql_tot}",
        "avg_elapsed_s":  round(avg_time, 2),
        "avg_tools_used": round(avg_tools, 2),
        "csv_path":       csv_path,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 60)
    print(f"  Tasks:        {len(tasks)} × {runs} runs = {len(all_records)} total"
          + (f"  ({n_skipped} skipped — fixture missing)" if n_skipped else ""))
    print(f"  Success rate: {rate:.1f}%  "
          f"({outcome_counts.get('SUCCESS',0)} SUCCESS + "
          f"{outcome_counts.get('PARTIAL',0)} PARTIAL / {n_total} scored)")
    if q_tot:
        print(f"  Physics pass: {q_met}/{q_tot} quantitative criteria "
              f"({q_met/q_tot*100:.1f}%) — DWSIM-verified")
    if ql_tot:
        print(f"  Analysis:     {ql_met}/{ql_tot} qualitative criteria "
              f"({ql_met/ql_tot*100:.1f}%) — keyword-scored")
    print(f"  Avg time:     {avg_time:.1f}s per run")
    print(f"  Outcomes:     {outcome_counts}")
    print(f"\n  CSV:          {csv_path}")
    print(f"  JSON summary: {json_path}")
    print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global SERVER_URL, API_KEY   # declared first — used below before assignment
    parser = argparse.ArgumentParser(
        description="Run DWSIM Agentic AI benchmark tasks against the live server."
    )
    parser.add_argument("--task",    default=None,
                        help="Run a single task by task_id (e.g. heat_01)")
    parser.add_argument("--runs",    type=int, default=RUNS_DEFAULT,
                        help=f"Runs per task (default {RUNS_DEFAULT})")
    parser.add_argument("--cat",     default=None,
                        help="Filter by category (e.g. single_unit_creation)")
    parser.add_argument("--complexity", type=int, default=None,
                        help="Filter by complexity level (1, 2, or 3)")
    parser.add_argument("--out-dir", default="benchmark_results",
                        help="Output directory for CSV and JSON")
    parser.add_argument("--api-key", default="",
                        help="X-API-Key for the server (or set API_SECRET_KEY env var)")
    parser.add_argument("--server",  default=SERVER_URL,
                        help=f"Server base URL (default {SERVER_URL})")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress per-task output")
    args = parser.parse_args()

    SERVER_URL = args.server
    if args.api_key:
        API_KEY = args.api_key

    print(f"Server: {SERVER_URL}")
    if not _health_check():
        print("ERROR: Server is not reachable or not healthy.")
        print(f"  Make sure the DWSIM server is running: python api.py")
        sys.exit(1)
    print("Server health: OK\n")

    # Filter tasks
    tasks = list(BENCHMARK_TASKS)
    if args.task:
        tasks = [t for t in tasks if t.task_id == args.task]
        if not tasks:
            print(f"ERROR: task_id '{args.task}' not found.")
            sys.exit(1)
    if args.cat:
        tasks = [t for t in tasks if t.category == args.cat]
    if args.complexity:
        tasks = [t for t in tasks if t.complexity == args.complexity]

    if not tasks:
        print("No tasks matched the given filters.")
        sys.exit(1)

    print(f"Running {len(tasks)} task(s) × {args.runs} run(s) = "
          f"{len(tasks) * args.runs} total evaluations\n")

    run_all(tasks, args.runs, args.out_dir, verbose=not args.quiet)


if __name__ == "__main__":
    main()
