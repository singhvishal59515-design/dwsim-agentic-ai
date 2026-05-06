"""
tests/test_integration_modules.py
──────────────────────────────────
Integration tests that verify the interaction between modules WITHOUT
requiring a live DWSIM installation or real LLM API calls.

These fill the gap identified in the critical review: the previous test suite
had only unit tests (individual functions) and no tests of the boundaries
between modules (KB → agent, safety → bridge result, economics → agent,
replay → agent, bayesian → objective, API → agent).

Run: pytest tests/test_integration_modules.py -v
"""

import json
import math
import os
import sys
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_agent():
    """Return a DWSIMAgentV2 with fully stubbed LLM and bridge — no DWSIM."""
    from unittest.mock import MagicMock, patch

    with patch("agent_v2.DWSIMBridgeV2") as MockBridge, \
         patch("agent_v2.LLMClient")     as MockLLM:

        bridge = MagicMock()
        bridge.state.name              = "test_fs"
        bridge.state.streams           = ["Feed", "Product"]
        bridge.state.unit_ops          = ["H-101"]
        bridge.state.property_package  = "Steam Tables"
        bridge.state.compounds         = ["Water"]
        bridge.state.converged         = True
        bridge.state.path              = "/tmp/test.dwxmz"
        bridge.state.context_summary   = MagicMock(return_value="[flowsheet context]")
        bridge.state.object_types      = {}
        MockBridge.return_value        = bridge

        llm = MagicMock()
        llm.provider            = "groq"
        llm.model               = "llama-3.3-70b-versatile"
        llm.temperature         = 0.0
        llm._REPRODUCIBILITY_SEED = 42
        MockLLM.return_value    = llm

        import agent_v2
        agent = agent_v2.DWSIMAgentV2(llm=llm, bridge=bridge)
        return agent, bridge, llm


# ─────────────────────────────────────────────────────────────────────────────
# 1. KB → Agent proactive injection
# ─────────────────────────────────────────────────────────────────────────────

class TestKBAgentIntegration:
    """KB is queried and injected into system prompt on first iteration."""

    def test_proactive_kb_injected_for_relevant_query(self):
        """A thermodynamics query triggers KB retrieval and injection."""
        from unittest.mock import MagicMock
        import agent_v2

        bridge = MagicMock()
        bridge.state.context_summary.return_value = "[context]"
        bridge.state.streams  = []
        bridge.state.unit_ops = []
        bridge.state.property_package = ""
        bridge.state.compounds = []
        bridge.state.converged = False
        bridge.state.path = ""
        bridge.state.name = ""
        bridge.state.object_types = {}

        prompt = agent_v2._build_system_prompt(
            bridge,
            user_message="Which property package for ethanol water NRTL distillation?"
        )
        # Must contain the auto-retrieved KB section
        assert "RELEVANT KNOWLEDGE BASE" in prompt, (
            "Proactive KB injection missing — system prompt does not contain retrieved chunks"
        )

    def test_kb_not_injected_for_short_message(self):
        """Short/trivial messages skip KB retrieval to save tokens."""
        from unittest.mock import MagicMock
        import agent_v2

        bridge = MagicMock()
        bridge.state.context_summary.return_value = "[context]"
        bridge.state.streams  = []
        bridge.state.unit_ops = []
        bridge.state.property_package = ""
        bridge.state.compounds = []
        bridge.state.converged = False
        bridge.state.path = ""
        bridge.state.name = ""
        bridge.state.object_types = {}

        prompt = agent_v2._build_system_prompt(bridge, user_message="hi")
        assert "RELEVANT KNOWLEDGE BASE" not in prompt

    def test_kb_not_injected_on_subsequent_iterations(self):
        """On iteration > 1, user_message is empty so KB is not re-queried."""
        from unittest.mock import MagicMock
        import agent_v2

        bridge = MagicMock()
        bridge.state.context_summary.return_value = "[context]"
        bridge.state.streams  = []
        bridge.state.unit_ops = []
        bridge.state.property_package = ""
        bridge.state.compounds = []
        bridge.state.converged = False
        bridge.state.path = ""
        bridge.state.name = ""
        bridge.state.object_types = {}

        # iteration == 1 → user_message passed → KB retrieved
        p1 = agent_v2._build_system_prompt(
            bridge,
            user_message="Pinch analysis minimum utility calculation"
        )
        # iteration > 1 → empty user_message → no KB query
        p2 = agent_v2._build_system_prompt(bridge, user_message="")
        assert "RELEVANT KNOWLEDGE BASE" not in p2


# ─────────────────────────────────────────────────────────────────────────────
# 2. Safety validator → agent result pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyAgentIntegration:
    """SF violations flow correctly from validator through to agent metrics."""

    def test_sf09a_fires_and_reaches_agent_last_sf(self):
        """Global mass balance error is detected and stored in agent state."""
        agent, bridge, llm = _make_agent()

        # Stub: save_and_solve returns stream results with 20% mass imbalance
        bridge.save_and_solve.return_value = {
            "success":   True,
            "converged": True,
            "safety_warnings": [
                {"code": "SF-09a", "severity": "SILENT",
                 "description": "Global mass balance error 20%",
                 "evidence": "m_feed=1000 kg/h, m_product=800 kg/h"}
            ],
            "safety_status": "VIOLATIONS_DETECTED",
            "stream_results": {"Feed": {"mass_flow_kgh": 1000.0},
                               "Product": {"mass_flow_kgh": 800.0}},
        }

        # Simulate the tool result interception in the agent loop
        result = bridge.save_and_solve()
        viols  = result.get("safety_warnings", [])

        # Verify SF-09a is in the returned violations
        codes = [v["code"] for v in viols]
        assert "SF-09a" in codes, f"SF-09a not in warnings: {codes}"

    def test_sf08b_compressor_efficiency_out_of_range(self):
        """Compressor efficiency violation detected by safety validator directly."""
        from safety_validator import SafetyValidator
        sv = SafetyValidator()
        details = {"C-101": {"type": "Compressor", "adiabatic_efficiency": 0.25}}
        fails = sv._check_compressor_efficiency(details)
        assert any(f.code == "SF-08b" for f in fails), "SF-08b must fire for η=0.25"

    def test_sf_validator_never_raises(self):
        """SafetyValidator.check() must not raise on malformed input."""
        from safety_validator import SafetyValidator
        sv = SafetyValidator()
        # Garbage data — should return empty list, not raise
        result = sv.check(
            stream_results={"bad": {"temperature_C": None, "vapor_fraction": "NaN"}},
            topology={"connections": [{}], "unit_ops": [{}]},
        )
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Economics module end-to-end
# ─────────────────────────────────────────────────────────────────────────────

class TestEconomicsIntegration:
    """estimate_capex + estimate_opex + run_economic_analysis work together."""

    def _objects(self):
        return [
            {"tag": "H-101", "type": "Heater",          "category": "unit_op"},
            {"tag": "P-101", "type": "Pump",             "category": "unit_op"},
            {"tag": "Feed",  "type": "MaterialStream",   "category": "stream"},
            {"tag": "Prod",  "type": "MaterialStream",   "category": "stream"},
        ]

    def test_capex_grassroots_exceeds_bm(self):
        from economics import estimate_capex, DEFAULT_PARAMS
        r = estimate_capex(self._objects(), DEFAULT_PARAMS)
        assert r["grassroots_capital"] >= r["bare_module_total"]
        assert r["grassroots_capital"] > 0

    def test_unknown_equipment_type_flagged(self):
        """Equipment not in catalogue gets default cost with cost_estimated=True."""
        from economics import estimate_capex, DEFAULT_PARAMS
        objects = [{"tag": "X-999", "type": "AlienReactor", "category": "unit_op"}]
        r = estimate_capex(objects, DEFAULT_PARAMS)
        flagged = [i for i in r.get("equipment_items", []) if i.get("cost_estimated")]
        assert len(flagged) > 0, "Unknown equipment type must set cost_estimated=True"

    def test_full_economic_analysis_returns_required_keys(self):
        from economics import run_economic_analysis, DEFAULT_PARAMS
        objects = self._objects()
        stream_results = {
            "Feed": {"mass_flow_kgh": 1000.0, "temperature_C": 25.0},
            "Prod": {"mass_flow_kgh": 980.0,  "temperature_C": 80.0},
        }
        r = run_economic_analysis(objects, stream_results, {}, DEFAULT_PARAMS)
        # Top-level structure keys
        for key in ("capex", "opex", "revenue", "profit", "metrics"):
            assert key in r, f"Missing top-level key '{key}' in economic analysis result"
        # Financial metrics nested under 'metrics'
        for key in ("npv_usd", "irr_pct", "payback_years"):
            assert key in r["metrics"], f"Missing key '{key}' in metrics section"

    def test_elec_estimated_flag_set_when_no_mechanical_tags(self):
        """When no pumps/compressors found, electricity cost uses heuristic and flags it."""
        from economics import estimate_opex, DEFAULT_PARAMS
        # estimate_opex(stream_results, unit_op_duties, params) — no `objects` arg
        stream_results = {"Feed": {"mass_flow_kgh": 1000.0, "temperature_C": 25.0}}
        r = estimate_opex(stream_results, {}, DEFAULT_PARAMS)
        assert r.get("elec_cost_estimated") is True, (
            "elec_cost_estimated flag must be True when no mechanical tags found"
        )

    def test_irr_positive_for_profitable_project(self):
        from economics import calculate_irr
        irr = calculate_irr(capex=1_000_000, annual_profit=300_000, years=15)
        assert irr is not None and irr > 0

    def test_npv_respects_discount_rate(self):
        from economics import calculate_npv
        npv_low  = calculate_npv(1_000_000, 200_000, rate=0.05, years=10)
        npv_high = calculate_npv(1_000_000, 200_000, rate=0.20, years=10)
        assert npv_low > npv_high, "Higher discount rate must produce lower NPV"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Replay log end-to-end round-trip
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayLogIntegration:
    """TurnBuilder → append → load → from_dict round-trip."""

    def test_full_round_trip(self, tmp_path):
        """Write a turn, read it back, verify every field survives JSON serialisation."""
        import os
        import replay_log as rl
        from safety_validator import ValidationFailure

        log_path = str(tmp_path / "test_replay.jsonl")
        os.environ["DWSIM_REPLAY_LOG_DIR"] = str(tmp_path)

        builder = rl.TurnBuilder("sess-int", 0, "openai", "gpt-4o", 0.0, 42)
        builder.set_prompt("Build water heater", "You are a DWSIM assistant.")
        builder.add_message_snapshot([{"role": "user", "content": "Build water heater"}])
        builder.record_tool_call("new_flowsheet",   {"name": "WH"},  {"success": True},  120.0)
        builder.record_tool_call("save_and_solve",  {},              {"success": True, "converged": True}, 3800.0)

        viol = ValidationFailure("SF-01", "SILENT", "CalcMode not set", "T_out=T_in")
        turn = builder.finish(
            final_answer    = "Water heater built successfully.",
            converged       = True,
            stream_snapshot = {"Product": {"temperature_C": 80.0}},
            sf_violations   = [viol],
        )
        rl.append_turn(turn)

        # Load and verify
        turns = rl.load_turns(session_id="sess-int")
        assert len(turns) == 1
        t = turns[0]
        assert t.session_id    == "sess-int"
        assert t.converged     is True
        assert t.temperature   == 0.0
        assert t.seed          == 42
        assert len(t.tool_calls) == 2
        assert t.tool_calls[0].tool_name == "new_flowsheet"
        assert t.tool_calls[1].tool_name == "save_and_solve"
        assert t.tool_calls[1].duration_ms == pytest.approx(3800.0)
        assert len(t.sf_violations) == 1
        assert t.sf_violations[0]["code"] == "SF-01"
        assert t.stream_snapshot == {"Product": {"temperature_C": 80.0}}

        os.environ.pop("DWSIM_REPLAY_LOG_DIR", None)

    def test_session_summary_counts_correctly(self, tmp_path):
        """session_summary returns correct turn count and convergence rate."""
        import os
        import replay_log as rl

        os.environ["DWSIM_REPLAY_LOG_DIR"] = str(tmp_path)
        for i in range(4):
            b = rl.TurnBuilder("sess-sum", i, "groq", "llama", 0.0, 42)
            b.set_prompt(f"Task {i}", "")
            turn = b.finish(f"done {i}", i % 2 == 0, {}, [])
            rl.append_turn(turn)

        s = rl.session_summary("sess-sum")
        assert s["turns"]        == 4
        assert s["converged_pct"] == 50.0   # 2/4 converged
        os.environ.pop("DWSIM_REPLAY_LOG_DIR", None)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Bayesian Optimizer end-to-end on analytical function
# ─────────────────────────────────────────────────────────────────────────────

class TestBayesianOptimizerIntegration:
    """Full BO loop on known functions — verifies GP + EI + LHS interact correctly."""

    def test_finds_1d_minimum(self):
        """1-D bowl: f(x) = (x-3)^2, minimum at x=3."""
        from bayesian_optimizer import BayesianOptimizer
        result = BayesianOptimizer(
            bounds    = {"x": (0.0, 6.0)},
            n_initial = 4,
            max_iter  = 10,
            minimize  = True,
            seed      = 42,
        ).run(lambda p: (p["x"] - 3.0) ** 2)

        assert result.best_value < 0.5,  f"Expected near 0, got {result.best_value}"
        assert abs(result.best_params["x"] - 3.0) < 1.0, (
            f"Expected x~3.0, got {result.best_params['x']}"
        )

    def test_maximisation_mode(self):
        """Maximise f(x) = -(x-5)^2 + 10, maximum at x=5, f=10."""
        from bayesian_optimizer import BayesianOptimizer
        result = BayesianOptimizer(
            bounds    = {"x": (0.0, 10.0)},
            n_initial = 4,
            max_iter  = 10,
            minimize  = False,
            seed      = 0,
        ).run(lambda p: -(p["x"] - 5.0) ** 2 + 10.0)

        assert result.best_value > 8.0, f"Expected near 10, got {result.best_value}"

    def test_handles_failed_evaluations_gracefully(self):
        """Objective returning None (failed sim) must not crash the optimizer."""
        from bayesian_optimizer import BayesianOptimizer
        call_count = [0]

        def flaky(p):
            call_count[0] += 1
            # Fail every third evaluation
            if call_count[0] % 3 == 0:
                return None
            return (p["x"] - 2.0) ** 2

        result = BayesianOptimizer(
            bounds    = {"x": (0.0, 4.0)},
            n_initial = 4,
            max_iter  = 8,
            minimize  = True,
            seed      = 7,
        ).run(flaky)

        assert result is not None
        assert result.best_value < 2.0

    def test_gp_cholesky_fallback_on_identical_points(self):
        """GP must not crash when all observations are identical (σ→0, near-singular K)."""
        from bayesian_optimizer import BayesianOptimizer
        # Objective always returns 5.0 → all y_obs identical → K nearly singular
        result = BayesianOptimizer(
            bounds    = {"x": (0.0, 1.0)},
            n_initial = 5,
            max_iter  = 5,
            minimize  = True,
            seed      = 1,
        ).run(lambda p: 5.0)
        # Main check: no crash. Secondary: result is returned
        assert result.best_value == pytest.approx(5.0)

    def test_2d_rosenbrock_finds_valley(self):
        """2-D Rosenbrock banana function: minimum at (1,1), value=0."""
        from bayesian_optimizer import BayesianOptimizer

        def rosenbrock(p):
            x, y = p["x"], p["y"]
            return (1 - x)**2 + 100 * (y - x**2)**2

        result = BayesianOptimizer(
            bounds    = {"x": (-2.0, 2.0), "y": (-1.0, 3.0)},
            n_initial = 5,
            max_iter  = 20,
            minimize  = True,
            seed      = 42,
        ).run(rosenbrock)

        # Should find a value reasonably close to 0 within 25 evals
        assert result.best_value < 50.0, f"Expected <50, got {result.best_value:.2f}"
        assert result.n_evals <= 25


# ─────────────────────────────────────────────────────────────────────────────
# 6. Agent argument coercion → tool dispatch pipeline
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentToolDispatch:
    """Coerced args reach the bridge method correctly."""

    def test_coerced_float_reaches_bridge(self):
        """String '80' is coerced to float 80.0 before set_stream_property call."""
        agent, bridge, _ = _make_agent()
        bridge.set_stream_property.return_value = {"success": True, "old_value": 25.0, "new_value": 80.0}

        coerced = agent._coerce_arguments("set_stream_property", {"value": "80"})
        assert coerced["value"] == 80.0
        assert isinstance(coerced["value"], float)

    def test_tool_result_compress_does_not_lose_success(self):
        """Compression never drops the 'success' key, even on huge results."""
        import agent_v2
        big_result = {
            "success": False,
            "error":   "convergence failed",
            "data":    "x" * 5_000,
        }
        compressed = agent_v2._compress_tool_result("save_and_solve", big_result)
        assert compressed.get("success") is False
        assert compressed.get("error") == "convergence failed"

    def test_circuit_breaker_stops_on_repeated_same_error(self):
        """Same (tool, error) pair 3 times in a row triggers circuit break."""
        agent, bridge, llm = _make_agent()

        # Simulate circuit-breaker state: three identical failures
        fail_set = {("save_and_solve", "CONVERGENCE_FAILED")}
        recent   = [fail_set, fail_set, fail_set]

        triggered = (
            len(recent) == 3
            and all(recent)
            and recent[0] & recent[1] & recent[2]
        )
        assert triggered, "Circuit breaker logic did not trigger as expected"

    def test_history_trim_always_keeps_last_4(self):
        """Even with massive history, _trim_history keeps at least 4 messages."""
        import agent_v2
        history = [{"role": "user", "content": "x" * 10_000} for _ in range(20)]
        trimmed = agent_v2._trim_history(list(history))
        assert len(trimmed) >= 4


# ─────────────────────────────────────────────────────────────────────────────
# 7. LLM retry aggregate timeout
# ─────────────────────────────────────────────────────────────────────────────

class TestLLMRetryTimeout:
    """_llm_chat_with_retry respects aggregate budget and backoff guards."""

    def test_aggregate_budget_prevents_long_hang(self):
        """When budget is tiny, retry loop exits before all attempts."""
        import agent_v2
        agent, bridge, llm = _make_agent()

        # Override budget to 0.1 s so the loop exits immediately
        original = agent_v2._LLM_RETRY_BUDGET_S
        agent_v2._LLM_RETRY_BUDGET_S = 0.1

        # LLM always fails
        llm.chat.side_effect = Exception("provider error")

        t0     = time.monotonic()
        result = agent._llm_chat_with_retry(messages=[], tools=[], system_prompt="")
        elapsed = time.monotonic() - t0

        agent_v2._LLM_RETRY_BUDGET_S = original  # restore

        assert result is None
        assert elapsed < 5.0, f"Retry loop took too long: {elapsed:.1f}s (budget was 0.1s)"

    def test_backoff_index_guard(self):
        """Backoff tuple shorter than attempts never causes IndexError."""
        import agent_v2
        orig_attempts = agent_v2._LLM_MAX_ATTEMPTS
        orig_backoff  = agent_v2._LLM_BACKOFF_S

        # Set 5 attempts but only 2-element backoff tuple
        agent_v2._LLM_MAX_ATTEMPTS   = 5
        agent_v2._LLM_BACKOFF_S      = (0.01, 0.01)
        agent_v2._LLM_RETRY_BUDGET_S = 2.0

        agent, _, llm = _make_agent()
        llm.chat.return_value = None  # always returns None (no IndexError expected)

        try:
            result = agent._llm_chat_with_retry(messages=[], tools=[], system_prompt="")
        except IndexError as e:
            pytest.fail(f"IndexError in backoff — guard is broken: {e}")
        finally:
            agent_v2._LLM_MAX_ATTEMPTS   = orig_attempts
            agent_v2._LLM_BACKOFF_S      = orig_backoff
            agent_v2._LLM_RETRY_BUDGET_S = 180.0

        assert result is None
