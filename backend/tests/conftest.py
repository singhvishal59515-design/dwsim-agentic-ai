"""Shared pytest fixtures. The bridge fixture is session-scoped so we only
spin up DWSIM's .NET runtime once per test run."""
import glob
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def bridge():
    from dwsim_bridge_v2 import DWSIMBridgeV2
    b = DWSIMBridgeV2()
    r = b.initialize()
    assert r.get("success"), f"bridge init failed: {r}"
    return b


@pytest.fixture()
def sample_flowsheet(tmp_path):
    """Return a path to a loadable FOSSEE sample copied into tmp_path."""
    fossee = r"c:\Users\hp\AppData\Local\DWSIM\FOSSEE"
    for preferred in ("CoolingTower.dwxmz", "ESP_MultipleCompound.dwxmz",
                      "batchreactor.dwxmz"):
        hits = glob.glob(os.path.join(fossee, "*", preferred))
        if hits:
            src = hits[0]
            break
    else:
        pytest.skip("no FOSSEE sample available")
    dst = tmp_path / os.path.basename(src)
    shutil.copy2(src, dst)
    return str(dst)
