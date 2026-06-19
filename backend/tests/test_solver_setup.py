"""
Tests for solver_setup — making the IDAES-bundled solvers (ipopt/bonmin/
couenne/cbc) discoverable on PATH.

The binaries ship via `idaes get-extensions` but the bin dir is not on PATH, so
a bare SolverFactory("ipopt") reported available=False even though IPOPT is
present and solves. register_idaes_solvers() fixes that process-wide.

These tests are environment-tolerant: where the bundle is absent (e.g. CI) they
assert graceful no-op behaviour rather than requiring the binaries.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import solver_setup


def test_register_is_idempotent_and_never_raises():
    a = solver_setup.register_idaes_solvers()
    b = solver_setup.register_idaes_solvers()
    assert set(a.keys()) == {"registered", "bin_dir", "available"}
    assert a["registered"] == b["registered"]


def test_bin_dir_on_path_when_present():
    bin_dir = solver_setup.idaes_bin_dir()
    if bin_dir is None:
        # No bundle in this environment — registration must be a clean no-op.
        r = solver_setup.register_idaes_solvers()
        assert r["registered"] is False and r["available"] == []
        return
    solver_setup.register_idaes_solvers()
    assert bin_dir in os.environ.get("PATH", "").split(os.pathsep)


def test_ipopt_discoverable_after_register_when_present():
    bin_dir = solver_setup.idaes_bin_dir()
    if bin_dir is None or not os.path.exists(os.path.join(bin_dir, "ipopt.exe")):
        return  # bundle/ipopt not present here; nothing to assert
    solver_setup.register_idaes_solvers()
    import shutil
    assert shutil.which("ipopt") is not None
    assert "ipopt" in solver_setup.available_solvers()
