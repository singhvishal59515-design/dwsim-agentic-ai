# Capstone Case Study: Two-Stage Compression with Intercooling (live DWSIM)

A live DWSIM optimization driven to an **interior** optimum with a known closed-form answer — the strongest single optimization result, validated three independent ways. No LLM involved.

**Process:** Nitrogen, Feed 25 °C / 1 bar / 1 kg/s → C1 → intercool to 25 °C → C2 → 10 bar. Decision variable: intermediate pressure P_int. Objective: minimise total compressor power.

**Closed-form optimum:** P_int* = √(P₁·P₂) = √10 = **3.162 bar** (equal-efficiency stages, intercool to inlet T).

| Method | Optimal P_int (bar) | Agreement |
|---|--:|:--:|
| 1. Analytical (geometric mean) | 3.162 | — |
| 2. Independent parametric sweep | 3.000 | ✅ vs analytical |
| 3. Project optimizer (live solve) | 3.170 | ✅ vs both |

**Result:** ✅ all three agree to within real-gas tolerance — the live optimizer finds the textbook interior optimum of a real DWSIM flowsheet, independently confirmed by a parametric sweep.

## Parametric sweep (total power vs P_int)

| P_int (bar) | Total power (kW) |
|--:|--:|
| 2.00 | 0.332 |
| 2.50 | 0.324 |
| 3.00 | 0.322  ← min |
| 3.50 | 0.322 |
| 4.00 | 0.324 |
| 4.50 | 0.327 |
| 5.00 | 0.331 |
| 5.50 | 0.336 |
| 6.00 | 0.341 |
| 6.50 | 0.346 |
| 7.00 | 0.351 |
| 7.50 | 0.356 |
| 8.00 | 0.362 |

_Scope: validates live DWSIM optimization on an interior optimum with a known analytical answer. The geometric-mean result is independent of stage efficiency, so the agreement isolates the optimizer + engine coupling, not a tuned efficiency._
