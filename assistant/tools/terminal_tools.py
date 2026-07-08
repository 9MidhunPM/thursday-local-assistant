from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.agent.safety import SafetyManager, TerminalSafetyRules
from assistant.config.loader import ToolConfig
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class RunTerminalCommandTool(BaseTool):
    config: ToolConfig
    safety: SafetyManager
    metadata: ToolMetadata = ToolMetadata(
        name="run_terminal_command",
        description="Execute a safe shell command and return stdout/stderr.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_sec": {"type": "integer"},
            },
            "required": ["command"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        command = arguments.get("command")
        if not isinstance(command, str):
            return {"success": False, "error": "Command is required."}

        decision = self.safety.evaluate_command(command)
        if not decision.allowed:
            return {"success": False, "error": decision.reason or "Command blocked."}

        if decision.requires_confirmation:
            if not context.confirm(f"Confirm execution of: {command}"):
                return {"success": False, "error": "Command execution canceled."}

        timeout = int(arguments.get("timeout_sec", self.config.terminal.timeout_sec))
        if self.config.terminal.allow_shell:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        else:
            parts = shlex.split(command)
            result = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        timestamp = context.now().isoformat()
        context.memory.record_command(command, timestamp)
        return {
            "success": result.returncode == 0,
            "output": result.stdout.strip(),
            "error": result.stderr.strip() or None,
            "return_code": result.returncode,
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
