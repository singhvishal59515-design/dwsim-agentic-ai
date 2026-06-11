"""
Tests for the optimisation ambiguity gate: vague goals must be flagged for
clarification (ask, don't guess); specific goals must pass through.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from optimization_orchestrator import _optimization_goal_clarity as clarity


def test_vague_goals_need_clarification():
    for g in ["analyse loaded flowsheet",      # the real failure case
              "analyse the flowsheet",
              "optimise the flowsheet",         # direction but no target
              "optimize it",
              "make it better",
              "improve the process",
              "what is in this flowsheet?",
              ""]:
        assert clarity(g)["clear"] is False, f"should ask for clarification: {g!r}"


def test_specific_goals_are_clear():
    for g in ["minimise heater duty",
              "maximise H2 purity",
              "reduce total energy consumption",
              "maximise the product yield",
              "minimise operating cost",
              "minimise Products.mass_flow_kgh",   # explicit tag.property
              "increase the reboiler reflux"]:
        assert clarity(g)["clear"] is True, f"should proceed: {g!r}"


def test_analysis_with_optimisation_intent_is_clear():
    # 'analyse' + a clear optimisation direction+target → still actionable
    assert clarity("analyse and minimise the heater energy duty")["clear"] is True
