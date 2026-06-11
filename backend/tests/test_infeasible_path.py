"""
Infeasible-path (simultaneous tear + optimize) SQP — the central Aspen optimizer
technique. Validated on an analytic reactor-with-recycle whose optimum is found
independently by the classic feasible-path approach (fully converge the recycle
for each design, then optimize). The two must agree, the recycle must close, and
infeasible-path must use far fewer flowsheet passes (it avoids the inner
convergence loop). Engine-agnostic: the optimiser only sees one_pass numbers.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import pytest
pytest.importorskip("scipy")

from infeasible_path_optimizer import run_infeasible_path_optimization

# Reactor with recycle: fresh feed F, per-pass conversion u (design), recycle
# fraction rf of the unreacted stream. One forward pass with a GUESSED recycle t.
_F, _RF, _PRICE, _K = 100.0, 0.6, 1.0, 80.0


def _one_pass(design, tear, counter=None):
    u, t = float(design[0]), float(tear[0])
    if counter is not None:
        counter[0] += 1
    total = _F + t
    return {"objective": _PRICE * total * u - _K * u * u,     # profit
            "computed_tear": [total * (1 - u) * _RF],          # recomputed recycle
            "constraint_values": []}


def _feasible_path_reference():
    """Classic approach: converge the recycle for each u, then optimize u."""
    from scipy.optimize import minimize_scalar
    c = [0]

    def profit_converged(u):
        t = 0.0
        for _ in range(300):
            nt = _one_pass([u], [t], c)["computed_tear"][0]
            if abs(nt - t) < 1e-11:
                t = nt; break
            t = nt
        return _one_pass([u], [t], c)["objective"]

    r = minimize_scalar(lambda u: -profit_converged(u), bounds=(0.3, 0.9),
                        method="bounded")
    return r.x, -r.fun, c[0]


DESIGN = [{"tag": "RX", "property": "conversion", "lower": 0.3, "upper": 0.9}]
TEAR = [{"tag": "REC", "property": "flow", "lower": 0.0, "upper": 100.0}]


def test_matches_feasible_path_optimum_and_closes_recycle():
    u_ref, p_ref, _ = _feasible_path_reference()
    r = run_infeasible_path_optimization(
        _one_pass, DESIGN, TEAR, minimize=False,
        x0_design=[0.6], x0_tear=[30.0])
    assert r["success"]
    u = list(r["design"].values())[0]
    # Reaches the SAME optimum found by full recycle convergence.
    assert abs(u - u_ref) < 0.02, (u, u_ref)
    assert abs(r["objective"] - p_ref) < 0.5, (r["objective"], p_ref)
    # And the recycle is actually closed at that point.
    assert r["recycle_closed"]
    assert abs(r["max_closure_residual"]) < 1e-4


def test_uses_far_fewer_passes_than_feasible_path():
    _, _, feasible_passes = _feasible_path_reference()
    r = run_infeasible_path_optimization(
        _one_pass, DESIGN, TEAR, minimize=False,
        x0_design=[0.6], x0_tear=[30.0])
    # Avoiding the inner convergence loop must cut passes substantially
    # (measured ~13x here; assert a safe >=3x).
    assert r["n_passes"] * 3 <= feasible_passes, (r["n_passes"], feasible_passes)


def _one_pass_with_product(design, tear):
    u, t = float(design[0]), float(tear[0])
    total = _F + t
    return {"objective": _PRICE * total * u - _K * u * u,
            "computed_tear": [total * (1 - u) * _RF],
            "constraint_values": [total * u]}          # product rate P


def test_respects_a_process_constraint():
    # Unconstrained optimum produces P ~ 72. Impose P <= 65: the optimiser must
    # back off conversion AND still close the recycle simultaneously.
    r = run_infeasible_path_optimization(
        _one_pass_with_product, DESIGN, TEAR,
        constraint_specs=[{"tag": "PROD", "property": "P",
                           "operator": "<=", "value": 65.0}],
        minimize=False, x0_design=[0.6], x0_tear=[30.0])
    assert r["success"] and r["recycle_closed"]
    assert r["feasible"], r["closure_residuals"]
    # Product must honour the cap (small tolerance), proving the constraint and
    # the recycle closure are satisfied together.
    u = list(r["design"].values())[0]
    t = list(r["tear"].values())[0]
    P = (_F + t) * u
    assert P <= 65.0 + 0.5, P
