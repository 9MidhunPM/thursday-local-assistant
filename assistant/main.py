from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from assistant.runtime import build_runtime, default_config_path
from assistant.server import start_server, open_browser, broadcaster

# ANSI Styling Constants
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[32m"
COLOR_CYAN = "\033[36m"
COLOR_YELLOW = "\033[33m"
COLOR_RED = "\033[31m"
COLOR_GRAY = "\033[90m"
COLOR_BOLD = "\033[1m"


def print_banner() -> None:
    print(f"{COLOR_BOLD}{COLOR_CYAN}")
    print("=========================================")
    print("      ⚡ THURSDAY LOCAL AI AGENT ⚡      ")
    print("=========================================")
    print(f"{COLOR_RESET}")


def print_tool_call(tool_name: str, arguments: dict[str, Any]) -> None:
    args_str = ", ".join(f"{k}={v!r}" for k, v in arguments.items())
    print(f"\n{COLOR_CYAN}⚙️  [Tool Call] {tool_name}({args_str})...{COLOR_RESET}")


def print_tool_result(result: dict[str, Any]) -> None:
    tool_name = result.get("tool", "unknown")
    success = result.get("success", True)
    if success is False or "error" in result:
        err = result.get("error", "Unknown error")
        print(f"{COLOR_RED}📥 [Tool Error] {tool_name}: {err}{COLOR_RESET}\n")
    else:
        res_keys = [k for k in result.keys() if k not in ("tool", "success")]
        if not res_keys:
            print(f"{COLOR_GREEN}📥 [Tool Success] {tool_name}{COLOR_RESET}\n")
        else:
            summary = ", ".join(f"{k}: {str(result[k])[:80]}" for k in res_keys[:3])
            if len(res_keys) > 3:
                summary += " ..."
            print(f"{COLOR_GREEN}📥 [Tool Success] {tool_name} -> {summary}{COLOR_RESET}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local-first AI assistant.")
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to config.json or config.yaml",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run the assistant with a web-based interface instead of the CLI.",
    )
    args = parser.parse_args()

    runtime = build_runtime(args.config)
    agent = runtime.agent
    config = runtime.config
    loggers = runtime.loggers
    llm = runtime.llm

    # Start the HTTP/SSE server
    start_server(runtime)
    from assistant.server import running_port

    mode = "local" if llm.is_local else f"cloud ({llm.provider})"
    model_line = f"{COLOR_GRAY}LLM: {mode} · {llm.model} · user={config.agent.user_name}{COLOR_RESET}"

    if args.web:
        print_banner()
        print(model_line)
        print(f"{COLOR_BOLD}{COLOR_GREEN}✔ Web Server started!{COLOR_RESET}")
        print(f"👉 Local Web UI is available at: {COLOR_BOLD}http://127.0.0.1:{running_port}{COLOR_RESET}")
        print("Opening browser automatically...")
        open_browser()
        print("\nPress Ctrl+C to stop the server.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nShutting down web server.")
        raise SystemExit(0)

    # CLI mode
    print_banner()
    print(model_line)
    print(f"Status: Ready (Web UI running at http://127.0.0.1:{running_port})")
    print("Type 'exit' or 'quit' to close.\n")

    while True:
        try:
            user_text = input(f"{COLOR_BOLD}{COLOR_GREEN}➜  {COLOR_RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit"}:
            break
        try:
            # Broadcast user message to SSE clients
            broadcaster.broadcast("user_message", {"content": user_text})

            def show_tool_call(tool: str, arguments: dict[str, Any]) -> None:
                print_tool_call(tool, arguments)
                broadcaster.broadcast("tool_call", {"tool": tool, "arguments": arguments})

            def show_tool_result(result: dict[str, Any]) -> None:
                print_tool_result(result)
                broadcaster.broadcast("tool_result", result)

            print(f"\n{COLOR_BOLD}💬 Thursday:{COLOR_RESET} ", end="", flush=True)

            if config.agent.stream_responses:
                def on_stream(chunk: str) -> None:
                    print(chunk, end="", flush=True)
                    broadcaster.broadcast("token", {"chunk": chunk})

                response = agent.handle_message(
                    user_text,
                    on_stream=on_stream,
                    on_tool_result=show_tool_result,
                    on_tool_call=show_tool_call,
                )
                print()
            else:
                response = agent.handle_message(
                    user_text,
                    on_tool_result=show_tool_result,
                    on_tool_call=show_tool_call,
                )
                print(response)

            # Broadcast final response to SSE clients
            broadcaster.broadcast("final_response", {"content": response})
            print()
        except Exception as exc:  # noqa: BLE001 - surface errors
            loggers.error.error(str(exc))
            error_msg = f"Error: {exc}"
            print(f"\n{COLOR_RED}{error_msg}{COLOR_RESET}\n")
            broadcaster.broadcast("error", {"content": error_msg})


if __name__ == "__main__":
    main()

