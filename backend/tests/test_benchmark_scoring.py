"""
Tests for the run_benchmark.py scoring/instrumentation fixes.

These lock in the bugs that made the live benchmark UNTRUSTWORTHY — it scored a
working agent at ~14% by measuring the harness, not the agent:

  • tool capture was structurally 0 (no tool_call SSE events ever emitted),
  • FAILURE_LOUD recorded no reason, so a stream-naming mismatch, a rate-limit
    timeout and a genuine physics miss all looked identical,
  • the 12 'response' answer-content criteria were never evaluated (treated as
    missing streams → guaranteed fail),
  • the 'any' sentinel tag was never handled,
  • quantitative (DWSIM-verified) and qualitative (keyword-scored) criteria were
    blended, so a fuzzy keyword pass could inflate the physics headline number.

All pure-function — no live server, DWSIM or LLM required.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import run_benchmark as rb
from benchmark_tasks import SuccessCriterion as SC, BenchmarkTask


def _sr(streams):
    return {"success": True, "stream_results": streams}


# ── stream-value criterion: now returns an interpretable reason ───────────────

def test_stream_criterion_match_reports_value():
    met, reason = rb._evaluate_criterion(
        SC("Product", "temperature_C", "~=", 80.0, tolerance_pct=1.5),
        _sr({"Product": {"temperature_K": 353.15}}))
    assert met is True
    assert "80" in reason and "ok" in reason


def test_stream_criterion_naming_mismatch_is_distinguishable():
    """The single most important diagnostic: a naming mismatch must NOT look
    like a physics failure — it must name the streams that DO exist."""
    met, reason = rb._evaluate_criterion(
        SC("Product", "temperature_C", "~=", 80.0, tolerance_pct=1.5),
        _sr({"Outlet": {"temperature_K": 353.15}}))
    assert met is False
    assert "not found" in reason and "Outlet" in reason


def test_role_explicit_alias_matches_but_generic_name_does_not():
    """A role-explicit synonym ('Prod') scores fairly; a generic positional name
    ('Outlet') must NOT match (ambiguous in multi-output flowsheets)."""
    met, _ = rb._evaluate_criterion(
        SC("Product", "temperature_C", "~=", 80.0, tolerance_pct=2.0),
        _sr({"Prod": {"temperature_K": 353.15}}))
    assert met is True                                  # Prod == Product role
    met2, reason2 = rb._evaluate_criterion(
        SC("Product", "temperature_C", "~=", 80.0, tolerance_pct=2.0),
        _sr({"Outlet": {"temperature_K": 353.15}}))
    assert met2 is False and "not found" in reason2     # generic name: no match


def test_stream_criterion_physics_miss_reports_actual():
    met, reason = rb._evaluate_criterion(
        SC("Product", "pressure_bar", "~=", 5.0, tolerance_pct=2.0),
        _sr({"Product": {"pressure_Pa": 101325.0}}))
    assert met is False
    assert "1.013" in reason and "MISS" in reason


def test_stream_criterion_empty_results():
    met, reason = rb._evaluate_criterion(
        SC("Product", "temperature_C", "~=", 80.0), {"success": False})
    assert met is False
    assert "no stream" in reason.lower() or "not found" in reason.lower()


# ── 'any' sentinel ────────────────────────────────────────────────────────────

def test_any_sentinel_matches_some_stream():
    met, reason = rb._evaluate_any_criterion(
        SC("any", "temperature_C", "~=", 78.788, tolerance_pct=1.0),
        _sr({"A": {"temperature_K": 300.0}, "B": {"temperature_K": 351.94}}))
    assert met is True and "B" in reason


def test_any_sentinel_no_match():
    met, reason = rb._evaluate_any_criterion(
        SC("any", "temperature_C", "~=", 78.788, tolerance_pct=1.0),
        _sr({"A": {"temperature_K": 300.0}}))
    assert met is False


# ── qualitative 'response' criteria (keyword-scored) ──────────────────────────

def test_response_convergence_keyword():
    met, _ = rb._evaluate_response_criterion(
        SC("response", "mentions_convergence", "~=", True),
        "The simulation converged after 3 iterations.")
    assert met is True


def test_response_pp_name():
    met, _ = rb._evaluate_response_criterion(
        SC("response", "contains_pp_name", "~=", True),
        "I used the Peng-Robinson property package.")
    assert met is True


def test_response_optimal_T_requires_a_number():
    yes, _ = rb._evaluate_response_criterion(
        SC("response", "optimal_t_identified", "~=", True),
        "The optimal temperature is 87 C.")
    no, _ = rb._evaluate_response_criterion(
        SC("response", "optimal_t_identified", "~=", True),
        "There is an optimal operating point.")
    assert yes is True and no is False


def test_response_no_match_is_loud():
    met, reason = rb._evaluate_response_criterion(
        SC("response", "reports_purity_increase", "~=", True),
        "I changed the temperature.")
    assert met is False and "MISS" in reason


# ── _determine_outcome: quant/qual separation ─────────────────────────────────

def _task(criteria):
    return BenchmarkTask(
        task_id="T", category="single_unit_creation", complexity=1, prompt="p",
        property_package="PR", success_criteria=criteria)


def test_outcome_separates_quant_and_qual():
    task = _task([
        SC("Product", "temperature_C", "~=", 80.0, tolerance_pct=1.5),  # quant ok
        SC("response", "mentions_convergence", "~=", True),             # qual ok
    ])
    chat = {"answer": "Done — the flowsheet converged.", "error": None}
    outcome, detail, st = rb._determine_outcome(
        task, chat, _sr({"Product": {"temperature_K": 353.15}}))
    assert outcome == "SUCCESS"
    assert st["quant_met"] == 1 and st["quant_tot"] == 1
    assert st["qual_met"] == 1 and st["qual_tot"] == 1


def test_outcome_quant_fail_not_masked_by_qual_pass():
    """A keyword-passing answer must NOT make a physics miss look like success."""
    task = _task([
        SC("Product", "pressure_bar", "~=", 5.0, tolerance_pct=2.0),    # quant MISS
        SC("response", "mentions_convergence", "~=", True),             # qual ok
    ])
    chat = {"answer": "The flowsheet converged successfully.", "error": None}
    outcome, detail, st = rb._determine_outcome(
        task, chat, _sr({"Product": {"pressure_Pa": 101325.0}}))
    assert st["quant_met"] == 0 and st["quant_tot"] == 1
    assert st["qual_met"] == 1
    assert outcome in ("FAILURE_LOUD", "PARTIAL")


def test_outcome_timeout_is_loud_with_reason():
    task = _task([SC("Product", "temperature_C", "~=", 80.0)])
    outcome, detail, st = rb._determine_outcome(
        task, {"answer": "", "error": "timed out"}, {})
    assert outcome == "FAILURE_LOUD"
    assert "timed out" in detail
    assert st["quant_tot"] == 0  # never got to evaluate criteria


# ── fixture-dependent task handling (SKIPPED, not failed) ─────────────────────

def test_fixture_dependent_tasks_are_flagged():
    """The 13 tasks referencing a loaded flowsheet must be marked
    requires_fixture so a missing fixture never depresses the pass-rate."""
    import benchmark_tasks as bt
    flagged = {t.task_id for t in bt.BENCHMARK_TASKS
               if getattr(t, "requires_fixture", False)}
    # All of C3, C4, C5, plus the loaded-distillation and the pre-existing
    # convergence-repair tasks.
    for tid in ("C3-T01", "C3-T02", "C4-T01", "C5-T03", "C6-T03", "C8-T01"):
        assert tid in flagged, tid
    # Self-contained creation tasks must NOT be flagged.
    for tid in ("C1-T01", "C2-T01", "C6-T01", "C7-T01", "C8-T04"):
        assert tid not in flagged, tid


def test_skip_record_shape():
    task = _task([SC("Product", "temperature_C", "~=", 80.0)])
    task.task_id = "C9-T99"
    rec = rb._skip_record(task, 1, "fixture missing")
    assert rec["outcome"] == "SKIPPED"
    assert rec["detail"] == "fixture missing"
    # Skipped rows carry zero criteria so they cannot move the criteria totals.
    assert rec["quant_tot"] == 0 and rec["qual_tot"] == 0
