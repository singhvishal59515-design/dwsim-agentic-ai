"""
Tests for NSGA-II multi-objective optimization (multiobjective_nsga.run_nsga2)
and its wiring into bridge.optimize_multiobjective.

Uses a known NON-CONVEX bi-objective problem:
    minimise f1 = x,  f2 = 1 - sqrt(x),   x in [0, 1]
The Pareto front is the entire concave curve. Weighted-sum can only reach the
two endpoints of such a front; NSGA-II recovers the whole spread — which is the
whole reason for adding it.
"""
from __future__ import annotations
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

pytest.importorskip("pymoo")

from multiobjective_nsga import run_nsga2, pymoo_available


VARIABLES = [{"tag": "X", "property": "v", "unit": "", "lower": 0.0, "upper": 1.0}]
OBJECTIVES = [
    {"tag": "F1", "property": "val", "minimize": True},
    {"tag": "F2", "property": "val", "minimize": True},
]


def _analytic_eval(x):
    xv = x[0]
    f1 = xv
    f2 = 1.0 - (xv ** 0.5)
    return [f1, f2]


def test_pymoo_is_available():
    assert pymoo_available() is True


def test_nsga2_recovers_nonconvex_front_spread():
    out = run_nsga2(_analytic_eval, VARIABLES, OBJECTIVES,
                    pop_size=24, n_gen=20, seed=1)
    assert out["success"], out
    assert out["method"] == "NSGA-II (pymoo)"
    pts = out["pareto_front"]
    assert len(pts) >= 8, f"front too small: {len(pts)}"

    xs = sorted(p["optimal_variables"]["X.v"] for p in pts)
    # NSGA-II must spread across the front, not collapse to the endpoints.
    assert xs[0] < 0.2 and xs[-1] > 0.8, xs
    interior = [x for x in xs if 0.2 < x < 0.8]
    assert len(interior) >= 2, f"no interior (non-convex) points: {xs}"


def test_nsga2_objective_values_reported_in_real_units():
    out = run_nsga2(_analytic_eval, VARIABLES, OBJECTIVES,
                    pop_size=16, n_gen=12, seed=3)
    for p in out["pareto_front"]:
        x = p["optimal_variables"]["X.v"]
        f1 = p["objective_values"]["F1.val"]
        f2 = p["objective_values"]["F2.val"]
        # Values and x are independently rounded to 6 dp, so recomputing from
        # the rounded x carries ~1e-6 of round-off; 1e-3 is a safe check.
        assert abs(f1 - x) < 1e-3
        assert abs(f2 - (1 - x ** 0.5)) < 1e-3


def test_nsga2_respects_maximize_sign():
    objs = [{"tag": "F1", "property": "val", "minimize": True},
            {"tag": "F2", "property": "val", "minimize": False}]
    # maximise f2 = sqrt(x); minimise f1 = x -> tension, front spreads in x
    out = run_nsga2(lambda x: [x[0], x[0] ** 0.5], VARIABLES, objs,
                    pop_size=16, n_gen=15, seed=5)
    assert out["success"]
    for p in out["pareto_front"]:
        x = p["optimal_variables"]["X.v"]
        assert abs(p["objective_values"]["F2.val"] - x ** 0.5) < 1e-3


# ── Bridge integration with a mock flowsheet ───────────────────────────────

class _MockBridge:
    """Minimal stand-in exposing exactly what optimize_multiobjective touches."""
    def __init__(self):
        self._flowsheet = object()
        self._flowsheet_path = None
        self._active_alias = None
        self._x = 0.0

    def load_flowsheet(self, path, alias=None):
        return {"success": True}

    def set_stream_property(self, tag, prop, value, unit=""):
        self._x = float(value)
        return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self._x = float(value)
        return {"success": True}

    def run_simulation(self):
        return {"success": True}

    def get_stream_properties(self, tag):
        if tag == "F1":
            return {"success": True, "properties": {"val": self._x}}
        if tag == "F2":
            return {"success": True, "properties": {"val": 1.0 - self._x ** 0.5}}
        return {"success": False, "properties": {}}

    def get_object_properties(self, tag):
        return self.get_stream_properties(tag)


def test_bridge_optimize_multiobjective_uses_nsga2():
    from dwsim_bridge_v2 import DWSIMBridge
    br = _MockBridge()
    # Call the real method bound onto the mock instance.
    out = DWSIMBridge.optimize_multiobjective(
        br, variables=VARIABLES, objectives=OBJECTIVES,
        n_points=16, n_gen=12, seed=2)
    assert out["success"], out
    assert out["method"] == "NSGA-II (pymoo)"
    assert out["n_points"] >= 6
