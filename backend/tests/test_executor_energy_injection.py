"""
Regression: execute_build_plan (the step-wise build path) must also auto-create
an EnergyStream object for energy-requiring unit ops that the plan omitted, and
wire it — mirroring the topology builder. Uses a fake bridge (no DWSIM needed).
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from flowsheet_executor import execute_build_plan


class _FakeBridge:
    def __init__(self):
        self.objects = []        # (tag, type)
        self.connections = []    # (from, to, from_port, to_port)

    def new_flowsheet(self, name, compounds, property_package):
        return {"success": True, "name": name}

    def add_object(self, tag, type):
        self.objects.append((tag, type))
        return {"success": True}

    def connect_streams(self, from_tag, to_tag, from_port=0, to_port=0):
        self.connections.append((from_tag, to_tag, from_port, to_port))
        return {"success": True}

    def set_stream_property(self, **k):
        return {"success": True}


def _heater_plan():
    return {
        "name": "water_heating_process",
        "compounds": ["Water"],
        "property_package": "Steam Tables (IAPWS-IF97)",
        "unit_ops": [{"tag": "Heater", "type": "Heater"}],
        "streams": [{"tag": "Feed"}, {"tag": "Product"}],
        "connections": [{"from": "Feed", "to": "Heater"},
                        {"from": "Heater", "to": "Product"}],
    }


def test_executor_adds_energy_stream_to_heater():
    br = _FakeBridge()
    res = execute_build_plan(_heater_plan(), br, solve=False)
    assert res["success"], res

    # An EnergyStream object was created for the heater.
    es = [t for (t, ty) in br.objects if ty == "EnergyStream"]
    assert es == ["Q-Heater"], br.objects

    # And it was wired into the heater's energy port (index 1).
    wired = [c for c in br.connections if c[1] == "Heater" and c[3] == 1]
    assert wired and wired[0][0] == "Q-Heater", br.connections


def test_executor_no_double_add_when_present():
    plan = _heater_plan()
    plan["unit_ops"][0]["tag"] = "H-101"
    plan["streams"].append({"tag": "Q"})
    plan["connections"] = [
        {"from": "Feed", "to": "H-101"},
        {"from": "H-101", "to": "Product"},
        {"from": "Q", "to": "H-101", "from_port": 0, "to_port": 1},
    ]
    # Mark Q as an energy stream so the rule sees it as already wired.
    plan["streams"] = [{"tag": "Feed"}, {"tag": "Product"},
                       {"tag": "Q", "type": "EnergyStream"}]
    br = _FakeBridge()
    res = execute_build_plan(plan, br, solve=False)
    assert res["success"], res
    auto = [t for (t, ty) in br.objects if ty == "EnergyStream" and t != "Q"]
    assert auto == [], br.objects


def test_executor_mixer_gets_no_energy_stream():
    plan = {
        "name": "mix", "compounds": ["Water"],
        "property_package": "Steam Tables (IAPWS-IF97)",
        "unit_ops": [{"tag": "MIX", "type": "Mixer"}],
        "streams": [{"tag": "A"}, {"tag": "B"}, {"tag": "Out"}],
        "connections": [{"from": "A", "to": "MIX"}, {"from": "B", "to": "MIX"},
                        {"from": "MIX", "to": "Out"}],
    }
    br = _FakeBridge()
    res = execute_build_plan(plan, br, solve=False)
    assert res["success"], res
    assert not any(ty == "EnergyStream" for (_, ty) in br.objects)
