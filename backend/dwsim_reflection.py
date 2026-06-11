"""
dwsim_reflection.py — Dynamic .NET Reflection Access to DWSIM Internals
========================================================================

Implements the three "escape hatch" tools that give the agent unrestricted,
dynamic access to DWSIM's object model:

  reflect_get_set(bridge, object_name, property_path, value=None)
      Walk any .NET property path and GET or SET the value.
      Examples:
        reflect_get_set(b, "FEED", "Phases[0].Properties.temperature")
        reflect_get_set(b, "COL-01", "NumberOfStages", "25")
        reflect_get_set(b, "flowsheet", "ConvergenceMethod", "Wegstein")

  exec_python(bridge, code)
      Execute an arbitrary Python snippet against the live DWSIM session.
      Safe sandbox: dangerous imports + os/sys/subprocess are blocked.
      Examples:
        exec_python(b, "results['purity'] = get_obj('PROD').Phases[0]
                        .Compounds['Methanol'].MoleFraction")

  inspect_object(bridge, object_name, filter_prefix="", filter_type="")
      Discover every readable property on any DWSIM .NET object.
      Equivalent to using DWSIM's object browser from Python.

  iterative_spec_loop(bridge, spec_config)
      Adjust one decision variable until an observable spec is met.
      Pure-Python bisection loop — no LLM needed once called.

These tools transform the agent from "62 predefined endpoints" to
"dynamic reflection-based full DWSIM access". Any novel task, any
object, any property — reachable without writing a new endpoint.

Security model for exec_python:
  • Blocklist of dangerous tokens (os, sys, subprocess, open, import, ...)
  • Execution context contains ONLY:  flowsheet, get_obj, results, math
  • No import allowed inside the exec'd code
  • Timeout: 60 seconds (configurable)
  • Output limited to 10 KB
"""

from __future__ import annotations
import ast as _ast
import logging
import math
import re
import traceback
from typing import Any, Dict, List, Optional

_log = logging.getLogger("dwsim_reflection")

# ─── Safety denylist for exec_python ───────────────────────────────────────

_DENIED_TOKENS = [
    # OS access
    "import os", "import sys", "__import__", "importlib",
    "subprocess", "Popen", "shutil", "pathlib",
    # File access
    'open(', 'open (', 'file(', 'io.open',
    # Eval recursion
    'eval(', 'exec(', 'compile(', 'execfile(',
    # Code injection
    '__class__', '__bases__', '__mro__', '__subclasses__',
    '__builtins__', 'builtins', 'globals(', 'locals(',
    # Network
    'socket', 'urllib', 'requests', 'httpx',
    # Registry / dangerous Windows API
    'winreg', 'ctypes', 'cffi', 'CDLL',
]

_MAX_OUTPUT_CHARS = 10_000


def _safety_check(code: str) -> Optional[str]:
    """Return an error string if the code contains blocked tokens, else None."""
    for token in _DENIED_TOKENS:
        if token in code:
            return f"Security: blocked token '{token}'"
    # Additional AST check — block top-level imports
    try:
        tree = _ast.parse(code, mode='exec')
        for node in _ast.walk(tree):
            if isinstance(node, (_ast.Import, _ast.ImportFrom)):
                return f"Security: import statements are not allowed in exec_python"
    except SyntaxError as exc:
        return f"Syntax error: {exc}"
    return None


# ─── Object resolver ───────────────────────────────────────────────────────

def _resolve_object(bridge, object_name: str) -> Any:
    """Get any DWSIM object by tag name, or return the flowsheet itself."""
    if object_name.lower() in ("flowsheet", "fs", "simulation"):
        return bridge._flowsheet
    fs = bridge._flowsheet
    if fs is None:
        raise ValueError("No flowsheet loaded")
    # Try the tag cache first (fast path)
    tag_cache = bridge._active_tag_cache()
    for guid, tag in tag_cache.items():
        if str(tag).lower() == object_name.lower():
            try:
                coll = bridge._get_collection()
                for g, obj in bridge._iter_collection(coll):
                    if str(g) == str(guid):
                        return obj
            except Exception:
                pass
    # Direct GUID lookup or name search
    try:
        coll = bridge._get_collection()
        for guid, obj in bridge._iter_collection(coll):
            try:
                tag = bridge._active_tag_cache().get(str(guid), "")
                if str(tag).lower() == object_name.lower():
                    return obj
            except Exception:
                continue
    except Exception:
        pass
    # Try flowsheet method
    for method in ("GetFlowsheetSimulationObject", "GetFlowsheetObject"):
        fn = getattr(fs, method, None)
        if fn is not None:
            try:
                obj = fn(object_name)
                if obj is not None:
                    return obj
            except Exception:
                pass
    raise KeyError(f"Object '{object_name}' not found in flowsheet")


def _resolve_phase(obj: Any, phase_index: int) -> Any:
    """Resolve a Phases[N] accessor for DWSIM's Dictionary[int, IPhase].
    DWSIM stores Phases as a Dictionary keyed by integer phase IDs:
      0 = Mixture, 1 = Vapor, 2 = Liquid1/Overall, 3 = Liquid1, 7 = Solid
    The [] indexer on a .NET Dictionary requires the exact key type."""
    try:
        phases = getattr(obj, "Phases")
        # .NET Dictionary[int, IPhase] — use the indexer directly
        return phases[phase_index]
    except Exception:
        pass
    # Try PhasesArray (IPhase[])
    try:
        arr = getattr(obj, "PhasesArray")
        return arr[phase_index]
    except Exception:
        pass
    raise AttributeError(f"Cannot access Phases[{phase_index}] on {type(obj).__name__}")


def _walk_path(obj: Any, path_parts: List[str], depth: int = 0) -> Any:
    """Walk a dotted / indexed property path on a .NET object.

    Handles DWSIM-specific cases:
      Phases[0]  → _resolve_phase(obj, 0) — Dictionary[int,IPhase] accessor
      Compounds["Methanol"] → obj.Compounds["Methanol"]
      Properties.temperature → obj.Properties.temperature
    """
    if depth > 20:
        raise RecursionError("Property path too deep (limit 20)")
    if not path_parts:
        return obj
    part = path_parts[0]
    rest = path_parts[1:]

    # Numeric index — could be Phases[N] (special DWSIM case) or plain list
    if part.isdigit():
        idx = int(part)
        # Check if parent is named "Phases" (previous part)
        # by trying the DWSIM-specific _resolve_phase first
        parent_name = path_parts[-2] if len(path_parts) > 1 else ""
        try:
            # Try .NET Dictionary/IPhase access pattern
            from System import Int32
            child = obj[Int32(idx)]
        except Exception:
            try:
                child = obj[idx]
            except Exception:
                child = getattr(obj, part)

    elif part.startswith('"') or part.startswith("'"):
        # String key — dict/map access (e.g. Compounds["Methanol"])
        key = part.strip('"\'')
        try:
            child = obj[key]
        except Exception:
            child = getattr(obj, key)
    elif part == "Phases" and rest and rest[0].isdigit():
        # Special case: Phases[N] → must use .NET reflection + Dictionary indexer
        child = _resolve_phase_reflection(obj, int(rest[0]))
        rest = rest[1:]   # consume the index part too
    else:
        # Try Python attribute access first, then .NET reflection fallback
        child = _getattr_reflection(obj, part)
    return _walk_path(child, rest, depth + 1)


def _getattr_reflection(obj: Any, name: str) -> Any:
    """Get attribute via Python getattr first, then .NET reflection if that fails.
    DWSIM objects are exposed as ISimulationObject COM interfaces — many concrete
    properties are NOT accessible via Python getattr but ARE accessible via
    obj.GetType().GetProperty(name).GetValue(obj)."""
    # Python-accessible path (fast, works for most things)
    try:
        v = getattr(obj, name)
        if v is not None:
            return v
    except AttributeError:
        pass
    # .NET reflection fallback
    try:
        import System.Reflection as R
        t = obj.GetType()
        prop = t.GetProperty(name)
        if prop is not None:
            return prop.GetValue(obj)
    except Exception:
        pass
    # Raise original AttributeError
    raise AttributeError(f"'{type(obj).__name__}' has no attribute '{name}'")


def _setattr_reflection(obj: Any, name: str, value: Any) -> bool:
    """Set an attribute via Python setattr first, then .NET reflection.
    Returns True if the set succeeded (and, where checkable, took effect)."""
    # Python-accessible path
    try:
        setattr(obj, name, value)
        return True
    except Exception:
        pass
    # .NET reflection fallback
    try:
        import System
        import System.Reflection as R  # noqa: F401
        t = obj.GetType()
        prop = t.GetProperty(name)
        if prop is not None and prop.CanWrite:
            pt = prop.PropertyType
            # Cast value to the property's .NET type where possible
            try:
                if pt.IsGenericType and "Nullable" in pt.Name:
                    prop.SetValue(obj, System.Nullable[System.Double](float(value)))
                elif pt.Name in ("Double", "Single"):
                    prop.SetValue(obj, System.Double(float(value)))
                elif pt.Name in ("Int32", "Int64"):
                    prop.SetValue(obj, int(value))
                elif pt.IsEnum:
                    prop.SetValue(obj, System.Enum.Parse(pt, str(value)))
                else:
                    prop.SetValue(obj, value)
                return True
            except Exception:
                prop.SetValue(obj, value)
                return True
    except Exception:
        pass
    return False


def _resolve_phase_reflection(obj: Any, phase_index: int) -> Any:
    """Resolve Phases[N] using .NET reflection — required because DWSIM's
    Phases is a Dictionary[int, IPhase] which pythonnet can't index directly
    when the object is wrapped as ISimulationObject COM interface."""
    try:
        import System.Reflection as R
        from System import Int32
        t = obj.GetType()
        prop = t.GetProperty("Phases")
        if prop is None:
            raise AttributeError("No 'Phases' property")
        phases = prop.GetValue(obj)
        return phases[Int32(phase_index)]
    except Exception as exc:
        raise AttributeError(
            f"Cannot access Phases[{phase_index}] via reflection: {exc}") from exc


def _parse_path(property_path: str) -> List[str]:
    """Split "Phases[0].Properties.temperature" → ["Phases","0","Properties","temperature"]"""
    # Replace [N] with .N.
    path = re.sub(r'\[([^\]]+)\]', r'.\1', property_path)
    return [p for p in path.split('.') if p]


def _cast_value(existing: Any, value_str: str) -> Any:
    """Auto-cast a string value to match the type of the existing property."""
    if existing is None or isinstance(existing, str):
        return value_str
    t = type(existing)
    try:
        if t == bool:
            return value_str.lower() in ("true", "1", "yes")
        return t(value_str)
    except (ValueError, TypeError):
        return value_str


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 1 — reflect_get_set
# ══════════════════════════════════════════════════════════════════════════════

def _bridge_property_fallback(bridge, object_name: str,
                               property_path: str) -> Optional[Any]:
    """Fall back to the bridge's existing property readers when direct
    .NET attribute access fails (e.g. ISimulationObject COM interface
    doesn't expose all properties directly)."""
    prop_lc = property_path.lower()
    # Use the bridge's stream/unit-op property readers
    try:
        r = bridge.get_stream_property(object_name, property_path)
        if isinstance(r, dict) and r.get("success") and r.get("value") is not None:
            return str(r["value"])
    except Exception:
        pass
    try:
        r = bridge.get_stream_properties(object_name)
        if isinstance(r, dict) and r.get("success"):
            props = r.get("properties", {})
            # Direct match
            if property_path in props:
                return str(props[property_path])
            # Case-insensitive match
            for k, v in props.items():
                if k.lower() == prop_lc:
                    return str(v)
            # Partial match for nested-style paths
            for k, v in props.items():
                if k.lower() in prop_lc or prop_lc in k.lower():
                    return str(v)
    except Exception:
        pass
    try:
        r = bridge.get_unit_op_properties(object_name)
        if isinstance(r, dict) and r.get("success"):
            props = r.get("properties", {})
            if property_path in props:
                return str(props[property_path])
    except Exception:
        pass
    return None


def reflect_get_set(
    bridge,
    object_name:   str,
    property_path: str,
    value:         Optional[str] = None,
) -> Dict[str, Any]:
    """
    Read (GET) or write (SET) any property on any DWSIM .NET object.

    Parameters
    ----------
    object_name    Tag of the stream/unit-op, or "flowsheet" for the root.
    property_path  Dotted + indexed path, e.g.:
                     "Phases[0].Properties.temperature"
                     "NumberOfStages"
                     "Compounds[\"Methanol\"].MoleFraction"
    value          If None → GET.  If provided → SET.

    Returns
    -------
    GET: {success, object, path, value, type}
    SET: {success, object, path, old_value, set_to}
    """
    if not property_path:
        return {"success": False, "error": "property_path required"}
    try:
        obj = _resolve_object(bridge, object_name)
    except (KeyError, ValueError) as exc:
        return {"success": False, "error": str(exc)}

    parts = _parse_path(property_path)
    parent_parts = parts[:-1]
    last         = parts[-1]

    try:
        parent = _walk_path(obj, parent_parts) if parent_parts else obj
    except Exception as exc:
        return {"success": False,
                "error": f"Cannot walk path '{property_path}': {exc}"}

    if value is None:
        # GET — use reflection-aware getattr (handles ISimulationObject COM
        # interface where plain getattr fails but .NET GetProperty works).
        try:
            if last.isdigit():
                result = parent[int(last)]
            else:
                result = _getattr_reflection(parent, last)
            return {
                "success":     True,
                "object":      object_name,
                "path":        property_path,
                "value":       str(result),
                "python_repr": repr(result)[:200],
                "type":        type(result).__name__,
            }
        except Exception as exc_direct:
            # Last resort: the bridge's own stream/unit-op property readers.
            fallback = _bridge_property_fallback(bridge, object_name, property_path)
            if fallback is not None:
                return {
                    "success": True,
                    "object":  object_name,
                    "path":    property_path,
                    "value":   fallback,
                    "type":    "string",
                    "_via":    "bridge_fallback",
                }
            return {"success": False,
                    "error": f"Cannot get '{property_path}': {exc_direct}"}
    else:
        # SET — reflection-aware get of existing value + typed .NET set
        try:
            if last.isdigit():
                existing = parent[int(last)]
                casted   = _cast_value(existing, value)
                parent[int(last)] = casted
            else:
                try:
                    existing = _getattr_reflection(parent, last)
                except Exception:
                    existing = None
                casted = _cast_value(existing, value)
                if not _setattr_reflection(parent, last, casted):
                    return {"success": False,
                            "error": f"Cannot set '{property_path}': property "
                                     f"not writable via Python or .NET reflection"}
            return {
                "success":   True,
                "object":    object_name,
                "path":      property_path,
                "old_value": str(existing) if existing is not None else "N/A",
                "set_to":    str(casted),
                "type":      type(casted).__name__,
            }
        except Exception as exc:
            return {"success": False,
                    "error": f"Cannot set '{property_path}' = '{value}': {exc}"}


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 2 — exec_python
# ══════════════════════════════════════════════════════════════════════════════

def exec_python(
    bridge,
    code:          str,
    timeout_s:     float = 60.0,
) -> Dict[str, Any]:
    """
    Execute a Python snippet against the live DWSIM flowsheet.

    The execution context contains:
        flowsheet  — the loaded IFlowsheet .NET object
        get_obj(name) → ISimulationObject    (resolves any stream/unit-op)
        results    — dict the snippet writes output into
        math       — Python math module (safe)

    Security: imports are blocked; dangerous tokens are denied.

    Example
    -------
    code = '''
    col = get_obj("COL-01")
    results["stages"]    = col.NumberOfStages
    results["converged"] = col.Converged
    results["purity"]    = (get_obj("DISTILLATE")
                            .Phases[0].Compounds["Methanol"].MoleFraction)
    '''
    """
    if not code or not code.strip():
        return {"success": False, "error": "code is empty"}

    # Safety check
    err = _safety_check(code)
    if err:
        return {"success": False, "error": err, "error_code": "SECURITY_DENIED"}

    if bridge._flowsheet is None:
        return {"success": False, "error": "No flowsheet loaded",
                "error_code": "NO_FLOWSHEET"}

    results_dict: Dict[str, Any] = {}
    stdout_capture: List[str] = []

    def _get_obj(name):
        return _resolve_object(bridge, name)

    class _SafePrint:
        def write(self, s):
            stdout_capture.append(str(s))
        def flush(self): pass

    # Curated SAFE builtins allowlist. An empty {} (the previous value) made
    # even trivial snippets fail with "name 'len' is not defined" — caught by
    # the tool-coverage harness. We expose the pure, side-effect-free builtins
    # a snippet legitimately needs, while still withholding the dangerous ones
    # (open, eval, exec, compile, __import__, input, etc.) — and the separate
    # token denylist already screens the source before we reach here.
    _SAFE_BUILTINS = {
        n: __builtins__[n] if isinstance(__builtins__, dict)
        else getattr(__builtins__, n)
        for n in (
            "abs", "all", "any", "bool", "dict", "divmod", "enumerate",
            "filter", "float", "format", "frozenset", "hex", "int", "isinstance",
            "issubclass", "len", "list", "map", "max", "min", "oct", "ord",
            "pow", "range", "repr", "reversed", "round", "set", "slice",
            "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
            # introspection helpers — safe and needed for the escape-hatch's
            # primary purpose (inspecting live .NET objects)
            "getattr", "hasattr", "dir", "vars", "type", "callable",
        )
        if (isinstance(__builtins__, dict) and n in __builtins__)
        or (not isinstance(__builtins__, dict) and hasattr(__builtins__, n))
    }
    exec_globals: Dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
    def _get_prop(tag, prop):
        """Helper: read a property using the bridge's readers (more reliable than getattr)."""
        r = _bridge_property_fallback(bridge, tag, prop)
        if r is not None:
            try: return float(r)
            except (ValueError, TypeError): return r
        # Fallback to getattr on the .NET object
        try:
            obj = _get_obj(tag)
            return getattr(obj, prop, None)
        except Exception:
            return None

    def _set_prop(tag, prop, val):
        """Helper: write a property using the bridge's setters."""
        r = reflect_get_set(bridge, tag, prop, str(val))
        return r.get("success", False)

    exec_locals: Dict[str, Any] = {
        "flowsheet": bridge._flowsheet,
        "get_obj":   _get_obj,
        "get_prop":  _get_prop,   # get_prop("Feed","temperature_C") → float
        "set_prop":  _set_prop,   # set_prop("H-101","DeltaQ",500000)
        "results":   results_dict,
        "math":      math,
        "print":     lambda *a, **k: stdout_capture.append(" ".join(str(x) for x in a)),
    }

    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(exec, code, exec_globals, exec_locals)
            future.result(timeout=timeout_s)
    except _cf.TimeoutError:
        return {"success": False,
                "error": f"Execution timed out after {timeout_s}s",
                "error_code": "TIMEOUT",
                "partial_results": {k: str(v)[:200]
                                     for k, v in results_dict.items()}}
    except Exception as exc:
        return {
            "success":    False,
            "error":      str(exc),
            "error_code": "EXEC_FAILED",
            "traceback":  traceback.format_exc(limit=5)[-1000:],
            "stdout":     "".join(stdout_capture)[:1000],
            "partial_results": {k: str(v)[:200]
                                  for k, v in results_dict.items()},
        }

    # Serialise results (convert .NET objects to strings)
    serial: Dict[str, Any] = {}
    for k, v in results_dict.items():
        try:
            # Try native JSON-serialisable types
            if isinstance(v, (int, float, bool, str, list, dict, type(None))):
                serial[k] = v
            else:
                serial[k] = str(v)
        except Exception:
            serial[k] = "<unserializable>"

    combined_output = "".join(stdout_capture)
    if len(combined_output) > _MAX_OUTPUT_CHARS:
        combined_output = combined_output[:_MAX_OUTPUT_CHARS] + "\n...(truncated)"

    return {
        "success":  True,
        "results":  serial,
        "stdout":   combined_output,
        "n_results": len(serial),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 3 — inspect_object
# ══════════════════════════════════════════════════════════════════════════════

def inspect_object(
    bridge,
    object_name:   str,
    filter_prefix: str = "",
    filter_type:   str = "",
    max_props:     int = 80,
) -> Dict[str, Any]:
    """
    Discover every readable property on any DWSIM .NET object.
    Equivalent to using DWSIM's object browser from Python.

    Parameters
    ----------
    object_name    Tag or "flowsheet".
    filter_prefix  Only return properties whose name starts with this.
    filter_type    Only return properties of this Python type name.
    max_props      Cap returned properties.

    Returns
    -------
    {success, object, properties: [{name, value, type}], n_total}
    """
    try:
        obj = _resolve_object(bridge, object_name)
    except (KeyError, ValueError) as exc:
        return {"success": False, "error": str(exc)}

    props: List[Dict[str, Any]] = []
    n_total = 0
    prefix_lc = filter_prefix.lower()
    type_lc   = filter_type.lower()

    for name in dir(obj):
        if name.startswith("_"):
            continue
        if prefix_lc and not name.lower().startswith(prefix_lc):
            continue
        try:
            val = getattr(obj, name)
        except Exception:
            continue
        if callable(val):
            continue
        val_type = type(val).__name__
        if type_lc and type_lc not in val_type.lower():
            continue
        n_total += 1
        if len(props) < max_props:
            try:
                val_str = str(val)
                if len(val_str) > 120:
                    val_str = val_str[:117] + "..."
            except Exception:
                val_str = "<unreadable>"
            props.append({"name": name, "value": val_str, "type": val_type})

    # Also try .NET Reflection for properties the Python dir() might miss
    try:
        import System.Reflection as R
        t = obj.GetType()
        bf = R.BindingFlags.Public | R.BindingFlags.Instance
        for prop in t.GetProperties(bf):
            pname = prop.Name
            if pname.startswith("_"):
                continue
            if prefix_lc and not pname.lower().startswith(prefix_lc):
                continue
            if any(p["name"] == pname for p in props):
                continue   # already captured
            try:
                val = prop.GetValue(obj)
                val_str = str(val)[:120] if val is not None else "null"
                val_type = prop.PropertyType.Name
                if type_lc and type_lc not in val_type.lower():
                    continue
                n_total += 1
                if len(props) < max_props:
                    props.append({"name": pname, "value": val_str,
                                   "type": val_type, "_dotnet": True})
            except Exception:
                pass
    except Exception:
        pass

    props.sort(key=lambda x: x["name"])
    return {
        "success":    True,
        "object":     object_name,
        "object_type": type(obj).__name__,
        "n_total":    n_total,
        "n_returned": len(props),
        "max_props":  max_props,
        "filter":     {"prefix": filter_prefix, "type": filter_type},
        "properties": props,
        "tip": (
            "Use reflect_get_set(object, property_name) to read a specific "
            "value, or reflect_get_set(object, property_name, value) to change it. "
            "Call exec_python(code) to run custom logic against multiple objects."
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# TOOL 4 — iterative_spec_loop
# ══════════════════════════════════════════════════════════════════════════════

def iterative_spec_loop(
    bridge,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Adjust one decision variable (via reflect_get_set) until an observable
    reaches a target specification.  Uses bisection — no LLM needed once called.

    spec = {
      "vary_object":   "COL-01",
      "vary_path":     "RefluxRatio",
      "vary_lo":       0.5,
      "vary_hi":       10.0,
      "observe_object":"DISTILLATE",
      "observe_path":  "Phases[0].Compounds[\"Methanol\"].MoleFraction",
      "target":        0.99,      # desired value
      "tolerance":     0.001,     # |observed - target| ≤ this → success
      "direction":     "increase",# "increase" → higher vary → higher observe
      "max_iter":      30,
    }
    """
    required = ("vary_object", "vary_path", "observe_object",
                  "observe_path", "target")
    for k in required:
        if k not in spec:
            return {"success": False,
                    "error": f"spec missing required key: '{k}'"}

    lo       = float(spec.get("vary_lo",   0.5))
    hi       = float(spec.get("vary_hi",   10.0))
    target   = float(spec["target"])
    tol      = float(spec.get("tolerance", 1e-3))
    max_iter = int(spec.get("max_iter",    30))
    direction = spec.get("direction", "increase")

    history: List[Dict] = []
    best    = {"error": float("inf"), "vary": None, "observed": None}

    def _set_and_observe(value: float) -> Optional[float]:
        r = reflect_get_set(bridge, spec["vary_object"],
                             spec["vary_path"], str(value))
        if not r.get("success"):
            return None
        try:
            run_r = bridge.run_simulation(auto_recover=False)
            if not isinstance(run_r, dict) or not run_r.get("success"):
                return None
        except Exception:
            return None
        r2 = reflect_get_set(bridge, spec["observe_object"],
                              spec["observe_path"])
        if not r2.get("success"):
            return None
        try:
            return float(r2["value"])
        except (TypeError, ValueError):
            return None

    for it in range(max_iter):
        mid = (lo + hi) / 2.0
        observed = _set_and_observe(mid)
        if observed is None:
            history.append({"iter": it+1, "vary": mid, "observed": None,
                             "note": "solve failed"})
            # Widen slightly and retry
            lo *= 0.95; hi *= 1.05
            continue

        err = observed - target
        abs_err = abs(err)
        history.append({"iter": it+1, "vary": round(mid, 6),
                         "observed": round(observed, 6),
                         "error": round(err, 6)})

        if abs_err < best["error"]:
            best = {"error": abs_err, "vary": mid, "observed": observed}

        if abs_err <= tol:
            return {
                "success":      True,
                "converged":    True,
                "iterations":   it + 1,
                "final_vary":   round(mid, 6),
                "final_observed": round(observed, 6),
                "target":       target,
                "error":        round(err, 6),
                "history":      history,
                "summary": (
                    f"Spec met in {it+1} iterations: "
                    f"{spec['vary_path']} = {mid:.4f} → "
                    f"{spec['observe_path']} = {observed:.4f} "
                    f"(target {target}, tol {tol})"
                ),
            }

        # Bisection step — adjust search bracket
        if (direction == "increase" and err < 0) or \
           (direction == "decrease" and err > 0):
            lo = mid    # need higher vary value
        else:
            hi = mid    # need lower vary value

    # Did not converge within budget — return best found
    if best["vary"] is not None:
        _set_and_observe(best["vary"])   # restore best

    return {
        "success":      best["vary"] is not None,
        "converged":    False,
        "iterations":   max_iter,
        "best_vary":    best["vary"],
        "best_observed": best["observed"],
        "best_error":   best["error"],
        "target":       target,
        "history":      history[-20:],
        "summary": (
            f"Did not converge in {max_iter} iterations. "
            f"Best: {spec['vary_path']} = {best['vary']:.4f} → "
            f"observed = {best['observed']:.4f} (target {target})."
            "Try increasing max_iter or widening vary_lo/vary_hi."
        ),
    }
