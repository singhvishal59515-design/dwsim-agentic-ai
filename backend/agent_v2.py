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
import re as _re
import sys
import textwrap
import time
from typing import Any, Callable, Dict, List, Optional

from dwsim_bridge_v2 import DWSIMBridgeV2
from llm_client      import LLMClient
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
    """Recursively scan tool-result data for prompt-injection patterns.
    Strings that match are replaced with a warning token so they never
    instruct the model.  Depth-limited to avoid stack overflow on deeply
    nested DWSIM data structures.
    """
    if _depth > 8:
        return obj
    if isinstance(obj, str):
        if _INJECTION_PATTERNS.search(obj):
            _log.warning("Prompt-injection pattern detected in tool result — blocked.")
            return "[CONTENT_BLOCKED: possible prompt injection in tool output]"
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_llm(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
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
_DEFAULT_TOOL_TIMEOUT_S = 60.0
_TOOL_TIMEOUT_S = {
    "run_simulation":              180.0,
    "load_flowsheet":              180.0,
    "create_flowsheet":            240.0,  # disabled but keep timeout entry
    "new_flowsheet":               60.0,
    "add_object":                  30.0,
    "save_and_solve":              240.0,  # save + DWSIM solve
    "get_available_compounds":      30.0,
    "get_available_property_packages": 30.0,
    "search_knowledge":             20.0,
    "find_flowsheets":              30.0,
    # New tools — generous timeouts for optimisation-based calls
    "monte_carlo_study":          1800.0,  # 100 samples × ~10s each = up to 1000s; cap 30min
    "bayesian_optimize":           900.0,  # 25 evals × ~10s each + GP fit ≈ 250–500s; allow 15min
    "optimize_multivar":           600.0,  # DE: 100 iter × ~2s each = up to 200s; allow 600s
    "optimize_parameter":          300.0,  # scalar: 50 iter
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

# History limits.
# Token-aware trimming: 1 token ≈ 4 chars (conservative for mixed content).
# Groq Llama-3.3-70b: 32k context. Reserve ~8k for system prompt + tools schema
# + response headroom → ~24k tokens for history = ~96k chars.
# For safety, trim at 80k chars (~20k tokens) to stay well under all providers.
_MAX_HISTORY_MESSAGES = 30          # hard message count cap (fast path)
_MAX_HISTORY_CHARS    = 80_000      # soft token cap — trim oldest if exceeded


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
    "monte_carlo_study", "get_phase_results",
}


def _check_tool_preconditions(name: str, bridge) -> Optional[dict]:
    """
    Returns an error dict if tool `name` cannot run given bridge state,
    or None if preconditions are met.
    Keeps the check lightweight — no DWSIM calls, only bridge state inspection.
    """
    if name not in _REQUIRES_FLOWSHEET:
        return None  # tool has no preconditions

    # Check if a flowsheet is active
    has_flowsheet = False
    try:
        fs = bridge.state
        has_flowsheet = bool(getattr(fs, "active_alias", None) or
                             getattr(fs, "flowsheet_name", None) or
                             getattr(fs, "loaded_flowsheets", {}))
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

    # Final safety net: if still too large, truncate JSON representation
    try:
        serialised = json.dumps(result)
        if len(serialised) > _MAX_RESULT_CHARS:
            truncated = serialised[:_MAX_RESULT_CHARS]
            return {
                "success":   result.get("success"),
                "error":     result.get("error"),
                "_truncated": True,
                "_note":     f"Result truncated from {len(serialised)} chars to {_MAX_RESULT_CHARS}.",
                "_preview":  truncated,
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
        "role":    "user",
        "content": summary_text,
    }
    # Insert summary + a brief acknowledgement so the model processes it
    ack_msg = {
        "role":    "assistant",
        "content": "[Acknowledged. Continuing with full awareness of earlier session context.]",
    }

    new_history = [summary_msg, ack_msg] + list(to_keep)

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

except Exception:
    def _prop_db_lookup(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_pair(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_psat(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}
    def _prop_db_search(*a, **kw):
        return {"success": False, "error": "Property database unavailable."}


# ─────────────────────────────────────────────────────────────────────────────
BASE_SYSTEM_PROMPT = textwrap.dedent("""
    You are an expert chemical process simulation engineer with deep knowledge
    of DWSIM process simulation software and thermodynamics.

    You control DWSIM through Python tool functions. Follow these rules:

    CRITICAL TOOL CALLING RULES
    ────────────────────────────
    • Tools with NO parameters must be called with an EMPTY argument object {}.
      Never pass null. Affected tools: find_flowsheets, list_simulation_objects,
      run_simulation, get_simulation_results, list_loaded_flowsheets,
      get_property_package.

    • NEVER guess file paths. When you need a flowsheet path, call
      find_flowsheets first (with {}). Use ONLY paths it returns.

    STANDARD WORKFLOW
    ─────────────────
    1. find_flowsheets {}           ← discover real paths on disk
    2. load_flowsheet path          ← loads AND auto-solves; reports property package
    3. (list_simulation_objects optional — see FLOWSHEET STATE below)
    4. Read / modify properties as requested
    5. run_simulation {}            ← always call after any parameter change
    6. get_simulation_results {}    ← read updated stream values
    7. Report with units: °C, bar, kg/h, mole fractions

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

    AI FLOWSHEET BUILDING (step-by-step)
    ─────────────────────────────────────
    When a user asks to BUILD or CREATE a new flowsheet, do NOT use create_flowsheet.
    NEVER call create_flowsheet — it is disabled. Build step-by-step using these tools:

    STEP 1  new_flowsheet {name, compounds, property_package}
            → Call EXACTLY ONCE. Creates a fresh blank simulation.
            → If a previous flowsheet already exists, new_flowsheet REPLACES it.
            → Do NOT skip this step even if a flowsheet was loaded/built before.

    STEP 2  add_object {tag, type}   ← repeat for EVERY stream and unit op
            → Add all MaterialStreams first, then EnergyStreams, then unit ops.

    STEP 3  connect_streams {from_tag, to_tag, from_port, to_port}
            → Port rules:
               Material inlet to unit op   → to_port=0
               Material outlet from unit op → from_port=0
               Second material outlet       → from_port=1
               Energy stream to unit op    → to_port=1  (Heater/Cooler/Pump)
               Second feed (Mixer)         → to_port=1

    STEP 4  set_stream_property {tag, property_name, value, unit}
            → property_name: "temperature", "pressure", "molarflow", "massflow"
            → Set T, P, flow on every FEED (inlet) stream.

    STEP 5  set_stream_composition {tag, fractions}
            → fractions must sum to 1.0: {"Methanol": 0.6, "Water": 0.4}

    STEP 6  set_unit_op_property {tag, property_name, value}
            → Heater/Cooler: "OutletTemperature" (K), "DeltaP" (Pa)
            → Pump: "OutletPressure" (Pa), "AdiabaticEfficiency" (0-1)

    STEP 7  save_and_solve {}
            → Saves to disk, runs DWSIM solver, returns stream_results inline.
            → Do NOT call run_simulation or get_simulation_results after this.

    RULES:
    • NEVER call create_flowsheet — it is disabled and will return an error.
    • Call new_flowsheet ONCE at the START of each build — then add objects.
    • Do NOT call new_flowsheet again mid-build (objects are already queued).
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
    • All 7 silent failure modes are now caught before or immediately after solve:
        SF-01 fixed in bridge    SF-02 blocked pre-solve   SF-03 fixed in bridge
        SF-04 rejected pre-set   SF-05 auto-corrected       SF-06 blocked pre-solve
        SF-07 blocked pre-solve

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

    PHASE-SPECIFIC RESULTS (v5)
    ────────────────────────────
    • After run_simulation, use get_phase_results to read vapor/liquid split separately.
    • phase values: 'vapor', 'liquid', 'liquid1', 'liquid2', 'solid', 'overall'.
    • Useful after flash drums, condensers, and distillation columns.

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

    PARAMETRIC STUDY
    ────────────────
    • Use parametric_study when the user asks how one output varies with an input.
    • Present the result as a formatted table with headers.

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

    LLM-DRIVEN OPTIMISATION
    ────────────────────────
    • For optimisation tasks, prefer the built-in optimize_parameter tool
      which uses SciPy bounded minimisation automatically.
    • For complex multi-variable or constrained optimisation that optimize_parameter
      cannot handle, use parametric_study iteratively to explore and narrow
      the search space, reporting each iteration to the user.
    • Always state the objective, decision variable, bounds, and result clearly.
    • After optimisation, call get_simulation_results to confirm the final state.

    ENGINEERING REPORTING
    ──────────────────────
    • Report temperature in both K and °C.
    • Report pressure in both Pa and bar.
    • If convergence_errors is non-empty, warn the user clearly.
    • Never invent property values — only report what tools return.
    • If a tool returns success=false, read the error and try a corrected call.

    MANDATORY WORKFLOW — FOLLOW THIS ORDER
    ───────────────────────────────────────
    Before creating ANY simulation, you MUST:
    1. Call lookup_compound_properties for each key compound to verify
       Tc, Pc, omega, and boiling point — never guess these values.
    2. Call lookup_binary_parameters for polar component pairs (alcohols,
       ketones, acids + water) to get NRTL/UNIQUAC BIPs.
    3. Choose the correct property package based on lookup results:
       - Hydrocarbons / gas mixtures → Peng-Robinson (PR)
       - Polar organics + water → NRTL or UNIQUAC
       - Pure water / steam → Steam Tables (IAPWS-IF97)
       - Refrigerants → CoolProp or PR
       - Acid gases (CO2, H2S) in hydrocarbons → PR with kij ≠ 0
    4. Then proceed: new_flowsheet → add_object → connect_streams →
       set_stream_property → save_and_solve.

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


def _build_system_prompt(bridge: DWSIMBridgeV2,
                         state_delta: Optional[str] = None,
                         user_message: str = "") -> str:
    context = bridge.state.context_summary()
    prompt  = BASE_SYSTEM_PROMPT.replace("{flowsheet_context}", context)

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
    if user_message and len(user_message.strip()) > 10:
        try:
            if _kb_instance is not None:
                _kb = _kb_instance
            else:
                from knowledge_base import KnowledgeBase as _KB
                _kb = _KB()
            kb_res = _kb.search(user_message, top_k=4)
            chunks = kb_res.get("results", []) if kb_res.get("success") else []
            # Raise threshold slightly to 0.5 BM25 score (was 0.15 TF-IDF)
            # BM25 scores are higher in magnitude — 0.5 filters noise effectively
            chunks = [c for c in chunks if c.get("relevance_score", 0) > 0.5]
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
            "set_stream_property":    lambda tag, property_name, value, unit="":
                                          self.bridge.set_stream_property(
                                              tag, property_name, value, unit),
            "set_stream_composition": self._set_stream_composition_wrapper,
            "get_object_properties":  lambda tag:
                                          self.bridge.get_object_properties(tag),
            "set_unit_op_property":   lambda tag, property_name, value:
                                          self.bridge.set_unit_op_property(
                                              tag, property_name, value),
            "run_simulation":         self.bridge.run_simulation,
            "get_simulation_results": self.bridge.get_simulation_results,
            "get_property_package":   self.bridge.get_property_package,
            "check_convergence":      self.bridge.check_convergence,
            "validate_feed_specs":    self.bridge.validate_feed_specs,
            "parametric_study":       self._parametric_study_with_progress,
            "optimize_multivar":      lambda **kw:
                                          self.bridge.optimize_multivar(**kw),
            "bayesian_optimize":      self._bayesian_optimize_with_progress,
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
            "set_column_property":    lambda tag, property_name, value:
                                          self.bridge.set_column_property(
                                              tag, property_name, value),
            "get_reactor_properties": lambda tag:
                                          self.bridge.get_reactor_properties(tag),
            "set_reactor_property":   lambda tag, property_name, value:
                                          self.bridge.set_reactor_property(
                                              tag, property_name, value),
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
            "add_object":             lambda tag, type:
                                          self.bridge.add_object(tag, type),
            "save_and_solve":         lambda: self.bridge.save_and_solve(),
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
            "set_binary_interaction_parameters": lambda compound_1, compound_2,
                                                        **params:
                                          self.bridge.set_binary_interaction_parameters(
                                              compound_1, compound_2, **params),
            "configure_heat_exchanger": lambda hx_tag, **kwargs:
                                          self.bridge.configure_heat_exchanger(
                                              hx_tag, **kwargs),
            "set_stream_flash_spec":  lambda stream_tag, spec="TP":
                                          self.bridge.set_stream_flash_spec(stream_tag, spec),
            "get_energy_stream":      lambda stream_tag:
                                          self.bridge.get_energy_stream(stream_tag),
            "set_energy_stream":      lambda stream_tag, duty_W:
                                          self.bridge.set_energy_stream(stream_tag, float(duty_W)),
            "delete_object":          lambda tag:
                                          self.bridge.delete_object(tag),
            "disconnect_streams":     lambda uo_tag, stream_tag:
                                          self.bridge.disconnect_streams(uo_tag, stream_tag),
            "connect_streams":        lambda from_tag, to_tag, from_port=0, to_port=0:
                                          self.bridge.connect_streams(
                                              from_tag, to_tag,
                                              int(from_port), int(to_port)),
            "validate_topology":      lambda: self.bridge.validate_topology(),
            "setup_reaction":         lambda reactor_tag, reactions:
                                          self.bridge.setup_reaction(reactor_tag, reactions),
            "set_column_specs":       lambda column_tag, **kwargs:
                                          self.bridge.set_column_specs(column_tag, **kwargs),
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
            # Persistent memory
            "remember_goal":          self._remember_goal,
            "remember_constraint":    self._remember_constraint,
            "recall_memory":          self._recall_memory,
        }

    def _remember_goal(self, text: str) -> dict:
        try:
            import session_memory
            return session_memory.set_goal(text)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def _remember_constraint(self, text: str) -> dict:
        try:
            import session_memory
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
          1. check_convergence  — identify which streams/unit-ops failed
          2. validate_feed_specs — check for missing T/P/flow specs
        Inject findings into the result dict so the LLM sees them immediately.
        """
        diagnostics: Dict[str, Any] = {}
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
            self._replay_builder = _rl.TurnBuilder(
                session_id  = self._session_id,
                turn_index  = self._turn_index,
                provider    = getattr(self.llm, "provider", ""),
                model       = getattr(self.llm, "model", ""),
                temperature = getattr(self.llm, "temperature", 0.0),
                seed        = getattr(self.llm, "_REPRODUCIBILITY_SEED", 42),
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

        for iteration in range(1, _effective_max + 1):
            self._log(f"\n[Iter {iteration}] Calling {self.llm.provider.upper()}…")

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

            response = self._llm_chat_with_retry(
                messages=self._history,
                tools=DWSIM_TOOLS,
                system_prompt=system,
            )

            if response is None:
                self._log("[Agent] LLM returned None after retries — all providers exhausted")
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
                self._history.append({"role": "assistant",
                                      "content": text_content})
                self._emit_turn_metrics(turn_t0, iteration, text_content)
                # Re-snapshot after the turn so next turn's diff captures
                # everything this turn's tool calls changed.
                try:
                    self._last_state_snapshot = snapshot_flowsheet_state(self.bridge)
                except Exception:
                    self._last_state_snapshot = _turn_start_snap
                return text_content

            self._history.append(self.llm.assistant_turn(response))

            # Deduplicate: if the LLM sends multiple new_flowsheet calls in one
            # batch (parallel tool calls), execute only the first one and skip
            # the rest with a "already initialized" reply — prevents purge-loop.
            _seen_init_tools: set = set()
            _ONCE_PER_BATCH = {"new_flowsheet", "save_and_solve"}

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
                    result = _compress_tool_result(name, result)
                    result = _sanitize_for_llm(result)   # Gap 1: injection defence
                    # ── Auto-diagnosis on save_and_solve failure ──────────────
                    # When simulation fails to converge, automatically call
                    # check_convergence and validate_feed_specs so the LLM has
                    # root-cause information without needing to ask for it.
                    if name == "save_and_solve" and not result.get("success", True):
                        result = self._auto_diagnose_convergence(result)
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

            self._history.extend(
                self.llm.tool_result_turns(tool_calls, results)
            )

            # Track (tool_name, error_code) pairs that failed this iteration.
            # If the same pair recurs 3 iterations in a row, the agent is
            # looping on the same problem — stop and surface it.
            failed_this_iter = {
                (tc["name"], str(r.get("code") or "ERROR"))
                for tc, r in zip(tool_calls, results)
                if not r.get("success")
            }
            for tc, r in zip(tool_calls, results):
                if not r.get("success"):
                    last_error_msg = str(r.get("error") or "")
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
        """
        last_err   = None
        budget_t0  = time.monotonic()

        for attempt in range(_LLM_MAX_ATTEMPTS):
            # Check aggregate budget before each attempt
            elapsed = time.monotonic() - budget_t0
            if elapsed >= _LLM_RETRY_BUDGET_S:
                self._log(
                    f"[LLM] aggregate retry budget ({_LLM_RETRY_BUDGET_S:.0f}s) "
                    f"exhausted after {elapsed:.1f}s — giving up"
                )
                break

            try:
                resp = self.llm.chat(
                    messages=messages,
                    tools=tools,
                    system_prompt=system_prompt,
                )
                if resp is not None:
                    return resp
                last_err = "provider returned None"
            except Exception as exc:
                last_err = str(exc)
                _log.warning("LLM attempt %d failed: %s", attempt + 1, last_err)

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
            print('    "Load C:\\Users\\hp\\Documents\\HE.dwxmz"\n')

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
        """Return the curated template library."""
        try:
            from flowsheet_templates import list_templates
            return {"success": True, "templates": list_templates()}
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

    def _coerce_arguments(self, name: str, arguments: dict) -> dict:
        """Coerce argument types to match expected signatures, fixing common LLM mistakes."""
        coerced = {}
        for k, v in arguments.items():
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
            timeout_s = _TOOL_TIMEOUT_S.get(name, _DEFAULT_TOOL_TIMEOUT_S)
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
              "Load C:\\Users\\hp\\Documents\\HE.dwxmz"
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
