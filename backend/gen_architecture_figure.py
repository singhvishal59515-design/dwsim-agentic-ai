#!/usr/bin/env python3
"""
Generate the corrected system-architecture figure (PNG) for the thesis/paper.

Fixes the data-flow errors in the earlier diagram:
  - the UI/MCP clients talk ONLY to the FastAPI backend (not the agent directly);
  - the LLM providers are called by the Agent core's llm_client (not the backend);
  - the server-side subsystems (agent, orchestrators, optimizer stack, thermo
    intelligence) are explicit and all reach the DWSIM bridge.

    python gen_architecture_figure.py   ->  architecture.png
"""
from __future__ import annotations
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

_HERE = os.path.dirname(os.path.abspath(__file__))

# colour palette (close to the original)
C_CLIENT = "#FDE9C8"; E_CLIENT = "#E69138"      # yellow/orange
C_API    = "#CFE2F3"; E_API    = "#3D6EB5"      # blue
C_SVR    = "#D9EAD3"; E_SVR    = "#6AA84F"      # green
C_ENG    = "#F9CBAD"; E_ENG    = "#E0703A"      # orange
C_LLM    = "#FDE9C8"; E_LLM    = "#E69138"

# box registry: name -> (cx, cy, w, h, text, face, edge)
BOXES = {
    "browser": (2.1, 9.6, 2.5, 0.95, "User\n(Browser)", C_CLIENT, E_CLIENT),
    "mcp":     (2.1, 8.05, 2.5, 0.85, "MCP Client\n(Claude / IDE)", C_CLIENT, E_CLIENT),
    "chatui":  (5.4, 9.6, 2.7, 0.95, "Chat UI\n(HTML / React, SSE)", C_API, E_API),
    "api":     (9.0, 9.6, 3.3, 0.95, "FastAPI Backend\nREST · SSE · WebSocket · auth", C_API, E_API),
    "llm":     (13.9, 9.6, 3.0, 1.05,
                "LLM Providers (×4 + failover)\ngroq · openai · anthropic · ollama",
                C_LLM, E_LLM),
    "agent":   (5.0, 6.7, 5.0, 1.7,
                "Agent Core  (agent_v2)\nReAct loop · dynamic tool select (107 tools)\n"
                "RAG (BM25 KB) · SafetyValidator\nquality guard + LLM-as-judge · replay log",
                C_SVR, E_SVR),
    "orch":    (11.6, 6.7, 4.8, 1.7,
                "Orchestrators\nOptimisation / Build-plan / Complex", C_SVR, E_SVR),
    "thermo":  (5.0, 4.25, 5.0, 1.25,
                "Thermodynamic Intelligence\nPP registry (28 packages → Aspen)\n"
                "auto-select · multi-model uncertainty", C_SVR, E_SVR),
    "opt":     (11.6, 4.25, 4.8, 1.25,
                "Optimizer Stack\nCMA-ES · NSGA-II · NLopt · trust-region EO\nSALib · TAC",
                C_SVR, E_SVR),
    "bridge":  (8.3, 2.1, 11.2, 1.15,
                "DWSIM Bridge  (pythonnet)\nbuild/solve · recycle auto-tear · "
                "energy-stream injection · read-back verify", C_ENG, E_ENG),
    "engine":  (8.3, 0.55, 11.2, 0.85,
                "DWSIM Engine  —  FlowsheetSolver + DotNumerics", C_ENG, E_ENG),
}


def _edge(name, side):
    cx, cy, w, h, *_ = BOXES[name]
    return {"top": (cx, cy + h / 2), "bottom": (cx, cy - h / 2),
            "left": (cx - w / 2, cy), "right": (cx + w / 2, cy)}[side]


def main() -> int:
    fig, ax = plt.subplots(figsize=(15.5, 10.5))
    ax.set_xlim(0, 16); ax.set_ylim(0, 10.6); ax.axis("off")

    for cx, cy, w, h, text, face, edge in BOXES.values():
        ax.add_patch(FancyBboxPatch(
            (cx - w / 2, cy - h / 2), w, h,
            boxstyle="round,pad=0.02,rounding_size=0.08",
            facecolor=face, edgecolor=edge, linewidth=1.8))
        ax.text(cx, cy, text, ha="center", va="center", fontsize=8.6,
                weight="bold", color="#1a1a1a")

    def arrow(a, sa, b, sb, color="#5a5a5a", style="-|>", lw=1.8, rad=0.0):
        ax.add_patch(FancyArrowPatch(
            _edge(a, sa), _edge(b, sb), arrowstyle=style,
            mutation_scale=16, color=color, linewidth=lw,
            shrinkA=2, shrinkB=2, connectionstyle=f"arc3,rad={rad}"))

    # client → backend (only path in)
    arrow("browser", "right", "chatui", "left")
    arrow("chatui", "right", "api", "left")
    arrow("mcp", "right", "api", "left", color="#888888")
    # backend → server-side subsystems
    arrow("api", "bottom", "agent", "top")
    arrow("api", "bottom", "orch", "top")
    # agent → LLM (the corrected LLM call path) — bow it up over the top so it
    # reads cleanly as "the Agent calls the providers", not the backend.
    arrow("agent", "top", "llm", "left", color="#E0703A", lw=2.2, rad=-0.28)
    ax.text(9.4, 8.35, "llm_client\nfailover", ha="center", va="center",
            fontsize=7.0, style="italic", color="#E0703A")
    # agent ↔ orchestrators
    arrow("agent", "right", "orch", "left", color="#888888")
    # capability layer
    arrow("orch", "bottom", "opt", "top")
    arrow("agent", "bottom", "thermo", "top")
    # everything that touches the engine → bridge
    arrow("thermo", "bottom", "bridge", "top")
    arrow("opt", "bottom", "bridge", "top")
    arrow("orch", "bottom", "bridge", "top", color="#888888")
    # bridge → engine
    arrow("bridge", "bottom", "engine", "top", color="#E0703A", lw=2.2)

    ax.set_title("Agentic AI for Process Flowsheet Design & Optimization in DWSIM "
                 "— System Architecture", fontsize=13, weight="bold", pad=12)
    out = os.path.join(_HERE, "architecture.png")
    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
    print(f"[arch] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
