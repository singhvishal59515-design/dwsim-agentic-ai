"""
Total Annualized Cost (TAC) as an optimization objective — the canonical Aspen
economic-optimization workflow. Validates (a) the TAC arithmetic (CRF, Turton
power-law CAPEX, utility OPEX) against hand calculations, and (b) that the
project's optimizer driven by a TAC objective finds the cost-optimal size on a
convex CAPEX↕OPEX trade-off (matching a brute-force reference), with the optimum
strictly interior — i.e. a real economic trade-off, not a bound.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import pytest
pytest.importorskip("numpy")

from tac_objective import (capital_recovery_factor, total_annualized_cost,
                           equipment_capex, make_tac_objective)


def test_crf_formula():
    assert capital_recovery_factor(0.10, 10) == pytest.approx(0.162745, abs=1e-5)
    assert capital_recovery_factor(0.0, 10) == pytest.approx(0.1)        # 1/n
    assert capital_recovery_factor(0.10, 1) == pytest.approx(1.10)


def test_tac_components_match_hand_calc():
    r = total_annualized_cost([{"type": "heatexchanger", "size": 100.0}],
                              [{"kind": "heat", "duty_kW": 500.0}],
                              rate=0.10, years=10, hours_per_year=8000)
    cap = 12000 * 100 ** 0.6 * 3.2          # a·S^b·F_BM
    op = 500 * 8000 * 3600 * 18e-6          # kW·h·3600·$/kJ
    assert r["capex_installed"] == pytest.approx(cap, rel=1e-9)
    assert r["annual_opex"] == pytest.approx(op, rel=1e-9)
    assert r["tac"] == pytest.approx(0.162745 * cap + op, rel=1e-4)


def test_capex_scales_with_size():
    big = equipment_capex([{"type": "heatexchanger", "size": 200.0}])["installed_total"]
    small = equipment_capex([{"type": "heatexchanger", "size": 50.0}])["installed_total"]
    assert big > small                       # power law is increasing
    # b<1 => sub-linear (economy of scale): 4× size costs < 4× money.
    assert big < 4 * small


def _trade_off_tac(x):
    # HX area x; external heating duty falls with area (diminishing returns).
    return total_annualized_cost(
        [{"type": "heatexchanger", "size": float(x)}],
        [{"kind": "heat", "duty_kW": 2000.0 / float(x)}])["tac"]


def test_optimizer_minimizes_tac_to_interior_optimum():
    import numpy as np
    from dwsim_native_optimizer import run_dwsim_native_optimization

    # Brute-force reference optimum.
    xs = np.linspace(5, 120, 4000)
    ys = np.array([_trade_off_tac(x) for x in xs])
    x_ref = float(xs[int(np.argmin(ys))])
    assert 5 < x_ref < 120                    # genuinely interior

    # Mock bridge whose objective reads the TAC at the current area.
    class _Bridge:
        def __init__(self): self.p = {}
        def get_stream_property(self, t, pr):
            if t == "OBJ" and pr == "value":
                return {"success": True, "value": _trade_off_tac(self.p.get(("HX", "area"), 50.0))}
            v = self.p.get((t, pr)); return {"success": v is not None, "value": v}
        def get_stream_properties(self, t):
            return {"success": True, "properties": {k[1]: v for k, v in self.p.items() if k[0] == t}}
        def set_unit_op_property(self, t, pr, v, unit=""): self.p[(t, pr)] = float(v); return {"success": True}
        def set_stream_property(self, t, pr, v, u=""): return {"success": False}
        def run_simulation(self): return {"success": True}
        def save_and_solve(self): return {"success": True}

    b = _Bridge(); b.p[("HX", "area")] = 50.0
    res = run_dwsim_native_optimization(
        b, variables=[{"tag": "HX", "property": "area", "unit": "m2",
                       "lower": 5, "upper": 120, "initial": 50}],
        objective={"type": "variable", "tag": "OBJ", "property": "value"},
        method="simplex", minimize=True, max_iter=200, tolerance=1e-8)
    assert res["success"]
    x_opt = [r["new_value"] for r in res["variables_table"]
             if r["variable"].startswith("HX")][0]
    assert abs(x_opt - x_ref) < 1.5, (x_opt, x_ref)        # found the cost optimum
    assert res["best_objective"] < _trade_off_tac(5) * 0.6  # well below the bound


def test_make_tac_objective_reads_live_state():
    state = {"equipment": [{"type": "column", "size": 20}],
             "duties": [{"kind": "heat", "duty_kW": 800},
                        {"kind": "cool", "duty_kW": 600}]}
    obj = make_tac_objective(lambda: state)
    out = obj()
    assert out["objective"] > 0
    assert out["tac_breakdown"]["annual_opex"] > 0
    # Empty state -> None objective (failed evaluation), not a crash.
    assert make_tac_objective(lambda: {})()["objective"] is None
