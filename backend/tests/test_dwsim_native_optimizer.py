"""
Tests for the DWSIM-native optimization stack (run_dwsim_native_optimization
+ API endpoint /optimize/dwsim-native). Uses a mock bridge so the test
needs no DWSIM install.
"""

from __future__ import annotations
import math
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── Mock bridge that simulates a DWSIM flowsheet ─────────────────────────

class _MockBridge:
    """In-memory mock that stores property values and returns them on
    get/set without actually solving anything. Solve is always trivially
    'successful' so the optimizer can iterate."""

    def __init__(self):
        self.props = {}   # (tag, property) -> value
        self.solve_calls = 0

    def get_stream_property(self, tag, prop):
        v = self.props.get((tag, prop))
        return {"success": v is not None, "value": v} if v is not None \
            else {"success": False, "error": "not found"}

    def get_stream_properties(self, tag):
        out = {k[1]: v for k, v in self.props.items() if k[0] == tag}
        return {"success": True, "properties": out}

    def get_unit_op_properties(self, tag):
        return self.get_stream_properties(tag)

    def set_stream_property(self, tag, prop, value, unit=""):
        self.props[(tag, prop)] = float(value)
        return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self.props[(tag, prop)] = float(value)
        return {"success": True}

    def run_simulation(self):
        self.solve_calls += 1
        # The "physics": objective is a function of the inputs we've stored
        # — set by the test fixture below.
        return {"success": True}

    def save_and_solve(self):
        return self.run_simulation()


def _setup_parabola_landscape(bridge):
    """Wire up a simple parabolic objective.
    Objective = (T - 600)^2 + (F - 100)^2 (minimum at T=600, F=100, value=0).
    To simulate this through the property store, we'll have get_stream_property
    on 'OBJ.value' compute the objective from the currently-set decision vars."""

    real_get = bridge.get_stream_property

    def patched_get(tag, prop):
        if tag == "OBJ" and prop == "value":
            T = bridge.props.get(("RC", "T"), 0)
            F = bridge.props.get(("RC", "F"), 0)
            obj = (T - 600) ** 2 + (F - 100) ** 2
            return {"success": True, "value": obj}
        return real_get(tag, prop)

    bridge.get_stream_property = patched_get


# ─── Tests ────────────────────────────────────────────────────────────────

def test_native_optimizer_minimises_parabola_with_simplex():
    """Nelder-Mead simplex should drive a 2-D parabola to its known minimum."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    # Seed initial values away from the optimum so the table shows real change
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650,"initial":580},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="simplex", minimize=True, max_iter=80, tolerance=1e-4,
    )

    assert result["success"] is True, result
    assert result["best_objective"] is not None
    assert result["best_objective"] < 5.0, \
        f"Simplex did not converge: best={result['best_objective']}"
    # Variables table must contain Old/New/Change for both inputs
    by_var = {r["variable"]: r for r in result["variables_table"]}
    assert "RC.T" in by_var and "RC.F" in by_var
    assert abs(by_var["RC.T"]["new_value"] - 600) < 5, \
        f"T did not converge near 600: {by_var['RC.T']['new_value']}"
    assert abs(by_var["RC.F"]["new_value"] - 100) < 5, \
        f"F did not converge near 100: {by_var['RC.F']['new_value']}"


def test_native_optimizer_maximises_when_minimize_false():
    """Maximize mode: invert sign so the solver finds the global max."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    # Inverted parabola: max at T=600, F=100, value=100^2 + 50^2 = 12500
    def get_neg(tag, prop):
        if tag == "OBJ" and prop == "value":
            T = bridge.props.get(("RC", "T"), 0)
            F = bridge.props.get(("RC", "F"), 0)
            return {"success": True, "value": 50000 - ((T - 600) ** 2 + (F - 100) ** 2)}
        return {"success": False}
    bridge.get_stream_property = get_neg
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="simplex", minimize=False, max_iter=80,
    )
    assert result["success"] is True
    assert result["best_objective"] > 49990  # near 50000


def test_native_optimizer_reports_eval_cache():
    """Per-run evaluation cache: n_evaluations counts only real (uncached)
    solves, so it must equal the number of unique points; cache hits are
    reported separately and never negative."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650,"initial":580},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="de", minimize=True, max_iter=60, tolerance=1e-4,
    )
    assert result["success"] is True
    assert "n_cache_hits" in result and "n_unique_points" in result
    assert result["n_cache_hits"] >= 0
    # Each uncached solve adds exactly one unique point.
    assert result["n_evaluations"] == result["n_unique_points"]


def test_native_optimizer_expression_objective():
    """Composite objective via expression with named values."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    # Maximise H2 + CO  where H2 = 0.75 - 0.001*(T-620)^2,  CO = 0.20 - 0.0005*(T-620)^2
    def patched_get(tag, prop):
        T = bridge.props.get(("RC", "T"), 0)
        if (tag, prop) == ("PSA", "h2"):
            return {"success": True, "value": 0.75 - 0.0001 * (T - 620) ** 2}
        if (tag, prop) == ("PSA", "co"):
            return {"success": True, "value": 0.20 - 0.00005 * (T - 620) ** 2}
        return {"success": False}
    bridge.get_stream_property = patched_get
    bridge.props[("RC", "T")] = 580.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[{"tag":"RC","property":"T","unit":"C","lower":550,"upper":700}],
        objective={
            "type":"expression",
            "expression":"H2 + CO",
            "named_values":[
                {"name":"H2","tag":"PSA","property":"h2"},
                {"name":"CO","tag":"PSA","property":"co"},
            ],
        },
        method="simplex", minimize=False, max_iter=50,
    )
    assert result["success"] is True
    new_T = result["variables_table"][0]["new_value"]
    # The max of H2+CO occurs at T=620 (both terms peak there)
    assert abs(new_T - 620) < 5, f"T={new_T}, expected near 620"
    # Objective at optimum is 0.75 + 0.20 = 0.95
    assert result["best_objective"] >= 0.94


def test_native_optimizer_lbfgs_works():
    """Ensure the L-BFGS-B method path is reachable (not just simplex)."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650,"initial":580},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="lbfgs", minimize=True, max_iter=50,
    )
    assert result["success"] is True
    assert "LBFGS" in result["method"] or "BFGS" in result["method"]


def test_native_optimizer_de_works():
    """Differential Evolution must converge on a non-trivial landscape.
    DE is stochastic and DotNumerics' implementation has its own internal
    population size — give it a larger budget than the gradient methods."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0

    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="de", minimize=True, max_iter=400, tolerance=1e-3,
    )
    assert result["success"] is True
    # Looser bound — DE is global stochastic, parabola worst-corner is 5000,
    # so anything <500 is real convergence (10x improvement).
    assert result["best_objective"] < 500.0, \
        f"DE did not converge: best={result['best_objective']}"


def test_native_optimizer_returns_poster_style_table():
    """The variables_table must be exactly the poster format."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0
    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="simplex", minimize=True, max_iter=40,
    )
    assert result["success"] is True
    for row in result["variables_table"]:
        for key in ("variable","tag","property","unit","old_value",
                    "new_value","change","change_pct","lower","upper",
                    "at_lower","at_upper"):
            assert key in row, f"variables_table row missing '{key}': {row}"


def test_native_optimizer_handles_empty_vars():
    """Bad input must surface a structured error code."""
    from dwsim_native_optimizer import run_dwsim_native_optimization
    bridge = _MockBridge()
    r = run_dwsim_native_optimization(bridge, variables=[], objective={})
    assert r["success"] is False
    assert r["error_code"] in ("NO_VARIABLES", "NO_OBJECTIVE")


# ─── API integration test ────────────────────────────────────────────────

def test_api_optimize_dwsim_native_endpoint(monkeypatch):
    """POST /optimize/dwsim-native — full stack via TestClient + mock bridge."""
    from fastapi.testclient import TestClient
    import api as api_module
    from dwsim_native_optimizer import run_dwsim_native_optimization

    bridge = _MockBridge()
    _setup_parabola_landscape(bridge)
    bridge.props[("RC", "T")] = 580.0
    bridge.props[("RC", "F")] = 120.0
    # Add the dwsim_optimize shim the bridge would normally provide
    bridge.dwsim_optimize = (
        lambda variables, objective, method="simplex", minimize=True,
               max_iter=50, tolerance=1e-3, on_progress=None:
        run_dwsim_native_optimization(
            bridge, variables=variables, objective=objective,
            method=method, minimize=minimize, max_iter=max_iter,
            tolerance=tolerance, on_progress=on_progress,
        )
    )
    monkeypatch.setattr(api_module, "_get_bridge", lambda: bridge)

    client = TestClient(api_module.app)
    body = client.post("/optimize/dwsim-native", json={
        "variables":[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650,"initial":580},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150,"initial":120},
        ],
        "objective":{"type":"variable","tag":"OBJ","property":"value"},
        "method":"simplex","minimize":True,"max_iter":80,
    }).json()
    assert body["success"] is True, body
    assert body["best_objective"] < 5.0
    assert len(body["variables_table"]) == 2
