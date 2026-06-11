# DWSIM Agentic AI v2 — Architecture Reference

Technical deep-dive into how the system is built, how modules interact, and where to find things.

---

## System Overview

```
Browser  (ui.html — 404 KB single-file app)
    │
    │  HTTP REST / SSE streaming / WebSocket
    ▼
┌─────────────────────────────────────────────────────┐
│  api.py  —  FastAPI server  (port 8080)             │
│                                                     │
│  Singletons (lazy-init, thread-safe):               │
│    _bridge  = DWSIMBridgeV2    (bridge lock)        │
│    _agent   = DWSIMAgentV2     (agent lock)         │
│    _watcher = FlowsheetWatcher (WebSocket push)     │
└──────┬──────────────────────────────────────────────┘
       │
       ├── agent_v2.py          LLM agent (tool-use loop)
       │       └── llm_client.py       Provider abstraction (failover chain)
       │       └── tools_schema_v2.py  105 tool definitions
       │       └── prompts.py          System prompts
       │       └── fast_path.py        Regex shortcuts
       │       └── auto_correct.py     Output healing
       │       └── intent.py           Intent parsing
       │       └── experience_store.py Case-based learning loop
       │
       ├── dwsim_bridge_v2.py   .NET interop via pythonnet
       │       └── flowsheet_builder.py    Build flowsheets (topology)
       │       └── flowsheet_executor.py   Build flowsheets (step-wise plan)
       │       └── flowsheet_templates.py  Pre-built templates
       │       └── recycle_analyzer.py     networkx cycle detection + tearing
       │       └── write_verification.py   Read-back-after-write checks
       │       └── dwsim_gui_bridge.py     GUI automation
       │
       ├── optimization_orchestrator.py  NL-goal → gated optimization workflow
       │       └── complex_optimizer.py        multi-solver cascade, bound-widen
       │       └── dwsim_native_optimizer.py   DotNumerics/SciPy + CMA-ES, eval cache
       │       └── surrogate_optimizer.py      EGO / Bayesian surrogate routing
       │       └── eo_optimizer.py             equation-oriented surrogate NLP (IPOPT)
       │       └── multiobjective_nsga.py      NSGA-II Pareto fronts (pymoo)
       │       └── nlopt_constrained.py        native-constraint NLP (NLopt)
       │       └── global_sensitivity.py       Sobol/Morris GSA (SALib)
       │       └── objective_quality.py        hollow-objective advisory gate
       │       └── adaptive_replanner.py        re-plan on insensitive objective
       │
       ├── optimizer.py          scipy single/multi-var optimise (legacy)
       ├── bayesian_optimizer.py Gaussian-process optimisation
       ├── economics.py          CAPEX / OPEX / NPV
       ├── hydrogen_case_study.py Ullah 2025 H2 case study
       │
       ├── dwsim_mcp_server.py   MCP stdio server (9 tools → backend proxy)
       │
       ├── knowledge_base.py     RAG (TF-IDF / sentence-transformers)
       ├── property_db.py        Compound thermodynamic properties
       ├── process_library.py    Literature process benchmarks
       ├── safety_validator.py   Hazard limit catalogue
       │
       ├── evaluation.py         Session scoring, metrics
       ├── accuracy.py           Reference capture & comparison
       ├── reliability.py        Reliability / SLA tracking
       ├── session.py            Session persistence
       └── session_memory.py     Goals and memory store
```

---

## Module Reference

### Core Server

| File | Size | Role |
|------|------|------|
| `api.py` | 113 KB | FastAPI app — ~154 routes, singleton management, lifespan |
| `main_v2.py` | 8 KB | CLI entry-point wrapper, argument parsing |
| `suppress_dotnet_output.py` | 2 KB | Mutes .NET console noise on startup |

**Singleton lifecycle (`api.py`):**
```
startup (lifespan) → _get_bridge() [8s timeout]
                   → _get_agent()  [8s timeout]
                   → watcher.start()
```
All singletons use double-checked locking. The bridge uses `_bridge_lock`, the agent uses `_agent_lock`. WebSocket client list uses `_ws_lock`.

---

### AI Agent Layer

| File | Size | Role |
|------|------|------|
| `agent_v2.py` | 165 KB | Core agent — multi-step tool-use loop, `chat()` method |
| `tools_schema_v2.py` | 104 KB | **105** tool definitions (JSON schema + handlers) |
| `prompts.py` | 9 KB | System prompts, few-shot examples, role definitions |
| `fast_path.py` | 8 KB | Regex shortcuts — bypass LLM for trivial queries |
| `auto_correct.py` | 12 KB | Post-process and self-heal malformed agent outputs |
| `intent.py` | 12 KB | Parse user intent into structured `IntentSpec` dataclass |
| `experience_store.py` | 9 KB | Case-based learning — persist (goal→objective→outcome) cases |

The live tool count is reported dynamically by `/health` (`tool_count = len(DWSIM_TOOLS)`) and shown in the UI badge.

**Chat flow:**
```
POST /chat/stream
  → background thread: agent.chat(message)
  → if agent.chat_stream() exists: yields SSE events
  → else: blocking chat() → single "done" SSE event
  → SSE events sent to browser via asyncio.Queue
```

---

### LLM Layer

| File | Size | Role |
|------|------|------|
| `llm_client.py` | 43 KB | Unified `LLMClient` class for all providers |

**Supported providers:**

| Provider | Default model | Notes |
|----------|--------------|-------|
| `groq` | `llama-3.3-70b-versatile` | Free, fast |
| `openai` | `gpt-4o-mini` | Paid |
| `anthropic` | `claude-sonnet-4-5` | Paid |
| `ollama` | `llama3.2` | Local, no key needed |

Timeout: 90 seconds per request (`_LLM_REQUEST_TIMEOUT_S`).

**Failover & robustness:** a provider failover chain (default `anthropic,groq,openai`) retries on a different provider when one fails, with a **provider-lock** (no mid-turn drift), a **sticky within-turn client**, and **parse-with-producing-client** so a fallback response is parsed by the client that produced it. **Gemini was removed as a provider** (2026-05-31) — `provider='gemini'` now raises — due to incompatible history formatting; the helper code remains dormant.

---

### DWSIM Integration

| File | Size | Role |
|------|------|------|
| `dwsim_bridge_v2.py` | 279 KB | Core bridge — load/save/run flowsheets, get/set all properties |
| `flowsheet_builder.py` | 57 KB | Build flowsheets from a topology dict (objects + connections) |
| `flowsheet_executor.py` | 13 KB | Build flowsheets by walking a step-wise plan via the bridge |
| `flowsheet_templates.py` | 43 KB | Pre-built templates: distillation, reactors, recycle loops, etc. |
| `recycle_analyzer.py` | 7 KB | networkx graph analysis — detect untorn loops, plan tear streams |
| `write_verification.py` | 21 KB | Read-back-after-write — confirm every property set actually took |
| `flowsheet_watcher.py` | 8 KB | Poll `.dwsim` files for changes, push events via WebSocket |
| `dwsim_gui_bridge.py` | 8 KB | Automate DWSIM GUI: open, push flowsheet, read GUI state |

**Key bridge methods:**
- `load_flowsheet(path, alias)` — load a `.dwsim` file
- `run_simulation()` / `robust_solve()` — solve (robust_solve cascades Direct→Wegstein→Broyden)
- `get_stream_properties(tag)` / `set_stream_property(tag, prop, value, unit)`
- `parametric_study(...)` / `parametric_study_2d(...)` — sweeps
- `global_sensitivity(...)` — Sobol/Morris GSA (SALib)
- `optimize_constrained / optimize_multiobjective / optimize_eo(...)` — advanced optimisation
- `initialize_recycle(...)` — seed a recycle stream guess (Wegstein/Broyden)

**Construction-robustness passes** (run automatically during build, both paths):
1. **Recycle auto-tearing** — an untorn algebraic loop can't converge in a sequential-modular solver. `recycle_analyzer` finds cycles lacking an `OT_Recycle` block; the builder splices one in (`tear→[OT_Recycle]→new_stream→consumer`) and **seeds the tear stream** with T/P/composition from the upstream source.
2. **Energy-stream injection** — any energy-requiring unit (Heater/Cooler/Pump/Compressor/Expander) created without a duty connector gets an `EnergyStream` auto-attached, so it is solvable.

Both no-op when the topology is already correct (templates unaffected).

**Thread safety:** All bridge calls in `api.py` are wrapped with `with _bridge_lock:`. The bridge is single-threaded; concurrent requests are serialised. DWSIM runs in-process via pythonnet (single .NET CLR) — so DWSIM solves cannot be parallelised.

---

### Optimisation & Analysis

The optimisation stack is layered: a **natural-language orchestrator** on top, a
**robustness layer** beneath it, and a set of **solver backends** (DWSIM-native +
external libraries) at the bottom. Everything degrades gracefully — each external
library falls back to SciPy/DE when absent.

| File | Size | Role |
|------|------|------|
| `optimization_orchestrator.py` | 106 KB | NL goal → spec; gates; multi-start; verification; the `/optimize/workflow` entry |
| `complex_optimizer.py` | 29 KB | Multi-solver cascade: global pass → local refine, bound-widening, stagnation/eval-failure handling |
| `dwsim_native_optimizer.py` | 30 KB | Core solver loop — DotNumerics/SciPy + **CMA-ES**; per-run **evaluation cache** |
| `dwsim_native_solvers.py` | 12 KB | DotNumerics bindings (same engines as DWSIM's GUI Optimizer) |
| `dwsim_algorithms.py` | 23 KB | 14 DWSIM optimisation algorithms (PSO, DE, GA, …) |
| `surrogate_optimizer.py` | 9 KB | EGO / Bayesian surrogate routing for expensive evaluations |
| `eo_optimizer.py` | 18 KB | Equation-oriented surrogate NLP (DOE → quadratic surrogate → **IPOPT**/SLSQP → validate), adaptive refinement + cross-validated trust flag |
| `multiobjective_nsga.py` | 4 KB | **NSGA-II** Pareto fronts (pymoo) — non-convex fronts |
| `nlopt_constrained.py` | 7 KB | Native nonlinear-constraint NLP (**NLopt** GN_ISRES/COBYLA) |
| `global_sensitivity.py` | 5 KB | **Sobol/Morris** global sensitivity (SALib) |
| `objective_quality.py` | 5 KB | Advisory gate — flags hollow/trivial objectives, suggests intensive ones |
| `constraint_solver.py` | 10 KB | Penalty-function inequality/equality constraint handling |
| `adaptive_replanner.py` | 13 KB | Re-plan to a responsive objective when one is insensitive |
| `optimizer.py` | 39 KB | `DWSIMOptimizer` — scipy `minimize` wrapper (legacy single/multi-var) |
| `bayesian_optimizer.py` | 21 KB | Gaussian-process optimisation (scikit-learn / skopt) |
| `economics.py` | 24 KB | Lang-factor CAPEX, utility OPEX, NPV/payback period |
| `hydrogen_case_study.py` | 19 KB | Full Ullah 2025 biogas-SMR H₂ production study |

**External optimiser libraries** (in `requirements.txt`): `cma` (CMA-ES),
`pymoo` (NSGA-II), `nlopt` (constrained NLP), `SALib` (GSA), `pyomo` +
`idaes-pse` (the IPOPT binary for equation-oriented NLP), `networkx`
(flowsheet graph analysis).

**Orchestrator gate sequence** (`run_optimization_workflow`): goal-clarity →
property-package check → objective readability/confidence → **objective
meaningfulness (hollow-objective advisory)** → converged-baseline check →
**objective-sensitivity gate** (perturb each var, confirm the objective moves) →
optional surrogate routing → multi-start solve → **post-run optimum-reproducibility
verification** (re-apply optimum, re-solve, confirm it reproduces).

---

### Knowledge & Data

| File | Size | Role |
|------|------|------|
| `knowledge_base.py` | 310 KB | Chemical engineering RAG — TF-IDF search, optional sentence-transformers |
| `property_db.py` | 58 KB | Compound Cp, Hf, Antoine constants (SQLite: `thermo_properties.db`) |
| `process_library.py` | 25 KB | Literature benchmarks for 20+ processes (ammonia, methanol, ethylene, ...) |
| `safety_validator.py` | 78 KB | HAZOP-style limit catalogue + stream validation logic |

**Knowledge base:** Falls back from semantic (sentence-transformers) to TF-IDF automatically if the model isn't installed. Embeddings cached in `__pycache__/kb_embeddings_*.npy`.

---

### Evaluation & Quality

| File | Size | Role |
|------|------|------|
| `evaluation.py` | 36 KB | Session scoring (0–1), metrics logging, failure tracking |
| `accuracy.py` | 27 KB | Capture simulation state as reference, compare later |
| `reliability.py` | 37 KB | Rolling reliability metrics, uptime, error rates |
| `eval_harness.py` | 22 KB | Evaluation test harness — run predefined evaluation suites |
| `benchmark_tasks.py` | 31 KB | 25 benchmark tasks + in-process `list_tasks` / `run_task` / `run_all` / `summarize_results` (back the Eval-tab panel) |
| `run_benchmark.py` | 16 KB | CLI benchmark runner (HTTP); pure scorers reused in-process |
| `ablation.py` | 31 KB | Component ablation framework — knock out modules, measure impact |
| `ablation_paper_table.py` | 8 KB | Generate LaTeX tables for academic papers |
| `finetune_data.py` | 12 KB | Export interaction logs as JSONL for fine-tuning |
| `replay_log.py` | 17 KB | Replay recorded interaction logs for regression testing |

**Stored evaluation data:**
- `eval_log.json` — all session records
- `failure_cases.json` — logged failures for review
- `benchmarks.json` — benchmark task results
- `accuracy_store.json` — reference snapshots
- `ablation_log.jsonl` — ablation experiment records
- `hydrogen_results.jsonl` — H₂ case study history

---

### Session & Memory

| File | Size | Role |
|------|------|------|
| `session.py` | 3 KB | Save/load full session (agent + bridge state) |
| `session_memory.py` | 10 KB | Persistent goals, recent entries, text search |

---

### Reporting

| File | Size | Role |
|------|------|------|
| `report_generator.py` | 26 KB | Generate PDF/HTML simulation reports (reportlab + matplotlib) |
| `diagnostics.py` | 12 KB | Probe LLM providers, system health, model availability |
| `benchmark_report.py` | 8 KB | Format and export benchmark results |

---

### MCP Server

| File | Size | Role |
|------|------|------|
| `dwsim_mcp_server.py` | 11 KB | Model Context Protocol stdio server (JSON-RPC 2.0) |

A thin **proxy** that exposes 9 curated tools (`dwsim_health`, `dwsim_load_flowsheet`,
`dwsim_list_objects`, `dwsim_get_stream`, `dwsim_set_stream_property`, `dwsim_solve`,
`dwsim_optimize`, `dwsim_agent`, `dwsim_loaded_flowsheet`) to MCP clients. Each tool
forwards to the backend over HTTP (`DWSIM_BACKEND_URL`, default `http://localhost:8080`),
so it drives the same live DWSIM instance — the backend must be running.

Works in Claude Desktop (native), Claude Code, and Cursor — **not** the browser
Claude. Config lives in each client's `mcpServers` block; the packaged (MSIX) Claude
Desktop reads from a redirected `…\Packages\Claude_…\LocalCache\Roaming\Claude\` path.
`dwsim_optimize` runs the full orchestrator (`/optimize/workflow`).

---

## API Endpoint Map

### Chat
| Route | Method | Description |
|-------|--------|-------------|
| `/chat/stream` | POST | Chat with SSE streaming |
| `/chat/reset` | POST | Clear conversation history |

### Flowsheet
| Route | Method | Description |
|-------|--------|-------------|
| `/flowsheet/load` | POST | Load `.dwsim` file |
| `/flowsheet/save` | POST | Save current flowsheet |
| `/flowsheet/run` | POST | Execute solver |
| `/flowsheet/validate` | POST | Check feed consistency |
| `/flowsheet/objects` | GET | List streams and unit ops |
| `/flowsheet/results` | GET | Get all simulation results |
| `/flowsheet/diagram` | GET | Get topology as nodes/edges |
| `/flowsheet/switch` | POST | Switch active flowsheet |
| `/flowsheet/templates` | GET | List available templates |
| `/flowsheet/create-from-template` | POST | Instantiate a template |
| `/flowsheet/compare` | GET | Compare two loaded flowsheets |
| `/flowsheet/pinch` | GET | Pinch analysis |
| `/flowsheet/backups` | GET | List backups |
| `/flowsheet/backups/restore` | POST | Restore a backup |

### Simulation Properties
| Route | Method | Description |
|-------|--------|-------------|
| `/stream/properties` | POST | Get all stream properties |
| `/stream/set_property` | POST | Set a stream property |
| `/stream/set_composition` | POST | Set stream composition |
| `/unitop/set_property` | POST | Set unit op property |
| `/object/properties` | POST | Get object properties |

### Optimisation & Studies
| Route | Method | Description |
|-------|--------|-------------|
| `/optimize` | POST | Single-variable optimisation (legacy) |
| `/optimize/multivar` | POST | Multi-variable optimisation |
| `/optimize/bayesian` | POST · `/async` | Bayesian / surrogate optimisation |
| `/optimize/workflow` | POST · `/async` | **NL-goal orchestrated workflow** (gates + verification; CMA-ES global) |
| `/optimize/complex` | POST | Robust multi-solver cascade (bound-widening) |
| `/optimize/dwsim-native` | POST · `/async` | DotNumerics engines (DWSIM GUI Optimizer parity) |
| `/optimize/internal` | POST · `/async` | DWSIM internal optimiser |
| `/optimize/algorithm` | POST · `/async` | Pick one of 14 algorithms (PSO/DE/GA/…) |
| `/optimize/suggest-variables` | GET | LLM suggests decision variables from the flowsheet |
| `/optimize/algorithms` · `/benchmarks` · `/complexity` · `/pp-check` | GET | Solver metadata / pre-checks |
| `/parametric` | POST | Parametric sweep |
| `/monte-carlo` | POST | Monte Carlo uncertainty study |

Multi-objective (NSGA-II), global sensitivity (Sobol/Morris), and equation-oriented
(EO/IPOPT) optimisation are exposed as **agent tools** (`optimize_multiobjective`,
`global_sensitivity`, `optimize_eo`) reached via `/chat/stream`, plus the bridge
methods of the same name — they are not standalone REST routes.

### Knowledge & Data
| Route | Method | Description |
|-------|--------|-------------|
| `/knowledge` | GET | Semantic search in knowledge base |
| `/knowledge/topics` | GET | List knowledge topics |
| `/compounds` | GET | Search compound database |
| `/compounds/{name}/properties` | GET | Compound thermodynamic data |
| `/property-packages` | GET | Available property packages |
| `/process-library` | GET | List literature processes |
| `/literature/compare` | POST | Compare simulation to literature |

### Economics & Safety
| Route | Method | Description |
|-------|--------|-------------|
| `/economics/defaults` | GET | Default cost parameters |
| `/economics/estimate` | POST | Run economic analysis |
| `/safety/catalogue` | GET | Hazard limit catalogue |
| `/safety/validate` | POST | Validate stream against limits |

### Accuracy & Evaluation
| Route | Method | Description |
|-------|--------|-------------|
| `/accuracy/capture` | POST | Snapshot current simulation state |
| `/accuracy/compare` | POST | Compare current vs reference |
| `/accuracy/reference` | GET | List captured references |
| `/eval/metrics` | GET | Current evaluation metrics |
| `/eval/sessions` | GET | Session evaluation history |
| `/eval/feedback/{id}` | POST | Submit human feedback |
| `/eval/failures` | GET | List failure cases |
| `/eval/benchmarks` | GET | List the 25 benchmark tasks (Eval-tab panel) |
| `/eval/benchmark/run` | POST | Run one task in-process against live DWSIM |
| `/eval/benchmark/run-all` | POST | Run the whole suite (async) → measured pass-rate report |
| `/eval/benchmark/results` | GET | Most-recent result per task + pass-rate |
| `/benchmark/tasks` · `/benchmark/run` | GET · POST | Same, second (Tasks-tab) consumer shape |

### Sessions & Memory
| Route | Method | Description |
|-------|--------|-------------|
| `/sessions` | GET | List saved sessions |
| `/sessions/save` | POST | Save current session |
| `/sessions/load` | POST | Load a session |
| `/memory/recent` | GET | Recent memory entries |
| `/memory/search` | GET | Search memory |
| `/memory/goals` | GET | Active goals |
| `/memory/record` | POST | Record a memory entry |

### LLM Management
| Route | Method | Description |
|-------|--------|-------------|
| `/llm/status` | GET | Current provider and model |
| `/llm/switch` | POST | Switch provider/model live |
| `/llm/groq/models` | GET | Available Groq models |
| `/llm/ollama/models` | GET | Available local Ollama models |

### Hydrogen Case Study
| Route | Method | Description |
|-------|--------|-------------|
| `/hydrogen/build` | POST | Build H₂ flowsheet from template |
| `/hydrogen/run` | POST | Run base + optimal case |
| `/hydrogen/sensitivity` | POST | Run sensitivity analysis |
| `/hydrogen/report` | GET | Retrieve last report |

### System
| Route | Method | Description |
|-------|--------|-------------|
| `/health` | GET | Server health + bridge status + live `tool_count` |
| `/diagnostics` | GET | Full system diagnostics |
| `/diagnostics/providers` | GET | Probe all LLM providers |
| `/admin/reload-env` | POST | Reload `.env` without restart |
| `/ws/flowsheets` | WS | WebSocket for file-change events |
| `/docs` | GET | Swagger UI |

---

## Data Flow: Chat Request

```
1. Browser POSTs { message, auto_reflect } to /chat/stream
2. api.py opens SSE StreamingResponse
3. Background thread spawned → agent.chat(message)
4. Agent parses message → selects tools → calls bridge
5. Bridge executes DWSIM operation (under _bridge_lock)
6. Agent synthesises answer
7. Answer emitted as { type: "done", data: "..." } SSE event
8. Browser renders markdown in chat panel
```

---

## Data Flow: Optimisation (orchestrated workflow)

```
1. Browser POSTs { goal } to /optimize/workflow/async
   (or the agent calls it; or the panel's NL box)
2. optimization_orchestrator.run_optimization_workflow:
   a. build spec from goal (LLM) → decision vars + objective + direction
   b. GATES: goal-clarity · pp-check · objective confidence ·
             hollow-objective advisory · baseline-converges ·
             objective-sensitivity (perturb each var)
   c. route: surrogate (EGO) if expensive, else complex_optimizer cascade
   d. complex_optimizer: global pass (CMA-ES/DE) → local refine,
      bound-widening, multi-start, eval-failure handling
   e. each evaluation: set vars → solve (cached) → read objective
   f. VERIFY: re-apply optimum, re-solve, confirm it reproduces
3. Streams steps/evals via the task queue; final poster-style result
   includes verification banner + objective_quality assessment.
```

The legacy single-shot path (`/optimize` → `DWSIMOptimizer` → `scipy.minimize`)
still exists for simple one-variable cases.

---

## Threading Model

```
Main thread:  uvicorn event loop (asyncio)
              └── handles all HTTP/WS/SSE

Per chat:     daemon thread (agent.chat blocks here)
              └── communicates back via asyncio.Queue

Watcher:      daemon thread (polls file system every 3s)
              └── pushes via asyncio.run_coroutine_threadsafe()

Locks:
  _bridge_lock  — serialises all DWSIM bridge calls
  _agent_lock   — guards singleton agent creation
  _ws_lock      — guards WebSocket client list mutations
```

---

## Test Suite

**~50 test files, 414 passing / 5 skipped** (run `pytest -q` from `backend/`).
Most use a mock bridge/agent, so the suite needs no DWSIM install; tests that
need live DWSIM are skipped automatically.

Notable suites:
```
tests/
├── test_sync_safety.py                 Bridge lock thread-safety
├── test_agent_logic.py                 Agent tool-use and routing logic
├── test_optimization_orchestrator.py   Gates, multi-start, workflow
├── test_complex_optimizer.py           Multi-solver cascade, bound-widening
├── test_dwsim_native_optimizer.py      Core solver loop + eval cache
├── test_cma_es_optimizer.py            CMA-ES backend
├── test_nsga_multiobjective.py         NSGA-II Pareto fronts
├── test_nlopt_constrained.py           NLopt native constraints
├── test_global_sensitivity.py          Sobol/Morris (Ishigami benchmark)
├── test_eo_optimizer.py                EO surrogate NLP + CV trust guard
├── test_objective_quality.py           Hollow-objective gate (LLE case)
├── test_optimum_verification.py        Post-run reproducibility check
├── test_recycle_injection.py           Recycle auto-tearing + tear seeding
├── test_energy_stream_injection.py     Energy-stream auto-injection
├── test_benchmark_panel.py             list_tasks / run_task / run_all
├── test_mcp_server.py                  MCP stdio JSON-RPC
└── test_write_verification*.py         Read-back-after-write surface
```

Tests requiring a live DWSIM installation are automatically skipped when DWSIM
is not available.

---

## File Sizes at a Glance

| File | Size | Note |
|------|------|------|
| `knowledge_base.py` | 310 KB | Largest — embedded knowledge corpus |
| `dwsim_bridge_v2.py` | 279 KB | Core .NET bridge |
| `agent_v2.py` | 165 KB | Split candidate |
| `tools_schema_v2.py` | 104 KB | Split candidate |
| `safety_validator.py` | 78 KB | Embedded limit tables |
| `property_db.py` | 58 KB | Embedded property data |
| `optimization_orchestrator.py` | 106 KB | Split candidate |
| `ui.html` | 404 KB | Entire frontend in one file |

Files marked "Split candidate" are too large to review effectively and should be broken into focused submodules.

---

## Known Technical Debt

| Issue | Severity | Location |
|-------|----------|----------|
| No `chat_stream()` — responses not streamed token-by-token | High | `agent_v2.py` |
| No authentication | High | `api.py` — `allow_origins=["*"]` |
| `agent_v2.py` is 165 KB — unmaintainable | Medium | `agent_v2.py` |
| `optimization_orchestrator.py` now 106 KB — split candidate | Medium | `optimization_orchestrator.py` |
| Benchmark run path needs **live DWSIM** to produce a measured pass-rate | Medium | `/eval/benchmark/run-all` |
| Equation-oriented optimisation is **surrogate-based**, not native (DWSIM doesn't expose equations) | Medium | `eo_optimizer.py` |
| DWSIM solves cannot be parallelised (single in-process CLR) | Medium | `dwsim_bridge_v2.py` |
| Raw `dict` request bodies (no Pydantic validation) | Medium | Several endpoints |
| `print()` instead of `logging` | Medium | Throughout |
| React frontend incomplete | Low | `frontend/` |

**Resolved since earlier revisions:** long-running jobs now have **async/task-queue
variants** (`/optimize/*/async`, `/eval/benchmark/run-all`) instead of blocking HTTP;
the optimisation stack gained CMA-ES/NSGA-II/NLopt/SALib/EO backends, a hollow-objective
gate, a per-run evaluation cache, and recycle/energy auto-injection on build.
