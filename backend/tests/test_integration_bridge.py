"""End-to-end bridge integration: load → read → set → re-solve → recheck.

Covers the write-path that a previous thesis review flagged as suspect —
set_stream_property must actually reach the solver, not just mutate a Python
shadow attribute. Each test uses a real .dwxmz sample and a real solve call.
"""
import glob
import os
import shutil

import pytest


def _find_sample(patterns):
    fossee = r"c:\Users\hp\AppData\Local\DWSIM\FOSSEE"
    for pat in patterns:
        hits = glob.glob(os.path.join(fossee, "*", pat))
        if hits:
            return hits[0]
    return None


@pytest.fixture()
def he_flowsheet(tmp_path):
    """Prefer the user's HE.dwxmz; fall back to a FOSSEE heat-exchanger sample."""
    candidates = [
        r"c:\Users\hp\Documents\HE.dwxmz",
    ]
    for c in candidates:
        if os.path.exists(c):
            src = c
            break
    else:
        src = _find_sample(["custom_model.dwxmz", "CoolingTower.dwxmz"])
        if not src:
            pytest.skip("no heat-exchanger sample available")
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    return str(dst)


def _is_material_stream(obj):
    try:
        return "materialstream" in obj.GetType().FullName.lower()
    except Exception:
        return False


def _has_upstream(obj):
    """A stream is an inlet (user-specified) iff no InputConnector is attached."""
    try:
        go = obj.GraphicObject
        for c in (getattr(go, "InputConnectors", None) or []):
            if getattr(c, "IsAttached", False):
                return True
    except Exception:
        pass
    return False


def _first_stream_tag(bridge, inlet_only=False):
    """Return the tag of the first MaterialStream by sorted tag (deterministic).
    If inlet_only, skip streams whose temperature is computed by an upstream
    unit op."""
    for tag in sorted(bridge._active_tag_cache().values()):
        obj = bridge._find_object(tag)
        if obj is None or not _is_material_stream(obj):
            continue
        if inlet_only and _has_upstream(obj):
            continue
        return tag
    return None


def test_load_and_read_temperature(bridge, he_flowsheet):
    """Loading a flowsheet and reading a stream returns a physical T in K."""
    r = bridge.load_flowsheet(he_flowsheet)
    assert r["success"], r
    tag = _first_stream_tag(bridge)
    assert tag, "no stream found in loaded flowsheet"
    props = bridge.get_stream_properties(tag)
    assert props["success"], props
    T = props["properties"].get("temperature_K")
    assert T is not None, f"missing temperature_K in {props['properties']}"
    # Room-temp chemical process streams live between liquid-N2 and flame temps.
    assert 77.0 < T < 3000.0, f"temperature {T}K is non-physical"


def test_set_then_read_roundtrip(bridge, he_flowsheet):
    """Writing T then re-solving must change the stream's T in the solver."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge, inlet_only=True)
    assert tag, "no inlet stream found in flowsheet"

    before_props = bridge.get_stream_properties(tag)["properties"]
    assert "temperature_K" in before_props, \
        f"stream '{tag}' has no temperature_K: {before_props!r}"
    before = before_props["temperature_K"]
    # Target at least 15 K away from current so a no-op write can't masquerade
    # as success.
    target_C = (before - 273.15) + 15.0
    expected_K = target_C + 273.15

    r = bridge.set_stream_property(tag, "Temperature", target_C, "C")
    assert r["success"], r

    solve = bridge.run_simulation()
    assert solve["success"], solve

    after = bridge.get_stream_properties(tag)["properties"]["temperature_K"]
    assert abs(after - expected_K) < 0.5, \
        f"T write didn't propagate: before={before}K after={after}K " \
        f"expected≈{expected_K}K"
    assert abs(after - before) > 10.0, \
        f"T barely moved after +15K set+solve — write path may be dead " \
        f"(before={before}K after={after}K)"


def test_set_rejects_sub_absolute_zero(bridge, he_flowsheet):
    """Physical validation: T below absolute zero must be refused."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag

    r = bridge.set_stream_property(tag, "Temperature", -300.0, "C")
    assert not r["success"]
    assert r.get("code") == "INVALID_VALUE", r


def test_set_rejects_negative_flow(bridge, he_flowsheet):
    """Physical validation: negative mass flow must be refused."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag

    r = bridge.set_stream_property(tag, "MassFlow", -1.0, "kg/s")
    assert not r["success"]
    assert r.get("code") == "INVALID_VALUE", r


def test_set_rejects_vapor_fraction_out_of_range(bridge, he_flowsheet):
    """Physical validation: vapor fraction must stay in [0,1]."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag

    r = bridge.set_stream_property(tag, "VaporFraction", 1.5, "")
    assert not r["success"]
    assert r.get("code") == "INVALID_VALUE", r


def test_set_rejects_nonfinite(bridge, he_flowsheet):
    """Physical validation: NaN and infinity must be refused outright."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag

    for bad in (float("nan"), float("inf"), float("-inf")):
        r = bridge.set_stream_property(tag, "Temperature", bad, "K")
        assert not r["success"], f"NaN/inf {bad!r} was accepted: {r}"
        assert r.get("code") == "INVALID_VALUE", r


def test_composition_rejects_non_sum_to_one(bridge, he_flowsheet):
    """Mole fractions that don't sum to 1.0 (±0.01) must be refused."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag
    r = bridge.set_stream_composition(tag, {"Water": 0.3, "Methanol": 0.3})
    assert not r["success"], r
    assert "sum to" in r["error"].lower()


def test_composition_rejects_negative_and_nan(bridge, he_flowsheet):
    """Negative fractions and NaN/inf must be refused before sum-check."""
    bridge.load_flowsheet(he_flowsheet)
    tag = _first_stream_tag(bridge)
    assert tag
    for bad in (
        {"Water": -0.1, "Methanol": 1.1},
        {"Water": float("nan"), "Methanol": 1.0},
        {"Water": 1.5, "Methanol": -0.5},
        {},
    ):
        r = bridge.set_stream_composition(tag, bad)
        assert not r["success"], f"bad composition accepted: {bad} → {r}"
        assert r.get("code") == "INVALID_VALUE", r
