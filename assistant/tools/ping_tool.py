"""Ping tool – checks reachability of a host.

Provides `ping_host` function that runs the system `ping` command and returns output.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class PingTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="ping_host",
        description="Ping a host to check connectivity and latency.",
        parameters={
            "type": "object",
            "properties": {
                "host": {
                    "type": "string",
                    "description": "Hostname or IP address to ping."
                },
                "count": {
                    "type": "integer",
                    "description": "Number of echo requests to send (default 4).",
                    "default": 4,
                },
            },
            "required": ["host"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        host = arguments.get("host", "").strip()
        count = int(arguments.get("count", 4))
        if not host:
            return {"success": False, "error": "Host is required"}
        if shutil.which("ping") is None:
            return {"success": False, "error": "System 'ping' command not available"}
        try:
            result = subprocess.run(["ping", "-c", str(count), host], capture_output=True, text=True)
            if result.returncode == 0:
                return {"success": True, "output": result.stdout.strip()}
            else:
                return {"success": False, "error": result.stderr.strip() or "Ping failed"}
        except Exception as e:
            return {"success": False, "error": f"Ping execution error: {e}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [PingTool()]
