"""
Regression: the free-build path must auto-attach an energy stream to any
energy-requiring unit op (Heater, Cooler, Pump, Compressor, Expander) that was
created without one.

Bug it guards: "Create a water heating process ..." produced a Heater named
"Heater" with NO energy stream (no duty connector), so DWSIM could not compute
or report its duty and the flowsheet would not fully converge — unlike the
template path, which always wires Q to the heater's port 1.
"""
from __future__ import annotations
import os
import sys

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

from flowsheet_builder import _inject_missing_energy_streams


def _run(streams, unit_ops, connections):
    warnings = []
    s, u, c = _inject_missing_energy_streams(
        list(streams), list(unit_ops), list(connections), warnings)
    return s, u, c, warnings


def _energy_into(connections, uo_tag, port=1):
    return [c for c in connections
            if c.get("to") == uo_tag and int(c.get("to_port", 0)) == port]


def test_heater_without_energy_gets_one():
    # The exact reported bug: a heater wired Feed->Heater->Product, no energy.
    streams = [{"tag": "Feed", "type": "MaterialStream"},
               {"tag": "Product", "type": "MaterialStream"}]
    unit_ops = [{"tag": "Heater", "type": "Heater"}]
    connections = [{"from": "Feed", "to": "Heater"},
                   {"from": "Heater", "to": "Product"}]

    s, u, c, w = _run(streams, unit_ops, connections)

    assert _energy_into(c, "Heater"), "heater should now have an energy stream on port 1"
    qtags = [x["tag"] for x in s if x.get("type") == "EnergyStream"]
    assert qtags == ["Q-Heater"]
    assert any("Auto-added energy stream" in m for m in w)


def test_heater_with_energy_is_untouched():
    # Template-style: energy already wired -> must be a no-op.
    streams = [{"tag": "Feed", "type": "MaterialStream"},
               {"tag": "Hot", "type": "MaterialStream"},
               {"tag": "Q", "type": "EnergyStream"}]
    unit_ops = [{"tag": "H-101", "type": "Heater"}]
    connections = [{"from": "Feed", "to": "H-101", "from_port": 0, "to_port": 0},
                   {"from": "H-101", "to": "Hot", "from_port": 0, "to_port": 0},
                   {"from": "Q", "to": "H-101", "from_port": 0, "to_port": 1}]

    s, u, c, w = _run(streams, unit_ops, connections)

    assert len([x for x in s if x.get("type") == "EnergyStream"]) == 1
    assert not any("Auto-added" in m for m in w)
    assert len(c) == 3


def test_all_energy_requiring_types_covered():
    for t in ("Heater", "Cooler", "Pump", "Compressor", "Expander", "Turbine"):
        streams = [{"tag": "In", "type": "MaterialStream"},
                   {"tag": "Out", "type": "MaterialStream"}]
        unit_ops = [{"tag": "U1", "type": t}]
        connections = [{"from": "In", "to": "U1"}, {"from": "U1", "to": "Out"}]
        s, u, c, w = _run(streams, unit_ops, connections)
        assert _energy_into(c, "U1"), f"{t} should get an energy stream"


def test_non_energy_unit_untouched():
    # A Mixer needs no energy stream.
    streams = [{"tag": "A", "type": "MaterialStream"},
               {"tag": "B", "type": "MaterialStream"},
               {"tag": "M", "type": "MaterialStream"}]
    unit_ops = [{"tag": "MIX", "type": "Mixer"}]
    connections = [{"from": "A", "to": "MIX"}, {"from": "B", "to": "MIX"},
                   {"from": "MIX", "to": "M"}]
    s, u, c, w = _run(streams, unit_ops, connections)
    assert not any(x.get("type") == "EnergyStream" for x in s)
    assert not any("Auto-added" in m for m in w)


def test_energy_tag_does_not_collide():
    # A pre-existing "Q-Heater" forces a unique suffix.
    streams = [{"tag": "Feed", "type": "MaterialStream"},
               {"tag": "Q-Heater", "type": "MaterialStream"}]
    unit_ops = [{"tag": "Heater", "type": "Heater"}]
    connections = [{"from": "Feed", "to": "Heater"}]
    s, u, c, w = _run(streams, unit_ops, connections)
    new = [x["tag"] for x in s if x.get("type") == "EnergyStream"]
    assert new == ["Q-Heater-2"]


def test_multiple_heaters_each_get_distinct_streams():
    streams = [{"tag": "Feed", "type": "MaterialStream"}]
    unit_ops = [{"tag": "H1", "type": "Heater"}, {"tag": "H2", "type": "Heater"}]
    connections = []
    s, u, c, w = _run(streams, unit_ops, connections)
    new = sorted(x["tag"] for x in s if x.get("type") == "EnergyStream")
    assert new == ["Q-H1", "Q-H2"]
