"""
Tests for the DWSIM MCP server (JSON-RPC 2.0 / MCP handshake + tool dispatch).
HTTP is mocked so no DWSIM/backend is needed.
"""
from __future__ import annotations
import io, json, os, sys
_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import dwsim_mcp_server as mcp


# ── MCP handshake ──────────────────────────────────────────────────────────

def test_initialize_returns_capabilities_and_echoes_protocol():
    r = mcp.handle_request("initialize", {"protocolVersion": "2025-06-18"})
    assert r["protocolVersion"] == "2025-06-18"      # echoes client's
    assert "tools" in r["capabilities"]
    assert r["serverInfo"]["name"] == "dwsim-agent"


def test_initialize_defaults_protocol_when_absent():
    r = mcp.handle_request("initialize", {})
    assert r["protocolVersion"] == mcp.DEFAULT_PROTOCOL


def test_tools_list_exposes_curated_toolset():
    r = mcp.handle_request("tools/list", {})
    names = {t["name"] for t in r["tools"]}
    # mirrors the APS-style toolset
    for expected in ("dwsim_health", "dwsim_list_objects", "dwsim_get_stream",
                     "dwsim_set_stream_property", "dwsim_solve",
                     "dwsim_optimize", "dwsim_agent"):
        assert expected in names
    # every tool has a description + JSON input schema
    for t in r["tools"]:
        assert t["description"] and t["inputSchema"]["type"] == "object"


def test_ping():
    assert mcp.handle_request("ping", {}) == {}


def test_unknown_method_raises_method_not_found():
    import pytest
    with pytest.raises(mcp._RpcError) as e:
        mcp.handle_request("does/not/exist", {})
    assert e.value.code == -32601


# ── tools/call dispatch ────────────────────────────────────────────────────

def test_tools_call_success(monkeypatch):
    monkeypatch.setattr(mcp, "_http_post",
                        lambda path, body=None, timeout=0: {"success": True,
                                                            "properties": {"temperature_C": 80.0}})
    r = mcp.handle_request("tools/call",
                           {"name": "dwsim_get_stream", "arguments": {"tag": "OUT"}})
    assert r["isError"] is False
    payload = json.loads(r["content"][0]["text"])
    assert payload["properties"]["temperature_C"] == 80.0


def test_tools_call_marks_backend_failure_as_error(monkeypatch):
    monkeypatch.setattr(mcp, "_http_post",
                        lambda path, body=None, timeout=0: {"success": False,
                                                            "error": "no flowsheet"})
    r = mcp.handle_request("tools/call",
                           {"name": "dwsim_solve", "arguments": {}})
    assert r["isError"] is True


def test_tools_call_transport_error_is_surfaced(monkeypatch):
    def _boom(*a, **k):
        raise ConnectionError("backend down")
    monkeypatch.setattr(mcp, "_http_get", _boom)
    r = mcp.handle_request("tools/call",
                           {"name": "dwsim_health", "arguments": {}})
    assert r["isError"] is True
    txt = json.loads(r["content"][0]["text"])
    assert "backend" in txt["hint"].lower()


def test_tools_call_unknown_tool():
    import pytest
    with pytest.raises(mcp._RpcError) as e:
        mcp.handle_request("tools/call", {"name": "nope", "arguments": {}})
    assert e.value.code == -32602


# ── end-to-end serve() loop over fake stdio ────────────────────────────────

def test_serve_loop_handles_initialize_and_notifications(monkeypatch):
    monkeypatch.setattr(mcp, "_http_get",
                        lambda path, timeout=0: {"status": "ok"})
    inp = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),  # no id
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "dwsim_health", "arguments": {}}}),
    ]) + "\n"
    out = io.StringIO()
    mcp.serve(stdin=io.StringIO(inp), stdout=out)
    lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    # initialize + tools/call → 2 responses; the notification produced none
    assert len(lines) == 2
    assert lines[0]["id"] == 1 and "protocolVersion" in lines[0]["result"]
    assert lines[1]["id"] == 2 and lines[1]["result"]["isError"] is False
