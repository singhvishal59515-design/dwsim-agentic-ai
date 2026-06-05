"""
Tests for the Bayesian Optimization stack.

Three layers verified:
  1. CORE  — bayesian_optimizer.BayesianOptimizer on the Branin benchmark
            (pure NumPy, no DWSIM dependency)
  2. API   — POST /optimize/bayesian + POST /optimize/bayesian/async
            (mock objective via monkeypatched bridge method)
  3. BRIDGE — only one bayesian_optimize method (regression guard for the
              duplicate-method bug that previously broke this stack)
"""

from __future__ import annotations
import math
import os
import sys
import time

import pytest

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


# ─── CORE: algorithm on Branin (no DWSIM) ─────────────────────────────────

def _branin(p):
    x1 = p["x1"]; x2 = p["x2"]
    b = 5.1 / (4 * math.pi ** 2); c = 5 / math.pi
    return (x2 - b * x1 ** 2 + c * x1 - 6) ** 2 \
        + 10 * (1 - 1 / (8 * math.pi)) * math.cos(x1) + 10


def test_bo_core_converges_on_branin():
    """Algorithm correctness: BO must find Branin global min (~0.398) <1.5 in 25 evals."""
    from bayesian_optimizer import BayesianOptimizer
    opt = BayesianOptimizer(
        bounds={"x1": (-5.0, 10.0), "x2": (0.0, 15.0)},
        n_initial=5, max_iter=20, minimize=True, seed=42,
    )
    result = opt.run(_branin)
    assert result.best_value < 1.5, \
        f"Branin best={result.best_value} — algorithm did not converge"
    assert result.n_evals <= 25
    assert isinstance(result.best_params, dict)
    assert set(result.best_params.keys()) == {"x1", "x2"}


def test_bo_core_maximize_mode_works():
    """maximize=True must invert the objective sign correctly."""
    from bayesian_optimizer import BayesianOptimizer
    # Maximize the negated Branin → should find a point with value near
    # the NEGATIVE of Branin's minimum (~ -0.398)
    opt = BayesianOptimizer(
        bounds={"x1": (-5.0, 10.0), "x2": (0.0, 15.0)},
        n_initial=5, max_iter=20, minimize=False, seed=42,
    )
    result = opt.run(lambda p: -_branin(p))
    assert result.best_value > -1.5, \
        f"Maximize-mode best={result.best_value} — sign handling broken"


def test_bo_core_handles_failed_evaluations():
    """Returning None from objective must penalise — not crash."""
    from bayesian_optimizer import BayesianOptimizer
    n_calls = [0]

    def flaky(p):
        n_calls[0] += 1
        # Fail every 3rd call
        if n_calls[0] % 3 == 0:
            return None
        return _branin(p)

    opt = BayesianOptimizer(
        bounds={"x1": (-5.0, 10.0), "x2": (0.0, 15.0)},
        n_initial=5, max_iter=10, minimize=True, seed=42,
    )
    result = opt.run(flaky)
    assert result.best_value is not None
    assert result.n_evals >= 5


# ─── BRIDGE: regression guard for the duplicate-method bug ────────────────

def test_bridge_has_exactly_one_bayesian_optimize_method():
    """The duplicate definition that previously shadowed the working method
    must stay deleted. This test reads the source file to confirm only one
    method definition exists."""
    src_path = os.path.join(_BACKEND_DIR, "dwsim_bridge_v2.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    count = src.count("def bayesian_optimize(")
    assert count == 1, \
        f"Expected exactly 1 bayesian_optimize method, found {count}"


def test_bridge_bayesian_optimize_signature_matches_callers():
    """Bridge method must accept the kwargs the API endpoint passes."""
    from inspect import signature
    from dwsim_bridge_v2 import DWSIMBridgeV2
    sig = signature(DWSIMBridgeV2.bayesian_optimize)
    expected = {"variables", "observe_tag", "observe_property",
                "minimize", "n_initial", "max_iter", "xi", "seed",
                "save_plot", "on_progress"}
    actual = set(sig.parameters.keys()) - {"self"}
    assert expected.issubset(actual), \
        f"Bridge signature missing params: {expected - actual}"


# ─── SCHEMA: regression guard for duplicate tool entries ──────────────────

def test_no_duplicate_tool_schemas():
    """tools_schema_v2 must not have two entries for the same tool name."""
    from tools_schema_v2 import DWSIM_TOOLS
    from collections import Counter
    names = [t.get("name") for t in DWSIM_TOOLS]
    counts = Counter(names)
    dups = {n: c for n, c in counts.items() if c > 1}
    assert not dups, f"Duplicate tool schemas found: {dups}"


def test_bayesian_optimize_schema_matches_bridge_params():
    """The single BO schema entry must declare parameters that the bridge
    actually accepts. Previously the schema used 'lower_bound/upper_bound/
    tolerance' which the working bridge method does not accept."""
    from tools_schema_v2 import DWSIM_TOOLS
    bo_schemas = [t for t in DWSIM_TOOLS if t.get("name") == "bayesian_optimize"]
    assert len(bo_schemas) == 1
    props = bo_schemas[0]["parameters"]["properties"]["variables"]["items"]["properties"]
    # Working bridge expects 'lower' and 'upper' (not '_bound' suffix)
    assert "lower" in props, "Schema must declare 'lower' (matches bridge param)"
    assert "upper" in props, "Schema must declare 'upper' (matches bridge param)"


# ─── API: full stack against monkey-patched bridge ────────────────────────

@pytest.fixture
def api_client_with_mock_bridge(monkeypatch):
    """FastAPI TestClient where _get_bridge returns a mock with a working
    bayesian_optimize implementation backed by the real algorithm."""
    from fastapi.testclient import TestClient
    import api as api_module

    class MockBridge:
        def __init__(self):
            self.calls = 0

        def bayesian_optimize(self, variables, observe_tag, observe_property,
                              minimize=True, n_initial=5, max_iter=20,
                              xi=0.01, seed=42, save_plot="", on_progress=None):
            from bayesian_optimizer import BayesianOptimizer
            bounds = {f"{v['tag']}.{v['property']}": (float(v['lower']), float(v['upper']))
                      for v in variables}

            def obj(params):
                self.calls += 1
                # Branin-like landscape on the first two variables
                xs = list(params.values())
                x1, x2 = (xs + [0, 0])[:2]
                b = 5.1 / (4 * math.pi ** 2); c = 5 / math.pi
                return (x2 - b * x1 ** 2 + c * x1 - 6) ** 2 \
                    + 10 * (1 - 1 / (8 * math.pi)) * math.cos(x1) + 10

            opt = BayesianOptimizer(
                bounds=bounds, n_initial=n_initial, max_iter=max_iter,
                minimize=minimize, xi=xi, seed=seed, save_plot=save_plot,
                on_progress=on_progress,
            )
            r = opt.run(obj)
            return {
                "success": True,
                "best_params": r.best_params,
                "best_value": r.best_value,
                "observe": f"{observe_tag}.{observe_property}",
                "minimize": minimize,
                "n_evals": r.n_evals,
                "n_initial": r.n_initial,
                "max_iter": max_iter,
                "converged": r.converged,
                "duration_s": r.duration_s,
                "convergence_plot": "",
                "history": r.history,
                "variables": [
                    {"name": f"{v['tag']}.{v['property']}",
                     "lower": float(v['lower']),
                     "upper": float(v['upper']),
                     "best": r.best_params[f"{v['tag']}.{v['property']}"]}
                    for v in variables
                ],
            }

    mock = MockBridge()
    monkeypatch.setattr(api_module, "_get_bridge", lambda: mock)
    return TestClient(api_module.app), mock


def test_api_optimize_bayesian_endpoint_returns_optimum(api_client_with_mock_bridge):
    """POST /optimize/bayesian must convert a JSON request into a successful
    BO run and return the standard result envelope."""
    client, _mock = api_client_with_mock_bridge
    payload = {
        "variables": [
            {"tag": "FEED", "property": "T", "unit": "C",
             "lower": -5.0, "upper": 10.0},
            {"tag": "FEED", "property": "P", "unit": "bar",
             "lower": 0.0, "upper": 15.0},
        ],
        "observe_tag": "PROD",
        "observe_property": "objective",
        "minimize": True,
        "n_initial": 5,
        "max_iter": 15,
    }
    r = client.post("/optimize/bayesian", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, f"BO failed: {body}"
    assert body["best_value"] < 1.5, f"BO did not converge: best={body['best_value']}"
    assert body["n_evals"] <= 20
    assert len(body["history"]) > 0


def test_api_optimize_bayesian_async_returns_task_id(api_client_with_mock_bridge):
    """POST /optimize/bayesian/async must enqueue and return immediately."""
    client, _mock = api_client_with_mock_bridge
    payload = {
        "variables": [{"tag": "F", "property": "T",
                       "lower": -5.0, "upper": 10.0}],
        "observe_tag": "P", "observe_property": "y",
        "n_initial": 3, "max_iter": 5,
    }
    r = client.post("/optimize/bayesian/async", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert "task_id" in body
    assert body["poll_url"].startswith("/tasks/")

    # Poll once after a moment — task should exist with some status
    time.sleep(0.5)
    r2 = client.get(body["poll_url"])
    assert r2.status_code == 200
    status = r2.json()
    assert status["status"] in ("queued", "running", "done", "failed")


def test_api_optimize_bayesian_bad_input_returns_structured_error(api_client_with_mock_bridge):
    """Bad inputs (empty variables) must surface a structured error."""
    client, _mock = api_client_with_mock_bridge
    r = client.post("/optimize/bayesian", json={
        "variables": [], "observe_tag": "X", "observe_property": "y"
    })
    body = r.json()
    assert body["success"] is False
