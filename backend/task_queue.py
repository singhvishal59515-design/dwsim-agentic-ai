"""
task_queue.py — In-memory async task queue for long-running ops.

The DWSIM bridge serializes all calls under _bridge_lock. A `run_simulation`
call can hold the lock for 60–180 s, blocking even /status and /chat. This
queue submits long ops to a worker thread and returns a task_id immediately.
The UI polls /tasks/{task_id} for completion.

Tasks are kept for 1 hour after completion. A maximum of 200 tasks is held
in memory; oldest completed are evicted first.
"""

from __future__ import annotations
import logging
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Any, Callable, Dict, Optional

_log = logging.getLogger("task_queue")

_TASK_TTL_S = 3600.0
_MAX_TASKS_RETAINED = 200


class _Task:
    __slots__ = (
        "id", "name", "status", "created_at", "started_at", "finished_at",
        "result", "error", "future", "progress", "extra",
    )

    def __init__(self, task_id: str, name: str):
        self.id = task_id
        self.name = name
        self.status = "queued"
        self.created_at = time.time()
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.result: Any = None
        self.error: Optional[str] = None
        self.future: Optional[Future] = None
        self.progress: float = 0.0
        self.extra: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        # Copy `extra` (and any live 'steps' list) so concurrent worker appends
        # cannot mutate the structure mid-serialisation.
        extra = dict(self.extra)
        if isinstance(extra.get("steps"), list):
            extra["steps"] = list(extra["steps"])
        return {
            "task_id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "queued_seconds":
                round((self.started_at or time.time()) - self.created_at, 2),
            "running_seconds":
                round((self.finished_at or time.time())
                      - (self.started_at or self.created_at), 2)
                if self.status != "queued" else 0.0,
            "result": self.result if self.status == "done" else None,
            "error": self.error,
            **extra,
        }


class TaskQueue:
    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dwsim-task")
        self._tasks: Dict[str, _Task] = {}
        self._lock = threading.RLock()

    def submit(self, name: str, fn: Callable, *args, **kwargs) -> str:
        tid = uuid.uuid4().hex[:12]
        task = _Task(tid, name)
        with self._lock:
            self._evict_old()
            self._tasks[tid] = task

        def _runner():
            task.status = "running"
            task.started_at = time.time()
            try:
                task.result = fn(*args, **kwargs)
                task.status = "done"
                task.progress = 1.0
            except Exception as exc:
                task.error = f"{exc.__class__.__name__}: {exc}"
                task.status = "failed"
                _log.warning("task %s (%s) failed: %s\n%s",
                             tid, name, exc, traceback.format_exc())
            finally:
                task.finished_at = time.time()

        task.future = self._executor.submit(_runner)
        return tid

    def submit_streaming(self, name: str, fn: Callable) -> str:
        """Like submit(), but `fn` is called as fn(report) where
        report(stage, detail="") records a live progress step that the UI can
        read via /tasks/{id} → task.extra['steps']. Lets a long synchronous
        worker (e.g. the optimization workflow) stream its stages while running,
        the same way the chat path streams on_step/on_eval."""
        tid = uuid.uuid4().hex[:12]
        task = _Task(tid, name)
        task.extra["steps"] = []
        with self._lock:
            self._evict_old()
            self._tasks[tid] = task

        def _report(stage: Any, detail: Any = "") -> None:
            with self._lock:
                steps = task.extra.setdefault("steps", [])
                steps.append({
                    "stage": str(stage),
                    "detail": str(detail),
                    "t": round(time.time()
                               - (task.started_at or task.created_at), 1),
                })
                if len(steps) > 250:          # bound memory on long runs
                    del steps[:len(steps) - 250]

        def _runner():
            task.status = "running"
            task.started_at = time.time()
            try:
                task.result = fn(_report)
                task.status = "done"
                task.progress = 1.0
            except Exception as exc:
                task.error = f"{exc.__class__.__name__}: {exc}"
                task.status = "failed"
                _log.warning("task %s (%s) failed: %s\n%s",
                             tid, name, exc, traceback.format_exc())
            finally:
                task.finished_at = time.time()

        task.future = self._executor.submit(_runner)
        return tid

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            t = self._tasks.get(task_id)
            return t.to_dict() if t else None

    def list_tasks(self, status_filter: str = "") -> Dict[str, Any]:
        with self._lock:
            items = list(self._tasks.values())
        if status_filter:
            items = [t for t in items if t.status == status_filter]
        items.sort(key=lambda t: t.created_at, reverse=True)
        return {
            "success": True,
            "count": len(items),
            "tasks": [t.to_dict() for t in items[:100]],
        }

    def cancel(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return {"success": False, "error": "task not found"}
            if t.status in ("done", "failed", "cancelled"):
                return {"success": True, "already_finished": True,
                        "status": t.status}
            cancelled = bool(t.future and t.future.cancel())
            if cancelled:
                t.status = "cancelled"
                t.finished_at = time.time()
            return {"success": cancelled, "status": t.status,
                    "note": ("Future was already running; "
                             "Python cannot interrupt a running thread.")
                            if not cancelled else None}

    def _evict_old(self) -> None:
        # Only called under _lock
        now = time.time()
        to_drop = [
            tid for tid, t in self._tasks.items()
            if t.status in ("done", "failed", "cancelled")
            and (t.finished_at or 0) + _TASK_TTL_S < now
        ]
        for tid in to_drop:
            self._tasks.pop(tid, None)
        if len(self._tasks) > _MAX_TASKS_RETAINED:
            # drop oldest finished first
            finished = sorted(
                [t for t in self._tasks.values()
                 if t.status in ("done", "failed", "cancelled")],
                key=lambda t: t.finished_at or 0,
            )
            for t in finished[:len(self._tasks) - _MAX_TASKS_RETAINED]:
                self._tasks.pop(t.id, None)


_DEFAULT_QUEUE: Optional[TaskQueue] = None


def get_queue() -> TaskQueue:
    global _DEFAULT_QUEUE
    if _DEFAULT_QUEUE is None:
        _DEFAULT_QUEUE = TaskQueue(max_workers=2)
    return _DEFAULT_QUEUE
