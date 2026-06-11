"""
error_utils.py — Structured error formatting for API responses.

Every endpoint should wrap exceptions through format_error() so the response
shape is consistent. UI and clients can rely on error_code for programmatic
handling, request_id for log correlation, and module for source isolation.

Schema:
{
    "success": False,
    "error": "<human-readable summary>",
    "error_code": "MODULE_REASON",          # e.g. BRIDGE_NOT_READY
    "module": "api.flowsheet",
    "request_id": "8b3f...",                # uuid4
    "exception_type": "ValueError",
    "trace_brief": "...",                    # 5-line tail of traceback
}
"""

from __future__ import annotations
import logging
import traceback
import uuid
from typing import Any, Dict, Optional

_log = logging.getLogger("dwsim_api")


def make_request_id() -> str:
    return uuid.uuid4().hex[:12]


def format_error(
    exc: BaseException,
    *,
    module: str = "api",
    error_code: str = "INTERNAL_ERROR",
    user_message: Optional[str] = None,
    request_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a structured error dict and log it with the correlation id."""
    rid = request_id or make_request_id()
    msg = user_message or str(exc) or exc.__class__.__name__
    tb_full = traceback.format_exception(type(exc), exc, exc.__traceback__)
    tb_brief = "".join(tb_full[-5:])

    out: Dict[str, Any] = {
        "success": False,
        "error": msg,
        "error_code": error_code,
        "module": module,
        "request_id": rid,
        "exception_type": exc.__class__.__name__,
        "trace_brief": tb_brief.strip(),
    }
    if extra:
        out.update(extra)

    try:
        _log.error("[%s] %s.%s: %s\n%s", rid, module, error_code, msg, tb_brief)
    except Exception:
        pass
    return out


def classify_exception(exc: BaseException) -> str:
    """Return a best-guess error_code from the exception type & message."""
    name = exc.__class__.__name__
    msg = str(exc).lower()
    if name == "TimeoutError" or "timed out" in msg or "timeout" in msg:
        return "TIMEOUT"
    if name == "ConnectionError" or "connection" in msg or "refused" in msg:
        return "CONNECTION_FAILED"
    if name == "FileNotFoundError" or "no such file" in msg or "not found" in msg:
        return "NOT_FOUND"
    if name == "PermissionError" or "permission denied" in msg or "access" in msg:
        return "PERMISSION_DENIED"
    if name == "ValueError":
        return "INVALID_INPUT"
    if name == "KeyError":
        return "MISSING_KEY"
    if name == "TypeError":
        return "TYPE_MISMATCH"
    if name == "NotImplementedError":
        return "NOT_IMPLEMENTED"
    if "convergence" in msg or "did not converge" in msg:
        return "CONVERGENCE_FAILED"
    if "bridge" in msg and ("not ready" in msg or "not initialized" in msg):
        return "BRIDGE_NOT_READY"
    if "license" in msg:
        return "LICENSE_ERROR"
    return "INTERNAL_ERROR"
