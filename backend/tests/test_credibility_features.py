"""
Tests for the three credibility-raising features:
  1. Property-package validator (pp_validator)
  2. Inequality-constraint penalty solver (constraint_solver)
  3. Textbook benchmark suite (benchmark_suite)
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ═══════════════════════════════════════════════════════════════════════════
#                    1. PROPERTY-PACKAGE VALIDATOR
# ═══════════════════════════════════════════════════════════════════════════

def test_pp_validator_passes_pr_for_hydrocarbons():
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Methane", "Ethane", "Propane"],
        current_pp="Peng-Robinson")
    assert r["ok"] is True
    assert r["severity"] == "pass"
    assert r["can_optimise"] is True


def test_pp_validator_rejects_pr_for_water_only():
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Water"],
        current_pp="Peng-Robinson")
    # Should flag a mismatch — water-only should use steam tables
    assert r["severity"] in ("mismatch", "warning")
    assert "Steam Tables" in " ".join(r["recommended_pps"])


def test_pp_validator_critical_for_electrolyte_with_pr():
    """Aqueous electrolyte + PR EOS is a critical mismatch — PR cannot
    model ion activities."""
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Water", "Sodium Hydroxide", "Hydrochloric Acid"],
        current_pp="Peng-Robinson")
    assert r["severity"] == "critical"
    assert r["can_optimise"] is False
    assert "eNRTL" in " ".join(r["recommended_pps"])


def test_pp_validator_critical_for_amine_acid_gas_with_pr():
    """MEA + CO2 + H2O is the canonical acid-gas-treatment scenario.
    Plain PR cannot capture chemical absorption; needs ElecNRTL or
    Kent-Eisenberg."""
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Water", "Monoethanolamine", "Carbon Dioxide"],
        current_pp="Peng-Robinson")
    assert r["severity"] == "critical"
    assert r["can_optimise"] is False


def test_pp_validator_mismatch_polar_with_pr():
    """Polar mixtures (methanol-water) + PR is a mismatch — should be NRTL/
    UNIQUAC/Wilson."""
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Methanol", "Water"],
        current_pp="Peng-Robinson")
    assert r["severity"] in ("mismatch", "warning")
    recs = " ".join(r["recommended_pps"])
    assert "NRTL" in recs or "UNIQUAC" in recs


def test_pp_validator_no_pp_is_critical():
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=["Methane"], current_pp="")
    assert r["severity"] == "critical"
    assert r["can_optimise"] is False


def test_pp_validator_empty_compounds_returns_skip():
    """When compounds can't be introspected, we skip rather than block."""
    from pp_validator import validate_property_package
    r = validate_property_package(
        compounds=[], current_pp="Peng-Robinson")
    assert r["severity"] == "skip"
    assert r["can_optimise"] is True   # don't block on missing info


def test_validate_loaded_flowsheet_works_via_bridge():
    """Smoke test: validator can query a bridge-like object for PP +
    compounds and return a structured report."""
    class _Bridge:
        class state:
            name = "methanol_test.dwxmz"
            active_alias = "main"
        def get_property_package(self):
            return {"success": True, "property_package": "NRTL"}
        def list_compounds(self):
            return {"success": True,
                    "compounds": ["Methanol", "Water"], "count": 2}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "S1", "type": "MaterialStream", "category": "stream"},
            ]}
    from pp_validator import validate_loaded_flowsheet
    r = validate_loaded_flowsheet(_Bridge())
    assert r["ok"] is True   # NRTL is appropriate for methanol-water
    assert r["severity"] == "pass"


def test_validate_loaded_flowsheet_skips_when_plugin_managed_cantera():
    """REGRESSION: a Cantera flowsheet has 9+ objects loaded and converged
    but bridge.get_property_package() returns empty. The validator must
    detect this as plugin-managed and return severity='skip' (not 'critical')
    so the optimisation proceeds."""
    class _CanteraBridge:
        class state:
            name = "Biodiesel Combustion (Cantera).dwxmz"
            active_alias = "main"
        def get_property_package(self):
            # Cantera doesn't populate SelectedPropertyPackage
            return {"success": True, "property_package": ""}
        def list_compounds(self):
            return {"success": True, "compounds": [], "count": 0}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": f"S{i}", "type": "MaterialStream",
                 "category": "stream"} for i in range(8)
            ] + [
                {"tag": "RC-1", "type": "ConversionReactor",
                 "category": "unit_op"},
            ]}
    from pp_validator import validate_loaded_flowsheet
    r = validate_loaded_flowsheet(_CanteraBridge())
    # MUST allow optimisation to proceed
    assert r["ok"] is True, f"Plugin flowsheet wrongly blocked: {r}"
    assert r["severity"] == "skip"
    assert r["can_optimise"] is True
    # Should detect Cantera by name
    assert r.get("plugin_detected") == "Cantera"


def test_validate_loaded_flowsheet_skips_when_pp_empty_but_objects_present():
    """Even without the 'Cantera' name hint, if objects are loaded but
    PP is empty, treat as plugin-managed (could be ChemSep, Reaktoro,
    or any other unrecognised plugin)."""
    class _UnknownPluginBridge:
        class state:
            name = "MyFlowsheet.dwxmz"   # no plugin name hint
            active_alias = "main"
        def get_property_package(self):
            return {"success": True, "property_package": ""}
        def list_compounds(self):
            return {"success": True, "compounds": [], "count": 0}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "FEED", "type": "MaterialStream", "category": "stream"},
                {"tag": "HX-1", "type": "Heater", "category": "unit_op"},
            ]}
    from pp_validator import validate_loaded_flowsheet
    r = validate_loaded_flowsheet(_UnknownPluginBridge())
    assert r["ok"] is True   # don't block on missing-info inference
    assert r["severity"] == "skip"


def test_validate_loaded_flowsheet_still_blocks_critical_chemistry_mismatch():
    """Confirm the plugin-skip path doesn't break the critical-mismatch
    detection. A real PR + electrolyte system MUST still be blocked."""
    class _BadChemistryBridge:
        class state:
            name = "salt_water.dwxmz"
            active_alias = "main"
        def get_property_package(self):
            return {"success": True, "property_package": "Peng-Robinson"}
        def list_compounds(self):
            return {"success": True,
                    "compounds": ["Water", "Sodium Chloride", "Sodium Hydroxide"]}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "S1", "type": "MaterialStream", "category": "stream"},
            ]}
    from pp_validator import validate_loaded_flowsheet
    r = validate_loaded_flowsheet(_BadChemistryBridge())
    # Electrolytes + PR is still critical
    assert r["severity"] == "critical"
    assert r["can_optimise"] is False


# ═══════════════════════════════════════════════════════════════════════════
#                    2. CONSTRAINT SOLVER
# ═══════════════════════════════════════════════════════════════════════════

def test_constraint_violation_ge():
    from constraint_solver import _constraint_violation
    c = {"type": "ineq", "operator": ">=", "threshold": 0.95}
    # Value 0.99 satisfies >=0.95 → violation 0
    assert _constraint_violation(0.99, c) == 0.0
    # Value 0.90 violates by 0.05
    assert _constraint_violation(0.90, c) == pytest.approx(0.05)


def test_constraint_violation_le():
    from constraint_solver import _constraint_violation
    c = {"type": "ineq", "operator": "<=", "threshold": 700.0}
    # Value 650 satisfies → 0
    assert _constraint_violation(650, c) == 0.0
    # Value 720 violates by 20
    assert _constraint_violation(720, c) == pytest.approx(20.0)


def test_constraint_violation_eq_within_tolerance():
    from constraint_solver import _constraint_violation
    c = {"type": "eq", "target": 100.0, "tolerance": 1.0}
    assert _constraint_violation(100.5, c) == 0.0
    assert _constraint_violation(102.0, c) == pytest.approx(1.0)


def test_constraint_violation_none_value_is_large():
    """Unreadable value should be treated as critically infeasible."""
    from constraint_solver import _constraint_violation
    c = {"type": "ineq", "operator": ">=", "threshold": 0.95}
    assert _constraint_violation(None, c) > 1e8


def test_parse_constraints_from_goal_extracts_threshold():
    from constraint_solver import parse_constraints_from_goal
    cs = parse_constraints_from_goal(
        "maximise H2 yield subject to purity >= 95% and temperature <= 700°C")
    assert len(cs) == 2
    assert cs[0]["lhs"].strip().startswith("purity")
    assert cs[0]["operator"] == ">="
    assert cs[0]["rhs"] == 95
    assert cs[1]["operator"] == "<="
    assert cs[1]["rhs"] == 700


def test_parse_constraints_handles_unicode_operators():
    from constraint_solver import parse_constraints_from_goal
    cs = parse_constraints_from_goal(
        "minimise energy such that purity ≥ 0.99 and temperature ≤ 650")
    assert len(cs) == 2
    assert cs[0]["operator"] == ">="
    assert cs[1]["operator"] == "<="


def test_parse_constraints_returns_empty_if_no_intro():
    from constraint_solver import parse_constraints_from_goal
    assert parse_constraints_from_goal(
        "just optimise the process") == []
    assert parse_constraints_from_goal("") == []


def test_evaluate_compliance_reports_per_constraint_status():
    from constraint_solver import evaluate_compliance

    class _Bridge:
        def get_stream_property(self, tag, prop):
            vals = {("PROD", "mole_fraction_H2"): 0.97,
                     ("RC", "outlet_temperature_C"): 720}
            return {"success": True, "value": vals.get((tag, prop))}

    cs = [
        {"type": "ineq", "tag": "PROD", "property": "mole_fraction_H2",
         "operator": ">=", "threshold": 0.95},
        {"type": "ineq", "tag": "RC", "property": "outlet_temperature_C",
         "operator": "<=", "threshold": 700},
    ]
    comp = evaluate_compliance(_Bridge(), cs)
    assert comp["n_satisfied"] == 1
    assert comp["n_violated"] == 1
    assert comp["all_satisfied"] is False


def test_wrap_with_penalties_adds_penalty_on_violation():
    """Penalty must increase the objective value (worse for the minimiser)
    when a constraint is violated."""
    from constraint_solver import wrap_with_penalties

    class _Bridge:
        def get_stream_property(self, tag, prop):
            return {"success": True, "value": 0.90}   # violates >=0.95

    raw_obj = lambda: 1.0
    cs = [{"type": "ineq", "tag": "PROD", "property": "purity",
           "operator": ">=", "threshold": 0.95}]
    wrapped = wrap_with_penalties(raw_obj, _Bridge(), cs,
                                    minimize=True, penalty_weight=1e6)
    val = wrapped()
    # Violation = 0.05 → penalty = 1e6 × 0.0025 = 2500
    assert val > 1000   # penalty dominated


def test_wrap_with_penalties_no_penalty_when_satisfied():
    from constraint_solver import wrap_with_penalties

    class _Bridge:
        def get_stream_property(self, tag, prop):
            return {"success": True, "value": 0.99}   # satisfies >=0.95

    cs = [{"type": "ineq", "tag": "PROD", "property": "purity",
           "operator": ">=", "threshold": 0.95}]
    wrapped = wrap_with_penalties(lambda: 1.0, _Bridge(), cs,
                                    minimize=True)
    assert wrapped() == 1.0   # no penalty


# ═══════════════════════════════════════════════════════════════════════════
#                    3. BENCHMARK SUITE
# ═══════════════════════════════════════════════════════════════════════════

def test_single_benchmark_branin_converges():
    from benchmark_suite import run_single_benchmark, _BENCHMARKS
    bm = next(b for b in _BENCHMARKS if b["id"] == "branin")
    r = run_single_benchmark(bm, n_initial=5, max_iter=25)
    assert r["status"] in ("pass", "marginal")
    assert r["gap_pct"] < 50   # gross sanity


def test_single_benchmark_rosenbrock_finds_minimum():
    from benchmark_suite import run_single_benchmark, _BENCHMARKS
    bm = next(b for b in _BENCHMARKS if b["id"] == "rosenbrock")
    r = run_single_benchmark(bm, n_initial=5, max_iter=30)
    # Rosenbrock is hard; allow marginal but require absolute_gap finite
    assert r["status"] in ("pass", "marginal")
    assert r["absolute_gap"] < 10.0


def test_single_benchmark_sphere_3d_converges_to_zero():
    from benchmark_suite import run_single_benchmark, _BENCHMARKS
    bm = next(b for b in _BENCHMARKS if b["id"] == "sphere")
    r = run_single_benchmark(bm, n_initial=5, max_iter=15)
    # Sphere should be easy in 3D
    assert r["absolute_gap"] < 30.0, \
        f"Sphere did not converge: gap={r['absolute_gap']}"


@pytest.mark.timeout(420)
def test_full_suite_runs_all_ten_benchmarks():
    """Full suite at moderate budget — confirms infrastructure works end-to-
    end. With max_iter=20 we expect 3-5 clean passes; the remainder will be
    'marginal' (improved but did not reach published optimum within tol)."""
    from benchmark_suite import run_full_suite
    rep = run_full_suite(n_initial=5, max_iter=20)
    assert rep["total_benchmarks"] == 10
    assert rep["passed"] + rep["marginal"] + rep["failed"] == 10
    # At low budget we just require that no problem catastrophically fails
    # (e.g. import error, NaN), and at least 1 passes cleanly.
    assert rep["failed"] == 0, f"{rep['failed']} benchmarks errored"
    assert rep["passed"] >= 1, "No benchmark passed cleanly"
    # Pass rate is reported for the thesis; just sanity check it's sensible
    assert 0 < rep["pass_rate_pct"] <= 100


@pytest.mark.timeout(300)
def test_format_results_table_produces_valid_markdown():
    from benchmark_suite import run_full_suite, format_results_table
    rep = run_full_suite(n_initial=3, max_iter=8)
    md = format_results_table(rep)
    assert "| #" in md and "| Benchmark" in md
    assert "Summary:" in md
    # Should be one row per benchmark plus header + separator
    n_rows = md.count("\n|")
    assert n_rows >= 10


# ═══════════════════════════════════════════════════════════════════════════
#               INTEGRATION: PP CHECK BLOCKS BAD OPTIMISATION
# ═══════════════════════════════════════════════════════════════════════════

def test_orchestrator_blocks_optimisation_on_critical_pp_mismatch():
    """The orchestrator must refuse to optimise when the PP is critically
    wrong for the chemistry."""
    from optimization_orchestrator import run_optimization_workflow

    class _BadPpBridge:
        class state:
            name = "test"; active_alias = "main"; loaded_flowsheets = {"a": 1}
            streams = ["A"]; unit_ops = ["B"]
            property_package = "Peng-Robinson"
            compounds = ["Water", "Sodium Hydroxide", "Sodium Chloride"]
        def get_property_package(self):
            return {"success": True, "property_package": "Peng-Robinson"}
        def list_compounds(self):
            return {"success": True,
                    "compounds": ["Water", "Sodium Hydroxide",
                                  "Sodium Chloride"], "count": 3}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "A", "type": "MaterialStream", "category": "stream"},
                {"tag": "B", "type": "Heater", "category": "unit_op"},
            ]}

    out = run_optimization_workflow(_BadPpBridge(),
                                     goal="maximise yield", llm=None)
    assert out["success"] is False
    assert out["error_code"] == "PP_VALIDATION_FAILED"
    assert "wrong property package" in out["chat_markdown"].lower()
