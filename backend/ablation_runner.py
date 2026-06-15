"""
ablation_runner.py
══════════════════
Drives the REAL agent over the frozen 25-task benchmark under each ablation
condition and writes per-task results to `ablation_logs/*.jsonl` in the schema
the analysis (`ablation_report.py` / `ablation_stats.py`) consumes.

This is the piece that makes the ablation *fully working*: it ties together the
real agent loop (`agent_v2`), the condition toggles (`ablation_config` — which
actually disable RAG / SafetyValidator / tools in the live agent), the frozen
task specs + benchmark scoring (`benchmark_tasks.run_task`), and the JSONL log
format the statistics read. No deterministic stand-ins — the agent under test is
the same one users run.

Conditions (codes match the analysis script):
  A = Full System          (DWSIM_ABLATION_CONDITION=full)
  B = No-RAG               (... = no_rag)
  C = No-SafetyValidator   (... = no_safety)
  D = Direct LLM           (... = direct_llm — no tools)

Per-task JSONL record:
  {condition, task_id, category, complexity, rep, success(1/0/-1),
   tool_calls, wall_time_s, error_recovery_events, outcome}
  success = 1 pass / 0 fail / -1 not-applicable (e.g. fixture missing) — a -1 is
  skipped by the analysis so it never depresses a condition's score.

Usage:
  # full study (needs LLM throughput): 4 conditions × 25 tasks × 3 reps
  python ablation_runner.py --reps 3
  # mechanics smoke test (1 task, 1 condition) — proves the pipeline end to end
  python ablation_runner.py --smoke
  python ablation_report.py        # then analyse the logs

The agent is dependency-injected (build via build_default_agent) so the runner is
testable with a mock — see tests/test_ablation_runner.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

CONDITION_MAP = {"A": "full", "B": "no_rag", "C": "no_safety", "D": "direct_llm"}
CONDITION_NAMES = {"A": "Full System", "B": "No-RAG",
                   "C": "No-SafetyValidator", "D": "Direct LLM"}
DEFAULT_LOG_DIR = os.path.join(_HERE, "ablation_logs")


class _ToolRecorder:
    """Captures (tool_name, success) per task so we can count error-recovery
    events — a failed tool call the agent later recovered from."""
    def __init__(self):
        self.events: List[tuple] = []

    def clear(self):
        self.events = []

    def __call__(self, name, args, result):
        ok = True
        if isinstance(result, dict):
            ok = bool(result.get("success", True))
        self.events.append((name, ok))

    def error_recovery_events(self) -> int:
        oks = [ok for _, ok in self.events]
        return sum(1 for i, ok in enumerate(oks)
                   if not ok and any(oks[i + 1:]))


def build_default_agent():
    """Construct the real agent (bridge + LLM from env), mirroring api._get_agent."""
    from dwsim_bridge_v2 import DWSIMBridgeV2
    bridge = DWSIMBridgeV2(); bridge.initialize()
    from agent_v2 import DWSIMAgentV2
    try:
        from llm_client import LLMClient, DEFAULT_MODELS as _DM
    except Exception:
        from llm_client import LLMClient
        _DM = {}
    provider = (os.getenv("LLM_PROVIDER", "groq") or "groq").lower()
    env_key = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY",
               "anthropic": "ANTHROPIC_API_KEY", "ollama": ""}.get(provider, "")
    api_key = os.getenv(env_key, "") if env_key else ""
    model = os.getenv("LLM_MODEL", _DM.get(provider, "") if isinstance(_DM, dict) else "")
    llm = LLMClient(provider=provider, api_key=api_key, model=model)
    return DWSIMAgentV2(llm=llm, bridge=bridge)


def _reset_for_task(agent, task) -> Optional[str]:
    """Put the engine in the right starting state for a task.
    Returns a skip-reason string if the task cannot be run (→ success = -1),
    else None."""
    bridge = getattr(agent, "bridge", None)
    setup = getattr(task, "setup_load", "") or ""
    if setup:
        # Fixture-based task: load the named flowsheet if we can find it.
        path = setup if os.path.isabs(setup) else None
        for cand in ([setup] if path else
                     [setup,
                      os.path.join(os.path.expanduser("~"), "Documents", setup),
                      os.path.join(_HERE, "fixtures", setup)]):
            if cand and os.path.exists(cand) and bridge is not None:
                try:
                    r = bridge.load_flowsheet(cand)
                    if isinstance(r, dict) and r.get("success"):
                        return None
                except Exception:
                    pass
        if getattr(task, "requires_fixture", False):
            return f"fixture '{setup}' not available"
    elif getattr(task, "requires_fixture", False):
        return "requires a pre-loaded fixture; none available"
    else:
        # Build task: start from a clean engine so prior tasks don't bleed in.
        if bridge is not None:
            for meth in ("reset", "reset_flowsheet", "new_empty_flowsheet"):
                fn = getattr(bridge, meth, None)
                if callable(fn):
                    try: fn(); break
                    except Exception: pass
    return None


def _record(cond_code: str, task, env: Dict[str, Any], rec: _ToolRecorder,
            rep: int, skip: Optional[str]) -> Dict[str, Any]:
    if skip is not None:
        success = -1
    else:
        success = 1 if env.get("passed") else 0
    return {
        "condition":             cond_code,
        "condition_name":        CONDITION_NAMES[cond_code],
        "task_id":               getattr(task, "task_id", "?"),
        "category":              getattr(task, "category", "unknown"),
        "complexity":            getattr(task, "complexity", None),
        "rep":                   rep,
        "success":               success,
        "tool_calls":            int(env.get("tool_calls", 0) or 0),
        "wall_time_s":           float(env.get("duration_s", 0.0) or 0.0),
        "error_recovery_events": rec.error_recovery_events(),
        "outcome":               env.get("outcome", "SKIP" if skip else ""),
        "skip_reason":           skip,
    }


def run_condition(cond_code: str, agent, reps: int = 3,
                  task_ids: Optional[List[str]] = None,
                  log_dir: str = DEFAULT_LOG_DIR,
                  on_task=None) -> List[str]:
    """Run one condition for `reps` repetitions; one JSONL file per rep.
    Returns the list of written file paths."""
    from benchmark_tasks import BENCHMARK_TASKS, run_task
    os.makedirs(log_dir, exist_ok=True)
    tasks = [t for t in BENCHMARK_TASKS
             if not task_ids or t.task_id in task_ids]
    cfg = CONDITION_MAP[cond_code]
    written = []
    for rep in range(1, reps + 1):
        os.environ["DWSIM_ABLATION_CONDITION"] = cfg
        os.environ["DWSIM_ABLATION_REP"] = str(rep)
        path = os.path.join(log_dir, f"cond_{cond_code}_rep{rep}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for task in tasks:
                os.environ["DWSIM_ABLATION_TASK"] = task.task_id
                rec = _ToolRecorder()
                skip = None
                env: Dict[str, Any] = {}
                try:
                    skip = _reset_for_task(agent, task)
                    if skip is None:
                        try: agent.on_tool_call = rec  # run_task chains to this
                        except Exception: pass
                        env = run_task(task.task_id, agent) or {}
                except Exception as exc:
                    env = {"passed": False, "outcome": "RUNNER_ERROR",
                           "duration_s": 0.0, "tool_calls": len(rec.events),
                           "notes": str(exc)[:200]}
                row = _record(cond_code, task, env, rec, rep, skip)
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                if callable(on_task):
                    try: on_task(row)
                    except Exception: pass
        written.append(path)
    return written


def run(conditions: Optional[List[str]] = None, reps: int = 3,
        task_ids: Optional[List[str]] = None, log_dir: str = DEFAULT_LOG_DIR,
        agent=None) -> Dict[str, Any]:
    conditions = conditions or ["A", "B", "C", "D"]
    if agent is None:
        agent = build_default_agent()
    t0 = time.monotonic()
    all_files: List[str] = []
    for cond in conditions:
        print(f"\n{'='*64}\n[ablation] condition {cond} = {CONDITION_NAMES[cond]} "
              f"({CONDITION_MAP[cond]}), {reps} rep(s)\n{'='*64}", flush=True)
        files = run_condition(cond, agent, reps=reps, task_ids=task_ids,
                              log_dir=log_dir,
                              on_task=lambda r: print(
                                  f"  {r['condition']} {r['task_id']:<7} "
                                  f"success={r['success']:>2} "
                                  f"tools={r['tool_calls']:>2} "
                                  f"t={r['wall_time_s']:.1f}s", flush=True))
        all_files += files
    print(f"\n[ablation] done in {time.monotonic()-t0:.0f}s. Wrote {len(all_files)} "
          f"log file(s) to {log_dir}\n[ablation] now run:  python ablation_report.py", flush=True)
    return {"log_dir": log_dir, "files": all_files, "conditions": conditions,
            "reps": reps}


def _cleanup_env():
    for k in ("DWSIM_ABLATION_CONDITION", "DWSIM_ABLATION_TASK", "DWSIM_ABLATION_REP"):
        os.environ.pop(k, None)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="DWSIM agentic-AI ablation runner")
    ap.add_argument("--conditions", default="A,B,C,D",
                    help="comma-separated condition codes (A/B/C/D)")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--tasks", default="",
                    help="comma-separated task ids (default: all 25)")
    ap.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    ap.add_argument("--smoke", action="store_true",
                    help="mechanics check: 1 condition (A), 1 rep, first task")
    args = ap.parse_args()

    conds = [c.strip().upper() for c in args.conditions.split(",") if c.strip()]
    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()] or None
    reps = args.reps
    if args.smoke:
        conds, reps = ["A"], 1
        if not task_ids:
            from benchmark_tasks import BENCHMARK_TASKS
            task_ids = [BENCHMARK_TASKS[0].task_id]
        print("[ablation] SMOKE mode — proving the pipeline end to end.")
    try:
        run(conditions=conds, reps=reps, task_ids=task_ids, log_dir=args.log_dir)
    finally:
        _cleanup_env()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
