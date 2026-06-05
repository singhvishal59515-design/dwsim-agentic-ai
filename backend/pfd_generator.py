"""
pfd_generator.py — Auto-generate Process Flow Diagrams as SVG.

No external dependencies (no graphviz install required). Uses a simple
layered layout (Sugiyama-style) computed in pure Python.

Public API:
    generate_pfd_svg(objects, connections, options) -> str

Each object: {tag, type, category}
Each conn:   {from, to, phase?}

Output: a self-contained SVG string ready to embed in HTML or save to file.
"""

from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict


# ─── Unit-op icons (compact SVG shapes) ──────────────────────────────────────
_ICONS = {
    "heater":         {"shape": "circle",   "fill": "#fb7185", "label": "HX"},
    "cooler":         {"shape": "circle",   "fill": "#38bdf8", "label": "CL"},
    "heat_exchanger": {"shape": "double_circle", "fill": "#fbbf24", "label": "HX"},
    "heatexchanger":  {"shape": "double_circle", "fill": "#fbbf24", "label": "HX"},
    "pump":           {"shape": "circle",   "fill": "#a78bfa", "label": "P"},
    "compressor":     {"shape": "trapezoid","fill": "#c084fc", "label": "K"},
    "expander":       {"shape": "rev_trapezoid","fill": "#7c3aed","label": "EX"},
    "valve":          {"shape": "bowtie",   "fill": "#94a3b8", "label": "V"},
    "mixer":          {"shape": "circle",   "fill": "#22c55e", "label": "M"},
    "splitter":       {"shape": "circle",   "fill": "#16a34a", "label": "S"},
    "separator":      {"shape": "pill",     "fill": "#0ea5e9", "label": "FL"},
    "flash":          {"shape": "pill",     "fill": "#0ea5e9", "label": "FL"},
    "gas_liquid_separator": {"shape": "pill", "fill": "#0ea5e9", "label": "FL"},
    "tank":           {"shape": "rect",     "fill": "#475569", "label": "TK"},
    "distillation_column":  {"shape": "tower", "fill": "#f59e0b", "label": "C"},
    "distillationcolumn":   {"shape": "tower", "fill": "#f59e0b", "label": "C"},
    "absorption_column":    {"shape": "tower", "fill": "#fbbf24", "label": "A"},
    "absorptioncolumn":     {"shape": "tower", "fill": "#fbbf24", "label": "A"},
    "shortcut_column":      {"shape": "tower", "fill": "#fb923c", "label": "C"},
    "cstr":           {"shape": "circle",   "fill": "#ef4444", "label": "R"},
    "pfr":            {"shape": "long_rect","fill": "#dc2626", "label": "R"},
    "conversionreactor":  {"shape": "circle", "fill": "#b91c1c", "label": "R"},
    "conversion_reactor": {"shape": "circle", "fill": "#b91c1c", "label": "R"},
    "equilibriumreactor": {"shape": "circle", "fill": "#991b1b", "label": "R"},
    "equilibrium_reactor":{"shape": "circle", "fill": "#991b1b", "label": "R"},
    "gibbsreactor":   {"shape": "circle",   "fill": "#7f1d1d", "label": "R"},
    "pipe":           {"shape": "line_seg", "fill": "#64748b", "label": "PI"},
    "recycle":        {"shape": "diamond",  "fill": "#a855f7", "label": "RC"},
    "materialstream": {"shape": "stream",   "fill": "#3b82f6", "label": ""},
    "energystream":   {"shape": "stream",   "fill": "#eab308", "label": ""},
}


def _icon_for(obj: Dict) -> Dict:
    """Pick icon by category/type."""
    cat = (obj.get("category") or "").lower()
    typ = (obj.get("type") or "").lower().replace(" ", "")
    if "materialstream" in cat or "materialstream" in typ:
        return _ICONS["materialstream"]
    if "energystream" in cat or "energystream" in typ:
        return _ICONS["energystream"]
    key = typ or cat
    return _ICONS.get(key, {"shape": "rect", "fill": "#6b7280", "label": typ[:2].upper() or "?"})


# ─── Sugiyama layered layout (simplified) ─────────────────────────────────────

def _topological_layers(
    nodes: List[str], edges: List[Tuple[str, str]]
) -> Dict[str, int]:
    """Assign each node a layer (longest path from source)."""
    in_degree = {n: 0 for n in nodes}
    successors: Dict[str, List[str]] = defaultdict(list)
    for s, d in edges:
        if s in in_degree and d in in_degree:
            in_degree[d] += 1
            successors[s].append(d)
    # BFS from sources (in_degree=0); cycles handled by capping iterations
    layer = {n: 0 for n in nodes}
    queue = [n for n, d in in_degree.items() if d == 0]
    visited = set(queue)
    max_iter = len(nodes) * 4
    while queue and max_iter > 0:
        max_iter -= 1
        cur = queue.pop(0)
        for nxt in successors[cur]:
            new_layer = layer[cur] + 1
            if new_layer > layer[nxt]:
                layer[nxt] = new_layer
            if nxt not in visited:
                visited.add(nxt)
                queue.append(nxt)
    return layer


def _assign_positions(
    layer: Dict[str, int], node_size: Tuple[int, int] = (90, 70),
    gap_x: int = 50, gap_y: int = 50,
) -> Dict[str, Tuple[float, float]]:
    """Place nodes in columns by layer; offset vertically to avoid overlap."""
    cols: Dict[int, List[str]] = defaultdict(list)
    for n, lyr in layer.items():
        cols[lyr].append(n)
    positions: Dict[str, Tuple[float, float]] = {}
    nw, nh = node_size
    for lyr in sorted(cols):
        nodes = cols[lyr]
        x = 40 + lyr * (nw + gap_x)
        for i, n in enumerate(sorted(nodes)):
            y = 40 + i * (nh + gap_y)
            positions[n] = (x + nw / 2, y + nh / 2)
    return positions


# ─── SVG generation ──────────────────────────────────────────────────────────

def _svg_icon(cx: float, cy: float, icon: Dict, tag: str) -> str:
    """Draw the unit-op icon at (cx, cy)."""
    fill = icon["fill"]
    label = icon["label"]
    parts: List[str] = []
    shape = icon["shape"]
    if shape == "circle":
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="22" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "double_circle":
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="25" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="18" fill="none" stroke="#0f172a" stroke-width="1.5"/>')
    elif shape == "tower":
        parts.append(f'<rect x="{cx-14}" y="{cy-30}" width="28" height="60" rx="14" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
        # internal stage lines
        for i in range(1, 5):
            parts.append(f'<line x1="{cx-12}" y1="{cy-30+i*12}" x2="{cx+12}" y2="{cy-30+i*12}" stroke="#0f172a" stroke-width="0.5" opacity="0.5"/>')
    elif shape == "pill":
        parts.append(f'<rect x="{cx-25}" y="{cy-15}" width="50" height="30" rx="15" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "trapezoid":
        parts.append(f'<polygon points="{cx-22},{cy+15} {cx-12},{cy-15} {cx+12},{cy-15} {cx+22},{cy+15}" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "rev_trapezoid":
        parts.append(f'<polygon points="{cx-12},{cy+15} {cx-22},{cy-15} {cx+22},{cy-15} {cx+12},{cy+15}" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "bowtie":
        parts.append(f'<polygon points="{cx-15},{cy-12} {cx-15},{cy+12} {cx+15},{cy-12} {cx+15},{cy+12}" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "diamond":
        parts.append(f'<polygon points="{cx},{cy-18} {cx+18},{cy} {cx},{cy+18} {cx-18},{cy}" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "long_rect":
        parts.append(f'<rect x="{cx-30}" y="{cy-10}" width="60" height="20" rx="3" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')
    elif shape == "stream":
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="10" fill="{fill}" stroke="#0f172a" stroke-width="1.5"/>')
    else:
        parts.append(f'<rect x="{cx-22}" y="{cy-15}" width="44" height="30" rx="4" fill="{fill}" stroke="#0f172a" stroke-width="2"/>')

    if label and shape != "stream":
        parts.append(f'<text x="{cx}" y="{cy+4}" text-anchor="middle" fill="#0f172a" font-size="11" font-weight="bold" font-family="monospace">{label}</text>')

    # Tag label below
    parts.append(f'<text x="{cx}" y="{cy+45}" text-anchor="middle" fill="#e2e8f0" font-size="10" font-family="sans-serif">{_esc(tag)}</text>')
    return "\n  ".join(parts)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_pfd_svg(
    objects: List[Dict],
    connections: List[Dict],
    width:  Optional[int] = None,
    height: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generate a Process Flow Diagram as an SVG string.

    objects:     [{tag, type/category}, ...]
    connections: [{from, to, phase?}, ...]
    """
    if not objects:
        return {"success": False, "error": "No objects to draw"}

    tags = [o["tag"] for o in objects if "tag" in o]
    obj_by_tag = {o["tag"]: o for o in objects if "tag" in o}
    edges = [(c.get("from"), c.get("to")) for c in connections
             if c.get("from") and c.get("to")]

    # Layout
    layer = _topological_layers(tags, edges)
    positions = _assign_positions(layer)
    if not positions:
        return {"success": False, "error": "No positions computed (empty graph)"}

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    W = width  or int(max(xs) + 120)
    H = height or int(max(ys) + 100)

    # Build SVG
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" style="background:#0a1120;font-family:sans-serif">',
        '<defs>',
        '  <marker id="arrow" viewBox="0 -5 10 10" refX="10" refY="0" markerWidth="6" markerHeight="6" orient="auto">',
        '    <path d="M0,-4L10,0L0,4z" fill="#64748b"/>',
        '  </marker>',
        '  <marker id="arrow_v" viewBox="0 -5 10 10" refX="10" refY="0" markerWidth="6" markerHeight="6" orient="auto">',
        '    <path d="M0,-4L10,0L0,4z" fill="#3b82f6"/>',
        '  </marker>',
        '</defs>',
        f'<text x="10" y="20" fill="#94a3b8" font-size="10">DWSIM Auto-PFD</text>',
    ]

    # Draw connections (behind nodes)
    for c in connections:
        src, dst = c.get("from"), c.get("to")
        if src not in positions or dst not in positions:
            continue
        x1, y1 = positions[src]
        x2, y2 = positions[dst]
        phase = (c.get("phase") or "").lower()
        if phase == "vapor":
            color, dash = "#fb923c", ""
        elif phase == "liquid":
            color, dash = "#3b82f6", ""
        elif phase == "energy":
            color, dash = "#eab308", "stroke-dasharray=\"4,2\""
        else:
            color, dash = "#64748b", ""
        # Slight bezier curve for visual clarity
        mx = (x1 + x2) / 2
        path = f'M {x1},{y1} C {mx},{y1} {mx},{y2} {x2},{y2}'
        svg.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
            f'opacity="0.85" {dash} marker-end="url(#arrow)"/>'
        )

    # Draw nodes
    for tag, (cx, cy) in positions.items():
        icon = _icon_for(obj_by_tag.get(tag, {"type": "unknown"}))
        svg.append(_svg_icon(cx, cy, icon, tag))

    # Legend (bottom-right)
    legend_y = H - 50
    svg.extend([
        f'<g transform="translate({W-180},{legend_y})">',
        '<rect width="170" height="42" rx="5" fill="#0f172a" stroke="#334155"/>',
        '<text x="6" y="13" fill="#94a3b8" font-size="10" font-weight="bold">Legend</text>',
        '<line x1="6" y1="24" x2="30" y2="24" stroke="#fb923c" stroke-width="2"/><text x="34" y="28" fill="#fb923c" font-size="9">Vapor</text>',
        '<line x1="6" y1="36" x2="30" y2="36" stroke="#3b82f6" stroke-width="2"/><text x="34" y="40" fill="#3b82f6" font-size="9">Liquid</text>',
        '<line x1="80" y1="24" x2="104" y2="24" stroke="#eab308" stroke-width="2" stroke-dasharray="4,2"/><text x="108" y="28" fill="#eab308" font-size="9">Energy</text>',
        '<line x1="80" y1="36" x2="104" y2="36" stroke="#64748b" stroke-width="2"/><text x="108" y="40" fill="#64748b" font-size="9">Material</text>',
        '</g>',
    ])

    svg.append('</svg>')
    svg_str = "\n".join(svg)

    return {
        "success": True,
        "svg": svg_str,
        "width": W,
        "height": H,
        "n_objects": len(positions),
        "n_connections": len([c for c in connections if c.get("from") in positions and c.get("to") in positions]),
    }
