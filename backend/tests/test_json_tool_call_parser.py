"""
Tests for _parse_json_tool_call_content — recovers tool calls when the LLM
(Groq Llama-4 / Hermes-style fine-tunes) emits them as JSON text in the
content field instead of using the OpenAI tool-call API.
"""

from __future__ import annotations
import os
import sys

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_parser_extracts_simple_json_tool_call():
    from llm_client import _parse_json_tool_call_content
    content = ('To optimize, I will use the tool.\n'
               '{"type": "function", "name": "optimize_flowsheet_with_llm", '
               '"parameters": {"goal": "maximize hydrogen production"}}')
    out = _parse_json_tool_call_content(content)
    assert out is not None
    assert len(out["tool_calls"]) == 1
    tc = out["tool_calls"][0]
    assert tc["name"] == "optimize_flowsheet_with_llm"
    assert tc["arguments"] == {"goal": "maximize hydrogen production"}


def test_parser_unwraps_schema_style_parameters():
    """Some models wrap each param value in {"type":"X", "value":Y}.
    Parser must unwrap those into plain kwargs."""
    from llm_client import _parse_json_tool_call_content
    content = ('{"type": "function", "name": "optimize_flowsheet_with_llm", '
               '"parameters": {"goal": {"type": "string", '
               '"value": "maximize hydrogen production"}}}')
    out = _parse_json_tool_call_content(content)
    assert out is not None
    tc = out["tool_calls"][0]
    assert tc["arguments"] == {"goal": "maximize hydrogen production"}


def test_parser_handles_arguments_alias():
    """Some emitters use "arguments" instead of "parameters"."""
    from llm_client import _parse_json_tool_call_content
    content = ('{"type": "function", "name": "load_flowsheet", '
               '"arguments": {"path": "C:/test.dwxmz"}}')
    out = _parse_json_tool_call_content(content)
    assert out is not None
    assert out["tool_calls"][0]["name"] == "load_flowsheet"
    assert out["tool_calls"][0]["arguments"] == {"path": "C:/test.dwxmz"}


def test_parser_returns_none_for_non_tool_content():
    """Regular chat content with no tool-call JSON must NOT trigger recovery."""
    from llm_client import _parse_json_tool_call_content
    assert _parse_json_tool_call_content(
        "Hello, here is some text with {\"key\": \"value\"} embedded.") is None
    assert _parse_json_tool_call_content("plain text response") is None
    assert _parse_json_tool_call_content("") is None


def test_parser_extracts_multiple_tool_calls():
    from llm_client import _parse_json_tool_call_content
    content = ('First I will check.\n'
               '{"type":"function","name":"check_convergence","parameters":{}}'
               '\nThen optimise.\n'
               '{"type":"function","name":"optimize_flowsheet_with_llm",'
               '"parameters":{"goal":"max H2"}}')
    out = _parse_json_tool_call_content(content)
    assert out is not None
    assert len(out["tool_calls"]) == 2
    names = [tc["name"] for tc in out["tool_calls"]]
    assert names == ["check_convergence", "optimize_flowsheet_with_llm"]


def test_parser_strips_consumed_json_from_clean_content():
    """The natural-language preamble survives in `content`; the JSON is removed."""
    from llm_client import _parse_json_tool_call_content
    content = ('I will use the tool to optimize.\n'
               '{"type":"function","name":"optimize_flowsheet_with_llm",'
               '"parameters":{"goal":"max H2"}}')
    out = _parse_json_tool_call_content(content)
    assert out is not None
    assert "I will use the tool" in out["content"]
    assert "type" not in out["content"] or '"function"' not in out["content"]


def test_unwrap_schema_params_handles_nested():
    from llm_client import _unwrap_schema_params
    # Nested schema wrapping
    inp = {
        "variables": {
            "type": "array",
            "value": [
                {"type": "object", "value": {
                    "tag": {"type": "string", "value": "RC-01"},
                    "lower": {"type": "number", "value": 580.0},
                }},
            ],
        },
        "method": {"type": "string", "value": "simplex"},
    }
    out = _unwrap_schema_params(inp)
    assert out == {
        "variables": [{"tag": "RC-01", "lower": 580.0}],
        "method": "simplex",
    }


def test_unwrap_schema_params_passes_through_plain_kwargs():
    """Plain kwargs (no schema wrapping) must be returned unchanged."""
    from llm_client import _unwrap_schema_params
    inp = {"goal": "max H2", "max_iter": 50}
    assert _unwrap_schema_params(inp) == inp
