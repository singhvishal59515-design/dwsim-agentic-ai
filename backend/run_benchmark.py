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

# ── Config ────────────────────────────────────────────────────────────────────
SERVER_URL  = os.getenv("BENCHMARK_SERVER", "http://localhost:8080")
API_KEY     = os.getenv("API_SECRET_KEY", "")
TIMEOUT_S   = 300   # 5-minute timeout per task (complex flowsheets can be slow)
RUNS_DEFAULT = 3

# ── Import task definitions ───────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
from benchmark_tasks import BENCHMARK_TASKS, BenchmarkTask, SuccessCriterion


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


def _evaluate_criterion(criterion: SuccessCriterion, stream_results: dict) -> bool:
    """
    Check a single SuccessCriterion against the live simulation results.
    Returns True if the criterion is met within tolerance.
    """
    streams = stream_results.get("streams", {})
    if criterion.stream_tag not in streams:
        return False

    s   = streams[criterion.stream_tag]
    val = s.get(criterion.property)
    if val is None:
        return False

    try:
        val    = float(val)
        target = float(criterion.value)
    except (TypeError, ValueError):
        return False

    tol = criterion.tolerance_pct / 100.0

    op = criterion.operator
    if op == "==":
        return abs(val - target) <= abs(target) * tol if target != 0 else abs(val) <= tol
    elif op == ">":
        return val > target * (1 - tol)
    elif op == "<":
        return val < target * (1 + tol)
    elif op == ">=":
        return val >= target * (1 - tol)
    elif op == "<=":
        return val <= target * (1 + tol)
    return False


def _determine_outcome(
    task: BenchmarkTask,
    chat_result: dict,
    stream_results: dict,
) -> str:
    """
    Map a completed task run to an outcome code.
    SUCCESS / PARTIAL / FAILURE_LOUD / FAILURE_SILENT
    """
    if chat_result.get("error"):
        return "FAILURE_LOUD"

    answer = (chat_result.get("answer") or "").lower()
    if any(phrase in answer for phrase in [
        "i cannot", "i am unable", "failed to", "error occurred",
        "could not complete", "unable to build",
    ]):
        return "FAILURE_LOUD"

    # Evaluate SuccessCriterion objects
    criteria_results = []
    for criterion in task.success_criteria:
        met = _evaluate_criterion(criterion, stream_results)
        criteria_results.append(met)

    if not criteria_results:
        # No quantitative criteria — treat as SUCCESS if no error
        return "SUCCESS"

    n_met = sum(criteria_results)
    n_tot = len(criteria_results)

    if n_met == n_tot:
        return "SUCCESS"
    elif n_met >= n_tot - 1 and n_tot > 1:
        return "PARTIAL"     # one criterion missed
    else:
        return "FAILURE_LOUD"


# ─────────────────────────────────────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────────────────────────────────────

def run_task(task: BenchmarkTask, run_number: int, verbose: bool = True) -> dict:
    """Run one benchmark task and return a result record."""
    if verbose:
        print(f"  Run {run_number}: {task.task_id} | {task.prompt[:60]}...")

    _reset_chat()          # clear conversation history before each run
    time.sleep(1.0)        # small pause to let server settle

    chat_result    = _run_chat(task.prompt)
    stream_results = _get_stream_results()
    outcome        = _determine_outcome(task, chat_result, stream_results)

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
        "answer_words":  len((chat_result.get("answer") or "").split()),
        "timestamp":     datetime.utcnow().isoformat(),
    }

    status_symbol = {"SUCCESS": "✓", "PARTIAL": "~", "FAILURE_LOUD": "✗",
                     "FAILURE_SILENT": "⚠"}.get(outcome, "?")
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
    n_success = outcome_counts.get("SUCCESS", 0) + outcome_counts.get("PARTIAL", 0)
    n_total   = len(all_records)
    rate      = n_success / n_total * 100 if n_total else 0.0

    avg_time  = sum(r["elapsed_s"] for r in all_records) / n_total if n_total else 0.0
    avg_tools = sum(r["n_tools"]   for r in all_records) / n_total if n_total else 0.0

    summary = {
        "timestamp":      ts,
        "n_tasks":        len(tasks),
        "n_runs":         runs,
        "n_total_runs":   n_total,
        "outcome_counts": outcome_counts,
        "success_rate_pct": round(rate, 2),
        "avg_elapsed_s":  round(avg_time, 2),
        "avg_tools_used": round(avg_tools, 2),
        "csv_path":       csv_path,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 60)
    print(f"  Tasks:        {len(tasks)} × {runs} runs = {n_total} total")
    print(f"  Success rate: {rate:.1f}%  "
          f"({outcome_counts.get('SUCCESS',0)} SUCCESS + "
          f"{outcome_counts.get('PARTIAL',0)} PARTIAL / {n_total})")
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
