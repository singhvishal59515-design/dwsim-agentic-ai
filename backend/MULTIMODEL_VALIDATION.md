# Multi-Model Thermodynamic Uncertainty (live DWSIM)

The SAME water-heater flowsheet (Feed 25 °C / 1 atm / 1 kg/s, heated to 80 °C) solved under three property packages; every stream output is compared across models. No LLM is involved.

**Models solved:** Peng-Robinson (PR), Soave-Redlich-Kwong (SRK), Steam Tables (IAPWS-IF97)  
**Most model-dependent output:** Feed.density_kg_m3 (0.07 % spread)  
**Verdict:** ROBUST

| Output | Peng-Robinson (PR) | Soave-Redlich-Kwong (SRK) | Steam Tables (IAPWS-IF97) | spread % |
|---|--:|--:|--:|--:|
| Hot.temperature_C | 80 | 80 | 80 | 0.00 |
| Hot.pressure_bar | 1.013 | 1.013 | 1.013 | 0.00 |
| Hot.mass_flow_kgh | 3600 | 3600 | 3600 | 0.00 |
| Hot.vapor_fraction | 0 | 0 | 0 | n/a |
| Hot.density_kg_m3 | 971.5 | 971.5 | 971.8 | 0.03 |
| Feed.temperature_C | 25 | 25 | 25 | 0.00 |
| Feed.pressure_bar | 1.013 | 1.013 | 1.013 | 0.00 |
| Feed.mass_flow_kgh | 3600 | 3600 | 3600 | 0.00 |
| Feed.vapor_fraction | 0 | 0 | 0 | n/a |
| Feed.density_kg_m3 | 996.3 | 996.3 | 997 | 0.07 |

_Result is ROBUST to the thermodynamic model: the largest output spread is 0.07% (<= 5% across 3 packages). The conclusion does not hinge on the package choice._

**Why this matters vs a commercial simulator:** a validated-thermo tool gives one number; this gives the number AND how much it depends on the model — surfacing, in one call, when a result is only as good as the package choice (here, liquid-water density under cubic EOS).

_model_status: {'Peng-Robinson (PR)': 'ok', 'Soave-Redlich-Kwong (SRK)': 'ok', 'Steam Tables (IAPWS-IF97)': 'ok'}_
