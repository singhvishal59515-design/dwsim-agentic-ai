"""
ablation_config.py
──────────────────
Single source of truth for ablation-grade run configuration, read from the
environment so every one of the four ablation conditions is configured
identically and reproducibly (no code edits between conditions).

The ablation study (Phase 4) compares: Full System / No-RAG / No-SafetyValidator
/ Direct-LLM. For the comparison to be defensible, every condition must share:
  • ONE locked provider+model (no silent cross-provider failover mid-study),
  • temperature 0 on EVERY attempt (no retry-temperature diversity),
  • a fixed retry budget,
and every recorded turn must carry which condition / task / repetition it
belongs to so the JSONL transcript can be grouped for statistics.

Environment variables (all optional; absent ⇒ normal interactive behaviour):
  DWSIM_ABLATION_CONDITION   e.g. "full" | "no_rag" | "no_safety" | "direct_llm"
  DWSIM_ABLATION_TASK        task id, e.g. "C1-T01"
  DWSIM_ABLATION_REP         repetition index, e.g. "1"
  DWSIM_DETERMINISTIC=1      force temperature 0 on all attempts (implied when a
                             condition is set)
  DWSIM_LOCK_PROVIDER=1      disable cross-provider failover (implied when a
                             condition is set)

Setting DWSIM_ABLATION_CONDITION turns on deterministic + provider-lock
automatically, so a single env var is enough to enter ablation mode.

Values are read live from os.environ on every access so a test (or the ablation
runner) can change them between turns without reimporting.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional, Sequence, Tuple


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


class _Ablation:
    # ── identity of the current run ──────────────────────────────────────────
    @property
    def condition(self) -> Optional[str]:
        return os.environ.get("DWSIM_ABLATION_CONDITION") or None

    @property
    def task_id(self) -> Optional[str]:
        return os.environ.get("DWSIM_ABLATION_TASK") or None

    @property
    def rep(self) -> Optional[int]:
        v = os.environ.get("DWSIM_ABLATION_REP")
        if v in (None, ""):
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @property
    def active(self) -> bool:
        """True when running under the ablation harness (a condition is set)."""
        return self.condition is not None

    # ── determinism levers ───────────────────────────────────────────────────
    @property
    def deterministic(self) -> bool:
        return _flag("DWSIM_DETERMINISTIC") or self.active

    @property
    def lock_provider(self) -> bool:
        return _flag("DWSIM_LOCK_PROVIDER") or self.active

    def retry_temperatures(self, default: Sequence[float]) -> Tuple[float, ...]:
        """In deterministic mode every attempt uses temperature 0; otherwise the
        caller's default diversity schedule is preserved."""
        if self.deterministic:
            return tuple(0.0 for _ in default)
        return tuple(default)

    # ── condition toggles (Phase 4) ──────────────────────────────────────────
    # The four ablation conditions, selected entirely by DWSIM_ABLATION_CONDITION:
    #   "full"       — everything on (also the default when no condition is set)
    #   "no_rag"     — retrieval-augmented generation disabled
    #   "no_safety"  — the SafetyValidator post-solve checks disabled
    #   "direct_llm" — bare LLM: no tools, no RAG, no SafetyValidator (the
    #                  "does the agentic apparatus help at all?" baseline)
    @property
    def direct_llm(self) -> bool:
        return self.condition == "direct_llm"

    @property
    def disable_rag(self) -> bool:
        return self.condition == "no_rag" or self.direct_llm

    @property
    def disable_safety(self) -> bool:
        return self.condition == "no_safety" or self.direct_llm

    @property
    def disable_tools(self) -> bool:
        return self.direct_llm

    #   "no_cot"     — chain-of-thought reasoning guidance removed from the
    #                  system prompt (Tian et al. Table 4, "w/o CoT")
    #   "no_fewshot" — few-shot worked examples removed from the system prompt
    #                  (Tian et al. Table 4, "w/o Few-Shot")
    @property
    def disable_cot(self) -> bool:
        return self.condition == "no_cot"

    @property
    def disable_fewshot(self) -> bool:
        return self.condition == "no_fewshot"

    # ── replay-log tagging ───────────────────────────────────────────────────
    def tags(self) -> Dict[str, Any]:
        return {"condition": self.condition,
                "task_id":   self.task_id,
                "rep":       self.rep}


ablation = _Ablation()
