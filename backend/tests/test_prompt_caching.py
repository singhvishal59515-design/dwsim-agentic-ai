"""
Anthropic prompt caching: the large stable system-prompt prefix and the tool
definitions must be sent with cache_control so they are billed/processed once
per ~5-min window instead of on every loop iteration. The CACHE_BREAKPOINT
marker that drives the split must never leak into any other provider's prompt.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from llm_client import LLMClient, CACHE_BREAKPOINT


class _FakeUsage:
    input_tokens = 100
    output_tokens = 10
    cache_creation_input_tokens = 80
    cache_read_input_tokens = 0


class _FakeBlock:
    type = "text"
    text = "hi"


class _FakeResp:
    content = [_FakeBlock()]
    stop_reason = "end_turn"
    usage = _FakeUsage()


class _CapturingClient:
    def __init__(self):
        self.captured = None

    class _Messages:
        def __init__(self, outer): self.outer = outer
        def create(self, **kwargs):
            self.outer.captured = kwargs
            return _FakeResp()

    @property
    def messages(self):
        return _CapturingClient._Messages(self)


def _anthropic_client():
    c = LLMClient.__new__(LLMClient)
    c.provider = "anthropic"
    c.model = "claude-sonnet-4-5"
    c.temperature = 0.0
    c._client = _CapturingClient()
    return c


def test_anthropic_caches_system_prefix_and_last_tool():
    c = _anthropic_client()
    system = "STABLE INSTRUCTIONS" + CACHE_BREAKPOINT + "dynamic flowsheet state"
    tools = [{"name": "a", "description": "", "parameters": {}},
             {"name": "b", "description": "", "parameters": {}}]
    c._chat_anthropic([{"role": "user", "content": "x"}], tools, system)
    cap = c._client.captured

    # System split into a cached stable block + an uncached dynamic block.
    sys_blocks = cap["system"]
    assert isinstance(sys_blocks, list) and len(sys_blocks) == 2
    assert sys_blocks[0]["text"] == "STABLE INSTRUCTIONS"
    assert sys_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in sys_blocks[1]
    assert sys_blocks[1]["text"] == "dynamic flowsheet state"
    # The marker itself must never reach the API.
    assert CACHE_BREAKPOINT not in sys_blocks[0]["text"]
    assert CACHE_BREAKPOINT not in sys_blocks[1]["text"]

    # Only the LAST tool carries the cache breakpoint.
    api_tools = cap["tools"]
    assert "cache_control" not in api_tools[0]
    assert api_tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_without_marker_uses_plain_string_system():
    c = _anthropic_client()
    c._chat_anthropic([{"role": "user", "content": "x"}], [], "plain system")
    assert c._client.captured["system"] == "plain system"


def test_marker_stripped_for_non_anthropic():
    # The chat() dispatch must strip the marker before a non-Anthropic provider
    # ever sees the system prompt.
    c = LLMClient.__new__(LLMClient)
    c.provider = "groq"
    c.model = "m"

    seen = {}

    def fake_groq(messages, tools, system_prompt):
        seen["system"] = system_prompt
        return {"content": "ok", "tool_calls": [], "stop_reason": "stop", "_raw": None}

    c._chat_groq = fake_groq
    c.normalize_history = lambda m: m
    sysprompt = "STABLE" + CACHE_BREAKPOINT + "dynamic"
    c.chat([{"role": "user", "content": "x"}], [], sysprompt)
    assert CACHE_BREAKPOINT not in seen["system"]
    assert seen["system"] == "STABLEdynamic"   # exact concat, no leak, no extra ws
