"""
accuracy.py  —  Accuracy Comparison for DWSIM Agentic AI
─────────────────────────────────────────────────────────
Answers the core research validation question:

  "Does your system produce the same numbers as manual DWSIM?"

Three-way comparison table (publishable for paper):

  ┌──────────────────────┬───────────┬──────────────┬──────────────────┐
  │ Method               │ T (°C)    │ P (bar)      │ Vapor Frac       │
  ├──────────────────────┼───────────┼──────────────┼──────────────────┤
  │ Manual DWSIM (ref)   │ 78.8      │ 1.013        │ 0.000            │
  │ Direct DWSIM API     │ 78.75     │ 1.013        │ 0.000            │
  │ AI Agent Response    │ 78.7      │ 1.013        │ 0.000            │
  ├──────────────────────┼───────────┼──────────────┼──────────────────┤
  │ Error vs Manual      │ 0.06%     │ 0.00%        │ 0.00%            │
  │ (Direct DWSIM API)   │           │              │                  │
  │ Error vs Manual      │ 0.13%     │ 0.00%        │ 0.00%            │
  │ (AI Agent Response)  │           │              │                  │
  └──────────────────────┴───────────┴──────────────┴──────────────────┘

  Direct DWSIM ≈ Manual → proves the bridge is physically correct
  AI Agent ≈ Direct DWSIM → proves the LLM reports faithfully, not hallucinating

Usage:
  from accuracy import get_accuracy_store, AccuracyComparer
"""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Property metadata — display labels, units, keys
# ─────────────────────────────────────────────────────────────────────────────

# Internal key (matches sim_results dict keys) → display info
PROPERTIES = {
    "temperature_C":     {"label": "Temperature",    "unit": "°C",     "sigfigs": 3},
    "pressure_bar":      {"label": "Pressure",       "unit": "bar",     "sigfigs": 4},
    "vapor_fraction":    {"label": "Vapor Fraction", "unit": "",        "sigfigs": 4},
    "mass_flow_kgh":     {"label": "Mass Flow",      "unit": "kg/h",    "sigfigs": 4},
    "molar_flow_kmolh":  {"label": "Molar Flow",     "unit": "kmol/h",  "sigfigs": 4},
}

PROP_LABELS = {k: f'{v["label"]} ({v["unit"]})' if v["unit"] else v["label"]
               for k, v in PROPERTIES.items()}

# ─────────────────────────────────────────────────────────────────────────────
# Agent text parser — extracts numerical property claims
# ─────────────────────────────────────────────────────────────────────────────

# Pattern: "temperature ... 78.7 °C" or "78.7°C" or "T = 78.7 C"
# All numeric patterns allow optional whitespace between value and unit
# so both "78.7°C" and "78.7 °C" are matched.
_TEMP_RE = re.compile(
    r'(?:temperature|outlet\s+temp(?:erature)?|inlet\s+temp(?:erature)?'
    r'|temp\.?|T\s*[=:]\s*)'
    r'\D{0,30}?'
    r'([-+]?\d+\.?\d*)\s*°?\s*(°C|°F|degC|degF|C\b|K\b)',
    re.IGNORECASE,
)
# Pattern: "pressure ... 1.013 bar" or "P=1.013bar"
_PRES_RE = re.compile(
    r'(?:pressure|press\.?|P\s*[=:]\s*)'
    r'\D{0,30}?'
    r'([-+]?\d+\.?\d*)\s*(bar|Pa|kPa|MPa|atm|psi)',
    re.IGNORECASE,
)
# Vapor fraction — also matches "VF: 0.0" or "vapour fraction = 0.0"
_VF_RE = re.compile(
    r'(?:vapor(?:isation)?\s+fraction|vapour\s+fraction|VF\s*[=:]\s*|vapori[sz]ed)'
    r'[\s:=]*'
    r'([-+]?\d+\.?\d*)',
    re.IGNORECASE,
)
# Mass flow — handles "mass flow rate: 1000 kg/h" and "massflow=1000kg/h"
_MF_RE = re.compile(
    r'(?:mass\s+flow(?:\s+rate)?|massflow)'
    r'\D{0,20}?'
    r'([-+]?\d+\.?\d*)\s*(kg/h|kg/s|t/h)',
    re.IGNORECASE,
)
# Molar flow
_MOL_RE = re.compile(
    r'(?:molar\s+flow(?:\s+rate)?|mole\s+flow|kmol/h|mol\s+flow)'
    r'\D{0,20}?'
    r'([-+]?\d+\.?\d*)\s*(kmol/h|mol/s|kmol/s)',
    re.IGNORECASE,
)


def parse_agent_values_tagged(
    text: str, known_tags: List[str]
) -> Dict[Tuple[str, str], float]:
    """Tag-scoped variant: for each known tag, parse property values in a
    local window after the tag is mentioned. Returns {(tag, prop): value}.

    Windowing: from the end of the tag match to either 300 chars later or
    the next tag mention, whichever is sooner. This keeps values from one
    stream section from leaking into another.
    """
    result: Dict[Tuple[str, str], float] = {}
    if not text or not known_tags:
        return result
    # Longest-first so "Heptane Out" wins over "Heptane".
    tags_sorted = sorted(set(t for t in known_tags if t), key=len, reverse=True)
    tag_pats = [(t, re.compile(r'\b' + re.escape(t) + r'\b', re.IGNORECASE))
                for t in tags_sorted]

    # Collect every (tag, start, end) match, then drop matches whose span is
    # contained in a longer tag's match (so "Heptane Out" suppresses the
    # substring match for "Heptane" at the same position).
    raw: List[Tuple[str, int, int]] = []
    for tag, pat in tag_pats:
        for m in pat.finditer(text):
            raw.append((tag, m.start(), m.end()))
    spans = sorted(raw, key=lambda x: (x[1], -(x[2] - x[1])))
    kept: List[Tuple[str, int, int]] = []
    for tag, s, e in spans:
        if any(s >= ks and e <= ke and (ke - ks) > (e - s)
               for _, ks, ke in kept):
            continue
        kept.append((tag, s, e))

    # Window each kept match until the next kept match (any tag) or +300 chars.
    bounds = sorted(kept, key=lambda x: x[1])
    for i, (tag, _s, end) in enumerate(bounds):
        next_pos = bounds[i + 1][1] if i + 1 < len(bounds) else len(text)
        window = text[end:min(end + 300, next_pos)]
        for prop, val in parse_agent_values(window).items():
            result.setdefault((tag, prop), val)
    return result


def parse_agent_values(text: str) -> Dict[str, float]:
    """
    Extract numerical property claims from agent natural-language response.
    Returns {property_key: value_in_internal_unit}.

    Internal units match DWSIM bridge output:
      temperature_C   → °C
      pressure_bar    → bar
      vapor_fraction  → dimensionless [0,1]
      mass_flow_kgh   → kg/h
      molar_flow_kmolh → kmol/h
    """
    result: Dict[str, float] = {}

    # Temperature — convert to °C
    for m in _TEMP_RE.finditer(text):
        try:
            val, unit = float(m.group(1)), m.group(2).strip()
            if unit.upper() == "K":
                val = val - 273.15
            elif "F" in unit.upper():
                val = (val - 32) * 5/9
            result.setdefault("temperature_C", val)
        except Exception:
            pass

    # Pressure — convert to bar
    for m in _PRES_RE.finditer(text):
        try:
            val, unit = float(m.group(1)), m.group(2).strip().lower()
            if unit == "pa":   val = val / 1e5
            elif unit == "kpa": val = val / 100
            elif unit == "mpa": val = val * 10
            result.setdefault("pressure_bar", val)
        except Exception:
            pass

    # Vapor fraction — unitless
    for m in _VF_RE.finditer(text):
        try:
            val = float(m.group(1))
            if 0 <= val <= 1:
                result.setdefault("vapor_fraction", val)
        except Exception:
            pass

    # Mass flow — convert to kg/h
    for m in _MF_RE.finditer(text):
        try:
            val, unit = float(m.group(1)), m.group(2).strip().lower()
            if unit == "kg/s":
                val = val * 3600
            result.setdefault("mass_flow_kgh", val)
        except Exception:
            pass

    # Molar flow — convert to kmol/h
    for m in _MOL_RE.finditer(text):
        try:
            val, unit = float(m.group(1)), m.group(2).strip().lower()
            if unit == "mol/s":
                val = val * 3.6
            result.setdefault("molar_flow_kmolh", val)
        except Exception:
            pass

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReferenceEntry:
    """One (stream, property, value) from a manual DWSIM run."""
    stream_tag:    str
    property_key:  str     # e.g. "temperature_C"
    manual_value:  float
    note:          str = ""

    def display_label(self) -> str:
        return PROP_LABELS.get(self.property_key, self.property_key)

    def unit(self) -> str:
        return PROPERTIES.get(self.property_key, {}).get("unit", "")


@dataclass
class ReferenceSet:
    """
    A named collection of manual reference values for one scenario.
    Created by the user from their manual DWSIM run.
    """
    ref_id:       str
    name:         str          # e.g. "Water Heater – Design Case"
    flowsheet:    str = ""     # flowsheet file name (optional)
    created_at:   str = field(default_factory=lambda: datetime.now().isoformat())
    entries:      List[ReferenceEntry] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "ref_id":     self.ref_id,
            "name":       self.name,
            "flowsheet":  self.flowsheet,
            "created_at": self.created_at,
            "entries":    [
                {"stream_tag":   e.stream_tag,
                 "property_key": e.property_key,
                 "manual_value": e.manual_value,
                 "note":         e.note}
                for e in self.entries
            ],
        }

    @staticmethod
    def from_dict(d: Dict) -> "ReferenceSet":
        rs = ReferenceSet(
            ref_id    = d["ref_id"],
            name      = d["name"],
            flowsheet = d.get("flowsheet", ""),
            created_at= d.get("created_at", ""),
        )
        for e in d.get("entries", []):
            rs.entries.append(ReferenceEntry(
                stream_tag   = e["stream_tag"],
                property_key = e["property_key"],
                manual_value = float(e["manual_value"]),
                note         = e.get("note", ""),
            ))
        return rs


@dataclass
class ComparisonRow:
    """One row in the accuracy comparison table."""
    stream_tag:    str
    property_key:  str
    property_label: str
    unit:          str

    manual_value:         Optional[float]   # from user reference
    direct_dwsim_value:   Optional[float]   # from bridge get_simulation_results()
    agent_stated_value:   Optional[float]   # parsed from agent text

    error_direct_pct:     Optional[float]   # |direct - manual| / |manual| × 100
    error_agent_pct:      Optional[float]   # |agent  - manual| / |manual| × 100
    error_agent_vs_direct_pct: Optional[float]  # |agent - direct| / |direct| × 100

    def to_dict(self) -> Dict:
        def _fmt(v): return round(v, 6) if v is not None else None
        return {
            "stream_tag":             self.stream_tag,
            "property_key":           self.property_key,
            "property_label":         self.property_label,
            "unit":                   self.unit,
            "manual_value":           _fmt(self.manual_value),
            "direct_dwsim_value":     _fmt(self.direct_dwsim_value),
            "agent_stated_value":     _fmt(self.agent_stated_value),
            "error_direct_pct":       _fmt(self.error_direct_pct),
            "error_agent_pct":        _fmt(self.error_agent_pct),
            "error_agent_vs_direct_pct": _fmt(self.error_agent_vs_direct_pct),
        }


@dataclass
class ComparisonResult:
    """Full comparison for one reference set against one simulation run."""
    result_id:     str
    ref_id:        str
    ref_name:      str
    session_id:    str
    timestamp:     str
    flowsheet:     str
    rows:          List[ComparisonRow]
    overall_accuracy_pct:   Optional[float]  # 100 - mean(|errors|)
    agent_fidelity_pct:     Optional[float]  # 100 - mean(agent_vs_direct)

    def to_dict(self) -> Dict:
        return {
            "result_id":            self.result_id,
            "ref_id":               self.ref_id,
            "ref_name":             self.ref_name,
            "session_id":           self.session_id,
            "timestamp":            self.timestamp,
            "flowsheet":            self.flowsheet,
            "rows":                 [r.to_dict() for r in self.rows],
            "overall_accuracy_pct": self.overall_accuracy_pct,
            "agent_fidelity_pct":   self.agent_fidelity_pct,
        }

    def as_markdown_table(self) -> str:
        """Generate the publishable markdown table for the paper."""
        if not self.rows:
            return "(no data)"

        # Header
        col_headers = [r.property_label for r in self.rows]
        header = "| Method | " + " | ".join(col_headers) + " |"
        sep    = "|--------|" + "|".join(["-" * max(len(h), 8) for h in col_headers]) + "|"

        def _fv(v, sigfigs=4):
            if v is None: return "N/A"
            if sigfigs == 3: return f"{v:.3f}"
            return f"{v:.4f}"

        def _fe(v):
            if v is None: return "N/A"
            return f"{v:.2f}%"

        rows_md = []
        # Manual DWSIM row
        vals = [_fv(r.manual_value) for r in self.rows]
        rows_md.append("| Manual DWSIM | " + " | ".join(vals) + " |")
        # Direct DWSIM API row
        vals = [_fv(r.direct_dwsim_value) for r in self.rows]
        rows_md.append("| Direct DWSIM API | " + " | ".join(vals) + " |")
        # AI Agent row
        vals = [(_fv(r.agent_stated_value) if r.agent_stated_value is not None else "—") for r in self.rows]
        rows_md.append("| AI Agent Response | " + " | ".join(vals) + " |")
        # Error rows
        sep2 = "|" + "|".join(["—" * max(len(h), 8) for h in ["Method"] + col_headers]) + "|"
        errs_d = [_fe(r.error_direct_pct) for r in self.rows]
        rows_md.append("| Error (Direct vs Manual) | " + " | ".join(errs_d) + " |")
        errs_a = [_fe(r.error_agent_pct) for r in self.rows]
        rows_md.append("| Error (AI vs Manual) | " + " | ".join(errs_a) + " |")

        return "\n".join([header, sep] + rows_md)


# ─────────────────────────────────────────────────────────────────────────────
# AccuracyComparer
# ─────────────────────────────────────────────────────────────────────────────

class AccuracyComparer:
    """
    Given a ReferenceSet + current simulation results + agent text,
    produces a ComparisonResult.
    """

    def compare(
        self,
        ref:            ReferenceSet,
        sim_results:    Dict,           # from get_simulation_results()
        agent_text:     str = "",
        session_id:     str = "",
        flowsheet:      str = "",
    ) -> ComparisonResult:

        stream_results = sim_results.get("stream_results") or {}

        # Parse agent-stated values — tag-scoped first, then a global fallback
        # so single-stream answers still land on the right row.
        agent_vals: Dict[str, float] = {}
        agent_vals_tagged: Dict[Tuple[str, str], float] = {}
        if agent_text:
            agent_vals = parse_agent_values(agent_text)
            known_tags = [e.stream_tag for e in ref.entries]
            agent_vals_tagged = parse_agent_values_tagged(agent_text, known_tags)

        rows: List[ComparisonRow] = []

        for entry in ref.entries:
            tag  = entry.stream_tag
            prop = entry.property_key
            meta = PROPERTIES.get(prop, {"label": prop, "unit": ""})

            manual_val = entry.manual_value
            dwsim_val  = None
            agent_val  = None

            # Direct DWSIM value — from stream results
            stream_data = stream_results.get(tag) or {}
            if prop in stream_data and stream_data[prop] is not None:
                try:
                    dwsim_val = float(stream_data[prop])
                except Exception:
                    pass

            # If not found by exact tag, try case-insensitive match
            if dwsim_val is None:
                for st, sd in stream_results.items():
                    if st.lower() == tag.lower() and prop in sd and sd[prop] is not None:
                        try:
                            dwsim_val = float(sd[prop])
                        except Exception:
                            pass
                        break

            # Agent stated value — prefer tag-scoped match, else global.
            agent_val = agent_vals_tagged.get((tag, prop))
            if agent_val is None:
                # Case-insensitive tag fallback before giving up on scoping.
                for (t2, p2), v2 in agent_vals_tagged.items():
                    if p2 == prop and t2.lower() == tag.lower():
                        agent_val = v2
                        break
            if agent_val is None:
                agent_val = agent_vals.get(prop)

            # Compute errors
            err_direct = _pct_error(dwsim_val, manual_val)
            err_agent  = _pct_error(agent_val,  manual_val)
            err_a_vs_d = _pct_error(agent_val,  dwsim_val)

            rows.append(ComparisonRow(
                stream_tag             = tag,
                property_key           = prop,
                property_label         = PROP_LABELS.get(prop, prop),
                unit                   = meta.get("unit", ""),
                manual_value           = manual_val,
                direct_dwsim_value     = dwsim_val,
                agent_stated_value     = agent_val,
                error_direct_pct       = err_direct,
                error_agent_pct        = err_agent,
                error_agent_vs_direct_pct = err_a_vs_d,
            ))

        # Overall accuracy — mean of |direct errors|
        direct_errors  = [r.error_direct_pct for r in rows if r.error_direct_pct is not None]
        agent_vs_direct = [r.error_agent_vs_direct_pct for r in rows
                           if r.error_agent_vs_direct_pct is not None]

        overall_acc   = round(100 - _mean(direct_errors), 2)  if direct_errors  else None
        agent_fidelity = round(100 - _mean(agent_vs_direct), 2) if agent_vs_direct else None

        return ComparisonResult(
            result_id            = str(uuid.uuid4())[:8],
            ref_id               = ref.ref_id,
            ref_name             = ref.name,
            session_id           = session_id,
            timestamp            = datetime.now().isoformat(),
            flowsheet            = flowsheet,
            rows                 = rows,
            overall_accuracy_pct = overall_acc,
            agent_fidelity_pct   = agent_fidelity,
        )


def _pct_error(value: Optional[float], reference: Optional[float]) -> Optional[float]:
    """Return percentage error |value - reference| / |reference| * 100.

    BUG-15 fix: when reference == 0 and value != 0, returning None was silently
    hiding a real error. Now returns 100.0 (100% relative error) so the comparison
    row is still surfaced to the user with a clear error signal.
    """
    if value is None or reference is None:
        return None
    if reference == 0:
        return 0.0 if abs(value) < 1e-9 else 100.0  # 100% error when ref=0, value≠0
    return round(abs(value - reference) / abs(reference) * 100, 4)


def _mean(lst: List[float]) -> float:
    return sum(lst) / len(lst) if lst else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# AccuracyStore — persistent JSON
# ─────────────────────────────────────────────────────────────────────────────

_STORE_FILE = os.path.join(os.path.dirname(__file__), "accuracy_store.json")


class AccuracyStore:
    """
    Persists:
      reference_sets  — user-defined manual reference values
      comparisons     — history of comparison runs
    """

    MAX_COMPARISONS = 200

    def __init__(self, path: str = _STORE_FILE) -> None:
        self.path         = path
        self._refs:   List[Dict] = []
        self._comps:  List[Dict] = []
        self._load()

    def _load(self) -> None:
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._refs  = data.get("reference_sets", [])
            self._comps = data.get("comparisons", [])
        except Exception:
            pass

    def _save(self) -> None:
        # Atomic write: write to .tmp, then rename. Prevents corruption on crash.
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {"reference_sets": self._refs,
                     "comparisons":    self._comps[-self.MAX_COMPARISONS:]},
                    f, indent=2,
                )
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ── Reference sets ────────────────────────────────────────────────────────

    def add_reference_set(self, rs: ReferenceSet) -> None:
        self._refs.append(rs.to_dict())
        self._save()

    def list_reference_sets(self) -> List[Dict]:
        return list(self._refs)

    def get_reference_set(self, ref_id: str) -> Optional[ReferenceSet]:
        for d in self._refs:
            if d["ref_id"] == ref_id:
                return ReferenceSet.from_dict(d)
        return None

    def delete_reference_set(self, ref_id: str) -> bool:
        before = len(self._refs)
        self._refs = [d for d in self._refs if d["ref_id"] != ref_id]
        if len(self._refs) < before:
            self._save()
            return True
        return False

    def update_reference_set(self, rs: ReferenceSet) -> None:
        self._refs = [d for d in self._refs if d["ref_id"] != rs.ref_id]
        self._refs.append(rs.to_dict())
        self._save()

    # ── Comparisons ───────────────────────────────────────────────────────────

    def add_comparison(self, result: ComparisonResult) -> None:
        self._comps.append(result.to_dict())
        self._save()

    def list_comparisons(self, ref_id: Optional[str] = None) -> List[Dict]:
        comps = self._comps
        if ref_id:
            comps = [c for c in comps if c["ref_id"] == ref_id]
        return comps[-50:][::-1]   # newest first

    def get_comparison(self, result_id: str) -> Optional[Dict]:
        for c in self._comps:
            if c["result_id"] == result_id:
                return c
        return None

    def latest_comparison(self, ref_id: Optional[str] = None) -> Optional[Dict]:
        comps = self.list_comparisons(ref_id)
        return comps[0] if comps else None

    # ── Auto-capture helpers ──────────────────────────────────────────────────

    def capture_from_sim_results(
        self,
        sim_results: Dict,
        name:        str,
        flowsheet:   str = "",
        stream_tags: Optional[List[str]] = None,
        properties:  Optional[List[str]] = None,
    ) -> ReferenceSet:
        """
        Build a ReferenceSet directly from current simulation results.
        Use this when the user wants to treat the current DWSIM run as the reference.
        """
        stream_data = sim_results.get("stream_results") or {}
        props = properties or list(PROPERTIES.keys())
        tags  = stream_tags or list(stream_data.keys())

        rs = ReferenceSet(
            ref_id    = str(uuid.uuid4())[:8],
            name      = name,
            flowsheet = flowsheet,
        )
        for tag in tags:
            if tag not in stream_data:
                continue
            sd = stream_data[tag]
            for prop in props:
                val = sd.get(prop)
                if val is not None:
                    try:
                        rs.entries.append(ReferenceEntry(
                            stream_tag   = tag,
                            property_key = prop,
                            manual_value = float(val),
                            note         = "auto-captured from DWSIM",
                        ))
                    except Exception:
                        pass
        return rs

    def get_summary(self) -> Dict:
        n_refs  = len(self._refs)
        n_comps = len(self._comps)
        last    = self._comps[-1] if self._comps else None
        return {
            "reference_sets":    n_refs,
            "total_comparisons": n_comps,
            "last_comparison":   last,
        }

    def clear_comparisons(self) -> None:
        self._comps = []
        self._save()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_store    = AccuracyStore()
_comparer = AccuracyComparer()


def get_accuracy_store()    -> AccuracyStore:    return _store
def get_accuracy_comparer() -> AccuracyComparer: return _comparer
