"""
Tests for the objective-sensitivity pre-check: it must detect when the
objective does NOT respond to the decision variables (so the optimisation would
be hollow), and pass when it does. Flowsheet eval is replaced by a monkeypatched
analytic objective — no DWSIM needed.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

VARS = [{"tag": "V1", "property": "x", "lower": 0.0, "upper": 10.0},
        {"tag": "V2", "property": "x", "lower": 0.0, "upper": 10.0}]
OBJ = {"type": "variable", "tag": "OBJ", "property": "val"}


def _patch(monkeypatch, obj_fn):
    import dwsim_native_optimizer as dno
    # Seed each variable's current value (a real flowsheet always has one), so
    # baseline and per-variable restore are consistent.
    state = {("V1", "x"): 5.0, ("V2", "x"): 5.0}
    monkeypatch.setattr(dno, "_write_object_property",
                        lambda b, t, p, v, u="": state.__setitem__((t, p), float(v)))
    monkeypatch.setattr(dno, "_solve_flowsheet", lambda b: True)
    def _read(b, t, p):
        if (t, p) == ("OBJ", "val"):
            return obj_fn(state)
        return state.get((t, p))
    monkeypatch.setattr(dno, "_read_object_property", _read)


def test_responsive_objective_is_sensitive(monkeypatch):
    # objective depends on both variables → must be flagged sensitive
    _patch(monkeypatch, lambda st: (st.get(("V1", "x"), 0) - 3) ** 2
           + st.get(("V2", "x"), 0))
    from optimization_orchestrator import _check_objective_sensitivity
    r = _check_objective_sensitivity(None, VARS, OBJ)
    assert r["checked"] is True
    assert r["sensitive"] is True
    assert len(r["responding"]) >= 1


def test_constant_objective_is_insensitive(monkeypatch):
    # objective never changes (like Products.mass_flow fixed by a spec)
    _patch(monkeypatch, lambda st: 486359.9584)
    from optimization_orchestrator import _check_objective_sensitivity
    r = _check_objective_sensitivity(None, VARS, OBJ)
    assert r["checked"] is True
    assert r["sensitive"] is False
    assert r["responding"] == []


def test_partial_response_still_sensitive(monkeypatch):
    # objective responds to V1 only → still sensitive (optimisation worthwhile)
    _patch(monkeypatch, lambda st: 5.0 * st.get(("V1", "x"), 0))
    from optimization_orchestrator import _check_objective_sensitivity
    r = _check_objective_sensitivity(None, VARS, OBJ)
    assert r["sensitive"] is True
    assert any("V1" in x for x in r["responding"])
    assert not any("V2" in x for x in r["responding"])
