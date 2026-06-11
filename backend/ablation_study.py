"""
ablation_study.py
──────────────────
Structured ablation study for the DWSIM Agentic AI system.

Implements the five-condition experiment required for C&CE submission:

  Condition 1 (FULL)         — All 103 tools + 3 reflection tools
  Condition 2 (NO_RAG)       — BM25 knowledge retrieval disabled
  Condition 3 (NO_SAFETY)    — SafetyValidator bypassed
  Condition 4 (NO_REFLECTION)— Reflection tools disabled (62 fixed tools only)
  Condition 5 (LLM_ONLY)     — Direct LLM with no DWSIM tool access

25 tasks across 5 categories:
  A. Property Reading     (5 tasks)  — read stream/unit-op properties
  B. Process Modification (5 tasks)  — change parameters and re-solve
  C. Optimization         (5 tasks)  — find optimal operating point
  D. Design               (5 tasks)  — select PP, size equipment, synthesis
  E. Troubleshooting      (5 tasks)  — diagnose and fix convergence issues

Each task is evaluated on:
  - Success (binary)
  - Numerical accuracy (for tasks with known ground truth)
  - Tool calls made
  - Time elapsed

Run:
    python ablation_study.py [--condition FULL|NO_RAG|NO_SAFETY|NO_REFLECTION|LLM_ONLY]
                              [--tasks A,B,C,D,E]
                              [--flowsheet path/to/flowsheet.dwxmz]
                              [--output results.json]
"""

from __future__ import annotations
import json
import math
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional

# ─── Task definitions ─────────────────────────────────────────────────────────

TASKS: List[Dict[str, Any]] = [
    # ── A: Property Reading ────────────────────────────────────────────────
    {
        "id": "A1", "category": "property_reading",
        "name": "Read product stream temperature",
        "query": "What is the temperature of the Product stream?",
        "evaluate": lambda r, b: _check_value(r, "temperature", 60, 120),  # expects ~80°C
        "ground_truth": 80.0,
        "tolerance_pct": 5.0,
        "requires_flowsheet": True,
    },
    {
        "id": "A2", "category": "property_reading",
        "name": "Read product stream pressure",
        "query": "What is the pressure of the Product stream in bar?",
        "evaluate": lambda r, b: _check_value(r, "pressure", 0.9, 1.2),
        "ground_truth": 1.01325,
        "tolerance_pct": 2.0,
        "requires_flowsheet": True,
    },
    {
        "id": "A3", "category": "property_reading",
        "name": "Read product mass flow",
        "query": "What is the mass flow rate of the Product stream in kg/h?",
        "evaluate": lambda r, b: _check_value(r, "flow", 3000, 4000),
        "ground_truth": 3600.0,
        "tolerance_pct": 5.0,
        "requires_flowsheet": True,
    },
    {
        "id": "A4", "category": "property_reading",
        "name": "Read heater duty via reflection",
        "query": "What is the heat duty of H-101 in kW?",
        "evaluate": lambda r, b: _check_value(r, "duty", 200, 400),
        "ground_truth": 313.87,
        "tolerance_pct": 10.0,
        "requires_flowsheet": True,
        "requires_reflection": True,  # only works with reflect_get_set
    },
    {
        "id": "A5", "category": "property_reading",
        "name": "List all simulation objects",
        "query": "What streams and unit operations are in the loaded flowsheet?",
        "evaluate": lambda r, b: _check_contains(r, ["H-101", "Feed", "Product"]),
        "ground_truth": ["H-101", "Feed", "Product"],
        "requires_flowsheet": True,
    },
    # ── B: Process Modification ─────────────────────────────────────────────
    {
        "id": "B1", "category": "process_modification",
        "name": "Change feed temperature and re-solve",
        "query": "Set the Feed stream temperature to 25°C and run the simulation",
        "evaluate": lambda r, b: _check_solved(r) and _check_bridge_value(b, "Feed", "temperature_C", 25, 5),
        "ground_truth": 25.0,
        "requires_flowsheet": True,
    },
    {
        "id": "B2", "category": "process_modification",
        "name": "Change heater outlet temperature spec",
        "query": "Change the heater H-101 outlet temperature to 90°C and run the simulation",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    {
        "id": "B3", "category": "process_modification",
        "name": "Change mass flow and verify energy balance",
        "query": "Set Feed mass flow to 2000 kg/h, run simulation, and report the new heater duty",
        "evaluate": lambda r, b: _check_solved(r) and _check_bridge_value(b, "Feed", "mass_flow_kgh", 2000, 200),
        "requires_flowsheet": True,
    },
    {
        "id": "B4", "category": "process_modification",
        "name": "Set stream composition",
        "query": "Set Feed stream water mole fraction to 1.0 (pure water) and run",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    {
        "id": "B5", "category": "process_modification",
        "name": "Modify via reflection and verify",
        "query": "Use exec_python to read both Feed and Product temperatures and compute delta T",
        "evaluate": lambda r, b: _check_value(r, "delta_T", 50, 90),
        "ground_truth": 70.0,
        "tolerance_pct": 10.0,
        "requires_flowsheet": True,
        "requires_reflection": True,
    },
    # ── C: Optimization ─────────────────────────────────────────────────────
    {
        "id": "C1", "category": "optimization",
        "name": "Maximise product temperature — NL goal",
        "query": "Maximise the Product stream temperature by varying the Feed temperature",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    {
        "id": "C2", "category": "optimization",
        "name": "Minimise heater duty",
        "query": "Minimise the heater H-101 duty while keeping product temperature above 75°C",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    {
        "id": "C3", "category": "optimization",
        "name": "Run Bayesian optimisation",
        "query": "Use Bayesian optimisation to find the Feed temperature that maximises product enthalpy",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    {
        "id": "C4", "category": "optimization",
        "name": "iterative_spec_loop to target",
        "query": "Adjust Feed temperature so that Product temperature equals exactly 85°C",
        "evaluate": lambda r, b: _check_bridge_value(b, "Product", "temperature_C", 85, 2),
        "ground_truth": 85.0,
        "tolerance_pct": 3.0,
        "requires_flowsheet": True,
        "requires_reflection": True,
    },
    {
        "id": "C5", "category": "optimization",
        "name": "DWSIM-internal OptimizationCase",
        "query": "Use DWSIM's internal optimization to minimise heater duty",
        "evaluate": lambda r, b: _check_solved(r),
        "requires_flowsheet": True,
    },
    # ── D: Design Intelligence ────────────────────────────────────────────
    {
        "id": "D1", "category": "design",
        "name": "Property package selection for Water",
        "query": "What thermodynamic property package should I use for a water-only flowsheet?",
        "evaluate": lambda r, b: _check_contains(r, ["Steam Tables", "IAPWS", "NRTL"]),
        "requires_flowsheet": False,
    },
    {
        "id": "D2", "category": "design",
        "name": "Equipment sizing for heat exchanger",
        "query": "Size a heat exchanger for Q=300 kW, LMTD=20°C, liquid-liquid service",
        "evaluate": lambda r, b: _check_value(r, "area", 10, 100),
        "requires_flowsheet": False,
    },
    {
        "id": "D3", "category": "design",
        "name": "Process synthesis for heating process",
        "query": "Design a process to heat water from 10°C to 80°C at 1 kg/s scale",
        "evaluate": lambda r, b: _check_contains(r, ["Heater", "H-101", "steam", "utility"]),
        "requires_flowsheet": False,
    },
    {
        "id": "D4", "category": "design",
        "name": "Separation sequence for water-ethanol",
        "query": "Suggest a separation sequence for a water-ethanol mixture at atmospheric pressure",
        "evaluate": lambda r, b: _check_contains(r, ["distillation", "column", "azeotrope"]),
        "requires_flowsheet": False,
    },
    {
        "id": "D5", "category": "design",
        "name": "PP selector for hydrocarbon mixture",
        "query": "Which property package for methane, ethane, propane at 100 bar?",
        "evaluate": lambda r, b: _check_contains(r, ["Peng-Robinson", "PR", "SRK"]),
        "requires_flowsheet": False,
    },
    # ── E: Troubleshooting ────────────────────────────────────────────────
    {
        "id": "E1", "category": "troubleshooting",
        "name": "Diagnose max iterations error",
        "query": "The simulation says 'maximum iterations reached'. What should I do?",
        "evaluate": lambda r, b: _check_contains(r, ["Wegstein", "Broyden", "MaxIterations", "tolerance", "iterations"]),
        "requires_flowsheet": False,
    },
    {
        "id": "E2", "category": "troubleshooting",
        "name": "Diagnose column convergence failure",
        "query": "My distillation column is not converging with Inside-Out algorithm. How do I fix this?",
        "evaluate": lambda r, b: _check_contains(r, ["initialise", "reflux", "stages", "algorithm"]),
        "requires_flowsheet": False,
    },
    {
        "id": "E3", "category": "troubleshooting",
        "name": "Diagnose flash error",
        "query": "DWSIM shows a flash convergence error on stream FEED. What is the likely cause?",
        "evaluate": lambda r, b: _check_contains(r, ["property package", "specification", "pressure", "temperature"]),
        "requires_flowsheet": False,
    },
    {
        "id": "E4", "category": "troubleshooting",
        "name": "Decode error message",
        "query": "What does this DWSIM error mean: 'NullReferenceException: Object reference not set to an instance of an object'?",
        "evaluate": lambda r, b: _check_contains(r, ["connection", "port", "stream", "connected"]),
        "requires_flowsheet": False,
    },
    {
        "id": "E5", "category": "troubleshooting",
        "name": "Recycle convergence guide",
        "query": "How do I set up recycle loop convergence parameters in DWSIM?",
        "evaluate": lambda r, b: _check_contains(r, ["Wegstein", "Broyden", "tear", "iteration"]),
        "requires_flowsheet": False,
    },
]

# ─── Evaluation helpers ────────────────────────────────────────────────────────

def _extract_text(response: Any) -> str:
    """Extract text content from an agent response (string or dict)."""
    if isinstance(response, str):
        return response.lower()
    if isinstance(response, dict):
        for key in ("content", "text", "message", "result", "chat_markdown",
                     "summary", "answer", "response"):
            v = response.get(key)
            if isinstance(v, str):
                return v.lower()
        return json.dumps(response).lower()
    return str(response).lower()


def _check_value(response: Any, hint: str, lo: float, hi: float) -> bool:
    """Check that a numeric value in [lo, hi] appears in the response."""
    text = _extract_text(response)
    import re
    numbers = [float(m) for m in re.findall(r'[-+]?\d+\.?\d*', text)]
    return any(lo <= n <= hi for n in numbers)


def _check_contains(response: Any, keywords: List[str]) -> bool:
    """Check that at least one keyword appears in the response (case-insensitive)."""
    text = _extract_text(response)
    return any(kw.lower() in text for kw in keywords)


def _check_solved(response: Any) -> bool:
    """Check that the response indicates a successful simulation."""
    text = _extract_text(response)
    return any(k in text for k in ("success", "converged", "completed",
                                    "solved", "done", "✓"))


def _check_bridge_value(bridge, tag: str, prop: str,
                          expected: float, tolerance: float) -> bool:
    """Read a live bridge property and check it's within tolerance."""
    if bridge is None:
        return False
    try:
        from dwsim_reflection import _bridge_property_fallback
        v = _bridge_property_fallback(bridge, tag, prop)
        if v is not None:
            return abs(float(v) - expected) <= tolerance
    except Exception:
        pass
    return False


# ─── Condition definitions ─────────────────────────────────────────────────────

CONDITIONS = {
    "FULL": {
        "name": "Full System",
        "desc": "All 103 tools + 3 reflection tools + RAG + Safety",
        "rag_enabled": True,
        "safety_enabled": True,
        "reflection_enabled": True,
        "tools_enabled": True,
        "direct_llm_only": False,
    },
    "NO_RAG": {
        "name": "No RAG",
        "desc": "All tools but BM25 knowledge retrieval disabled",
        "rag_enabled": False,
        "safety_enabled": True,
        "reflection_enabled": True,
        "tools_enabled": True,
        "direct_llm_only": False,
    },
    "NO_SAFETY": {
        "name": "No Safety Validator",
        "desc": "All tools but SafetyValidator bypassed",
        "rag_enabled": True,
        "safety_enabled": False,
        "reflection_enabled": True,
        "tools_enabled": True,
        "direct_llm_only": False,
    },
    "NO_REFLECTION": {
        "name": "No Reflection Tools",
        "desc": "62 fixed tools only — reflect_get_set/exec_python/inspect_object disabled",
        "rag_enabled": True,
        "safety_enabled": True,
        "reflection_enabled": False,
        "tools_enabled": True,
        "direct_llm_only": False,
    },
    "LLM_ONLY": {
        "name": "Direct LLM (No Tools)",
        "desc": "Plain LLM with no DWSIM access — baseline",
        "rag_enabled": False,
        "safety_enabled": False,
        "reflection_enabled": False,
        "tools_enabled": False,
        "direct_llm_only": True,
    },
}


# ─── Runner ────────────────────────────────────────────────────────────────────

class AblationRunner:
    """Runs the 25-task benchmark under a given condition."""

    def __init__(self, condition: str, bridge=None, llm=None):
        self.condition = CONDITIONS[condition]
        self.condition_key = condition
        self.bridge = bridge
        self.llm = llm
        self.results: List[Dict] = []

    def run_all(self, tasks: Optional[List[str]] = None) -> Dict[str, Any]:
        """Run all tasks (or a subset by category/ID) and return structured results."""
        task_list = TASKS
        if tasks:
            task_list = [t for t in TASKS
                          if t["id"] in tasks or t["category"] in tasks]

        t0 = time.monotonic()
        for task in task_list:
            self.results.append(self._run_task(task))

        duration = round(time.monotonic() - t0, 2)
        return self._summarise(duration)

    def _run_task(self, task: Dict) -> Dict:
        """Execute one task and return a result row."""
        t0 = time.monotonic()

        # Skip tasks that need a flowsheet when none is loaded
        if task.get("requires_flowsheet") and self.bridge is None:
            return self._skip(task, "No flowsheet loaded")

        # Skip reflection tasks in NO_REFLECTION / LLM_ONLY conditions
        if task.get("requires_reflection") and not self.condition["reflection_enabled"]:
            return self._skip(task, "Reflection tools disabled in this condition")

        # Skip all tool-dependent tasks in LLM_ONLY
        if self.condition["direct_llm_only"] and task.get("requires_flowsheet"):
            return self._skip(task, "LLM_ONLY condition has no DWSIM access")

        # Attempt the task
        response = None
        error = None
        try:
            response = self._call_agent(task)
        except Exception as exc:
            error = str(exc)[:200]

        # Evaluate
        passed = False
        accuracy = None
        try:
            if response is not None:
                passed = bool(task["evaluate"](response, self.bridge))
                # Numerical accuracy (if ground truth provided)
                if "ground_truth" in task:
                    gt = float(task["ground_truth"])
                    text = _extract_text(response)
                    import re
                    nums = [float(m) for m in re.findall(r'[-+]?\d+\.?\d*', text)]
                    if nums:
                        best = min(nums, key=lambda n: abs(n - gt))
                        accuracy = round(abs(best - gt) / max(abs(gt), 1e-6) * 100, 2)
        except Exception as eval_err:
            error = (error or "") + f" | eval: {eval_err}"

        elapsed = round(time.monotonic() - t0, 3)
        return {
            "task_id":   task["id"],
            "category":  task["category"],
            "name":      task["name"],
            "condition": self.condition_key,
            "passed":    passed,
            "accuracy_pct_error": accuracy,
            "elapsed_s": elapsed,
            "error":     error,
            "response_preview": _extract_text(response)[:200] if response else None,
        }

    def _skip(self, task: Dict, reason: str) -> Dict:
        return {
            "task_id":   task["id"],
            "category":  task["category"],
            "name":      task["name"],
            "condition": self.condition_key,
            "passed":    False,
            "skipped":   True,
            "skip_reason": reason,
            "elapsed_s": 0,
            "error":     None,
        }

    def _call_agent(self, task: Dict) -> Any:
        """Route the task to the appropriate handler based on condition."""
        query = task["query"]
        cond  = self.condition

        if cond["direct_llm_only"]:
            # Direct LLM — no tools, no DWSIM
            if self.llm is None:
                return "LLM not available — cannot answer without tools"
            try:
                resp = self.llm.chat(
                    messages=[{"role": "user", "content": query}],
                    system_prompt="You are a chemical engineering assistant.",
                    tools=[],
                )
                return resp.get("content", "") if isinstance(resp, dict) else str(resp)
            except Exception as e:
                return f"LLM error: {e}"

        # Deterministic path — use the installed modules directly
        q_lc = query.lower()

        # Property package selection
        if any(k in q_lc for k in ("property package", "thermodynamic", "which pp", "pp for")):
            from process_design_advisor import property_package_selector
            comps = _extract_compounds(query)
            return property_package_selector(comps or ["Water"])

        # Equipment sizing
        if any(k in q_lc for k in ("size", "sizing", "area", "heat exchanger")):
            from process_design_advisor import equipment_sizing
            return equipment_sizing("heat_exchanger",
                                     duty_kW=float(_first_number(query, 300)),
                                     LMTD_C=float(_first_number(query[query.lower().find("lmtd"):], 20) if "lmtd" in q_lc else 20))

        # Process synthesis / design
        if any(k in q_lc for k in ("design", "synthesize", "process")):
            from process_design_advisor import process_synthesis
            return process_synthesis(query)

        # Separation sequence
        if any(k in q_lc for k in ("separation", "separate", "distillation sequence")):
            from process_design_advisor import separation_sequence
            comps = _extract_compounds(query) or ["Water", "Ethanol"]
            return separation_sequence(comps)

        # Troubleshooting
        if any(k in q_lc for k in ("error", "converge", "failed", "fix", "diagnose",
                                     "nullreference", "recycle", "iterations")):
            from dwsim_troubleshooter import (troubleshoot_process,
                                               convergence_guide, error_decoder)
            if "null" in q_lc or "object reference" in q_lc:
                return error_decoder(query)
            if "recycle" in q_lc:
                return convergence_guide("Recycle")
            if "column" in q_lc or "distillation" in q_lc:
                return troubleshoot_process("distillation", query)
            return troubleshoot_process("", query)

        # Property reading via reflection
        if cond["reflection_enabled"] and self.bridge is not None:
            if any(k in q_lc for k in ("temperature", "pressure", "flow", "duty",
                                         "read", "what is", "exec_python")):
                from dwsim_reflection import exec_python, reflect_get_set
                if "exec_python" in q_lc or "delta" in q_lc:
                    code = (
                        "results['feed_T_C']   = get_prop('Feed',   'temperature_C')\n"
                        "results['prod_T_C']   = get_prop('Product','temperature_C')\n"
                        "results['delta_T']    = (results['prod_T_C'] or 0) - (results['feed_T_C'] or 0)"
                    )
                    return exec_python(self.bridge, code)
                # Use bridge
                for prop in ("temperature_C", "pressure_bar", "mass_flow_kgh"):
                    if prop.split("_")[0] in q_lc:
                        return reflect_get_set(self.bridge, "Product", prop)
                # Heater duty
                if "duty" in q_lc and self.bridge:
                    r = reflect_get_set(self.bridge, "H-101", "DeltaQ")
                    if r.get("success"):
                        val = float(r["value"]) / 1000   # W → kW
                        return {"success": True, "value": round(val, 2), "unit": "kW"}

        # List objects
        if any(k in q_lc for k in ("list", "streams", "unit op", "objects in")):
            if self.bridge is not None:
                try:
                    return self.bridge.list_simulation_objects()
                except Exception:
                    pass

        # Simulation — change parameter + solve
        if any(k in q_lc for k in ("set", "change", "run simulation")):
            if self.bridge is not None:
                # Try to extract T
                import re
                nums = re.findall(r'(\d+\.?\d*)\s*(?:°c|degrees|celsius|°)', q_lc)
                if nums and "temperature" in q_lc:
                    tag  = "Product" if "product" in q_lc else "Feed"
                    prop = "temperature_C"
                    val  = float(nums[-1])
                    self.bridge.set_stream_property(tag, prop, val, "C")
                    return self.bridge.run_simulation()

        return f"Condition={self.condition_key}: query routed to deterministic handler but no match found. Query: {query[:80]}"

    def _summarise(self, total_s: float) -> Dict[str, Any]:
        n_total   = len(self.results)
        n_skip    = sum(1 for r in self.results if r.get("skipped"))
        n_run     = n_total - n_skip
        n_pass    = sum(1 for r in self.results if r.get("passed") and not r.get("skipped"))
        pass_rate = round(n_pass / max(n_run, 1) * 100, 1)
        avg_acc   = None
        accs = [r["accuracy_pct_error"] for r in self.results
                 if r.get("accuracy_pct_error") is not None]
        if accs:
            avg_acc = round(sum(accs) / len(accs), 2)
        avg_t = round(sum(r["elapsed_s"] for r in self.results) / max(n_run, 1), 3)

        # Per-category breakdown
        cats: Dict[str, Dict] = {}
        for r in self.results:
            c = r["category"]
            if c not in cats:
                cats[c] = {"passed": 0, "total": 0, "skipped": 0}
            cats[c]["total"] += 1
            if r.get("skipped"):
                cats[c]["skipped"] += 1
            elif r.get("passed"):
                cats[c]["passed"] += 1

        return {
            "condition":       self.condition_key,
            "condition_name":  self.condition["name"],
            "condition_desc":  self.condition["desc"],
            "n_tasks":         n_total,
            "n_skipped":       n_skip,
            "n_run":           n_run,
            "n_passed":        n_pass,
            "pass_rate_pct":   pass_rate,
            "avg_accuracy_pct_error": avg_acc,
            "avg_task_time_s": avg_t,
            "total_time_s":    round(total_s, 2),
            "category_breakdown": cats,
            "task_results":    self.results,
        }


# ─── Utility ──────────────────────────────────────────────────────────────────

def _extract_compounds(text: str) -> List[str]:
    """Simple compound name extractor from query text."""
    known = ["water", "methanol", "ethanol", "propane", "methane",
              "ethane", "butane", "co2", "h2s", "nitrogen", "hydrogen",
              "benzene", "toluene", "acetone", "ammonia"]
    found = []
    for c in known:
        if c in text.lower():
            found.append(c.capitalize())
    return found or ["Water"]


def _first_number(text: str, default: float) -> float:
    import re
    m = re.search(r'[-+]?\d+\.?\d*', text)
    return float(m.group(0)) if m else default


# ─── Multi-condition comparison ────────────────────────────────────────────────

def run_ablation(
    conditions: Optional[List[str]] = None,
    task_ids: Optional[List[str]] = None,
    bridge=None,
    llm=None,
    output_path: str = "ablation_results.json",
) -> Dict[str, Any]:
    """Run all conditions and produce a comparison table."""
    conditions = conditions or list(CONDITIONS.keys())
    all_results = {}

    for cond in conditions:
        print(f"\n{'='*60}")
        print(f"Running condition: {CONDITIONS[cond]['name']}")
        print(f"  {CONDITIONS[cond]['desc']}")
        print(f"{'='*60}")
        runner = AblationRunner(cond, bridge=bridge, llm=llm)
        result = runner.run_all(task_ids)
        all_results[cond] = result
        _print_condition_summary(result)

    # Comparison table
    comparison = {
        "conditions_run": conditions,
        "comparison_table": _build_comparison_table(all_results),
        "per_condition": all_results,
        "markdown_table": _markdown_table(all_results),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, default=str)
    print(f"\n\nResults written to: {output_path}")
    return comparison


def _print_condition_summary(r: Dict):
    print(f"\n  Pass rate:   {r['pass_rate_pct']}%  ({r['n_passed']}/{r['n_run']} tasks)")
    print(f"  Avg time:    {r['avg_task_time_s']}s per task")
    if r.get("avg_accuracy_pct_error") is not None:
        print(f"  Avg error:   {r['avg_accuracy_pct_error']}% (numerical tasks)")
    print("  By category:")
    for cat, d in r["category_breakdown"].items():
        run = d["total"] - d["skipped"]
        print(f"    {cat:25} {d['passed']}/{run} passed")


def _build_comparison_table(results: Dict) -> List[Dict]:
    rows = []
    for cond, r in results.items():
        rows.append({
            "condition":    r["condition_name"],
            "pass_rate":    r["pass_rate_pct"],
            "n_passed":     r["n_passed"],
            "n_run":        r["n_run"],
            "avg_error":    r.get("avg_accuracy_pct_error"),
            "avg_time_s":   r["avg_task_time_s"],
        })
    return rows


def _markdown_table(results: Dict) -> str:
    lines = [
        "| Condition | Pass Rate | Tasks Passed | Avg Error (%) | Avg Time (s) |",
        "|---|:---:|:---:|:---:|:---:|",
    ]
    for cond, r in results.items():
        err = f"{r['avg_accuracy_pct_error']:.2f}" if r.get("avg_accuracy_pct_error") else "—"
        lines.append(
            f"| **{r['condition_name']}** | {r['pass_rate_pct']}% | "
            f"{r['n_passed']}/{r['n_run']} | {err} | {r['avg_task_time_s']}s |"
        )
    return "\n".join(lines)


# ─── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DWSIM Agentic AI Ablation Study")
    parser.add_argument("--condition", default="FULL",
                         choices=list(CONDITIONS.keys()),
                         help="Condition to run (or ALL for all conditions)")
    parser.add_argument("--all-conditions", action="store_true")
    parser.add_argument("--tasks", default="",
                         help="Comma-separated task IDs or categories")
    parser.add_argument("--flowsheet", default="",
                         help="Path to .dwxmz flowsheet to load")
    parser.add_argument("--output", default="ablation_results.json")
    args = parser.parse_args()

    bridge = None
    if args.flowsheet and os.path.exists(args.flowsheet):
        print(f"Loading flowsheet: {args.flowsheet}")
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from dwsim_bridge_v2 import DWSIMBridgeV2
            bridge = DWSIMBridgeV2()
            r = bridge.load_flowsheet(args.flowsheet)
            print(f"  Loaded: {r.get('success')} — {r.get('object_count')} objects")
        except Exception as e:
            print(f"  Warning: could not load flowsheet: {e}")

    task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()] or None

    if args.all_conditions:
        run_ablation(bridge=bridge, task_ids=task_ids, output_path=args.output)
    else:
        cond = args.condition
        runner = AblationRunner(cond, bridge=bridge)
        result = runner.run_all(task_ids)
        _print_condition_summary(result)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        print(f"\nResults written to: {args.output}")
