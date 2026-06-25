# Multi-Variable Live Optimization — Five-Stage Compression (live DWSIM)

A genuine **4-decision-variable** optimisation on a real DWSIM flowsheet (5 compressors + 4 intercoolers), validated against a known multi-dimensional optimum. This moves the validated live ceiling above the single-variable heater/2-stage cases. No LLM.

**Problem:** compress nitrogen 1 → 32 bar in 5 stages, intercooling to 25 °C; minimise total compressor power over the four intermediate pressures.
**Closed-form optimum:** equal stage ratios r = (Pf/P0)^(1/5) = 2.000 ⇒ **[2.0, 4.0, 8.0, 16.0] bar**.

| Intermediate pressure | Analytic (bar) | Optimizer (bar) |
|---|--:|--:|
| P1 (C1 outlet) | 2.0 | 2.013 |
| P2 (C2 outlet) | 4.0 | 4.046 |
| P3 (C3 outlet) | 8.0 | 8.111 |
| P4 (C4 outlet) | 16.0 | 16.182 |

| Total compressor power | kW |
|---|--:|
| at initial guess [3.0, 6.0, 11.0, 18.0] | 0.456 |
| at analytic optimum [2.0, 4.0, 8.0, 16.0] | 0.452 |
| at optimizer optimum | 0.452 |

**Result:** ✅ the optimizer recovers the equal-ratio geometric-progression optimum of a real 5-stage DWSIM flowsheet across **four** simultaneous decision variables (all within tolerance; total power at or below the analytic optimum). This demonstrates multi-variable live optimisation, not just the single-variable cases.

_Scope: 4 continuous decision variables on a real flowsheet with a known closed-form optimum; still well below industrial DOF counts (see the paper's scaling discussion), but a concrete step above 1-D._
