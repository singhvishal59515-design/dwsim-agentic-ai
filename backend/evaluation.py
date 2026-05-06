"""
evaluation.py  —  Quantitative Evaluation Framework for DWSIM Agentic AI
─────────────────────────────────────────────────────────────────────────
Tracks four key research metrics:
  1. Success rate            — did the agent complete the task?
  2. Accuracy vs reference   — how close are numerical results to known values?
  3. Convergence failure rate — how often does DWSIM fail to converge?
  4. Task duration           — wall-clock seconds (AI time; compare to human_time_min)

Usage (from api.py):
  from evaluation import get_eval_log, get_benchmark_suite, SessionTracker

  tracker = SessionTracker(user_message, get_eval_log())
  ...agent runs, calls tracker.record_tool_call() via on_tool_call hook...
  tracker.finish(final_answer)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# File paths
# ─────────────────────────────────────────────────────────────────────────────

_DIR = os.path.dirname(__file__)
LOG_FILE       = os.path.join(_DIR, "eval_log.json")
BENCHMARK_FILE = os.path.join(_DIR, "benchmarks.json")


# ─────────────────────────────────────────────────────────────────────────────
# EvalSession — one agent chat interaction
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvalSession:
    session_id:           str
    user_message:         str
    start_time:           float
    end_time:             Optional[float]  = None
    success:              bool             = False
    error:                Optional[str]    = None
    tool_calls:           List[Dict]       = field(default_factory=list)
    tool_records_raw:     List[Dict]       = field(default_factory=list)  # full {name,args,result}
    convergence_achieved: Optional[bool]   = None
    final_answer:         Optional[str]    = None
    benchmark_id:         Optional[str]    = None
    reliability_issues:   List[Dict]       = field(default_factory=list)
    judge_scores:         Optional[Dict]   = None

    # ── computed ──────────────────────────────────────────────────────────────

    @property
    def duration_s(self) -> Optional[float]:
        if self.end_time is not None:
            return round(self.end_time - self.start_time, 2)
        return None

    @property
    def tool_count(self) -> int:
        return len(self.tool_calls)

    @property
    def failed_tools(self) -> int:
        return sum(1 for tc in self.tool_calls if not tc.get("success", True))

    def to_dict(self) -> Dict:
        return {
            "session_id":           self.session_id,
            "user_message":         self.user_message[:200],
            "start_time":           self.start_time,
            "end_time":             self.end_time,
            "duration_s":           self.duration_s,
            "success":              self.success,
            "error":                self.error,
            "tool_count":           self.tool_count,
            "failed_tools":         self.failed_tools,
            "convergence_achieved": self.convergence_achieved,
            "tools_used":           [tc["name"] for tc in self.tool_calls],
            "benchmark_id":         self.benchmark_id,
            "timestamp_iso":        datetime.fromtimestamp(self.start_time).isoformat(),
            "reliability_issues":   self.reliability_issues,
            "judge_scores":         getattr(self, 'judge_scores', None),
        }


# ─────────────────────────────────────────────────────────────────────────────
# BenchmarkResult — score for one benchmark run
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    benchmark_id:     str
    session_id:       str
    passed:           bool
    accuracy_checks:  List[Dict]       # {metric, expected, actual, error_pct, passed}
    convergence:      Optional[bool]
    duration_s:       Optional[float]
    notes:            str = ""

    def to_dict(self) -> Dict:
        return {
            "benchmark_id":    self.benchmark_id,
            "session_id":      self.session_id,
            "passed":          self.passed,
            "accuracy_checks": self.accuracy_checks,
            "convergence":     self.convergence,
            "duration_s":      self.duration_s,
            "notes":           self.notes,
            "timestamp_iso":   datetime.now().isoformat(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# EvaluationLog — persistent JSON store
# ─────────────────────────────────────────────────────────────────────────────

class EvaluationLog:
    """Append-only log; keeps last 500 sessions and 200 benchmark results."""

    MAX_SESSIONS   = 500
    MAX_BENCHMARKS = 200

    def __init__(self, log_file: str = LOG_FILE):
        self.log_file          = log_file
        self._sessions:    List[Dict] = []
        self._bm_results:  List[Dict] = []
        self._load()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not os.path.isfile(self.log_file):
            return
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._sessions   = data.get("sessions", [])
            self._bm_results = data.get("benchmark_results", [])
        except Exception:
            pass

    def _save(self) -> None:
        # Atomic write: write to .tmp, then rename. Prevents corruption on crash.
        try:
            tmp = self.log_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "sessions":          self._sessions[-self.MAX_SESSIONS:],
                        "benchmark_results": self._bm_results[-self.MAX_BENCHMARKS:],
                    },
                    f,
                    indent=2,
                )
            os.replace(tmp, self.log_file)
        except Exception:
            pass

    # ── write ─────────────────────────────────────────────────────────────────

    def add_session(self, session: EvalSession) -> None:
        self._sessions.append(session.to_dict())
        self._save()

    def add_benchmark_result(self, result: BenchmarkResult) -> None:
        self._bm_results.append(result.to_dict())
        self._save()

    def clear(self) -> None:
        self._sessions   = []
        self._bm_results = []
        self._save()

    # ── aggregate metrics ─────────────────────────────────────────────────────

    def get_metrics(self) -> Dict:
        sessions = self._sessions
        n = len(sessions)
        if n == 0:
            return {
                "total_sessions":          0,
                "success_rate":            None,
                "avg_duration_s":          None,
                "avg_tool_calls":          None,
                "convergence_rate":        None,
                "tool_error_rate":         None,
                "recent_sessions":         [],
                "tool_frequency":          {},
                "sessions_with_issues":    0,
                "reliability_issue_types": {},
                "reliability_rate":        None,
            }

        successes  = sum(1 for s in sessions if s.get("success"))
        durations  = [s["duration_s"] for s in sessions if s.get("duration_s")]
        tool_cnts  = [s.get("tool_count", 0) for s in sessions]
        fail_tools = [s.get("failed_tools", 0) for s in sessions]
        total_tool_calls = sum(tool_cnts) or 1

        conv_sessions = [s for s in sessions if s.get("convergence_achieved") is not None]
        conv_rate = None
        if conv_sessions:
            conv_ok = sum(1 for s in conv_sessions if s["convergence_achieved"])
            conv_rate = round(conv_ok / len(conv_sessions) * 100, 1)

        # Tool frequency count
        tool_freq: Dict[str, int] = {}
        for s in sessions:
            for t in s.get("tools_used", []):
                tool_freq[t] = tool_freq.get(t, 0) + 1
        tool_freq = dict(sorted(tool_freq.items(), key=lambda x: -x[1])[:15])

        # Reliability issue roll-up from stored sessions
        rel_sessions_with_issues = 0
        rel_issue_types: Dict[str, int] = {}
        for s in sessions:
            issues = s.get("reliability_issues", [])
            if issues:
                rel_sessions_with_issues += 1
            for issue in issues:
                t = issue.get("error_type", "UNKNOWN")
                rel_issue_types[t] = rel_issue_types.get(t, 0) + 1

        return {
            "total_sessions":          n,
            "success_rate":            round(successes / n * 100, 1),
            "avg_duration_s":          round(sum(durations) / len(durations), 2) if durations else None,
            "avg_tool_calls":          round(sum(tool_cnts) / n, 1),
            "convergence_rate":        conv_rate,
            "tool_error_rate":         round(sum(fail_tools) / total_tool_calls * 100, 1),
            "recent_sessions":         sessions[-20:][::-1],   # newest first
            "tool_frequency":          tool_freq,
            "sessions_with_issues":    rel_sessions_with_issues,
            "reliability_issue_types": rel_issue_types,
            "reliability_rate":        round(rel_sessions_with_issues / n * 100, 1),
        }

    def get_benchmark_metrics(self) -> Dict:
        results = self._bm_results
        n = len(results)
        if n == 0:
            return {"total_runs": 0, "pass_rate": None, "results": []}
        passed = sum(1 for r in results if r.get("passed"))
        return {
            "total_runs": n,
            "pass_rate":  round(passed / n * 100, 1),
            "results":    results[-50:][::-1],   # newest first
        }

    def get_extended_metrics(self) -> Dict[str, Any]:
        """Compute extended quality metrics from session history."""
        return ExtendedMetrics.compute(self._sessions)


# ─────────────────────────────────────────────────────────────────────────────
# AIJudge — LLM-based evaluation of agent responses (Chip Huyen Ch.3)
# "AI as a Judge: use a separate LLM to score another LLM's outputs."
# Judge criteria for DWSIM:
#   1. Property package correctness  — did agent choose right EOS?
#   2. Physical plausibility         — are results thermodynamically consistent?
#   3. Completeness                  — did answer address the user's actual question?
#   4. Hallucination detection       — did agent invent values not from tool results?
# ─────────────────────────────────────────────────────────────────────────────

class AIJudge:
    """
    Uses an LLM to score agent responses on 4 criteria.
    Runs asynchronously after each session to avoid blocking the user.

    Scores are 1-5 per criterion, stored in eval_log.json under "judge_scores".

    Usage:
        judge = AIJudge(llm_client)
        score = judge.evaluate(user_query, agent_answer, tool_calls, stream_results)
    """

    JUDGE_PROMPT = """You are an expert chemical engineer evaluating an AI assistant's response to a DWSIM simulation task.

User query: {user_query}

Agent's final answer: {agent_answer}

Tools called (sequence): {tool_sequence}

Simulation results summary: {results_summary}

Rate the agent's response on these 4 criteria (1=very poor, 3=acceptable, 5=excellent):

1. PROPERTY_PACKAGE_CORRECTNESS: Did the agent choose the right thermodynamic model?
   - PR/SRK for hydrocarbons = correct
   - NRTL/UNIQUAC for polar organics + water = correct
   - Steam Tables for pure water/steam = correct
   - Wrong EOS for system type = incorrect

2. PHYSICAL_PLAUSIBILITY: Are the reported results thermodynamically reasonable?
   - Temperatures, pressures in realistic ranges?
   - Mass balance approximately closed?
   - Phase behavior consistent with conditions?

3. COMPLETENESS: Did the answer address what the user actually asked?
   - Answered the specific question asked?
   - Provided numerical results when requested?
   - Explained key decisions?

4. HALLUCINATION_ABSENCE: Did agent avoid inventing values?
   - Only reported values from tool results?
   - Did not fabricate stream properties?
   - Did not invent BIPs, Tc, Pc values without lookup?

Respond ONLY with JSON:
{{"property_package_correctness": <1-5>, "physical_plausibility": <1-5>, "completeness": <1-5>, "hallucination_absence": <1-5>, "overall": <1-5>, "reasoning": "<one sentence explanation>"}}"""

    def __init__(self, llm_client=None) -> None:
        self._llm = llm_client

    def evaluate(
        self,
        user_query:      str,
        agent_answer:    str,
        tool_calls:      List[Dict] = None,
        stream_results:  Dict = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Run AI-as-judge evaluation. Returns score dict or None if LLM unavailable.
        Never raises — evaluation failure must not break the application.
        """
        if self._llm is None:
            return None
        try:
            tool_sequence = [tc.get("name", "") for tc in (tool_calls or [])][:10]
            results_summary = ""
            if stream_results:
                streams = list(stream_results.items())[:3]
                results_summary = "; ".join(
                    f"{tag}: T={props.get('temperature_C','?')}°C P={props.get('pressure_bar','?')}bar"
                    for tag, props in streams
                )

            prompt = self.JUDGE_PROMPT.format(
                user_query      = user_query[:300],
                agent_answer    = agent_answer[:500],
                tool_sequence   = ", ".join(tool_sequence) or "(none)",
                results_summary = results_summary or "(no stream results)",
            )

            response = self._llm.chat(
                messages      = [{"role": "user", "content": prompt}],
                tools         = [],
                system_prompt = "You are a chemical engineering evaluation expert. Respond only with valid JSON.",
            )

            if response and response.get("content"):
                content = response["content"].strip()
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    scores = json.loads(json_match.group())
                    scores["evaluated_at"] = datetime.now().isoformat()
                    scores["judge_model"]  = getattr(self._llm, "model", "unknown")
                    return scores
        except Exception:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ExtendedMetrics — session-level quality metrics (Chip Huyen Ch.4)
# "Evaluate AI Systems: evaluate components independently, then system."
# ─────────────────────────────────────────────────────────────────────────────

class ExtendedMetrics:
    """
    Compute extended evaluation metrics from session history.
    Following Chip Huyen Ch.4: evaluate components independently, then system.

    Metrics beyond pass/fail:
      - EOS selection accuracy    (correct property package chosen)
      - Token efficiency          (tokens per successful task)
      - Tool trajectory quality   (tool call ordering correctness)
      - Mass balance closure rate (did simulation mass balance close?)
      - First-call success rate   (did first save_and_solve succeed?)
      - Average SF violations per session
    """

    @staticmethod
    def compute(sessions: List[Dict]) -> Dict[str, Any]:
        """Compute extended metrics from a list of session dicts."""
        if not sessions:
            return {}

        n = len(sessions)

        # EOS selection: count sessions where property package was set
        eos_sessions = [s for s in sessions if any(
            t in s.get("tools_used", [])
            for t in ["get_property_package", "new_flowsheet", "set_property_package"]
        )]

        # Tool trajectory quality: penalize sessions with precondition violations
        precond_violations = sum(
            1 for s in sessions
            if any("_precondition_failed" in str(s) for _ in [None])
        )

        # First-call success rate for save_and_solve
        first_solve_success = 0
        first_solve_total   = 0
        for s in sessions:
            tools = s.get("tools_used", [])
            if "save_and_solve" in tools:
                first_solve_total += 1
                # Success = session succeeded overall (proxy for first-call success)
                if s.get("success"):
                    first_solve_success += 1

        # Average tool calls per successful session
        successful = [s for s in sessions if s.get("success")]
        avg_tools_successful = (
            round(sum(s.get("tool_count", 0) for s in successful) / len(successful), 1)
            if successful else None
        )

        # SF violations per session
        sf_data = [s for s in sessions if s.get("reliability_issues")]
        avg_sf_violations = (
            round(sum(len(s.get("reliability_issues", [])) for s in sf_data) / len(sf_data), 2)
            if sf_data else 0.0
        )

        return {
            "eos_sessions_pct":         round(len(eos_sessions) / n * 100, 1),
            "first_solve_success_rate": (
                round(first_solve_success / first_solve_total * 100, 1)
                if first_solve_total else None
            ),
            "avg_tools_per_success":    avg_tools_successful,
            "avg_sf_violations":        avg_sf_violations,
            "sessions_with_sf":         len(sf_data),
            "precondition_violations":  precond_violations,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SessionTracker — lightweight hook for a single chat() call
# ─────────────────────────────────────────────────────────────────────────────

class SessionTracker:
    """
    Attach to one agent.chat() invocation via on_tool_call / finish().

    Usage:
        tracker = SessionTracker(user_message, get_eval_log())
        agent.on_tool_call = tracker.record_tool_call   # (extra wrapper in api.py)
        answer = agent.chat(user_message)
        tracker.finish(answer)
    """

    def __init__(
        self,
        user_message: str,
        log: EvaluationLog,
        benchmark_id: Optional[str] = None,
    ) -> None:
        self.log = log
        self.session = EvalSession(
            session_id   = str(uuid.uuid4())[:8],
            user_message = user_message,
            start_time   = time.time(),
            benchmark_id = benchmark_id,
        )

    def record_tool_call(self, name: str, args: dict, result: dict) -> None:
        ok = result.get("success", True)
        # Infer convergence from DWSIM tool results
        if name in ("run_simulation", "get_simulation_results", "check_convergence"):
            conv_check = result.get("convergence_check") or {}
            if conv_check:
                not_conv = conv_check.get("not_converged", [])
                self.session.convergence_achieved = (len(not_conv) == 0)
            elif name == "run_simulation":
                self.session.convergence_achieved = bool(ok)

        self.session.tool_calls.append({"name": name, "success": ok})
        # Store full record for reliability analysis (trim large results)
        try:
            result_trimmed = json.loads(json.dumps(result, default=str))
            # Cap mole_fractions entries to keep memory small
            if "stream_results" in result_trimmed:
                for sr in result_trimmed["stream_results"].values():
                    mf = sr.get("mole_fractions") or {}
                    if len(mf) > 20:
                        sr["mole_fractions"] = dict(list(mf.items())[:20])
        except Exception:
            result_trimmed = {"success": ok}
        self.session.tool_records_raw.append({"name": name, "args": {}, "result": result_trimmed})

    def finish(self, final_answer: str, error: Optional[str] = None) -> EvalSession:
        self.session.end_time     = time.time()
        self.session.success      = (error is None)
        self.session.error        = error
        self.session.final_answer = (final_answer or "")[:500]
        self.log.add_session(self.session)
        return self.session


# ─────────────────────────────────────────────────────────────────────────────
# Default benchmark cases
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BENCHMARKS: List[Dict] = [
    {
        "id":          "BM-001",
        "name":        "Flash Vapour Fraction Check",
        "description": "50% methane + 50% ethane at 25 °C, 1 bar. Vapour fraction must be ≥ 0.8.",
        "prompt":      "I have a stream with 50% methane and 50% ethane (mole basis) at 25 °C and 1 bar. What is the vapour fraction? Use a flash calculation.",
        "expected":    {"vapour_fraction": {"min": 0.8, "max": 1.0}},
        "requires_flowsheet": False,
        "tags":        ["flash", "accuracy", "no_flowsheet"],
        "difficulty":  "easy",
        "human_time_min": 5,
    },
    {
        "id":          "BM-002",
        "name":        "Property Package Retrieval",
        "description": "Agent correctly identifies and reports the thermodynamic model in use.",
        "prompt":      "What thermodynamic property package is this flowsheet using?",
        "expected":    {},
        "requires_flowsheet": True,
        "tags":        ["metadata", "accuracy"],
        "difficulty":  "easy",
        "human_time_min": 1,
    },
    {
        "id":          "BM-003",
        "name":        "Stream Composition Setting",
        "description": "Set feed to 70 mol% Water, 30 mol% Methanol; re-run and confirm acceptance.",
        "prompt":      "Set the feed stream composition to 70% water and 30% methanol (mole fraction), then run the simulation and report the outlet temperature.",
        "expected":    {},
        "requires_flowsheet": True,
        "tags":        ["composition", "workflow"],
        "difficulty":  "easy",
        "human_time_min": 3,
    },
    {
        "id":          "BM-004",
        "name":        "Optimisation Convergence",
        "description": "SciPy optimizer finds best parameter within bounded range and reports the result.",
        "prompt":      "Optimise the heat exchanger: find the cold stream inlet temperature that maximises the heat duty. Use bounds 20 °C to 80 °C.",
        "expected":    {},
        "requires_flowsheet": True,
        "tags":        ["optimization", "convergence"],
        "difficulty":  "hard",
        "human_time_min": 20,
    },
    {
        "id":          "BM-005",
        "name":        "KB — Property Package Guidance",
        "description": "Agent retrieves knowledge base chunk about Peng-Robinson vs SRK and gives a reasoned answer.",
        "prompt":      "When should I use the Peng-Robinson EOS instead of SRK for my simulation?",
        "expected":    {},
        "requires_flowsheet": False,
        "tags":        ["knowledge", "rag", "no_flowsheet"],
        "difficulty":  "easy",
        "human_time_min": 5,
    },
    {
        "id":          "BM-006",
        "name":        "Multi-step Workflow — Load → Modify → Run → Report",
        "description": "Full 4-step pipeline: find file, load, change property, run, read result.",
        "prompt":      "Find a flowsheet on my computer, load it, increase the feed temperature by 20 °C, run the simulation, and tell me how the product temperature changed.",
        "expected":    {},
        "requires_flowsheet": False,
        "tags":        ["workflow", "multi_step"],
        "difficulty":  "hard",
        "human_time_min": 15,
    },
    {
        "id":          "BM-007",
        "name":        "Convergence Check — All Streams",
        "description": "Agent calls check_convergence and correctly reports converged / not-converged streams.",
        "prompt":      "Did all streams converge in the last simulation run? List any that did not.",
        "expected":    {},
        "requires_flowsheet": True,
        "tags":        ["convergence", "reporting"],
        "difficulty":  "easy",
        "human_time_min": 2,
    },
    {
        "id":          "BM-008",
        "name":        "Parametric Study — Temperature Sweep",
        "description": "Agent runs a parametric study over 5 temperature points and presents a table.",
        "prompt":      "Run a parametric study: vary the feed temperature from 50 °C to 150 °C in 5 steps and show how the product molar flow changes.",
        "expected":    {},
        "requires_flowsheet": True,
        "tags":        ["parametric", "workflow"],
        "difficulty":  "medium",
        "human_time_min": 10,
    },
    {
        "id":          "BM-009",
        "name":        "Template Build — Heater/Cooler from Scratch",
        "description": "Agent builds a heater flowsheet via create_from_template, sets outlet T to 80 °C, runs the sim, and reports duty.",
        "prompt":      "Build a new flowsheet from scratch using the heater_cooler template with water as the working fluid. Set the feed to 25 °C and the heater outlet to 80 °C, run the simulation, and report the heater duty.",
        "expected":    {},
        "requires_flowsheet": False,
        "tags":        ["template", "build", "workflow"],
        "difficulty":  "medium",
        "human_time_min": 8,
    },
    {
        "id":          "BM-010",
        "name":        "Design Goal Recall Across Turns",
        "description": "Agent remembers a stated design goal and references it on a later turn within the same session.",
        "prompt":      "Remember this design goal: maximise product purity above 99.5 mol%. Then tell me which property package you would recommend for a methanol-water distillation column and justify why it supports that goal.",
        "expected":    {},
        "requires_flowsheet": False,
        "tags":        ["memory", "reasoning", "no_flowsheet"],
        "difficulty":  "easy",
        "human_time_min": 4,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# BenchmarkSuite — loads cases and scores responses
# ─────────────────────────────────────────────────────────────────────────────

class BenchmarkSuite:
    """Load benchmark definitions and evaluate agent sessions against them."""

    def __init__(self, benchmark_file: str = BENCHMARK_FILE) -> None:
        self.benchmark_file = benchmark_file
        self.cases          = self._load()

    def _load(self) -> List[Dict]:
        if os.path.isfile(self.benchmark_file):
            try:
                with open(self.benchmark_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        # First run — write defaults atomically
        try:
            tmp = self.benchmark_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_DEFAULT_BENCHMARKS, f, indent=2)
            os.replace(tmp, self.benchmark_file)
        except Exception:
            pass
        return list(_DEFAULT_BENCHMARKS)

    def list_all(self) -> List[Dict]:
        return self.cases

    def get(self, benchmark_id: str) -> Optional[Dict]:
        for c in self.cases:
            if c["id"] == benchmark_id:
                return c
        return None

    def evaluate(
        self,
        benchmark_id: str,
        session: EvalSession,
        simulation_results: Optional[Dict] = None,
    ) -> BenchmarkResult:
        """
        Score a completed EvalSession against the named benchmark.
        Checks:
          1. Agent completed without error (session.success)
          2. At least one tool call was made
          3. Numerical expected values match (if provided + simulation_results given)
        """
        case = self.get(benchmark_id)
        if case is None:
            return BenchmarkResult(
                benchmark_id=benchmark_id, session_id=session.session_id,
                passed=False, accuracy_checks=[], convergence=None,
                duration_s=session.duration_s,
                notes=f"Benchmark {benchmark_id} not found",
            )

        accuracy_checks: List[Dict] = []
        expected = case.get("expected", {})

        for metric, bounds in expected.items():
            if simulation_results is None:
                accuracy_checks.append({
                    "metric": metric, "expected": bounds, "actual": None,
                    "error_pct": None, "passed": False,
                    "note": "No simulation_results provided",
                })
                continue

            actual = simulation_results.get(metric)
            if actual is None:
                accuracy_checks.append({
                    "metric": metric, "expected": bounds, "actual": None,
                    "error_pct": None, "passed": False,
                    "note": f"{metric} not in simulation results",
                })
                continue

            lo  = bounds.get("min")
            hi  = bounds.get("max")
            ref = bounds.get("value")

            if ref is not None:
                tol  = bounds.get("tolerance_pct", 5.0)
                ep   = abs(actual - ref) / abs(ref) * 100 if ref != 0 else 0
                ok   = ep <= tol
            else:
                ep   = None
                ok   = True
                if lo is not None and actual < lo:
                    ok = False
                if hi is not None and actual > hi:
                    ok = False

            accuracy_checks.append({
                "metric": metric, "expected": bounds, "actual": actual,
                "error_pct": round(ep, 2) if ep is not None else None,
                "passed": ok,
            })

        all_accuracy_ok = all(c["passed"] for c in accuracy_checks) if accuracy_checks else True
        # If no expected values defined, pass = agent succeeded + used tools
        overall_passed = (
            session.success
            and session.tool_count > 0
            and all_accuracy_ok
        )

        notes_parts: List[str] = []
        if not session.success:
            notes_parts.append(f"Error: {session.error}")
        if session.tool_count == 0:
            notes_parts.append("No tools called")
        if session.convergence_achieved is False:
            notes_parts.append("Convergence failed")

        return BenchmarkResult(
            benchmark_id     = benchmark_id,
            session_id       = session.session_id,
            passed           = overall_passed,
            accuracy_checks  = accuracy_checks,
            convergence      = session.convergence_achieved,
            duration_s       = session.duration_s,
            notes            = " | ".join(notes_parts),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Singletons (imported by api.py)
# ─────────────────────────────────────────────────────────────────────────────

_eval_log        = EvaluationLog()
_benchmark_suite = BenchmarkSuite()


def get_eval_log() -> EvaluationLog:
    return _eval_log


def get_benchmark_suite() -> BenchmarkSuite:
    return _benchmark_suite
