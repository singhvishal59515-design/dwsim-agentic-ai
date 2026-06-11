"""
cape_open_integration.py — CAPE-OPEN component discovery and loading.

CAPE-OPEN is a Windows COM-based interoperability standard for process
simulation. This module:

  1. Discovers all CAPE-OPEN components installed on the system by scanning
     the Windows registry for components implementing the CO categories.
  2. Loads selected components into DWSIM via the bridge.
  3. Exposes their parameters and ports through generic reflection so the
     LLM can configure them.

Falls back gracefully on non-Windows systems or when no CO components are
installed (returns an empty list with an explanation).

Key CAPE-OPEN category GUIDs (from CO 1.1 spec, CAPE-OPEN Laboratories Network):
  Unit Operation:         {678c09a1-7d66-11d2-a67d-00105a42887f}
  Property Package:       {678c09a4-7d66-11d2-a67d-00105a42887f}
  Property Package Mgr:   {678c0996-7d66-11d2-a67d-00105a42887f}
  Reaction Package:       {678c09a5-7d66-11d2-a67d-00105a42887f}
  Equilibrium Solver:     {678c09a6-7d66-11d2-a67d-00105a42887f}
"""

from __future__ import annotations
import os
import sys
from typing import Any, Dict, List, Optional


# CAPE-OPEN 1.1 Implemented Categories
_CO_CATEGORIES = {
    "{678c09a1-7d66-11d2-a67d-00105a42887f}": "UnitOperation",
    "{678c09a4-7d66-11d2-a67d-00105a42887f}": "PropertyPackage",
    "{678c0996-7d66-11d2-a67d-00105a42887f}": "PropertyPackageManager",
    "{678c09a5-7d66-11d2-a67d-00105a42887f}": "ReactionPackage",
    "{678c09a6-7d66-11d2-a67d-00105a42887f}": "EquilibriumSolver",
}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Discovery — scan Windows registry for CAPE-OPEN components
# ─────────────────────────────────────────────────────────────────────────────

def discover_cape_open_components(category_filter: str = "") -> Dict[str, Any]:
    """
    Scan the Windows registry for CAPE-OPEN components.

    category_filter: optional, one of "UnitOperation", "PropertyPackage",
                     "ReactionPackage", "EquilibriumSolver", "PropertyPackageManager".
                     Empty = all categories.

    Returns:
      success, components: list of {clsid, prog_id, name, category, description, vendor}
    """
    if sys.platform != "win32":
        return {
            "success": False,
            "error": "CAPE-OPEN discovery requires Windows (COM registry).",
            "platform": sys.platform,
            "components": [],
        }

    try:
        import winreg
    except ImportError:
        return {"success": False, "error": "winreg not available", "components": []}

    if category_filter and category_filter not in {v for v in _CO_CATEGORIES.values()}:
        return {
            "success": False,
            "error": f"Unknown CO category '{category_filter}'. "
                     f"Valid: {sorted(set(_CO_CATEGORIES.values()))}",
            "components": [],
        }

    # Reverse lookup category-name → GUID for filtering
    cat_filter_guid = ""
    if category_filter:
        for guid, name in _CO_CATEGORIES.items():
            if name == category_filter:
                cat_filter_guid = guid.lower()
                break

    components: List[Dict[str, Any]] = []
    seen_clsids: set = set()
    errors: List[str] = []

    # Iterate all CLSIDs in HKEY_CLASSES_ROOT\CLSID looking for ones with
    # CAPE-OPEN Implemented Categories.
    try:
        clsid_root = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"CLSID")
    except OSError as exc:
        return {"success": False, "error": f"Cannot open HKCR\\CLSID: {exc}", "components": []}

    i = 0
    max_scan = 10000  # safety cap — most systems have <2000 CLSIDs
    while i < max_scan:
        try:
            clsid = winreg.EnumKey(clsid_root, i)
            i += 1
        except OSError:
            break  # end of enumeration

        clsid_lower = clsid.lower()
        if clsid_lower in seen_clsids:
            continue

        # Check if this CLSID has any Implemented Category we care about
        try:
            cats_key = winreg.OpenKey(
                clsid_root, clsid + r"\Implemented Categories"
            )
        except OSError:
            continue  # not a CO component

        matched_cat: Optional[str] = None
        j = 0
        while True:
            try:
                cat_guid = winreg.EnumKey(cats_key, j)
                j += 1
            except OSError:
                break
            cg_low = cat_guid.lower()
            if cg_low in _CO_CATEGORIES:
                if cat_filter_guid and cg_low != cat_filter_guid:
                    continue
                matched_cat = _CO_CATEGORIES[cg_low]
                break
        winreg.CloseKey(cats_key)

        if not matched_cat:
            continue

        # Pull component metadata
        info = _read_clsid_metadata(clsid_root, clsid)
        info["clsid"] = clsid
        info["category"] = matched_cat
        components.append(info)
        seen_clsids.add(clsid_lower)

    winreg.CloseKey(clsid_root)

    # De-dup and sort by name
    components.sort(key=lambda c: (c.get("category", ""), (c.get("name") or c.get("prog_id") or "").lower()))

    return {
        "success": True,
        "platform": sys.platform,
        "count": len(components),
        "components": components,
        "categories_scanned": sorted(set(_CO_CATEGORIES.values())) if not category_filter else [category_filter],
        "errors": errors or None,
        "summary": (
            f"Found {len(components)} CAPE-OPEN component(s). "
            f"Use add_cape_open_unit with the clsid to add one to your flowsheet."
            if components else
            "No CAPE-OPEN components found on this system. Install third-party "
            "CO unit ops (e.g. ChemSep, Sulzer SULCOL, AspenTech CO models) to use this feature."
        ),
    }


def _read_clsid_metadata(clsid_root, clsid: str) -> Dict[str, Any]:
    """Pull name, ProgID, description, vendor from a CO component's CLSID branch."""
    import winreg
    info: Dict[str, Any] = {"name": "", "prog_id": "", "description": "", "vendor": "", "dll_path": ""}

    # Default value of HKCR\CLSID\{clsid} = friendly name
    try:
        key = winreg.OpenKey(clsid_root, clsid)
        try:
            name, _ = winreg.QueryValueEx(key, "")
            info["name"] = str(name) if name else ""
        except OSError:
            pass

        # ProgID
        try:
            prog_key = winreg.OpenKey(key, "ProgID")
            try:
                pid, _ = winreg.QueryValueEx(prog_key, "")
                info["prog_id"] = str(pid) if pid else ""
            except OSError:
                pass
            winreg.CloseKey(prog_key)
        except OSError:
            pass

        # InprocServer32 = DLL path
        try:
            srv_key = winreg.OpenKey(key, "InprocServer32")
            try:
                dll, _ = winreg.QueryValueEx(srv_key, "")
                info["dll_path"] = str(dll) if dll else ""
            except OSError:
                pass
            winreg.CloseKey(srv_key)
        except OSError:
            try:
                srv_key = winreg.OpenKey(key, "LocalServer32")
                try:
                    dll, _ = winreg.QueryValueEx(srv_key, "")
                    info["dll_path"] = str(dll) if dll else ""
                except OSError:
                    pass
                winreg.CloseKey(srv_key)
            except OSError:
                pass

        # CAPE-OPEN-specific metadata (vendor, description, version)
        for sub in ("CapeDescription", "CAPE-OPEN", "CapeOpen"):
            try:
                desc_key = winreg.OpenKey(key, sub)
                for vname, target in (
                    ("Description", "description"),
                    ("Vendor", "vendor"),
                    ("CapeVersion", "cape_version"),
                    ("Version", "version"),
                ):
                    try:
                        v, _ = winreg.QueryValueEx(desc_key, vname)
                        info[target] = str(v) if v else ""
                    except OSError:
                        pass
                winreg.CloseKey(desc_key)
                break
            except OSError:
                continue

        winreg.CloseKey(key)
    except OSError:
        pass

    # If no friendly name, fall back to ProgID
    if not info["name"] and info["prog_id"]:
        info["name"] = info["prog_id"]
    if not info["name"]:
        info["name"] = clsid

    return info


# ─────────────────────────────────────────────────────────────────────────────
# 2. Load CO component into DWSIM flowsheet via the bridge
# ─────────────────────────────────────────────────────────────────────────────

def add_cape_open_unit_to_flowsheet(
    bridge: Any, tag: str, clsid_or_progid: str
) -> Dict[str, Any]:
    """
    Instantiate a CAPE-OPEN unit operation and add it to the current DWSIM
    flowsheet under the given tag.

    Approach (in order of preference):
      1. Use DWSIM's native add_object with type="CapeOpenUO" plus a hint
         pointing at the CLSID — DWSIM resolves it via its CO manager.
      2. Manual COM activation via pythonnet (System.Activator).

    bridge: the DWSIMBridgeV2 instance.
    """
    if not tag or not clsid_or_progid:
        return {"success": False, "error": "tag and clsid_or_progid are required"}

    # Path 1: ask DWSIM to add a generic CapeOpenUO, then attach the CO component
    try:
        if hasattr(bridge, "add_object"):
            r = bridge.add_object(tag, "CapeOpenUO")
            if r.get("success"):
                # Now try to bind the CO component to the placeholder
                bind_r = _bind_co_component_to_placeholder(bridge, tag, clsid_or_progid)
                if bind_r.get("success"):
                    return {
                        "success": True,
                        "tag": tag,
                        "method": "dwsim_add_then_bind",
                        "clsid": clsid_or_progid,
                        "message": f"Added CapeOpenUO '{tag}' and bound CO component {clsid_or_progid}",
                    }
                # Roll back if binding failed
                try:
                    if hasattr(bridge, "delete_object"):
                        bridge.delete_object(tag)
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": "Added CapeOpenUO placeholder but could not bind CO component",
                    "bind_error": bind_r.get("error"),
                    "rolled_back": True,
                }
    except Exception as exc:
        return {"success": False, "error": f"add_object('{tag}', 'CapeOpenUO') failed: {exc}"}

    return {"success": False, "error": "Bridge has no add_object method"}


def _bind_co_component_to_placeholder(
    bridge: Any, tag: str, clsid_or_progid: str
) -> Dict[str, Any]:
    """
    Instantiate the COM object for clsid_or_progid and attach it to the
    CapeOpenUO placeholder already in the flowsheet.

    Uses pythonnet (`clr`) — the same .NET interop the bridge already uses.
    """
    try:
        import clr  # pythonnet
        from System import Activator, Type
    except Exception as exc:
        return {
            "success": False,
            "error": f"pythonnet not available — cannot instantiate COM object: {exc}",
        }

    # Find the placeholder object in the flowsheet
    try:
        placeholder = bridge._find_object(tag) if hasattr(bridge, "_find_object") else None
        if placeholder is None:
            return {"success": False, "error": f"Placeholder '{tag}' not found in flowsheet"}
    except Exception as exc:
        return {"success": False, "error": f"Could not find placeholder: {exc}"}

    # Resolve CLSID from ProgID if needed
    clsid = clsid_or_progid
    if not clsid.startswith("{"):
        # Looks like a ProgID — resolve via registry
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, clsid_or_progid + r"\CLSID") as k:
                clsid, _ = winreg.QueryValueEx(k, "")
        except Exception as exc:
            return {"success": False, "error": f"Could not resolve ProgID '{clsid_or_progid}': {exc}"}

    # Instantiate the COM object
    try:
        t = Type.GetTypeFromCLSID(System_Guid(clsid))
        com_obj = Activator.CreateInstance(t)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Activator.CreateInstance failed for CLSID {clsid}: {exc}",
            "hint": "The CO component may not be properly registered. Try re-running its installer as admin.",
        }

    # Attach to placeholder via DWSIM's API
    # DWSIM's CapeOpenUO usually has a CO_Object property or similar
    try:
        for attr in ("CO_Object", "COObject", "CapeUnit", "InternalObject"):
            if hasattr(placeholder, attr):
                setattr(placeholder, attr, com_obj)
                return {
                    "success": True,
                    "clsid": clsid,
                    "attribute_used": attr,
                    "message": f"CO component bound to '{tag}' via {attr}",
                }
        return {
            "success": False,
            "error": "Could not find a writable CO attribute on CapeOpenUO placeholder",
            "tried_attributes": ["CO_Object", "COObject", "CapeUnit", "InternalObject"],
        }
    except Exception as exc:
        return {"success": False, "error": f"Failed to bind CO component: {exc}"}


def System_Guid(clsid_str: str):
    """Convert a string CLSID to a .NET System.Guid."""
    from System import Guid
    s = clsid_str.strip("{}")
    return Guid(s)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Parameter and port inspection
# ─────────────────────────────────────────────────────────────────────────────

def list_cape_open_parameters(bridge: Any, tag: str) -> Dict[str, Any]:
    """
    Enumerate parameters of a CAPE-OPEN unit op via ICapeUnit + ICapeCollection.
    """
    try:
        obj = bridge._find_object(tag) if hasattr(bridge, "_find_object") else None
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        # Try to navigate to the CO component
        co = None
        for attr in ("CO_Object", "COObject", "CapeUnit", "InternalObject"):
            v = getattr(obj, attr, None)
            if v is not None:
                co = v
                break
        if co is None:
            return {"success": False, "error": "No bound CO component found on object"}

        params = []
        try:
            param_collection = getattr(co, "parameters", None) or getattr(co, "Parameters", None)
            if param_collection is None:
                return {"success": False, "error": "CO component has no parameters collection"}
            count_attr = getattr(param_collection, "Count", None) or getattr(param_collection, "count", None)
            n = int(count_attr) if count_attr is not None else 0
            for i in range(1, n + 1):
                try:
                    p = param_collection.Item(i)
                    pname = str(getattr(p, "ComponentName", "") or getattr(p, "name", "") or f"param{i}")
                    pval  = getattr(p, "value", None)
                    pmode = str(getattr(p, "Mode", "") or getattr(p, "mode", ""))
                    pspec = str(getattr(p, "Specification", "") or getattr(p, "specification", ""))
                    params.append({
                        "name": pname,
                        "value": str(pval) if pval is not None else None,
                        "mode": pmode,
                        "specification": pspec,
                    })
                except Exception as exc:
                    params.append({"name": f"param{i}", "error": str(exc)})
        except Exception as exc:
            return {"success": False, "error": f"Could not enumerate parameters: {exc}"}

        return {"success": True, "tag": tag, "n_parameters": len(params), "parameters": params}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def list_cape_open_ports(bridge: Any, tag: str) -> Dict[str, Any]:
    """Enumerate ports (inlets/outlets) of a CO unit op via ICapeUnit.ports."""
    try:
        obj = bridge._find_object(tag) if hasattr(bridge, "_find_object") else None
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        co = None
        for attr in ("CO_Object", "COObject", "CapeUnit", "InternalObject"):
            v = getattr(obj, attr, None)
            if v is not None:
                co = v
                break
        if co is None:
            return {"success": False, "error": "No bound CO component found on object"}

        ports = []
        try:
            port_collection = getattr(co, "ports", None) or getattr(co, "Ports", None)
            if port_collection is None:
                return {"success": False, "error": "CO component has no ports collection"}
            count_attr = getattr(port_collection, "Count", None) or getattr(port_collection, "count", None)
            n = int(count_attr) if count_attr is not None else 0
            for i in range(1, n + 1):
                try:
                    p = port_collection.Item(i)
                    ports.append({
                        "name":      str(getattr(p, "ComponentName", "") or getattr(p, "name", "") or f"port{i}"),
                        "direction": str(getattr(p, "direction", "") or getattr(p, "Direction", "")),
                        "port_type": str(getattr(p, "portType", "") or getattr(p, "PortType", "")),
                        "connected": getattr(p, "connectedObject", None) is not None,
                    })
                except Exception as exc:
                    ports.append({"name": f"port{i}", "error": str(exc)})
        except Exception as exc:
            return {"success": False, "error": f"Could not enumerate ports: {exc}"}

        return {"success": True, "tag": tag, "n_ports": len(ports), "ports": ports}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def set_cape_open_parameter(
    bridge: Any, tag: str, parameter_name: str, value: Any
) -> Dict[str, Any]:
    """Set a parameter on a CO unit op via ICapeParameter.value."""
    try:
        obj = bridge._find_object(tag) if hasattr(bridge, "_find_object") else None
        if obj is None:
            return {"success": False, "error": f"Object '{tag}' not found"}

        co = None
        for attr in ("CO_Object", "COObject", "CapeUnit", "InternalObject"):
            v = getattr(obj, attr, None)
            if v is not None:
                co = v
                break
        if co is None:
            return {"success": False, "error": "No bound CO component found on object"}

        param_collection = getattr(co, "parameters", None) or getattr(co, "Parameters", None)
        if param_collection is None:
            return {"success": False, "error": "CO component has no parameters collection"}

        # Search by name
        count_attr = getattr(param_collection, "Count", None) or getattr(param_collection, "count", None)
        n = int(count_attr) if count_attr is not None else 0
        found_param = None
        for i in range(1, n + 1):
            p = param_collection.Item(i)
            pname = str(getattr(p, "ComponentName", "") or getattr(p, "name", "") or "")
            if pname.lower() == parameter_name.lower():
                found_param = p
                break

        if found_param is None:
            return {
                "success": False,
                "error": f"Parameter '{parameter_name}' not found on CO unit '{tag}'",
            }

        # Set the value — type depends on parameter, try several attribute names
        try:
            if hasattr(found_param, "value"):
                found_param.value = value
            elif hasattr(found_param, "Value"):
                found_param.Value = value
            else:
                return {"success": False, "error": "Parameter has no 'value' attribute"}
        except Exception as exc:
            return {"success": False, "error": f"Failed to set value: {exc}",
                    "hint": "Value may have wrong type (try string, int, or float)."}

        # Validate
        try:
            if hasattr(found_param, "Validate"):
                msg = ""
                ok = found_param.Validate(msg)
                if not ok:
                    return {"success": False, "error": f"Validation failed: {msg}", "value": value}
        except Exception:
            pass

        return {
            "success": True,
            "tag": tag,
            "parameter": parameter_name,
            "value": value,
            "message": f"Set parameter '{parameter_name}' = {value} on '{tag}'",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
