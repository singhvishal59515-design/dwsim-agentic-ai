"""
Regression: cross-turn LLM history-format mixing.

When a turn fails over to another provider, the assistant/tool messages it
appends to `self._history` are in that provider's format. The next turn resets
to the primary provider and feeds the SAME history back — previously a foreign
(e.g. Anthropic content-block) history was handed verbatim to an OpenAI-family
SDK, which rejects it. `LLMClient.normalize_history` now converts a foreign
history to the active provider's format (flattening non-portable tool linkage to
plain text) and is a no-op when the history is already native.

Also covers the failover budget fix: per-minute rate-limit waits get a fresh
retry count per provider instead of sharing one global attempt budget, so a
single throttled provider can no longer starve the rest of the chain.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import llm_client as L
from llm_client import LLMClient


class _Block:
    """Mimics an Anthropic SDK content block (object, not dict)."""
    def __init__(self, **k):
        self.__dict__.update(k)


def _client(provider):
    c = LLMClient.__new__(LLMClient)
    c.provider = provider
    c.model = "m"
    return c


def test_anthropic_history_normalized_for_openai_family():
    hist = [
        {"role": "user", "content": "build a flowsheet"},
        {"role": "assistant", "content": [
            _Block(type="text", text="Sure."),
            _Block(type="tool_use", name="add_object", input={"kind": "Heater"})]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "ok"}]},
        {"role": "assistant", "content": [_Block(type="text", text="Done.")]},
    ]
    out = _client("groq").normalize_history(hist)
    # Everything is plain-string content the OpenAI SDK accepts.
    assert all(isinstance(m["content"], str) for m in out)
    # No foreign keys leaked through.
    assert all("tool_calls" not in m and "parts" not in m for m in out)
    # Tool call/result context is preserved as readable text.
    joined = " ".join(m["content"] for m in out)
    assert "add_object" in joined and "ok" in joined


def test_openai_history_normalized_for_anthropic_alternates():
    hist = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "calling",
         "tool_calls": [{"id": "1", "type": "function",
                         "function": {"name": "run", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "name": "run", "content": "42"},
        {"role": "assistant", "content": "result is 42"},
    ]
    out = _client("anthropic").normalize_history(hist)
    roles = [m["role"] for m in out]
    # Anthropic requires strict user/assistant alternation starting with user.
    assert roles[0] == "user"
    assert all(roles[i] != roles[i + 1] for i in range(len(roles) - 1)), roles
    assert "42" in " ".join(m["content"] for m in out)


def test_native_history_returned_unchanged():
    same = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    # Plain-text universal history must be passed through by identity (no churn).
    assert _client("openai").normalize_history(same) is same


def test_rate_limit_retries_do_not_starve_failover(monkeypatch):
    """A provider stuck on per-minute rate limits must still let the chain reach
    every other provider — each provider gets its own retry budget."""
    monkeypatch.setattr(L.time, "sleep", lambda *_a, **_k: None)
    c = LLMClient.__new__(LLMClient)
    c.provider, c.model = "groq", "m"
    c._allow_provider_switch = True

    def _rate_limited(*_a, **_k):
        raise Exception("429 rate_limit_exceeded per-minute")

    c._chat_groq = _rate_limited
    c._chat_openai = _rate_limited
    c._chat_anthropic = _rate_limited
    c._try_next_groq_model = lambda: False

    visited = []
    chain = iter(["openai", "anthropic"])

    def _switch(reason=""):
        try:
            c.provider = next(chain)
            visited.append(c.provider)
            return True
        except StopIteration:
            return False

    c._switch_provider = _switch
    result = c.chat([{"role": "user", "content": "hi"}], [], "")
    assert result is None                      # all providers genuinely exhausted
    assert visited == ["openai", "anthropic"]  # but the chain was fully traversed
