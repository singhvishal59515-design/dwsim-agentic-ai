"""
Tests for thermo_preflight.py — the deterministic pre-design thermodynamic gate
(Tian et al. "Stage 1: Thermodynamic Analysis"). Pure function of (compounds,
requested package, P, T); no DWSIM, no LLM, so fully covered here.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_cubic_eos_on_polar_azeotrope_is_a_mismatch():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Methanol", "Water"], "Peng-Robinson (PR)", pressure_bar=1.0)
    assert r["mismatch"] is True
    assert r["ok"] is False
    assert r["severity"] == "mismatch"
    assert r["needed_family"] == "activity_coefficient"
    assert r["requested_family"] == "equation_of_state"
    assert r["suggested_pp"]                       # an activity-coefficient model
    assert any("azeotrope" in w.lower() or "equilibrium" in w.lower()
               for w in r["warnings"])


def test_activity_model_on_polar_mixture_is_ok_but_flags_azeotrope():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Methanol", "Water"], "NRTL", pressure_bar=1.0)
    assert r["mismatch"] is False
    assert r["ok"] is True
    assert r["azeotrope_risk"] is True             # still worth a T-x-y check
    assert r["needed_family"] == "activity_coefficient"


def test_hydrocarbons_with_eos_are_appropriate():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Benzene", "Toluene"], "Peng-Robinson (PR)", pressure_bar=1.1)
    assert r["mismatch"] is False
    assert r["azeotrope_risk"] is False
    assert r["severity"] == "ok"
    assert r["needed_family"] == "equation_of_state"


def test_high_pressure_gas_with_eos_is_ok():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Nitrogen"], "Peng-Robinson (PR)", pressure_bar=50.0)
    assert r["mismatch"] is False
    assert r["severity"] == "ok"


def test_pure_water_with_eos_warns_but_is_not_a_mismatch():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Water"], "Peng-Robinson (PR)")
    assert r["mismatch"] is False
    assert r["needed_family"] == "steam"
    assert r["severity"] == "warning"              # steam tables preferred


def test_unrecognised_package_flagged_not_instantiable():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Methane", "Ethane"], "TotallyMadeUpPackage")
    assert r["requested_instantiable"] is False
    assert any("not a DWSIM package" in w for w in r["warnings"])


def test_no_requested_package_still_advises():
    from thermo_preflight import preflight_thermo
    r = preflight_thermo(["Ethanol", "Water"], None, pressure_bar=1.0)
    assert r["recommended_pp"]
    assert r["azeotrope_risk"] is True
    assert r["mismatch"] is False                  # nothing to mismatch against


def test_family_helpers():
    from thermo_preflight import _pp_family, _needed_family
    assert _pp_family("NRTL") == "activity_coefficient"
    assert _pp_family("Peng-Robinson (PR)") == "equation_of_state"
    assert _pp_family("Steam Tables (IAPWS-IF97)") == "steam"
    assert _needed_family({"water_only": True}) == "steam"
    assert _needed_family({"polar": True, "hydrocarbon": False,
                           "pressure_bar": 1.0}) == "activity_coefficient"
    assert _needed_family({"hydrocarbon": True}) == "equation_of_state"


def test_is_deterministic():
    from thermo_preflight import preflight_thermo
    a = preflight_thermo(["Methanol", "Water"], "PR", pressure_bar=1.0)
    b = preflight_thermo(["Methanol", "Water"], "PR", pressure_bar=1.0)
    assert a == b
