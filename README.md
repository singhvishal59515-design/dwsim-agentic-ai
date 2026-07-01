# DWSIM Agentic AI v2

An AI agent that controls DWSIM chemical process simulations through natural language. Ask questions, optimise processes, run sensitivity analyses, and generate reports — all via a chat interface.

---

## What It Does

- **Chat with your flowsheet** — "What is the ethanol mole fraction in stream S3?"
- **Optimise process variables** — single-variable, multi-variable, or Bayesian optimisation
- **Parametric & Monte Carlo studies** — sweep parameters, propagate uncertainty
- **Economic estimation** — CAPEX, OPEX, NPV from simulation results
- **Safety validation** — check streams against hazard limits
- **Literature comparison** — compare results to published process benchmarks
- **Report generation** — export PDF/HTML reports with charts
- **Session memory** — goals and history persist across sessions

---

## Quick Start

### 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.9 | 3.10+ untested with pythonnet |
| .NET 6+ Runtime | Required for DWSIM bridge |
| DWSIM 8.x | Installed locally |
| Groq API key | Free at [console.groq.com](https://console.groq.com) |

### 2. Install dependencies

```bash
cd dwsim_full/backend
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env and add your GROQ_API_KEY (minimum required)
```

### 4. Start the server

```bash
python api.py
# Server starts at http://localhost:8080
```

Open `http://localhost:8080` in your browser.

---

## LLM Providers

The system supports multiple providers with automatic fallback:

| Provider | Key env var | Cost | Notes |
|----------|-------------|------|-------|
| **Groq** *(default)* | `GROQ_API_KEY` | Free | Fastest, recommended |
| Gemini | `GEMINI_API_KEY` | Free tier | Google AI Studio |
| OpenAI | `OPENAI_API_KEY` | Paid | GPT-4o |
| Anthropic | `ANTHROPIC_API_KEY` | Paid | Claude |
| Ollama | *(none)* | Free, local | Requires Ollama running |

Switch providers live from the UI without restarting the server.

---

## Project Structure

```
dwsim_full/
├── backend/              # Python FastAPI server + all modules
│   ├── api.py            # Main server (50+ REST endpoints)
│   ├── agent_v2.py       # LLM agent with tool-use loop
│   ├── dwsim_bridge_v2.py# DWSIM .NET interop (pythonnet)
│   ├── llm_client.py     # Multi-provider LLM abstraction
│   ├── optimizer.py      # scipy-based optimisation
│   ├── bayesian_optimizer.py
│   ├── economics.py      # CAPEX/OPEX estimation
│   ├── knowledge_base.py # Chemical engineering RAG
│   ├── safety_validator.py
│   ├── ui.html           # Single-file web UI (served at /)
│   ├── .env.example      # Configuration template
│   ├── requirements.txt
│   └── tests/            # pytest test suite
└── frontend/             # React app (in progress)
```

See [backend/ARCHITECTURE.md](backend/ARCHITECTURE.md) for the full technical reference.

---

## Key API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Web UI |
| `/health` | GET | Server health check |
| `/chat/stream` | POST | Chat with SSE streaming |
| `/flowsheet/load` | POST | Load a `.dwsim` file |
| `/flowsheet/run` | POST | Run simulation |
| `/optimize` | POST | Single-variable optimisation |
| `/monte-carlo` | POST | Monte Carlo uncertainty study |
| `/parametric` | POST | Parametric sweep |
| `/economics/estimate` | POST | Economic analysis |
| `/safety/validate` | POST | Safety limit check |
| `/accuracy/compare` | POST | Compare to reference state |
| `/docs` | GET | Auto-generated API docs (Swagger) |

---

## Running Tests

```bash
cd dwsim_full/backend
pytest tests/ -v
```

Tests that require a live DWSIM connection are skipped automatically when DWSIM is not available.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_PROVIDER` | `groq` | Active provider |
| `LLM_MODEL` | provider default | Override model name |
| `GROQ_API_KEY` | — | Groq API key |
| `GEMINI_API_KEY` | — | Google Gemini key |
| `OPENAI_API_KEY` | — | OpenAI key |
| `ANTHROPIC_API_KEY` | — | Anthropic key |
| `DWSIM_DLL_FOLDER` | auto-detected | Path to DWSIM DLLs |
| `PORT` | `8080` | Server port |

---

## Known Limitations

- Chat responses are blocking (no token-by-token streaming yet)
- Long-running jobs (Monte Carlo, optimisation) block the HTTP connection — plan to add background job queue
- No authentication — intended for local/trusted-network use only
- DWSIM bridge is single-threaded; concurrent simulation requests are serialised

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.9, FastAPI, Uvicorn |
| LLM | Groq / Gemini / OpenAI / Anthropic / Ollama |
| DWSIM interop | pythonnet (.NET 6) |
| Optimisation | scipy, numpy |
| Data | pandas, SQLite |
| Reporting | matplotlib, reportlab, pdfplumber |
| Frontend | Single-file HTML/CSS/JS (ui.html) |
| Tests | pytest |
