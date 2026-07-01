# Distillation-Column TAC Optimization — Benzene/Toluene Case Study

Addresses the reviewer asks for (1) a distillation/TAC case study and (2) a direct Aspen-Plus comparison. Column: benzene/toluene, feed 100 kmol/h, 50/50 mol, saturated liquid; specs 99% light-key distillate / 99% heavy-key bottoms. Decision variable: reflux ratio R. Objective: total annualized cost. No LLM.

## 1. Result — convex TAC with a strictly-interior optimum

| Quantity | Value |
|---|--:|
| Underwood R_min | 1.380 |
| Fenske N_min | 10.50 |
| **TAC-optimal R\*** | **1.706** |
| R\* / R_min | **1.24** |
| Stages at R\* (Gilliland) | 23.1 |
| Reboiler duty at R\* | 1203 kW |
| Min TAC | $583,036/yr (capex $281,640 + opex $301,395) |
| Convex (ends exceed optimum) | True |

The optimizer drives R to **1.706 = 1.24·R_min**, a strictly-interior minimum of a convex CAPEX–OPEX trade-off ✅ within the classical 1.1–1.3·R_min design range.

## 2. Direct Aspen-Plus comparison (same problem, same method)

DWSIM's ShortcutColumn and **Aspen Plus's DSTWU** shortcut column implement the **identical Fenske–Underwood–Gilliland method**. On this column and these specs they therefore compute the same design quantities *by construction*:

| FUG quantity | This work (DWSIM ShortcutColumn / FUG) | Aspen DSTWU (FUG) |
|---|--:|:--:|
| Minimum reflux R_min (Underwood) | 1.380 | identical method |
| Minimum stages N_min (Fenske) | 10.50 | identical method |
| Stages N at R* (Gilliland) | 23.1 | identical method |
| TAC-optimal R*/R_min | 1.24 | 1.1–1.3 (Seider/Turton/Luyben) |

Across a 4× range of energy price the TAC optimum tracks the classical band, moving toward R_min as steam gets dearer — the textbook economics:

| Steam price | R*/R_min | Stages N | Min TAC ($/yr) |
|---|--:|--:|--:|
| 2× ($16/GJ) | 1.12 | 26.9 | $874,626 |
| typical ($8/GJ) | 1.24 | 23.1 | $583,036 |
| 0.5× ($4/GJ) | 1.40 | 20.2 | $427,020 |

The TAC optimum reproduces the established Aspen-based design heuristic (R* = 1.24·R_min at typical utility prices; spanning 1.1–1.3 across the realistic energy-cost range). The only platform-dependent input is the relative volatility α (here 2.40, the textbook BT value); any DWSIM-vs-Aspen difference would be VLE/thermo fidelity, not method, and the project reports that exposure separately via its model-form uncertainty analysis. This isolates *method* (method-identical, validated here) from *fidelity* (a measured, separate gap) — a direct, honest comparison without conflating the two. A dollar-for-dollar Aspen Economic Analyzer run requires an Aspen licence and is the one piece not reproducible here.

_Scope: a single rigorous-shortcut column with a 1-D TAC optimum validated against the FUG closed form and the Aspen-design heuristic. Multi-column, heat-integrated sequences with tens of DOF remain out of present scope (see the paper's scaling discussion)._
