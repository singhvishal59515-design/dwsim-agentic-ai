"""
solver_setup.py
───────────────
Make the IDAES-bundled optimisation solvers (installed by `idaes
get-extensions` into ~/AppData/Local/idaes/bin) discoverable PROJECT-WIDE.

Why this exists: the binaries ship with ipopt.exe / bonmin.exe / couenne.exe /
cbc.exe and their DLLs, but the directory is NOT on PATH. So a bare
`SolverFactory("ipopt")` reports available=False even though IPOPT is present and
works — only eo_optimizer's private explicit-path fallback found it, and BONMIN /
COUENNE (MINLP + deterministic-global) were unreachable entirely.

Calling register_idaes_solvers() once at startup prepends that bin dir to PATH
(and to the DLL search path on Windows), so Pyomo's normal solver lookup finds
all of them. Idempotent and best-effort: never raises.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

# Solvers we expect from the IDAES extensions bundle.
_SOLVER_EXES = ("ipopt", "bonmin", "couenne", "cbc", "clp")

_registered = False


def idaes_bin_dir() -> Optional[str]:
    """Return the IDAES solver bin directory if it exists, else None."""
    home = os.path.expanduser("~")
    for d in (
        os.path.join(home, "AppData", "Local", "idaes", "bin"),  # Windows default
        os.path.join(home, ".idaes", "bin"),                     # Linux/Mac default
    ):
        if os.path.isdir(d):
            return d
    return None


def register_idaes_solvers() -> Dict[str, object]:
    """Prepend the IDAES bin dir to PATH so Pyomo finds ipopt/bonmin/couenne/cbc.

    Returns {registered: bool, bin_dir: str|None, available: [solver names]}.
    Idempotent; safe to call repeatedly. Never raises.
    """
    global _registered
    bin_dir = idaes_bin_dir()
    if bin_dir is None:
        return {"registered": False, "bin_dir": None, "available": []}

    try:
        # Prepend to PATH (so the bundled exes + their DLLs resolve).
        parts = os.environ.get("PATH", "").split(os.pathsep)
        if bin_dir not in parts:
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        # On modern Windows/Python the DLL search also needs this explicitly.
        if hasattr(os, "add_dll_directory") and os.name == "nt":
            try:
                os.add_dll_directory(bin_dir)
            except Exception:
                pass
        _registered = True
    except Exception:
        return {"registered": False, "bin_dir": bin_dir, "available": []}

    return {"registered": True, "bin_dir": bin_dir,
            "available": available_solvers()}


def available_solvers() -> List[str]:
    """Names of the bundled solvers Pyomo can actually invoke right now.

    A solver counts as available if the executable resolves on PATH (the
    reliable signal) OR Pyomo's own check passes. The which() fallback matters
    because Pyomo's specialised IPOPT interface sometimes reports available=False
    on a cold first probe even though the binary is present and solves fine.
    """
    import shutil
    found: List[str] = []
    try:
        from pyomo.environ import SolverFactory  # type: ignore
    except Exception:
        SolverFactory = None  # type: ignore
    for name in _SOLVER_EXES:
        ok = shutil.which(name) is not None
        if not ok and SolverFactory is not None:
            try:
                ok = bool(SolverFactory(name).available(exception_flag=False))
            except Exception:
                ok = False
        if ok:
            found.append(name)
    return found
