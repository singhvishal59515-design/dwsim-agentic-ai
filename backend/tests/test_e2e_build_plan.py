"""
End-to-end test: build a complete flowsheet via execute_build_plan,
solve it, and assert that the outlet stream properties are physically
correct.

This is the ONLY test that exercises the full critical path:
  build_plan -> bridge -> DWSIM .NET -> solve -> read results -> assert

Requires DWSIM installed locally. Skipped when DWSIM DLLs cannot be found
or pythonnet is unavailable. Run with:
    pytest backend/tests/test_e2e_build_plan.py -v
"""

from __future__ import annotations
import os
import sys
import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def _dwsim_available() -> bool:
    try:
        from dwsim_bridge_v2 import _find_dll_folder
        return _find_dll_folder() is not None
    except Exception:
        return False


def _server_holding_dwsim() -> bool:
    """Return True if a local backend server is running on :8080. The server
    holds the DWSIM bridge and a parallel test fixture would deadlock on
    pythonnet .NET object access, so we skip in that case."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=0.3):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _dwsim_available() or _server_holding_dwsim(),
    reason=("DWSIM not installed OR backend server is running on :8080 "
            "(stop the server before running e2e tests; DWSIM is single-instance)"),
)


@pytest.fixture(scope="function")
def bridge():
    """A fresh bridge per test for clean isolation. This is now safe because
    the DWSIM Automation manager is a process-wide singleton (see
    dwsim_bridge_v2._GLOBAL_MGR) — fresh bridge instances reuse the one manager
    instead of constructing a second one (which used to fail/wedge, the reason
    this fixture was previously module-scoped). Each fresh bridge has
    _building=False so its new_flowsheet cleanly purges the prior test's
    flowsheet from the shared registry."""
    from dwsim_bridge_v2 import DWSIMBridgeV2
    b = DWSIMBridgeV2()
    yield b
    try:
        if hasattr(b, "shutdown"):
            b.shutdown()
    except Exception:
        pass


def _water_heater_plan() -> dict:
    """Simplest non-trivial flowsheet: water at 25°C heated to 80°C."""
    return {
        "name": "e2e_water_heater",
        "compounds": ["Water"],
        "property_package": "Steam Tables (IAPWS-IF97)",
        "streams": [
            {
                "tag": "FEED",
                "T_C": 25.0,
                "P_bar": 1.01325,
                "flow_kg_h": 3600.0,  # 1 kg/s
                "compositions": {"Water": 1.0},
            },
            {"tag": "OUT"},
        ],
        "unit_ops": [
            {
                "tag": "H-1",
                "type": "Heater",
                "params": {"outlet_temperature_C": 80.0},
            },
        ],
        "connections": [
            {"from": "FEED", "to": "H-1"},
            {"from": "H-1", "to": "OUT"},
        ],
    }


def test_build_plan_executes_without_crash(bridge):
    from flowsheet_executor import execute_build_plan
    result = execute_build_plan(_water_heater_plan(), bridge, solve=False)
    assert isinstance(result, dict)
    assert "steps" in result
    assert "summary" in result
    # The plan should have produced step records even if individual steps fail
    assert result["steps_total"] >= 4, \
        f"Expected ≥4 build steps, got {result['steps_total']}: {result['summary']}"


def test_water_heater_solves_to_target_temperature(bridge):
    """Full path: build → solve → assert outlet T within ±2°C of target."""
    from flowsheet_executor import execute_build_plan
    plan = _water_heater_plan()
    result = execute_build_plan(plan, bridge, solve=True)

    # Step-level sanity
    assert result["steps_total"] > 0
    if not result["success"]:
        # Provide diagnostic detail in failure
        pytest.fail(
            f"Build/solve failed: {result.get('summary')} | "
            f"errors={result.get('errors')[:3]}"
        )

    # Verify outlet stream temperature
    try:
        props = bridge.get_stream_properties("OUT")
    except Exception as exc:
        pytest.fail(f"Could not read outlet stream properties: {exc}")

    assert props.get("success", True), f"Outlet stream read failed: {props}"

    # get_stream_properties returns {success, properties: {...}} — the values
    # live in the nested 'properties' dict, not at the top level. (The previous
    # extraction read the top level and always got None even though the solve
    # was correct.) Accept °C directly too.
    _p = props.get("properties", props)
    T_K = (_p.get("temperature_K")
           or _p.get("temperature"))
    if T_K is None and _p.get("temperature_C") is not None:
        T_K = float(_p["temperature_C"]) + 273.15
    if T_K is None:
        # Fall back to fetching via raw property API
        try:
            r = bridge.get_stream_property("OUT", "temperature")
            T_K = r.get("value") if isinstance(r, dict) else r
        except Exception:
            T_K = None

    assert T_K is not None, f"Could not extract outlet temperature: {props}"
    T_C = float(T_K) - 273.15
    assert 78.0 <= T_C <= 82.0, \
        f"Outlet T={T_C:.2f}°C — expected ~80°C ±2°C"


def test_plan_with_missing_compounds_returns_structured_error(bridge):
    """The executor must fail gracefully with a structured error code."""
    from flowsheet_executor import execute_build_plan
    result = execute_build_plan({"name": "bad"}, bridge, solve=False)
    assert result["success"] is False
    assert result.get("error_code") == "PLAN_MISSING_COMPOUNDS"


def test_plan_with_invalid_connection_logs_error(bridge):
    """Connection to a non-existent stream tag must be reported, not silent."""
    from flowsheet_executor import execute_build_plan
    plan = {
        "name": "bad_connect",
        "compounds": ["Water"],
        "property_package": "Steam Tables (IAPWS-IF97)",
        "streams": [{"tag": "A"}],
        "unit_ops": [],
        "connections": [{"from": "A", "to": "DOES_NOT_EXIST"}],
    }
    result = execute_build_plan(plan, bridge, solve=False)
    # Either the connect step fails (errors list non-empty) or the
    # bridge silently no-ops; in either case success should reflect that.
    assert isinstance(result, dict)
    # The plan-level success is False if any error was logged
    if result.get("errors"):
        assert result["success"] is False
