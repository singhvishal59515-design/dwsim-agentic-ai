"""
flowsheet_executor.py — Deterministic flowsheet construction from a plan.

The LLM is unreliable at issuing 20+ ordered tool calls in sequence.
Instead, the LLM emits ONE structured plan describing the entire flowsheet,
and this module walks it deterministically with full error reporting.

Plan schema:
{
  "name": "my_flowsheet",
  "compounds": ["Methane", "Water", ...],
  "property_package": "Peng-Robinson (PR)",
  "streams": [
    {"tag": "FEED",  "T_C": 25, "P_bar": 10, "flow_kmol_h": 100,
     "compositions": {"Methane": 1.0}},
    {"tag": "PROD"}
  ],
  "unit_ops": [
    {"tag": "H-1", "type": "Heater", "params": {"outlet_T_C": 300}}
  ],
  "connections": [
    {"from": "FEED", "to": "H-1"},
    {"from": "H-1", "to": "PROD"}
  ],
  "solve": false
}

Returns the same shape as instantiate_template — a step-by-step build log
with per-step success and an aggregated errors list.
"""

from __future__ import annotations
from typing import Any, Dict, List


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Accept either snake_case or camelCase keys; emit a normalized dict."""
    if not isinstance(plan, dict):
        return {}
    p = dict(plan)
    # Allow alternate keys
    p.setdefault("compounds", plan.get("Compounds") or plan.get("comps") or [])
    p.setdefault("property_package",
                 plan.get("PropertyPackage") or plan.get("propertyPackage")
                 or plan.get("pp") or "Peng-Robinson (PR)")
    p.setdefault("streams", plan.get("Streams") or [])
    p.setdefault("unit_ops",
                 plan.get("unitOps") or plan.get("UnitOps") or plan.get("units") or [])
    p.setdefault("connections", plan.get("Connections") or plan.get("edges") or [])
    p.setdefault("name", plan.get("Name") or plan.get("flowsheet_name")
                 or "ai_plan")
    return p


def execute_build_plan(plan: Dict[str, Any], bridge: Any,
                       solve: bool = False) -> Dict[str, Any]:
    """Deterministically walk a flowsheet build plan."""
    p = _normalize_plan(plan or {})

    if not p.get("compounds"):
        return {
            "success": False,
            "error": "Plan missing 'compounds' (list of compound names).",
            "error_code": "PLAN_MISSING_COMPOUNDS",
        }

    steps: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    def _step(name: str, ok: bool, detail: Any = None):
        steps.append({"step": name, "ok": ok, "detail": detail})

    # 0. Tear untorn recycle loops with OT_Recycle blocks (same rule as the
    #    topology builder). An unbroken algebraic loop cannot converge in a
    #    sequential-modular solver. Mutating the plan here means the normal
    #    create/connect steps below build the recycle blocks + rerouted streams.
    try:
        from recycle_analyzer import plan_recycle_insertions
        rplans = plan_recycle_insertions(
            p.get("streams", []), p.get("unit_ops", []), p.get("connections", []))
        for rp in rplans:
            tear, consumer = rp["tear_stream"], rp["consumer"]
            rec_tag, new_stream = rp["rec_tag"], rp["new_stream_tag"]
            port = int(rp.get("consumer_port", 0) or 0)
            p["connections"] = [
                c for c in p.get("connections", [])
                if not (isinstance(c, dict)
                        and (c.get("from") or c.get("source")) == tear
                        and (c.get("to") or c.get("target")) == consumer)]
            p.setdefault("unit_ops", []).append(
                {"tag": rec_tag, "type": "Recycle",
                 "params": {"max_iterations": 50, "tolerance": 1e-4}})
            # Seed the tear stream from the seed source so DWSIM's recycle
            # iteration starts from a physical guess, not a blank stream.
            new_spec = {"tag": new_stream}
            src_tag = rp.get("seed_from")
            if src_tag:
                src = next((s for s in p.get("streams", [])
                            if isinstance(s, dict) and s.get("tag") == src_tag), {})
                for kk in ("T_C", "T_K", "P_bar", "P_Pa",
                           "compositions", "composition"):
                    if kk in src:
                        new_spec[kk] = src[kk]
            p.setdefault("streams", []).append(new_spec)
            p["connections"].append({"from": tear, "to": rec_tag, "from_port": 0, "to_port": 0})
            p["connections"].append({"from": rec_tag, "to": new_stream, "from_port": 0, "to_port": 0})
            p["connections"].append({"from": new_stream, "to": consumer, "from_port": 0, "to_port": port})
            _step(f"auto_recycle[{rec_tag}]", True,
                  {"torn_edge": f"{tear}->{consumer}"})
    except Exception as exc:
        _step("auto_recycle", False, {"exception": str(exc)})

    # 1. new_flowsheet
    try:
        r = bridge.new_flowsheet(
            name=str(p.get("name") or "ai_plan"),
            compounds=list(p["compounds"]),
            property_package=str(p.get("property_package")),
        )
        ok = bool(r.get("success", False))
        _step("new_flowsheet", ok, r if not ok else {"name": p["name"]})
        if not ok:
            return {"success": False, "steps": steps, "errors": [r],
                    "error": "Failed to create blank flowsheet",
                    "error_code": "NEW_FLOWSHEET_FAILED"}
    except Exception as exc:
        _step("new_flowsheet", False, {"exception": str(exc)})
        return {"success": False, "steps": steps,
                "error": f"new_flowsheet raised: {exc}",
                "error_code": "NEW_FLOWSHEET_EXCEPTION"}

    # 2. unit ops
    for uop in p.get("unit_ops", []):
        if not isinstance(uop, dict):
            continue
        tag = uop.get("tag")
        utype = uop.get("type", "")
        if not tag or not utype:
            _step(f"add_object[invalid]", False,
                  {"reason": "missing tag or type", "raw": uop})
            errors.append({"step": "add_object[invalid]", "raw": uop})
            continue
        try:
            r = bridge.add_object(tag=tag, type=utype)
            ok = bool(r.get("success", False))
            _step(f"add_object[{tag}:{utype}]", ok, r if not ok else None)
            if not ok:
                errors.append({"step": f"add_object[{tag}]", "detail": r})
        except Exception as exc:
            _step(f"add_object[{tag}:{utype}]", False, {"exception": str(exc)})
            errors.append({"step": f"add_object[{tag}]", "detail": str(exc)})

    # 2b. Auto-add energy streams for any energy-requiring unit op that was not
    #     given one, so Heaters/Coolers/Pumps/Compressors/Expanders always get a
    #     duty connector and can converge — identical rule to the topology
    #     builder. Created here (before connections) as real EnergyStream objects;
    #     the connection dicts are appended so step 4 wires them.
    try:
        from flowsheet_builder import plan_energy_injections, _resolve_type as _rt
        _est = {s.get("tag", "") for s in p.get("streams", [])
                if isinstance(s, dict)
                and (_rt(s.get("type", "")) or "") == "EnergyStream"}
        _est |= {u.get("tag", "") for u in p.get("unit_ops", [])
                 if isinstance(u, dict)
                 and (_rt(u.get("type", "")) or "") == "EnergyStream"}
        _taken = ({s.get("tag", "") for s in p.get("streams", [])
                   if isinstance(s, dict)}
                  | {u.get("tag", "") for u in p.get("unit_ops", [])
                     if isinstance(u, dict)})
        for q_tag, uo_tag, port in plan_energy_injections(
                p.get("unit_ops", []), p.get("connections", []), _est, _taken):
            r = bridge.add_object(tag=q_tag, type="EnergyStream")
            ok = bool(r.get("success", False))
            _step(f"add_object[{q_tag}:EnergyStream:auto]", ok, r if not ok else None)
            if not ok:
                errors.append({"step": f"add_object[{q_tag}:auto]", "detail": r})
                continue
            p.setdefault("connections", []).append(
                {"from": q_tag, "to": uo_tag, "from_port": 0, "to_port": port})
    except Exception as exc:
        _step("auto_energy_streams", False, {"exception": str(exc)})

    # 3. material streams (those not auto-created)
    for s in p.get("streams", []):
        if not isinstance(s, dict):
            continue
        tag = s.get("tag")
        if not tag:
            continue
        try:
            r = bridge.add_object(tag=tag, type="MaterialStream")
            _step(f"add_stream[{tag}]",
                  bool(r.get("success", True))
                  or "exists" in str(r.get("code", "")).lower(),
                  r)
        except Exception as exc:
            _step(f"add_stream[{tag}]", False, {"exception": str(exc)})

    # 4. connections
    for c in p.get("connections", []):
        if not isinstance(c, dict):
            continue
        src = c.get("from") or c.get("source")
        dst = c.get("to") or c.get("target")
        if not src or not dst:
            _step("connect[invalid]", False, {"raw": c})
            errors.append({"step": "connect[invalid]", "raw": c})
            continue
        try:
            r = bridge.connect_streams(
                from_tag=src, to_tag=dst,
                from_port=int(c.get("from_port", 0)),
                to_port=int(c.get("to_port", 0)),
            )
            ok = bool(r.get("success", False))
            _step(f"connect[{src}->{dst}]", ok, r if not ok else None)
            if not ok:
                errors.append({"step": f"connect[{src}->{dst}]", "detail": r})
        except Exception as exc:
            _step(f"connect[{src}->{dst}]", False, {"exception": str(exc)})
            errors.append({"step": f"connect[{src}->{dst}]", "detail": str(exc)})

    # 5. feed stream properties + composition
    for s in p.get("streams", []):
        if not isinstance(s, dict):
            continue
        tag = s.get("tag")
        if not tag:
            continue
        for prop_key, dwsim_prop, unit in (
            ("T_C", "temperature", "C"),
            ("T_K", "temperature", "K"),
            ("P_bar", "pressure", "bar"),
            ("P_Pa", "pressure", "Pa"),
            ("flow_kmol_h", "molar_flow", "kmol/h"),
            ("flow_kg_h", "mass_flow", "kg/h"),
            ("flow_mol_s", "molar_flow", "mol/s"),
            ("flow_kg_s", "mass_flow", "kg/s"),
        ):
            val = s.get(prop_key)
            if val is None:
                continue
            try:
                r = bridge.set_stream_property(
                    tag=tag, property_name=dwsim_prop,
                    value=float(val), unit=unit,
                )
                _step(f"set[{tag}.{dwsim_prop}={val}{unit}]",
                      bool(r.get("success", False)), r)
                if not r.get("success", False):
                    errors.append({"step": f"set[{tag}.{dwsim_prop}]", "detail": r})
            except Exception as exc:
                _step(f"set[{tag}.{dwsim_prop}]", False, {"exception": str(exc)})

        comps = s.get("compositions") or s.get("composition")
        if isinstance(comps, dict) and comps and hasattr(bridge, "set_stream_composition"):
            try:
                r = bridge.set_stream_composition(tag=tag, composition=comps)
                _step(f"composition[{tag}]",
                      bool(r.get("success", False)), r)
                if not r.get("success", False):
                    errors.append({"step": f"composition[{tag}]", "detail": r})
            except Exception as exc:
                _step(f"composition[{tag}]", False, {"exception": str(exc)})

    # 6. unit op scalar params
    for uop in p.get("unit_ops", []):
        if not isinstance(uop, dict):
            continue
        tag = uop.get("tag")
        params = uop.get("params") or {}
        if not isinstance(params, dict):
            continue
        for pkey, pval in params.items():
            if isinstance(pval, (list, dict)):
                # Complex param structures (reactions, kinetics) handled separately
                continue
            try:
                r = bridge.set_unit_op_property(
                    tag=tag, property_name=pkey, value=pval,
                )
                _step(f"uop[{tag}.{pkey}={pval}]",
                      bool(r.get("success", False)), r)
                if not r.get("success", False):
                    errors.append({"step": f"uop[{tag}.{pkey}]", "detail": r})
            except Exception as exc:
                _step(f"uop[{tag}.{pkey}]", False, {"exception": str(exc)})

    # 7. optional solve
    solve_result = None
    if (solve or p.get("solve")) and hasattr(bridge, "save_and_solve"):
        try:
            solve_result = bridge.save_and_solve()
            _step("save_and_solve",
                  bool(solve_result.get("success", False)), solve_result)
        except Exception as exc:
            solve_result = {"success": False, "error": str(exc)}
            _step("save_and_solve", False, {"exception": str(exc)})

    n_ok = sum(1 for s in steps if s["ok"])
    overall_ok = (len(errors) == 0)

    msg = (
        f"Plan executed: {n_ok}/{len(steps)} steps OK"
        + (f", {len(errors)} errors" if errors else "")
        + "."
    )

    return {
        "success": overall_ok,
        "plan_name": p.get("name"),
        "steps_total": len(steps),
        "steps_ok": n_ok,
        "steps": steps,
        "errors": errors,
        "solve_result": solve_result,
        "summary": msg,
    }
