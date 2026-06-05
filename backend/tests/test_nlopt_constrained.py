"""
Tests for the NLopt constrained-NLP backend (nlopt_constrained.run_nlopt_constrained)
and its wiring into bridge.optimize_constrained.

Analytic problem with the unconstrained optimum OUTSIDE the feasible region:
    minimise f = (x-2)² + (y-2)²      (unconstrained min at (2,2))
    subject to x + y ≤ 2
The constrained optimum sits on the line x+y=2 nearest (2,2): (1,1), f = 2.
A solver that respects the constraint reaches (1,1); a soft-penalty one tends to
drift slightly infeasible.
"""
from __future__ import annotations
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

pytest.importorskip("nlopt")

from nlopt_constrained import run_nlopt_constrained, nlopt_available


def _evaluate(x):
    xx, yy = x
    return {"objective": (xx - 2) ** 2 + (yy - 2) ** 2,
            "constraint_values": [xx + yy]}


def test_nlopt_is_available():
    assert nlopt_available() is True


def test_nlopt_finds_constrained_optimum_inequality():
    res = run_nlopt_constrained(
        _evaluate, lower=[0, 0], upper=[3, 3], x0=[0.0, 0.0],
        constraint_specs=[{"operator": "<=", "value": 2.0}],
        minimize=True, max_evals=600, algorithm="isres", seed=7)
    assert res["success"], res
    assert res["feasible"] is True, res
    x, y = res["x"]
    assert x + y <= 2.0 + 1e-3, (x, y)
    # Objective near the true constrained optimum f=2.
    assert res["objective"] < 2.3, res["objective"]


def test_nlopt_equality_constraint():
    # Force x + y == 3 -> constrained min nearest (2,2) is (1.5,1.5), f=0.5.
    res = run_nlopt_constrained(
        _evaluate, lower=[0, 0], upper=[3, 3], x0=[0.0, 3.0],
        constraint_specs=[{"operator": "==", "value": 3.0}],
        minimize=True, max_evals=800, algorithm="isres", seed=11, eq_tol=1e-2)
    assert res["success"], res
    x, y = res["x"]
    assert abs((x + y) - 3.0) < 5e-2, (x, y)
    assert res["objective"] < 0.7, res["objective"]


def test_nlopt_counts_one_solve_per_point():
    """The cache must collapse objective + constraint queries to one solve."""
    solves = {"n": 0}

    def counting_eval(x):
        solves["n"] += 1
        return _evaluate(x)

    res = run_nlopt_constrained(
        counting_eval, lower=[0, 0], upper=[3, 3], x0=[1.0, 1.0],
        constraint_specs=[{"operator": "<=", "value": 2.0},
                          {"operator": ">=", "value": 0.5}],
        minimize=True, max_evals=120, algorithm="cobyla", seed=3)
    # Two constraints + objective would be 3× solves without caching; with the
    # cache the reported evaluation count should match the real solve count.
    assert res["n_evaluations"] == solves["n"], (res["n_evaluations"], solves["n"])


# ── Bridge integration ─────────────────────────────────────────────────────

class _MockBridge:
    def __init__(self):
        self._flowsheet = object()
        self._flowsheet_path = None
        self._active_alias = None
        self._vals = {}

    def load_flowsheet(self, path, alias=None):
        return {"success": True}

    def set_stream_property(self, tag, prop, value, unit=""):
        self._vals[(tag, prop)] = float(value)
        return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self._vals[(tag, prop)] = float(value)
        return {"success": True}

    def run_simulation(self):
        return {"success": True}

    def get_stream_properties(self, tag):
        x = self._vals.get(("X", "v"), 0.0)
        y = self._vals.get(("Y", "v"), 0.0)
        if tag == "OBJ":
            return {"success": True,
                    "properties": {"f": (x - 2) ** 2 + (y - 2) ** 2}}
        if tag == "SUM":
            return {"success": True, "properties": {"s": x + y}}
        return {"success": False, "properties": {}}

    def get_object_properties(self, tag):
        return self.get_stream_properties(tag)

    def get_simulation_results(self):
        return {"stream_results": {}}


def test_bridge_optimize_constrained_uses_nlopt():
    from dwsim_bridge_v2 import DWSIMBridgeV2
    br = _MockBridge()
    out = DWSIMBridgeV2.optimize_constrained(
        br,
        variables=[{"tag": "X", "property": "v", "unit": "", "lower": 0, "upper": 3, "initial": 0},
                   {"tag": "Y", "property": "v", "unit": "", "lower": 0, "upper": 3, "initial": 0}],
        observe_tag="OBJ", observe_property="f",
        constraints=[{"tag": "SUM", "property": "s", "operator": "<=", "value": 2.0}],
        minimize=True, max_iter=80)
    assert out["success"], out
    assert "NLopt" in out["solver_backend"], out["solver_backend"]
    assert out["all_constraints_satisfied"] is True, out["constraints"]
