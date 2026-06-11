# DWSIM MCP Server

Exposes the DWSIM agent bridge over the **Model Context Protocol** so any
MCP-capable client (Claude Desktop, Cursor, Continue, …) can drive DWSIM —
mirroring the published AVEVA APS-Agent design (arXiv:2601.11650) for DWSIM.

## What it is
- `dwsim_mcp_server.py` — an MCP **stdio** server (JSON-RPC 2.0). No third-party
  MCP SDK needed; the protocol is implemented directly (zero new dependencies).
- It is a **thin proxy** to the running FastAPI backend (`api.py`, default
  `http://localhost:8080`). All DWSIM access, write-verification, optimisation
  and the single-instance lock stay in the backend — the MCP process never
  constructs its own DWSIM instance (DWSIM is single-instance).

## Tools exposed
| Tool | Purpose |
|---|---|
| `dwsim_health` | Backend/bridge up? |
| `dwsim_list_objects` | List streams + unit ops (tags) |
| `dwsim_loaded_flowsheet` | Current flowsheet name / PP / counts |
| `dwsim_load_flowsheet` | Load a `.dwxmz` by path |
| `dwsim_get_stream` | Read a stream's T/P/flow/composition |
| `dwsim_set_stream_property` | Set a stream property (verified by read-back) |
| `dwsim_solve` | Solve the flowsheet, report convergence |
| `dwsim_optimize` | NL optimization (baseline check + multi-solver + surrogate routing) |
| `dwsim_agent` | Free-form NL request to the full autonomous agent |

## Run
1. Start the backend: `python api.py`
2. The MCP client launches the server (it speaks MCP over stdio):
   `python dwsim_mcp_server.py`

## Claude Desktop config
`%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "dwsim": {
      "command": "python",
      "args": ["C:/Users/hp/project_llm1/dwsim_full/backend/dwsim_mcp_server.py"],
      "env": { "DWSIM_BACKEND_URL": "http://localhost:8080" }
    }
  }
}
```
Then ask Claude Desktop e.g. *"Using DWSIM, load my_plant.dwxmz, raise FEED to
360 K, solve, and report the PRODUCT composition."*

## Env vars
- `DWSIM_BACKEND_URL` — backend base URL (default `http://localhost:8080`)
- `DWSIM_MCP_TIMEOUT_S` — per-call HTTP timeout (default 180)
