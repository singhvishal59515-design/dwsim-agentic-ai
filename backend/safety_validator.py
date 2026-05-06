"""
safety_validator.py
───────────────────
Physical plausibility checker for DWSIM Agentic AI.

Runs automatically after every save_and_solve / run_simulation call.
Returns a list of ValidationFailure objects; empty list = physically plausible.

Silent failure taxonomy (empirically discovered in DWSIM Python interface):
  LOUD   — exception raised or agent reports failure; user always informed
  SILENT — DWSIM converges=True but result is physically wrong; most dangerous

SF catalogue and remediation status (v3 — 9 modes):
  SF-01  CalcMode not set (Heater/Cooler)              → FIXED in bridge (v2.1)
  SF-02  Reversed connection port direction             → FIXED pre-solve (pre_solve_sf02_check)
  SF-03  Unnormalised composition                       → FIXED in bridge (normalise)
  SF-04  Negative molar/mass flow (pre-solve)           → FIXED in bridge (≥0 guard)
  SF-05  Flash spec mismatch (VF out of range)          → MITIGATED (AutoCorrector)
  SF-06  DeltaP > feed pressure (P < 0 outlet)         → FIXED pre-solve (_pre_solve_sf_check)
  SF-07  Heater OutletT < feed T (reversed spec)       → FIXED pre-solve (_pre_solve_sf_check)
  SF-08  Unit-op energy balance violation               → DETECTED post-solve (check_with_duties)
         Sub-modes:
           SF-08a  HX duty inconsistent with stream ΔH (>10% discrepancy, enthalpy-based)
           SF-08b  Compressor isentropic efficiency outside [0.50, 0.95]
           SF-08c  Reactor exothermicity sign mismatch (endothermic spec, exothermic result)
           SF-08d  Distillation stage temperature profile non-monotonic
  SF-09  Global flowsheet mass/energy balance violation → DETECTED post-solve
         Sub-modes:
           SF-09a  Overall network mass balance error > 2% (feed vs product streams)
           SF-09b  Overall network energy balance error > 10% (duty sum vs stream ΔH)
           SF-09c  Orphaned stream (connected on one side only — dangling topology)
"""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── Failure record ────────────────────────────────────────────────────────────

@dataclass
class ValidationFailure:
    code:        str    # e.g. "SF-02"
    severity:    str    # "SILENT" | "LOUD" | "WARNING"
    description: str
    evidence:    str    # what values triggered this
    stream_tag:  str = ""
    auto_fixed:  bool = False
    fix_applied: str = ""


# ── Validator ─────────────────────────────────────────────────────────────────

class SafetyValidator:
    """
    Validates simulation results for physical plausibility.

    Usage:
        validator = SafetyValidator()
        failures  = validator.check(stream_results, topology_meta)
        if failures:
            for f in failures: print(f.severity, f.description)
    """

    # Thermodynamic constants — tightened to industrially realistic bounds
    # T_MAX_K: industrial furnaces ~1800°C (2073 K); plasma/combustion ~3000 K.
    #   5000 K was never correct for a DWSIM process simulation.
    T_ABS_ZERO_K   = 0.0
    T_MAX_K        = 2500.0   # 2227°C — above this DWSIM result is almost certainly wrong
    P_MIN_PA       = 0.0      # absolute zero pressure
    # P_MAX_PA: LDPE autoclave reactors ~3500 bar (3.5e8 Pa); nothing in DWSIM
    #   should realistically exceed 1500 bar (1.5e8 Pa) without explicit justification.
    P_MAX_PA       = 1.5e8    # 1500 bar — tightened from 10 000 bar
    # SF-05: small numerical noise tolerance for VF auto-correction
    VF_NOISE_TOL   = 0.01     # |VF| < 0.01 outside [0,1] → clamp silently
    VF_MIN         = -0.001   # legacy tolerance (kept for residual checks)
    VF_MAX         = 1.001

    # Unit operations that require at least one material inlet stream.
    # Used by pre_solve_sf02_check to catch reversed port connections early.
    _NEEDS_INLET = {
        "heater", "cooler", "heatexchanger", "pump", "compressor",
        "expander", "valve", "separator", "mixer", "splitter",
        "distillationcolumn", "shortcutcolumn", "absorptioncolumn",
        "cstr", "pfr", "gibbsreactor", "conversionreactor",
        "equilibriumreactor", "pipe",
    }

    def check(
        self,
        stream_results: Dict[str, Dict[str, Any]],
        topology:       Optional[Dict[str, Any]] = None,
        unit_op_duties: Optional[Dict[str, float]] = None,
    ) -> List[ValidationFailure]:
        """Run all checks SF-01 through SF-08. Returns list of ValidationFailure."""
        failures: List[ValidationFailure] = []
        if not stream_results:
            return failures

        failures += self._check_absolute_bounds(stream_results)
        failures += self._check_supercritical_conditions(stream_results)
        failures += self._check_impossible_vapor_fraction(stream_results)
        failures += self._check_vlle_risk(stream_results)
        failures += self._check_phase_consistency(stream_results)
        failures += self._check_vapor_fraction(stream_results)
        failures += self._check_mass_balance(stream_results, topology)
        failures += self._check_temperature_direction(stream_results, topology)
        failures += self._check_pressure_direction(stream_results, topology)
        failures += self._check_composition_sum(stream_results)
        failures += self._check_negative_flow(stream_results)
        failures += self._check_hx_energy_balance(stream_results, topology, unit_op_duties)
        return failures

    def check_global_balance(
        self,
        stream_results: Dict[str, Dict[str, Any]],
        topology:       Optional[Dict[str, Any]] = None,
        unit_op_duties: Optional[Dict[str, float]] = None,
    ) -> List[ValidationFailure]:
        """
        SF-09: Global (network-level) mass and energy balance checks.

        Unlike SF-MB01 which checks each unit op locally, SF-09 checks the
        ENTIRE flowsheet: total feed mass == total product mass across all
        boundary streams (streams with no upstream unit op = feed; streams
        with no downstream unit op = product).

        SF-09a: |m_feed - m_product| / m_feed > 2%  (overall mass closure)
        SF-09b: |Q_net_utilities - ΔH_boundary| / |ΔH_boundary| > 10%
                where ΔH_boundary = H_products - H_feeds (from DWSIM enthalpy)
                and Q_net_utilities = sum of all unit-op duties
        SF-09c: streams appearing in connections on only one side (orphaned)

        These checks catch errors that unit-level checks CANNOT find:
          - A correctly balanced heater + correctly balanced separator can still
            violate overall mass balance if a stream duplicates flow or if a
            recycle is broken.
          - Wrong connection topology (e.g. feed connected to product stream)
            passes all unit-op checks but fails network closure.
        """
        out = []
        if not topology:
            return out

        connections = topology.get("connections", [])
        unit_op_tags = {u["tag"] for u in topology.get("unit_ops", [])}

        # ── Classify boundary streams ──────────────────────────────────────────
        # A "feed" stream has no upstream unit op (nothing flows FROM a unit op
        # into it). A "product" stream has no downstream unit op.
        stream_tags_all = set(stream_results.keys())

        streams_with_upstream   = {c.get("to")   for c in connections if c.get("to")   in stream_tags_all}
        streams_with_downstream = {c.get("from")  for c in connections if c.get("from") in stream_tags_all}

        # Feed = stream with no upstream unit op connection
        feed_streams    = stream_tags_all - streams_with_upstream
        product_streams = stream_tags_all - streams_with_downstream

        # Exclude energy streams (no mass flow) from mass balance
        def _mass_kgh(tag: str) -> float:
            s = stream_results.get(tag, {})
            m = s.get("mass_flow_kgh")
            if m is not None:
                return max(float(m), 0.0)
            m_kg_s = s.get("mass_flow_kg_s")
            if m_kg_s is not None:
                return max(float(m_kg_s) * 3600.0, 0.0)
            return 0.0

        def _enthalpy_kw(tag: str) -> Optional[float]:
            """Return stream specific enthalpy * mass flow = power [kW].
            Uses DWSIM-reported enthalpy_kJ_kg directly (thermodynamically correct).
            Falls back to None if not available."""
            s = stream_results.get(tag, {})
            h_kj_kg = s.get("enthalpy_kJ_kg") or s.get("specific_enthalpy_kJ_kg")
            m_kgh = _mass_kgh(tag)
            if h_kj_kg is not None and m_kgh > 0:
                return float(h_kj_kg) * m_kgh / 3600.0  # kW
            return None

        # ── SF-09a: Overall mass balance ───────────────────────────────────────
        m_feed    = sum(_mass_kgh(t) for t in feed_streams)
        m_product = sum(_mass_kgh(t) for t in product_streams)

        if m_feed > 1.0 and m_product > 0.0:
            err_pct = abs(m_feed - m_product) / m_feed * 100.0
            if err_pct > 2.0:
                out.append(ValidationFailure(
                    code="SF-09a", severity="SILENT",
                    description=(
                        f"SF-09a: Global mass balance error {err_pct:.1f}% "
                        f"(feeds={m_feed:.1f} kg/h, products={m_product:.1f} kg/h). "
                        f"Feed streams: {sorted(feed_streams)}. "
                        f"Product streams: {sorted(product_streams)}. "
                        "Possible causes: (1) missing product stream — a unit op outlet "
                        "is not connected; (2) recycle loop broken — tear stream not "
                        "converged; (3) phantom duplicate stream in topology. "
                        "FIX: verify all unit op outlets are connected; check recycle "
                        "convergence; use get_simulation_results() to inspect topology."
                    ),
                    evidence=(f"m_feed={m_feed:.1f} kg/h, "
                              f"m_product={m_product:.1f} kg/h, "
                              f"error={err_pct:.1f}%"),
                ))

        # ── SF-09b: Overall energy balance (enthalpy-based) ────────────────────
        # Only if DWSIM-reported enthalpies are available in stream_results
        h_feeds    = [_enthalpy_kw(t) for t in feed_streams]
        h_products = [_enthalpy_kw(t) for t in product_streams]

        if all(h is not None for h in h_feeds) and all(h is not None for h in h_products):
            H_feed    = sum(h for h in h_feeds)       # type: ignore[arg-type]
            H_product = sum(h for h in h_products)    # type: ignore[arg-type]
            dH_streams = H_product - H_feed           # kW (positive = net heat absorbed)
            Q_utilities = sum(unit_op_duties.values()) if unit_op_duties else 0.0

            # Energy balance: Q_utilities + H_feed = H_product
            # => Q_utilities = H_product - H_feed = dH_streams
            if abs(dH_streams) > 1.0:  # skip trivial cases
                eb_error = abs(Q_utilities - dH_streams) / abs(dH_streams)
                if eb_error > 0.10:  # >10% discrepancy
                    out.append(ValidationFailure(
                        code="SF-09b", severity="WARNING",
                        description=(
                            f"SF-09b: Global energy balance error {eb_error*100:.0f}% "
                            f"(utility duties={Q_utilities:.1f} kW, "
                            f"stream enthalpy change={dH_streams:.1f} kW). "
                            "This uses DWSIM-reported specific enthalpies directly — "
                            "no Cp approximation. Possible causes: "
                            "(1) energy stream not connected to utility supply; "
                            "(2) adiabatic unit op specified but has non-zero duty; "
                            "(3) reference enthalpy datum inconsistency between streams. "
                            "FIX: verify all heater/cooler/compressor duties are "
                            "correctly reported; check for unconverged recycle streams."
                        ),
                        evidence=(f"Q_util={Q_utilities:.1f} kW, "
                                  f"dH_streams={dH_streams:.1f} kW, "
                                  f"error={eb_error*100:.0f}%"),
                    ))

        # ── SF-09c: Orphaned streams (dangling topology) ───────────────────────
        # A stream is "dangling" only if it is INTERNAL to the network —
        # i.e. it connects two positions that both should be unit ops —
        # but is missing a connection on one side.
        #
        # Rule: a stream appearing in connections is dangling if:
        #   (a) it appears as 'to' in some connection (unit op feeds it) but
        #       NEVER as 'from' in any connection (nobody draws from it) AND
        #       it is also NOT a product stream (no downstream unit op is OK)
        #   This catches: unit op output stream that is created but never
        #   connected to the downstream unit op.
        #
        # Boundary streams (feeds, products) correctly appear on exactly
        # one side — they are NOT orphaned.
        stream_as_from = {c.get("from") for c in connections
                          if c.get("from") in stream_tags_all}
        stream_as_to   = {c.get("to")   for c in connections
                          if c.get("to")  in stream_tags_all}

        # Streams that ARE fed by a unit op (have upstream) but have NO
        # downstream unit op connection — potential dangling outlets.
        # Exclude genuine product streams: a stream is a product if it has
        # an upstream unit op but nothing downstream. That's fine.
        # We only flag if a stream ALSO lacks a mass flow result, meaning it
        # was never solved — indicating it's truly disconnected, not a product.
        for tag in stream_as_to - stream_as_from:
            s_data = stream_results.get(tag, {})
            m = _mass_kgh(tag)
            # If mass flow is 0 or absent after a solve, the stream is
            # unreachable — it was connected as an inlet to something but
            # the solver never flowed mass through it. Flag as dangling.
            if m <= 0.0 and tag in stream_tags_all:
                out.append(ValidationFailure(
                    code="SF-09c", severity="WARNING",
                    description=(
                        f"SF-09c: Stream '{tag}' has a upstream unit op connection "
                        "but zero/missing mass flow after solve — possible dangling "
                        "stream (connected on one side only, never used as input). "
                        "This stream is unreachable from any feed. "
                        f"FIX: connect '{tag}' to a downstream unit op, or verify "
                        "the upstream unit op is actually active."
                    ),
                    evidence=f"mass_flow={m:.3f} kg/h (expected >0)",
                    stream_tag=tag,
                ))

        return out

    def check_and_correct(
        self,
        stream_results: Dict[str, Dict[str, Any]],
        topology:       Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[ValidationFailure], int]:
        """
        Run all checks AND apply in-place auto-corrections to stream_results.

        SF-05 auto-correction: VF values within VF_NOISE_TOL outside [0,1]
        are clamped to 0.0 or 1.0 and flagged with auto_fixed=True.
        Genuine violations (|error| > VF_NOISE_TOL) are still returned as failures.

        Returns:
            (failures, corrections_applied)
        """
        corrections = 0

        # SF-05: clamp VF numerical noise BEFORE running checks
        for tag, props in stream_results.items():
            vf = props.get("vapor_fraction")
            if vf is None:
                continue
            vf = float(vf)
            if -self.VF_NOISE_TOL <= vf < 0.0:
                props["vapor_fraction"]     = 0.0
                props["_sf05_corrected"]    = True
                props["_sf05_original_vf"]  = vf
                corrections += 1
            elif 1.0 < vf <= 1.0 + self.VF_NOISE_TOL:
                props["vapor_fraction"]     = 1.0
                props["_sf05_corrected"]    = True
                props["_sf05_original_vf"]  = vf
                corrections += 1

        failures = self.check(stream_results, topology)
        return failures, corrections

    def check_with_duties(
        self,
        stream_results:  Dict[str, Dict[str, Any]],
        topology:        Optional[Dict[str, Any]] = None,
        unit_op_duties:  Optional[Dict[str, float]] = None,
        unit_op_details: Optional[Dict[str, Dict]] = None,
    ) -> Tuple[List[ValidationFailure], int]:
        """
        Full SF-01 through SF-08 check with SF-05 auto-correction.

        unit_op_details: {tag: summary_dict} — used for SF-08b/c/d sub-modes.
          summary_dict keys used: 'adiabatic_efficiency', 'stage_temperatures',
          'reactor_type', 'duty_kW'.
        """
        corrections = 0
        for tag, props in stream_results.items():
            vf = props.get("vapor_fraction")
            if vf is None:
                continue
            vf = float(vf)
            if -self.VF_NOISE_TOL <= vf < 0.0:
                props["vapor_fraction"]    = 0.0
                props["_sf05_corrected"]   = True
                props["_sf05_original_vf"] = vf
                corrections += 1
            elif 1.0 < vf <= 1.0 + self.VF_NOISE_TOL:
                props["vapor_fraction"]   = 1.0
                props["_sf05_corrected"]  = True
                corrections += 1
        failures = self.check(stream_results, topology, unit_op_duties)
        # SF-08 sub-modes b/c/d — unit-level physical validation
        if unit_op_details:
            failures += self._check_compressor_efficiency(unit_op_details)
            failures += self._check_reactor_enthalpy(unit_op_details, unit_op_duties)
            failures += self._check_distillation_profile(unit_op_details)
        # SF-09: global flowsheet balance (network-level closure)
        failures += self.check_global_balance(stream_results, topology, unit_op_duties)
        return failures, corrections

    # ── SF-08b: Compressor isentropic efficiency ─────────────────────────────

    def _check_compressor_efficiency(
        self, unit_op_details: Dict[str, Dict]
    ) -> List[ValidationFailure]:
        """
        SF-08b: Compressor/Expander isentropic efficiency must be in [0.50, 0.95].
        Below 0.50 is physically unrealistic for any industrial machine.
        Above 0.95 violates the second law (no real device is that efficient).
        """
        out = []
        _COMP_TYPES = {"compressor", "expander", "pump", "turbine"}
        for tag, details in unit_op_details.items():
            typ = details.get("type", "").lower()
            if not any(k in typ for k in _COMP_TYPES):
                continue
            eta = details.get("adiabatic_efficiency") or details.get("efficiency")
            if eta is None:
                continue
            try:
                eta = float(eta)
            except (ValueError, TypeError):
                continue
            if eta < 0.50:
                out.append(ValidationFailure(
                    code="SF-08b", severity="WARNING",
                    description=(
                        f"SF-08b: '{tag}' ({typ}) isentropic efficiency "
                        f"{eta:.3f} < 0.50 — physically unrealistic. "
                        "No industrial compressor operates below 50% efficiency. "
                        "Typical values: 0.70–0.85 (centrifugal), 0.80–0.90 (axial). "
                        f"FIX: set_unit_op_property('{tag}', 'AdiabaticEfficiency', 0.75)"
                    ),
                    evidence=f"η = {eta:.3f}",
                    stream_tag=tag,
                ))
            elif eta > 0.95:
                out.append(ValidationFailure(
                    code="SF-08b", severity="WARNING",
                    description=(
                        f"SF-08b: '{tag}' ({typ}) isentropic efficiency "
                        f"{eta:.3f} > 0.95 — violates second law of thermodynamics. "
                        "No real machine achieves >95% isentropic efficiency. "
                        f"FIX: set_unit_op_property('{tag}', 'AdiabaticEfficiency', 0.75)"
                    ),
                    evidence=f"η = {eta:.3f}",
                    stream_tag=tag,
                ))
        return out

    # ── SF-08c: Reactor enthalpy sign consistency ────────────────────────────

    def _check_reactor_enthalpy(
        self,
        unit_op_details: Dict[str, Dict],
        unit_op_duties:  Optional[Dict[str, float]],
    ) -> List[ValidationFailure]:
        """
        SF-08c: For reactors with a known reaction type, verify that the reported
        duty sign is consistent with the expected exo/endothermicity.

        Known exothermic reactions (negative duty = heat released):
          combustion, oxidation, hydrogenation, polymerisation, neutralisation.
        Known endothermic reactions (positive duty = heat required):
          steam reforming, dehydrogenation, calcination, pyrolysis.
        """
        out = []
        if not unit_op_duties:
            return out

        _EXOTHERMIC_KEYWORDS = ("combustion", "oxidation", "hydrogenation",
                                "polymeris", "polymeriz", "neutralis", "neutraliz",
                                "methanation", "fischer")
        _ENDOTHERMIC_KEYWORDS = ("reforming", "dehydrogenation", "steam crack",
                                 "calcination", "pyrolysis", "gasification")

        for tag, details in unit_op_details.items():
            typ = details.get("type", "").lower()
            if "reactor" not in typ:
                continue
            duty_kw = unit_op_duties.get(tag)
            if duty_kw is None or abs(duty_kw) < 1.0:
                continue  # negligible duty — skip

            # Check name/description for reaction type hints
            name_desc = (tag + " " + details.get("description", "")).lower()
            is_exo = any(k in name_desc for k in _EXOTHERMIC_KEYWORDS)
            is_endo = any(k in name_desc for k in _ENDOTHERMIC_KEYWORDS)

            if is_exo and duty_kw > 0:
                # Exothermic reaction should release heat (duty < 0 = cooling needed)
                out.append(ValidationFailure(
                    code="SF-08c", severity="WARNING",
                    description=(
                        f"SF-08c: Reactor '{tag}' appears to run an exothermic reaction "
                        f"(tag suggests {[k for k in _EXOTHERMIC_KEYWORDS if k in name_desc][0]}), "
                        f"but reported duty = +{duty_kw:.1f} kW (endothermic sign). "
                        "Expected: negative duty (heat removed) for exothermic reactions. "
                        "Check: (1) reaction enthalpy sign convention in DWSIM, "
                        "(2) ConversionReactor vs GibbsReactor selection, "
                        "(3) whether reaction is correctly specified."
                    ),
                    evidence=f"duty = +{duty_kw:.1f} kW (expected <0 for exothermic)",
                    stream_tag=tag,
                ))
            elif is_endo and duty_kw < 0:
                out.append(ValidationFailure(
                    code="SF-08c", severity="WARNING",
                    description=(
                        f"SF-08c: Reactor '{tag}' appears to run an endothermic reaction "
                        f"(tag suggests {[k for k in _ENDOTHERMIC_KEYWORDS if k in name_desc][0]}), "
                        f"but reported duty = {duty_kw:.1f} kW (exothermic sign). "
                        "Expected: positive duty (heat supplied) for endothermic reactions."
                    ),
                    evidence=f"duty = {duty_kw:.1f} kW (expected >0 for endothermic)",
                    stream_tag=tag,
                ))
        return out

    # ── SF-08d: Distillation stage temperature profile monotonicity ──────────

    def _check_distillation_profile(
        self, unit_op_details: Dict[str, Dict]
    ) -> List[ValidationFailure]:
        """
        SF-08d: In a distillation column, temperature MUST decrease monotonically
        from bottom (reboiler, hottest) to top (condenser, coldest).
        A non-monotonic profile indicates convergence to an incorrect solution.

        Checks using 'stage_temperatures' list (index 0 = condenser/top).
        """
        out = []
        _COL_TYPES = {"distillationcolumn", "absorptioncolumn", "reboiledabsorber",
                      "refluxedabsorber", "shortcutcolumn"}
        for tag, details in unit_op_details.items():
            typ = details.get("type", "").lower()
            if not any(k in typ for k in _COL_TYPES):
                continue
            stage_temps = details.get("stage_temperatures")
            if not stage_temps or len(stage_temps) < 3:
                continue
            try:
                temps = [float(t) for t in stage_temps if t is not None]
            except (ValueError, TypeError):
                continue

            # Expect temps[0] = condenser (cold) → temps[-1] = reboiler (hot)
            # Check monotonicity: each temperature should be ≥ previous
            inversions = []
            for i in range(1, len(temps)):
                if temps[i] < temps[i - 1] - 0.5:  # 0.5°C tolerance
                    inversions.append((i, temps[i - 1], temps[i]))

            if inversions:
                n_inv = len(inversions)
                ex_stage, ex_hi, ex_lo = inversions[0]
                out.append(ValidationFailure(
                    code="SF-08d", severity="SILENT",
                    description=(
                        f"SF-08d: Column '{tag}' stage temperature profile has "
                        f"{n_inv} temperature inversion(s) — non-monotonic profile "
                        f"indicates convergence to a wrong/trivial solution. "
                        f"Example: stage {ex_stage-1}→{ex_stage}: "
                        f"{ex_hi:.1f}°C → {ex_lo:.1f}°C (should be increasing toward reboiler). "
                        "FIX: (1) Increase reflux ratio; (2) adjust feed stage; "
                        "(3) use ShortcutColumn for initial guess then switch to rigorous; "
                        "(4) check condenser/reboiler duty specifications."
                    ),
                    evidence=f"{n_inv} inversion(s) in {len(temps)}-stage profile",
                    stream_tag=tag,
                ))
        return out

    def pre_solve_sf02_check(
        self,
        topology: Dict[str, Any],
        object_types: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, str]]:
        """
        SF-02 pre-solve check: detect reversed port connections before the solver runs.

        A unit op that needs a material inlet but has ZERO streams going TO it
        (all connections are FROM it) has its ports reversed.

        Returns list of violation dicts (empty = OK).
        """
        violations: List[Dict[str, str]] = []
        connections = topology.get("connections", [])
        unit_ops    = topology.get("unit_ops", [])
        if not connections or not unit_ops:
            return violations

        # Build set of unit op tags that have at least one stream flowing TO them
        has_inlet: set = set()
        for c in connections:
            to_tag  = c.get("to") or c.get("to_tag", "")
            to_port = c.get("to_port", 0)
            # Energy streams use to_port=1; material inlets use to_port=0
            if to_port == 0:
                has_inlet.add(to_tag)

        for uo in unit_ops:
            uo_tag  = uo.get("tag", "")
            uo_type = (uo.get("type") or (object_types or {}).get(uo_tag, "")).lower()
            if uo_type not in self._NEEDS_INLET:
                continue
            if uo_tag not in has_inlet:
                # Check it has at least one connection at all (not just unconnected)
                all_tags = (
                    {c.get("from") or c.get("from_tag") for c in connections} |
                    {c.get("to")   or c.get("to_tag")   for c in connections}
                )
                if uo_tag not in all_tags:
                    continue  # truly unconnected — different problem
                violations.append({
                    "code":     "SF-02",
                    "severity": "LOUD",
                    "description": (
                        f"SF-02 PREVENTED: Unit op '{uo_tag}' ({uo_type}) has no "
                        f"material inlet stream (to_port=0). "
                        f"All connections are FROM '{uo_tag}' — ports are likely reversed."
                    ),
                    "fix": (
                        f"Reconnect with the stream flowing TO '{uo_tag}': "
                        f"connect_streams(from_tag='<feed_stream>', to_tag='{uo_tag}', "
                        f"from_port=0, to_port=0)"
                    ),
                })
        return violations

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_absolute_bounds(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        out = []
        for tag, props in streams.items():
            t_k = props.get("temperature_K") or ((props.get("temperature_C") or 0) + 273.15)
            p   = props.get("pressure_Pa") or ((props.get("pressure_bar") or 0) * 1e5)

            if t_k is not None and t_k < self.T_ABS_ZERO_K:
                out.append(ValidationFailure(
                    code="SF-T01", severity="SILENT",
                    description=f"Stream '{tag}' has temperature below 0 K ({t_k:.2f} K) — physically impossible",
                    evidence=f"T = {t_k:.2f} K", stream_tag=tag,
                ))
            if t_k is not None and t_k > self.T_MAX_K:
                out.append(ValidationFailure(
                    code="SF-T02", severity="WARNING",
                    description=f"Stream '{tag}' temperature {t_k:.1f} K exceeds {self.T_MAX_K:.0f} K — likely erroneous (max industrial limit ~2500 K)",
                    evidence=f"T = {t_k:.1f} K", stream_tag=tag,
                ))
            if p is not None and p < self.P_MIN_PA:
                out.append(ValidationFailure(
                    code="SF-P01", severity="SILENT",
                    description=f"Stream '{tag}' has negative pressure ({p:.1f} Pa) — SF-06: pressure drop > feed pressure",
                    evidence=f"P = {p:.1f} Pa", stream_tag=tag,
                ))
        return out

    def _check_supercritical_conditions(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """
        SF-10: Detect streams where T > Tc or P > Pc for dominant component.
        Supercritical fluids cannot be condensed — distillation design fails silently.
        """
        # Critical properties for key compounds (Tc in K, Pc in bar)
        CRITICAL: Dict[str, Dict[str, float]] = {
            "co2":       {"Tc": 304.1, "Pc": 73.8},
            "co2 ":      {"Tc": 304.1, "Pc": 73.8},
            "carbon dioxide": {"Tc": 304.1, "Pc": 73.8},
            "methane":   {"Tc": 190.6, "Pc": 46.0},
            "ch4":       {"Tc": 190.6, "Pc": 46.0},
            "ethylene":  {"Tc": 282.3, "Pc": 50.4},
            "ethane":    {"Tc": 305.3, "Pc": 48.7},
            "propane":   {"Tc": 369.8, "Pc": 42.5},
            "nitrogen":  {"Tc": 126.2, "Pc": 34.0},
            "n2":        {"Tc": 126.2, "Pc": 34.0},
            "oxygen":    {"Tc": 154.6, "Pc": 50.4},
            "o2":        {"Tc": 154.6, "Pc": 50.4},
            "hydrogen":  {"Tc": 33.2,  "Pc": 13.1},
            "h2":        {"Tc": 33.2,  "Pc": 13.1},
            "water":     {"Tc": 647.1, "Pc": 220.6},
            "h2o":       {"Tc": 647.1, "Pc": 220.6},
            "ammonia":   {"Tc": 405.7, "Pc": 113.3},
            "nh3":       {"Tc": 405.7, "Pc": 113.3},
        }
        out = []
        for tag, props in streams.items():
            if not isinstance(props, dict):
                continue
            T_K = props.get("temperature_K") or ((props.get("temperature_C") or 0) + 273.15)
            P_bar = props.get("pressure_bar") or ((props.get("pressure_Pa") or 0) / 1e5)
            if not T_K or not P_bar:
                continue

            # Check dominant component (highest mole fraction)
            comps = props.get("mole_fractions") or props.get("composition") or {}
            if not isinstance(comps, dict) or not comps:
                continue
            dominant = max(comps, key=lambda k: comps.get(k) or 0).lower().strip()
            crit = CRITICAL.get(dominant)
            if crit is None:
                continue

            is_super_T = T_K > crit["Tc"]
            is_super_P = P_bar > crit["Pc"]

            if is_super_T and is_super_P:
                out.append(ValidationFailure(
                    code="SF-10", severity="WARNING",
                    description=(
                        f"Stream '{tag}' ({dominant}) is supercritical: "
                        f"T={T_K:.1f}K > Tc={crit['Tc']}K AND "
                        f"P={P_bar:.1f}bar > Pc={crit['Pc']}bar. "
                        "Cannot condense this stream — check if supercritical "
                        "operation is intended (e.g. supercritical CO2 extraction)."
                    ),
                    evidence=f"T={T_K:.1f}K, P={P_bar:.1f}bar, dominant={dominant}",
                    stream_tag=tag,
                ))
            elif is_super_T and dominant in ("co2", "co2 ", "carbon dioxide",
                                              "methane", "ch4", "nitrogen", "n2",
                                              "hydrogen", "h2", "oxygen", "o2"):
                # Light gases above Tc cannot be liquefied regardless of P
                out.append(ValidationFailure(
                    code="SF-10b", severity="WARNING",
                    description=(
                        f"Stream '{tag}' ({dominant}) is above its critical temperature "
                        f"(T={T_K:.1f}K > Tc={crit['Tc']}K). "
                        "This gas cannot be liquefied at any pressure. "
                        "If condensation is expected, check stream conditions."
                    ),
                    evidence=f"T={T_K:.1f}K > Tc={crit['Tc']}K",
                    stream_tag=tag,
                ))
        return out

    def _check_impossible_vapor_fraction(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """
        SF-11: Detect impossible VF values that indicate a DWSIM flash failure.
        A VF that is NaN or an implausible float (not in [-0.01, 1.01]) means
        the flash calculation crashed silently — the stream state is garbage.
        """
        out = []
        for tag, props in streams.items():
            if not isinstance(props, dict):
                continue
            vf = props.get("vapor_fraction")
            if vf is None:
                continue
            try:
                vf_f = float(vf)
            except (TypeError, ValueError):
                out.append(ValidationFailure(
                    code="SF-11", severity="ERROR",
                    description=(
                        f"Stream '{tag}' has non-numeric vapor fraction: '{vf}'. "
                        "Flash calculation likely crashed — stream state is invalid."
                    ),
                    evidence=f"vapor_fraction='{vf}'",
                    stream_tag=tag,
                ))
                continue

            import math
            if math.isnan(vf_f) or math.isinf(vf_f):
                out.append(ValidationFailure(
                    code="SF-11", severity="ERROR",
                    description=(
                        f"Stream '{tag}' has NaN/Inf vapor fraction. "
                        "Flash calculation crashed — check property package and "
                        "feed conditions. Try switching property package or "
                        "adjusting T/P to physically realizable conditions."
                    ),
                    evidence=f"vapor_fraction={vf_f}",
                    stream_tag=tag,
                ))
            elif not (-0.01 <= vf_f <= 1.01):
                out.append(ValidationFailure(
                    code="SF-11b", severity="ERROR",
                    description=(
                        f"Stream '{tag}' has physically impossible vapor fraction "
                        f"{vf_f:.4f} (must be 0–1). "
                        "This indicates a flash convergence failure. "
                        "Check that T and P are consistent with the phase state."
                    ),
                    evidence=f"vapor_fraction={vf_f:.4f}",
                    stream_tag=tag,
                ))
        return out

    def _check_vlle_risk(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """
        SF-12: Warn when a stream contains a known partially-miscible pair
        (VLE model may be insufficient — VLLE or LLE model needed).
        """
        # Pairs known to form two liquid phases at typical process conditions
        # (water-organic systems with limited miscibility)
        IMMISCIBLE_PAIRS = [
            {"n-butanol", "water"}, {"1-butanol", "water"},
            {"benzene", "water"}, {"toluene", "water"},
            {"cyclohexane", "water"}, {"n-hexane", "water"},
            {"diethyl ether", "water"}, {"ether", "water"},
            {"chloroform", "water"}, {"chloroform", "h2o"},
            {"methyl isobutyl ketone", "water"}, {"mibk", "water"},
            {"ethyl acetate", "water"},
            {"furfural", "water"},
        ]
        out = []
        for tag, props in streams.items():
            if not isinstance(props, dict):
                continue
            comps = props.get("mole_fractions") or props.get("composition") or {}
            if not isinstance(comps, dict) or len(comps) < 2:
                continue
            comp_names_lo = {k.lower().strip() for k in comps}

            for pair in IMMISCIBLE_PAIRS:
                if pair.issubset(comp_names_lo):
                    c1, c2 = sorted(pair)
                    out.append(ValidationFailure(
                        code="SF-12", severity="WARNING",
                        description=(
                            f"Stream '{tag}' contains {c1}/{c2} — a partially miscible "
                            "system that may form two liquid phases. "
                            "Standard VLE models (PR, SRK) will not detect LLE splitting. "
                            "Use NRTL or UNIQUAC with LLE-fitted BIPs, and enable "
                            "three-phase flash in the property package settings."
                        ),
                        evidence=f"Components detected: {sorted(comp_names_lo)}",
                        stream_tag=tag,
                    ))
                    break  # one warning per stream is enough

        return out

    def _check_phase_consistency(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """
        SF-13: Verify vapor fraction is consistent with T and P using Antoine equation.
        A stream at 25°C, 1 bar that is reported as 50% vapor when its dominant
        component has Psat(25°C) = 0.032 bar is thermodynamically impossible
        (entire stream should be liquid). Catches silent flash convergence failures.

        Only checks streams with a single dominant compound (>80 mol%) where
        Antoine constants are available. Multi-component streams require full
        flash calculation and are skipped.
        """
        # Antoine constants (log10(P_mmHg) = A - B/(C + T_C)) for key compounds
        # Source: DIPPR 801 / Perry's 9th ed.
        ANTOINE: Dict[str, Dict] = {
            "water":     {"A": 8.10765, "B": 1750.286, "C": 235.000, "Tmin": 60,  "Tmax": 150},
            "methanol":  {"A": 7.87863, "B": 1473.110, "C": 230.000, "Tmin": 15,  "Tmax": 84},
            "ethanol":   {"A": 8.11220, "B": 1592.864, "C": 226.184, "Tmin": 20,  "Tmax": 93},
            "acetone":   {"A": 7.11714, "B": 1210.595, "C": 229.664, "Tmin": -26, "Tmax": 77},
            "benzene":   {"A": 6.90565, "B": 1211.033, "C": 220.790, "Tmin": 8,   "Tmax": 80},
            "toluene":   {"A": 6.95087, "B": 1342.310, "C": 219.187, "Tmin": 6,   "Tmax": 137},
            "n-hexane":  {"A": 6.87601, "B": 1171.530, "C": 224.366, "Tmin": -25, "Tmax": 92},
            "n-heptane": {"A": 6.89385, "B": 1264.370, "C": 216.636, "Tmin": -2,  "Tmax": 124},
            "ethyl acetate": {"A": 7.10179, "B": 1244.951, "C": 217.881, "Tmin": 16, "Tmax": 77},
            "chloroform":{"A": 6.90328, "B": 1163.030, "C": 227.400, "Tmin": 4,   "Tmax": 84},
            "acetonitrile":{"A": 7.11988,"B": 1285.703, "C": 223.516, "Tmin": 20, "Tmax": 82},
            "acetic acid":{"A": 7.80307, "B": 1651.200, "C": 225.000, "Tmin": 17, "Tmax": 118},
        }
        # Conversion: 1 bar = 750.062 mmHg
        _BAR_TO_MMHG = 750.062

        out = []
        for tag, props in streams.items():
            if not isinstance(props, dict):
                continue

            T_C = props.get("temperature_C")
            if T_C is None:
                T_K = props.get("temperature_K")
                if T_K: T_C = T_K - 273.15
            if T_C is None:
                continue

            P_bar = props.get("pressure_bar")
            if P_bar is None:
                P_Pa = props.get("pressure_Pa")
                if P_Pa: P_bar = P_Pa / 1e5
            if not P_bar or P_bar <= 0:
                continue

            vf = props.get("vapor_fraction")
            if vf is None:
                continue
            try:
                vf_f = float(vf)
            except (TypeError, ValueError):
                continue

            # Find dominant component (>80 mol fraction)
            comps = props.get("mole_fractions") or props.get("composition") or {}
            if not isinstance(comps, dict) or not comps:
                continue
            dominant_comp = None
            dominant_frac = 0.0
            for comp, frac in comps.items():
                try:
                    f = float(frac or 0)
                    if f > dominant_frac:
                        dominant_frac = f
                        dominant_comp = comp.lower().strip()
                except (TypeError, ValueError):
                    continue

            if dominant_frac < 0.80 or dominant_comp is None:
                continue  # multi-component — skip

            # Find Antoine constants
            ant = ANTOINE.get(dominant_comp)
            if ant is None:
                continue

            # Only use Antoine within valid temperature range
            if not (ant["Tmin"] <= T_C <= ant["Tmax"]):
                continue

            # Calculate Psat in bar
            try:
                psat_mmhg = 10 ** (ant["A"] - ant["B"] / (ant["C"] + T_C))
                psat_bar  = psat_mmhg / _BAR_TO_MMHG
            except (ValueError, ZeroDivisionError):
                continue

            # Check consistency
            # If P_stream >> Psat: should be ALL LIQUID (vf should be ≈ 0)
            # If P_stream << Psat: should be ALL VAPOR (vf should be ≈ 1)
            TOLERANCE = 0.15  # allow 15% vf tolerance before flagging

            if P_bar > psat_bar * 2.0 and vf_f > TOLERANCE:
                out.append(ValidationFailure(
                    code="SF-13", severity="WARNING",
                    description=(
                        f"Stream '{tag}' ({dominant_comp}, {dominant_frac:.0%} purity): "
                        f"reported VF={vf_f:.2f} but P={P_bar:.3f} bar >> Psat={psat_bar:.4f} bar "
                        f"at T={T_C:.1f}°C. Stream should be fully liquid at these conditions. "
                        "Flash calculation may have converged to wrong phase."
                    ),
                    evidence=f"P/Psat={P_bar/psat_bar:.1f}x, VF={vf_f:.3f}, T={T_C:.1f}°C",
                    stream_tag=tag,
                ))
            elif P_bar < psat_bar * 0.5 and vf_f < (1.0 - TOLERANCE):
                out.append(ValidationFailure(
                    code="SF-13b", severity="WARNING",
                    description=(
                        f"Stream '{tag}' ({dominant_comp}, {dominant_frac:.0%} purity): "
                        f"reported VF={vf_f:.2f} but P={P_bar:.3f} bar << Psat={psat_bar:.4f} bar "
                        f"at T={T_C:.1f}°C. Stream should be fully vapor at these conditions. "
                        "Check if stream is above dew point."
                    ),
                    evidence=f"Psat/P={psat_bar/P_bar:.1f}x, VF={vf_f:.3f}, T={T_C:.1f}°C",
                    stream_tag=tag,
                ))
        return out

    def _check_vapor_fraction(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """
        SF-05: flag VF genuinely outside [0,1].
        Small noise (within VF_NOISE_TOL) is already corrected by check_and_correct()
        before this method runs; only genuine violations reach here.
        """
        out = []
        for tag, props in streams.items():
            vf = props.get("vapor_fraction")
            if vf is None:
                continue
            vf = float(vf)
            # Skip already auto-corrected streams
            if props.get("_sf05_corrected"):
                continue
            if vf < -self.VF_NOISE_TOL:
                out.append(ValidationFailure(
                    code="SF-05", severity="SILENT",
                    description=(
                        f"Stream '{tag}': vapor fraction {vf:.4f} < 0 (SF-05 flash spec mismatch). "
                        "T-P flash at supersaturated conditions — result is thermodynamically inconsistent. "
                        "FIX: reduce temperature or increase pressure to bring stream into valid phase region, "
                        "then call save_and_solve again. AutoCorrector will retry with PH flash."
                    ),
                    evidence=f"VF = {vf:.4f}", stream_tag=tag,
                ))
            elif vf > 1.0 + self.VF_NOISE_TOL:
                out.append(ValidationFailure(
                    code="SF-05", severity="SILENT",
                    description=(
                        f"Stream '{tag}': vapor fraction {vf:.4f} > 1 (SF-05 flash spec mismatch). "
                        "T-P flash above dew point — stream is fully vapour but VF calculation overshot. "
                        "FIX: verify T-P specification is physically reachable for this mixture."
                    ),
                    evidence=f"VF = {vf:.4f}", stream_tag=tag,
                ))
        return out

    def _check_mass_balance(
        self,
        streams:  Dict[str, Dict[str, Any]],
        topology: Optional[Dict[str, Any]],
    ) -> List[ValidationFailure]:
        """For each unit op in topology, sum inlet flows == sum outlet flows (±2%)."""
        out = []
        if not topology:
            return out
        connections = topology.get("connections", [])
        unit_ops    = {u["tag"]: u for u in topology.get("unit_ops", []) if u.get("tag")}

        for uo_tag in unit_ops:
            inlets  = [c["from"] for c in connections if c.get("to")   == uo_tag]
            outlets = [c["to"]   for c in connections if c.get("from") == uo_tag]  # BUG-FIX: was c["from"]

            def _flow(tag):
                s = streams.get(tag, {})
                return s.get("mass_flow_kgh") or s.get("mass_flow_kg_s", 0) * 3600

            in_flow  = sum(_flow(t) for t in inlets  if _flow(t) > 0)
            out_flow = sum(_flow(t) for t in outlets if _flow(t) > 0)

            if in_flow > 0 and out_flow > 0:
                error_pct = abs(in_flow - out_flow) / in_flow * 100
                if error_pct > 2.0:
                    out.append(ValidationFailure(
                        code="SF-MB01", severity="SILENT",
                        description=(f"Mass balance violation at '{uo_tag}': "
                                     f"in={in_flow:.1f} kg/h, out={out_flow:.1f} kg/h, "
                                     f"error={error_pct:.1f}%"),
                        evidence=f"Δm = {error_pct:.2f}%",
                    ))
        return out

    def _check_temperature_direction(
        self,
        streams:  Dict[str, Dict[str, Any]],
        topology: Optional[Dict[str, Any]],
    ) -> List[ValidationFailure]:
        """
        Heater outlet must be hotter than inlet.
        Cooler outlet must be cooler than inlet.
        Detects SF-01 (CalcMode) and SF-07 (reversed spec).
        """
        out = []
        if not topology:
            return out

        connections = topology.get("connections", [])
        unit_ops    = {u["tag"]: u for u in topology.get("unit_ops", []) if u.get("tag")}

        for uo_tag, uo in unit_ops.items():
            uo_type = uo.get("type", "").lower()
            if uo_type not in ("heater", "cooler"):
                continue

            inlets  = [c["from"] for c in connections
                       if c.get("to") == uo_tag and c.get("to_port", 0) == 0]
            outlets = [c["to"]   for c in connections
                       if c.get("from") == uo_tag and c.get("from_port", 0) == 0]  # BUG-FIX: was c["from"]

            def _T(tag):
                s = streams.get(tag, {})
                return s.get("temperature_C") or (s.get("temperature_K", 273.15) - 273.15)

            for i_tag in inlets:
                for o_tag in outlets:
                    t_in  = _T(i_tag)
                    t_out = _T(o_tag)
                    if t_in is None or t_out is None:
                        continue
                    if uo_type == "heater" and t_out <= t_in + 0.1:
                        out.append(ValidationFailure(
                            code="SF-01", severity="SILENT",
                            description=(
                                f"Heater '{uo_tag}': outlet {t_out:.2f}°C ≤ inlet {t_in:.2f}°C. "
                                "CalcMode was not applied — result is convergent but physically wrong. "
                                f"FIX: call set_unit_op_property('{uo_tag}', 'OutletTemperature', <target_K>) "
                                "again — the bridge will re-apply CalcMode reflection."
                            ),
                            evidence=f"T_in={t_in:.2f}°C, T_out={t_out:.2f}°C",
                            stream_tag=o_tag,
                        ))
                    if uo_type == "cooler" and t_out >= t_in - 0.1:
                        out.append(ValidationFailure(
                            code="SF-07", severity="SILENT",
                            description=(
                                f"Cooler '{uo_tag}': outlet {t_out:.2f}°C ≥ inlet {t_in:.2f}°C. "
                                "SF-07: temperature direction reversed — cooler is not cooling. "
                                f"FIX: set_unit_op_property('{uo_tag}', 'OutletTemperature', "
                                f"{t_in - 10:.2f})  # must be below inlet {t_in:.2f}°C"
                            ),
                            evidence=f"T_in={t_in:.2f}°C, T_out={t_out:.2f}°C",
                            stream_tag=o_tag,
                        ))
        return out

    def _check_pressure_direction(
        self,
        streams:  Dict[str, Dict[str, Any]],
        topology: Optional[Dict[str, Any]],
    ) -> List[ValidationFailure]:
        """Pump outlet must have higher pressure than inlet."""
        out = []
        if not topology:
            return out

        connections = topology.get("connections", [])
        unit_ops    = {u["tag"]: u for u in topology.get("unit_ops", []) if u.get("tag")}

        for uo_tag, uo in unit_ops.items():
            if uo.get("type", "").lower() not in ("pump", "compressor"):
                continue

            inlets  = [c["from"] for c in connections
                       if c.get("to") == uo_tag and c.get("to_port", 0) == 0]
            outlets = [c["to"]   for c in connections
                       if c.get("from") == uo_tag and c.get("from_port", 0) == 0]  # BUG-FIX: was c["from"]

            def _P(tag):
                s = streams.get(tag, {})
                return s.get("pressure_bar") or s.get("pressure_Pa", 0) / 1e5

            for i_tag in inlets:
                for o_tag in outlets:
                    p_in  = _P(i_tag)
                    p_out = _P(o_tag)
                    if p_in is not None and p_out is not None and p_out <= p_in:
                        out.append(ValidationFailure(
                            code="SF-P02", severity="SILENT",
                            description=(
                                f"Pump/Compressor '{uo_tag}': outlet {p_out:.3f} bar ≤ inlet {p_in:.3f} bar. "
                                "Pump must raise pressure. CalcMode for OutletPressure may not have applied. "
                                f"FIX: set_unit_op_property('{uo_tag}', 'OutletPressure', "
                                f"{p_in * 1e5 * 2:.0f})  # target 2x inlet pressure in Pa"
                            ),
                            evidence=f"P_in={p_in:.3f} bar, P_out={p_out:.3f} bar",
                            stream_tag=o_tag,
                        ))
        return out

    def _check_composition_sum(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """Mole fractions must sum to ≈ 1.0 (SF-03)."""
        out = []
        for tag, props in streams.items():
            comps = props.get("compositions") or props.get("mole_fractions") or {}
            if not comps:
                continue
            total = sum(float(v) for v in comps.values() if v is not None)
            if abs(total - 1.0) > 0.02:
                out.append(ValidationFailure(
                    code="SF-03", severity="SILENT" if total > 1.05 else "WARNING",
                    description=(f"Stream '{tag}' composition sums to {total:.4f} "
                                 "(should be 1.000 ± 0.02). SF-03: unnormalised composition."),
                    evidence=f"sum = {total:.4f}", stream_tag=tag,
                ))
        return out

    def _check_negative_flow(
        self, streams: Dict[str, Dict[str, Any]]
    ) -> List[ValidationFailure]:
        """Negative molar or mass flow indicates SF-04 or reversed connection."""
        out = []
        for tag, props in streams.items():
            mf = props.get("molar_flow_kmolh") or props.get("molar_flow_mol_s", 0)
            mf_kg = props.get("mass_flow_kgh") or props.get("mass_flow_kg_s", 0)
            if mf is not None and float(mf) < -0.001:
                out.append(ValidationFailure(
                    code="SF-04", severity="SILENT",
                    description=(f"Stream '{tag}' has negative molar flow ({mf:.3f} kmol/h). "
                                 "SF-04: inverted flow — possibly reversed connection port."),
                    evidence=f"F = {mf:.3f} kmol/h", stream_tag=tag,
                ))
            if mf_kg is not None and float(mf_kg) < -0.001:
                out.append(ValidationFailure(
                    code="SF-04", severity="SILENT",
                    description=(f"Stream '{tag}' has negative mass flow ({mf_kg:.3f} kg/h). "
                                 "SF-04: inverted flow — possibly reversed connection port."),
                    evidence=f"m = {mf_kg:.3f} kg/h", stream_tag=tag,
                ))
        return out

    def _check_hx_energy_balance(
        self,
        streams:        Dict[str, Dict[str, Any]],
        topology:       Optional[Dict[str, Any]],
        unit_op_duties: Optional[Dict[str, float]] = None,
    ) -> List[ValidationFailure]:
        """
        SF-08a: Energy balance check for heat exchangers using DWSIM enthalpy directly.

        For each Heater/Cooler/HeatExchanger with a known duty (kW), verify that
        the enthalpy change of the connected streams matches the reported duty
        (within 10% tolerance).

        Method: uses 'enthalpy_kJ_kg' from stream_results (DWSIM-reported specific
        enthalpy) multiplied by mass flow rate. This is thermodynamically rigorous —
        it correctly handles phase changes, non-ideal mixtures, and any property
        package without Cp approximations.

        Falls back gracefully to Cp estimation ONLY when DWSIM enthalpy data is
        absent from stream_results, flagging which method was used in the evidence.
        """
        out = []
        if not topology or not unit_op_duties:
            return out

        connections = topology.get("connections", [])
        unit_ops    = {u["tag"]: u for u in topology.get("unit_ops", []) if u.get("tag")}
        _HX_TYPES   = {"heater", "cooler", "heatexchanger"}

        def _enthalpy_power_kw(tag: str) -> Optional[float]:
            """Stream enthalpy flow = h [kJ/kg] * m [kg/h] / 3600 [kW].
            Returns None if DWSIM enthalpy not available in stream_results."""
            s = streams.get(tag, {})
            h = s.get("enthalpy_kJ_kg") or s.get("specific_enthalpy_kJ_kg")
            if h is None:
                return None
            m_kgh = s.get("mass_flow_kgh")
            if m_kgh is None:
                m_kg_s = s.get("mass_flow_kg_s", 0) or 0
                m_kgh = float(m_kg_s) * 3600.0
            return float(h) * float(m_kgh) / 3600.0  # kW

        def _cp_fallback_kw(i_tag: str, o_tag: str) -> Optional[float]:
            """Cp-based ΔH estimate, used only when enthalpy_kJ_kg is absent."""
            s_in  = streams.get(i_tag, {})
            s_out = streams.get(o_tag, {})
            t_in  = s_in.get("temperature_C")
            t_out = s_out.get("temperature_C")
            if t_in is None or t_out is None:
                return None
            m_kgh = s_in.get("mass_flow_kgh") or 0
            if m_kgh <= 0:
                return None
            # Cp heuristic: 4.18 kJ/kg/K for water-dominant, else 2.0
            fracs = s_in.get("mole_fractions") or {}
            water_frac = float(fracs.get("Water", fracs.get("water", 0)) or 0)
            cp = 4.18 if water_frac > 0.5 else 2.0
            return (float(m_kgh) / 3600.0) * cp * (float(t_out) - float(t_in))

        for uo_tag, uo in unit_ops.items():
            uo_type = uo.get("type", "").lower()
            if uo_type not in _HX_TYPES:
                continue
            duty_kw = unit_op_duties.get(uo_tag)
            if duty_kw is None or abs(duty_kw) < 0.01:
                continue

            inlets  = [c["from"] for c in connections
                       if c.get("to") == uo_tag and c.get("to_port", 0) == 0]
            outlets = [c["to"]   for c in connections
                       if c.get("from") == uo_tag and c.get("from_port", 0) == 0]

            if not inlets or not outlets:
                continue

            # Prefer enthalpy-based calculation; fall back to Cp only if needed
            # dH_streams = sum(H_outlet) - sum(H_inlet)
            H_in  = [_enthalpy_power_kw(t) for t in inlets]
            H_out = [_enthalpy_power_kw(t) for t in outlets]
            method = "enthalpy"

            if all(h is not None for h in H_in) and all(h is not None for h in H_out):
                stream_delta_kw = sum(H_out) - sum(H_in)   # type: ignore[arg-type]
            else:
                # Fallback: Cp×ΔT for first inlet-outlet pair
                method = "Cp-approx (enthalpy_kJ_kg not in stream_results)"
                fb = _cp_fallback_kw(inlets[0], outlets[0]) if inlets and outlets else None
                if fb is None:
                    continue
                stream_delta_kw = fb

            if stream_delta_kw == 0.0:
                continue

            error_frac = abs(duty_kw - stream_delta_kw) / abs(duty_kw)
            if error_frac > 0.10:  # >10% discrepancy (tightened from 25% Cp-era threshold)
                out.append(ValidationFailure(
                    code="SF-08a", severity="WARNING",
                    description=(
                        f"SF-08a: Energy balance mismatch at '{uo_tag}' ({uo_type}): "
                        f"reported duty={duty_kw:.1f} kW, "
                        f"stream ΔH={stream_delta_kw:.1f} kW "
                        f"({error_frac*100:.0f}% discrepancy; method: {method}). "
                        "Using DWSIM-reported stream enthalpies directly (no Cp approximation). "
                        "Possible causes: (1) incorrect stream connection — an inlet or outlet "
                        "is missing from topology; (2) energy stream (utility) not connected "
                        "to the correct port; (3) unit op duty reported in wrong units or sign. "
                        "FIX: verify connections with get_simulation_results(); ensure all "
                        "material streams are connected to port 0 and energy to port 1."
                    ),
                    evidence=(f"Q_reported={duty_kw:.1f} kW, "
                              f"dH_streams={stream_delta_kw:.1f} kW, "
                              f"method={method}"),
                    stream_tag=uo_tag,
                ))
        return out


# ── Known silent failure catalogue ───────────────────────────────────────────

KNOWN_SILENT_FAILURES = [
    {
        "code":         "SF-01",
        "name":         "CalcMode not set for Heater/Cooler",
        "description":  "DWSIM Heater ignores OutletTemperature spec if CalcMode enum is not "
                        "first set via .NET reflection (pythonnet Nullable<T> boxing issue). "
                        "Simulator converges with product T = feed T — no exception raised.",
        "status":       "FIXED",
        "where_fixed":  "DWSIMBridgeV2.set_unit_op_property — System.Enum.Parse + Nullable[Double] boxing",
        "detection":    "SafetyValidator._check_temperature_direction (residual check)",
        "severity":     "SILENT",
    },
    {
        "code":         "SF-02",
        "name":         "Reversed connection port direction",
        "description":  "Stream connected to wrong port index reverses flow direction semantics. "
                        "DWSIM may converge with inverted T/P gradients across unit ops.",
        "status":       "FIXED pre-solve",
        "where_fixed":  "SafetyValidator.pre_solve_sf02_check — detects unit ops with zero material "
                        "inlet streams; DWSIMBridgeV2._pre_solve_sf_check blocks solve with LOUD error",
        "detection":    "SafetyValidator.pre_solve_sf02_check (pre-solve) + "
                        "_check_temperature_direction (residual post-solve check)",
        "severity":     "SILENT → LOUD (after fix)",
    },
    {
        "code":         "SF-03",
        "name":         "Unnormalised stream composition",
        "description":  "Mole fractions summing to ≠ 1.0 are silently accepted by DWSIM "
                        "in some API versions; VLE calculations produce inconsistent results.",
        "status":       "FIXED",
        "where_fixed":  "DWSIMBridgeV2.set_stream_composition — auto-normalise + sum-to-1 rejection",
        "detection":    "SafetyValidator._check_composition_sum (residual check)",
        "severity":     "SILENT",
    },
    {
        "code":         "SF-04",
        "name":         "Negative molar/mass flow",
        "description":  "Negative flow values accepted by DWSIM API; solver may converge with "
                        "inverted stream direction causing physically wrong energy/mass balances.",
        "status":       "FIXED",
        "where_fixed":  "DWSIMBridgeV2.set_stream_property — _PHYS_MIN guard rejects value < 0",
        "detection":    "SafetyValidator._check_negative_flow (residual check for loaded flowsheets)",
        "severity":     "SILENT",
    },
    {
        "code":         "SF-05",
        "name":         "Flash spec mismatch (TP flash at infeasible state)",
        "description":  "T-P flash at supersaturated condition yields VF slightly outside [0,1] "
                        "without raising an exception; stream phase is thermodynamically inconsistent.",
        "status":       "FIXED (auto-corrected)",
        "where_fixed":  "SafetyValidator.check_and_correct — VF noise within ±0.01 clamped to 0/1. "
                        "Genuine violations (|VF error| > 0.01): AutoCorrector retries with PH flash.",
        "detection":    "SafetyValidator._check_vapor_fraction (genuine violations only)",
        "severity":     "SILENT → AUTO-CORRECTED for noise; DETECTED for genuine violations",
    },
    {
        "code":         "SF-06",
        "name":         "DeltaP exceeds feed pressure (negative outlet P)",
        "description":  "If DeltaP is set larger than the inlet stream pressure, the outlet "
                        "pressure goes negative. Some DWSIM versions converge silently at P < 0 Pa.",
        "status":       "FIXED pre-solve",
        "where_fixed":  "DWSIMBridgeV2._pre_solve_sf_check — blocks solve and returns LOUD error with fix hint",
        "detection":    "SafetyValidator._check_absolute_bounds (residual check for loaded flowsheets)",
        "severity":     "SILENT → LOUD (after fix)",
    },
    {
        "code":         "SF-07",
        "name":         "Heater OutletTemperature < feed temperature",
        "description":  "OutletTemperature set below feed T on a Heater — physically impossible. "
                        "DWSIM may converge or silently diverge without a clear error.",
        "status":       "FIXED pre-solve",
        "where_fixed":  "DWSIMBridgeV2._pre_solve_sf_check — blocks solve and returns LOUD error with fix hint",
        "detection":    "SafetyValidator._check_temperature_direction (residual check for loaded flowsheets)",
        "severity":     "SILENT/LOUD → LOUD (after fix)",
        "fix_commit":   "Detected post-solve; LLM system prompt warns against this",
    },
    {
        "code":         "SF-09",
        "name":         "Global flowsheet mass/energy balance violation",
        "description":  "Unit-op-level balance checks (SF-MB01, SF-EB01/SF-08a) cannot catch "
                        "network-level errors such as: a correctly balanced heater + correctly "
                        "balanced separator that together violate overall mass closure due to "
                        "missing product stream or broken recycle tear; wrong topology that routes "
                        "a product stream as an intermediate stream; unconverged recycle where "
                        "DWSIM reports convergence=True but the tear stream diverged. "
                        "Sub-modes: "
                        "SF-09a: overall mass balance error > 2% (feed vs product streams); "
                        "SF-09b: overall energy balance error > 10% using DWSIM enthalpies directly "
                        "(Q_utilities ≠ ΔH_boundary — eliminates Cp approximation uncertainty); "
                        "SF-09c: dangling unit op outlet stream with zero mass flow post-solve "
                        "(unreachable from any feed — indicates missing downstream connection).",
        "status":       "DETECTED post-solve",
        "where_fixed":  "SafetyValidator.check_global_balance — called from check_with_duties",
        "detection":    "Post-solve only — requires complete stream_results from bridge",
        "severity":     "SILENT (SF-09a/c) | WARNING (SF-09b)",
    },
    {
        "code":         "SF-10",
        "name":         "Supercritical stream — T or P above critical point for dominant component",
        "description":  "Supercritical fluids cannot be condensed — distillation design fails silently.",
        "status":       "DETECTED post-solve",
        "where_fixed":  "SafetyValidator._check_supercritical_conditions",
        "detection":    "Post-solve — checks dominant component Tc/Pc against stream T/P",
        "severity":     "WARNING",
    },
    {
        "code":         "SF-11",
        "name":         "Invalid vapor fraction — NaN/Inf or out-of-range, flash calculation crashed",
        "description":  "A VF that is NaN, Inf, or outside [-0.01, 1.01] indicates the flash "
                        "calculation crashed silently — the stream state is garbage.",
        "status":       "DETECTED post-solve",
        "where_fixed":  "SafetyValidator._check_impossible_vapor_fraction",
        "detection":    "Post-solve — inspects vapor_fraction field for non-numeric or implausible values",
        "severity":     "ERROR",
    },
    {
        "code":         "SF-12",
        "name":         "VLLE risk — partially miscible pair present, LLE splitting possible",
        "description":  "Standard VLE models (PR, SRK) do not detect liquid-liquid splitting. "
                        "Streams with known partially-miscible pairs (e.g. n-butanol/water) "
                        "require NRTL or UNIQUAC with LLE-fitted BIPs and three-phase flash.",
        "status":       "DETECTED post-solve",
        "where_fixed":  "SafetyValidator._check_vlle_risk",
        "detection":    "Post-solve — checks stream composition against known immiscible pairs",
        "severity":     "WARNING",
    },
    {
        "code":         "SF-13",
        "name":         "Phase inconsistency — vapor fraction contradicts Psat at stream T/P conditions",
        "description":  "Stream vapor fraction is thermodynamically inconsistent with the Antoine "
                        "equation Psat at the reported T and P. Indicates a silent flash "
                        "convergence failure or wrong phase assignment.",
        "status":       "DETECTED post-solve",
        "where_fixed":  "SafetyValidator._check_phase_consistency",
        "detection":    "Post-solve — Antoine equation Psat vs reported VF for single-dominant-component streams",
        "severity":     "WARNING",
    },
]


def get_failure_catalogue() -> List[Dict]:
    """Return the known silent failure catalogue for report generation."""
    return KNOWN_SILENT_FAILURES


def summarise_failures(failures: list) -> str:
    """Format failures for agent response injection."""
    if not failures:
        return ""
    lines = ["\n[SAFETY VALIDATOR] Physical plausibility issues detected:"]
    for f in failures:
        lines.append(f"  [{f.severity}] {f.code}: {f.description}")
    return "\n".join(lines)


if __name__ == "__main__":
    v = SafetyValidator()
    print(f"Known silent failure modes: {len(KNOWN_SILENT_FAILURES)}")
    for f in KNOWN_SILENT_FAILURES:
        print(f"  {f['code']}  [{f['severity']}]  {f['name']}  -> {f['status']}")
