"""
multimodel_uncertainty.py
─────────────────────────
Model-form (thermodynamic) uncertainty for a flowsheet.

This is the deliberate counter-move to a commercial simulator's one genuine
advantage — decades-validated thermo. Rather than *claim* parity on fidelity
(which an open-source engine cannot honestly do), this quantifies the
uncertainty: solve the SAME flowsheet under several property packages and report
the spread of every output. A small spread means the result is robust to the
thermo choice; a large spread flags a result that is only as trustworthy as the
package selection — exactly the caveat a careful engineer wants surfaced, and
something a commercial tool does not hand you in one command.

This module is the PURE core (no DWSIM): it aggregates per-model observations
into spread statistics, so it is unit-testable without an engine or an LLM. The
bridge orchestrates the builds and calls `aggregate_model_spread`.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


def _stats(values: List[float]) -> Dict[str, Any]:
    n = len(values)
    mean = sum(values) / n
    if n >= 2:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0
    vmin, vmax = min(values), max(values)
    rng = vmax - vmin
    # Relative spread: range as a percent of |mean|. Undefined near mean 0
    # (use absolute range there instead of a meaningless huge percent).
    rel = (rng / abs(mean) * 100.0) if abs(mean) > 1e-12 else None
    return {"n": n, "mean": round(mean, 6), "std": round(std, 6),
            "min": round(vmin, 6), "max": round(vmax, 6),
            "range": round(rng, 6),
            "rel_spread_pct": (round(rel, 3) if rel is not None else None)}


def aggregate_model_spread(
    per_model: Dict[str, Dict[str, Dict[str, Any]]],
    rel_spread_warn_pct: float = 5.0,
) -> Dict[str, Any]:
    """
    per_model: {package_name: {stream_tag: {property: value, ...}, ...}, ...}
               (only numeric properties are aggregated; non-numeric are ignored)

    Returns a structured report:
      observations[(tag.prop)] = {package values + spread stats}
      summary = {n_models, max_rel_spread_pct, most_sensitive, robust}
    """
    models = list(per_model.keys())
    # Union of every (tag, property) seen across models.
    keys: List[Tuple[str, str]] = []
    seen = set()
    for tagmap in per_model.values():
        for tag, props in (tagmap or {}).items():
            for prop in (props or {}):
                k = (tag, prop)
                if k not in seen:
                    seen.add(k)
                    keys.append(k)

    observations: Dict[str, Any] = {}
    worst: Optional[Tuple[str, float]] = None
    for tag, prop in keys:
        vals: Dict[str, float] = {}
        for m in models:
            v = (per_model.get(m, {}) or {}).get(tag, {}).get(prop)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)) and math.isfinite(v):
                vals[m] = float(v)
        if len(vals) < 2:
            continue  # need >= 2 models to have a spread
        st = _stats(list(vals.values()))
        label = f"{tag}.{prop}"
        observations[label] = {"by_model": {m: round(v, 6) for m, v in vals.items()},
                               **st}
        rel = st["rel_spread_pct"]
        if rel is not None and (worst is None or rel > worst[1]):
            worst = (label, rel)

    max_rel = worst[1] if worst else 0.0
    summary = {
        "n_models": len(models),
        "models": models,
        "n_observations": len(observations),
        "max_rel_spread_pct": max_rel,
        "most_sensitive": (worst[0] if worst else None),
        # "robust" = the result barely moves with the thermo choice.
        "robust": bool(observations) and max_rel <= rel_spread_warn_pct,
        "warn_threshold_pct": rel_spread_warn_pct,
    }
    summary["interpretation"] = _interpret(summary, worst)
    return {"success": True, "summary": summary, "observations": observations}


def _interpret(summary: Dict[str, Any], worst) -> str:
    if not summary["n_observations"]:
        return ("No comparable outputs across models — fewer than two packages "
                "produced numeric results.")
    if summary["robust"]:
        return (f"Result is ROBUST to the thermodynamic model: the largest "
                f"output spread is {summary['max_rel_spread_pct']:.2f}% "
                f"(<= {summary['warn_threshold_pct']:.0f}% across "
                f"{summary['n_models']} packages). The conclusion does not hinge "
                f"on the package choice.")
    return (f"Result is SENSITIVE to the thermodynamic model: "
            f"{worst[0]} varies by {summary['max_rel_spread_pct']:.2f}% across "
            f"{summary['n_models']} packages. Treat this output as model-dependent "
            f"and prefer a package validated for this chemistry, or report the "
            f"range rather than a single value.")
