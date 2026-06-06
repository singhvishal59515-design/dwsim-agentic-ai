"""
api.py — DWSIM Agentic AI v2 FastAPI backend (reconstructed)

Serves:
  • /           → ui.html (classic v2 UI)
  • /app        → React build (if present)
  • /chat/stream, /flowsheet/*, /stream/*, /unitop/*, /hydrogen/*, /intent/*,
    /literature/*, /accuracy/*, /memory/*, /sessions/*, /eval/*, /knowledge/*,
    /process-library/*, /monte-carlo, /economics/*, /optimize/*, /parametric, etc.

Run:
  python api.py
"""

import os
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

# ── SSL: build combined CA bundle (certifi + Windows ROOT/CA store) ──
# Fixes httpx/openai/anthropic SDKs failing with "unable to get local issuer certificate"
def _build_combined_ca_bundle():
    import ssl as _ssl, tempfile as _tf
    try:
        import certifi
        bundle_path = os.path.join(_tf.gettempdir(), "_dwsim_combined_ca.pem")
        chunks = []
        try:
            with open(certifi.where(), "r", encoding="utf-8") as f:
                chunks.append(f.read())
        except Exception:
            pass
        for store in ("ROOT", "CA"):
            try:
                for cert_bytes, _enc, _trust in _ssl.enum_certificates(store):
                    try:
                        chunks.append(_ssl.DER_cert_to_PEM_cert(cert_bytes))
                    except Exception:
                        pass
            except Exception:
                pass
        with open(bundle_path, "w", encoding="utf-8") as f:
            f.write("\n".join(chunks))
        return bundle_path
    except Exception:
        return None

_ca_bundle = _build_combined_ca_bundle()
if _ca_bundle:
    os.environ["SSL_CERT_FILE"]       = _ca_bundle
    os.environ["REQUESTS_CA_BUNDLE"]  = _ca_bundle
    os.environ["CURL_CA_BUNDLE"]      = _ca_bundle
    os.environ["HTTPX_SSL_CERT_FILE"] = _ca_bundle
    print(f"[DWSIM API] Using combined CA bundle: {_ca_bundle}")
else:
    print(
        "[DWSIM API] WARNING: CA bundle build failed — LLM API calls may fail with "
        "SSL certificate verification errors. "
        "Try: pip install certifi pip-system-certs"
    )

import asyncio
import difflib
import io
import json
import sys
import threading
import time
import traceback
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
# Load the backend-local .env with override=True so it wins over any parent
# .env (the project-root .env can carry a stale LLM_PROVIDER that otherwise
# shadows the backend's configured provider).
import os as _os_env
_backend_env = _os_env.path.join(_os_env.path.dirname(_os_env.path.abspath(__file__)), ".env")
if _os_env.path.exists(_backend_env):
    load_dotenv(_backend_env, override=True)
else:
    load_dotenv()

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND_DIR)

# Lazy imports — done in lifespan/per-request to avoid hanging on startup
DWSIMBridgeV2 = None
DWSIMAgentV2 = None
LLMClient = None
DEFAULT_MODELS: Dict[str, str] = {}
FlowsheetWatcher = None

def _safe_import_bridge():
    global DWSIMBridgeV2
    if DWSIMBridgeV2 is None:
        from dwsim_bridge_v2 import DWSIMBridgeV2 as _B
        DWSIMBridgeV2 = _B
    return DWSIMBridgeV2

def _safe_import_agent():
    global DWSIMAgentV2, LLMClient, DEFAULT_MODELS
    if DWSIMAgentV2 is None:
        from agent_v2 import DWSIMAgentV2 as _A
        from llm_client import LLMClient as _L, DEFAULT_MODELS as _DM
        DWSIMAgentV2 = _A; LLMClient = _L; DEFAULT_MODELS = _DM
    return DWSIMAgentV2, LLMClient, DEFAULT_MODELS

def _safe_import_watcher():
    global FlowsheetWatcher
    if FlowsheetWatcher is None:
        try:
            from flowsheet_watcher import FlowsheetWatcher as _W
            FlowsheetWatcher = _W
        except Exception:
            FlowsheetWatcher = False
    return FlowsheetWatcher

# Singletons
_bridge:  Any = None
_agent:   Any = None
_watcher: Any = None
_bridge_lock = threading.Lock()

def _get_bridge():
    global _bridge
    if _bridge is None:
        with _bridge_lock:
            if _bridge is None:
                Bridge = _safe_import_bridge()
                _bridge = Bridge(dll_folder=os.getenv("DWSIM_DLL_FOLDER"))
                _bridge.initialize()
    return _bridge

_agent_lock = threading.Lock()

def _get_agent():
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:                    # double-checked locking
                Agent, LLM, DM = _safe_import_agent()
                provider = (os.getenv("LLM_PROVIDER", "groq") or "groq").lower()
                if provider == "gemini":     # removed provider — redirect gracefully
                    provider = "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "groq"
                env_key = {"groq":"GROQ_API_KEY",
                           "openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","ollama":""}.get(provider,"")
                api_key = os.getenv(env_key, "") if env_key else ""
                model   = os.getenv("LLM_MODEL", DM.get(provider, ""))
                llm = LLM(provider=provider, api_key=api_key, model=model)
                _agent = Agent(llm=llm, bridge=_get_bridge())
    return _agent

def _broadcast_file_event(evt: dict):
    asyncio.run_coroutine_threadsafe(_ws_broadcast(evt), _loop) if _loop else None

# WebSocket connections
_ws_clients: List[WebSocket] = []
_ws_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None

async def _ws_broadcast(evt: dict):
    with _ws_lock:
        clients = list(_ws_clients)          # snapshot under lock
    dead = []
    for ws in clients:
        try: await ws.send_json(evt)
        except Exception: dead.append(ws)
    if dead:
        with _ws_lock:
            for w in dead:
                if w in _ws_clients: _ws_clients.remove(w)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    global _watcher, _loop
    _loop = asyncio.get_running_loop()
    loop = _loop
    # Eager init with timeouts so server always starts
    try:
        await asyncio.wait_for(loop.run_in_executor(None, _get_bridge), timeout=8.0)
        print("[DWSIM API] Bridge initialised")
    except asyncio.TimeoutError:
        print("[DWSIM API] Bridge init timed out — lazy retry on first request")
    except Exception as exc:
        print(f"[DWSIM API] Bridge warning: {exc}")
    try:
        await asyncio.wait_for(loop.run_in_executor(None, _get_agent), timeout=8.0)
        print("[DWSIM API] Agent initialised")
    except asyncio.TimeoutError:
        print("[DWSIM API] Agent init timed out — lazy retry on first request")
    except Exception as exc:
        print(f"[DWSIM API] Agent warning: {exc}")
    try:
        W = _safe_import_watcher()
        if W:
            _watcher = W(on_change=_broadcast_file_event, poll_interval=3.0)
            _watcher.start()
            print("[DWSIM API] Flowsheet watcher started")
    except Exception as exc:
        print(f"[DWSIM API] Watcher warning: {exc}")
    yield
    if _watcher:
        try: _watcher.stop()
        except Exception: pass
        print("[DWSIM API] Flowsheet watcher stopped")


app = FastAPI(title="DWSIM Agentic AI v2", version="2.0.0", lifespan=_lifespan)
_cors_origins_env = os.getenv("CORS_ORIGINS", "")
_cors_origins = (
    [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
    if _cors_origins_env else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Global structured exception handler ────────────────────────────────────
# Any uncaught exception from a route now returns a uniform structured
# {success, error, error_code, module, request_id, exception_type} body.
from fastapi.requests import Request as _FastAPIRequest
from fastapi.responses import JSONResponse as _JSONResponse
try:
    from error_utils import format_error, classify_exception
except Exception:  # pragma: no cover
    format_error = None
    classify_exception = None

if format_error is not None:
    @app.exception_handler(Exception)
    async def _global_exception_handler(request: _FastAPIRequest, exc: Exception):
        # Let HTTPException keep its native status code (FastAPI handles it)
        if isinstance(exc, HTTPException):
            raise exc
        code = classify_exception(exc) if classify_exception else "INTERNAL_ERROR"
        body = format_error(
            exc,
            module=f"api.{request.url.path.strip('/').replace('/', '.') or 'root'}",
            error_code=code,
        )
        return _JSONResponse(status_code=500, content=body)

# ── Root: serve ui.html ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root():
    p = os.path.join(_BACKEND_DIR, "ui.html")
    if os.path.exists(p):
        return FileResponse(p, media_type="text/html")
    raise HTTPException(404, "ui.html not found")

# ── React app at /app ───────────────────────────────────────────────────────
_react_build = os.path.join(os.path.dirname(_BACKEND_DIR), "frontend", "build")
if os.path.isdir(_react_build):
    @app.get("/app", response_class=HTMLResponse)
    def react_app():
        return FileResponse(os.path.join(_react_build, "index.html"))
    app.mount("/static", StaticFiles(directory=os.path.join(_react_build, "static")), name="static")

# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    fs = None; pp = None
    try:
        b = _bridge
        if b:
            # `current_flowsheet_name` does not exist on the bridge — it always
            # returned None, so /health wrongly reported flowsheet:null even with
            # a flowsheet loaded. Read the real active-flowsheet state instead.
            fs = (getattr(b, "current_flowsheet_name", None)
                  or getattr(b, "_active_alias", None)
                  or getattr(getattr(b, "state", None), "name", None))
            st = getattr(b, "state", None)
            pp = (getattr(b, "current_property_package", None)
                  or getattr(st, "property_package", None))
    except Exception: pass
    tool_count = None
    try:
        from tools_schema_v2 import DWSIM_TOOLS
        tool_count = len(DWSIM_TOOLS)
    except Exception: pass
    return {"status": "ok", "bridge_ready": _bridge is not None,
            "flowsheet": fs, "property_package": pp, "tool_count": tool_count}

# ── Diagnostics ─────────────────────────────────────────────────────────────
@app.get("/diagnostics")
def diagnostics(skip_providers: bool = True):
    info = {"version": "2.0.0", "bridge_ready": _bridge is not None,
            "agent_ready": _agent is not None,
            "llm_provider": os.getenv("LLM_PROVIDER","groq"),
            "llm_model": os.getenv("LLM_MODEL","")}
    if not skip_providers:
        try:
            import diagnostics as diag
            info["providers"] = diag.probe_llm_providers(timeout_s=3.0)
        except Exception as exc:
            info["providers_error"] = str(exc)
    return info

@app.get("/diagnostics/version")
def diag_version(): return {"version": "2.0.0"}

@app.get("/diagnostics/providers")
def diag_providers():
    try:
        import diagnostics as diag
        return diag.probe_llm_providers(timeout_s=3.0)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── Chat ────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    auto_reflect: bool = False
    reflect_close_first: bool = False

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    async def gen():
        try:
            agent = _get_agent()
        except Exception as exc:
            yield f"data: {json.dumps({'type':'error','data':str(exc)})}\n\n"
            return

        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def emit(evt):
            try:
                loop.call_soon_threadsafe(q.put_nowait, evt)
            except Exception: pass

        def run():
            try:
                if hasattr(agent, "chat_stream"):
                    for evt in agent.chat_stream(req.message): emit(evt)
                else:
                    answer = agent.chat(req.message)
                    # The agent has no streaming loop, so replay the tools it
                    # used this turn as tool_call events. Without this, any SSE
                    # consumer (notably run_benchmark.py) records 0 tools for
                    # every run — making tool metrics structurally meaningless.
                    for _t in (getattr(agent, "_turn_tool_timings", []) or []):
                        emit({"type":"tool_call","data":_t})
                    emit({"type":"done","data":answer,"session_id":""})
            except Exception as exc:
                emit({"type":"error","data":str(exc)})
            emit(None)

        threading.Thread(target=run, daemon=True).start()
        while True:
            evt = await q.get()
            if evt is None: break
            yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/chat/reset")
def chat_reset():
    try:
        a = _get_agent()
        if hasattr(a, "reset"): a.reset()
        return {"success": True}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── Flowsheet ───────────────────────────────────────────────────────────────
class LoadRequest(BaseModel):
    path: str
    alias: Optional[str] = None

@app.post("/flowsheet/load")
def fs_load(req: LoadRequest):
    try:
        with _bridge_lock:
            r = _get_bridge().load_flowsheet(req.path, alias=req.alias) if hasattr(_get_bridge(),"load_flowsheet") else {"success":False,"error":"bridge missing"}
        return r
    except Exception as exc: return {"success": False, "error": str(exc)}

class SaveRequest(BaseModel):
    path: Optional[str] = None
    push_to_gui: bool = False

@app.post("/flowsheet/save")
def fs_save(req: SaveRequest):
    try:
        with _bridge_lock:
            r = _get_bridge().save_flowsheet(req.path) if hasattr(_get_bridge(),"save_flowsheet") else {"success":False}
        return r
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/flowsheet/run")
def fs_run():
    try:
        with _bridge_lock:
            b = _get_bridge()
            result = b.run_simulation()
            try:
                conv = b.check_convergence() if hasattr(b, "check_convergence") else {}
                result["convergence"] = conv
                if conv and not conv.get("all_converged", True):
                    result["convergence_warning"] = (
                        f"Not converged: {conv.get('not_converged', [])}"
                    )
            except Exception:
                pass
            return result
    except Exception as exc: return {"success": False, "error": str(exc)}


# ── Async task queue ─────────────────────────────────────────────────────────
# Long-running ops (run_simulation, large optimizations) hold _bridge_lock for
# up to several minutes, blocking every other request. These endpoints push the
# work to a thread-pool task and return a task_id the UI can poll. The actual
# bridge call still acquires _bridge_lock — but only inside the worker thread,
# so the API event loop stays responsive.

def _run_simulation_worker():
    with _bridge_lock:
        b = _get_bridge()
        result = b.run_simulation()
        try:
            conv = b.check_convergence() if hasattr(b, "check_convergence") else {}
            result["convergence"] = conv
            if conv and not conv.get("all_converged", True):
                result["convergence_warning"] = (
                    f"Not converged: {conv.get('not_converged', [])}"
                )
        except Exception:
            pass
        return result


@app.post("/flowsheet/run/async")
def fs_run_async():
    """Submit run_simulation to the task queue. Returns task_id immediately."""
    try:
        from task_queue import get_queue
        q = get_queue()
        tid = q.submit("run_simulation", _run_simulation_worker)
        return {"success": True, "task_id": tid,
                "poll_url": f"/tasks/{tid}",
                "message": "Submitted. Poll /tasks/{task_id} for completion."}
    except Exception as exc:
        return {"success": False, "error_code": "TASK_SUBMIT_FAILED",
                "error": str(exc)}


@app.get("/tasks/{task_id}")
def task_status(task_id: str):
    """Poll a background task. Status: queued | running | done | failed | cancelled."""
    try:
        from task_queue import get_queue
        info = get_queue().get(task_id)
        if info is None:
            raise HTTPException(404, f"task {task_id} not found or expired")
        return info
    except HTTPException:
        raise
    except Exception as exc:
        return {"success": False, "error_code": "TASK_GET_FAILED",
                "error": str(exc)}


@app.get("/tasks")
def tasks_list(status: str = ""):
    """List recent tasks. Optional ?status=running|done|failed filter."""
    try:
        from task_queue import get_queue
        return get_queue().list_tasks(status)
    except Exception as exc:
        return {"success": False, "error_code": "TASK_LIST_FAILED",
                "error": str(exc)}


@app.post("/tasks/{task_id}/cancel")
def task_cancel(task_id: str):
    """Attempt to cancel a queued task (running tasks cannot be interrupted)."""
    try:
        from task_queue import get_queue
        return get_queue().cancel(task_id)
    except Exception as exc:
        return {"success": False, "error_code": "TASK_CANCEL_FAILED",
                "error": str(exc)}


@app.post("/flowsheet/run/robust")
def fs_run_robust(max_attempts: int = 4):
    """Run with progressive recycle tolerance relaxation until convergence."""
    strategies = [
        {"method": "Direct",   "tolerance": 1e-4, "max_iterations": 100},
        {"method": "Wegstein", "tolerance": 1e-3, "max_iterations": 150},
        {"method": "Broyden",  "tolerance": 1e-2, "max_iterations": 200},
        {"method": "Direct",   "tolerance": 5e-2, "max_iterations": 300},
    ]
    last: Dict = {}
    for attempt, strategy in enumerate(strategies[:max(1, min(max_attempts, 4))], 1):
        try:
            with _bridge_lock:
                b = _get_bridge()
                if hasattr(b, "configure_recycle"):
                    try:
                        b.configure_recycle(
                            method=strategy["method"],
                            tolerance=strategy["tolerance"],
                            max_iterations=strategy["max_iterations"],
                        )
                    except Exception:
                        pass
                result = b.run_simulation()
                try:
                    conv = b.check_convergence() if hasattr(b, "check_convergence") else {}
                except Exception:
                    conv = {}
                converged = conv.get("all_converged", result.get("success", False))
                last = {
                    **result,
                    "convergence": conv,
                    "attempt": attempt,
                    "strategy": strategy,
                }
                if converged:
                    last["message"] = (
                        f"Converged on attempt {attempt} "
                        f"(method={strategy['method']}, tol={strategy['tolerance']})"
                    )
                    return last
        except Exception as exc:
            last = {"success": False, "error": str(exc), "attempt": attempt, "strategy": strategy}
    last.setdefault("success", False)
    last["message"] = f"Did not converge after {len(strategies[:max_attempts])} attempts"
    return last

@app.post("/flowsheet/validate")
def fs_validate():
    try:
        with _bridge_lock:
            b = _get_bridge()
            if hasattr(b, "validate_feeds"): return b.validate_feeds()
            return {"success": True, "warnings": []}
    except Exception as exc: return {"success": False, "error": str(exc)}

class DeleteObjectRequest(BaseModel):
    tag: str

@app.post("/flowsheet/delete")
def fs_delete(req: DeleteObjectRequest):
    try:
        with _bridge_lock:
            return _get_bridge().delete_object(req.tag)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/flowsheet/new")
def fs_new():
    """Purge to a clean empty flowsheet. Used by the benchmark runner to make
    each task independent (no cross-task flowsheet bleed)."""
    try:
        b = _get_bridge()
        with _bridge_lock:
            if hasattr(b, "reset_to_empty"):
                r = b.reset_to_empty()
            else:
                r = {"success": False, "error": "bridge has no reset_to_empty"}
        # Also clear the agent's conversation/state so it doesn't reference the
        # purged flowsheet.
        try:
            a = _get_agent()
            if hasattr(a, "reset"):
                a.reset()
        except Exception:
            pass
        return r
    except Exception as exc:
        return {"success": False, "error": str(exc)}

class DisconnectRequest(BaseModel):
    uo_tag: str
    stream_tag: str

@app.post("/flowsheet/disconnect")
def fs_disconnect(req: DisconnectRequest):
    try:
        with _bridge_lock:
            return _get_bridge().disconnect_streams(req.uo_tag, req.stream_tag)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/flowsheet/objects")
def fs_objects():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.list_objects() if hasattr(b,"list_objects") else {"streams":[],"unit_ops":[]}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/results")
def fs_results():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_simulation_results() if hasattr(b,"get_simulation_results") else {}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/convergence")
def fs_conv():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.check_convergence() if hasattr(b,"check_convergence") else {"all_converged": True, "not_converged": []}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/diagram")
def fs_diagram():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_diagram() if hasattr(b,"get_diagram") else {"nodes":[], "connections":[]}
    except Exception as exc: return {"nodes":[], "connections":[], "error": str(exc)}

@app.get("/flowsheet/topology")
def fs_topology():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_topology() if hasattr(b,"get_topology") else {}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/meta")
def fs_meta():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_meta() if hasattr(b,"get_meta") else {}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/package")
def fs_package():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return {"property_package": getattr(b, "current_property_package", None)}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/unitops")
def fs_unitops():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_unit_ops_details() if hasattr(b,"get_unit_ops_details") else {"unit_ops":[]}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/loaded")
def fs_loaded():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.list_loaded_flowsheets() if hasattr(b,"list_loaded_flowsheets") else {"loaded":[]}
    except Exception as exc: return {"loaded": [], "error": str(exc)}

@app.post("/flowsheet/switch")
def fs_switch(alias: str):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.switch_flowsheet(alias) if hasattr(b,"switch_flowsheet") else {"success":False}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/templates")
def fs_templates():
    try:
        import flowsheet_templates as ft
        names = ft.list_templates() if hasattr(ft, "list_templates") else []
        templates = []
        for n in names:
            entry = {"name": n}
            if hasattr(ft, "get_template_meta"):
                m = ft.get_template_meta(n) or {}
                entry.update(m)
            templates.append(entry)
        return {"templates": templates}
    except Exception as exc: return {"templates": [], "error": str(exc)}

class CreateFromTemplateRequest(BaseModel):
    name: str
    flowsheet_name: Optional[str] = None

@app.post("/flowsheet/create-from-template")
def fs_create_template(req: CreateFromTemplateRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            if hasattr(b, "create_from_template"):
                return b.create_from_template(req.name, req.flowsheet_name)
            return {"success": False, "error": "create_from_template not implemented"}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/compare")
def fs_compare():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.compare_flowsheets() if hasattr(b,"compare_flowsheets") else {"streams": [], "error":"need 2 flowsheets"}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/flowsheet/pinch")
def fs_pinch(min_approach_temp_C: float = 10.0):
    if not (0.5 <= min_approach_temp_C <= 100):
        return {"success": False, "error": f"min_approach_temp_C must be 0.5–100 °C (got {min_approach_temp_C})"}
    try:
        with _bridge_lock:
            b = _get_bridge()
            # Try native bridge first
            if hasattr(b, "pinch_analysis"):
                try:
                    r = b.pinch_analysis(min_approach_temp_C)
                    if r and r.get("success") and r.get("pinch_temp_C") is not None:
                        return r
                except Exception:
                    pass
            # Pure-Python Linnhoff cascade
            try:
                objs = b.list_objects() if hasattr(b, "list_objects") else {}
                stream_list = objs.get("streams", [])
            except Exception:
                return {"success": False, "error": "Could not retrieve streams — run simulation first"}
            if not stream_list:
                return {"success": False, "error": "No streams found — load and run a flowsheet first"}
            stream_data = []
            for entry in stream_list:
                tag = entry.get("tag") if isinstance(entry, dict) else str(entry)
                if not tag:
                    continue
                try:
                    props = {}
                    if hasattr(b, "get_object_properties"):
                        props = b.get_object_properties(tag) or {}
                    elif hasattr(b, "get_stream_properties"):
                        props = b.get_stream_properties(tag) or {}

                    def _get(*keys):
                        for k in keys:
                            v = props.get(k)
                            if v is not None:
                                try: return float(v)
                                except Exception: pass
                        return None

                    t_in  = _get("Temperature","T","temperature","InletTemperature","Inlet Temperature","T_in")
                    t_out = _get("OutletTemperature","Outlet Temperature","outlet_temperature","T_out","TargetTemperature")
                    q     = _get("HeatDuty","Heat Duty","heat_duty","Q","EnergyFlow","Enthalpy","DeltaH")

                    if t_in is None or t_out is None or q is None or abs(t_in - t_out) < 0.5:
                        continue
                    # Convert K → °C if needed
                    if t_in  > 200: t_in  -= 273.15
                    if t_out > 200: t_out -= 273.15
                    stream_data.append({"tag": tag, "t_supply_C": t_in, "t_target_C": t_out, "heat_kw": abs(q)})
                except Exception:
                    continue

            if not stream_data:
                return {
                    "success": False,
                    "error": (
                        "No stream heat data available. Ensure simulation has converged and "
                        "streams have inlet/outlet temperatures with non-zero heat duty."
                    ),
                }
            return _compute_pinch_analysis(stream_data, min_approach_temp_C)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/flowsheet/backups")
def fs_backups():
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.list_backups() if hasattr(b,"list_backups") else {"backups":[]}
    except Exception as exc: return {"backups": [], "error": str(exc)}

class BackupRestoreRequest(BaseModel):
    path: str

@app.post("/flowsheet/backups/restore")
def fs_backups_restore(req: BackupRestoreRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.restore_backup(req.path) if hasattr(b,"restore_backup") else {"success": False}
    except Exception as exc: return {"success": False, "error": str(exc)}

class PushGuiRequest(BaseModel):
    path: Optional[str] = None
    close_first: bool = False

@app.post("/flowsheet/push-to-gui")
def fs_push_gui(req: PushGuiRequest):
    try:
        from dwsim_gui_bridge import push_to_gui
        path = (req.path or "").strip() or None
        with _bridge_lock:
            b = _get_bridge()
            # No explicit path → use the currently-active flowsheet's saved path.
            # (The UI's Push button sends no path; previously this passed None
            #  straight through → "File not found: None".)
            if not path:
                path = getattr(b, "_flowsheet_path", None)
            # Path known but file not yet written to disk → save it first; you
            # cannot push an unsaved flowsheet into the DWSIM GUI.
            if path and not os.path.exists(os.path.expanduser(path)) \
                    and hasattr(b, "save_flowsheet"):
                try:
                    sr = b.save_flowsheet(path)
                    if isinstance(sr, dict) and sr.get("saved_to"):
                        path = sr["saved_to"]
                except Exception:
                    pass
            # No active path at all → save the current flowsheet to obtain one.
            if not path and hasattr(b, "save_flowsheet"):
                sr = b.save_flowsheet()
                if isinstance(sr, dict) and sr.get("success"):
                    path = sr.get("saved_to") or getattr(b, "_flowsheet_path", None)
        if not path:
            return {"success": False,
                    "error": "No flowsheet to push — create or load a flowsheet "
                             "first, then Push to DWSIM."}
        path = os.path.abspath(os.path.expanduser(path))
        return push_to_gui(path, close_first=req.close_first)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/flowsheet/gui-state")
def fs_gui_state():
    try:
        from dwsim_gui_bridge import get_gui_state
        return get_gui_state()
    except Exception as exc: return {"running": False, "error": str(exc)}

# ── Flowsheet discovery ─────────────────────────────────────────────────────
@app.get("/find")
def find_flowsheets(name_filter: str = "", max_results: int = 30,
                    deep_scan: bool = False):
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "find_flowsheets"):
                return {"flowsheets": []}
            return b.find_flowsheets(
                name_filter=name_filter,
                max_results=max_results,
                deep_scan=deep_scan,
            )
    except Exception as exc:
        return {"flowsheets": [], "error_code": "FIND_FAILED", "error": str(exc)}

@app.get("/flowsheets/scan")
def fs_scan(max_files: int = 80):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.scan_flowsheets(max_files=max_files) if hasattr(b,"scan_flowsheets") else {"flowsheets": []}
    except Exception as exc: return {"flowsheets": [], "error": str(exc)}

@app.get("/flowsheets/scan/path")
def fs_scan_path(directory: str):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.scan_flowsheets_path(directory) if hasattr(b,"scan_flowsheets_path") else {"flowsheets": []}
    except Exception as exc: return {"flowsheets": [], "error": str(exc)}

@app.post("/flowsheets/load-by-path")
def fs_load_by_path(req: LoadRequest): return fs_load(req)

# ── Stream / Unit op ────────────────────────────────────────────────────────
class StreamPropertyRequest(BaseModel):
    tag: str

@app.post("/stream/properties")
def stream_props(req: StreamPropertyRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_stream_properties(req.tag) if hasattr(b,"get_stream_properties") else {"success": False}
    except Exception as exc: return {"success": False, "error": str(exc)}

class SetStreamPropertyRequest(BaseModel):
    tag: str
    property_name: str
    value: float
    unit: str = ""

@app.post("/stream/set_property")
def stream_set(req: SetStreamPropertyRequest):
    try:
        v = _validate_property_name("material_stream", req.property_name)
        with _bridge_lock:
            b = _get_bridge()
            result = b.set_stream_property(req.tag, req.property_name, req.value, req.unit)
        if not v["valid"] and v["warning"]:
            result.setdefault("warnings", []).append(v["warning"])
        return result
    except Exception as exc: return {"success": False, "error": str(exc)}

class SetCompositionRequest(BaseModel):
    tag: str
    compositions: Dict[str, float]

@app.post("/stream/set_composition")
def stream_comp(req: SetCompositionRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.set_stream_composition(req.tag, req.compositions)
    except Exception as exc: return {"success": False, "error": str(exc)}

class SetUnitOpPropertyRequest(BaseModel):
    tag: str
    property_name: str
    value: str
    unit: str = ""

@app.post("/unitop/set_property")
def uo_set(req: SetUnitOpPropertyRequest):
    try:
        # Infer object type from tag prefix heuristic or accept unknown
        obj_type = req.tag.split("_")[0].lower() if "_" in req.tag else req.tag.lower()
        v = _validate_property_name(obj_type, req.property_name)
        with _bridge_lock:
            b = _get_bridge()
            result = b.set_unit_op_property(req.tag, req.property_name, req.value, req.unit)
        if not v["valid"] and v["warning"]:
            result.setdefault("warnings", []).append(v["warning"])
        return result
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/property-names/{object_type}")
def property_names(object_type: str):
    """Return known property names for an object type with fuzzy lookup support."""
    known = _PROPERTY_NAMES.get(object_type)
    if known is None:
        close = difflib.get_close_matches(object_type, list(_PROPERTY_NAMES.keys()), n=3, cutoff=0.5)
        return {
            "object_type": object_type,
            "properties": [],
            "known": False,
            "suggestions": close,
        }
    return {
        "object_type": object_type,
        "properties": known,
        "known": True,
        "all_types": sorted(_PROPERTY_NAMES.keys()),
    }

@app.get("/property-names")
def property_names_all():
    return {"types": sorted(_PROPERTY_NAMES.keys()), "map": _PROPERTY_NAMES}

@app.post("/object/properties")
def obj_props(req: StreamPropertyRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.get_object_properties(req.tag) if hasattr(b,"get_object_properties") else {}
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Property Name Validation ────────────────────────────────────────────────
_PROPERTY_NAMES: Dict[str, List[str]] = {
    "material_stream": [
        "Temperature","Pressure","MassFlow","MolarFlow","VolumetricFlow",
        "VaporFraction","Enthalpy","Entropy","Density","MolarEnthalpy","MassEnthalpy",
        "SpecificHeat","Viscosity","ThermalConductivity","MolecularWeight",
        "MassFraction_*","MolarFraction_*","CompoundMassFlow_*","CompoundMolarFlow_*",
    ],
    "energy_stream": ["EnergyFlow"],
    "heater": ["OutletTemperature","PressureDrop","HeatDuty","EnergyFlow","Efficiency"],
    "cooler": ["OutletTemperature","PressureDrop","HeatDuty","EnergyFlow","Efficiency"],
    "heat_exchanger": [
        "Area","OverallHeatTransferCoefficient","HotSideOutletTemperature",
        "ColdSideOutletTemperature","HotSidePressureDrop","ColdSidePressureDrop",
        "HeatDuty","FlowConfig","OverdesignFactor",
    ],
    "pump": ["OutletPressure","Efficiency","PowerConsumed","DeltaP"],
    "compressor": [
        "OutletPressure","AdiabaticEfficiency","PolytropicEfficiency",
        "MechanicalEfficiency","PowerConsumed","PolytropicHead","CompressionRatio",
    ],
    "expander": ["OutletPressure","IsentropicEfficiency","MechanicalEfficiency","PowerGenerated"],
    "valve": ["OutletPressure","PressureDrop","Cv"],
    "pipe": ["Roughness","Diameter","Length","ThermalConductivity","HeatTransferCoefficient"],
    "mixer": [],
    "splitter": ["SplitRatio_*"],
    "distillation_column": [
        "NumberOfStages","FeedStage","RefluxRatio","ReboilRatio",
        "CondenserPressure","ReboilerPressure","CondenserType",
        "LightKeyRecovery","HeavyKeyRecovery","DistillateFlowRate","BottomFlowRate",
    ],
    "absorption_column": ["NumberOfStages","SolventFeedStage","SolventFlowRate"],
    "shortcut_column": [
        "RefluxRatio","LightKey","HeavyKey","LightKeyRecovery","HeavyKeyRecovery",
        "CondenserPressure","ReboilerPressure",
    ],
    "conversion_reactor": ["Temperature","Pressure","Volume","ConversionSpec_*"],
    "equilibrium_reactor": ["Temperature","Pressure","Volume"],
    "gibbs_reactor": ["Temperature","Pressure"],
    "cstr": ["Temperature","Pressure","Volume","ResidenceTime"],
    "pfr": ["Temperature","Pressure","Volume","Length","Diameter"],
    "flash": ["Temperature","Pressure","VaporFraction"],
    "separator": ["SeparationEfficiency"],
    "gas_liquid_separator": ["Temperature","Pressure","SeparationEfficiency"],
    "component_separator": ["SeparationEfficiency_*"],
    "recycle": ["ConvergenceMethod","Tolerance","MaxIterations","Acceleration"],
    "adjust": [
        "ManipulatedObjectTag","ManipulatedProperty","ControlledObjectTag",
        "ControlledProperty","SetPoint",
    ],
    "specification": ["SourceTag","SourceProperty","TargetTag","TargetProperty","SpecValue"],
    "orifice_plate": ["OrificeDiameter","InletDiameter","DischargeCoefficient"],
    "tank": ["Volume","Height","Diameter"],
    "crystallizer": ["Temperature","SolventRecovery"],
    "dryer": ["OutletMoisture","InletTemperature","OutletTemperature"],
    "filter": ["SolidsEfficiency"],
    "cake_filter": ["Area","MediumResistance","CakeResistance"],
    "hydrocyclone": ["CutSize","Efficiency"],
    "crusher": ["WorkIndex","ProductSize"],
    "screen": ["ScreenSize"],
    "conveyor": ["Angle","Length"],
    "solids_separator": ["SolidsEfficiency"],
    "python_script": [],
    "spreadsheet": [],
}

def _validate_property_name(obj_type: str, prop_name: str) -> Dict:
    """Fuzzy-validate a property name for an object type."""
    known = _PROPERTY_NAMES.get(obj_type, None)
    if known is None:
        return {"valid": True, "suggestions": [], "warning": None}  # unknown type, allow all
    if not known:
        return {"valid": True, "suggestions": [], "warning": None}  # no constraints defined
    exact = [k for k in known if not k.endswith("_*")]
    prefixes = [k[:-1] for k in known if k.endswith("_*")]
    if prop_name in exact:
        return {"valid": True, "suggestions": [], "warning": None}
    for p in prefixes:
        if prop_name.startswith(p):
            return {"valid": True, "suggestions": [], "warning": None}
    suggestions = difflib.get_close_matches(prop_name, exact, n=3, cutoff=0.45)
    msg = f"Property '{prop_name}' not recognised for '{obj_type}'."
    if suggestions:
        msg += f" Did you mean: {', '.join(suggestions)}?"
    return {"valid": False, "suggestions": suggestions, "warning": msg}


# ── Pinch Analysis (Linnhoff cascade) ───────────────────────────────────────
def _build_composite_curve(
    streams: List[Dict], side: str
) -> List[Dict]:
    """Build composite curve points [{T, H}] for hot or cold streams."""
    if not streams:
        return []
    pts_set: set = set()
    for s in streams:
        pts_set.add(s["Ts"]); pts_set.add(s["Tt"])
    if side == "hot":
        pts = sorted(pts_set, reverse=True)
    else:
        pts = sorted(pts_set)
    curve: List[Dict] = [{"T": pts[0], "H": 0.0}]
    cum = 0.0
    for i in range(len(pts) - 1):
        if side == "hot":
            T_hi, T_lo = pts[i], pts[i + 1]
            dH = sum(
                ((min(s["Ts"], T_hi) - max(s["Tt"], T_lo)) / (s["Ts"] - s["Tt"])) * s["Q"]
                for s in streams
                if s["Ts"] > s["Tt"] and min(s["Ts"], T_hi) > max(s["Tt"], T_lo)
            )
        else:
            T_lo, T_hi = pts[i], pts[i + 1]
            dH = sum(
                ((min(s["Tt"], T_hi) - max(s["Ts"], T_lo)) / (s["Tt"] - s["Ts"])) * s["Q"]
                for s in streams
                if s["Tt"] > s["Ts"] and min(s["Tt"], T_hi) > max(s["Ts"], T_lo)
            )
        cum += dH
        curve.append({"T": pts[i + 1] if side == "hot" else pts[i + 1], "H": round(cum, 4)})
    return curve


def _compute_pinch_analysis(stream_data: List[Dict], delta_t: float) -> Dict:
    """
    Linnhoff cascade pinch analysis.
    stream_data: list of {tag, t_supply_C, t_target_C, heat_kw}
    """
    half = delta_t / 2.0
    hot, cold = [], []
    for s in stream_data:
        ts, tt, q = s.get("t_supply_C"), s.get("t_target_C"), s.get("heat_kw")
        if ts is None or tt is None or q is None or abs(ts - tt) < 0.01:
            continue
        if ts > tt:
            hot.append({"tag": s["tag"], "Ts": ts, "Tt": tt, "Q": abs(q), "type": "hot"})
        else:
            cold.append({"tag": s["tag"], "Ts": ts, "Tt": tt, "Q": abs(q), "type": "cold"})
    if not hot and not cold:
        return {"success": False, "error": "No processable hot/cold stream pairs found"}

    # Shifted temperatures
    temps_set: set = set()
    for s in hot:
        temps_set.add(s["Ts"] - half); temps_set.add(s["Tt"] - half)
    for s in cold:
        temps_set.add(s["Ts"] + half); temps_set.add(s["Tt"] + half)
    temps = sorted(temps_set, reverse=True)

    # Interval heat balance
    intervals = []
    for i in range(len(temps) - 1):
        T_hi, T_lo = temps[i], temps[i + 1]
        q_hot = sum(
            ((min(s["Ts"] - half, T_hi) - max(s["Tt"] - half, T_lo)) / (s["Ts"] - s["Tt"])) * s["Q"]
            for s in hot
            if s["Ts"] - s["Tt"] > 0 and min(s["Ts"] - half, T_hi) > max(s["Tt"] - half, T_lo)
        )
        q_cold = sum(
            ((min(s["Tt"] + half, T_hi) - max(s["Ts"] + half, T_lo)) / (s["Tt"] - s["Ts"])) * s["Q"]
            for s in cold
            if s["Tt"] - s["Ts"] > 0 and min(s["Tt"] + half, T_hi) > max(s["Ts"] + half, T_lo)
        )
        intervals.append({"T_high": T_hi, "T_low": T_lo, "q_hot": q_hot, "q_cold": q_cold, "net": q_hot - q_cold})

    # Cascade
    cascade = [0.0]
    for iv in intervals:
        cascade.append(cascade[-1] + iv["net"])
    q_hmin = max(0.0, -min(cascade))

    cascade_f = [q_hmin]
    for iv in intervals:
        cascade_f.append(cascade_f[-1] + iv["net"])
    q_cmin = cascade_f[-1]

    pinch_temp = None
    for i, val in enumerate(cascade_f):
        if abs(val) < 1e-4:
            pinch_temp = temps[i]
            break

    return {
        "success": True,
        "pinch_temp_C": round(pinch_temp, 2) if pinch_temp is not None else None,
        "q_hmin_kw": round(q_hmin, 3),
        "q_cmin_kw": round(q_cmin, 3),
        "n_hot": len(hot),
        "n_cold": len(cold),
        "delta_t_min_C": delta_t,
        "streams": [
            {"tag": s["tag"], "type": s["type"], "t_supply": s["Ts"], "t_target": s["Tt"], "heat_kw": s["Q"]}
            for s in hot + cold
        ],
        "hot_composite": _build_composite_curve(hot, "hot"),
        "cold_composite": _build_composite_curve(cold, "cold"),
        "summary": (
            f"ΔT_min={delta_t}°C | Pinch @ {pinch_temp:.1f}°C | "
            f"QH_min={q_hmin:.1f} kW | QC_min={q_cmin:.1f} kW"
            if pinch_temp is not None else
            f"ΔT_min={delta_t}°C | No pinch (threshold problem) | "
            f"QH_min={q_hmin:.1f} kW | QC_min={q_cmin:.1f} kW"
        ),
    }


# ── Parametric / Optimize ───────────────────────────────────────────────────
class ParametricRequest(BaseModel):
    vary_tag: str
    vary_property: str
    vary_unit: str = ""
    values: List[float]
    observe_tag: str
    observe_property: str

@app.post("/parametric")
def parametric(req: ParametricRequest):
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.parametric_study(req.vary_tag, req.vary_property, req.vary_unit,
                                       req.values, req.observe_tag, req.observe_property)
    except Exception as exc: return {"success": False, "error": str(exc)}

class OptimizeRequest(BaseModel):
    vary_tag: str
    vary_property: str
    vary_unit: str = ""
    lower_bound: float
    upper_bound: float
    observe_tag: str
    observe_property: str
    minimize: bool = True

@app.post("/optimize")
def optimize(req: OptimizeRequest):
    try:
        from optimizer import DWSIMOptimizer
        opt = DWSIMOptimizer(_get_bridge())
        with _bridge_lock:
            return opt.optimize(
                vary_tag=req.vary_tag,
                vary_property=req.vary_property,
                vary_unit=req.vary_unit,
                observe_tag=req.observe_tag,
                observe_property=req.observe_property,
                objective="minimize" if req.minimize else "maximize",
                lower_bound=req.lower_bound,
                upper_bound=req.upper_bound,
            )
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/optimize/multivar")
def optimize_mv(req: dict):
    try:
        from optimizer import DWSIMOptimizer
        opt = DWSIMOptimizer(_get_bridge())
        with _bridge_lock:
            return opt.multi_optimize(**req) if hasattr(opt,"multi_optimize") else {"success":False,"error":"multi_optimize not available"}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/optimize/bayesian")
def optimize_bo(req: dict):
    """Gaussian-process Bayesian Optimization of a DWSIM simulation.

    Request shape:
      {
        "variables": [{"tag": "FEED", "property": "temperature",
                       "unit": "C", "lower": 25, "upper": 200}, ...],
        "observe_tag": "PROD",
        "observe_property": "temperature_C",
        "minimize": false,           // true → minimize, false → maximize
        "n_initial": 5,              // LHS warm-up samples
        "max_iter":  20,             // BO iterations after warm-up
        "seed":      42
      }
    """
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "bayesian_optimize"):
                return {"success": False, "error_code": "BO_NOT_AVAILABLE",
                        "error": "bridge has no bayesian_optimize method"}
            return b.bayesian_optimize(
                variables        = req.get("variables", []),
                observe_tag      = req.get("observe_tag", ""),
                observe_property = req.get("observe_property", ""),
                minimize         = bool(req.get("minimize", True)),
                n_initial        = int(req.get("n_initial", 5)),
                max_iter         = int(req.get("max_iter", 20)),
                xi               = float(req.get("xi", 0.01)),
                seed             = int(req.get("seed", 42)),
                save_plot        = str(req.get("save_plot", "")),
            )
    except Exception as exc:
        return {"success": False, "error_code": "BO_FAILED", "error": str(exc)}


# ── NL-driven end-to-end optimization workflow (poster flow) ────────────────

@app.post("/optimize/workflow")
def optimize_workflow(req: dict):
    """Natural-language optimization workflow matching the poster.

    Request:
      {"goal": "Maximize H2+CO purity at PSA while minimising total energy",
       "max_iter": 50,
       "tolerance": 1e-3,
       "llm_provider": "groq" | "openai" | ... (optional, uses default)}

    Returns:
      {success, spec, result, chat_markdown}
    where chat_markdown is the poster-style result ready to render in chat.
    """
    try:
        goal = str(req.get("goal", "")).strip()
        if not goal:
            return {"success": False, "error_code": "NO_GOAL",
                    "error": "Provide a 'goal' string."}
        # Build an LLM client. Fall back to None on failure — the orchestrator
        # has a heuristic backup.
        llm = None
        try:
            from llm_client import LLMClient
            llm = LLMClient(provider=req.get("llm_provider"))
        except Exception as exc:
            print(f"[optimize/workflow] LLMClient unavailable, falling back to heuristic: {exc}")
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "optimize_flowsheet_with_llm"):
                return {"success": False,
                        "error_code": "WORKFLOW_NOT_AVAILABLE"}
            return b.optimize_flowsheet_with_llm(
                goal      = goal, llm = llm,
                max_iter  = int(req.get("max_iter", 50)),
                tolerance = float(req.get("tolerance", 1e-3)),
            )
    except Exception as exc:
        return {"success": False, "error_code": "WORKFLOW_FAILED",
                "error": str(exc)}


@app.post("/optimize/workflow/async")
def optimize_workflow_async(req: dict):
    """Submit the end-to-end NL optimization workflow to the task queue."""
    try:
        from task_queue import get_queue
        goal = str(req.get("goal", "")).strip()
        if not goal:
            return {"success": False, "error_code": "NO_GOAL",
                    "error": "Provide a 'goal' string."}

        def _worker(report):
            llm = None
            try:
                from llm_client import LLMClient
                llm = LLMClient(provider=req.get("llm_provider"))
            except Exception:
                pass

            # Stream each workflow stage to the task so the panel shows the
            # same step-by-step unfolding as the chat path.
            def _on_step(stage, detail=""):
                try: report(stage, detail)
                except Exception: pass

            _last = [0]
            def _on_eval(it, params, obj, best):
                # Throttle like the chat stream: first 5 evals, then every 5th.
                if not (it <= 5 or it % 5 == 0 or it - _last[0] >= 5):
                    return
                _last[0] = it
                obj_s  = f"{obj:.4g}"  if obj  is not None else "failed"
                best_s = f"{best:.4g}" if best is not None else "—"
                try: report(f"eval {it}", f"obj={obj_s}  best={best_s}")
                except Exception: pass

            with _bridge_lock:
                b = _get_bridge()
                return b.optimize_flowsheet_with_llm(
                    goal=goal, llm=llm,
                    max_iter  = int(req.get("max_iter", 50)),
                    tolerance = float(req.get("tolerance", 1e-3)),
                    on_step   = _on_step,
                    on_eval   = _on_eval,
                )

        tid = get_queue().submit_streaming("optimize_workflow", _worker)
        return {"success": True, "task_id": tid, "poll_url": f"/tasks/{tid}",
                "message": "NL optimization workflow submitted."}
    except Exception as exc:
        return {"success": False, "error_code": "WORKFLOW_SUBMIT_FAILED",
                "error": str(exc)}


@app.get("/optimize/suggest-variables")
def optimize_suggest_variables(max_n: int = 8):
    """Inspect the loaded flowsheet and propose decision variables.
    Useful for the UI's "Auto-suggest" button."""
    try:
        from optimization_orchestrator import suggest_decision_variables
        with _bridge_lock:
            b = _get_bridge()
            return {"success": True,
                    "variables": suggest_decision_variables(b, max_n=int(max_n))}
    except Exception as exc:
        return {"success": False, "error_code": "SUGGEST_FAILED",
                "error": str(exc)}


# ── Robust complex-flowsheet optimization ──────────────────────────────────

@app.post("/optimize/complex")
def optimize_complex_endpoint(req: dict):
    """Robust optimization for complex flowsheets — adds:
      • Pre-flight validation (vars writeable, objective readable)
      • Multi-solver: DE (global) → Simplex (local refinement)
      • Auto bound-widening when optimum hugs a bound (up to 3 rounds)
      • LLM sanity-check on objective↔goal alignment
      • Evaluation-failure-rate analysis with warning at >30 %

    Same request shape as /optimize/dwsim-native, plus:
      "widen_bounds": true/false (default true)
      "multi_solver": true/false (default true)
      "user_goal":   "<original NL goal for sanity-check>"
    """
    try:
        llm = None
        if req.get("user_goal"):
            try:
                from llm_client import LLMClient
                llm = LLMClient()
            except Exception:
                pass
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "optimize_complex"):
                return {"success": False, "error_code": "COMPLEX_OPT_UNAVAILABLE"}
            return b.optimize_complex(
                variables    = req.get("variables", []),
                objective    = req.get("objective", {}),
                minimize     = bool(req.get("minimize", True)),
                max_iter     = int(req.get("max_iter", 80)),
                tolerance    = float(req.get("tolerance", 1e-3)),
                widen_bounds = bool(req.get("widen_bounds", True)),
                multi_solver = bool(req.get("multi_solver", True)),
                llm          = llm,
                user_goal    = str(req.get("user_goal", "")),
            )
    except Exception as exc:
        return {"success": False, "error_code": "COMPLEX_OPT_FAILED",
                "error": str(exc)}


# ── Reflection / escape-hatch endpoints ─────────────────────────────────────

@app.post("/reflect")
def reflect(req: dict):
    """GET or SET any property on any DWSIM object via .NET reflection.
    {object_name, property_path, value (optional for SET)}"""
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.reflect_get_set(
                object_name   = req.get("object_name", "flowsheet"),
                property_path = req.get("property_path", ""),
                value         = req.get("value"),
            )
    except Exception as exc:
        return {"success": False, "error_code": "REFLECT_FAILED", "error": str(exc)}


@app.post("/exec_python")
def exec_python_endpoint(req: dict):
    """Execute a sandboxed Python snippet against the live DWSIM flowsheet.
    {code: str, timeout_s: float}
    Context: flowsheet, get_obj(name), results{}, math"""
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.exec_python(
                code      = req.get("code", ""),
                timeout_s = float(req.get("timeout_s", 60.0)),
            )
    except Exception as exc:
        return {"success": False, "error_code": "EXEC_FAILED", "error": str(exc)}


@app.post("/inspect")
def inspect(req: dict):
    """Discover all properties on any DWSIM object.
    {object_name, filter_prefix?, filter_type?, max_props?}"""
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.inspect_object(
                object_name   = req.get("object_name", "flowsheet"),
                filter_prefix = req.get("filter_prefix", ""),
                filter_type   = req.get("filter_type", ""),
                max_props     = int(req.get("max_props", 80)),
            )
    except Exception as exc:
        return {"success": False, "error_code": "INSPECT_FAILED", "error": str(exc)}


@app.post("/spec_loop")
def spec_loop(req: dict):
    """Bisection loop: vary a decision variable until an observable meets a spec.
    {vary_object, vary_path, vary_lo, vary_hi,
     observe_object, observe_path, target, tolerance, direction, max_iter}"""
    try:
        with _bridge_lock:
            b = _get_bridge()
            return b.iterative_spec_loop(spec=req)
    except Exception as exc:
        return {"success": False, "error_code": "SPEC_LOOP_FAILED", "error": str(exc)}


# ── Process design advisor endpoints ────────────────────────────────────────

@app.post("/design/synthesize")
def design_synthesize(req: dict):
    try:
        from process_design_advisor import process_synthesis
        return process_synthesis(
            goal=req.get("goal", ""),
            reactants=req.get("reactants", []),
            products=req.get("products", []),
            phase=req.get("phase", "liquid"),
            scale_tonne_h=float(req.get("scale_tonne_h", 10.0)),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/design/equipment-size")
def design_equipment_size(req: dict):
    try:
        from process_design_advisor import equipment_sizing
        return equipment_sizing(**{k: v for k, v in req.items()})
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/design/separation-sequence")
def design_sep_seq(req: dict):
    try:
        from process_design_advisor import separation_sequence
        return separation_sequence(
            compounds=req.get("compounds", []),
            property_package=req.get("property_package", "Peng-Robinson"),
            purity_target=float(req.get("purity_target", 0.99)),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/design/pp-select")
def design_pp_select(req: dict):
    try:
        from process_design_advisor import property_package_selector
        return property_package_selector(
            compounds=req.get("compounds", []),
            pressure_bar=float(req.get("pressure_bar", 1.01325)),
            temperature_C=float(req.get("temperature_C", 25.0)),
            application=req.get("application", ""),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/design/heat-integration")
def design_heat_integration(req: dict):
    try:
        from process_design_advisor import heat_integration_targets
        return heat_integration_targets(
            hot_streams=req.get("hot_streams", []),
            cold_streams=req.get("cold_streams", []),
            delta_T_min_C=float(req.get("delta_T_min_C", 10.0)),
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/design/checklist/{process_type}")
def design_checklist_endpoint(process_type: str):
    try:
        from process_design_advisor import design_checklist
        return design_checklist(process_type)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── DWSIM troubleshooter endpoints ──────────────────────────────────────────

@app.post("/troubleshoot")
def troubleshoot(req: dict):
    """Diagnose DWSIM issues with ranked root-cause + step-by-step fixes."""
    try:
        from dwsim_troubleshooter import troubleshoot_process
        with _bridge_lock:
            b = _get_bridge()
            st = getattr(b, "state", None)
            fs_state = ({"unit_ops": [{"type": t} for t in getattr(st, "unit_ops", []) or []],
                          "streams": getattr(st, "streams", []) or []} if st else None)
        return troubleshoot_process(
            process_type=req.get("process_type", ""),
            issue=req.get("issue", ""),
            flowsheet_state=fs_state,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/troubleshoot/convergence/{unit_type}")
def troubleshoot_convergence(unit_type: str):
    try:
        from dwsim_troubleshooter import convergence_guide
        return convergence_guide(unit_type)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.post("/troubleshoot/decode-error")
def troubleshoot_decode_error(req: dict):
    try:
        from dwsim_troubleshooter import error_decoder
        return error_decoder(req.get("message", ""))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/optimize/algorithms")
def list_optimization_algorithms():
    """List ALL DWSIM optimization algorithms with availability status.
    Returns 14 algorithms across DotNumerics, DWSIM MathOps, IPOPT, SwarmOps."""
    try:
        from dwsim_algorithms import list_algorithms
        algos = list_algorithms()
        return {
            "success": True,
            "count":   len(algos),
            "available_count": sum(1 for a in algos if a["available"]),
            "algorithms": algos,
        }
    except Exception as exc:
        return {"success": False, "error_code": "ALGO_LIST_FAILED",
                "error": str(exc)}


@app.post("/optimize/algorithm")
def run_algorithm(req: dict):
    """Run any DWSIM optimization algorithm directly on the loaded flowsheet.

    Request:
      {
        "algorithm": "simplex" | "lbfgs" | "newton" | "hooke" | "sa" |
                     "pso_math" | "de" | "ipopt" | "de_swarm" | "pso_swarm" |
                     "jde" | "desuite" | "hill" | "newton_math",
        "variables":  [{tag, property, unit, lower, upper, initial}],
        "objective":  {type, tag, property} or {type, expression},
        "minimize":   true,
        "max_iter":   100,
        "tolerance":  1e-4
      }
    """
    try:
        with _bridge_lock:
            b = _get_bridge()
            from dwsim_algorithms import optimize_flowsheet
            return optimize_flowsheet(
                bridge    = b,
                algorithm = req.get("algorithm", "simplex"),
                variables = req.get("variables", []),
                objective = req.get("objective", {}),
                minimize  = bool(req.get("minimize", True)),
                max_iter  = int(req.get("max_iter", 100)),
                tolerance = float(req.get("tolerance", 1e-4)),
            )
    except Exception as exc:
        return {"success": False, "error_code": "ALGORITHM_RUN_FAILED",
                "error": str(exc)}


@app.post("/optimize/algorithm/async")
def run_algorithm_async(req: dict):
    """Submit a DWSIM algorithm run to the background task queue."""
    try:
        from task_queue import get_queue
        def _worker():
            with _bridge_lock:
                b = _get_bridge()
                from dwsim_algorithms import optimize_flowsheet
                return optimize_flowsheet(
                    bridge    = b,
                    algorithm = req.get("algorithm", "simplex"),
                    variables = req.get("variables", []),
                    objective = req.get("objective", {}),
                    minimize  = bool(req.get("minimize", True)),
                    max_iter  = int(req.get("max_iter", 100)),
                    tolerance = float(req.get("tolerance", 1e-4)),
                )
        tid = get_queue().submit(f"algo_{req.get('algorithm','?')}", _worker)
        return {"success": True, "task_id": tid, "poll_url": f"/tasks/{tid}",
                "message": f"Algorithm '{req.get('algorithm')}' submitted."}
    except Exception as exc:
        return {"success": False, "error_code": "ALGO_SUBMIT_FAILED",
                "error": str(exc)}


@app.post("/optimize/internal")
def optimize_internal(req: dict):
    """TRUE DWSIM-internal optimization via OptimizationCase — the same
    engine as the DWSIM GUI Optimizer button.

    Same request shape as /optimize/dwsim-native:
      {variables, objective, minimize, method, max_iter, tolerance, case_name}

    Supported methods: simplex (default), lbfgs, newton, brent, al-lbfgs
    """
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "optimize_with_internal_engine"):
                return {"success": False,
                        "error_code": "INTERNAL_OPT_UNAVAILABLE"}
            return b.optimize_with_internal_engine(
                variables  = req.get("variables", []),
                objective  = req.get("objective", {}),
                minimize   = bool(req.get("minimize", True)),
                method     = str(req.get("method", "simplex")),
                max_iter   = int(req.get("max_iter", 100)),
                tolerance  = float(req.get("tolerance", 1e-4)),
                case_name  = str(req.get("case_name", "AI_Optimization")),
            )
    except Exception as exc:
        return {"success": False, "error_code": "INTERNAL_OPT_FAILED",
                "error": str(exc)}


@app.post("/optimize/internal/async")
def optimize_internal_async(req: dict):
    """Submit DWSIM-internal optimization to the task queue."""
    try:
        from task_queue import get_queue
        def _worker():
            with _bridge_lock:
                b = _get_bridge()
                return b.optimize_with_internal_engine(
                    variables  = req.get("variables", []),
                    objective  = req.get("objective", {}),
                    minimize   = bool(req.get("minimize", True)),
                    method     = str(req.get("method", "simplex")),
                    max_iter   = int(req.get("max_iter", 100)),
                    tolerance  = float(req.get("tolerance", 1e-4)),
                    case_name  = str(req.get("case_name", "AI_Optimization")),
                )
        tid = get_queue().submit("optimize_internal", _worker)
        return {"success": True, "task_id": tid,
                "poll_url": f"/tasks/{tid}",
                "message": "DWSIM-internal OptimizationCase submitted."}
    except Exception as exc:
        return {"success": False, "error_code": "INTERNAL_OPT_SUBMIT_FAILED",
                "error": str(exc)}


@app.get("/optimize/pp-check")
def optimize_pp_check(override: bool = False):
    """Validate that the loaded property package suits the compound list.
    Returns a credibility report used as a preflight gate for optimisation."""
    try:
        from pp_validator import validate_loaded_flowsheet
        with _bridge_lock:
            b = _get_bridge()
            return validate_loaded_flowsheet(b, override=bool(override))
    except Exception as exc:
        return {"success": False, "error_code": "PP_CHECK_FAILED",
                "error": str(exc)}


@app.get("/optimize/benchmarks")
def optimize_benchmarks(max_iter: int = 25, n_initial: int = 5,
                          seed: int = 42):
    """Run the textbook benchmark suite — 10 problems with published or
    analytical optima. Returns gap-from-optimum table for each problem
    plus aggregate statistics. Used to validate framework accuracy."""
    try:
        from benchmark_suite import run_full_suite, format_results_table
        rep = run_full_suite(n_initial=int(n_initial),
                              max_iter=int(max_iter), seed=int(seed))
        rep["markdown_table"] = format_results_table(rep)
        return rep
    except Exception as exc:
        return {"success": False, "error_code": "BENCHMARK_FAILED",
                "error": str(exc)}


@app.get("/optimize/complexity")
def optimize_complexity():
    """Inspect the loaded flowsheet and report its complexity score, so the
    UI / agent can decide whether to use the simple or complex path."""
    try:
        from complex_optimizer import detect_flowsheet_complexity
        with _bridge_lock:
            b = _get_bridge()
            return detect_flowsheet_complexity(b)
    except Exception as exc:
        return {"success": False, "error_code": "COMPLEXITY_FAILED",
                "error": str(exc)}


# ── DWSIM-native optimization (uses DWSIM's own L-BFGS-B / Simplex / DE) ────

@app.post("/optimize/dwsim-native")
def optimize_dwsim_native(req: dict):
    """Run an optimization using DWSIM's INTERNAL solvers (the same engines
    the DWSIM GUI Optimizer uses). Synchronous — blocks until done.

    Request:
      {
        "variables": [{"tag":"RC-01","property":"outlet_temperature_C",
                       "unit":"C","lower":580,"upper":650,"initial":600}, ...],
        "objective": {"type":"variable","tag":"PROD","property":"mole_fraction_H2"}
          OR
        "objective": {"type":"expression",
                      "expression":"H2 + CO - 0.01*energy",
                      "named_values":[
                        {"name":"H2","tag":"PSA","property":"mole_fraction_H2"},
                        {"name":"CO","tag":"PSA","property":"mole_fraction_CO"},
                        {"name":"energy","tag":"TOTAL","property":"duty_kW"}]},
        "method":   "simplex"  | "lbfgs" | "newton" | "powell" | "de",
        "minimize": false,
        "max_iter": 50,
        "tolerance": 1e-3
      }
    """
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "dwsim_optimize"):
                return {"success": False,
                        "error_code": "NATIVE_OPT_NOT_AVAILABLE"}
            return b.dwsim_optimize(
                variables = req.get("variables", []),
                objective = req.get("objective", {}),
                method    = str(req.get("method", "simplex")),
                minimize  = bool(req.get("minimize", True)),
                max_iter  = int(req.get("max_iter", 50)),
                tolerance = float(req.get("tolerance", 1e-3)),
            )
    except Exception as exc:
        return {"success": False, "error_code": "NATIVE_OPT_FAILED",
                "error": str(exc)}


@app.post("/optimize/dwsim-native/async")
def optimize_dwsim_native_async(req: dict):
    """Submit a DWSIM-native optimization to the background task queue.
    Returns task_id; poll /tasks/{id}."""
    try:
        from task_queue import get_queue

        def _worker():
            with _bridge_lock:
                b = _get_bridge()
                return b.dwsim_optimize(
                    variables = req.get("variables", []),
                    objective = req.get("objective", {}),
                    method    = str(req.get("method", "simplex")),
                    minimize  = bool(req.get("minimize", True)),
                    max_iter  = int(req.get("max_iter", 50)),
                    tolerance = float(req.get("tolerance", 1e-3)),
                )

        tid = get_queue().submit("dwsim_native_optimize", _worker)
        return {"success": True, "task_id": tid, "poll_url": f"/tasks/{tid}",
                "message": "DWSIM-native optimization submitted."}
    except Exception as exc:
        return {"success": False, "error_code": "NATIVE_OPT_SUBMIT_FAILED",
                "error": str(exc)}


@app.post("/optimize/bayesian/async")
def optimize_bo_async(req: dict):
    """Submit a Bayesian Optimization run to the background task queue.
    Returns immediately with a task_id; poll /tasks/{task_id}.

    BO with 25 evals × 30 s each = ~12 min; running synchronously would
    block every other request via _bridge_lock the whole time."""
    try:
        from task_queue import get_queue

        def _bo_worker():
            with _bridge_lock:
                b = _get_bridge()
                return b.bayesian_optimize(
                    variables        = req.get("variables", []),
                    observe_tag      = req.get("observe_tag", ""),
                    observe_property = req.get("observe_property", ""),
                    minimize         = bool(req.get("minimize", True)),
                    n_initial        = int(req.get("n_initial", 5)),
                    max_iter         = int(req.get("max_iter", 20)),
                    xi               = float(req.get("xi", 0.01)),
                    seed             = int(req.get("seed", 42)),
                    save_plot        = str(req.get("save_plot", "")),
                )

        tid = get_queue().submit("bayesian_optimize", _bo_worker)
        return {"success": True, "task_id": tid,
                "poll_url": f"/tasks/{tid}",
                "message": f"BO submitted (n_evals ≈ {int(req.get('n_initial', 5)) + int(req.get('max_iter', 20))}). Poll /tasks/{tid}."}
    except Exception as exc:
        return {"success": False, "error_code": "BO_SUBMIT_FAILED",
                "error": str(exc)}

@app.post("/monte-carlo")
def monte_carlo(req: dict):
    try:
        with _bridge_lock:
            b = _get_bridge()
            if hasattr(b, "monte_carlo_study"):
                return b.monte_carlo_study(
                    vary_params=req.get("vary_params", []),
                    observe_tag=req.get("observe_tag", ""),
                    observe_property=req.get("observe_property", ""),
                    n_samples=req.get("n_samples", 100),
                )
            return {"success": False, "error": "monte_carlo_study not available on bridge"}
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Economics ───────────────────────────────────────────────────────────────
@app.get("/economics/defaults")
def econ_defaults():
    try:
        from economics import get_defaults
        return get_defaults()
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/economics/estimate")
def econ_estimate(params: dict):
    try:
        from economics import estimate
        return estimate(_get_bridge(), params)
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Report ──────────────────────────────────────────────────────────────────
@app.post("/report/generate")
def report_gen(req: dict):
    try:
        from report_generator import generate_report
        return generate_report(_get_bridge(), req)
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/report/download")
def report_dl(path: str):
    # Security: only allow downloads from temp dir or backend dir
    import tempfile
    abs_path = os.path.abspath(path)
    allowed_dirs = [
        os.path.abspath(tempfile.gettempdir()),
        os.path.abspath(_BACKEND_DIR),
    ]
    if not any(abs_path.startswith(d) for d in allowed_dirs):
        raise HTTPException(403, "Access denied — path outside allowed directories")
    if not os.path.isfile(abs_path):
        raise HTTPException(404, "Not found")
    return FileResponse(abs_path, filename=os.path.basename(abs_path))

@app.get("/results/export/excel")
def export_xlsx():
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "export_excel"):
                raise HTTPException(501, "Excel export not available in this bridge version")
            path = b.export_excel()
        if not path:
            raise HTTPException(500, "Export returned no path — ensure a simulation has been run first")
        if not os.path.exists(path):
            raise HTTPException(500, f"Export file missing at: {path}")
        return FileResponse(
            path, filename="results.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Excel export failed: {exc}")

@app.get("/results/export/csv")
def export_csv():
    try:
        with _bridge_lock:
            b = _get_bridge()
            if not hasattr(b, "export_csv"):
                raise HTTPException(501, "CSV export not available in this bridge version")
            path = b.export_csv()
        if not path:
            raise HTTPException(500, "Export returned no path — ensure a simulation has been run first")
        if not os.path.exists(path):
            raise HTTPException(500, f"Export file missing at: {path}")
        return FileResponse(path, filename="results.csv", media_type="text/csv")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"CSV export failed: {exc}")

# ── LLM management ──────────────────────────────────────────────────────────
@app.get("/llm/status")
def llm_status():
    try:
        a = _agent
        prov = os.getenv("LLM_PROVIDER","groq")
        mod  = os.getenv("LLM_MODEL","")
        if a and hasattr(a,"llm"):
            prov = getattr(a.llm,"provider", prov)
            mod  = getattr(a.llm,"model", mod)
        return {"provider": prov, "model": mod, "ready": a is not None}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/llm/switch")
def llm_switch(provider: str, model: str):
    global _agent
    try:
        if (provider or "").lower() == "gemini":
            return {"success": False,
                    "error": "Gemini is no longer a supported provider. "
                             "Choose groq, openai, anthropic, or ollama."}
        _, LLM, _DM = _safe_import_agent()
        env_key = {"groq":"GROQ_API_KEY",
                   "openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","ollama":""}.get(provider,"")
        api_key = os.getenv(env_key, "") if env_key else ""
        os.environ["LLM_PROVIDER"] = provider
        os.environ["LLM_MODEL"]    = model
        new_llm = LLM(provider=provider, api_key=api_key, model=model)
        # User's explicit selection must not silently self-switch provider on a
        # transient failure (the agent's own failover handles that). Without this
        # a rate-limit on the chosen provider would permanently flip the UI to
        # another provider.
        try: new_llm._allow_provider_switch = False
        except Exception: pass
        with _bridge_lock:  # prevent mid-request mutation
            if _agent and hasattr(_agent, "llm"): _agent.llm = new_llm
            else: _agent = None  # force re-init
        return {"success": True, "provider": provider, "model": model}
    except Exception as exc: return {"success": False, "error": str(exc)}

_GROQ_MODELS_FALLBACK = [
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.1-8b-instant",
    "llama-3.2-90b-vision-preview",
    "qwen/qwen3-32b",
]

@app.get("/llm/groq/models")
def llm_groq_models():
    api_key = os.getenv("GROQ_API_KEY", "")
    if api_key:
        try:
            import urllib.request as _ur
            req = _ur.Request(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp = _ur.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            models = sorted(
                m["id"] for m in data.get("data", [])
                if m.get("id") and not m.get("id", "").startswith("whisper")
            )
            if models:
                return {"models": models, "source": "api"}
        except Exception:
            pass
    return {"models": _GROQ_MODELS_FALLBACK, "source": "fallback"}

@app.get("/llm/ollama/models")
def llm_ollama_models():
    try:
        import urllib.request
        r = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        data = json.loads(r.read())
        return {"models": [m["name"] for m in data.get("models",[])]}
    except Exception:
        return {"models": []}

# ── Sessions ────────────────────────────────────────────────────────────────
@app.get("/sessions")
def sessions_list():
    try:
        from session import list_sessions
        return {"sessions": list_sessions()}
    except Exception as exc: return {"sessions": [], "error": str(exc)}

@app.post("/sessions/save")
def sessions_save(name: str):
    try:
        from session import save_session
        return save_session(name, agent=_agent, bridge=_bridge)
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/sessions/load")
def sessions_load(path: str):
    try:
        from session import load_session
        return load_session(path)
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Memory ──────────────────────────────────────────────────────────────────
@app.get("/memory/recent")
def memory_recent(limit: int = 10):
    try:
        from session_memory import recent_entries
        return {"entries": recent_entries(limit=limit)}
    except Exception as exc: return {"entries": [], "error": str(exc)}

@app.get("/memory/search")
def memory_search(q: str):
    try:
        from session_memory import search_memory
        return {"results": search_memory(q)}
    except Exception as exc: return {"results": [], "error": str(exc)}

@app.get("/memory/goals")
def memory_goals():
    try:
        from session_memory import get_goals
        return {"goals": get_goals()}
    except Exception as exc: return {"goals": [], "error": str(exc)}

class MemoryRecordRequest(BaseModel):
    entry_type: str
    content: Optional[str] = ""
    metadata: Optional[dict] = None

@app.post("/memory/record")
def memory_record(req: MemoryRecordRequest):
    try:
        from session_memory import record_entry
        record_entry(req.entry_type, req.content or "", req.metadata or {})
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Eval ────────────────────────────────────────────────────────────────────
@app.get("/eval/metrics")
def eval_metrics():
    try:
        from evaluation import get_metrics
        return get_metrics()
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/eval/extended")
def eval_extended():
    try:
        from evaluation import get_extended
        return get_extended()
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/eval/reliability")
def eval_rel():
    try:
        from reliability import get_reliability
        return get_reliability()
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/eval/failures")
def eval_failures():
    try:
        from evaluation import get_failures
        return {"failures": get_failures()}
    except Exception as exc: return {"failures": [], "error": str(exc)}

@app.delete("/eval/clear")
def eval_clear():
    try:
        from evaluation import clear_log
        clear_log()
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/eval/sessions")
def eval_sessions(limit: int = 50, offset: int = 0, min_score: float = 0):
    try:
        from evaluation import list_sessions
        return {"sessions": list_sessions(limit=limit, offset=offset, min_score=min_score)}
    except Exception as exc: return {"sessions": [], "error": str(exc)}

@app.get("/eval/sessions/{session_id}")
def eval_session_detail(session_id: str):
    try:
        from evaluation import get_session
        return get_session(session_id)
    except Exception as exc: return {"success": False, "error": str(exc)}

class FeedbackRequest(BaseModel):
    feedback: str
    note: str = ""

@app.post("/eval/feedback/{session_id}")
def eval_feedback(session_id: str, req: FeedbackRequest):
    try:
        from evaluation import record_feedback
        record_feedback(session_id, req.feedback, req.note)
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Accuracy ────────────────────────────────────────────────────────────────
class AccCaptureRequest(BaseModel):
    ref_id: str
    description: str = ""

@app.post("/accuracy/capture")
def acc_capture(req: AccCaptureRequest):
    try:
        from accuracy import capture_reference
        return capture_reference(_get_bridge(), req.ref_id, req.description)
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/accuracy/reference")
def acc_refs():
    try:
        from accuracy import list_references
        return {"references": list_references()}
    except Exception as exc: return {"references": [], "error": str(exc)}

@app.delete("/accuracy/reference/{ref_id}")
def acc_delete(ref_id: str):
    try:
        from accuracy import delete_reference
        delete_reference(ref_id)
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

class AccCompareRequest(BaseModel):
    ref_id: str
    auto_query: bool = False
    use_last_agent_answer: bool = False

@app.post("/accuracy/compare")
def acc_compare(req: AccCompareRequest):
    try:
        from accuracy import compare_to_reference
        return compare_to_reference(_get_bridge(), req.ref_id, agent=_agent if req.auto_query else None)
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/accuracy/comparisons")
def acc_comps():
    try:
        from accuracy import list_comparisons
        return {"comparisons": list_comparisons()}
    except Exception as exc: return {"comparisons": [], "error": str(exc)}

@app.get("/accuracy/summary")
def acc_summary():
    try:
        from accuracy import get_summary
        return get_summary()
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Safety ──────────────────────────────────────────────────────────────────
@app.get("/safety/catalogue")
def safety_cat():
    try:
        from safety_validator import get_catalogue
        return get_catalogue()
    except Exception as exc: return {"catalogue": [], "error": str(exc)}

@app.post("/safety/validate")
def safety_validate(req: dict):
    try:
        from safety_validator import validate
        return validate(req.get("stream_results", {}))
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Benchmark ───────────────────────────────────────────────────────────────
@app.get("/benchmark/tasks")
def bench_tasks():
    try:
        from benchmark_tasks import list_tasks, task_summary
        return {"success": True, "tasks": list_tasks(), "summary": task_summary()}
    except Exception as exc:
        return {"success": False, "tasks": [], "summary": {}, "error": str(exc)}

class BenchRunRequest(BaseModel):
    task_id: str

@app.post("/benchmark/run")
def bench_run(req: BenchRunRequest):
    try:
        from benchmark_tasks import run_task
        return run_task(req.task_id, agent=_get_agent())
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Eval-tab benchmark panel (UI-facing aliases of the above) ───────────────
@app.get("/eval/benchmarks")
def eval_benchmarks():
    try:
        from benchmark_tasks import list_tasks
        return {"benchmarks": list_tasks()}
    except Exception as exc:
        return {"benchmarks": [], "error": str(exc)}

@app.get("/eval/benchmark/results")
def eval_benchmark_results():
    try:
        from benchmark_tasks import get_results
        return get_results()
    except Exception as exc:
        return {"results": [], "total_runs": 0, "pass_rate": None, "error": str(exc)}

class EvalBenchRunRequest(BaseModel):
    benchmark_id: str

@app.post("/eval/benchmark/run")
def eval_benchmark_run(req: EvalBenchRunRequest):
    try:
        from benchmark_tasks import run_task
        return run_task(req.benchmark_id, agent=_get_agent())
    except Exception as exc:
        return {"success": False, "passed": False,
                "benchmark_id": req.benchmark_id, "error": str(exc),
                "notes": str(exc)}

@app.post("/eval/benchmark/run-all")
def eval_benchmark_run_all(req: dict):
    """Run the WHOLE benchmark suite (or a subset of task_ids) against live
    DWSIM and return a measured pass-rate report. Async (slow: each task ~30-90s).
    Poll the returned task_id; the final result has results[] + summary{}."""
    try:
        from task_queue import get_queue
        from benchmark_tasks import run_task, summarize_results, BENCHMARK_TASKS
        ids = req.get("task_ids") or [t.task_id for t in BENCHMARK_TASKS]

        def _worker(report):
            agent = _get_agent()
            results = []
            for i, tid in enumerate(ids, 1):
                try: report(f"task {i}/{len(ids)}", tid)
                except Exception: pass
                results.append(run_task(tid, agent))
            return {"success": True, "results": results,
                    "summary": summarize_results(results)}

        task_id = get_queue().submit_streaming("benchmark_run_all", _worker)
        return {"success": True, "task_id": task_id, "poll_url": f"/tasks/{task_id}",
                "n_tasks": len(ids),
                "message": f"Benchmark suite ({len(ids)} tasks) submitted — "
                           f"slow; poll the task for the measured pass-rate."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── Knowledge ───────────────────────────────────────────────────────────────
@app.get("/knowledge")
def knowledge(q: str, k: int = 5):
    try:
        from knowledge_base import search
        return {"results": search(q, k=k)}
    except Exception as exc: return {"results": [], "error": str(exc)}

@app.get("/knowledge/topics")
def knowledge_topics():
    try:
        from knowledge_base import list_topics
        return {"topics": list_topics()}
    except Exception as exc: return {"topics": [], "error": str(exc)}

# ── Compounds ───────────────────────────────────────────────────────────────
@app.get("/compounds")
def compounds(search: str = ""):
    try:
        from property_db import search_compounds
        return {"compounds": search_compounds(search)}
    except Exception as exc: return {"compounds": [], "error": str(exc)}

@app.get("/compounds/{name}/properties")
def compound_props(name: str):
    try:
        from property_db import get_properties
        return get_properties(name)
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/property-packages")
def prop_packages():
    try:
        from property_db import list_packages
        return {"packages": list_packages()}
    except Exception as exc: return {"packages": [], "error": str(exc)}

# ── Process library + Literature ────────────────────────────────────────────
@app.get("/process-library")
def proc_lib():
    try:
        from process_library import list_processes
        return {"processes": list_processes()}
    except Exception as exc: return {"processes": [], "error": str(exc)}

@app.get("/process-library/{key}")
def proc_lib_detail(key: str):
    try:
        from process_library import get_process
        return get_process(key)
    except Exception as exc: return {"success": False, "error": str(exc)}

class LiteratureCompareRequest(BaseModel):
    process: str
    tolerance_pct: float = 5.0
    include_kpis: bool = True

@app.post("/literature/compare")
def lit_compare(req: LiteratureCompareRequest):
    try:
        from process_library import compare_to_literature
        return compare_to_literature(_get_bridge(), req.process, req.tolerance_pct, req.include_kpis)
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── Intent ──────────────────────────────────────────────────────────────────
class IntentDeclareRequest(BaseModel):
    feed_streams: List[str] = []
    product_streams: List[str] = []
    note: str = ""
    targets: List[Dict[str, Any]] = []

_active_intent: Optional[dict] = None

@app.post("/intent/declare")
def intent_declare(req: IntentDeclareRequest):
    global _active_intent
    try:
        from intent import parse_intent
        intent = parse_intent(req.dict(), bridge=_bridge)
        _active_intent = req.dict()
        if _agent and hasattr(_agent, "_active_intent"):
            _agent._active_intent = intent
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/intent/status")
def intent_status():
    return {"active": _active_intent is not None, "intent": _active_intent}

@app.post("/intent/verify")
def intent_verify():
    if _active_intent is None: return {"active": False}
    try:
        from intent import parse_intent, verify_intent
        intent = parse_intent(_active_intent, bridge=_get_bridge())
        result = verify_intent(intent, _get_bridge())
        return result if isinstance(result, dict) else asdict(result)
    except Exception as exc: return {"active": True, "passed": False, "error": str(exc)}

@app.delete("/intent")
def intent_clear():
    global _active_intent
    _active_intent = None
    if _agent and hasattr(_agent, "_active_intent"): _agent._active_intent = None
    return {"success": True}

# ── Hydrogen Case Study (Ullah 2025) ────────────────────────────────────────
class HydrogenBuildRequest(BaseModel):
    template_variant: str = "biogas_smr_h2_gibbs"
    mock: bool = False

class HydrogenRunRequest(BaseModel):
    mode: str = "quick"
    mock: bool = False
    template_variant: str = "biogas_smr_h2_gibbs"

class HydrogenSensitivityRequest(BaseModel):
    parameter: str
    values: List[float]
    mock: bool = False

_h2_study: Optional[Any] = None

def _get_h2_study(mock: bool = False):
    global _h2_study
    try:
        from hydrogen_case_study import HydrogenCaseStudy
        if _h2_study is None: _h2_study = HydrogenCaseStudy()
        if mock: _h2_study._mock_mode = True
        return _h2_study
    except Exception as exc:
        raise HTTPException(500, f"hydrogen_case_study unavailable: {exc}")

@app.post("/hydrogen/build")
def h2_build(req: HydrogenBuildRequest):
    try:
        study = _get_h2_study(req.mock)
        with _bridge_lock:
            ok = study.build(variant=req.template_variant)
        if not ok and study._mock_mode: ok = True
        return {"success": ok, "template": req.template_variant,
                "template_path": study._template_path, "mock_mode": study._mock_mode}
    except HTTPException: raise
    except Exception as exc: return {"success": False, "error": str(exc), "mock_mode": False}

@app.post("/hydrogen/run")
def h2_run(req: HydrogenRunRequest):
    try:
        study = _get_h2_study(req.mock)
        with _bridge_lock:
            if not study._mock_mode and study._template_path is None:
                study.build(variant=req.template_variant)
            base = study.run_base_case()
            opt  = study.run_optimal_case()
            report = study.generate_report(base, opt, {}, mode=req.mode)
        return {"success": True, "report": report}
    except HTTPException: raise
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.post("/hydrogen/sensitivity")
def h2_sens(req: HydrogenSensitivityRequest):
    valid = {"temperature","pressure","biogas_flow","steam_flow"}
    if req.parameter not in valid:
        raise HTTPException(422, f"parameter must be one of {sorted(valid)}")
    if not req.values: raise HTTPException(422, "values cannot be empty")
    try:
        study = _get_h2_study(req.mock)
        with _bridge_lock:
            if not study._mock_mode and study._template_path is None: study.build()
            pts = study.run_sensitivity(req.parameter, req.values)
        return {"success": True, "parameter": req.parameter,
                "n_points": len(pts), "results": [asdict(p) for p in pts]}
    except HTTPException: raise
    except Exception as exc: return {"success": False, "error": str(exc)}

@app.get("/hydrogen/report")
def h2_report():
    rpath = os.path.join(_BACKEND_DIR, "hydrogen_report.json")
    if not os.path.exists(rpath):
        raise HTTPException(404, "No report — run POST /hydrogen/run first.")
    try:
        with open(rpath, "r", encoding="utf-8") as f:
            return {"success": True, "report": json.load(f)}
    except Exception as exc: raise HTTPException(500, str(exc))

# ── Industrial features: pre-flight, tear streams, HEN, diagnostics ─────────

@app.get("/flowsheet/preflight")
def fs_preflight():
    """Pre-solve graph validation. Catches ~50% of convergence issues without a solve."""
    try:
        from industrial_features import preflight_validate
        with _bridge_lock:
            b = _get_bridge()
            objs = b.list_objects() if hasattr(b, "list_objects") else {}
            conns = []
            if hasattr(b, "list_connections"):
                conns = b.list_connections() or []
            elif hasattr(b, "get_connections"):
                conns = b.get_connections() or []
            compounds = []
            if hasattr(b, "list_compounds"):
                try: compounds = list((b.list_compounds() or {}).get("compounds") or [])
                except Exception: pass
            pp = ""
            if hasattr(b, "get_property_package"):
                try: pp = (b.get_property_package() or {}).get("property_package", "")
                except Exception: pass
        all_objs = [{"tag": s.get("tag"), "category": "MaterialStream"} for s in objs.get("streams", [])] + \
                   [{"tag": u.get("tag"), "type": u.get("type"), "category": "UnitOperation"} for u in objs.get("unit_ops", [])]
        return preflight_validate(all_objs, conns, compounds, pp)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/flowsheet/tear-streams")
def fs_tear_streams():
    """Auto-detect minimum tear stream set via Pho-Lapidus."""
    try:
        from industrial_features import detect_tear_streams
        with _bridge_lock:
            b = _get_bridge()
            objs = b.list_objects() if hasattr(b, "list_objects") else {}
            conns = b.list_connections() if hasattr(b, "list_connections") else []
        nodes = [s.get("tag") for s in objs.get("streams", []) if s.get("tag")] + \
                [u.get("tag") for u in objs.get("unit_ops", []) if u.get("tag")]
        edges = [(c.get("from"), c.get("to")) for c in conns
                 if c.get("from") and c.get("to")]
        return detect_tear_streams(nodes, edges)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

class HENRequest(BaseModel):
    delta_t_min_C: float = 10.0
    use_pinch_result: bool = True

@app.post("/flowsheet/hen-synthesis")
def fs_hen(req: HENRequest):
    """Synthesize a heat-exchanger network via Linnhoff-Hindmarsh."""
    try:
        from industrial_features import synthesize_hen
        # Get pinch result first to get stream classification
        pinch = fs_pinch(req.delta_t_min_C)
        if not pinch.get("success"):
            return {"success": False, "error": "Pinch analysis prerequisite failed", "pinch_error": pinch.get("error")}
        streams = pinch.get("streams", [])
        hot  = [{"tag": s["tag"], "t_supply_C": s["t_supply"], "t_target_C": s["t_target"], "heat_kw": s["heat_kw"]}
                for s in streams if s["type"] == "hot"]
        cold = [{"tag": s["tag"], "t_supply_C": s["t_supply"], "t_target_C": s["t_target"], "heat_kw": s["heat_kw"]}
                for s in streams if s["type"] == "cold"]
        result = synthesize_hen(hot, cold, pinch.get("pinch_temp_C"), req.delta_t_min_C)
        result["pinch_temp_C"] = pinch.get("pinch_temp_C")
        result["q_hmin_kw"]    = pinch.get("q_hmin_kw")
        result["q_cmin_kw"]    = pinch.get("q_cmin_kw")
        return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/flowsheet/diagnose")
def fs_diagnose():
    """Root-cause analysis for failed convergence."""
    try:
        from industrial_features import diagnose_convergence
        with _bridge_lock:
            b = _get_bridge()
            conv = b.check_convergence() if hasattr(b, "check_convergence") else {}
            states: Dict[str, Dict] = {}
            for tag_entry in (conv.get("not_converged", []) or []):
                tag = tag_entry.get("tag") if isinstance(tag_entry, dict) else tag_entry
                if tag and hasattr(b, "get_stream_properties"):
                    try:
                        r = b.get_stream_properties(tag)
                        if r.get("success"):
                            states[tag] = r.get("properties", {})
                    except Exception:
                        pass
        return diagnose_convergence(conv, states)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── Process templates library ───────────────────────────────────────────────

@app.get("/process-templates")
def process_templates_list(category: str = "", complexity: str = ""):
    """List the 10+ industrial reference designs."""
    try:
        from process_templates import list_templates
        return list_templates(category, complexity)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/process-templates/{template_id}")
def process_template_get(template_id: str):
    """Get the full template spec by id."""
    try:
        from process_templates import get_template
        return get_template(template_id)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


class TemplateInstantiateRequest(BaseModel):
    template_id: str
    overrides: Optional[Dict[str, Any]] = None
    solve: bool = False


@app.post("/process-templates/instantiate")
def process_template_instantiate(req: TemplateInstantiateRequest):
    """Deterministically instantiate a template (build the DWSIM flowsheet
    step-by-step). Bypasses LLM tool-calling for known templates so 20+
    unit-op designs reliably reach a solvable state."""
    try:
        from process_templates import instantiate_template
        with _bridge_lock:
            b = _get_bridge()
            return instantiate_template(
                template_id=req.template_id,
                bridge=b,
                overrides=req.overrides or {},
                solve=bool(req.solve),
            )
    except Exception as exc:
        return {"success": False, "error": str(exc),
                "error_code": "TEMPLATE_INSTANTIATE_FAILED"}


class BuildPlanRequest(BaseModel):
    plan: Dict[str, Any]
    solve: bool = False


@app.post("/flowsheet/build-plan")
def flowsheet_build_plan(req: BuildPlanRequest):
    """Deterministically execute a complete flowsheet build plan.
    Plan shape: {compounds[], property_package, streams[], unit_ops[],
    connections[]}. See flowsheet_executor.execute_build_plan."""
    try:
        from flowsheet_executor import execute_build_plan
        with _bridge_lock:
            b = _get_bridge()
            return execute_build_plan(req.plan or {}, b, solve=bool(req.solve))
    except Exception as exc:
        return {"success": False, "error_code": "BUILD_PLAN_FAILED",
                "error": str(exc)}

# ── PFD generation ──────────────────────────────────────────────────────────

@app.get("/flowsheet/pfd")
def fs_pfd(width: int = 0, height: int = 0):
    """Generate a Process Flow Diagram as SVG."""
    try:
        from pfd_generator import generate_pfd_svg
        with _bridge_lock:
            b = _get_bridge()
            objs = b.list_objects() if hasattr(b, "list_objects") else {}
            conns = b.list_connections() if hasattr(b, "list_connections") else []
        all_objs = (
            [{"tag": s.get("tag"), "category": "MaterialStream"} for s in objs.get("streams", [])] +
            [{"tag": u.get("tag"), "type": u.get("type"), "category": "UnitOperation"} for u in objs.get("unit_ops", [])]
        )
        return generate_pfd_svg(
            all_objs, conns,
            width=width if width > 0 else None,
            height=height if height > 0 else None,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

# ── Reaction kinetics database ──────────────────────────────────────────────

@app.get("/kinetics")
def kinetics_list(catalyst: str = "", reactant: str = ""):
    """List curated reaction kinetics."""
    try:
        from kinetics_db import list_reactions
        return list_reactions(catalyst, reactant)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/kinetics/{reaction_id}")
def kinetics_get(reaction_id: str):
    """Get full kinetic spec by id."""
    try:
        from kinetics_db import get_reaction
        return get_reaction(reaction_id)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

class KineticsSuggestRequest(BaseModel):
    reactants: List[str]
    T_K: float = 0
    P_bar: float = 0

@app.post("/kinetics/suggest")
def kinetics_suggest(req: KineticsSuggestRequest):
    """Suggest matching reactions for given reactants + conditions."""
    try:
        from kinetics_db import suggest_kinetics
        return suggest_kinetics(req.reactants, req.T_K, req.P_bar)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/kinetics/{reaction_id}/rate")
def kinetics_rate(reaction_id: str, T_K: float):
    """Evaluate Arrhenius rate constant at temperature T_K."""
    try:
        from kinetics_db import evaluate_rate_arrhenius
        return evaluate_rate_arrhenius(reaction_id, T_K)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── CAPE-OPEN integration ───────────────────────────────────────────────────

@app.get("/capeopen/discover")
def capeopen_discover(category: str = ""):
    """
    Scan the Windows registry for installed CAPE-OPEN components.
    category: optional filter — UnitOperation, PropertyPackage, ReactionPackage,
              EquilibriumSolver, PropertyPackageManager.
    """
    try:
        from cape_open_integration import discover_cape_open_components
        return discover_cape_open_components(category)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

class CapeOpenAddRequest(BaseModel):
    tag: str
    clsid_or_progid: str

@app.post("/capeopen/add")
def capeopen_add(req: CapeOpenAddRequest):
    """Add a CAPE-OPEN unit op to the flowsheet by CLSID or ProgID."""
    try:
        from cape_open_integration import add_cape_open_unit_to_flowsheet
        with _bridge_lock:
            b = _get_bridge()
            return add_cape_open_unit_to_flowsheet(b, req.tag, req.clsid_or_progid)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/capeopen/{tag}/parameters")
def capeopen_params(tag: str):
    """List parameters of a CO unit op."""
    try:
        from cape_open_integration import list_cape_open_parameters
        with _bridge_lock:
            b = _get_bridge()
            return list_cape_open_parameters(b, tag)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

@app.get("/capeopen/{tag}/ports")
def capeopen_ports(tag: str):
    """List ports (inlets/outlets) of a CO unit op."""
    try:
        from cape_open_integration import list_cape_open_ports
        with _bridge_lock:
            b = _get_bridge()
            return list_cape_open_ports(b, tag)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

class CapeOpenSetParamRequest(BaseModel):
    tag: str
    parameter_name: str
    value: Any

@app.post("/capeopen/set_parameter")
def capeopen_set_param(req: CapeOpenSetParamRequest):
    """Set a parameter on a CO unit op."""
    try:
        from cape_open_integration import set_cape_open_parameter
        with _bridge_lock:
            b = _get_bridge()
            return set_cape_open_parameter(b, req.tag, req.parameter_name, req.value)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ── Admin ───────────────────────────────────────────────────────────────────
@app.post("/admin/reload-env")
def reload_env():
    try:
        load_dotenv(override=True)
        return {"success": True}
    except Exception as exc: return {"success": False, "error": str(exc)}

# ── WebSocket for file watcher ──────────────────────────────────────────────
@app.websocket("/ws/flowsheets")
async def ws_flowsheets(ws: WebSocket):
    await ws.accept()
    with _ws_lock:
        _ws_clients.append(ws)
    try:
        while True:
            msg = await ws.receive_text()
            if msg == "ping": await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        with _ws_lock:
            if ws in _ws_clients: _ws_clients.remove(ws)


# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    print(f"[DWSIM API v2] Starting on http://localhost:{port}")
    print(f"[DWSIM API v2] Docs at  http://localhost:{port}/docs")
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
