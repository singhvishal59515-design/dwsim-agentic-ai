"""
Tests for the surrogate-assisted (EGO) optimizer. The flowsheet evaluation is
replaced by a known analytic objective (a quadratic bowl) via monkeypatching
the native optimizer's write/solve/read helpers, so the test needs no DWSIM and
can assert (a) it converges near the true optimum and (b) it does so within a
BOUNDED number of real solves — the whole point of the surrogate approach.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

VARS = [{"tag": "V1", "property": "x", "lower": 0.0, "upper": 10.0},
        {"tag": "V2", "property": "x", "lower": 0.0, "upper": 10.0}]
OBJ = {"type": "variable", "tag": "OBJ", "property": "val"}


def _patch_bowl(monkeypatch, target=(3.0, 7.0), solve_ok=True):
    """Objective = (x1-t1)^2 + (x2-t2)^2 — minimum 0 at `target`."""
    import dwsim_native_optimizer as dno
    state = {}
    def fake_write(bridge, tag, prop, val, unit=""):
        state[(tag, prop)] = float(val)
    def fake_solve(bridge):
        return solve_ok
    def fake_read(bridge, tag, prop):
        if (tag, prop) == ("OBJ", "val"):
            x1 = state.get(("V1", "x"), 0.0)
            x2 = state.get(("V2", "x"), 0.0)
            return (x1 - target[0]) ** 2 + (x2 - target[1]) ** 2
        return state.get((tag, prop))
    monkeypatch.setattr(dno, "_write_object_property", fake_write)
    monkeypatch.setattr(dno, "_solve_flowsheet", fake_solve)
    monkeypatch.setattr(dno, "_read_object_property", fake_read)
    return state


def test_surrogate_converges_within_bounded_real_solves(monkeypatch):
    _patch_bowl(monkeypatch)
    from surrogate_optimizer import run_surrogate_assisted_optimization
    res = run_surrogate_assisted_optimization(
        None, VARS, OBJ, minimize=True,
        n_initial=12, n_refine=10, surrogate_samples=1500)
    assert res["success"] is True
    assert res["method"] == "surrogate_ego"
    # The defining property: real solves are bounded by the budget, NOT by the
    # thousands of cheap surrogate evaluations.
    assert res["real_solves"] <= 12 + 10
    # And it actually got close to the true optimum (0).
    assert res["best_objective"] < 4.0, res["best_objective"]
    assert "kriging" in res["solver_backend"].lower()


def test_surrogate_maximize_sense(monkeypatch):
    # Maximise -(bowl): optimum is 0 at the target, maximisation should approach 0.
    _patch_bowl(monkeypatch, target=(5.0, 5.0))
    from surrogate_optimizer import run_surrogate_assisted_optimization
    res = run_surrogate_assisted_optimization(
        None, VARS, OBJ, minimize=False,
        n_initial=12, n_refine=8, surrogate_samples=1200)
    # The reported best_objective is in the user's (maximise) sense; for the
    # bowl read directly that means the largest value seen — sanity check shape.
    assert res["success"] is True
    assert "best_params" in res and len(res["variables_table"]) <= 2


def test_surrogate_all_failed_solves(monkeypatch):
    _patch_bowl(monkeypatch, solve_ok=False)   # every solve fails
    from surrogate_optimizer import run_surrogate_assisted_optimization
    res = run_surrogate_assisted_optimization(
        None, VARS, OBJ, minimize=True, n_initial=6, n_refine=2)
    assert res["success"] is False
    assert res["error_code"] == "ALL_EVALS_FAILED"


def test_result_shape_for_workflow(monkeypatch):
    _patch_bowl(monkeypatch)
    from surrogate_optimizer import run_surrogate_assisted_optimization
    res = run_surrogate_assisted_optimization(
        None, VARS, OBJ, minimize=True, n_initial=8, n_refine=4)
    for key in ("best_objective", "n_evaluations", "variables_table",
                "method", "solver_backend", "minimize", "history"):
        assert key in res, f"missing {key}"
