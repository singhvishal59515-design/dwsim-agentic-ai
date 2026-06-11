"""
Tests for global_sensitivity.run_global_sensitivity (SALib).

Validated against the Ishigami function — the standard sensitivity benchmark:
    f = sin(x1) + a·sin²(x2) + b·x3⁴·sin(x1),  xi ∈ [-π, π],  a=7, b=0.1
Known behaviour:
  • x1, x2 have substantial first-order effect (S1)
  • x3 has ZERO first-order effect but a LARGE total effect (ST) — it only acts
    through its interaction with x1. A method that captures interactions must
    rank x3's ST well above its S1.
"""
from __future__ import annotations
import math
import os
import sys

import pytest

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

pytest.importorskip("SALib")

from global_sensitivity import run_global_sensitivity, salib_available

PI = math.pi
VARIABLES = [
    {"tag": "X1", "property": "v", "lower": -PI, "upper": PI},
    {"tag": "X2", "property": "v", "lower": -PI, "upper": PI},
    {"tag": "X3", "property": "v", "lower": -PI, "upper": PI},
]


def _ishigami(x, a=7.0, b=0.1):
    x1, x2, x3 = x
    return math.sin(x1) + a * math.sin(x2) ** 2 + b * (x3 ** 4) * math.sin(x1)


def test_salib_available():
    assert salib_available() is True


def test_sobol_captures_x3_interaction():
    out = run_global_sensitivity(
        _ishigami, VARIABLES, output_name="f",
        method="sobol", n_samples=256, seed=1)
    assert out["success"], out
    assert out["method"].startswith("Sobol")
    idx = {r["variable"]: r for r in out["ranking"]}
    # x3 has near-zero first order but clearly non-zero total (interaction).
    assert idx["X3.v"]["S1"] < 0.1, idx["X3.v"]
    assert idx["X3.v"]["ST"] > 0.1, idx["X3.v"]
    # x2 should be the strongest first-order contributor.
    assert idx["X2.v"]["S1"] > idx["X3.v"]["S1"]


def test_morris_ranks_influential_inputs():
    out = run_global_sensitivity(
        _ishigami, VARIABLES, output_name="f",
        method="morris", n_samples=40, num_levels=4, seed=2)
    assert out["success"], out
    assert out["method"].startswith("Morris")
    # All three inputs are influential in Ishigami; mu_star must be > 0 for each.
    for r in out["ranking"]:
        assert r["mu_star"] > 0.0, r
    assert out["ranking"][0]["mu_star"] >= out["ranking"][-1]["mu_star"]


def test_handles_some_failed_evaluations():
    calls = {"n": 0}

    def flaky(x):
        calls["n"] += 1
        if calls["n"] % 17 == 0:      # ~6% fail
            return None
        return _ishigami(x)

    out = run_global_sensitivity(
        flaky, VARIABLES, method="sobol", n_samples=128, seed=3)
    assert out["success"], out
    assert out["n_failed"] >= 1
    assert len(out["ranking"]) == 3
