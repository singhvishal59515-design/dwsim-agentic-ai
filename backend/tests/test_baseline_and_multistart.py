"""
Tests for the two research-report (§4) improvements:
  • _verify_baseline_converges — converged-baseline pre-flight (§4a)
  • multi-start helpers (_sample_starts / _spec_with_start / _run_multistart) (§4c)
No DWSIM needed; mock bridge + mock run_fn.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# ── §4a: baseline convergence ─────────────────────────────────────────────

def test_baseline_converged_when_all_streams_ok():
    from optimization_orchestrator import _verify_baseline_converges
    class B:
        def check_convergence(self):
            return {"success": True, "converged": ["Feed", "Out"],
                    "not_converged": [], "missing": []}
    r = _verify_baseline_converges(B())
    assert r["converged"] is True
    assert r["n_converged"] == 2


def test_baseline_not_converged_flags_streams():
    from optimization_orchestrator import _verify_baseline_converges
    class B:
        def check_convergence(self):
            return {"success": True, "converged": [],
                    "not_converged": [{"tag": "Recycle"}], "missing": ["Out"]}
    r = _verify_baseline_converges(B())
    assert r["converged"] is False
    assert r["n_converged"] == 0
    assert "Recycle" in r["detail"] or "Out" in r["detail"]


def test_baseline_unknown_is_soft_pass():
    from optimization_orchestrator import _verify_baseline_converges
    class B:  # no check_convergence method
        pass
    r = _verify_baseline_converges(B())
    assert r["converged"] is None   # soft pass, never blocks


# ── §4c: multi-start ──────────────────────────────────────────────────────

def test_sample_starts_first_is_current_rest_in_bounds():
    from optimization_orchestrator import _sample_starts
    variables = [{"lower": 0.0, "upper": 10.0}, {"lower": 100.0, "upper": 200.0}]
    starts = _sample_starts(variables, 4)
    assert len(starts) == 4
    assert starts[0] is None                       # first = current point
    for s in starts[1:]:
        assert 0.0 <= s[0] <= 10.0
        assert 100.0 <= s[1] <= 200.0


def test_spec_with_start_overrides_initial():
    from optimization_orchestrator import _spec_with_start
    spec = {"variables": [{"tag": "F", "property": "T", "lower": 0, "upper": 10,
                           "initial": 5}], "minimize": True}
    out = _spec_with_start(spec, {0: 8.0})
    assert out["variables"][0]["initial"] == 8.0
    # original spec untouched
    assert spec["variables"][0]["initial"] == 5


def test_run_multistart_keeps_best_minimize():
    from optimization_orchestrator import _run_multistart
    spec = {"variables": [{"lower": 0, "upper": 10, "initial": 5}],
            "minimize": True}
    seen_initials = []
    def run_fn(s):
        init = s["variables"][0].get("initial")
        seen_initials.append(init)
        # objective improves as initial grows; best should be the largest init
        return {"success": True, "best_objective": -float(init)}
    best = _run_multistart(run_fn, spec, n_starts=3, minimize=True,
                           emit=lambda *a: None)
    assert best["_multistart_runs"] == 3
    assert len(seen_initials) == 3
    # minimising -init → best is the run with the largest initial value
    assert best["best_objective"] == min(-float(i) for i in seen_initials)


def test_run_multistart_returns_last_when_all_fail():
    from optimization_orchestrator import _run_multistart
    spec = {"variables": [{"lower": 0, "upper": 10, "initial": 5}],
            "minimize": True}
    best = _run_multistart(lambda s: {"success": False, "error": "x"},
                           spec, n_starts=2, minimize=True, emit=lambda *a: None)
    assert best["success"] is False
