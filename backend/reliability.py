"""
reliability.py  —  LLM Reliability Analyzer for DWSIM Agentic AI
─────────────────────────────────────────────────────────────────
Answers four critical research questions:

  1. Does the LLM hallucinate wrong units?
       → UnitConsistencyChecker: extract (value, unit) pairs from
         agent text, compare against actual tool-returned values.

  2. Does it choose wrong property packages?
       → PropertyPackageChecker: rules-based appropriateness check
         for the active PP vs the compounds in the flowsheet.

  3. Does it break mass balance?
       → PhysicalConsistencyChecker: mole fractions sum to 1,
         T > 0 K, P > 0, vapor fraction ∈ [0,1], molar flow > 0.
         For simple non-reactive systems: Σ(outlet) ≈ Σ(inlet).

  4. Does it hallucinate numerical values?
       → HallucinationDetector: specific numeric claims in agent
         text that don't match any tool return value.

Each failure produces a FailureCase with:
  error_type, severity, detail, evidence_snippet, session_id
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Error taxonomy
# ─────────────────────────────────────────────────────────────────────────────

class E:
    # Unit errors
    UNIT_CONFUSION      = "UNIT_CONFUSION"        # K reported as °C or vice-versa
    UNIT_SCALE_ERROR    = "UNIT_SCALE_ERROR"       # Pa/bar, kW/MW mismatch
    UNIT_HALLUCINATION  = "UNIT_HALLUCINATION"     # Numeric value not from any tool

    # Property package
    PP_WRONG_MODEL      = "PP_WRONG_MODEL"         # Clearly wrong EOS for mixture
    PP_SUBOPTIMAL       = "PP_SUBOPTIMAL"          # Technically works but not ideal
    PP_UNVERIFIED       = "PP_UNVERIFIED"          # Agent didn't check PP at all

    # Physical consistency
    MOLFRAC_SUM_ERROR   = "MOLFRAC_SUM_ERROR"      # x_i don't sum to 1
    NEGATIVE_TEMP       = "NEGATIVE_TEMP"          # T < 0 K (impossible)
    NEGATIVE_PRESSURE   = "NEGATIVE_PRESSURE"      # P ≤ 0 (impossible)
    NEGATIVE_FLOW       = "NEGATIVE_FLOW"          # Molar/mass flow < 0
    VAPOR_FRAC_INVALID  = "VAPOR_FRAC_INVALID"     # VF ∉ [0,1]
    MASS_BALANCE_VIOLATION = "MASS_BALANCE_VIOLATION"  # Σout ≠ Σin > 1%

    # Hallucination
    NO_TOOL_SIMULATION  = "NO_TOOL_SIMULATION"     # Answered sim question without tools
    HALLUCINATED_VALUE  = "HALLUCINATED_VALUE"     # Numeric claim not in tool results

    SEVERITY_HIGH   = "HIGH"
    SEVERITY_MEDIUM = "MEDIUM"
    SEVERITY_LOW    = "LOW"

    ALL_TYPES = [
        UNIT_CONFUSION, UNIT_SCALE_ERROR, UNIT_HALLUCINATION,
        PP_WRONG_MODEL, PP_SUBOPTIMAL, PP_UNVERIFIED,
        MOLFRAC_SUM_ERROR, NEGATIVE_TEMP, NEGATIVE_PRESSURE,
        NEGATIVE_FLOW, VAPOR_FRAC_INVALID, MASS_BALANCE_VIOLATION,
        NO_TOOL_SIMULATION, HALLUCINATED_VALUE,
    ]


@dataclass
class FailureCase:
    session_id:       str
    error_type:       str
    severity:         str
    detail:           str
    evidence_snippet: str   # part of agent text or tool result that shows the error
    timestamp_iso:    str   = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        return {
            "session_id":       self.session_id,
            "error_type":       self.error_type,
            "severity":         self.severity,
            "detail":           self.detail,
            "evidence_snippet": self.evidence_snippet[:300],
            "timestamp_iso":    self.timestamp_iso,
        }


@dataclass
class ReliabilityReport:
    session_id:    str
    issues:        List[FailureCase]
    clean:         bool   # True if no HIGH/MEDIUM severity issues

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "issue_count": len(self.issues),
            "clean": self.clean,
            "issues": [f.to_dict() for f in self.issues],
        }

    def summary_by_type(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for issue in self.issues:
            counts[issue.error_type] = counts.get(issue.error_type, 0) + 1
        return counts


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unit Consistency Checker
# ─────────────────────────────────────────────────────────────────────────────

# Regex: captures numeric value + common ChemE unit
_UNIT_RE = re.compile(
    r'(?<!\w)([-+]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*'
    r'(°C|°F|K\b|bar\b|Pa\b|kPa\b|MPa\b|'
    r'kg/h\b|kg/s\b|kmol/h\b|mol/s\b|kmol/s\b|'
    r'kW\b|MW\b|GJ/h\b|kJ/kg\b)',
    re.IGNORECASE,
)

# Conversion to SI base (K, Pa, kg/s, mol/s, W)
def _to_si(value: float, unit: str) -> Optional[float]:
    u = unit.lower().replace("°", "").strip()
    if u == "c":            return value + 273.15
    if u == "f":            return (value - 32) * 5/9 + 273.15
    if u == "k":            return value
    if u == "bar":          return value * 1e5
    if u == "pa":           return value
    if u == "kpa":          return value * 1e3
    if u == "mpa":          return value * 1e6
    if u == "kg/h":         return value / 3600
    if u == "kg/s":         return value
    if u == "kmol/h":       return value / 3.6
    if u == "mol/s":        return value
    if u == "kmol/s":       return value * 1e3
    if u == "kw":           return value * 1e3
    if u == "mw":           return value * 1e6
    if u == "gj/h":         return value * 1e9 / 3600
    if u == "kj/kg":        return value * 1e3
    return None


def _extract_unit_claims(text: str) -> List[Tuple[float, str]]:
    """Extract all (value, unit) pairs from agent text."""
    return [(float(m.group(1)), m.group(2)) for m in _UNIT_RE.finditer(text)]


def _extract_sim_values_from_result(name: str, result: dict) -> Dict[str, float]:
    """
    Pull known physical quantities from a tool result into a flat dict.
    Keys: 'T_K', 'P_Pa', 'mass_flow_kgs', 'molar_flow_mols', 'VF'
    """
    vals: Dict[str, float] = {}
    if not result.get("success"):
        return vals

    def _pull_stream(s: dict):
        try:
            t = s.get("temperature_C")
            if t is not None: vals["T_K"] = float(t) + 273.15
        except Exception: pass
        try:
            p = s.get("pressure_bar")
            if p is not None: vals["P_Pa"] = float(p) * 1e5
        except Exception: pass
        try:
            mf = s.get("mass_flow_kgh")
            if mf is not None: vals["mass_flow_kgs"] = float(mf) / 3600
        except Exception: pass
        try:
            mol = s.get("molar_flow_kmolh")
            if mol is not None: vals["molar_flow_mols"] = float(mol) / 3.6
        except Exception: pass
        try:
            vf = s.get("vapor_fraction")
            if vf is not None: vals["VF"] = float(vf)
        except Exception: pass

    if name == "get_stream_properties":
        _pull_stream(result)
    elif name in ("get_simulation_results", "run_simulation"):
        for s in (result.get("stream_results") or {}).values():
            _pull_stream(s)

    return vals


class UnitConsistencyChecker:
    """
    Compare (value, unit) claims in agent text against recorded tool values.
    Flags:
      - UNIT_CONFUSION: value matches SI magnitude of a different-dimension unit
        (e.g. agent says "25 K" but simulation value is 25 °C = 298 K)
      - UNIT_SCALE_ERROR: ratio between claim and actual is 1000 or 100000
        (kPa/Pa, MW/kW, °C/K additive offset instead of 0)
    """

    # Suspicious ratio thresholds (value_claim / value_si_from_tool)
    _SCALE_RATIOS = [1000, 100000, 100, 1/1000, 1/100000, 1/100]
    _ADDITIVE_OFFSET = 273.15   # K ↔ °C confusion

    def check(
        self,
        agent_text: str,
        tool_values_si: Dict[str, float],
        session_id: str,
    ) -> List[FailureCase]:
        issues: List[FailureCase] = []
        if not agent_text or not tool_values_si:
            return issues

        claims = _extract_unit_claims(agent_text)
        if not claims:
            return issues

        for val, unit in claims:
            val_si = _to_si(val, unit)
            if val_si is None:
                continue

            for key, ref_si in tool_values_si.items():
                if ref_si == 0:
                    continue
                ratio = val_si / ref_si

                # Check for scale errors (off by factor of 1000, 100000, etc.)
                for bad_ratio in self._SCALE_RATIOS:
                    if abs(ratio - bad_ratio) / bad_ratio < 0.15:
                        issues.append(FailureCase(
                            session_id       = session_id,
                            error_type       = E.UNIT_SCALE_ERROR,
                            severity         = E.SEVERITY_HIGH,
                            detail           = (
                                f"Agent claimed '{val} {unit}' (= {val_si:.3g} SI) "
                                f"but simulation {key} = {ref_si:.3g} SI "
                                f"(ratio {ratio:.2f} — likely {int(round(1/ratio))}× scale error)"
                            ),
                            evidence_snippet = _find_snippet(agent_text, str(val)),
                        ))

                # Check for K ↔ °C additive confusion
                if key == "T_K" and "k" in unit.lower():
                    diff = abs(val - (ref_si - 273.15))
                    if diff < abs(ref_si) * 0.05:
                        issues.append(FailureCase(
                            session_id       = session_id,
                            error_type       = E.UNIT_CONFUSION,
                            severity         = E.SEVERITY_HIGH,
                            detail           = (
                                f"Agent reported '{val} K' but simulation temperature is "
                                f"{ref_si - 273.15:.2f} °C ({ref_si:.2f} K). "
                                f"Looks like °C value stated as K."
                            ),
                            evidence_snippet = _find_snippet(agent_text, str(val)),
                        ))

        return _deduplicate(issues)


def _find_snippet(text: str, keyword: str, radius: int = 60) -> str:
    idx = text.find(keyword)
    if idx < 0:
        return text[:120]
    return "…" + text[max(0, idx - radius): idx + radius + len(keyword)] + "…"


def _deduplicate(issues: List[FailureCase]) -> List[FailureCase]:
    seen = set()
    out = []
    for i in issues:
        key = (i.error_type, i.detail[:80])
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. Property Package Checker
# ─────────────────────────────────────────────────────────────────────────────

# Compound → category mapping
_POLAR    = {"Water", "Methanol", "Ethanol", "1-Propanol", "2-Propanol", "Acetone",
             "Acetic Acid", "Ammonia", "Formic Acid", "Dimethyl Sulfoxide", "Glycerol",
             "Ethylene Glycol", "Diethyl Amine", "Triethyl Amine", "Diethanolamine"}
_HC       = {"Methane", "Ethane", "Propane", "n-Butane", "i-Butane", "n-Pentane",
             "i-Pentane", "n-Hexane", "n-Heptane", "n-Octane", "Benzene", "Toluene",
             "Ethylene", "Propylene", "1-Butene", "Acetylene", "Cyclohexane",
             "n-Decane", "Naphthalene", "Styrene", "Cumene"}
_GASES    = {"Hydrogen", "Nitrogen", "Oxygen", "Carbon Dioxide", "Carbon Monoxide",
             "Hydrogen Sulfide", "Sulfur Dioxide", "Argon", "Helium", "Chlorine",
             "Hydrogen Chloride", "Hydrogen Fluoride"}
_WATER    = {"Water"}

# PP rules: (pp_substring_match, conditions) → (error_type, severity, message)
# pp_match is a substring that identifies the PP (case-insensitive)
_PP_RULES: List[Dict] = [
    # Steam Tables — only for water/steam
    {
        "pp_match":    "steam tables",
        "bad_if":      lambda c: bool(c - _WATER - _GASES),  # non-water, non-gas compounds
        "error_type":  E.PP_WRONG_MODEL,
        "severity":    E.SEVERITY_HIGH,
        "message":     (
            "Steam Tables (IAPWS-IF97) is for pure water/steam systems. "
            "Flowsheet contains non-water organic compounds — use NRTL, "
            "Peng-Robinson, or SRK instead."
        ),
    },
    # Peng-Robinson / SRK — avoid for highly polar systems
    {
        "pp_match":    ("peng-robinson", "peng robinson", "pr2", "srk"),
        "bad_if":      lambda c: len(c & _POLAR) >= 2 and not (c & _HC),
        "error_type":  E.PP_SUBOPTIMAL,
        "severity":    E.SEVERITY_MEDIUM,
        "message":     (
            "Peng-Robinson / SRK assumes non-polar / slightly-polar behaviour. "
            "For mixtures with multiple polar compounds (alcohols, water, ketones) "
            "NRTL or UNIQUAC gives better VLE/LLE accuracy."
        ),
    },
    # Peng-Robinson used for water-only
    {
        "pp_match":    ("peng-robinson", "peng robinson", "pr2"),
        "bad_if":      lambda c: c == _WATER,
        "error_type":  E.PP_WRONG_MODEL,
        "severity":    E.SEVERITY_HIGH,
        "message":     (
            "Using Peng-Robinson for pure water. Steam Tables (IAPWS-IF97) "
            "is far more accurate for water/steam systems."
        ),
    },
    # Ideal Gas — only at low pressure
    {
        "pp_match":    ("ideal gas", "ideal"),
        "bad_if":      lambda c: True,   # always a note; pressure check done separately
        "error_type":  E.PP_SUBOPTIMAL,
        "severity":    E.SEVERITY_LOW,
        "message":     (
            "Ideal Gas model is only accurate at low pressure (<5 bar) and "
            "for non-condensable gas mixtures. "
            "Consider Peng-Robinson for more realistic results."
        ),
    },
    # NRTL/UNIQUAC — not for gas-phase only
    {
        "pp_match":    ("nrtl", "uniquac", "wilson"),
        "bad_if":      lambda c: bool(c & _HC) and not (c & _POLAR),
        "error_type":  E.PP_SUBOPTIMAL,
        "severity":    E.SEVERITY_LOW,
        "message":     (
            "NRTL/UNIQUAC/Wilson are activity-coefficient models designed for "
            "liquid-phase non-ideality. For pure hydrocarbon systems, "
            "Peng-Robinson or SRK is more appropriate."
        ),
    },
]


class PropertyPackageChecker:
    """
    Check if the active property package is appropriate for the compounds present.
    Also checks whether the agent verified the PP at all.
    """

    def check(
        self,
        pp_name: Optional[str],
        compounds: List[str],
        pp_tool_called: bool,
        session_id: str,
    ) -> List[FailureCase]:
        issues: List[FailureCase] = []

        if not pp_tool_called:
            issues.append(FailureCase(
                session_id       = session_id,
                error_type       = E.PP_UNVERIFIED,
                severity         = E.SEVERITY_LOW,
                detail           = (
                    "Agent did not call get_property_package during this session. "
                    "Thermodynamic model selection was not verified."
                ),
                evidence_snippet = "(no get_property_package call detected)",
            ))

        if not pp_name or not compounds:
            return issues

        pp_lower    = pp_name.lower()
        compound_set = set(compounds)

        for rule in _PP_RULES:
            match_targets = rule["pp_match"]
            if isinstance(match_targets, str):
                match_targets = (match_targets,)
            if not any(t in pp_lower for t in match_targets):
                continue
            if rule["bad_if"](compound_set):
                issues.append(FailureCase(
                    session_id       = session_id,
                    error_type       = rule["error_type"],
                    severity         = rule["severity"],
                    detail           = f"PP='{pp_name}', Compounds={sorted(compound_set)}: {rule['message']}",
                    evidence_snippet = f"Property package: {pp_name}; compounds: {', '.join(sorted(compound_set)[:5])}",
                ))

        return issues


# ─────────────────────────────────────────────────────────────────────────────
# 3. Physical Consistency Checker
# ─────────────────────────────────────────────────────────────────────────────

class PhysicalConsistencyChecker:
    """
    Validates simulation results for physical impossibilities.
    Also checks molar mass balance for simple non-reactive systems.
    """

    MOLFRAC_TOL   = 0.005    # Σx_i must be 1.0 ± this
    MASS_BAL_TOL  = 0.02     # 2 % relative tolerance on total molar flow

    def check(
        self,
        sim_results: Optional[Dict],
        session_id: str,
        stream_objects: Optional[List[Dict]] = None,   # from list_simulation_objects
    ) -> List[FailureCase]:
        issues: List[FailureCase] = []
        if not sim_results or not sim_results.get("success"):
            return issues

        streams = sim_results.get("stream_results") or {}
        if not streams:
            return issues

        for name, s in streams.items():
            prefix = f"Stream '{name}'"

            # Temperature sanity
            T_C = s.get("temperature_C")
            if T_C is not None:
                T_K = float(T_C) + 273.15
                if T_K <= 0:
                    issues.append(FailureCase(
                        session_id       = session_id,
                        error_type       = E.NEGATIVE_TEMP,
                        severity         = E.SEVERITY_HIGH,
                        detail           = f"{prefix}: T = {T_C:.2f} °C ({T_K:.2f} K) — physically impossible.",
                        evidence_snippet = f"{name}: temperature_C={T_C}",
                    ))
                elif T_K < 50:
                    issues.append(FailureCase(
                        session_id       = session_id,
                        error_type       = E.NEGATIVE_TEMP,
                        severity         = E.SEVERITY_MEDIUM,
                        detail           = f"{prefix}: T = {T_C:.2f} °C ({T_K:.2f} K) — extremely cold, possible unit error.",
                        evidence_snippet = f"{name}: temperature_C={T_C}",
                    ))

            # Pressure sanity
            P_bar = s.get("pressure_bar")
            if P_bar is not None:
                if float(P_bar) <= 0:
                    issues.append(FailureCase(
                        session_id       = session_id,
                        error_type       = E.NEGATIVE_PRESSURE,
                        severity         = E.SEVERITY_HIGH,
                        detail           = f"{prefix}: P = {P_bar} bar — physically impossible (P ≤ 0).",
                        evidence_snippet = f"{name}: pressure_bar={P_bar}",
                    ))

            # Molar flow sanity
            mol = s.get("molar_flow_kmolh")
            if mol is not None and float(mol) < 0:
                issues.append(FailureCase(
                    session_id       = session_id,
                    error_type       = E.NEGATIVE_FLOW,
                    severity         = E.SEVERITY_MEDIUM,
                    detail           = f"{prefix}: molar_flow = {mol} kmol/h — negative flow detected.",
                    evidence_snippet = f"{name}: molar_flow_kmolh={mol}",
                ))

            # Vapor fraction range
            vf = s.get("vapor_fraction")
            if vf is not None:
                vf_f = float(vf)
                if vf_f < -0.01 or vf_f > 1.01:
                    issues.append(FailureCase(
                        session_id       = session_id,
                        error_type       = E.VAPOR_FRAC_INVALID,
                        severity         = E.SEVERITY_HIGH,
                        detail           = f"{prefix}: vapor_fraction = {vf_f:.4f} — must be in [0, 1].",
                        evidence_snippet = f"{name}: vapor_fraction={vf}",
                    ))

            # Mole fraction sum
            mf = s.get("mole_fractions") or {}
            if mf:
                total = sum(float(v) for v in mf.values())
                if abs(total - 1.0) > self.MOLFRAC_TOL:
                    issues.append(FailureCase(
                        session_id       = session_id,
                        error_type       = E.MOLFRAC_SUM_ERROR,
                        severity         = E.SEVERITY_HIGH,
                        detail           = (
                            f"{prefix}: mole fractions sum to {total:.6f} "
                            f"(expected 1.000 ± {self.MOLFRAC_TOL}) — "
                            f"{'LLM likely set wrong values' if abs(total-1.0) > 0.05 else 'small numerical drift'}."
                        ),
                        evidence_snippet = f"{name} mole_fractions: {dict(list(mf.items())[:4])}",
                    ))

        # Molar mass balance — requires knowing feed vs product streams
        if stream_objects:
            self._check_mass_balance(streams, stream_objects, session_id, issues)

        return issues

    def _check_mass_balance(
        self,
        streams:       Dict,
        stream_objects: List[Dict],
        session_id:    str,
        issues:        List[FailureCase],
    ) -> None:
        """
        For non-reactive systems: Σ(feed molar flows) ≈ Σ(product molar flows).
        stream_objects entries have 'tag' and 'category'='stream'.
        We classify by naming heuristics: feed/inlet/in → feed; product/outlet/out → product.
        """
        feed_tags    = set()
        product_tags = set()

        FEED_HINTS    = ("feed", "inlet", "in", "f-", "fi", "raw")
        PRODUCT_HINTS = ("product", "outlet", "out", "p-", "pro", "result", "effluent")

        for obj in (stream_objects or []):
            if obj.get("category") != "stream":
                continue
            tag_l = obj.get("tag", "").lower()
            if any(h in tag_l for h in FEED_HINTS):
                feed_tags.add(obj["tag"])
            elif any(h in tag_l for h in PRODUCT_HINTS):
                product_tags.add(obj["tag"])

        if not feed_tags or not product_tags:
            return   # can't classify — skip

        feed_flow    = sum(float(streams[t]["molar_flow_kmolh"])
                          for t in feed_tags if t in streams and streams[t].get("molar_flow_kmolh") is not None)
        product_flow = sum(float(streams[t]["molar_flow_kmolh"])
                          for t in product_tags if t in streams and streams[t].get("molar_flow_kmolh") is not None)

        if feed_flow <= 0 or product_flow <= 0:
            return

        rel_err = abs(product_flow - feed_flow) / feed_flow
        if rel_err > self.MASS_BAL_TOL:
            issues.append(FailureCase(
                session_id       = session_id,
                error_type       = E.MASS_BALANCE_VIOLATION,
                severity         = E.SEVERITY_HIGH if rel_err > 0.05 else E.SEVERITY_MEDIUM,
                detail           = (
                    f"Molar mass balance error: feed = {feed_flow:.4f} kmol/h, "
                    f"products = {product_flow:.4f} kmol/h, "
                    f"relative error = {rel_err*100:.2f}% "
                    f"(feeds: {sorted(feed_tags)}, products: {sorted(product_tags)})."
                ),
                evidence_snippet = f"feed_flow={feed_flow:.3f}, product_flow={product_flow:.3f}",
            ))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Hallucination Detector
# ─────────────────────────────────────────────────────────────────────────────

# Keywords that indicate the user was asking about simulation results
_SIM_QUESTION_RE = re.compile(
    r"\b(temperature|pressure|flow|molar flow|mass flow|vapor fraction|"
    r"composition|converge|stream|outlet|inlet|duty|reflux|conversion)\b",
    re.IGNORECASE,
)

# Regex for standalone numbers with ≥ 1 decimal place (specific claims)
_SPECIFIC_NUM_RE = re.compile(r'\b(\d{1,8}\.\d{1,6})\b')


class HallucinationDetector:
    """
    Detects two hallucination patterns:
      1. NO_TOOL_SIMULATION: answered a simulation question without calling tools
      2. HALLUCINATED_VALUE: stated a specific float not returned by any tool
    """

    _ROUND_NUMBERS = {0, 1, 2, 5, 10, 20, 25, 50, 100, 200, 273.15, 298.15, 373.15}

    def check(
        self,
        user_message:   str,
        agent_text:     str,
        tools_called:   List[str],
        tool_values_si: Dict[str, float],
        session_id:     str,
    ) -> List[FailureCase]:
        issues: List[FailureCase] = []

        # --- Pattern 1: Answered sim question without tools ---
        user_asks_sim   = bool(_SIM_QUESTION_RE.search(user_message))
        no_tools_called = len(tools_called) == 0
        if user_asks_sim and no_tools_called:
            # Check if agent gives specific numbers (not just "I don't know")
            nums_in_answer = _SPECIFIC_NUM_RE.findall(agent_text)
            if nums_in_answer:
                issues.append(FailureCase(
                    session_id       = session_id,
                    error_type       = E.NO_TOOL_SIMULATION,
                    severity         = E.SEVERITY_HIGH,
                    detail           = (
                        f"Agent answered a simulation question ('{user_message[:80]}…') "
                        f"with specific numerical values ({nums_in_answer[:4]}) "
                        f"without calling any simulation tools — potential hallucination."
                    ),
                    evidence_snippet = agent_text[:200],
                ))

        # --- Pattern 2: Specific float in answer not backed by any tool ---
        if tool_values_si:
            known_si = set(round(v, 2) for v in tool_values_si.values() if math.isfinite(v))
            agent_nums = _SPECIFIC_NUM_RE.findall(agent_text)
            suspicious = []

            for num_str in agent_nums:
                try:
                    num = float(num_str)
                except ValueError:
                    continue
                if num in self._ROUND_NUMBERS:
                    continue
                if len(num_str.replace('.', '')) < 3:
                    continue   # very short — likely not a simulation value

                # Check if this value (or a simple unit conversion of it) matches any tool value
                matched = False
                for ref_si in tool_values_si.values():
                    if not math.isfinite(ref_si) or ref_si == 0:
                        continue
                    # Direct match, or common unit conversion factors
                    for factor in (1, 3600, 1/3600, 1e5, 1e-5, 1e3, 1e-3, 273.15):
                        candidate = num * factor if factor != 273.15 else num + factor
                        if abs(candidate - ref_si) / max(abs(ref_si), 1e-9) < 0.02:
                            matched = True
                            break
                        # Additive offset (C → K)
                        candidate2 = num + 273.15
                        if abs(candidate2 - ref_si) / max(abs(ref_si), 1e-9) < 0.02:
                            matched = True
                            break
                    if matched:
                        break

                if not matched:
                    suspicious.append(num_str)

            if len(suspicious) >= 3:   # only flag if multiple unmatched values
                issues.append(FailureCase(
                    session_id       = session_id,
                    error_type       = E.HALLUCINATED_VALUE,
                    severity         = E.SEVERITY_MEDIUM,
                    detail           = (
                        f"Agent stated {len(suspicious)} specific numeric values "
                        f"({suspicious[:5]}) not traceable to any tool return value."
                    ),
                    evidence_snippet = agent_text[:250],
                ))

        return issues


# ─────────────────────────────────────────────────────────────────────────────
# Master Reliability Analyzer
# ─────────────────────────────────────────────────────────────────────────────

class ReliabilityAnalyzer:
    """
    Orchestrates all checkers for one completed session.
    Call analyze() after SessionTracker.finish().
    """

    def __init__(self) -> None:
        self._unit_checker  = UnitConsistencyChecker()
        self._pp_checker    = PropertyPackageChecker()
        self._phys_checker  = PhysicalConsistencyChecker()
        self._halluc_detect = HallucinationDetector()

    def analyze(
        self,
        session_id:     str,
        user_message:   str,
        agent_text:     str,
        tool_records:   List[Dict],     # [{name, args, result}]
        sim_results:    Optional[Dict] = None,
        stream_objects: Optional[List] = None,
    ) -> ReliabilityReport:
        """
        Run all four checkers. Returns a ReliabilityReport with all issues found.

        tool_records: full list of {name, args, result} dicts — captured by tracker
        sim_results:  last get_simulation_results call result (if any)
        stream_objects: from list_simulation_objects (for mass balance)
        """
        issues: List[FailureCase] = []

        # Build tool_values_si from all tool records
        tool_values_si: Dict[str, float] = {}
        for rec in tool_records:
            vals = _extract_sim_values_from_result(rec["name"], rec["result"])
            tool_values_si.update(vals)

        tools_called = [r["name"] for r in tool_records]

        # 1. Unit consistency
        issues += self._unit_checker.check(agent_text, tool_values_si, session_id)

        # 2. Property package
        pp_name     = None
        compounds   = []
        pp_called   = "get_property_package" in tools_called
        for rec in tool_records:
            if rec["name"] == "get_property_package" and rec["result"].get("success"):
                pp_name   = rec["result"].get("property_package") or rec["result"].get("name")
                compounds = rec["result"].get("compounds", [])
                break
            # Also extract from load_flowsheet result
            if rec["name"] == "load_flowsheet" and rec["result"].get("success"):
                if not pp_name:
                    pp_name   = rec["result"].get("property_package")
                    compounds = rec["result"].get("compounds", compounds)

        issues += self._pp_checker.check(pp_name, compounds, pp_called, session_id)

        # 3. Physical consistency
        if sim_results:
            issues += self._phys_checker.check(sim_results, session_id, stream_objects)

        # 4. Hallucination
        issues += self._halluc_detect.check(
            user_message, agent_text, tools_called, tool_values_si, session_id
        )

        clean = not any(i.severity in (E.SEVERITY_HIGH, E.SEVERITY_MEDIUM) for i in issues)
        return ReliabilityReport(session_id=session_id, issues=issues, clean=clean)


# ─────────────────────────────────────────────────────────────────────────────
# Failure Case Log (persistent JSON)
# ─────────────────────────────────────────────────────────────────────────────

_FAILURE_LOG_FILE = os.path.join(os.path.dirname(__file__), "failure_cases.json")


class FailureCaseLog:
    MAX_CASES = 1000

    def __init__(self, path: str = _FAILURE_LOG_FILE) -> None:
        self.path    = path
        self._cases: List[Dict] = []
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    self._cases = json.load(f)
            except Exception:
                self._cases = []

    def _save(self) -> None:
        # Atomic write: write to .tmp, then rename. Prevents corruption on crash.
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._cases[-self.MAX_CASES:], f, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def add(self, report: ReliabilityReport) -> None:
        for issue in report.issues:
            self._cases.append(issue.to_dict())
        self._save()

    def clear(self) -> None:
        self._cases = []
        self._save()

    def get_all(self) -> List[Dict]:
        return self._cases[-self.MAX_CASES:]

    def get_summary(self) -> Dict:
        """Aggregate counts by error_type and severity."""
        n = len(self._cases)
        if n == 0:
            return {
                "total_issues": 0,
                "by_type":      {},
                "by_severity":  {},
                "recent":       [],
                "hallucination_rate": None,
                "unit_error_rate":    None,
                "pp_error_rate":      None,
                "physics_error_rate": None,
            }

        by_type: Dict[str, int] = {}
        by_sev:  Dict[str, int] = {}
        for c in self._cases:
            t = c.get("error_type", "UNKNOWN")
            s = c.get("severity",   "LOW")
            by_type[t] = by_type.get(t, 0) + 1
            by_sev[s]  = by_sev.get(s, 0) + 1

        # Category roll-ups
        def _cnt(*types): return sum(by_type.get(t, 0) for t in types)

        return {
            "total_issues":       n,
            "by_type":            by_type,
            "by_severity":        by_sev,
            "recent":             self._cases[-30:][::-1],
            "hallucination_rate": _cnt(E.NO_TOOL_SIMULATION, E.HALLUCINATED_VALUE),
            "unit_error_rate":    _cnt(E.UNIT_CONFUSION, E.UNIT_SCALE_ERROR, E.UNIT_HALLUCINATION),
            "pp_error_rate":      _cnt(E.PP_WRONG_MODEL, E.PP_SUBOPTIMAL),
            "physics_error_rate": _cnt(E.MOLFRAC_SUM_ERROR, E.NEGATIVE_TEMP,
                                       E.NEGATIVE_PRESSURE, E.NEGATIVE_FLOW,
                                       E.VAPOR_FRAC_INVALID, E.MASS_BALANCE_VIOLATION),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────────────────────────────────────

_analyzer     = ReliabilityAnalyzer()
_failure_log  = FailureCaseLog()


def get_analyzer()    -> ReliabilityAnalyzer: return _analyzer
def get_failure_log() -> FailureCaseLog:      return _failure_log
