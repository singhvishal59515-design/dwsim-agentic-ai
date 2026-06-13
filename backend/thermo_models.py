"""
thermo_models.py
────────────────
A grounded registry of the thermodynamic property packages this project can
actually use — i.e. the ones DWSIM 9.0.5 installs (enumerated live from
`AvailablePropertyPackages`, 28 packages) — each mapped to the Aspen Plus method
name(s) an engineer knows, with its applicability domain. This is the project's
equivalent of Aspen's *Methods Assistant*, and it is deliberately honest:

  • It NEVER recommends a model the engine cannot instantiate. (The old
    process_design_advisor selector returned names like "eNRTL", "PR-HV",
    "Wong-Sandler PR", "SRK" that are NOT DWSIM packages, so a build using the
    recommendation would fail or silently fall back.)
  • Aspen methods with no DWSIM equivalent are recorded explicitly as gaps with
    the closest available substitute — not papered over.

We do not (and cannot honestly) "add" Aspen's proprietary thermo into an
open-source engine; we expose and correctly select across the real catalogue.

The canonical DWSIM names below are verbatim from the live engine so they can be
passed straight to build_flowsheet_atomic / property packages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# Verbatim DWSIM 9.0.5 AvailablePropertyPackages keys (the only strings the
# engine accepts). Kept as the source of truth for validity checks.
DWSIM_PACKAGES = [
    "Black Oil",
    "CAPE-OPEN",
    "Chao-Seader",
    "CoolProp",
    "CoolProp (Incompressible Fluids)",
    "CoolProp (Incompressible Mixtures)",
    "GERG-2008",
    "Grayson-Streed",
    "Ideal Solution (Aqueous Electrolytes)",
    "Lee-Kesler-Plöcker",
    "Modified UNIFAC (Dortmund)",
    "Modified UNIFAC (NIST)",
    "NRTL",
    "PC-SAFT (with Association Support) (.NET Code)",
    "Peng-Robinson (PR)",
    "Peng-Robinson 1978 (PR78)",
    "Peng-Robinson 1978 (PR78) Advanced",
    "Peng-Robinson-Stryjek-Vera 2 (PRSV2-M)",
    "Peng-Robinson-Stryjek-Vera 2 (PRSV2-VL)",
    "Raoult's Law",
    "Seawater IAPWS-08",
    "Soave-Redlich-Kwong (SRK)",
    "Soave-Redlich-Kwong (SRK) Advanced",
    "Steam Tables (IAPWS-IF97)",
    "UNIFAC",
    "UNIFAC-LL",
    "UNIQUAC",
    "Wilson",
]
DWSIM_PACKAGES_SET = set(DWSIM_PACKAGES)

# Families for grouping / selection.
FAMILY_CUBIC      = "cubic_eos"
FAMILY_SAFT       = "saft"
FAMILY_REFERENCE  = "reference_eos"
FAMILY_ACTIVITY   = "activity_coefficient"
FAMILY_PETROLEUM  = "petroleum_semi_empirical"
FAMILY_ELECTROLYTE = "electrolyte"
FAMILY_IDEAL      = "ideal"
FAMILY_EXTERNAL   = "external"


@dataclass
class ThermoModel:
    dwsim_name: str                 # verbatim engine key (must be in DWSIM_PACKAGES)
    family: str
    aspen_equivalents: List[str]    # Aspen Plus method-name(s) an engineer knows
    best_for: List[str]             # compound classes / regimes it suits
    avoid_for: List[str] = field(default_factory=list)
    regime: str = ""                # pressure/temperature guidance
    accuracy: str = ""              # rough fidelity note
    notes: str = ""


# ── The registry: every installed DWSIM package, mapped to Aspen ─────────────
REGISTRY: List[ThermoModel] = [
    # ---- Cubic equations of state ----
    ThermoModel("Peng-Robinson (PR)", FAMILY_CUBIC,
                ["PENG-ROB", "PR-BM"],
                ["nonpolar/slightly-polar hydrocarbons", "gas processing",
                 "high pressure", "refining", "general default"],
                ["strongly polar mixtures", "electrolytes", "low-P VLE of "
                 "associating fluids (use an activity model)"],
                regime="all P; best for vapour + high-P", accuracy="Good (±2–5%)",
                notes="Industry default; slightly better liquid density than SRK."),
    ThermoModel("Peng-Robinson 1978 (PR78)", FAMILY_CUBIC,
                ["PENG-ROB (1978 alpha)"],
                ["hydrocarbons", "heavier components (improved alpha function)"],
                regime="all P", accuracy="Good (±2–5%)",
                notes="1978 alpha-function revision; better for high acentric factors."),
    ThermoModel("Peng-Robinson 1978 (PR78) Advanced", FAMILY_CUBIC,
                ["PENG-ROB with advanced mixing rules"],
                ["polar + nonpolar via advanced mixing rules"],
                regime="all P", accuracy="Good (±2–5%)",
                notes="Advanced mixing rules extend PR toward polar systems "
                      "(closest DWSIM analogue to Huron-Vidal / Wong-Sandler PR)."),
    ThermoModel("Peng-Robinson-Stryjek-Vera 2 (PRSV2-M)", FAMILY_CUBIC,
                ["PRSV / PRSV2 (Mathias)"],
                ["polar + nonpolar VLE", "improved vapour pressure"],
                regime="low–high P", accuracy="Good (±2–4%)",
                notes="Stryjek-Vera modification; better pure-component vapour "
                      "pressures than PR. M = Mathias mixing."),
    ThermoModel("Peng-Robinson-Stryjek-Vera 2 (PRSV2-VL)", FAMILY_CUBIC,
                ["PRSV / PRSV2 (van Laar)"],
                ["polar + nonpolar VLE"],
                regime="low–high P", accuracy="Good (±2–4%)",
                notes="PRSV2 with van Laar mixing rule for stronger non-ideality."),
    ThermoModel("Soave-Redlich-Kwong (SRK)", FAMILY_CUBIC,
                ["RK-SOAVE", "SRK"],
                ["hydrocarbons", "gas processing", "refining"],
                ["polar mixtures", "electrolytes"],
                regime="all P", accuracy="Good (±2–5%)",
                notes="Classic cubic; vapour properties on par with PR."),
    ThermoModel("Soave-Redlich-Kwong (SRK) Advanced", FAMILY_CUBIC,
                ["RK-SOAVE with advanced mixing rules"],
                ["polar + nonpolar via advanced mixing rules"],
                regime="all P", accuracy="Good (±2–5%)",
                notes="Advanced mixing-rule SRK (toward Huron-Vidal-type behaviour)."),
    ThermoModel("Lee-Kesler-Plöcker", FAMILY_CUBIC,
                ["LK-PLOCK"],
                ["light hydrocarbons", "natural gas", "cryogenic", "enthalpy/density"],
                ["polar mixtures"],
                regime="wide T incl. cryogenic", accuracy="Good for HC enthalpy",
                notes="Corresponding-states; strong for hydrocarbon enthalpies."),

    # ---- SAFT ----
    ThermoModel("PC-SAFT (with Association Support) (.NET Code)", FAMILY_SAFT,
                ["PC-SAFT", "POLYPCSF (polymer variant in Aspen)"],
                ["associating fluids", "polymers (monomer-level)", "CO2",
                 "complex/large molecules", "high pressure"],
                regime="wide T/P", accuracy="High for associating systems",
                notes="Statistical-associating-fluid theory; handles hydrogen "
                      "bonding explicitly. Closest DWSIM analogue to Aspen PC-SAFT."),

    # ---- Reference / high-accuracy EOS ----
    ThermoModel("GERG-2008", FAMILY_REFERENCE,
                ["GERG2008", "REFPROP (natural-gas)"],
                ["natural gas", "LNG", "custody transfer", "cryogenic"],
                ["non-gas-mixture chemistry outside its 21 components"],
                regime="wide T/P (reference quality)", accuracy="Reference (±0.1%)",
                notes="ISO 20765 reference EOS for natural-gas mixtures."),
    ThermoModel("CoolProp", FAMILY_REFERENCE,
                ["REFPROP (via CoolProp backend)"],
                ["pure fluids and mixtures with high-accuracy reference EOS",
                 "refrigerants", "CO2", "water"],
                regime="component-dependent", accuracy="Reference where available",
                notes="Multiparameter Helmholtz EOS via the CoolProp library."),
    ThermoModel("CoolProp (Incompressible Fluids)", FAMILY_REFERENCE,
                ["(incompressible reference fluids)"],
                ["heat-transfer fluids", "brines", "thermal oils (pure)"],
                regime="liquid phase", accuracy="High for the tabulated fluids",
                notes="Incompressible-fluid correlations (no VLE)."),
    ThermoModel("CoolProp (Incompressible Mixtures)", FAMILY_REFERENCE,
                ["(incompressible reference mixtures)"],
                ["brines/glycol-water mixtures", "secondary coolants"],
                regime="liquid phase", accuracy="High for tabulated mixtures",
                notes="Incompressible mixture correlations (e.g. glycol/water)."),
    ThermoModel("Steam Tables (IAPWS-IF97)", FAMILY_REFERENCE,
                ["STEAM-TA", "STEAMNBS", "IAPWS-95"],
                ["pure water/steam", "steam cycles", "utilities"],
                ["multicomponent mixtures (water only)"],
                regime="full water phase diagram", accuracy="Reference (±0.1%)",
                notes="The accurate choice whenever the system is essentially water."),
    ThermoModel("Seawater IAPWS-08", FAMILY_REFERENCE,
                ["(seawater thermodynamics)"],
                ["desalination", "seawater", "brine"],
                regime="seawater conditions", accuracy="Reference for seawater",
                notes="IAPWS-08 seawater formulation; for desalination duty."),

    # ---- Activity-coefficient models (liquid-phase non-ideality) ----
    ThermoModel("NRTL", FAMILY_ACTIVITY,
                ["NRTL", "NRTL-RK"],
                ["strongly non-ideal liquids", "polar mixtures", "azeotropes",
                 "VLE/LLE at low–moderate P with binary data"],
                ["high pressure without an EOS vapour model"],
                regime="low–moderate P", accuracy="High with fitted BIPs",
                notes="Needs binary interaction parameters; pair with an EOS or "
                      "ideal gas for the vapour. Aspen's NRTL-RK is NRTL + RK vapour."),
    ThermoModel("UNIQUAC", FAMILY_ACTIVITY,
                ["UNIQUAC", "UNIQ-RK"],
                ["non-ideal liquids", "VLE/LLE", "partially miscible systems"],
                regime="low–moderate P", accuracy="High with fitted BIPs",
                notes="Two-parameter; good LLE. Needs binary data."),
    ThermoModel("Wilson", FAMILY_ACTIVITY,
                ["WILSON"],
                ["miscible polar mixtures", "VLE"],
                ["liquid-liquid equilibrium (Wilson cannot predict LLE)"],
                regime="low–moderate P", accuracy="High for VLE with data",
                notes="Excellent VLE; CANNOT represent phase splitting (no LLE)."),
    ThermoModel("UNIFAC", FAMILY_ACTIVITY,
                ["UNIFAC"],
                ["predictive VLE when no binary data exist", "screening"],
                regime="low–moderate P", accuracy="Predictive (±5–10%)",
                notes="Group-contribution; use when binary parameters are unavailable."),
    ThermoModel("UNIFAC-LL", FAMILY_ACTIVITY,
                ["UNIF-LL"],
                ["predictive liquid-liquid equilibrium", "solvent screening"],
                regime="low–moderate P", accuracy="Predictive",
                notes="UNIFAC parameterised for LLE specifically."),
    ThermoModel("Modified UNIFAC (Dortmund)", FAMILY_ACTIVITY,
                ["UNIF-DMD"],
                ["predictive VLE/LLE/excess enthalpy", "wide T range"],
                regime="low–moderate P", accuracy="Predictive, improved vs UNIFAC",
                notes="Temperature-dependent groups; the best general predictive choice."),
    ThermoModel("Modified UNIFAC (NIST)", FAMILY_ACTIVITY,
                ["(NIST-modified UNIFAC)"],
                ["predictive VLE/LLE with NIST-revised parameters"],
                regime="low–moderate P", accuracy="Predictive",
                notes="NIST re-parameterisation of modified UNIFAC."),

    # ---- Petroleum / semi-empirical ----
    ThermoModel("Chao-Seader", FAMILY_PETROLEUM,
                ["CHAO-SEA"],
                ["light hydrocarbons", "refinery", "H2-rich (moderate P/T)"],
                ["polar/aqueous", "very high P"],
                regime="moderate P/T refinery range", accuracy="Refinery-grade",
                notes="Semi-empirical; legacy refinery method."),
    ThermoModel("Grayson-Streed", FAMILY_PETROLEUM,
                ["GRAYSON", "GS"],
                ["heavy hydrocarbons", "hydrogen-rich refinery streams",
                 "high temperature"],
                ["polar/aqueous"],
                regime="higher T than Chao-Seader", accuracy="Refinery-grade",
                notes="Grayson-Streed extension of Chao-Seader for H2 + heavy HC."),
    ThermoModel("Black Oil", FAMILY_PETROLEUM,
                ["(black-oil correlations)"],
                ["upstream reservoir/oil-gas", "pipeline black-oil modelling"],
                regime="production conditions", accuracy="Correlation-based",
                notes="Black-oil PVT correlations (GOR, FVF) for upstream duty."),

    # ---- Electrolyte ----
    ThermoModel("Ideal Solution (Aqueous Electrolytes)", FAMILY_ELECTROLYTE,
                ["(approximate; cf. Aspen ELECNRTL / ENRTL-RK)"],
                ["dilute aqueous electrolytes (approximate)"],
                ["concentrated electrolytes", "speciation-sensitive systems",
                 "amine acid-gas treating (needs true eNRTL)"],
                regime="dilute aqueous", accuracy="Approximate (ideal mixing)",
                notes="DWSIM's only built-in electrolyte option. It is an IDEAL "
                      "electrolyte model — NOT a true activity-based electrolyte "
                      "NRTL. See ASPEN_GAPS for ELECNRTL/ENRTL-RK."),

    # ---- Ideal ----
    ThermoModel("Raoult's Law", FAMILY_IDEAL,
                ["RAOULT", "IDEAL"],
                ["ideal/near-ideal mixtures", "teaching", "quick estimates"],
                ["any non-ideal system"],
                regime="low P", accuracy="Rough",
                notes="Ideal-solution baseline; use only for near-ideal systems."),

    # ---- External ----
    ThermoModel("CAPE-OPEN", FAMILY_EXTERNAL,
                ["CAPE-OPEN thermo plugins"],
                ["any model exposed by an installed CAPE-OPEN thermo server"],
                regime="depends on plugin", accuracy="depends on plugin",
                notes="Bridge to external CAPE-OPEN thermodynamic packages."),
]

_BY_NAME: Dict[str, ThermoModel] = {m.dwsim_name: m for m in REGISTRY}


# ── Aspen methods with NO DWSIM equivalent (honest gaps) ─────────────────────
# Each: the Aspen method, why it matters, and the closest DWSIM substitute.
ASPEN_GAPS: List[Dict[str, Any]] = [
    {"aspen": "ELECNRTL / ENRTL-RK",
     "keywords": ["elecnrtl", "enrtl-rk", "enrtl", "electrolyte nrtl"],
     "use": "rigorous aqueous electrolytes, amine acid-gas treating, sour water",
     "closest_dwsim": "Ideal Solution (Aqueous Electrolytes)",
     "caveat": "DWSIM's electrolyte model is ideal, not a true activity-based "
               "electrolyte NRTL — speciation and ionic non-ideality are not "
               "rigorously captured. This is a genuine fidelity gap."},
    {"aspen": "Polymer methods (POLYNRTL, POLYPCSF, polymer NRTL)",
     "keywords": ["polynrtl", "polypcsf", "polymer"],
     "use": "polymer process modelling (chain-length distributions)",
     "closest_dwsim": "PC-SAFT (with Association Support) (.NET Code)",
     "caveat": "No polymer-specific property methods in DWSIM (PC-SAFT is "
               "monomer-level only)."},
    {"aspen": "PSRK / SR-POLAR (predictive SRK with UNIFAC mixing)",
     "keywords": ["psrk", "sr-polar", "srpolar"],
     "use": "predictive high-pressure polar/asymmetric mixtures",
     "closest_dwsim": "Soave-Redlich-Kwong (SRK) Advanced",
     "caveat": "No single PSRK package; approximate with SRK Advanced or "
               "PRSV2 + UNIFAC."},
    {"aspen": "Aspen-regressed databank BIPs (PR-BM, RK-SOAVE with APV BIPs)",
     "keywords": ["pr-bm", "aspen bip", "apv"],
     "use": "the same EOS but with Aspen's proprietary regressed binary parameters",
     "closest_dwsim": "Peng-Robinson (PR)",
     "caveat": "DWSIM HAS the equations of state; what it lacks is Aspen's "
               "decades-regressed binary-parameter databank. Set BIPs from data "
               "where accuracy matters."},
    {"aspen": "BK10, refinery assay/petroleum cut methods",
     "keywords": ["bk10", "bk-10"],
     "use": "crude assay characterisation, refinery fractionation",
     "closest_dwsim": "Grayson-Streed",
     "caveat": "Petroleum methods exist but assay characterisation is less "
               "developed than Aspen's refining suite."},
]


# ── Public API ───────────────────────────────────────────────────────────────

def is_available(name: str) -> bool:
    """True if `name` is a package DWSIM can instantiate verbatim."""
    return name in DWSIM_PACKAGES_SET


def get_model(dwsim_name: str) -> Optional[ThermoModel]:
    return _BY_NAME.get(dwsim_name)


def resolve_to_dwsim(requested: str) -> Dict[str, Any]:
    """Map a requested model name (Aspen name, alias, or loose text) to a real
    DWSIM package. Returns {dwsim_name, exact, matched_on, note}."""
    if not requested:
        return {"dwsim_name": None, "exact": False, "matched_on": None,
                "note": "no model requested"}
    req = requested.strip()
    if req in DWSIM_PACKAGES_SET:
        return {"dwsim_name": req, "exact": True, "matched_on": "dwsim_name",
                "note": ""}
    rl = req.lower()
    # 1) Known Aspen gap FIRST — checked before the fuzzy alias match because a
    # gap name can contain a real package as a substring (e.g. "elecnrtl"
    # contains "nrtl"). Match only the gap's distinctive keywords.
    for g in ASPEN_GAPS:
        if any(k in rl for k in g.get("keywords", [])):
            return {"dwsim_name": g["closest_dwsim"], "exact": False,
                    "matched_on": "aspen_gap",
                    "note": (f"'{requested}' has no DWSIM equivalent. Closest: "
                             f"'{g['closest_dwsim']}'. {g['caveat']}")}
    # 2) Aspen-equivalent / alias match
    for m in REGISTRY:
        for a in m.aspen_equivalents:
            if rl == a.lower() or rl in a.lower() or a.lower() in rl:
                return {"dwsim_name": m.dwsim_name, "exact": False,
                        "matched_on": f"aspen:{a}",
                        "note": f"Mapped Aspen '{a}' → DWSIM '{m.dwsim_name}'."}
    # 3) Substring against DWSIM names (e.g. "peng" → PR)
    for name in DWSIM_PACKAGES:
        if rl in name.lower() or name.lower().split(" (")[0] in rl:
            return {"dwsim_name": name, "exact": False, "matched_on": "substring",
                    "note": f"Loose match '{requested}' → '{name}'."}
    return {"dwsim_name": "Peng-Robinson (PR)", "exact": False,
            "matched_on": "default",
            "note": f"Unrecognised model '{requested}'; defaulting to PR."}


def recommend(*, electrolyte: bool = False, acid_gas_amine: bool = False,
              polar: bool = False, hydrocarbon: bool = False,
              water_only: bool = False, natural_gas: bool = False,
              refinery_heavy: bool = False, have_binary_data: bool = False,
              pressure_bar: float = 1.01325) -> Dict[str, Any]:
    """Return a DWSIM-INSTANTIABLE recommendation plus the ideal Aspen method and
    an honest availability caveat. The recommended_pp is always a real DWSIM key."""
    ideal_aspen: Optional[str] = None
    caveat = ""
    if water_only:
        pp = "Steam Tables (IAPWS-IF97)"; ideal_aspen = "STEAM-TA / IAPWS-95"
    elif electrolyte or acid_gas_amine:
        pp = "Ideal Solution (Aqueous Electrolytes)"
        ideal_aspen = "ELECNRTL / ENRTL-RK"
        caveat = ("DWSIM's electrolyte model is IDEAL, not a true electrolyte "
                  "NRTL — for rigorous amine/electrolyte work this is a known "
                  "fidelity gap vs Aspen (see ASPEN_GAPS).")
    elif natural_gas:
        pp = "GERG-2008"; ideal_aspen = "GERG2008 / REFPROP"
    elif polar and pressure_bar < 10:
        pp = ("NRTL" if have_binary_data else "Modified UNIFAC (Dortmund)")
        ideal_aspen = "NRTL-RK" if have_binary_data else "UNIF-DMD"
        if not have_binary_data:
            caveat = ("No binary data assumed → predictive modified-UNIFAC "
                      "(Dortmund). Switch to NRTL/UNIQUAC once BIPs are fitted.")
    elif polar and pressure_bar >= 10:
        pp = "Peng-Robinson 1978 (PR78) Advanced"
        ideal_aspen = "PR with Huron-Vidal/Wong-Sandler mixing"
        caveat = ("High-pressure polar system → advanced-mixing-rule PR (DWSIM's "
                  "closest analogue to Huron-Vidal/Wong-Sandler PR).")
    elif refinery_heavy:
        pp = "Grayson-Streed"; ideal_aspen = "GRAYSON"
    elif hydrocarbon:
        pp = "Peng-Robinson (PR)"; ideal_aspen = "PENG-ROB / PR-BM"
    else:
        pp = "Peng-Robinson (PR)"; ideal_aspen = "PENG-ROB"
    m = get_model(pp)
    return {
        "recommended_pp": pp,                 # guaranteed valid DWSIM key
        "dwsim_available": is_available(pp),
        "aspen_equivalent": ideal_aspen,
        "family": m.family if m else None,
        "accuracy": m.accuracy if m else None,
        "caveat": caveat,
    }


_RECOMMEND_FLAGS = ("electrolyte", "acid_gas_amine", "polar", "hydrocarbon",
                    "water_only", "natural_gas", "refinery_heavy",
                    "have_binary_data", "pressure_bar")


def assistant(action: str = "catalogue", model: Optional[str] = None,
              **flags: Any) -> Dict[str, Any]:
    """Single entry point for the agent 'methods assistant' tool.
    action: 'catalogue' (full mapped list + gaps) | 'recommend' (pass flags) |
            'resolve' (map a requested/Aspen name to a DWSIM package)."""
    if action == "resolve":
        return {"success": True, **resolve_to_dwsim(model or "")}
    if action == "recommend":
        kw = {k: flags[k] for k in _RECOMMEND_FLAGS if k in flags}
        return {"success": True, "recommendation": recommend(**kw)}
    return {"success": True, **catalogue()}


def catalogue() -> Dict[str, Any]:
    """Full registry + gaps, for a 'methods assistant' tool response."""
    return {
        "n_packages": len(REGISTRY),
        "packages": [
            {"dwsim_name": m.dwsim_name, "family": m.family,
             "aspen_equivalents": m.aspen_equivalents, "best_for": m.best_for,
             "avoid_for": m.avoid_for, "regime": m.regime,
             "accuracy": m.accuracy, "notes": m.notes}
            for m in REGISTRY
        ],
        "aspen_gaps": ASPEN_GAPS,
    }
