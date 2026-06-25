# Surrogate-EO Approximation Quality (live DWSIM)

The equation-oriented optimiser is surrogate-based, so its honesty depends on knowing when the surrogate predicts the flowsheet. Run live on a water-heater (decision variable = outlet T in [40,120] C; objective = computed heater duty), no LLM.

| Metric | Value | Meaning |
|---|--:|---|
| In-sample R² (objective) | 1.0 | quadratic fit on the DOE samples |
| **Cross-validated R²** | **1.0** | the trust metric (in-sample R² overfits) |
| Surrogate prediction at optimum | 67.744864 kW | what the surrogate predicted |
| Actual DWSIM solve at optimum | 67.580375 kW | a real solve at the predicted optimum |
| **Surrogate-vs-actual gap** | **0.164489** (0.243%) | prediction error at the reported optimum |
| Adaptive refinements | 1 | real solves added at the predicted optimum to sharpen the fit |
| DWSIM solves total | 13 | DOE + validation + refinement |
| Trustworthy flag | True | cross-val R² ≥ 0.70 or gap within tolerance |

**Reading:** on this flowsheet the surrogate is reliable — cross-validated R² = 1.0, and the prediction error at the reported optimum is 0.243% after 1 adaptive refinement(s). The cross-validated R² is the honest guard: when it falls below 0.70 (as on the analytic Rosenbrock valley in validate_optimization.py, whose curvature defeats a quadratic surrogate) the EO optimum is explicitly flagged rather than trusted blindly.

_Scope: validates the surrogate-quality reporting on a smooth live objective; the trust flag's discriminating power on a hard (low-R²) objective is covered analytically (Rosenbrock) in validate_optimization.py._
