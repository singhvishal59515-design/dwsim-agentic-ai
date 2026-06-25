# Multi-Variable Live Optimization — 6-Stage Compression (live DWSIM)

A genuine **5-decision-variable** optimisation on a real DWSIM flowsheet (6 compressors + 5 intercoolers), validated against a known multi-dimensional optimum. This moves the validated live ceiling above the single-variable heater/2-stage cases. No LLM.

**Problem:** compress nitrogen 1 → 64 bar in 6 stages, intercooling to 25 °C; minimise total compressor power over the 5 intermediate pressures.
**Closed-form optimum:** equal stage ratios r = (Pf/P0)^(1/6) = 2.000 ⇒ **[2.0, 4.0, 8.0, 16.0, 32.0] bar**.

| Intermediate pressure | Analytic (bar) | Optimizer (bar) |
|---|--:|--:|
| P1 (C1 outlet) | 2.0 | 2.022 |
| P2 (C2 outlet) | 4.0 | 4.082 |
| P3 (C3 outlet) | 8.0 | 8.221 |
| P4 (C4 outlet) | 16.0 | 16.471 |
| P5 (C5 outlet) | 32.0 | 32.706 |

| Total compressor power | kW |
|---|--:|
| at initial guess [2.6, 5.2, 10.4, 20.8, 41.6] | 0.545 |
| at analytic optimum [2.0, 4.0, 8.0, 16.0, 32.0] | 0.542 |
| at optimizer optimum | 0.542 |

**Result:** ✅ the optimizer recovers the equal-ratio geometric-progression optimum of a real 6-stage DWSIM flowsheet across **5** simultaneous decision variables (all within tolerance; total power at or below the analytic optimum). This demonstrates multi-variable live optimisation, not just the single-variable cases.

_Scope: 5 continuous decision variables on a real flowsheet with a known closed-form optimum; still well below industrial DOF counts (see the paper's scaling discussion), but a concrete step above 1-D._
