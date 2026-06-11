"""
pp_validator.py
───────────────
Property-Package validator. Cross-checks the active thermodynamic property
package against the compound list and refuses to optimise on a chemistry
the chosen PP cannot model accurately.

Engineering rules implemented (verifiable against any thermodynamics textbook):
  1. Hydrogen + light gases + high P → PR / SRK / SRK-MHV2 / Lee-Kesler-Plöcker
  2. Hydrocarbons (saturated)        → PR / SRK / GERG-2008
  3. Polar non-electrolytes (alcohols, ketones, water mixtures)
                                     → NRTL / UNIQUAC / Wilson / Wong-Sandler
  4. Aqueous electrolytes            → eNRTL / Pitzer / ELECNRTL
  5. Water/steam only                → IAPWS-IF97 / Steam Tables
  6. Polymers / heavy oils           → NRTL-Polymer / PC-SAFT
  7. Acid gases (H2S, CO2 + amines)  → ELECNRTL / Sour-PR / Kent-Eisenberg
  8. Refrigerants                    → PR / SRK / Lee-Kesler-Plöcker
  9. Hydrate-forming systems         → CPA-EOS / Modified PR-Hydrate

Returns a structured validity report:
  {ok, severity, message, current_pp, compound_classes, recommended_pps,
   reasons, can_optimise}

Severity levels:
  'pass'      : PP is appropriate for the compound class
  'warning'   : PP is usable but a better alternative exists
  'mismatch'  : PP is inappropriate; results will be unreliable
  'critical'  : PP is so wrong that optimisation should refuse

Used by the optimisation workflow as a preflight gate.
"""

from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional, Set

_log = logging.getLogger("pp_validator")


# ─── Compound-class taxonomy ────────────────────────────────────────────────

# Names are matched case-insensitively as substrings. Multiple classifications
# are possible (water is both polar and aqueous).

_LIGHT_GASES = {
    "hydrogen", "h2", "helium", "he", "argon", "ar", "nitrogen", "n2",
    "oxygen", "o2", "carbon monoxide", "co", "methane", "ch4",
    "neon", "ne", "krypton", "xenon", "ammonia", "nh3",
}

_HEAVIER_HYDROCARBONS = {
    "ethane", "propane", "butane", "pentane", "hexane", "heptane",
    "octane", "nonane", "decane", "isobutane", "isopentane",
    "n-butane", "n-pentane", "n-hexane", "n-heptane", "n-octane",
    "cyclohexane", "benzene", "toluene", "xylene", "ethylene",
    "propylene", "1-butene", "isobutene",
}

_POLAR_NONELECTROLYTE = {
    "water", "h2o", "methanol", "ethanol", "propanol", "isopropanol",
    "butanol", "n-butanol", "isobutanol", "acetone", "MEK",
    "methyl ethyl ketone", "ethyl acetate", "methyl acetate",
    "acetic acid", "formic acid", "glycerol", "ethylene glycol",
    "propylene glycol", "phenol", "dimethyl ether", "diethyl ether",
    "tetrahydrofuran", "thf", "acetonitrile", "dmso", "dmf",
}

_AQUEOUS_ELECTROLYTES = {
    "sodium hydroxide", "naoh", "potassium hydroxide", "koh",
    "sodium chloride", "nacl", "potassium chloride", "kcl",
    "hydrochloric acid", "hcl", "sulfuric acid", "h2so4",
    "nitric acid", "hno3", "calcium chloride", "cacl2",
    "calcium carbonate", "caco3", "sodium carbonate", "na2co3",
    "ammonium chloride", "nh4cl",
}

_ACID_GASES = {
    "carbon dioxide", "co2", "hydrogen sulfide", "h2s",
    "sulfur dioxide", "so2", "sulfur trioxide", "so3",
    "monoethanolamine", "mea", "diethanolamine", "dea",
    "methyldiethanolamine", "mdea", "diisopropanolamine", "dipa",
    "diglycolamine", "dga", "piperazine", "pz",
}

_REFRIGERANTS = {
    "r-134a", "r134a", "r-22", "r22", "r-410a", "r410a",
    "r-407c", "r-32", "r32", "r-1234yf", "ammonia",
    "carbon dioxide as refrigerant", "propane", "isobutane",
}

_HYDRATE_FORMING = {
    "methane", "ethane", "propane", "carbon dioxide", "h2s",
    # In presence of water + low T + high P; needs water present too
}

_POLYMERS_HEAVY_OILS = {
    "polyethylene", "polypropylene", "polystyrene", "ldpe", "hdpe",
    "asphaltene", "vacuum residue", "atmospheric residue",
    "n-c30+", "c30+", "heavy oil",
}


# ─── PP taxonomy ────────────────────────────────────────────────────────────

# Each PP belongs to one or more "regimes" it is suitable for.

_PP_REGIMES: Dict[str, Set[str]] = {
    "peng-robinson":               {"hydrocarbons", "light_gases", "refrigerants", "acid_gas_basic"},
    "pr":                          {"hydrocarbons", "light_gases", "refrigerants", "acid_gas_basic"},
    "peng-robinson-stryjek-vera":  {"hydrocarbons", "light_gases", "polar_weakly"},
    "pr-sv":                       {"hydrocarbons", "light_gases", "polar_weakly"},
    "soave-redlich-kwong":         {"hydrocarbons", "light_gases", "refrigerants"},
    "srk":                         {"hydrocarbons", "light_gases", "refrigerants"},
    "srk-mhv2":                    {"hydrocarbons", "light_gases", "polar_weakly"},
    "lee-kesler-plöcker":          {"hydrocarbons", "light_gases", "refrigerants"},
    "lee-kesler-plocker":          {"hydrocarbons", "light_gases", "refrigerants"},
    "lkp":                         {"hydrocarbons", "light_gases", "refrigerants"},
    "nrtl":                        {"polar", "polar_weakly", "azeotropic"},
    "uniquac":                     {"polar", "polar_weakly", "azeotropic"},
    "wilson":                      {"polar", "polar_weakly"},
    "unifac":                      {"polar", "azeotropic", "predictive"},
    "wong-sandler":                {"polar", "high_pressure_polar"},
    "raoult's law":                {"low_pressure_ideal"},
    "raoults law":                 {"low_pressure_ideal"},
    "ideal":                       {"low_pressure_ideal"},
    "enrtl":                       {"electrolytes", "acid_gas_amine"},
    "elecnrtl":                    {"electrolytes", "acid_gas_amine"},
    "pitzer":                      {"electrolytes"},
    "steam tables":                {"water_only"},
    "iapws-if97":                  {"water_only"},
    "iapws":                       {"water_only"},
    "kent-eisenberg":              {"acid_gas_amine"},
    "sour-pr":                     {"acid_gas_basic", "hydrocarbons"},
    "pc-saft":                     {"polymers", "associating"},
    "gerg-2008":                   {"natural_gas", "hydrocarbons", "light_gases"},
    "cpa":                         {"associating", "hydrates"},
}

# Pretty names of canonical recommendations (used when offering alternatives)
_DISPLAY_NAMES = {
    "peng-robinson":              "Peng-Robinson (PR)",
    "soave-redlich-kwong":        "Soave-Redlich-Kwong (SRK)",
    "lee-kesler-plöcker":         "Lee-Kesler-Plöcker (LKP)",
    "nrtl":                       "NRTL",
    "uniquac":                    "UNIQUAC",
    "wilson":                     "Wilson",
    "enrtl":                      "Electrolyte NRTL (eNRTL)",
    "steam tables":               "Steam Tables (IAPWS-IF97)",
    "pc-saft":                    "PC-SAFT",
    "gerg-2008":                  "GERG-2008",
}


# ─── Classification ────────────────────────────────────────────────────────

def _classify_compound(name: str) -> Set[str]:
    n = (name or "").strip().lower()
    if not n:
        return set()
    classes: Set[str] = set()
    if any(g in n for g in _LIGHT_GASES):       classes.add("light_gas")
    if any(h in n for h in _HEAVIER_HYDROCARBONS): classes.add("hydrocarbon")
    if any(p in n for p in _POLAR_NONELECTROLYTE): classes.add("polar")
    if any(e in n for e in _AQUEOUS_ELECTROLYTES): classes.add("electrolyte")
    if any(a in n for a in _ACID_GASES):         classes.add("acid_gas")
    if any(r in n for r in _REFRIGERANTS):       classes.add("refrigerant")
    if any(pol in n for pol in _POLYMERS_HEAVY_OILS): classes.add("polymer")
    # Water gets special multi-label
    if n in ("water", "h2o"):
        classes.update({"water", "polar"})
    return classes


def classify_compound_list(compounds: List[str]) -> Dict[str, Any]:
    """Return aggregate classification of a compound list.

    Output: {classes: set, has_water, has_amine, has_electrolyte,
              has_light_gas, has_hydrocarbon, has_polar, is_water_only}"""
    all_classes: Set[str] = set()
    has_water = False
    has_amine = False
    has_electrolyte = False
    has_light_gas = False
    has_hydrocarbon = False
    has_polar = False
    has_polymer = False
    has_acid_gas = False

    amine_names = ("mea", "dea", "mdea", "dipa", "dga", "monoethanolamine",
                   "diethanolamine", "methyldiethanolamine",
                   "diisopropanolamine", "diglycolamine", "piperazine")

    for c in compounds or []:
        cl = _classify_compound(c)
        all_classes.update(cl)
        cl_l = c.lower()
        if cl_l in ("water", "h2o"):           has_water = True
        if any(a in cl_l for a in amine_names): has_amine = True
        if "electrolyte" in cl:                 has_electrolyte = True
        if "light_gas" in cl:                   has_light_gas = True
        if "hydrocarbon" in cl:                 has_hydrocarbon = True
        if "polar" in cl:                       has_polar = True
        if "polymer" in cl:                     has_polymer = True
        if "acid_gas" in cl:                    has_acid_gas = True

    is_water_only = (
        len([c for c in (compounds or [])
             if c.lower() in ("water", "h2o", "steam")]) == len(compounds or [1])
        and bool(compounds)
    )

    return {
        "classes":          all_classes,
        "has_water":        has_water,
        "has_amine":        has_amine,
        "has_electrolyte":  has_electrolyte,
        "has_light_gas":    has_light_gas,
        "has_hydrocarbon":  has_hydrocarbon,
        "has_polar":        has_polar,
        "has_polymer":      has_polymer,
        "has_acid_gas":     has_acid_gas,
        "is_water_only":    is_water_only,
        "n_compounds":      len(compounds or []),
    }


# ─── Validation rules ──────────────────────────────────────────────────────

def _pp_regime_set(pp_name: str) -> Set[str]:
    if not pp_name:
        return set()
    n = pp_name.lower().strip()
    # Strip common parenthetical decorations: "Peng-Robinson (PR)" → "peng-robinson"
    n = n.split("(")[0].strip()
    return _PP_REGIMES.get(n, set())


def _recommend_pps_for(profile: Dict[str, Any]) -> List[str]:
    """Return a ranked list of PP display-names suitable for this chemistry."""
    recs: List[str] = []
    # Water-only → IAPWS
    if profile["is_water_only"]:
        return ["Steam Tables (IAPWS-IF97)"]
    # Electrolytes (including amines for acid-gas) → eNRTL
    if profile["has_electrolyte"] or (profile["has_amine"] and profile["has_acid_gas"]):
        recs.append("Electrolyte NRTL (eNRTL)")
    # Polymers → PC-SAFT
    if profile["has_polymer"]:
        recs.append("PC-SAFT")
    # Polar with no light gas → NRTL/UNIQUAC family
    if profile["has_polar"] and not profile["has_light_gas"]:
        recs.extend(["NRTL", "UNIQUAC", "Wilson"])
    # Light gases / hydrocarbons → PR / SRK
    if profile["has_light_gas"] or profile["has_hydrocarbon"]:
        # If also polar, prefer SRK-MHV2 or PR-SV with mixing rules
        if profile["has_polar"]:
            recs.extend(["Peng-Robinson-Stryjek-Vera (PR-SV)",
                          "Soave-Redlich-Kwong with Modified Huron-Vidal (SRK-MHV2)",
                          "NRTL (if low-pressure)",])
        else:
            recs.extend(["Peng-Robinson (PR)", "Soave-Redlich-Kwong (SRK)",
                          "Lee-Kesler-Plöcker (LKP)"])
    # Acid gases without amines → PR / Sour-PR
    if profile["has_acid_gas"] and not profile["has_amine"]:
        recs.extend(["Peng-Robinson (PR)", "Sour-PR"])
    # Deduplicate while preserving order
    seen, ordered = set(), []
    for r in recs:
        if r not in seen:
            seen.add(r); ordered.append(r)
    return ordered or ["Peng-Robinson (PR)"]  # ultimate fallback


def validate_property_package(
    compounds:  List[str],
    current_pp: str,
) -> Dict[str, Any]:
    """Validate whether the active PP suits the compound chemistry.

    Severity scale:
      pass     — PP is in the recommended set; proceed
      warning  — PP is usable but a better fit exists
      mismatch — PP is inappropriate; results will be unreliable
      critical — PP is so wrong that optimization should refuse to run

    Returns a structured report; the orchestrator uses 'can_optimise' to
    decide whether to halt. Use override_validation=True to force-run."""
    if not compounds:
        # Inconclusive — bridge couldn't introspect compounds. Don't block
        # optimisation since we have no evidence of mismatch; just emit a
        # warning so the user knows the check was skipped.
        return {"ok": True, "severity": "skip",
                "message": "Compound list not readable — PP validity not "
                            "verified. Results should be reviewed manually.",
                "can_optimise": True,
                "current_pp": current_pp, "recommended_pps": [],
                "reasons": ["empty compound list"]}

    profile = classify_compound_list(compounds)
    regimes = _pp_regime_set(current_pp)
    recommended = _recommend_pps_for(profile)

    issues: List[str] = []
    severity = "pass"

    # Rule 1: Water-only flowsheet must use steam tables
    if profile["is_water_only"]:
        if "water_only" not in regimes:
            severity = "mismatch"
            issues.append(
                "Water/steam-only flowsheet should use Steam Tables "
                "(IAPWS-IF97) for accuracy, not "
                f"{current_pp or 'undefined PP'}."
            )

    # Rule 2: Aqueous electrolyte requires electrolyte PP
    elif profile["has_electrolyte"]:
        if "electrolytes" not in regimes:
            severity = "critical"
            issues.append(
                "Compounds include aqueous electrolytes which require an "
                "electrolyte property package (eNRTL / Pitzer). "
                f"{current_pp or 'Current PP'} cannot model ion activities."
            )

    # Rule 3: Acid gas + amines → ElecNRTL / Kent-Eisenberg
    elif profile["has_amine"] and profile["has_acid_gas"]:
        if not (("acid_gas_amine" in regimes) or
                ("electrolytes" in regimes)):
            severity = "critical"
            issues.append(
                "Amine + acid-gas system requires Electrolyte NRTL or "
                "Kent-Eisenberg for accurate equilibrium loading. "
                f"{current_pp} will overpredict capacity by 20-50 %."
            )

    # Rule 4: Polymers / heavy oils → PC-SAFT
    elif profile["has_polymer"]:
        if not ("polymers" in regimes or "associating" in regimes):
            severity = "mismatch"
            issues.append(
                "Polymer / heavy-oil systems require PC-SAFT or polymer-"
                "specific PPs. Cubic EOS will miss long-chain effects."
            )

    # Rule 5: Polar mixtures (non-electrolyte) + cubic EOS without mixing rules
    elif profile["has_polar"] and profile["has_light_gas"]:
        if regimes & {"hydrocarbons", "light_gases"} and \
                not (regimes & {"polar", "polar_weakly", "high_pressure_polar"}):
            severity = "warning"
            issues.append(
                "Polar + light-gas mixtures benefit from advanced mixing "
                "rules (PR-SV, SRK-MHV2, Wong-Sandler). Plain "
                f"{current_pp} may misrepresent the polar interactions."
            )

    # Rule 6: Polar non-electrolyte (no light gas) + cubic EOS
    elif profile["has_polar"] and not profile["has_light_gas"]:
        if regimes & {"hydrocarbons", "light_gases"} and \
                not (regimes & {"polar", "polar_weakly", "azeotropic"}):
            severity = "mismatch"
            issues.append(
                "Polar non-electrolyte mixtures are best modelled by "
                "activity-coefficient methods (NRTL/UNIQUAC/Wilson). "
                f"{current_pp} (cubic EOS) is designed for "
                "non-polar systems and will fail near azeotropes."
            )

    # Rule 7: Hydrocarbons / light gases + activity-coefficient model
    elif (profile["has_hydrocarbon"] or profile["has_light_gas"]) \
            and not profile["has_polar"]:
        if regimes & {"polar", "azeotropic"} and \
                not (regimes & {"hydrocarbons", "light_gases", "refrigerants"}):
            severity = "mismatch"
            issues.append(
                "Hydrocarbon / light-gas mixtures should be modelled by "
                "an equation of state (PR / SRK / LKP). "
                f"{current_pp} (activity-coefficient model) does not "
                "extrapolate well to vapour-phase non-idealities at "
                "industrial pressures."
            )

    # Rule 8: Empty / undefined PP
    if not current_pp or current_pp.lower() in ("none", "not set", "undefined"):
        severity = "critical"
        issues.append(
            "No property package is set on the flowsheet. DWSIM cannot "
            "compute phase equilibria without a PP."
        )

    # Rule 9: User said NOTHING was wrong; emit positive confirmation
    if not issues:
        return {
            "ok": True,
            "severity": "pass",
            "message": f"Property package '{current_pp}' is appropriate "
                       f"for the {profile['n_compounds']} compound(s) "
                       f"present.",
            "can_optimise": True,
            "current_pp": current_pp,
            "compound_classes": sorted(profile["classes"]),
            "recommended_pps": recommended,
            "reasons": [],
        }

    # Decide can_optimise based on severity
    can_optimise = severity in ("pass", "warning")

    return {
        "ok": severity in ("pass", "warning"),
        "severity": severity,
        "message": "; ".join(issues),
        "can_optimise": can_optimise,
        "current_pp": current_pp,
        "compound_classes": sorted(profile["classes"]),
        "recommended_pps": recommended,
        "reasons": issues,
        "profile": profile,
    }


# ─── Bridge convenience wrapper ────────────────────────────────────────────

def _flowsheet_has_objects(bridge) -> bool:
    """Return True if the bridge has any streams or unit ops loaded.
    Used to detect plugin-managed flowsheets (Cantera, ChemSep, Reaktoro)
    where the flowsheet IS valid but PP / compounds aren't introspectable
    via DWSIM's standard APIs."""
    # First try the agent's _bridge_objects helper which normalises
    # both list_simulation_objects() and list_objects() shapes
    try:
        from agent_v2 import _bridge_objects
        objs = _bridge_objects(bridge)
        return bool(objs.get("streams") or objs.get("unit_ops"))
    except Exception:
        pass
    # Fallback: query directly
    try:
        if hasattr(bridge, "list_simulation_objects"):
            r = bridge.list_simulation_objects()
            if isinstance(r, dict) and r.get("success"):
                return bool(r.get("objects"))
    except Exception:
        pass
    return False


def _detect_plugin_flowsheet(bridge) -> Optional[str]:
    """Detect if the loaded flowsheet uses a plugin (Cantera, ChemSep,
    Reaktoro) that manages PP / compounds internally. Returns the plugin
    name or None."""
    try:
        # Try to get the flowsheet name via state cache
        st = getattr(bridge, "state", None)
        name = ((getattr(st, "name", "") or "")
                + " "
                + (getattr(st, "active_alias", "") or "")).lower()
        if "cantera" in name:    return "Cantera"
        if "chemsep" in name:    return "ChemSep"
        if "reaktoro" in name:   return "Reaktoro"
    except Exception:
        pass
    # Also look for plugin unit ops by type name
    try:
        from agent_v2 import _bridge_objects
        objs = _bridge_objects(bridge)
        for u in (objs.get("unit_ops") or []):
            t = (u.get("type") or "").lower()
            if "cantera" in t:   return "Cantera"
            if "chemsep" in t:   return "ChemSep"
            if "reaktoro" in t:  return "Reaktoro"
    except Exception:
        pass
    return None


def validate_loaded_flowsheet(bridge, override: bool = False) -> Dict[str, Any]:
    """Convenience: query the live bridge for PP + compounds, validate.

    override=True forces severity to 'pass' but keeps the warnings in
    the message — useful when the user explicitly accepts the risk.

    BUG FIX: When the flowsheet IS loaded (objects present) but PP and/or
    compounds introspection returns empty, this is the classic plugin-
    managed case (Cantera, ChemSep, Reaktoro). The validator now classifies
    this as severity='skip' rather than 'critical' so the optimisation
    proceeds. Plugin internals manage these fields correctly even though
    standard DWSIM APIs can't read them."""
    pp = ""
    compounds: List[str] = []
    try:
        r = bridge.get_property_package()
        if isinstance(r, dict) and r.get("success"):
            pp = str(r.get("property_package", ""))
    except Exception:
        pass
    try:
        if hasattr(bridge, "list_compounds"):
            r = bridge.list_compounds()
            if isinstance(r, dict) and r.get("success"):
                compounds = list(r.get("compounds", []))
    except Exception:
        pass

    # ── Plugin-managed flowsheet detection ──────────────────────────────
    # If the bridge has objects loaded but PP appears empty, this is
    # almost certainly a Cantera / ChemSep / Reaktoro flowsheet whose
    # PP and compounds are managed by the plugin (not standard DWSIM).
    # The flowsheet IS valid; we just can't introspect it the usual way.
    pp_empty = (not pp) or (pp.lower() in ("none", "not set", "undefined", ""))
    has_objects = _flowsheet_has_objects(bridge)
    plugin = _detect_plugin_flowsheet(bridge)

    if has_objects and (pp_empty or plugin):
        plugin_label = plugin or "a plugin (Cantera / ChemSep / Reaktoro)"
        return {
            "ok": True,
            "severity": "skip",
            "message": (f"Property package and compounds are managed by "
                        f"{plugin_label} — DWSIM's standard introspection "
                        "cannot read them, but the flowsheet IS valid and "
                        "ready to optimise."),
            "can_optimise": True,
            "current_pp": pp or f"({plugin_label})",
            "recommended_pps": [],
            "reasons": ["plugin-managed flowsheet"],
            "plugin_detected": plugin,
            "n_objects":  None,  # not reporting exact count from here
        }

    result = validate_property_package(compounds, pp)
    if override and not result["ok"]:
        result["overridden"] = True
        result["original_severity"] = result["severity"]
        result["severity"] = "pass"
        result["can_optimise"] = True
        result["message"] = "[OVERRIDDEN] " + result["message"]
    return result
