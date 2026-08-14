from __future__ import annotations

import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.agent.safety import SafetyManager, TerminalSafetyRules
from assistant.config.loader import ToolConfig
from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.file_tools import _resolve_path


@dataclass
class RunTerminalCommandTool(BaseTool):
    config: ToolConfig
    safety: SafetyManager
    metadata: ToolMetadata = ToolMetadata(
        name="run_terminal_command",
        description=(
            "Execute a shell command on the user's computer and return its real stdout, stderr, "
            "exit code, and duration. Prefer this when the user asks to run or inspect something "
            "that has no dedicated tool. Risky or mutating commands require confirmation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {"type": "integer"},
                "cwd": {
                    "type": "string",
                    "description": "Optional existing working directory. Defaults to Thursday's process directory.",
                },
            },
            "required": ["command"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"success": False, "error": "Command is required."}

        decision = self.safety.evaluate_command(command)
        if not decision.allowed:
            return {"success": False, "error": decision.reason or "Command blocked."}

        if decision.requires_confirmation:
            if not context.confirm(f"Confirm execution of: {command}"):
                return {"success": False, "error": "Command execution canceled."}

        timeout = max(1, min(int(arguments.get("timeout_sec", self.config.terminal.timeout_sec)), 300))
        cwd_value = arguments.get("cwd")
        cwd: str | None = None
        if cwd_value is not None:
            if not isinstance(cwd_value, str) or not cwd_value.strip():
                return {"success": False, "error": "cwd must be a non-empty path."}
            resolved_cwd = _resolve_path(cwd_value, self.config.read_roots)
            if not resolved_cwd.is_dir():
                return {"success": False, "error": "Working directory does not exist."}
            cwd = str(resolved_cwd)

        started = time.monotonic()
        try:
            if self.config.terminal.allow_shell:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    cwd=cwd,
                )
            else:
                parts = shlex.split(command)
                result = subprocess.run(
                    parts,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    cwd=cwd,
                )
        except subprocess.TimeoutExpired as exc:
            duration_ms = round((time.monotonic() - started) * 1000)
            stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
            stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
            return {
                "success": False,
                "output": (stdout or "").strip(),
                "error": (stderr or "").strip() or f"Command timed out after {timeout} seconds.",
                "return_code": None,
                "command": command,
                "cwd": cwd,
                "duration_ms": duration_ms,
                "timed_out": True,
            }
        duration_ms = round((time.monotonic() - started) * 1000)
        timestamp = context.now().isoformat()
        context.memory.record_command(command, timestamp)
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
            "return_code": result.returncode,
            "command": command,
            "cwd": cwd,
            "duration_ms": duration_ms,
        }


def get_tools(config: ToolConfig) -> list[BaseTool]:
    rules = TerminalSafetyRules(
        allow_shell=config.terminal.allow_shell,
        whitelist_commands=config.terminal.whitelist_commands,
        blacklist_patterns=config.terminal.blacklist_patterns,
        confirm_patterns=config.terminal.confirm_patterns,
    )
    safety = SafetyManager(rules)
    return [RunTerminalCommandTool(config=config, safety=safety)]
