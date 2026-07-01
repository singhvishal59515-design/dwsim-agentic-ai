"""
agent_v2.py
───────────
Enhanced agent loop for dwsim_full.
Wired to DWSIMBridgeV2 and tools_schema_v2 (16 tools).
Supports on_token streaming callback for the FastAPI SSE endpoint.
"""

import concurrent.futures
import json
import logging
import math as _math
import os
import re as _re
import queue as _queue
import sys
import textwrap
import threading
import time
from typing import Any, Callable, Dict, List, Iterator, Optional

from dwsim_bridge_v2 import DWSIMBridgeV2
from llm_client      import LLMClient, CACHE_BREAKPOINT
from tools_schema_v2 import DWSIM_TOOLS

_log = logging.getLogger("agent_v2")

# ── Gap 1: Prompt-injection defence ──────────────────────────────────────────
# DWSIM object names (stream/unit-op tags) flow verbatim into the LLM context.
# A malicious flowsheet could embed an instruction inside a tag name.
# Scan every string that arrives from a tool result; replace suspicious content.
_INJECTION_PATTERNS = _re.compile(
    r"(?i)\b("
    r"ignore\s+(previous|all|above|prior)\s+(instructions?|rules?|prompt)|"
    r"disregard\s+(all|previous|the\s+above)\s+(instructions?|rules?)|"
    r"forget\s+(all|everything|previous)\s+(instructions?|rules?)|"
    r"you\s+are\s+now\s+(a\s+)?(?!a\s+chemical)|"   # "you are now a..." except legit role
    r"new\s+(system\s+prompt|instructions?|role\s+is)|"
    r"override\s+(the\s+)?(system\s+prompt|instructions?)|"
    r"act\s+as\s+if\s+you\s+are|"
    r"do\s+not\s+follow\s+(the\s+)?(rules?|instructions?)"
    r")"
)


def _sanitize_for_llm(obj: Any, _depth: int = 0) -> Any:
    """Recursively sanitise tool-result data before it is JSON-serialised and
    sent to the LLM:
      • strings matching prompt-injection patterns → warning token;
      • non-finite floats (NaN / ±Inf) → None.  DWSIM returns NaN/Inf for
        unconverged or degenerate streams, and json.dumps(allow_nan=False) —
        which the LLM APIs require — raises "Out of range float values are not
        JSON compliant", aborting the whole turn. This was the single most
        frequent runtime crash. Converting to null keeps the turn alive.
    Depth-limited to avoid stack overflow on deeply nested DWSIM structures.
    """
    if _depth > 8:
        return obj
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        return obj if _math.isfinite(obj) else None
    if isinstance(obj, str):
        if _INJECTION_PATTERNS.search(obj):
            _log.warning("Prompt-injection pattern detected in tool result — blocked.")
            return "[CONTENT_BLOCKED: possible prompt injection in tool output]"
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_llm(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_llm(item, _depth + 1) for item in obj]
    return obj


# ── Gap 2: Output content filter ─────────────────────────────────────────────
# If the LLM makes unverified qualitative safety/hazard claims, append a
# standard disclaimer so the user knows those claims weren't simulation-verified.
_UNVERIFIED_SAFETY_RE = _re.compile(
    r"(?i)\b("
    r"safe\s+to\s+(heat|cool|pump|pressuri[sz]e|mix|store|handle|use\s+at)|"
    r"safe\s+at\s+\d|"
    r"safe\s+for\s+(human|worker|personnel|continuous\s+operation)|"
    r"non[-\s]?toxic(?!\s+solvent)|"          # allow "non-toxic solvent" (common label)
    r"not\s+(hazardous|dangerous|flammable|explosive|toxic)\s+at|"
    r"no\s+risk\s+of\s+(explosion|fire|ignition|toxicity)|"
    r"harmless\s+(at|below|under|when)|"
    r"will\s+not\s+(explode|ignite|react\s+violently|decompose\s+dangerously)"
    r")"
)

_SAFETY_DISCLAIMER = (
    "\n\n---\n"
    "*Safety note: The qualitative safety or hazard statements above have not "
    "been verified by process simulation. Always consult a certified process "
    "safety engineer and relevant safety data sheets (SDS) before acting on "
    "safety-related conclusions.*"
)

# Per-tool wall-clock timeout. run_simulation is heavier than the rest.
_DEFAULT_TOOL_TIMEOUT_S = float(os.getenv("AGENT_DEFAULT_TOOL_TIMEOUT_S", "60.0"))
# Global multiplier — set AGENT_TIMEOUT_MULT=2.0 to double every tool timeout on slow systems
_TOOL_TIMEOUT_MULT = float(os.getenv("AGENT_TIMEOUT_MULT", "1.0"))
_TOOL_TIMEOUT_S = {
    "run_simulation":              180.0,
    "load_flowsheet":              180.0,
    "create_flowsheet":            240.0,  # disabled but keep timeout entry
    "new_flowsheet":               60.0,
    "add_object":                  30.0,
    "save_and_solve":              240.0,  # save + DWSIM solve
    "build_flowsheet_atomic":      360.0,  # full pipeline: new+add+connect+set+solve
    "instantiate_process_template": 480.0,  # deterministic template build (large templates)
    "execute_build_plan":          480.0,  # deterministic ad-hoc plan execution
    "get_available_compounds":      30.0,
    "get_available_property_packages": 30.0,
    "search_knowledge":             20.0,
    "find_flowsheets":              45.0,  # bridge has 20s internal budget; allow IO slack
    # Reflection escape-hatch tools
    "reflect_get_set":             10.0,
    "exec_python":                 90.0,  # user code may take a while
    "inspect_object":              10.0,
    "iterative_spec_loop":        900.0,  # bisection × 25 solves × ~10s each
    # Process design advisor — pure Python, very fast
    "process_synthesis":            5.0,
    "equipment_sizing":             5.0,
    "separation_sequence":          5.0,
    "property_package_selector":    5.0,
    "heat_integration_targets":     5.0,
    "design_checklist":             5.0,
    # DWSIM troubleshooter — pure Python
    "troubleshoot_dwsim":           5.0,
    "convergence_guide":            5.0,
    "numerical_settings_advisor":   5.0,
    "decode_error":                 5.0,
    # New tools — generous timeouts for optimisation-based calls
    "monte_carlo_study":          1800.0,  # 100 samples × ~10s each = up to 1000s; cap 30min
    "bayesian_optimize":           900.0,  # 25 evals × ~10s each + GP fit ≈ 250–500s; allow 15min
    "dwsim_optimize":             1800.0,  # native simplex/L-BFGS-B can take 50-200 evals × ~10s
    "dwsim_internal_optimize":   1800.0,  # OptimizationCase: same budget
    "optimize_flowsheet_with_llm": 2400.0,  # full NL workflow: discovery + LLM + solve
    "optimize_multivar":           600.0,  # DE: 100 iter × ~2s each = up to 200s; allow 600s
    "optimize_parameter":          300.0,  # scalar: 50 iter
    "robust_solve":                300.0,   # up to 5 attempts × 60s
    "initialize_distillation":     600.0,   # 4 algorithm attempts × 120s
    "optimize_constrained":       3600.0,   # 100 evals × grid
    "optimize_multiobjective":    3600.0,   # n_points × iterations
    "global_sensitivity":         3600.0,   # N·(D+2) DWSIM solves
    "optimize_eo":                3600.0,   # DOE sampling + surrogate NLP
    "parametric_study_2d":        3600.0,   # up to 100 points
    "parametric_study":            300.0,  # N points sequential
    "pinch_analysis":               60.0,  # bridge iteration
    "initialize_recycle":           30.0,
    "get_compound_properties":      20.0,
    "get_phase_results":            30.0,
    "calculate_phase_envelope":    120.0,
    "setup_reaction":               30.0,
    "generate_report":             120.0,
    # Property database tools — pure Python/SQLite, very fast
    "lookup_compound_properties":    5.0,
    "lookup_binary_parameters":      5.0,
    "compute_vapor_pressure":         5.0,
    "search_compound_database":       5.0,
    "batch_lookup_properties":       10.0,
}
# LLM retry: exponential backoff for transient failures.
# IMPORTANT: _LLM_BACKOFF_S must have exactly _LLM_MAX_ATTEMPTS - 1 entries.
# Increasing _LLM_MAX_ATTEMPTS without extending the tuple causes IndexError.
_LLM_MAX_ATTEMPTS   = 3
_LLM_BACKOFF_S      = (1.0, 3.0)        # len == _LLM_MAX_ATTEMPTS - 1
# Aggregate wall-clock budget for ALL retry attempts combined (seconds).
# Prevents the retry loop from hanging for 6+ minutes when every provider
# takes ~90 s to timeout before failing (3 attempts × 90 s = 270 s hang).
_LLM_RETRY_BUDGET_S = 180.0             # 3 minutes max across all attempts

# Retry temperature diversity (Chip Huyen Ch.2: test-time compute via diverse sampling).
# On first attempt use temperature=0 (deterministic, reproducible).
# On retry attempts, add small temperature to explore different reasoning paths —
# this helps when the first approach always fails identically (stuck in a loop).
_RETRY_TEMPERATURES = (0.0, 0.15, 0.25)   # attempt 0, 1, 2 temperatures

# Cross-provider failover chain (Review-3 AI Gap 3).
# After all per-attempt retries on the primary provider fail, the agent will
# transparently retry on the next provider in this list (if its API key is set).
# Order: openai (paid, high-quality) → groq (free, fast Llama) →
# anthropic (paid, high-quality).
# Failover order: most-reliable first. Anthropic (paid, robust) and Groq
# (fast, free) lead; OpenAI follows. Configurable via the LLM_FAILOVER_CHAIN
# env var (comma-separated) so a bad/limited provider can be deprioritised
# without a code change. (Gemini is removed as a supported provider; any
# "gemini" entry in the env var is filtered out below.)
import os as _os_fc
_FAILOVER_CHAIN = tuple(
    p.strip().lower() for p in
    _os_fc.getenv("LLM_FAILOVER_CHAIN", "anthropic,groq,openai").split(",")
    if p.strip() and p.strip().lower() != "gemini"
)

# ── Token cost estimator (Review-3 LangSmith feature) ────────────────────────
# Costs in USD per 1M tokens (input, output). Free providers → 0.
_TOKEN_COSTS_USD_PER_1M: Dict[str, tuple] = {
    "gpt-4o":              (5.00,  15.00),
    "gpt-4o-mini":         (0.15,   0.60),
    "gpt-4.1":             (2.00,   8.00),
    "gpt-4.1-mini":        (0.40,   1.60),
    "claude-opus-4":       (15.00,  75.00),
    "claude-sonnet-4-5":   (3.00,  15.00),
    "claude-sonnet-4-6":   (3.00,  15.00),
    "claude-haiku-4-5":    (0.80,   4.00),
    # groq, ollama are free (or unknown cost) → 0
}

def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for one LLM call. Returns 0 for free/unknown models."""
    for prefix, (c_in, c_out) in _TOKEN_COSTS_USD_PER_1M.items():
        if model.startswith(prefix):
            return (tokens_in * c_in + tokens_out * c_out) / 1_000_000
    return 0.0

# History limits.
# Token-aware trimming: 1 token ≈ 4 chars (conservative for mixed content).
# Groq Llama-3.3-70b: 32k context. Reserve ~8k for system prompt + tools schema
# + response headroom → ~24k tokens for history = ~96k chars.
# For safety, trim at 80k chars (~20k tokens) to stay well under all providers.
_MAX_HISTORY_MESSAGES = 30          # hard message count cap (fast path)
_MAX_HISTORY_CHARS    = 80_000      # soft token cap — trim oldest if exceeded


def _bridge_objects(bridge) -> Dict[str, list]:
    """Return {streams, unit_ops} from whichever method the bridge has.

    The bridge's real method is list_simulation_objects() which returns
    {objects: [{tag, type, category}]}. Some legacy code paths call the
    method 'list_objects()' — try both, normalise the shape."""
    raw = None
    for m in ("list_simulation_objects", "list_objects",
              "get_simulation_objects"):
        if hasattr(bridge, m):
            try:
                raw = getattr(bridge, m)()
                break
            except Exception:
                continue
    if not isinstance(raw, dict):
        return {"streams": [], "unit_ops": []}
    # Already split shape
    if "unit_ops" in raw or "streams" in raw:
        return {"streams":  list(raw.get("streams",  [])),
                "unit_ops": list(raw.get("unit_ops", []))}
    # Flat objects[] shape
    streams, unit_ops = [], []
    for o in raw.get("objects") or []:
        if not isinstance(o, dict) or not o.get("tag"):
            continue
        cat = (o.get("category") or "").lower()
        tn  = (o.get("type")     or "").lower()
        if "stream" in cat or "materialstream" in tn or tn == "stream":
            streams.append(o)
        elif cat == "energy" or "energystream" in tn:
            continue
        else:
            unit_ops.append(o)
    return {"streams": streams, "unit_ops": unit_ops}


def _apply_output_filter(text: str) -> str:
    """Append a safety disclaimer if the LLM response makes unverified hazard claims.
    These claims come from the model's parametric knowledge, not simulation results,
    so we flag them rather than silently passing them through.
    """
    if _UNVERIFIED_SAFETY_RE.search(text):
        return text + _SAFETY_DISCLAIMER
    return text


# ── Gap 3: Physical property hard limits ─────────────────────────────────────
# Applied in _run_tool() BEFORE the bridge call, so the .NET layer never sees
# physically impossible values.  All values are compared in SI units after a
# lightweight conversion that mirrors what DWSIMBridge._convert_to_si() does.
_STREAM_PROP_SI_LIMITS: Dict[str, tuple] = {
    "temperature":    (0.0,    2500.0),  # K   — absolute zero → ~2500 K industrial max
    "pressure":       (0.0,    1.5e8),   # Pa  — vacuum → 1500 bar
    "mass_flow":      (0.0,    1e9),     # kg/s
    "molar_flow":     (0.0,    1e9),     # mol/s
    "vapor_fraction": (0.0,    1.0),
}


# ── Tool call ordering state machine ─────────────────────────────────────────
# Prevents the LLM from calling DWSIM tools in impossible orders
# (e.g. set_stream_property before new_flowsheet, or save_and_solve before
# any objects have been added). These out-of-order calls cause DWSIM
# NullReferenceExceptions that cascade into confusing error loops.

_REQUIRES_FLOWSHEET = {
    # These tools require an active flowsheet to be loaded/created first
    "list_simulation_objects", "get_stream_properties", "set_stream_property",
    "set_stream_composition", "get_object_properties", "set_unit_op_property",
    "run_simulation", "save_and_solve", "get_simulation_results",
    "check_convergence", "validate_feed_specs", "get_property_package",
    "set_property_package", "parametric_study", "optimize_parameter",
    "optimize_multivar", "bayesian_optimize", "monte_carlo_study",
    "robust_solve", "initialize_distillation", "optimize_constrained",
    "optimize_multiobjective", "global_sensitivity", "optimize_eo",
    "parametric_study_2d",
    "get_column_properties", "set_column_property", "set_column_specs",
    "get_reactor_properties", "set_reactor_property", "setup_reaction",
    "get_phase_results", "get_energy_stream", "set_energy_stream",
    "get_transport_properties", "calculate_phase_envelope",
    "get_binary_interaction_parameters", "set_binary_interaction_parameters",
    "configure_heat_exchanger", "set_stream_flash_spec",
    "delete_object", "disconnect_streams", "connect_streams",
    "validate_topology", "detect_simulation_mode",
    "add_object", "get_available_compounds", "get_available_property_packages",
    "pinch_analysis", "initialize_recycle",
}

_REQUIRES_OBJECTS = {
    # These additionally require at least one object to exist in the flowsheet
    "save_and_solve", "run_simulation", "parametric_study",
    "optimize_parameter", "optimize_multivar", "bayesian_optimize",
    "monte_carlo_study", "robust_solve", "initialize_distillation",
    "optimize_constrained", "optimize_multiobjective", "parametric_study_2d",
    "global_sensitivity", "optimize_eo",
    "get_phase_results",
}


def _check_tool_preconditions(name: str, bridge) -> Optional[dict]:
    """
    Returns an error dict if tool `name` cannot run given bridge state,
    or None if preconditions are met.
    Keeps the check lightweight — no DWSIM calls, only bridge state inspection.
    """
    if name not in _REQUIRES_FLOWSHEET:
        return None  # tool has no preconditions

    # Check if a flowsheet is active.  FlowsheetState has `name` and `path`
    # populated by new_flowsheet/load_flowsheet — those are the canonical
    # signals.  (Older code checked active_alias/flowsheet_name which don't
    # exist on FlowsheetState — that bug silently blocked add_object on
    # every parallel batch where the LLM ordered add_object before
    # new_flowsheet.)
    has_flowsheet = False
    try:
        fs = bridge.state
        has_flowsheet = bool(getattr(fs, "name", "") or
                             getattr(fs, "path", "") or
                             getattr(bridge, "_active_alias", None))
    except Exception:
        has_flowsheet = False

    if not has_flowsheet:
        return {
            "success": False,
            "error": (
                f"Cannot call '{name}' — no flowsheet is loaded or created yet. "
                "Call new_flowsheet (to build from scratch) or load_flowsheet (to open an existing file) first. "
                "If you're building a new simulation, the correct sequence is: "
                "new_flowsheet → add_object → connect_streams → set_stream_property → save_and_solve."
            ),
            "_precondition_failed": True,
        }

    if name in _REQUIRES_OBJECTS:
        # Quick check: do we have any streams/unit-ops?
        has_objects = False
        try:
            fs = bridge.state
            has_objects = bool(
                getattr(fs, "streams", {}) or
                getattr(fs, "unit_ops", {}) or
                getattr(fs, "objects", {})
            )
        except Exception:
            has_objects = True  # assume OK if state inspection fails

        if not has_objects:
            return {
                "success": False,
                "error": (
                    f"Cannot call '{name}' — flowsheet exists but contains no objects yet. "
                    "Add streams and unit operations first using add_object, "
                    "then connect them with connect_streams, then set feed conditions "
                    "with set_stream_property before solving."
                ),
                "_precondition_failed": True,
            }

    return None  # all preconditions met


def _compress_tool_result(name: str, result: dict) -> dict:
    """
    Reduce large tool results before storing in history to prevent context overflow.

    Strategy per tool type:
    - get_simulation_results / save_and_solve: keep success/error/safety fields
      verbatim; keep first 6 streams fully, summarise the rest.
    - search_knowledge: trim to first 1500 chars of each chunk.
    - Any result > 3000 chars: truncate with a note so the agent knows data exists.
    """
    if not isinstance(result, dict):
        return result

    _STREAM_RESULT_TOOLS = {
        "save_and_solve", "run_simulation", "get_simulation_results",
        "load_flowsheet", "new_flowsheet",
    }
    _MAX_RESULT_CHARS = 3000
    _MAX_STREAMS_FULL = 6   # streams shown in full; rest summarised

    if name in _STREAM_RESULT_TOOLS:
        sr = result.get("stream_results")
        if isinstance(sr, dict) and len(sr) > _MAX_STREAMS_FULL:
            keys = list(sr.keys())
            kept   = {k: sr[k] for k in keys[:_MAX_STREAMS_FULL]}
            rest_n = len(keys) - _MAX_STREAMS_FULL
            # Quick summary for omitted streams (tag + T + flow only)
            omitted_summary = []
            for k in keys[_MAX_STREAMS_FULL:]:
                p = sr[k]
                t = p.get("temperature_C", "?")
                f = p.get("mass_flow_kgh", "?")
                omitted_summary.append(f"{k}: T={t}°C, flow={f} kg/h")
            kept[f"[+{rest_n} more streams]"] = "; ".join(omitted_summary)
            result = {**result, "stream_results": kept}

    # ── Intelligent result summarization for high-information-density tools ───
    # Book insight (Chip Huyen Ch.6): inject summaries not raw data.
    # A parametric study with 50 points is 50 numbers — the LLM needs the
    # trend and optimum, not every data point.
    if name == "parametric_study" and isinstance(result, dict):
        data_pts = result.get("data_points") or result.get("results", [])
        if isinstance(data_pts, list) and len(data_pts) > 10:
            vals = [p.get("observe_value") for p in data_pts if p.get("observe_value") is not None]
            params = [p.get("vary_value") for p in data_pts if p.get("vary_value") is not None]
            if vals and params:
                best_idx = vals.index(min(vals))
                summary = {
                    "success":         result.get("success"),
                    "n_points":        len(data_pts),
                    "vary_range":      [min(params), max(params)],
                    "observe_range":   [min(vals), max(vals)],
                    "best_vary":       params[best_idx],
                    "best_observe":    vals[best_idx],
                    "trend":           "decreasing" if vals[-1] < vals[0] else "increasing" if vals[-1] > vals[0] else "non-monotonic",
                    "_summarized":     True,
                    "_note":           f"Parametric study with {len(data_pts)} points summarized. Full data available via get_simulation_results.",
                }
                return summary

    # find_flowsheets: cap path list, keep directory summary intact so the
    # agent can suggest where to look rather than refusing with a truncation
    # error. The LLM gets the count + a sample + top directories — enough to
    # answer "do I have any flowsheets?" or "where are they?" usefully.
    if name == "find_flowsheets" and isinstance(result, dict):
        paths = result.get("flowsheets") or []
        if isinstance(paths, list) and len(paths) > 15:
            kept = paths[:15]
            return {
                **{k: v for k, v in result.items() if k != "flowsheets"},
                "flowsheets": kept,
                "_truncated_to": 15,
                "_total_found": result.get("count", len(paths)),
                "_note": (
                    f"Showing {len(kept)} most-recent of "
                    f"{result.get('count', len(paths))} flowsheets. "
                    "Use 'top_directories' to suggest where to look, or "
                    "call find_flowsheets again with name_filter='...' to narrow."
                ),
            }

    if name == "monte_carlo_study" and isinstance(result, dict):
        samples = result.get("samples") or result.get("results", [])
        if isinstance(samples, list) and len(samples) > 10:
            vals = [s.get("output") or s.get("value") for s in samples if s.get("output") is not None or s.get("value") is not None]
            if vals:
                import statistics as _stats
                summary = {
                    "success":     result.get("success"),
                    "n_samples":   len(samples),
                    "mean":        round(_stats.mean(vals), 4),
                    "std":         round(_stats.stdev(vals), 4) if len(vals) > 1 else 0,
                    "min":         min(vals),
                    "max":         max(vals),
                    "p5":          sorted(vals)[int(0.05 * len(vals))],
                    "p95":         sorted(vals)[int(0.95 * len(vals))],
                    "_summarized": True,
                    "_note":       f"Monte Carlo with {len(samples)} samples summarized. Statistics shown.",
                }
                return summary

    # Final safety net: if still too large, truncate but report what was kept vs dropped
    try:
        serialised = json.dumps(result)
        if len(serialised) > _MAX_RESULT_CHARS:
            # Build per-key size summary so the agent knows what was dropped
            key_sizes = {}
            for k, v in result.items():
                try:
                    key_sizes[k] = len(json.dumps(v))
                except Exception:
                    key_sizes[k] = -1
            # Keep small-but-important keys verbatim
            kept = {}
            dropped = {}
            preserved_keys = {"success", "error", "warnings", "code", "message",
                              "_summarized", "_note", "convergence_warning",
                              "physical_warnings"}
            running = 0
            for k, sz in sorted(key_sizes.items(), key=lambda kv: kv[1]):
                if k in preserved_keys or running + sz < _MAX_RESULT_CHARS - 500:
                    kept[k] = result[k]
                    running += max(0, sz)
                else:
                    dropped[k] = f"{sz} chars"
            return {
                **kept,
                "_truncated":      True,
                "_dropped_keys":   dropped,
                "_original_size":  len(serialised),
                "_kept_size_est":  running,
                "_note":           (
                    f"Result truncated from {len(serialised)} to ~{running} chars. "
                    f"Dropped keys: {list(dropped.keys())[:5]}. "
                    f"Use get_simulation_results or specific getters to re-fetch dropped data."
                ),
            }
    except Exception:
        pass

    return result


def _trim_history(history: List[Dict], llm=None) -> List[Dict]:
    """
    Hierarchical history management:
      1. If history fits within limits → return as-is.
      2. If over limit → summarize the oldest half of messages into a compact
         state block, then keep the summary + recent messages.
    This preserves flowsheet context across long sessions instead of silently
    dropping the first tool calls (which tell the agent what was built).
    """
    # Fast path: within limits
    total_chars = sum(len(str(m.get("content", ""))) for m in history)
    if len(history) <= _MAX_HISTORY_MESSAGES and total_chars <= _MAX_HISTORY_CHARS:
        return history

    # Keep at minimum the last 8 messages intact (immediate context)
    _KEEP_RECENT = 8
    if len(history) <= _KEEP_RECENT:
        return history

    # Split: summarize older half, keep recent half
    split_idx    = max(len(history) - _KEEP_RECENT, len(history) // 2)
    to_summarize = history[:split_idx]
    to_keep      = history[split_idx:]

    # Build a compact summary from the older messages
    summary_lines = ["[SESSION HISTORY SUMMARY — earlier turns compressed to save context]"]
    tool_calls_seen: List[str] = []
    key_facts: List[str] = []

    for msg in to_summarize:
        role    = msg.get("role", "")
        content = msg.get("content") or ""

        if role == "user" and content:
            summary_lines.append(f"User asked: {str(content)[:120]}")

        elif role == "assistant" and content:
            # Extract tool call names from text if present
            if "tool_calls" in msg:
                for tc in (msg.get("tool_calls") or []):
                    tname = tc.get("name") or tc.get("function", {}).get("name", "")
                    if tname:
                        tool_calls_seen.append(tname)
            # Keep short assistant observations
            text = str(content).strip()
            if text and len(text) > 10:
                summary_lines.append(f"Agent: {text[:150]}")

        elif role == "tool":
            # Capture key facts from tool results
            try:
                result = json.loads(content) if isinstance(content, str) else content
                if isinstance(result, dict):
                    if result.get("success") is False:
                        err = str(result.get("error", ""))[:80]
                        summary_lines.append(f"Tool error: {err}")
                    elif "stream_results" in result:
                        n = len(result["stream_results"])
                        summary_lines.append(f"Simulation solved: {n} streams converged")
                    elif "flowsheet_name" in result or "name" in result:
                        name = result.get("flowsheet_name") or result.get("name", "")
                        summary_lines.append(f"Flowsheet created: {name}")
            except Exception:
                pass

    if tool_calls_seen:
        unique_tools = list(dict.fromkeys(tool_calls_seen))  # preserve order, deduplicate
        summary_lines.append(f"Tools used in earlier turns: {', '.join(unique_tools)}")

    summary_text = "\n".join(summary_lines)
    summary_msg  = {
        "role":    "system",
        "content": summary_text,
    }
    # No ack_msg needed — system messages don't require assistant acknowledgement

    new_history = [summary_msg] + list(to_keep)

    # Final safety net: if still too large, hard-trim to keep only the most recent messages
    final_chars = sum(len(str(m.get("content", ""))) for m in new_history)
    while final_chars > _MAX_HISTORY_CHARS and len(new_history) > _KEEP_RECENT:
        removed = new_history.pop(0)
        final_chars -= len(str(removed.get("content", "")))

    return new_history


try:
    from knowledge_base import KnowledgeBase as _KB
    _kb_instance = _KB()
except Exception:
    _kb_instance = None

# ── Thermodynamic property DB (SQLite-backed, lazy-init) ─────────────────────
try:
    from property_db import lookup_compound, lookup_pair_bips
    from property_db import PropertyDB as _PropDB

    def _prop_db_lookup(compound: str, properties=None):
        return _PropDB().lookup(compound, properties)

    def _prop_db_pair(comp1: str, comp2: str, model: str = "nrtl"):
        return _PropDB().lookup_pair(comp1, comp2, model)

    def _prop_db_psat(compound: str, T_C: float):
        return _PropDB().antoine_psat(compound, T_C)

    def _prop_db_search(query: str):
        return _PropDB().search_compound(query)

    def _prop_db_batch(compounds, properties=None):
        """Batch lookup — one round trip for N compounds (Review-3 AI Gap 7).
        Saves N-1 LLM iterations vs calling lookup_compound_properties N times."""
        # LLM sometimes passes a comma-separated string instead of a JSON array.
        # Coerce to list so the tool never hard-fails on a type mismatch.
        if isinstance(compounds, str):
            # Handle "CH4, CO2, H2O" or '["CH4","CO2"]' or "CH4"
            s = compounds.strip()
            if s.startswith("["):
                import json as _json
                try:
                    compounds = _json.loads(s)
                except Exception:
                    compounds = [x.strip().strip('"\'') for x in s.strip("[]").split(",")]
            else:
                compounds = [x.strip().strip('"\'') for x in s.split(",")]
        if not isinstance(compounds, list):
            compounds = [str(compounds)]
        db = _PropDB()
        out = {}
        missing = []
        for c in compounds:
            r = db.lookup(c, properties)
            if r.get("success"):
                out[c] = r
            else:
                missing.append(c)
        return {
            "success": len(missing) == 0,
            "results": out,
            "missing": missing,
            "count": len(out),
        }

except Exception:
    def _prop_db_lookup(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_pair(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_psat(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_search(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_batch(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}


# ─────────────────────────────────────────────────────────────────────────────
BASE_SYSTEM_PROMPT = textwrap.dedent("""
    You are an expert chemical process simulation engineer with deep knowledge
    of DWSIM process simulation software and thermodynamics.

    You control DWSIM through Python tool functions. Follow these rules:

    DYNAMIC REFLECTION — FULL DWSIM ACCESS (ESCAPE-HATCH TOOLS)
    ─────────────────────────────────────────────────────────────
    You have UNRESTRICTED access to EVERY property on EVERY DWSIM object.
    Use these tools when predefined tools are insufficient:

    • inspect_object(object_name, filter_prefix?)
        Discover all properties on any stream/unit-op/flowsheet object.
        ALWAYS call this FIRST when you don't know what properties exist.
        Example: inspect_object("COL-01", "Number") → shows NumberOfStages etc.

    • reflect_get_set(object_name, property_path, value?)
        Read ANY property: reflect_get_set("FEED", "Phases[0].Properties.temperature")
        Write ANY property: reflect_get_set("COL-01", "NumberOfStages", "25")
        Walk nested paths: reflect_get_set("FEED", "Compounds[\"Methanol\"].MoleFraction")

    • exec_python(code)
        Execute Python snippets against the live flowsheet.
        Context available: flowsheet, get_obj(name), results{}, math
        Use this for: custom diagnostics, multi-object operations, loops.
        Example:
          results["purity"] = get_obj("DISTILLATE").Phases[0].Compounds["Methanol"].MoleFraction
          results["stages"] = get_obj("COL-01").NumberOfStages

    • iterative_spec_loop(vary_object, vary_path, vary_lo, vary_hi,
                           observe_object, observe_path, target, tolerance=0.001)
        Automatically adjust a variable until an observable meets a specification.
        Uses bisection — no iteration from your side needed.
        Example: vary RefluxRatio until distillate Methanol.MoleFraction = 0.99

    PROTOCOL: For ANY novel task or unknown property:
      1. Call inspect_object() to discover what's available
      2. Call reflect_get_set() to read/write specific properties
      3. Call exec_python() for complex multi-step operations
      4. Call iterative_spec_loop() to autonomously meet a design spec
    NEVER say "I don't have access" — these tools give you full access.

    PROCESS DESIGN CAPABILITIES
    ────────────────────────────
    You can now design complex processes from scratch. Available tools:

    • process_synthesis(goal)          — Suggest complete flowsheet (Douglas methodology)
    • equipment_sizing(type, ...)      — Size heat exchangers, columns, pumps, compressors
    • separation_sequence(compounds)   — Synthesize separation train (distillation/absorption)
    • property_package_selector(comps) — Rigorous Carlson-1996 PP selection decision tree
    • heat_integration_targets(streams)— Pinch Analysis targets (Linnhoff cascade)
    • design_checklist(process_type)   — HAZOP-lite checklist for distillation/reactor/HX/compressor

    TROUBLESHOOTING CAPABILITIES
    ─────────────────────────────
    You can diagnose and fix ANY DWSIM convergence or configuration issue:

    • troubleshoot_dwsim(issue)        — Ranked root-cause analysis + step-by-step fixes
    • convergence_guide(unit_type)     — Unit-specific algorithm settings (column/recycle/PFR)
    • numerical_settings_advisor(desc) — Recommend solver parameters for a problem
    • decode_error(message)            — Decode DWSIM error text to human explanation + fixes

    When the user reports any convergence problem, ALWAYS call troubleshoot_dwsim FIRST,
    then explain the diagnosis and fixes. Do not say "I cannot troubleshoot" — you can.

    When the user asks to DESIGN a process:
    1. Call process_synthesis(goal) to get the flowsheet structure
    2. Call property_package_selector(compounds) to pick the right PP
    3. Call separation_sequence if product separation is needed
    4. Call equipment_sizing for each major unit
    5. Then build the flowsheet in DWSIM using the recommended units

    CRITICAL TOOL CALLING RULES
    ────────────────────────────
    • Tools with NO parameters must be called with an EMPTY argument object {}.
      Never pass null. Affected tools: find_flowsheets, list_simulation_objects,
      run_simulation, get_simulation_results, list_loaded_flowsheets,
      get_property_package.

    • NEVER guess file paths. When you need a flowsheet path, call
      find_flowsheets first (with {}). Use ONLY paths it returns.

    • find_flowsheets ALWAYS returns useful data: a `count`, up to 30 most-recent
      paths in `flowsheets`, and `top_directories`. NEVER respond "unable to list
      due to technical limitation" or "result was truncated". If the user asks
      to see flowsheets, REPORT what was returned: "You have N flowsheets;
      here are the most recent: …" and offer to narrow with name_filter or to
      load one by path.

    STANDARD WORKFLOW
    ─────────────────
    1. find_flowsheets {}           ← discover real paths on disk
    2. load_flowsheet path          ← loads AND auto-solves; reports property package
    3. (list_simulation_objects optional — see FLOWSHEET STATE below)
    4. Read / modify properties as requested
    5. run_simulation {}            ← always call after any parameter change
    6. get_simulation_results {}    ← read updated stream values
    7. Report with units: °C, bar, kg/h, mole fractions

    CRITICAL RULE — FLOWSHEET READINESS
    ─────────────────────────────────────
    When the state card shows Streams ≥ 1 OR Unit ops ≥ 1, the loaded
    flowsheet is READY. PROCEED with the user's request. DO NOT REFUSE
    on the basis that "property package is not set" or "compounds are not
    defined". The state card's PP / Compounds fields can appear empty
    when:
      (a) the flowsheet uses a plugin (Cantera, ChemSep, Reaktoro) that
          manages PP/compounds internally — they ARE configured, just not
          via DWSIM's standard introspection;
      (b) the bridge could not introspect those fields but the flowsheet
          itself has them defined.
    In both cases the flowsheet WILL solve correctly. Just call the
    requested operation (run_simulation, optimize, get_simulation_results,
    etc.) — the bridge will return a clear error ONLY if something is
    genuinely missing.

    NEVER reply with phrases like:
      ✗ "I cannot proceed because the flowsheet does not have a property
        package or compounds defined."
      ✗ "Please specify the compounds and the desired property package."
    when the state card shows objects are present. Just proceed.

    COMPOSITION SETTING (ACC-1)
    ───────────────────────────
    • Use set_stream_composition to set mole fractions on a feed stream.
    • Example: {"Methanol": 0.8, "Water": 0.2}
    • Values must sum to 1.0. Always call run_simulation after.

    THERMODYNAMIC MODEL (ACC-3)
    ───────────────────────────
    • Call get_property_package {} to see which EOS/activity model is in use.
    • Report the model to the user when discussing accuracy of results.

    CONVERGENCE AND AUTO-CORRECTION
    ────────────────────────────────
    • run_simulation returns convergence_check for all streams.
    • If convergence_check.all_converged is true — all streams solved; report results.
    • If auto_corrected is true — the engine applied automatic fixes (listed in
      fixes_applied) before achieving convergence. Report what was fixed to the user.
    • If auto_corrected is false and fixes_applied is non-empty — auto-correction
      was attempted but failed. Tell the user what was tried, then suggest:
        1. Check property package (polar → NRTL, hydrocarbons → PR)
        2. Check feed stream specs (T, P, flow all defined)
        3. For recycle loops: provide a good initial guess for the recycle stream
        4. For columns: start with a higher reflux ratio, then reduce
    • If convergence_check shows not_converged with no fixes_applied — the solver
      failed immediately; diagnose missing specs or wrong connections.

    FEED VALIDATION (ACC-4)
    ───────────────────────
    • load_flowsheet returns feed_warnings if T/P/flow specs are missing.
    • Report these warnings to the user.

    OPTIMISATION (ACC-5)
    ────────────────────
    • Use optimize_parameter to find the best parameter value automatically.
    • Example: find water flow that minimises outlet temperature.

    DISTILLATION COLUMNS (v3) — MODEL SELECTION IS CRITICAL
    ─────────────────────────────────────────────────────────
    Two fundamentally different distillation models exist in DWSIM:

    ShortcutColumn (Fenske-Underwood-Gilliland):
      • Purpose: preliminary design and feasibility screening only
      • Inputs:  light/heavy key components, desired recoveries, reflux ratio multiplier
      • Outputs: minimum reflux ratio (Rmin), minimum stages (Nmin), actual N
      • CANNOT produce: stage-by-stage temperature/composition profiles
      • Stage profile validation (SF-08d): NOT applicable — no per-stage data
      • Use when: user asks for "approximate" design or Nmin/Rmin estimates

    DistillationColumn (rigorous — MESH equations, stage-by-stage):
      • Purpose: accurate simulation with full stage profiles
      • Inputs:  N_stages, feed_stage, reflux_ratio, distillate_rate or reboiler_duty
      • Outputs: temperature profile (T must increase monotonically bottom→top),
                 composition profile per stage, condenser/reboiler duties
      • Stage profile validation (SF-08d): ACTIVE — temperature inversions are flagged
      • Use when: user asks for rigorous simulation, purity specs, or column sizing
      • Convergence tip: start with RR = 1.5 × Rmin; increase if it fails to converge

    DECISION RULE:
      1. Always state which model you are using in your response
      2. For journal-quality results: use DistillationColumn (rigorous)
      3. For quick screening: ShortcutColumn is acceptable, but state limitations
      4. Do NOT mix up the two — they have different input requirements

    • Use get_column_properties to read reflux ratio, stages, duties, condenser type.
    • Use set_column_property to change specs, then run_simulation.
    • Also supported: AbsorptionColumn, RefluxedAbsorber, ReboiledAbsorber.

    REACTORS (v3)
    ─────────────
    • Use get_reactor_properties for any of 5 reactor types:
      CSTR, PFR, GibbsReactor, ConversionReactor, EquilibriumReactor.
    • Use set_reactor_property to change Volume, Conversion, Temperature, etc.
    • Always call run_simulation after changes.

    ACADEMIC RESEARCH REPORTS (v4)
    ──────────────────────────────────
    • When the user asks for a report, research paper, or PDF of results:
      1. Run parametric_study to collect data (if not already done)
      2. Analyse the data — identify the trend, find optima, note inflection points
      3. Draft ALL SIX report_text sections BEFORE calling generate_report:
           abstract, introduction, methodology, results, discussion, conclusion
         Write them as complete academic paragraphs — no placeholders.
      4. Call generate_report {title, study_data, report_text, output_dir (optional)}
      5. Tell the user the PDF path and highlight the key finding

    • report_text quality rules:
      - abstract (150-250 w): State context, objective, method, key quantitative result.
      - introduction (150-300 w): Background on the process, industrial relevance,
        motivation for the parametric study, scope of the analysis.
      - methodology (200-400 w): DWSIM flowsheet description, thermodynamic model
        (call get_property_package to get the exact name), feed specifications,
        parameter range studied, and simulation procedure.
      - results (200-350 w): Objective description of what the data shows — trend
        direction, magnitude of change, location of optima. Reference Figure 1/2/3.
      - discussion (200-350 w): Explain WHY each trend exists using thermodynamics /
        transport phenomena. Compare with theory or literature. Discuss model limits.
      - conclusion (100-200 w): Engineering recommendation with specific numbers,
        and one or two suggestions for future work.

    • NEVER call generate_report without all six sections — empty sections produce
      a useless PDF.

    AI FLOWSHEET BUILDING
    ─────────────────────
    PREFERRED — build_flowsheet_atomic (1 call, whole flowsheet)
    ─────────────────────────────────────────────────────────────
    When you have the complete spec (compounds, objects, connections, feeds,
    unit op settings), call build_flowsheet_atomic once.  It pre-validates the
    spec, builds, connects, configures, and solves in a single round trip.
    Use this for ALL new flowsheet builds unless the user asks to modify an
    existing one step by step.

    Example (water heater):
      build_flowsheet_atomic {
        name: "water_heater",
        compounds: ["Water"],
        property_package: "Steam Tables (IAPWS-IF97)",
        objects: [
          {tag: "Feed",    type: "MaterialStream"},
          {tag: "Product", type: "MaterialStream"},
          {tag: "Q",       type: "EnergyStream"},
          {tag: "H-101",   type: "Heater"}
        ],
        connections: [
          {from_tag: "Feed",  to_tag: "H-101",   from_port: 0, to_port: 0},
          {from_tag: "H-101", to_tag: "Product", from_port: 0, to_port: 0},
          {from_tag: "Q",     to_tag: "H-101",   from_port: 0, to_port: 1}
        ],
        feed_specs: [
          {tag: "Feed", temperature: 25, temperature_unit: "C",
           pressure: 1, pressure_unit: "bar",
           massflow: 3600, massflow_unit: "kg/h",
           composition: {"Water": 1.0}}
        ],
        unit_op_specs: [
          {tag: "H-101", property_name: "OutletTemperature", value: "80", unit: "C"},
          {tag: "H-101", property_name: "DeltaP", value: "0"}
        ]
      }

    FALLBACK — step-by-step (use only when modifying an existing flowsheet)
    ────────────────────────────────────────────────────────────────────────
    NEVER call create_flowsheet — it is disabled. Use these tools instead:

    STEP 1  new_flowsheet {name, compounds, property_package}
            → Call EXACTLY ONCE. Creates a fresh blank simulation.

    STEP 2  add_object {tag, type}   ← repeat for EVERY stream and unit op

    STEP 3  connect_streams {from_tag, to_tag, from_port, to_port}
            → Port rules:
               Material inlet to unit op   → to_port=0
               Material outlet from unit op → from_port=0
               Second material outlet       → from_port=1
               Energy stream to unit op    → to_port=1  (Heater/Cooler/Pump)
               Second feed (Mixer)         → to_port=1

    STEP 4  set_stream_property {tag, property_name, value, unit}

    STEP 5  set_stream_composition {tag, fractions}

    STEP 6  set_unit_op_property {tag, property_name, value}

    STEP 7  save_and_solve {}

    RULES:
    • ALWAYS prefer build_flowsheet_atomic over the 7-step approach for new builds.
    • When a tool returns a "hint" field, follow it before retrying.
    • Add EnergyStream for every Heater, Cooler, Pump, Compressor, Expander.
    • If save_and_solve returns converged=False, call run_simulation once more.

    SAFETY VALIDATOR (runs automatically after every solve):
    • save_and_solve returns a "safety_status" field:
        - "PASSED"              → all 7 SF checks cleared; result is physically plausible
        - "PRE_SOLVE_VIOLATION" → solve was BLOCKED; pre_solve_failures list has exact fix
        - "VIOLATIONS_DETECTED" → solve ran but post-solve checks found issues
    • If safety_status = "PRE_SOLVE_VIOLATION":
        1. Read pre_solve_failures[].fix — it contains the EXACT tool call to fix it
        2. Execute that fix, then call save_and_solve again
        3. Do NOT report the original bad result to the user
    • If safety_warnings is non-empty after solve:
        1. Report each violation code + description to the user
        2. The description field contains an exact corrective tool call
        3. For SILENT severity: result is convergent but wrong — do NOT present as valid
    • If sf05_auto_corrections > 0: VF noise was auto-corrected; mention to user
    • All 13 silent failure modes are now caught before or immediately after solve:
        SF-01 fixed in bridge      SF-02 blocked pre-solve   SF-03 fixed in bridge
        SF-04 rejected pre-set     SF-05 auto-corrected      SF-06 blocked pre-solve
        SF-07 blocked pre-solve    SF-08 post-solve duty/η check
        SF-09 global mass+energy balance violation
        SF-10 supercritical T>Tc AND P>Pc (cubic EOS may fail)
        SF-11 NaN/Inf/out-of-range vapor fraction (impossible flash)
        SF-12 VLLE risk for partially-miscible pairs (water+hydrocarbon, etc.)
        SF-13 phase consistency: stream P vs Antoine Psat vs reported VF

    PROPERTY PACKAGE SELECTION:
    • Water / steam                 → "Steam Tables (IAPWS-IF97)"
    • Hydrocarbons, oil, gas        → "Peng-Robinson (PR)"
    • Alcohol / water, polar mix    → "NRTL"
    • Light gases, petrochemical    → "Soave-Redlich-Kwong (SRK)"
    • Refrigerants, cryogenics      → "CoolProp"

    UNIT OP TYPE NAMES (exact values for add_object):
      Heater, Cooler, HeatExchanger, Pump, Compressor, Expander, Valve
      Mixer (stream combiner), Splitter (stream divider)
      Separator (flash drum / two-phase vessel)
      DistillationColumn, AbsorptionColumn, ShortcutColumn
      CSTR, PFR, GibbsReactor, ConversionReactor, EquilibriumReactor
      Pipe, CompoundSeparator

    <<ICL:FEWSHOT>>
    EXAMPLE — Water heater (25°C → 80°C, 1 atm, 1 kg/s, Steam Tables):
      new_flowsheet {name:"water_heater", compounds:["Water"],
                     property_package:"Steam Tables (IAPWS-IF97)"}
      add_object {tag:"Feed",    type:"MaterialStream"}
      add_object {tag:"Product", type:"MaterialStream"}
      add_object {tag:"Q",       type:"EnergyStream"}
      add_object {tag:"H-101",   type:"Heater"}
      connect_streams {from_tag:"Feed",  to_tag:"H-101",   from_port:0, to_port:0}
      connect_streams {from_tag:"H-101", to_tag:"Product", from_port:0, to_port:0}
      connect_streams {from_tag:"Q",     to_tag:"H-101",   from_port:0, to_port:1}
      set_stream_property {tag:"Feed", property_name:"temperature", value:298.15, unit:"K"}
      set_stream_property {tag:"Feed", property_name:"pressure",    value:101325, unit:"Pa"}
      set_stream_property {tag:"Feed", property_name:"massflow",    value:1,      unit:"kg/s"}
      set_stream_composition {tag:"Feed", fractions:{"Water":1.0}}
      set_unit_op_property {tag:"H-101", property_name:"OutletTemperature", value:353.15}
      set_unit_op_property {tag:"H-101", property_name:"DeltaP",            value:0}
      save_and_solve {}
    <<ICL:/FEWSHOT>>

    PHASE-SPECIFIC RESULTS (v5)
    ────────────────────────────
    • After run_simulation, use get_phase_results to read vapor/liquid split separately.
    • phase values: 'vapor', 'liquid', 'liquid1', 'liquid2', 'solid', 'overall'.
    • Useful after flash drums, condensers, and distillation columns.

    PHASE ENVELOPE / PT DIAGRAM
    ────────────────────────────
    • Use calculate_phase_envelope when the user asks about: dew point, bubble point,
      cricondentherm, cricondenbar, retrograde condensation, two-phase pipeline design,
      gas-condensate phase behaviour, hydrocarbon phase region, P-T envelope.
    • Returns the bubble curve, dew curve, and critical point for the active mixture.
    • Interpret: cricondentherm = max T at which two phases coexist (above this, only
      vapor regardless of P). Cricondenbar = max P at which two phases coexist.
      Retrograde region: between critical point and cricondentherm — liquid forms on
      pressure REDUCTION (counterintuitive; common in gas-condensate reservoirs).

    ENERGY STREAMS (v5)
    ────────────────────
    • Use get_energy_stream to read condenser/reboiler/heater duties (W, kW, kJ/h).
    • Use set_energy_stream to fix a duty before solving (value in Watts).

    FLOWSHEET EDITING (v5)
    ───────────────────────
    • delete_object: remove a stream or unit op (irreversible without reload).
    • disconnect_streams: sever the link between a unit op and a stream.
    • Both require run_simulation afterward to recalculate the flowsheet.

    REACTION SETUP (v5)
    ────────────────────
    • Use setup_reaction on ConversionReactor to set conversion and base compound.
    • Provide a list of reactions with name, type, base_compound, conversion.
    • For complex kinetics, configure in the DWSIM GUI and load the saved file.

    COLUMN BATCH SPECS (v5)
    ────────────────────────
    • Use set_column_specs to set n_stages, reflux_ratio, feed_stage, condenser_type,
      duties, and flow rates in a single call. Omit any parameter you don't want to change.

    DYNAMIC FLOWSHEETS (v3)
    ───────────────────────
    • Call detect_simulation_mode {} to check if flowsheet is steady-state or dynamic.
    • For dynamic flowsheets, report integrator type, time step, and current time.

    PLUGIN / CUSTOM UNIT OPS (v3)
    ──────────────────────────────
    • Use get_plugin_info to inspect Cantera, Reaktoro, Excel UO, Script UO, FOSSEE blocks.
    • Returns plugin type, configuration, script path, and last calculation status.

    MULTI-FLOWSHEET
    ───────────────
    • To compare two flowsheets: load both with different aliases, then use
      switch_flowsheet to toggle between them.

    PARAMETRIC STUDY (1D and 2D)
    ─────────────────────────────
    • parametric_study: one input → one output. Present as formatted table.
    • parametric_study_2d: TWO inputs → one output (full response surface).
      Use when user asks "how do temperature AND pressure affect yield?"
      Equivalent to RSM / Central Composite Design data in research papers.
      Returns a matrix: ideal for identifying the optimal operating region.

    KNOWLEDGE BASE (RAG)
    ─────────────────────
    • A chemical engineering knowledge base is available via search_knowledge.
    • WHEN TO USE: Any time the user asks a conceptual question (e.g. "which
      property package should I use?", "why is my column not converging?",
      "what does LMTD mean?", "how do I choose a reactor type?").
    • HOW TO USE: Call search_knowledge {"query": "your question"} FIRST,
      then synthesise the retrieved chunks with your own reasoning.
    • Always cite the source field from retrieved chunks when you use them.
    • Do NOT use knowledge base for pure simulation tasks (set properties,
      run, read results) — use the DWSIM tools directly for those.

    INDUSTRIAL OPTIMISATION — DECISION TREE
    ─────────────────────────────────────────
    Choose the RIGHT tool for the optimisation task:

    0. VAGUE / NATURAL-LANGUAGE GOAL (FIRST CHOICE for under-specified requests):
       Phrases like: "maximise hydrogen yield", "optimise the process",
       "improve purity", "minimise energy", "find the best operating point"
       — when the user did NOT explicitly give variables/bounds/objective:
       → optimize_flowsheet_with_llm {"goal": "<user's phrase verbatim>"}
       DO NOT ask the user for variables, bounds, or properties up front.
       The tool auto-discovers decision variables from the loaded flowsheet,
       maps the goal to an objective via the LLM, runs DWSIM-internal
       optimisation (DotNumerics: L-BFGS-B / Simplex / DE), and returns a
       poster-style result. If it fails with NO_FLOWSHEET, only THEN tell
       the user to load a flowsheet first.

    1. SINGLE VARIABLE — user gave EXACT vary_tag, vary_property, bounds:
       → optimize_parameter {vary_tag, vary_property, lower, upper, observe_tag, observe_property}

    2. MULTI-VARIABLE, USER GAVE FULL SPEC (variables[], objective dict):
       → dwsim_optimize — uses DWSIM's internal solvers (L-BFGS-B, Simplex,
         Truncated Newton, Powell, DE). Best when caller wants a specific
         solver.
       → bayesian_optimize — adaptive GP surrogate, best for expensive sims
       → optimize_multivar — differential evolution, good for many variables

    3. MULTI-VARIABLE WITH CONSTRAINTS (industrial: purity ≥ X%, T ≤ Y°C):
       → optimize_constrained — inequality constraints via penalty functions
       Example: maximize H2 yield subject to CO ≤ 100 ppm AND P ≤ 20 bar

    4. COMPETING OBJECTIVES (yield vs energy trade-off → Pareto front):
       → optimize_multiobjective — returns n Pareto-optimal design points
       Use when user asks "what's the trade-off between X and Y?"

    5. RESPONSE SURFACE (show how two variables interact → like RSM papers):
       → parametric_study_2d — full n1×n2 matrix of results

    CONVERGENCE STRATEGY — INDUSTRIAL FLOWSHEETS
    ──────────────────────────────────────────────
    • ALWAYS use robust_solve instead of save_and_solve for:
      - Recycle loops (OT_Recycle blocks)
      - Gas processing trains with many unit ops
      - Any flowsheet that fails save_and_solve on first attempt
      strategy='robust'    → 3 reload+solve cycles
      strategy='aggressive'→ 5 cycles + feed temperature perturbation

    • For DISTILLATION COLUMNS (DistillationColumn, AbsorptionColumn):
      NEVER use save_and_solve directly. ALWAYS use initialize_distillation.
      It auto-escalates: Inside-Out → Burningham-Otto → Sum-Rates.
      Always provide T_top_C (condenser region T) and T_bot_C (reboiler T).
      Example:
        initialize_distillation {column_tag:"COL-01", T_top_C:78, T_bot_C:105,
                                  algorithm:"auto", reflux_ratio:2.5}

    • After optimisation, call get_simulation_results to confirm the final state.
    • Always state the objective, decision variables, bounds, constraints, and result.

    ENGINEERING REPORTING
    ──────────────────────
    • Report temperature in both K and °C.
    • Report pressure in both Pa and bar.
    • If convergence_errors is non-empty, warn the user clearly.
    • Never invent property values — only report what tools return.
    • If a tool returns success=false, read the error and try a corrected call.
    • NEVER report data from the wrong flowsheet. If the user asks to LOAD or USE
      a specific named flowsheet (e.g. "the heat exchanger flowsheet") and you
      cannot find or load it, SAY SO EXPLICITLY ("I could not find a heat
      exchanger flowsheet — here is what is available: …") and STOP. Do NOT
      report the streams of whatever happens to be loaded as if it were the
      requested flowsheet — that is a fabrication. Before reporting results,
      confirm via loaded_flowsheet / list_simulation_objects that the active
      flowsheet is actually the one the user asked about.

    MANDATORY WORKFLOW — FOLLOW THIS ORDER
    ───────────────────────────────────────
    ⚡ TEMPLATE SHORTCUT: If the user asks to "use template X" or "build
    from template X", call create_from_template {name:"X"} IMMEDIATELY —
    skip ALL property lookups below. Templates have compounds, property
    package, streams, unit ops, and reactions pre-configured.
    Available templates: biogas_smr_h2, biogas_smr_h2_gibbs, flash_separation,
    shortcut_distillation, absorber, heater_cooler, heat_exchanger, pump_valve,
    conversion_reactor, gibbs_reactor, reactor_recycle, stream_blender, water_electrolyzer.

    For building flowsheets FROM SCRATCH (no template), you MUST:
    1. Call batch_lookup_properties with ALL key compounds in ONE call
       (not lookup_compound_properties one-at-a-time) to verify Tc, Pc,
       omega, and boiling point. Example:
       batch_lookup_properties {compounds: ["Methane","CO2","Water","Hydrogen"]}
    2. Call lookup_binary_parameters ONLY for polar pairs (alcohols,
       ketones, acids + water). Skip for pure hydrocarbon / gas systems.
    3. Choose the correct property package based on lookup results:
       - Hydrocarbons / gas mixtures → Peng-Robinson (PR)
       - Polar organics + water → NRTL or UNIQUAC
       - Pure water / steam → Steam Tables (IAPWS-IF97)
       - Refrigerants → CoolProp or PR
       - Acid gases (CO2, H2S) in hydrocarbons → PR with kij ≠ 0
    4. Then proceed: new_flowsheet → add_object → connect_streams →
       set_stream_property → save_and_solve.

    <<ICL:COT>>
    STRUCTURED REASONING FOR PROCESS ENGINEERING
    ──────────────────────────────────────────────
    For any simulation task, reason in this order:
    1. MASS BALANCE: Identify feeds, products, and recycles. Check that
       mass in ≈ mass out (within 2%) after solving.
    2. ENERGY BALANCE: Account for all heater/cooler duties and heat
       exchanger transfers. Flag if duty seems unrealistically large.
    3. EQUIPMENT SIZING: Verify temperatures and pressures are physically
       achievable. Check that column reflux ratio > minimum reflux.
    4. OPTIMISATION: Only after the base case converges, explore
       parametric variations or call bayesian_optimize.
    State each step explicitly in your response.
    <<ICL:/COT>>

    DISTILLATION COLUMN INITIALIZATION
    ────────────────────────────────────
    When building a distillation column that fails to converge:
    1. Run shortcut first: use get_object_properties to check if a
       ShortcutColumn is available — use it to estimate theoretical stages N
       and minimum reflux ratio R_min from Fenske-Underwood-Gilliland.
    2. Initialize rigorous column with N_actual = 1.5–2× N_shortcut,
       R_actual = 1.2–1.5× R_min.
    3. Set a temperature profile: top T ≈ bubble point of distillate,
       bottom T ≈ bubble point of bottoms (call compute_vapor_pressure
       to estimate these before setting).
    4. For azeotropic systems: check azeotrope data via search_knowledge
       before assuming simple distillation is feasible.

    CONVERGENCE TROUBLESHOOTING
    ────────────────────────────
    If save_and_solve fails:
    1. Check _diagnosis_hint in the error response (auto-generated).
    2. Verify all feed streams have T, P, and flow specified.
    3. Check for disconnected streams via validate_topology.
    4. For recycle loops: call initialize_recycle before solving.
    5. Reduce complexity: simplify the flowsheet, solve partial sections.

    INTENT DECLARATION (verifier-aware planning)
    ────────────────────────────────────────────────────
    Convergence is NOT correctness. A run can converge with mass balance off
    by 30%, the wrong phase in a product stream, or a unit setpoint silently
    overridden. Call declare_intent ONCE after planning, BEFORE the first
    save_and_solve / build_flowsheet_atomic. The verifier will score every
    target after solving and attach a per-target intent_verification block
    to the solve result with a repair_hint per failed target.

    Example (ammonia loop with a purity target):
      declare_intent {
        feed_streams: ["FreshFeed"],
        product_streams: ["V1.liquid"],
        note: "Haber-Bosch loop producing >=95% pure ammonia",
        targets: [
          {kind: "product_purity", stream_tag: "V1.liquid",
           compound: "Ammonia", expected: 0.95},
          {kind: "unit_setpoint", unit_tag: "R1",
           property_name: "OutletTemperature", expected: 723.15, tolerance: 5.0}
        ]
      }

    If a target fails after solve, fix the SPECIFIC issue named in the
    repair_hint — do not rewrite the flowsheet. Re-solve and re-check.

    LITERATURE COMPARISON & PUBLICATION-QUALITY RESULTS
    ─────────────────────────────────────────────────────
    After any simulation completes, call compare_to_literature to validate
    results against published data:
      compare_to_literature {process: "biogas_smr_h2", tolerance_pct: 5}

    This compares your DWSIM results against Ullah et al. (2025) Digital Chem Eng 14:100205
    and returns a formatted markdown table for direct inclusion in a paper.

    WORKFLOW FOR PUBLICATION-QUALITY BIOGAS-TO-H2 RESULTS:
    ────────────────────────────────────────────────────────
    1. Build flowsheet:
       create_from_template {name: "biogas_smr_h2_gibbs"}
       (or build_flowsheet_atomic with the full spec)
    2. Solve:
       robust_solve {strategy: "robust"}
    3. Validate against literature:
       compare_to_literature {process: "biogas_smr_h2", tolerance_pct: 5}
    4. Run parametric study to reproduce RSM results from Ullah 2025:
       parametric_study_2d {
         vary1_tag: "REF-101", vary1_property: "Temperature", vary1_unit: "C",
         vary1_values: [750, 800, 850, 900, 950],
         vary2_tag: "BIOGAS-IN", vary2_property: "massflow", vary2_unit: "kg/h",
         vary2_values: [30, 35, 38.5, 42, 46],
         observe_tag: "HYDROGEN", observe_property: "mass_flow_kgh"
       }
    5. Optimize for maximum H2 yield:
       bayesian_optimize {
         variables: [
           {tag:"REF-101", property:"Temperature", unit:"C", lower:750, upper:950},
           {tag:"BIOGAS-IN", property:"massflow", unit:"kg/h", lower:30, upper:50}
         ],
         observe_tag: "HYDROGEN", observe_property: "mass_flow_kgh",
         minimize: false, n_initial: 5, max_iter: 20
       }
    6. Generate academic report:
       generate_report {title: "Biogas-to-Hydrogen via SMR: DWSIM Optimization", ...}

    KEY REFERENCE VALUES (Ullah 2025, base case T=909°C, P=16 bar, S/C=2.5):
      BIOGAS-IN: 38.5 kg/h (CH4=59.97%, CO2=40.06%)  WATER-IN: 46.0 kg/h
      HYDROGEN product: 10.8 kg/h, 99.9% purity, P=15.5 bar
      CH4 conversion: 97.8%  |  CO2 conversion: 95.2%  |  H2 yield: 3.45 mol/mol CH4
      If your results are within 5% of these values → simulation is validated.

    REACT REASONING PATTERN (Reason → Act → Observe → Reason)
    ────────────────────────────────────────────────────────────
    Before EVERY tool call, state your reasoning briefly:
      REASON: "I need to check the Tc of ethanol before choosing property package."
      ACT: [call lookup_compound_properties]
      OBSERVE: [read result]
      REASON: "Tc=513.9K, polar compound — NRTL is correct."
      ACT: [call new_flowsheet with NRTL]
    This explicit reasoning prevents cascading errors and helps the user
    understand your decisions. Keep each REASON to 1-2 sentences.

    {flowsheet_context}
""").strip()


def _strip_icl(prompt: str, disable_cot: bool = False,
               disable_fewshot: bool = False) -> str:
    """ICL ablation (Tian et al. Table 4): when a toggle is set, remove the
    marker-delimited few-shot worked examples (<<ICL:FEWSHOT>>…) or
    chain-of-thought reasoning block (<<ICL:COT>>…). The bare <<ICL:…>> markers
    are ALWAYS stripped, so a normal (un-ablated) prompt equals the unmarked
    original."""
    import re
    for tag, drop in (("FEWSHOT", disable_fewshot), ("COT", disable_cot)):
        if drop:
            prompt = re.sub(r"[ \t]*<<ICL:%s>>.*?<<ICL:/%s>>[ \t]*\n?" % (tag, tag),
                            "", prompt, flags=re.DOTALL)
    return re.sub(r"[ \t]*<<ICL:/?(?:FEWSHOT|COT)>>[ \t]*\n?", "", prompt)


def _build_system_prompt(bridge: DWSIMBridgeV2,
                         state_delta: Optional[str] = None,
                         user_message: str = "") -> str:
    context = bridge.state.context_summary()
    # Insert the cache breakpoint just before the dynamic flowsheet context so
    # the stable instruction prefix above it can be prompt-cached (Anthropic).
    # The placeholder sits at the very end of BASE_SYSTEM_PROMPT, so everything
    # appended below (state delta, RAG, memory) also lands in the dynamic, an
    # uncached suffix — exactly what we want.
    prompt  = BASE_SYSTEM_PROMPT.replace(
        "{flowsheet_context}", CACHE_BREAKPOINT + context)

    # ICL ablation (Tian et al. Table 4): strip few-shot examples / CoT reasoning
    # under the no_fewshot / no_cot conditions. This ALWAYS runs so the bare
    # <<ICL:…>> markers never leak into a live prompt; the toggles are False in
    # normal operation, leaving the content intact.
    try:
        from ablation_config import ablation as _abl_icl
        prompt = _strip_icl(prompt, disable_cot=_abl_icl.disable_cot,
                            disable_fewshot=_abl_icl.disable_fewshot)
    except Exception:
        prompt = _strip_icl(prompt)

    if state_delta:
        prompt = prompt + "\n\nFLOWSHEET STATE SINCE LAST TURN\n" \
                          "───────────────────────────────\n" + state_delta

    # ── Proactive RAG injection ───────────────────────────────────────────────
    # Retrieve relevant KB chunks for the user's message BEFORE the LLM call.
    # This ensures domain knowledge is always available, even when the LLM
    # forgets to call search_knowledge explicitly (common with smaller models).
    # Only runs when user_message is provided (first iteration of each turn).
    # ── Proactive RAG injection ───────────────────────────────────────────────
    # Fire when: (a) user_message provided AND (b) message is substantive.
    # Unlike the original iter==1 restriction (in chat()), _build_system_prompt
    # is called every iteration — but with user_message="" after iter 1.
    # We therefore inject on ANY call that receives a non-empty user_message,
    # which covers: first turn, AND any turn where query changed substantially.
    # Ablation: the no_rag (and direct_llm) condition disables retrieval entirely.
    try:
        from ablation_config import ablation as _abl_rag
        _rag_disabled = _abl_rag.disable_rag
    except Exception:
        _rag_disabled = False
    if not _rag_disabled and user_message and len(user_message.strip()) > 10:
        try:
            if _kb_instance is not None:
                _kb = _kb_instance
            else:
                from knowledge_base import KnowledgeBase as _KB
                _kb = _KB()
            _rag_threshold = float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0.5"))
            _rag_top_k = int(os.getenv("RAG_TOP_K", "4"))
            kb_res = _kb.search(user_message, top_k=_rag_top_k)
            chunks = kb_res.get("results", []) if kb_res.get("success") else []
            # Filter on BM25 score. Set RAG_RELEVANCE_THRESHOLD=0 to disable filtering.
            chunks = [c for c in chunks if c.get("relevance_score", 0) > _rag_threshold]
            if chunks:
                logging.getLogger("agent_v2").info(
                    "RAG: injecting %d chunks (threshold=%s)", len(chunks), _rag_threshold
                )
            if chunks:
                kb_block = "\n\nRELEVANT KNOWLEDGE BASE CONTEXT (auto-retrieved)\n" \
                           "─────────────────────────────────────────────────\n"
                for c in chunks[:3]:  # cap at 3 to control context size
                    title = c.get("title", "")
                    text  = (c.get("text") or "")[:500]  # slightly more per chunk
                    score = c.get("relevance_score", 0)
                    kb_block += f"[{title}] (relevance={score:.1f})\n{text}\n\n"
                kb_block += "Call search_knowledge for deeper retrieval on specific sub-topics."
                prompt = prompt + kb_block
        except Exception:
            pass   # KB must never break the agent

    # ── Session memory ────────────────────────────────────────────────────────
    try:
        import session_memory
        memory_block = session_memory.compose_context_block()
    except Exception:
        memory_block = ""
    if memory_block:
        prompt = prompt + "\n\n" + memory_block

    return prompt


# Numerical keys we snapshot per stream. Kept small — values are rounded so
# floating-point noise doesn't spuriously trigger a diff.
_SNAPSHOT_STREAM_KEYS = (
    "temperature_K", "pressure_Pa", "mass_flow_kg_s",
    "molar_flow_mol_s", "vapor_fraction",
)


def _round_for_diff(v: Any) -> Any:
    # Sig-fig rounding so the diff behaves sensibly across scales: a mass flow
    # of 1e-5 kg/s mustn't collapse to 0.0, and 101325.0 Pa mustn't be reported
    # to 4 decimals. 5 sig figs coalesces solver noise without losing resolution.
    if not isinstance(v, float):
        return v
    import math as _math
    if v == 0.0 or not _math.isfinite(v):
        return v
    return float(f"{v:.6g}")


def snapshot_flowsheet_state(bridge: DWSIMBridgeV2) -> Dict[str, Dict[str, Any]]:
    """{stream_tag: {T, P, flows, VF}} for the currently loaded flowsheet.
    Empty dict if nothing is loaded or the bridge errors."""
    snap: Dict[str, Dict[str, Any]] = {}
    if not bridge.state.name:
        return snap
    for tag in list(bridge.state.streams):
        try:
            r = bridge.get_stream_properties(tag)
        except Exception:
            continue
        if not r.get("success"):
            continue
        props = r.get("properties", {})
        snap[tag] = {k: _round_for_diff(props[k])
                     for k in _SNAPSHOT_STREAM_KEYS
                     if k in props}
    return snap


def diff_flowsheet_state(prev: Dict[str, Dict[str, Any]],
                         curr: Dict[str, Dict[str, Any]]) -> str:
    """Human-readable delta between two snapshots. Empty string if identical."""
    # Flowsheet just unloaded (or never loaded) — don't emit a wall of "removed"
    # lines that would only confuse the model. The topology summary in
    # context_summary() already communicates "no flowsheet loaded".
    if not curr:
        return "" if not prev else "(flowsheet unloaded since last turn)"
    lines: List[str] = []
    added   = sorted(set(curr) - set(prev))
    removed = sorted(set(prev) - set(curr))
    for tag in added:
        vals = ", ".join(f"{k}={v}" for k, v in curr[tag].items())
        lines.append(f"+ {tag}: {vals}")
    for tag in removed:
        lines.append(f"- {tag} (removed)")
    for tag in sorted(set(prev) & set(curr)):
        p, c = prev[tag], curr[tag]
        changed = [(k, p.get(k), c.get(k)) for k in set(p) | set(c)
                   if p.get(k) != c.get(k)]
        if changed:
            parts = ", ".join(f"{k}: {pv} → {cv}" for k, pv, cv in changed)
            lines.append(f"~ {tag}: {parts}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────

class DWSIMAgentV2:

    def __init__(
        self,
        llm:            LLMClient,
        bridge:         Optional[DWSIMBridgeV2] = None,
        *,
        max_iterations: int = 20,
        verbose:        bool = True,
        stream_output:  bool = True,
        on_tool_call:   Optional[Callable[[str, dict, dict], None]] = None,
        on_token:       Optional[Callable[[str], None]] = None,
        auto_correct:   bool = True,
    ) -> None:
        self.llm            = llm
        # The user's SELECTED provider must not silently/permanently switch on a
        # transient failure — the agent does its own (transient) failover via
        # _FAILOVER_CHAIN. So disable in-client cross-provider switching on the
        # primary; it still does within-provider model fallback.
        if self.llm is not None:
            try: self.llm._allow_provider_switch = False
            except Exception: pass
        # The client that produced the most recent response — used to parse it
        # (assistant_turn/tool_result_turns) with the matching provider format
        # even when a transient failover answered on a different provider.
        self._response_client = llm
        # Sticky failover client for the CURRENT user turn: once a turn fails
        # over to a fallback provider, the rest of that turn's iterations stay
        # on it so the message history stays in ONE provider's format (Anthropic
        # uses content-blocks and rejects the OpenAI/Groq {"role":"tool",…} and
        # tool_calls turn shapes, and vice-versa).
        # Reset to None at the start of every new user turn so the user's
        # selected provider always gets first chance again (no hidden drift).
        self._turn_client = None
        self.bridge         = bridge or DWSIMBridgeV2()
        self.max_iterations = max_iterations
        self.verbose        = verbose
        self._auto_correct  = auto_correct
        self.stream_output  = stream_output
        self.on_tool_call   = on_tool_call
        self.on_token       = on_token   # called with each token for SSE streaming

        self._history: List[Dict] = []
        # State-diffing: snapshot of per-stream numerical values from the
        # previous turn. The first turn injects the full state; subsequent
        # turns inject only the delta to keep the system prompt small.
        self._last_state_snapshot: Dict[str, Dict[str, Any]] = {}

        # Reproducibility replay logging
        import uuid as _uuid
        self._session_id  = _uuid.uuid4().hex
        self._turn_index  = 0
        self._replay_builder = None   # TurnBuilder, active during chat()

        self._tools: Dict[str, Callable] = {
            "find_flowsheets":        self.bridge.find_flowsheets,
            "load_flowsheet":         lambda path, alias=None:
                                          self.bridge.load_flowsheet(path, alias),
            "save_flowsheet":         lambda path=None:
                                          self.bridge.save_flowsheet(path),
            "list_loaded_flowsheets": self.bridge.list_loaded_flowsheets,
            "switch_flowsheet":       lambda alias:
                                          self.bridge.switch_flowsheet(alias),
            "list_simulation_objects":self.bridge.list_simulation_objects,
            "get_stream_properties":  lambda tag:
                                          self.bridge.get_stream_properties(tag),
            "set_stream_property":    self._verified_set_stream_property,
            "set_stream_composition": self._set_stream_composition_wrapper,
            "get_object_properties":  lambda tag:
                                          self.bridge.get_object_properties(tag),
            "set_unit_op_property":   self._verified_set_unit_op_property,
            "run_simulation":         self.bridge.run_simulation,
            "get_simulation_results": self.bridge.get_simulation_results,
            "get_property_package":   self.bridge.get_property_package,
            "check_convergence":      self.bridge.check_convergence,
            "validate_feed_specs":    self.bridge.validate_feed_specs,
            "parametric_study":       self._parametric_study_with_progress,
            "optimize_multivar":      lambda **kw:
                                          self.bridge.optimize_multivar(**kw),
            "bayesian_optimize":      self._bayesian_optimize_with_progress,
            "dwsim_optimize":         self._dwsim_optimize_with_progress,
            "optimize_flowsheet_with_llm": self._optimize_workflow_with_progress,
            "dwsim_internal_optimize":    self._dwsim_internal_optimize_with_progress,
            "robust_solve":           lambda **kw: self.bridge.robust_solve(**kw),
            "initialize_distillation": lambda **kw: self.bridge.initialize_distillation(**kw),
            "optimize_constrained":   lambda **kw: self.bridge.optimize_constrained(**kw),
            "optimize_multiobjective": lambda **kw: self.bridge.optimize_multiobjective(**kw),
            "global_sensitivity":     lambda **kw: self.bridge.global_sensitivity(**kw),
            "optimize_eo":            lambda **kw: self.bridge.optimize_eo(**kw),
            "parametric_study_2d":    lambda **kw: self.bridge.parametric_study_2d(**kw),
            "pinch_analysis":         lambda min_approach_temp_C=10.0:
                                          self.bridge.pinch_analysis(min_approach_temp_C),
            "initialize_recycle":     lambda recycle_tag, T_guess_C, P_guess_bar,
                                             composition, solver="Wegstein":
                                          self.bridge.initialize_recycle(
                                              recycle_tag, T_guess_C, P_guess_bar,
                                              composition, solver),
            "get_compound_properties":lambda name:
                                          self.bridge.get_compound_properties(name),
                    "monte_carlo_study":      self._monte_carlo_with_progress,
            "optimize_parameter":     lambda **kwargs:
                                          self.bridge.optimize_parameter(**kwargs),
            # v3 tools
            "get_column_properties":  lambda tag:
                                          self.bridge.get_column_properties(tag),
            "set_column_property":    self._verified_set_column_property,
            "get_reactor_properties": lambda tag:
                                          self.bridge.get_reactor_properties(tag),
            "set_reactor_property":   self._verified_set_reactor_property,
            "detect_simulation_mode": self.bridge.detect_simulation_mode,
            "get_plugin_info":        lambda tag:
                                          self.bridge.get_plugin_info(tag),
            # v4 tools — autonomous flowsheet generation + reporting
            "get_available_compounds":
                                      lambda search="":
                                          self.bridge.get_available_compounds(search),
            "get_available_property_packages":
                                      self.bridge.get_available_property_packages,
            # create_flowsheet DISABLED — AI must use step-by-step primitives
            "create_flowsheet":       lambda topology:
                                          {"success": False,
                                           "error": (
                                               "create_flowsheet is disabled. Use the "
                                               "step-by-step approach: new_flowsheet -> "
                                               "add_object -> connect_streams -> "
                                               "set_stream_property -> set_unit_op_property "
                                               "-> save_and_solve."
                                           )},
            # AI step-by-step building primitives
            "new_flowsheet":          lambda name, compounds, property_package,
                                             save_path=None:
                                          self.bridge.new_flowsheet(
                                              name, compounds, property_package, save_path),
            "add_object":             self._verified_add_object,
            "save_and_solve":         lambda: self.bridge.save_and_solve(),
            "build_flowsheet_atomic": lambda **spec:
                                          self.bridge.build_flowsheet_atomic(spec),
            "multi_model_uncertainty": lambda spec, property_packages=None,
                                              observe_props=None:
                                          self.bridge.multi_model_uncertainty(
                                              spec, property_packages, observe_props),
            "thermo_method_assistant": lambda action="catalogue", model=None, **kw:
                                          __import__("thermo_models").assistant(
                                              action, model, **kw),
            "list_flowsheet_templates": self._list_flowsheet_templates,
            "create_from_template":   self._create_from_template,
            "generate_report":        lambda **kwargs:
                                          self.bridge.generate_report(kwargs),
            # v5 tools — phase results, energy streams, delete/disconnect, reactions, column
            "get_phase_results":      lambda stream_tag, phase="vapor":
                                          self.bridge.get_phase_results(stream_tag, phase),
            # v6 tools — transport props, phase envelope, flash spec
            "get_transport_properties": lambda stream_tag, phase="overall":
                                          self.bridge.get_transport_properties(stream_tag, phase),
            "calculate_phase_envelope": lambda stream_tag, envelope_type="PT",
                                               max_points=50, quality=0.0,
                                               fixed_P_Pa=101325.0,
                                               fixed_T_K=298.15,
                                               step_count=40:
                                          self.bridge.calculate_phase_envelope(
                                              stream_tag, envelope_type,
                                              int(max_points), float(quality),
                                              float(fixed_P_Pa),
                                              float(fixed_T_K),
                                              int(step_count)),
            "get_binary_interaction_parameters": lambda compound_1="",
                                                        compound_2="":
                                          self.bridge.get_binary_interaction_parameters(
                                              compound_1, compound_2),
            "set_binary_interaction_parameters":
                                      self._verified_set_binary_interaction_parameters,
            "configure_heat_exchanger": self._verified_configure_heat_exchanger,
            "set_stream_flash_spec":  self._verified_set_stream_flash_spec,
            "get_energy_stream":      lambda stream_tag:
                                          self.bridge.get_energy_stream(stream_tag),
            "set_energy_stream":      self._verified_set_energy_stream,
            "delete_object":          self._verified_delete_object,
            "disconnect_streams":     self._verified_disconnect_streams,
            "connect_streams":        lambda from_tag, to_tag, from_port=0, to_port=0:
                                          self.bridge.connect_streams(
                                              from_tag, to_tag,
                                              int(from_port), int(to_port)),
            "validate_topology":      lambda: self.bridge.validate_topology(),
            "setup_reaction":         lambda reactor_tag, reactions:
                                          self._setup_reaction_validated(reactor_tag, reactions),
            "set_column_specs":       self._verified_set_column_specs,
            # RAG knowledge base
            "search_knowledge":       lambda query="", top_k=4:
                                          (_kb_instance.search(query, top_k=min(int(top_k), 8))
                                           if _kb_instance
                                           else {"success": False,
                                                 "error": "Knowledge base not available"}),
            # Thermodynamic property database (DIPPR/DECHEMA — exact numerical data)
            "lookup_compound_properties":
                                      lambda compound, properties=None:
                                          _prop_db_lookup(compound, properties),
            "lookup_binary_parameters":
                                      lambda comp1, comp2, model="nrtl":
                                          _prop_db_pair(comp1, comp2, model),
            "compute_vapor_pressure":
                                      lambda compound, T_C:
                                          _prop_db_psat(compound, float(T_C)),
            "search_compound_database":
                                      lambda query:
                                          _prop_db_search(query),
            "batch_lookup_properties":
                                      lambda compounds, properties=None:
                                          _prop_db_batch(compounds, properties),
            # Persistent memory
            "remember_goal":          self._remember_goal,
            "remember_constraint":    self._remember_constraint,
            "recall_memory":          self._recall_memory,
            # Literature comparison
            "compare_to_literature":  self._compare_to_literature,
            # Intent declaration (blueprint Phase 3 verifier pattern)
            "declare_intent":         self._declare_intent,
            # ─── Industrial-grade tools (Phase 3) ─────────────────────────────
            "preflight_validate":          self._tool_preflight,
            "detect_tear_streams":         self._tool_tear_streams,
            "synthesize_hen":              self._tool_hen,
            "diagnose_convergence":        self._tool_diagnose,
            # ── Escape-hatch reflection tools ──────────────────────────────
            "reflect_get_set":             self._tool_reflect_get_set,
            "exec_python":                 self._tool_exec_python,
            "inspect_object":              self._tool_inspect_object,
            "iterative_spec_loop":         self._tool_iterative_spec_loop,
            # ── Process design advisor ──────────────────────────────────────
            "process_synthesis":           self._tool_process_synthesis,
            "equipment_sizing":            self._tool_equipment_sizing,
            "separation_sequence":         self._tool_separation_sequence,
            "property_package_selector":   self._tool_pp_selector,
            "heat_integration_targets":    self._tool_heat_integration,
            "design_checklist":            self._tool_design_checklist,
            # ── DWSIM troubleshooter ─────────────────────────────────────────
            "troubleshoot_dwsim":          self._tool_troubleshoot_dwsim,
            "convergence_guide":           self._tool_convergence_guide,
            "numerical_settings_advisor":  self._tool_numerical_settings,
            "decode_error":                self._tool_decode_error,
            # ── Templates ───────────────────────────────────────────────────
            "list_process_templates":      self._tool_list_templates,
            "get_process_template":        self._tool_get_template,
            "instantiate_process_template": self._tool_instantiate_template,
            "execute_build_plan":          self._tool_execute_build_plan,
            "generate_pfd":                self._tool_pfd,
            "list_reactions":              self._tool_list_reactions,
            "get_reaction_kinetics":       self._tool_get_reaction,
            "suggest_kinetics_for_reaction": self._tool_suggest_kinetics,
            # ─── CAPE-OPEN integration (Phase 6) ──────────────────────────────
            "discover_cape_open_components": self._tool_co_discover,
            "add_cape_open_unit":            self._tool_co_add,
            "list_cape_open_parameters":     self._tool_co_list_params,
            "list_cape_open_ports":          self._tool_co_list_ports,
            "set_cape_open_parameter":       self._tool_co_set_param,
        }
        # Holds the latest intent declared this session. Cleared by
        # new_flowsheet / load_flowsheet. Read by post-solve hook.
        self._active_intent: Optional[Any] = None

    # ─── Industrial-grade tool wrappers ───────────────────────────────────
    def _tool_preflight(self) -> dict:
        try:
            from industrial_features import preflight_validate
            objs = _bridge_objects(self.bridge)
            conns = self.bridge.list_connections() if hasattr(self.bridge, "list_connections") else []
            all_objs = (
                [{"tag": s.get("tag"), "category": "MaterialStream"} for s in objs.get("streams", [])] +
                [{"tag": u.get("tag"), "type": u.get("type"), "category": "UnitOperation"} for u in objs.get("unit_ops", [])]
            )
            compounds = []
            if hasattr(self.bridge, "list_compounds"):
                try: compounds = list((self.bridge.list_compounds() or {}).get("compounds") or [])
                except Exception: pass
            pp = ""
            if hasattr(self.bridge, "get_property_package"):
                try: pp = (self.bridge.get_property_package() or {}).get("property_package", "")
                except Exception: pass
            return preflight_validate(all_objs, conns, compounds, pp)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_tear_streams(self) -> dict:
        try:
            from industrial_features import detect_tear_streams
            objs = _bridge_objects(self.bridge)
            conns = self.bridge.list_connections() if hasattr(self.bridge, "list_connections") else []
            nodes = [s.get("tag") for s in objs.get("streams", []) if s.get("tag")] + \
                    [u.get("tag") for u in objs.get("unit_ops", []) if u.get("tag")]
            edges = [(c.get("from"), c.get("to")) for c in conns if c.get("from") and c.get("to")]
            return detect_tear_streams(nodes, edges)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_hen(self, delta_t_min_C: float = 10.0) -> dict:
        try:
            from industrial_features import synthesize_hen
            # Need pinch result first
            if hasattr(self.bridge, "pinch_analysis"):
                pinch = self.bridge.pinch_analysis(delta_t_min_C)
            else:
                return {"success": False, "error": "pinch_analysis not available on bridge"}
            if not pinch.get("success"):
                return {"success": False, "error": "pinch analysis prerequisite failed", "pinch_error": pinch.get("error")}
            streams = pinch.get("streams", [])
            hot  = [{"tag": s["tag"], "t_supply_C": s["t_supply"], "t_target_C": s["t_target"], "heat_kw": s["heat_kw"]}
                    for s in streams if s.get("type") == "hot"]
            cold = [{"tag": s["tag"], "t_supply_C": s["t_supply"], "t_target_C": s["t_target"], "heat_kw": s["heat_kw"]}
                    for s in streams if s.get("type") == "cold"]
            return synthesize_hen(hot, cold, pinch.get("pinch_temp_C"), delta_t_min_C)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_diagnose(self) -> dict:
        try:
            from industrial_features import diagnose_convergence
            conv = self.bridge.check_convergence() if hasattr(self.bridge, "check_convergence") else {}
            states: Dict[str, Dict] = {}
            for tag_entry in (conv.get("not_converged", []) or []):
                tag = tag_entry.get("tag") if isinstance(tag_entry, dict) else tag_entry
                if tag and hasattr(self.bridge, "get_stream_properties"):
                    try:
                        r = self.bridge.get_stream_properties(tag)
                        if r.get("success"):
                            states[tag] = r.get("properties", {})
                    except Exception:
                        pass
            return diagnose_convergence(conv, states)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ── Escape-hatch reflection tools ─────────────────────────────────────

    def _tool_reflect_get_set(self, object_name: str, property_path: str,
                               value: str = None) -> dict:
        """GET or SET any DWSIM property via .NET reflection."""
        return self.bridge.reflect_get_set(object_name, property_path, value)

    def _tool_exec_python(self, code: str, timeout_s: float = 30.0) -> dict:
        """Execute sandboxed Python against the live DWSIM flowsheet."""
        return self.bridge.exec_python(code, timeout_s)

    def _tool_inspect_object(self, object_name: str, filter_prefix: str = "",
                               filter_type: str = "", max_props: int = 60) -> dict:
        """Discover all properties on any DWSIM .NET object."""
        return self.bridge.inspect_object(object_name, filter_prefix, filter_type, max_props)

    def _tool_iterative_spec_loop(self, vary_object: str, vary_path: str,
                                    vary_lo: float, vary_hi: float,
                                    observe_object: str, observe_path: str,
                                    target: float, tolerance: float = 0.001,
                                    direction: str = "increase",
                                    max_iter: int = 25) -> dict:
        """Bisection loop: vary a decision variable until an observable meets a spec."""
        return self.bridge.iterative_spec_loop({
            "vary_object": vary_object, "vary_path": vary_path,
            "vary_lo": vary_lo, "vary_hi": vary_hi,
            "observe_object": observe_object, "observe_path": observe_path,
            "target": target, "tolerance": tolerance,
            "direction": direction, "max_iter": max_iter,
        })

    # ── Process design advisor tools ──────────────────────────────────────

    def _tool_process_synthesis(self, goal: str, reactants: list = None,
                                  products: list = None, phase: str = "liquid",
                                  scale_tonne_h: float = 10.0) -> dict:
        """Suggest a complete process flowsheet structure (Douglas methodology)."""
        try:
            from process_design_advisor import process_synthesis
            return process_synthesis(goal, reactants, products, phase, scale_tonne_h)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_equipment_sizing(self, equipment_type: str, duty_kW: float = 0,
                                 flow_m3h: float = 0, delta_P_bar: float = 0,
                                 T_in_C: float = 25, T_out_C: float = 100,
                                 LMTD_C: float = 20, service: str = "liquid-liquid",
                                 n_theoretical: int = 20, alpha: float = 2.0) -> dict:
        """Preliminary equipment sizing using Perry's / Turton correlations."""
        try:
            from process_design_advisor import equipment_sizing
            return equipment_sizing(equipment_type, duty_kW, flow_m3h, delta_P_bar,
                                     T_in_C, T_out_C, LMTD_C, service, n_theoretical, alpha)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_separation_sequence(self, compounds: list, property_package: str = "Peng-Robinson",
                                    feed_phase: str = "mixed", purity_target: float = 0.99) -> dict:
        """Suggest separation train sequence (Smith 2005 heuristics)."""
        try:
            from process_design_advisor import separation_sequence
            return separation_sequence(compounds, property_package, feed_phase, purity_target)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_pp_selector(self, compounds: list, pressure_bar: float = 1.01325,
                            temperature_C: float = 25.0, application: str = "") -> dict:
        """Select the correct thermodynamic property package (Carlson 1996 decision tree)."""
        try:
            from process_design_advisor import property_package_selector
            return property_package_selector(compounds, pressure_bar, temperature_C, application)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_heat_integration(self, hot_streams: list, cold_streams: list,
                                 delta_T_min_C: float = 10.0) -> dict:
        """Compute pinch analysis targets for heat integration."""
        try:
            from process_design_advisor import heat_integration_targets
            return heat_integration_targets(hot_streams, cold_streams, delta_T_min_C)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_design_checklist(self, process_type: str) -> dict:
        """Return a HAZOP-lite design checklist for a process type."""
        try:
            from process_design_advisor import design_checklist
            return design_checklist(process_type)
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── DWSIM troubleshooter tools ─────────────────────────────────────────

    def _tool_troubleshoot_dwsim(self, issue: str, process_type: str = "",
                                   symptoms: list = None) -> dict:
        """Diagnose DWSIM convergence/configuration issues and return ranked fixes."""
        try:
            from dwsim_troubleshooter import troubleshoot_process, diagnose
            st = self.bridge.state if hasattr(self.bridge, "state") else None
            fs_state = None
            if st:
                fs_state = {
                    "unit_ops": [{"type": t} for t in getattr(st, "unit_ops", []) or []],
                    "streams":  getattr(st, "streams", []) or [],
                }
            syms = (symptoms or []) + [issue]
            return troubleshoot_process(process_type or issue, issue, fs_state)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_convergence_guide(self, unit_type: str) -> dict:
        """Get unit-specific convergence settings and algorithm recommendations."""
        try:
            from dwsim_troubleshooter import convergence_guide
            return convergence_guide(unit_type)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_numerical_settings(self, problem_description: str) -> dict:
        """Recommend DWSIM numerical solver settings for a convergence problem."""
        try:
            from dwsim_troubleshooter import numerical_settings_advisor
            return numerical_settings_advisor(problem_description)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_decode_error(self, error_message: str) -> dict:
        """Decode a DWSIM error message to a human-readable explanation with fix steps."""
        try:
            from dwsim_troubleshooter import error_decoder
            return error_decoder(error_message)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_list_templates(self, category: str = "", complexity: str = "") -> dict:
        try:
            from process_templates import list_templates
            return list_templates(category, complexity)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_get_template(self, template_id: str) -> dict:
        try:
            from process_templates import get_template
            return get_template(template_id)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_instantiate_template(self, template_id: str,
                                   overrides: dict = None,
                                   solve: bool = False) -> dict:
        """Deterministically build a complete flowsheet from a curated template.
        Use this instead of issuing 20+ add_object/connect_streams calls."""
        try:
            from process_templates import instantiate_template
            return instantiate_template(
                template_id=template_id,
                bridge=self.bridge,
                overrides=overrides or {},
                solve=bool(solve),
            )
        except Exception as exc:
            return {"success": False,
                    "error_code": "TEMPLATE_INSTANTIATE_FAILED",
                    "error": str(exc)}

    def _tool_execute_build_plan(self, plan: dict, solve: bool = False) -> dict:
        """Execute a complete flowsheet build plan (compounds, PP, streams,
        unit ops, connections) in one deterministic pass. PREFERRED tool for
        building ad-hoc industrial flowsheets — emit the full plan once
        instead of 20+ sequential tool calls."""
        try:
            from flowsheet_executor import execute_build_plan
            return execute_build_plan(plan or {}, self.bridge, solve=bool(solve))
        except Exception as exc:
            return {"success": False,
                    "error_code": "PLAN_EXECUTE_FAILED",
                    "error": str(exc)}

    def _tool_pfd(self) -> dict:
        try:
            from pfd_generator import generate_pfd_svg
            objs = _bridge_objects(self.bridge)
            conns = self.bridge.list_connections() if hasattr(self.bridge, "list_connections") else []
            all_objs = (
                [{"tag": s.get("tag"), "category": "MaterialStream"} for s in objs.get("streams", [])] +
                [{"tag": u.get("tag"), "type": u.get("type"), "category": "UnitOperation"} for u in objs.get("unit_ops", [])]
            )
            result = generate_pfd_svg(all_objs, conns)
            # Strip the giant SVG string from the agent's history — return only metadata
            if result.get("success"):
                svg_len = len(result.pop("svg", ""))
                result["svg_size_chars"] = svg_len
                result["note"] = "PFD generated. View at /flowsheet/pfd endpoint."
            return result
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_list_reactions(self, catalyst: str = "", reactant: str = "") -> dict:
        try:
            from kinetics_db import list_reactions
            return list_reactions(catalyst, reactant)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_get_reaction(self, reaction_id: str) -> dict:
        try:
            from kinetics_db import get_reaction
            return get_reaction(reaction_id)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_suggest_kinetics(self, reactants: List[str], T_K: float = 0, P_bar: float = 0) -> dict:
        try:
            from kinetics_db import suggest_kinetics
            return suggest_kinetics(reactants, T_K, P_bar)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    # ─── CAPE-OPEN tool wrappers (Phase 6) ─────────────────────────────────
    def _tool_co_discover(self, category: str = "") -> dict:
        try:
            from cape_open_integration import discover_cape_open_components
            return discover_cape_open_components(category)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_co_add(self, tag: str, clsid_or_progid: str) -> dict:
        try:
            from cape_open_integration import add_cape_open_unit_to_flowsheet
            return add_cape_open_unit_to_flowsheet(self.bridge, tag, clsid_or_progid)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_co_list_params(self, tag: str) -> dict:
        try:
            from cape_open_integration import list_cape_open_parameters
            return list_cape_open_parameters(self.bridge, tag)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_co_list_ports(self, tag: str) -> dict:
        try:
            from cape_open_integration import list_cape_open_ports
            return list_cape_open_ports(self.bridge, tag)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _tool_co_set_param(self, tag: str, parameter_name: str, value: Any) -> dict:
        try:
            from cape_open_integration import set_cape_open_parameter
            return set_cape_open_parameter(self.bridge, tag, parameter_name, value)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    _MAX_GOALS_PER_SESSION = int(os.getenv("MAX_GOALS_PER_SESSION", "50"))
    _MAX_CONSTRAINTS_PER_SESSION = int(os.getenv("MAX_CONSTRAINTS_PER_SESSION", "100"))
    _MAX_MEMORY_TEXT_LEN = int(os.getenv("MAX_MEMORY_TEXT_LEN", "2000"))

    def _remember_goal(self, text: str) -> dict:
        try:
            if not text or not text.strip():
                return {"success": False, "error": "Goal text is empty"}
            if len(text) > self._MAX_MEMORY_TEXT_LEN:
                return {"success": False,
                        "error": f"Goal text exceeds {self._MAX_MEMORY_TEXT_LEN} chars (got {len(text)})"}
            import session_memory
            existing = session_memory.get_goals().get("goals", [])
            if len(existing) >= self._MAX_GOALS_PER_SESSION:
                return {"success": False,
                        "error": f"Session goal limit ({self._MAX_GOALS_PER_SESSION}) reached. Clear old goals first."}
            return session_memory.set_goal(text)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _remember_constraint(self, text: str) -> dict:
        try:
            if not text or not text.strip():
                return {"success": False, "error": "Constraint text is empty"}
            if len(text) > self._MAX_MEMORY_TEXT_LEN:
                return {"success": False,
                        "error": f"Constraint text exceeds {self._MAX_MEMORY_TEXT_LEN} chars (got {len(text)})"}
            import session_memory
            existing = session_memory.get_goals().get("constraints", [])
            if len(existing) >= self._MAX_CONSTRAINTS_PER_SESSION:
                return {"success": False,
                        "error": f"Session constraint limit ({self._MAX_CONSTRAINTS_PER_SESSION}) reached. Clear old constraints first."}
            return session_memory.set_constraint(text)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _recall_memory(self, query: str = "", limit: int = 5) -> dict:
        try:
            import session_memory
            if query:
                entries = session_memory.search(query, limit)
            else:
                entries = session_memory.recent(limit)
            goals = session_memory.get_goals()
            return {"success": True, "entries": entries,
                    "goals": goals.get("goals", []),
                    "constraints": goals.get("constraints", [])}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _declare_intent(self, **payload) -> dict:
        """Store the user's stated goal so post-solve verification can score it.
        Blueprint §"Intent — declared by the LLM before run_flowsheet"."""
        try:
            from intent import parse_intent
        except ImportError:
            return {"success": False,
                    "error": "intent module not available."}
        try:
            self._active_intent = parse_intent(payload)
        except Exception as exc:
            return {"success": False, "error": f"failed to parse intent: {exc}"}

        n_targets = len(self._active_intent.targets)
        return {
            "success": True,
            "intent_stored": True,
            "feed_streams":    self._active_intent.feed_streams,
            "product_streams": self._active_intent.product_streams,
            "n_targets":       n_targets,
            "note": (f"Intent registered with {n_targets} target(s). "
                     "After save_and_solve, the verifier will report any target "
                     "that wasn't met — read intent_verification in the solve result."),
        }

    def _verify_active_intent(self) -> Optional[Dict[str, Any]]:
        """Run intent verification against the current bridge state.
        Returns a summary dict (or None if no intent is active)."""
        if self._active_intent is None:
            return None
        try:
            from intent import verify_intent
        except ImportError:
            return None

        # Snapshot stream + unit properties from the bridge
        stream_results: Dict[str, Dict[str, Any]] = {}
        unit_results:   Dict[str, Dict[str, Any]] = {}
        try:
            for tag in list(self.bridge.state.streams):
                r = self.bridge.get_stream_properties(tag)
                if r.get("success"):
                    stream_results[tag] = r.get("properties", {})
        except Exception:
            pass
        try:
            for tag in list(self.bridge.state.unit_ops):
                r = self.bridge.get_object_properties(tag)
                if r.get("success"):
                    unit_results[tag] = r.get("properties", {})
        except Exception:
            pass

        findings = verify_intent(self._active_intent, stream_results, unit_results)
        passed = not any(f.severity == "error" for f in findings)
        return {
            "passed":   passed,
            "findings": [f.to_dict() for f in findings],
            "n_targets":  len(self._active_intent.targets),
            "n_failed":   sum(1 for f in findings if f.severity == "error"),
            "n_warnings": sum(1 for f in findings if f.severity == "warning"),
            "summary": (
                "All declared intent targets met." if passed and findings == []
                else f"{len(findings)} finding(s); {'PASS' if passed else 'FAIL'}."
            ),
        }

    def _compare_to_literature(
        self,
        process: str,
        tolerance_pct: float = 5.0,
        include_kpis: bool = True,
    ) -> dict:
        """Compare current simulation results against published literature values."""
        try:
            from process_library import compare_to_literature as _ctl
        except ImportError:
            return {
                "success": False,
                "error": "process_library module not available. "
                         "Ensure process_library.py is in the backend directory.",
            }
        # Build stream_results by querying each stream individually — more
        # reliable than get_simulation_results which may not include every
        # composition key the literature reference expects.
        stream_results: dict = {}
        try:
            for tag in list(self.bridge.state.streams):
                r = self.bridge.get_stream_properties(tag)
                if r.get("success"):
                    stream_results[tag] = r.get("properties", {})
        except Exception:
            pass
        sim_results = {"stream_results": stream_results}

        return _ctl(
            process=process,
            sim_results=sim_results,
            tolerance_pct=float(tolerance_pct),
            include_kpis=bool(include_kpis),
        )

    def _bayesian_optimize_with_progress(self, **kwargs) -> dict:
        """
        Wraps bridge.bayesian_optimize with per-evaluation SSE token streaming.
        Each DWSIM call streams a progress line so the UI shows live BO progress
        instead of an unresponsive wait for up to 25 evaluations.
        """
        n_total   = kwargs.get("n_initial", 5) + kwargs.get("max_iter", 20)
        minimize  = kwargs.get("minimize", True)
        obs_prop  = kwargs.get("observe_property", "objective")
        action    = "minimise" if minimize else "maximise"

        def _progress(it: int, params: dict, val, best) -> None:
            if self.on_token:
                phase = "LHS" if it <= kwargs.get("n_initial", 5) else "BO "
                val_s = f"{val:.4g}" if val is not None else "failed"
                try:
                    self.on_token(
                        f"[BO {phase}] eval {it:2d}/{n_total} | "
                        f"{obs_prop}={val_s} | best={best:.4g} ({action})\n"
                    )
                except Exception:
                    pass

        return self.bridge.bayesian_optimize(on_progress=_progress, **kwargs)

    def _dwsim_internal_optimize_with_progress(self, **kwargs) -> dict:
        """TRUE DWSIM-internal optimization via OptimizationCase objects —
        the same engine as the DWSIM GUI Optimizer button.  Per-evaluation
        progress streamed live via SSE."""
        n_total = int(kwargs.get("max_iter", 100))
        minimize = kwargs.get("minimize", True)
        action   = "minimise" if minimize else "maximise"
        obs_prop = (kwargs.get("objective") or {}).get("property", "objective")

        def _progress(it, params, val, best):
            if self.on_token:
                phase = "eval"
                val_s = f"{val:.4g}" if val is not None else "failed"
                try:
                    self.on_token(
                        f"[DWSIM-Internal {phase}] "
                        f"eval {it:3d}/{n_total} | "
                        f"{obs_prop}={val_s} | best={best:.4g if best is not None else '—'} ({action})\n"
                    )
                except Exception:
                    pass

        kwargs["on_progress"] = _progress
        try:
            return self.bridge.optimize_with_internal_engine(**kwargs)
        except Exception as exc:
            return {"success": False, "error_code": "INTERNAL_OPT_FAILED",
                    "error": str(exc)}

    def _optimize_workflow_with_progress(self, **kwargs) -> dict:
        """End-to-end NL optimization workflow with per-step + per-eval
        SSE streaming. Each solver evaluation streams a one-line iteration
        update to the chat so the user sees the optimization unfolding."""

        def _on_step(stage: str, detail: str):
            if self.on_token:
                try:
                    self.on_token(f"{stage}  {detail}\n")
                except Exception:
                    pass

        # Per-evaluation streaming. Throttled so a 200-eval run doesn't
        # flood the chat — every 1st, 2nd, 3rd, 5th, then every 5th eval.
        last_emit = [0]
        def _on_eval(it, params, obj, best):
            if not self.on_token:
                return
            should_emit = (
                it <= 5                                # first 5 always
                or it % 5 == 0                         # then every 5th
                or it - last_emit[0] >= 5              # safety net
            )
            if not should_emit:
                return
            last_emit[0] = it
            obj_s  = f"{obj:.4g}"  if obj  is not None else "failed"
            best_s = f"{best:.4g}" if best is not None else "—"
            try:
                self.on_token(f"   eval {it:3d}: obj={obj_s}  best={best_s}\n")
            except Exception:
                pass

        kwargs["llm"]     = self.llm
        kwargs["on_step"] = _on_step
        kwargs["on_eval"] = _on_eval
        return self.bridge.optimize_flowsheet_with_llm(**kwargs)

    def _dwsim_optimize_with_progress(self, **kwargs) -> dict:
        """DWSIM-native optimization with per-evaluation SSE progress."""
        method   = kwargs.get("method", "simplex")
        minimize = kwargs.get("minimize", True)
        action   = "minimise" if minimize else "maximise"

        def _progress(it, params, obj, best):
            if self.on_token:
                obj_s  = f"{obj:.4g}" if obj is not None else "failed"
                best_s = f"{best:.4g}" if best is not None else "—"
                try:
                    self.on_token(
                        f"[DWSIM-Opt {method}] eval {it:3d} | "
                        f"obj={obj_s} | best={best_s} ({action})\n"
                    )
                except Exception:
                    pass

        return self.bridge.dwsim_optimize(on_progress=_progress, **kwargs)

    def _monte_carlo_with_progress(self, **kwargs) -> dict:
        """Monte Carlo with live token progress (same pattern as parametric study)."""
        n_total = kwargs.get("n_samples", 100)

        def _progress(i: int, n: int, inputs, val) -> None:
            if self.on_token and val is not None:
                try:
                    self.on_token(
                        f"[monte-carlo] Sample {i}/{n}: "
                        f"{kwargs.get('observe_property','output')}={val:.4g}\n"
                    )
                except Exception:
                    pass

        return self.bridge.monte_carlo_study(on_progress=_progress, **kwargs)

    def _auto_diagnose_convergence(self, failed_result: dict) -> dict:
        """
        When save_and_solve fails, automatically run:
          1. check_convergence       — identify which streams/unit-ops failed
          2. validate_feed_specs     — check for missing T/P/flow specs
          3. industrial_features.diagnose_convergence  — root-cause analysis with ranked fixes
        Inject findings into the result dict so the LLM sees them immediately.
        """
        diagnostics: Dict[str, Any] = {}
        # New Phase 3: ranked root-cause diagnosis via industrial_features
        try:
            from industrial_features import diagnose_convergence as _diag_rca
            conv_state = self.bridge.check_convergence() if hasattr(self.bridge, "check_convergence") else {}
            obj_states: Dict[str, Dict] = {}
            for nc in (conv_state.get("not_converged", []) or []):
                tag = nc.get("tag") if isinstance(nc, dict) else nc
                if tag and hasattr(self.bridge, "get_stream_properties"):
                    try:
                        r = self.bridge.get_stream_properties(tag)
                        if r.get("success"):
                            obj_states[tag] = r.get("properties", {})
                    except Exception:
                        pass
            rca = _diag_rca(conv_state, obj_states)
            if rca.get("success") and rca.get("diagnoses"):
                diagnostics["root_cause_analysis"] = {
                    "top_cause":   rca["diagnoses"][0],
                    "all_causes":  rca["diagnoses"][:5],
                    "next_step":   rca.get("next_step"),
                }
        except Exception as exc:
            diagnostics["root_cause_analysis"] = {"error": str(exc)}

        try:
            conv = self.bridge.check_convergence()
            if conv.get("success"):
                diagnostics["convergence_check"] = {
                    "converged":        conv.get("converged", False),
                    "failed_streams":   conv.get("failed_streams", []),
                    "failed_unit_ops":  conv.get("failed_unit_ops", []),
                    "warnings":         conv.get("warnings", []),
                }
        except Exception as exc:
            diagnostics["convergence_check"] = {"error": str(exc)}

        try:
            feed_val = self.bridge.validate_feed_specs()
            if feed_val.get("success"):
                issues = feed_val.get("issues", [])
                if issues:
                    diagnostics["feed_spec_issues"] = issues
        except Exception as exc:
            diagnostics["feed_spec_issues"] = {"error": str(exc)}

        # Compose a human-readable diagnostic hint for the LLM
        hint_lines = ["Auto-diagnosis after convergence failure:"]
        cc = diagnostics.get("convergence_check", {})
        if cc.get("failed_streams"):
            hint_lines.append(f"  Failed streams: {cc['failed_streams']}")
        if cc.get("failed_unit_ops"):
            hint_lines.append(f"  Failed unit-ops: {cc['failed_unit_ops']}")
        if cc.get("warnings"):
            for w in cc["warnings"][:3]:
                hint_lines.append(f"  Warning: {w}")
        feed_issues = diagnostics.get("feed_spec_issues", [])
        if isinstance(feed_issues, list) and feed_issues:
            for iss in feed_issues[:3]:
                hint_lines.append(f"  Feed issue: {iss}")
        hint_lines.append(
            "Suggested fixes: check missing T/P/flow specs on feed streams, "
            "verify property package suits all components, "
            "check for disconnected streams, and ensure stream flash specs are set."
        )

        failed_result["_auto_diagnosis"] = diagnostics
        failed_result["_diagnosis_hint"] = "\n".join(hint_lines)
        return failed_result

    def _setup_reaction_validated(self, reactor_tag: str, reactions: list) -> dict:
        """
        Wrapper around bridge.setup_reaction with Arrhenius parameter validation
        and KineticReactor guidance (Chip Huyen review: kinetics still conceptual).

        Validates:
        - Activation energy Ea in realistic range (0 < Ea < 400 kJ/mol)
        - Pre-exponential factor k0 is positive
        - Reaction stoichiometry sums to near-zero for balanced reactions
        - Temperature range for kinetics is within property package validity

        Adds defaults for missing optional parameters.
        """
        import math
        validated = []
        warnings  = []

        for rxn in (reactions or []):
            if not isinstance(rxn, dict):
                continue
            rxn_copy = dict(rxn)

            # Validate Arrhenius parameters if kinetic type
            rxn_type = rxn_copy.get("type", "conversion").lower()
            if rxn_type in ("kinetic", "arrhenius", "powerlaw"):
                Ea = rxn_copy.get("activation_energy_kJmol") or rxn_copy.get("Ea_kJmol")
                k0 = rxn_copy.get("pre_exponential") or rxn_copy.get("k0")
                T_ref = rxn_copy.get("reference_temperature_K", 298.15)

                if Ea is not None:
                    try:
                        Ea_f = float(Ea)
                        if not (0 < Ea_f < 400):
                            warnings.append(
                                f"Reaction '{rxn_copy.get('name','?')}': "
                                f"Ea={Ea_f} kJ/mol is outside typical range 0-400 kJ/mol. "
                                "Verify units (should be kJ/mol, not J/mol or cal/mol)."
                            )
                        # Store in J/mol for DWSIM (bridge expects J/mol)
                        rxn_copy["activation_energy_Jmol"] = Ea_f * 1000
                    except (TypeError, ValueError):
                        warnings.append(f"Invalid activation energy: {Ea}")

                if k0 is not None:
                    try:
                        k0_f = float(k0)
                        if k0_f <= 0:
                            warnings.append(f"Pre-exponential factor k0={k0_f} must be positive.")
                    except (TypeError, ValueError):
                        warnings.append(f"Invalid pre-exponential factor: {k0}")

            # Check stoichiometry balance (atoms not checked — just mole balance hint)
            stoich = rxn_copy.get("stoichiometry") or rxn_copy.get("components") or {}
            if isinstance(stoich, dict) and stoich:
                total_coeff = sum(float(v) for v in stoich.values() if v is not None)
                # Positive = products, negative = reactants. Sum should be small for balanced rxn.
                # Allow up to ±3 (e.g., A → 3B gives sum = +2)
                # Just note if sum is unexpectedly large
                if abs(total_coeff) > 10:
                    warnings.append(
                        f"Stoichiometry coefficients sum to {total_coeff:.1f} — "
                        "verify signs (reactants negative, products positive)."
                    )

            validated.append(rxn_copy)

        # Call bridge
        result = self.bridge.setup_reaction(reactor_tag, validated)

        # Attach validation warnings to result
        if warnings:
            result["_validation_warnings"] = warnings
            self._log(f"[setup_reaction] Validation warnings: {warnings}")

        return result

    # ── Dynamic tool selection (Review-3 AI Gap 1) ────────────────────────────
    # Tool sets keyed by phase. The LLM sees only the tools relevant to the
    # current state. Reduces 60+ tools to ~20, which research (Anthropic 2024)
    # shows materially improves selection accuracy.
    _TOOLS_DISCOVERY = {
        "find_flowsheets", "load_flowsheet", "list_loaded_flowsheets",
        "switch_flowsheet", "list_flowsheet_templates", "create_from_template",
        "new_flowsheet", "build_flowsheet_atomic", "search_knowledge",
        "lookup_compound_properties", "batch_lookup_properties",
        "search_compound_database", "lookup_binary_parameters",
        "compute_vapor_pressure",
        "remember_goal", "remember_constraint", "recall_memory",
        "get_available_compounds", "get_available_property_packages",
    }
    _TOOLS_BUILD = {
        "add_object", "connect_streams", "disconnect_streams", "delete_object",
        "set_stream_property", "set_stream_composition", "set_unit_op_property",
        "set_property_package", "set_stream_flash_spec",
        "set_column_property", "set_column_specs", "set_reactor_property",
        "set_energy_stream", "set_binary_interaction_parameters",
        "configure_heat_exchanger", "setup_reaction",
        "list_simulation_objects", "validate_topology", "validate_feed_specs",
        "search_knowledge", "lookup_compound_properties",
        "lookup_binary_parameters",
        # batch_lookup_properties excluded from build phase — compound research
        # should happen before new_flowsheet, not during add_object loops.
        "save_flowsheet",
    }
    _TOOLS_SOLVE = {
        "save_and_solve", "run_simulation", "check_convergence",
        "validate_feed_specs", "validate_topology", "initialize_recycle",
        "list_simulation_objects", "get_property_package",
        "search_knowledge",
        # Industrial solve tools — always needed in solve phase
        "robust_solve", "initialize_distillation",
    }
    _TOOLS_ANALYZE = {
        "get_simulation_results", "get_stream_properties", "get_phase_results",
        "get_object_properties", "get_unit_op_property",
        "get_column_properties", "get_reactor_properties",
        "get_energy_stream", "get_transport_properties",
        "calculate_phase_envelope", "get_binary_interaction_parameters",
        "parametric_study", "parametric_study_2d",
        "optimize_parameter",
        # NL-driven autonomous optimization workflow + DWSIM-native variant.
        # These two ROUTERS stay always-available so any optimisation request
        # can start; the heavy specialised optimisers (_TOOLS_OPTIMIZE_HEAVY)
        # are gated behind optimisation-intent keywords to keep the per-call
        # payload small (they are the largest tool schemas and were sent on
        # EVERY analyse/report turn, ~2.7k tokens of dead weight).
        "optimize_flowsheet_with_llm", "dwsim_optimize",
        # Escape-hatch reflection tools (always available — universal access)
        "reflect_get_set", "exec_python", "inspect_object",
        "iterative_spec_loop",
        # Process design advisor (always available)
        "process_synthesis", "equipment_sizing", "separation_sequence",
        "property_package_selector", "heat_integration_targets",
        "design_checklist",
        # DWSIM troubleshooter (always available)
        "troubleshoot_dwsim", "convergence_guide",
        "numerical_settings_advisor", "decode_error",
        "pinch_analysis", "generate_report",
        "search_knowledge", "compute_vapor_pressure",
        "list_simulation_objects",
        "robust_solve", "initialize_distillation",
        "compare_to_literature",
    }
    _TOOLS_ALWAYS = {
        # Always exposed regardless of phase — universally useful
        "search_knowledge", "list_simulation_objects",
        # Templates must always be available — user can call them at any point
        "create_from_template", "list_flowsheet_templates",
        "list_process_templates", "get_process_template",
        "instantiate_process_template",   # deterministic template build
        "execute_build_plan",             # deterministic ad-hoc plan executor
        # Batch property lookup should always be available
        "batch_lookup_properties",
        # Intent declaration — must be callable in any phase (planning tool)
        "declare_intent",
    }

    # Heavy specialised optimisers — the largest tool schemas. Gated OUT of the
    # base analyse set and added back only when the user's message shows
    # optimisation intent (see the 'optim'/'minim'/'maxim'/… keyword boosts).
    # Routers (optimize_flowsheet_with_llm, dwsim_optimize) stay always-available
    # so a plain "optimise this" never lacks an entry point.
    _TOOLS_OPTIMIZE_HEAVY = {
        "optimize_multivar", "optimize_constrained", "optimize_multiobjective",
        "global_sensitivity", "optimize_eo", "bayesian_optimize",
        "monte_carlo_study", "dwsim_internal_optimize",
    }

    # Per-intent keyword tool boosts. When user's message contains these
    # keywords, force-include the related tools (overrides phase pruning).
    _TOOLS_INTENT_KEYWORDS = {
        "template":     {"instantiate_process_template", "list_process_templates",
                         "get_process_template"},
        "synthesi":     {"instantiate_process_template", "list_process_templates",
                         "synthesize_hen", "list_reactions",
                         "suggest_kinetics_for_reaction"},
        "pinch":        {"pinch_analysis", "synthesize_hen"},
        "hen":          {"synthesize_hen"},
        "diagnos":      {"diagnose_convergence", "validate_feed_specs",
                         "validate_topology", "preflight_validate",
                         "check_convergence"},
        "tear":         {"detect_tear_streams"},
        "cape":         {"discover_cape_open_components", "add_cape_open_unit",
                         "list_cape_open_parameters", "list_cape_open_ports",
                         "set_cape_open_parameter"},
        "co-":          {"discover_cape_open_components"},
        "kinet":        {"list_reactions", "get_reaction_kinetics",
                         "suggest_kinetics_for_reaction"},
        "reaction":     {"list_reactions", "suggest_kinetics_for_reaction",
                         "setup_reaction"},
        "pfd":          {"generate_pfd"},
        "diagram":      {"generate_pfd"},
        "monte":        {"monte_carlo_study"},
        "bayes":        {"bayesian_optimize"},
        # Any explicit optimisation intent unlocks the FULL specialised toolkit.
        "optim":        _TOOLS_OPTIMIZE_HEAVY | {"optimize_flowsheet_with_llm",
                         "optimize_parameter", "dwsim_optimize"},
        "sensitiv":     {"global_sensitivity", "parametric_study",
                         "parametric_study_2d"},
        "pareto":       {"optimize_multiobjective"},
        "trade-off":    {"optimize_multiobjective"},
        "equation-orient": {"optimize_eo"},
        "maximi":       _TOOLS_OPTIMIZE_HEAVY | {"optimize_flowsheet_with_llm",
                         "dwsim_optimize", "optimize_parameter"},
        "maximise":     _TOOLS_OPTIMIZE_HEAVY | {"optimize_flowsheet_with_llm",
                         "dwsim_optimize", "optimize_parameter"},
        "minimi":       _TOOLS_OPTIMIZE_HEAVY | {"optimize_flowsheet_with_llm",
                         "dwsim_optimize", "optimize_parameter"},
        "minimise":     _TOOLS_OPTIMIZE_HEAVY | {"optimize_flowsheet_with_llm",
                         "dwsim_optimize", "optimize_parameter"},
        # Lighter phrasings that still imply an objective — surface the routers
        # (the routers then dispatch to the right specialised solver).
        "reduce":       {"optimize_flowsheet_with_llm", "dwsim_optimize",
                         "optimize_parameter"},
        "lower the":    {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "highest":      {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "lowest":       {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "improve":      {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "best operating": {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "yield":        {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "purity":       {"optimize_flowsheet_with_llm", "dwsim_optimize"},
        "report":       {"generate_report"},
        "literature":   {"compare_to_literature", "search_knowledge"},
    }

    def _should_analysis_fast_answer(self, user_message: str) -> bool:
        """Detect introspection-style questions about what CAN be optimised
        on the loaded flowsheet. Examples:
          • "what can be optimised in this flowsheet?"
          • "what variables can I tune?"
          • "analyse the flowsheet and find optimisation opportunities"
          • "list the variables you would optimise"
        Returns True when we should answer DETERMINISTICALLY using the
        suggester rather than going to the LLM. Critical for when LLM
        providers are all down."""
        if not user_message:
            return False
        msg = user_message.lower().strip()
        if len(msg) > 300:
            return False

        # Must mention optimisation in some form
        opt_words = ("optimis", "optimiz", "improv", "vary", "tune",
                      "adjust", "change", "modify", "decision variable",
                      "parameter")
        if not any(w in msg for w in opt_words):
            return False

        # Must look like a question / analysis request
        analysis_triggers = (
            "what ", "which ", "what's ", "can be ", "could be ",
            "would be ", "should be ",
            "analyse ", "analyze ", "list ", "show ", "tell me ",
            "give me ", "find ", "identify ", "suggest ",
            "explain ", "describe ", "available ", "possible ",
            "options for ",
        )
        if not any(t in msg for t in analysis_triggers):
            return False

        # Require flowsheet to be loaded — otherwise nothing to analyse
        try:
            st = self.bridge.state
            if not (getattr(st, "name", None) or
                    getattr(st, "active_alias", None) or
                    getattr(st, "loaded_flowsheets", {}) or
                    getattr(self.bridge, "_flowsheet", None)):
                return False
        except Exception:
            return False
        return True

    def _analysis_fast_answer(self, user_message: str) -> str:
        """Produce a deterministic answer enumerating decision variables
        and suggested objectives for the loaded flowsheet. Used when:
          (a) the user asks "what can be optimised"
          (b) the LLM is unavailable / quota-exhausted
        Returns a markdown response ready to render in chat."""
        try:
            from optimization_orchestrator import (
                suggest_decision_variables, _enumerate_flowsheet_objects,
            )
            from pp_validator import _detect_plugin_flowsheet
        except Exception as exc:
            return f"**Analysis failed:** {exc}"

        # Suggest variables
        suggestions = suggest_decision_variables(self.bridge, max_n=10)
        plugin = _detect_plugin_flowsheet(self.bridge)
        objs = _enumerate_flowsheet_objects(self.bridge)
        n_streams = len(objs.get("streams") or [])
        n_uops    = len(objs.get("unit_ops") or [])

        # Try to identify product/output streams to suggest as objectives
        output_keywords = ("product", "out", "outlet", "distillate",
                            "extract", "raffinate", "bottoms", "top",
                            "overhead", "effluent")
        product_streams = []
        for s in objs.get("streams") or []:
            tag = (s.get("tag") or "")
            if any(kw in tag.lower() for kw in output_keywords):
                product_streams.append(tag)

        parts: List[str] = []
        parts.append("# 🔬 Flowsheet Optimisation Analysis\n")
        parts.append(f"**Flowsheet:** {getattr(self.bridge.state, 'name', '?')}")
        parts.append(f"**Composition:** {n_streams} streams, "
                     f"{n_uops} unit operations"
                     + (f" ({plugin}-managed)" if plugin else "") + "\n")

        if not suggestions:
            parts.append("⚠ **No optimisable variables auto-detected.**\n")
            parts.append("This can happen when:\n")
            parts.append("- The flowsheet uses a plugin (Cantera, ChemSep) "
                          "that doesn't expose feeds in the standard way")
            parts.append("- All streams are computed outputs (no feeds)")
            parts.append("- Feed flow rates are currently zero\n")
            parts.append("**You can still try a manual optimisation by naming "
                          "specific variables in your goal, e.g.:**")
            parts.append("> *minimise Soot mass flow by varying Air flow "
                          "between 100 and 500 kg/h*\n")
            return "\n".join(parts)

        # Group by role for readability
        by_role: Dict[str, List[Dict]] = {}
        for s in suggestions:
            by_role.setdefault(s.get("role", "other"), []).append(s)

        role_labels = {
            "reactor_T":  "🔥 Reactor outlet temperatures",
            "pressure":   "🔧 Pressures (compressors / valves)",
            "reflux":     "🌡 Column reflux ratios",
            "flow":       "💧 Feed mass flow rates",
            "feed_T":     "🌡 Feed temperatures",
            "operating":  "⚙️ Operating parameters",
        }

        parts.append(f"### ✅ {len(suggestions)} variable(s) recommended "
                      "for optimisation:\n")
        parts.append("| # | Variable | Current | Suggested Range | Role |")
        parts.append("|---|---|---:|:---|:---:|")
        for i, s in enumerate(suggestions, 1):
            label = role_labels.get(s.get("role", "other"),
                                     s.get("role", "other"))
            cur = s.get("initial", "?")
            cur_s = (f"{cur:.4g}" if isinstance(cur, (int, float))
                      else str(cur))
            parts.append(
                f"| {i} | `{s['tag']}.{s['property']}` | "
                f"{cur_s} {s.get('unit','')} | "
                f"[{s['lower']:.4g}, {s['upper']:.4g}] | {label} |"
            )

        # Objective suggestions
        parts.append("\n### 🎯 Suggested Objectives")
        if product_streams:
            parts.append("Product-stream observables that look like good "
                          "optimisation objectives:\n")
            for tag in product_streams[:5]:
                parts.append(f"- **{tag}** — maximise purity / flow, "
                              "minimise impurities, minimise unwanted species")
        else:
            parts.append("- Any product stream's purity, flow, or composition")
            parts.append("- Total energy duty (sum of heater/cooler duties) — "
                          "minimise")
            parts.append("- Reactor conversion / yield — maximise")

        # Example commands
        parts.append("\n### 💡 Example Commands You Can Run")
        ex_var = suggestions[0]
        if product_streams:
            obj_stream = product_streams[0]
            parts.append(f"> *minimise {obj_stream} mass flow*")
            parts.append(f"> *maximise H2 purity in {obj_stream}*")
        parts.append(f"> *optimise the process to maximise yield*")
        parts.append(f"> *minimise total energy consumption*")
        parts.append(f"> *maximise efficiency*")

        parts.append("\n_Run any of these as a chat command and I will "
                      "execute the optimisation against the loaded flowsheet._")
        return "\n".join(parts)

    def _should_fast_path_optimization(self, user_message: str) -> bool:
        """Return True if the user message is a vague optimization request
        AND a flowsheet is loaded — meaning we should bypass the LLM
        tool-call loop and run optimize_flowsheet_with_llm directly.

        Heuristics: must contain an optimization verb + a process noun, AND
        NOT contain explicit variable/bound specifications (which would
        suggest the user wants dwsim_optimize with their custom spec)."""
        if not user_message:
            return False
        msg = user_message.lower().strip()
        if len(msg) > 400:
            # Too long — let the LLM parse; user probably gave details
            return False

        # Skip questions and meta-queries — these want an LLM explanation,
        # not an actual optimisation run.
        question_patterns = (
            "what can ", "what could ", "what would ", "what should ",
            "what are ", "what is ", "what do you ",
            "which ", "how can ", "how could ", "how do ", "how would ",
            "how should ", "how to ", "why ", "where ",
            "can you ", "could you ", "would you ", "do you ", "are you ",
            "is it possible", "is there ",
            "list ", "show me ", "tell me ", "explain ", "describe ",
            "give me an overview", "help me understand",
            "what's possible", "what's available",
        )
        # Use leading-word matching — only catch genuine questions, not
        # commands like "minimise X subject to..." which contain "to".
        first_words = msg.split(maxsplit=3)
        first_part = " ".join(first_words[:3]) + " "
        if any(p in first_part for p in question_patterns):
            return False
        if "?" in user_message:
            return False
        # Phrases that explicitly ask for capability info, not action
        if any(k in msg for k in (
                "what optimisable", "what is optimisable",
                "what can be optimised", "what can be optimized",
                "what are the optimisation options",
                "what are the optimization options",
                "list the variables", "show variables")):
            return False

        # ── Creation / build intent is NOT optimisation ──────────────────────
        # A request to CREATE/BUILD/DESIGN a flowsheet or process must go to the
        # normal tool-call loop (which builds it), never to the optimisation
        # fast-path. Checked BEFORE the flowsheet-presence test, because a
        # plugin flowsheet (e.g. Cantera) already being loaded must not cause a
        # build request like "create a water heating process" to be misrouted
        # into "what would you like to optimise?".
        build_intent = (
            "create ", "build ", "make ", "design ", "construct ",
            "set up ", "set-up ", "add a ", "add an ", "new flowsheet",
            "generate a flowsheet", "draw a ", "model a ", "simulate a ",
        )
        if any((" " + msg).find(" " + b) >= 0 for b in build_intent):
            return False

        # ── Require genuine optimisation intent BEFORE the flowsheet check ────
        # Without an optimisation verb/noun this is not an optimisation request,
        # regardless of which (plugin or standard) flowsheet is loaded. This is
        # the guard that stops non-optimisation messages from fast-pathing on a
        # loaded plugin flowsheet. Substring matching so optimise/optimisation,
        # maximis*/maximiz*, minimis*/minimiz* all hit.
        opt_substrings = (
            "optimis", "optimiz",   # optimise/optimize and ALL their forms
            "maximis", "maximiz",   # maximise/maximize/maximisation/maximization
            "minimis", "minimiz",   # minimise/minimize/minimisation/minimization
            "improve", "increase", "decrease",
            "reduce ", "reduction",
            "lower ", "raise ",
            "best operating", "best conditions",
        )
        if not any(s in msg for s in opt_substrings):
            return False

        # Require a flowsheet to be loaded — check state cache FIRST, then
        # fall back to live bridge query (state may be empty if the
        # flowsheet was loaded via the UI Load button, not the agent).
        try:
            st = self.bridge.state
            has_fs = bool(getattr(st, "name", None) or
                          getattr(st, "active_alias", None) or
                          getattr(st, "loaded_flowsheets", {}))
            # Also trust the bridge's internal _flowsheet attribute, which is
            # set whenever load_flowsheet succeeds — including for Cantera /
            # ChemSep / Reaktoro plugin flowsheets whose list_simulation_objects
            # may return empty even when the flowsheet is real.
            if not has_fs and getattr(self.bridge, "_flowsheet", None):
                has_fs = True

            n_streams = len(getattr(st, "streams", []) or [])
            n_uops    = len(getattr(st, "unit_ops", []) or [])

            # Plugin flowsheets: trust has_fs alone, don't require object count
            # (plugins manage objects internally; standard introspection may
            # return fewer than the actual count).
            name_lc = (getattr(st, "name", "") or "").lower()
            is_plugin = any(k in name_lc for k in
                            ("cantera", "chemsep", "reaktoro"))
            if has_fs and is_plugin:
                return True

            if not has_fs or (n_streams + n_uops < 2):
                # Live fallback
                try:
                    live = _bridge_objects(self.bridge)
                    if len(live.get("streams") or []) + \
                       len(live.get("unit_ops") or []) >= 2:
                        return True
                except Exception:
                    pass
                # If the bridge HAS a loaded flowsheet but we can't count
                # objects, still trust the has_fs signal — the flowsheet is
                # real and the user wants to optimise it. The orchestrator
                # and solver will handle introspection failures gracefully.
                if has_fs:
                    return True
                return False
        except Exception:
            return False

        # (Optimisation-intent already confirmed above, before the flowsheet
        # check, so no need to re-test the opt_substrings here.)

        # Short messages: bare "optimise" / "do optimisation" / "minimise it"
        # are clear enough on their own (≤ 6 words, optimization verb present)
        # — the orchestrator will pick a sensible default objective from the
        # loaded flowsheet.
        word_count = len(msg.split())
        if word_count <= 6:
            return True

        # Longer messages: also require a process noun to distinguish a real
        # optimization request from "explain how optimisation works in chem eng".
        nouns = ("yield", "purity", "production", "emission", "energy",
                  "efficiency", "conversion", "selectivity", "duty", "cost",
                  "throughput", "recovery", "loss", "flow", "temperature",
                  "pressure", "concentration", "consumption", "process",
                  "flowsheet", "simulation", "operation", "hydrogen",
                  "methanol", "ethanol", "product", "feed", "reactor",
                  "column", "separation", "it ", "this ", "that ")
        if not any(n in msg + " " for n in nouns):
            return False

        # If user gave a full structured spec (e.g. "vary T between 100-200"),
        # skip — they want fine-grained control via dwsim_optimize.
        if any(p in msg for p in ("between ", " to ", "lower bound",
                                    "upper bound", "bounds:")):
            # …unless they also explicitly say "auto" or "the process"
            if not any(p in msg for p in ("auto", "the process", "the flowsheet")):
                return False

        return True

    def _detect_phase(self) -> str:
        """Inspect bridge state to decide which tool subset is relevant."""
        try:
            st = self.bridge.state
            has_fs = bool(getattr(st, "name", None) or
                          getattr(st, "active_alias", None) or
                          getattr(st, "loaded_flowsheets", {}))
            if not has_fs:
                return "discovery"
            has_objects = bool(getattr(st, "streams", []) or
                               getattr(st, "unit_ops", []))
            if not has_objects:
                return "build"
            # Heuristic: if last tool was a solve, we're in analyze; else build
            last_solve_idx = -1
            last_build_idx = -1
            for i, tc in enumerate(getattr(self, "_turn_tool_timings", []) or []):
                n = tc.get("name", "")
                if n in ("save_and_solve", "run_simulation"):
                    last_solve_idx = i
                if n in ("add_object", "connect_streams", "set_stream_property",
                         "set_unit_op_property"):
                    last_build_idx = i
            if last_solve_idx > last_build_idx:
                return "analyze"
            return "build"
        except Exception:
            return "build"

    @classmethod
    def _active_tool_names(cls, phase: str, user_message: str) -> set:
        """Pure tool-selection logic: phase subset + always-on + intent-keyword
        boosts. Separated from _select_active_tools so it is unit-testable
        without a live bridge/LLM (verifies that gating the heavy optimisers
        out of the base set never strands an optimisation request)."""
        allowed = {
            "discovery": cls._TOOLS_DISCOVERY,
            "build":     cls._TOOLS_BUILD | cls._TOOLS_SOLVE,
            "solve":     cls._TOOLS_SOLVE | cls._TOOLS_ANALYZE,
            "analyze":   cls._TOOLS_ANALYZE | cls._TOOLS_BUILD,
        }.get(phase, set())
        allowed = allowed | cls._TOOLS_ALWAYS
        um = (user_message or "").lower()
        for keyword, extra in cls._TOOLS_INTENT_KEYWORDS.items():
            if keyword in um:
                allowed = allowed | extra
        return allowed

    # Essential primitives that are always safe to include. When phase
    # filtering yields a tiny set, we top up with these instead of dumping ALL
    # ~107 schemas (~21k tokens) — the old `< 5 → return all_tools` path
    # silently re-bloated every such call. These are the build/solve/inspect
    # basics the agent needs regardless of phase.
    _CORE_TOOL_NAMES = frozenset({
        "new_flowsheet", "build_flowsheet_atomic", "add_object",
        "connect_streams", "set_stream_property", "set_stream_composition",
        "set_unit_op_property", "save_and_solve", "run_simulation",
        "list_simulation_objects", "get_stream_properties", "check_convergence",
        "load_flowsheet", "find_flowsheets", "search_knowledge",
    })

    def _select_active_tools(self, all_tools: list) -> list:
        """Return only the tools relevant to the current agent phase.
        Falls back to all_tools on any inspection error so behaviour never
        degrades to "no tools available"."""
        try:
            phase = self._detect_phase()
            # Intent-keyword boost reads _turn_user_message set by chat().
            allowed = self._active_tool_names(
                phase, getattr(self, "_turn_user_message", "") or "")

            if not allowed:
                return all_tools
            filtered = [t for t in all_tools if t.get("name") in allowed]
            # If phase filtering produced a tiny set, top up with the core
            # primitives rather than re-sending all ~107 schemas. Only a truly
            # degenerate result (e.g. every tool renamed) falls back to the full
            # set, and that is logged loudly so the re-bloat is never silent.
            if len(filtered) < 5:
                names = set(allowed) | self._CORE_TOOL_NAMES
                topped = [t for t in all_tools if t.get("name") in names]
                if len(topped) >= 5:
                    self._log(f"[ToolSelect] phase={phase} → small set "
                              f"({len(filtered)}), topped up to {len(topped)} "
                              f"with core primitives")
                    return topped
                _log.warning("[ToolSelect] phase=%s produced only %d tools even "
                             "with core top-up — sending all %d schemas (~21k "
                             "tokens); check tool-name drift.",
                             phase, len(topped), len(all_tools))
                return all_tools
            self._log(f"[ToolSelect] phase={phase} → {len(filtered)}/{len(all_tools)} tools active")
            return filtered
        except Exception as exc:
            _log.debug("dynamic tool selection skipped: %s", exc)
            return all_tools

    # ── Quality heuristic guard (Review-3 AI Gap 4) ───────────────────────────
    # Red flag patterns: numerical claims that would normally come from
    # tool output (temperatures, pressures, duties, conversions).
    _NUMERIC_CLAIM_RE = _re.compile(
        r"(?i)\b(?:T|temperature|P|pressure|duty|Q|conversion|yield|"
        r"flowrate|flow rate|composition|purity|reflux|reboiler|condenser)\b"
        r"[^.]*?\d+(?:\.\d+)?\s*"
        r"(?:K|°?C|bar|Pa|kPa|MPa|kW|MW|kg/s|kmol/h|mol/s|%|%w|%v)?"
    )

    # Deterministic confabulation guard: detect "load/open <…> flowsheet" intent.
    _LOAD_INTENT_RE = _re.compile(
        r"(?i)\b(?:load|open)\b[^.?!]{0,60}\bflowsheet\b")
    # Tools that actually ESTABLISH an active flowsheet (load it or build it).
    _ESTABLISH_TOOLS = {
        "load_flowsheet", "find_flowsheets", "load_flowsheet_by_path",
        "new_flowsheet", "build_flowsheet", "build_flowsheet_atomic",
        "instantiate_process_template", "create_from_template",
        "create_flowsheet_from_template", "add_object", "execute_build_plan",
    }

    def _apply_quality_guard(self, answer: str, tool_calls: List[Dict]) -> str:
        """
        Synchronous heuristic check. Appends a disclaimer when:
        (a) Answer cites numerical results but NO simulation/lookup tool was called.
        (b) save_and_solve was called and returned converged=False but the answer
            does not mention convergence/error.
        (c) A safety-related tool returned violations but answer omits SF codes.
        (d) The user asked to LOAD a specific flowsheet, the answer reports stream
            data, yet NO flowsheet was actually loaded or built this turn — i.e.
            the agent is reporting a previously-loaded flowsheet as if it were the
            requested one (confabulation).
        """
        if not answer or not isinstance(answer, str):
            return answer

        flags = []
        confab = False
        tool_names = [tc.get("name", "") for tc in (tool_calls or [])]
        tool_set = set(tool_names)

        # (d) Load-mismatch confabulation — deterministic.
        um = getattr(self, "_turn_user_message", "") or ""
        if self._LOAD_INTENT_RE.search(um) and self._NUMERIC_CLAIM_RE.search(answer):
            # _turn_tool_timings entries are {name, success, elapsed_s}; success
            # is top-level (some callers pass full {name, result} dicts too).
            def _ok(tc):
                if tc.get("success") is not None:
                    return bool(tc.get("success"))
                r = tc.get("result") or tc.get("output") or {}
                return bool(isinstance(r, dict) and r.get("success"))
            established_ok = any(
                tc.get("name") in self._ESTABLISH_TOOLS and _ok(tc)
                for tc in (tool_calls or [])
            )
            if not established_ok:
                confab = True
                flags.append(
                    "you asked to LOAD a specific flowsheet, but no flowsheet was "
                    "loaded or built in this turn — the stream data above is from a "
                    "PREVIOUSLY-loaded flowsheet and is very likely NOT the one you "
                    "requested. Do not trust it as the requested flowsheet"
                )

        # (a) Numerical claim without backing tool call
        sim_tools = {
            "save_and_solve", "run_simulation", "get_simulation_results",
            "get_stream_properties", "get_phase_results", "lookup_compound_properties",
            "compute_vapor_pressure", "batch_lookup_properties",
            "calculate_phase_envelope", "parametric_study", "optimize_parameter",
            "monte_carlo_study", "bayesian_optimize", "get_column_properties",
            "get_reactor_properties",
        }
        if self._NUMERIC_CLAIM_RE.search(answer) and not (tool_set & sim_tools):
            flags.append(
                "numerical values were not verified by a simulation or property-lookup tool"
            )

        # (b) Convergence failure not mentioned
        for tc in (tool_calls or []):
            if tc.get("name") in ("save_and_solve", "run_simulation"):
                result = tc.get("result") or tc.get("output") or {}
                if isinstance(result, dict):
                    if (result.get("converged") is False or
                        result.get("convergence_errors") or
                        (isinstance(result.get("safety_status"), str) and
                         "VIOLATION" in result.get("safety_status", "").upper())):
                        ans_lo = answer.lower()
                        if not any(w in ans_lo for w in
                                   ("converge", "error", "violation", "fail", "warning", "sf-")):
                            flags.append(
                                "the simulation reported convergence errors or safety "
                                "violations that are NOT reflected in the answer above"
                            )
                            break

        if not flags:
            return answer

        # Confabulation (reporting the wrong flowsheet) is serious — lead with a
        # prominent warning at the TOP so it can't be missed, rather than a
        # footnote the user might skim past.
        if confab:
            banner = (
                "> ⚠ **Likely wrong flowsheet.** " + flags[0] + ".\n"
                "> If you wanted a specific flowsheet, ask me to find/load it by "
                "name or path first.\n\n")
            other = [f for f in flags[1:]]
            tail = ("\n\n---\n*Quality note: " + "; ".join(other) +
                    ". Please verify before relying on them.*") if other else ""
            return banner + answer + tail

        disclaimer = (
            "\n\n---\n"
            "*Quality note: " + "; ".join(flags) +
            ". Please verify these results before relying on them.*"
        )
        return answer + disclaimer

    def _run_ai_judge_async(
        self,
        user_message: str,
        answer:       str,
        tool_calls:   List[Dict],
    ) -> None:
        """
        Run AI-as-judge evaluation asynchronously (fire-and-forget thread).
        Scores are appended to eval_log.json without blocking the user.
        Never raises — evaluation failure is silently ignored.
        """
        import threading

        def _judge_task():
            try:
                from evaluation import AIJudge, get_eval_log
                judge = AIJudge(self.llm)
                scores = judge.evaluate(
                    user_query   = user_message,
                    agent_answer = answer,
                    tool_calls   = tool_calls,
                )
                if scores:
                    log = get_eval_log()
                    # Append judge scores to the most recent session
                    sessions = log._sessions
                    if sessions:
                        sessions[-1]["judge_scores"] = scores
                        log._save()
                    self._log(
                        f"[Judge] EOS={scores.get('property_package_correctness','?')} "
                        f"Plaus={scores.get('physical_plausibility','?')} "
                        f"Complete={scores.get('completeness','?')} "
                        f"Halluc={scores.get('hallucination_absence','?')} "
                        f"Overall={scores.get('overall','?')}/5"
                    )
            except Exception as exc:
                self._log(f"[Judge] evaluation failed (non-fatal): {exc}")

        t = threading.Thread(target=_judge_task, daemon=True)
        t.start()

    def _parametric_study_with_progress(self, **kwargs) -> dict:
        """
        Wraps bridge.parametric_study with an on_progress callback that streams
        each solved data point as a token, so the UI shows a live progress bar
        instead of a blank screen for the full study duration.

        Note: DWSIM bridge is single-threaded (.NET COM STA model).
        True parallelism would crash the bridge — sequential with streaming
        progress is the correct approach for DWSIM.
        """
        n_total = len(kwargs.get("values", []))

        def _progress(i: int, n: int, v: float, obs_val) -> None:
            if self.on_token:
                try:
                    if obs_val is not None:
                        msg = f"[parametric] Point {i}/{n}: {kwargs.get('vary_property','input')}={v} → {kwargs.get('observe_property','output')}={obs_val:.4g}\n"
                    else:
                        msg = f"[parametric] Solving point {i}/{n}: {kwargs.get('vary_property','input')}={v}…\n"
                    self.on_token(msg)
                except Exception:
                    pass

        return self.bridge.parametric_study(on_progress=_progress, **kwargs)

    # ── public API ────────────────────────────────────────────────────────────

    def chat(self, user_message: str) -> str:
        # ── Persistent State Card (Review-3 AI Gap 2) ─────────────────────────
        # After hierarchical history summarization, the LLM may lose track of
        # the active flowsheet, compounds, and property package. Inject a
        # compact "state card" as a system message at the top of each turn
        # so the model never has to reconstruct state from compressed history.
        try:
            st = self.bridge.state
            fs_name = getattr(st, "name", None) or getattr(st, "flowsheet_name", None) or "none"
            pp      = getattr(st, "property_package", None)
            comps   = getattr(st, "compounds", []) or []
            streams = getattr(st, "streams", []) or []
            unitops = getattr(st, "unit_ops", []) or []

            # BUG FIX: when a flowsheet is loaded via Load button (not the
            # agent's own tools), the state cache may report empty PP /
            # compounds even though the live flowsheet has them. Query the
            # bridge directly as a fallback so the LLM doesn't say "no
            # property package or compounds defined" for a loaded flowsheet.
            real_objs_present = bool(streams or unitops)
            try:
                live_objs = _bridge_objects(self.bridge)
                live_streams = live_objs.get("streams") or []
                live_unitops = live_objs.get("unit_ops") or []
                if not real_objs_present and (live_streams or live_unitops):
                    streams = live_streams
                    unitops = live_unitops
                    real_objs_present = True
            except Exception:
                pass

            if real_objs_present and (not pp or pp in ("not set", "None", "none")):
                try:
                    r = self.bridge.get_property_package()
                    if isinstance(r, dict) and r.get("success"):
                        v = r.get("property_package") or ""
                        if v and v.lower() not in ("none", "not set"):
                            pp = v
                except Exception:
                    pass
            if real_objs_present and not comps:
                try:
                    r = self.bridge.list_compounds()
                    if isinstance(r, dict) and r.get("success"):
                        comps = r.get("compounds") or []
                except Exception:
                    pass

            # Plugin-flowsheet detection. Some flowsheets (Cantera, Reaktoro,
            # ChemSep) define compounds and PP through plugin internals rather
            # than DWSIM's standard SelectedCompounds.Keys / SelectedPropertyPackage.
            # When objects ARE present but PP/compounds appear empty, infer
            # that they're plugin-managed and tell the LLM so explicitly,
            # rather than letting it conclude the flowsheet is broken.
            is_plugin_managed = bool(
                real_objs_present
                and (not pp or pp in ("not set", "None", "none"))
                and not comps
            )
            # Also detect by flowsheet-name hints (Cantera, ChemSep, Reaktoro)
            name_lc = (fs_name or "").lower()
            plugin_hint = ""
            if "cantera" in name_lc:
                plugin_hint = "Cantera"
            elif "chemsep" in name_lc:
                plugin_hint = "ChemSep"
            elif "reaktoro" in name_lc:
                plugin_hint = "Reaktoro"

            if is_plugin_managed:
                pp_display = (f"(managed by {plugin_hint} plugin)"
                              if plugin_hint
                              else "(managed by a plugin / not introspectable)")
                comp_display = (f"(managed by {plugin_hint} plugin)"
                                if plugin_hint
                                else "(managed by a plugin / not introspectable)")
            else:
                pp_display = pp or "not set"
                comp_str = ", ".join(list(comps)[:6]) + (
                    f" (+{len(comps)-6} more)" if len(comps) > 6 else "")
                comp_display = comp_str or "none"

            state_card = (
                f"[CURRENT FLOWSHEET STATE] "
                f"Name: {fs_name} | Property Package: {pp_display} | "
                f"Compounds: {comp_display} | "
                f"Streams: {len(streams)} | Unit ops: {len(unitops)}"
                + (f" | NOTE: Flowsheet is loaded and READY. "
                    "Property package and compounds are configured "
                    f"via the {plugin_hint or 'flowsheet plugin'} — "
                    "proceed with any user request; do NOT ask the user "
                    "to specify compounds or property package."
                   if is_plugin_managed else "")
            )
            # Avoid duplicate state cards if the previous message was already a state card.
            if not (self._history and
                    self._history[-1].get("role") == "system" and
                    isinstance(self._history[-1].get("content"), str) and
                    self._history[-1]["content"].startswith("[CURRENT FLOWSHEET STATE]")):
                self._history.append({"role": "system", "content": state_card})
        except Exception as _exc:
            _log.debug("state card injection skipped: %s", _exc)

        self._history.append({"role": "user", "content": user_message})
        # Token-aware + message-count trimming (see _trim_history above).
        self._history = _trim_history(self._history)
        # Step-by-step AI building needs more iterations than the old single-call
        # approach (new_flowsheet + N*add_object + M*connect + set props + solve).
        # Remove the old 6-iteration cap — use the full max_iterations budget.
        _effective_max = self.max_iterations
        self._log(f"\n{'━'*60}")
        self._log(f"User ▶ {user_message}")

        # ── Reproducibility replay builder ────────────────────────────────────
        try:
            import replay_log as _rl
            from ablation_config import ablation as _abl
            self._replay_builder = _rl.TurnBuilder(
                session_id  = self._session_id,
                turn_index  = self._turn_index,
                provider    = getattr(self.llm, "provider", ""),
                model       = getattr(self.llm, "model", ""),
                temperature = getattr(self.llm, "temperature", 0.0),
                seed        = getattr(self.llm, "_REPRODUCIBILITY_SEED", 42),
                **_abl.tags(),   # condition / task_id / rep for ablation grouping
            )
        except Exception:
            self._replay_builder = None

        # Timing metrics (D1)
        turn_t0 = time.monotonic()
        first_token_ts: Optional[float] = None
        self._turn_tool_timings: List[Dict[str, Any]] = []
        self._turn_user_message = user_message

        # State delta vs. previous turn — computed once per user message, not
        # per iteration. On the first turn with a loaded flowsheet we inject
        # the full state (empty prev → every tag counted as "added").
        try:
            _turn_start_snap = snapshot_flowsheet_state(self.bridge)
            _state_delta     = diff_flowsheet_state(
                self._last_state_snapshot, _turn_start_snap)
        except Exception as _exc:
            _log.warning("state snapshot failed: %s", _exc)
            _turn_start_snap, _state_delta = {}, ""

        # Circuit breaker: if the same (tool, error_code) pair shows up in
        # three consecutive iterations, break out so the user gets a real
        # error rather than waiting for max_iterations to expire.
        recent_fail_codes: List[set] = []
        last_error_msg = ""
        # Detect user-intent class so the breaker can give a context-aware
        # message instead of always pushing "build a biogas flowsheet".
        _um_lc = (user_message or "").lower()
        # NOTE: "open"/"load"/"use" + flowsheet implies find→load workflow, so
        # classify those as discovery too — the agent legitimately needs to
        # call find_flowsheets, then load_flowsheet.
        if any(k in _um_lc for k in (
                "find", "list", "search", "discover",
                "scan", "locate", "where", "show me", "files on",
                "open ", "load ", "use ", "pick ", "select ")) and (
                "flowsheet" in _um_lc or "file" in _um_lc
                or "simulation" in _um_lc or ".dwxm" in _um_lc
                or any(k in _um_lc for k in ("find", "search", "list", "scan",
                                             "discover", "locate", "where"))):
            _user_intent_class = "discovery"
        elif any(k in _um_lc for k in ("build", "create", "make", "design", "synthesize",
                                       "generate flowsheet", "new flowsheet")):
            _user_intent_class = "build"
        elif any(k in _um_lc for k in ("optimize", "minimize", "maximize", "sensitivity")):
            _user_intent_class = "optimize"
        else:
            _user_intent_class = "other"
        self._turn_user_intent_class = _user_intent_class

        # ── Deterministic ANALYSIS FAST-ANSWER ────────────────────────────────
        # When the user asks a QUESTION about what can be optimised — e.g.
        # "what variables can be optimised?", "analyse the flowsheet and
        # find what can be tuned", "list optimisable parameters" — answer
        # deterministically using the variable suggester. Works even when
        # ALL LLM providers are down.
        try:
            if self._should_analysis_fast_answer(user_message):
                self._log("[ANALYSIS-FAST-ANSWER] introspection question detected")
                if self.on_token:
                    try:
                        self.on_token("🔬 Analysing flowsheet for optimisation "
                                      "opportunities…\n")
                    except Exception:
                        pass
                md = self._analysis_fast_answer(user_message)
                self._history.append({"role": "assistant", "content": md})
                self._last_state_snapshot = _turn_start_snap
                try:
                    self._emit_turn_metrics(turn_t0, 1, md, exhausted=False)
                except Exception:
                    pass
                return md
        except Exception as _exc:
            _log.debug("analysis fast-answer skipped: %s", _exc)

        # ── Deterministic FAST PATH for vague optimization goals ──────────────
        # When the user says "maximise yield", "optimise the process",
        # "minimise energy" etc. AND a flowsheet IS loaded, bypass the LLM
        # tool-choice loop entirely and call optimize_flowsheet_with_llm
        # directly. This:
        #   • Eliminates Gemini-Flash sometimes asking clarification questions
        #     instead of using the new tool;
        #   • Makes optimization work even when ALL LLM providers are down
        #     (the orchestrator has a heuristic objective-mapper fallback);
        #   • Is faster — one bridge call instead of 3+ LLM round-trips.
        try:
            _fast_path_disabled = os.getenv("AGENT_OPT_FAST_PATH", "1") == "0"
            if (not _fast_path_disabled
                    and self._should_fast_path_optimization(user_message)):
                self._log("[FAST-PATH] vague optimization goal detected — "
                          "calling optimize_flowsheet_with_llm directly")
                if self.on_token:
                    try:
                        self.on_token(
                            "🎯 Detected optimization goal — running DWSIM-internal "
                            "auto-optimization workflow…\n"
                        )
                    except Exception:
                        pass
                kwargs = {"goal": user_message, "llm": self.llm,
                          "max_iter": 50, "tolerance": 1e-3}
                kwargs["on_step"] = lambda stage, detail: (
                    self.on_token(f"{stage}  {detail}\n")
                    if self.on_token else None
                )
                # Per-eval streaming (throttled): first 5 evals + every 5th
                _last_emit = [0]
                def _fp_on_eval(it, params, obj, best):
                    if not self.on_token: return
                    if not (it <= 5 or it % 5 == 0 or it - _last_emit[0] >= 5):
                        return
                    _last_emit[0] = it
                    obj_s  = f"{obj:.4g}"  if obj  is not None else "failed"
                    best_s = f"{best:.4g}" if best is not None else "—"
                    try:
                        self.on_token(f"   eval {it:3d}: obj={obj_s}  best={best_s}\n")
                    except Exception:
                        pass
                kwargs["on_eval"] = _fp_on_eval
                try:
                    result = self.bridge.optimize_flowsheet_with_llm(**kwargs)
                except Exception as exc:
                    result = {"success": False, "error": str(exc),
                              "chat_markdown": f"**Optimization failed:** {exc}"}
                md = (result.get("chat_markdown")
                      or result.get("error")
                      or "Optimization complete.")
                self._history.append({"role": "assistant", "content": md})
                # Emit metrics + return
                self._last_state_snapshot = _turn_start_snap
                try:
                    self._emit_turn_metrics(turn_t0, 1, md, exhausted=False)
                except Exception:
                    pass
                return md
        except Exception as _exc:
            _log.debug("optimization fast-path skipped: %s", _exc)

        # Repetition breaker: track how many times each SUCCESSFUL tool was called.
        # If a non-destructive tool (discovery/read-only) is called 3+ times in a
        # row with no other work done, the agent is spinning — break out.
        _DISCOVERY_TOOLS = {
            # Only pure list/read-only tools trigger the repetition breaker.
            # create_from_template, batch_lookup_properties are productive — excluded.
            "list_flowsheet_templates", "find_flowsheets", "list_loaded_flowsheets",
            "recall_memory",
            "list_simulation_objects",   # read-only; calling 3+ times means agent is stuck
            "get_property_package",      # same: static after load
            "validate_topology",         # topology doesn't change without an add/connect
        }
        _success_tool_counts: Dict[str, int] = {}

        # New user turn: give the SELECTED provider first chance again. Any
        # failover that happened on a previous turn does not carry over.
        self._turn_client = None

        for iteration in range(1, _effective_max + 1):
            _cur = getattr(self, "_turn_client", None) or self.llm
            self._log(f"\n[Iter {iteration}] Calling {_cur.provider.upper()}…")

            # Pass user_message on iter 1 for proactive RAG.
            # On subsequent iters pass empty string (RAG already injected).
            # Exception: if a convergence error was returned last iter, re-inject
            # user message so RAG can surface convergence troubleshooting chunks.
            _inject_msg = user_message if iteration == 1 else ""
            if iteration > 1 and last_error_msg and "converge" in last_error_msg.lower():
                _inject_msg = f"convergence failure: {last_error_msg[:120]}"
            system = _build_system_prompt(
                self.bridge,
                state_delta  = _state_delta,
                user_message = _inject_msg,
            )

            # Set prompt in builder on first iteration; snapshot messages each call
            if self._replay_builder is not None:
                if iteration == 1:
                    self._replay_builder.set_prompt(user_message, system)
                self._replay_builder.add_message_snapshot(self._history)

            # Dynamic tool selection (Review-3 AI Gap 1):
            # Filter the 60+ tool catalog to ~20 tools relevant to current state.
            # Improves LLM tool-selection accuracy (research: degrades >25 tools).
            # Ablation: the direct_llm condition gives the model NO tools, so it
            # must answer from its own knowledge — the "does the agentic loop
            # help at all?" baseline.
            try:
                from ablation_config import ablation as _abl_tools
                _no_tools = _abl_tools.disable_tools
            except Exception:
                _no_tools = False
            active_tools = [] if _no_tools else self._select_active_tools(DWSIM_TOOLS)
            response = self._llm_chat_with_retry(
                messages=self._history,
                tools=active_tools,
                system_prompt=system,
            )

            if response is None:
                self._log("[Agent] LLM returned None after retries — all providers exhausted")
                try:
                    msg = self._build_provider_failure_message()
                except Exception:
                    msg = ("All LLM providers are currently unavailable or quota "
                           "exhausted. Please try again in a moment.")
                self._history.append({"role": "assistant", "content": msg})
                self._last_state_snapshot = _turn_start_snap
                return msg

            tool_calls   = response.get("tool_calls", [])
            text_content = (response.get("content") or "").strip()

            if not tool_calls:
                # Final answer from the model. Guarantee we never return empty.
                if not text_content:
                    text_content = self._last_resort_summary(
                        reason="model returned empty content with no tool calls"
                    )
                if self.stream_output and self.verbose:
                    self._stream_print("Agent ▶ ", text_content)
                else:
                    self._log(f"\nAgent ▶ {text_content[:400]}"
                              + ("…" if len(text_content) > 400 else ""))
                text_content = _apply_output_filter(text_content)  # Gap 2: safety disclaimer
                # ── Quality-heuristic guard (Review-3 AI Gap 4) ──────────────
                # Synchronous, no-LLM heuristic: catches the most common quality
                # red flags (numerical claims without tool calls, ignored
                # convergence errors, missing safety status mention). The async
                # AI-judge below still computes 4-criterion scores into eval_log.
                text_content = self._apply_quality_guard(
                    text_content, self._turn_tool_timings
                )
                self._history.append({"role": "assistant",
                                      "content": text_content})
                self._emit_turn_metrics(turn_t0, iteration, text_content)
                # ── Async AI-as-judge evaluation ─────────────────────────────
                # Fire-and-forget: score this response quality in background.
                # Never blocks the response. Scores stored in eval_log.json.
                self._run_ai_judge_async(
                    user_message = self._turn_user_message,
                    answer       = text_content,
                    tool_calls   = self._turn_tool_timings,
                )
                # Re-snapshot after the turn so next turn's diff captures
                # everything this turn's tool calls changed.
                try:
                    self._last_state_snapshot = snapshot_flowsheet_state(self.bridge)
                except Exception:
                    self._last_state_snapshot = _turn_start_snap
                return text_content

            # Use the client that PRODUCED the response (may be a failover
            # provider) so it is parsed with the matching provider format.
            _rc = getattr(self, "_response_client", None) or self.llm
            self._history.append(_rc.assistant_turn(response))

            # Reorder tool calls: when an LLM sends a parallel batch like
            # [add_object, add_object, new_flowsheet, ...], the add_objects
            # would run BEFORE new_flowsheet and silently no-op (no flowsheet
            # exists yet).  Sort so init tools run first, then state-changers,
            # then read-only tools.  Stable sort preserves LLM ordering within
            # each priority bucket.
            _PRIORITY = {
                "execute_build_plan": 0,  # deterministic ad-hoc plan
                "instantiate_process_template": 0,  # deterministic template build
                "build_flowsheet_atomic": 0,  # atomic build always runs first
                "new_flowsheet":      0,
                "load_flowsheet":     0,
                "create_from_template": 0,
                "switch_flowsheet":   1,
                "add_object":         2,
                "connect_streams":    3,
                "set_stream_property":      4,
                "set_stream_composition":   4,
                "set_unit_op_property":     4,
                "set_property_package":     4,
                "set_column_property":      4,
                "set_reactor_property":     4,
                "set_energy_stream":        4,
                "save_flowsheet":     5,
                "save_and_solve":     6,
                "run_simulation":     6,
            }
            tool_calls = sorted(tool_calls,
                                key=lambda tc: _PRIORITY.get(tc.get("name",""), 9))

            # Deduplicate: if the LLM sends multiple new_flowsheet calls in one
            # batch (parallel tool calls), execute only the first one and skip
            # the rest with a "already initialized" reply — prevents purge-loop.
            _seen_init_tools: set = set()
            # list_flowsheet_templates returns a static list — calling it twice
            # per turn wastes iterations and causes 9× spin loops.
            _ONCE_PER_BATCH = {"new_flowsheet", "save_and_solve",
                               "list_flowsheet_templates"}

            results = []
            for tc in tool_calls:
                name = tc["name"]
                if name in _ONCE_PER_BATCH and name in _seen_init_tools:
                    result: dict = {
                        "success": True,
                        "message": (f"Duplicate {name} call skipped — "
                                    "already called once this batch. "
                                    "Proceed to the next step."),
                        "_skipped_duplicate": True,
                    }
                else:
                    _seen_init_tools.add(name)
                    _tool_t0 = time.monotonic()
                    result = self._run_tool(name, tc.get("arguments"))
                    result = self._enrich_error_with_hint(name, result)  # structured hints
                    result = _compress_tool_result(name, result)
                    result = _sanitize_for_llm(result)   # Gap 1: injection defence
                    # ── Auto-diagnosis on save_and_solve failure ──────────────
                    # When simulation fails to converge, automatically call
                    # check_convergence and validate_feed_specs so the LLM has
                    # root-cause information without needing to ask for it.
                    if name == "save_and_solve" and not result.get("success", True):
                        result = self._auto_diagnose_convergence(result)
                    # ── Intent verification on successful solves ──────────────
                    # Blueprint §"Verifier" — score the run against declared intent
                    # (purity targets, setpoints, yields). Attaches an
                    # `intent_verification` block to the tool result so the LLM
                    # sees pass/fail per target with a repair_hint per failure.
                    if (name in ("save_and_solve", "run_simulation",
                                 "build_flowsheet_atomic", "robust_solve")
                            and isinstance(result, dict)
                            and result.get("success", False)
                            and self._active_intent is not None):
                        try:
                            iv = self._verify_active_intent()
                            if iv is not None:
                                result["intent_verification"] = iv
                        except Exception as _iv_exc:
                            _log.debug("intent verification failed: %s", _iv_exc)
                    # Clear active intent on new_flowsheet / load_flowsheet so it
                    # doesn't bleed across flowsheets in the same session.
                    if name in ("new_flowsheet", "load_flowsheet"):
                        self._active_intent = None
                    _tool_ms = (time.monotonic() - _tool_t0) * 1000
                    # Record in replay log
                    if self._replay_builder is not None:
                        try:
                            self._replay_builder.record_tool_call(
                                tool_name   = name,
                                arguments   = tc.get("arguments") or {},
                                result      = result,
                                duration_ms = _tool_ms,
                            )
                        except Exception:
                            pass
                results.append(result)
                # Capture SF violations from solve results for replay log
                if name in ("save_and_solve", "run_simulation") and isinstance(result, dict):
                    viols = result.get("safety_warnings") or result.get("sf_violations") or []
                    if viols:
                        self._last_sf_violations = viols
                if self.on_token:
                    try:
                        self.on_token(f"[tool:{name}] ")
                    except Exception:
                        pass

            _rc2 = getattr(self, "_response_client", None) or self.llm
            self._history.extend(
                _rc2.tool_result_turns(tool_calls, results)
            )

            # Track (tool_name, error_code) pairs that failed this iteration.
            # If the same pair recurs 3 iterations in a row, the agent is
            # looping on the same problem — stop and surface it.
            failed_this_iter = {
                (tc["name"], str(r.get("code") or "ERROR"))
                for tc, r in zip(tool_calls, results)
                if not r.get("success")
            }
            # Track previous (name, args) so a refinement (same name + different
            # args) counts as PROGRESS not spinning.
            if not hasattr(self, "_prev_tool_signatures"):
                self._prev_tool_signatures = {}
            for tc, r in zip(tool_calls, results):
                if not r.get("success"):
                    last_error_msg = str(r.get("error") or "")
                elif tc["name"] in _DISCOVERY_TOOLS:
                    # Compute a stable signature for this call's arguments.
                    try:
                        arg_sig = json.dumps(tc.get("arguments") or {},
                                             sort_keys=True, default=str)
                    except Exception:
                        arg_sig = str(tc.get("arguments"))
                    prev_sig = self._prev_tool_signatures.get(tc["name"])
                    if prev_sig is not None and prev_sig != arg_sig:
                        # Different arguments = refinement = progress. Reset.
                        _success_tool_counts[tc["name"]] = 1
                    else:
                        _success_tool_counts[tc["name"]] = (
                            _success_tool_counts.get(tc["name"], 0) + 1
                        )
                    self._prev_tool_signatures[tc["name"]] = arg_sig
                else:
                    # Any productive tool resets the discovery counters
                    _success_tool_counts.clear()
                    self._prev_tool_signatures.clear()
            # Repetition breaker: bail if ANY discovery tool called 3+ times
            # with the SAME arguments (was 2 — too aggressive on legitimate
            # broad-then-narrow workflows).
            for _tname, _cnt in _success_tool_counts.items():
                if _cnt >= 3:
                    # If the discovery tool already returned NON-EMPTY data on
                    # any call this turn, treat it as discovery success
                    # regardless of intent_class — the agent has the data it
                    # needs to take the next step (e.g. load_flowsheet). The
                    # breaker exists to stop empty-spin, not refinement-spin.
                    _has_useful_result = any(
                        tc.get("name") == _tname
                        and r.get("success")
                        and (
                            (isinstance(r.get("flowsheets"), list) and r.get("flowsheets"))
                            or (isinstance(r.get("files"), list) and r.get("files"))
                            or r.get("count", 0) > 0
                        )
                        for tc, r in zip(tool_calls, results)
                    )
                    _intent_cls = getattr(self, "_turn_user_intent_class", "other")
                    if _intent_cls == "discovery" or _has_useful_result:
                        # Find the last successful result for this tool and
                        # return it directly so the user sees the data.
                        last_result = next(
                            (r for tc, r in reversed(list(zip(tool_calls, results)))
                             if tc.get("name") == _tname and r.get("success")),
                            None
                        )
                        # Summarize result in a useful way.
                        if last_result:
                            preview = ""
                            files = last_result.get("flowsheets") or last_result.get("files") or []
                            count = last_result.get("count") or len(files) if isinstance(files, list) else 0
                            if isinstance(files, list) and files:
                                shown = "\n".join(
                                    f"  • `{f.get('path', f) if isinstance(f, dict) else f}`"
                                    for f in files[:15]
                                )
                                more = f"\n  …and {count - 15} more" if count > 15 else ""
                                preview = f"\n\nFound {count} item(s):\n{shown}{more}"
                            limit_hint = ""
                            if last_result.get("limit_hit"):
                                limit_hint = (
                                    "\n\n(Result was truncated at the scan limit. "
                                    "Set environment variable `FLOWSHEET_SCAN_LIMIT=500` "
                                    "to increase, or narrow the search with a more "
                                    "specific path.)"
                                )
                            # If the user said "open/load X", auto-suggest a
                            # specific load_flowsheet call with the top match
                            _top_path = None
                            if isinstance(files, list) and files:
                                _top = files[0]
                                _top_path = (_top.get("path")
                                             if isinstance(_top, dict) else _top)
                            _open_hint = ""
                            _um_l = (getattr(self, "_turn_user_message", "") or "").lower()
                            if _top_path and any(k in _um_l for k in ("open ", "load ", "use ")):
                                _open_hint = (
                                    f"\n\nThe top match is:\n  `{_top_path}`\n"
                                    "Say **'load it'** or **'load <number>'** "
                                    "and I'll open that flowsheet."
                                )
                            msg = (
                                f"Here is what I found for your discovery query.{preview}"
                                f"{limit_hint}{_open_hint}\n\n"
                                "Tell me which one to load, or refine the search."
                            )
                        else:
                            msg = (
                                f"`{_tname}` returned no usable data after "
                                f"{_cnt} attempts. Try a more specific path, "
                                f"or set `FLOWSHEET_SCAN_LIMIT` higher if the "
                                f"result was truncated."
                            )
                    elif _intent_cls == "build":
                        msg = (
                            f"Stopped: `{_tname}` was called {_cnt} times "
                            f"without progress on your build request. "
                            f"The agent is stuck on a read-only tool. "
                            f"Try using a more concrete request such as:\n\n"
                            f"'Build a flowsheet with PR package: a Methane feed "
                            f"stream at 25 C 10 bar 100 kmol/h, into a Heater "
                            f"raising temperature to 300 C.'"
                        )
                    else:
                        msg = (
                            f"Stopped: `{_tname}` was called {_cnt} times "
                            f"without making progress. Please restate your "
                            f"request with concrete numbers/specs the agent "
                            f"can act on."
                        )
                    self._log(f"[repetition-break] {msg}")
                    self._history.append({"role": "assistant", "content": msg})
                    self._emit_turn_metrics(turn_t0, iteration, msg, exhausted=True)
                    return msg
            recent_fail_codes.append(failed_this_iter)
            if len(recent_fail_codes) > 3:
                recent_fail_codes.pop(0)
            if (len(recent_fail_codes) == 3 and
                    all(recent_fail_codes) and
                    recent_fail_codes[0] & recent_fail_codes[1] & recent_fail_codes[2]):
                stuck_pair = next(iter(
                    recent_fail_codes[0] & recent_fail_codes[1] & recent_fail_codes[2]
                ))
                tool_name, code = stuck_pair
                msg = (
                    f"Stopped after `{tool_name}` failed 3 times in a row "
                    f"with `{code}`. Last error: {last_error_msg or '(no detail)'}\n\n"
                    f"Try rephrasing the request, unloading the current "
                    f"flowsheet first, or check the server console for traces."
                )
                self._log(f"[circuit-break] {msg}")
                self._history.append({"role": "assistant", "content": msg})
                self._emit_turn_metrics(turn_t0, iteration, msg,
                                        exhausted=True)
                try:
                    self._last_state_snapshot = snapshot_flowsheet_state(self.bridge)
                except Exception:
                    self._last_state_snapshot = _turn_start_snap
                return msg

        # Max iterations reached. Force a tool-less final-answer call so the
        # user always gets a real summary rather than a template apology.
        final = self._last_resort_summary(
            reason=f"max_iterations={_effective_max} exhausted"
        )
        self._history.append({"role": "assistant", "content": final})
        self._emit_turn_metrics(turn_t0, _effective_max, final,
                                exhausted=True)
        try:
            self._last_state_snapshot = snapshot_flowsheet_state(self.bridge)
        except Exception:
            self._last_state_snapshot = _turn_start_snap
        return final

    def chat_stream(self, user_message: str) -> Iterator[Dict[str, Any]]:
        """Streaming variant of `chat()` for the `/chat/stream` SSE endpoint.

        Yields event dicts in real time as the turn progresses:
          * ``{"type":"token","data":str}``     — incremental answer/progress text
          * ``{"type":"tool_call","data":{...}}`` — emitted as each tool completes
          * ``{"type":"done","data":str}``      — the full final answer
          * ``{"type":"error","data":str}``     — if the turn raised

        It reuses the entire `chat()` loop unchanged — it only installs live
        ``on_token`` / ``on_tool_call`` sinks that push into a thread-safe queue
        while `chat()` runs on a worker thread, then restores them. The terminal
        ``done`` event always carries the authoritative full answer, so a client
        can treat the token stream as a live preview and `done` as final."""
        q: "_queue.Queue" = _queue.Queue()
        _DONE = object()

        # Preserve any externally-configured sinks/flags and restore them after.
        _saved = (self.on_token, self.on_tool_call,
                  self.stream_output, self.verbose)

        def _emit_token(text: str) -> None:
            if text:
                q.put({"type": "token", "data": text})

        def _emit_tool(name: str, arguments: dict, result: dict) -> None:
            q.put({"type": "tool_call",
                   "data": {"name": name, "arguments": arguments,
                            "result": result}})

        self.on_token      = _emit_token
        self.on_tool_call  = _emit_tool
        # Final-answer word streaming in chat() is gated on both flags.
        self.stream_output = True
        self.verbose       = True

        holder: Dict[str, Any] = {}

        def _run() -> None:
            try:
                holder["answer"] = self.chat(user_message)
            except Exception as exc:  # surfaced as an error event below
                holder["error"] = str(exc)
            finally:
                q.put(_DONE)

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        try:
            while True:
                evt = q.get()
                if evt is _DONE:
                    break
                yield evt
            if "error" in holder:
                yield {"type": "error", "data": holder["error"]}
            else:
                yield {"type": "done",
                       "data": holder.get("answer", ""), "session_id": ""}
        finally:
            (self.on_token, self.on_tool_call,
             self.stream_output, self.verbose) = _saved

    def _emit_turn_metrics(self, t0: float, iterations: int,
                           final_text: str,
                           exhausted: bool = False) -> None:
        """Capture per-turn timing, reproducibility fingerprint, and persist memory."""
        import hashlib
        total_s = time.monotonic() - t0
        tool_timings = getattr(self, "_turn_tool_timings", []) or []

        # ── Reproducibility fingerprint (journal requirement) ─────────────────
        # A SHA-256 of (prompt + tool_sequence) lets reviewers verify that
        # two runs with the same fingerprint are comparable. temperature=0 + seed=42
        # is set on all providers to reduce (not eliminate) non-determinism.
        tool_sequence = [t.get("name", "") for t in tool_timings]
        prompt_hash = hashlib.sha256(
            (getattr(self, "_turn_user_message", "") + json.dumps(tool_sequence)).encode()
        ).hexdigest()[:16]

        self.last_turn_metrics = {
            "total_s":        round(total_s, 3),
            "iterations":     iterations,
            "tool_count":     len(tool_timings),
            "tool_sequence":  tool_sequence,      # ordered list for ablation reporting
            "tool_timings":   tool_timings,
            "exhausted":      exhausted,
            "provider":       getattr(self.llm, "provider", ""),
            "model":          getattr(self.llm, "model", ""),
            "temperature":    getattr(self.llm, "temperature", 0.0),
            "seed":           getattr(self.llm, "_REPRODUCIBILITY_SEED", 42),
            "prompt_hash":    prompt_hash,        # fingerprint for reproducibility
        }
        self._log(
            f"[metrics] {total_s:.1f}s  iters={iterations}  "
            f"tools={len(tool_timings)}  provider={self.llm.provider}"
        )

        # ── Reproducibility replay log ────────────────────────────────────────
        try:
            import replay_log as _rl
            if self._replay_builder is not None:
                # Collect final state
                try:
                    sr = self.bridge.get_simulation_results()
                    snap = sr.get("stream_results", {}) or {}
                    converged = bool(getattr(self.bridge.state, "converged", False))
                except Exception:
                    snap, converged = {}, False
                sf_viols: list = getattr(self, "_last_sf_violations", []) or []
                turn = self._replay_builder.finish(
                    final_answer    = final_text,
                    converged       = converged,
                    stream_snapshot = snap,
                    sf_violations   = sf_viols,
                )
                _rl.append_turn(turn)
                self._turn_index += 1
                self._replay_builder = None
        except Exception as _rle:
            self._log(f"[replay_log] skipped: {_rle}")

        # Persist: only record a "flowsheet_built" entry if a write-tool ran
        # this turn. Otherwise the journal fills up with no-op chats.
        try:
            import session_memory
            write_tools = {
                "new_flowsheet", "add_object", "save_and_solve",
                "load_flowsheet", "run_simulation",
                "connect_streams", "set_stream_composition",
                "set_stream_property", "set_unit_op_property",
            }
            wrote = any(t.get("name") in write_tools for t in tool_timings)
            if wrote:
                st = self.bridge.state
                session_memory.record_flowsheet_built(
                    name=getattr(st, "name", "") or "",
                    compounds=list(getattr(st, "compounds", []) or []),
                    property_package=getattr(st, "property_package", "") or "",
                    path=getattr(st, "path", "") or "",
                    template=None,
                    streams=len(getattr(st, "streams", []) or []),
                    unit_ops=len(getattr(st, "unit_ops", []) or []),
                    converged=getattr(st, "converged", None),
                    prompt=self._turn_user_message,
                )
        except Exception as exc:
            self._log(f"[metrics] memory record skipped: {exc}")

    def _llm_chat_with_retry(self, *, messages, tools, system_prompt):
        """
        Call the LLM with exponential backoff on transient failures.

        Two-level timeout protection:
          Per-attempt : controlled by provider SDK timeout (_LLM_REQUEST_TIMEOUT_S)
          Aggregate   : _LLM_RETRY_BUDGET_S — total wall-clock across ALL attempts.
                        Prevents a 6-min hang when every provider takes 90 s to fail.

        Cross-provider failover (Review-3 AI Gap 3):
          After all per-attempt retries are exhausted on the primary provider,
          try the next provider in _FAILOVER_CHAIN if its API key is available.
          Order: openai → groq → anthropic (groq is free + fast).

        On total failure, populates self._last_provider_errors with a per-
        provider error map so the caller can surface a useful error message
        (e.g. "Groq: 429 quota; OpenAI: no key").
        """
        # Reset provider-error log for this call
        self._last_provider_errors: Dict[str, str] = {}
        # Track which providers had keys but failed; which had no key at all
        self._tried_providers: List[str] = []
        self._skipped_providers: Dict[str, str] = {}  # provider -> reason

        # Single aggregate budget across primary + ALL fallback providers,
        # so total wall-clock is bounded regardless of how many providers
        # the failover chain visits.
        budget_t0 = time.monotonic()

        def _budget_remaining() -> float:
            return _LLM_RETRY_BUDGET_S - (time.monotonic() - budget_t0)

        # Try primary provider first. Within a turn that already failed over,
        # prefer the sticky failover client so the history stays in one
        # provider's format (see _turn_client docstring in __init__).
        primary_client = getattr(self, "_turn_client", None) or self.llm
        primary = getattr(primary_client, "provider", "primary")
        if _budget_remaining() > 0:
            self._tried_providers.append(primary)
            resp = self._try_one_provider(
                messages, tools, system_prompt, primary_client, budget_t0
            )
            if resp is not None:
                # Remember WHICH client produced this response so the response is
                # parsed (assistant_turn / tool_result_turns) with the matching
                # provider format — not self.llm, which may differ after failover.
                self._response_client = primary_client
                self._turn_client = primary_client
                return resp

        # Ablation provider lock: a defensible ablation pins ONE provider+model
        # across all conditions, so silent cross-provider failover (which would
        # swap the model under test mid-study) must be disabled. When locked,
        # the turn fails on the primary rather than failing over.
        try:
            from ablation_config import ablation as _abl
            if _abl.lock_provider:
                self._skipped_providers["failover"] = "provider locked (ablation)"
                return None
        except Exception:
            pass

        # Cross-provider failover — only if primary exhausted retries AND
        # enough budget remains. SDK setup for a new provider can itself
        # take seconds (DNS, TLS, key validation), so require >= 5s of
        # remaining budget before attempting failover.
        _MIN_FAILOVER_BUDGET_S = 5.0
        for fallback_provider in _FAILOVER_CHAIN:
            if _budget_remaining() < _MIN_FAILOVER_BUDGET_S:
                self._skipped_providers[fallback_provider] = "budget exhausted"
                continue
            if fallback_provider == primary:
                continue
            fb_client = self._build_fallback_client(fallback_provider)
            if fb_client is None:
                self._skipped_providers[fallback_provider] = "no API key"
                continue
            self._log(f"[LLM] primary provider exhausted, failing over to {fallback_provider}")
            self._tried_providers.append(fallback_provider)
            resp = self._try_one_provider(
                messages, tools, system_prompt, fb_client, budget_t0
            )
            if resp is not None:
                # Failover succeeded on fb_client — parse with THAT client and
                # stick to it for the rest of this turn (consistent history).
                self._response_client = fb_client
                self._turn_client = fb_client
                return resp
        return None

    def _build_provider_failure_message(self) -> str:
        """Format a user-visible message explaining which providers failed.
        Used when _llm_chat_with_retry returns None."""
        tried = getattr(self, "_tried_providers", []) or []
        errors = getattr(self, "_last_provider_errors", {}) or {}
        skipped = getattr(self, "_skipped_providers", {}) or {}
        if not tried and not skipped:
            return ("LLM call failed — no providers available. Check that "
                    "GROQ_API_KEY (or OPENAI/ANTHROPIC_API_KEY) is set "
                    "in the environment.")
        parts = []
        for prov in tried:
            err = errors.get(prov, "no response")
            # Classify common errors for a friendlier hint
            err_lc = err.lower()
            if "429" in err or "quota" in err_lc or "rate" in err_lc:
                hint = "rate/quota exceeded — wait or switch model in the LLM dropdown"
            elif "401" in err or "403" in err or "key" in err_lc:
                hint = "invalid API key — update via the 🔑 icon in the header"
            elif "timeout" in err_lc or "timed out" in err_lc:
                hint = "request timed out — provider is slow or unreachable"
            elif "model" in err_lc and ("decommiss" in err_lc or "not found" in err_lc):
                hint = "model no longer available — pick another in the LLM dropdown"
            else:
                hint = err[:120]
            parts.append(f"• **{prov}**: {hint}")
        skipped_list = [f"{p} ({r})" for p, r in skipped.items()]
        msg = (
            "**All LLM providers failed.** Tried:\n\n"
            + "\n".join(parts)
        )
        if skipped_list:
            msg += "\n\n*Skipped:* " + ", ".join(skipped_list)
        msg += (
            "\n\n**What you can do:**\n"
            "• Try a different model from the LLM dropdown (top-right).\n"
            "• Wait ~1 minute if it was a quota/rate limit.\n"
            "• Click 🔑 in the header to add an API key for another provider "
            "(Groq is free at console.groq.com).\n"
            "• If running Ollama locally, ensure `ollama serve` is up."
        )
        return msg

    def _build_fallback_client(self, provider: str):
        """Build a transient LLMClient for the named provider if a key is available.
        Returns None if the provider has no key configured (silently skip)."""
        import os
        key_env = {
            "openai":    "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq":      "GROQ_API_KEY",
        }.get(provider)
        if not key_env:
            return None
        api_key = os.environ.get(key_env, "")
        if not api_key:
            return None
        # Re-use cached fallback clients to avoid repeated SDK setup overhead
        cache = getattr(self, "_fallback_clients", None)
        if cache is None:
            cache = {}
            self._fallback_clients = cache
        if provider in cache:
            return cache[provider]
        try:
            from llm_client import LLMClient
            client = LLMClient(provider=provider, api_key=api_key, temperature=0.0)
            # Fallback clients also must not self-switch provider — the agent's
            # failover loop owns cross-provider routing.
            try: client._allow_provider_switch = False
            except Exception: pass
            cache[provider] = client
            return client
        except Exception as exc:
            _log.debug("fallback client setup failed for %s: %s", provider, exc)
            cache[provider] = None
            return None

    def _try_one_provider(self, messages, tools, system_prompt, llm_client,
                          budget_t0=None):
        """Run the retry loop against a single provider. Returns response or None.
        If budget_t0 is provided, it is the SHARED start time for the aggregate
        retry budget across primary + fallback providers; otherwise we start a
        fresh budget here (legacy behaviour)."""
        last_err   = None
        if budget_t0 is None:
            budget_t0 = time.monotonic()

        # Ablation determinism: in deterministic mode every attempt uses
        # temperature 0 (no retry-temperature diversity), so a repeated run is
        # reproducible. Otherwise the normal diversity schedule is preserved.
        try:
            from ablation_config import ablation as _abl
            _temps = _abl.retry_temperatures(_RETRY_TEMPERATURES)
        except Exception:
            _temps = _RETRY_TEMPERATURES

        for attempt in range(_LLM_MAX_ATTEMPTS):
            # Check aggregate budget before each attempt
            elapsed = time.monotonic() - budget_t0
            if elapsed >= _LLM_RETRY_BUDGET_S:
                self._log(
                    f"[LLM] aggregate retry budget ({_LLM_RETRY_BUDGET_S:.0f}s) "
                    f"exhausted after {elapsed:.1f}s — giving up"
                )
                break

            retry_temp = _temps[min(attempt, len(_temps) - 1)]
            _orig_temp = getattr(llm_client, "temperature", 0.0)
            if attempt > 0 and retry_temp > 0:
                llm_client.temperature = retry_temp
            try:
                resp = llm_client.chat(
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                )
                if resp is not None:
                    # Accumulate token usage for eval_log (LangSmith token tracking)
                    _usg = resp.get("_usage", {})
                    if _usg:
                        try:
                            from evaluation import get_eval_log
                            log = get_eval_log()
                            sid = getattr(self, "_session_id", None)
                            if sid:
                                _tok_in  = int(_usg.get("tokens_in",  0))
                                _tok_out = int(_usg.get("tokens_out", 0))
                                _cost    = _estimate_cost(
                                    getattr(llm_client, "model", ""),
                                    _tok_in, _tok_out
                                )
                                log.record_tokens(sid, _tok_in, _tok_out, _cost)
                        except Exception:
                            pass
                    return resp
                # Surface the client's real underlying reason if it captured
                # one (e.g. "request too large", "429 quota") rather than the
                # opaque "provider returned None".
                _real = getattr(llm_client, "_last_error", None)
                last_err = (f"no usable response ({str(_real)[:120]})"
                            if _real else "provider returned None")
            except Exception as exc:
                last_err = str(exc)
                _log.warning("LLM attempt %d (%s) failed: %s",
                             attempt + 1, getattr(llm_client, "provider", "?"), last_err)
            # Record per-provider error for the user-visible message
            try:
                prov = getattr(llm_client, "provider", "?")
                # Keep ONLY the most recent error per provider (overwriting
                # earlier transient retries — the last one is the most
                # informative).
                if hasattr(self, "_last_provider_errors") and last_err:
                    self._last_provider_errors[prov] = last_err
            except Exception:
                pass
            finally:
                if attempt > 0 and retry_temp > 0:
                    llm_client.temperature = _orig_temp

            if attempt < _LLM_MAX_ATTEMPTS - 1:
                # Guard against tuple index out of range
                delay = _LLM_BACKOFF_S[attempt] if attempt < len(_LLM_BACKOFF_S) else _LLM_BACKOFF_S[-1]
                # Don't sleep past the budget
                remaining = _LLM_RETRY_BUDGET_S - (time.monotonic() - budget_t0)
                delay     = min(delay, max(remaining - 1.0, 0.0))
                if delay > 0:
                    self._log(f"[LLM] transient failure ({last_err}); retrying in {delay:.1f}s")
                    time.sleep(delay)
        return None

    def _last_resort_summary(self, *, reason: str) -> str:
        """Make a final tool-less LLM call to summarize what happened.

        Guarantees the user never sees an empty assistant message. If even
        this call fails, fall back to a deterministic summary derived from
        bridge state.
        """
        self._log(f"[Agent] invoking last-resort summary ({reason})")
        nudge = {
            "role": "user",
            "content": (
                "Summarize what you just did for the user in 2–4 sentences. "
                "Include key numbers (streams, unit ops, property package, any "
                "simulation results) and call out what remains undone, if "
                "anything. Do NOT call any more tools — reply with plain text."
            ),
        }
        try:
            resp = self._llm_chat_with_retry(
                messages=self._history + [nudge],
                tools=[],  # force text-only
                system_prompt=_build_system_prompt(self.bridge),  # no RAG on fallback
            )
            if resp and (resp.get("content") or "").strip():
                return resp["content"].strip()
        except Exception as exc:
            _log.warning("last-resort summary failed: %s", exc)

        # Deterministic fallback from bridge state.
        st = self.bridge.state
        parts = []
        if st.name:
            parts.append(f"Flowsheet: {st.name}")
        if st.property_package:
            parts.append(f"Property package: {st.property_package}")
        if st.streams:
            parts.append(f"{len(st.streams)} stream(s): {', '.join(st.streams[:6])}")
        if st.unit_ops:
            parts.append(f"{len(st.unit_ops)} unit op(s): {', '.join(st.unit_ops[:6])}")
        body = " — ".join(parts) if parts else "No flowsheet state recorded."
        return (f"I wasn't able to produce a narrative summary ({reason}). "
                f"Current state: {body}")

    def reset(self) -> None:
        self._history.clear()

    # ── interactive CLI ───────────────────────────────────────────────────────

    def run_cli(self) -> None:
        banner = textwrap.dedent(f"""
            ╔══════════════════════════════════════════════════════╗
            ║      DWSIM Agentic AI v2  –  Natural Language        ║
            ║  Provider : {self.llm.provider.upper():<10}  Model : {self.llm.model:<18}║
            ╠══════════════════════════════════════════════════════╣
            ║  Commands: 'reset' | 'sessions' | 'help' | 'quit'   ║
            ╚══════════════════════════════════════════════════════╝
        """).strip()
        print(f"\n{banner}\n")

        if self.bridge.state.name:
            print(f"  Flowsheet: {self.bridge.state.name}")
            if self.bridge.state.property_package:
                print(f"  Thermo:    {self.bridge.state.property_package}")
            print(f"  Streams:   {', '.join(self.bridge.state.streams)}")
            print(f"  Unit ops:  {', '.join(self.bridge.state.unit_ops) or 'none'}\n")
        else:
            print("  Tip: No flowsheet loaded. Example queries:")
            print('    "Find flowsheet files on my computer"')
            print('    "Load C:\\Users\\<your_username>\\Documents\\flowsheet.dwxmz"\n')

        while True:
            try:
                user = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user:
                continue
            cmd = user.lower()
            if cmd in ("quit", "exit", "q"):
                print("Goodbye!")
                break
            if cmd == "reset":
                self.reset()
                print("[History cleared]\n")
                continue
            if cmd == "sessions":
                r = self.bridge.list_loaded_flowsheets()
                print(f"Active: {r.get('active')}")
                for alias, path in r.get("loaded", {}).items():
                    print(f"  {alias}: {path}")
                print()
                continue
            if cmd == "help":
                self._print_help()
                continue

            try:
                reply = self.chat(user)
                if not (self.stream_output and self.verbose):
                    print(f"\nAgent: {reply}\n")
                else:
                    print()
            except KeyboardInterrupt:
                print("\n[Interrupted]\n")
            except Exception as exc:
                print(f"\n[Error] {exc}\n")

    # ── private helpers ───────────────────────────────────────────────────────

    def _list_flowsheet_templates(self) -> dict:
        """Return the curated template library.

        Includes the rationale meta block (sensitivity, known_failure_modes,
        variations, intent_template) when present — blueprint §"Adaptation
        hints". Falls back to bare topology summary for templates without meta.
        """
        try:
            from flowsheet_templates import list_templates, get_template_meta
            base = list_templates()
            for entry in base:
                meta = get_template_meta(entry["name"])
                if meta:
                    # Compact view — keep payload tight for LLM context budget
                    entry["has_rationale"] = True
                    entry["reference"]            = meta.get("reference", "")
                    entry["operating_regime"]     = meta.get("operating_regime", {})
                    entry["known_failure_modes"]  = meta.get("known_failure_modes", [])
                    entry["variations"]           = [v["name"] for v in (meta.get("variations") or [])]
                    entry["intent_template"]      = meta.get("intent_template", {})
            return {"success": True, "templates": base}
        except Exception as exc:
            return {"success": False, "error": f"templates unavailable: {exc}"}

    def _create_from_template(self, name: str,
                              overrides: Optional[dict] = None,
                              save_path: Optional[str] = None) -> dict:
        """Render a template topology, apply overrides, and build the flowsheet."""
        try:
            from flowsheet_templates import render_template, list_templates
        except Exception as exc:
            return {"success": False, "error": f"templates unavailable: {exc}"}

        topology = render_template(name, overrides or {})
        if topology is None:
            avail = [t["name"] for t in list_templates()]
            return {"success": False,
                    "code": "TEMPLATE_NOT_FOUND",
                    "error": f"Unknown template {name!r}",
                    "available": avail}
        if save_path:
            topology["save_path"] = save_path
        return self.bridge.create_flowsheet(topology)

    def _verified_set_stream_property(self, tag, property_name, value, unit="") -> dict:
        """Set a stream property AND verify by read-back. Returns success=False
        with WRITE_NOT_VERIFIED if DWSIM's state does not match the intended
        value — so the LLM cannot believe a silent failure succeeded."""
        try:
            from write_verification import verified_set_stream_property
            return verified_set_stream_property(self.bridge, tag, property_name, value, unit)
        except Exception:
            # Never let the verification layer break the core write
            return self.bridge.set_stream_property(tag, property_name, value, unit)

    def _verified_set_unit_op_property(self, tag, property_name, value, unit="") -> dict:
        """Set a unit-op property AND verify by read-back."""
        try:
            from write_verification import verified_set_unit_op_property
            return verified_set_unit_op_property(self.bridge, tag, property_name, value, unit)
        except Exception:
            return self.bridge.set_unit_op_property(tag, property_name, value, unit)

    def _verified_add_object(self, tag, type) -> dict:
        """Add an object AND verify it exists in the flowsheet afterward."""
        try:
            from write_verification import verified_add_object
            return verified_add_object(self.bridge, tag, type)
        except Exception:
            return self.bridge.add_object(tag, type)

    # ── Verified wrappers for the remaining state-changing tools ───────────
    # Every mutation now carries a verification verdict; a confirmed mismatch
    # is downgraded to WRITE_NOT_VERIFIED, and writes that genuinely cannot be
    # read back are flagged 'unverifiable' rather than reported as success.

    def _verified_set_column_property(self, tag, property_name, value) -> dict:
        try:
            from write_verification import verified_set_column_property
            return verified_set_column_property(self.bridge, tag, property_name, value)
        except Exception:
            return self.bridge.set_column_property(tag, property_name, value)

    def _verified_set_reactor_property(self, tag, property_name, value) -> dict:
        try:
            from write_verification import verified_set_reactor_property
            return verified_set_reactor_property(self.bridge, tag, property_name, value)
        except Exception:
            return self.bridge.set_reactor_property(tag, property_name, value)

    def _verified_set_energy_stream(self, stream_tag, duty_W) -> dict:
        try:
            from write_verification import verified_set_energy_stream
            return verified_set_energy_stream(self.bridge, stream_tag, float(duty_W))
        except Exception:
            return self.bridge.set_energy_stream(stream_tag, float(duty_W))

    def _verified_set_column_specs(self, column_tag, **kwargs) -> dict:
        try:
            from write_verification import verified_set_column_specs
            return verified_set_column_specs(self.bridge, column_tag, **kwargs)
        except Exception:
            return self.bridge.set_column_specs(column_tag, **kwargs)

    def _verified_set_binary_interaction_parameters(self, compound_1,
                                                    compound_2, **params) -> dict:
        try:
            from write_verification import verified_set_binary_interaction_parameters
            return verified_set_binary_interaction_parameters(
                self.bridge, compound_1, compound_2, **params)
        except Exception:
            return self.bridge.set_binary_interaction_parameters(
                compound_1, compound_2, **params)

    def _verified_delete_object(self, tag) -> dict:
        try:
            from write_verification import verified_delete_object
            return verified_delete_object(self.bridge, tag)
        except Exception:
            return self.bridge.delete_object(tag)

    def _verified_set_stream_flash_spec(self, stream_tag, spec="TP") -> dict:
        """Flash spec has no clean numeric read-back; wrap so the result still
        carries an explicit (unverifiable) verdict instead of a bare success."""
        try:
            from write_verification import verified_generic
            return verified_generic(
                self.bridge,
                lambda: self.bridge.set_stream_flash_spec(stream_tag, spec),
                describe=f"set_stream_flash_spec({stream_tag}, {spec})")
        except Exception:
            return self.bridge.set_stream_flash_spec(stream_tag, spec)

    def _verified_configure_heat_exchanger(self, hx_tag, **kwargs) -> dict:
        try:
            from write_verification import verified_generic
            def _verify():
                # Best-effort: confirm any numeric kwarg read-back via object props
                from write_verification import _read_via_getter, _canon_key
                for k, v in kwargs.items():
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    rb = _read_via_getter(self.bridge, "get_object_properties",
                                          hx_tag, k)
                    if rb is not None:
                        if abs(rb - fv) > max(abs(fv) * 0.01, 1e-6):
                            return False
                        return True
                return None
            return verified_generic(
                self.bridge,
                lambda: self.bridge.configure_heat_exchanger(hx_tag, **kwargs),
                describe=f"configure_heat_exchanger({hx_tag})",
                verify_callable=_verify)
        except Exception:
            return self.bridge.configure_heat_exchanger(hx_tag, **kwargs)

    def _verified_disconnect_streams(self, uo_tag, stream_tag) -> dict:
        """Disconnect has no simple positive read-back; wrap with topology
        validation so the result carries an explicit verdict."""
        try:
            from write_verification import verified_generic
            def _verify():
                try:
                    r = self.bridge.validate_topology()
                    if isinstance(r, dict) and r.get("success"):
                        return True
                except Exception:
                    pass
                return None
            return verified_generic(
                self.bridge,
                lambda: self.bridge.disconnect_streams(uo_tag, stream_tag),
                describe=f"disconnect_streams({uo_tag}, {stream_tag})",
                verify_callable=_verify)
        except Exception:
            return self.bridge.disconnect_streams(uo_tag, stream_tag)

    def _set_stream_composition_wrapper(self, tag: str = "", **kwargs) -> dict:
        """
        Handle multiple LLM calling styles:
          1. fractions={"Methanol": 0.8, "Water": 0.2}      (step-by-step prompt)
          2. compositions={"Methanol": 0.8, "Water": 0.2}   (classic key)
          3. composition={"Methanol": 0.8, "Water": 0.2}    (typo key)
          4. Methanol=0.8, Water=0.2                         (flat kwargs)
        """
        compositions = (kwargs.get("fractions")
                        or kwargs.get("compositions")
                        or kwargs.get("composition"))
        if compositions is None:
            # Flat kwargs — every key that is not a known meta-key is a compound
            _meta = {"tag", "compositions", "composition", "fractions"}
            compositions = {k: v for k, v in kwargs.items() if k not in _meta}
        if not tag:
            return {"success": False, "error": "tag is required"}
        # Ensure values are floats (LLM sometimes sends strings)
        if isinstance(compositions, dict):
            compositions = {k: float(v) for k, v in compositions.items()}
        # Verify the composition write by read-back (catches silent failures)
        try:
            from write_verification import verified_set_stream_composition
            return verified_set_stream_composition(self.bridge, tag, compositions)
        except Exception:
            return self.bridge.set_stream_composition(tag, compositions)

    # ── Argument type coercion — prevents 30-40% of LLM type mistakes ──────────
    # LLMs frequently pass numerics as strings ("80" instead of 80).
    # This map defines expected types per argument name across all tools.
    _ARG_COERCE: Dict[str, type] = {
        "value":          float,
        "lower_bound":    float,
        "upper_bound":    float,
        "tolerance":      float,
        "duty_W":         float,
        "min_approach_temp_C": float,
        "T_guess_C":      float,
        "P_guess_bar":    float,
        "from_port":      int,
        "to_port":        int,
        "max_iterations": int,
        "population_size":int,
        "n_samples":      int,
        "n_initial":      int,
        "max_iter":       int,
        "xi":             float,
        "minimize":       bool,
        "seed":           int,
    }

    # Fields that must be lists of strings. If the LLM sends a plain string
    # (e.g. "CH4, CO2, H2O" or '["CH4","H2O"]'), coerce it before the tool call
    # so the Anthropic API validator never sees a wrong type and raises
    # "compounds must be a list of names".
    _ARG_COERCE_LIST = {"compounds", "reactions", "values"}

    @staticmethod
    def _coerce_to_str_list(v) -> list:
        """Convert any LLM-provided value to a list of strings."""
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                try:
                    import json as _j
                    parsed = _j.loads(s)
                    if isinstance(parsed, list):
                        return [str(x) for x in parsed]
                except Exception:
                    pass
            # Comma-separated string: "CH4, CO2, H2O"
            return [x.strip().strip("\"'") for x in s.split(",") if x.strip()]
        return [str(v)]

    def _coerce_arguments(self, name: str, arguments: dict) -> dict:
        """Coerce argument types to match expected signatures, fixing common LLM mistakes."""
        coerced = {}
        for k, v in arguments.items():
            # List-of-strings fields: coerce before schema validation
            if k in self._ARG_COERCE_LIST and v is not None:
                coerced[k] = self._coerce_to_str_list(v)
                continue
            expected = self._ARG_COERCE.get(k)
            if expected is None or v is None:
                coerced[k] = v
                continue
            try:
                if expected is bool:
                    if isinstance(v, str):
                        coerced[k] = v.lower() not in ("false", "0", "no", "")
                    else:
                        coerced[k] = bool(v)
                elif expected is float:
                    coerced[k] = float(v)
                elif expected is int:
                    coerced[k] = int(float(v))
                else:
                    coerced[k] = v
            except (ValueError, TypeError):
                coerced[k] = v   # keep original if coercion fails; let bridge handle it
        return coerced

    # ── Structured error hints ────────────────────────────────────────────────
    # Maps (tool_name, error_keyword) → actionable repair instruction.
    # Appended to tool error responses as a "hint" field so the LLM gets a
    # specific fix rather than retrying blindly.
    _ERROR_HINTS: Dict[str, Dict[str, str]] = {
        "add_object": {
            "no active flowsheet": (
                "Call new_flowsheet (or build_flowsheet_atomic) before add_object. "
                "new_flowsheet must complete successfully before any add_object calls."
            ),
            "unknown object type": (
                "Valid types: MaterialStream, EnergyStream, Heater, Cooler, Pump, "
                "Compressor, Expander, Valve, Mixer, Splitter, Separator, HeatExchanger, "
                "ConversionReactor, GibbsReactor, EquilibriumReactor, CSTR, PFR, "
                "DistillationColumn, ShortcutColumn, AbsorptionColumn."
            ),
            "addflowsheetobject": (
                "DWSIM rejected this object type. Check exact spelling — type names "
                "are case-sensitive. Use list_simulation_objects after adding some "
                "objects to verify which were accepted."
            ),
        },
        "connect_streams": {
            "not found": (
                "One of the tags doesn't exist yet. Verify all add_object calls "
                "succeeded before connecting. Call list_simulation_objects to see "
                "what's currently in the flowsheet."
            ),
            "port": (
                "Port rules: material inlet → to_port=0; material outlet → from_port=0; "
                "second outlet (Splitter/Flash) → from_port=1; "
                "energy stream (Heater/Pump) → to_port=1."
            ),
        },
        "set_stream_property": {
            "not found": (
                "Stream tag not found. Use list_simulation_objects to get exact tag names. "
                "Tags are case-sensitive."
            ),
            "no flowsheet": (
                "No flowsheet is loaded. Call load_flowsheet with a valid path first, "
                "or use build_flowsheet_atomic to create a new one."
            ),
        },
        "set_unit_op_property": {
            "not found": (
                "Unit op tag not found. Use list_simulation_objects to check exact tags."
            ),
            "attribute": (
                "Property name not recognised. Call get_object_properties on this tag "
                "to see available property names for this unit op type."
            ),
        },
        "save_and_solve": {
            "no active flowsheet": (
                "No flowsheet to solve. Call new_flowsheet first, add objects, "
                "connect streams, set feed conditions, then save_and_solve."
            ),
            "no objects": (
                "Flowsheet has no objects. Add streams and unit ops with add_object "
                "before calling save_and_solve."
            ),
            "not converge": (
                "Solver did not converge. Try: (1) check property package matches "
                "the chemistry; (2) verify all feed streams have T, P, and flow set; "
                "(3) for recycle loops, add an initialize_recycle call with a good "
                "initial guess; (4) for columns, start with a higher reflux ratio."
            ),
        },
        "set_stream_composition": {
            "not found": "Stream tag not found. Check exact tag spelling with list_simulation_objects.",
            "sum": (
                "Mole fractions must sum to 1.0. "
                "Divide each value by the total if they don't sum correctly."
            ),
            "unknown compound": (
                "Compound name doesn't match the flowsheet's compound list. "
                "Call list_simulation_objects or new_flowsheet to see which compounds "
                "are registered. DWSIM compound names are case-sensitive."
            ),
        },
        "new_flowsheet": {
            "compound": (
                "Compound name not in DWSIM database. Call get_available_compounds "
                "with a search term to find the exact name."
            ),
            "property package": (
                "Property package name not recognised. Common ones: "
                "'Peng-Robinson (PR)', 'NRTL', 'SRK', "
                "'Steam Tables (IAPWS-IF97)', \"Raoult's Law\"."
            ),
        },
        "build_flowsheet_atomic": {
            "compound": (
                "One or more compound names not in DWSIM database. "
                "Call get_available_compounds('methane') to find exact spelling."
            ),
            "unknown type": (
                "Invalid object type in spec.objects. "
                "Valid types: MaterialStream, EnergyStream, Heater, Cooler, Pump, "
                "ConversionReactor, GibbsReactor, Mixer, Splitter, Separator, "
                "DistillationColumn, etc."
            ),
            "unknown tag": (
                "A connection references a tag not in spec.objects. "
                "Make sure every from_tag and to_tag appears in the objects list."
            ),
            "duplicate": (
                "Two objects in spec.objects have the same tag. Every tag must be unique."
            ),
        },
        # ── Reactor / reaction setup ──────────────────────────────────────────
        "setup_reaction": {
            "not found": (
                "Reactor tag not found. Call list_simulation_objects to verify the "
                "reactor was added. Reaction setup only works on ConversionReactor / "
                "EquilibriumReactor / KineticReactor — Gibbs reactors do not need it."
            ),
            "stoichiometry": (
                "Stoichiometry must be a dict {compound: coeff} with NEGATIVE values "
                "for reactants and POSITIVE for products. Example for SMR: "
                "{'Methane': -1, 'Water': -1, 'Carbon monoxide': 1, 'Hydrogen': 3}."
            ),
            "compound": (
                "A compound in stoichiometry is not in the flowsheet's compound list. "
                "Add it via new_flowsheet's compounds list, or check spelling."
            ),
            "conversion": (
                "Conversion must be in [0,1]. For pseudo-equilibrium guesses, "
                "use 0.6-0.9; for kinetic, use a kinetic reactor type instead."
            ),
        },
        # ── Distillation columns ──────────────────────────────────────────────
        "initialize_distillation": {
            "not found": (
                "Column tag not found. Check the tag with list_simulation_objects. "
                "initialize_distillation only works on DistillationColumn / "
                "AbsorptionColumn / RefluxedAbsorber / ReboiledAbsorber."
            ),
            "not converge": (
                "Column did not converge. Try: (1) widen T_top_C / T_bot_C estimates "
                "by ±20°C; (2) drop reflux_ratio by 20% on retry; "
                "(3) switch algorithm to 'BO' for wide-boiling or 'SR' for non-ideal mixes."
            ),
        },
        "set_column_property": {
            "not found": "Column tag not found. Check spelling with list_simulation_objects.",
            "attribute": (
                "Property name not recognised. Common: 'reflux_ratio', 'n_stages', "
                "'feed_stage', 'condenser_type', 'distillate_rate_mol_s'. "
                "Use get_column_properties to inspect the column first."
            ),
        },
        "set_column_specs": {
            "not found": "Column tag not found. Check spelling with list_simulation_objects.",
        },
        # ── Optimisation tools ────────────────────────────────────────────────
        "optimize_parameter": {
            "not found": (
                "vary_tag or observe_tag not found in flowsheet. "
                "Call list_simulation_objects to see what's available."
            ),
            "bounds": (
                "Lower bound must be < upper bound, and both must be physically valid. "
                "Check that the parameter you're optimising is settable."
            ),
            "not converge": (
                "One or more inner simulation evaluations failed. "
                "First confirm save_and_solve succeeds at the midpoint of your bounds."
            ),
        },
        "bayesian_optimize": {
            "not converge": (
                "Inner simulations failed during BO. Run save_and_solve at the lower "
                "bound of each variable first to confirm the design is feasible."
            ),
            "variables": (
                "variables must be a list of dicts: "
                "[{tag, property, unit, lower, upper}, ...]. Bounds must be numeric."
            ),
        },
        "optimize_constrained": {
            "constraint": (
                "constraint format: {tag, property, unit, operator ('>=' or '<='), value}. "
                "Constraints are penalised, not strictly enforced — set tight bounds "
                "if you need hard limits."
            ),
        },
        # ── Recycle initialisation ────────────────────────────────────────────
        "initialize_recycle": {
            "not found": (
                "recycle_tag must point to a MaterialStream that closes a recycle loop. "
                "If you haven't added a Recycle unit op (OT_Recycle), add one with "
                "add_object first."
            ),
            "composition": (
                "composition dict must sum to ~1.0 and reference compounds in the "
                "flowsheet's compound list."
            ),
        },
        # ── Property package & compounds ──────────────────────────────────────
        "set_property_package": {
            "not recognised": (
                "Invalid property package name. Use get_available_property_packages "
                "to list valid options."
            ),
        },
        "get_available_compounds": {
            "not found": (
                "Search returned no matches. Try a partial name or CAS number, e.g. "
                "'methan' instead of 'methane', or '74-82-8' (CAS)."
            ),
        },
        # ── Energy streams ────────────────────────────────────────────────────
        "set_energy_stream": {
            "not found": (
                "Energy stream tag not found. Energy streams must be added with "
                "add_object {tag, type:'EnergyStream'} and connected to a unit op "
                "(Heater/Cooler/Pump/Compressor) before setting duty."
            ),
        },
        # ── Compound separator (PSA / membrane surrogate) ─────────────────────
        "configure_heat_exchanger": {
            "not found": "HX tag not found. Use list_simulation_objects to verify.",
            "mode": (
                "Invalid mode. Valid: 'CalcTempHotOut', 'CalcTempColdOut', "
                "'CalcBothTemp', 'CalcBothTemp_UA', 'CalcArea', 'PinchPoint', etc."
            ),
        },
        # ── Intent declaration ────────────────────────────────────────────────
        "declare_intent": {
            "parse": (
                "Intent payload malformed. feed_streams and product_streams must be "
                "lists of strings; targets must be a list of dicts each with a 'kind' "
                "field ('product_purity' | 'max_impurity' | 'min_yield' | 'unit_setpoint')."
            ),
        },
        # ── Literature comparison ─────────────────────────────────────────────
        "compare_to_literature": {
            "no reference": (
                "No literature reference for that process key. Use 'biogas_smr_h2' "
                "(Ullah et al. 2025), 'methanol_synthesis', or 'ammonia_synthesis'."
            ),
            "no matching": (
                "Simulation results don't contain the property keys the reference "
                "expects. Run save_and_solve first to populate stream data."
            ),
        },
    }

    def _enrich_error_with_hint(self, tool_name: str, result: dict) -> dict:
        """Append a hint field to a failed tool result based on known error patterns."""
        if result.get("success") or "hint" in result:
            return result
        error_text = str(result.get("error") or "").lower()
        hints = self._ERROR_HINTS.get(tool_name, {})
        for keyword, hint in hints.items():
            if keyword.lower() in error_text:
                result = dict(result)  # shallow copy — don't mutate original
                result["hint"] = hint
                break
        return result

    def _validate_property_range(self, tool_name: str, arguments: dict) -> Optional[str]:
        """Return an error string if a stream or unit-op property value is outside
        its physical bounds, or None if the value is acceptable.

        Converts to SI internally to handle all unit variants uniformly.
        Mirrors the conversion logic in DWSIMBridge._convert_to_si().
        """
        try:
            if tool_name == "set_stream_property":
                prop = arguments.get("property_name", "")
                raw  = arguments.get("value")
                unit = str(arguments.get("unit") or "").strip().lower()
                if raw is None or prop not in _STREAM_PROP_SI_LIMITS:
                    return None
                v = float(raw)

                # Convert to SI
                if prop == "temperature":
                    if unit in ("c", "°c", "celsius"):
                        si = v + 273.15
                    elif unit in ("f", "°f", "fahrenheit"):
                        si = (v - 32) * 5 / 9 + 273.15
                    else:
                        si = v          # assume K
                elif prop == "pressure":
                    _P = {"bar": 1e5, "kpa": 1e3, "atm": 101325.0,
                          "psi": 6894.76, "mpa": 1e6, "barg": 1e5}
                    if unit in _P:
                        si = v * _P[unit]
                    elif unit == "barg":
                        si = (v + 1.01325) * 1e5
                    else:
                        si = v          # assume Pa
                else:
                    si = v              # flows and VF already in SI for this check

                lo, hi = _STREAM_PROP_SI_LIMITS[prop]
                if si < lo:
                    return (f"set_stream_property: {prop}={v} {unit or '(SI)'} converts to "
                            f"{si:.3g} which is below minimum {lo} (SI).")
                if si > hi:
                    return (f"set_stream_property: {prop}={v} {unit or '(SI)'} converts to "
                            f"{si:.3g} which exceeds safety limit {hi} (SI). "
                            f"Max industrial values: T≤2500 K, P≤1500 bar.")

            elif tool_name == "set_unit_op_property":
                prop  = str(arguments.get("property_name") or "").lower()
                raw   = arguments.get("value")
                unit  = str(arguments.get("unit") or "").strip().lower()
                if raw is None:
                    return None
                try:
                    v = float(raw)
                except (TypeError, ValueError):
                    return None         # non-numeric value — let bridge handle it

                # Efficiency / conversion are dimensionless fractions [0, 1]
                if any(k in prop for k in ("efficiency", "conversion")):
                    if not (0.0 <= v <= 1.0):
                        return (f"set_unit_op_property: {prop}={v} must be in [0, 1] "
                                f"(it is a dimensionless fraction).")

                # Outlet temperature (K by default)
                if "outlettemperature" in prop or "outlet_temperature" in prop:
                    if unit in ("c", "°c"):
                        si = v + 273.15
                    elif unit in ("f", "°f"):
                        si = (v - 32) * 5 / 9 + 273.15
                    else:
                        si = v
                    if si < 0:
                        return f"set_unit_op_property: OutletTemperature {v} {unit or 'K'} is below absolute zero."
                    if si > 2500:
                        return (f"set_unit_op_property: OutletTemperature {si:.1f} K "
                                f"exceeds safety limit (2500 K).")
        except Exception:
            pass   # validation must never crash the agent
        return None

    def _run_tool(self, name: str, arguments) -> dict:
        if not isinstance(arguments, dict):
            arguments = {}

        # Coerce argument types before dispatching to bridge
        arguments = self._coerce_arguments(name, arguments)

        # Gap 3: validate physical property ranges before the .NET bridge sees them
        range_error = self._validate_property_range(name, arguments)
        if range_error is not None:
            self._log(f"[RangeGuard] Blocked '{name}': {range_error}")
            return {"success": False, "code": "RANGE_VIOLATION", "error": range_error}

        # Guard: check tool call ordering preconditions
        precond_error = _check_tool_preconditions(name, self.bridge)
        if precond_error is not None:
            self._log(f"[Precondition] Blocked '{name}': {precond_error['error'][:80]}")
            return precond_error

        self._log(f"\n  ┌─ Tool: {name}")
        self._log(f"  │  Args: {json.dumps(arguments, default=str)[:200]}")

        t0 = time.monotonic()
        if name not in self._tools:
            result: dict = {"success": False,
                            "code": "UNKNOWN_TOOL",
                            "error": f"Unknown tool: {name}"}
        else:
            # Per-tool env override: AGENT_TIMEOUT_<TOOL_NAME>=120
            _env_override = os.getenv(f"AGENT_TIMEOUT_{name.upper()}")
            if _env_override:
                try:
                    timeout_s = float(_env_override)
                except ValueError:
                    timeout_s = _TOOL_TIMEOUT_S.get(name, _DEFAULT_TOOL_TIMEOUT_S) * _TOOL_TIMEOUT_MULT
            else:
                timeout_s = _TOOL_TIMEOUT_S.get(name, _DEFAULT_TOOL_TIMEOUT_S) * _TOOL_TIMEOUT_MULT
            fn = self._tools[name]

            def _invoke():
                return fn(**arguments)

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_invoke)
                    try:
                        result = future.result(timeout=timeout_s)
                    except concurrent.futures.TimeoutError:
                        # The worker thread can't be cancelled (it may hold
                        # the .NET runtime); leave it running and surface
                        # the timeout so the agent can decide what to do.
                        elapsed = time.monotonic() - t0
                        result = {"success": False,
                                  "code": "TOOL_TIMEOUT",
                                  "error": (f"{name} exceeded {timeout_s:.0f}s "
                                            f"(elapsed {elapsed:.1f}s)")}
            except TypeError as exc:
                result = {"success": False,
                          "code": "BAD_ARGS",
                          "error": f"Wrong arguments for {name}: {exc}"}
            except Exception as exc:
                result = {"success": False,
                          "code": "TOOL_ERROR",
                          "error": str(exc)}

            if not isinstance(result, dict):
                # BUG-5 fix: use "result" key (not "value") so downstream
                # code that iterates dict keys finds a consistent shape.
                # Wrap bool/str/None returns from bridge methods gracefully.
                result = {"success": True, "result": result,
                          "message": str(result) if result is not None else "OK"}

            # ── Auto-correction: if run_simulation returned unconverged
            # streams, attempt non-destructive fixes and re-run.
            if (name == "run_simulation"
                    and self._auto_correct
                    and result.get("success")
                    and not result.get("convergence_check", {}).get("all_converged", True)):
                try:
                    from auto_correct import AutoCorrector
                    corrector = AutoCorrector(self.bridge)
                    result = corrector.attempt_fixes(result)
                    if result.get("auto_corrected"):
                        self._log(
                            f"  [AutoCorrect] Converged after fixes: "
                            f"{result.get('fixes_applied')}"
                        )
                    elif result.get("fixes_applied"):
                        self._log(
                            f"  [AutoCorrect] {len(result['fixes_applied'])} fix(es) tried, "
                            f"still not fully converged"
                        )
                except Exception as _ac_exc:
                    _log.warning("auto_correct failed: %s", _ac_exc)

        status  = "✓" if result.get("success") else "✗"
        summary = json.dumps(result, default=str)
        self._log(f"  └─ {status}  {summary[:400]}")

        # Accumulate per-turn timings for benchmarking (D1).
        try:
            self._turn_tool_timings.append({
                "name": name,
                "success": bool(result.get("success")),
                "elapsed_s": round(time.monotonic() - t0, 3),
            })
        except Exception:
            pass

        if self.on_tool_call:
            try:
                self.on_tool_call(name, arguments, result)
            except Exception:
                pass
        return result

    def _stream_print(self, prefix: str, text: str) -> None:
        sys.stdout.write(prefix)
        sys.stdout.flush()
        words = text.split(" ")
        for i, word in enumerate(words):
            sys.stdout.write(word)
            if i < len(words) - 1:
                sys.stdout.write(" ")
            sys.stdout.flush()
            if self.on_token:
                try:
                    self.on_token(word + (" " if i < len(words) - 1 else ""))
                except Exception:
                    pass
        sys.stdout.write("\n")
        sys.stdout.flush()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    @staticmethod
    def _print_help() -> None:
        print(textwrap.dedent("""
            Example queries (v2)
            ────────────────────
            Load & inspect:
              "Find all flowsheet files on my computer"
              "Load C:\\Users\\<your_username>\\Documents\\flowsheet.dwxmz"
              "What thermodynamic model is this flowsheet using?"
              "Did all streams converge after the last simulation?"

            Read & modify:
              "What is the temperature and pressure of Water in?"
              "Set Methanol In temperature to 100 C then run"
              "Set Methanol In to 80% methanol 20% water and run"

            Optimisation:
              "Find the water flow between 5000-30000 kg/h that minimises
               methanol outlet temperature"

            Parametric study:
              "How does Methanol out temperature change as Water in mass flow
               varies from 5000 to 30000 kg/h in steps of 5000?"

            Multi-flowsheet:
              "Load reactor.dwxmz as 'reactor' and compare with HE"

            Utility:
              "Save the modified flowsheet"
              reset   — clear conversation history
              sessions — list loaded flowsheets
        """).strip())
