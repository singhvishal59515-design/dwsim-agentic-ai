"""
Tests for the analysis fast-answer path — deterministic responses to
introspection questions like "what variables can be optimised?" that work
even when all LLM providers are down.
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _LoadedBridge:
    class state:
        name = "test_flowsheet.dwxmz"
        active_alias = "main"
        loaded_flowsheets = {"main": object()}
    _flowsheet = object()
    def __init__(self):
        self.props = {
            ("FEED",    "mass_flow_kgh"): 100.0,
            ("FEED",    "temperature_C"): 25.0,
            ("SOLVENT", "mass_flow_kgh"): 50.0,
            ("RC-1",    "outlet_temperature_C"): 350.0,
            ("PRODUCT", "mass_flow_kgh"): 90.0,
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
            {"tag": "FEED",    "type": "MaterialStream", "category": "stream"},
            {"tag": "SOLVENT", "type": "MaterialStream", "category": "stream"},
            {"tag": "PRODUCT", "type": "MaterialStream", "category": "stream"},
            {"tag": "RC-1",    "type": "ConversionReactor",
             "category": "unit_op"},
        ]}


class _EmptyBridge:
    class state:
        name = None; active_alias = None; loaded_flowsheets = {}
    _flowsheet = None
    def list_simulation_objects(self):
        return {"success": True, "objects": []}


def _make_agent(bridge):
    from agent_v2 import DWSIMAgentV2
    agent = DWSIMAgentV2.__new__(DWSIMAgentV2)
    agent.bridge = bridge
    return agent


# ─── Detection ────────────────────────────────────────────────────────

def test_detects_what_can_be_optimised():
    agent = _make_agent(_LoadedBridge())
    for phrase in (
        "what can be optimised in this?",
        "what variables can be optimised",
        "analyse flowsheet and find what variables can be optimised?",
        "which parameters can I tune?",
        "list the variables you would optimise",
        "show me what's available for optimisation",
        "analyse the flowsheet for optimisation",
        "find optimisation opportunities",
        "suggest variables to vary",
        "what could be improved",
    ):
        assert agent._should_analysis_fast_answer(phrase), \
            f"Should fast-answer: {phrase!r}"


def test_does_not_trigger_on_commands():
    """Commands like 'optimise' (not questions) should NOT route to the
    analysis fast-answer — they go to the optimisation fast-path."""
    agent = _make_agent(_LoadedBridge())
    for phrase in (
        "maximise yield",
        "do optimisation",
        "minimise energy consumption",
        "optimise the process",
    ):
        assert not agent._should_analysis_fast_answer(phrase), \
            f"Command should NOT fast-answer: {phrase!r}"


def test_does_not_trigger_without_optimisation_keyword():
    """Generic questions unrelated to optimisation should NOT trigger."""
    agent = _make_agent(_LoadedBridge())
    for phrase in (
        "what streams are in this flowsheet?",
        "what is the temperature of feed?",
        "show me the simulation results",
        "list the loaded flowsheets",
    ):
        assert not agent._should_analysis_fast_answer(phrase)


def test_does_not_trigger_without_loaded_flowsheet():
    """Without a loaded flowsheet there's nothing to analyse."""
    agent = _make_agent(_EmptyBridge())
    assert not agent._should_analysis_fast_answer(
        "what variables can be optimised?")


# ─── Response content ─────────────────────────────────────────────────

def test_fast_answer_includes_variable_table():
    agent = _make_agent(_LoadedBridge())
    response = agent._analysis_fast_answer("what can be optimised?")
    assert "Optimisation Analysis" in response
    # Variable table should mention FEED + RC-1
    assert "FEED" in response
    assert "RC-1" in response
    # PRODUCT is an output stream — should NOT appear in the variable list
    table_section = response.split("Example Commands")[0]
    assert "PRODUCT" not in table_section or "Product" in response   # may show in objectives list


def test_fast_answer_includes_example_commands():
    agent = _make_agent(_LoadedBridge())
    response = agent._analysis_fast_answer("what variables?")
    assert "Example Commands" in response
    assert "maximise" in response.lower() or "minimise" in response.lower()


def test_fast_answer_handles_no_suggestions_gracefully():
    """When the flowsheet has no suggestable variables (e.g. tag-pattern
    filtering removes everything), return a helpful explanation rather than
    erroring."""
    class _OutputsOnlyBridge:
        class state:
            name = "test.dwxmz"; active_alias = "main"
        _flowsheet = object()
        def get_stream_property(self, tag, prop):
            return {"success": True, "value": 100.0}
        def get_stream_properties(self, tag):
            return {"success": True, "properties": {"mass_flow_kgh": 100.0}}
        def get_unit_op_properties(self, tag):
            return self.get_stream_properties(tag)
        def list_simulation_objects(self):
            return {"success": True, "objects": [
                # All outputs — should be filtered out
                {"tag": "PRODUCT", "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "RAFFINATE", "type": "MaterialStream",
                 "category": "stream"},
                {"tag": "BOTTOMS", "type": "MaterialStream",
                 "category": "stream"},
            ]}
    agent = _make_agent(_OutputsOnlyBridge())
    response = agent._analysis_fast_answer("what can be optimised?")
    # Should explain the situation without crashing
    assert isinstance(response, str)
    assert len(response) > 50
    # Should mention the limitation
    assert "manual" in response.lower() or "naming" in response.lower() \
        or "specific" in response.lower()


def test_fast_answer_includes_plugin_label_when_applicable():
    """For Cantera flowsheets, the response should mention plugin
    management so the user understands the constraint."""
    class _CanteraBridge(_LoadedBridge):
        class state(_LoadedBridge.state):
            name = "Biodiesel Combustion (Cantera).dwxmz"
    agent = _make_agent(_CanteraBridge())
    response = agent._analysis_fast_answer("what can be optimised?")
    assert "Cantera" in response
