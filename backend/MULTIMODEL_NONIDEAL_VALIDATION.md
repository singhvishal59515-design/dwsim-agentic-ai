# Non-Ideal Multi-Model Thermodynamic Uncertainty (live DWSIM)

The SAME methanol/water flowsheet (50/50 mol feed, heated to 75 C at 1 atm — a two-phase state) solved under four activity-coefficient packages. Unlike pure water, a strongly non-ideal mixture near its azeotrope makes the package choice materially change the answer. No LLM.

**Models solved:** NRTL, UNIQUAC, Wilson, Modified UNIFAC (Dortmund)  
**Most model-dependent output:** Hot.density_kg_m3 (394.18 % spread)  
**Verdict:** MODEL-DEPENDENT

| Output | NRTL | UNIQUAC | Wilson | Modified UNIFAC (Dortmund) | spread % |
|---|--:|--:|--:|--:|--:|
| Hot.temperature_C | 75 | 75 | 75 | 75 | 0.00 |
| Hot.pressure_bar | 1.013 | 1.013 | 1.013 | 1.013 | 0.00 |
| Hot.vapor_fraction | 0.2787 | 0 | 0.2936 | 0.2662 | 140.06 |
| Hot.density_kg_m3 | 3.136 | 840.8 | 2.977 | 3.282 | 394.18 |
| Feed.temperature_C | 25 | 25 | 25 | 25 | 0.00 |
| Feed.pressure_bar | 1.013 | 1.013 | 1.013 | 1.013 | 0.00 |
| Feed.vapor_fraction | 0 | 0 | 0 | 0 | n/a |
| Feed.density_kg_m3 | 880.5 | 880.5 | 880.5 | 880.5 | 0.00 |

_Result is SENSITIVE to the thermodynamic model: Hot.density_kg_m3 varies by 394.18% across 4 packages. Treat this output as model-dependent and prefer a package validated for this chemistry, or report the range rather than a single value._

**Why this matters:** for this non-ideal separation the vapour fraction (the engineering result) differs by the spread above purely from the thermodynamic-model choice. The platform surfaces that in one command, so a result that depends on the package is flagged rather than reported as a single unqualified number — directly quantifying the fidelity gap a commercial tool leaves implicit.

_model_status: {'NRTL': 'ok', 'UNIQUAC': 'ok', 'Wilson': 'ok', 'Modified UNIFAC (Dortmund)': 'ok'}_
