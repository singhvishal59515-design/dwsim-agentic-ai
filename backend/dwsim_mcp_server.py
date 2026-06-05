#!/usr/bin/env python
"""
dwsim_mcp_server.py — Model Context Protocol (MCP) server for the DWSIM agent.

Exposes the DWSIM simulation bridge as an MCP toolset so ANY MCP-capable client
(Claude Desktop, Cursor, Continue, etc.) can drive DWSIM in natural language or
via structured tool calls. This mirrors the published AVEVA APS-Agent design
(LLM agent ↔ simulator via MCP; arXiv:2601.11650) for DWSIM specifically.

Design
------
• Transport: MCP stdio (JSON-RPC 2.0, newline-delimited) — the standard local
  MCP transport. No third-party MCP SDK required; the protocol is implemented
  directly so the project has zero new dependencies.
• It is a THIN PROXY to the running FastAPI backend (default
  http://localhost:8080). All DWSIM access, write-verification, optimisation and
  the single-instance lock live in the backend; the MCP server only translates
  MCP tool calls into HTTP calls. This deliberately avoids constructing a second
  DWSIM Automation instance (DWSIM is single-instance) in the MCP process.

Run
---
    python dwsim_mcp_server.py            # speaks MCP over stdio
Configure a client (Claude Desktop ~/.../claude_desktop_config.json):
    {
      "mcpServers": {
        "dwsim": {
          "command": "python",
          "args": ["C:/Users/hp/project_llm1/dwsim_full/backend/dwsim_mcp_server.py"],
          "env": {"DWSIM_BACKEND_URL": "http://localhost:8080"}
        }
      }
    }
The FastAPI backend must be running (python api.py) for tools to work.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

BACKEND = os.environ.get("DWSIM_BACKEND_URL", "http://localhost:8080").rstrip("/")
# Echo a widely-supported protocol revision; we also accept the client's.
DEFAULT_PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "dwsim-agent", "version": "1.0.0"}
HTTP_TIMEOUT = float(os.environ.get("DWSIM_MCP_TIMEOUT_S", "180"))


# ── HTTP helpers (kept tiny + injectable so tests need no backend) ──────────

def _http_get(path: str, timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    import requests
    r = requests.get(f"{BACKEND}{path}", timeout=timeout)
    return r.json()


def _http_post(path: str, body: Dict[str, Any] | None = None,
               timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    import requests
    r = requests.post(f"{BACKEND}{path}", json=body or {}, timeout=timeout)
    return r.json()


def _agent_chat(message: str, timeout: float = HTTP_TIMEOUT) -> Dict[str, Any]:
    """Forward a natural-language request to the full agent (/chat/stream) and
    collect the final answer from the SSE stream."""
    import re
    import requests
    final, tokens = None, []
    with requests.post(f"{BACKEND}/chat/stream", json={"message": message},
                       stream=True, timeout=timeout) as r:
        for line in r.iter_lines():
            if not line:
                continue
            m = re.match(rb"data: (.*)", line)
            if not m:
                continue
            try:
                evt = json.loads(m.group(1).decode("utf-8", "replace"))
            except Exception:
                continue
            t = evt.get("type")
            if t == "token":
                tokens.append(str(evt.get("data", "")))
            elif t == "done":
                final = evt.get("data")
            elif t == "error":
                return {"success": False, "error": evt.get("data", "agent error")}
    return {"success": True, "answer": final or "".join(tokens)}


# ── Tool implementations (each returns a JSON-able dict) ────────────────────

def _t_health(**_) -> Dict[str, Any]:
    return _http_get("/health")

def _t_list_objects(**_) -> Dict[str, Any]:
    return _http_get("/flowsheet/objects")

def _t_loaded(**_) -> Dict[str, Any]:
    return _http_get("/flowsheet/loaded")

def _t_load_flowsheet(path: str = "", **_) -> Dict[str, Any]:
    return _http_post("/flowsheet/load", {"path": path})

def _t_get_stream(tag: str = "", **_) -> Dict[str, Any]:
    return _http_post("/stream/properties", {"tag": tag})

def _t_set_stream_property(tag: str = "", property_name: str = "",
                           value: float = 0.0, unit: str = "", **_) -> Dict[str, Any]:
    return _http_post("/stream/set_property",
                      {"tag": tag, "property_name": property_name,
                       "value": value, "unit": unit})

def _t_solve(**_) -> Dict[str, Any]:
    return _http_post("/flowsheet/run", {})

def _t_optimize(goal: str = "", max_iter: int = 50, **_) -> Dict[str, Any]:
    return _http_post("/optimize/workflow", {"goal": goal, "max_iter": max_iter})

def _t_agent(request: str = "", **_) -> Dict[str, Any]:
    return _agent_chat(request)


# ── Tool catalogue (curated, mirrors the APS-Agent toolset) ────────────────

TOOLS: List[Dict[str, Any]] = [
    {"name": "dwsim_health",
     "description": "Check the DWSIM backend is up and a flowsheet bridge is ready.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dwsim_list_objects",
     "description": "List all streams and unit operations (with tags) in the loaded DWSIM flowsheet.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dwsim_loaded_flowsheet",
     "description": "Report the currently loaded flowsheet (name, property package, object counts).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dwsim_load_flowsheet",
     "description": "Load a DWSIM flowsheet file (.dwxmz/.dwxml) by absolute path.",
     "inputSchema": {"type": "object",
                     "properties": {"path": {"type": "string",
                                             "description": "Absolute path to the .dwxmz file"}},
                     "required": ["path"]}},
    {"name": "dwsim_get_stream",
     "description": "Read all properties (T, P, flow, composition, …) of a material stream by tag.",
     "inputSchema": {"type": "object",
                     "properties": {"tag": {"type": "string"}}, "required": ["tag"]}},
    {"name": "dwsim_set_stream_property",
     "description": "Set a material-stream property (e.g. temperature, pressure, mass_flow). "
                    "The backend verifies the write by read-back.",
     "inputSchema": {"type": "object",
                     "properties": {"tag": {"type": "string"},
                                    "property_name": {"type": "string",
                                                      "description": "e.g. temperature, pressure, mass_flow"},
                                    "value": {"type": "number"},
                                    "unit": {"type": "string", "description": "e.g. C, K, bar, kg/s"}},
                     "required": ["tag", "property_name", "value"]}},
    {"name": "dwsim_solve",
     "description": "Solve (calculate) the entire DWSIM flowsheet and report convergence.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "dwsim_optimize",
     "description": "Run the natural-language optimization workflow on the loaded flowsheet "
                    "(e.g. 'minimise heater duty', 'maximise H2 purity'). Uses the agent's "
                    "objective-mapping, baseline check, multi-solver and surrogate routing.",
     "inputSchema": {"type": "object",
                     "properties": {"goal": {"type": "string"},
                                    "max_iter": {"type": "integer", "default": 50}},
                     "required": ["goal"]}},
    {"name": "dwsim_agent",
     "description": "Send a free-form natural-language request to the full DWSIM agent "
                    "(builds/reads/solves/optimises autonomously) and get its final answer. "
                    "Use this for anything not covered by the specific tools above.",
     "inputSchema": {"type": "object",
                     "properties": {"request": {"type": "string"}}, "required": ["request"]}},
]

TOOL_IMPL = {
    "dwsim_health": _t_health,
    "dwsim_list_objects": _t_list_objects,
    "dwsim_loaded_flowsheet": _t_loaded,
    "dwsim_load_flowsheet": _t_load_flowsheet,
    "dwsim_get_stream": _t_get_stream,
    "dwsim_set_stream_property": _t_set_stream_property,
    "dwsim_solve": _t_solve,
    "dwsim_optimize": _t_optimize,
    "dwsim_agent": _t_agent,
}


# ── JSON-RPC / MCP dispatch ────────────────────────────────────────────────

class _RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def handle_request(method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle one MCP request method, returning the JSON-RPC `result` object."""
    if method == "initialize":
        client_proto = (params or {}).get("protocolVersion")
        return {
            "protocolVersion": client_proto or DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = (params or {}).get("name", "")
        args = (params or {}).get("arguments", {}) or {}
        fn = TOOL_IMPL.get(name)
        if fn is None:
            raise _RpcError(-32602, f"Unknown tool: {name}")
        try:
            result = fn(**args)
            is_error = isinstance(result, dict) and result.get("success") is False
        except Exception as exc:  # tool/transport failure → surfaced to the LLM
            result = {"success": False,
                      "error": f"{type(exc).__name__}: {exc}",
                      "hint": "Is the DWSIM backend running at "
                              f"{BACKEND}? Start it with `python api.py`."}
            is_error = True
        return {"content": [{"type": "text",
                             "text": json.dumps(result, default=str)}],
                "isError": bool(is_error)}
    raise _RpcError(-32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None) -> None:
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        # Notifications (no id / notifications/*) get no response.
        if mid is None or (isinstance(method, str) and method.startswith("notifications/")):
            continue
        try:
            result = handle_request(method, msg.get("params", {}) or {})
            resp = {"jsonrpc": "2.0", "id": mid, "result": result}
        except _RpcError as e:
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": e.code, "message": e.message}}
        except Exception as e:  # never crash the server loop
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": str(e)}}
        stdout.write(json.dumps(resp) + "\n")
        stdout.flush()


if __name__ == "__main__":
    serve()
