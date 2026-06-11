"""
Tests for dwsim_native_solvers — the direct DotNumerics bindings.

Skipped on systems without DWSIM installed (DotNumerics is bundled with DWSIM,
so we use the same DLL-folder check as the e2e build-plan test).
"""

from __future__ import annotations
import math
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _dwsim_dlls_present() -> bool:
    try:
        from dwsim_bridge_v2 import _find_dll_folder
        return _find_dll_folder() is not None
    except Exception:
        return False


def _ensure_clr_loaded():
    """One-time CLR + DLL load. Required before importing dwsim_native_solvers
    if no bridge has been instantiated yet in this process."""
    if not _dwsim_dlls_present():
        return False
    from dwsim_bridge_v2 import _find_dll_folder
    dll = _find_dll_folder()
    try:
        import clr
        import glob as _g
        if dll not in sys.path:
            sys.path.insert(0, dll)
        for path in _g.glob(os.path.join(dll, "*.dll")):
            try: clr.AddReference(os.path.splitext(os.path.basename(path))[0])
            except Exception: pass
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _dwsim_dlls_present(),
    reason="DWSIM not installed — DotNumerics solvers require DWSIM DLLs",
)


@pytest.fixture(scope="module", autouse=True)
def _clr_setup():
    _ensure_clr_loaded()


# A simple 2-D quadratic with known min at (3, 4)
def _objective(x):
    return (x[0] - 3.0) ** 2 + (x[1] - 4.0) ** 2


def test_dotnumerics_available_after_clr_load():
    from dwsim_native_solvers import _dotnumerics_available
    assert _dotnumerics_available() is True, \
        "DotNumerics should be available after CLR + DLLs are loaded"


def test_lbfgs_b_finds_quadratic_minimum():
    from dwsim_native_solvers import solve_lbfgs
    r = solve_lbfgs(_objective, lower=[-10, -10], upper=[10, 10],
                     initial=[0.0, 0.0], tolerance=1e-8, max_iter=200)
    assert r["success"], r
    assert abs(r["best_x"][0] - 3.0) < 1e-4
    assert abs(r["best_x"][1] - 4.0) < 1e-4
    assert r["best_f"] < 1e-6
    assert r["solver"].startswith("DotNumerics.LBFGSB")


def test_simplex_finds_quadratic_minimum():
    from dwsim_native_solvers import solve_simplex
    r = solve_simplex(_objective, lower=[-10, -10], upper=[10, 10],
                       initial=[0.0, 0.0])
    assert r["success"], r
    assert abs(r["best_x"][0] - 3.0) < 1e-3
    assert abs(r["best_x"][1] - 4.0) < 1e-3
    assert r["solver"] == "DotNumerics.Simplex"


def test_truncated_newton_finds_quadratic_minimum():
    from dwsim_native_solvers import solve_truncated_newton
    r = solve_truncated_newton(_objective, lower=[-10, -10], upper=[10, 10],
                                initial=[0.0, 0.0])
    assert r["success"], r
    assert abs(r["best_x"][0] - 3.0) < 1e-3
    assert abs(r["best_x"][1] - 4.0) < 1e-3
    assert r["solver"] == "DotNumerics.TruncatedNewton"


def test_de_finds_quadratic_minimum():
    from dwsim_native_solvers import solve_de
    r = solve_de(_objective, lower=[-10, -10], upper=[10, 10],
                  initial=[0.0, 0.0], max_iter=100)
    assert r["success"], r
    # DE is stochastic; broader tolerance
    assert abs(r["best_x"][0] - 3.0) < 0.5
    assert abs(r["best_x"][1] - 4.0) < 0.5
    assert r["solver"].startswith("DWSIM.MathOps.DE")


def test_run_native_solver_dispatcher_handles_aliases():
    from dwsim_native_solvers import run_native_solver
    for name in ("simplex", "Nelder-Mead", "NelderMead",
                 "lbfgs", "L-BFGS", "lbfgs-b",
                 "newton", "truncated-newton", "tnc",
                 "de", "Differential-Evolution"):
        r = run_native_solver(name, _objective,
                               lower=[-10, -10], upper=[10, 10],
                               initial=[0.0, 0.0], max_iter=50,
                               tolerance=1e-4)
        assert r["success"], f"{name} failed: {r}"
        assert "solver_label" in r


def test_native_optimizer_actually_uses_dotnumerics():
    """The dwsim_native_optimizer wrapper must mark used_native_dotnumerics=True
    when DotNumerics is available."""
    from dwsim_native_optimizer import run_dwsim_native_optimization

    class _Bridge:
        def __init__(self):
            self.props = {("RC", "T"): 0.0, ("RC", "F"): 0.0}
        def get_stream_property(self, tag, prop):
            if (tag, prop) == ("OBJ", "value"):
                T = self.props.get(("RC", "T"), 0)
                F = self.props.get(("RC", "F"), 0)
                return {"success": True, "value": (T - 600) ** 2 + (F - 100) ** 2}
            v = self.props.get((tag, prop))
            return {"success": v is not None, "value": v}
        def get_stream_properties(self, tag):
            return {"success": True,
                    "properties": {k[1]: v for k, v in self.props.items() if k[0] == tag}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)
        def set_stream_property(self, tag, prop, val, unit=""):
            self.props[(tag, prop)] = float(val); return {"success": True}
        def set_unit_op_property(self, tag, prop, val):
            self.props[(tag, prop)] = float(val); return {"success": True}
        def run_simulation(self):
            return {"success": True}
        def save_and_solve(self):
            return {"success": True}

    bridge = _Bridge()
    result = run_dwsim_native_optimization(
        bridge,
        variables=[
            {"tag":"RC","property":"T","unit":"C","lower":550,"upper":650,"initial":580},
            {"tag":"RC","property":"F","unit":"kg/h","lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        method="simplex", minimize=True, max_iter=100,
    )
    assert result["success"]
    assert result["used_native_dotnumerics"] is True, \
        f"Wrapper did not use DotNumerics: backend={result.get('solver_backend')}"
    assert "DotNumerics" in result["solver_backend"]
    # Must still converge to the optimum
    assert result["best_objective"] < 1.0
