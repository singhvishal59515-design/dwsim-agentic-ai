"""
Tests for multi-model (thermodynamic) uncertainty — the "measure the fidelity
gap instead of claiming parity with Aspen" capability.

The aggregator is pure (no DWSIM), so robustness/sensitivity logic is fully
covered here. The bridge method's spec-validation guard is tested without an
engine; the live build-and-compare path is covered by
validate_multimodel_live.py.
"""
from __future__ import annotations
import os
import sys
import types

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# ── pure aggregator ──────────────────────────────────────────────────────────

def test_robust_result_low_spread():
    from multimodel_uncertainty import aggregate_model_spread
    per_model = {
        "PR":  {"Hot": {"temperature_C": 80.00, "pressure_bar": 1.0}},
        "SRK": {"Hot": {"temperature_C": 80.20, "pressure_bar": 1.0}},
        "NRTL": {"Hot": {"temperature_C": 79.90, "pressure_bar": 1.0}},
    }
    r = aggregate_model_spread(per_model, rel_spread_warn_pct=5.0)
    assert r["success"] is True
    assert r["summary"]["robust"] is True
    assert r["summary"]["max_rel_spread_pct"] < 5.0
    assert "ROBUST" in r["summary"]["interpretation"]
    assert "Hot.temperature_C" in r["observations"]
    assert r["observations"]["Hot.temperature_C"]["n"] == 3


def test_model_dependent_result_flagged():
    from multimodel_uncertainty import aggregate_model_spread
    # vapor fraction swings wildly across models → sensitive
    per_model = {
        "PR":   {"V": {"vapor_fraction": 0.10}},
        "SRK":  {"V": {"vapor_fraction": 0.30}},
        "NRTL": {"V": {"vapor_fraction": 0.60}},
    }
    r = aggregate_model_spread(per_model, rel_spread_warn_pct=5.0)
    assert r["summary"]["robust"] is False
    assert r["summary"]["most_sensitive"] == "V.vapor_fraction"
    assert r["summary"]["max_rel_spread_pct"] > 5.0
    assert "SENSITIVE" in r["summary"]["interpretation"]


def test_property_needs_two_models_to_count():
    from multimodel_uncertainty import aggregate_model_spread
    per_model = {
        "PR":  {"S": {"temperature_C": 50.0, "density_kg_m3": 998.0}},
        "SRK": {"S": {"temperature_C": 50.5}},   # no density here
    }
    r = aggregate_model_spread(per_model)
    assert "S.temperature_C" in r["observations"]      # 2 models → counted
    assert "S.density_kg_m3" not in r["observations"]  # only 1 model → dropped


def test_non_numeric_and_bool_ignored():
    from multimodel_uncertainty import aggregate_model_spread
    per_model = {
        "PR":  {"S": {"phase": "liquid", "ok": True, "temperature_C": 25.0}},
        "SRK": {"S": {"phase": "liquid", "ok": True, "temperature_C": 25.0}},
    }
    r = aggregate_model_spread(per_model)
    assert list(r["observations"].keys()) == ["S.temperature_C"]


def test_zero_mean_uses_range_not_percent():
    from multimodel_uncertainty import aggregate_model_spread
    per_model = {
        "PR":  {"S": {"net_duty_kW": -1.0}},
        "SRK": {"S": {"net_duty_kW": 1.0}},   # mean 0
    }
    r = aggregate_model_spread(per_model)
    obs = r["observations"]["S.net_duty_kW"]
    assert obs["rel_spread_pct"] is None     # undefined near zero mean
    assert obs["range"] == 2.0


# ── bridge guard (no DWSIM) ──────────────────────────────────────────────────

def test_bridge_requires_full_spec():
    from dwsim_bridge_v2 import DWSIMBridgeV2
    fake = types.SimpleNamespace()
    r = DWSIMBridgeV2.multi_model_uncertainty(fake, {"objects": []})
    assert r["success"] is False
    assert "spec" in r["error"].lower()
