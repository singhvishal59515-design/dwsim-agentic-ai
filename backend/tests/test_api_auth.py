"""
Bearer-token auth middleware: a non-ASCII token must yield a clean 401, not an
unhandled 500. hmac.compare_digest raises TypeError on non-ASCII str operands,
so the comparison must be done on bytes.
"""
from __future__ import annotations
import importlib
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import pytest
fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient


def _client_with_token(token: str):
    os.environ["API_AUTH_TOKEN"] = token
    import api
    importlib.reload(api)          # re-evaluate _API_AUTH_TOKEN + middleware
    return api


def test_health_exempt_and_tokens(monkeypatch):
    api = _client_with_token("secret-123")
    try:
        cl = TestClient(api.app)
        assert cl.get("/health").status_code == 200          # exempt
        assert cl.get("/llm/status").status_code == 401       # no token
        # The regression: a non-ASCII token (reachable via the ?token= query
        # param, which carries UTF-8 unlike the latin-1 Authorization header)
        # must be a clean 401, never an unhandled 500 from compare_digest.
        r = cl.get("/llm/status", params={"token": "café☕"})
        assert r.status_code == 401, r.status_code
        # Correct token still authorises (200, or 500 only from downstream).
        good = cl.get("/llm/status", headers={"Authorization": "Bearer secret-123"})
        assert good.status_code in (200, 500)
    finally:
        os.environ.pop("API_AUTH_TOKEN", None)
        importlib.reload(api)      # restore open-by-default for other tests
