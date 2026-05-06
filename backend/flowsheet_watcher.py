"""
flowsheet_watcher.py — File system watcher for DWSIM flowsheet files
────────────────────────────────────────────────────────────────────
Watches Documents, Desktop, Downloads for .dwxmz/.dwxm files.
Provides:
  - FlowsheetScanner: one-shot scan returning files with metadata
  - FlowsheetWatcher: background thread that detects new/modified files
  - Callback-based notification for WebSocket push to frontend
"""

import logging
import os
import time
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

_log = logging.getLogger("flowsheet_watcher")


# ─────────────────────────────────────────────────────────────────────────────
# Scanner: one-shot directory scan
# ─────────────────────────────────────────────────────────────────────────────

DWSIM_EXTENSIONS = (".dwxmz", ".dwxm")

DEFAULT_WATCH_DIRS = [
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Downloads"),
    os.path.expanduser(r"~\AppData\Local\DWSIM"),
]


def _file_meta(path: str) -> Dict[str, Any]:
    """Return metadata dict for a single flowsheet file."""
    try:
        stat = os.stat(path)
        return {
            "path": path,
            "name": os.path.basename(path),
            "size_bytes": stat.st_size,
            "size_display": _human_size(stat.st_size),
            "modified_ts": stat.st_mtime,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "directory": os.path.dirname(path),
        }
    except OSError:
        return {"path": path, "name": os.path.basename(path), "error": "cannot stat"}


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024  # type: ignore
    return f"{nbytes:.1f} TB"


class FlowsheetScanner:
    """Scans directories for DWSIM flowsheet files with metadata."""

    def __init__(self, watch_dirs: Optional[List[str]] = None):
        self.watch_dirs = watch_dirs or DEFAULT_WATCH_DIRS

    def scan(self, max_files: int = 100) -> List[Dict[str, Any]]:
        """Scan all watch directories, return files sorted by modified time (newest first)."""
        found: List[Dict[str, Any]] = []
        seen: Set[str] = set()

        for root_dir in self.watch_dirs:
            if not root_dir or not os.path.isdir(root_dir):
                continue
            try:
                for dirpath, _, filenames in os.walk(root_dir):
                    for f in filenames:
                        if f.lower().endswith(DWSIM_EXTENSIONS):
                            full = os.path.normpath(os.path.join(dirpath, f))
                            if full not in seen:
                                seen.add(full)
                                found.append(_file_meta(full))
                    if len(found) >= max_files:
                        break
            except PermissionError:
                continue

        # Sort by modified time, newest first
        found.sort(key=lambda x: x.get("modified_ts", 0), reverse=True)
        return found[:max_files]

    def scan_single(self, path: str) -> Optional[Dict[str, Any]]:
        """Get metadata for a single file."""
        if os.path.isfile(path) and path.lower().endswith(DWSIM_EXTENSIONS):
            return _file_meta(path)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Watcher: background thread polling for changes
# ─────────────────────────────────────────────────────────────────────────────

class FlowsheetWatcher:
    """
    Background thread that polls watched directories for new/modified .dwxmz files.
    Calls on_change(event_type, file_meta) when a change is detected.

    Uses polling (not watchdog) to avoid extra dependencies.
    Poll interval default: 3 seconds.
    """

    def __init__(
        self,
        watch_dirs: Optional[List[str]] = None,
        poll_interval: float = 3.0,
        on_change: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self.scanner = FlowsheetScanner(watch_dirs)
        self.poll_interval = poll_interval
        self.on_change = on_change

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._known_files: Dict[str, float] = {}  # path → mtime
        self._error_count = 0
        self._last_error: Optional[str] = None
        self._consecutive_errors = 0

    def start(self) -> None:
        """Start the background watcher thread."""
        if self._thread and self._thread.is_alive():
            return

        try:
            for f in self.scanner.scan(max_files=200):
                self._known_files[f["path"]] = f.get("modified_ts", 0)
        except Exception as exc:
            _log.warning("watcher initial scan failed: %s", exc)
            self._last_error = f"initial scan: {exc}"

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        _log.info("FlowsheetWatcher started (poll=%.1fs, dirs=%d)",
                  self.poll_interval, len(self.scanner.watch_dirs))

    def stop(self) -> None:
        """Stop the background watcher thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_changes()
                self._consecutive_errors = 0
            except Exception as exc:
                self._error_count += 1
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {exc}"
                _log.warning("watcher poll error #%d: %s",
                             self._error_count, exc)
                # Back off exponentially on consecutive errors (max 60s).
                backoff = min(self.poll_interval * (2 ** self._consecutive_errors),
                              60.0)
                if self._stop_event.wait(backoff):
                    return
                continue
            self._stop_event.wait(self.poll_interval)

    @property
    def status(self) -> Dict[str, Any]:
        return {
            "running": self.is_running,
            "tracked_files": len(self._known_files),
            "error_count": self._error_count,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
        }

    def _check_changes(self) -> None:
        current_files: Dict[str, float] = {}

        for f in self.scanner.scan(max_files=200):
            path = f["path"]
            mtime = f.get("modified_ts", 0)
            current_files[path] = mtime

            if path not in self._known_files:
                # New file detected
                self._fire("created", f)
            elif mtime > self._known_files[path]:
                # File was modified (re-saved in DWSIM)
                self._fire("modified", f)

        # Check for deleted files
        for path in list(self._known_files.keys()):
            if path not in current_files:
                self._fire("deleted", {"path": path, "name": os.path.basename(path)})

        self._known_files = current_files

    def _fire(self, event_type: str, file_meta: Dict[str, Any]) -> None:
        if self.on_change:
            try:
                self.on_change(event_type, file_meta)
            except Exception as exc:
                _log.warning("watcher on_change(%s, %s) failed: %s",
                             event_type, file_meta.get("name"), exc)
