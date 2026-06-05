"""
Tests for the CMA-ES backend (external `cma` package) wired into
run_dwsim_native_optimization, plus the complex_optimizer global-method
selection. Uses an in-memory mock bridge — no DWSIM install required.

CMA-ES is added as an external optimiser because it is markedly more
sample-efficient than Nelder-Mead/DE on expensive black-box evaluations
(each DWSIM solve costs seconds), which is the real bottleneck here.
"""
from __future__ import annotations
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

cma = pytest.importorskip("cma")  # skip cleanly if the package is absent


class _MockBridge:
    def __init__(self, objective_fn):
        self.props = {}
        self._obj = objective_fn

    def get_stream_property(self, tag, prop):
        if tag == "OBJ" and prop == "value":
            return {"success": True, "value": self._obj(self.props)}
        v = self.props.get((tag, prop))
        return {"success": v is not None, "value": v}

    def get_stream_properties(self, tag):
        return {"success": True,
                "properties": {k[1]: v for k, v in self.props.items()
                               if k[0] == tag}}

    def get_unit_op_properties(self, tag):
        return self.get_stream_properties(tag)

    def set_stream_property(self, tag, prop, value, unit=""):
        self.props[(tag, prop)] = float(value)
        return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self.props[(tag, prop)] = float(value)
        return {"success": True}

    def run_simulation(self):
        return {"success": True}

    def save_and_solve(self):
        return {"success": True}


def test_cma_minimises_parabola():
    from dwsim_native_optimizer import run_dwsim_native_optimization
    # min at T=600, F=100, value 0
    br = _MockBridge(lambda p: (p.get(("RC", "T"), 0) - 600) ** 2
                              + (p.get(("RC", "F"), 0) - 100) ** 2)
    br.props[("RC", "T")] = 580.0
    br.props[("RC", "F")] = 120.0

    res = run_dwsim_native_optimization(
        br,
        variables=[
            {"tag": "RC", "property": "T", "unit": "C", "lower": 550, "upper": 650, "initial": 580},
            {"tag": "RC", "property": "F", "unit": "kg/h", "lower": 50, "upper": 150, "initial": 120},
        ],
        objective={"type": "variable", "tag": "OBJ", "property": "value"},
        method="cma", minimize=True, max_iter=200, tolerance=1e-6,
    )
    assert res["success"] is True, res
    assert res["best_objective"] < 1.0, res["best_objective"]
    assert res["solver_backend"] == "CMA-ES (cma package)", res["solver_backend"]
    assert res["used_native_dotnumerics"] is False
    by = {r["variable"]: r for r in res["variables_table"]}
    assert abs(by["RC.T"]["new_value"] - 600) < 2
    assert abs(by["RC.F"]["new_value"] - 100) < 2


def test_cma_handles_heterogeneous_scales():
    """One var ~1e5, the other ~10 — exercises the [0,1] normalisation so a
    single CMA-ES step size works across very different magnitudes."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    # min at FLOW=120000, T=40
    br = _MockBridge(lambda p: ((p.get(("F", "mass_flow"), 0) - 120000) / 1e4) ** 2
                              + (p.get(("F", "temperature"), 0) - 40) ** 2)
    br.props[("F", "mass_flow")] = 100000.0
    br.props[("F", "temperature")] = 15.0

    res = run_dwsim_native_optimization(
        br,
        variables=[
            {"tag": "F", "property": "mass_flow", "unit": "kg/h",
             "lower": 80000, "upper": 160000, "initial": 100000},
            {"tag": "F", "property": "temperature", "unit": "C",
             "lower": 10, "upper": 80, "initial": 15},
        ],
        objective={"type": "variable", "tag": "OBJ", "property": "value"},
        method="cma", minimize=True, max_iter=300, tolerance=1e-6,
    )
    assert res["success"] is True, res
    by = {r["variable"]: r for r in res["variables_table"]}
    assert abs(by["F.mass_flow"]["new_value"] - 120000) < 3000, by["F.mass_flow"]
    assert abs(by["F.temperature"]["new_value"] - 40) < 3, by["F.temperature"]


def test_cma_maximises():
    from dwsim_native_optimizer import run_dwsim_native_optimization
    br = _MockBridge(lambda p: 50000 - ((p.get(("RC", "T"), 0) - 600) ** 2
                                        + (p.get(("RC", "F"), 0) - 100) ** 2))
    br.props[("RC", "T")] = 580.0
    br.props[("RC", "F")] = 120.0
    res = run_dwsim_native_optimization(
        br,
        variables=[
            {"tag": "RC", "property": "T", "lower": 550, "upper": 650},
            {"tag": "RC", "property": "F", "lower": 50, "upper": 150},
        ],
        objective={"type": "variable", "tag": "OBJ", "property": "value"},
        method="cma", minimize=False, max_iter=200,
    )
    assert res["success"] is True
    assert res["best_objective"] > 49995, res["best_objective"]


def test_complex_optimizer_selects_cma_when_available():
    import complex_optimizer
    method, label = complex_optimizer._select_global_method()
    assert method == "cma" and label == "CMA-ES"


def test_cma_alias_resolves():
    from dwsim_native_optimizer import _METHOD_ALIASES
    for k in ("cma", "cma-es", "cmaes"):
        assert _METHOD_ALIASES[k] == "CMA_ES"
