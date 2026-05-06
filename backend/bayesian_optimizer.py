"""
bayesian_optimizer.py
─────────────────────
Gaussian Process Bayesian Optimization for expensive DWSIM simulations.

Zero new dependencies — implemented entirely in NumPy (already installed).
No scikit-learn, GPy, or bayesian-optimization packages required.

Algorithm:
  1. Latin Hypercube Sampling (LHS) — diverse initial exploration
  2. Gaussian Process (RBF kernel + white-noise regularisation) surrogate
  3. Expected Improvement (EI) acquisition function
  4. Iterates until max_iter budget is exhausted or 5 consecutive no-improve

Why Bayesian Optimisation over differential_evolution for DWSIM?
  Each DWSIM solve costs 1–30 seconds. BO finds good solutions in 15–30
  evaluations vs 200–500 for DE, by building a probabilistic model of the
  objective and querying only the most promising regions.
  Preferred for: distillation column tuning, reactor conversion optimisation,
  HEN synthesis — any case with >2 variables or slow convergence.

Usage (programmatic):
    from bayesian_optimizer import BayesianOptimizer

    result = BayesianOptimizer(
        bounds   = {'temperature': (200.0, 350.0), 'pressure': (10.0, 80.0)},
        n_initial= 5, max_iter=20, minimize=False, seed=42,
    ).run(lambda p: get_purity(p['temperature'], p['pressure']))

    print(result.best_params, result.best_value)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BOResult:
    """Complete result of one Bayesian Optimisation run."""
    best_params:    Dict[str, float]       # variable → value at optimum
    best_value:     float                  # objective value at optimum
    n_evals:        int                    # total DWSIM evaluations used
    n_initial:      int                    # LHS points used for warm-up
    converged:      bool                   # True if EI improvement < tol × 5 iters
    history:        List[Dict[str, Any]]   # per-iteration trace
    param_names:    List[str]
    bounds:         Dict[str, Tuple[float, float]]
    minimize:       bool
    duration_s:     float
    convergence_plot: str = ""             # path to PNG if saved, else ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("param_names", None)
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Latin Hypercube Sampling
# ─────────────────────────────────────────────────────────────────────────────

def _lhs(n: int, d: int, rng: np.random.Generator) -> np.ndarray:
    """Return (n × d) Latin Hypercube sample in [0, 1]^d."""
    result = np.zeros((n, d))
    for j in range(d):
        perm = rng.permutation(n)
        result[:, j] = (perm + rng.uniform(size=n)) / n
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Gaussian Process (RBF + white noise kernel)
# ─────────────────────────────────────────────────────────────────────────────

class _GP:
    """
    Exact GP regression.
    Kernel: k(x,x') = sf2 * exp(-0.5 * ||x-x'||^2 / l^2) + sn2 * I
    Hyperparameters fitted by grid search over log marginal likelihood.
    Inputs must be normalised to [0,1]^d before calling fit().
    """

    def __init__(self) -> None:
        self.l    = 0.5     # length-scale
        self.sf2  = 1.0     # signal variance
        self.sn2  = 1e-4    # noise variance
        self._X: Optional[np.ndarray] = None
        self._L: Optional[np.ndarray] = None
        self._alpha: Optional[np.ndarray] = None

    def _K(self, A: np.ndarray, B: np.ndarray) -> np.ndarray:
        d2 = np.sum((A[:, None] - B[None]) ** 2, axis=-1)
        return self.sf2 * np.exp(-0.5 * d2 / self.l ** 2)

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        n = len(y)
        best_lml, best_l, best_sf2 = -np.inf, self.l, self.sf2
        any_succeeded = False
        for l in (0.1, 0.2, 0.5, 1.0, 2.0):
            for sf2 in (0.1, 0.5, 1.0, 2.0):
                self.l, self.sf2 = l, sf2
                # Add progressively larger jitter to handle near-singular matrices
                for jitter in (1e-6, 1e-4, 1e-2):
                    K = self._K(X, X) + (self.sn2 + jitter) * np.eye(n)
                    try:
                        L = np.linalg.cholesky(K)
                        any_succeeded = True
                        break
                    except np.linalg.LinAlgError:
                        continue
                else:
                    continue   # all jitter levels failed — skip this (l, sf2) pair
                a   = np.linalg.solve(L.T, np.linalg.solve(L, y))
                lml = -0.5 * y @ a - np.sum(np.log(np.diag(L)))
                if lml > best_lml:
                    best_lml, best_l, best_sf2 = lml, l, sf2

        if not any_succeeded:
            # Fallback: use identity covariance (mean predictor only)
            # This is safe — BO will explore randomly until more data arrives
            self.l, self.sf2, self.sn2 = 1.0, 1.0, 0.1
            best_l, best_sf2 = 1.0, 1.0
            import warnings
            warnings.warn("GP: all Cholesky attempts failed — using fallback identity kernel", RuntimeWarning)

        self.l, self.sf2 = best_l, best_sf2
        # Final fit with best hyperparameters + jitter escalation
        for jitter in (1e-6, 1e-4, 1e-2, 0.1):
            K = self._K(X, X) + (self.sn2 + jitter) * np.eye(n)
            try:
                L = np.linalg.cholesky(K)
                break
            except np.linalg.LinAlgError:
                continue
        else:
            # Ultimate fallback: diagonal matrix (pure noise model)
            L = np.eye(n) * (np.std(y) + 1e-6)
        self._X     = X.copy()
        self._L     = L
        self._alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))

    def predict(self, Xp: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        Ks  = self._K(Xp, self._X)
        mu  = Ks @ self._alpha
        v   = np.linalg.solve(self._L, Ks.T)
        var = np.diag(self._K(Xp, Xp)) - np.sum(v ** 2, axis=0)
        return mu, np.maximum(var, 1e-10)


# ─────────────────────────────────────────────────────────────────────────────
# Expected Improvement acquisition
# ─────────────────────────────────────────────────────────────────────────────

def _erf_approx(z: np.ndarray) -> np.ndarray:
    """Vectorised erf approximation (no scipy needed)."""
    return np.array([math.erf(float(zi)) for zi in z])

def _ei(mu: np.ndarray, var: np.ndarray, f_best: float, xi: float = 0.01) -> np.ndarray:
    """Expected Improvement (minimisation convention)."""
    sigma = np.sqrt(var)
    imp   = f_best - mu - xi
    Z     = imp / (sigma + 1e-9)
    Phi   = 0.5 * (1.0 + _erf_approx(Z / math.sqrt(2)))
    phi   = np.exp(-0.5 * Z ** 2) / math.sqrt(2 * math.pi)
    ei    = imp * Phi + sigma * phi
    ei[sigma < 1e-9] = 0.0
    return ei


# ─────────────────────────────────────────────────────────────────────────────
# Convergence plot
# ─────────────────────────────────────────────────────────────────────────────

def plot_convergence(result: BOResult, out_path: str) -> str:
    """
    Save a convergence PNG to out_path.
    Returns path on success, '' if matplotlib is not available.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        its   = [h["iteration"]    for h in result.history]
        vals  = [h.get("value")    for h in result.history]
        bests = [h["best_so_far"]  for h in result.history]
        phases= [h.get("phase","BO") for h in result.history]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4))

        # Left: raw evaluations + best-so-far
        lx = [i for i,p in zip(its,phases) if p=="LHS"]
        ly = [v for v,p in zip(vals,phases) if p=="LHS" and v is not None]
        bx = [i for i,p in zip(its,phases) if p=="BO"]
        by = [v for v,p in zip(vals,phases) if p=="BO"  and v is not None]
        ax1.scatter(lx, ly, color="#38bdf8", s=45, zorder=3, label="LHS init")
        ax1.scatter(bx, by, color="#f472b6", s=45, zorder=3, label="BO query")
        ax1.plot(its, bests, color="#4ade80", lw=2, label="Best so far")
        ax1.axvline(result.n_initial + 0.5, color="#64748b", ls="--", alpha=0.5)
        ax1.set_xlabel("Evaluation #"); ax1.set_ylabel("Objective")
        ax1.set_title("Convergence history"); ax1.legend(fontsize=8); ax1.grid(alpha=0.3)

        # Right: improvement per BO iteration
        bo_h  = [h for h in result.history if h.get("phase") == "BO"]
        bo_ei = [h.get("ei_improvement", 0) or 0 for h in bo_h]
        bo_it = [h["iteration"] for h in bo_h]
        ax2.bar(bo_it, bo_ei, color="#818cf8", alpha=0.8)
        ax2.set_xlabel("BO Iteration #"); ax2.set_ylabel("Improvement over prev. best")
        ax2.set_title("EI improvement per BO step"); ax2.grid(alpha=0.3)

        fig.suptitle(
            f"Bayesian Optimisation  |  best={result.best_value:.4f}  "
            f"n_evals={result.n_evals}  converged={result.converged}",
            fontsize=11
        )
        fig.tight_layout()
        fig.savefig(out_path, dpi=130, bbox_inches="tight")
        plt.close(fig)
        return out_path
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# BayesianOptimizer — main public class
# ─────────────────────────────────────────────────────────────────────────────

class BayesianOptimizer:
    """
    Bayesian Optimisation with GP surrogate + EI acquisition.

    Parameters
    ----------
    bounds      : {name: (lo, hi)} — variable search space
    n_initial   : LHS warm-up samples (default 5)
    max_iter    : BO iterations after warm-up (default 20); total = n_initial + max_iter
    minimize    : True → minimise objective; False → maximise (default True)
    xi          : EI exploration bonus — higher → more exploration (default 0.01)
    grid_size   : EI maximisation random grid per dim, capped at 10 000 (default 50)
    tol         : convergence if |Δbest| < tol for 5 consecutive iters (default 1e-4)
    seed        : RNG seed for reproducibility (default 42)
    save_plot   : path to save PNG convergence plot, or '' to skip (default '')
    on_progress : callback(iter, params, value, best) called after each eval
    """

    def __init__(
        self,
        bounds:      Dict[str, Tuple[float, float]],
        n_initial:   int   = 5,
        max_iter:    int   = 20,
        minimize:    bool  = True,
        xi:          float = 0.01,
        grid_size:   int   = 50,
        tol:         float = 1e-4,
        seed:        int   = 42,
        save_plot:   str   = "",
        on_progress: Optional[Callable] = None,
    ) -> None:
        if not bounds:
            raise ValueError("bounds dict must not be empty")
        if not all(lo < hi for lo, hi in bounds.values()):
            raise ValueError("each bound must satisfy lo < hi")

        self.bounds      = bounds
        self.names       = list(bounds.keys())
        self.d           = len(self.names)
        self.n_initial   = max(2, n_initial)
        self.max_iter    = max(1, max_iter)
        self.minimize    = minimize
        self.xi          = xi
        self.grid_size   = grid_size
        self.tol         = tol
        self.seed        = seed
        self.save_plot   = save_plot
        self.on_progress = on_progress
        self._rng        = np.random.default_rng(seed)

    # ── coordinate helpers ────────────────────────────────────────────────────

    def _to01(self, p: Dict[str, float]) -> np.ndarray:
        lo = np.array([self.bounds[n][0] for n in self.names])
        hi = np.array([self.bounds[n][1] for n in self.names])
        x  = np.array([p[n] for n in self.names])
        return (x - lo) / (hi - lo + 1e-12)

    def _from01(self, x: np.ndarray) -> Dict[str, float]:
        lo = np.array([self.bounds[n][0] for n in self.names])
        hi = np.array([self.bounds[n][1] for n in self.names])
        # Clip to bounds to prevent floating-point overshoot
        v  = np.clip(lo + x * (hi - lo), lo, hi)
        return {n: float(v[i]) for i, n in enumerate(self.names)}

    # ── EI maximisation ───────────────────────────────────────────────────────

    def _next_x(self, gp: _GP, f_best: float) -> np.ndarray:
        n_cand = min(self.grid_size ** self.d, 10_000)
        X_c    = self._rng.uniform(0, 1, (n_cand, self.d))
        mu, var = gp.predict(X_c)
        acq     = _ei(mu, var, f_best, self.xi)
        return X_c[int(np.argmax(acq))]

    # ── main run ──────────────────────────────────────────────────────────────

    def run(
        self,
        objective: Callable[[Dict[str, float]], Optional[float]],
    ) -> BOResult:
        """
        Run the full BO loop.

        objective(params) → float | None
            Returns None when the simulation fails/doesn't converge.
            Failed evaluations are penalised so the GP steers away from them.
        """
        t0      = time.monotonic()
        history: List[Dict[str, Any]] = []
        sign    = 1.0 if self.minimize else -1.0  # store always as minimise
        X_obs   = np.empty((0, self.d))
        y_obs   = np.empty(0)

        # ── Phase 1: LHS warm-up ──────────────────────────────────────────────
        X_lhs = _lhs(self.n_initial, self.d, self._rng)
        for i, xu in enumerate(X_lhs):
            params = self._from01(xu)
            val    = objective(params)
            if val is None:
                continue
            y_store = sign * val
            X_obs   = np.vstack([X_obs, xu]) if X_obs.size else xu[None]
            y_obs   = np.append(y_obs, y_store)
            best_sf = sign * float(np.min(y_obs))
            rec     = {"iteration": i + 1, "phase": "LHS",
                       "params": params, "value": val, "best_so_far": best_sf}
            history.append(rec)
            if self.on_progress:
                self.on_progress(i + 1, params, val, best_sf)

        if X_obs.size == 0:
            raise RuntimeError("All LHS evaluations failed — check objective/simulation")

        # ── Phase 2: BO iterations ────────────────────────────────────────────
        gp         = _GP()
        no_improve = 0
        prev_best  = float(np.min(y_obs))

        for i in range(self.max_iter):
            gp.fit(X_obs, y_obs)
            f_best  = float(np.min(y_obs))
            xu_next = self._next_x(gp, f_best)
            params  = self._from01(xu_next)
            val     = objective(params)
            it      = self.n_initial + i + 1

            if val is None:
                # Penalise failed region — add 3σ above current best
                # Penalty = f_best + 3σ. When σ→0 (all evals identical / plateau),
                # use absolute fallback of 1.0 so penalty always exceeds f_best.
                sigma_obs = float(np.std(y_obs))
                pen = f_best + 3.0 * (sigma_obs if sigma_obs > 1e-8 else abs(f_best) * 0.1 + 1.0)
                X_obs = np.vstack([X_obs, xu_next])
                y_obs = np.append(y_obs, pen)
                history.append({"iteration": it, "phase": "BO", "params": params,
                                 "value": None, "best_so_far": sign * f_best,
                                 "note": "failed — region penalised"})
                continue

            y_store  = sign * val
            X_obs    = np.vstack([X_obs, xu_next])
            y_obs    = np.append(y_obs, y_store)
            cur_best = float(np.min(y_obs))
            improve  = max(0.0, prev_best - cur_best)
            best_sf  = sign * cur_best

            history.append({"iteration": it, "phase": "BO", "params": params,
                             "value": val, "best_so_far": best_sf,
                             "ei_improvement": improve})
            if self.on_progress:
                self.on_progress(it, params, val, best_sf)

            no_improve = 0 if improve > self.tol else no_improve + 1
            prev_best  = cur_best
            if no_improve >= 5:
                break

        # ── Collect result ────────────────────────────────────────────────────
        bi          = int(np.argmin(y_obs))
        best_params = self._from01(X_obs[bi])
        best_value  = sign * float(y_obs[bi])

        result = BOResult(
            best_params      = best_params,
            best_value       = best_value,
            n_evals          = len(y_obs),
            n_initial        = self.n_initial,
            converged        = no_improve >= 5,
            history          = history,
            param_names      = self.names,
            bounds           = self.bounds,
            minimize         = self.minimize,
            duration_s       = round(time.monotonic() - t0, 2),
            convergence_plot = "",
        )

        if self.save_plot:
            result.convergence_plot = plot_convergence(result, self.save_plot)

        return result


# ─────────────────────────────────────────────────────────────────────────────
# Self-test (python bayesian_optimizer.py)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Branin 2-D benchmark: global min ≈ 0.398 at several known points
    def branin(p: Dict[str, float]) -> float:
        x1, x2 = p["x1"], p["x2"]
        b = 5.1 / (4 * math.pi**2)
        c = 5 / math.pi
        return float((x2 - b*x1**2 + c*x1 - 6)**2 + 10*(1 - 1/(8*math.pi))*math.cos(x1) + 10)

    print("Running Branin benchmark (global min ~0.398)...")
    result = BayesianOptimizer(
        bounds    = {"x1": (-5.0, 10.0), "x2": (0.0, 15.0)},
        n_initial = 5, max_iter = 20, minimize = True, seed = 42,
        on_progress = lambda it, p, v, best:
            print(f"  [{it:2d}] v={v:.4f}  best={best:.4f}  params={p}"),
    ).run(branin)

    print(f"\nbest value  : {result.best_value:.4f}  (target < 1.0)")
    print(f"best params : {result.best_params}")
    print(f"evals used  : {result.n_evals}")
    print(f"converged   : {result.converged}")
    assert result.best_value < 1.5, f"Branin test FAILED: {result.best_value}"
    print("Self-test PASSED")
