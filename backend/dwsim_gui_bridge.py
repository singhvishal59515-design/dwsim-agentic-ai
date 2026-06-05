"""
dwsim_gui_bridge.py — Push AI-side changes into a running DWSIM GUI.

DWSIM's desktop app has no file watcher of its own, so after the AI saves a
flowsheet the user must normally File→Open in DWSIM to see the change.

This module provides a *best-effort* push: it enumerates top-level windows,
detects any DWSIM main windows, optionally asks them to close, then re-launches
DWSIM with the saved flowsheet path. Uses only the Windows stdlib (ctypes).

Limitations:
  - If DWSIM shows an unsaved-changes prompt on close, the user must click through.
  - Multi-document state inside the running DWSIM (other open flowsheets) is lost
    on close. Only use close_first=True when you know the user expects a reload.
  - No-op on non-Windows platforms.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import List, Tuple

_log = logging.getLogger("dwsim_gui_bridge")

_IS_WINDOWS = sys.platform.startswith("win")

# Win32 constants
_WM_CLOSE = 0x0010
_GW_OWNER = 4


def _find_dwsim_exe() -> str:
    """Best-effort lookup for DWSIM.exe.

    Tries (in order):
      1. The Windows registry App Paths entry for DWSIM.exe
      2. The handler registered for the .dwxmz file association
      3. Common hard-coded install paths
    Returns '' if not found.
    """
    if _IS_WINDOWS:
        path = _registry_dwsim_exe()
        if path and os.path.exists(path):
            return path

    candidates = [
        r"C:\Program Files\DWSIM\DWSIM.exe",
        r"C:\Program Files\DWSIM8\DWSIM.exe",
        r"C:\Program Files (x86)\DWSIM\DWSIM.exe",
        os.path.expanduser(r"~\AppData\Local\DWSIM\DWSIM.exe"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def _registry_dwsim_exe() -> str:
    """Probe Windows registry for DWSIM.exe path. Returns '' on any failure."""
    try:
        import winreg
    except ImportError:
        return ""
    # App Paths entry is the canonical location.
    paths_to_try = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\DWSIM.exe"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\DWSIM.exe"),
    ]
    for hive, key in paths_to_try:
        try:
            with winreg.OpenKey(hive, key) as k:
                val, _ = winreg.QueryValueEx(k, "")
                if val and os.path.exists(val):
                    return val
        except OSError:
            continue
    # Fallback: follow the .dwxmz file-type handler.
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r".dwxmz") as k:
            progid, _ = winreg.QueryValueEx(k, "")
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                            rf"{progid}\shell\open\command") as k:
            cmd, _ = winreg.QueryValueEx(k, "")
        # cmd looks like: "C:\...\DWSIM.exe" "%1"
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split()[0]
        if os.path.exists(exe):
            return exe
    except (OSError, IndexError, KeyError):
        pass
    return ""


def _enum_dwsim_windows() -> List[Tuple[int, str]]:
    """Return [(hwnd, title), ...] for every visible top-level window
    whose title contains 'DWSIM'."""
    if not _IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    GetWindowTextLengthW = user32.GetWindowTextLengthW
    GetWindowTextW = user32.GetWindowTextW
    IsWindowVisible = user32.IsWindowVisible
    GetWindow = user32.GetWindow

    found: List[Tuple[int, str]] = []

    def _cb(hwnd, _lparam):
        try:
            if not IsWindowVisible(hwnd):
                return True
            # Skip tool/owned windows — we only want top-level main windows.
            if GetWindow(hwnd, _GW_OWNER):
                return True
            n = GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
            if "DWSIM" in title.upper():
                found.append((int(hwnd), title))
        except Exception:
            pass
        return True

    EnumWindows(EnumWindowsProc(_cb), 0)
    return found


def _close_window(hwnd: int) -> None:
    if not _IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    except Exception as exc:
        _log.warning("PostMessage WM_CLOSE failed hwnd=%s: %s", hwnd, exc)


_SAVE_PROMPT_KEYWORDS = ("save changes", "unsaved", "do you want to save")


def _detect_save_prompt() -> List[Tuple[int, str]]:
    """Return any DWSIM modal that looks like a 'save changes?' prompt."""
    return [(h, t) for h, t in _enum_dwsim_windows()
            if any(kw in t.lower() for kw in _SAVE_PROMPT_KEYWORDS)]


def _wait_for_close(titles: List[str], timeout: float = 5.0) -> dict:
    """Block until no DWSIM window with these titles remains, or timeout.
    Returns a structured result flagging any lingering save-prompt modal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        remaining = _enum_dwsim_windows()
        remaining_titles = [w[1] for w in remaining]
        if not any(t in remaining_titles for t in titles):
            return {"closed": True, "remaining": []}
        time.sleep(0.2)
    remaining = _enum_dwsim_windows()
    return {
        "closed": False,
        "remaining": [t for _, t in remaining],
        "save_prompt": [t for _, t in _detect_save_prompt()],
    }


def detect_state() -> dict:
    """Return info about any running DWSIM GUI without changing anything."""
    if not _IS_WINDOWS:
        return {"available": False, "reason": "not Windows"}
    windows = _enum_dwsim_windows()
    return {
        "available": True,
        "exe_path": _find_dwsim_exe(),
        "windows": [{"hwnd": h, "title": t} for h, t in windows],
        "window_count": len(windows),
    }


def push_to_gui(path: str,
                close_first: bool = False,
                wait_close_s: float = 5.0) -> dict:
    """Best-effort: make the running DWSIM GUI show the file at `path`.

    close_first=True  →  post WM_CLOSE to every DWSIM window, wait, then launch.
                          Destructive to any other open flowsheets in DWSIM.
    close_first=False →  just launch DWSIM with the file via the shell
                          (os.startfile). DWSIM may refuse if the file is
                          already open in another instance.
    """
    if not _IS_WINDOWS:
        return {"success": False, "error": "DWSIM GUI push is Windows-only"}
    if not path or not os.path.exists(path):
        return {"success": False, "error": f"File not found: {path}"}

    action: List[str] = []
    extra: dict = {}
    windows = _enum_dwsim_windows()
    if close_first and windows:
        titles = [t for _, t in windows]
        for h, _t in windows:
            _close_window(h)
        status = _wait_for_close(titles, timeout=wait_close_s)
        if status["closed"]:
            action.append("closed")
        else:
            action.append("close_timeout")
            if status.get("save_prompt"):
                action.append("save_prompt_pending")
                extra["save_prompt_titles"] = status["save_prompt"]
                extra["hint"] = ("DWSIM is waiting on a 'save changes?' "
                                 "dialog — click through it in the DWSIM "
                                 "window, then retry.")
                _log.warning("DWSIM save-prompt blocking close: %s",
                             status["save_prompt"])
                return {"success": False, "code": "SAVE_PROMPT_PENDING",
                        "action": action, **extra}

    exe = _find_dwsim_exe()
    try:
        if exe:
            subprocess.Popen([exe, path], close_fds=True)
            action.append("launched_via_exe")
        else:
            os.startfile(path)  # type: ignore[attr-defined]
            action.append("launched_via_shell")
    except Exception as exc:
        return {"success": False, "error": str(exc),
                "action": action}
    return {
        "success": True,
        "path": path,
        "action": action,
        "previous_windows": len(windows),
    }
