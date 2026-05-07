# Data & Knowledge Sources — DWSIM Agentic AI

This document answers the methodology questions raised by reviewers about the
provenance of:

1. The **84-compound Property Database** (`property_db.py`)
2. The **165-chunk Knowledge Base** (`knowledge_base.py`)
3. The **13 Silent Failure modes** (SF-01 → SF-13) in `safety_validator.py`

All sources are publicly available, peer-reviewed, or commercial standards data.

---

## 1. Property Database (84 compounds, `property_db.py`)

### 1.1 Critical Properties (Tc, Pc, Vc, ω, Tb)

| Source | Coverage |
|---|---|
| **DIPPR 801 (2023)** — Design Institute for Physical Properties | All 84 compounds |
| **Poling, Prausnitz & O'Connell (2001)** — *Properties of Gases and Liquids*, 5th ed., McGraw-Hill | Cross-validated |
| **NIST WebBook** (webbook.nist.gov) | Public-data validation |

The DB is a **structured mirror of public-domain data**, not new regressions. Values are
quoted to the precision reported in the primary sources.

### 1.2 Antoine Equation Constants (27 datasets)

| Source | Compounds |
|---|---|
| **Perry's Chemical Engineers' Handbook**, 9th ed., Sec. 4 | All 27 with explicit T-range |
| **NIST WebBook** | Cross-validation |

Equation form: `log₁₀(P_mmHg) = A − B/(C + T_°C)` with explicit `Tmin`/`Tmax`.

### 1.3 NRTL Binary Interaction Parameters (16 pairs)

| Source | Notes |
|---|---|
| **Gmehling et al. — DECHEMA VLE Data Collection** (Vols. 1-4) | Primary regression source |
| **Aspen Plus default databank** (cross-checked at standard T/P) | Validation |
| **DWSIM default databank** | Source-of-truth for DWSIM users |

Each pair carries `τ12, τ21, α, T_ref` and a free-form `source` string in the SQLite table.

### 1.4 UNIQUAC Binary Interaction Parameters (10 pairs)

| Source | Notes |
|---|---|
| **DECHEMA Chemistry Data Series** Vol. I, Parts 1a/1b | Original Anderson-Prausnitz fits |
| **Smith, Van Ness & Abbott** Ch. 12 worked examples | Methodology validation |

Stored as `u12-u22` and `u21-u11` in K (equivalent to ΔU₁₂ and ΔU₂₁ formulations).

### 1.5 Temperature-Dependent NRTL (5 pairs)

Parameters `(a₁₂, b₁₂, a₂₁, b₂₁, α)` for the form `τᵢⱼ(T) = aᵢⱼ + bᵢⱼ/T`.

| Source | Pairs |
|---|---|
| Renon & Prausnitz (1968) — original NRTL paper | Methodology |
| **DECHEMA VLE Data Collection** (T-dependent fits) | Ethanol/water, methanol/water |
| **Aspen Plus APV120 PURE36 databank** | Cross-validation |

### 1.6 Henry's Law Constants (13 entries)

| Source | Notes |
|---|---|
| **Sander, R. (2015)** *Compilation of Henry's law constants for water*, Atmos. Chem. Phys. 15:4399 | Primary source |
| **Perry's Handbook** Sec. 14 | Common gases |

Includes ΔH_sol so van 't Hoff temperature correction is supported.

> **No values in the property DB are novel regressions** — every entry is traceable
> to one of the public sources above. The contribution is the **structured SQLite
> packaging** with consistent units and aliasing, not new thermodynamic data.

---

## 2. Knowledge Base (165 chunks, `knowledge_base.py`)

The KB grew through versioned additions. Total chunks per topic area:

| Topic Area | Chunks | Primary Sources |
|---|---|---|
| Property packages (PR/SRK/NRTL/UNIQUAC/SAFT/CoolProp) | 12 | Smith, Van Ness & Abbott; Perry's Sec. 4 |
| Compound properties (20 compound-specific) | 20 | Poling/Prausnitz/O'Connell 5th ed. |
| Activity-coefficient theory | 8 | Gmehling, Kojima & Tochigi (1979) |
| Wilson, NRTL, UNIQUAC details | 6 | DECHEMA series, Renon-Prausnitz (1968) |
| Reaction thermodynamics (SMR, WGS, Haber-Bosch, methanol, EO, fermentation, esterification) | 13 | Fogler (2016); Perry's Sec. 23 |
| LLE / VLLE | 3 | Sørensen & Arlt DECHEMA LLE Vols. |
| Equipment sizing (column, HX, pump/compressor, vessel) | 6 | Seider et al.; Coulson Vol. 6; Perry's Sec. 11 |
| Distillation (FUG, multicomponent, HEN, algorithms) | 6 | Seader, Henley & Roper Ch. 9-11 |
| Safety / silent failure remediation | 13 | DWSIM forums; empirical from this project |
| Process integration (pinch, HEN) | 4 | Linnhoff & Hindmarsh (1983); Smith (2005) |
| Pipe hydraulics, two-phase flow | 1 | Crane TP-410; Coulson Vol. 1 |
| Electrolyte / amine gas treating | 1 | Kohl & Nielsen (1997) Ch. 2 |
| **Industrial case studies** (incl. biogas-to-H2) | 1 | Ullah et al. (2025) Digital Chem Eng |
| DWSIM-specific procedures, troubleshooting | 35 | DWSIM v9 documentation, forums |
| Renewable / fuel cell / electrolyzer | 12 | IEA Hydrogen Reports; IRENA |
| Economics, costing | 8 | Turton et al. (2018); Couper, Penney & Fair |
| Miscellaneous (units, conventions, thermo basics) | 17 | Various textbooks |

### Source Policy

Every chunk has a `source` field. Chunks marked with **textbook citation** quote
methodology only — no extended verbatim text. DWSIM-specific procedural chunks are
authored by this project from observed bridge behaviour and DWSIM v9 documentation,
under the assumption that procedural knowledge ("call X then Y") is not copyrightable.

---

## 3. Silent Failure Catalogue (SF-01 → SF-13)

### Discovered empirically from DWSIM v9.0.5.0 testing

| Code | Failure Mode | Discovery |
|---|---|---|
| SF-01 | CalcMode not set on Heater/Cooler | DWSIM v8 issue, fixed in bridge v2.1 |
| SF-02 | Reversed connection port direction | DWSIM forum reports + this project |
| SF-03 | Unnormalised composition | DWSIM API quirk (>1 sums silently accepted) |
| SF-04 | Negative molar/mass flow accepted | Bridge-level guard added |
| SF-05 | VF flash spec out of [0,1] tolerance | Numerical noise from solver |
| SF-06 | DeltaP > feed pressure → P < 0 outlet | Pre-solve check |
| SF-07 | Heater outlet T < feed T (reversed) | Pre-solve check |
| SF-08a-d | Unit-op energy balance violations | Post-solve detection (4 sub-modes) |
| SF-09a-c | Global flowsheet balance violations | Post-solve (mass/energy/orphan stream) |
| SF-10 | Supercritical T>Tc AND P>Pc (cubic EOS unreliable) | Added Round 3 |
| SF-11 | Impossible vapor fraction (NaN/Inf, out-of-range) | Added Round 3 |
| SF-12 | VLLE risk for partially-miscible pairs | Added Round 3 |
| SF-13 | Phase consistency: P vs Antoine Psat vs reported VF | Added Round 3 |

SF-10 through SF-13 are **empirical additions discovered during the second project review** — not speculative. The associated methods in `safety_validator.py` operate on real
stream data and are exercised by `test_safety_validator.py` (20 tests).

---

## 4. Reproducibility — How to Verify These Claims

```bash
# 1. Check property DB compound count and source list
python -c "from property_db import PropertyDB; db = PropertyDB(); print(db.count_compounds())"
# expected: 84

# 2. Check KB chunk count
python -c "from knowledge_base import KNOWLEDGE_CHUNKS; print(len(KNOWLEDGE_CHUNKS))"
# expected: 166 (165 + biogas case study just added)

# 3. Run safety validator tests
python -m pytest tests/test_safety_validator.py -v

# 4. Inspect a specific BIP source
python -c "from property_db import PropertyDB; \
  print(PropertyDB().lookup_pair('ethanol', 'water', 'nrtl'))"
```

---

## 5. Outstanding Methodology Items (from third review)

These items are infrastructure-only as of 2026-05-07 and need experimental runs
before the paper can claim them as results:

- **AIJudge scoring**: implemented, async, writes scores to `eval_log.json`.
  ⚠ Has not been run across the 25 benchmark tasks yet.
- **Ablation study (BM25 vs TF-IDF, state-card on/off, failover effectiveness)**:
  framework in `ablation.py` ready. ⚠ Has not been run.
- **PromptRegistry A/B testing**: 6 prompt versions stored. ⚠ No A/B comparison run yet.

The next session task is to execute these and append results to this document.
