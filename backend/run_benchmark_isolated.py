#!/usr/bin/env python3
"""
Crash-isolated live benchmark runner.

Some DWSIM operations (notably parametric_study) can raise a process-terminating
.NET/pythonnet exception that Python cannot catch — one bad task would otherwise
kill the whole 25-task run. This runner executes EACH task in its own
subprocess with its own DWSIM engine, so a crash loses only that task; the
orchestrator continues. Each subprocess persists its own result via the per-task
merge in benchmark_tasks.persist_results, so results accumulate in
benchmark_results.json / eval_log.json regardless of crashes.

Usage:  python run_benchmark_isolated.py            # all tasks
        python run_benchmark_isolated.py C1-T01 ... # a subset
Env:    LLM_PROVIDER / LLM_MODEL / LLM_FAILOVER_CHAIN are passed to each child.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PER_TASK_TIMEOUT_S = 300   # generous: build + solve + score for one task

_CHILD = r'''
import os, json, datetime
import benchmark_tasks as bt
import api
tid = {tid!r}
try:
    agent = api._get_agent()
    r = bt.run_task(tid, agent)
    bt.persist_results({{"success": True, "mode": bt._bridge_mode(agent),
                         "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         "results": [r], "summary": bt.summarize_results([r])}})
    print("TASK_DONE", tid, r.get("outcome"), bool(r.get("passed")), flush=True)
except Exception as exc:
    print("TASK_PYERR", tid, str(exc)[:200], flush=True)
'''


def main(argv):
    import benchmark_tasks as bt
    ids = argv or [t.task_id for t in bt.BENCHMARK_TASKS]
    print(f"[isolated] running {len(ids)} task(s), one subprocess each "
          f"(timeout {_PER_TASK_TIMEOUT_S}s/task)…", flush=True)

    crashed, done = [], []
    for i, tid in enumerate(ids, 1):
        print(f"\n[isolated] ({i}/{len(ids)}) {tid} …", flush=True)
        code = _CHILD.format(tid=tid)
        try:
            # Decode child stdout as UTF-8 with replacement — the agent's output
            # contains non-cp1252 chars (emoji, °, →) that would otherwise raise
            # UnicodeDecodeError in the reader thread and lose the completion
            # marker (mislabelling a finished task as "crashed").
            _env = os.environ.copy()
            _env["PYTHONIOENCODING"] = "utf-8"
            p = subprocess.run([sys.executable, "-c", code], cwd=_HERE,
                               env=_env, timeout=_PER_TASK_TIMEOUT_S,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            tail = "\n".join(l for l in (p.stdout or "").splitlines()
                             if l.startswith(("TASK_DONE", "TASK_PYERR")))
            if "TASK_DONE" in (p.stdout or ""):
                print("   " + (tail or "done"), flush=True); done.append(tid)
            elif "TASK_PYERR" in (p.stdout or ""):
                print("   " + tail + " (python error — recorded as failure)", flush=True)
                done.append(tid)
            else:
                # No completion marker => the child crashed (e.g. .NET exception).
                print(f"   CRASHED (exit {p.returncode}) — task isolated, continuing",
                      flush=True); crashed.append(tid)
        except subprocess.TimeoutExpired:
            print(f"   TIMEOUT after {_PER_TASK_TIMEOUT_S}s — continuing",
                  flush=True); crashed.append(tid)

    # Final report from the accumulated, persisted results.
    bj = os.path.join(_HERE, "benchmark_results.json")
    report = json.load(open(bj, encoding="utf-8")) if os.path.exists(bj) else {}
    s = report.get("summary", {})
    print("\n" + "=" * 60, flush=True)
    print(f"[isolated] recorded {s.get('total', 0)} task(s) | "
          f"pass-rate {s.get('passed', 0)}/{s.get('total', 0)} "
          f"({s.get('pass_rate')}%)", flush=True)
    if crashed:
        print(f"[isolated] {len(crashed)} task(s) crashed/timed out (CLR): "
              f"{', '.join(crashed)}", flush=True)
    print(f"[isolated] now run:  python eval_summary.py", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
