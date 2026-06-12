# Live-DWSIM Validation of the Aspen-parity Contributions

Real DWSIM v9.0.5 engine, no LLM. Test flowsheet: Water heater (Feed 25 C / 2 bar / 1 kg/s → H-101 → Hot), objective = heater duty (kW).

## 1. Trust-region surrogate EO (live)
- minimise duty → outlet T = **[40.0]** C, duty = **67.58 kW**, converged=False, evals=9, 11s
- expected: drives to the 40 C lower bound (min duty); reached ✅

## 2. Parallel flowsheet evaluation (live, 4 private CLRs)
- evaluated 8 designs: serial **4.0s**, parallel(4w) **38.1s** → speedup **0.11×**
- duties (outletT C → kW): [(40, 67.6), (50, 112.7), (60, 157.8), (70, 203.0), (80, 248.4), (90, 293.9), (100, 339.6), (110, 385.6)]

**Honest finding (corrects the earlier mock number).** Parallel is *slower* here
because each worker initialises its OWN DWSIM CLR (~30 s) and loads the
flowsheet, and that startup dwarfs 8 fast (~0.5 s) solves. The 1.9× measured
earlier was on a mock evaluator with NO init cost — not representative of real
DWSIM. The pool only pays off when per-worker CLR init is amortised: a
**persistent** pool reused across many generations of a population optimiser
with non-trivial per-solve time (rough breakeven for this engine:
total_solve_work ≳ n_workers × ~30 s). For one-shot small batches the single
in-process CLR is faster. The **correctness** guarantee (parallel == serial)
holds regardless; only the speed-up is workload-dependent.

## 3. TAC from live DWSIM results
- at outlet 90 C, live duty = **293.90 kW** → TAC = **$267,533/yr** (annualised capex $115,174 + opex $152,359)

## Note
Infeasible-path SQP is not exercised here (needs a recycle flowsheet + the OT_Recycle single-pass hook); it remains validated on the analytic recycle.
