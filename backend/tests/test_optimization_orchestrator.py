"""
Tests for optimization_orchestrator — the NL-driven end-to-end workflow that
matches the poster (user goal → LLM picks vars → DWSIM optimizes → poster
result).

No DWSIM install needed — uses a mock bridge.
"""

from __future__ import annotations
import os
import sys
import json

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── Mock bridge ────────────────────────────────────────────────────────

class _MockBridge:
    """Mimics a syngas-loop flowsheet matching the poster: RC-01/02/03
    reactors, AIR feed, PSA product."""

    def __init__(self):
        self.props = {
            ("RC-01", "outlet_temperature_C"): 600.0,
            ("RC-02", "outlet_temperature_C"): 850.0,
            ("RC-03", "outlet_temperature_C"): 900.0,
            ("AIR",   "mass_flow_kgh"):        500.0,
            ("PSA",   "mole_fraction_H2"):     0.70,
            ("PSA",   "mole_fraction_CO"):     0.15,
            ("PSA",   "mole_fraction_CO2"):    0.05,
            ("TOTAL", "duty_kW"):              300.0,
        }
        self.solve_calls = 0

    def get_stream_property(self, tag, prop):
        # Synthetic "physics": purity peaks at RC-01=620, RC-02=870, RC-03=880,
        # AIR=470 — matches the poster's optimum. Energy drops as T-deviation
        # from the optimum decreases.
        rc1 = self.props.get(("RC-01", "outlet_temperature_C"), 600.0)
        rc2 = self.props.get(("RC-02", "outlet_temperature_C"), 850.0)
        rc3 = self.props.get(("RC-03", "outlet_temperature_C"), 900.0)
        air = self.props.get(("AIR",   "mass_flow_kgh"),        500.0)

        if (tag, prop) == ("PSA", "mole_fraction_H2"):
            # peak 0.75 at the optimum, drops quadratically
            d2 = ((rc1-620)/20)**2 + ((rc2-870)/30)**2 + ((rc3-880)/30)**2 + ((air-470)/50)**2
            return {"success": True, "value": max(0.0, 0.75 - 0.04 * d2)}
        if (tag, prop) == ("PSA", "mole_fraction_CO"):
            d2 = ((rc1-620)/20)**2 + ((rc2-870)/30)**2 + ((rc3-880)/30)**2 + ((air-470)/50)**2
            return {"success": True, "value": max(0.0, 0.20 - 0.02 * d2)}
        if (tag, prop) == ("TOTAL", "duty_kW"):
            d2 = ((rc1-620)/20)**2 + ((rc2-870)/30)**2 + ((rc3-880)/30)**2 + ((air-470)/50)**2
            return {"success": True, "value": 263.0 + 20 * d2}
        v = self.props.get((tag, prop))
        return {"success": v is not None, "value": v}

    def get_stream_properties(self, tag):
        out = {k[1]: v for k, v in self.props.items() if k[0] == tag}
        return {"success": True, "properties": out}

    def get_unit_op_properties(self, tag):
        return self.get_stream_properties(tag)

    def set_stream_property(self, tag, prop, value, unit=""):
        self.props[(tag, prop)] = float(value); return {"success": True}

    def set_unit_op_property(self, tag, prop, value):
        self.props[(tag, prop)] = float(value); return {"success": True}

    def run_simulation(self):
        self.solve_calls += 1; return {"success": True}

    def save_and_solve(self):
        return self.run_simulation()

    def list_simulation_objects(self):
        """Mimics the REAL bridge return shape (flat objects[] with type/
        category fields, like DWSIM's GetType().Name produces)."""
        return {
            "success": True,
            "count":   6,
            "objects": [
                {"tag": "RC-01", "type": "ConversionReactor", "category": "unit_op"},
                {"tag": "RC-02", "type": "ConversionReactor", "category": "unit_op"},
                {"tag": "RC-03", "type": "ConversionReactor", "category": "unit_op"},
                {"tag": "AIR",   "type": "MaterialStream",    "category": "stream"},
                {"tag": "PSA",   "type": "MaterialStream",    "category": "stream"},
                {"tag": "TOTAL", "type": "MaterialStream",    "category": "stream"},
            ],
        }

    def list_objects(self):
        """Legacy split-shape method — many call sites expect this. Reuse
        the bridge's normalisation logic by hand for the test."""
        raw = self.list_simulation_objects()
        streams, unit_ops = [], []
        for o in raw["objects"]:
            cat = o.get("category", "").lower()
            tn  = o.get("type", "").lower()
            if "stream" in cat or "materialstream" in tn:
                streams.append(o)
            else:
                unit_ops.append(o)
        return {"success": True, "streams": streams, "unit_ops": unit_ops,
                "objects": raw["objects"]}


# ─── Mock LLM that emits the expected spec ─────────────────────────────

class _MockLLM:
    """Returns a hard-coded optimization spec — simulates what a real LLM
    would output for the poster's goal."""
    def __init__(self, response_obj):
        self.response_obj = response_obj
        self.last_user = None
    def chat(self, messages=None, tools=None, system_prompt="", **kwargs):
        # Matches the real LLMClient.chat(messages, tools, system_prompt)
        self.last_user = messages[-1]["content"] if messages else ""
        return {"content": json.dumps(self.response_obj)}


# ─── Tests ──────────────────────────────────────────────────────────────

def test_suggest_decision_variables_finds_reactors():
    from optimization_orchestrator import suggest_decision_variables
    bridge = _MockBridge()
    sugg = suggest_decision_variables(bridge, max_n=10)
    assert sugg, "Expected at least some suggestions"
    # Reactor temps should come first
    rc_vars = [s for s in sugg if s["tag"].startswith("RC-")]
    assert len(rc_vars) >= 3, f"Expected 3 reactors, got {len(rc_vars)}"
    # Each should have sensible bounds (±5% of current)
    for s in rc_vars:
        assert s["lower"] < s["initial"] < s["upper"]
        assert s["role"] == "reactor_T"


def test_suggest_decision_variables_finds_stream_flows():
    from optimization_orchestrator import suggest_decision_variables
    bridge = _MockBridge()
    sugg = suggest_decision_variables(bridge, max_n=10)
    flow_vars = [s for s in sugg if s["property"] == "mass_flow_kgh"]
    assert flow_vars, "Expected at least one flow variable"


def test_build_spec_from_goal_with_llm():
    from optimization_orchestrator import build_spec_from_goal
    bridge = _MockBridge()
    llm = _MockLLM({
        "minimize": False, "method": "simplex",
        "objective": {
            "type": "expression",
            "expression": "H2 + CO - 0.001 * energy",
            "named_values": [
                {"name": "H2", "tag": "PSA", "property": "mole_fraction_H2"},
                {"name": "CO", "tag": "PSA", "property": "mole_fraction_CO"},
                {"name": "energy", "tag": "TOTAL", "property": "duty_kW"},
            ],
        },
        "variables_to_use": [
            "RC-01.outlet_temperature_C",
            "RC-02.outlet_temperature_C",
            "RC-03.outlet_temperature_C",
            "AIR.mass_flow_kgh",
        ],
    })
    spec = build_spec_from_goal(
        llm, bridge,
        "Maximize (H2+CO) purity while minimising total energy")
    assert spec["success"]
    assert spec["minimize"] is False
    assert spec["objective"]["type"] == "expression"
    assert len(spec["variables"]) >= 3


def test_build_spec_falls_back_to_heuristic_when_llm_unavailable():
    from optimization_orchestrator import build_spec_from_goal
    bridge = _MockBridge()
    spec = build_spec_from_goal(
        None, bridge,
        "Maximize H2 mole fraction at PSA")
    assert spec["success"]
    # Heuristic should detect "Maximize" → minimize=False
    assert spec["minimize"] is False


def test_build_spec_rejects_no_variables():
    """If the flowsheet has nothing to optimize, return a structured error."""
    from optimization_orchestrator import build_spec_from_goal
    class EmptyBridge:
        def list_objects(self): return {"unit_ops": [], "streams": []}
    spec = build_spec_from_goal(None, EmptyBridge(), "optimize anything")
    assert spec["success"] is False
    assert spec["error_code"] == "NO_VARIABLES_FOUND"


def test_run_optimization_workflow_end_to_end_with_llm():
    """The poster's full flow: NL goal → spec → DWSIM solve → poster result."""
    from optimization_orchestrator import run_optimization_workflow
    bridge = _MockBridge()
    llm = _MockLLM({
        "minimize": False, "method": "simplex",
        "objective": {
            "type": "expression",
            "expression": "H2 + CO - 0.001 * energy",
            "named_values": [
                {"name": "H2", "tag": "PSA", "property": "mole_fraction_H2"},
                {"name": "CO", "tag": "PSA", "property": "mole_fraction_CO"},
                {"name": "energy", "tag": "TOTAL", "property": "duty_kW"},
            ],
        },
        "variables_to_use": [
            "RC-01.outlet_temperature_C",
            "RC-02.outlet_temperature_C",
            "RC-03.outlet_temperature_C",
            "AIR.mass_flow_kgh",
        ],
    })

    step_log = []
    def _on_step(stage, detail):
        step_log.append((stage, detail))

    out = run_optimization_workflow(
        bridge, goal="Maximize (H2+CO) purity while minimising total energy",
        llm=llm, on_step=_on_step, max_iter=80, tolerance=1e-4,
    )
    assert out["success"], out
    assert out["result"]["best_objective"] is not None
    # The composite objective is H2 + CO - 0.001*energy.
    # At the optimum: 0.75 + 0.20 - 0.001*263 = 0.687.
    # Starting point: ~0.51. Anything > 0.6 means real convergence.
    assert out["result"]["best_objective"] > 0.6, \
        f"Optimization did not improve: best={out['result']['best_objective']}"
    # Step callbacks were invoked
    assert any("step 1" in s[0] for s in step_log), step_log
    assert any("step 4" in s[0] for s in step_log)
    # Markdown contains all the poster sections
    md = out["chat_markdown"]
    assert "OBJECTIVE ACHIEVED" in md
    assert "KEY MODIFIED VARIABLES" in md
    assert "Old Value" in md
    assert "New Value" in md
    assert "Summary" in md


def test_run_optimization_workflow_reaches_poster_optimum():
    """With our synthetic poster physics, the solver should drive the
    variables CLOSE to the poster's reported new values:
        RC-01 600→620, RC-02 850→870, RC-03 900→880, AIR 500→470."""
    from optimization_orchestrator import run_optimization_workflow
    bridge = _MockBridge()
    llm = _MockLLM({
        "minimize": False, "method": "simplex",
        "objective": {
            "type": "expression",
            "expression": "H2 + CO - 0.001 * energy",
            "named_values": [
                {"name": "H2", "tag": "PSA", "property": "mole_fraction_H2"},
                {"name": "CO", "tag": "PSA", "property": "mole_fraction_CO"},
                {"name": "energy", "tag": "TOTAL", "property": "duty_kW"},
            ],
        },
        "variables_to_use": [
            "RC-01.outlet_temperature_C",
            "RC-02.outlet_temperature_C",
            "RC-03.outlet_temperature_C",
            "AIR.mass_flow_kgh",
        ],
    })
    out = run_optimization_workflow(
        bridge, goal="Maximize H2+CO purity, minimise energy",
        llm=llm, max_iter=300, tolerance=1e-5,
    )
    assert out["success"]
    rows = {r["variable"]: r for r in out["result"]["variables_table"]}
    # Each tolerance ±5% of bound width because of mock-physics noise
    assert abs(rows["RC-01.outlet_temperature_C"]["new_value"] - 620) < 6
    assert abs(rows["RC-02.outlet_temperature_C"]["new_value"] - 870) < 30
    # Confirm we used DotNumerics if available
    backend = out["result"].get("solver_backend", "")
    assert "DotNumerics" in backend or "SciPy" in backend


def test_format_poster_chat_handles_minimal_result():
    """Result formatter must not crash on partial input."""
    from optimization_orchestrator import format_poster_chat
    md = format_poster_chat({
        "success": True,
        "best_objective": 0.95,
        "method": "simplex",
        "n_evaluations": 50,
        "duration_s": 12.3,
        "minimize": False,
        "variables_table": [
            {"variable": "RC-01.T", "tag": "RC-01", "property": "T",
             "old_value": 600, "new_value": 620, "change": 20,
             "change_pct": 3.33, "at_lower": False, "at_upper": False},
        ],
    }, goal="Maximize purity")
    assert "OBJECTIVE ACHIEVED" in md
    assert "RC-01.T" in md
    assert "620" in md


# ─── API integration test ──────────────────────────────────────────────

def test_api_workflow_endpoint(monkeypatch):
    """POST /optimize/workflow — full FastAPI stack with mock bridge + LLM."""
    from fastapi.testclient import TestClient
    import api as api_module
    from optimization_orchestrator import run_optimization_workflow

    bridge = _MockBridge()
    # Patch the workflow method on the bridge to inject our mock LLM
    bridge.optimize_flowsheet_with_llm = (
        lambda goal, llm=None, max_iter=50, tolerance=1e-3, on_step=None:
        run_optimization_workflow(
            bridge, goal=goal,
            llm=_MockLLM({
                "minimize": False, "method": "simplex",
                "objective": {
                    "type": "expression",
                    "expression": "H2 + CO",
                    "named_values": [
                        {"name": "H2", "tag": "PSA", "property": "mole_fraction_H2"},
                        {"name": "CO", "tag": "PSA", "property": "mole_fraction_CO"},
                    ],
                },
                "variables_to_use": [
                    "RC-01.outlet_temperature_C",
                    "RC-02.outlet_temperature_C",
                ],
            }),
            on_step=on_step, max_iter=int(max_iter),
            tolerance=float(tolerance),
        )
    )
    monkeypatch.setattr(api_module, "_get_bridge", lambda: bridge)

    client = TestClient(api_module.app)
    body = client.post("/optimize/workflow", json={
        "goal": "Maximize H2+CO purity",
        "max_iter": 80,
    }).json()
    assert body["success"] is True, body
    assert "chat_markdown" in body
    assert "OBJECTIVE ACHIEVED" in body["chat_markdown"]


def test_suggest_variables_uses_real_bridge_method():
    """The bug we just fixed: orchestrator was calling bridge.list_objects()
    but the real bridge method is list_simulation_objects() returning a flat
    objects[] list. With ONLY list_simulation_objects() exposed, the
    suggester must still discover decision variables."""
    from optimization_orchestrator import suggest_decision_variables

    class _OnlyListSimObjects:
        """Bridge mock that ONLY exposes list_simulation_objects() (no
        list_objects alias), matching the real bridge prior to the fix."""
        def __init__(self):
            self.props = {
                ("RC-01", "outlet_temperature_C"): 600.0,
                ("AIR",   "mass_flow_kgh"):        500.0,
            }
        def list_simulation_objects(self):
            return {
                "success": True,
                "objects": [
                    {"tag": "RC-01", "type": "ConversionReactor",
                     "category": "unit_op"},
                    {"tag": "AIR",   "type": "MaterialStream",
                     "category": "stream"},
                ],
            }
        def get_stream_property(self, tag, prop):
            v = self.props.get((tag, prop))
            return {"success": v is not None, "value": v}
        def get_stream_properties(self, tag):
            return {"success": True,
                    "properties": {k[1]: v for k, v in self.props.items()
                                    if k[0] == tag}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)

    sugg = suggest_decision_variables(_OnlyListSimObjects(), max_n=5)
    # Must find at least the reactor temperature + air flow
    assert len(sugg) >= 1, (
        "Suggester returned empty when only list_simulation_objects exists. "
        "This is the bug from the screenshot — flowsheet loaded but no vars found.")
    tags = {(s["tag"], s["property"]) for s in sugg}
    assert ("RC-01", "outlet_temperature_C") in tags or \
           ("AIR",   "mass_flow_kgh")        in tags, \
        f"Expected reactor T or air flow in suggestions: {sugg}"


def test_unit_op_type_matcher_handles_case_and_variants():
    """The matcher must accept full namespace types like
    'DWSIM.UnitOperations.ConversionReactor' or 'Heater_v2'."""
    from optimization_orchestrator import _match_unit_op_hints
    assert _match_unit_op_hints("Heater")
    assert _match_unit_op_hints("DWSIM.UnitOperations.Heater")
    assert _match_unit_op_hints("Heater_v2")
    assert _match_unit_op_hints("CONVERSIONREACTOR")
    assert _match_unit_op_hints("ConversionReactor")
    assert _match_unit_op_hints("Heat_Exchanger")
    assert _match_unit_op_hints("HX-1")
    assert not _match_unit_op_hints("")
    assert not _match_unit_op_hints("UnknownGadget")


def test_bare_optimise_goal_asks_for_clarification():
    """A bare 'do optimisation' goal has NO objective — the workflow must now
    ASK the user what to optimise rather than guess a (possibly meaningless)
    objective. (Previously it guessed a default, which could produce a hollow
    result; the ambiguity gate prevents that.)"""
    from optimization_orchestrator import run_optimization_workflow
    bridge = _MockBridge()
    out = run_optimization_workflow(
        bridge, goal="do optimisation", llm=None, max_iter=40)
    assert out["success"] is False
    assert out["error_code"] == "NEEDS_CLARIFICATION"
    assert out.get("needs_clarification") is True
    assert "optimise" in out["chat_markdown"].lower()


def test_specific_goal_still_runs_with_heuristic_fallback():
    """A SPECIFIC goal with no LLM passes the ambiguity gate and still picks the
    objective via the heuristic fallback and runs."""
    from optimization_orchestrator import run_optimization_workflow
    bridge = _MockBridge()
    out = run_optimization_workflow(
        bridge, goal="maximise H2 purity at PSA", llm=None, max_iter=40)
    assert out["success"], out
    assert out["spec"]["objective"].get("type") == "variable"
    assert "OBJECTIVE ACHIEVED" in out["chat_markdown"]


def test_workflow_streams_per_evaluation_progress():
    """The orchestrator must invoke on_eval for each solver evaluation so
    the chat can stream iteration data live."""
    from optimization_orchestrator import run_optimization_workflow
    bridge = _MockBridge()

    eval_log = []
    def _on_eval(it, params, obj, best):
        eval_log.append((it, obj, best))

    out = run_optimization_workflow(
        bridge,
        goal="Maximize H2+CO purity",
        llm=_MockLLM({
            "minimize": False, "method": "simplex",
            "objective": {
                "type": "expression",
                "expression": "H2 + CO",
                "named_values": [
                    {"name":"H2","tag":"PSA","property":"mole_fraction_H2"},
                    {"name":"CO","tag":"PSA","property":"mole_fraction_CO"},
                ],
            },
            "variables_to_use": ["RC-01.outlet_temperature_C"],
        }),
        on_eval=_on_eval,
        max_iter=20,
    )
    assert out["success"]
    # Solver must have emitted at least one evaluation
    assert len(eval_log) >= 1, f"No eval callbacks fired"
    # The eval numbers must be monotonically non-decreasing WITHIN each
    # solver phase. The complex path may run multiple solver phases (DE then
    # Simplex), each restarting from iteration 1, so we allow restarts.
    its = [it for it, _, _ in eval_log]
    phases = [[its[0]]]
    for n in its[1:]:
        if n < phases[-1][-1]:   # iteration restarted → new phase
            phases.append([n])
        else:
            phases[-1].append(n)
    for phase in phases:
        assert phase == sorted(phase), \
            f"Phase eval numbers not monotonic: {phase}"
    # best-so-far should be monotonically improving (for maximize, increasing)
    bests = [b for _, _, b in eval_log if b is not None]
    if len(bests) > 2:
        # Allow some early non-monotonic behaviour but final ≥ first
        assert bests[-1] >= bests[0] - 1e-6, \
            f"best-so-far went backwards: {bests[0]} → {bests[-1]}"


def test_fast_path_rejects_questions_not_commands():
    """A QUESTION like 'what can you optimise in this?' must route to the
    LLM for explanation, NOT trigger an actual optimisation run."""
    from agent_v2 import DWSIMAgentV2

    class _LoadedBridge:
        class state:
            name = "loaded_flowsheet.dwxmz"
            active_alias = "main"
            loaded_flowsheets = {"main": object()}
            streams = ["FEED", "PROD"]
            unit_ops = ["RC-1"]

    agent = DWSIMAgentV2.__new__(DWSIMAgentV2)
    agent.bridge = _LoadedBridge()

    # POSITIVE — these are commands; fast-path should fire
    for phrase in [
        "maximise hydrogen yield",
        "do optimisation",
        "optimise the process",
        "minimise total energy consumption",
    ]:
        assert agent._should_fast_path_optimization(phrase), \
            f"Should fast-path command: {phrase!r}"

    # NEGATIVE — these are questions; fast-path must NOT fire
    for phrase in [
        "what can you optimise in this",
        "what can you optimise in this?",
        "what are the optimisation options",
        "what optimisable variables exist",
        "how do I optimise this",
        "how can I maximise yield",
        "which variables can be optimised",
        "can you list the optimisation options",
        "tell me what can be optimised",
        "show me the variables you would optimise",
        "explain optimisation",
        "is it possible to optimise this",
    ]:
        assert not agent._should_fast_path_optimization(phrase), \
            f"Should NOT fast-path question: {phrase!r}"


def test_suggest_decision_variables_skips_output_streams():
    """REGRESSION: a Liquid-Liquid Extraction flowsheet has output streams
    named BOTTOMS, EXTRACTED PRODUCT, SOLVENT, FEED. The suggester must
    only pick the actual feeds (SOLVENT, FEED) and skip outputs."""
    from optimization_orchestrator import suggest_decision_variables

    class _LLEBridge:
        def __init__(self):
            self.props = {
                ("FEED", "mass_flow_kgh"): 12254.0,
                ("FEED", "temperature_C"): 25.0,
                ("SOLVENT", "mass_flow_kgh"): 100000.0,
                ("SOLVENT", "temperature_C"): 25.0,
                ("BOTTOMS", "mass_flow_kgh"): 24321.0,
                ("BOTTOMS", "temperature_C"): 11.55,
                ("EXTRACTED PRODUCT", "mass_flow_kgh"): 198223.0,
                ("EXTRACTED PRODUCT", "temperature_C"): 23.39,
            }
        def get_stream_property(self, tag, prop):
            v = self.props.get((tag, prop))
            return {"success": v is not None, "value": v}
        def get_stream_properties(self, tag):
            return {"success": True,
                    "properties": {k[1]: v for k, v in self.props.items()
                                    if k[0] == tag}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                {"tag": "FEED",              "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "SOLVENT",           "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "BOTTOMS",           "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "EXTRACTED PRODUCT", "type": "MaterialStream",
                 "category": "stream"},
            ]}

    suggestions = suggest_decision_variables(_LLEBridge(), max_n=10)
    tags = {s["tag"] for s in suggestions}
    # Feeds present
    assert "FEED" in tags or "SOLVENT" in tags, \
        f"At least one feed should be suggested. Got tags: {tags}"
    # Output streams MUST be excluded
    assert "BOTTOMS" not in tags
    assert "EXTRACTED PRODUCT" not in tags


def test_fast_path_recognises_vague_optimization_goals():
    """The fast-path matcher must accept vague optimization phrases when a
    flowsheet is loaded, and reject everything else."""
    from agent_v2 import DWSIMAgentV2
    from llm_client import LLMClient

    # Build an agent with a mock bridge that LOOKS like a loaded flowsheet
    class _LoadedBridge:
        class state:
            name = "biodiesel_combustion"
            active_alias = "main"
            loaded_flowsheets = {"main": object()}
            streams = ["Air", "Biodiesel", "Product"]
            unit_ops = ["Combustor", "HX-1"]
        # Methods the agent might poke
        def list_objects(self): return {"unit_ops": [], "streams": []}

    class _EmptyBridge:
        class state:
            name = None; active_alias = None; loaded_flowsheets = {}
            streams = []; unit_ops = []

    # Avoid needing a real LLM client
    import os
    os.environ.setdefault("GROQ_API_KEY", "test_key")

    # We just need the method-level call — skip __init__ heavy setup
    loaded_agent = DWSIMAgentV2.__new__(DWSIMAgentV2)
    loaded_agent.bridge = _LoadedBridge()

    empty_agent = DWSIMAgentV2.__new__(DWSIMAgentV2)
    empty_agent.bridge = _EmptyBridge()

    # — POSITIVE cases (flowsheet loaded, vague optimization phrase) —
    for phrase in (
        "maximise hydrogen yield",
        "maximize H2 purity",
        "minimise total energy consumption",
        "optimize the process",
        "improve efficiency",
        "reduce CO2 emissions",
        "increase production yield",
        # Noun forms (the bug we just fixed)
        "do optimisation of hydrogen production in loaded flowsheet",
        "do optimization of hydrogen production",
        "perform optimisation of the reactor",
        "run optimization on this flowsheet",
        # Conjugations
        "optimised the process",
        "optimised reactor performance",
        "maximisation of yield",
        "Maximize H2+CO purity at the PSA outlet",
        # Bare short commands (the latest bug we just fixed)
        "do optimisation",
        "optimise",
        "optimize",
        "do optimization",
        "minimise it",
        "maximise it",
        "optimise this",
        "optimize this flowsheet",
    ):
        assert loaded_agent._should_fast_path_optimization(phrase), \
            f"Should fast-path: {phrase!r}"

    # — NEGATIVE cases (no flowsheet) —
    for phrase in (
        "maximise hydrogen yield",
        "optimize the process",
    ):
        assert not empty_agent._should_fast_path_optimization(phrase), \
            f"Should NOT fast-path without flowsheet: {phrase!r}"

    # — NEGATIVE: generic questions —
    for phrase in (
        "what is the H2 mole fraction?",
        "show me the streams",
        "load the methanol flowsheet",
        "",
    ):
        assert not loaded_agent._should_fast_path_optimization(phrase), \
            f"Should NOT fast-path: {phrase!r}"


def test_api_suggest_variables_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import api as api_module
    monkeypatch.setattr(api_module, "_get_bridge", lambda: _MockBridge())
    client = TestClient(api_module.app)
    r = client.get("/optimize/suggest-variables?max_n=5").json()
    assert r["success"] is True
    assert len(r["variables"]) > 0
    assert all("tag" in v and "property" in v and "lower" in v and "upper" in v
               for v in r["variables"])
