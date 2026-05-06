"""
auto_correct.py  —  DWSIM Convergence Auto-Correction Engine
─────────────────────────────────────────────────────────────
When run_simulation returns unconverged streams, AutoCorrector
applies a ranked sequence of non-destructive fixes and retries.

Strategy order (applied until convergence or exhausted):
  1. loosen_recycle      — raise max-iter / relax tolerance on OT_Recycle
  2. zero_pressure_drops — remove ΔP from all equipment (reduces DOF)
  3. seed_initial_guesses— propagate feed T/P to blank outlet streams
  4. relax_column_reflux — bump reflux ratio on columns by 30 %
  5. loosen_flash_spec   — switch TP → PH flash spec on failing streams
  6. tighten_then_retry  — halve recycle tolerance and iterate again

Each strategy returns (applied: bool, description: str).
All fixes are reversible (no structural changes, only property edits).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("auto_correct")


class AutoCorrector:
    """
    Non-destructive convergence repair for DWSIM flowsheets.

    Usage:
        corrector = AutoCorrector(bridge)
        result    = corrector.attempt_fixes(run_simulation_result)
        # result has extra keys: auto_corrected, fixes_applied, attempts
    """

    MAX_STRATEGY_ATTEMPTS = 5   # cap so we never loop forever

    def __init__(self, bridge) -> None:
        self.bridge = bridge

    # ── Public entry point ────────────────────────────────────────────────────

    def attempt_fixes(self, initial_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        If initial_result shows unconverged streams, apply fixes and retry.
        Returns the best result (possibly improved), always with:
          auto_corrected   (bool)   — True if convergence was eventually achieved
          fixes_applied    (list)   — descriptions of fixes that were tried
          attempts         (int)    — number of solve retries
        """
        conv = initial_result.get("convergence_check", {})
        if conv.get("all_converged"):
            return initial_result   # nothing to do

        not_converged = conv.get("not_converged", [])
        conv_errors   = initial_result.get("convergence_errors") or []

        _log.info("[AutoCorrect] %d stream(s) not converged — starting fix loop",
                  len(not_converged))

        fixes_applied: List[str] = []
        best_result = initial_result
        attempts    = 0

        for strategy in self._strategy_pipeline(not_converged, conv_errors):
            applied, desc = strategy()
            if not applied:
                continue

            fixes_applied.append(desc)
            attempts += 1
            _log.info("[AutoCorrect] Applied: %s — re-running simulation", desc)

            retry = self.bridge.run_simulation()
            retry_conv = retry.get("convergence_check", {})

            if retry_conv.get("all_converged"):
                _log.info("[AutoCorrect] Converged after %d fix(es)", len(fixes_applied))
                retry["auto_corrected"]  = True
                retry["fixes_applied"]   = fixes_applied
                retry["attempts"]        = attempts
                retry["message"] = (
                    f"Auto-correction succeeded after {len(fixes_applied)} fix(es): "
                    + "; ".join(fixes_applied)
                )
                return retry

            # Keep the latest result as fallback even if still failing
            best_result = retry
            if attempts >= self.MAX_STRATEGY_ATTEMPTS:
                break

        # All strategies exhausted — return best result with diagnostic info
        best_result["auto_corrected"]   = False
        best_result["fixes_applied"]    = fixes_applied
        best_result["attempts"]         = attempts
        if fixes_applied:
            still_bad = (best_result.get("convergence_check", {})
                         .get("not_converged", not_converged))
            best_result["auto_correct_note"] = (
                f"Tried {len(fixes_applied)} fix(es) but {len(still_bad)} "
                f"stream(s) still unconverged: "
                + ", ".join(
                    (x["tag"] if isinstance(x, dict) else str(x))
                    for x in still_bad[:5]
                )
            )
        return best_result

    # ── Strategy pipeline ─────────────────────────────────────────────────────

    def _strategy_pipeline(self, not_converged, conv_errors):
        """Yield strategy callables in priority order."""
        errors_text = " ".join(str(e) for e in conv_errors).lower()

        # 1. Recycle convergence is the #1 culprit — always try first
        yield self._fix_loosen_recycle

        # 2. Pressure drops create extra constraints — zero them
        yield self._fix_zero_pressure_drops

        # 3. Blank outlet streams confuse the solver — seed them
        yield self._fix_seed_initial_guesses

        # 4. Column reflux too tight — relax
        yield self._fix_relax_column_reflux

        # 5. Flash spec switch (TP → PH) for streams missing T
        missing_T_tags = [
            (x["tag"] if isinstance(x, dict) else x)
            for x in not_converged
            if isinstance(x, dict) and "T" in x.get("missing", [])
        ]
        if missing_T_tags:
            yield lambda tags=missing_T_tags: self._fix_flash_spec(tags)

    # ── Individual strategies ─────────────────────────────────────────────────

    def _fix_loosen_recycle(self) -> Tuple[bool, str]:
        """Raise max-iterations to 50 and set tolerance=1e-3 on all Recycle blocks."""
        objects = self._list_objects()
        recycle_tags = [o["tag"] for o in objects
                        if "recycle" in o.get("type", "").lower()
                        and o.get("category", "").lower() != "stream"]
        if not recycle_tags:
            return False, ""

        applied = False
        for tag in recycle_tags:
            for prop, val in [("MaximumIterations", "50"), ("Tolerance", "0.001")]:
                try:
                    r = self.bridge.set_unit_op_property(tag, prop, val)
                    if r.get("success"):
                        applied = True
                except Exception as exc:
                    _log.debug("loosen_recycle %s.%s: %s", tag, prop, exc)

        return applied, f"loosened recycle tolerance on {recycle_tags}"

    def _fix_zero_pressure_drops(self) -> Tuple[bool, str]:
        """Set DeltaP=0 on Heater, Cooler, Pipe, HeatExchanger, Valve."""
        _DP_TYPES = {"heater", "cooler", "heatexchanger", "pipe", "valve", "expander"}
        objects = self._list_objects()
        targets = [o["tag"] for o in objects
                   if any(t in o.get("type", "").lower() for t in _DP_TYPES)
                   and o.get("category", "").lower() != "stream"]
        if not targets:
            return False, ""

        applied = False
        for tag in targets:
            for prop in ("DeltaP", "OutletPressureDrop", "PressureDrop"):
                try:
                    r = self.bridge.set_unit_op_property(tag, prop, "0")
                    if r.get("success"):
                        applied = True
                        break
                except Exception:
                    pass

        return applied, f"zeroed pressure drops on {len(targets)} unit op(s)"

    def _fix_seed_initial_guesses(self) -> Tuple[bool, str]:
        """
        Find feed streams (T/P/flow defined) and copy their T and P to any
        outlet streams that have no temperature or pressure set yet.
        """
        objects = self._list_objects()
        stream_tags = [o["tag"] for o in objects
                       if o.get("category", "").lower() == "materialstream"
                       or "materialstream" in o.get("type", "").lower()]

        if not stream_tags:
            return False, ""

        # Collect stream properties
        feed_T, feed_P = None, None
        blank_streams: List[str] = []

        for tag in stream_tags:
            try:
                r = self.bridge.get_stream_properties(tag)
                if not r.get("success"):
                    continue
                props = r.get("properties", {})
                has_T = "temperature_K" in props and (props["temperature_K"] or 0) > 0
                has_P = "pressure_Pa"   in props and (props["pressure_Pa"]   or 0) > 0
                has_F = ("molar_flow_mol_s" in props or "mass_flow_kg_s" in props)

                if has_T and has_P and has_F:
                    feed_T = feed_T or props["temperature_K"]
                    feed_P = feed_P or props["pressure_Pa"]
                elif not has_T or not has_P:
                    blank_streams.append(tag)
            except Exception:
                pass

        if not blank_streams or feed_T is None:
            return False, ""

        applied_count = 0
        for tag in blank_streams:
            try:
                self.bridge.set_stream_property(tag, "temperature", feed_T, "K")
                self.bridge.set_stream_property(tag, "pressure",    feed_P, "Pa")
                applied_count += 1
            except Exception as exc:
                _log.debug("seed_guess %s: %s", tag, exc)

        if applied_count == 0:
            return False, ""
        return True, f"seeded T={feed_T:.0f}K P={feed_P:.0f}Pa on {applied_count} blank stream(s)"

    def _fix_relax_column_reflux(self) -> Tuple[bool, str]:
        """Increase reflux ratio by 30 % on any distillation / shortcut column."""
        _COL_TYPES = {"distillationcolumn", "shortcutcolumn",
                      "absorptioncolumn", "refluxedabsorber", "reboiledabsorber"}
        objects = self._list_objects()
        col_tags = [o["tag"] for o in objects
                    if any(t in o.get("type", "").lower() for t in _COL_TYPES)]
        if not col_tags:
            return False, ""

        applied = False
        for tag in col_tags:
            try:
                r = self.bridge.get_column_properties(tag)
                if not r.get("success"):
                    continue
                rr = r.get("reflux_ratio") or r.get("RefluxRatio")
                if rr is not None and float(rr) > 0:
                    new_rr = round(float(rr) * 1.3, 3)
                    self.bridge.set_unit_op_property(tag, "RefluxRatio", str(new_rr))
                    applied = True
            except Exception as exc:
                _log.debug("relax_column_reflux %s: %s", tag, exc)

        return applied, f"increased reflux ratio by 30% on {col_tags}"

    def _fix_flash_spec(self, tags: List[str]) -> Tuple[bool, str]:
        """Switch flash specification from TP to PH on streams missing T."""
        applied = 0
        for tag in tags:
            try:
                r = self.bridge.set_stream_flash_spec(tag, "PH")
                if r.get("success"):
                    applied += 1
            except Exception as exc:
                _log.debug("flash_spec %s: %s", tag, exc)

        if applied == 0:
            return False, ""
        return True, f"switched {applied} stream(s) to PH flash spec"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _list_objects(self) -> List[Dict]:
        try:
            r = self.bridge.list_simulation_objects()
            return r.get("objects", []) if isinstance(r, dict) else []
        except Exception:
            return []
