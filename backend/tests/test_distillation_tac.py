"""
Tests for the distillation-column TAC case study (distillation_tac_case_study.py).

The Fenske–Underwood–Gilliland model and the TAC-over-reflux optimisation are
pure (no DWSIM, no LLM), so the known-answer behaviour — R_min, N_min, a convex
TAC with a strictly-interior optimum near the classical 1.1–1.3·R_min band, and
the correct energy-price trend — is fully covered here.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


def test_fenske_underwood_known_values():
    from distillation_tac_case_study import (fenske_min_stages,
                                             underwood_min_reflux,
                                             ALPHA, ZF, XD, XB)
    n_min = fenske_min_stages(XD, XB, ALPHA)
    r_min = underwood_min_reflux(ALPHA, ZF, XD)
    # textbook BT split: N_min ≈ 10.5, R_min ≈ 1.38
    assert 10.0 < n_min < 11.0
    assert 1.30 < r_min < 1.45


def test_gilliland_monotonic_in_reflux():
    from distillation_tac_case_study import gilliland_stages
    n_min, r_min = 10.5, 1.38
    n_low = gilliland_stages(n_min, 1.10 * r_min, r_min)
    n_high = gilliland_stages(n_min, 2.0 * r_min, r_min)
    # more reflux → fewer stages, always above N_min
    assert n_low > n_high > n_min


def test_distillate_balance():
    from distillation_tac_case_study import distillate_flow
    assert abs(distillate_flow() - 50.0) < 1e-6   # balanced 50/50 → 99/1 split


def test_tac_optimum_is_interior_and_in_heuristic_band():
    from distillation_tac_case_study import optimize_tac
    econ = {"rate": 0.10, "years": 10, "hours_per_year": 8000.0,
            "capex_scale": 1.0, "heat_price_usd_per_kJ": 8e-6,
            "cool_price_usd_per_kJ": 0.7e-6}
    r = optimize_tac(econ)
    assert r["convex"] is True
    # strictly interior: above R_min, and at the classical ~1.2·R_min
    assert r["optimum"]["R"] > r["r_min"]
    assert 1.05 <= r["ratio_to_rmin"] <= 1.35


def test_optimum_moves_toward_rmin_as_energy_gets_dearer():
    from distillation_tac_case_study import energy_price_sensitivity
    rows = energy_price_sensitivity()
    # rows are [2× , typical , 0.5×] → ratio should increase as energy gets cheaper
    ratios = [row["ratio"] for row in rows]
    assert ratios[0] < ratios[1] < ratios[2]


def test_main_writes_artifact():
    import distillation_tac_case_study as m
    rc = m.main(live=False)
    assert rc == 0
    assert os.path.isfile(os.path.join(_B, "DISTILLATION_TAC_CASE_STUDY.md"))
