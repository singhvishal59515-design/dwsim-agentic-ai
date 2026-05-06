"""
session.py  — IMP-10
─────────────────────
Save and load agent conversation sessions to/from JSON.
Preserves: conversation history, loaded flowsheet state, provider/model info.
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional


SESSION_DIR = os.path.join(os.path.expanduser("~"), ".dwsim_agent_sessions")


def save_session(
    history:        list,
    provider:       str,
    model:          str,
    flowsheet_path: Optional[str] = None,
    flowsheet_name: Optional[str] = None,
    session_name:   Optional[str] = None,
) -> str:
    """
    Save conversation history and metadata to a JSON file.
    Returns the saved file path.
    """
    os.makedirs(SESSION_DIR, exist_ok=True)

    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = session_name or f"session_{ts}"
    if not name.endswith(".json"):
        name += ".json"

    payload = {
        "version":        1,
        "saved_at":       datetime.now().isoformat(),
        "provider":       provider,
        "model":          model,
        "flowsheet_path": flowsheet_path,
        "flowsheet_name": flowsheet_name,
        "message_count":  len(history),
        "history":        _serialise_history(history),
    }

    # Atomic write: write to .tmp, then rename. Prevents corruption on crash.
    path = os.path.join(SESSION_DIR, name)
    tmp  = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    os.replace(tmp, path)

    return path


def load_session(path: str) -> Dict[str, Any]:
    """
    Load a previously saved session.
    Returns dict with keys: history, provider, model, flowsheet_path, etc.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    data["history"] = _deserialise_history(data.get("history", []))
    return data


def list_sessions() -> list:
    """List all saved session files, most recent first."""
    if not os.path.exists(SESSION_DIR):
        return []
    files = [
        os.path.join(SESSION_DIR, f)
        for f in os.listdir(SESSION_DIR)
        if f.endswith(".json")
    ]
    return sorted(files, reverse=True)


def _serialise_history(history: list) -> list:
    """Convert history to a JSON-safe format."""
    safe = []
    for msg in history:
        m: Dict[str, Any] = {"role": msg.get("role", "user")}
        content = msg.get("content", "")
        if isinstance(content, str):
            m["content"] = content
        elif isinstance(content, list):
            # Anthropic-style content blocks — keep only text blocks
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            m["content"] = " ".join(texts)
        else:
            m["content"] = str(content)
        # Include tool_calls if present
        if "tool_calls" in msg:
            m["tool_calls"] = msg["tool_calls"]
        safe.append(m)
    return safe


def _deserialise_history(raw: list) -> list:
    return raw   # already plain dicts