"""
Regression: optimisation of a UNIT-OP decision variable (e.g. a heater outlet
temperature) was silently frozen. _write_object_property tried
set_stream_property first, which can spuriously report success on a unit-op tag
WITHOUT changing the setpoint, so set_unit_op_property was never reached — the
variable never moved and the objective stayed at its baseline. Found by running
a live DWSIM heater-duty optimisation end-to-end.

This mock reproduces that exact asymmetry: set_stream_property "succeeds" on the
unit-op tag but is a no-op; only set_unit_op_property actually writes. With the
strict-first fix the optimiser can move the variable and reach the optimum.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _MisroutingBridge:
    """set_stream_property spuriously succeeds (no-op) on the unit-op tag;
    only set_unit_op_property actually changes the value."""
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
                "properties": {k[1]: v for k, v in self.props.items() if k[0] == tag}}

    def set_stream_property(self, tag, prop, value, unit=""):
        # The bug: reports success but does NOT write the unit-op setpoint.
        return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self.props[(tag, prop)] = float(value)
        return {"success": True}

    def run_simulation(self):
        return {"success": True}

    def save_and_solve(self):
        return {"success": True}


def test_unit_op_variable_actually_moves_and_optimises():
    from dwsim_native_optimizer import run_dwsim_native_optimization
    # Objective minimised at outlet_T = 40 (duty grows with T): f = (T - 40)^2.
    br = _MisroutingBridge(lambda p: (p.get(("H-101", "outlet_temperature"), 90.0) - 40.0) ** 2)
    br.props[("H-101", "outlet_temperature")] = 90.0   # baseline

    res = run_dwsim_native_optimization(
        br,
        variables=[{"tag": "H-101", "property": "outlet_temperature", "unit": "C",
                    "lower": 40, "upper": 120, "initial": 90}],
        objective={"type": "variable", "tag": "OBJ", "property": "value"},
        method="simplex", minimize=True, max_iter=200, tolerance=1e-8,
    )
    assert res["success"], res
    # The setpoint must actually have changed away from the 90 baseline …
    final = br.props[("H-101", "outlet_temperature")]
    assert abs(final - 90.0) > 1.0, f"variable never moved: {final}"
    # … and converged to the true optimum at 40.
    assert abs(final - 40.0) < 1.0, f"did not reach optimum: {final}"
    assert res["best_objective"] < 1.0, res["best_objective"]


def test_write_helper_prefers_strict_unitop_setter():
    from dwsim_native_optimizer import _write_object_property
    br = _MisroutingBridge(lambda p: 0.0)
    ok = _write_object_property(br, "H-101", "outlet_temperature", 55.0, "C")
    assert ok is True
    # Strict-first means the real (unit-op) write happened, not the no-op stream one.
    assert br.props[("H-101", "outlet_temperature")] == 55.0
