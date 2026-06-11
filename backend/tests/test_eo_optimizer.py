"""
Tests for the equation-oriented (surrogate-NLP) optimizer, eo_optimizer.

Smooth quadratic so the response surface is EXACT:
    minimise f = (x-2)² + (y-2)²   subject to   x + y ≤ 2
Constrained optimum: (1,1), f = 2. With an exact surrogate the EO solve should
land essentially on it and the surrogate-vs-actual gap should be ~0.
"""
from __future__ import annotations
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

pytest.importorskip("scipy")

from eo_optimizer import run_eo_optimization, ipopt_available

VARIABLES = [
    {"tag": "X", "property": "v", "unit": "", "lower": 0.0, "upper": 3.0},
    {"tag": "Y", "property": "v", "unit": "", "lower": 0.0, "upper": 3.0},
]


def _evaluate(x):
    xx, yy = x
    return {"objective": (xx - 2) ** 2 + (yy - 2) ** 2,
            "constraint_values": [xx + yy]}


def test_eo_solves_constrained_quadratic():
    out = run_eo_optimization(
        _evaluate, VARIABLES,
        constraint_specs=[{"operator": "<=", "value": 2.0}],
        minimize=True, seed=1)
    assert out["success"], out
    x = out["x"]["X.v"]; y = out["x"]["Y.v"]
    assert x + y <= 2.0 + 1e-2, (x, y)
    assert abs(x - 1.0) < 0.1 and abs(y - 1.0) < 0.1, (x, y)
    # Exact surrogate -> tiny gap and near-perfect fit.
    assert out["r2_objective"] > 0.999, out["r2_objective"]
    assert out["surrogate_gap"] is not None and out["surrogate_gap"] < 1e-2
    assert out["feasible"] is True


def test_eo_unconstrained_reaches_interior_optimum():
    out = run_eo_optimization(_evaluate, VARIABLES, constraint_specs=None,
                              minimize=True, seed=2)
    assert out["success"], out
    # Unconstrained min at (2,2).
    assert abs(out["x"]["X.v"] - 2.0) < 0.1
    assert abs(out["x"]["Y.v"] - 2.0) < 0.1


def test_eo_reports_solver_and_note():
    out = run_eo_optimization(
        _evaluate, VARIABLES,
        constraint_specs=[{"operator": "<=", "value": 2.0}], seed=3)
    # Without an IPOPT binary this must transparently fall back to SciPy SLSQP.
    if ipopt_available():
        assert "IPOPT" in out["solver"]
    else:
        assert "SLSQP" in out["solver"]
    assert "equation-oriented" in out["note"].lower()


def test_eo_adaptive_refinement_shrinks_gap_on_nonquadratic():
    """A non-quadratic objective makes the quadratic surrogate imperfect, so the
    first round has a real surrogate-vs-actual gap. Adaptive refinement adds the
    validated optimum to the pool and should reduce that gap over rounds."""
    # f = exp(0.5x) + exp(0.5y): smooth but NOT quadratic; min at the lower bound.
    import math

    def _evaluate_nonquad(x):
        xx, yy = x
        return {"objective": math.exp(0.5 * xx) + math.exp(0.5 * yy),
                "constraint_values": []}

    out = run_eo_optimization(
        _evaluate_nonquad, VARIABLES, constraint_specs=None,
        minimize=True, seed=7, max_refine=4, refine_rel_tol=1e-4)
    assert out["success"], out
    assert out["n_refinements"] >= 1
    hist = out["refinement_history"]
    # The last round's gap should be no worse than the first (refinement helps,
    # never hurts, because the optimum is pinned by an exact sample).
    first_gap = hist[0]["surrogate_gap"]
    last_gap = hist[-1]["surrogate_gap"]
    assert last_gap <= first_gap + 1e-6, hist
    # Final validated point sits at/near the true optimum (0,0).
    assert out["x"]["X.v"] < 0.3 and out["x"]["Y.v"] < 0.3, out["x"]


def test_eo_quadratic_converges_in_one_round():
    """An exact quadratic surrogate -> gap ~0 immediately -> early convergence."""
    out = run_eo_optimization(
        _evaluate, VARIABLES,
        constraint_specs=[{"operator": "<=", "value": 2.0}],
        minimize=True, seed=1, max_refine=4, refine_rel_tol=1e-2)
    assert out["success"], out
    assert out["converged"] is True
    assert out["n_refinements"] == 1, out["refinement_history"]


def test_eo_quadratic_is_trustworthy():
    out = run_eo_optimization(
        _evaluate, VARIABLES,
        constraint_specs=[{"operator": "<=", "value": 2.0}],
        minimize=True, seed=1)
    # Exact quadratic → CV R² ≈ 1 → trustworthy.
    assert out["trustworthy"] is True
    assert out["cv_r2_objective"] is not None and out["cv_r2_objective"] > 0.9


def test_eo_flags_untrustworthy_on_highly_nonlinear():
    """A rugged, high-frequency objective is NOT well-described by a quadratic;
    the cross-validated guard must flag it as untrustworthy (honest about the
    surrogate-EO limitation rather than reporting a confident wrong optimum)."""
    import math

    def rugged(x):
        xx, yy = x
        return {"objective": math.sin(6 * xx) * math.cos(6 * yy) + 0.3 * xx,
                "constraint_values": []}

    out = run_eo_optimization(rugged, VARIABLES, constraint_specs=None,
                              minimize=True, seed=2, max_refine=0)
    assert out["success"] is True
    assert out["cv_r2_objective"] is not None
    # Quadratic cannot cross-validate a high-frequency surface.
    assert out["trustworthy"] is False
    assert "unreli" in out["note"].lower() or "R²" in out["note"]


def test_eo_insufficient_samples_errors_cleanly():
    out = run_eo_optimization(
        lambda x: {"objective": None, "constraint_values": []},
        VARIABLES, constraint_specs=[{"operator": "<=", "value": 2.0}],
        n_samples=6, seed=4)
    assert out["success"] is False
    assert "sample" in out["error"].lower()
