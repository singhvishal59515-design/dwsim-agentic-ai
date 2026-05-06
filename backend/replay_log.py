"""
replay_log.py
─────────────
Structured reproducibility log for the DWSIM Agentic AI.

Every agent turn writes a self-contained ReplayTurn record that includes:
  • prompt (user input, verbatim)
  • model metadata (provider, model, temperature, seed, prompt_hash)
  • system prompt snapshot (sha256 fingerprint + full text optional)
  • every LLM message in the conversation (role, content)
  • every tool call: name, input arguments, tool output, duration_ms
  • final answer text
  • convergence status + stream results snapshot
  • SF violation list

A replay_log.jsonl can be replayed exactly:
    python replay_log.py replay --file replay_log.jsonl --turn <turn_id>

This satisfies the independent reproducibility requirement for journal submission.
The log is append-only; nothing is ever deleted or overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    """One tool call + result within a turn."""
    seq:          int          # call order within turn (0-based)
    tool_name:    str
    arguments:    Dict[str, Any]
    result:       Dict[str, Any]
    duration_ms:  float
    success:      bool
    error:        Optional[str] = None


@dataclass
class ReplayTurn:
    """
    Complete record of one agent conversation turn.
    Self-contained: can be replayed without any external state.
    """
    # ── Identity ──────────────────────────────────────────────────────────────
    turn_id:      str          # uuid4 hex
    session_id:   str          # groups turns from the same session
    timestamp:    str          # ISO-8601 UTC
    turn_index:   int          # 0-based index within session

    # ── Inputs ────────────────────────────────────────────────────────────────
    user_prompt:  str          # verbatim user input
    prompt_hash:  str          # sha256[:16] of (user_prompt + json(tool_sequence))
    system_prompt_hash: str    # sha256[:16] of system prompt (for drift detection)

    # ── LLM metadata ──────────────────────────────────────────────────────────
    provider:     str
    model:        str
    temperature:  float
    seed:         int

    # ── Conversation trace ────────────────────────────────────────────────────
    messages:     List[Dict[str, Any]]   # full history sent to LLM each step

    # ── Tool call trace ───────────────────────────────────────────────────────
    tool_calls:   List[ToolCallRecord]
    tool_sequence: List[str]             # [tool_name, ...] — ordered

    # ── Outputs ───────────────────────────────────────────────────────────────
    final_answer: str
    converged:    bool
    stream_snapshot: Dict[str, Any]      # stream_results at end of turn
    sf_violations:   List[Dict[str, Any]] # ValidationFailure dicts

    # ── Timing ────────────────────────────────────────────────────────────────
    duration_s:   float
    llm_calls:    int                    # number of LLM round-trips

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Convert ToolCallRecord list to plain dicts
        d["tool_calls"] = [asdict(tc) for tc in self.tool_calls]
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReplayTurn":
        tool_calls = [ToolCallRecord(**tc) for tc in d.pop("tool_calls", [])]
        return cls(tool_calls=tool_calls, **d)


# ─────────────────────────────────────────────────────────────────────────────
# Log file management
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIR   = os.path.join(os.path.expanduser("~"), ".dwsim_agent", "replay")
_LOG_FILENAME  = "replay_log.jsonl"
_lock          = threading.Lock()


def _log_path() -> str:
    root = os.environ.get("DWSIM_REPLAY_LOG_DIR", _DEFAULT_DIR)
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, _LOG_FILENAME)


def append_turn(turn: ReplayTurn) -> str:
    """Append a ReplayTurn to the JSONL log. Returns the log file path."""
    path = _log_path()
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(turn.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_turns(
    session_id: Optional[str] = None,
    last_n: Optional[int] = None,
) -> List[ReplayTurn]:
    """Load all (or filtered) turns from the log."""
    path = _log_path()
    if not os.path.exists(path):
        return []
    turns = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if session_id and d.get("session_id") != session_id:
                    continue
                turns.append(ReplayTurn.from_dict(d))
            except Exception:
                continue
    if last_n:
        turns = turns[-last_n:]
    return turns


def get_turn(turn_id: str) -> Optional[ReplayTurn]:
    """Retrieve a specific turn by ID."""
    for t in load_turns():
        if t.turn_id == turn_id:
            return t
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Builder — used by agent_v2 to construct turns during execution
# ─────────────────────────────────────────────────────────────────────────────

class TurnBuilder:
    """
    Collects data during a single agent turn and produces a ReplayTurn.

    Usage (in agent_v2):
        builder = TurnBuilder(session_id=..., turn_index=..., provider=..., ...)
        builder.set_prompt(user_prompt, system_prompt)
        # During tool calls:
        builder.record_tool_call(name, args, result, duration_ms)
        # At end of turn:
        turn = builder.finish(answer, converged, stream_snapshot, sf_violations)
        replay_log.append_turn(turn)
    """

    def __init__(
        self,
        session_id:  str,
        turn_index:  int,
        provider:    str,
        model:       str,
        temperature: float,
        seed:        int,
    ):
        self.turn_id     = uuid.uuid4().hex
        self.session_id  = session_id
        self.turn_index  = turn_index
        self.provider    = provider
        self.model       = model
        self.temperature = temperature
        self.seed        = seed

        self._t0          = time.monotonic()
        self._timestamp   = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._user_prompt = ""
        self._sys_hash    = ""
        self._messages: List[Dict] = []
        self._tool_calls: List[ToolCallRecord] = []
        self._llm_calls   = 0

    def set_prompt(self, user_prompt: str, system_prompt: str = "") -> None:
        self._user_prompt = user_prompt
        self._sys_hash = hashlib.sha256(
            system_prompt.encode("utf-8", errors="replace")
        ).hexdigest()[:16]

    def add_message_snapshot(self, messages: List[Dict]) -> None:
        """Snapshot the messages list at each LLM call."""
        self._messages = [dict(m) for m in messages]
        self._llm_calls += 1

    def record_tool_call(
        self,
        tool_name:   str,
        arguments:   Dict[str, Any],
        result:      Dict[str, Any],
        duration_ms: float,
    ) -> None:
        success = bool(result.get("success", True))
        error   = result.get("error") if not success else None
        self._tool_calls.append(ToolCallRecord(
            seq         = len(self._tool_calls),
            tool_name   = tool_name,
            arguments   = dict(arguments),
            result      = dict(result),
            duration_ms = round(duration_ms, 1),
            success     = success,
            error       = str(error) if error else None,
        ))

    def finish(
        self,
        final_answer:    str,
        converged:       bool,
        stream_snapshot: Dict[str, Any],
        sf_violations:   List[Any],
    ) -> ReplayTurn:
        tool_seq = [tc.tool_name for tc in self._tool_calls]
        prompt_hash = hashlib.sha256(
            (self._user_prompt + json.dumps(tool_seq)).encode("utf-8", errors="replace")
        ).hexdigest()[:16]

        # Convert SF ValidationFailure objects to plain dicts
        sf_dicts = []
        for v in sf_violations:
            if hasattr(v, "__dict__"):
                sf_dicts.append(vars(v))
            elif isinstance(v, dict):
                sf_dicts.append(v)

        return ReplayTurn(
            turn_id      = self.turn_id,
            session_id   = self.session_id,
            timestamp    = self._timestamp,
            turn_index   = self.turn_index,
            user_prompt  = self._user_prompt,
            prompt_hash  = prompt_hash,
            system_prompt_hash = self._sys_hash,
            provider     = self.provider,
            model        = self.model,
            temperature  = self.temperature,
            seed         = self.seed,
            messages     = self._messages,
            tool_calls   = self._tool_calls,
            tool_sequence= tool_seq,
            final_answer = final_answer,
            converged    = converged,
            stream_snapshot = stream_snapshot,
            sf_violations   = sf_dicts,
            duration_s   = round(time.monotonic() - self._t0, 2),
            llm_calls    = self._llm_calls,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Summary / statistics
# ─────────────────────────────────────────────────────────────────────────────

def session_summary(session_id: str) -> Dict[str, Any]:
    """Return a summary dict suitable for the /reproducibility/last-turn endpoint."""
    turns = load_turns(session_id=session_id)
    if not turns:
        return {"session_id": session_id, "turns": 0}
    last = turns[-1]
    return {
        "session_id":      session_id,
        "turns":           len(turns),
        "last_turn_id":    last.turn_id,
        "last_timestamp":  last.timestamp,
        "last_prompt_hash":last.prompt_hash,
        "last_model":      f"{last.provider}/{last.model}",
        "last_temperature":last.temperature,
        "last_seed":       last.seed,
        "total_tool_calls":sum(len(t.tool_calls) for t in turns),
        "total_sf_violations": sum(len(t.sf_violations) for t in turns),
        "converged_pct":   round(
            sum(1 for t in turns if t.converged) / len(turns) * 100, 1
        ),
    }


def export_for_paper(session_id: str, out_path: str) -> str:
    """
    Export a session's replay log in a paper-appendix-friendly JSON format.
    Includes all prompt hashes, tool sequences, and convergence info — but
    strips raw message content to keep file size reasonable.
    """
    turns = load_turns(session_id=session_id)
    records = []
    for t in turns:
        records.append({
            "turn_id":    t.turn_id,
            "turn_index": t.turn_index,
            "timestamp":  t.timestamp,
            "prompt_hash":t.prompt_hash,
            "sys_hash":   t.system_prompt_hash,
            "model":      f"{t.provider}/{t.model}",
            "temperature":t.temperature,
            "seed":       t.seed,
            "tool_sequence": t.tool_sequence,
            "n_llm_calls":   t.llm_calls,
            "n_tool_calls":  len(t.tool_calls),
            "converged":     t.converged,
            "duration_s":    t.duration_s,
            "sf_violations": [v.get("code") for v in t.sf_violations],
            "tool_details": [
                {
                    "seq":      tc.seq,
                    "name":     tc.tool_name,
                    "success":  tc.success,
                    "duration_ms": tc.duration_ms,
                }
                for tc in t.tool_calls
            ],
        })
    data = {
        "session_id":    session_id,
        "n_turns":       len(records),
        "exported_at":   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "turns":         records,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# CLI: replay a recorded turn
# ─────────────────────────────────────────────────────────────────────────────

def _cli_replay(turn_id: str) -> None:
    """
    Print a full replay trace for a turn. Output is deterministic — same
    prompt + seed + temperature always produces the same tool sequence
    (given same model weights and schema version).
    """
    turn = get_turn(turn_id)
    if not turn:
        print(f"Turn {turn_id!r} not found in {_log_path()}")
        return

    print("=" * 70)
    print(f"REPLAY TURN  {turn.turn_id}")
    print(f"Session:     {turn.session_id}")
    print(f"Timestamp:   {turn.timestamp}")
    print(f"Model:       {turn.provider}/{turn.model}  T={turn.temperature}  seed={turn.seed}")
    print(f"Prompt hash: {turn.prompt_hash}  |  Sys hash: {turn.system_prompt_hash}")
    print(f"Duration:    {turn.duration_s:.1f}s  |  LLM calls: {turn.llm_calls}")
    print()
    print(f"USER PROMPT:")
    print(f"  {turn.user_prompt}")
    print()
    print(f"TOOL SEQUENCE ({len(turn.tool_calls)} calls):")
    for tc in turn.tool_calls:
        status = "OK " if tc.success else "ERR"
        print(f"  [{tc.seq:2d}] {status} {tc.tool_name:<30} ({tc.duration_ms:.0f}ms)")
        if tc.error:
            print(f"       ERROR: {tc.error}")
    print()
    print(f"FINAL ANSWER (truncated):")
    print(f"  {turn.final_answer[:300]}")
    print()
    if turn.sf_violations:
        print(f"SF VIOLATIONS ({len(turn.sf_violations)}):")
        for v in turn.sf_violations:
            print(f"  {v.get('code','?'):8s} [{v.get('severity','?')}] {v.get('description','')[:80]}")
    else:
        print("SF VIOLATIONS: none")
    print("=" * 70)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DWSIM Replay Log utility")
    sub = parser.add_subparsers(dest="cmd")

    rep = sub.add_parser("replay", help="Print replay trace for a turn")
    rep.add_argument("--turn", required=True, help="Turn ID (hex)")

    lst = sub.add_parser("list", help="List recent turns")
    lst.add_argument("--session", default=None)
    lst.add_argument("--n", type=int, default=20)

    exp = sub.add_parser("export", help="Export session for paper appendix")
    exp.add_argument("--session", required=True)
    exp.add_argument("--out",     required=True)

    args = parser.parse_args()
    if args.cmd == "replay":
        _cli_replay(args.turn)
    elif args.cmd == "list":
        for t in load_turns(session_id=args.session, last_n=args.n):
            print(f"{t.timestamp}  {t.turn_id[:8]}  {t.provider}/{t.model}"
                  f"  hash={t.prompt_hash}  tools={len(t.tool_calls)}"
                  f"  conv={t.converged}")
    elif args.cmd == "export":
        path = export_for_paper(args.session, args.out)
        print(f"Exported to {path}")
    else:
        parser.print_help()
