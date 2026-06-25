"""
parallel_evaluator.py
─────────────────────
Parallel flowsheet evaluation across multiple DWSIM engines.

DWSIM runs in-process via pythonnet — ONE CLR per process — so a single server
process can only solve one flowsheet at a time. Population optimizers (NSGA-II,
CMA-ES), global sensitivity (Sobol), and parametric sweeps all evaluate a BATCH
of designs per step and are therefore starved by that serialization.

This module runs N **separate worker processes**, each hosting its OWN pythonnet
CLR and its OWN copy of the flowsheet (loaded once from a file), fed a queue of
decision-variable vectors. Independent designs solve concurrently.

WHEN IT PAYS OFF (measured on real DWSIM): each worker must initialise its own
CLR, costing ~30 s. So the pool only beats the single in-process CLR when that
init is amortised — a PERSISTENT pool reused across many batches (e.g. NSGA-II
generations) with non-trivial per-solve time. Rough breakeven:
total_solve_work >= n_workers * ~30 s. For a one-shot SMALL/FAST batch the single
CLR wins (a live 8 x 0.5 s batch came out ~9x SLOWER under 4 workers). The
correctness guarantee (parallel == serial) holds regardless. To get the benefit,
create the pool ONCE (ProcessPoolEvaluator context manager) and reuse `.map`.

Design
------
`ProcessPoolExecutor` with a per-worker `initializer` that builds the expensive
per-worker state (bridge + loaded flowsheet) ONCE; tasks then reference it via a
module global, so the ~30 s DWSIM init is paid once per worker, not per eval.
The evaluator is supplied by a picklable FACTORY so the pool is generic and
unit-testable with a mock (no DWSIM needed); `make_dwsim_evaluator` is the real
specialization.

Falls back transparently to serial evaluation if a worker can't start (e.g. no
DWSIM on the box) — never worse than the single-CLR path.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# Per-worker evaluator, built once by the pool initializer and reused per task.
_WORKER_EVAL: Optional[Callable[[Sequence[float]], Dict[str, Any]]] = None


def _init_worker(factory: Callable[..., Callable], factory_args: tuple) -> None:
    global _WORKER_EVAL
    _WORKER_EVAL = factory(*factory_args)


def _eval_one(x):
    assert _WORKER_EVAL is not None, "worker evaluator not initialised"
    return _WORKER_EVAL(list(x))


class ProcessPoolEvaluator:
    """N-process pool. `factory(*factory_args)` is called ONCE per worker and
    must return a callable evaluate(x)->result; results from `map` are returned
    in input order. Both factory and args must be picklable (module-level
    function + plain data)."""

    def __init__(self, factory: Callable[..., Callable], factory_args: tuple = (),
                 n_workers: int = 4):
        self.n_workers = max(1, int(n_workers))
        self._factory = factory
        self._factory_args = factory_args
        self._ex: Optional[ProcessPoolExecutor] = None

    def __enter__(self):
        self._ex = ProcessPoolExecutor(
            max_workers=self.n_workers,
            initializer=_init_worker,
            initargs=(self._factory, self._factory_args),
        )
        return self

    def __exit__(self, *exc):
        self.close()

    def map(self, X: Sequence[Sequence[float]]) -> List[Dict[str, Any]]:
        """Evaluate every x in X across the worker pool (input order preserved).
        Falls back to serial on any pool failure so a result is always returned."""
        X = [list(x) for x in X]
        if self._ex is None:
            self.__enter__()
        try:
            return list(self._ex.map(_eval_one, X))   # type: ignore[union-attr]
        except Exception:
            # Pool died (worker init failed / DWSIM unavailable): serial fallback.
            ev = self._factory(*self._factory_args)
            return [ev(list(x)) for x in X]

    def close(self):
        if self._ex is not None:
            self._ex.shutdown(wait=True)
            self._ex = None


def parallel_map(factory: Callable[..., Callable], factory_args: tuple,
                 X: Sequence[Sequence[float]], n_workers: int = 4
                 ) -> List[Dict[str, Any]]:
    """One-shot convenience: spin up the pool, evaluate X, tear down."""
    with ProcessPoolEvaluator(factory, factory_args, n_workers) as pool:
        return pool.map(X)


# ── Persistent-pool population optimizer (#2: amortise CLR init) ─────────────

def run_parallel_de(factory: Callable[..., Callable], factory_args: tuple,
                    bounds: Sequence[Tuple[float, float]], *,
                    popsize: int = 0, generations: int = 20, n_workers: int = 4,
                    seed: int = 42, minimize: bool = True, F: float = 0.7,
                    CR: float = 0.9) -> Dict[str, Any]:
    """Differential evolution whose every generation's whole population is
    evaluated through a SINGLE PERSISTENT worker pool.

    This is the piece that turns the worker pool from a net loss into a win for
    population optimisers: the pool — and its ~30 s/worker DWSIM CLR
    initialisation — is created ONCE (the `with` block) and reused across all
    `generations`, so the init is amortised over generations * popsize solves
    instead of being paid per batch. A one-shot `parallel_map` per generation
    would re-pay it every generation; this does not.

    `factory(*factory_args) -> evaluate(x) -> {"objective": float|None, ...}`
    (the same picklable factory the pool uses, e.g. `make_dwsim_evaluator`).
    Falls back to serial inside the pool on worker failure. Returns
    {success, x, objective, generations, popsize, n_workers, evaluations}.
    """
    import random
    rng = random.Random(seed)
    d = len(bounds)
    lo = [float(b[0]) for b in bounds]
    hi = [float(b[1]) for b in bounds]
    popsize = int(popsize) if popsize and popsize > 0 else max(8, 5 * d)
    sign = 1.0 if minimize else -1.0
    _PEN = 1e18

    def _clamp(v):
        return [min(hi[k], max(lo[k], v[k])) for k in range(d)]

    pop = [[rng.uniform(lo[i], hi[i]) for i in range(d)] for _ in range(popsize)]

    with ProcessPoolEvaluator(factory, factory_args, n_workers) as pool:
        def fit(X: List[List[float]]) -> List[float]:
            out = []
            for r in pool.map(X):
                o = r.get("objective") if isinstance(r, dict) else None
                out.append(sign * float(o) if o is not None else _PEN)
            return out

        fpop = fit(pop)
        for _g in range(generations):
            trials = []
            for i in range(popsize):
                a, b, c = rng.sample([j for j in range(popsize) if j != i], 3)
                jr = rng.randrange(d)
                trial = [(pop[a][k] + F * (pop[b][k] - pop[c][k]))
                         if (rng.random() < CR or k == jr) else pop[i][k]
                         for k in range(d)]
                trials.append(_clamp(trial))
            ftr = fit(trials)                      # whole generation, one pool.map
            for i in range(popsize):
                if ftr[i] <= fpop[i]:
                    pop[i], fpop[i] = trials[i], ftr[i]

    best = min(range(popsize), key=lambda i: fpop[i])
    return {"success": True, "x": pop[best], "objective": sign * fpop[best],
            "generations": generations, "popsize": popsize,
            "n_workers": n_workers, "evaluations": popsize * (generations + 1)}


def make_init_cost_evaluator(init_s: float = 0.5, eval_s: float = 0.02):
    """Mock factory that SLEEPS `init_s` when constructed (stands in for the
    ~30 s per-worker DWSIM CLR init) and `eval_s` per evaluation. Used to
    demonstrate that a persistent pool pays the init once, not per batch."""
    time.sleep(init_s)                      # paid once per worker, in the initializer

    def evaluate(x: Sequence[float]) -> Dict[str, Any]:
        time.sleep(eval_s)
        return {"objective": float(sum(xi * xi for xi in x)), "constraint_values": []}
    return evaluate


# ── DWSIM specialisation ─────────────────────────────────────────────────────

def make_dwsim_evaluator(flowsheet_path: str,
                         variables: List[Dict[str, Any]],
                         observe_tag: str,
                         observe_property: str,
                         constraint_specs: Optional[List[Dict[str, Any]]] = None,
                         dll_folder: Optional[str] = None
                         ) -> Callable[[Sequence[float]], Dict[str, Any]]:
    """Build a per-worker evaluate(x) that drives a private DWSIM engine: set the
    decision variables, solve, and read the objective + constraint quantities.
    Initialises the bridge and loads the flowsheet ONCE (this runs in the worker
    initializer); the returned closure is then cheap to call per design."""
    from dwsim_bridge_v2 import DWSIMBridgeV2, _route_set_variable
    from dwsim_native_optimizer import _read_object_property
    bridge = DWSIMBridgeV2(dll_folder=dll_folder)
    bridge.initialize()
    bridge.load_flowsheet(flowsheet_path)
    cons = constraint_specs or []

    def _read(tag, prop):
        # Robust read: stream/unit-op properties, with a .NET-reflection fallback
        # for unit-op quantities (HeatDuty, etc.) the bulk readers don't expose.
        return _read_object_property(bridge, tag, prop)

    def evaluate(x: Sequence[float]) -> Dict[str, Any]:
        for v, xi in zip(variables, x):
            _route_set_variable(bridge, v["tag"], v["property"], float(xi),
                                v.get("unit", ""))
        if not bridge.run_simulation().get("success"):
            return {"objective": None, "constraint_values": [None] * len(cons)}
        obj = _read(observe_tag, observe_property)
        cvals = [_read(c["tag"], c["property"]) for c in cons]
        return {"objective": (float(obj) if obj is not None else None),
                "constraint_values": [float(v) if v is not None else None
                                      for v in cvals]}
    return evaluate


# ── Test / demo factory (module-level so it's picklable for spawn) ───────────

def make_sleepy_evaluator(delay_s: float = 0.1):
    """A mock per-worker evaluator: a sphere objective plus a fixed delay that
    stands in for a DWSIM solve. Lets the pool's parallelism and correctness be
    validated without a DWSIM install."""
    def evaluate(x: Sequence[float]) -> Dict[str, Any]:
        time.sleep(delay_s)
        return {"objective": float(sum(xi * xi for xi in x)),
                "constraint_values": []}
    return evaluate


def _self_demo() -> Tuple[float, float]:
    """Measure serial vs 4-worker wall-clock on a sleepy batch. Returns
    (serial_s, parallel_s)."""
    X = [[i * 0.1, -i * 0.1] for i in range(16)]
    t0 = time.monotonic()
    ev = make_sleepy_evaluator(0.15)
    [ev(x) for x in X]
    serial = time.monotonic() - t0
    t0 = time.monotonic()
    parallel_map(make_sleepy_evaluator, (0.15,), X, n_workers=4)
    par = time.monotonic() - t0
    return serial, par


if __name__ == "__main__":
    s, p = _self_demo()
    print(f"serial={s:.2f}s  parallel(4 workers)={p:.2f}s  speedup={s / p:.2f}x")
