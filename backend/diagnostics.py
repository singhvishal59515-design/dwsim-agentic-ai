"""
diagnostics.py
──────────────
Reports runtime health: DWSIM DLL versions, LLM provider reachability,
FOSSEE sample scan, dependency check. Used by /diagnostics endpoint and
the status bar in the UI. No side effects — read-only probes.
"""

from __future__ import annotations

import glob
import importlib
import os
import time
import traceback
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Python dependency probe
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_PKGS: List[str] = [
    "clr",           # pythonnet
    "fastapi",
    "uvicorn",
    "pydantic",
    "aiofiles",
]

_OPTIONAL_PKGS: List[str] = [
    "groq",
    "openai",
    "anthropic",
    "google.generativeai",
    "matplotlib",
    "reportlab",
    "chromadb",
]


def check_python_deps() -> Dict[str, Any]:
    results: Dict[str, Any] = {"required": {}, "optional": {}}
    missing_required: List[str] = []
    for name in _REQUIRED_PKGS:
        try:
            importlib.import_module(name)
            results["required"][name] = True
        except Exception:
            results["required"][name] = False
            missing_required.append(name)
    for name in _OPTIONAL_PKGS:
        try:
            importlib.import_module(name)
            results["optional"][name] = True
        except Exception:
            results["optional"][name] = False
    results["all_required_ok"] = not missing_required
    results["missing_required"] = missing_required
    return results


# ─────────────────────────────────────────────────────────────────────────────
# DWSIM install probe
# ─────────────────────────────────────────────────────────────────────────────

def probe_dwsim(bridge) -> Dict[str, Any]:
    """Report on the DWSIM runtime: install path, DLL versions, Automation class."""
    info: Dict[str, Any] = {
        "ready": bool(getattr(bridge, "_ready", False)),
        "dll_folder": getattr(bridge, "dll_folder", None),
        "automation_class": None,
        "dll_versions": {},
        "issues": [],
    }

    if not info["dll_folder"]:
        info["issues"].append("DWSIM DLL folder not resolved")
        return info

    # DLL versions via System.Diagnostics.FileVersionInfo
    key_dlls = ("DWSIM.Automation.dll", "DWSIM.Interfaces.dll",
                "DWSIM.Thermodynamics.dll", "DWSIM.UnitOperations.dll")
    for name in key_dlls:
        path = os.path.join(info["dll_folder"], name)
        if not os.path.isfile(path):
            info["dll_versions"][name] = None
            info["issues"].append(f"Missing DLL: {name}")
            continue
        try:
            import System
            from System.Diagnostics import FileVersionInfo
            fvi = FileVersionInfo.GetVersionInfo(path)
            info["dll_versions"][name] = str(fvi.FileVersion or fvi.ProductVersion)
        except Exception as exc:
            info["dll_versions"][name] = f"? ({exc})"

    # Which Automation class is live
    mgr = getattr(bridge, "_mgr", None)
    if mgr is not None:
        try:
            info["automation_class"] = mgr.GetType().FullName
        except Exception:
            info["automation_class"] = "<unknown>"

    # Surface known-problematic PR/LK DLL absence (caused the FOSSEE 3-fail)
    pr_lk_hit = any("PR" in k and "LK" in k for k in info["dll_versions"])
    if not pr_lk_hit:
        # Not strictly an error — DWSIM carries PR/LK as an internal type, not
        # a separate DLL. The check here documents the known runtime quirk.
        info["notes"] = [
            "PR/LK property-package errors when loading some FOSSEE samples "
            "are a known runtime-init quirk; open the file once in DWSIM "
            "desktop to prime the PP cache."
        ]

    return info


# ─────────────────────────────────────────────────────────────────────────────
# FOSSEE sample scan
# ─────────────────────────────────────────────────────────────────────────────

def scan_fossee(root: Optional[str] = None,
                 max_files: int = 30) -> Dict[str, Any]:
    """Enumerate bundled FOSSEE samples without loading them."""
    if root is None:
        root = os.path.expanduser(r"~\AppData\Local\DWSIM\FOSSEE")
    if not os.path.isdir(root):
        return {"root": root, "exists": False, "samples": []}
    hits = sorted(glob.glob(os.path.join(root, "*", "*.dwxmz")))[:max_files]
    samples = []
    for p in hits:
        try:
            sz = os.path.getsize(p)
            mt = os.path.getmtime(p)
        except OSError:
            continue
        samples.append({
            "name": os.path.basename(p),
            "path": p,
            "size_bytes": sz,
            "mtime": mt,
        })
    return {"root": root, "exists": True, "sample_count": len(samples),
            "samples": samples}


# ─────────────────────────────────────────────────────────────────────────────
# LLM provider reachability
# ─────────────────────────────────────────────────────────────────────────────

def probe_llm_providers(timeout_s: float = 4.0) -> Dict[str, Any]:
    """Cheap reachability probe for all four providers.

    Does NOT burn tokens — uses model-list or health endpoints where possible.
    """
    import os
    import urllib.request
    import urllib.error

    def _has(env_var: str) -> bool:
        return bool(os.environ.get(env_var, "").strip())

    def _probe(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                ok = 200 <= resp.status < 300
                return {"reachable": ok, "status": resp.status,
                        "latency_ms": round((time.monotonic() - t0) * 1000, 1)}
        except urllib.error.HTTPError as e:
            # 200-299 = usable. 401/403 = key bad/quota — NOT usable for fallback.
            # 5xx = server error but endpoint alive.
            usable = 200 <= e.code < 300
            reachable = e.code >= 500  # only mark reachable if server-side error
            return {"reachable": reachable, "usable": usable, "status": e.code,
                    "error": f"HTTP {e.code}",
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1)}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)[:120],
                    "latency_ms": round((time.monotonic() - t0) * 1000, 1)}

    results: Dict[str, Any] = {}

    # Groq
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if _has("GROQ_API_KEY"):
        results["groq"] = _probe(
            "https://api.groq.com/openai/v1/models",
            {"Authorization": f"Bearer {key}"})
        results["groq"]["key_configured"] = True
    else:
        results["groq"] = {"reachable": False, "key_configured": False,
                           "error": "GROQ_API_KEY not set"}

    # OpenAI
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if _has("OPENAI_API_KEY"):
        results["openai"] = _probe(
            "https://api.openai.com/v1/models",
            {"Authorization": f"Bearer {key}"})
        results["openai"]["key_configured"] = True
    else:
        results["openai"] = {"reachable": False, "key_configured": False,
                             "error": "OPENAI_API_KEY not set"}

    # Anthropic — the /v1/messages endpoint requires POST; use /v1/models list.
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if _has("ANTHROPIC_API_KEY"):
        results["anthropic"] = _probe(
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"})
        results["anthropic"]["key_configured"] = True
    else:
        results["anthropic"] = {"reachable": False, "key_configured": False,
                                "error": "ANTHROPIC_API_KEY not set"}

    # Gemini
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if _has("GEMINI_API_KEY"):
        results["gemini"] = _probe(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            {})
        results["gemini"]["key_configured"] = True
    else:
        results["gemini"] = {"reachable": False, "key_configured": False,
                             "error": "GEMINI_API_KEY not set"}

    # Ollama — localhost only
    results["ollama"] = _probe("http://localhost:11434/api/tags", {})
    results["ollama"]["key_configured"] = None  # N/A

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Ordered fallback chain — uses reachability data to pick primary + alternates
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDER_PREFERENCE = ("groq", "openai", "anthropic", "gemini", "ollama")


def recommended_provider_order(probe: Dict[str, Any]) -> List[str]:
    """Return providers ordered by: reachable+key → reachable → configured → unusable."""
    def _score(p: str) -> int:
        r = probe.get(p) or {}
        if r.get("reachable") and r.get("key_configured"):
            return 0
        if r.get("reachable"):
            return 1
        if r.get("key_configured"):
            return 2
        return 3
    return sorted(_PROVIDER_PREFERENCE, key=_score)


# ─────────────────────────────────────────────────────────────────────────────
# All-in-one
# ─────────────────────────────────────────────────────────────────────────────

def full_diagnostics(bridge, *, skip_providers: bool = False) -> Dict[str, Any]:
    t0 = time.monotonic()
    report: Dict[str, Any] = {
        "timestamp": time.time(),
        "python_deps": check_python_deps(),
        "dwsim": probe_dwsim(bridge),
        "fossee": scan_fossee(),
    }
    if not skip_providers:
        probe = probe_llm_providers()
        report["llm_providers"] = probe
        report["recommended_provider_order"] = recommended_provider_order(probe)

    # Roll up to a single "healthy" verdict
    deps_ok = report["python_deps"]["all_required_ok"]
    dwsim_ok = bool(report["dwsim"]["ready"])
    provider_ok = True
    if "llm_providers" in report:
        provider_ok = any(v.get("reachable") and v.get("key_configured")
                          for v in report["llm_providers"].values())
    report["healthy"] = bool(deps_ok and dwsim_ok and provider_ok)
    report["elapsed_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return report
