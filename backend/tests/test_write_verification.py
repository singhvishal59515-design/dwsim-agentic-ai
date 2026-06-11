"""
Tests for the write-verification layer — proves silent failures are caught.
No DWSIM needed; uses a mock bridge.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _Bridge:
    """Mock bridge whose stored value can diverge from what was 'set' —
    simulating a silent failure."""
    def __init__(self, accept_writes=True):
        self.store = {("Feed", "temperature"): 283.15}   # 10 C in K
        self.accept = accept_writes
    def set_stream_property(self, tag, prop, value, unit=""):
        if self.accept:
            # store in K
            k = value + 273.15 if unit.lower() in ("c", "celsius") else value
            self.store[(tag, prop)] = k
        return {"success": True}   # always claims success (the danger)
    def get_stream_properties(self, tag):
        props = {}
        for (t, p), v in self.store.items():
            if t == tag:
                props[p] = v
                props[p + "_K"] = v
                props[p + "_C"] = v - 273.15
        return {"success": True, "properties": props}


def test_legitimate_write_verifies():
    from write_verification import verified_set_stream_property
    b = _Bridge(accept_writes=True)
    r = verified_set_stream_property(b, "Feed", "temperature", 45.0, "C")
    assert r["success"] is True
    assert r["verification"]["verified"] is True
    assert r["verification"]["status"] == "verified"


def test_silent_failure_is_caught():
    """Bridge claims success but does NOT change the value → must be caught."""
    from write_verification import verified_set_stream_property
    b = _Bridge(accept_writes=False)   # ignores writes, still says success
    r = verified_set_stream_property(b, "Feed", "temperature", 45.0, "C")
    # The bridge said success, but value is still 10 C → verification fails
    assert r["success"] is False, "Silent failure was NOT caught!"
    assert r["error_code"] == "WRITE_NOT_VERIFIED"
    assert r["verification"]["status"] == "mismatch"


def test_verify_property_write_unit_conversion():
    """45 C must verify against a 318.15 K read-back."""
    from write_verification import verify_property_write
    b = _Bridge(accept_writes=True)
    b.set_stream_property("Feed", "temperature", 45.0, "C")
    v = verify_property_write(b, "Feed", "temperature", 45.0, "C")
    assert v["verified"] is True
    assert abs(v["read_back"] - 318.15) < 0.5


def test_unverifiable_property_not_hard_fail():
    """A property that can't be read back → 'unverifiable', not a failure."""
    from write_verification import verify_property_write
    class _Empty:
        def get_stream_properties(self, tag):
            return {"success": True, "properties": {}}
    v = verify_property_write(_Empty(), "X", "some_derived_output", 5.0, "")
    assert v["status"] == "unverifiable"
    assert v["verified"] is None   # unknown, not False
