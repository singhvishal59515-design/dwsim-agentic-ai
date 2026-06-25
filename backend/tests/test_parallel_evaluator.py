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


def test_run_parallel_de_finds_sphere_optimum():
    from parallel_evaluator import run_parallel_de
    res = run_parallel_de(make_sleepy_evaluator, (0.0,),
                          bounds=[(-5, 5), (-5, 5)], popsize=12, generations=30,
                          n_workers=2, seed=1)
    assert res["success"]
    assert res["objective"] < 0.05, res         # sphere min at origin
    assert all(abs(xi) < 0.3 for xi in res["x"])
    assert res["evaluations"] == 12 * 31


@pytest.mark.timeout(120)
def test_persistent_pool_amortises_init():
    # #2: a persistent pool pays the per-worker init ONCE across generations; a
    # fresh pool per generation re-pays it. Relative comparison → robust to
    # machine speed. make_init_cost_evaluator simulates the ~30 s CLR init.
    from parallel_evaluator import run_parallel_de, make_init_cost_evaluator
    G, nw, init_s = 2, 2, 0.3
    bounds = [(-2.0, 2.0), (-2.0, 2.0)]
    t0 = time.monotonic()
    run_parallel_de(make_init_cost_evaluator, (init_s, 0.01), bounds=bounds,
                    popsize=8, generations=G, n_workers=nw, seed=2)
    t_persist = time.monotonic() - t0
    X = [[0.1, 0.1]] * 8
    t0 = time.monotonic()
    for _ in range(G + 1):                       # fresh pool each generation
        parallel_map(make_init_cost_evaluator, (init_s, 0.01), X, n_workers=nw)
    t_reinit = time.monotonic() - t0
    assert t_persist < t_reinit, (
        f"persistent={t_persist:.2f}s should beat per-gen re-init={t_reinit:.2f}s")
