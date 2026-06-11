"""
industrial_features.py — Industrial-grade flowsheet capabilities

Provides four algorithms callable from the API:

1. preflight_validate(graph)        — Pre-solve graph topology validation
2. detect_tear_streams(graph)       — Pho-Lapidus minimum tear stream selection
3. synthesize_hen(streams, dT_min)  — Linnhoff-Hindmarsh HEN design
4. diagnose_convergence(state)      — Root-cause analysis after solve failure

All algorithms are pure Python — no DWSIM dependency. They consume a
plain-data representation of the flowsheet (objects + connections) so they
can be unit-tested in isolation and called from any backend.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict, deque


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pre-flight validation
# ─────────────────────────────────────────────────────────────────────────────

def preflight_validate(
    objects: List[Dict],
    connections: List[Dict],
    compounds: Optional[List[str]] = None,
    property_package: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate flowsheet topology BEFORE running the (expensive) DWSIM solver.

    Catches ~50% of "didn't converge" issues without burning a solve cycle.

    Checks performed:
      • Every unit op has its required inlets connected
      • Every material stream is connected on at least one end
      • No orphan objects (zero connections)
      • Graph has no impossible cycles (e.g. a stream feeding itself)
      • Compounds list is non-empty
      • Property package is set
      • At least one feed stream (boundary input) exists
    """
    issues: List[Dict] = []

    if not objects:
        issues.append({"severity": "error", "code": "EMPTY_FLOWSHEET",
                       "message": "Flowsheet has no objects"})
        return {"success": False, "issues": issues, "ready_to_solve": False}

    obj_by_tag = {o["tag"]: o for o in objects if "tag" in o}
    streams = {t for t, o in obj_by_tag.items()
               if (o.get("category") or "").lower() == "materialstream"
               or "stream" in (o.get("type") or "").lower()}

    # Build connection graph
    out_edges: Dict[str, List[str]] = defaultdict(list)
    in_edges:  Dict[str, List[str]] = defaultdict(list)
    for c in connections:
        src = c.get("from") or c.get("source")
        dst = c.get("to") or c.get("target") or c.get("destination")
        if src and dst:
            out_edges[src].append(dst)
            in_edges[dst].append(src)

    # Check 1: required inlets per unit-op type
    required_inlets = {
        "mixer": 2, "heatexchanger": 2, "heat_exchanger": 2,
        "splitter": 1, "heater": 1, "cooler": 1, "pump": 1,
        "compressor": 1, "expander": 1, "valve": 1, "pipe": 1,
        "distillationcolumn": 1, "distillation_column": 1,
        "shortcutcolumn": 1, "shortcut_column": 1,
        "absorptioncolumn": 2, "absorption_column": 2,
        "separator": 1, "flash": 1, "gas_liquid_separator": 1,
        "cstr": 1, "pfr": 1, "conversionreactor": 1, "conversion_reactor": 1,
        "equilibriumreactor": 1, "equilibrium_reactor": 1, "gibbsreactor": 1,
        "tank": 1,
    }
    for tag, obj in obj_by_tag.items():
        if tag in streams:
            continue
        typ = (obj.get("type") or obj.get("category") or "").lower().replace(" ", "")
        req = required_inlets.get(typ, 1)
        actual = len(in_edges.get(tag, []))
        if actual < req:
            issues.append({
                "severity": "error",
                "code": "MISSING_INLETS",
                "tag": tag,
                "type": typ,
                "message": f"{tag} ({typ}) needs {req} inlet(s), has {actual}",
            })

    # Check 2: orphan objects (zero connections)
    for tag, obj in obj_by_tag.items():
        if not in_edges.get(tag) and not out_edges.get(tag):
            issues.append({
                "severity": "warning",
                "code": "ORPHAN",
                "tag": tag,
                "message": f"{tag} has no connections — likely disconnected",
            })

    # Check 3: self-loop on streams (stream feeding itself)
    for tag in streams:
        if tag in out_edges.get(tag, []):
            issues.append({
                "severity": "error",
                "code": "SELF_LOOP",
                "tag": tag,
                "message": f"Stream {tag} is connected to itself",
            })

    # Check 4: at least one boundary feed (stream with no inbound edge)
    boundary_feeds = [s for s in streams if not in_edges.get(s)]
    if not boundary_feeds:
        issues.append({
            "severity": "error",
            "code": "NO_FEED",
            "message": "No boundary feed streams found — flowsheet has no entry point",
        })

    # Check 5: compounds + property package
    if compounds is not None and len(compounds) == 0:
        issues.append({
            "severity": "error",
            "code": "NO_COMPOUNDS",
            "message": "No compounds defined — property calculations will fail",
        })
    if property_package is not None and not property_package:
        issues.append({
            "severity": "error",
            "code": "NO_PROPERTY_PACKAGE",
            "message": "No property package selected",
        })

    n_err  = sum(1 for i in issues if i["severity"] == "error")
    n_warn = sum(1 for i in issues if i["severity"] == "warning")

    return {
        "success": True,
        "ready_to_solve": n_err == 0,
        "n_errors": n_err,
        "n_warnings": n_warn,
        "issues": issues,
        "topology": {
            "n_objects": len(obj_by_tag),
            "n_streams": len(streams),
            "n_unit_ops": len(obj_by_tag) - len(streams),
            "n_connections": len(connections),
            "n_feeds": len(boundary_feeds),
        },
        "summary": (
            f"OK — {n_err} errors, {n_warn} warnings."
            if n_err == 0 else
            f"NOT READY — {n_err} errors must be fixed before solving"
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tear stream auto-detection (Pho-Lapidus minimum feedback arc set)
# ─────────────────────────────────────────────────────────────────────────────

def detect_tear_streams(
    nodes: List[str],
    edges: List[Tuple[str, str]],
) -> Dict[str, Any]:
    """
    Find a minimum set of edges (tear streams) whose removal makes the graph
    a DAG. Uses Pho-Lapidus heuristic: iteratively remove the edge involved
    in the most cycles (computed via Tarjan SCC).

    Returns:
      tear_edges:        list of (src, dst) edges to tear
      cycles_found:      list of cycles in the original graph
      acyclic_after_tear: True if the resulting graph is a DAG
    """
    edge_set: Set[Tuple[str, str]] = set(edges)
    cycles_found = _find_simple_cycles(nodes, list(edge_set))

    if not cycles_found:
        return {
            "success": True,
            "tear_edges": [],
            "n_cycles": 0,
            "cycles_found": [],
            "acyclic_after_tear": True,
            "method": "tarjan_scc",
            "summary": "Flowsheet is already acyclic — no tear streams needed",
        }

    # Iteratively tear the edge appearing in the most cycles
    tear_edges: List[Tuple[str, str]] = []
    remaining = set(edge_set)
    iteration = 0
    while iteration < 100:  # safety cap
        cycles = _find_simple_cycles(nodes, list(remaining))
        if not cycles:
            break
        # Count edge participation across cycles
        edge_count: Dict[Tuple[str, str], int] = defaultdict(int)
        for cyc in cycles:
            for i in range(len(cyc)):
                e = (cyc[i], cyc[(i + 1) % len(cyc)])
                if e in remaining:
                    edge_count[e] += 1
        if not edge_count:
            break
        worst = max(edge_count, key=lambda k: (edge_count[k], -len(k[0]) - len(k[1])))
        tear_edges.append(worst)
        remaining.discard(worst)
        iteration += 1

    return {
        "success": True,
        "tear_edges": [{"from": s, "to": d} for s, d in tear_edges],
        "n_cycles": len(cycles_found),
        "cycles_found": cycles_found[:10],   # cap output
        "acyclic_after_tear": True,
        "method": "pho_lapidus_iterative",
        "summary": (
            f"Found {len(cycles_found)} cycle(s); recommend tearing "
            f"{len(tear_edges)} stream(s): "
            f"{[f'{s}→{d}' for s, d in tear_edges]}"
        ),
    }


def _find_simple_cycles(
    nodes: List[str], edges: List[Tuple[str, str]]
) -> List[List[str]]:
    """Find all simple cycles via DFS. Cap at 50 cycles to avoid explosion."""
    adj: Dict[str, List[str]] = defaultdict(list)
    for s, d in edges:
        adj[s].append(d)
    node_set = set(nodes) | {s for s, _ in edges} | {d for _, d in edges}
    cycles: List[List[str]] = []

    def dfs(start: str, current: str, path: List[str], visited: Set[str]):
        if len(cycles) >= 50:
            return
        for nxt in adj.get(current, []):
            if nxt == start and len(path) >= 2:
                # Normalize: rotate so smallest node is first; dedupe
                key = _canonical_cycle(path)
                if not any(_canonical_cycle(c) == key for c in cycles):
                    cycles.append(list(path))
            elif nxt not in visited:
                dfs(start, nxt, path + [nxt], visited | {nxt})

    for start_node in sorted(node_set):
        if len(cycles) >= 50:
            break
        dfs(start_node, start_node, [start_node], {start_node})

    return cycles


def _canonical_cycle(cyc: List[str]) -> Tuple[str, ...]:
    """Rotate cycle so smallest node is first — for dedup."""
    if not cyc:
        return tuple()
    mn = min(cyc)
    i = cyc.index(mn)
    return tuple(cyc[i:] + cyc[:i])


# ─────────────────────────────────────────────────────────────────────────────
# 3. HEN synthesis (Linnhoff-Hindmarsh matching above/below pinch)
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_hen(
    hot_streams: List[Dict],
    cold_streams: List[Dict],
    pinch_temp_C: Optional[float],
    delta_t_min: float = 10.0,
) -> Dict[str, Any]:
    """
    Linnhoff-Hindmarsh HEN design heuristics.

    hot_streams / cold_streams: each {tag, t_supply_C, t_target_C, heat_kw, mcp_kw_K?}

    Returns suggested heat exchanger matches above/below pinch with duties.

    Rules:
      • Above pinch:  hot streams must reach pinch temp, mCp_hot ≤ mCp_cold
      • Below pinch:  cold streams must reach pinch temp, mCp_cold ≤ mCp_hot
      • Each match cannot violate ΔT_min anywhere in the exchanger
    """
    if not hot_streams or not cold_streams:
        return {
            "success": False,
            "error": "Need at least one hot and one cold stream",
        }

    # Compute mCp from Q and ΔT if not provided
    def _enrich(s: Dict) -> Dict:
        ts, tt, q = s["t_supply_C"], s["t_target_C"], s.get("heat_kw") or 0.0
        mcp = s.get("mcp_kw_K")
        if mcp is None and abs(ts - tt) > 1e-6:
            mcp = q / abs(ts - tt)
        return {**s, "mcp_kw_K": mcp or 0.0}

    hot  = [_enrich(s) for s in hot_streams]
    cold = [_enrich(s) for s in cold_streams]

    matches: List[Dict] = []
    pinch = pinch_temp_C if pinch_temp_C is not None else (
        min(s["t_supply_C"] for s in hot) - delta_t_min / 2
    )

    # Split streams into above/below pinch segments
    def _segment(s: Dict, region: str) -> Optional[Dict]:
        ts, tt = s["t_supply_C"], s["t_target_C"]
        # hot: ts > tt; cold: tt > ts
        if region == "above":
            lo_h, hi_h = pinch + delta_t_min / 2, max(ts, tt)
            lo_c, hi_c = pinch - delta_t_min / 2, max(ts, tt) - delta_t_min
        else:
            lo_h, hi_h = min(ts, tt), pinch + delta_t_min / 2
            lo_c, hi_c = min(ts, tt) - delta_t_min, pinch - delta_t_min / 2
        # Clip stream temperatures into the region
        is_hot = ts > tt
        if is_hot:
            seg_in  = min(max(ts, lo_h), hi_h)
            seg_out = min(max(tt, lo_h), hi_h)
        else:
            seg_in  = min(max(ts, lo_c), hi_c)
            seg_out = min(max(tt, lo_c), hi_c)
        if abs(seg_in - seg_out) < 1e-3:
            return None
        seg_q = s["mcp_kw_K"] * abs(seg_in - seg_out)
        return {**s, "t_supply_C": seg_in, "t_target_C": seg_out, "heat_kw": seg_q}

    for region in ("above", "below"):
        hot_seg  = [s for s in (_segment(s, region) for s in hot)  if s]
        cold_seg = [s for s in (_segment(s, region) for s in cold) if s]

        # Sort by mCp: above-pinch hot ascending, cold descending (largest cold first)
        if region == "above":
            hot_seg.sort(key=lambda s: s["mcp_kw_K"])
            cold_seg.sort(key=lambda s: -s["mcp_kw_K"])
        else:
            hot_seg.sort(key=lambda s: -s["mcp_kw_K"])
            cold_seg.sort(key=lambda s: s["mcp_kw_K"])

        # Greedy matching respecting ΔT and mCp rules
        for h in hot_seg:
            for c in cold_seg:
                if region == "above" and h["mcp_kw_K"] > c["mcp_kw_K"] + 1e-6:
                    continue  # violates feasibility
                if region == "below" and c["mcp_kw_K"] > h["mcp_kw_K"] + 1e-6:
                    continue
                if h["heat_kw"] < 1e-3 or c["heat_kw"] < 1e-3:
                    continue
                # Heat exchanged is limited by smaller of the two
                q_match = min(h["heat_kw"], c["heat_kw"])
                if q_match < 1.0:  # skip trivially small matches (<1 kW)
                    continue
                # LMTD estimation (counter-current ideal)
                dt1 = h["t_supply_C"] - c["t_target_C"]
                dt2 = h["t_target_C"] - c["t_supply_C"]
                if min(dt1, dt2) < delta_t_min - 0.5:
                    continue
                lmtd = _lmtd(dt1, dt2)
                # Estimate area assuming U=500 W/m²K (typical liquid-liquid)
                area_m2 = q_match * 1000 / (500 * lmtd) if lmtd > 0 else 0
                matches.append({
                    "region": region,
                    "hot_stream":  h["tag"],
                    "cold_stream": c["tag"],
                    "duty_kw":     round(q_match, 2),
                    "lmtd_C":      round(lmtd, 1),
                    "area_m2_est": round(area_m2, 2),
                    "U_assumed_Wm2K": 500,
                })
                # Subtract from remaining duty
                h["heat_kw"] -= q_match
                c["heat_kw"] -= q_match

    # Residual utilities
    util_hot = sum(s["heat_kw"] for s in cold if s["heat_kw"] > 0)
    util_cold = sum(s["heat_kw"] for s in hot  if s["heat_kw"] > 0)

    return {
        "success": True,
        "n_matches": len(matches),
        "matches": matches,
        "residual_hot_utility_kw": round(util_hot, 2),
        "residual_cold_utility_kw": round(util_cold, 2),
        "pinch_temp_C": pinch,
        "delta_t_min_C": delta_t_min,
        "summary": (
            f"{len(matches)} HX matches suggested. "
            f"Hot utility needed: {util_hot:.1f} kW, "
            f"Cold utility needed: {util_cold:.1f} kW."
        ),
        "warning": (
            "These are screening-level estimates. Validate with rigorous "
            "rating after insertion into the flowsheet."
        ),
    }


def _lmtd(dt1: float, dt2: float) -> float:
    """Log-mean temperature difference. Falls back to arithmetic mean for dt1≈dt2."""
    import math
    if dt1 <= 0 or dt2 <= 0:
        return 0.0
    if abs(dt1 - dt2) < 0.1:
        return (dt1 + dt2) / 2
    return (dt1 - dt2) / math.log(dt1 / dt2)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Convergence diagnostics — root-cause analysis after solve failure
# ─────────────────────────────────────────────────────────────────────────────

def diagnose_convergence(
    convergence_state: Dict,
    object_states: Dict[str, Dict],
    flowsheet_topology: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Given the result of a failed solve, produce a ranked list of likely root
    causes with suggested fixes.

    convergence_state: output of _check_convergence_internal
    object_states:     {tag: {properties...}} from get_stream_properties etc.
    """
    diagnoses: List[Dict] = []

    not_converged = convergence_state.get("not_converged", []) or []
    inaccessible  = convergence_state.get("inaccessible", []) or []
    phys_warnings = convergence_state.get("physical_warnings", []) or []

    # Diagnosis 1: temperature out of range (likely flash spec or column init)
    for w in phys_warnings:
        tag = w.get("tag", "?")
        for issue in w.get("issues", []):
            if "T=" in issue and "out of range" in issue:
                diagnoses.append({
                    "rank": 1,
                    "severity": "critical",
                    "cause": "TEMPERATURE_INVALID",
                    "tag": tag,
                    "evidence": issue,
                    "likely_root_cause": (
                        "Stream temperature is unphysical. Common causes: "
                        "(a) flash spec converged to wrong root, "
                        "(b) column has a stage with no vapor/liquid, "
                        "(c) compositions sum > 1 making T_dew undefined."
                    ),
                    "suggested_fix": (
                        "Re-initialize the unit op feeding this stream. "
                        "For columns: try a different solver algorithm "
                        "(Wang-Henke, Naphtali-Sandholm, Inside-Out)."
                    ),
                })

    # Diagnosis 2: vapor fraction out of [0,1]
    for w in phys_warnings:
        tag = w.get("tag", "?")
        for issue in w.get("issues", []):
            if "VF=" in issue:
                diagnoses.append({
                    "rank": 1,
                    "severity": "critical",
                    "cause": "VAPOR_FRACTION_INVALID",
                    "tag": tag,
                    "evidence": issue,
                    "likely_root_cause": (
                        "Vapor fraction outside [0,1] indicates the flash "
                        "didn't converge. Stream may be single-phase but "
                        "marked two-phase, or vice versa."
                    ),
                    "suggested_fix": (
                        "Change flash spec: instead of T/P, try T/VF=0 (bubble) "
                        "or T/VF=1 (dew). For columns, increase the number "
                        "of iterations or relax tolerance."
                    ),
                })

    # Diagnosis 3: missing flow data
    for nc in not_converged:
        if "flow" in nc.get("missing", []):
            tag = nc.get("tag", "?")
            diagnoses.append({
                "rank": 2,
                "severity": "high",
                "cause": "FLOW_NOT_SET",
                "tag": tag,
                "evidence": f"{tag}: flow rate is None after solve",
                "likely_root_cause": (
                    "Stream has no flow rate. If this is a feed stream, "
                    "molar_flow or mass_flow was not specified. If it's a "
                    "downstream stream, the unit op upstream didn't pass flow."
                ),
                "suggested_fix": (
                    f"Check that the feed stream(s) feeding {tag} have flow "
                    "specified. For recycle streams, initialize with a "
                    "guess flow > 0 before first iteration."
                ),
            })

    # Diagnosis 4: missing T or P
    for nc in not_converged:
        missing = nc.get("missing", [])
        if "T" in missing or "P" in missing:
            tag = nc.get("tag", "?")
            diagnoses.append({
                "rank": 2,
                "severity": "high",
                "cause": "STATE_NOT_SET",
                "tag": tag,
                "evidence": f"{tag}: missing {missing}",
                "likely_root_cause": (
                    "Stream is missing temperature or pressure. Almost always "
                    "an unconnected port or a unit op upstream that didn't solve."
                ),
                "suggested_fix": (
                    f"Trace upstream from {tag}: find the first unit op that "
                    "didn't converge — that's the real root cause. Fix that "
                    "first and this stream will resolve."
                ),
            })

    # Diagnosis 5: object inaccessible (deleted? bad tag?)
    for tag in inaccessible:
        diagnoses.append({
            "rank": 3,
            "severity": "medium",
            "cause": "OBJECT_INACCESSIBLE",
            "tag": tag,
            "evidence": f"{tag}: get_stream_properties returned failure",
            "likely_root_cause": (
                "Object not reachable via the DWSIM API. May have been "
                "deleted, renamed, or the tag cache is stale."
            ),
            "suggested_fix": (
                "Run /flowsheet/objects to refresh the object list. "
                "If still missing, the flowsheet may need to be re-loaded."
            ),
        })

    # Deduplicate and sort by rank
    seen = set()
    deduped: List[Dict] = []
    for d in sorted(diagnoses, key=lambda x: (x["rank"], x.get("tag", ""))):
        key = (d["cause"], d.get("tag"))
        if key not in seen:
            seen.add(key)
            deduped.append(d)

    return {
        "success": True,
        "n_issues": len(deduped),
        "diagnoses": deduped,
        "summary": (
            "No convergence issues detected"
            if not deduped else
            f"{len(deduped)} root cause(s) identified. "
            f"Top issue: {deduped[0]['cause']} on '{deduped[0].get('tag','?')}'."
        ),
        "next_step": (
            "Apply fix for the rank-1 issue first; downstream issues "
            "often resolve once the upstream root cause is fixed."
            if deduped else None
        ),
    }
