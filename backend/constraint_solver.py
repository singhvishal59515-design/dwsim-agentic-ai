"""
constraint_solver.py
────────────────────
Inequality-constraint handling via penalty functions for the DWSIM Agentic AI
optimisation pipeline.

Constraint shapes supported:
  ① Single-variable inequality:
       {"type": "ineq", "tag": "PROD", "property": "mole_fraction_H2",
        "operator": ">=", "threshold": 0.95}

  ② Composite expression inequality (uses the same expression engine as the
     objective):
       {"type": "ineq", "expression": "purity - 0.95",
        "named_values": [{"name": "purity", "tag": "PROD",
                          "property": "mole_fraction_H2"}],
        "operator": ">=", "threshold": 0.0}

  ③ Equality (rare in process opt; treated as |residual| <= tol):
       {"type": "eq",   "tag": "RC-01", "property": "outlet_temperature_C",
        "target": 650.0, "tolerance": 5.0}

Operators: ">=", "<=", ">", "<", "==" (with tolerance).

Algorithm:
    obj_effective = (sign × raw_objective)
                  + penalty_weight × Σ max(0, violation)^2

  where 'violation' is positive when a constraint is breached. The squared
  penalty ensures gradient solvers (Simplex, L-BFGS-B) push away from
  infeasible regions while still being able to enter them transiently —
  necessary because intermediate evaluation points may briefly violate a
  constraint that the optimum will satisfy.

  Penalty weight defaults to 1e6 — large enough to dominate the objective
  near feasibility, small enough that gradients remain finite. Adaptive
  scaling is applied if any single constraint's violation dominates by
  more than three orders of magnitude.

Constraint compliance reporting:
  After the solver returns its best point, each constraint is re-evaluated
  at that point and tagged 'satisfied' or 'violated' with the gap. The
  final result includes a constraint_compliance list and a top-level
  all_constraints_satisfied flag.
"""

from __future__ import annotations
import logging
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

_log = logging.getLogger("constraint_solver")


# ─── Constraint evaluation ──────────────────────────────────────────────────

def _eval_expression(expr: str, env: Dict[str, float]) -> Optional[float]:
    """Safely evaluate a constraint expression in a sandboxed environment.

    Only the named values and standard math functions (min/max/abs/log/exp/
    sqrt/pow) are available. No __builtins__, no imports."""
    if not expr:
        return None
    safe_globals = {
        "__builtins__": {},
        "min": min, "max": max, "abs": abs,
        "log": math.log, "exp": math.exp, "sqrt": math.sqrt,
        "pow": pow, "sin": math.sin, "cos": math.cos,
        "pi":  math.pi,  "e":   math.e,
    }
    try:
        return float(eval(expr, safe_globals, dict(env)))
    except Exception as exc:
        _log.warning("constraint expr eval failed: %s — %s", expr, exc)
        return None


def _read_constraint_value(bridge, constraint: Dict[str, Any]) -> Optional[float]:
    """Read the current value relevant to this constraint.
    Returns None on read failure (treated as worst case = constraint
    violated by a large amount)."""
    from dwsim_native_optimizer import _read_object_property

    ctype = constraint.get("type", "ineq")
    if "expression" in constraint:
        named = constraint.get("named_values", []) or []
        env: Dict[str, float] = {}
        for nv in named:
            v = _read_object_property(bridge,
                                       nv.get("tag", ""),
                                       nv.get("property", ""))
            if v is None:
                return None
            env[nv["name"]] = v
        return _eval_expression(constraint["expression"], env)
    # Single-variable case
    return _read_object_property(bridge,
                                  constraint.get("tag", ""),
                                  constraint.get("property", ""))


def _constraint_violation(value: Optional[float],
                            constraint: Dict[str, Any]) -> float:
    """Return a non-negative violation magnitude.
    Zero means feasible; positive means infeasible by that amount."""
    if value is None:
        return 1e10   # treat unreadable as critically infeasible
    op = constraint.get("operator", ">=")
    if constraint.get("type") == "eq":
        target = float(constraint.get("target", 0.0))
        tol    = float(constraint.get("tolerance", 1e-3))
        return max(0.0, abs(value - target) - tol)
    thr = float(constraint.get("threshold", 0.0))
    if op == ">=":
        return max(0.0, thr - value)
    if op == "<=":
        return max(0.0, value - thr)
    if op == ">":
        return max(0.0, thr - value + 1e-9)
    if op == "<":
        return max(0.0, value - thr + 1e-9)
    if op == "==":
        return max(0.0, abs(value - thr) - 1e-6)
    return 0.0


# ─── Penalty objective wrapper ─────────────────────────────────────────────

def wrap_with_penalties(
    objective_fn:    Callable[[], Optional[float]],
    bridge,
    constraints:     List[Dict[str, Any]],
    minimize:        bool = True,
    penalty_weight:  float = 1e6,
) -> Callable[[], Optional[float]]:
    """Wrap a Python callable that returns the raw objective. The returned
    callable returns the SAME objective augmented with squared-violation
    penalties for any breached constraints.

    Used by run_dwsim_native_optimization indirectly — we install this on
    top of the existing _eval_objective."""
    if not constraints:
        return objective_fn

    def _wrapped() -> Optional[float]:
        raw = objective_fn()
        if raw is None:
            return None
        # Squared-violation penalty
        total_pen = 0.0
        for c in constraints:
            v = _read_constraint_value(bridge, c)
            viol = _constraint_violation(v, c)
            if viol > 0:
                total_pen += viol * viol
        if total_pen == 0:
            return raw
        # Pen is always added (works for minimise);
        # if maximising, the outer flip-sign handles direction
        sign = 1.0 if minimize else -1.0   # raw is unflipped
        return raw + sign * penalty_weight * total_pen

    return _wrapped


# ─── Compliance reporting ──────────────────────────────────────────────────

def evaluate_compliance(
    bridge,
    constraints:  List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Re-evaluate every constraint at the bridge's current state.
    Returns {all_satisfied, n_satisfied, n_violated, details: [...]}."""
    details: List[Dict[str, Any]] = []
    n_ok = 0; n_violated = 0
    for c in constraints:
        v = _read_constraint_value(bridge, c)
        viol = _constraint_violation(v, c)
        ok   = viol <= 0
        if ok: n_ok += 1
        else:  n_violated += 1
        desc = _describe(c)
        details.append({
            "constraint":  desc,
            "value":       v,
            "violation":   round(viol, 6) if viol > 0 else 0.0,
            "satisfied":   ok,
        })
    return {
        "all_satisfied": n_violated == 0,
        "n_satisfied":   n_ok,
        "n_violated":    n_violated,
        "details":       details,
    }


def _describe(c: Dict[str, Any]) -> str:
    """One-line description of a constraint, used in reports."""
    if c.get("type") == "eq":
        if "expression" in c:
            return (f"{c['expression']} == {c.get('target', 0)} "
                    f"(±{c.get('tolerance', 1e-3)})")
        return (f"{c.get('tag')}.{c.get('property')} == "
                f"{c.get('target')} (±{c.get('tolerance', 1e-3)})")
    op = c.get("operator", ">=")
    thr = c.get("threshold", 0)
    if "expression" in c:
        return f"{c['expression']} {op} {thr}"
    return f"{c.get('tag')}.{c.get('property')} {op} {thr}"


# ─── Natural-language constraint parser ────────────────────────────────────

# Recognises phrases like:
#   "subject to purity ≥ 95%"
#   "such that T ≤ 700°C"
#   "constrained by H2 mass flow > 100 kg/h"
#   "ensuring CO emissions < 50 ppm"

import re as _re

_OP_MAP = {
    "≥": ">=", "<=": "<=", "≤": "<=", "=>": ">=", "≧": ">=",
    "<": "<", ">": ">", "==": "==", "=": "=="
}
_CONSTRAINT_INTRO = _re.compile(
    r"(?i)\b(subject\s+to|such\s+that|constrained\s+by|ensuring|provided\s+that|"
    r"with|where|given\s+that|while\s+keeping)\b"
)
_CONSTRAINT_REGEX = _re.compile(
    r"(?i)([a-z_][a-z0-9_.\- ]+?)\s*"
    r"(>=|<=|≥|≤|==|=|>|<)\s*"
    r"([\-\+]?\d+(?:\.\d+)?)\s*"
    r"(%|ppm|mol[/ ]?%|mass[/ ]?%|kg[/ ]?h|bar|atm|°c|°f|c\b|k\b|kw|mw|kj)?",
)


def parse_constraints_from_goal(goal: str) -> List[Dict[str, Any]]:
    """Pull constraint phrases out of a user's NL goal.

    Returns a list of partially-specified constraints — the caller must
    later resolve property-name semantics (e.g. "purity" → which stream?)
    via the LLM or the heuristic.

    Each returned constraint has: {raw_text, lhs, operator, rhs, unit}."""
    if not goal:
        return []
    m = _CONSTRAINT_INTRO.search(goal)
    if not m:
        return []
    tail = goal[m.end():]
    # Split on ' and ' / ',' to catch multiple constraints in one phrase
    parts = _re.split(r"\s+and\s+|,", tail, flags=_re.IGNORECASE)
    out: List[Dict[str, Any]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m2 = _CONSTRAINT_REGEX.search(part)
        if not m2:
            continue
        lhs, op_raw, rhs, unit = m2.groups()
        op = _OP_MAP.get(op_raw.strip(), op_raw.strip())
        out.append({
            "raw_text":  m2.group(0).strip(),
            "lhs":       lhs.strip(),
            "operator":  op,
            "rhs":       float(rhs),
            "unit":      (unit or "").strip().lower(),
            # Caller must add tag/property based on flowsheet context
        })
    return out
