"""
Regression: _sanitize_for_llm must convert non-finite floats (NaN / ±Inf) to
None so tool results survive json.dumps(allow_nan=False) — which the LLM APIs
require. DWSIM returns NaN/Inf for unconverged/degenerate streams; the old code
let them through and the encoder raised "Out of range float values are not JSON
compliant", aborting the whole turn. This was the single most frequent runtime
crash in the agent logs (48×).
"""
from __future__ import annotations
import json
import math
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from agent_v2 import _sanitize_for_llm


def test_nan_inf_become_none():
    out = _sanitize_for_llm({"T": float("nan"), "P": float("inf"),
                             "dp": float("-inf"), "flow": 3600.0})
    assert out["T"] is None and out["P"] is None and out["dp"] is None
    assert out["flow"] == 3600.0


def test_result_is_json_serialisable_with_allow_nan_false():
    data = {"streams": {"Feed": {"T": float("nan"), "P": 1e5},
                        "Prod": {"vf": float("inf")}},
            "history": [float("nan"), 1.0, float("-inf")]}
    clean = _sanitize_for_llm(data)
    # The crash was here — must NOT raise.
    s = json.dumps(clean, allow_nan=False)
    assert "NaN" not in s and "Infinity" not in s


def test_booleans_preserved_not_coerced():
    out = _sanitize_for_llm({"ok": True, "bad": False, "n": 1.5})
    assert out["ok"] is True and out["bad"] is False and out["n"] == 1.5


def test_nested_lists_and_tuples_sanitised():
    out = _sanitize_for_llm([1.0, float("nan"), (float("inf"), {"x": float("nan")})])
    assert out[1] is None
    assert out[2][0] is None and out[2][1]["x"] is None


def test_finite_floats_untouched():
    out = _sanitize_for_llm({"a": 0.0, "b": -273.15, "c": 1.23e10})
    assert out == {"a": 0.0, "b": -273.15, "c": 1.23e10}
