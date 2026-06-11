"""
recycle_analyzer.py
───────────────────
Graph analysis of a flowsheet topology to make recycle (feedback-loop)
construction robust — the #1 reason free-built COMPLEX flowsheets fail to
converge.

The problem
-----------
DWSIM is a sequential-modular solver: it cannot solve an unbroken algebraic
loop. A recycle loop must be "torn" by an `OT_Recycle` logical block, which
holds a guessed stream and iterates (Wegstein/Broyden) until the loop closes.
Templates wire this correctly; the LLM free-build path often connects a
downstream stream back to an upstream unit WITHOUT a recycle block, leaving an
untorn loop that simply will not converge.

The fix (this module)
---------------------
Build the directed connection graph (networkx), find every cycle, and check
whether each already contains an `OT_Recycle` block. For cycles that don't,
choose a tear edge (a material-stream → unit edge, preferring the loop-closing
mixer) and emit an insertion plan. The builder then splices an `OT_Recycle`
block onto that edge — exactly the topology the reactor_recycle template uses:

    … → UnconvStream → [OT_Recycle] → RecycleStream → Mixer(loop close) → …

This is the recycle analogue of the energy-stream auto-injection: it guarantees
the loop is STRUCTURALLY solvable. (Numerical convergence still benefits from a
good initial guess — see bridge.initialize_recycle — but an untorn loop never
converges at all.)

Import-guarded on networkx; callers fall back to a no-op when it is absent.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("recycle_analyzer")

_MAX_CYCLES = 200   # safety cap; real flowsheets have very few


def networkx_available() -> bool:
    try:
        import networkx  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def _resolve(type_str: str) -> str:
    try:
        from flowsheet_builder import _resolve_type
        return _resolve_type(type_str) or ""
    except Exception:
        return (type_str or "").strip()


def _type_maps(streams, unit_ops):
    """tag → resolved type, plus the set of unit-op tags."""
    types: Dict[str, str] = {}
    for s in streams:
        if isinstance(s, dict) and s.get("tag"):
            types[s["tag"]] = _resolve(s.get("type", "MaterialStream")) or "MaterialStream"
    unit_tags = set()
    for u in unit_ops:
        if isinstance(u, dict) and u.get("tag"):
            types[u["tag"]] = _resolve(u.get("type", ""))
            unit_tags.add(u["tag"])
    return types, unit_tags


def find_recycle_loops(streams, unit_ops, connections) -> Dict[str, Any]:
    """Return {cycles, untorn, recycle_units, available} describing feedback
    loops in the topology. `untorn` are cycles with no OT_Recycle block."""
    if not networkx_available():
        return {"available": False, "cycles": [], "untorn": [],
                "recycle_units": []}
    import networkx as nx

    types, _ = _type_maps(streams, unit_ops)
    recycle_units = {u["tag"] for u in unit_ops
                     if isinstance(u, dict)
                     and _resolve(u.get("type", "")) in ("OT_Recycle", "OT_EnergyRecycle")}

    G = nx.DiGraph()
    for c in connections:
        if not isinstance(c, dict):
            continue
        frm = c.get("from") or c.get("source")
        to = c.get("to") or c.get("target")
        if frm and to:
            G.add_edge(frm, to)

    cycles: List[List[str]] = []
    try:
        for i, cyc in enumerate(nx.simple_cycles(G)):
            if i >= _MAX_CYCLES:
                break
            cycles.append(list(cyc))
    except Exception as exc:
        _log.debug("simple_cycles failed: %s", exc)

    untorn = [c for c in cycles if not any(n in recycle_units for n in c)]
    return {"available": True, "cycles": cycles, "untorn": untorn,
            "recycle_units": sorted(recycle_units)}


def _cycle_edges(cycle: List[str]) -> List[Tuple[str, str]]:
    """Consecutive (a, b) edges of a cycle, wrapping the last back to the first."""
    return [(cycle[i], cycle[(i + 1) % len(cycle)]) for i in range(len(cycle))]


def plan_recycle_insertions(streams, unit_ops, connections) -> List[Dict[str, Any]]:
    """For each untorn cycle, pick ONE tear edge and describe the OT_Recycle to
    splice in. Greedy: once an edge is chosen, any other cycle that contains it
    is already torn and skipped.

    Each plan item: {tear_stream, consumer, consumer_port, rec_tag,
                     new_stream_tag}.
    """
    info = find_recycle_loops(streams, unit_ops, connections)
    if not info["available"] or not info["untorn"]:
        return []

    types, unit_tags = _type_maps(streams, unit_ops)
    existing = ({s.get("tag", "") for s in streams if isinstance(s, dict)}
                | {u.get("tag", "") for u in unit_ops if isinstance(u, dict)})

    # Map (from,to) -> to_port for rewiring.
    port_of: Dict[Tuple[str, str], int] = {}
    for c in connections:
        if isinstance(c, dict):
            frm = c.get("from") or c.get("source")
            to = c.get("to") or c.get("target")
            if frm and to:
                port_of[(frm, to)] = int(c.get("to_port", 0) or 0)

    def _is_material(tag: str) -> bool:
        return types.get(tag, "") == "MaterialStream"

    def _pick_tear(cycle: List[str]) -> Optional[Tuple[str, str]]:
        edges = [(a, b) for (a, b) in _cycle_edges(cycle)
                 if _is_material(a) and b in unit_tags]
        if not edges:
            return None
        # Prefer the loop-closing join (stream feeding a Mixer).
        for a, b in edges:
            if types.get(b, "") == "Mixer":
                return (a, b)
        return edges[0]

    smap = {s.get("tag"): s for s in streams if isinstance(s, dict) and s.get("tag")}
    _cond_keys = ("T", "T_C", "T_K", "temperature", "P", "P_bar", "P_Pa",
                  "pressure", "compositions", "composition",
                  "molar_flow", "mass_flow", "flow_kmol_h", "flow_kg_h")

    def _seed_source(tear_stream: str) -> Optional[str]:
        """Pick a stream to copy initial conditions from onto the new tear
        stream — gives DWSIM's Wegstein/Broyden iteration a physical starting
        guess instead of an empty stream. Prefer the tear stream's own spec;
        else the first composition-bearing feed."""
        ts = smap.get(tear_stream, {})
        if any(k in ts for k in _cond_keys):
            return tear_stream
        for s in streams:
            if isinstance(s, dict) and ("compositions" in s or "composition" in s):
                return s.get("tag")
        return None

    plans: List[Dict[str, Any]] = []
    torn_edges: set = set()
    n = 1
    for cycle in info["untorn"]:
        if any(e in torn_edges for e in _cycle_edges(cycle)):
            continue   # already broken by a previously planned tear
        tear = _pick_tear(cycle)
        if tear is None:
            continue
        tear_stream, consumer = tear
        rec_tag = f"REC-{n:02d}"
        while rec_tag in existing:
            n += 1
            rec_tag = f"REC-{n:02d}"
        existing.add(rec_tag)
        new_stream = f"{tear_stream}_rec"
        k = 2
        while new_stream in existing:
            new_stream = f"{tear_stream}_rec{k}"
            k += 1
        existing.add(new_stream)

        plans.append({
            "tear_stream":   tear_stream,
            "consumer":      consumer,
            "consumer_port": port_of.get((tear_stream, consumer), 0),
            "rec_tag":       rec_tag,
            "new_stream_tag": new_stream,
            "seed_from":     _seed_source(tear_stream),
        })
        torn_edges.add(tear)
        n += 1
    return plans
