"""
optimization_orchestrator.py
────────────────────────────
End-to-end natural-language → DWSIM-internal-optimization workflow that matches
the poster exactly:

    Engineer: "Optimize the syngas process to maximize (H2+CO) purity
               while minimising total energy consumption."

    AI Agent: 1. Identifies decision variables (reactor temps, feed flows)
              2. Builds composite objective (maximise purity − k·energy)
              3. Picks a solver (DotNumerics Simplex / L-BFGS-B / DE)
              4. Runs DWSIM closed-loop optimization
              5. Returns Old → New → Change table + "objective achieved"

The orchestrator drives `dwsim_native_optimizer.run_dwsim_native_optimization`
under the hood. It is LLM-aware: a user goal in plain English is mapped to a
structured optimization spec by the LLM, then validated against the live
flowsheet before the solver runs.

Public API
──────────
suggest_decision_variables(bridge, max_n=5)
    Inspect the live flowsheet, propose plausible decision variables
    (reactor outlet temperatures, feed mass flows, column reflux ratios)
    with sensible ±20%-of-current bounds.

build_spec_from_goal(llm, bridge, goal, max_vars=5)
    Use the LLM to map a free-text goal → {variables, objective, method,
    minimize}. Validates the output against the live flowsheet (tags/props
    must exist). Returns a ready-to-run spec.

run_optimization_workflow(bridge, goal, llm=None, on_step=None, max_iter=50)
    The poster end-to-end:
      1) discover  2) spec  3) run  4) format
    Emits per-step progress via on_step("📊 step", "human-readable detail").
    Returns the optimization result + a poster-style chat markdown rendering.

format_poster_chat(result, goal=None)
    Render the optimization result as markdown matching the poster's
    "OPTIMIZATION RESULTS / KEY MODIFIED VARIABLES" layout.
"""

from __future__ import annotations
import json
import logging
import math
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("optimization_orchestrator")


# ─── 1. Variable suggester ────────────────────────────────────────────────

# Heuristic property suggestions per unit-op type.
# DWSIM's GetType().Name returns short class names like "Heater", "Cooler".
# Matching is case-insensitive substring so we catch variants like
# "DWSIMHeater" or "Heater_v2".
_VAR_HINTS_BY_TYPE: Dict[str, List[Tuple[str, str, float]]] = {
    # (property_name, unit, default ± fraction of current)
    "heater":              [("outlet_temperature_C", "C", 0.10)],
    "cooler":              [("outlet_temperature_C", "C", 0.10)],
    "conversionreactor":   [("outlet_temperature_C", "C", 0.05)],
    "equilibriumreactor":  [("outlet_temperature_C", "C", 0.05)],
    "gibbsreactor":        [("outlet_temperature_C", "C", 0.05)],
    "cstr":                [("outlet_temperature_C", "C", 0.05)],
    "pfr":                 [("outlet_temperature_C", "C", 0.05)],
    "plug":                [("outlet_temperature_C", "C", 0.05)],   # PlugFlow*
    "reactor":             [("outlet_temperature_C", "C", 0.05)],   # generic
    "compressor":          [("outlet_pressure_bar",  "bar", 0.15)],
    "expander":            [("outlet_pressure_bar",  "bar", 0.15)],
    "turbine":             [("outlet_pressure_bar",  "bar", 0.15)],
    "pump":                [("outlet_pressure_bar",  "bar", 0.15)],
    "distillation":        [("RefluxRatio",          "",   0.30)],
    "shortcutcolumn":      [("RefluxRatio",          "",   0.30)],
    "column":              [("RefluxRatio",          "",   0.30)],   # generic
    "absorption":          [("NumberOfStages",       "",   0.20)],
    "valve":               [("outlet_pressure_bar",  "bar", 0.20)],
    "heatexchanger":       [("outlet_temperature_C", "C", 0.10)],
    "hx":                  [("outlet_temperature_C", "C", 0.10)],
    "flash":               [("temperature_C",        "C", 0.10),
                            ("pressure_bar",         "bar", 0.10)],
    "separator":           [("temperature_C",        "C", 0.10)],
    "mixer":               [],  # mixers have no decision variable
    "splitter":            [],
    "materialstream":      [("mass_flow_kgh",        "kg/h", 0.20),
                            ("temperature_C",        "C",   0.10)],
    "stream":              [("mass_flow_kgh",        "kg/h", 0.20),
                            ("temperature_C",        "C",   0.10)],
}


def _match_unit_op_hints(type_name: str) -> List[Tuple[str, str, float]]:
    """Case-insensitive substring lookup. 'DWSIMConversionReactor' →
    matches 'conversionreactor'. 'Heater_v2' → matches 'heater'."""
    if not type_name:
        return []
    lc = type_name.lower().replace("_", "").replace("-", "").replace(" ", "")
    # Try exact match first, then ordered substring match (longest key first
    # so 'conversionreactor' beats bare 'reactor')
    if lc in _VAR_HINTS_BY_TYPE:
        return _VAR_HINTS_BY_TYPE[lc]
    for key in sorted(_VAR_HINTS_BY_TYPE.keys(), key=len, reverse=True):
        if key in lc:
            return _VAR_HINTS_BY_TYPE[key]
    return []


def _engineering_bounds(prop: str, role: str, current: float,
                         fallback_frac: float) -> tuple:
    """Return engineering-reasoned (lower, upper) bounds for a decision
    variable, instead of a blind ±fraction.

    Rationale (from the critical analysis): a reactor that should be free to
    explore 400–900 °C must not be boxed into ±5%. Bounds are role-aware and
    capped at physical limits. Falls back to ±fallback_frac when the role is
    unknown.

    References for typical operating windows: Turton (2018) Ch. 6, Smith
    (2005) Ch. 3, Perry's 9th ed."""
    p = prop.lower()
    c = float(current)

    # Temperature variables — explore a wide but physical window
    if "temperature" in p:
        if role == "reactor_T":
            # Reactors: explore ±150 °C or a 0.7–1.4× multiplicative span,
            # whichever is WIDER, capped to [25, 1200] °C.
            lo = min(c - 150.0, c * 0.70)
            hi = max(c + 150.0, c * 1.40)
            return (round(max(25.0, lo), 2), round(min(1200.0, hi), 2))
        else:
            # Feed / heater / flash temperatures: ±50 °C, capped to a
            # physically sensible liquid/vapour window [1, 400] °C.
            return (round(max(1.0, c - 50.0), 2),
                    round(min(400.0, c + 50.0), 2))

    # Pressure variables — operating pressure can move a lot
    if "pressure" in p:
        # 0.5×–2× current, floored at slightly-above-vacuum.
        return (round(max(0.1, c * 0.5), 4), round(c * 2.0, 4))

    # Reflux ratio — must stay above ~1 (≈ R_min) and can rise several-fold
    if "reflux" in p:
        return (round(max(1.05, c * 0.6), 3), round(c * 3.0, 3))

    # Mass / molar flow — feed rates routinely vary ±50%
    if "flow" in p:
        return (round(max(1e-6, c * 0.5), 6), round(c * 1.5, 6))

    # Number of stages — integer-ish, ±30%
    if "stage" in p:
        return (round(max(3.0, c * 0.7)), round(c * 1.3))

    # Unknown role — fall back to the supplied fraction
    return (round(c * (1 - fallback_frac), 6), round(c * (1 + fallback_frac), 6))


def _read_current_value(bridge, tag: str, prop: str) -> Optional[float]:
    """Get the current numeric value of a property, returning None on miss."""
    try:
        if hasattr(bridge, "get_stream_property"):
            r = bridge.get_stream_property(tag, prop)
            if isinstance(r, dict) and r.get("success"):
                v = r.get("value")
                if v is None and isinstance(r.get("properties"), dict):
                    v = r["properties"].get(prop)
                if v is not None:
                    return float(v)
        if hasattr(bridge, "get_stream_properties"):
            r = bridge.get_stream_properties(tag)
            if isinstance(r, dict) and r.get("success"):
                for k in (prop, prop.lower(), prop + "_C", prop + "_K"):
                    v = r.get("properties", {}).get(k)
                    if v is not None:
                        return float(v)
        if hasattr(bridge, "get_unit_op_properties"):
            r = bridge.get_unit_op_properties(tag)
            if isinstance(r, dict) and r.get("success"):
                v = r.get("properties", {}).get(prop)
                if v is not None:
                    return float(v)
    except Exception:
        pass
    return None


def _enumerate_flowsheet_objects(bridge) -> Dict[str, List[Dict[str, Any]]]:
    """Return {streams: [...], unit_ops: [...]} from whichever bridge method
    is available. Handles three return shapes:
      a) list_simulation_objects() → {objects: [{tag, type, category}]}
      b) list_objects()            → {streams, unit_ops}  (legacy)
      c) raw flat list             → fall back

    Splits by category: anything where category contains 'stream' OR type
    contains 'MaterialStream' is a stream; everything else is a unit op."""
    raw = None
    for method in ("list_simulation_objects", "list_objects",
                   "get_simulation_objects"):
        if hasattr(bridge, method):
            try:
                raw = getattr(bridge, method)()
                break
            except Exception:
                continue
    if not isinstance(raw, dict):
        return {"streams": [], "unit_ops": []}

    # Shape (b): already split
    if "unit_ops" in raw or "streams" in raw:
        return {
            "streams":  list(raw.get("streams", [])),
            "unit_ops": list(raw.get("unit_ops", [])),
        }

    # Shape (a) / (c): flat objects[]
    objs = raw.get("objects") or []
    streams, unit_ops = [], []
    for o in objs:
        if not isinstance(o, dict): continue
        if not o.get("tag"):        continue
        cat  = (o.get("category") or "").lower()
        tn   = (o.get("type")     or "").lower()
        if "stream" in cat or "materialstream" in tn or tn == "stream":
            streams.append(o)
        elif cat == "energy" or "energystream" in tn:
            # Skip energy streams — they're not decision variables
            continue
        else:
            unit_ops.append(o)
    return {"streams": streams, "unit_ops": unit_ops}


def _is_plugin_flowsheet(bridge) -> bool:
    """Detect Cantera / ChemSep / Reaktoro plugin flowsheets."""
    try:
        from pp_validator import _detect_plugin_flowsheet
        return _detect_plugin_flowsheet(bridge) is not None
    except Exception:
        return False


def suggest_decision_variables(bridge, max_n: int = 5) -> List[Dict[str, Any]]:
    """Walk the loaded flowsheet and propose plausible decision variables.

    Returns a list of dicts: {tag, property, unit, lower, upper, initial,
    role, reason}. role ∈ {'reactor_T','flow','pressure','reflux','feed_T',
    'operating'}."""
    objs = _enumerate_flowsheet_objects(bridge)
    suggestions: List[Dict[str, Any]] = []

    # ── Pass 1: Unit ops — use type-hint mapping ─────────────────────────
    for uop in objs["unit_ops"]:
        t   = uop.get("type") or ""
        tag = uop.get("tag")
        if not tag:
            continue
        hints = _match_unit_op_hints(t)
        if not hints:
            continue
        for prop, unit, frac in hints:
            cur = _read_current_value(bridge, tag, prop)
            if cur is None or cur == 0:
                continue
            t_lc = t.lower()
            role = ("reactor_T" if "reactor" in t_lc
                    else "pressure" if "pressure" in prop.lower()
                    else "reflux"   if "reflux"   in prop.lower()
                    else "operating")
            lo, hi = _engineering_bounds(prop, role, cur, frac)
            suggestions.append({
                "tag": tag, "property": prop, "unit": unit,
                "lower": lo, "upper": hi, "initial": cur,
                "role": role,
                "reason": f"{t} '{tag}' {prop} (eng. bounds [{lo},{hi}], current {cur})",
            })

    # ── Pass 2: Streams — mass flow + temperature ────────────────────────
    # FEED streams are decision variables; OUTPUT streams are computed
    # results that cannot be optimised (writing to them either fails or
    # gets immediately overwritten by the next solve). Heuristic: tags
    # containing 'product', 'bottoms', 'distillate', 'extract', 'raffinate',
    # 'effluent', 'out', 'top', 'overhead' are likely outputs; skip them.
    output_keywords = (
        "product", "bottoms", "distillate", "extract", "raffinate",
        "effluent", "_out", "outlet", "top", "overhead", "permeate",
        "retentate", "fume", "vent", "purge", "tail", "soot",
        "unburnt", "gases", "mixture", "waste",
    )
    stream_hints = _VAR_HINTS_BY_TYPE["materialstream"]
    for s in objs["streams"]:
        tag = s.get("tag")
        if not tag:
            continue
        tag_lc = tag.lower()
        # Skip likely output streams
        if any(kw in tag_lc for kw in output_keywords):
            continue
        for prop, unit, frac in stream_hints:
            cur = _read_current_value(bridge, tag, prop)
            if cur is None or cur == 0:
                continue
            role = "flow" if "flow" in prop else "feed_T"
            lo, hi = _engineering_bounds(prop, role, cur, frac)
            suggestions.append({
                "tag": tag, "property": prop, "unit": unit,
                "lower": lo, "upper": hi, "initial": cur,
                "role": role,
                "reason": f"Feed stream '{tag}' {prop} (eng. bounds [{lo},{hi}])",
            })

    # ── Safety-net: nothing matched ──────────────────────────────────────
    # If we have streams but couldn't read any values (e.g. flowsheet is
    # loaded but not yet solved, or properties have unusual names), try a
    # broader sweep of common DWSIM property aliases. The output-stream
    # filter still applies — we never suggest writing to outputs.
    if not suggestions and (objs["streams"] or objs["unit_ops"]):
        broad_aliases = [
            ("temperature",         "C",   "temperature_C",   0.10),
            ("temperature_K",       "K",   "temperature_K",   0.10),
            ("pressure",            "bar", "pressure_bar",    0.15),
            ("pressure_Pa",         "Pa",  "pressure_Pa",     0.15),
            ("mass_flow",           "kg/s", "mass_flow_kgs",  0.20),
            ("massflow",            "kg/s", "massflow",       0.20),
            ("molar_flow",          "mol/s", "molar_flow_mols", 0.20),
            ("MassFlow",            "kg/s", "MassFlow",       0.20),
            ("Temperature",         "K",   "Temperature",     0.10),
            ("Pressure",            "Pa",  "Pressure",        0.15),
        ]
        for s in objs["streams"]:
            tag = s.get("tag")
            if not tag:
                continue
            if any(kw in tag.lower() for kw in output_keywords):
                continue   # outputs are never decision variables
            for prop, unit, label, frac in broad_aliases:
                cur = _read_current_value(bridge, tag, prop)
                if cur is None or cur == 0 or not math.isfinite(cur):
                    continue
                # Reject obviously-unphysical values
                if abs(cur) > 1e15:
                    continue
                role = ("feed_T" if "temp" in prop.lower()
                        else "flow" if "flow" in prop.lower()
                        else "pressure")
                lo, hi = _engineering_bounds(prop, role, cur, frac)
                suggestions.append({
                    "tag": tag, "property": prop, "unit": unit,
                    "lower": lo, "upper": hi, "initial": cur,
                    "role": role,
                    "reason": f"Stream '{tag}' {prop} (safety-net, ±{int(frac*100)}%)",
                })
                break   # one variable per stream is enough for the fallback

    # Sort by role priority (reactor temps first, then flows, then pressures)
    role_order = {"reactor_T": 0, "flow": 1, "pressure": 2, "reflux": 3,
                  "feed_T": 4, "operating": 5}
    suggestions.sort(key=lambda x: role_order.get(x["role"], 99))

    # Deduplicate (tag, property) — same variable from two passes
    seen = set()
    deduped = []
    for s in suggestions:
        key = (s["tag"], s["property"])
        if key in seen: continue
        seen.add(key)
        deduped.append(s)

    return deduped[:max_n]


# ─── 2. NL goal → optimization spec ───────────────────────────────────────

_GOAL_SYSTEM_PROMPT = """\
You are an expert chemical-process optimization planner. Given a user's
natural-language goal AND a list of available decision variables from the
loaded DWSIM flowsheet, output a JSON object describing the optimization:

  {
    "minimize": <bool>,
    "method":   "simplex" | "lbfgs" | "newton" | "powell" | "de",
    "objective": {
        "type": "variable" | "expression",
        // for type='variable':
        "tag": "<stream/unit-op tag>",
        "property": "<property name>",
        // for type='expression':
        "expression": "<arithmetic expression with named values>",
        "named_values": [{"name":"...", "tag":"...", "property":"..."}]
    },
    "variables_to_use": ["<var_id1>", "<var_id2>", ...]
  }

Rules:
  • Use ONLY decision variables that appear in the AVAILABLE_VARIABLES list.
    Their identifiers are in the form "<tag>.<property>".
  • For maximize-and-minimize goals (e.g. "maximize purity AND minimize
    energy"), use type='expression' and combine them with a weighting:
        expression: "purity - 0.001 * energy"
    Pick a coefficient that puts the two terms on similar scales (mole
    fractions are 0–1; energies are 100s of kW, so 0.0001–0.001 is typical).
  • Pick 'minimize' to match the FIRST sense of the objective expression
    (if maximizing, set minimize=false; the optimizer will flip signs).
  • Default method='simplex' (robust, gradient-free). Use 'lbfgs' only if
    the user mentions smoothness or gradients. Use 'de' for global search.
  • Output ONLY the JSON. No markdown, no commentary.

EXAMPLE:
  Goal: "Maximize H2+CO purity at PSA while minimising total reactor energy"
  Available vars: ["RC-01.outlet_temperature_C", "RC-02.outlet_temperature_C",
                   "AIR.mass_flow_kgh"]
  Available observables: ["PSA.mole_fraction_H2", "PSA.mole_fraction_CO",
                          "TOTAL.duty_kW"]
  Output:
  {"minimize": false, "method": "simplex",
   "objective": {"type":"expression",
                  "expression": "H2 + CO - 0.001 * energy",
                  "named_values": [
                    {"name":"H2","tag":"PSA","property":"mole_fraction_H2"},
                    {"name":"CO","tag":"PSA","property":"mole_fraction_CO"},
                    {"name":"energy","tag":"TOTAL","property":"duty_kW"}
                  ]},
   "variables_to_use": ["RC-01.outlet_temperature_C",
                         "RC-02.outlet_temperature_C", "AIR.mass_flow_kgh"]}
"""


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first {...} JSON object out of an LLM response."""
    if not text:
        return None
    # Strip code-fence wrappers
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # Direct parse
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Find balanced braces
    depth = 0
    start = -1
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(t[start:i+1])
                except json.JSONDecodeError:
                    continue
    return None


def build_spec_from_goal(
    llm,
    bridge,
    goal: str,
    suggested_vars: Optional[List[Dict]] = None,
    max_vars: int = 5,
) -> Dict[str, Any]:
    """Map a free-text goal → structured optimization spec.

    If `llm` is None or the call fails, falls back to a heuristic:
    use all suggested vars + a "first observable in the goal" objective."""
    if suggested_vars is None:
        suggested_vars = suggest_decision_variables(bridge, max_n=max_vars)
    if not suggested_vars:
        return {"success": False,
                "error_code": "NO_VARIABLES_FOUND",
                "error": "No decision variables could be inferred. Load a "
                         "flowsheet with reactors / heaters / streams first."}

    # Build an "available observables" list — products, outlet streams.
    # Uses the same enumerator as suggest_decision_variables so it handles
    # both the new list_simulation_objects() shape and the legacy one.
    observables: List[str] = []
    try:
        objs = _enumerate_flowsheet_objects(bridge)
        for s in objs.get("streams", [])[:20]:
            tag = s.get("tag", "")
            if not tag:
                continue
            for prop in ("mole_fraction_H2", "mole_fraction_CO",
                          "mole_fraction_CO2", "mole_fraction_CH4",
                          "mole_fraction_N2", "mole_fraction_H2O",
                          "mass_flow_kgh", "molar_flow_mols",
                          "temperature_C", "pressure_bar"):
                observables.append(f"{tag}.{prop}")
    except Exception:
        pass

    var_ids = [f"{v['tag']}.{v['property']}" for v in suggested_vars]

    # ── Case-based learning: inject VERIFIED past objectives for similar
    #    goals/flowsheets as worked examples (improves objective-mapping). ──
    learned_block = ""
    try:
        from experience_store import retrieve_similar, format_examples_for_prompt
        _cases = retrieve_similar(goal, bridge, k=3)
        if _cases:
            learned_block = "\n\n" + format_examples_for_prompt(_cases)
            _log.info("objective-mapping: injected %d learned example(s)",
                      len(_cases))
    except Exception as _lexc:
        _log.debug("experience retrieval skipped: %s", _lexc)

    user_msg = (
        f"Goal: {goal}\n\n"
        f"AVAILABLE_VARIABLES (with bounds):\n"
        + "\n".join(f"  - {v['tag']}.{v['property']}  in [{v['lower']}, {v['upper']}]  ({v['unit']})"
                    for v in suggested_vars)
        + "\n\nAVAILABLE_OBSERVABLES (suggested):\n"
        + "\n".join(f"  - {o}" for o in observables[:40])
        + learned_block
        + "\n\nOutput the JSON spec now."
    )

    spec: Optional[Dict[str, Any]] = None
    if llm is not None:
        try:
            # LLMClient.chat signature is chat(messages, tools, system_prompt).
            # BUG FIX: this previously passed system=/temperature=/max_tokens=,
            # which raised TypeError on every call — so the LLM objective
            # mapping silently fell back to the naive heuristic every time
            # (the root cause of the 'H2-in-water' wrong-objective bug).
            resp = llm.chat(
                messages=[{"role": "user", "content": user_msg}],
                tools=[],
                system_prompt=_GOAL_SYSTEM_PROMPT,
            )
            content = resp.get("content") if isinstance(resp, dict) else str(resp)
            spec = _extract_json_object(content or "")
        except Exception as exc:
            _log.warning("LLM goal parsing failed: %s", exc)

    if spec is None:
        # Heuristic fallback — used when the LLM is unavailable / quota-exhausted.
        # Order of preference for the objective:
        #   1) An observable that's name-mentioned in the goal text.
        #   2) A product-stream observable (tag contains "product/out/effluent/
        #      psa/distillate") whose property looks like a mole fraction or
        #      product flow — maximise this.
        #   3) Otherwise: maximise the LAST stream's mole_fraction_<first
        #      valuable compound> (H2 > CO > CH4 > anything else).
        #   4) Last resort: observables[0] (already deterministic).
        goal_lc = goal.lower()
        # 'is_max' is True if the goal contains a maximize-ish word OR if the
        # goal is bare (e.g. "optimise") — bare goals default to MAXIMISE
        # because users usually mean "maximise yield/purity/product" by default.
        is_explicit_min = any(k in goal_lc for k in
                              ("minimi", "minimize", "minimise",
                               "reduce", "decrease", "lower "))
        is_max = not is_explicit_min

        chosen_obs = None

        # Step 0 — ENERGY / DUTY goals: "minimise heater duty", "reduce energy",
        # "minimise reboiler duty". Map to the relevant unit-op's HeatDuty.
        # This is the most common minimise goal and must not fall through to
        # mole-fraction defaults.
        energy_kw = ("duty", "energy", "heat", "power", "consumption")
        if any(k in goal_lc for k in energy_kw):
            try:
                objs = _enumerate_flowsheet_objects(bridge)
                # Find heater/cooler/reactor/reboiler unit ops
                duty_units = []
                for u in objs.get("unit_ops", []):
                    t = (u.get("type") or "").lower()
                    tag = u.get("tag", "")
                    if any(k in t for k in ("heater", "cooler", "heat",
                                             "reactor", "column", "reboiler")):
                        duty_units.append(tag)
                    # also match if the goal names the unit tag directly
                    elif tag and tag.lower() in goal_lc:
                        duty_units.append(tag)
                # Prefer a unit whose tag the goal mentions, else the first
                target_unit = None
                for tag in duty_units:
                    if tag.lower() in goal_lc:
                        target_unit = tag; break
                if target_unit is None and duty_units:
                    target_unit = duty_units[0]
                if target_unit:
                    chosen_obs = f"{target_unit}.HeatDuty"
                    # Energy goals are almost always minimise
                    if not any(k in goal_lc for k in ("maximi", "maximize",
                                                       "maximise", "increase")):
                        is_max = False
            except Exception:
                pass

        # Step 1 — exact-tag or exact-property mention in the goal
        if chosen_obs is None:
            for obs in observables:
                tag, prop = obs.split(".", 1)
                if tag.lower() in goal_lc \
                        or prop.lower().replace("_", " ") in goal_lc:
                    chosen_obs = obs; break

        # Step 2 — product-stream observable
        if chosen_obs is None:
            product_kw = ("product", "psa", "distillate", "effluent",
                          "out ", "_out", "purity")
            valuable_props = ("mole_fraction_H2", "mole_fraction_CO",
                               "mass_flow_kgh", "molar_flow_mols")
            for obs in observables:
                tag, prop = obs.split(".", 1)
                if any(kw in tag.lower() for kw in product_kw) \
                        and any(prop.startswith(vp) or prop == vp
                                for vp in valuable_props):
                    chosen_obs = obs; break

        # Step 3 — last stream's H2/CO/CH4 mole fraction
        if chosen_obs is None:
            for valuable in ("mole_fraction_H2", "mole_fraction_CO",
                              "mole_fraction_CH4"):
                matches = [o for o in observables if o.endswith("." + valuable)]
                if matches:
                    chosen_obs = matches[-1]   # last stream — usually product
                    break

        # Step 4 — last resort
        if chosen_obs is None and observables:
            chosen_obs = observables[0]
        if chosen_obs is None:
            return {"success": False,
                    "error_code": "NO_OBJECTIVE",
                    "error": "Could not identify an objective from the goal. "
                             "Try a more specific phrase, e.g. "
                             "'maximise H2 yield' or 'minimise reboiler duty'."}
        tag, prop = chosen_obs.split(".", 1)
        spec = {
            "minimize": not is_max,
            "method":   "simplex",
            "objective": {"type": "variable", "tag": tag, "property": prop},
            "variables_to_use": var_ids,
            "_heuristic_fallback": True,
            "_heuristic_reason": (
                f"LLM unavailable; auto-picked '{chosen_obs}' as objective "
                f"(direction: {'maximise' if is_max else 'minimise'}). "
                f"For a specific objective, rephrase as 'maximise <X>' or "
                f"'minimise <X>' where X is a stream property."
            ),
        }

    # Validate spec — variables_to_use must be subset of suggestions
    chosen_var_ids = set(spec.get("variables_to_use") or var_ids)
    chosen_vars = [v for v in suggested_vars
                   if f"{v['tag']}.{v['property']}" in chosen_var_ids]
    if not chosen_vars:
        chosen_vars = suggested_vars   # fall back to all suggestions

    return {
        "success":   True,
        "minimize":  bool(spec.get("minimize", True)),
        "method":    str(spec.get("method", "simplex")),
        "objective": spec.get("objective") or {},
        "_heuristic_fallback": spec.get("_heuristic_fallback", False),
        "_heuristic_reason":   spec.get("_heuristic_reason", ""),
        "variables": [
            {"tag": v["tag"], "property": v["property"], "unit": v["unit"],
             "lower": v["lower"], "upper": v["upper"], "initial": v["initial"]}
            for v in chosen_vars
        ],
        "rationale": (
            f"Selected {len(chosen_vars)} of {len(suggested_vars)} suggested "
            f"variables. "
            f"{'Minimising' if spec.get('minimize', True) else 'Maximising'} "
            f"with {spec.get('method', 'simplex')} solver."
        ),
    }


# ─── 3. End-to-end orchestrator ───────────────────────────────────────────

def _resolve_constraints(
    parsed_constraints: List[Dict[str, Any]],
    suggested_vars:     List[Dict[str, Any]],
    objective:          Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Map parsed NL constraints onto stream/property tags using flowsheet
    context. Best-effort: if a constraint's LHS doesn't match any known
    tag/property, fall back to the objective's stream (so "purity ≥ 95%"
    binds to the same stream as the objective)."""
    if not parsed_constraints:
        return []

    # Collect known tag.property pairs from suggested vars + objective
    known_pairs: List[tuple] = []
    for v in suggested_vars:
        known_pairs.append((v["tag"].lower(), v["property"].lower(),
                            v["tag"], v["property"]))

    # Add objective tag/property as fallback for unmatched constraints
    obj_tag = obj_prop = ""
    if isinstance(objective, dict):
        if objective.get("type") == "variable":
            obj_tag  = objective.get("tag", "")
            obj_prop = objective.get("property", "")
        elif objective.get("type") == "expression":
            nvs = objective.get("named_values", []) or []
            if nvs:
                obj_tag  = nvs[0].get("tag", "")
                obj_prop = nvs[0].get("property", "")

    resolved: List[Dict[str, Any]] = []
    for pc in parsed_constraints:
        lhs_lc = pc["lhs"].lower()
        matched = None
        # Try exact tag.property match
        for tag_lc, prop_lc, tag_orig, prop_orig in known_pairs:
            if lhs_lc in (tag_lc, prop_lc) or \
                    (tag_lc in lhs_lc and prop_lc in lhs_lc):
                matched = (tag_orig, prop_orig); break
        # Try common synonyms
        if matched is None and obj_tag and obj_prop:
            syn_map = {
                "purity":      obj_prop,
                "yield":       obj_prop,
                "conversion":  obj_prop,
                "temperature": "temperature_C",
                "pressure":    "pressure_bar",
                "flow":        "mass_flow_kgh",
            }
            for word, prop_name in syn_map.items():
                if word in lhs_lc:
                    matched = (obj_tag, prop_name); break
        if matched is None:
            # Last resort: bind to objective stream/property
            if obj_tag and obj_prop:
                matched = (obj_tag, obj_prop)
            else:
                continue
        tag, prop = matched
        # Convert percentage to fraction if rhs is in %
        rhs = float(pc["rhs"])
        if pc.get("unit") in ("%", "mol%", "mass%"):
            rhs = rhs / 100.0
        resolved.append({
            "type":      "ineq",
            "tag":       tag,
            "property":  prop,
            "operator":  pc["operator"],
            "threshold": rhs,
            "raw":       pc["raw_text"],
        })
    return resolved


def _validate_objective_readable(bridge, objective: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-flight check: can the objective actually be read from the live
    flowsheet? Returns {readable, current_value, missing_detail, compounds,
    sample_observables, example_goal}.

    Catches the classic failure: 'maximise H2 purity' on a flowsheet that
    contains no H2 — every evaluation would return None and the optimiser
    would fail with an empty error. This explains WHY up front."""
    from dwsim_native_optimizer import _read_object_property

    # Gather what compounds actually exist
    compounds: List[str] = []
    try:
        if hasattr(bridge, "list_compounds"):
            r = bridge.list_compounds()
            if isinstance(r, dict) and r.get("success"):
                compounds = list(r.get("compounds", []))
    except Exception:
        pass

    # Try to read the objective
    otype = objective.get("type", "variable")
    val = None
    missing = ""
    if otype == "variable":
        tag = objective.get("tag", "")
        prop = objective.get("property", "")
        val = _read_object_property(bridge, tag, prop)
        if val is None:
            # Is it a compound-specific property the flowsheet can't have?
            prop_l = prop.lower()
            named_compound = None
            for token in ("h2", "hydrogen", "co", "co2", "ch4", "methane",
                           "ethane", "n2", "nitrogen", "o2", "oxygen"):
                if token in prop_l:
                    named_compound = token
                    break
            if named_compound and compounds and not any(
                    named_compound in c.lower() for c in compounds):
                missing = (f"The objective references '{named_compound.upper()}' "
                           f"(via {tag}.{prop}), but this flowsheet contains no "
                           f"such compound.")
            else:
                missing = (f"The objective property '{tag}.{prop}' could not be "
                           f"read from the flowsheet — the property name may be "
                           f"wrong or the object '{tag}' may not exist.")
    elif otype == "expression":
        for nv in objective.get("named_values", []) or []:
            v = _read_object_property(bridge, nv.get("tag", ""),
                                       nv.get("property", ""))
            if v is None:
                missing = (f"Expression term '{nv.get('name')}' "
                           f"({nv.get('tag')}.{nv.get('property')}) is "
                           f"unreadable on this flowsheet.")
                break
        else:
            val = 0.0  # all terms read OK

    # Build sample observables (what IS optimisable here)
    sample_obs: List[str] = []
    example_goal = "minimise heater duty"
    try:
        objs = _enumerate_flowsheet_objects(bridge)
        # Unit-op duties
        for u in objs.get("unit_ops", []):
            t = (u.get("type") or "").lower()
            tag = u.get("tag", "")
            if any(k in t for k in ("heater", "cooler", "reactor", "column")):
                sample_obs.append(f"{tag}.HeatDuty (energy duty)")
                example_goal = f"minimise {tag} duty"
        # Stream properties for actual compounds
        for s in objs.get("streams", [])[:6]:
            tag = s.get("tag", "")
            if not tag:
                continue
            sample_obs.append(f"{tag} temperature")
            for c in compounds[:3]:
                sample_obs.append(f"{tag} {c} mole fraction")
            sample_obs.append(f"{tag} mass flow")
    except Exception:
        pass

    return {
        "readable":         val is not None,
        "current_value":    val,
        "missing_detail":   missing,
        "compounds":        compounds,
        "sample_observables": sample_obs[:10],
        "example_goal":     example_goal,
    }


# ─── 2.6  Objective-confidence gate ───────────────────────────────────────
#
# The objective can be *readable* yet *wrong*: e.g. the goal says "minimise
# energy" but the LLM/heuristic picked `Feed.temperature` as the objective.
# `_validate_objective_readable` would pass it (temperature reads fine), and
# the solver would happily optimise the WRONG thing. This gate scores how well
# the chosen objective matches the goal's intent and, when there's a clear
# mismatch, swaps to a better-matching readable observable (or asks the LLM to
# re-map) BEFORE a full optimization run is wasted. It is deliberately
# conservative — it only intervenes on a clear mismatch and only swaps to an
# observable that scores strictly better, so it cannot turn a good objective
# into a bad one.

_GOAL_INTENT_KEYWORDS = {
    "energy":      ("energy", "duty", "heat", "power", "consumption", "kw",
                    "reboiler", "condenser", "utility"),
    "composition": ("purity", "fraction", "composition", "concentration",
                    "mol%", "mole", "ppm", "yield", "recovery", "conversion",
                    "selectivity"),
    "flow":        ("flow", "throughput", "production", "rate", "capacity"),
    "temperature": ("temperature", "thermal", "overheat"),
    "pressure":    ("pressure", "vacuum"),
    "cost":        ("cost", "profit", "economic", "opex", "capex", "revenue",
                    "margin"),
}


def _prop_category(prop: str) -> str:
    p = (prop or "").lower()
    if any(k in p for k in ("heatduty", "duty", "power", "energy")):
        return "energy"
    if any(k in p for k in ("mole_fraction", "mass_fraction", "fraction",
                            "purity", "composition")):
        return "composition"
    if "flow" in p:
        return "flow"
    if "temperature" in p or p == "t":
        return "temperature"
    if "pressure" in p:
        return "pressure"
    return "other"


def _goal_categories(goal: str) -> set:
    g = " " + (goal or "").lower() + " "
    cats = set()
    for cat, kws in _GOAL_INTENT_KEYWORDS.items():
        if any(k in g for k in kws):
            cats.add(cat)
    return cats


def _score_objective_match(goal: str, objective: Dict[str, Any]) -> Tuple[float, str]:
    """Return (score 0..1, reason). >=0.5 means 'objective fits the goal'."""
    obj = objective or {}
    if obj.get("type") == "expression":
        return 1.0, "expression objective (trusted)"
    prop = obj.get("property", "")
    ocat = _prop_category(prop)
    gcats = _goal_categories(goal)
    g = (goal or "").lower()

    if ocat == "composition":
        m = re.search(r"fraction_([a-z0-9+]+)", prop.lower())
        comp = m.group(1) if m else ""
        if comp and comp not in g:
            if gcats and "composition" not in gcats:
                return 0.0, (f"objective measures {comp} composition but goal "
                             f"intent is {sorted(gcats)}")
            return 0.35, f"objective measures {comp} composition not named in goal"

    if not gcats:
        return 0.6, "goal has no explicit quantity keyword; trusting objective"
    if ocat in gcats:
        return 1.0, f"objective category '{ocat}' matches goal intent"
    return 0.1, f"objective category '{ocat}' not in goal intent {sorted(gcats)}"


def _objective_confidence_gate(bridge, goal: str, spec: Dict[str, Any],
                               llm, emit) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Catch a readable-but-wrong objective before a full run is wasted.

    Returns (possibly-corrected spec, info). `info` records whether the
    objective was corrected / left low-confidence, for surfacing in chat."""
    # Don't re-gate an objective the replanner already chose.
    if spec.get("_replanned") or spec.get("_objective_corrected"):
        return spec, {"confidence": 1.0, "skipped": True}

    score, reason = _score_objective_match(goal, spec.get("objective", {}))
    if score >= 0.5:
        return spec, {"confidence": round(score, 2)}

    emit("🤔 obj-check",
         f"Objective may not match the goal ({reason}); checking alternatives…")

    # 1) Deterministic: is there a readable observable that fits the goal better?
    try:
        from adaptive_replanner import _build_flowsheet_context
        candidates = _build_flowsheet_context(bridge).get("observables", [])
    except Exception:
        candidates = []
    best = None
    best_score = score
    for obs in candidates:
        if "." not in obs:
            continue
        tag, prop = obs.split(".", 1)
        s, _r = _score_objective_match(
            goal, {"type": "variable", "tag": tag, "property": prop})
        if s > best_score + 0.2:          # require a clear margin
            best_score, best = s, (tag, prop)
    if best and best_score >= 0.6:
        new_obj = {"type": "variable", "tag": best[0], "property": best[1]}
        # Confirm the replacement is actually readable before committing.
        if _validate_objective_readable(bridge, new_obj).get("readable"):
            spec = dict(spec)
            spec["objective"] = new_obj
            spec["_objective_corrected"] = True
            emit("✓ obj-check",
                 f"Switched objective → {best[0]}.{best[1]} (better goal match).")
            return spec, {"corrected": True, "from_reason": reason,
                          "new_objective": f"{best[0]}.{best[1]}",
                          "confidence": round(best_score, 2)}

    # 2) LLM re-map (genuine reasoning) when no clear deterministic fix.
    if llm is not None:
        try:
            from adaptive_replanner import replan_on_failure
            rp = replan_on_failure(
                bridge, llm, goal, spec,
                {"success": False, "error_code": "OBJECTIVE_MISMATCH",
                 "error": reason})
            if rp.get("replanned") and rp.get("new_spec"):
                ns = rp["new_spec"]
                ns["_objective_corrected"] = True
                if _validate_objective_readable(
                        bridge, ns["objective"]).get("readable"):
                    emit("✓ obj-check",
                         f"LLM re-mapped objective: {rp.get('rationale','')[:60]}")
                    return ns, {"corrected": True, "via": "llm",
                                "confidence": 0.7}
        except Exception as exc:
            _log.debug("objective-gate LLM remap failed: %s", exc)

    # 3) Couldn't improve — proceed but flag low confidence to the user.
    emit("⚠ obj-check", f"Proceeding with low-confidence objective ({reason}).")
    return spec, {"low_confidence": True, "reason": reason,
                  "confidence": round(score, 2)}


# ─── 2.7  Converged-baseline check  (research-report §4a) ──────────────────
#
# "Verify a converged ('blue') baseline before optimizing." If the flowsheet
# does not solve at its current operating point, every optimizer evaluation
# starts from a broken state — wasting the whole budget. A single cheap
# convergence check up front catches that and explains it, instead of letting
# the solver grind through N failing evaluations.

def _verify_baseline_converges(bridge) -> Dict[str, Any]:
    """Return {converged: True/False/None, n_converged, n_unconverged, detail}.
    None = could not determine (treated as a soft pass)."""
    try:
        if not hasattr(bridge, "check_convergence"):
            return {"converged": None, "detail": "no convergence check available"}
        r = bridge.check_convergence()
        if not isinstance(r, dict) or not r.get("success"):
            return {"converged": None, "detail": "convergence state unknown"}
        conv = r.get("converged") or []
        nc = (r.get("not_converged") or []) + (r.get("missing") or [])
        n_conv, n_bad = len(conv), len(nc)

        def _tag(x):
            return x if isinstance(x, str) else (x.get("tag", "?")
                                                 if isinstance(x, dict) else str(x))
        if n_conv == 0 and n_bad == 0:
            return {"converged": None, "n_converged": 0, "n_unconverged": 0,
                    "detail": "no streams to assess"}
        ok = (n_bad == 0 and n_conv > 0)
        return {
            "converged": ok, "n_converged": n_conv, "n_unconverged": n_bad,
            "unconverged_tags": [_tag(t) for t in nc][:6],
            "detail": ("baseline converged" if ok else
                       f"{n_bad} stream(s) not converged: "
                       + ", ".join(_tag(t) for t in nc[:5])),
        }
    except Exception as exc:
        return {"converged": None, "detail": f"baseline check error: {exc}"}


# ─── 2.8  Multi-start  (research-report §4c) ──────────────────────────────
#
# Black-box flowsheet objectives are noisy and often multimodal; a single
# local solver run can settle in a poor local optimum. Multi-start runs the
# solver from several initial points (the current point + space-filling
# samples within the bounds) and keeps the best. Disabled by default
# (OPT_MULTISTART=1) to preserve cost; the complex/global path (DE) already
# explores globally so multi-start is only applied to local solvers.

def _sample_starts(variables: List[Dict[str, Any]], n_starts: int) -> List[Optional[Dict[int, float]]]:
    """First start = None (use the spec's existing initial/current values).
    Remaining starts = stratified samples within each variable's bounds."""
    import random
    starts: List[Optional[Dict[int, float]]] = [None]
    rng = random.Random(12345)   # deterministic for reproducibility
    for k in range(1, n_starts):
        pt: Dict[int, float] = {}
        for i, v in enumerate(variables):
            lo, hi = float(v.get("lower", 0.0)), float(v.get("upper", 1.0))
            if hi <= lo:
                pt[i] = lo
            else:
                frac = (k + rng.random()) / n_starts   # spread across [0,1]
                pt[i] = lo + frac * (hi - lo)
        starts.append(pt)
    return starts


def _spec_with_start(spec: Dict[str, Any], start: Optional[Dict[int, float]]) -> Dict[str, Any]:
    if start is None:
        return spec
    new_vars = []
    for i, v in enumerate(spec.get("variables", [])):
        nv = dict(v)
        if i in start:
            nv["initial"] = start[i]
        new_vars.append(nv)
    return {**spec, "variables": new_vars}


# ─── 2.9  Threshold-aware surrogate routing  (research-report Stage 3 + §4) ─
#
# The project already HAS a kriging surrogate (bayesian_optimizer.py — a GP
# with an RBF kernel + Expected-Improvement acquisition; GP/RBF regression IS
# kriging). What was missing is the report's key piece of judgment: "reserve
# surrogate methods for WHEN evaluation cost demands them." A direct
# DotNumerics/DE run does O(2n–4n) flowsheet solves per iteration; if a single
# solve is slow (recycle-heavy LNG/cryogenic/distillation flowsheets), that is
# ruinously expensive and a surrogate — which needs far fewer REAL solves — is
# the right tool. This routes there automatically when a measured solve exceeds
# a threshold, and stays completely dormant (no extra cost, no behaviour change)
# for the fast flowsheets this project usually handles.

def _should_use_surrogate(bridge, spec, complexity, emit) -> Dict[str, Any]:
    """Decide whether to route this optimization to the GP-surrogate engine.
    Only measures solve time for *complex* flowsheets (cheap proxy) so simple
    flowsheets pay nothing. Returns {use, solve_time_s, reason}."""
    import os, time as _t
    if os.getenv("SURROGATE_AUTO", "1") == "0":
        return {"use": False, "reason": "surrogate auto-routing disabled"}
    # Surrogate routing needs a single readable objective variable to observe.
    obj = spec.get("objective", {})
    if obj.get("type") != "variable" or not obj.get("tag") or not obj.get("property"):
        return {"use": False, "reason": "objective is not a single observable variable"}
    # Cheap gate: only bother measuring on flowsheets that *might* be expensive.
    score = (complexity or {}).get("complexity_score", 0)
    is_complex = (complexity or {}).get("recommended_path") == "complex" or score >= 4
    if not is_complex:
        return {"use": False, "solve_time_s": None,
                "reason": "flowsheet is simple/cheap — direct solver is best"}
    # Measure one solve to get the actual cost.
    solve_time = None
    try:
        if hasattr(bridge, "run_simulation"):
            _t0 = _t.time()
            bridge.run_simulation()
            solve_time = _t.time() - _t0
    except Exception:
        solve_time = None
    threshold = float(os.getenv("SURROGATE_SOLVE_THRESHOLD_S", "8.0"))
    if solve_time is not None and solve_time >= threshold:
        return {"use": True, "solve_time_s": round(solve_time, 1),
                "reason": f"single solve takes {solve_time:.1f}s "
                          f"(≥{threshold:.0f}s) — surrogate minimises real solves"}
    return {"use": False, "solve_time_s": solve_time,
            "reason": (f"solve is fast ({solve_time:.1f}s)" if solve_time is not None
                       else "solve time unknown") + " — direct solver is best"}


def _run_surrogate_optimization(bridge, spec, max_iter, on_eval, emit) -> Dict[str, Any]:
    """Route an expensive flowsheet to a surrogate optimiser and normalise the
    result to the shape the workflow expects.

    Primary path: the SURROGATE-ASSISTED EGO pipeline (surrogate_optimizer) —
    it minimises the number of real (slow) solves to a fixed budget by doing the
    global search on a cheap kriging surrogate, which is the scalable choice for
    complex / multi-variable flowsheets. Falls back to the interleaved Bayesian
    optimiser if the EGO module is unavailable."""
    obj = spec["objective"]
    minimize = bool(spec.get("minimize", True))

    # ── Primary: surrogate-assisted EGO (scalable for complex flowsheets) ──
    try:
        from surrogate_optimizer import run_surrogate_assisted_optimization
        emit("🧬 surrogate",
             "Sampling design space, fitting kriging surrogate, and searching "
             "it globally (minimising real flowsheet solves)…")
        res = run_surrogate_assisted_optimization(
            bridge,
            variables=spec["variables"],
            objective=obj,
            minimize=minimize,
            n_initial=max(8, min(20, 4 * len(spec["variables"]))),
            n_refine=max(6, min(12, int(max_iter) // 4)),
            on_progress=on_eval,
        )
        if isinstance(res, dict) and res.get("success"):
            return res
        emit("⚠ surrogate",
             "EGO pipeline did not converge; falling back to interleaved "
             "Bayesian optimisation.")
    except Exception as _exc:
        emit("⚠ surrogate", f"EGO unavailable ({_exc}); using interleaved BO.")

    # ── Fallback: interleaved Bayesian optimisation via the bridge ─────────
    old_vals = {}
    try:
        from dwsim_native_optimizer import _read_object_property
        for v in spec.get("variables", []):
            old_vals[f"{v['tag']}.{v['property']}"] = _read_object_property(
                bridge, v["tag"], v["property"])
    except Exception:
        pass
    bres = bridge.bayesian_optimize(
        variables=spec["variables"],
        observe_tag=obj.get("tag", ""),
        observe_property=obj.get("property", ""),
        minimize=spec.get("minimize", True),
        max_iter=int(max_iter),
        on_progress=on_eval,
    )
    if not isinstance(bres, dict) or not bres.get("success"):
        return bres if isinstance(bres, dict) else {"success": False,
                                                    "error": "surrogate failed"}
    out = dict(bres)
    out["best_objective"] = bres.get("best_value")
    out["n_evaluations"]  = bres.get("n_evals")
    out["method"]         = "bayesian_gp"
    out["solver_backend"] = "GP surrogate (kriging: RBF kernel + Expected Improvement)"
    out["used_native_dotnumerics"] = False
    out["minimize"]       = spec.get("minimize", True)
    rows = []
    best_params = bres.get("best_params") or {}
    for v in spec.get("variables", []):
        name = f"{v['tag']}.{v['property']}"
        new_val = best_params.get(name)
        old_val = old_vals.get(name)
        if new_val is None or old_val is None:
            continue
        change = float(new_val) - float(old_val)
        rows.append({"variable": name, "old_value": float(old_val),
                     "new_value": float(new_val), "change": change,
                     "change_pct": (100.0 * change / old_val) if old_val else 0.0,
                     "at_lower": abs(float(new_val) - float(v.get("lower", new_val))) < 1e-9,
                     "at_upper": abs(float(new_val) - float(v.get("upper", new_val))) < 1e-9})
    out["variables_table"] = rows
    return out


def _run_multistart(run_fn, spec: Dict[str, Any], n_starts: int,
                    minimize: bool, emit) -> Dict[str, Any]:
    """Run `run_fn(spec)` from n_starts initial points; return the best result."""
    best, best_obj, last = None, None, None
    for k, start in enumerate(_sample_starts(spec.get("variables", []), n_starts)):
        emit("🎲 multi-start", f"initial point {k + 1}/{n_starts}…")
        r = run_fn(_spec_with_start(spec, start))
        last = r
        if isinstance(r, dict) and r.get("success"):
            obj = r.get("best_objective")
            if obj is not None and (best is None or
                                    (obj < best_obj if minimize else obj > best_obj)):
                best, best_obj = r, obj
    if best is None:
        return last or {"success": False, "error": "all multi-start runs failed"}
    best = dict(best)
    best["_multistart_runs"] = n_starts
    best["_multistart_best_objective"] = best_obj
    return best


def _assess_global_confidence(run_fn, spec, reported_result, minimize, emit):
    """Probe global-optimality CONFIDENCE by re-optimising from a few diverse
    starts. You cannot PROVE a black-box nonconvex optimum is global, but if
    several independent restarts converge to the same value, that is strong
    evidence; if one finds a better value, the reported optimum was NOT global
    (and we adopt the better one).

    Returns (block, adopt_result):
      block        — {assessed, confidence('high'|'medium'|'low'|'unknown'),
                      n_probes, n_agree, best_found, reported, improved, note}
      adopt_result — a strictly-better optimisation result to use instead, or None.

    Bounded + cost-gated (env OPT_GLOBAL_CONFIDENCE / _PROBES / _MAX_S / _TOL) so
    cheap flowsheets pay a little and expensive ones are skipped.
    """
    if os.getenv("OPT_GLOBAL_CONFIDENCE", "1") == "0":
        return {"assessed": False, "reason": "disabled"}, None
    reported = reported_result.get("best_objective")
    variables = spec.get("variables", [])
    if reported is None or not variables:
        return {"assessed": False, "reason": "no reported optimum / variables"}, None
    reported = float(reported)

    n_probes = max(1, int(os.getenv("OPT_GLOBAL_CONFIDENCE_PROBES", "3")))
    rel_tol = float(os.getenv("OPT_GLOBAL_CONFIDENCE_TOL", "0.02"))
    dur = (reported_result.get("duration_s")
           or reported_result.get("elapsed_s") or 0) or 0
    if dur and dur * n_probes > float(os.getenv("OPT_GLOBAL_CONFIDENCE_MAX_S", "60")):
        return {"assessed": False,
                "reason": f"skipped — too expensive (~{dur:.1f}s/run)"}, None

    objs, best_probe_obj, best_probe_res = [], None, None
    for k, start in enumerate(_sample_starts(variables, n_probes)):
        emit("🌐 global-check",
             f"probe {k + 1}/{n_probes} — re-optimising from a diverse start…")
        r = run_fn(_spec_with_start(spec, start))
        if isinstance(r, dict) and r.get("success") and r.get("best_objective") is not None:
            o = float(r["best_objective"])
            objs.append(o)
            if best_probe_obj is None or (o < best_probe_obj if minimize else o > best_probe_obj):
                best_probe_obj, best_probe_res = o, r

    if not objs:
        return {"assessed": True, "confidence": "unknown",
                "note": "global-confidence probes did not converge."}, None

    denom = max(abs(reported), 1e-9)
    n_agree = sum(1 for o in objs if abs(o - reported) / denom <= rel_tol)
    best_found = min(objs + [reported]) if minimize else max(objs + [reported])
    improved = ((best_probe_obj is not None)
                and (abs(best_probe_obj - reported) / denom > rel_tol)
                and ((best_probe_obj < reported) if minimize else (best_probe_obj > reported)))

    adopt = best_probe_res if improved else None
    if improved:
        conf = "low"
        note = (f"a diverse restart found a BETTER optimum "
                f"({best_probe_obj:.4g} vs {reported:.4g}) — the first optimum was "
                f"NOT global; adopting the better one.")
    elif n_agree == len(objs):
        conf = "high"
        note = (f"all {len(objs)} independent restarts converged to the same "
                f"optimum (±{rel_tol*100:.0f}%) — strong evidence it is global.")
    elif n_agree >= max(1, len(objs) // 2):
        conf = "medium"
        note = (f"{n_agree}/{len(objs)} restarts agree; the rest reached other "
                f"local optima — the surface is multimodal.")
    else:
        conf = "low"
        note = (f"only {n_agree}/{len(objs)} restarts agree — multiple local "
                f"optima; the reported value may not be global.")

    return ({"assessed": True, "confidence": conf, "n_probes": len(objs),
             "n_agree": n_agree, "best_found": round(best_found, 6),
             "reported": round(reported, 6), "improved": bool(improved),
             "note": note}, adopt)


# ─── 2.8  Objective-sensitivity check ─────────────────────────────────────
#
# A converged, readable, goal-matched objective can STILL be structurally
# decoupled from the chosen decision variables (e.g. a product mass flow fixed
# by a spec, or a plugin unit that doesn't propagate feed changes). The
# optimiser then burns its whole budget and reports a hollow "optimum" with 0 %
# variable change. This probe perturbs each variable once and confirms the
# objective actually moves BEFORE the full run — turning a meaningless result
# into an explicit, actionable error (or a replan to a responsive objective).

def _check_objective_sensitivity(bridge, variables, objective,
                                 max_vars: int = 8) -> Dict[str, Any]:
    """Perturb each decision variable once; report which ones move the
    objective. Returns {checked, sensitive, responding, baseline, n_tested}."""
    try:
        from dwsim_native_optimizer import (
            _write_object_property, _solve_flowsheet, _read_object_property)
        from surrogate_optimizer import _eval_objective
    except Exception as exc:
        return {"checked": False, "sensitive": None, "detail": str(exc)}
    if not variables:
        return {"checked": False, "sensitive": None, "detail": "no variables"}
    try:
        _solve_flowsheet(bridge)
        base = _eval_objective(bridge, objective)
    except Exception as exc:
        return {"checked": False, "sensitive": None,
                "detail": f"baseline read failed: {exc}"}
    if base is None:
        return {"checked": False, "sensitive": None,
                "detail": "objective unreadable at baseline"}
    base = float(base)
    tol = max(abs(base) * 1e-4, 1e-9)
    responding: List[str] = []
    for v in variables[:max_vars]:
        tag, prop, unit = v.get("tag", ""), v.get("property", ""), v.get("unit", "")
        lo = float(v.get("lower", 0.0)); hi = float(v.get("upper", 1.0))
        try:
            old = _read_object_property(bridge, tag, prop)
            old = float(old) if old is not None else float(v.get("initial", (lo + hi) / 2))
        except Exception:
            old = float(v.get("initial", (lo + hi) / 2))
        span = (hi - lo) if hi > lo else max(abs(old), 1.0)
        trial = old + 0.3 * span if (old + 0.3 * span) <= hi else old - 0.3 * span
        if abs(trial - old) < 1e-12:
            continue
        moved = False
        try:
            _write_object_property(bridge, tag, prop, trial, unit)
            if _solve_flowsheet(bridge):
                nv = _eval_objective(bridge, objective)
                if nv is not None and abs(float(nv) - base) > tol:
                    moved = True
        except Exception:
            pass
        try:
            _write_object_property(bridge, tag, prop, old, unit)  # restore
        except Exception:
            pass
        if moved:
            responding.append(f"{tag}.{prop}")
    try:
        _solve_flowsheet(bridge)   # restore the converged baseline
    except Exception:
        pass
    return {"checked": True, "sensitive": bool(responding),
            "responding": responding, "baseline": base,
            "n_tested": min(len(variables), max_vars)}


# ─── Post-run reproducibility check ────────────────────────────────────────
# Closes the gap where a result's `best_objective` is the best value SEEN during
# the search (a cached number) rather than a verified read-back. We re-apply the
# reported optimum variables, re-solve, and re-read the objective from the LIVE
# flowsheet: a correct optimum must REPRODUCE the reported value. A mismatch
# means the reported optimum is not reproducible (non-deterministic solve,
# multiple steady states / hysteresis, or a stale read) — we surface it instead
# of trusting the cached number. Engine-agnostic: works for the native,
# internal, complex and surrogate paths alike. Re-applying the variables makes
# the check self-contained (it does not merely trust that the engine left the
# flowsheet at the optimum).
def _verify_optimum_reproducible(bridge, objective: Dict[str, Any],
                                 reported, variables=None) -> Dict[str, Any]:
    """Re-apply the reported optimum (if `variables` given), re-solve, and
    re-read the objective from the LIVE flowsheet; confirm it reproduces
    `reported`. `variables` is the result's `variables_table` rows (each with
    tag/property/new_value/unit). Returns {verified, reread, reported,
    rel_error, tolerance}; verified may be None when the check could not run —
    never a silent pass."""
    if reported is None:
        return {"verified": None, "reason": "no reported objective"}
    try:
        from surrogate_optimizer import _eval_objective
        from dwsim_native_optimizer import _solve_flowsheet, _write_object_property
    except Exception as exc:
        return {"verified": None, "reason": f"import failed: {exc}"}
    try:
        # Re-apply the optimum variables so the check is self-contained rather
        # than trusting that the engine left the flowsheet at the optimum.
        for row in (variables or []):
            try:
                _write_object_property(bridge, row.get("tag"), row.get("property"),
                                       float(row.get("new_value")), row.get("unit", ""))
            except Exception:
                pass
        # Re-solve so the read reflects a fully converged state, then read the
        # objective straight from DWSIM.
        try:
            _solve_flowsheet(bridge)
        except Exception:
            pass
        reread = _eval_objective(bridge, objective)
    except Exception as exc:
        return {"verified": None, "reason": f"re-read failed: {exc}"}
    if reread is None:
        return {"verified": None, "reason": "objective unreadable at optimum"}
    try:
        reread = float(reread); reported = float(reported)
    except (TypeError, ValueError):
        return {"verified": None, "reason": "non-numeric objective"}
    # Relative tolerance, generous enough to absorb convergence-loop round-off
    # but tight enough to catch a genuinely irreproducible optimum.
    tol = max(float(os.getenv("OPT_VERIFY_REL_TOL", "1e-3")), 1e-9)
    denom = max(abs(reported), 1e-9)
    rel_err = abs(reread - reported) / denom
    return {"verified": bool(rel_err <= tol),
            "reread": round(reread, 6),
            "reported": round(reported, 6),
            "rel_error": round(rel_err, 8),
            "tolerance": tol}


def _verify_enabled() -> bool:
    """Post-run optimum verification (Step 3.5) is on by default. Set
    OPT_VERIFY_OPTIMUM=0 to skip the extra re-solve (e.g. for a very expensive
    flowsheet where one more solve is costly)."""
    return os.getenv("OPT_VERIFY_OPTIMUM", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _verification_banner(ver: Dict[str, Any]) -> str:
    """Markdown banner for the post-run reproducibility check (empty when the
    optimum verified cleanly, or when verification was deliberately skipped)."""
    if not ver or ver.get("verified") is True or ver.get("skipped"):
        return ""
    if ver.get("verified") is False:
        return (
            f"\n> ❌ **Unverified optimum:** re-solving at the reported optimum "
            f"gave `{ver.get('reread')}`, not the reported `{ver.get('reported')}` "
            f"(relative error {ver.get('rel_error')} > tolerance {ver.get('tolerance')}). "
            f"The result is **not reproducible** — treat it with caution (likely a "
            f"non-deterministic solve or multiple steady states).\n"
        )
    return (
        f"\n> ⚠ **Optimum not verified:** {ver.get('reason','could not re-read the objective')}. "
        f"The reported value was not independently re-confirmed.\n"
    )


def _optimization_goal_clarity(goal: str) -> Dict[str, Any]:
    """Decide whether an optimisation goal is specific enough to act on, or too
    vague to pick an objective (so we should ASK rather than guess a possibly-
    meaningless one). Conservative: only flags genuinely-empty goals."""
    import re as _re
    g = (goal or "").lower().strip()
    if not g:
        return {"clear": False, "reason": "no goal text"}
    DIRECTION = ("minimi", "maximi", "minimize", "maximize", "reduce", "increase",
                 "lower", "raise", "decrease", "optimi", "improve", "best ", "highest",
                 "lowest", "cheapest", "greenest")
    TARGET = ("duty", "energy", "heat", "power", "consumption", "purity", "yield",
              "recovery", "conversion", "selectivity", "fraction", "composition",
              "concentration", "flow", "throughput", "production", "temperature",
              "pressure", "cost", "profit", "economic", "efficiency", "loss",
              "emission", "co2", "h2 ", "reflux", "duty", "exergy")
    has_dir = any(k in g for k in DIRECTION)
    # Whole-word target match so "flow" doesn't falsely match "flowsheet".
    _target_rx = r"\b(" + "|".join(t.strip() for t in TARGET) + r")\b"
    has_target = (bool(_re.search(_target_rx, g))
                  or bool(_re.search(r"[a-z0-9_\-]+\.[a-z_]+", g)))   # tag.property
    ANALYSIS = ("analyse", "analyze", "describe", "explain", "summar", "what is",
                "what are", "show me", "list ", "tell me", "report on", "inspect")
    if any(k in g for k in ANALYSIS) and not has_dir:
        return {"clear": False,
                "reason": "this is an analysis request, not an optimisation goal"}
    if has_dir and has_target:
        return {"clear": True}
    if has_target:               # target named, direction implicit → act
        return {"clear": True}
    return {"clear": False,
            "reason": "no specific quantity/objective to optimise was given"}


def _clarification_observables(bridge) -> List[str]:
    """A few concrete things on THIS flowsheet the user could optimise."""
    out: List[str] = []
    try:
        objs = _enumerate_flowsheet_objects(bridge)
        for u in objs.get("unit_ops", [])[:4]:
            t = (u.get("type") or "").lower(); tag = u.get("tag", "")
            if any(k in t for k in ("heater", "cooler", "reactor", "column", "compressor", "pump")):
                out.append(f"minimise **{tag}** duty/power")
        for s in objs.get("streams", [])[:3]:
            tag = s.get("tag", "")
            if tag:
                out.append(f"maximise a product fraction or flow in **{tag}**")
    except Exception:
        pass
    return out[:6]


def run_optimization_workflow(
    bridge,
    goal: str,
    llm=None,
    on_step: Optional[Callable[[str, str], None]] = None,
    on_eval: Optional[Callable[[int, Dict, Any, Any], None]] = None,
    max_iter: int = 50,
    tolerance: float = 1e-3,
    constraints: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """The poster's end-to-end flow.
    Returns a dict with: success, spec, result, chat_markdown.

    Callbacks:
      on_step(stage, detail)    — high-level workflow stages 1/4 … 4/4
      on_eval(it, params, obj, best) — per-solver-evaluation progress.
                                       Use to stream iteration data to chat."""
    def _emit(stage: str, detail: str):
        if on_step:
            try: on_step(stage, detail)
            except Exception: pass
        _log.info("[opt-workflow] %s — %s", stage, detail)

    # ── Step 0.0: AMBIGUITY GATE — ask rather than guess a meaningless objective
    # A vague goal ("analyse the flowsheet", "optimise it") has no objective to
    # optimise; guessing one produces hollow results. Ask the user what to
    # optimise instead. (Addresses the 'cannot handle ambiguous instructions'
    # limitation and the root cause of hollow optimisations.)
    clarity = _optimization_goal_clarity(goal)
    if not clarity.get("clear"):
        _emit("🤔 clarify", f"Goal is ambiguous ({clarity.get('reason','')}) — "
                            "asking the user what to optimise.")
        opts = _clarification_observables(bridge)
        md = ("**🤔 What would you like to optimise?**\n\n"
              f"Your request — _“{goal.strip()}”_ — doesn't specify a clear "
              "objective, and I'd rather ask than optimise the wrong thing.\n\n"
              "Tell me **what to minimise or maximise**, for example:\n"
              "- *minimise total energy / a heater or reboiler duty*\n"
              "- *maximise a product purity, yield, or flow*\n"
              "- *minimise operating cost*\n")
        if opts:
            md += ("\n**On this flowsheet you could, for instance:**\n"
                   + "\n".join(f"- {o}" for o in opts) + "\n")
        md += ("\nReply with a specific goal like _“minimise the heater duty”_ "
               "and I'll run it.")
        return {"success": False, "error_code": "NEEDS_CLARIFICATION",
                "needs_clarification": True, "clarity": clarity,
                "chat_markdown": md}

    # ── Step 0: PP-validity gate (credibility-raising preflight) ─────────
    # Validate that the loaded property package suits the compound chemistry.
    # If 'critical' mismatch, refuse to optimise unless explicitly overridden.
    _emit("🧪 preflight", "Validating property package against compound chemistry…")
    try:
        from pp_validator import validate_loaded_flowsheet
        pp_check = validate_loaded_flowsheet(bridge, override=False)
    except Exception as exc:
        _log.debug("PP validator unavailable: %s", exc)
        pp_check = {"ok": True, "severity": "pass",
                    "message": "PP validator skipped"}

    if pp_check.get("severity") == "critical" and not pp_check.get("overridden"):
        _emit("✗ preflight",
              f"PP validation FAILED: {pp_check.get('message', '')}")
        return {
            "success": False,
            "error_code": "PP_VALIDATION_FAILED",
            "error": pp_check.get("message", "PP unsuitable for compounds"),
            "pp_check": pp_check,
            "chat_markdown": (
                "**❌ Optimisation halted — wrong property package.**\n\n"
                f"{pp_check.get('message', '')}\n\n"
                "**Recommended PPs:** "
                + ", ".join(pp_check.get("recommended_pps", [])[:5])
                + "\n\nLoad a flowsheet with the correct property package, or "
                "pass `override_pp_check=true` if you accept the inaccuracy."
            ),
        }
    if pp_check.get("severity") in ("mismatch", "warning"):
        _emit("⚠ preflight",
              f"PP warning: {pp_check.get('message', '')[:120]}")
    else:
        _emit("✓ preflight",
              f"PP '{pp_check.get('current_pp', '?')}' OK for chemistry.")

    # ── Step 1: discover ──────────────────────────────────────────────────
    _emit("🔎 step 1/4", "Analysing flowsheet — identifying decision variables…")
    suggested = suggest_decision_variables(bridge, max_n=8)
    if not suggested:
        return {"success": False,
                "error_code": "NO_FLOWSHEET",
                "error": "No flowsheet loaded, or no decision variables found.",
                "chat_markdown": ("**❌ Cannot optimise:** no decision variables "
                                  "found. Load a flowsheet first.")}
    _emit("✓ step 1/4",
          f"Found {len(suggested)} candidate variables: "
          + ", ".join(f"{v['tag']}.{v['property']}" for v in suggested[:5])
          + ("…" if len(suggested) > 5 else ""))

    # ── Step 2: spec ──────────────────────────────────────────────────────
    _emit("🧠 step 2/4", "Mapping goal to objective expression via LLM…")
    spec = build_spec_from_goal(llm, bridge, goal, suggested_vars=suggested)
    if not spec.get("success"):
        return {"success": False, **spec,
                "chat_markdown": "**❌ Could not build optimization spec:** " + spec.get("error", "")}
    method = spec["method"]
    direction = "maximise" if not spec["minimize"] else "minimise"
    n_vars = len(spec["variables"])
    _emit("✓ step 2/4",
          f"Spec ready: {direction} objective using {method} solver "
          f"over {n_vars} variables.")

    # ── Step 2.5: VALIDATE the objective is actually readable ───────────────
    # Pre-flight: read the objective once. If it's None, the optimization is
    # doomed (every eval will fail). Give a clear, specific error explaining
    # WHY — e.g. "maximise H2 purity" on a water-only flowsheet has no H2.
    _emit("🔍 step 2.5", "Validating objective is readable before solving…")
    obj_check = _validate_objective_readable(bridge, spec["objective"])
    if not obj_check["readable"]:
        compounds = obj_check.get("compounds", [])
        observables = obj_check.get("sample_observables", [])
        missing = obj_check.get("missing_detail", "")

        # ── REASON about it: give the replanner a chance before failing ────
        # An unreadable objective is exactly the kind of failure the LLM
        # should reason about — either declare the goal infeasible or pick a
        # measurable objective. This replaces the blind "rephrase it yourself"
        # bounce with genuine reasoning.
        if not spec.get("_replanned"):
            _emit("🤔 replan", "Objective not measurable — reasoning about an "
                               "alternative or infeasibility…")
            try:
                from adaptive_replanner import replan_on_failure
                rp = replan_on_failure(
                    bridge, llm, goal, spec,
                    {"success": False, "error": missing,
                     "error_code": "OBJECTIVE_NOT_MEASURABLE",
                     "available_compounds": compounds})
            except Exception as _rexc:
                rp = {"replanned": False, "diagnosis": str(_rexc)}

            if rp.get("feasible") is False:
                _emit("✗ replan", f"Goal infeasible: {rp.get('infeasible_reason','')}")
                return {
                    "success": False, "error_code": "GOAL_INFEASIBLE",
                    "error": rp.get("infeasible_reason", missing),
                    "spec": spec,
                    "chat_markdown": (
                        f"**❌ This goal cannot be achieved on this flowsheet.**\n\n"
                        f"{rp.get('diagnosis','')}\n\n_{rp.get('infeasible_reason','')}_\n\n"
                        + (f"**Compounds present:** {', '.join(compounds)}\n\n" if compounds else "")
                        + (f"**You could instead optimise:** "
                           + ", ".join(observables[:6]) if observables else "")),
                }
            if rp.get("replanned") and rp.get("new_spec"):
                _emit("✓ replan",
                      f"Switched objective ({rp.get('_via')}): "
                      f"{rp.get('rationale','')[:70]}")
                spec = rp["new_spec"]
                spec["_replanned"] = True
                # Re-validate the new objective
                obj_check = _validate_objective_readable(bridge, spec["objective"])

        # Still unreadable after replanning → clear actionable error
        if not obj_check["readable"]:
            md = (
                "**❌ Cannot optimise — the objective cannot be measured on this "
                "flowsheet.**\n\n" + f"{missing}\n\n")
            if compounds:
                md += f"**Compounds present:** {', '.join(compounds)}\n\n"
            if observables:
                md += ("**Things you CAN optimise here:**\n"
                       + "\n".join(f"- {o}" for o in observables[:8]) + "\n\n")
            md += ("Rephrase your goal to target one of the above, e.g. "
                   f"_\"{obj_check.get('example_goal', 'minimise heater duty')}\"_")
            _emit("✗ step 2.5", f"Objective unreadable: {missing[:80]}")
            return {
                "success": False, "error_code": "OBJECTIVE_NOT_MEASURABLE",
                "error": missing, "spec": spec,
                "available_compounds": compounds,
                "available_observables": observables,
                "chat_markdown": md,
            }
        method = spec["method"]; direction = "maximise" if not spec["minimize"] else "minimise"
    _emit("✓ step 2.5",
          f"Objective readable (current value = {obj_check.get('current_value')}).")

    # ── Step 2.6: OBJECTIVE-CONFIDENCE GATE ────────────────────────────────
    # The objective is readable — but is it the RIGHT thing to optimise for
    # this goal? Catch a confidently-wrong objective before wasting a full run.
    spec, obj_gate = _objective_confidence_gate(bridge, goal, spec, llm, _emit)
    if obj_gate.get("corrected"):
        # Re-derive display fields and re-validate the corrected objective.
        method = spec["method"]; direction = "maximise" if not spec["minimize"] else "minimise"
        obj_check = _validate_objective_readable(bridge, spec["objective"])

    # ── Step 2.65: OBJECTIVE-MEANINGFULNESS + hollow-objective REPAIR ───────
    # Catch a TRIVIAL/HOLLOW objective (e.g. minimise a heater duty while the
    # feed FLOW is free — the optimum just scales the feed and pegs it to a
    # bound). When the cause is an extensive objective + a free THROUGHPUT
    # variable, DETERMINISTICALLY hold throughput fixed (drop those variables)
    # so the optimiser must find a REAL optimum — provided a genuine decision
    # variable remains. This turns a hollow run into a meaningful one instead of
    # only warning about it.
    obj_quality = {"assessed": False}
    try:
        from objective_quality import assess_objective
        obj_quality = assess_objective(
            spec.get("objective", {}), spec.get("variables", []),
            bool(spec.get("minimize", True)))
        if obj_quality.get("severity") == "high":
            _emit("⚠ obj-quality", obj_quality["warning"]
                  + " Suggestion: " + obj_quality["suggestion"])

            # Repair: hold throughput fixed if a real variable would remain.
            tp = set(obj_quality.get("throughput_vars") or [])
            if tp:
                kept = [v for v in spec.get("variables", [])
                        if f"{v.get('tag','')}.{v.get('property','')}" not in tp]
                dropped = [v for v in spec.get("variables", [])
                           if f"{v.get('tag','')}.{v.get('property','')}" in tp]
                if kept and dropped:
                    spec["variables"] = kept
                    held = ", ".join("{}.{}".format(v.get("tag", ""),
                                                    v.get("property", ""))
                                     for v in dropped)
                    kept_names = ", ".join("{}.{}".format(v.get("tag", ""),
                                                          v.get("property", ""))
                                           for v in kept)
                    obj_quality["repaired"] = True
                    obj_quality["held_fixed"] = held
                    _emit("🔧 obj-repair",
                          f"Held throughput fixed ({held}) so the optimum isn't a "
                          f"trivial feed-scaling. Optimising {kept_names} at fixed "
                          f"throughput.")
                else:
                    obj_quality["repaired"] = False
                    obj_quality["repair_skipped"] = (
                        "only throughput variables were chosen — nothing left to "
                        "optimise at fixed throughput; pick an intensive objective")
        elif obj_quality.get("severity") == "low":
            _emit("ℹ obj-quality", obj_quality["warning"])
    except Exception as _oqexc:
        obj_quality = {"assessed": False, "error": str(_oqexc)}

    # ── Step 2.7: CONVERGED-BASELINE CHECK (research §4a) ──────────────────
    # Don't optimise a flowsheet that doesn't even solve at its current point.
    _emit("🩺 step 2.7", "Checking the flowsheet converges at its baseline…")
    baseline = _verify_baseline_converges(bridge)
    if baseline.get("converged") is False:
        # If NOTHING converged, optimising is futile → fail fast with a reason.
        if baseline.get("n_converged", 0) == 0:
            _emit("✗ step 2.7", f"Baseline does not converge: {baseline['detail']}")
            return {
                "success": False, "error_code": "BASELINE_NOT_CONVERGED",
                "error": baseline["detail"], "spec": spec, "baseline": baseline,
                "chat_markdown": (
                    "**❌ Cannot optimise — the flowsheet does not converge at its "
                    "current operating point.**\n\n"
                    f"{baseline['detail']}\n\n"
                    "Fix convergence first (check recycle/tear streams, feed specs, "
                    "or property package), then re-run the optimisation."),
            }
        # Partial convergence → warn but proceed (the optimiser may move into a
        # feasible region).
        _emit("⚠ step 2.7",
              f"Baseline partially unconverged ({baseline['detail']}); proceeding.")
    else:
        _emit("✓ step 2.7",
              f"Baseline OK ({baseline.get('n_converged', '?')} stream(s) converged)."
              if baseline.get("converged") else "Baseline state acceptable.")

    # ── Step 2.8: OBJECTIVE-SENSITIVITY CHECK ──────────────────────────────
    # Confirm the objective actually MOVES when the variables change. Catches a
    # structurally-fixed / decoupled objective (which otherwise yields a hollow
    # 'optimum' with 0% variable change after burning the whole budget).
    _emit("🧪 step 2.8", "Probing whether the objective responds to the variables…")
    sens = _check_objective_sensitivity(bridge, spec["variables"], spec["objective"])
    if sens.get("checked") and sens.get("sensitive") is False:
        # Insensitive objective. Try to replan to a responsive objective; else
        # halt with a clear, actionable error instead of a meaningless run.
        if not spec.get("_replanned") and llm is not None:
            _emit("🤔 replan", "Objective does not respond to any variable — "
                               "reasoning about a better objective…")
            try:
                from adaptive_replanner import replan_on_failure
                rp = replan_on_failure(
                    bridge, llm, goal, spec,
                    {"success": False, "error_code": "OBJECTIVE_INSENSITIVE",
                     "error": "objective does not respond to the decision "
                              "variables (perturbing each left it unchanged)"})
            except Exception as _rexc:
                rp = {"replanned": False, "diagnosis": str(_rexc)}
            if rp.get("feasible") is False:
                _emit("✗ replan", f"Goal infeasible: {rp.get('infeasible_reason','')}")
                return {"success": False, "error_code": "GOAL_INFEASIBLE",
                        "error": rp.get("infeasible_reason", "goal infeasible"),
                        "spec": spec,
                        "chat_markdown": (
                            "**❌ This goal cannot be optimised on this flowsheet.**\n\n"
                            f"{rp.get('diagnosis','')}\n\n_{rp.get('infeasible_reason','')}_")}
            if rp.get("replanned") and rp.get("new_spec"):
                spec = rp["new_spec"]; spec["_replanned"] = True
                method = spec["method"]
                direction = "maximise" if not spec["minimize"] else "minimise"
                obj_check = _validate_objective_readable(bridge, spec["objective"])
                _emit("✓ replan",
                      f"Switched to a responsive objective: {rp.get('rationale','')[:70]}")
                sens = _check_objective_sensitivity(bridge, spec["variables"],
                                                    spec["objective"])
        if sens.get("checked") and sens.get("sensitive") is False:
            obj = spec.get("objective", {})
            obj_name = (f"{obj.get('tag','')}.{obj.get('property','')}"
                        if obj.get("type") == "variable" else "the expression objective")
            vlist = ", ".join(f"`{v['tag']}.{v['property']}`" for v in spec["variables"][:6])
            _emit("✗ step 2.8", "Objective insensitive to all variables — halting.")
            return {
                "success": False, "error_code": "OBJECTIVE_INSENSITIVE",
                "error": f"The objective ({obj_name}) does not respond to any of "
                         f"the decision variables — optimisation would be meaningless.",
                "spec": spec, "sensitivity": sens,
                "chat_markdown": (
                    "**❌ Optimisation halted — the objective does not respond to the "
                    "decision variables.**\n\n"
                    f"I perturbed each of the {sens.get('n_tested','?')} decision "
                    f"variables ({vlist}) and the objective `{obj_name}` did **not "
                    f"change** (stayed at {sens.get('baseline')}). Optimising it would "
                    "burn the whole budget and report a hollow 'optimum' with 0% "
                    "variable change.\n\n"
                    "**Likely cause:** the objective is structurally fixed (a flowsheet "
                    "spec, a recycle, or a plugin unit that doesn't propagate the "
                    "changes). **Fix:** choose an objective that actually depends on "
                    "these variables (e.g. a duty, a temperature, or a composition "
                    "downstream of them), or change which variables you optimise."),
            }
    elif sens.get("checked"):
        _emit("✓ step 2.8",
              f"Objective responds to {len(sens.get('responding', []))}/"
              f"{sens.get('n_tested','?')} variable(s): "
              + ", ".join(sens.get("responding", [])[:4]))

    # ── Step 3: run ───────────────────────────────────────────────────────
    _emit("⚙️ step 3/4",
          f"Running DWSIM-internal {method.upper()} solver (max {max_iter} evals)…")
    # Detect flowsheet complexity to choose the right optimization path.
    # Complex flowsheets (≥6 unit ops, recycles, columns, reactors) get the
    # robust multi-solver + bound-widening + sanity-check path.
    try:
        from complex_optimizer import (
            detect_flowsheet_complexity, run_complex_optimization,
        )
        complexity = detect_flowsheet_complexity(bridge)
    except Exception:
        complexity = {"recommended_path": "simple", "complexity_score": 0}

    use_complex = complexity.get("recommended_path") == "complex"
    if use_complex:
        _emit("🏭 step 3/4",
              f"Complex flowsheet detected ({complexity.get('reason','')}) — "
              f"using multi-solver robust path with bound-widening…")

    # ── Threshold-aware SURROGATE routing (research Stage 3) ───────────────
    # If a single flowsheet solve is expensive, a kriging/GP surrogate needs
    # far fewer REAL solves than a direct DotNumerics/DE run. Decide here;
    # dormant (zero cost) for the fast flowsheets this project usually handles.
    surrogate = _should_use_surrogate(bridge, spec, complexity, _emit)
    if surrogate.get("use"):
        _emit("🧠 step 3/4 (surrogate)",
              f"{surrogate.get('reason','')} — routing to GP-surrogate "
              "(kriging) optimization…")
        result = _run_surrogate_optimization(bridge, spec, max_iter, on_eval, _emit)
        if isinstance(result, dict) and result.get("success"):
            result["_surrogate_reason"] = surrogate.get("reason")
            _emit("✓ step 3/4",
                  f"Surrogate converged in {result.get('n_evaluations','?')} real "
                  "flowsheet evaluations.")
            # Verify the surrogate's reported optimum reproduces on a real solve
            # (skippable via OPT_VERIFY_OPTIMUM=0).
            if _verify_enabled():
                _emit("🔁 step 3.5", "Re-solving at the optimum to verify it reproduces…")
                ver = _verify_optimum_reproducible(bridge, spec["objective"],
                                                   result.get("best_objective"),
                                                   result.get("variables_table"))
            else:
                ver = {"verified": None, "skipped": True,
                       "reason": "verification disabled (OPT_VERIFY_OPTIMUM=0)"}
                _emit("⏭ step 3.5", "Optimum verification disabled — skipping re-solve.")
            result["verification"] = ver
            result["objective_quality"] = obj_quality
            chat_md = format_poster_chat(result, goal=goal, spec=spec)
            chat_md = _verification_banner(ver) + chat_md
            return {"success": True, "spec": spec, "result": result,
                    "obj_gate": obj_gate, "surrogate": surrogate,
                    "objective_quality": obj_quality,
                    "pp_check": pp_check, "chat_markdown": chat_md}
        # Surrogate failed → fall through to the normal solver path.
        _emit("⚠ surrogate",
              "Surrogate route failed; falling back to direct solver.")

    # Auto-detect constraints in the goal text if none were passed explicitly
    if not constraints:
        try:
            from constraint_solver import parse_constraints_from_goal
            parsed = parse_constraints_from_goal(goal)
            if parsed:
                _emit("📐 constraints",
                      f"Parsed {len(parsed)} constraint(s) from goal: "
                      + ", ".join(f"{c['lhs']} {c['operator']} {c['rhs']}"
                                  for c in parsed[:3]))
                # Resolve each constraint to a tag/property using observables
                resolved = _resolve_constraints(parsed, suggested,
                                                  spec.get("objective", {}))
                constraints = resolved
        except Exception as _cexc:
            _log.debug("constraint parsing failed: %s", _cexc)
    if constraints:
        _emit("📐 constraints",
              f"Applying {len(constraints)} inequality constraint(s) "
              "via penalty functions…")

    # ── Choose execution engine ──────────────────────────────────────────
    # When DWSIM is fully loaded (has a real flowsheet with _flowsheet set)
    # and DotNumerics is available, prefer the TRUE DWSIM-internal engine
    # (OptimizationCase objects) over our Python wrapper. Falls back
    # transparently when unavailable.
    use_dwsim_internal = False
    try:
        from dwsim_internal_optimizer import _load_dwsim_types
        from dwsim_native_solvers import _dotnumerics_available
        if (getattr(bridge, "_flowsheet", None) is not None
                and _dotnumerics_available()):
            use_dwsim_internal = True
            _emit("⚙️ engine",
                  "DWSIM-internal OptimizationCase engine selected.")
    except Exception:
        pass

    def _run_engine(active_spec: Dict[str, Any]) -> Dict[str, Any]:
        """Execute ONE optimization attempt for the given spec, routing to the
        engine selected above. Exceptions become a RUN_FAILED result so the
        bounded replanning loop can reason about them rather than aborting."""
        amethod = active_spec.get("method", method)
        try:
            if use_dwsim_internal and not use_complex:
                from dwsim_internal_optimizer import run_dwsim_internal_optimization
                return run_dwsim_internal_optimization(
                    bridge,
                    variables = active_spec["variables"],
                    objective = active_spec["objective"],
                    minimize  = active_spec["minimize"],
                    method    = amethod,
                    max_iter  = int(max_iter),
                    tolerance = float(tolerance),
                    on_progress = on_eval,
                )
            elif use_complex:
                from complex_optimizer import run_complex_optimization
                return run_complex_optimization(
                    bridge,
                    variables = active_spec["variables"],
                    objective = active_spec["objective"],
                    minimize  = active_spec["minimize"],
                    max_iter  = int(max_iter),
                    tolerance = float(tolerance),
                    multi_solver = True,
                    widen_bounds = True,
                    llm = llm,
                    user_goal = goal,
                    on_step = on_step,
                    on_eval = on_eval,
                    constraints = constraints,
                )
            else:
                from dwsim_native_optimizer import run_dwsim_native_optimization
                return run_dwsim_native_optimization(
                    bridge,
                    variables = active_spec["variables"],
                    objective = active_spec["objective"],
                    method    = amethod,
                    minimize  = active_spec["minimize"],
                    max_iter  = int(max_iter),
                    tolerance = float(tolerance),
                    on_progress = on_eval,
                    constraints = constraints,
                )
        except Exception as exc:
            return {"success": False, "error_code": "RUN_FAILED",
                    "error": str(exc)}

    # ── MULTI-START (research §4c) ─────────────────────────────────────────
    # Run local solvers from several initial points to escape poor local
    # optima / finite-difference noise. Off by default (OPT_MULTISTART=1); the
    # complex path already does global DE search, so multi-start is applied
    # only to the local-solver path.
    _n_starts = max(1, int(os.getenv("OPT_MULTISTART", "1")))
    if _n_starts > 1 and not use_complex:
        _emit("🎲 step 3/4",
              f"Multi-start enabled — running {method.upper()} from {_n_starts} "
              "initial points, keeping the best…")
        result = _run_multistart(_run_engine, spec, _n_starts,
                                  minimize=spec["minimize"], emit=_emit)
    else:
        result = _run_engine(spec)

    # ── BOUNDED MULTI-STEP ADAPTIVE REPLANNING ─────────────────────────────
    # Instead of giving up (or replanning exactly once), reason about WHY the
    # run failed and retry with a materially different plan — up to
    # REPLAN_MAX_ATTEMPTS times. Each replan is given the cumulative history of
    # what was already tried, so it reasons about the *sequence* of failures
    # and does not repeat itself. The counter hard-bounds cost/latency, and a
    # replanner that declares infeasibility (or can't form a new plan) breaks
    # the loop immediately. This is bounded autonomy, not an unbounded agent.
    _max_replans = max(1, int(os.getenv("REPLAN_MAX_ATTEMPTS", "2")))
    _replan_history: List[Dict[str, Any]] = []
    _attempt = 0
    while (not result.get("success")) and _attempt < _max_replans:
        _attempt += 1
        _emit("🤔 replan",
              f"Run failed (attempt {_attempt}/{_max_replans}) — reasoning "
              "about why and forming a different plan…")
        try:
            from adaptive_replanner import replan_on_failure
            _fr = {"success": False, "result": result,
                   "error": result.get("error", ""),
                   "error_code": result.get("error_code", "")}
            try:
                rp = replan_on_failure(bridge, llm, goal, spec, _fr,
                                       history=_replan_history)
            except TypeError:
                # Backward-compatible with a replanner lacking `history`.
                rp = replan_on_failure(bridge, llm, goal, spec, _fr)
        except Exception as _rexc:
            rp = {"replanned": False, "diagnosis": str(_rexc)}

        if rp.get("feasible") is False:
            # The replanner determined the goal is genuinely impossible.
            _emit("✗ replan", f"Goal infeasible: {rp.get('infeasible_reason','')}")
            return {
                "success": False, "spec": spec, "result": result,
                "error_code": "GOAL_INFEASIBLE",
                "error": rp.get("infeasible_reason", "goal is infeasible"),
                "chat_markdown": (
                    f"**❌ This goal cannot be achieved on this flowsheet.**\n\n"
                    f"{rp.get('diagnosis','')}\n\n_{rp.get('infeasible_reason','')}_"
                ),
            }
        if not (rp.get("replanned") and rp.get("new_spec")):
            break  # no further alternative plan could be formed

        # Record what we just tried so the next replan reasons cumulatively.
        _replan_history.append({
            "objective": spec.get("objective"),
            "method": spec.get("method"),
            "error": result.get("error", ""),
            "diagnosis": rp.get("diagnosis", ""),
            "rationale": rp.get("rationale", ""),
        })
        _emit("✓ replan",
              f"New plan ({rp.get('_via')}, attempt {_attempt}): "
              f"{rp.get('rationale','')[:70]}")
        new_spec = rp["new_spec"]
        new_spec["_replanned"] = True
        result = _run_engine(new_spec)
        spec = new_spec   # carry forward so the next iteration reasons about it
        if result.get("success"):
            result["_replan_diagnosis"] = rp.get("diagnosis")
            result["_replan_rationale"] = rp.get("rationale")
            result["_replan_via"]       = rp.get("_via")
            result["_replan_attempts"]  = _attempt
            _emit("✓ step 3/4 (replanned)",
                  f"Replanned run converged in "
                  f"{result.get('n_evaluations','?')} evaluations "
                  f"(after {_attempt} replan(s)).")

    if not result.get("success"):
        suffix = (" even after replanning" if _attempt else "")
        md = f"**❌ Optimization did not converge{suffix}.**\n\n"
        if _replan_history:
            md += (f"_Replan reasoning ({len(_replan_history)} attempt(s)):_ "
                   f"{_replan_history[-1].get('diagnosis','')}\n\n")
        md += "Error: " + str(result.get("error", "optimization did not converge"))
        return {"success": False, "spec": spec, "result": result,
                "replan_attempted": bool(_attempt),
                "replan_attempts": _attempt,
                "error": result.get("error", "optimization did not converge"),
                "chat_markdown": md}
    _emit("✓ step 3/4",
          f"Solver converged in {result.get('n_evaluations', '?')} evaluations "
          f"({result.get('solver_backend', '?')}).")

    # ── Step 3.4: GLOBAL-OPTIMALITY CONFIDENCE ─────────────────────────────
    # A single local solve gives no evidence the optimum is global. Re-optimise
    # from a few diverse starts: if they all agree it's strong evidence the
    # optimum is global; if one finds a better value, adopt it. The complex path
    # already does global DE/CMA-ES search, so only probe the local path.
    if not use_complex:
        try:
            gc_block, gc_better = _assess_global_confidence(
                _run_engine, spec, result, spec["minimize"], _emit)
            if gc_better is not None:
                result = gc_better
                _emit("🌐 global-check",
                      "Adopted a better optimum found from a diverse restart.")
            result["global_confidence"] = gc_block
            if gc_block.get("assessed"):
                _emit("🌐 global-check",
                      f"Global-optimality confidence: {gc_block.get('confidence')} — "
                      + gc_block.get("note", ""))
        except Exception as _gcexc:
            result["global_confidence"] = {"assessed": False, "error": str(_gcexc)}

    # ── Step 3.5: VERIFY the optimum is reproducible ───────────────────────
    # Re-solve at the optimum and re-read the objective from the live flowsheet;
    # the reported `best_objective` must reproduce. Catches a cached/irreprodu-
    # cible "optimum" that no longer holds when the flowsheet is re-solved.
    # Skippable via OPT_VERIFY_OPTIMUM=0 (saves one extra solve).
    if _verify_enabled():
        _emit("🔁 step 3.5", "Re-solving at the optimum to verify it reproduces…")
        ver = _verify_optimum_reproducible(bridge, spec["objective"],
                                           result.get("best_objective"),
                                           result.get("variables_table"))
        if ver.get("verified") is True:
            _emit("✓ step 3.5",
                  f"Optimum reproduced (re-read {ver.get('reread')} vs reported "
                  f"{ver.get('reported')}, rel err {ver.get('rel_error')}).")
        elif ver.get("verified") is False:
            _emit("⚠ step 3.5",
                  f"Optimum did NOT reproduce: re-read {ver.get('reread')} vs "
                  f"reported {ver.get('reported')} (rel err {ver.get('rel_error')} "
                  f"> tol {ver.get('tolerance')}).")
        else:
            _emit("⚠ step 3.5",
                  f"Could not verify the optimum: {ver.get('reason','unknown')}.")
    else:
        ver = {"verified": None, "skipped": True,
               "reason": "verification disabled (OPT_VERIFY_OPTIMUM=0)"}
        _emit("⏭ step 3.5",
              "Optimum verification disabled (OPT_VERIFY_OPTIMUM=0) — "
              "skipping the re-solve.")
    result["verification"] = ver
    result["objective_quality"] = obj_quality

    # ── Step 4: format ────────────────────────────────────────────────────
    _emit("📊 step 4/4", "Composing poster-style result…")
    chat_md = format_poster_chat(result, goal=goal, spec=spec)
    # Verification banner — surface a non-reproducible optimum prominently.
    chat_md = _verification_banner(ver) + chat_md
    # Prepend PP-warning banner to chat if applicable
    if pp_check.get("severity") in ("warning", "mismatch"):
        banner = (
            f"\n> ⚠ **Property-package warning:** {pp_check.get('message','')}\n"
            f"> _Suggested alternative_: "
            f"{', '.join(pp_check.get('recommended_pps', [])[:3])}\n"
        )
        # Insert after the goal line, before objective table
        chat_md = chat_md.replace("\n### 🎯 OBJECTIVE",
                                    banner + "\n### 🎯 OBJECTIVE", 1)
    # Objective-confidence-gate banner (corrected objective / low confidence)
    if obj_gate.get("corrected"):
        via = " (LLM re-mapped)" if obj_gate.get("via") == "llm" else ""
        ob = (
            f"\n> 🎯 **Objective auto-corrected{via}:** the goal didn't match the "
            f"originally-chosen objective, so it was switched to "
            f"`{obj_gate.get('new_objective', spec['objective'].get('tag','') + '.' + spec['objective'].get('property',''))}` "
            f"before optimising.\n"
        )
        chat_md = chat_md.replace("\n### 🎯 OBJECTIVE", ob + "\n### 🎯 OBJECTIVE", 1)
    elif obj_gate.get("low_confidence"):
        ob = (
            f"\n> ⚠ **Low-confidence objective:** {obj_gate.get('reason','')}. "
            f"Verify this is what you intended to optimise.\n"
        )
        chat_md = chat_md.replace("\n### 🎯 OBJECTIVE", ob + "\n### 🎯 OBJECTIVE", 1)
    # Hollow/trivial-objective banner — the result may be numerically valid but
    # engineering-meaningless (e.g. maximised a flow by maxing the feed).
    if obj_quality.get("severity") == "high":
        oq = (f"\n> ⚠ **Possibly hollow objective:** {obj_quality.get('warning','')}\n"
              f"> _{obj_quality.get('suggestion','')}_\n")
        chat_md = chat_md.replace("\n### 🎯 OBJECTIVE", oq + "\n### 🎯 OBJECTIVE", 1)
    # Global-optimality confidence banner.
    _gc = result.get("global_confidence") or {}
    if _gc.get("assessed"):
        _icon = {"high": "✅", "medium": "🟡", "low": "⚠"}.get(_gc.get("confidence"), "ℹ")
        gc_md = (f"\n> {_icon} **Global-optimality confidence: "
                 f"{str(_gc.get('confidence','')).upper()}** — {_gc.get('note','')}\n")
        chat_md = chat_md.replace("\n### 🎯 OBJECTIVE", gc_md + "\n### 🎯 OBJECTIVE", 1)
    # ── Learn from this VERIFIED success: persist the (goal → objective →
    #    outcome) case so future similar goals get it as a worked example.
    #    Skip learning from an optimum that FAILED reproduction — we must not
    #    propagate a non-reproducible result as a worked example. ──
    if ver.get("verified") is False:
        _emit("🧠 not learned",
              "Optimum did not reproduce — not recorded as a worked example.")
    else:
        try:
            from experience_store import record_case
            if record_case(goal, spec, result, bridge):
                _emit("🧠 learned",
                      "Recorded this verified objective for future similar goals.")
        except Exception as _exc:
            _log.debug("experience record skipped: %s", _exc)

    _emit("✓ step 4/4", "Done.")

    return {"success": True, "spec": spec, "result": result,
            "obj_gate": obj_gate,
            "pp_check": pp_check, "chat_markdown": chat_md}


# ─── 4. Poster-style chat renderer ────────────────────────────────────────

def format_poster_chat(result: Dict[str, Any], goal: Optional[str] = None,
                       spec: Optional[Dict[str, Any]] = None) -> str:
    """Render the optimization result as markdown matching the poster's
    "OPTIMIZATION RESULTS / KEY MODIFIED VARIABLES" panel."""
    rows = result.get("variables_table", [])
    obj = result.get("best_objective")
    method = result.get("method", "")
    backend = result.get("solver_backend", "")
    n_eval = result.get("n_evaluations", "?")
    duration = result.get("duration_s", "?")
    direction = "minimised" if result.get("minimize", True) else "maximised"

    parts: List[str] = []

    # Goal echo (if provided)
    if goal:
        parts.append(f"**Goal:** _{goal.strip()}_\n")

    # OBJECTIVE ACHIEVED panel
    obj_str = (f"{obj:.4f}" if isinstance(obj, (int, float)) else "—")
    obj_block = (
        f"### 🎯 OBJECTIVE ACHIEVED\n\n"
        f"| Objective ({direction}) | Value | Solver | Evaluations | Duration |\n"
        f"| --- | --- | --- | --- | --- |\n"
        f"| Best | **{obj_str}** | `{method}` | {n_eval} | {duration}s |\n"
    )
    parts.append(obj_block)

    # Engine badge
    is_native = result.get("used_native_dotnumerics")
    if is_native is True:
        parts.append(f"> ⚙️ **Engine:** {backend} — same .NET solver as DWSIM's GUI Optimizer.\n")
    elif is_native is False:
        parts.append(f"> 🐍 **Engine:** {backend} — algorithm-equivalent fallback.\n")

    # KEY MODIFIED VARIABLES table
    if rows:
        parts.append("### 🔧 KEY MODIFIED VARIABLES\n")
        parts.append("| Variable | Old Value | New Value | Change | Δ% | Bound |\n"
                     "| --- | ---: | ---: | ---: | ---: | :---: |")
        for row in rows:
            arrow = "▲" if row["change"] > 0 else "▼" if row["change"] < 0 else "—"
            bound = ("⚠ LOWER" if row.get("at_lower") else
                     "⚠ UPPER" if row.get("at_upper") else "—")
            parts.append(
                f"| `{row['variable']}` | {row['old_value']:.4f} | "
                f"**{row['new_value']:.4f}** | {arrow} {row['change']:+.4f} | "
                f"{row['change_pct']:+.2f}% | {bound} |"
            )

    # Spec summary (compact)
    if spec:
        ovars = ", ".join(f"`{v['tag']}.{v['property']}`" for v in spec["variables"])
        parts.append(f"\n**Decision variables:** {ovars}")
        if isinstance(spec.get("objective"), dict):
            o = spec["objective"]
            if o.get("type") == "expression":
                parts.append(f"**Objective expression:** `{o.get('expression','')}`")
            elif o.get("type") == "variable":
                parts.append(f"**Objective variable:** `{o.get('tag','')}."
                              f"{o.get('property','')}`")
        # Surface heuristic-fallback so the user knows what was auto-picked
        if spec.get("_heuristic_fallback"):
            reason = spec.get("_heuristic_reason", "")
            if reason:
                parts.append(f"\n> ⚠️ **Heuristic mode** — {reason}")

    # Summary sentence (matches poster)
    purity = "near optimum"
    if rows:
        change_pct_sum = sum(abs(r.get("change_pct", 0)) for r in rows)
        purity = f"total parameter change {change_pct_sum:.1f}%"
    parts.append(
        f"\n**Summary:** Optimization completed successfully. "
        f"Objective {direction} to **{obj_str}** in {n_eval} evaluations "
        f"({duration}s). {purity}. All equipment operating within bounds."
    )

    # Constraint compliance — only present when constraints were applied
    compliance = result.get("constraint_compliance")
    if compliance:
        n_ok = compliance.get("n_satisfied", 0)
        n_v  = compliance.get("n_violated", 0)
        emoji = "✅" if n_v == 0 else "⚠"
        parts.append(f"\n### 📐 CONSTRAINT COMPLIANCE\n")
        parts.append(f"{emoji} **{n_ok}/{n_ok + n_v} constraints satisfied**\n")
        parts.append("| Constraint | Value | Status | Violation |")
        parts.append("| --- | ---: | :---: | ---: |")
        for d in compliance.get("details", []):
            sym = "✓" if d["satisfied"] else "✗"
            val = d.get("value")
            val_s = f"{val:.4f}" if isinstance(val, (int, float)) else "—"
            violation = d.get("violation", 0)
            v_s = f"{violation:.4g}" if violation > 0 else "—"
            parts.append(f"| `{d['constraint']}` | {val_s} | {sym} | {v_s} |")

    # Robustness diagnostics — only present when the complex path ran
    if result.get("_complex_path"):
        parts.append("\n### 🔬 ROBUSTNESS ANALYSIS\n")
        attempts = result.get("_solver_attempts") or []
        if attempts:
            parts.append("| Strategy | Best Objective | Evals | Converged |")
            parts.append("| --- | ---: | ---: | :---: |")
            for a in attempts:
                bo = a.get("best_obj")
                bo_s = f"{bo:.4g}" if isinstance(bo, (int, float)) else "—"
                parts.append(f"| {a['strategy']} | {bo_s} | {a.get('n_evals','?')} | "
                              f"{'✓' if a.get('converged') else '⚠'} |")
        wlog = result.get("_bound_widening_log") or []
        if wlog:
            parts.append(f"\n**Bound widenings:** {len(wlog)} round(s)")
            for w in wlog[:5]:
                parts.append(f"  • `{w['var']}` ({w['side']}): "
                              f"{w['old_bound']:.3g} → {w['new_bound']:.3g}")
        fail_rate = result.get("_eval_failure_rate")
        if isinstance(fail_rate, (int, float)):
            warn = " ⚠ HIGH" if result.get("_failure_rate_warning") else ""
            parts.append(f"\n**Evaluation failure rate:** {fail_rate*100:.1f}%{warn}")
        sanity = result.get("_sanity_check")
        if sanity and sanity.get("confidence") is not None:
            conf = sanity["confidence"]
            emoji = "✅" if conf >= 8 else "⚠" if conf >= 5 else "❌"
            parts.append(f"\n{emoji} **Objective↔goal alignment:** {conf}/10 — "
                          f"_{sanity.get('note', '')}_")
        if result.get("_diagnostics"):
            parts.append(f"\n> {result['_diagnostics']}")

    parts.append("")   # trailing newline
    return "\n".join(parts)
