"""
fast_path.py
────────────
Pre-LLM template router for common flowsheet creation requests.

For simple, well-known process types (heater, pump, heat exchanger, flash,
distillation, compressor, reactor) this module bypasses the agent entirely and
calls bridge.create_flowsheet() directly with a pre-built topology.

Result: ~5–15 s (DWSIM solve) instead of ~30–90 s (LLM planning + solve).

Usage (from api.py / chat_stream):
    from fast_path import try_fast_path
    result = try_fast_path(user_message, bridge)
    if result is not None:
        return result  # SSE-format answer string
    # else: fall through to normal agent
"""

import re
from copy import deepcopy
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Pattern → template key mapping
# Each entry: (compiled regex, template_key, friendly_label)
# Patterns are tried in order; first match wins.
# ─────────────────────────────────────────────────────────────────────────────

_PATTERNS = [
    # Heater / cooler
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(heater|heating|heat\s+water|water\s+heat|simple\s+heat)\b",
        re.I), "heater_cooler", "water heater"),

    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(cooler|cooling)\b",
        re.I), "heater_cooler", "cooler"),

    # Heat exchanger
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(heat\s+exchanger|shell.and.tube|hx|he\b|counter.current\s+exchanger)\b",
        re.I), "heat_exchanger", "heat exchanger"),

    # Flash / separator
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(flash(\s+drum|\s+tank|\s+separator)?|two.phase\s+sep)\b",
        re.I), "flash_separation", "flash separator"),

    # Distillation
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(distillation|distillation\s+column|shortcut\s+column|benzene.toluene)\b",
        re.I), "shortcut_distillation", "shortcut distillation column"),

    # Absorber
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(absorber|absorption\s+column|co2\s+removal)\b",
        re.I), "absorber", "CO₂ absorber"),

    # Pump
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(pump|pump.valve|pressuri[sz]e)\b",
        re.I), "pump_valve", "pump"),

    # Compressor / expander
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(compressor|compress\s+gas|gas\s+compress)\b",
        re.I), "pump_valve", "compressor"),  # closest available template

    # Reactor
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(conversion\s+reactor|cstr|reactor)\b",
        re.I), "conversion_reactor", "conversion reactor"),

    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(gibbs\s+reactor|gibbs|water.gas\s+shift|wgs)\b",
        re.I), "gibbs_reactor", "Gibbs reactor"),

    # Recycle
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(recycle|reactor.with.recycle|recycle\s+loop)\b",
        re.I), "reactor_recycle", "reactor with recycle"),

    # Mixer / blender
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(mixer|mixing|blender|stream\s+blend)\b",
        re.I), "stream_blender", "stream mixer"),

    # Electrolyzer
    (re.compile(
        r"\b(create|build|make|design|set\s+up|simulate)\b.{0,60}"
        r"\b(electroly[zs]er|hydrogen\s+produc|water\s+split)\b",
        re.I), "water_electrolyzer", "water electrolyzer"),
]

# Words that disqualify fast-path (user wants something custom).
_DISQUALIFIERS = re.compile(
    r"\b(custom|my own|with\s+(my|these|the\s+following)\s+(compounds?|streams?|conditions?)"
    r"|add\s+\w+\s+to\s+existing|modify|change|update|what\s+is|explain|why|how"
    r"|report|parametric|optimis[ez]|sensitivity)\b",
    re.I
)


def _match_template(message: str):
    """Return (template_key, label) or (None, None)."""
    if _DISQUALIFIERS.search(message):
        return None, None
    for pattern, key, label in _PATTERNS:
        if pattern.search(message):
            return key, label
    return None, None


def try_fast_path(message: str, bridge) -> Optional[str]:
    """
    If *message* matches a known creation pattern, build the flowsheet
    directly (no LLM) and return a formatted answer string.

    Returns None if no pattern matched — caller should fall through to agent.
    """
    template_key, label = _match_template(message)
    if template_key is None:
        return None

    try:
        from flowsheet_templates import TEMPLATES
    except ImportError:
        return None

    if template_key not in TEMPLATES:
        return None

    topology = deepcopy(TEMPLATES[template_key]["topology"])

    try:
        result = bridge.create_flowsheet(topology)
    except Exception as exc:
        return (
            f"Fast-path template for **{label}** raised an exception: {exc}\n\n"
            "Falling back to full agent — please rephrase if this keeps failing."
        )

    if not result.get("success"):
        # Soft failure: return None so agent retries with full reasoning
        return None

    # ── Format a readable response ────────────────────────────────────────────
    converged   = result.get("converged", False)
    saved_to    = result.get("saved_to") or result.get("flowsheet_path", "")
    warnings    = result.get("warnings", [])
    stream_data = result.get("stream_results", {})

    lines = [
        f"**{label.title()} created successfully** via fast-path template.",
        "",
    ]

    if not converged:
        lines.append("> ⚠️ Simulation did **not** converge — check feed conditions.")
        lines.append("")

    if stream_data:
        lines.append("### Stream Results")
        lines.append("")
        for tag, props in stream_data.items():
            t_c  = props.get("temperature_C",  "—")
            p_b  = props.get("pressure_bar",   "—")
            mf   = props.get("mass_flow_kgh",  "—")
            vf   = props.get("vapor_fraction", "—")
            if isinstance(t_c, float):
                t_c = f"{t_c:.2f} °C"
            if isinstance(p_b, float):
                p_b = f"{p_b:.3f} bar"
            if isinstance(mf, float):
                mf  = f"{mf:.2f} kg/h"
            if isinstance(vf, float):
                vf  = f"{vf:.4f}"
            lines.append(f"**{tag}**: T = {t_c}, P = {p_b}, ṁ = {mf}, VF = {vf}")
        lines.append("")

    if saved_to:
        lines.append(f"Saved to: `{saved_to}`")

    if warnings:
        lines.append("")
        lines.append("**Warnings:**")
        for w in warnings:
            lines.append(f"- {w}")

    lines.append("")
    lines.append(
        "_Built via deterministic template — no LLM reasoning used. "
        "Ask follow-up questions to modify conditions or run a parametric study._"
    )

    return "\n".join(lines)
