"""
Covers the new DWSIMAgentV2.chat_stream() SSE generator: it must reuse the real
chat() loop, relay live on_token text as `token` events and on_tool_call as
structured `tool_call` events, terminate with a `done` event carrying the full
answer, restore the original sinks afterwards, and surface exceptions as an
`error` event instead of propagating.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from agent_v2 import DWSIMAgentV2


def _bare_agent():
    a = DWSIMAgentV2.__new__(DWSIMAgentV2)
    a.on_token = None
    a.on_tool_call = None
    a.stream_output = False
    a.verbose = False
    return a


def test_chat_stream_relays_tokens_tools_and_done():
    a = _bare_agent()

    def fake_chat(msg):
        assert a.on_token and a.on_tool_call          # sinks installed
        a.on_token("Hello ")
        a.on_tool_call("run_simulation", {"x": 1}, {"success": True})
        a.on_token("world")
        return "Hello world"

    a.chat = fake_chat
    events = list(a.chat_stream("hi"))

    types = [e["type"] for e in events]
    assert types == ["token", "tool_call", "token", "done"], types
    assert events[1]["data"]["name"] == "run_simulation"
    assert events[1]["data"]["result"] == {"success": True}
    assert events[-1]["data"] == "Hello world"
    # Sinks restored to their pre-call values.
    assert a.on_token is None and a.on_tool_call is None
    assert a.stream_output is False and a.verbose is False


def test_chat_stream_surfaces_errors():
    a = _bare_agent()

    def boom(msg):
        raise RuntimeError("kaboom")

    a.chat = boom
    events = list(a.chat_stream("hi"))
    assert events[-1]["type"] == "error"
    assert "kaboom" in events[-1]["data"]
    assert a.on_token is None   # restored even on failure
