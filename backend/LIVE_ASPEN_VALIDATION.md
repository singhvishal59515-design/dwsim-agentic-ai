# Live-DWSIM Validation of the Aspen-parity Contributions

Real DWSIM v9.0.5 engine, no LLM. Test flowsheet: Water heater (Feed 25 C / 2 bar / 1 kg/s → H-101 → Hot), objective = heater duty (kW).

## 1. Trust-region surrogate EO (live)
- minimise duty → outlet T = **[40.0]** C, duty = **67.58 kW**, converged=False, evals=9, 14s
- expected: drives to the 40 C lower bound (min duty); reached ✅

## 2. Parallel flowsheet evaluation (live, 4 private CLRs)
- evaluated 8 designs: serial **2.9s**, parallel(4w) **39.7s** → speedup **0.07×**
- duties (outletT C → kW): [(40, 67.6), (50, 112.7), (60, 157.8), (70, 203.0), (80, 248.4), (90, 293.9), (100, 339.6), (110, 385.6)]

## 3. TAC from live DWSIM results
- at outlet 90 C, live duty = **293.90 kW** → TAC = **$267,533/yr** (annualised capex $115,174 + opex $152,359)

## Note
Infeasible-path SQP is not exercised here (needs a recycle flowsheet + the OT_Recycle single-pass hook); it remains validated on the analytic recycle.
