"""
Regression: after a transient failover, the response must be parsed by the
client that PRODUCED it (matching provider format), not the primary client.
Bug observed: primary=Anthropic, response from Gemini fallback →
assistant_turn did response['_raw'].content on a GenerateContentResponse →
'GenerateContentResponse' object has no attribute 'content'.
"""
from __future__ import annotations
import os, sys
import pytest
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


class _RawNoContent:
    """Mimics Gemini's GenerateContentResponse — has no `.content` attribute."""
    pass


_GEMINI_RESPONSE = {"content": "hello", "tool_calls": [],
                    "stop_reason": "stop", "_raw": _RawNoContent()}


def test_primary_client_crashes_on_foreign_response():
    """An Anthropic client parsing a Gemini response reproduces the bug."""
    from llm_client import LLMClient
    anth = LLMClient(provider="anthropic", api_key="dummy")
    with pytest.raises(AttributeError):
        anth.assistant_turn(_GEMINI_RESPONSE)   # _raw.content → AttributeError


def test_producing_client_parses_its_own_response():
    """The Gemini-format branch parses the same response without touching _raw."""
    from llm_client import LLMClient
    c = LLMClient(provider="anthropic", api_key="dummy")
    c.provider = "gemini"                         # select the gemini branch
    turn = c.assistant_turn(_GEMINI_RESPONSE)     # must NOT raise
    assert isinstance(turn, dict)
    assert turn.get("role") in ("model", "assistant")


def test_agent_tracks_response_client():
    """The agent exposes _response_client (the producing client) and defaults
    it to the primary."""
    from unittest.mock import MagicMock
    from llm_client import LLMClient
    import agent_v2
    llm = LLMClient(provider="anthropic", api_key="dummy")
    a = agent_v2.DWSIMAgentV2(llm=llm, bridge=MagicMock())
    assert a._response_client is llm
    assert a._turn_client is None        # no failover has happened yet


def test_sticky_turn_client_prefers_failover_after_switch():
    """After a failover, the next iteration in the SAME turn must reuse the
    failover client (so history stays in one provider's format), not retry the
    selected provider with now-foreign history."""
    from unittest.mock import MagicMock
    from llm_client import LLMClient
    import agent_v2
    primary = LLMClient(provider="anthropic", api_key="dummy")
    fb = LLMClient(provider="openai", api_key="dummy")
    a = agent_v2.DWSIMAgentV2(llm=primary, bridge=MagicMock())

    # Primary fails, fallback succeeds → _try_one_provider returns None for the
    # primary client and a response for the fallback.
    _GOOD = {"content": "ok", "tool_calls": [], "stop_reason": "stop", "_raw": _RawNoContent()}

    def _try(messages, tools, system_prompt, client, t0):
        return None if client is primary else _GOOD

    a._try_one_provider = _try                      # type: ignore
    a._build_fallback_client = lambda prov: fb if prov == "openai" else None  # type: ignore

    resp = a._llm_chat_with_retry(messages=[], tools=[], system_prompt="")
    assert resp is _GOOD
    assert a._turn_client is fb                      # stuck to the fallback
    assert a._response_client is fb

    # Next iteration of the SAME turn: primary_client is now the sticky fb, so
    # the fallback is tried FIRST (and answers) — primary is never re-tried.
    tried = []
    def _try2(messages, tools, system_prompt, client, t0):
        tried.append(client); return _GOOD
    a._try_one_provider = _try2                      # type: ignore
    resp2 = a._llm_chat_with_retry(messages=[], tools=[], system_prompt="")
    assert tried == [fb]                             # primary not retried
    assert a._turn_client is fb
