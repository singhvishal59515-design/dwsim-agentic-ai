"""
prompts.py  —  Versioned Prompt Catalog for DWSIM Agentic AI
──────────────────────────────────────────────────────────────
Following Chip Huyen AI Engineering (O'Reilly 2025) Ch. 5:
  "Separate prompts from code — version them, add metadata, allow search."

Each prompt is a PromptRecord with:
  - name: unique identifier
  - version: semver string
  - model_name: which model this was tuned for (or "all")
  - date_created: ISO date string
  - description: one-line purpose
  - prompt_text: the actual prompt string
  - tags: searchable categories
  - temperature: recommended temperature (0.0 for deterministic)
  - changelog: list of changes per version

The active system prompt is loaded from the catalog at runtime.
To update the system prompt: add a new version entry, bump version number.
Previous versions are retained for rollback and A/B testing.
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PromptRecord:
    name:         str
    version:      str
    model_name:   str
    date_created: str
    description:  str
    prompt_text:  str
    tags:         List[str]
    temperature:  float = 0.0
    changelog:    List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name":         self.name,
            "version":      self.version,
            "model_name":   self.model_name,
            "date_created": self.date_created,
            "description":  self.description,
            "tags":         self.tags,
            "temperature":  self.temperature,
            "changelog":    self.changelog,
            "prompt_length_chars": len(self.prompt_text),
        }


# ── Prompt Registry ────────────────────────────────────────────────────────────

class PromptRegistry:
    """
    Catalog of all prompt versions. Supports lookup by name, version, tag.
    Thread-safe singleton per process.
    """

    _registry: List[PromptRecord] = []

    @classmethod
    def register(cls, record: PromptRecord) -> None:
        cls._registry.append(record)

    @classmethod
    def get(cls, name: str, version: str = "latest") -> Optional[PromptRecord]:
        matches = [r for r in cls._registry if r.name == name]
        if not matches:
            return None
        if version == "latest":
            # Return highest version (simple string sort works for semver x.y.z)
            return sorted(matches, key=lambda r: r.version)[-1]
        for r in matches:
            if r.version == version:
                return r
        return None

    @classmethod
    def list_prompts(cls) -> List[Dict[str, Any]]:
        """Return metadata for all registered prompts (latest version per name)."""
        seen: Dict[str, PromptRecord] = {}
        for r in cls._registry:
            if r.name not in seen or r.version > seen[r.name].version:
                seen[r.name] = r
        return [r.to_dict() for r in seen.values()]

    @classmethod
    def search(cls, tag: str) -> List[PromptRecord]:
        return [r for r in cls._registry if tag.lower() in [t.lower() for t in r.tags]]

    @classmethod
    def rollback(cls, name: str, target_version: str) -> Optional[PromptRecord]:
        """Retrieve a previous version for rollback or A/B testing."""
        return cls.get(name, target_version)


# ── Register all prompt versions ───────────────────────────────────────────────

PromptRegistry.register(PromptRecord(
    name         = "dwsim_agent_system",
    version      = "1.0.0",
    model_name   = "all",
    date_created = "2026-04-01",
    description  = "Initial DWSIM agent system prompt",
    prompt_text  = "[v1.0.0 - original prompt without ChE structured reasoning]",
    tags         = ["system", "dwsim", "agent", "base"],
    changelog    = ["Initial release"],
))

PromptRegistry.register(PromptRecord(
    name         = "dwsim_agent_system",
    version      = "1.1.0",
    model_name   = "all",
    date_created = "2026-04-20",
    description  = "Added SF taxonomy, safety validator integration",
    prompt_text  = "[v1.1.0 - added safety validator context]",
    tags         = ["system", "dwsim", "agent", "safety"],
    changelog    = ["Added SF-01 through SF-09 silent failure guidance",
                    "Added save_and_solve workflow instructions"],
))

PromptRegistry.register(PromptRecord(
    name         = "dwsim_agent_system",
    version      = "1.2.0",
    model_name   = "all",
    date_created = "2026-05-01",
    description  = "Added Bayesian optimizer, proactive RAG, replay log",
    prompt_text  = "[v1.2.0 - added optimization and RAG guidance]",
    tags         = ["system", "dwsim", "agent", "rag", "optimization"],
    changelog    = ["Added Bayesian optimization tool guidance",
                    "Added proactive RAG injection context",
                    "Added replay log for reproducibility"],
))

PromptRegistry.register(PromptRecord(
    name         = "dwsim_agent_system",
    version      = "1.3.0",
    model_name   = "all",
    date_created = "2026-05-07",
    description  = "Full production prompt: mandatory lookup workflow, ChE reasoning order, ReAct pattern, tool state machine",
    prompt_text  = "ACTIVE",   # Sentinel: actual text lives in agent_v2.BASE_SYSTEM_PROMPT
    tags         = ["system", "dwsim", "agent", "rag", "optimization",
                    "structured-reasoning", "react", "tool-ordering"],
    temperature  = 0.0,
    changelog    = ["Added MANDATORY WORKFLOW requiring compound property lookup before flowsheet creation",
                    "Added STRUCTURED REASONING FOR PROCESS ENGINEERING (mass->energy->equipment->optimize)",
                    "Added DISTILLATION COLUMN INITIALIZATION systematic procedure",
                    "Added CONVERGENCE TROUBLESHOOTING checklist",
                    "Added REACT REASONING PATTERN (Reason->Act->Observe->Reason)",
                    "Added tool call state machine blocking out-of-order DWSIM calls",
                    "Applied Chip Huyen AI Engineering book best practices"],
))

# Register the knowledge base search prompt (used for proactive RAG injection)
PromptRegistry.register(PromptRecord(
    name         = "kb_proactive_injection",
    version      = "1.1.0",
    model_name   = "all",
    date_created = "2026-05-07",
    description  = "Template for proactive RAG context injection into system prompt",
    prompt_text  = (
        "\n\nRELEVANT KNOWLEDGE BASE CONTEXT (auto-retrieved)\n"
        "─────────────────────────────────────────────────\n"
        "[{title}] (relevance={score:.1f})\n{text}\n\n"
        "Call search_knowledge for deeper retrieval on specific sub-topics."
    ),
    tags         = ["rag", "injection", "knowledge-base", "proactive"],
    changelog    = ["v1.0.0: Initial proactive injection",
                    "v1.1.0: Raised relevance threshold to 0.5 BM25 scale"],
))

# Register the convergence diagnosis hint prompt
PromptRegistry.register(PromptRecord(
    name         = "convergence_diagnosis",
    version      = "1.0.0",
    model_name   = "all",
    date_created = "2026-05-07",
    description  = "Auto-generated convergence diagnosis hint appended to failed save_and_solve results",
    prompt_text  = (
        "Auto-diagnosis after convergence failure:\n"
        "  Failed streams: {failed_streams}\n"
        "  Failed unit-ops: {failed_unit_ops}\n"
        "  Suggested fixes: check missing T/P/flow specs, verify property package, "
        "check disconnected streams, ensure stream flash specs are set."
    ),
    tags         = ["convergence", "diagnosis", "error-recovery", "safety"],
    changelog    = ["Initial release — auto-diagnosis on save_and_solve failure"],
))


def get_active_system_prompt_meta() -> Dict[str, Any]:
    """Return metadata for the currently active system prompt version."""
    rec = PromptRegistry.get("dwsim_agent_system", "latest")
    if rec:
        return rec.to_dict()
    return {"error": "No system prompt registered"}


def list_all_prompts() -> List[Dict[str, Any]]:
    """Return metadata for all registered prompts."""
    return PromptRegistry.list_prompts()
