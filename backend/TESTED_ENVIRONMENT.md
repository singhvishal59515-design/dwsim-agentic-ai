# Tested Environment & Reproducibility

The single biggest reproducibility risk for this project is **version drift** in
its two external dependencies — DWSIM (the .NET simulator) and the LLM provider
SDKs/models. This document records the exact stack the system was verified
against so results can be reproduced and regressions diagnosed.

## Verified platform

| Component | Tested version | Notes |
|---|---|---|
| **DWSIM** | **9.0.5.0** | .NET 8 build, installed at `%LOCALAPPDATA%\DWSIM` |
| **.NET runtime** | 8.x | DWSIM 9 migrated to .NET 8 |
| **Python** | 3.9.13 (64-bit) | Must match DWSIM bitness (64-bit) |
| **OS** | Windows 10 | Windows is the most reliable host for the pythonnet↔DWSIM bridge |
| **pythonnet** | 3.0.3 | `Runtime.PythonDLL` resolved automatically on this build |

## Python dependencies

Reproducible install (exact pins):

```bash
pip install -r requirements-lock.txt
```

`requirements.txt` keeps the looser human-readable spec; `requirements-lock.txt`
holds the exact versions this build was tested with (see that file).

## LLM providers & default models

The agent fails over across four providers; defaults are pinned in
`llm_client.py` (`DEFAULT_MODELS`). Provider behaviour observed during testing:

| Provider | Default model | Caveat |
|---|---|---|
| Anthropic | `claude-sonnet-4-5` | primary; reliable with the full tool schema |
| Groq | `llama-3.3-70b-versatile` | free tier 12k TPM — the ~30k-token tool schema 413s; the client now fails over fast |
| OpenAI | `gpt-4o` / `gpt-4o-mini` | requires a valid key |

(Gemini was removed as a supported provider; selecting it raises a clear error.)

Set keys in `dwsim_full/backend/.env` (git-ignored). Provider/model IDs change
over time — if a model 404s, update `DEFAULT_MODELS`.

## Reproducibility guarantees that are built in

- **DWSIM version is detected and logged** at bridge init (`_dwsim_version`); a
  warning is emitted for versions older than 8.x.
- **Deterministic optimisation seeds** — LHS/surrogate sampling uses fixed RNG
  seeds, so an optimisation run is reproducible given the same flowsheet.
- **Solve watchdog** bounds any single solve; **process-singleton** manager
  prevents the multi-instance wedge.

## Known version-sensitive behaviours

- DWSIM automation-mode solver timeouts existed in v8.3.0–v8.5.0; v9.0.5.0 is
  unaffected.
- DWSIM recycle-convergence changed in v8.8.0 (legacy-mode toggle added v8.8.1).
- Pin DWSIM and re-validate one known flowsheet end-to-end after any upgrade
  (run `python tool_coverage_harness.py`).
