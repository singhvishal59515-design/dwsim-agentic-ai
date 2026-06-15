"""
Tests for the grounded thermodynamic-model registry (Aspen Methods Assistant
equivalent). The key invariant: the agent must never be handed a package the
engine can't instantiate — every recommendation/resolution maps to a real DWSIM
package key.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_every_registry_entry_is_a_real_dwsim_package():
    import thermo_models as tm
    for m in tm.REGISTRY:
        assert m.dwsim_name in tm.DWSIM_PACKAGES_SET, m.dwsim_name


def test_registry_covers_all_installed_packages():
    # The registry should describe every package DWSIM installs (no blind spots).
    import thermo_models as tm
    described = {m.dwsim_name for m in tm.REGISTRY}
    assert described == set(tm.DWSIM_PACKAGES)


def test_resolve_exact_and_aspen_names():
    import thermo_models as tm
    assert tm.resolve_to_dwsim("Peng-Robinson (PR)")["exact"] is True
    assert tm.resolve_to_dwsim("PENG-ROB")["dwsim_name"] == "Peng-Robinson (PR)"
    assert tm.resolve_to_dwsim("RK-SOAVE")["dwsim_name"] == "Soave-Redlich-Kwong (SRK)"
    assert tm.resolve_to_dwsim("STEAM-TA")["dwsim_name"] == "Steam Tables (IAPWS-IF97)"


def test_resolve_electrolyte_gap_is_honest():
    import thermo_models as tm
    r = tm.resolve_to_dwsim("ELECNRTL")
    assert r["dwsim_name"] == "Ideal Solution (Aqueous Electrolytes)"
    assert "no DWSIM equivalent" in r["note"] or "gap" in r["note"].lower() \
        or "ideal" in r["note"].lower()


def test_recommend_always_returns_instantiable_package():
    import thermo_models as tm
    cases = [
        dict(water_only=True),
        dict(electrolyte=True),
        dict(acid_gas_amine=True),
        dict(natural_gas=True),
        dict(polar=True, pressure_bar=1.0),
        dict(polar=True, pressure_bar=50.0),
        dict(polar=True, have_binary_data=True, pressure_bar=1.0),
        dict(refinery_heavy=True),
        dict(hydrocarbon=True),
        dict(),
    ]
    for c in cases:
        rec = tm.recommend(**c)
        assert tm.is_available(rec["recommended_pp"]), (c, rec["recommended_pp"])
        assert rec["dwsim_available"] is True


def test_recommend_electrolyte_flags_fidelity_gap():
    import thermo_models as tm
    rec = tm.recommend(electrolyte=True)
    assert rec["recommended_pp"] == "Ideal Solution (Aqueous Electrolytes)"
    assert "ELECNRTL" in (rec["aspen_equivalent"] or "")
    assert rec["caveat"]


def test_assistant_actions():
    import thermo_models as tm
    cat = tm.assistant("catalogue")
    assert cat["success"] and cat["n_packages"] == 28
    assert cat["aspen_gaps"]
    rec = tm.assistant("recommend", water_only=True)
    assert rec["recommendation"]["recommended_pp"] == "Steam Tables (IAPWS-IF97)"
    res = tm.assistant("resolve", model="eNRTL")
    assert res["dwsim_name"] in tm.DWSIM_PACKAGES_SET


def test_classify_flags():
    import thermo_models as tm
    assert tm.classify(["Water"])["water_only"] is True
    assert tm.classify(["NaOH", "Water"])["electrolyte"] is True
    assert tm.classify(["Methane"], pressure_bar=60)["natural_gas"] is True
    assert tm.classify(["Methanol", "Water"])["polar"] is True
    assert tm.classify(["Benzene", "Toluene"])["hydrocarbon"] is True


def test_candidate_packages_are_all_real_and_system_specific():
    import thermo_models as tm
    # every candidate, for every system type, must be a real DWSIM package
    for comps, P, T in [(["Water"], 1.0, 25), (["Methane"], 60, -60),
                        (["Methanol", "Water"], 1.0, 25),
                        (["Benzene", "Toluene"], 1.0, 25),
                        (["Acetone", "Water"], 20, 25),
                        (["Oxygen", "Argon"], 1.0, 25)]:
        cand = tm.candidate_packages(comps, P, T)
        assert cand["candidates"], (comps, cand)
        for p in cand["candidates"]:
            assert tm.is_available(p), (comps, p)
    # water → reference steam tables among candidates; natural gas → GERG
    assert "Steam Tables (IAPWS-IF97)" in tm.candidate_packages(["Water"], 1, 25)["candidates"]
    assert "GERG-2008" in tm.candidate_packages(["Methane"], 60, -60)["candidates"]


def test_electrolyte_uncertainty_is_not_comparable():
    # Honest: DWSIM has only one electrolyte model → no model-form spread.
    import thermo_models as tm
    cand = tm.candidate_packages(["NaOH", "Water", "HCl"])
    assert cand["comparable"] is False
    assert len(cand["candidates"]) == 1


def test_thermodynamic_intelligence_unifies_selection_and_uncertainty():
    import thermo_models as tm
    ti = tm.thermodynamic_intelligence(["Methanol", "Water"], pressure_bar=1.0)
    assert tm.is_available(ti["recommended_pp"])
    assert ti["uncertainty_candidates"]
    assert all(tm.is_available(p) for p in ti["uncertainty_candidates"])
    assert "multi_model_uncertainty" in ti["fidelity_statement"]
    # electrolyte → fidelity statement states the gap, no false comparison
    ti_e = tm.thermodynamic_intelligence(["NaOH", "Water"])
    assert "cannot be formed" in ti_e["fidelity_statement"]


def test_assistant_intelligence_action():
    import thermo_models as tm
    r = tm.assistant("intelligence", compounds=["Water"])
    assert r["success"] and r["recommended_pp"] == "Steam Tables (IAPWS-IF97)"


def test_selector_now_returns_dwsim_valid_package():
    # The classic failure: an electrolyte system used to recommend
    # "Electrolyte NRTL (eNRTL)", which DWSIM cannot instantiate.
    import thermo_models as tm
    from process_design_advisor import property_package_selector
    r = property_package_selector(["NaOH", "Water", "HCl"], pressure_bar=1.0)
    assert tm.is_available(r["recommended_pp"]), r["recommended_pp"]
    assert r["ideal_model"]  # the theory-preferred (possibly Aspen-only) name kept
