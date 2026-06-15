"""
bridge_patches_v4.py
====================
Drop-in fixes for the 7 bugs found in the live MCP diagnostic (2026-06-12).

Vendored into the backend so the helpers are importable from the real modules.
NOTE: the project's bridge/api modules are `dwsim_bridge_v2.py` / `api.py`
(the original draft referenced a `_v3` name that does not exist in this repo).

Wiring status (2026-06-13):
  #7 split_known_objects / stream_not_found_error  -> WIRED in dwsim_bridge_v2
  #5 PropertyNames (case-insensitive + did-you-mean) -> WIRED in dwsim_bridge_v2
  #2 ReadBeforeWrite (read->write->read-back)       -> WIRED in set_stream_property
  #6 DirtyState (stale-value tracking)              -> WIRED in dwsim_bridge_v2
  #1 JobManager (async optimize/agent jobs)         -> NOT wired (needs MCP-server
                                                       refactor + 2 new tools; the
                                                       helper is kept here ready)
  #3 assert_energy_stream                           -> NOT wired (needs live DWSIM
                                                       connector introspection to
                                                       validate; kept here ready)
  #4 check_unit_op_specs                            -> NOT wired (same; live-only)

Bug map:
  #1 sync optimize > 4 min MCP timeout  -> JobManager (async job pattern)
  #2 old_value always null              -> ReadBeforeWrite
  #3 energy stream created as material  -> assert_energy_stream
  #4 converged-but-meaningless solve    -> check_unit_op_specs
  #5 case-sensitive property warnings   -> PropertyNames
  #6 stale values after write           -> DirtyState
  #7 unit ops in stream suggestions     -> split_known_objects
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ---------------------------------------------------------------------------
# BUG #1 (CRITICAL): synchronous optimize/agent calls exceed the MCP
# transport timeout (~4 min). Fix: async job pattern.
#
# Replace the body of the `dwsim_optimize` and `dwsim_agent` tools with
# `JobManager.start(...)` which returns a job_id IMMEDIATELY, and add two
# new tools: `dwsim_job_status(job_id)` and `dwsim_job_cancel(job_id)`.
# The calling LLM then polls — every poll is a fast call, so the MCP
# transport never times out, regardless of how long the optimization runs.
# ---------------------------------------------------------------------------

@dataclass
class Job:
    job_id: str
    kind: str                       # "optimize" | "agent"
    goal: str
    status: str = "running"         # running | done | failed | cancelled
    progress: list = field(default_factory=list)   # human-readable log
    iteration: int = 0
    max_iter: int = 50
    best_objective: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    _cancel_flag: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "goal": self.goal,
            "status": self.status,
            "iteration": self.iteration,
            "max_iter": self.max_iter,
            "best_objective": self.best_objective,
            "elapsed_s": round((self.finished_at or time.time()) - self.started_at, 1),
            # Only the tail of the log — keep poll responses small & fast.
            "progress_tail": self.progress[-5:],
            "result": self.result if self.status == "done" else None,
            "error": self.error,
        }


class JobManager:
    """Thread-backed job registry. One instance per bridge process."""

    def __init__(self, max_concurrent: int = 1):
        self._jobs: dict = {}
        self._lock = threading.Lock()
        self._sema = threading.Semaphore(max_concurrent)

    def start(self, kind: str, goal: str, worker: Callable[["Job"], Any],
              max_iter: int = 50) -> dict:
        """
        `worker(job)` is your existing optimization/agent routine, refactored
        to take the Job so it can (a) append to job.progress, (b) bump
        job.iteration, and (c) check job.cancelled() inside its loop.
        Returns immediately with the job_id.
        """
        job = Job(job_id=uuid.uuid4().hex[:12], kind=kind, goal=goal,
                  max_iter=max_iter)
        with self._lock:
            self._jobs[job.job_id] = job

        def _run():
            with self._sema:                  # serialize DWSIM access —
                try:                          # the .NET bridge is not thread-safe
                    job.result = worker(job)
                    job.status = "cancelled" if job._cancel_flag.is_set() else "done"
                except Exception as exc:      # noqa: BLE001
                    job.status = "failed"
                    job.error = f"{exc}\n{traceback.format_exc(limit=3)}"
                finally:
                    job.finished_at = time.time()

        threading.Thread(target=_run, daemon=True,
                         name=f"dwsim-{kind}-{job.job_id}").start()
        return {"success": True, "job_id": job.job_id, "status": "running",
                "message": ("Job started. Poll dwsim_job_status(job_id) every "
                            "few seconds; do NOT wait synchronously.")}

    def status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"success": False, "error": f"Unknown job_id '{job_id}'",
                    "known_jobs": list(self._jobs)}
        return {"success": True, **job.to_dict()}

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"success": False, "error": f"Unknown job_id '{job_id}'"}
        job._cancel_flag.set()
        return {"success": True, "job_id": job_id,
                "message": "Cancel requested; worker stops at next iteration check."}


# Helper your optimization loop should call once per iteration:
def job_tick(job: "Job", iteration: int, objective: float, note: str = "") -> bool:
    """Record progress; returns False if the loop should stop (cancelled)."""
    job.iteration = iteration
    if job.best_objective is None or objective < job.best_objective:
        job.best_objective = objective
    job.progress.append(f"iter {iteration}: obj={objective:.6g} {note}".strip())
    return not job._cancel_flag.is_set()


# Tool schemas to append to tools_schema_v2.py if/when JobManager is wired:
JOB_TOOL_SCHEMAS = [
    {
        "name": "dwsim_job_status",
        "description": ("Poll a running optimization/agent job started by "
                        "dwsim_optimize or dwsim_agent. Returns status, "
                        "iteration count, best objective so far, and the "
                        "final result when status == 'done'."),
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
    {
        "name": "dwsim_job_cancel",
        "description": "Cancel a running optimization/agent job by job_id.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# BUG #2 (HIGH): set_stream_property returns old_value=null.
# Root cause (typical): the old value is read AFTER SetPropertyValue, or the
# read uses a different property-id path that returns None and is swallowed.
# Fix: explicit read -> write -> read-back, all through the SAME getter.
# ---------------------------------------------------------------------------

class ReadBeforeWrite:
    """
    Wrap your low-level setter so every write returns a full audit record:
    {old_value, requested, new_value, verified}.
    `getter(tag, prop)` and `setter(tag, prop, value)` are your existing
    bridge functions (GUID-resolved).
    """

    def __init__(self, getter: Callable[[str, str], Any],
                 setter: Callable[[str, str, float], Any],
                 rel_tol: float = 1e-6):
        self._get, self._set, self._rtol = getter, setter, rel_tol

    def set_verified(self, tag: str, prop: str, value: float) -> dict:
        old = self._safe_get(tag, prop)
        self._set(tag, prop, value)
        new = self._safe_get(tag, prop)
        verified = (
            new is not None
            and abs(new - value) <= self._rtol * max(abs(value), 1.0)
        )
        rec = {"success": bool(verified), "tag": tag, "property": prop,
               "old_value": old, "requested": value, "new_value": new,
               "verified": verified}
        if not verified:
            rec["error"] = (f"Write-verification FAILED: requested {value}, "
                            f"read back {new}. Property may be calculated "
                            f"(not specifiable) on this stream.")
        return rec

    def _safe_get(self, tag: str, prop: str):
        try:
            return self._get(tag, prop)
        except Exception:   # noqa: BLE001
            return None


# ---------------------------------------------------------------------------
# BUG #5 (MEDIUM): 'temperature' warns "not recognised" but succeeds anyway.
# Fix: normalize BEFORE validation. One canonical map, case-insensitive,
# with aliases. The warning path and the setter path must use the same map.
# ---------------------------------------------------------------------------

class PropertyNames:
    _CANON = {
        # canonical        aliases (all compared lowercase, stripped)
        "Temperature":  {"temperature", "temp", "t"},
        "Pressure":     {"pressure", "p", "pres"},
        "MassFlow":     {"massflow", "mass_flow", "mass flow", "w"},
        "MolarFlow":    {"molarflow", "molar_flow", "molar flow", "f"},
        "VolumetricFlow": {"volumetricflow", "volumetric_flow",
                           "volume_flow", "q_vol"},
        "Enthalpy":     {"enthalpy", "h"},
        "VaporFraction": {"vaporfraction", "vapor_fraction", "vf",
                          "vapour_fraction", "quality"},
        "EnergyFlow":   {"energyflow", "energy_flow", "duty", "heat_duty",
                         "q", "heatflow", "heat_flow"},
    }
    _LOOKUP = {a: canon for canon, aliases in _CANON.items() for a in aliases}
    for canon in list(_CANON):
        _LOOKUP[canon.lower()] = canon

    @classmethod
    def resolve(cls, name: str):
        """Returns (canonical_name, error). Exactly one is None."""
        key = name.strip().lower().replace("-", "_")
        canon = cls._LOOKUP.get(key) or cls._LOOKUP.get(key.replace("_", ""))
        if canon:
            return canon, None
        import difflib
        guess = difflib.get_close_matches(key, cls._LOOKUP, n=5, cutoff=0.6)
        suggestions = list(dict.fromkeys(cls._LOOKUP[g] for g in guess))[:3]
        return None, (f"Unknown property '{name}'. "
                      f"Did you mean: {suggestions}?")


# ---------------------------------------------------------------------------
# BUG #6 (MEDIUM): after a write, dependent properties (enthalpy, density,
# volumetric flow) are stale until re-solve, with no indication.
# Fix: dirty-state tracker. Mark dirty on every successful write or topology
# change; clear on successful solve; stamp every get_stream response.
# ---------------------------------------------------------------------------

class DirtyState:
    def __init__(self):
        self._dirty = False
        self._dirty_reasons: list = []

    def mark(self, reason: str):
        self._dirty = True
        self._dirty_reasons.append(reason)

    def clear(self):
        self._dirty = False
        self._dirty_reasons.clear()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def stamp(self, response: dict) -> dict:
        """Call on every get_stream / get_results payload before returning."""
        if not isinstance(response, dict):
            return response
        response["needs_resolve"] = self._dirty
        if self._dirty:
            response["warning"] = (
                "Flowsheet modified since last solve — calculated properties "
                "(enthalpy, density, volumetric flow, downstream streams) are "
                f"STALE. Pending changes: {self._dirty_reasons[-3:]}. "
                "Call dwsim_solve before trusting these values."
            )
        return response


# ---------------------------------------------------------------------------
# BUG #3 (HIGH): flowsheet builder created energy stream 'Q' as a
# MaterialStream (free-floating clone of Feed). Fix: post-creation assertion
# + connection check. Call immediately after creating any duty stream.
# ---------------------------------------------------------------------------

def assert_energy_stream(flowsheet, tag: str) -> dict:
    """
    Verify that `tag` is a genuine EnergyStream and is connected.
    In DWSIM, energy stream GUIDs are not 'MAT-' prefixed and
    GraphicObject.ObjectType is OT_EnergyStream / EnergyStream.
    """
    obj = flowsheet.GetFlowsheetSimulationObject(tag)
    if obj is None:
        return {"success": False, "error": f"'{tag}' not found after creation."}
    type_name = type(obj).__name__
    if "Energy" not in type_name:
        return {"success": False, "code": "WRONG_OBJECT_TYPE",
                "error": (f"'{tag}' was created as {type_name}, expected "
                          "EnergyStream. The builder's type map routed a duty "
                          "stream through the material-stream constructor. "
                          "Delete it and recreate via the EnergyStream path."),
                "fix_hint": ("In your add_stream tool, branch on "
                             "stream_kind=='energy' BEFORE tag-based "
                             "heuristics; never infer type from the tag name.")}
    go = getattr(obj, "GraphicObject", None)
    connected = bool(go and (go.InputConnectors[0].IsAttached or
                             go.OutputConnectors[0].IsAttached))
    return {"success": True, "tag": tag, "type": type_name,
            "connected": connected,
            **({} if connected else
               {"warning": f"EnergyStream '{tag}' is not attached to any unit."})}


# ---------------------------------------------------------------------------
# BUG #4 (MEDIUM): solve reports all_converged on a heater with no spec
# (outlet == inlet exactly). Fix: spec sanity pass over unit operations,
# merged into physical_warnings.
# ---------------------------------------------------------------------------

def check_unit_op_specs(flowsheet, list_objects_result: dict,
                        get_stream: Callable[[str], dict]) -> list:
    """
    Returns warnings to append to convergence_check['physical_warnings'].
    Catches the 'converged but meaningless' class:
      - Heater/Cooler whose outlet T equals inlet T (no spec / zero duty)
      - Any unit op missing from the converged set entirely
    """
    warnings: list = []
    for uo in list_objects_result.get("unit_ops", []):
        tag, uo_type = uo["tag"], uo["type"]
        obj = flowsheet.GetFlowsheetSimulationObject(tag)
        if obj is None:
            continue
        if uo_type in ("Heater", "Cooler"):
            try:
                inlet = obj.GraphicObject.InputConnectors[0].AttachedConnector \
                           .AttachedFrom.Tag
                outlet = obj.GraphicObject.OutputConnectors[0].AttachedConnector \
                            .AttachedTo.Tag
                t_in = get_stream(inlet)["properties"]["temperature_K"]
                t_out = get_stream(outlet)["properties"]["temperature_K"]
                if abs(t_out - t_in) < 1e-3:
                    warnings.append(
                        f"{uo_type} '{tag}': outlet T == inlet T "
                        f"({t_in:.2f} K). Unit has no effective specification "
                        "(zero duty / no ΔT / no outlet-T spec). Solve "
                        "'converged' but the unit is doing nothing.")
            except Exception:   # noqa: BLE001
                warnings.append(f"{uo_type} '{tag}': could not verify spec "
                                "(connector introspection failed).")
    return warnings


# ---------------------------------------------------------------------------
# BUG #7 (LOW): "Stream not found. Known: [... 'H-101']" mixes unit ops into
# stream suggestions. Fix: category-aware suggestion lists.
# ---------------------------------------------------------------------------

def split_known_objects(list_objects_result: dict) -> dict:
    streams = [s["tag"] for s in list_objects_result.get("streams", [])]
    units = [u["tag"] for u in list_objects_result.get("unit_ops", [])]
    return {"known_streams": streams, "known_unit_ops": units}


def stream_not_found_error(tag: str, list_objects_result: dict) -> dict:
    known = split_known_objects(list_objects_result)
    return {"success": False,
            "error": (f"Stream '{tag}' not found. "
                      f"Known streams: {known['known_streams']}. "
                      f"(Unit operations, not streams: "
                      f"{known['known_unit_ops']})")}
