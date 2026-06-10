"""
llm_client.py
─────────────
Unified LLM client supporting 5 providers:

  PROVIDER     FREE?   API KEY NEEDED   NOTES
  ─────────────────────────────────────────────────────────────
  groq         ✅ YES   Yes (free)       Best free option. Fast Llama 3.3 / Gemma2
  gemini       ✅ YES   Yes (free)       Google AI Studio free tier
  ollama       ✅ YES   No               100% local, no internet needed
  openai       ❌ Paid  Yes              GPT-4o etc.
  anthropic    ❌ Paid  Yes              Claude etc.

Free API keys:
  Groq   -> https://console.groq.com          (no credit card)
  Gemini -> https://aistudio.google.com/app/apikey

Ollama (local):
  1. Install: https://ollama.com/download
  2. Pull a model: ollama pull llama3.2
  3. Run: python main.py --provider ollama --model llama3.2
"""

import json
import os
import re
import ssl as _ssl
import tempfile
import time
import urllib.request
import urllib.error
import warnings
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore", category=FutureWarning,
                        module=r"google\.(api_core|auth|oauth2|generativeai)")


# ─────────────────────────────────────────────────────────────────────────────
# SSL fix — corporate / antivirus TLS interception breaks the SDK HTTP clients
# (openai / anthropic / groq use httpx; gemini uses its own transport). Those
# default to certifi's CA bundle, which does NOT contain the interception root
# cert that the Windows trust store DOES contain. Build a combined bundle
# (certifi + Windows ROOT/CA store), set the env vars, AND patch ssl's default
# context so EVERY library (httpx, urllib, google-genai) trusts it.
# Without this, all four LLM providers fail with CERTIFICATE_VERIFY_FAILED.
# ─────────────────────────────────────────────────────────────────────────────

_COMBINED_CA_BUNDLE: Optional[str] = None


def _build_combined_ca_bundle() -> Optional[str]:
    global _COMBINED_CA_BUNDLE
    if _COMBINED_CA_BUNDLE:
        return _COMBINED_CA_BUNDLE
    try:
        import certifi
        pem = open(certifi.where(), encoding="utf-8").read()
    except Exception:
        return None
    added = 0
    if hasattr(_ssl, "enum_certificates"):     # Windows only
        for store in ("ROOT", "CA"):
            try:
                for cert_bytes, _enc, _trust in _ssl.enum_certificates(store):
                    try:
                        pem += "\n" + _ssl.DER_cert_to_PEM_cert(cert_bytes)
                        added += 1
                    except Exception:
                        pass
            except Exception:
                pass
    try:
        path = os.path.join(tempfile.gettempdir(), "dwsim_llm_combined_ca.pem")
        with open(path, "w", encoding="utf-8") as f:
            f.write(pem)
        _COMBINED_CA_BUNDLE = path
        return path
    except Exception:
        return None


def _install_ssl_fix() -> Optional[str]:
    """Build the combined bundle, export env vars, and patch ssl's default
    context so all HTTP libraries trust the Windows store. Returns the
    bundle path (or None if it couldn't be built)."""
    bundle = _build_combined_ca_bundle()
    if not bundle:
        return None
    # Env vars (picked up by requests, some httpx setups, curl, urllib)
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
                "CURL_CA_BUNDLE", "HTTPX_SSL_CERT_FILE"):
        os.environ.setdefault(var, bundle)
        # setdefault won't overwrite an existing value; force-set if it points
        # to a missing file
        if not os.path.exists(os.environ.get(var, "")):
            os.environ[var] = bundle
    # Patch ssl default context — the universal fix that httpx + google-genai
    # both honour because they build on ssl.create_default_context.
    try:
        _orig = _ssl.create_default_context
        def _patched(*a, **k):
            ctx = _orig(*a, **k)
            try:
                ctx.load_verify_locations(cafile=bundle)
            except Exception:
                pass
            return ctx
        _ssl.create_default_context = _patched          # type: ignore
        _ssl._create_default_https_context = _patched   # type: ignore
    except Exception:
        pass
    return bundle


# Run the SSL fix at import — BEFORE any SDK client is constructed.
_CA_BUNDLE = _install_ssl_fix()


def _make_httpx_client():
    """Return an httpx.Client that verifies against the combined CA bundle,
    or None if httpx / the bundle is unavailable (SDK then uses its default)."""
    if not _CA_BUNDLE:
        return None
    try:
        import httpx
        return httpx.Client(verify=_CA_BUNDLE, timeout=90.0)
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Provider defaults & model lists
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MODELS: Dict[str, str] = {
    "groq":      "llama-3.3-70b-versatile",
    "ollama":    "llama3.2",
    "openai":    "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
}

# Per-request LLM timeout. If the provider doesn't answer within this, we fail
# fast and let the agent loop report a clear error instead of hanging.
_LLM_REQUEST_TIMEOUT_S = 90.0

# Sentinel a system-prompt builder may insert to mark the boundary between the
# large STABLE instruction prefix and the per-turn DYNAMIC context (flowsheet
# state, RAG, memory). Providers with explicit prompt caching (Anthropic) cache
# everything before it; everyone else just strips it. Keeps the ~8k-token base
# prompt from being re-billed/re-processed on every iteration of a turn.
CACHE_BREAKPOINT = "<<<PROMPT_CACHE_BREAKPOINT>>>"

# Groq models with tool-calling support, ordered best→fastest.
# Decommissioned (do NOT add back):
#   llama-3.1-70b-versatile, llama-3.3-70b-specdec, mixtral-8x7b-32768
GROQ_MODELS: List[str] = [
    # ── Llama 4 (Meta, 2025) ────────────────────────────────────────────────
    "meta-llama/llama-4-maverick-17b-128e-instruct",  # 128-expert MoE, best quality
    "meta-llama/llama-4-scout-17b-16e-instruct",      # 16-expert MoE, fast + capable
    # ── Llama 3.3 / 3.1 ─────────────────────────────────────────────────────
    "llama-3.3-70b-versatile",          # best reliable quality, 32k ctx
    "llama-3.1-8b-instant",             # 500k TPD free — fast fallback
    # ── Llama 3.2 (vision-capable but text works fine) ───────────────────────
    "llama-3.2-90b-vision-preview",     # largest Llama 3.2
    "llama-3.2-11b-vision-preview",     # smaller vision
    "llama-3.2-3b-preview",             # very fast, low quota usage
    "llama-3.2-1b-preview",             # ultra-fast
    # ── Llama 3 (8k ctx) ─────────────────────────────────────────────────────
    "llama3-70b-8192",                  # Llama 3 70B
    "llama3-8b-8192",                   # Llama 3 8B, separate quota pool
    # ── Reasoning models ─────────────────────────────────────────────────────
    "deepseek-r1-distill-llama-70b",    # DeepSeek R1 reasoning (distilled)
    "qwen-qwq-32b",                     # QwQ-32B reasoning model
    # ── Specialist / other ───────────────────────────────────────────────────
    "mistral-saba-24b",                 # Mistral Saba 24B
    "gemma2-9b-it",                     # Google Gemma2 9B
    "gemma-7b-it",                      # Google Gemma 7B
    "allam-2-7b",                       # Arabic + multilingual
]

# Gemini model aliases: short name -> canonical SDK name
GEMINI_ALIASES: Dict[str, str] = {
    # Gemini 2.5 — point the stable alias at the now-GA stable model, not the
    # decommissioned dated preview (which 404s: "gemini-2.5-flash-preview-04-17
    # is not found"). The old preview alias is kept mapping to the stable model
    # so any cached reference still resolves.
    "gemini-2.5-flash":                    "gemini-2.5-flash",
    "gemini-2.5-flash-preview-04-17":      "gemini-2.5-flash",
    "gemini-2.5-pro":                      "gemini-2.5-pro-preview-03-25",
    "gemini-2.5-pro-preview-03-25":        "gemini-2.5-pro-preview-03-25",
    # Gemini 2.0
    "gemini-2.0-flash":        "gemini-2.0-flash",
    "gemini-2.0-flash-lite":   "gemini-2.0-flash-lite",
    # Gemini 1.5
    "gemini-1.5-flash":        "gemini-1.5-flash-latest",
    "gemini-1.5-flash-latest": "gemini-1.5-flash-latest",
    "gemini-1.5-flash-8b":     "gemini-1.5-flash-8b-latest",
    "gemini-1.5-pro":          "gemini-1.5-pro-latest",
    "gemini-1.5-pro-latest":   "gemini-1.5-pro-latest",
}

GEMINI_FALLBACK: List[str] = [
    "gemini-2.0-flash",           # best free tier (2025)
    "gemini-2.0-flash-lite",      # lighter, separate quota
    "gemini-2.5-flash-preview-04-17",  # latest preview
    "gemini-2.5-flash",           # 2.5 stable
    "gemini-1.5-flash",           # stable fallback
    "gemini-1.5-flash-8b",        # smallest, highest quota
    "gemini-1.5-pro",             # best quality, lower quota
]


# ─────────────────────────────────────────────────────────────────────────────

class LLMClient:
    """
    Provider-agnostic LLM client with unified tool-calling interface.
    Supports: groq | ollama | openai | anthropic
    (Gemini has been removed as a supported provider.)

    Reproducibility note (journal reviewer requirement):
      temperature=0 is set on all providers to maximise determinism.
      Note: temperature=0 reduces but does NOT eliminate stochasticity —
      providers implement it differently. For true reproducibility, log
      the exact prompt hash, tool sequence, and random seed (see
      _REPRODUCIBILITY_SEED). Report run-to-run variance in ablation tables.
    """

    # Reproducibility seed — used where provider API accepts explicit seed
    # (OpenAI, some Groq models). Logged in every request for paper reporting.
    _REPRODUCIBILITY_SEED: int = 42

    def __init__(self, provider: str, api_key: str = "",
                 model: Optional[str] = None,
                 ollama_host: str = "http://localhost:11434",
                 temperature: float = 0.0) -> None:
        self.provider     = provider.lower().strip()
        self.api_key      = api_key
        self.model        = model or DEFAULT_MODELS.get(self.provider, "")
        self.ollama_host  = ollama_host.rstrip("/")
        self.temperature  = temperature   # 0.0 = maximally deterministic
        self._client      = None
        self._gemini_sdk: Optional[str] = None
        self._last_error: Optional[str] = None   # most recent underlying error
        # When False, chat() will NOT permanently switch this client to a
        # different PROVIDER on failure (it still does within-provider model
        # fallback, but raises on a cross-provider failure instead of mutating
        # itself). The agent sets this False on the user's selected client so a
        # transient rate-limit doesn't silently/permanently move the user to a
        # different provider — the agent's own transient failover handles it.
        self._allow_provider_switch = True
        self._setup()

    # ── setup ─────────────────────────────────────────────────────────────────

    def _setup(self) -> None:
        p = self.provider

        if p == "groq":
            if not self.api_key:
                raise ValueError(
                    "Groq API key required.\n"
                    "Get a FREE key at: https://console.groq.com\n"
                    "Then: set GROQ_API_KEY=gsk_...")
            try:
                from groq import Groq
                _hc = _make_httpx_client()
                self._client = (Groq(api_key=self.api_key, http_client=_hc)
                                if _hc else Groq(api_key=self.api_key))
            except ImportError:
                raise ImportError("Run: pip install groq")

        elif p == "gemini":
            # Gemini has been removed as a supported provider for this project.
            # The code paths (_chat_gemini, _msgs_to_gemini, GEMINI_*) remain
            # dormant but Gemini is no longer selectable or used for failover.
            raise ValueError(
                "Gemini is no longer a supported provider in this project.\n"
                "Use one of: groq | ollama | openai | anthropic")

        elif p == "ollama":
            # No pip package needed — uses urllib
            self._verify_ollama()

        elif p == "openai":
            try:
                from openai import OpenAI
                _hc = _make_httpx_client()
                self._client = (OpenAI(api_key=self.api_key, http_client=_hc)
                                if _hc else OpenAI(api_key=self.api_key))
            except ImportError:
                raise ImportError("Run: pip install openai")

        elif p == "anthropic":
            try:
                import anthropic
                _hc = _make_httpx_client()
                self._client = (anthropic.Anthropic(api_key=self.api_key, http_client=_hc)
                                if _hc else anthropic.Anthropic(api_key=self.api_key))
            except ImportError:
                raise ImportError("Run: pip install anthropic")

        else:
            raise ValueError(
                f"Unknown provider '{self.provider}'.\n"
                "Free options: groq | ollama\n"
                "Paid options: openai | anthropic")

    def _verify_ollama(self) -> None:
        """Check Ollama is running and the model is available."""
        try:
            with urllib.request.urlopen(
                    f"{self.ollama_host}/api/tags", timeout=3) as r:
                data  = json.loads(r.read())
                names = [m["name"].split(":")[0] for m in data.get("models", [])]
            wanted = self.model.split(":")[0]
            if wanted not in names:
                print(f"   [Ollama] Model '{self.model}' not found locally.")
                print(f"   Available: {names}")
                print(f"   Pull it with: ollama pull {self.model}")
            else:
                print(f"   [Ollama] Model '{self.model}' ready.")
        except Exception as exc:
            print(f"   [Ollama] Cannot reach {self.ollama_host}: {exc}")
            print("   Make sure Ollama is running: https://ollama.com/download")

    # ── provider fallback chain ───────────────────────────────────────────────

    def _switch_provider(self, reason: str = "") -> bool:
        """
        Cross-provider fallback chain: Groq -> Gemini -> OpenAI -> Anthropic.
        Reads API keys from environment. Returns True if successfully switched.
        `reason` is logged with the switch message for debugging.
        """
        import os
        # Caller (e.g. the agent's selected client) has disabled persistent
        # cross-provider switching — return False so chat() raises and the
        # caller's own (transient) failover handles it without mutating the
        # user's selected provider.
        if not getattr(self, "_allow_provider_switch", True):
            return False
        if reason:
            print(f"   [Fallback] Reason for switching from {self.provider.upper()}: {reason}")
        # Ordered fallback chain — Groq is first if it's the primary provider,
        # otherwise it's last (since it can produce XML tool calls with large schemas)
        # Prefer free providers first; skip Groq if its key was already used
        # and returned 403 (quota/revoked) — it won't magically recover mid-session.
        # Fallback order: prefer free providers first; never re-try the current one.
        # The chain is just priority order — we always skip self.provider.
        if self.provider == "groq":
            chain = ["openai", "anthropic"]
        elif self.provider == "openai":
            chain = ["anthropic", "groq"]
        else:
            chain = ["openai", "groq"]
        key_env = {
            "groq":      "GROQ_API_KEY",
            "openai":    "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
        }

        for next_provider in chain:
            key = os.getenv(key_env.get(next_provider, ""), "")
            if not key:
                continue  # no key configured — skip
            try:
                print(f"\n   [Fallback] {self.provider.upper()} exhausted, switching to {next_provider.upper()}")
                self.provider = next_provider
                self.api_key  = key
                self.model    = DEFAULT_MODELS.get(next_provider, "")
                self._client  = None
                self._gemini_sdk = None
                self._setup()
                print(f"   [Fallback] Now using {next_provider.upper()} / {self.model}")
                return True
            except Exception as e:
                print(f"   [Fallback] {next_provider.upper()} setup failed: {e}")
                continue
        return False

    # ── public chat ───────────────────────────────────────────────────────────

    def normalize_history(self, messages: List[Dict]) -> List[Dict]:
        """Return a history guaranteed-compatible with the CURRENT provider.

        Tool-call / tool-result linkage is NOT portable across providers (the
        ids differ and each SDK rejects foreign shapes), so when the history
        contains any message that isn't native to the active provider family —
        i.e. a failover switched providers between turns — the whole history is
        flattened to neutral alternating plain-text turns that every provider
        accepts. When nothing is foreign (the overwhelmingly common case) the
        original list is returned unchanged, so there is zero behaviour change
        on the steady-state single-provider path."""
        target = _provider_family(self.provider)
        if all(_msg_family(m) in (target, None) for m in messages):
            return messages
        return _flatten_history_to_text(messages)

    def chat(self, messages: List[Dict], tools: List[Dict],
             system_prompt: str = "") -> Dict[str, Any]:
        """
        Send messages -> normalised response dict:
          {"content": str, "tool_calls": [...], "stop_reason": str, "_raw": ...}

        Fallback order on quota exhaustion:
          Groq (all models) -> OpenAI -> Anthropic
        """
        _FN = {
            "groq":      self._chat_groq,
            "gemini":    self._chat_gemini,
            "ollama":    self._chat_ollama,
            "openai":    self._chat_openai,
            "anthropic": self._chat_anthropic,
        }

        # Two independent budgets so a transient per-minute rate-limit on one
        # provider can no longer starve the cross-provider failover:
        #   * provider_attempt — calls made to the CURRENT provider; resets to 0
        #     on every model/provider switch. Drives XML-fail detection and the
        #     per-minute rate-limit retry count.
        #   * total_calls — global safety cap to guarantee termination even if
        #     providers keep switching (prevents an infinite fallback ping-pong).
        provider_attempt = 0
        total_calls      = 0
        _MAX_TOTAL_CALLS = 16
        _last_signature  = None

        while total_calls < _MAX_TOTAL_CALLS:
            # Reset the per-provider counter whenever the active provider/model
            # changed since the last iteration (a model or provider switch).
            _sig = (self.provider, self.model)
            if _sig != _last_signature:
                provider_attempt = 0
                _last_signature  = _sig
            total_calls      += 1
            provider_attempt += 1
            attempt = provider_attempt  # preserve existing per-provider semantics
            fn = _FN[self.provider]
            # Cross-turn / cross-provider safety: a previous turn (or an earlier
            # iteration of this loop) may have left `messages` in another
            # provider's tool-call format. Convert to the ACTIVE provider's
            # format before dispatch (no-op when already native).
            send_messages = self.normalize_history(messages)
            # Only Anthropic consumes the cache-breakpoint marker (to split the
            # cached prefix from dynamic context); strip it for everyone else so
            # the marker never leaks into a system prompt.
            sys_arg = system_prompt
            if sys_arg and self.provider != "anthropic" and CACHE_BREAKPOINT in sys_arg:
                sys_arg = sys_arg.replace(CACHE_BREAKPOINT, "")
            try:
                return fn(send_messages, tools, sys_arg)
            except Exception as exc:
                s = str(exc)
                is_404 = "404" in s or "NOT_FOUND" in s or "not found" in s.lower()
                is_decommissioned = "decommissioned" in s or "model_decommissioned" in s
                is_429 = _is_quota_error(exc)
                is_daily = _is_daily_quota(exc)
                # A request-size error must be checked BEFORE the rate-limit
                # branch (Groq's 413 also matches "rate_limit"), because waiting
                # can never shrink a statically-oversized request.
                is_too_large = _is_request_too_large(exc)
                # Groq XML tool_use_failed that couldn't be parsed -> switch provider
                is_xml_fail = (self.provider == "groq" and attempt >= 2 and
                               (getattr(exc, "_groq_xml_fail", False) or
                                "tool_use_failed" in s))

                # ── decommissioned model -> try next model in same provider ──
                if is_decommissioned:
                    switched = False
                    if self.provider == "groq":
                        switched = self._try_next_groq_model()
                    if switched:
                        print(f"   [{self.provider.upper()}] Decommissioned -> trying {self.model}")
                        continue
                    # No more models — fall through to cross-provider switch
                    if self._switch_provider(reason="all models decommissioned"):
                        continue
                    raise

                # ── 404 model not found -> try next model in same provider ──
                if is_404:
                    switched = False
                    if self.provider == "groq":
                        switched = self._try_next_groq_model()
                    elif self.provider == "gemini":
                        switched = self._try_next_gemini_model()
                    if switched:
                        print(f"   [{self.provider.upper()}] Model not found -> trying {self.model}")
                        continue
                    # No more models in this provider -> cross-provider fallback
                    print(f"   [{self.provider.upper()}] All models exhausted -> switching provider")
                    if self._switch_provider(reason=f"404 — all models tried (last error: {s[:80]})"):
                        continue
                    raise

                # ── request too large (non-retryable) ─────────────────────
                # The request exceeds the model/tier size limit — waiting for a
                # rate window cannot help. Remember the real reason and switch
                # provider IMMEDIATELY (no 65–120s wait loop). This is what was
                # making chat hang for minutes on Groq free tier with the full
                # 100+-tool schema (~30k tokens vs a 12k TPM limit).
                if is_too_large:
                    self._last_error = s
                    print(f"\n   [{self.provider.upper()}] Request too large for "
                          f"this model/tier — switching provider (no wait): {s[:120]}")
                    if self._switch_provider(reason=f"request too large: {s[:80]}"):
                        print(f"   Cross-provider fallback -> {self.provider}/{self.model}")
                        continue
                    raise

                # ── quota / rate limit ────────────────────────────────────
                if is_429:
                    if is_daily:
                        # Daily quota — switch model first, then provider
                        switched = False
                        if self.provider == "groq":
                            switched = self._try_next_groq_model()
                        elif self.provider == "gemini":
                            switched = self._try_next_gemini_model()

                        if switched:
                            print(f"\n   [Daily quota] Switching model -> {self.model}")
                            continue
                        # All models in this provider exhausted -> next provider
                        if self._switch_provider(reason=f"daily quota — all models tried (HTTP 429)"):
                            continue
                        raise
                    else:
                        # Per-minute rate limit — wait for the quota window to reset,
                        # then retry up to 3 times before switching provider.
                        # Waiting ~65s is more reliable than switching mid-run (which
                        # can break tool-call format compatibility).
                        if attempt <= 3:
                            retry_after = _parse_retry_delay(exc) or 0
                            # Cap wait: first attempt ~15s, subsequent ~65s (quota reset window)
                            # Never wait more than 120s regardless of what the API says
                            if attempt == 1:
                                wait = min(retry_after or 8, 15)
                            else:
                                wait = min(max(retry_after or 65, 65), 120)
                            print(f"\n   [Rate limit] {self.provider.upper()} quota — "
                                  f"waiting {wait}s… (attempt {attempt}/3)")
                            time.sleep(wait)
                            continue
                        else:
                            print(f"\n   [Rate limit] {self.provider.upper()} per-minute quota"
                                  f" — switching provider")
                            if self._switch_provider(reason="per-minute rate limit after 3 retries"):
                                continue
                            return None

                # XML tool_use_failed on Groq that couldn't be parsed -> next provider
                if is_xml_fail:
                    if self._switch_provider(reason="XML tool_use_failed unparseable"):
                        continue

                # ── Catch-all: any unhandled error → try cross-provider ──
                # Covers SDK bugs like 'AsyncRequest' errors, connection
                # errors, unexpected API responses, etc.
                print(f"\n   [{self.provider.title()}] Unexpected error: {s[:120]}")
                if self._switch_provider(reason=f"unexpected error: {s[:80]}"):
                    print(f"   Cross-provider fallback -> {self.provider}/{self.model}")
                    continue
                # All providers exhausted — return None so agent can report gracefully
                return None

        # Global call budget exhausted (e.g. repeated provider ping-pong) —
        # give up gracefully so the agent can report instead of hanging.
        print(f"\n   [LLM] Failover budget exhausted after {total_calls} calls — giving up")
        return None

    # ── history helpers ───────────────────────────────────────────────────────

    def assistant_turn(self, response: Dict[str, Any]) -> Dict[str, Any]:
        p = self.provider

        if p in ("groq", "openai"):
            raw = response["_raw"]
            return {
                "role":    "assistant",
                "content": raw.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in (raw.tool_calls or [])
                ],
            }
        elif p == "gemini":
            parts: List[Dict] = []
            if response["content"]:
                parts.append({"text": response["content"]})
            for tc in response["tool_calls"]:
                parts.append({"function_call": {
                    "name": tc["name"], "args": tc["arguments"]}})
            return {"role": "model", "parts": parts}

        elif p == "ollama":
            raw = response["_raw"]
            turn: Dict[str, Any] = {
                "role":    "assistant",
                "content": response["content"],
            }
            if response["tool_calls"]:
                turn["tool_calls"] = [
                    {"id":   tc["id"],
                     "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["arguments"])}}
                    for tc in response["tool_calls"]
                ]
            return turn

        elif p == "anthropic":
            return {"role": "assistant", "content": response["_raw"].content}

    def tool_result_turns(self, tool_calls: List[Dict],
                          results: List[Any]) -> List[Dict]:
        p = self.provider
        if p in ("groq", "openai", "ollama"):
            return [
                {"role": "tool", "tool_call_id": tc["id"],
                 "name": tc["name"], "content": _jstr(res)}
                for tc, res in zip(tool_calls, results)
            ]
        elif p == "gemini":
            return [{"role": "user", "parts": [
                {"function_response": {
                    "name": tc["name"],
                    "response": {"result": _jstr(res)}}}
                for tc, res in zip(tool_calls, results)
            ]}]
        elif p == "anthropic":
            return [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": tc["id"],
                 "content": _jstr(res)}
                for tc, res in zip(tool_calls, results)
            ]}]

    # ── GROQ (free) ───────────────────────────────────────────────────────────

    def _chat_groq(self, messages, tools, system_prompt) -> Dict[str, Any]:
        """
        Groq uses an OpenAI-compatible API.
        Free tier: https://console.groq.com
        Handles tool_use_failed (XML-style fallback) transparently.
        """
        full = ([{"role": "system", "content": system_prompt}]
                if system_prompt else []) + messages

        groq_tools = [{"type": "function", "function": t} for t in tools]

        try:
            _max_tok = getattr(self, "_MAX_TOKENS_OVERRIDE", None) or 4096
            create_kwargs = dict(
                model=self.model,
                messages=full,
                max_tokens=_max_tok,
                temperature=self.temperature,
                seed=self._REPRODUCIBILITY_SEED,
                timeout=_LLM_REQUEST_TIMEOUT_S,
            )
            if groq_tools:
                create_kwargs["tools"] = groq_tools
                create_kwargs["tool_choice"] = "auto"
            resp = self._client.chat.completions.create(**create_kwargs)
        except Exception as exc:
            # Groq sometimes returns 400 tool_use_failed when the model generates
            # XML-style calls like <function=name {}</function>.
            # Parse the failed_generation field and recover.
            recovered = _parse_groq_tool_use_failed(exc)
            if recovered is not None:
                return recovered
            # Mark as xml_fail so chat() can trigger provider switch
            exc._groq_xml_fail = True
            raise

        msg = resp.choices[0].message
        content = msg.content or ""

        # Detect XML tool calls in the content (model degraded to XML format)
        # e.g. <function=load_flowsheet {"path": "..."}></function>
        if not msg.tool_calls and re.search(r"<function=\w+", content):
            # Try to parse XML calls from content
            recovered = _parse_groq_xml_content(content)
            if recovered:
                return recovered
            # Unparseable XML — raise so chat() can switch provider
            err = Exception(f"tool_use_failed: model returned XML tool calls in content")
            err._groq_xml_fail = True  # type: ignore
            raise err

        # Detect JSON-formatted tool calls in content (Llama-4 / Hermes style).
        # e.g.  {"type": "function", "name": "x", "parameters": {...}}
        # The model writes the call as plain text instead of using the
        # structured tool_calls API — recover by parsing the JSON.
        if not msg.tool_calls and '"type"' in content and '"function"' in content \
                and ('"name"' in content) and ('"parameters"' in content or '"arguments"' in content):
            recovered = _parse_json_tool_call_content(content)
            if recovered:
                return recovered

        tool_calls = []
        for tc in (msg.tool_calls or []):
            args = tc.function.arguments
            try:
                args = json.loads(args)
            except Exception:
                args = {}
            tool_calls.append({
                "id":        tc.id,
                "name":      tc.function.name,
                "arguments": args,
            })
        _usage = {}
        if hasattr(resp, "usage") and resp.usage:
            _usage = {
                "tokens_in":  getattr(resp.usage, "prompt_tokens", 0) or 0,
                "tokens_out": getattr(resp.usage, "completion_tokens", 0) or 0,
            }
        return {"content":     content,
                "tool_calls":  tool_calls,
                "stop_reason": resp.choices[0].finish_reason,
                "_raw":        msg,
                "_usage":      _usage}

    def _try_next_groq_model(self) -> bool:
        try:
            idx = GROQ_MODELS.index(self.model)
        except ValueError:
            idx = -1
        if idx + 1 < len(GROQ_MODELS):
            self.model = GROQ_MODELS[idx + 1]
            return True
        return False

    # ── GEMINI (free) ─────────────────────────────────────────────────────────

    def _chat_gemini(self, messages, tools, system_prompt) -> Dict[str, Any]:
        if self._gemini_sdk == "new":
            return self._chat_gemini_new(messages, tools, system_prompt)
        return self._chat_gemini_old(messages, tools, system_prompt)

    def _chat_gemini_new(self, messages, tools, system_prompt) -> Dict[str, Any]:
        from google.genai import types as gt  # type: ignore
        decls = [gt.FunctionDeclaration(
            name=t["name"], description=t.get("description", ""),
            parameters=_schema_to_gemini(t.get("parameters", {})))
            for t in tools] if tools else []

        cfg = gt.GenerateContentConfig(
            system_instruction=system_prompt or None,
            tools=[gt.Tool(function_declarations=decls)] if decls else None,
            temperature=self.temperature,
        )
        resp = self._client.models.generate_content(
            model=self.model,
            contents=_msgs_to_gemini(messages),
            config=cfg,
        )
        tcs, txts = _parse_gemini_parts(_gemini_parts(resp), resp)
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": "tool_use" if tcs else "stop", "_raw": resp,
                "_usage": {}}

    def _chat_gemini_old(self, messages, tools, system_prompt) -> Dict[str, Any]:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from google.generativeai import protos  # type: ignore
        decls = [protos.FunctionDeclaration(
            name=t["name"], description=t.get("description", ""),
            parameters=_schema_to_gemini(t.get("parameters", {})))
            for t in tools] if tools else []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = self._client.GenerativeModel(
                model_name=self.model,
                system_instruction=system_prompt or None,
                tools=[protos.Tool(function_declarations=decls)] if decls else None,
                generation_config={"temperature": self.temperature},
            )
        resp = m.generate_content(_msgs_to_gemini(messages))
        _parts = getattr(resp, "parts", None)
        if _parts is None:
            _parts = _gemini_parts(resp)
        tcs, txts = _parse_gemini_parts(_parts, resp)
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": "tool_use" if tcs else "stop", "_raw": resp,
                "_usage": {}}

    def _try_next_gemini_model(self) -> bool:
        try:
            idx = GEMINI_FALLBACK.index(self.model)
        except ValueError:
            idx = -1
        if idx + 1 < len(GEMINI_FALLBACK):
            self.model = GEMINI_FALLBACK[idx + 1]
            return True
        return False

    # ── OLLAMA (local free) ───────────────────────────────────────────────────

    def _chat_ollama(self, messages, tools, system_prompt) -> Dict[str, Any]:
        """
        Calls Ollama's OpenAI-compatible /v1/chat/completions endpoint.
        Requires Ollama running locally: https://ollama.com
        """
        full = ([{"role": "system", "content": system_prompt}]
                if system_prompt else []) + messages

        ollama_tools = [{"type": "function", "function": t} for t in tools]

        payload = {
            "model":   self.model,
            "messages": full,
            "stream":  False,
        }
        if ollama_tools:
            payload["tools"] = ollama_tools

        body = json.dumps(payload).encode()
        req  = urllib.request.Request(
            f"{self.ollama_host}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Ollama error {exc.code}: {exc.read().decode()[:300]}\n"
                f"Make sure Ollama is running and model '{self.model}' is pulled.\n"
                f"Run: ollama pull {self.model}"
            )
        except ConnectionRefusedError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {self.ollama_host}.\n"
                "Start Ollama: https://ollama.com/download"
            )

        choice = data["choices"][0]
        msg    = choice["message"]
        content = msg.get("content") or ""

        tool_calls = []
        for tc in (msg.get("tool_calls") or []):
            fn   = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            tool_calls.append({
                "id":        tc.get("id", f"ollama_{len(tool_calls)}"),
                "name":      fn.get("name", ""),
                "arguments": args,
            })

        return {"content":     content,
                "tool_calls":  tool_calls,
                "stop_reason": choice.get("finish_reason", "stop"),
                "_raw":        msg,
                "_usage":      {}}

    # ── OPENAI (paid) ─────────────────────────────────────────────────────────

    def _chat_openai(self, messages, tools, system_prompt) -> Dict[str, Any]:
        full = ([{"role": "system", "content": system_prompt}]
                if system_prompt else []) + messages
        ot = [{"type": "function", "function": t} for t in tools]
        resp = self._client.chat.completions.create(
            model=self.model, messages=full,
            tools=ot or None, tool_choice="auto" if ot else None,
            temperature=self.temperature,
            seed=self._REPRODUCIBILITY_SEED,
            timeout=_LLM_REQUEST_TIMEOUT_S,
        )
        msg = resp.choices[0].message
        tcs = []
        for tc in (msg.tool_calls or []):
            tcs.append({"id": tc.id, "name": tc.function.name,
                         "arguments": json.loads(tc.function.arguments)})
        _usage = {}
        if hasattr(resp, "usage") and resp.usage:
            _usage = {
                "tokens_in":  getattr(resp.usage, "prompt_tokens", 0) or 0,
                "tokens_out": getattr(resp.usage, "completion_tokens", 0) or 0,
            }
        return {"content": msg.content or "", "tool_calls": tcs,
                "stop_reason": resp.choices[0].finish_reason, "_raw": msg,
                "_usage": _usage}

    # ── ANTHROPIC (paid) ──────────────────────────────────────────────────────

    def _chat_anthropic(self, messages, tools, system_prompt) -> Dict[str, Any]:
        at = [{"name": t["name"], "description": t.get("description", ""),
               "input_schema": t.get("parameters",
                                     {"type": "object", "properties": {}})}
              for t in tools]
        # Build kwargs conditionally: the Anthropic API rejects tools=None and
        # system=None ("Input should be a valid array"). Omit them entirely
        # when empty rather than passing None.
        kwargs = {
            "model":       self.model,
            "max_tokens":  4096,
            "messages":    messages,
            "temperature": self.temperature,  # Anthropic: 0.0–1.0
            "timeout":     _LLM_REQUEST_TIMEOUT_S,
        }
        if system_prompt:
            # Prompt caching: cache the large STABLE instruction prefix so it is
            # billed/processed once per ~5-min window instead of on every loop
            # iteration. The builder marks the split with CACHE_BREAKPOINT; the
            # dynamic suffix (flowsheet state, RAG, memory) stays uncached. Blocks
            # under the model's min cacheable size are silently not cached — safe.
            if CACHE_BREAKPOINT in system_prompt:
                stable, dynamic = system_prompt.split(CACHE_BREAKPOINT, 1)
                sys_blocks = [{"type": "text", "text": stable,
                               "cache_control": {"type": "ephemeral"}}]
                if dynamic.strip():
                    sys_blocks.append({"type": "text", "text": dynamic})
                kwargs["system"] = sys_blocks
            else:
                kwargs["system"] = system_prompt
        if at:                          # only include tools when non-empty
            # Cache the tool definitions too (a stable prefix segment): mark the
            # last tool so the whole block is cached up to that point.
            at[-1] = {**at[-1], "cache_control": {"type": "ephemeral"}}
            kwargs["tools"] = at
        resp = self._client.messages.create(**kwargs)
        tcs, txts = [], []
        for blk in resp.content:
            if blk.type == "text":
                txts.append(blk.text)
            elif blk.type == "tool_use":
                tcs.append({"id": blk.id, "name": blk.name,
                             "arguments": blk.input})
        _usage = {}
        if hasattr(resp, "usage") and resp.usage:
            _usage = {
                "tokens_in":  getattr(resp.usage, "input_tokens",  0) or 0,
                "tokens_out": getattr(resp.usage, "output_tokens", 0) or 0,
                # Prompt-cache visibility: how many input tokens were written to
                # vs read from cache this call (read tokens are ~10% the price).
                "cache_write": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read":  getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
            }
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": resp.stop_reason, "_raw": resp,
                "_usage": _usage}


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_groq_xml_content(content: str) -> Optional[Dict]:
    """
    Parse XML-style tool calls from Groq response content, e.g.:
      <function=load_flowsheet {"path": "..."}></function>

    Uses JSON raw_decode to find the JSON object boundary (handles cases
    where the JSON arguments contain "</function>" or other tag-like substrings).
    Returns a normalised response dict or None if parsing fails.
    """
    tool_calls = []
    decoder = json.JSONDecoder()
    consumed_spans = []
    pos = 0
    while True:
        m = re.search(r"<function=(\w+)\s*", content[pos:])
        if not m:
            break
        name = m.group(1).strip()
        json_start = pos + m.end()
        # Try JSON raw_decode at this position to find the args object boundary
        args: dict = {}
        json_end = json_start
        # Skip whitespace
        while json_end < len(content) and content[json_end] in " \t\n\r":
            json_end += 1
        if json_end < len(content) and content[json_end] == "{":
            try:
                args, consumed = decoder.raw_decode(content[json_end:])
                json_end += consumed
            except json.JSONDecodeError:
                # Fallback: take to next </function> or end
                end_tag = content.find("</function>", json_end)
                json_end = end_tag if end_tag >= 0 else len(content)
                args = {}
        # Consume optional ">" and "</function>"
        while json_end < len(content) and content[json_end] in " >":
            json_end += 1
        if content[json_end:json_end + 11] == "</function>":
            json_end += 11
        consumed_spans.append((pos + m.start(), json_end))
        tool_calls.append({
            "id":        f"call_{name}_{len(tool_calls)}",
            "name":      name,
            "arguments": args,
        })
        pos = json_end
    if not tool_calls:
        return None
    # Strip XML from content by removing consumed spans
    parts, last = [], 0
    for s, e in consumed_spans:
        parts.append(content[last:s])
        last = e
    parts.append(content[last:])
    clean = "".join(parts).strip()

    class _FakeMsg:
        def __init__(self):
            self.content    = clean
            self.tool_calls = None
    return {
        "content":     clean,
        "tool_calls":  tool_calls,
        "stop_reason": "tool_calls",
        "_raw":        _FakeMsg(),
    }


def _parse_json_tool_call_content(content: str) -> Optional[Dict]:
    """Parse JSON-formatted tool calls that some Llama-4 / Hermes-style
    fine-tunes emit as TEXT instead of using the OpenAI tool-call API:

        {"type": "function", "name": "optimize_flowsheet_with_llm",
         "parameters": {"goal": {"type": "string",
                                  "value": "maximize hydrogen production"}}}

    The "parameters" object can be either:
      • Direct kwargs:   {"goal": "..."}
      • Schema-wrapped:  {"goal": {"type":"string", "value":"..."}}
      • A literal "arguments" alias

    Returns a normalised response dict, or None if no parseable call found.
    """
    decoder = json.JSONDecoder()
    tool_calls: List[Dict] = []
    consumed_spans: List[tuple] = []
    pos = 0

    # Scan for `{` that opens a candidate tool-call object
    while pos < len(content):
        # Find the next `{"` — likely start of a JSON object
        m = re.search(r'\{\s*"', content[pos:])
        if not m:
            break
        start = pos + m.start()
        try:
            obj, consumed = decoder.raw_decode(content[start:])
        except json.JSONDecodeError:
            pos = start + 1
            continue
        end = start + consumed

        # Must be a tool-call shape: has "name" and either "parameters" or "arguments"
        if isinstance(obj, dict) and "name" in obj and \
                ("parameters" in obj or "arguments" in obj):
            name = str(obj.get("name", "")).strip()
            params_raw = obj.get("parameters") or obj.get("arguments") or {}
            # Unwrap schema-style {"type": "X", "value": Y} → Y for each leaf
            args = _unwrap_schema_params(params_raw)
            if name:
                tool_calls.append({
                    "id":        f"call_{name}_{len(tool_calls)}",
                    "name":      name,
                    "arguments": args,
                })
                consumed_spans.append((start, end))
        pos = end

    if not tool_calls:
        return None

    # Strip the consumed JSON from the content so the remaining text (if any)
    # is the model's natural-language explanation.
    parts, last = [], 0
    for s, e in consumed_spans:
        parts.append(content[last:s])
        last = e
    parts.append(content[last:])
    clean = "".join(parts).strip()

    class _FakeMsg:
        def __init__(self):
            self.content    = clean
            self.tool_calls = None

    return {
        "content":     clean,
        "tool_calls":  tool_calls,
        "stop_reason": "tool_calls",
        "_raw":        _FakeMsg(),
    }


def _unwrap_schema_params(params):
    """Recursively unwrap {"type": "X", "value": Y} → Y. Some models emit
    JSON-schema-style parameter objects instead of plain key/value kwargs.
    Returns a clean dict suitable for direct kwargs passing."""
    if isinstance(params, dict):
        # Single schema cell: {"type": "string", "value": "..."}
        if set(params.keys()) <= {"type", "value", "description"} \
                and "value" in params:
            return _unwrap_schema_params(params["value"])
        # Otherwise treat as a kwargs dict
        return {k: _unwrap_schema_params(v) for k, v in params.items()}
    if isinstance(params, list):
        return [_unwrap_schema_params(x) for x in params]
    return params


def _parse_groq_tool_use_failed(exc: Exception) -> Optional[Dict]:
    """
    Groq returns HTTP 400 / code='tool_use_failed' when the model generates
    an XML-style function call like:
        <function=list_simulation_objects {}</function>
    or  <function=set_stream_property {"tag":"X","property_name":"temperature","value":373}</function>

    Parse the failed_generation field and return a normalised response dict
    so the agent can continue without crashing.
    """
    fg = None

    # Path 1: Groq SDK stores structured body in exc.body
    try:
        body = getattr(exc, "body", None) or {}
        if isinstance(body, dict):
            err = body.get("error", {})
            if err.get("code") == "tool_use_failed":
                fg = err.get("failed_generation", "")
    except Exception:
        pass

    # Path 2: parse str(exc) as fallback
    if fg is None:
        s = str(exc)
        if "tool_use_failed" not in s and "failed_generation" not in s:
            return None
        for pat in (r"'failed_generation':\s*'([^']+)'",
                    r'"failed_generation":\s*"([^"]+)"'):
            m = re.search(pat, s)
            if m:
                fg = m.group(1).strip()
                break

    if not fg:
        return None

    # Parse the XML-style call: <function=NAME ARGS</function> or <function=NAME ARGS>
    m = re.match(r"<function=(\w+)\s*(.*?)(?:</function>)?\s*$", fg, re.DOTALL)
    if not m:
        # BUG-7 fix: log unparseable XML so it is not silently lost
        import logging as _log
        _log.getLogger(__name__).warning(
            "Groq tool_use_failed: could not parse failed_generation=%r", fg[:200]
        )
        return None

    name = m.group(1).strip()
    raw_args = m.group(2).strip().rstrip(">").strip()
    try:
        args = json.loads(raw_args) if raw_args and raw_args not in ("{}", "") else {}
    except Exception:
        args = {}

    tc_id   = f"groq_recovered_{name}"
    args_str = json.dumps(args)

    # Build a fake raw message that assistant_turn() can consume
    # (Groq path: raw.content, raw.tool_calls[i].id/function.name/function.arguments)
    class _FakeFn:
        def __init__(self):
            self.name      = name
            self.arguments = args_str
    class _FakeTc:
        def __init__(self):
            self.id       = tc_id
            self.function = _FakeFn()
    class _FakeMsg:
        def __init__(self):
            self.content    = ""   # BUG-4 fix: use "" not None — prevents None.strip() crash
            self.tool_calls = [_FakeTc()]

    import logging as _log
    _log.getLogger(__name__).debug(
        "Groq XML recovery succeeded: tool=%s args=%s", name, args_str[:120]
    )
    return {
        "content":     "",
        "tool_calls":  [{"id": tc_id, "name": name, "arguments": args}],
        "stop_reason": "tool_calls",
        "_raw":        _FakeMsg(),
    }

def _jstr(obj: Any) -> str:
    return obj if isinstance(obj, str) else json.dumps(obj, default=str)


def _provider_family(provider: str) -> str:
    """Group providers that share an on-the-wire message shape.
    groq/openai/ollama all speak the OpenAI chat format; anthropic and gemini
    each have their own. History is only portable WITHIN a family."""
    if provider in ("groq", "openai", "ollama"):
        return "openai"
    if provider == "gemini":
        return "gemini"
    return provider  # anthropic (and any future native shape)


def _msg_family(m: Dict) -> Optional[str]:
    """Best-effort classification of which provider family a stored history
    message belongs to. Returns None for plain `{role, content:str}` messages,
    which every provider accepts as-is."""
    role = m.get("role")
    if "parts" in m or role == "model":
        return "gemini"
    if role == "tool" or "tool_calls" in m:
        return "openai"
    if isinstance(m.get("content"), list):
        return "anthropic"
    return None  # plain-text / system — universal


def _msg_to_text(m: Dict) -> str:
    """Flatten any single history message (any provider shape) into readable
    plain text, folding tool calls / tool results into bracketed annotations so
    the conversational context survives a provider switch."""
    role    = m.get("role")
    content = m.get("content")
    chunks: List[str] = []

    # Gemini-style parts
    if "parts" in m:
        for p in m.get("parts") or []:
            if not isinstance(p, dict):
                chunks.append(str(p)); continue
            if "text" in p:
                chunks.append(p["text"])
            elif "function_call" in p:
                fc = p["function_call"]
                chunks.append(f"[called {fc.get('name','')}({_jstr(fc.get('args', {}))})]")
            elif "function_response" in p:
                fr = p["function_response"]
                chunks.append(f"[tool {fr.get('name','')} result: "
                              f"{_jstr(fr.get('response', {}).get('result', ''))}]")
        return " ".join(c for c in chunks if c)

    # Anthropic-style content-block list (dicts or SDK objects)
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                t = b.get("type")
                if t == "text" or "text" in b:
                    chunks.append(b.get("text", ""))
                elif t == "tool_use":
                    chunks.append(f"[called {b.get('name','')}({_jstr(b.get('input', {}))})]")
                elif t == "tool_result":
                    chunks.append(f"[tool result: {_jstr(b.get('content', ''))}]")
            else:  # anthropic SDK content block object
                t = getattr(b, "type", None)
                if t == "text":
                    chunks.append(getattr(b, "text", ""))
                elif t == "tool_use":
                    chunks.append(f"[called {getattr(b, 'name', '')}("
                                  f"{_jstr(getattr(b, 'input', {}))})]")
                elif t == "tool_result":
                    chunks.append(f"[tool result: {_jstr(getattr(b, 'content', ''))}]")
        text = " ".join(c for c in chunks if c)
    else:
        text = content if isinstance(content, str) else (str(content) if content else "")

    # OpenAI-style tool_calls attached to an assistant message
    for tc in (m.get("tool_calls") or []):
        fn   = tc.get("function", {}) if isinstance(tc, dict) else {}
        text = (text + f"\n[called {fn.get('name','')}({fn.get('arguments','')})]").strip()

    # OpenAI-style tool result message
    if role == "tool":
        text = f"[tool {m.get('name', 'tool')} result: {text}]"
    return text


def _flatten_history_to_text(messages: List[Dict]) -> List[Dict]:
    """Convert a (possibly mixed-provider) history into neutral, strictly
    alternating user/assistant `{role, content:str}` turns that EVERY provider
    accepts. Consecutive same-role messages are merged to satisfy Anthropic's
    alternation requirement; a leading assistant turn is dropped so the
    transcript always starts with the user."""
    flat: List[tuple] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue  # system prompt is passed out-of-band, never in history
        cat  = "assistant" if role in ("assistant", "model") else "user"
        text = _msg_to_text(m).strip()
        if text:
            flat.append((cat, text))

    merged: List[tuple] = []
    for cat, text in flat:
        if merged and merged[-1][0] == cat:
            merged[-1] = (cat, merged[-1][1] + "\n" + text)
        else:
            merged.append((cat, text))

    while merged and merged[0][0] == "assistant":
        merged.pop(0)  # conversation must start with a user turn
    return [{"role": r, "content": t} for r, t in merged]


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(k in s for k in ("429", "quota", "rate_limit", "rate limit",
                                 "resource_exhausted", "resource exhausted",
                                 "too many requests"))


def _is_request_too_large(exc: Exception) -> bool:
    """A NON-retryable size error: the request itself exceeds the model/tier
    limit, so waiting for a rate window to reset cannot help (the tool schema
    is statically too big). Distinct from a throughput rate-limit. Example:
    Groq free tier returns HTTP 413 'Request too large … Requested 29751,
    Limit 12000 TPM, please reduce your message size'."""
    s = str(exc).lower()
    return any(k in s for k in (
        "request too large", "request_too_large", "reduce your message size",
        "413", "payload too large", "context_length_exceeded",
        "maximum context length", "string too long", "too many tokens"))


def _is_daily_quota(exc: Exception) -> bool:
    s = str(exc).lower()
    return ("per_day" in s or "perday" in s or "daily" in s
            or "tokens per day" in s or "tpd" in s or "requests per day" in s)


def _parse_retry_delay(exc: Exception) -> int:
    for pat in (r"retry.*?(\d+)\s*s", r'"seconds":\s*(\d+)', r"(\d+)s"):
        m = re.search(pat, str(exc), re.IGNORECASE)
        if m:
            return int(m.group(1))
    return 30


def _gemini_parts(resp) -> list:
    """Safely extract the content parts from a Gemini response. Returns [] when
    the response was blocked / empty / quota-limited rather than crashing with
    "'NoneType' object is not iterable" (resp.candidates can be None)."""
    cands = getattr(resp, "candidates", None) or []
    if not cands:
        return []
    content = getattr(cands[0], "content", None)
    return list(getattr(content, "parts", None) or []) if content else []


def _parse_gemini_parts(parts, resp):
    """Turn Gemini parts into (tool_calls, text_chunks). When there are no
    usable parts, raise a descriptive error so chat()'s failover engages and
    the real reason (finish_reason / safety block / quota) is surfaced instead
    of an opaque crash."""
    tcs, txts = [], []
    for part in (parts or []):
        fc = getattr(part, "function_call", None)
        if fc and getattr(fc, "name", None):
            tcs.append({"id": f"g_{fc.name}_{len(tcs)}",
                        "name": fc.name,
                        "arguments": dict(fc.args) if fc.args else {}})
        elif getattr(part, "text", None):
            txts.append(part.text)
    if not tcs and not txts:
        cands = getattr(resp, "candidates", None) or []
        fr = getattr(cands[0], "finish_reason", None) if cands else None
        pf = getattr(resp, "prompt_feedback", None)
        raise RuntimeError(
            f"Gemini returned no usable content (finish_reason={fr}, "
            f"prompt_feedback={pf}) — likely a safety block or quota limit.")
    return tcs, txts


def _schema_to_gemini(schema: Dict) -> Dict:
    if not schema:
        return {"type": "OBJECT", "properties": {}}
    TM = {"string": "STRING", "number": "NUMBER", "integer": "INTEGER",
          "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT"}

    def _c(p: Dict) -> Dict:
        out: Dict = {"type": TM.get(p.get("type", "string"), "STRING")}
        for k in ("description", "enum"):
            if k in p:
                out[k] = p[k]
        if "properties" in p:
            out["properties"] = {k: _c(v) for k, v in p["properties"].items()}
        if "items" in p:
            out["items"] = _c(p["items"])
        elif out["type"] == "ARRAY":
            # Gemini REQUIRES every array to declare `items`; OpenAI/Anthropic
            # tolerate its absence. A tool that omits it (e.g. an array of
            # objects described only in prose) otherwise 400s the ENTIRE Gemini
            # request ("...parameters.properties[x].items: missing field").
            # Default to object items so one under-specified tool can't break
            # the whole provider.
            out["items"] = {"type": "OBJECT"}
        return out

    r: Dict = {"type": "OBJECT"}
    if "properties" in schema:
        r["properties"] = {k: _c(v) for k, v in schema["properties"].items()}
    if "required" in schema:
        r["required"] = schema["required"]
    return r


def _msgs_to_gemini(messages: List[Dict]) -> List[Dict]:
    out = []
    for m in messages:
        role    = "model" if m["role"] == "assistant" else "user"
        content = m.get("content", "")
        if m["role"] == "tool":
            out.append({"role": "user", "parts": [{
                "function_response": {
                    "name":     m.get("name", "tool"),
                    "response": {"result": content}}}]})
            continue
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    parts.append({"text": str(item)}); continue
                t = item.get("type", "")
                if t == "tool_result":
                    parts.append({"function_response": {
                        "name":     item.get("tool_use_id", "tool"),
                        "response": {"result": item.get("content", "")}}})
                elif t == "text":
                    parts.append({"text": item.get("text", "")})
                elif "function_response" in item or "function_call" in item:
                    parts.append(item)
                elif "text" in item:
                    parts.append({"text": item["text"]})
            out.append({"role": role, "parts": parts}); continue
        out.append({"role": role, "parts": [{"text": str(content)}]})
    return out