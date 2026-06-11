# End-to-End Optimization Validation (live DWSIM)

Built with `build_flowsheet_atomic`, optimised with `run_dwsim_native_optimization` (method: Nelder-Mead simplex). Every objective evaluation is a real DWSIM solve; no LLM involved.

**Test:** Water heater, Feed 25 C / 2 bar / 1 kg/s. Decision variable = outlet T in [40, 120] C; objective = heater duty (kW). Baseline outlet T = 90.0 C, duty = 293.90 kW.

| Goal | Found outlet T (C) | Found duty (kW) | At expected bound | Reproduces on re-solve |
|---|--:|--:|:--:|:--:|
| minimise duty | 40.00 | 67.58 | ✅ | ✅ |
| maximise duty | 120.00 | 431.83 | ✅ | ✅ |

**Result:** ✅ the optimizer drives a real DWSIM-computed objective to its known optimum in both directions and the optimum reproduces on an independent re-solve — the closed loop (set variable → real DWSIM solve → read objective → optimiser step) is validated end-to-end.

_Scope: this validates the live DWSIM coupling on a monotonic objective (optima at the bounds). Hard/multimodal search is covered separately by validate_optimization.py on analytic benchmarks._
