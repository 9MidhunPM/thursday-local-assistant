from __future__ import annotations

import argparse
import json
import os
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
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="With --web: don't open a browser window automatically.",
    )
    args = parser.parse_args()

    runtime = build_runtime(args.config)
    agent = runtime.agent
    config = runtime.config
    loggers = runtime.loggers
    llm = runtime.llm

    # Single-instance guard with self-healing: a second process normally
    # exits, but a dead or unresponsive lock holder (ghost with no port)
    # gets replaced instead of blocking launches forever.
    import fcntl
    import signal
    import urllib.request

    lock_path = "/tmp/thursday-server.lock"

    def _holder_pid() -> int | None:
        # Prefer the PID recorded in the lock file (written by current code).
        try:
            with open(lock_path) as f:
                pid = int(f.read().strip() or "0")
                if pid:
                    return pid
        except (OSError, ValueError):
            pass
        # Fallback: find the flock holder via /proc/locks (inode match), for
        # ghosts started before the PID-recording code existed.
        try:
            ino = str(os.stat(lock_path).st_ino)
            with open("/proc/locks") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) > 5 and parts[5].endswith(f":{ino}"):
                        return int(parts[4])
        except (OSError, ValueError):
            pass
        return None

    def _holder_unresponsive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except OSError:
            return True  # dead
        host = os.getenv("THURSDAY_HOST", "127.0.0.1")
        port = os.getenv("THURSDAY_PORT", "5005")
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=2) as r:
                return r.status != 200
        except Exception:
            return True

    lock_fd = open(lock_path, "a+")  # noqa: SIM115
    acquired = False
    for attempt in range(6):
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except OSError:
            pid = _holder_pid()
            if pid and pid != os.getpid() and _holder_unresponsive(pid):
                print(f"Replacing unresponsive Thursday server (pid {pid})...")
                try:
                    os.kill(pid, signal.SIGTERM if attempt < 3 else signal.SIGKILL)
                except OSError:
                    pass
                time.sleep(1)
                continue
            print("Another Thursday server instance is already running — exiting.")
            raise SystemExit(0) from None
    if not acquired:
        print("Could not acquire the Thursday server lock — exiting.")
        raise SystemExit(1)
    lock_fd.seek(0)
    lock_fd.truncate()
    lock_fd.write(str(os.getpid()))
    lock_fd.flush()

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
        if args.no_browser:
            print("Browser auto-open disabled (--no-browser).")
        else:
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

