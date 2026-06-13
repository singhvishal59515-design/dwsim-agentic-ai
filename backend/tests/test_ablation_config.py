"""
Phase-3 hardening tests: ablation-grade run configuration.

Verifies the determinism levers and replay-log tagging the ablation study
(Phase 4) depends on: a locked provider, temperature 0 on every attempt, and
condition/task/rep stamped onto every recorded turn — all driven by env vars
so the four conditions are configured identically with no code edits.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

_ENV_KEYS = ("DWSIM_ABLATION_CONDITION", "DWSIM_ABLATION_TASK",
             "DWSIM_ABLATION_REP", "DWSIM_DETERMINISTIC", "DWSIM_LOCK_PROVIDER")


def _clear(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


# ── ablation_config ──────────────────────────────────────────────────────────

def test_inactive_by_default(monkeypatch):
    _clear(monkeypatch)
    from ablation_config import ablation
    assert ablation.active is False
    assert ablation.condition is None
    assert ablation.deterministic is False
    assert ablation.lock_provider is False
    assert ablation.retry_temperatures((0.0, 0.15, 0.25)) == (0.0, 0.15, 0.25)
    assert ablation.tags() == {"condition": None, "task_id": None, "rep": None}


def test_condition_implies_deterministic_and_lock(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_CONDITION", "no_rag")
    monkeypatch.setenv("DWSIM_ABLATION_TASK", "C1-T01")
    monkeypatch.setenv("DWSIM_ABLATION_REP", "2")
    from ablation_config import ablation
    assert ablation.active is True
    assert ablation.deterministic is True   # implied
    assert ablation.lock_provider is True   # implied
    assert ablation.retry_temperatures((0.0, 0.15, 0.25)) == (0.0, 0.0, 0.0)
    assert ablation.tags() == {"condition": "no_rag", "task_id": "C1-T01", "rep": 2}


def test_deterministic_flag_without_condition(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_DETERMINISTIC", "1")
    from ablation_config import ablation
    assert ablation.active is False        # no condition set
    assert ablation.deterministic is True  # explicit flag
    assert ablation.retry_temperatures((0.0, 0.15, 0.25)) == (0.0, 0.0, 0.0)


def test_lock_provider_flag_without_condition(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_LOCK_PROVIDER", "true")
    from ablation_config import ablation
    assert ablation.lock_provider is True
    assert ablation.deterministic is False  # lock alone does not force temp 0


def test_bad_rep_is_none(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_REP", "notanumber")
    from ablation_config import ablation
    assert ablation.rep is None


# ── condition toggles ────────────────────────────────────────────────────────

def test_full_condition_disables_nothing(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_CONDITION", "full")
    from ablation_config import ablation
    assert (ablation.disable_rag, ablation.disable_safety,
            ablation.disable_tools, ablation.direct_llm) == (False, False, False, False)


def test_no_rag_only_disables_rag(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_CONDITION", "no_rag")
    from ablation_config import ablation
    assert ablation.disable_rag is True
    assert ablation.disable_safety is False
    assert ablation.disable_tools is False


def test_no_safety_only_disables_safety(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_CONDITION", "no_safety")
    from ablation_config import ablation
    assert ablation.disable_safety is True
    assert ablation.disable_rag is False
    assert ablation.disable_tools is False


def test_direct_llm_disables_everything(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DWSIM_ABLATION_CONDITION", "direct_llm")
    from ablation_config import ablation
    assert ablation.direct_llm is True
    assert ablation.disable_rag is True
    assert ablation.disable_safety is True
    assert ablation.disable_tools is True


# ── replay_log tagging ───────────────────────────────────────────────────────

def _build_turn(**kw):
    from replay_log import TurnBuilder
    b = TurnBuilder(session_id="s", turn_index=0, provider="anthropic",
                    model="claude-x", temperature=0.0, seed=42, **kw)
    b.set_prompt("hello", "SYSTEM")
    b.record_tool_call("list_objects", {}, {"success": True}, 12.0)
    return b.finish("done", True, {}, [])


def test_replay_turn_carries_ablation_tags():
    turn = _build_turn(condition="full", task_id="C2-T03", rep=1)
    assert turn.condition == "full"
    assert turn.task_id == "C2-T03"
    assert turn.rep == 1
    d = turn.to_dict()
    assert d["condition"] == "full" and d["task_id"] == "C2-T03" and d["rep"] == 1


def test_replay_turn_defaults_when_untagged():
    turn = _build_turn()
    assert turn.condition is None and turn.task_id is None and turn.rep is None


def test_replay_from_dict_back_compat_without_tag_keys():
    # A pre-Phase-3 log line has no condition/task_id/rep keys; it must still load.
    from replay_log import ReplayTurn
    d = _build_turn(condition="x", task_id="y", rep=3).to_dict()
    for k in ("condition", "task_id", "rep"):
        d.pop(k, None)
    turn = ReplayTurn.from_dict(d)
    assert turn.condition is None and turn.task_id is None and turn.rep is None
