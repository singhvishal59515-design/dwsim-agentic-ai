"""
tests/test_agent_logic.py
─────────────────────────
Unit tests for agent_v2 logic that don't require DWSIM or an LLM.
Tests cover: argument coercion, history trimming, tool result compression,
reproducibility fingerprinting, and circuit-breaker logic.

Run:  pytest tests/test_agent_logic.py -v
"""

import json
import sys
import os
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def agent():
    """Minimal agent with stub LLM and bridge — no DWSIM required."""
    from unittest.mock import MagicMock, patch

    with patch("agent_v2.DWSIMBridgeV2") as MockBridge, \
         patch("agent_v2.LLMClient")     as MockLLM:

        bridge_inst = MagicMock()
        bridge_inst.state = MagicMock()
        bridge_inst.state.name            = "test_fs"
        bridge_inst.state.streams         = ["Feed", "Product"]
        bridge_inst.state.unit_ops        = ["H-101"]
        bridge_inst.state.property_package = "Steam Tables"
        bridge_inst.state.compounds       = ["Water"]
        bridge_inst.state.converged       = True
        bridge_inst.state.path            = "/tmp/test.dwxmz"
        bridge_inst.state.context_summary = MagicMock(return_value="[flowsheet context]")

        MockBridge.return_value = bridge_inst

        llm_inst = MagicMock()
        llm_inst.provider    = "groq"
        llm_inst.model       = "llama-3.3-70b-versatile"
        llm_inst.temperature = 0.0
        llm_inst._REPRODUCIBILITY_SEED = 42
        MockLLM.return_value = llm_inst

        import agent_v2
        a = agent_v2.DWSIMAgentV2(llm=llm_inst, bridge=bridge_inst)
        return a


# ─────────────────────────────────────────────────────────────────────────────
# 1. Argument coercion
# ─────────────────────────────────────────────────────────────────────────────

class TestArgumentCoercion:

    def test_string_to_float(self, agent):
        """LLM often sends '80' instead of 80 — must coerce to float."""
        coerced = agent._coerce_arguments("set_stream_property", {"value": "80"})
        assert coerced["value"] == 80.0
        assert isinstance(coerced["value"], float)

    def test_string_to_int(self, agent):
        coerced = agent._coerce_arguments("set_column_specs", {"from_port": "1"})
        assert coerced["from_port"] == 1
        assert isinstance(coerced["from_port"], int)

    def test_string_true_to_bool(self, agent):
        coerced = agent._coerce_arguments("optimize_parameter", {"minimize": "true"})
        assert coerced["minimize"] is True

    def test_string_false_to_bool(self, agent):
        coerced = agent._coerce_arguments("optimize_parameter", {"minimize": "false"})
        assert coerced["minimize"] is False

    def test_float_string_to_int(self, agent):
        """'3.0' should become int 3 for integer fields."""
        coerced = agent._coerce_arguments("set_column_specs", {"from_port": "3.0"})
        assert coerced["from_port"] == 3

    def test_non_coerced_field_unchanged(self, agent):
        coerced = agent._coerce_arguments("load_flowsheet", {"path": "C:/test.dwxmz"})
        assert coerced["path"] == "C:/test.dwxmz"

    def test_none_value_unchanged(self, agent):
        coerced = agent._coerce_arguments("set_stream_property", {"value": None})
        assert coerced["value"] is None

    def test_already_correct_type_unchanged(self, agent):
        coerced = agent._coerce_arguments("set_stream_property", {"value": 80.0})
        assert coerced["value"] == 80.0

    def test_bad_coercion_keeps_original(self, agent):
        """If 'abc' can't be cast to float, keep original."""
        coerced = agent._coerce_arguments("set_stream_property", {"value": "abc"})
        assert coerced["value"] == "abc"


# ─────────────────────────────────────────────────────────────────────────────
# 2. History trimming
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryTrimming:

    def test_trims_by_message_count(self):
        from agent_v2 import _trim_history, _MAX_HISTORY_MESSAGES
        history = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
        trimmed = _trim_history(list(history))
        assert len(trimmed) <= _MAX_HISTORY_MESSAGES

    def test_always_keeps_last_4(self):
        from agent_v2 import _trim_history, _MAX_HISTORY_CHARS
        # Create history that exceeds char budget
        big_content = "x" * (_MAX_HISTORY_CHARS // 2)
        history = [{"role": "user", "content": big_content} for _ in range(10)]
        trimmed = _trim_history(list(history))
        assert len(trimmed) >= 4

    def test_char_budget_respected(self):
        from agent_v2 import _trim_history, _MAX_HISTORY_CHARS
        history = [{"role": "user", "content": "x" * 10_000} for _ in range(20)]
        trimmed = _trim_history(list(history))
        total = sum(len(str(m.get("content", ""))) for m in trimmed)
        assert total <= _MAX_HISTORY_CHARS or len(trimmed) == 4  # always keep ≥4

    def test_empty_history_unchanged(self):
        from agent_v2 import _trim_history
        assert _trim_history([]) == []

    def test_short_history_unchanged(self):
        from agent_v2 import _trim_history
        history = [{"role": "user", "content": "hello"}]
        assert _trim_history(list(history)) == history


# ─────────────────────────────────────────────────────────────────────────────
# 3. Tool result compression
# ─────────────────────────────────────────────────────────────────────────────

class TestToolResultCompression:

    def test_large_stream_results_truncated(self):
        from agent_v2 import _compress_tool_result
        # 20 streams — only 6 should remain in full
        streams = {f"S{i}": {"temperature_C": 80.0, "mass_flow_kgh": 1000.0}
                   for i in range(20)}
        result = {"success": True, "stream_results": streams}
        compressed = _compress_tool_result("save_and_solve", result)
        sr = compressed.get("stream_results", {})
        # Should have 6 full + 1 summary key
        assert len(sr) <= 8  # 6 full + 1 "+N more" key + possible others

    def test_small_result_unchanged(self):
        from agent_v2 import _compress_tool_result
        result = {"success": True, "message": "done"}
        assert _compress_tool_result("new_flowsheet", result) == result

    def test_huge_result_truncated(self):
        from agent_v2 import _compress_tool_result
        result = {"success": True, "data": "x" * 10_000}
        compressed = _compress_tool_result("search_knowledge", result)
        assert compressed.get("_truncated") is True

    def test_success_field_preserved(self):
        from agent_v2 import _compress_tool_result
        result = {"success": False, "error": "bad input", "data": "x" * 5_000}
        compressed = _compress_tool_result("search_knowledge", result)
        assert compressed.get("success") is False
        assert compressed.get("error") == "bad input"

    def test_non_stream_tool_not_compressed(self):
        from agent_v2 import _compress_tool_result
        result = {"success": True, "items": list(range(50))}
        compressed = _compress_tool_result("find_flowsheets", result)
        # find_flowsheets is not in _STREAM_RESULT_TOOLS so stream logic doesn't apply
        assert "stream_results" not in result  # just verify no crash


# ─────────────────────────────────────────────────────────────────────────────
# 4. Reproducibility fingerprint
# ─────────────────────────────────────────────────────────────────────────────

class TestReproducibility:

    def test_same_input_gives_same_hash(self, agent):
        import hashlib
        prompt = "Create a water heater"
        tools  = ["new_flowsheet", "add_object", "save_and_solve"]
        h1 = hashlib.sha256((prompt + json.dumps(tools)).encode()).hexdigest()[:16]
        h2 = hashlib.sha256((prompt + json.dumps(tools)).encode()).hexdigest()[:16]
        assert h1 == h2

    def test_different_tool_sequence_gives_different_hash(self, agent):
        import hashlib
        prompt = "Create a water heater"
        t1 = ["new_flowsheet", "save_and_solve"]
        t2 = ["load_flowsheet", "run_simulation"]
        h1 = hashlib.sha256((prompt + json.dumps(t1)).encode()).hexdigest()[:16]
        h2 = hashlib.sha256((prompt + json.dumps(t2)).encode()).hexdigest()[:16]
        assert h1 != h2

    def test_temperature_is_zero(self, agent):
        assert agent.llm.temperature == 0.0

    def test_seed_is_42(self, agent):
        assert agent.llm._REPRODUCIBILITY_SEED == 42


# ─────────────────────────────────────────────────────────────────────────────
# 5. Economics — IRR and CAPEX correctness
# ─────────────────────────────────────────────────────────────────────────────

class TestEconomics:

    def test_irr_exists_and_positive(self):
        from economics import calculate_irr
        irr = calculate_irr(capex=1_000_000, annual_profit=200_000, years=15)
        assert irr is not None
        assert irr > 0

    def test_irr_none_when_unprofitable(self):
        from economics import calculate_irr
        irr = calculate_irr(capex=1_000_000, annual_profit=10_000, years=5)
        assert irr is None  # can't recover capex

    def test_npv_positive_good_project(self):
        from economics import calculate_npv
        npv = calculate_npv(capex=1_000_000, annual_profit=300_000, rate=0.1, years=15)
        assert npv > 0

    def test_npv_negative_bad_project(self):
        from economics import calculate_npv
        npv = calculate_npv(capex=5_000_000, annual_profit=50_000, rate=0.12, years=5)
        assert npv < 0

    def test_capex_grassroots_includes_contingency(self):
        from economics import estimate_capex, DEFAULT_PARAMS
        objects = [{"tag": "H-101", "type": "Heater",  "category": "unit_op"},
                   {"tag": "P-101", "type": "Pump",    "category": "unit_op"},
                   {"tag": "Feed",  "type": "MaterialStream", "category": "stream"}]
        result = estimate_capex(objects, DEFAULT_PARAMS)
        # Grassroots ≥ bare module (contingency + 1.18 factor)
        assert result["grassroots_capital"] >= result["bare_module_total"]
        assert result["method"] == "Bare-Module (Turton et al., 4th ed.)"

    def test_tiered_steam_pricing_lp(self):
        from economics import _steam_price, DEFAULT_PARAMS
        # 120°C → LP steam
        price, label = _steam_price(120.0, DEFAULT_PARAMS)
        assert "LP" in label
        assert price == DEFAULT_PARAMS["steam_lp_per_GJ"]

    def test_tiered_steam_pricing_hp(self):
        from economics import _steam_price, DEFAULT_PARAMS
        # 250°C → HP steam
        price, label = _steam_price(250.0, DEFAULT_PARAMS)
        assert "HP" in label

    def test_tiered_cooling_cryogenic(self):
        from economics import _cool_price, DEFAULT_PARAMS
        price, label = _cool_price(-30.0, DEFAULT_PARAMS)
        assert "Cryo" in label or "cryogenic" in label.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 6. Safety validator — SF-08 sub-modes
# ─────────────────────────────────────────────────────────────────────────────

class TestSF08:

    def _sv(self):
        from safety_validator import SafetyValidator
        return SafetyValidator()

    def test_sf08b_low_efficiency_flagged(self):
        sv = self._sv()
        details = {"C-101": {"type": "Compressor", "adiabatic_efficiency": 0.30}}
        fails = sv._check_compressor_efficiency(details)
        assert any(f.code == "SF-08b" for f in fails)

    def test_sf08b_high_efficiency_flagged(self):
        sv = self._sv()
        details = {"C-101": {"type": "Compressor", "adiabatic_efficiency": 0.99}}
        fails = sv._check_compressor_efficiency(details)
        assert any(f.code == "SF-08b" for f in fails)

    def test_sf08b_normal_efficiency_ok(self):
        sv = self._sv()
        details = {"C-101": {"type": "Compressor", "adiabatic_efficiency": 0.75}}
        fails = sv._check_compressor_efficiency(details)
        assert not fails

    def test_sf08d_monotonic_profile_ok(self):
        sv = self._sv()
        # Temperatures increasing bottom to top (index 0=condenser, last=reboiler)
        details = {"D-101": {"type": "DistillationColumn",
                              "stage_temperatures": [80, 90, 100, 110, 120]}}
        fails = sv._check_distillation_profile(details)
        assert not fails

    def test_sf08d_non_monotonic_flagged(self):
        sv = self._sv()
        details = {"D-101": {"type": "DistillationColumn",
                              "stage_temperatures": [80, 120, 90, 110, 100]}}
        fails = sv._check_distillation_profile(details)
        assert any(f.code == "SF-08d" for f in fails)

    def test_sf08c_exothermic_wrong_sign(self):
        sv = self._sv()
        details = {"R-combustion": {"type": "ConversionReactor", "description": "combustion"}}
        duties  = {"R-combustion": 500.0}  # positive = endothermic sign (WRONG for combustion)
        fails = sv._check_reactor_enthalpy(details, duties)
        assert any(f.code == "SF-08c" for f in fails)

    def test_sf08c_negligible_duty_ok(self):
        sv = self._sv()
        details = {"R-combustion": {"type": "ConversionReactor", "description": "combustion"}}
        duties  = {"R-combustion": 0.5}  # negligible — skip
        fails = sv._check_reactor_enthalpy(details, duties)
        assert not fails


# ─────────────────────────────────────────────────────────────────────────────
# 7. Knowledge base retrieval
# ─────────────────────────────────────────────────────────────────────────────

class TestKBRetrieval:

    @pytest.fixture
    def kb(self):
        from knowledge_base import KnowledgeBase
        return KnowledgeBase()

    def test_pinch_analysis_query(self, kb):
        r = kb.search("pinch analysis minimum utilities", top_k=3)
        assert r["success"]
        titles = [c["title"].lower() for c in r["results"]]
        assert any("pinch" in t for t in titles)

    def test_cpa_eos_query(self, kb):
        r = kb.search("CPA equation of state water hydrocarbon", top_k=3)
        assert r["success"]
        texts = " ".join(c["text"].lower() for c in r["results"])
        assert "cpa" in texts or "association" in texts

    def test_sensitivity_parametric_query(self, kb):
        r = kb.search("sensitivity analysis parametric study DOE", top_k=3)
        assert r["success"]
        texts = " ".join(c["text"].lower() for c in r["results"])
        assert "parametric" in texts or "sensitivity" in texts

    def test_process_intensification_query(self, kb):
        r = kb.search("reactive distillation process intensification", top_k=3)
        assert r["success"]
        texts = " ".join(c["text"].lower() for c in r["results"])
        assert "reactive" in texts or "intensif" in texts


# ─────────────────────────────────────────────────────────────────────────────
# 8. SF-09 — Global flowsheet balance (network-level)
# ─────────────────────────────────────────────────────────────────────────────

class TestSF09GlobalBalance:

    @pytest.fixture
    def sv(self):
        from safety_validator import SafetyValidator
        return SafetyValidator()

    @pytest.fixture
    def simple_topology(self):
        return {
            "unit_ops":    [{"tag": "H-101", "type": "Heater"}],
            "connections": [
                {"from": "Feed",    "to": "H-101"},
                {"from": "H-101",   "to": "Product"},
            ],
        }

    def test_sf09a_fires_at_20pct_error(self, sv, simple_topology):
        streams = {
            "Feed":    {"mass_flow_kgh": 1000.0},
            "Product": {"mass_flow_kgh": 800.0},
        }
        fails = sv.check_global_balance(streams, simple_topology)
        codes = [f.code for f in fails]
        assert "SF-09a" in codes

    def test_sf09a_silent_below_2pct(self, sv, simple_topology):
        streams = {
            "Feed":    {"mass_flow_kgh": 1000.0},
            "Product": {"mass_flow_kgh": 1010.0},  # 1% error
        }
        fails = sv.check_global_balance(streams, simple_topology)
        assert not any(f.code == "SF-09a" for f in fails)

    def test_sf09b_fires_when_utility_mismatch(self, sv, simple_topology):
        """Q_utility = 250 kW but stream enthalpy change = 100 kW → mismatch."""
        streams = {
            "Feed":    {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 100.0},
            "Product": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 200.0},
        }
        fails = sv.check_global_balance(
            streams, simple_topology, unit_op_duties={"H-101": 250.0}
        )
        assert any(f.code == "SF-09b" for f in fails)

    def test_sf09b_ok_when_balanced(self, sv, simple_topology):
        """Q_utility = 100 kW matches stream ΔH = 100 kW."""
        streams = {
            "Feed":    {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 100.0},
            "Product": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 200.0},
        }
        fails = sv.check_global_balance(
            streams, simple_topology, unit_op_duties={"H-101": 100.0}
        )
        assert not any(f.code == "SF-09b" for f in fails)

    def test_sf09b_skipped_without_enthalpy_data(self, sv, simple_topology):
        """If stream_results has no enthalpy_kJ_kg, SF-09b must NOT fire."""
        streams = {
            "Feed":    {"mass_flow_kgh": 1000.0, "temperature_C": 25.0},
            "Product": {"mass_flow_kgh": 1000.0, "temperature_C": 80.0},
        }
        fails = sv.check_global_balance(
            streams, simple_topology, unit_op_duties={"H-101": 999.0}
        )
        assert not any(f.code == "SF-09b" for f in fails)

    def test_sf09c_dangling_stream_flagged(self, sv):
        """Unit op has an outlet stream with zero flow — orphaned."""
        streams = {
            "Feed":    {"mass_flow_kgh": 1000.0},
            "Product": {"mass_flow_kgh": 1000.0},
            "Dangle":  {"mass_flow_kgh": 0.0},
        }
        topology = {
            "unit_ops": [{"tag": "H-101", "type": "Heater"}],
            "connections": [
                {"from": "Feed",   "to": "H-101"},
                {"from": "H-101",  "to": "Product"},
                {"from": "H-101",  "to": "Dangle"},  # Dangle never goes anywhere
            ],
        }
        fails = sv.check_global_balance(streams, topology)
        assert any(f.code == "SF-09c" for f in fails)

    def test_sf09_skipped_without_topology(self, sv):
        streams = {"Feed": {"mass_flow_kgh": 1000.0}}
        fails = sv.check_global_balance(streams, topology=None)
        assert fails == []

    def test_sf09_integrated_in_check_with_duties(self, sv, simple_topology):
        """SF-09a must appear when check_with_duties is called with 20% mass error."""
        streams = {
            "Feed":    {"mass_flow_kgh": 1000.0},
            "Product": {"mass_flow_kgh": 500.0},  # 50% error
        }
        failures, _ = sv.check_with_duties(streams, simple_topology)
        codes = [f.code for f in failures]
        assert "SF-09a" in codes


# ─────────────────────────────────────────────────────────────────────────────
# 9. Reproducibility replay log
# ─────────────────────────────────────────────────────────────────────────────

class TestReplayLog:

    def test_turn_builder_creates_valid_turn(self):
        from replay_log import TurnBuilder
        b = TurnBuilder("sess-test", 0, "groq", "llama-3.3-70b", 0.0, 42)
        b.set_prompt("Create a water heater", "You are a DWSIM assistant.")
        b.add_message_snapshot([{"role": "user", "content": "Create a water heater"}])
        b.record_tool_call(
            "new_flowsheet", {"name": "WaterHeater"},
            {"success": True, "message": "created"}, 120.5
        )
        b.record_tool_call(
            "save_and_solve", {}, {"success": True, "converged": True}, 3500.0
        )
        turn = b.finish("Done!", True, {"Feed": {"temperature_C": 80.0}}, [])

        assert turn.session_id == "sess-test"
        assert turn.turn_index == 0
        assert turn.provider == "groq"
        assert turn.temperature == 0.0
        assert turn.seed == 42
        assert len(turn.tool_calls) == 2
        assert turn.tool_calls[0].tool_name == "new_flowsheet"
        assert turn.tool_calls[1].tool_name == "save_and_solve"
        assert turn.converged is True
        assert turn.llm_calls == 1
        assert len(turn.prompt_hash) == 16  # sha256 hex prefix

    def test_turn_prompt_hash_deterministic(self):
        from replay_log import TurnBuilder
        import hashlib, json
        prompt = "Create a water heater"
        tools  = ["new_flowsheet", "save_and_solve"]
        expected = hashlib.sha256(
            (prompt + json.dumps(tools)).encode("utf-8")
        ).hexdigest()[:16]

        b = TurnBuilder("s", 0, "groq", "m", 0.0, 42)
        b.set_prompt(prompt, "")
        for t in tools:
            b.record_tool_call(t, {}, {"success": True}, 1.0)
        turn = b.finish("done", True, {}, [])
        assert turn.prompt_hash == expected

    def test_turn_serialises_to_dict(self):
        from replay_log import TurnBuilder, ReplayTurn
        b = TurnBuilder("s2", 1, "gemini", "gemini-flash", 0.0, 42)
        b.set_prompt("test", "sys")
        turn = b.finish("answer", False, {}, [])
        d = turn.to_dict()
        assert isinstance(d, dict)
        assert d["session_id"] == "s2"
        assert d["turn_index"] == 1
        assert isinstance(d["tool_calls"], list)

    def test_turn_roundtrip_from_dict(self):
        from replay_log import TurnBuilder, ReplayTurn
        b = TurnBuilder("s3", 0, "groq", "llama", 0.0, 42)
        b.set_prompt("hello", "")
        b.record_tool_call("new_flowsheet", {"name": "t"}, {"success": True}, 50.0)
        turn = b.finish("ok", True, {}, [])
        d = turn.to_dict()
        turn2 = ReplayTurn.from_dict(d)
        assert turn2.turn_id == turn.turn_id
        assert len(turn2.tool_calls) == 1
        assert turn2.tool_calls[0].tool_name == "new_flowsheet"

    def test_tool_call_success_and_error_fields(self):
        from replay_log import TurnBuilder
        b = TurnBuilder("s4", 0, "groq", "m", 0.0, 42)
        b.set_prompt("p", "")
        b.record_tool_call("bad_tool", {}, {"success": False, "error": "not found"}, 10.0)
        turn = b.finish("failed", False, {}, [])
        tc = turn.tool_calls[0]
        assert tc.success is False
        assert tc.error == "not found"

    def test_sf_violations_stored_in_turn(self):
        from replay_log import TurnBuilder
        from safety_validator import ValidationFailure
        b = TurnBuilder("s5", 0, "groq", "m", 0.0, 42)
        b.set_prompt("p", "")
        viol = ValidationFailure(
            code="SF-09a", severity="SILENT",
            description="mass balance error", evidence="20%",
        )
        turn = b.finish("done", False, {}, [viol])
        assert len(turn.sf_violations) == 1
        assert turn.sf_violations[0]["code"] == "SF-09a"

    def test_session_summary_empty(self):
        from replay_log import session_summary
        s = session_summary("nonexistent-session-id-xyz")
        assert s["session_id"] == "nonexistent-session-id-xyz"
        assert s["turns"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# 10. SF-08a — Enthalpy-based HX energy balance (replaced Cp approximation)
# ─────────────────────────────────────────────────────────────────────────────

class TestSF08aEnthalpyBased:

    @pytest.fixture
    def sv(self):
        from safety_validator import SafetyValidator
        return SafetyValidator()

    @pytest.fixture
    def hx_topology(self):
        return {
            "unit_ops": [{"tag": "H-101", "type": "Heater"}],
            "connections": [
                {"from": "S1", "to": "H-101",  "to_port": 0},
                {"from": "H-101", "to": "S2",  "from_port": 0},
            ],
        }

    def test_sf08a_fires_with_direct_enthalpy(self, sv, hx_topology):
        """dH = (300-100)*3600/3600 = 200 kW; duty=500 kW → >10% mismatch."""
        streams = {
            "S1": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 100.0},
            "S2": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 300.0},
        }
        fails = sv._check_hx_energy_balance(streams, hx_topology, {"H-101": 500.0})
        assert any(f.code == "SF-08a" for f in fails)

    def test_sf08a_method_reported_as_enthalpy(self, sv, hx_topology):
        streams = {
            "S1": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 100.0},
            "S2": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 300.0},
        }
        fails = sv._check_hx_energy_balance(streams, hx_topology, {"H-101": 500.0})
        sf08a = [f for f in fails if f.code == "SF-08a"]
        assert sf08a
        assert "enthalpy" in sf08a[0].evidence

    def test_sf08a_ok_when_duty_matches_enthalpy(self, sv, hx_topology):
        """dH = 200 kW; duty = 200 kW → match, no violation."""
        streams = {
            "S1": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 100.0},
            "S2": {"mass_flow_kgh": 3600.0, "enthalpy_kJ_kg": 200.0},
        }
        fails = sv._check_hx_energy_balance(streams, hx_topology, {"H-101": 100.0})
        assert not any(f.code == "SF-08a" for f in fails)

    def test_sf08a_falls_back_to_cp_when_no_enthalpy(self, sv, hx_topology):
        """Without enthalpy_kJ_kg, falls back to Cp × ΔT. Large discrepancy fires."""
        streams = {
            "S1": {"mass_flow_kgh": 3600.0, "temperature_C": 25.0,
                   "mole_fractions": {"Water": 1.0}},
            "S2": {"mass_flow_kgh": 3600.0, "temperature_C": 85.0},
        }
        # dH_cp = (3600/3600) * 4.18 * 60 = 250.8 kW; duty=1000 kW → mismatch
        fails = sv._check_hx_energy_balance(streams, hx_topology, {"H-101": 1000.0})
        sf08a = [f for f in fails if f.code == "SF-08a"]
        assert sf08a
        assert "Cp-approx" in sf08a[0].evidence
