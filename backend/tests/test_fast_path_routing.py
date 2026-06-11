"""
Regression: the optimisation fast-path matcher must NOT fire on flowsheet
build/create requests just because a (plugin) flowsheet is already loaded.

Bug: with a Cantera flowsheet loaded, "Create a water heating process from
20C to 80C ..." was routed to the optimisation orchestrator (goal-clarity gate
asked "what would you like to optimise?") instead of building the flowsheet.
Root cause: the flowsheet-presence check returned True for plugin flowsheets
BEFORE the optimisation-intent check ran.
"""
from __future__ import annotations
import os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import agent_v2


class _FakeState:
    def __init__(self, name="", streams=None, unit_ops=None):
        self.name = name
        self.active_alias = name
        self.loaded_flowsheets = {name: 1} if name else {}
        self.streams = streams or []
        self.unit_ops = unit_ops or []


class _FakeBridge:
    def __init__(self, st):
        self.state = st
        self._flowsheet = object() if st.name else None


def _matcher(msg, name="", streams=("s1",), uops=("u1",)):
    a = agent_v2.DWSIMAgentV2.__new__(agent_v2.DWSIMAgentV2)
    a.bridge = _FakeBridge(_FakeState(name, list(streams), list(uops)))
    a._log = lambda *x, **k: None
    return a._should_fast_path_optimization(msg)


CANTERA = "Biodiesel Combustion (Cantera)"


def test_build_request_not_fastpathed_on_plugin_flowsheet():
    # The exact reported bug.
    assert _matcher(
        "Create a water heating process from 20C to 80C at 1 atm, 1 kg/s, "
        "using Steam Tables", CANTERA) is False


def test_other_build_verbs_not_fastpathed():
    for m in ["build a distillation column to separate ethanol and water",
              "design a reactor flowsheet",
              "make a heat exchanger network",
              "construct a flash separation process",
              "simulate a CSTR with a recycle"]:
        assert _matcher(m, CANTERA) is False, m


def test_genuine_optimisation_still_fastpaths():
    for m in ["do optimisation", "minimise heater duty", "maximise H2 purity",
              "optimise the flowsheet", "reduce total energy consumption"]:
        assert _matcher(m, CANTERA) is True, m


def test_questions_not_fastpathed():
    for m in ["what is in this flowsheet?", "what can I optimise here?",
              "how does optimisation work?"]:
        assert _matcher(m, CANTERA) is False, m
