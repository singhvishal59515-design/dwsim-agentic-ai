"""
Tests that the FULL state-changing tool surface now verifies its effect by
read-back — not just the original four tools. A write that DWSIM silently
ignores must be downgraded to WRITE_NOT_VERIFIED; a write that genuinely
cannot be read back must be flagged 'unverifiable', never a bare success.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _Bridge:
    """Mock bridge whose state can be made to honour or silently ignore writes."""
    def __init__(self, honour=True):
        self.honour = honour
        self.col = {"reflux_ratio": 2.0}
        self.rea = {"temperature_K": 600.0}
        self.energy = {"duty_W": 0.0}
        self.objects = [{"tag": "C-101"}, {"tag": "R-101"}, {"tag": "E-101"},
                        {"tag": "DOOMED"}]

    # columns
    def set_column_property(self, tag, prop, value):
        if self.honour:
            self.col[prop] = value
        return {"success": True}
    def get_column_properties(self, tag):
        return {"success": True, "properties": dict(self.col)}
    def set_column_specs(self, tag, **kw):
        if self.honour:
            self.col.update(kw)
        return {"success": True}

    # reactors
    def set_reactor_property(self, tag, prop, value):
        if self.honour:
            self.rea[prop] = value
        return {"success": True}
    def get_reactor_properties(self, tag):
        return {"success": True, "properties": dict(self.rea)}

    # energy streams
    def set_energy_stream(self, tag, duty_W):
        if self.honour:
            self.energy["duty_W"] = duty_W
        return {"success": True}
    def get_energy_stream(self, tag):
        return {"success": True, "duty_W": self.energy["duty_W"]}

    # delete
    def delete_object(self, tag):
        if self.honour:
            self.objects = [o for o in self.objects if o["tag"] != tag]
        return {"success": True}
    def list_simulation_objects(self):
        return {"success": True, "objects": list(self.objects)}


def test_column_property_verified_when_honoured():
    from write_verification import verified_set_column_property
    r = verified_set_column_property(_Bridge(honour=True), "C-101",
                                     "reflux_ratio", 3.5)
    assert r["success"] is True
    assert r["verification"]["status"] == "verified"


def test_column_property_silent_failure_caught():
    from write_verification import verified_set_column_property
    r = verified_set_column_property(_Bridge(honour=False), "C-101",
                                     "reflux_ratio", 3.5)
    assert r["success"] is False
    assert r["error_code"] == "WRITE_NOT_VERIFIED"


def test_reactor_property_silent_failure_caught():
    from write_verification import verified_set_reactor_property
    r = verified_set_reactor_property(_Bridge(honour=False), "R-101",
                                      "temperature_K", 750.0)
    assert r["success"] is False
    assert r["error_code"] == "WRITE_NOT_VERIFIED"


def test_energy_stream_verified_and_caught():
    from write_verification import verified_set_energy_stream
    assert verified_set_energy_stream(_Bridge(True), "E-101",
                                      125000.0)["success"] is True
    bad = verified_set_energy_stream(_Bridge(False), "E-101", 125000.0)
    assert bad["success"] is False and bad["error_code"] == "WRITE_NOT_VERIFIED"


def test_column_specs_per_spec_verification():
    from write_verification import verified_set_column_specs
    ok = verified_set_column_specs(_Bridge(True), "C-101", reflux_ratio=4.0)
    assert ok["success"] is True
    assert ok["verification"]["per_spec"]["reflux_ratio"]["status"] == "verified"
    bad = verified_set_column_specs(_Bridge(False), "C-101", reflux_ratio=4.0)
    assert bad["success"] is False


def test_delete_object_verified_gone_and_silent_failure():
    from write_verification import verified_delete_object
    ok = verified_delete_object(_Bridge(True), "DOOMED")
    assert ok["success"] is True
    assert ok["verification"]["status"] == "verified"
    bad = verified_delete_object(_Bridge(False), "DOOMED")
    assert bad["success"] is False
    assert bad["error_code"] == "WRITE_NOT_VERIFIED"


def test_unverifiable_write_is_flagged_not_silently_succeeded():
    """A write with no read-back path must be 'unverifiable', never bare True."""
    from write_verification import verified_generic
    b = _Bridge(True)
    r = verified_generic(b, lambda: {"success": True},
                         describe="set_stream_flash_spec(S1, TP)")
    assert r["success"] is True
    assert r["verification"]["status"] == "unverifiable"
    assert "unconfirmed" in r["verification"]["note"].lower()


def test_unverifiable_passes_through_set_failure():
    from write_verification import verified_generic
    r = verified_generic(_Bridge(True),
                         lambda: {"success": False, "error": "boom"},
                         describe="x")
    assert r["success"] is False
