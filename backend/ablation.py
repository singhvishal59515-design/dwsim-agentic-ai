"""
ablation.py  —  Ablation Study Framework for DWSIM Agentic AI
──────────────────────────────────────────────────────────────
Runs controlled experiments to measure the contribution of each system
component to overall success rate, accuracy, and convergence.

Ablation configurations tested:
  A0  Full system (baseline)
  A1  No safety validator (SF checks disabled)
  A2  No RAG knowledge base (search_knowledge always returns empty)
  A3  No auto-correction (AutoCorrector disabled)
  A4  No tool result compression (full verbose tool results)
  A5  temperature=1.0 (non-deterministic, baseline for stochasticity paper section)
  A6  ShortcutColumn forced (never uses rigorous DistillationColumn)
  A7  No context trimming (full history, no _trim_history)

Each configuration runs the same fixed benchmark task set (N=5 tasks by default)
and records: success_rate, mean_error_pct, mean_duration_s, convergence_rate,
tool_sequence_length, prompt_hash for reproducibility.

Usage:
    python ablation.py --tasks water_heater,distill_ethanol --runs 3
    python ablation.py --config A0,A1,A2 --tasks all --runs 5
"""

from __future__ import annotations

# Load environment variables (.env file) BEFORE any LLM/API imports
# override=True ensures .env takes precedence over stale system env vars
from dotenv import load_dotenv, dotenv_values
load_dotenv(override=True)

# Validate all LLM keys — blank-out any that are invalid/commented in .env
# This prevents fallback loops on providers with expired keys
import os as _os
_env_vals = dotenv_values()  # parse .env without side effects
for _k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY"):
    _val = _env_vals.get(_k, "")
    if not _val:  # key was commented out or empty in .env
        _os.environ.pop(_k, None)  # remove from environment so fallback skips it

import argparse
import json
import os
import sys
import io
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
import hashlib

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError for special chars)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf_8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# Compact tool schema  (reduces Groq token usage from ~9900 to ~2500 tokens)
# ─────────────────────────────────────────────────────────────────────────────

def _compact_tools(tools: list, max_desc: int = 60) -> list:
    """Return a trimmed copy of the DWSIM_TOOLS list for low-TPM providers.

    Keeps tool names, parameter names and types, required arrays, and enum
    values — but truncates free-text descriptions to `max_desc` characters.
    """
    import copy
    out = []
    for t in tools:
        tc = {"name": t["name"], "description": (t.get("description") or "")[:max_desc]}
        if "parameters" in t:
            p = t["parameters"]
            props_out = {}
            for pname, pval in (p.get("properties") or {}).items():
                trimmed = {"type": pval.get("type", "string")}
                if "enum" in pval:
                    trimmed["enum"] = pval["enum"]
                if "items" in pval:
                    trimmed["items"] = pval["items"]
                # Keep description very short
                if pval.get("description"):
                    trimmed["description"] = pval["description"][:40]
                props_out[pname] = trimmed
            tc["parameters"] = {
                "type": p.get("type", "object"),
                "properties": props_out,
                "required": p.get("required", []),
            }
        out.append(tc)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Ablation configuration registry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AblationConfig:
    """One ablation variant — defines which components are active."""
    config_id:           str
    description:         str
    disable_safety:      bool  = False   # A1
    disable_rag:         bool  = False   # A2
    disable_autocorrect: bool  = False   # A3
    disable_compression: bool  = False   # A4
    temperature:         float = 0.0     # A5 uses 1.0
    force_shortcut_col:  bool  = False   # A6
    disable_trim:        bool  = False   # A7


ABLATION_CONFIGS: Dict[str, AblationConfig] = {
    "A0": AblationConfig("A0", "Full system (all components active)"),
    "A1": AblationConfig("A1", "Safety validator disabled",
                         disable_safety=True),
    "A2": AblationConfig("A2", "RAG knowledge base disabled",
                         disable_rag=True),
    "A3": AblationConfig("A3", "Auto-corrector disabled",
                         disable_autocorrect=True),
    "A4": AblationConfig("A4", "Tool result compression disabled",
                         disable_compression=True),
    "A5": AblationConfig("A5", "LLM temperature=1.0 (stochastic baseline)",
                         temperature=1.0),
    "A6": AblationConfig("A6", "ShortcutColumn forced (no rigorous distillation)",
                         force_shortcut_col=True),
    "A7": AblationConfig("A7", "Context trimming disabled (full history)",
                         disable_trim=True),
}

# ─────────────────────────────────────────────────────────────────────────────
# Ablation task set (fixed subset of benchmark tasks)
# ─────────────────────────────────────────────────────────────────────────────

ABLATION_TASKS: List[Dict] = [
    {
        "task_id":        "ABL-01",
        "category":       "heat_exchange",
        "complexity":     1,
        "prompt":         "Create a water heater from 25°C to 80°C at 1 atm, 1 kg/s pure water. Use Steam Tables.",
        "success_criteria": [
            {"stream": "Product", "property": "temperature_C", "target": 80.0, "tol_pct": 2.0},
        ],
        "physical_constraints": ["T_out > T_in", "VF=0 (liquid)", "P_out = P_in"],
        "human_time_min": 5.0,
    },
    {
        "task_id":        "ABL-02",
        "category":       "distillation",
        "complexity":     2,
        "prompt":         "Create a distillation column to separate ethanol-water: feed 50 mol% ethanol, 100 kmol/h, 78°C, 1 atm. Use NRTL. Target 90 mol% ethanol distillate.",
        "success_criteria": [
            {"stream": "Distillate", "property": "mole_fraction_Ethanol", "target": 0.90, "tol_pct": 5.0},
        ],
        "physical_constraints": ["T_profile monotonic", "Mass balance ±2%", "VF∈[0,1]"],
        "human_time_min": 20.0,
    },
    {
        "task_id":        "ABL-03",
        "category":       "compression",
        "complexity":     1,
        "prompt":         "Compress air from 1 atm 25°C to 5 atm at 10 kg/s. Use Peng-Robinson. Adiabatic efficiency 75%.",
        "success_criteria": [
            {"stream": "Product", "property": "pressure_bar", "target": 5.066, "tol_pct": 2.0},
        ],
        "physical_constraints": ["P_out > P_in", "T_out > T_in", "η ∈ [0.5, 0.95]"],
        "human_time_min": 8.0,
    },
    {
        "task_id":        "ABL-04",
        "category":       "reaction",
        "complexity":     2,
        "prompt":         "Create a methanol synthesis reactor: CO + 2H2 → CH3OH at 250°C, 50 bar, conversion 85%. Feed: CO 33%, H2 67% mole, 1000 kmol/h. Use Peng-Robinson.",
        "success_criteria": [
            {"stream": "Product", "property": "mole_fraction_Methanol", "target": 0.28, "tol_pct": 10.0},
        ],
        "physical_constraints": ["Exothermic (duty < 0)", "Mass balance ±2%"],
        "human_time_min": 15.0,
    },
    {
        "task_id":        "ABL-05",
        "category":       "flash_separation",
        "complexity":     1,
        "prompt":         "Flash separate methane (0.3), ethane (0.3), propane (0.4) mixture at -40°C, 10 bar, 1000 kg/h. Use Peng-Robinson.",
        "success_criteria": [
            {"stream": "Vapor",  "property": "vapor_fraction", "target": 1.0, "tol_pct": 1.0},
            {"stream": "Liquid", "property": "vapor_fraction", "target": 0.0, "tol_pct": 1.0},
        ],
        "physical_constraints": ["VF ∈ [0,1]", "Mass balance ±2%", "T_out = -40°C"],
        "human_time_min": 10.0,
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Result record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AblationResult:
    run_id:           str
    config_id:        str
    task_id:          str
    run_number:       int
    timestamp:        str
    success:          bool
    converged:        bool
    duration_s:       float
    tool_sequence:    List[str]
    n_tools:          int
    safety_violations: int
    error_message:    Optional[str]
    stream_results:   Dict[str, Any]
    prompt_hash:      str
    provider:         str
    model:            str
    temperature:      float
    seed:             int
    notes:            str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Ablation runner
# ─────────────────────────────────────────────────────────────────────────────

_LOG_FILE = os.path.join(os.path.dirname(__file__), "ablation_log.jsonl")

# Module-level singleton bridge — DWSIM COM STA model: one per process
_ablation_bridge = None


def run_ablation(
    config_ids:       List[str],
    task_ids:         List[str],
    n_runs:           int = 3,
    provider:         str = "groq",
    model:            Optional[str] = None,
    api_key:          str = "",
    log_file:         str = _LOG_FILE,
    inter_run_delay:  int = 30,
) -> Dict[str, Any]:
    """
    Run ablation experiments and log results.

    For each (config, task, run) triple:
      1. Patch the system according to AblationConfig
      2. Send the task prompt to the agent
      3. Record success/failure, duration, tool sequence, violations
      4. Append to JSONL log
      5. Restore system state

    Returns aggregated summary table for paper reporting.
    """
    global _ablation_bridge  # must be at function level, not inside try/loops

    from llm_client  import LLMClient, DEFAULT_MODELS
    from agent_v2    import DWSIMAgentV2
    from dwsim_bridge_v2 import DWSIMBridgeV2

    configs = {cid: ABLATION_CONFIGS[cid]
               for cid in config_ids if cid in ABLATION_CONFIGS}
    tasks   = [t for t in ABLATION_TASKS
               if t["task_id"] in task_ids or "all" in task_ids]

    if not configs:
        return {"error": f"No valid config IDs. Available: {list(ABLATION_CONFIGS)}"}
    if not tasks:
        return {"error": f"No valid task IDs. Available: {[t['task_id'] for t in ABLATION_TASKS]}"}

    all_results: List[AblationResult] = []
    summary: Dict[str, Dict] = {}

    # Pre-declare so the restore block (after except) never hits NameError
    _av2 = None
    _orig_tools = None

    total_runs = len(configs) * len(tasks) * n_runs
    run_counter = 0

    print(f"\n{'='*65}")
    print(f"DWSIM AGENTIC AI - ABLATION STUDY")
    print(f"{'='*65}")
    print(f"Configs : {list(configs.keys())}")
    print(f"Tasks   : {[t['task_id'] for t in tasks]}")
    print(f"Runs/pair: {n_runs}  |  Total: {total_runs} agent interactions")
    print(f"Provider: {provider}  |  Temperature: 0.0  |  Seed: 42")
    print(f"{'='*65}\n")

    for cfg in configs.values():
        for task in tasks:
            task_results: List[AblationResult] = []
            for run_n in range(1, n_runs + 1):
                run_counter += 1
                run_id = uuid.uuid4().hex[:8]
                t0 = time.monotonic()
                print(f"[{run_counter:3d}/{total_runs}] {cfg.config_id} | {task['task_id']} | run {run_n} | {cfg.description[:35]}")

                # -- Build patched agent ----------------------------------------
                try:
                    llm = LLMClient(
                        provider=provider,
                        api_key=api_key or os.getenv(
                            {"groq":"GROQ_API_KEY","gemini":"GEMINI_API_KEY",
                             "openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY"
                             }.get(provider, "GROQ_API_KEY"), ""),
                        model=model or DEFAULT_MODELS.get(provider, ""),
                        temperature=cfg.temperature,
                    )
                    # Use the module-level singleton bridge (initialised once per process)
                    if _ablation_bridge is None:
                        _ablation_bridge = DWSIMBridgeV2()
                        init_r = _ablation_bridge.initialize()
                        if not init_r.get("success"):
                            raise RuntimeError(f"Bridge init failed: {init_r.get('error')}")
                        print(f"[ablation] Bridge ready: DWSIM {init_r.get('dwsim_version','?')}")
                    bridge = _ablation_bridge

                    # Patch: disable components per config
                    if cfg.disable_rag:
                        _patch_disable_rag()
                    if cfg.disable_safety:
                        _patch_disable_safety(bridge)
                    if cfg.disable_autocorrect:
                        _patch_disable_autocorrect(bridge)

                    # Limit output tokens to keep requests under Groq 12k TPM free tier
                    llm._MAX_TOKENS_OVERRIDE = 2048

                    agent = DWSIMAgentV2(
                        llm=llm, bridge=bridge,
                        max_iterations=20, verbose=False,
                        stream_output=False,
                    )

                    # Patch DWSIM_TOOLS module-level var with compact schema to
                    # stay within Groq free-tier 12k TPM limit (full schema ~10k tokens)
                    import agent_v2 as _av2_mod
                    from tools_schema_v2 import DWSIM_TOOLS as _FULL_TOOLS
                    _COMPACT_TOOLS = _compact_tools(_FULL_TOOLS)
                    _orig_tools = _av2_mod.DWSIM_TOOLS
                    _av2 = _av2_mod
                    _av2.DWSIM_TOOLS = _COMPACT_TOOLS

                    if cfg.disable_trim:
                        _av2._MAX_HISTORY_CHARS = 10_000_000  # effectively disabled
                    if cfg.disable_compression:
                        _av2._compress_tool_result = lambda n, r: r  # no-op

                    # -- Run task --------------------------------------------------
                    prompt = task["prompt"]
                    if cfg.force_shortcut_col:
                        prompt += " (use ShortcutColumn only, not rigorous DistillationColumn)"

                    answer = agent.chat(prompt)
                    duration_s = time.monotonic() - t0

                    metrics = getattr(agent, "last_turn_metrics", {}) or {}
                    tool_seq = metrics.get("tool_sequence", [])
                    prompt_hash = hashlib.sha256(
                        (prompt + json.dumps(tool_seq)).encode()
                    ).hexdigest()[:16]

                    # ── Rigorous success evaluation ──────────────────────────
                    # 1. Get actual stream results from the bridge
                    try:
                        sr = bridge.get_simulation_results()
                        stream_res = sr.get("stream_results", {}) or {}
                    except Exception:
                        stream_res = {}

                    # 2. Check each success criterion against real stream data
                    criteria = task.get("success_criteria", [])
                    criteria_met = 0
                    criteria_total = len(criteria)
                    criteria_details = []

                    for crit in criteria:
                        s_tag = crit.get("stream", "")
                        prop  = crit.get("property", "")
                        target = float(crit.get("target", 0))
                        tol   = float(crit.get("tol_pct", 2.0))
                        stream_data = stream_res.get(s_tag, {})
                        actual = stream_data.get(prop)
                        if actual is None:
                            # Try mole_fractions sub-dict
                            if prop.startswith("mole_fraction_"):
                                comp = prop.replace("mole_fraction_", "")
                                actual = (stream_data.get("mole_fractions") or {}).get(comp)
                        if actual is not None:
                            try:
                                err_pct = abs(float(actual) - target) / max(abs(target), 1e-9) * 100
                                met = err_pct <= tol
                            except Exception:
                                err_pct = 999.0
                                met = False
                        else:
                            err_pct = 999.0
                            met = False
                        if met:
                            criteria_met += 1
                        criteria_details.append({
                            "stream": s_tag, "property": prop,
                            "target": target, "actual": actual,
                            "error_pct": round(err_pct, 2), "met": met,
                        })

                    # 3. Physical constraint checks (VF in range, T/P positive)
                    phys_ok = True
                    safety_viols = 0
                    try:
                        from safety_validator import SafetyValidator
                        sv = SafetyValidator()
                        fails, _ = sv.check_and_correct(stream_res)
                        safety_viols = len(fails)
                        phys_ok = safety_viols == 0
                    except Exception:
                        pass

                    # 4. Converged = at least one stream solved (not empty)
                    converged = len(stream_res) > 0

                    # 5. Success = all criteria met AND no safety violations
                    if criteria_total > 0:
                        success = (criteria_met == criteria_total) and phys_ok
                    else:
                        # Fallback: success if converged and answer is non-trivial
                        import re as _re
                        success = converged and len(answer) > 100 and \
                                  bool(_re.search(r"\d+\.?\d*\s*(?:°C|bar|kg|kmol)", answer))

                    notes = f"criteria={criteria_met}/{criteria_total} sf_viols={safety_viols}"

                    result = AblationResult(
                        run_id=run_id, config_id=cfg.config_id,
                        task_id=task["task_id"], run_number=run_n,
                        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        success=success, converged=converged,
                        duration_s=round(duration_s, 2),
                        tool_sequence=tool_seq, n_tools=len(tool_seq),
                        safety_violations=safety_viols,
                        error_message=None,
                        stream_results={k: {p: v for p, v in s.items()
                                             if p in ("temperature_C","pressure_bar","vapor_fraction","mass_flow_kgh")}
                                        for k, s in stream_res.items()},
                        prompt_hash=prompt_hash,
                        provider=provider,
                        model=llm.model,
                        temperature=cfg.temperature,
                        seed=llm._REPRODUCIBILITY_SEED,
                        notes=notes,
                    )

                except Exception as exc:
                    duration_s = time.monotonic() - t0
                    result = AblationResult(
                        run_id=run_id, config_id=cfg.config_id,
                        task_id=task["task_id"], run_number=run_n,
                        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        success=False, converged=False,
                        duration_s=round(duration_s, 2),
                        tool_sequence=[], n_tools=0,
                        safety_violations=0, error_message=str(exc),
                        stream_results={}, prompt_hash="error",
                        provider=provider, model=model or "",
                        temperature=cfg.temperature, seed=42,
                    )

                # Log result
                all_results.append(result)
                task_results.append(result)
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result.to_dict()) + "\n")

                # Print run result
                status = "[PASS]" if result.success else ("[CONV]" if result.converged else "[FAIL]")
                extra = result.error_message or result.notes
                print(f"         > {status} | {result.duration_s:.1f}s | "
                      f"{result.n_tools} tools | SF={result.safety_violations} | {extra}")

                # Restore patches + tool schema
                if _av2 is not None and _orig_tools is not None:
                    _av2.DWSIM_TOOLS = _orig_tools
                _restore_patches()

                # Rate-limit guard: wait between runs (skip after last run)
                if inter_run_delay > 0 and run_counter < total_runs:
                    remaining = total_runs - run_counter
                    eta_min = remaining * (inter_run_delay + result.duration_s) / 60
                    print(f"           [wait {inter_run_delay}s | ~{eta_min:.0f}min remaining]")
                    time.sleep(inter_run_delay)

            # Per-(config, task) aggregation
            key = f"{cfg.config_id}|{task['task_id']}"
            successes  = [r for r in task_results if r.success]
            summary[key] = {
                "config":        cfg.config_id,
                "description":   cfg.description,
                "task_id":       task["task_id"],
                "n_runs":        n_runs,
                "success_rate":  round(len(successes) / n_runs, 3),
                "mean_duration": round(sum(r.duration_s for r in task_results) / n_runs, 1),
                "mean_n_tools":  round(sum(r.n_tools   for r in task_results) / n_runs, 1),
                "converge_rate": round(sum(1 for r in task_results if r.converged) / n_runs, 3),
                "prompt_hashes": list({r.prompt_hash for r in task_results}),
            }

    # Aggregate across tasks per config
    config_summary: Dict[str, Dict] = {}
    for cfg_id in config_ids:
        rows = [v for k, v in summary.items() if k.startswith(cfg_id + "|")]
        if not rows:
            continue
        n = len(rows)
        config_summary[cfg_id] = {
            "config_id":   cfg_id,
            "description": ABLATION_CONFIGS.get(cfg_id, AblationConfig(cfg_id, "")).description,
            "n_tasks":     n,
            "success_rate": round(sum(r["success_rate"] for r in rows) / n, 3),
            "mean_duration_s": round(sum(r["mean_duration"] for r in rows) / n, 1),
            "mean_tools":  round(sum(r["mean_n_tools"]  for r in rows) / n, 1),
            "converge_rate": round(sum(r["converge_rate"] for r in rows) / n, 3),
        }

    # ── Print final summary table ─────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("ABLATION RESULTS SUMMARY")
    print(f"{'='*65}")
    print(f"{'Config':<6} {'Description':<38} {'Success%':>8} {'Conv%':>6} {'Tools':>6} {'Time(s)':>8}")
    print(f"{'-'*6} {'-'*38} {'-'*8} {'-'*6} {'-'*6} {'-'*8}")
    for cfg_id, row in sorted(config_summary.items()):
        print(f"{cfg_id:<6} {row['description'][:38]:<38} "
              f"{row['success_rate']*100:>7.1f}% "
              f"{row['converge_rate']*100:>5.1f}% "
              f"{row['mean_tools']:>6.1f} "
              f"{row['mean_duration_s']:>8.1f}")
    print(f"{'='*65}")
    print(f"Total runs: {len(all_results)} | Log: {log_file}")
    print()

    return {
        "success":        True,
        "configs_run":    list(config_ids),
        "tasks_run":      [t["task_id"] for t in tasks],
        "n_runs_each":    n_runs,
        "log_file":       log_file,
        "per_task":       summary,
        "per_config":     config_summary,
        "total_runs":     len(all_results),
    }


# ── Patch helpers ─────────────────────────────────────────────────────────────

_orig_search_knowledge = None
_orig_check_with_duties = None
_orig_autocorrect = None
_orig_trim_chars = None
_orig_compress = None


def _patch_disable_rag():
    """A2: make search_knowledge always return empty results."""
    import knowledge_base as _kb
    global _orig_search_knowledge
    _orig_search_knowledge = _kb.KnowledgeBase.search
    _kb.KnowledgeBase.search = lambda self, q, k=5: []


def _patch_disable_safety(bridge):
    """A1: make check_with_duties always return no violations."""
    from safety_validator import SafetyValidator
    global _orig_check_with_duties
    _orig_check_with_duties = SafetyValidator.check_with_duties
    SafetyValidator.check_with_duties = lambda self, sr, t=None, d=None, od=None: ([], 0)


def _patch_disable_autocorrect(bridge):
    """A3: disable AutoCorrector (always return initial result)."""
    from auto_correct import AutoCorrector
    global _orig_autocorrect
    _orig_autocorrect = AutoCorrector.attempt_fixes
    AutoCorrector.attempt_fixes = lambda self, r: {**r, "auto_corrected": False,
                                                    "fixes_applied": [], "attempts": 0}


def _restore_patches():
    """Restore all patched functions to originals."""
    if _orig_search_knowledge is not None:
        import knowledge_base as _kb
        _kb.KnowledgeBase.search = _orig_search_knowledge
    if _orig_check_with_duties is not None:
        from safety_validator import SafetyValidator
        SafetyValidator.check_with_duties = _orig_check_with_duties
    if _orig_autocorrect is not None:
        from auto_correct import AutoCorrector
        AutoCorrector.attempt_fixes = _orig_autocorrect
    import agent_v2 as _av2
    if hasattr(_av2, "_MAX_HISTORY_CHARS"):
        _av2._MAX_HISTORY_CHARS = 80_000


# ── API endpoint helper ───────────────────────────────────────────────────────

def get_ablation_summary() -> Dict[str, Any]:
    """Read and aggregate existing ablation log for the /ablation/summary endpoint."""
    results = []
    if not os.path.isfile(_LOG_FILE):
        return {"success": True, "results": [], "message": "No ablation runs recorded yet."}
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except Exception:
                        pass
    except OSError:
        pass

    if not results:
        return {"success": True, "results": [], "message": "Log file empty."}

    # Aggregate by config_id × task_id
    from collections import defaultdict
    groups: Dict = defaultdict(list)
    for r in results:
        groups[(r["config_id"], r["task_id"])].append(r)

    summary = []
    for (cfg, task), rows in sorted(groups.items()):
        n = len(rows)
        summary.append({
            "config_id":     cfg,
            "task_id":       task,
            "n_runs":        n,
            "success_rate":  round(sum(1 for r in rows if r["success"]) / n, 3),
            "converge_rate": round(sum(1 for r in rows if r["converged"]) / n, 3),
            "mean_duration_s": round(sum(r["duration_s"] for r in rows) / n, 1),
            "mean_n_tools":  round(sum(r["n_tools"] for r in rows) / n, 1),
            "providers":     list({r["provider"] for r in rows}),
            "models":        list({r["model"] for r in rows}),
        })

    return {"success": True, "total_runs": len(results), "summary": summary}


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DWSIM Agentic AI Ablation Study Runner"
    )
    parser.add_argument("--configs", default="A0,A1,A2",
                        help="Comma-separated config IDs (default: A0,A1,A2)")
    parser.add_argument("--tasks",   default="all",
                        help="Comma-separated task IDs or 'all'")
    parser.add_argument("--runs",    type=int, default=3,
                        help="Runs per (config, task) (default: 3)")
    parser.add_argument("--provider",default="groq")
    parser.add_argument("--model",   default="")
    parser.add_argument("--api-key", default="", dest="api_key")
    parser.add_argument("--delay",   type=int, default=30,
                        help="Seconds to wait between runs to respect rate limits (default: 30)")
    args = parser.parse_args()

    result = run_ablation(
        config_ids=args.configs.split(","),
        task_ids=args.tasks.split(","),
        n_runs=args.runs,
        provider=args.provider,
        model=args.model or None,
        api_key=args.api_key,
        inter_run_delay=args.delay,
    )
    print(json.dumps(result, indent=2))
