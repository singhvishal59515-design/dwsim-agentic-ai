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
import re
import time
import urllib.request
import urllib.error
import warnings
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore", category=FutureWarning,
                        module=r"google\.(api_core|auth|oauth2|generativeai)")

# ─────────────────────────────────────────────────────────────────────────────
# Provider defaults & model lists
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MODELS: Dict[str, str] = {
    "groq":      "llama-3.3-70b-versatile",
    "gemini":    "gemini-2.0-flash",
    "ollama":    "llama3.2",
    "openai":    "gpt-4o",
    "anthropic": "claude-sonnet-4-5",
}

# Per-request LLM timeout. If the provider doesn't answer within this, we fail
# fast and let the agent loop report a clear error instead of hanging.
_LLM_REQUEST_TIMEOUT_S = 90.0

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
    # Gemini 2.5
    "gemini-2.5-flash":                    "gemini-2.5-flash-preview-04-17",
    "gemini-2.5-flash-preview-04-17":      "gemini-2.5-flash-preview-04-17",
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
    Supports: groq | gemini | ollama | openai | anthropic

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
                self._client = Groq(api_key=self.api_key)
            except ImportError:
                raise ImportError("Run: pip install groq")

        elif p == "gemini":
            if not self.api_key:
                raise ValueError(
                    "Gemini API key required.\n"
                    "Get a FREE key at: https://aistudio.google.com/app/apikey")
            self.model = GEMINI_ALIASES.get(self.model, self.model)
            try:
                import google.genai as genai          # type: ignore
                self._client     = genai.Client(api_key=self.api_key)
                self._gemini_sdk = "new"
            except ImportError:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        import google.generativeai as genai  # type: ignore
                        genai.configure(api_key=self.api_key)
                        self._client     = genai
                        self._gemini_sdk = "old"
                        print("   (legacy google.generativeai SDK — "
                              "upgrade: pip install google-genai)")
                except ImportError:
                    raise ImportError(
                        "Run: pip install google-genai")

        elif p == "ollama":
            # No pip package needed — uses urllib
            self._verify_ollama()

        elif p == "openai":
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("Run: pip install openai")

        elif p == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError("Run: pip install anthropic")

        else:
            raise ValueError(
                f"Unknown provider '{self.provider}'.\n"
                "Free options: groq | gemini | ollama\n"
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

    def _switch_provider(self) -> bool:
        """
        Cross-provider fallback chain: Groq -> Gemini -> OpenAI -> Anthropic.
        Reads API keys from environment. Returns True if successfully switched.
        """
        import os
        # Ordered fallback chain — Groq is first if it's the primary provider,
        # otherwise it's last (since it can produce XML tool calls with large schemas)
        # Prefer free providers first; skip Groq if its key was already used
        # and returned 403 (quota/revoked) — it won't magically recover mid-session.
        # Fallback order: prefer free providers first; never re-try the current one.
        # The chain is just priority order — we always skip self.provider.
        if self.provider == "groq":
            chain = ["gemini", "openai", "anthropic"]
        elif self.provider == "gemini":
            chain = ["openai", "anthropic", "groq"]
        elif self.provider == "openai":
            chain = ["gemini", "anthropic", "groq"]
        else:
            chain = ["gemini", "openai", "groq"]
        key_env = {
            "groq":      "GROQ_API_KEY",
            "gemini":    "GEMINI_API_KEY",
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

    def chat(self, messages: List[Dict], tools: List[Dict],
             system_prompt: str = "") -> Dict[str, Any]:
        """
        Send messages -> normalised response dict:
          {"content": str, "tool_calls": [...], "stop_reason": str, "_raw": ...}

        Fallback order on quota exhaustion:
          Groq (all models) -> Gemini -> OpenAI -> Anthropic
        """
        _FN = {
            "groq":      self._chat_groq,
            "gemini":    self._chat_gemini,
            "ollama":    self._chat_ollama,
            "openai":    self._chat_openai,
            "anthropic": self._chat_anthropic,
        }

        for attempt in range(1, 5):
            fn = _FN[self.provider]
            try:
                return fn(messages, tools, system_prompt)
            except Exception as exc:
                s = str(exc)
                is_404 = "404" in s or "NOT_FOUND" in s or "not found" in s.lower()
                is_decommissioned = "decommissioned" in s or "model_decommissioned" in s
                is_429 = _is_quota_error(exc)
                is_daily = _is_daily_quota(exc)
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
                    if self._switch_provider():
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
                    if self._switch_provider():
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
                        if self._switch_provider():
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
                            if self._switch_provider():
                                continue
                            return None

                # XML tool_use_failed on Groq that couldn't be parsed -> next provider
                if is_xml_fail:
                    if self._switch_provider():
                        continue

                # ── Catch-all: any unhandled error → try cross-provider ──
                # Covers SDK bugs like 'AsyncRequest' errors, connection
                # errors, unexpected API responses, etc.
                print(f"\n   [{self.provider.title()}] Unexpected error: {s[:120]}")
                if self._switch_provider():
                    print(f"   Cross-provider fallback -> {self.provider}/{self.model}")
                    continue
                # All providers exhausted — return None so agent can report gracefully
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
        return {"content":     content,
                "tool_calls":  tool_calls,
                "stop_reason": resp.choices[0].finish_reason,
                "_raw":        msg}

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
        tcs, txts = [], []
        for part in resp.candidates[0].content.parts:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                tcs.append({"id":   f"g_{fc.name}_{len(tcs)}",
                             "name": fc.name,
                             "arguments": dict(fc.args) if fc.args else {}})
            elif getattr(part, "text", None):
                txts.append(part.text)
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": "tool_use" if tcs else "stop", "_raw": resp}

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
        tcs, txts = [], []
        for part in resp.parts:
            fc = getattr(part, "function_call", None)
            if fc and getattr(fc, "name", None):
                tcs.append({"id":   f"g_{fc.name}_{len(tcs)}",
                             "name": fc.name,
                             "arguments": dict(fc.args) if fc.args else {}})
            elif getattr(part, "text", None):
                txts.append(part.text)
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": "tool_use" if tcs else "stop", "_raw": resp}

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
                "_raw":        msg}

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
        return {"content": msg.content or "", "tool_calls": tcs,
                "stop_reason": resp.choices[0].finish_reason, "_raw": msg}

    # ── ANTHROPIC (paid) ──────────────────────────────────────────────────────

    def _chat_anthropic(self, messages, tools, system_prompt) -> Dict[str, Any]:
        at = [{"name": t["name"], "description": t.get("description", ""),
               "input_schema": t.get("parameters",
                                     {"type": "object", "properties": {}})}
              for t in tools]
        resp = self._client.messages.create(
            model=self.model, max_tokens=4096,
            system=system_prompt or None,
            messages=messages, tools=at or None,
            temperature=self.temperature,  # Anthropic: 0.0–1.0
            timeout=_LLM_REQUEST_TIMEOUT_S,
        )
        tcs, txts = [], []
        for blk in resp.content:
            if blk.type == "text":
                txts.append(blk.text)
            elif blk.type == "tool_use":
                tcs.append({"id": blk.id, "name": blk.name,
                             "arguments": blk.input})
        return {"content": "".join(txts), "tool_calls": tcs,
                "stop_reason": resp.stop_reason, "_raw": resp}


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_groq_xml_content(content: str) -> Optional[Dict]:
    """
    Parse XML-style tool calls from Groq response content, e.g.:
      <function=load_flowsheet {"path": "..."}></function>
    Returns a normalised response dict or None if parsing fails.
    """
    tool_calls = []
    for m in re.finditer(
        r"<function=(\w+)\s*(.*?)(?:</function>|$)", content, re.DOTALL
    ):
        name = m.group(1).strip()
        raw_args = m.group(2).strip().rstrip(">").strip()
        try:
            args = json.loads(raw_args) if raw_args else {}
        except Exception:
            args = {}
        tool_calls.append({
            "id":        f"call_{name}_{len(tool_calls)}",
            "name":      name,
            "arguments": args,
        })
    if not tool_calls:
        return None
    # Strip XML from content
    clean = re.sub(r"<function=\w+.*?(?:</function>|$)", "", content, flags=re.DOTALL).strip()

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


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(k in s for k in ("429", "quota", "rate_limit", "rate limit",
                                 "resource_exhausted", "resource exhausted",
                                 "too many requests"))


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