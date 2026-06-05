"""
Regression test: the user's SELECTED LLM provider must not silently/permanently
switch to another provider on a transient failure. The agent disables in-client
cross-provider switching on its primary client (_allow_provider_switch=False) and
handles failover itself transiently, so /llm/status keeps reporting the chosen
provider. (Bug: a transient Anthropic rate-limit flipped the UI to Gemini.)
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_selected_client_refuses_provider_switch():
    from llm_client import LLMClient
    c = LLMClient(provider="anthropic", api_key="dummy-key")
    c._allow_provider_switch = False
    assert c._switch_provider("simulated rate limit") is False
    assert c.provider == "anthropic"        # provider unchanged → no drift


def test_default_client_allows_switch_flag_present():
    from llm_client import LLMClient
    c = LLMClient(provider="anthropic", api_key="dummy-key")
    # Standalone clients keep their internal failover (flag defaults True).
    assert c._allow_provider_switch is True


def test_agent_disables_switch_on_primary_client():
    from unittest.mock import MagicMock
    from llm_client import LLMClient
    import agent_v2

    c = LLMClient(provider="anthropic", api_key="dummy-key")
    # MagicMock auto-provides any bridge attribute the constructor references,
    # so we can build the agent without initialising real DWSIM.
    a = agent_v2.DWSIMAgentV2(llm=c, bridge=MagicMock())
    assert a.llm._allow_provider_switch is False
