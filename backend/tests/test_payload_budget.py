"""
Per-call LLM payload budget — regression guard.

The agent sends the system prompt + tool schemas on EVERY iteration of EVERY
turn. Measured 2026-06-11: system prompt ~8.0k tok, full 105-tool schema ~21.2k
tok, a typical analyze-phase call ~22.5k tok. That ~21k payload is what
rate-limited the live benchmark (groq free-tier 413 at 12k TPM; anthropic
throttled on sustained use), leaving 9/25 tasks unrun.

These budgets are intentionally set just ABOVE the current measured values so
the suite FAILS if a new feature silently regrows the payload — and so that any
deliberate reduction work can ratchet them DOWN as it lands (lower the number
in the same commit that shrinks the prompt/tools). Token estimate is chars/4
(provider-agnostic, matches how the payload was sized during the live runs).
"""
from __future__ import annotations
import json
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def _tok(s: str) -> int:
    return len(s) // 4


# Current measured baselines (2026-06-11) + a small margin. Ratchet DOWN when a
# reduction lands; this test then proves the saving and prevents regression.
_SYSTEM_PROMPT_BUDGET_TOK = 8300       # measured ~8007
_ALL_TOOLS_BUDGET_TOK     = 22000      # measured ~21230
_FATTEST_TOOL_BUDGET_TOK  = 1000       # measured ~878 (build_flowsheet_atomic)


def test_system_prompt_within_budget():
    from agent_v2 import BASE_SYSTEM_PROMPT
    t = _tok(BASE_SYSTEM_PROMPT)
    assert t <= _SYSTEM_PROMPT_BUDGET_TOK, (
        f"system prompt grew to ~{t} tok (budget {_SYSTEM_PROMPT_BUDGET_TOK}). "
        f"It is sent every iteration — trim it or raise the budget deliberately.")


def test_full_tool_schema_within_budget():
    from tools_schema_v2 import DWSIM_TOOLS
    t = _tok(json.dumps(DWSIM_TOOLS))
    assert t <= _ALL_TOOLS_BUDGET_TOK, (
        f"full tool schema grew to ~{t} tok (budget {_ALL_TOOLS_BUDGET_TOK}).")


def test_no_single_tool_schema_is_oversized():
    from tools_schema_v2 import DWSIM_TOOLS
    fat = [(_tok(json.dumps(t)), t.get("name")) for t in DWSIM_TOOLS]
    fat = [(n, name) for n, name in fat if n > _FATTEST_TOOL_BUDGET_TOK]
    assert not fat, (
        f"tool schema(s) over {_FATTEST_TOOL_BUDGET_TOK} tok: {fat}. "
        f"Move usage examples into the knowledge base; keep descriptions to "
        f"WHEN-to-use disambiguation.")
