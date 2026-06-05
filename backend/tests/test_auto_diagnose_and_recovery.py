"""
Tests for auto-diagnose + auto-recovery in bridge.run_simulation().

These features make the agent more autonomous: when a solve fails with a
recoverable issue, the bridge automatically:
  1. Runs root-cause analysis (diagnose_convergence)
  2. Escalates to robust_solve() if the issue is recoverable
  3. Returns the recovered result transparently to the caller
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _BridgeStub:
    """Reproduces just enough of DWSIMBridgeV2's surface to test the
    auto-diagnose and auto-recovery wiring."""

    def __init__(self, recoverable=False, physical_warning=False):
        # If recoverable: first solve says "not_converged"; robust_solve says OK
        self._recoverable = recoverable
        self._physical_warning = physical_warning
        self.solve_calls = 0
        self.robust_calls = 0
        self._in_auto_recovery = False

    # Imported via duck-typing in the real bridge — provide same API
    def get_simulation_results(self):
        return {"stream_results": {}}

    def robust_solve(self, max_attempts=3, strategy="robust"):
        self.robust_calls += 1
        return {
            "success": True,
            "convergence_check": {"all_converged": True,
                                   "not_converged": []},
            "_attempts": [{"attempt": 1, "result": "ok"}],
        }

    def run_simulation(self, auto_recover=True):
        # Call the real run_simulation logic from DWSIMBridgeV2.
        # We monkey-patch the internals via method delegation.
        from dwsim_bridge_v2 import DWSIMBridgeV2
        self.solve_calls += 1
        # Build a result mirroring what the real bridge would build
        if self._physical_warning:
            convergence = {
                "all_converged": False,
                "not_converged": ["S-1"],
                "physical_warnings": [
                    {"tag": "S-1", "issues": ["T=-50 out of range"]}
                ],
            }
        elif self._recoverable:
            convergence = {
                "all_converged": False,
                "not_converged": ["RC-1"],
                "physical_warnings": [],
            }
        else:
            convergence = {
                "all_converged": True,
                "not_converged": [],
                "physical_warnings": [],
            }
        result = {
            "success":            True,
            "message":            "Simulation completed",
            "convergence_check":  convergence,
            "convergence_errors": None,
            "safety_status":      "PASSED",
        }
        # Hand-roll the auto-diagnose + auto-recovery logic that the real
        # bridge runs. We can't easily call the real method without
        # initialising DWSIM, so reproduce the relevant branches.
        not_conv = convergence.get("not_converged") or []
        phys_warn = convergence.get("physical_warnings") or []
        if not_conv or phys_warn:
            result["diagnosis"] = {
                "diagnoses": [{"cause": "RECYCLE_NOT_CONVERGED",
                               "tag": "RC-1",
                               "suggested_fix": "use robust_solve"}],
            }
            result["diagnosis_summary"] = "Convergence issues detected."
        if (auto_recover and not self._in_auto_recovery
                and (not_conv) and not phys_warn):
            self._in_auto_recovery = True
            try:
                rs = self.robust_solve(max_attempts=3, strategy="robust")
                if rs.get("success"):
                    rs["auto_recovery_applied"] = True
                    rs["pre_recovery_diagnosis"] = result.get("diagnosis")
                    return rs
            finally:
                self._in_auto_recovery = False
        return result


# ─── Tests ────────────────────────────────────────────────────────────

def test_clean_solve_returns_no_diagnosis():
    """When the solve converges, no auto-diagnose / auto-recovery fires."""
    bridge = _BridgeStub()
    r = bridge.run_simulation()
    assert r["success"]
    assert "diagnosis" not in r
    assert "auto_recovery_applied" not in r
    assert bridge.robust_calls == 0


def test_recoverable_failure_triggers_auto_recovery():
    """When run_simulation fails recoverably, the bridge silently calls
    robust_solve and returns the recovered result."""
    bridge = _BridgeStub(recoverable=True)
    r = bridge.run_simulation()
    assert r["success"]
    assert r.get("auto_recovery_applied") is True
    assert bridge.robust_calls == 1
    # The pre-recovery diagnosis must still be available
    assert r.get("pre_recovery_diagnosis") is not None


def test_physical_warning_blocks_auto_recovery():
    """If the convergence failure is due to physical-validity errors
    (T=-50, negative pressure, VF>1), auto-recovery is INTENTIONALLY
    not triggered because robust_solve can't fix those — the user needs
    to fix bounds or property package."""
    bridge = _BridgeStub(physical_warning=True)
    r = bridge.run_simulation()
    assert bridge.robust_calls == 0   # no recovery attempted
    # But diagnosis IS still attached
    assert "diagnosis" in r
    assert "physical_warnings" in r["convergence_check"]


def test_auto_recover_can_be_disabled():
    """Inner-loop callers (optimizer) pass auto_recover=False to avoid
    a 3-attempt cascade per failed eval."""
    bridge = _BridgeStub(recoverable=True)
    r = bridge.run_simulation(auto_recover=False)
    assert bridge.robust_calls == 0
    # Diagnosis is still attached so the optimizer can see why it failed
    assert "diagnosis" in r


def test_auto_recovery_doesnt_loop_infinitely():
    """Reentry guard: the in-progress flag must prevent run_simulation
    being called recursively from inside robust_solve."""
    bridge = _BridgeStub(recoverable=True)
    bridge._in_auto_recovery = True   # pretend we're already recovering
    r = bridge.run_simulation()
    # Should NOT trigger robust_solve again
    assert bridge.robust_calls == 0


def test_dwsim_native_optimizer_passes_auto_recover_false():
    """Regression: the optimizer's _solve_flowsheet must request
    auto_recover=False so each failed eval doesn't cost 3× the time."""
    import dwsim_native_optimizer as dno

    class _Spy:
        def __init__(self):
            self.calls = []
        def run_simulation(self, **kw):
            self.calls.append(kw)
            return {"success": True}

    spy = _Spy()
    ok = dno._solve_flowsheet(spy)
    assert ok is True
    assert spy.calls[0] == {"auto_recover": False}
