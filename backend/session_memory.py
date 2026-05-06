"""
session_memory.py
─────────────────
Persistent domain memory for the agent across sessions.

Journal entries (append-only JSONL):
  • flowsheet_built  — tag, compounds, property_package, template_used, path
  • goal             — user-stated design objective (e.g. "max product purity")
  • constraint       — plant/process limit (e.g. "T ≤ 200 °C")
  • outcome          — converged / failed + key result numbers

The journal feeds a compact "recent context" block that gets injected into the
system prompt on each turn, so the agent has continuity without re-reading
files or asking the user to restate constraints.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# Default location — writable by the process user, no install-time config needed.
_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".dwsim_agent", "memory")
_JOURNAL = "sessions.jsonl"
_GOALS   = "goals.json"

_ENTRY_TYPES  = ("flowsheet_built", "goal", "constraint", "outcome", "note")
_MAX_ENTRIES  = 500    # prune journal once it exceeds this to prevent disk fill
_PRUNE_TO     = 400    # keep newest N after pruning

_lock = threading.Lock()


def _root() -> str:
    root = os.environ.get("DWSIM_AGENT_MEMORY", _DEFAULT_DIR)
    os.makedirs(root, exist_ok=True)
    return root


def _journal_path() -> str:
    return os.path.join(_root(), _JOURNAL)


def _goals_path() -> str:
    return os.path.join(_root(), _GOALS)


# ─────────────────────────────────────────────────────────────────────────────
# Write
# ─────────────────────────────────────────────────────────────────────────────

def record(entry_type: str, **payload) -> Dict[str, Any]:
    """Append a structured entry to the journal."""
    if entry_type not in _ENTRY_TYPES:
        return {"success": False,
                "error": f"unknown entry_type {entry_type!r}; "
                         f"valid: {_ENTRY_TYPES}"}
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "type": entry_type,
        **payload,
    }
    path = _journal_path()
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
        # Prune to prevent unbounded disk growth
        try:
            _prune_journal_if_needed(path)
        except Exception:
            pass
    return {"success": True, "entry": entry}


def _prune_journal_if_needed(path: str) -> None:
    """Keep only the newest _PRUNE_TO entries if journal exceeds _MAX_ENTRIES."""
    lines = []
    with open(path, "r", encoding="utf-8") as f:
        lines = [l for l in f if l.strip()]
    if len(lines) <= _MAX_ENTRIES:
        return
    # Keep newest lines (they are in append order, so tail = newest)
    lines = lines[-_PRUNE_TO:]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def record_flowsheet_built(*, name: str,
                            compounds: List[str],
                            property_package: str,
                            path: Optional[str] = None,
                            template: Optional[str] = None,
                            streams: int = 0,
                            unit_ops: int = 0,
                            converged: Optional[bool] = None,
                            prompt: str = "",
                            ) -> Dict[str, Any]:
    return record("flowsheet_built",
                  name=name,
                  compounds=compounds,
                  property_package=property_package,
                  path=path,
                  template=template,
                  streams=streams,
                  unit_ops=unit_ops,
                  converged=converged,
                  prompt=prompt[:500])


# ─────────────────────────────────────────────────────────────────────────────
# Read
# ─────────────────────────────────────────────────────────────────────────────

def _iter_entries():
    path = _journal_path()
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except OSError:
        return


def recent(limit: int = 10,
           entry_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the most recent entries, newest first. Optional type filter."""
    entries = list(_iter_entries())
    if entry_type:
        entries = [e for e in entries if e.get("type") == entry_type]
    entries.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return entries[:limit]


def search(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Substring-match search across entry text (no index — linear scan)."""
    q = (query or "").lower().strip()
    if not q:
        return recent(limit)
    hits = []
    for e in _iter_entries():
        blob = json.dumps(e, default=str).lower()
        if q in blob:
            hits.append(e)
    hits.sort(key=lambda e: e.get("ts", 0), reverse=True)
    return hits[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Goals & constraints (small, overwritable state)
# ─────────────────────────────────────────────────────────────────────────────

def _load_goals() -> Dict[str, Any]:
    p = _goals_path()
    if not os.path.isfile(p):
        return {"goals": [], "constraints": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("goals", [])
        data.setdefault("constraints", [])
        return data
    except Exception:
        return {"goals": [], "constraints": []}


def _save_goals(data: Dict[str, Any]) -> None:
    p = _goals_path()
    tmp = p + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, p)


def set_goal(text: str) -> Dict[str, Any]:
    data = _load_goals()
    item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "text": text}
    data["goals"].append(item)
    _save_goals(data)
    record("goal", text=text, id=item["id"])
    return {"success": True, "goal": item, "goal_count": len(data["goals"])}


def set_constraint(text: str) -> Dict[str, Any]:
    data = _load_goals()
    item = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "text": text}
    data["constraints"].append(item)
    _save_goals(data)
    record("constraint", text=text, id=item["id"])
    return {"success": True, "constraint": item,
            "constraint_count": len(data["constraints"])}


def clear_goals() -> Dict[str, Any]:
    _save_goals({"goals": [], "constraints": []})
    return {"success": True}


def get_goals() -> Dict[str, Any]:
    data = _load_goals()
    return {"success": True, **data}


# ─────────────────────────────────────────────────────────────────────────────
# System-prompt injection block
# ─────────────────────────────────────────────────────────────────────────────

def compose_context_block(*, max_recent: int = 3,
                          max_chars: int = 1200) -> str:
    """Return a compact context block for the system prompt.

    Short by design — the agent doesn't need chapter and verse, just
    enough continuity to avoid re-asking the user what they already said.
    """
    goals = _load_goals()
    recent_builds = recent(max_recent, entry_type="flowsheet_built")

    lines: List[str] = []
    if goals["goals"]:
        lines.append("DESIGN GOALS (from prior sessions):")
        for g in goals["goals"][-5:]:
            lines.append(f"  • {g['text']}")

    if goals["constraints"]:
        lines.append("CONSTRAINTS:")
        for c in goals["constraints"][-5:]:
            lines.append(f"  • {c['text']}")

    if recent_builds:
        lines.append("RECENT FLOWSHEETS (newest first):")
        for b in recent_builds:
            when = time.strftime("%Y-%m-%d", time.localtime(b.get("ts", 0)))
            comp = ", ".join((b.get("compounds") or [])[:4])
            converged = b.get("converged")
            conv_txt = ("converged" if converged is True else
                        "not converged" if converged is False else
                        "status unknown")
            tmpl = f" (template: {b['template']})" if b.get("template") else ""
            lines.append(
                f"  • {when}  {b.get('name','?')}: {comp} / "
                f"{b.get('property_package','?')} — "
                f"{b.get('streams',0)} streams, {b.get('unit_ops',0)} ops, "
                f"{conv_txt}{tmpl}")

    if not lines:
        return ""

    block = "\n".join(lines)
    if len(block) > max_chars:
        block = block[:max_chars - 3].rstrip() + "..."
    return (
        "── Persistent memory (use to avoid re-asking the user; may be "
        "outdated — verify if anything is load-bearing) ──\n"
        + block
    )
