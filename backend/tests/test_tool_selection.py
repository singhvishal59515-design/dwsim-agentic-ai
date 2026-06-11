"""
Tests for DWSIMAgentV2._active_tool_names — the per-turn tool-payload selection.

Motivation: every analyse/report turn was sending all 105 tool schemas
(~21k tokens; ~16.5k in the analyse phase), dominated by the heavy specialised
optimisers (bayesian, monte-carlo, EO, multiobjective, …). That bloat slowed and
inflated every LLM call and made low-TPM fallback providers (groq, 12k TPM)
return 413 'request too large'. The heavy optimisers are now GATED behind
optimisation-intent keywords.

The safety contract verified here: gating must never strand an optimisation
request. The two routers stay always-available in the analyse phase (they
dispatch to the full optimiser stack internally), and explicit optimisation
phrasings restore the specialised tools. Pure logic — no bridge/LLM.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from agent_v2 import DWSIMAgentV2 as Ag

_ROUTERS = {"optimize_flowsheet_with_llm", "dwsim_optimize"}
_HEAVY = {"optimize_multivar", "optimize_constrained", "optimize_multiobjective",
          "global_sensitivity", "optimize_eo", "bayesian_optimize",
          "monte_carlo_study", "dwsim_internal_optimize"}


def test_heavy_optimisers_gated_out_of_plain_analysis():
    names = Ag._active_tool_names("analyze", "report all stream temperatures")
    assert not (_HEAVY & names), "heavy optimisers must not load for a plain report"


def test_router_always_present_in_analyse_phase():
    # Even with zero optimisation intent, the entry point is available so the
    # agent can always start an optimisation (router dispatches internally).
    for msg in ("report results", "what is the outlet temperature",
                "summarise the flowsheet"):
        names = Ag._active_tool_names("analyze", msg)
        assert _ROUTERS & names, msg


def test_explicit_optimise_unlocks_full_toolkit():
    for msg in ("optimize the flowsheet", "minimize the reboiler duty",
                "maximise product purity"):
        names = Ag._active_tool_names("analyze", msg)
        assert _HEAVY <= names, f"{msg} must unlock all specialised optimisers"


def test_specific_intents_unlock_their_tool():
    assert "bayesian_optimize" in Ag._active_tool_names("analyze",
                                                        "run a bayesian optimization")
    assert "monte_carlo_study" in Ag._active_tool_names("analyze",
                                                        "do a monte carlo study")
    assert "global_sensitivity" in Ag._active_tool_names("analyze",
                                                         "global sensitivity analysis")
    assert "optimize_multiobjective" in Ag._active_tool_names("analyze",
                                                             "pareto trade-off")


def test_loose_optimisation_phrasings_still_get_a_router():
    # Phrasings with no specialised keyword must still reach an optimiser.
    for msg in ("find the best temperature", "reduce energy use",
                "get the lowest possible duty", "find the optimal feed rate"):
        names = Ag._active_tool_names("analyze", msg)
        assert _ROUTERS & names, msg


def test_build_phase_has_no_optimisers():
    names = Ag._active_tool_names("build", "create a water heating process")
    assert not (_HEAVY & names)
    # build phase shouldn't pull in the analysis routers either
    assert not (_ROUTERS & names)


def test_plain_analysis_is_smaller_than_optimisation():
    plain = Ag._active_tool_names("analyze", "report the results")
    opt   = Ag._active_tool_names("analyze", "optimize the process")
    assert len(plain) < len(opt), "gating must actually shrink the plain payload"
