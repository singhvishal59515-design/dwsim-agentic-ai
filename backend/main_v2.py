"""
main_v2.py  —  DWSIM Agentic AI v2 CLI entry point
────────────────────────────────────────────────────
Uses DWSIMBridgeV2 (5 accuracy improvements) + tools_schema_v2 (16 tools).

Usage
─────
python main_v2.py --provider groq --api-key gsk_XXX
python main_v2.py --provider groq --api-key gsk_XXX --flowsheet "C:\\path\\HE.dwxmz"
python main_v2.py --provider groq --api-key gsk_XXX --query "List all streams"

Environment variables
─────────────────────
  GROQ_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY
  LLM_PROVIDER, DWSIM_DLL_FOLDER
"""

from dotenv import load_dotenv
load_dotenv()

import os
import sys
import argparse
import textwrap

sys.path.insert(0, os.path.dirname(__file__))

from dwsim_bridge_v2 import DWSIMBridgeV2
from llm_client      import LLMClient, DEFAULT_MODELS
from agent_v2        import DWSIMAgentV2
from session         import save_session, load_session, list_sessions

ENV_KEY_MAP = {
    "groq":      "GROQ_API_KEY",
    "gemini":    "GEMINI_API_KEY",
    "ollama":    "",
    "openai":    "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dwsim_agent_v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            DWSIM Agentic AI v2 — Natural Language Process Simulation
            ──────────────────────────────────────────────────────────
            5 accuracy improvements over v1:
              ACC-1  Composition setting (mole fractions on feed streams)
              ACC-2  Convergence check (per-stream after solve)
              ACC-3  Property package detection (PR, SRK, NRTL, ...)
              ACC-4  Feed validation (warns if T/P/flow missing)
              ACC-5  Auto-optimisation (SciPy bounded minimise/maximise)

            FREE providers:
              groq    Best free.  https://console.groq.com
              gemini  Google free. https://aistudio.google.com/app/apikey
              ollama  Local/free.  https://ollama.com/download
        """),
    )
    p.add_argument("--provider", "-p",
        choices=["groq","gemini","ollama","openai","anthropic"],
        default=os.getenv("LLM_PROVIDER", "groq"))
    p.add_argument("--api-key",   "-k", default=None)
    p.add_argument("--model",     "-m", default=None)
    p.add_argument("--flowsheet", "-f", default=None,
        help="Pre-load a .dwxmz flowsheet")
    p.add_argument("--alias",           default=None)
    p.add_argument("--query",     "-q", default=None,
        help="Run a single query non-interactively and exit")
    p.add_argument("--no-stream",       action="store_true",
        help="Disable word-by-word streaming output")
    p.add_argument("--save-session",    default=None, metavar="NAME")
    p.add_argument("--load-session",    default=None, metavar="PATH")
    p.add_argument("--list-sessions",   action="store_true")
    p.add_argument("--dwsim-path",      default=os.getenv("DWSIM_DLL_FOLDER"))
    p.add_argument("--ollama-host",     default="http://localhost:11434")
    p.add_argument("--max-iter",        type=int, default=20)
    p.add_argument("--quiet",    "-Q",  action="store_true")
    p.add_argument("--list-models",     action="store_true")
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    if args.list_models:
        print("\nDefault model per provider:")
        for prov, mdl in DEFAULT_MODELS.items():
            free = "FREE" if prov in ("groq","gemini","ollama") else "paid"
            print(f"  {prov:<12} {mdl:<38} [{free}]")
        print()
        sys.exit(0)

    if args.list_sessions:
        sessions = list_sessions()
        if not sessions:
            print("No saved sessions.")
        else:
            print(f"Saved sessions ({len(sessions)}):")
            for s in sessions:
                print(f"  {s}")
        sys.exit(0)

    provider = args.provider.lower()
    api_key  = args.api_key or os.getenv(ENV_KEY_MAP.get(provider, ""), "")

    if not api_key and provider not in ("ollama",):
        env_var = ENV_KEY_MAP.get(provider, "API_KEY")
        print(f"\n[Error] No API key for '{provider}'.")
        if provider == "groq":
            print("  Free key: https://console.groq.com")
        elif provider == "gemini":
            print("  Free key: https://aistudio.google.com/app/apikey")
        print(f"  Set env:  set {env_var}=YOUR_KEY")
        print(f"  Or pass:  --api-key YOUR_KEY\n")
        sys.exit(1)

    model = args.model or DEFAULT_MODELS.get(provider, "")
    free_label = " [FREE]" if provider in ("groq","gemini","ollama") else " [paid]"
    print(f"[Init] Connecting {provider.upper()} / {model}{free_label} …",
          end=" ", flush=True)
    try:
        llm = LLMClient(provider=provider, api_key=api_key, model=model,
                        ollama_host=args.ollama_host)
        print("OK")
    except Exception as exc:
        print(f"\n[Error] {exc}")
        sys.exit(1)

    print("[Init] DWSIM bridge v2 …", end=" ", flush=True)
    bridge = DWSIMBridgeV2(dll_folder=args.dwsim_path)
    init   = bridge.initialize()
    print(f"OK — {init['message']}" if init["success"]
          else f"\n[Warning] {init.get('error')}")

    agent = DWSIMAgentV2(
        llm=llm, bridge=bridge,
        max_iterations=args.max_iter,
        verbose=not args.quiet,
        stream_output=not args.no_stream,
    )

    if args.load_session:
        try:
            sess = load_session(args.load_session)
            agent._history = sess["history"]
            print(f"[Session] Loaded {len(agent._history)} messages from {args.load_session}")
            if sess.get("flowsheet_path") and not args.flowsheet:
                args.flowsheet = sess["flowsheet_path"]
                print(f"[Session] Restoring flowsheet: {args.flowsheet}")
        except Exception as exc:
            print(f"[Session] Could not load: {exc}")

    if args.flowsheet:
        print(f"[Init] Loading: {args.flowsheet} …", end=" ", flush=True)
        r = bridge.load_flowsheet(args.flowsheet, alias=args.alias)
        if r["success"]:
            print(f"OK ({r['object_count']} objects)")
            print(f"  Streams:  {', '.join(r.get('streams', []))}")
            print(f"  Unit ops: {', '.join(r.get('unit_ops', [])) or 'none'}")
            if r.get("property_package"):
                print(f"  Thermo:   {r['property_package']}")
            if r.get("feed_warnings"):
                print("  [Feed warnings]")
                for w in r["feed_warnings"]:
                    print(f"    ⚠ {w}")
        else:
            print(f"\n[Warning] {r['error']}")

    if args.query:
        print(f"\nQuery: {args.query}\n{'─'*60}")
        reply = agent.chat(args.query)
        if args.no_stream or args.quiet:
            print(f"\nAnswer:\n{reply}\n")
        if args.save_session:
            path = save_session(agent._history, provider, model,
                                bridge.state.path, bridge.state.name,
                                args.save_session)
            print(f"[Session] Saved: {path}")
        sys.exit(0)

    try:
        agent.run_cli()
    finally:
        if args.save_session:
            path = save_session(agent._history, provider, model,
                                bridge.state.path, bridge.state.name,
                                args.save_session)
            print(f"\n[Session] Saved: {path}")


if __name__ == "__main__":
    main()
