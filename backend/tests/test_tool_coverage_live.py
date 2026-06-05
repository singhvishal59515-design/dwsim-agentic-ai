"""
Live tool-coverage test. Auto-skips when DWSIM is unavailable (CI / no engine)
so the rest of the suite stays green; when DWSIM is present it asserts the
core engine surface passes its read-back-verified probes.
"""
from __future__ import annotations
import os, sys
import pytest
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)


# This is a HEAVY, exclusive-resource integration test: it creates its own
# DWSIM bridge, and DWSIM is single-instance — running it alongside the other
# live-DWSIM tests in one process collides (a second Automation() instance
# fails). So it is OPT-IN: set RUN_LIVE_COVERAGE=1 to run it (or invoke the
# harness standalone: `python tool_coverage_harness.py`). The default suite
# skips it to stay green and avoid the single-instance conflict.
_OPT_IN = os.environ.get("RUN_LIVE_COVERAGE") == "1"


@pytest.mark.skipif(not _OPT_IN,
                    reason="set RUN_LIVE_COVERAGE=1 to run the live DWSIM "
                           "tool-coverage harness (single-instance; opt-in)")
def test_core_tool_surface_passes_read_back():
    from tool_coverage_harness import run_coverage
    report = run_coverage()
    s = report["summary"]
    # Expect the great majority of probes to pass; tolerate a couple of
    # environment-specific misses but flag a real regression.
    assert s["total"] >= 20
    assert s["coverage_pct"] >= 80.0, (
        f"tool coverage dropped to {s['coverage_pct']}% — "
        + "; ".join(f"{r['method']}: {r['detail']}"
                    for r in report["results"] if r["status"] == "fail"))
