"""
intent.py
─────────
Intent declaration + intent-aware verification.

Implements the blueprint pattern from "Agentic AI process draft" §Verifier:
the LLM calls declare_intent BEFORE run_flowsheet, the executor stores the
intent on the bridge, and post-solve checks score the run against intent
(purity targets, setpoint tolerances, yield minimums) — not just generic
convergence.

This closes the gap between "converged" and "correct" that the blueprint
identifies as the highest-leverage verifier improvement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Element atom counts for the 40 compounds in safety_validator._ATOM_DB.
# Kept in sync; do not duplicate beyond that set without updating SF-14.
try:
    from safety_validator import SafetyValidator as _SV
    _ATOM_DB = getattr(_SV, "_ATOM_DB", {})
except Exception:
    _ATOM_DB = {}


@dataclass
class IntentTarget:
    """Single target to score the run against."""
    kind: str                       # "product_purity" | "unit_setpoint" | "min_yield" | "max_impurity"
    stream_tag: Optional[str] = None
    unit_tag: Optional[str] = None
    property_name: Optional[str] = None
    compound: Optional[str] = None
    expected: Optional[float] = None
    tolerance: Optional[float] = None   # absolute, in the property's natural unit


@dataclass
class Intent:
    """What this flowsheet is supposed to achieve."""
    feed_streams: List[str] = field(default_factory=list)
    product_streams: List[str] = field(default_factory=list)
    targets: List[IntentTarget] = field(default_factory=list)
    note: str = ""                              # user-visible description
    elements: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # ^ {compound: {element: atom_count}} — auto-populated from _ATOM_DB

    def to_dict(self) -> dict:
        return {
            "feed_streams":    list(self.feed_streams),
            "product_streams": list(self.product_streams),
            "targets":         [t.__dict__ for t in self.targets],
            "note":            self.note,
            "elements":        dict(self.elements),
        }


def parse_intent(payload: Dict[str, Any]) -> Intent:
    """Build an Intent from a declare_intent tool call payload.

    Auto-populates `elements` from the safety_validator atom DB so the
    LLM doesn't have to (and can't fabricate) atom counts."""
    intent = Intent(
        feed_streams=list(payload.get("feed_streams") or []),
        product_streams=list(payload.get("product_streams") or []),
        note=str(payload.get("note") or ""),
    )

    # Parse targets — accept either list[dict] or a single dict
    raw_targets = payload.get("targets") or []
    if isinstance(raw_targets, dict):
        raw_targets = [raw_targets]
    for t in raw_targets:
        if not isinstance(t, dict):
            continue
        kind = str(t.get("kind") or "").strip()
        if kind not in ("product_purity", "unit_setpoint", "min_yield", "max_impurity"):
            continue
        intent.targets.append(IntentTarget(
            kind=kind,
            stream_tag=t.get("stream_tag"),
            unit_tag=t.get("unit_tag"),
            property_name=t.get("property_name"),
            compound=t.get("compound"),
            expected=_as_float(t.get("expected")),
            tolerance=_as_float(t.get("tolerance")),
        ))

    # Auto-populate elements from atom DB for all compounds referenced
    # in target purities (and any compounds in the atom DB are fair game
    # for element-balance closure).
    intent.elements = {c: dict(atoms) for c, atoms in _ATOM_DB.items()}
    return intent


def _as_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Verification — runs after every save_and_solve when an intent is active
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IntentFinding:
    target: str             # short label like "purity.V1.NH3"
    severity: str           # "error" | "warning" | "info"
    message: str
    repair_hint: Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__


def verify_intent(
    intent: Intent,
    stream_results: Dict[str, Dict[str, Any]],
    unit_results: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[IntentFinding]:
    """Score a converged run against declared intent.

    Returns a list of IntentFinding. Empty list = run hit every target.

    `stream_results` and `unit_results` are flat dicts as returned by the
    DWSIM bridge: {tag: {property_key: value}}. Missing properties degrade
    a check to a warning ("couldn't read") rather than an error.
    """
    findings: List[IntentFinding] = []
    unit_results = unit_results or {}

    for t in intent.targets:
        if t.kind == "product_purity":
            findings.extend(_check_purity(t, stream_results))
        elif t.kind == "max_impurity":
            findings.extend(_check_max_impurity(t, stream_results))
        elif t.kind == "min_yield":
            findings.extend(_check_min_yield(t, intent, stream_results))
        elif t.kind == "unit_setpoint":
            findings.extend(_check_setpoint(t, unit_results))

    return findings


def _check_purity(t: IntentTarget,
                  stream_results: Dict[str, Dict[str, Any]]
                  ) -> List[IntentFinding]:
    if not (t.stream_tag and t.compound and t.expected is not None):
        return []
    s = stream_results.get(t.stream_tag)
    if not s:
        return [IntentFinding(
            target=f"purity.{t.stream_tag}.{t.compound}",
            severity="warning",
            message=f"Stream {t.stream_tag!r} not found in results — cannot verify purity target.",
            repair_hint=("Confirm the stream tag is correct, or that "
                         "save_and_solve completed for this stream."),
        )]
    # Try several common composition property keys
    x = _extract_mole_fraction(s, t.compound)
    if x is None:
        return [IntentFinding(
            target=f"purity.{t.stream_tag}.{t.compound}",
            severity="warning",
            message=f"Mole fraction of {t.compound} in {t.stream_tag} not in results.",
            repair_hint="Call get_phase_results or get_stream_properties to read composition.",
        )]
    if x < t.expected:
        return [IntentFinding(
            target=f"purity.{t.stream_tag}.{t.compound}",
            severity="error",
            message=f"{t.compound} purity in {t.stream_tag} is {x:.4f}; "
                    f"intent target ≥ {t.expected:.4f}.",
            repair_hint=("Tighten separation: lower flash temperature, raise column "
                         "reflux, or add a polishing stage / recycle for additional "
                         "conversion."),
        )]
    return []


def _check_max_impurity(t: IntentTarget,
                        stream_results: Dict[str, Dict[str, Any]]
                        ) -> List[IntentFinding]:
    if not (t.stream_tag and t.compound and t.expected is not None):
        return []
    s = stream_results.get(t.stream_tag)
    if not s:
        return []
    x = _extract_mole_fraction(s, t.compound)
    if x is None:
        return []
    if x > t.expected:
        return [IntentFinding(
            target=f"impurity.{t.stream_tag}.{t.compound}",
            severity="error",
            message=f"{t.compound} fraction in {t.stream_tag} is {x:.4f}; "
                    f"intent max ≤ {t.expected:.4f}.",
            repair_hint=("Add a separation / cleanup stage upstream of this stream, "
                         "or increase the upstream reactor conversion."),
        )]
    return []


def _check_min_yield(t: IntentTarget,
                     intent: Intent,
                     stream_results: Dict[str, Dict[str, Any]]
                     ) -> List[IntentFinding]:
    """Sum molar flow of t.compound across all product streams."""
    if not (t.compound and t.expected is not None):
        return []
    total = 0.0
    for tag in intent.product_streams:
        s = stream_results.get(tag) or {}
        n = _extract_molar_flow(s)
        x = _extract_mole_fraction(s, t.compound)
        if n is not None and x is not None:
            total += n * x
    if total < t.expected:
        return [IntentFinding(
            target=f"yield.{t.compound}",
            severity="error",
            message=f"Total {t.compound} yield across product streams is "
                    f"{total:.4g}; intent min ≥ {t.expected:.4g}.",
            repair_hint=("Increase reactor conversion, recycle more, or check that all "
                         "product streams are listed in declare_intent."),
        )]
    return []


def _check_setpoint(t: IntentTarget,
                    unit_results: Dict[str, Dict[str, Any]]
                    ) -> List[IntentFinding]:
    if not (t.unit_tag and t.property_name and t.expected is not None):
        return []
    u = unit_results.get(t.unit_tag)
    if not u:
        return []
    actual = u.get(t.property_name)
    if actual is None:
        return []
    try:
        actual = float(actual)
    except (TypeError, ValueError):
        return []
    tol = t.tolerance or 0.01 * abs(t.expected)
    if abs(actual - t.expected) > tol:
        return [IntentFinding(
            target=f"setpoint.{t.unit_tag}.{t.property_name}",
            severity="error",
            message=f"{t.unit_tag}.{t.property_name} = {actual:.4g}, "
                    f"requested {t.expected:.4g} (tol ±{tol:.3g}).",
            repair_hint=(f"Unit {t.unit_tag} may be over- or under-specified. "
                         f"Look for another constraint forcing {t.property_name} "
                         "off setpoint."),
        )]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Composition / flow extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _extract_mole_fraction(props: Dict[str, Any], compound: str) -> Optional[float]:
    """Best-effort mole-fraction extractor from a stream properties dict.

    Tries: mole_frac_<compound>, mole_fraction_<compound>, composition[compound],
           mole_fractions[compound], <compound>_mol_frac. Returns None if not found.
    """
    if not props:
        return None
    c = compound
    candidates = [
        f"mole_frac_{c}", f"mole_fraction_{c}", f"{c}_mol_frac",
        f"x_{c}", f"y_{c}",
    ]
    for key in candidates:
        v = props.get(key)
        if isinstance(v, (int, float)):
            return float(v)

    # Nested dicts
    for nest_key in ("composition", "mole_fractions", "compositions",
                     "overall_composition"):
        d = props.get(nest_key)
        if isinstance(d, dict):
            v = d.get(c) or d.get(c.lower()) or d.get(c.capitalize())
            if isinstance(v, (int, float)):
                return float(v)
    return None


def _extract_molar_flow(props: Dict[str, Any]) -> Optional[float]:
    if not props:
        return None
    for key in ("molar_flow_mol_s", "molar_flow_kmolh", "molar_flow",
                "molar_flow_kmol_h"):
        v = props.get(key)
        if isinstance(v, (int, float)) and v > 0:
            # Normalise to mol/s
            if "kmolh" in key or "kmol_h" in key:
                return float(v) * 1000.0 / 3600.0
            return float(v)
    return None
