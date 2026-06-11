"""
Tests for the post-run reproducibility check: after the engine leaves the
flowsheet at the optimum, the orchestrator re-solves + re-reads the objective
and confirms the reported `best_objective` reproduces. A mismatch must be
flagged (verified=False), not silently trusted. DWSIM is replaced by
monkeypatched solve/read helpers — no simulator needed.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

OBJ = {"type": "variable", "tag": "OBJ", "property": "val"}


def _patch(monkeypatch, reread_value):
    import surrogate_optimizer, dwsim_native_optimizer as dno
    monkeypatch.setattr(dno, "_solve_flowsheet", lambda b: True)
    monkeypatch.setattr(surrogate_optimizer, "_eval_objective",
                        lambda b, o: reread_value)


def test_reproducible_optimum_is_verified(monkeypatch):
    # re-read matches the reported value → verified True
    _patch(monkeypatch, 123.456789)
    from optimization_orchestrator import _verify_optimum_reproducible
    r = _verify_optimum_reproducible(None, OBJ, 123.4568)
    assert r["verified"] is True
    assert r["rel_error"] <= r["tolerance"]


def test_irreproducible_optimum_is_flagged(monkeypatch):
    # re-solving gives a very different value → verified False (NOT trusted)
    _patch(monkeypatch, 50.0)
    from optimization_orchestrator import _verify_optimum_reproducible
    r = _verify_optimum_reproducible(None, OBJ, 100.0)
    assert r["verified"] is False
    assert r["reread"] == 50.0 and r["reported"] == 100.0
    assert r["rel_error"] > r["tolerance"]


def test_unreadable_objective_is_inconclusive_not_pass(monkeypatch):
    # objective unreadable at the optimum → verified None (never a silent pass)
    _patch(monkeypatch, None)
    from optimization_orchestrator import _verify_optimum_reproducible
    r = _verify_optimum_reproducible(None, OBJ, 100.0)
    assert r["verified"] is None
    assert "reason" in r


def test_missing_reported_objective_is_inconclusive():
    from optimization_orchestrator import _verify_optimum_reproducible
    r = _verify_optimum_reproducible(None, OBJ, None)
    assert r["verified"] is None


def test_tolerance_is_env_configurable(monkeypatch):
    # a 0.5% deviation passes at 1% tol, fails at 0.1% tol
    _patch(monkeypatch, 100.5)
    from optimization_orchestrator import _verify_optimum_reproducible
    monkeypatch.setenv("OPT_VERIFY_REL_TOL", "1e-2")
    assert _verify_optimum_reproducible(None, OBJ, 100.0)["verified"] is True
    monkeypatch.setenv("OPT_VERIFY_REL_TOL", "1e-3")
    assert _verify_optimum_reproducible(None, OBJ, 100.0)["verified"] is False


def test_optimum_variables_are_reapplied_before_resolve(monkeypatch):
    # The check must be self-contained: it re-writes the reported optimum
    # variables before re-solving, not merely trust the engine's leftover state.
    import surrogate_optimizer, dwsim_native_optimizer as dno
    writes = []
    monkeypatch.setattr(dno, "_write_object_property",
                        lambda b, t, p, v, u="": writes.append((t, p, v)))
    monkeypatch.setattr(dno, "_solve_flowsheet", lambda b: True)
    monkeypatch.setattr(surrogate_optimizer, "_eval_objective", lambda b, o: 100.0)
    from optimization_orchestrator import _verify_optimum_reproducible
    rows = [{"tag": "Heater", "property": "OutletTemperature",
             "new_value": 350.0, "unit": "K"}]
    r = _verify_optimum_reproducible(None, OBJ, 100.0, rows)
    assert r["verified"] is True
    assert writes == [("Heater", "OutletTemperature", 350.0)]


def test_verify_enabled_default_and_flag(monkeypatch):
    from optimization_orchestrator import _verify_enabled
    monkeypatch.delenv("OPT_VERIFY_OPTIMUM", raising=False)
    assert _verify_enabled() is True                 # on by default
    for off in ("0", "false", "no", "off", "OFF", "False"):
        monkeypatch.setenv("OPT_VERIFY_OPTIMUM", off)
        assert _verify_enabled() is False, off
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("OPT_VERIFY_OPTIMUM", on)
        assert _verify_enabled() is True, on


def test_banner_empty_when_skipped():
    from optimization_orchestrator import _verification_banner
    assert _verification_banner({"verified": None, "skipped": True,
                                 "reason": "disabled"}) == ""


def test_banner_empty_when_verified():
    from optimization_orchestrator import _verification_banner
    assert _verification_banner({"verified": True}) == ""


def test_banner_warns_when_not_reproduced():
    from optimization_orchestrator import _verification_banner
    b = _verification_banner({"verified": False, "reread": 50.0,
                              "reported": 100.0, "rel_error": 0.5,
                              "tolerance": 1e-3})
    assert "not reproducible" in b.lower()
