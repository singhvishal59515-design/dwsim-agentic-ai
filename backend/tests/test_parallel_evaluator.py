"""
Parallel flowsheet evaluation pool. Validated with a mock (sleepy) evaluator —
no DWSIM needed — for both CORRECTNESS (parallel results identical to serial,
order preserved) and SPEEDUP (wall-clock drops with workers). The DWSIM
specialisation `make_dwsim_evaluator` reuses the exact same pool.
"""
from __future__ import annotations
import os
import sys
import time

_B = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _B not in sys.path:
    sys.path.insert(0, _B)

import pytest
from parallel_evaluator import (ProcessPoolEvaluator, parallel_map,
                                make_sleepy_evaluator)


_X = [[i * 0.1, -i * 0.2] for i in range(12)]


def _serial(delay):
    ev = make_sleepy_evaluator(delay)
    return [ev(x) for x in _X]


def test_parallel_matches_serial_exactly():
    serial = _serial(0.0)
    par = parallel_map(make_sleepy_evaluator, (0.0,), _X, n_workers=4)
    assert len(par) == len(serial)
    # Objective is deterministic (sphere): parallel must equal serial, in order.
    for p, s in zip(par, serial):
        assert p["objective"] == pytest.approx(s["objective"])


def test_input_order_is_preserved():
    par = parallel_map(make_sleepy_evaluator, (0.0,), _X, n_workers=3)
    expected = [pytest.approx(sum(c * c for c in x)) for x in _X]
    assert [p["objective"] for p in par] == expected


def test_pool_is_reusable_across_batches():
    with ProcessPoolEvaluator(make_sleepy_evaluator, (0.0,), n_workers=2) as pool:
        a = pool.map(_X[:4])
        b = pool.map(_X[4:])
    assert len(a) == 4 and len(b) == 8


@pytest.mark.timeout(60)
def test_parallel_is_faster_than_serial():
    delay = 0.15                       # stand-in for a DWSIM solve
    t0 = time.monotonic(); _serial(delay); serial = time.monotonic() - t0
    t0 = time.monotonic()
    parallel_map(make_sleepy_evaluator, (delay,), _X, n_workers=4)
    par = time.monotonic() - t0
    # 12 × 0.15 s = 1.8 s serial; 4 workers should beat it clearly even after
    # process-spawn overhead. Lenient bound to stay robust on a loaded machine.
    assert par < serial * 0.8, f"serial={serial:.2f}s parallel={par:.2f}s"
