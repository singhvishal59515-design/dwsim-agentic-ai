"""
Tests for the ICL ablation (Tian et al. Table 4: w/o CoT, w/o Few-Shot).

The few-shot worked examples and the chain-of-thought "structured reasoning"
block are wrapped in <<ICL:…>> markers in the system prompt; the no_fewshot /
no_cot ablation conditions strip them, while the bare markers are always removed
so a normal prompt is unchanged. Pure string + config logic — no LLM, no DWSIM.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

_FEWSHOT = "EXAMPLE — Water heater"
_COT = "STRUCTURED REASONING FOR PROCESS ENGINEERING"


def test_raw_prompt_carries_markers_and_both_blocks():
    import agent_v2 as A
    assert "<<ICL:FEWSHOT>>" in A.BASE_SYSTEM_PROMPT
    assert "<<ICL:COT>>" in A.BASE_SYSTEM_PROMPT
    assert _FEWSHOT in A.BASE_SYSTEM_PROMPT
    assert _COT in A.BASE_SYSTEM_PROMPT


def test_default_strips_markers_keeps_content():
    import agent_v2 as A
    full = A._strip_icl(A.BASE_SYSTEM_PROMPT)
    assert "<<ICL" not in full          # bare markers never leak into a live prompt
    assert _FEWSHOT in full             # content intact in normal operation
    assert _COT in full


def test_no_fewshot_removes_only_examples():
    import agent_v2 as A
    p = A._strip_icl(A.BASE_SYSTEM_PROMPT, disable_fewshot=True)
    assert "<<ICL" not in p
    assert _FEWSHOT not in p            # few-shot worked example removed
    assert _COT in p                    # CoT reasoning kept


def test_no_cot_removes_only_reasoning():
    import agent_v2 as A
    p = A._strip_icl(A.BASE_SYSTEM_PROMPT, disable_cot=True)
    assert "<<ICL" not in p
    assert _COT not in p                # CoT reasoning removed
    assert _FEWSHOT in p                # few-shot kept


def test_config_toggles_keyed_to_conditions():
    from ablation_config import _Ablation
    saved = os.environ.get("DWSIM_ABLATION_CONDITION")
    try:
        for cond, cot, fs in [("no_cot", True, False),
                              ("no_fewshot", False, True),
                              ("full", False, False),
                              ("no_rag", False, False)]:
            os.environ["DWSIM_ABLATION_CONDITION"] = cond
            a = _Ablation()
            assert a.disable_cot is cot, cond
            assert a.disable_fewshot is fs, cond
        os.environ.pop("DWSIM_ABLATION_CONDITION", None)
        a = _Ablation()
        assert a.disable_cot is False and a.disable_fewshot is False
    finally:
        if saved is not None:
            os.environ["DWSIM_ABLATION_CONDITION"] = saved
        else:
            os.environ.pop("DWSIM_ABLATION_CONDITION", None)


def test_runner_recognises_icl_conditions():
    from ablation_runner import CONDITION_MAP, CONDITION_NAMES
    assert CONDITION_MAP["E"] == "no_cot"
    assert CONDITION_MAP["F"] == "no_fewshot"
    assert "No-CoT" in CONDITION_NAMES.values()
    assert "No-Few-Shot" in CONDITION_NAMES.values()
