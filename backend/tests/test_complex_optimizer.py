"""
Tests for complex_optimizer — the robust multi-stage optimization layer.

Covers:
  • Pre-flight validation (rejects bad specs)
  • Bound-hugging detection
  • Bound-widening mechanics
  • Multi-solver cascade (DE → Simplex)
  • Eval-failure-rate analysis
  • Flowsheet complexity detection
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── Mock bridge for the parabola test ────────────────────────────────────

class _ParabolaBridge:
    """Mimics a flowsheet whose objective is (T-600)² + (F-100)².
    Minimum at (T=600, F=100) → obj=0."""
    def __init__(self, with_recycle=False):
        self.props = {("RC-01", "outlet_temperature_C"): 580.0,
                      ("AIR",   "mass_flow_kgh"):        120.0}
        self.with_recycle = with_recycle
    def get_stream_property(self, tag, prop):
        if (tag, prop) == ("OBJ", "value"):
            T = self.props.get(("RC-01", "outlet_temperature_C"), 0)
            F = self.props.get(("AIR",   "mass_flow_kgh"),        0)
            return {"success": True, "value": (T - 600) ** 2 + (F - 100) ** 2}
        v = self.props.get((tag, prop))
        return {"success": v is not None, "value": v}
    def get_stream_properties(self, tag):
        out = {k[1]: v for k, v in self.props.items() if k[0] == tag}
        return {"success": True, "properties": out}
    def get_unit_op_properties(self, tag):
        return self.get_stream_properties(tag)
    def set_stream_property(self, tag, prop, val, unit=""):
        self.props[(tag, prop)] = float(val); return {"success": True}
    def set_unit_op_property(self, tag, prop, val):
        self.props[(tag, prop)] = float(val); return {"success": True}
    def run_simulation(self):  return {"success": True}
    def save_and_solve(self):  return {"success": True}
    def list_simulation_objects(self):
        unit_ops = [
            {"tag": "RC-01", "type": "ConversionReactor", "category": "unit_op"},
            {"tag": "C-101", "type": "DistillationColumn", "category": "unit_op"},
            {"tag": "HX-1",  "type": "HeatExchanger", "category": "unit_op"},
            {"tag": "C-102", "type": "AbsorptionColumn", "category": "unit_op"},
            {"tag": "RC-02", "type": "ConversionReactor", "category": "unit_op"},
            {"tag": "M-1",   "type": "Mixer", "category": "unit_op"},
        ]
        if self.with_recycle:
            unit_ops.append({"tag": "REC-1", "type": "Recycle",
                             "category": "unit_op"})
        return {
            "success": True,
            "objects": unit_ops + [
                {"tag": "AIR", "type": "MaterialStream", "category": "stream"},
                {"tag": "PROD", "type": "MaterialStream", "category": "stream"},
                {"tag": "OBJ",  "type": "MaterialStream", "category": "stream"},
            ],
        }


# ─── 1. Pre-flight ────────────────────────────────────────────────────────

def test_preflight_accepts_valid_spec():
    from complex_optimizer import preflight_validate
    bridge = _ParabolaBridge()
    pre = preflight_validate(
        bridge,
        variables=[
            {"tag":"RC-01","property":"outlet_temperature_C","unit":"C",
             "lower":550,"upper":650},
            {"tag":"AIR","property":"mass_flow_kgh","unit":"kg/h",
             "lower":50,"upper":150},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
    )
    assert pre["ok"] is True
    assert pre["issues"] == []


def test_preflight_rejects_unreadable_variable():
    from complex_optimizer import preflight_validate
    bridge = _ParabolaBridge()
    pre = preflight_validate(
        bridge,
        variables=[{"tag":"NONEXISTENT","property":"foo","unit":"",
                    "lower":0,"upper":1}],
        objective={"type":"variable","tag":"OBJ","property":"value"},
    )
    assert pre["ok"] is False
    assert any("NONEXISTENT" in i.get("var", "") for i in pre["issues"])


def test_preflight_rejects_inverted_bounds():
    from complex_optimizer import preflight_validate
    bridge = _ParabolaBridge()
    pre = preflight_validate(
        bridge,
        variables=[{"tag":"RC-01","property":"outlet_temperature_C",
                    "lower":700,"upper":600}],  # inverted!
        objective={"type":"variable","tag":"OBJ","property":"value"},
    )
    assert pre["ok"] is False
    assert any("lower" in i.get("problem", "") for i in pre["issues"])


# ─── 2. Bound-hugging ─────────────────────────────────────────────────────

def test_bound_hugging_detects_lower():
    from complex_optimizer import _detect_bound_hugging
    table = [
        {"variable": "T", "lower": 100, "upper": 200, "new_value": 100.5},
        {"variable": "F", "lower": 0,   "upper": 50,  "new_value": 25.0},
    ]
    hits = _detect_bound_hugging(table, tolerance_pct=1.0)
    assert len(hits) == 1
    assert hits[0]["side"] == "lower"
    assert hits[0]["var_index"] == 0


def test_bound_hugging_detects_upper():
    from complex_optimizer import _detect_bound_hugging
    table = [{"variable": "P", "lower": 1, "upper": 10, "new_value": 9.95}]
    hits = _detect_bound_hugging(table, tolerance_pct=1.0)
    assert hits[0]["side"] == "upper"


def test_bound_widening_caps_at_physical_limits():
    from complex_optimizer import _widen_bound
    # Pressure can't go negative
    var = {"tag": "FEED", "property": "pressure_bar", "lower": 0.0,
           "upper": 5.0}
    res = _widen_bound(var, "lower", factor=2.0)
    assert res["var"]["lower"] >= 0.0
    # Temperature_C can't exceed 2500
    var = {"tag": "RC", "property": "outlet_temperature_C", "lower": 100,
           "upper": 2400}
    res = _widen_bound(var, "upper", factor=2.0)
    assert res["var"]["upper"] <= 2500.0


# ─── 3. Multi-solver + bound widening end-to-end ──────────────────────────

def test_complex_optimizer_converges_on_parabola():
    """Full pipeline: preflight → DE → Simplex → bound check → done."""
    from complex_optimizer import run_complex_optimization
    bridge = _ParabolaBridge()
    result = run_complex_optimization(
        bridge,
        variables=[
            {"tag":"RC-01","property":"outlet_temperature_C","unit":"C",
             "lower":550,"upper":650,"initial":580},
            {"tag":"AIR","property":"mass_flow_kgh","unit":"kg/h",
             "lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        minimize=True, max_iter=80,
    )
    assert result["success"], result
    assert result["best_objective"] < 5.0
    assert result["_complex_path"] is True
    assert "_solver_attempts" in result
    assert len(result["_solver_attempts"]) >= 1


def test_complex_optimizer_widens_bounds_when_optimum_at_edge():
    """If the optimum lies outside the initial bounds, the widen pass must
    detect it, widen, re-solve, and find the true optimum."""
    from complex_optimizer import run_complex_optimization

    class _BiasedParabola(_ParabolaBridge):
        """Optimum at T=620 (NOT T=600), F=100."""
        def get_stream_property(self, tag, prop):
            if (tag, prop) == ("OBJ", "value"):
                T = self.props.get(("RC-01", "outlet_temperature_C"), 0)
                F = self.props.get(("AIR",   "mass_flow_kgh"),        0)
                return {"success": True,
                        "value": (T - 620) ** 2 + (F - 100) ** 2}
            return super().get_stream_property(tag, prop)

    bridge = _BiasedParabola()
    bridge.props[("RC-01", "outlet_temperature_C")] = 580.0
    # Bounds INTENTIONALLY exclude the optimum at T=620
    result = run_complex_optimization(
        bridge,
        variables=[
            {"tag":"RC-01","property":"outlet_temperature_C","unit":"C",
             "lower":550,"upper":605,"initial":580},   # upper=605 < opt=620
            {"tag":"AIR","property":"mass_flow_kgh","unit":"kg/h",
             "lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        minimize=True, max_iter=60,
        widen_bounds=True, max_widen_rounds=3,
    )
    # The widening loop should have widened the upper bound on T
    widened = result.get("_bound_widening_log") or []
    assert widened, "Bound-widening did NOT trigger when optimum was outside bounds"
    # And the final new_value should be closer to 620 than 605
    rows = {r["variable"]: r for r in result["variables_table"]}
    assert rows["RC-01.outlet_temperature_C"]["new_value"] > 605.0, \
        "Widening did not actually move the optimum past the original bound"


def test_complex_path_reports_diagnostics():
    """The result must include human-readable diagnostics describing what
    the complex optimizer did."""
    from complex_optimizer import run_complex_optimization
    bridge = _ParabolaBridge()
    out = run_complex_optimization(
        bridge,
        variables=[
            {"tag":"RC-01","property":"outlet_temperature_C","unit":"C",
             "lower":550,"upper":650,"initial":580},
            {"tag":"AIR","property":"mass_flow_kgh","unit":"kg/h",
             "lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        minimize=True, max_iter=50,
    )
    assert "_diagnostics" in out
    assert "Strategies tried" in out["_diagnostics"]
    assert isinstance(out["_eval_failure_rate"], float)


# ─── 4. Complexity detection ──────────────────────────────────────────────

def test_complexity_detector_flags_complex_flowsheet():
    """A flowsheet with multiple reactors + columns + recycle must score
    'complex'."""
    from complex_optimizer import detect_flowsheet_complexity
    bridge = _ParabolaBridge(with_recycle=True)
    c = detect_flowsheet_complexity(bridge)
    assert c["recommended_path"] == "complex"
    assert c["has_recycles"] is True
    assert c["n_complex_ops"] >= 3   # 2 reactors + 1 distillation column min


def test_preflight_skips_write_test_on_plugin_flowsheet():
    """REGRESSION: Cantera plugin flowsheets reject same-value rewrites in
    benign ways. Preflight must NOT block on those — it should skip the
    write-test probe and only enforce bounds + readability."""
    from complex_optimizer import preflight_validate

    class _CanteraBridge:
        class state:
            name = "Biodiesel Combustion (Cantera).dwxmz"
            active_alias = "main"
        def __init__(self):
            self.props = {("FEED", "T"): 300.0, ("FEED", "F"): 100.0,
                           ("PROD", "y"): 0.5}
        def get_stream_property(self, tag, prop):
            v = self.props.get((tag, prop))
            return {"success": v is not None, "value": v}
        def get_stream_properties(self, tag):
            return {"success": True,
                    "properties": {k[1]: v for k, v in self.props.items()
                                    if k[0] == tag}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)
        def set_stream_property(self, tag, prop, val, unit=""):
            # Cantera-style: write fails for derived outputs
            return {"success": False, "error": "Cantera derived output"}
        def set_unit_op_property(self, tag, prop, val):
            return {"success": False, "error": "Cantera derived output"}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "FEED", "type": "MaterialStream", "category": "stream"},
                {"tag": "PROD", "type": "MaterialStream", "category": "stream"},
            ]}

    pre = preflight_validate(
        _CanteraBridge(),
        variables=[
            {"tag": "FEED", "property": "T", "lower": 250, "upper": 350,
             "initial": 300},
        ],
        objective={"type": "variable", "tag": "PROD", "property": "y"},
    )
    # Plugin detected → write-test skipped → preflight passes
    assert pre["ok"] is True, f"Plugin preflight wrongly blocked: {pre}"
    assert pre["plugin_flowsheet"] is True


def test_preflight_still_blocks_unreadable_variable_on_plugin():
    """Plugin-skip must not hide REAL problems. An unreadable variable
    (the bridge can't even GET its value) is still a blocker."""
    from complex_optimizer import preflight_validate

    class _CanteraBridge:
        class state:
            name = "Cantera.dwxmz"; active_alias = "main"
        def get_stream_property(self, tag, prop):
            return {"success": False, "value": None}
        def get_stream_properties(self, tag):
            return {"success": True, "properties": {}}
        def get_unit_op_properties(self, tag):
            return {"success": True, "properties": {}}
        def set_stream_property(self, tag, prop, val, unit=""):
            return {"success": True}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "S1", "type": "MaterialStream", "category": "stream"}
            ]}

    pre = preflight_validate(
        _CanteraBridge(),
        variables=[{"tag": "NONEXISTENT", "property": "foo",
                    "lower": 0, "upper": 1, "initial": 0.5}],
        objective={"type": "variable", "tag": "S1", "property": "T"},
    )
    # Even on plugin flowsheet, an unreadable variable AND unreadable
    # objective are still issues
    assert pre["ok"] is False
    # Both variable and objective issues should appear
    problems = [i.get("problem", "") for i in pre["issues"]]
    assert any("not readable" in p for p in problems)


def test_preflight_recenters_bounds_when_current_value_outside_on_plugin():
    """On a plugin flowsheet, if the current value happens to fall outside
    the auto-suggested bounds (because it's a Cantera-derived output), we
    re-centre bounds around the current value instead of failing."""
    from complex_optimizer import preflight_validate

    class _Bridge:
        class state:
            name = "Cantera.dwxmz"; active_alias = "main"
        def get_stream_property(self, tag, prop):
            return {"success": True, "value": 1500.0}   # WAY outside bounds
        def get_stream_properties(self, tag):
            return {"success": True, "properties": {"T": 1500.0}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)
        def set_stream_property(self, tag, prop, val, unit=""):
            return {"success": True}
        def set_unit_op_property(self, tag, prop, val):
            return {"success": True}
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "S1", "type": "MaterialStream", "category": "stream"}
            ]}

    variables = [{"tag": "S1", "property": "T",
                  "lower": 280, "upper": 320, "initial": 300}]
    pre = preflight_validate(_Bridge(), variables,
                             {"type": "variable", "tag": "S1", "property": "T"})
    assert pre["ok"] is True
    # Bounds should have been re-centred around 1500
    assert variables[0]["lower"] < 1500 < variables[0]["upper"]


def test_complexity_detector_flags_simple_flowsheet():
    from complex_optimizer import detect_flowsheet_complexity
    class _Simple:
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "Heater-1", "type": "Heater", "category": "unit_op"},
                {"tag": "FEED", "type": "MaterialStream", "category": "stream"},
                {"tag": "OUT",  "type": "MaterialStream", "category": "stream"},
            ]}
    c = detect_flowsheet_complexity(_Simple())
    assert c["recommended_path"] == "simple"
    assert c["has_recycles"] is False


# ─── 5. LLM sanity check ─────────────────────────────────────────────────

def test_sanity_check_extracts_confidence_from_llm_reply():
    from complex_optimizer import _llm_sanity_check

    class _FakeLLM:
        # Mirrors the real LLMClient.chat signature (messages, tools,
        # system_prompt) — the old mock used (system, messages), which hid a
        # production crash where complex_optimizer called chat(system=…).
        def chat(self, messages, tools=None, system_prompt="", **kwargs):
            return {"content": '{"confidence": 9, '
                               '"note": "Objective directly represents the goal."}'}

    out = _llm_sanity_check(
        _FakeLLM(),
        user_goal="maximise H2 yield",
        objective={"type": "variable", "tag": "PSA",
                   "property": "mole_fraction_H2"},
        final_obj_value=0.75,
    )
    assert out["confidence"] == 9
    assert "directly" in out["note"]


def test_sanity_check_handles_unparseable_llm_reply():
    from complex_optimizer import _llm_sanity_check
    class _BadLLM:
        def chat(self, system, messages, **kwargs):
            return {"content": "Sorry I can't help with that."}
    out = _llm_sanity_check(
        _BadLLM(),
        user_goal="maximise X",
        objective={"type": "variable", "tag": "A", "property": "B"},
        final_obj_value=1.0,
    )
    assert out["confidence"] is None


# ─── 6. Failure tracking ──────────────────────────────────────────────────

def test_failure_rate_reported_for_unstable_objective():
    """If many evaluations fail (e.g. flowsheet diverges at extreme values),
    the result must surface a high failure rate."""
    from complex_optimizer import run_complex_optimization

    class _FlakyBridge(_ParabolaBridge):
        def __init__(self):
            super().__init__()
            self.n_solves = 0
        def run_simulation(self):
            self.n_solves += 1
            # Fail every 3rd solve to simulate convergence flakiness
            return {"success": self.n_solves % 3 != 0}

    bridge = _FlakyBridge()
    result = run_complex_optimization(
        bridge,
        variables=[
            {"tag":"RC-01","property":"outlet_temperature_C","unit":"C",
             "lower":550,"upper":650,"initial":580},
            {"tag":"AIR","property":"mass_flow_kgh","unit":"kg/h",
             "lower":50,"upper":150,"initial":120},
        ],
        objective={"type":"variable","tag":"OBJ","property":"value"},
        minimize=True, max_iter=50,
    )
    # Either the result fails (high failure rate) or it succeeds with the
    # failure rate clearly above 0
    rate = result.get("_eval_failure_rate", 0)
    assert rate > 0.1, f"Expected non-trivial failure rate, got {rate}"
