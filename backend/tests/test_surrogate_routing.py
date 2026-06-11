"""
Tests for threshold-aware surrogate routing (research-report Stage 3).
The kriging/GP surrogate already exists; these verify the WHEN-to-use-it
judgment: route to it only for complex flowsheets with expensive solves, and
normalise its result to the workflow's shape. No DWSIM needed.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

_VAR_OBJ = {"type": "variable", "tag": "PROD", "property": "mole_fraction_CH4"}
_SPEC = {"objective": _VAR_OBJ,
         "variables": [{"tag": "F", "property": "temperature",
                        "lower": 0, "upper": 100, "initial": 50}],
         "minimize": False, "method": "simplex"}


class _Bridge:
    def __init__(self): self.solved = 0
    def run_simulation(self): self.solved += 1; return {"success": True}


def test_simple_flowsheet_skips_surrogate():
    from optimization_orchestrator import _should_use_surrogate
    b = _Bridge()
    r = _should_use_surrogate(b, _SPEC, {"recommended_path": "simple",
                                         "complexity_score": 1}, lambda *a: None)
    assert r["use"] is False
    assert b.solved == 0          # never even measured (no cost on simple)


def test_complex_slow_solve_uses_surrogate(monkeypatch):
    from optimization_orchestrator import _should_use_surrogate
    monkeypatch.setenv("SURROGATE_SOLVE_THRESHOLD_S", "0")  # any solve counts as slow
    monkeypatch.setenv("SURROGATE_AUTO", "1")
    r = _should_use_surrogate(_Bridge(), _SPEC,
                              {"recommended_path": "complex",
                               "complexity_score": 8}, lambda *a: None)
    assert r["use"] is True
    assert r["solve_time_s"] is not None


def test_complex_fast_solve_skips_surrogate(monkeypatch):
    from optimization_orchestrator import _should_use_surrogate
    monkeypatch.setenv("SURROGATE_SOLVE_THRESHOLD_S", "9999")  # nothing is that slow
    r = _should_use_surrogate(_Bridge(), _SPEC,
                              {"recommended_path": "complex",
                               "complexity_score": 8}, lambda *a: None)
    assert r["use"] is False


def test_expression_objective_skips_surrogate(monkeypatch):
    from optimization_orchestrator import _should_use_surrogate
    monkeypatch.setenv("SURROGATE_SOLVE_THRESHOLD_S", "0")
    spec = {**_SPEC, "objective": {"type": "expression", "expression": "a-b"}}
    r = _should_use_surrogate(_Bridge(), spec,
                              {"recommended_path": "complex"}, lambda *a: None)
    assert r["use"] is False


def test_surrogate_disabled_by_env(monkeypatch):
    from optimization_orchestrator import _should_use_surrogate
    monkeypatch.setenv("SURROGATE_AUTO", "0")
    r = _should_use_surrogate(_Bridge(), _SPEC,
                              {"recommended_path": "complex"}, lambda *a: None)
    assert r["use"] is False


def test_surrogate_result_normalised_to_workflow_shape():
    from optimization_orchestrator import _run_surrogate_optimization
    class B:
        def bayesian_optimize(self, **kw):
            return {"success": True, "best_value": 0.95,
                    "best_params": {"F.temperature": 72.0}, "n_evals": 18,
                    "converged": True, "duration_s": 12.3}
        # for _read_object_property old-value lookup
        def get_stream_property(self, tag, prop):
            return {"success": True, "value": 50.0}
    out = _run_surrogate_optimization(B(), _SPEC, max_iter=20,
                                      on_eval=None, emit=lambda *a: None)
    assert out["success"] is True
    assert out["best_objective"] == 0.95          # mapped from best_value
    assert out["n_evaluations"] == 18             # mapped from n_evals
    assert out["method"] == "bayesian_gp"
    assert "kriging" in out["solver_backend"].lower()
    # variables_table built with old→new
    assert out["variables_table"][0]["old_value"] == 50.0
    assert out["variables_table"][0]["new_value"] == 72.0
