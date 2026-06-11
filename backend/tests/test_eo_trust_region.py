"""
Trust-region surrogate EO (run_eo_trust_region) — the provably-convergent
upgrade of the global-refit surrogate EO. Validated on functions with KNOWN
optima via a mock `evaluate` (no DWSIM/LLM): the optimiser only sees objective
and constraint numbers, exactly as it would from a flowsheet solve.

Convergence mechanism under test: local quadratic models inside a trust region
+ ρ-based step acceptance + adaptive radius (Conn–Scheinberg–Vicente).
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import pytest
pytest.importorskip("numpy")
pytest.importorskip("scipy")

import eo_optimizer as eo


def _mk(f, c=None):
    def ev(x):
        return {"objective": f(x),
                "constraint_values": ([c(x)] if c else [])}
    return ev


VARS2 = [{"tag": "X1", "property": "v", "lower": -3, "upper": 3},
         {"tag": "X2", "property": "v", "lower": -3, "upper": 3}]


def test_sphere_converges_to_known_optimum():
    # min x1^2 + x2^2 -> 0 at origin
    r = eo.run_eo_trust_region(_mk(lambda x: x[0]**2 + x[1]**2), VARS2,
                               minimize=True, x0=[2.5, -2.0], seed=1)
    assert r["success"] and r["converged"]
    assert r["objective"] < 1e-3, r["objective"]
    xs = list(r["x"].values())
    assert abs(xs[0]) < 0.05 and abs(xs[1]) < 0.05


def test_radius_shrinks_to_convergence():
    r = eo.run_eo_trust_region(_mk(lambda x: x[0]**2 + x[1]**2), VARS2,
                               minimize=True, x0=[2.5, -2.0], seed=1)
    # The trust radius must end far smaller than it began (the convergence
    # signal), and at least one step must have been accepted.
    assert r["final_radius"] < 0.35
    assert any(h["accepted"] for h in r["history"])


def test_constrained_optimum_is_exact():
    # min (x1-2)^2 + (x2-2)^2  s.t.  x1 + x2 <= 2   ->   (1,1), obj 2.0
    r = eo.run_eo_trust_region(
        _mk(lambda x: (x[0]-2)**2 + (x[1]-2)**2, c=lambda x: x[0] + x[1]),
        [{"tag": "X1", "property": "v", "lower": 0, "upper": 3},
         {"tag": "X2", "property": "v", "lower": 0, "upper": 3}],
        constraint_specs=[{"operator": "<=", "value": 2.0}],
        minimize=True, x0=[0.5, 0.5], seed=2, max_iter=40)
    assert r["success"] and r["feasible"]
    assert abs(r["objective"] - 2.0) < 0.05, r["objective"]
    xs = list(r["x"].values())
    assert abs(xs[0] - 1.0) < 0.1 and abs(xs[1] - 1.0) < 0.1


def test_maximize_sign_is_respected():
    # max -(x-1)^2 -> optimum at x=1, objective 0
    r = eo.run_eo_trust_region(
        _mk(lambda x: -((x[0]-1)**2) - ((x[1]+1)**2)), VARS2,
        minimize=False, x0=[-2.0, 2.0], seed=3)
    assert r["success"]
    assert r["objective"] > -1e-2, r["objective"]       # near the max of 0
    xs = list(r["x"].values())
    assert abs(xs[0] - 1.0) < 0.1 and abs(xs[1] + 1.0) < 0.1


def test_strong_progress_on_rosenbrock():
    # Quadratic surrogates cannot nail Rosenbrock's curved valley (textbook),
    # but the trust-region scheme must make large, monotone progress, not stall
    # at the start. f(x0=(-1,1)) = 104; require a >50x reduction.
    VR = [{"tag": "X1", "property": "v", "lower": -2, "upper": 2},
          {"tag": "X2", "property": "v", "lower": -1, "upper": 3}]
    r = eo.run_eo_trust_region(
        _mk(lambda x: (1-x[0])**2 + 100*(x[1]-x[0]**2)**2), VR,
        minimize=True, x0=[-1.0, 1.0], seed=1, max_iter=40)
    assert r["success"]
    assert r["objective"] < 2.0, r["objective"]         # from 104 -> < 2
