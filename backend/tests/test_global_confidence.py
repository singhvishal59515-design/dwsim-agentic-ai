"""
Tests for the global-optimality confidence assessment
(optimization_orchestrator._assess_global_confidence).

You can't PROVE a black-box nonconvex optimum is global, but diverse restarts
give EVIDENCE: agreement → high confidence; a better restart → the first optimum
wasn't global (adopt the better one); scatter → multimodal / low confidence.
Uses a mock optimizer (no live DWSIM).
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import optimization_orchestrator as oo

_SPEC = {"variables": [{"tag": "x", "property": "v",
                        "lower": 0, "upper": 10, "initial": 5}],
         "objective": {"type": "variable", "tag": "OBJ", "property": "value"},
         "minimize": True}


def _noemit(*a, **k):
    pass


def test_high_confidence_when_all_restarts_agree(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_PROBES", "3")
    block, adopt = oo._assess_global_confidence(
        lambda spec: {"success": True, "best_objective": 10.0},
        _SPEC, {"best_objective": 10.0, "duration_s": 0.05},
        minimize=True, emit=_noemit)
    assert block["assessed"] and block["confidence"] == "high"
    assert block["n_agree"] == block["n_probes"]
    assert adopt is None


def test_adopts_a_better_optimum(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_PROBES", "3")
    calls = {"n": 0}

    def run_fn(spec):
        calls["n"] += 1
        # first probe finds a strictly better (lower) optimum
        return {"success": True, "best_objective": 5.0 if calls["n"] == 1 else 10.0}

    block, adopt = oo._assess_global_confidence(
        run_fn, _SPEC, {"best_objective": 10.0, "duration_s": 0.05},
        minimize=True, emit=_noemit)
    assert block["improved"] is True and block["confidence"] == "low"
    assert adopt is not None and adopt["best_objective"] == 5.0


def test_low_confidence_when_scattered(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_PROBES", "3")
    seq = iter([8.0, 9.5, 7.0])  # all WORSE than reported 5.0, none agree

    block, adopt = oo._assess_global_confidence(
        lambda spec: {"success": True, "best_objective": next(seq)},
        _SPEC, {"best_objective": 5.0, "duration_s": 0.05},
        minimize=True, emit=_noemit)
    assert block["confidence"] == "low"
    assert block["improved"] is False and adopt is None


def test_skipped_when_too_expensive(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_PROBES", "3")
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_MAX_S", "10")
    block, adopt = oo._assess_global_confidence(
        lambda spec: {"success": True, "best_objective": 1.0},
        _SPEC, {"best_objective": 1.0, "duration_s": 20.0},  # 20s × 3 > 10s
        minimize=True, emit=_noemit)
    assert block["assessed"] is False and "expensive" in block["reason"]
    assert adopt is None


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE", "0")
    block, adopt = oo._assess_global_confidence(
        lambda spec: {"success": True, "best_objective": 1.0},
        _SPEC, {"best_objective": 1.0}, minimize=True, emit=_noemit)
    assert block["assessed"] is False and adopt is None


def test_maximise_adopts_higher(monkeypatch):
    monkeypatch.setenv("OPT_GLOBAL_CONFIDENCE_PROBES", "2")
    calls = {"n": 0}

    def run_fn(spec):
        calls["n"] += 1
        return {"success": True, "best_objective": 99.0 if calls["n"] == 1 else 50.0}

    block, adopt = oo._assess_global_confidence(
        run_fn, _SPEC, {"best_objective": 50.0, "duration_s": 0.05},
        minimize=False, emit=_noemit)
    assert block["improved"] is True and adopt["best_objective"] == 99.0
