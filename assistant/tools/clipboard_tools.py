from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class ClipboardTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="clipboard",
        description="Read, write, or get history of the system clipboard.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "history"],
                    "description": "Action: read clipboard, write text, or get recent history."
                },
                "text": {
                    "type": "string",
                    "description": "Text to write. Required for 'write' action."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max history items to return (default: 5). For 'history' action."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        text = arguments.get("text", "")
        limit = int(arguments.get("limit", 5))

        if action == "write":
            if not text:
                return {"success": False, "error": "Text is required for write action."}
            if shutil.which("wl-copy"):
                proc = subprocess.run(["wl-copy"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Copied to clipboard."}
            if shutil.which("xclip"):
                proc = subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Copied to clipboard."}
            if shutil.which("pbcopy"):
                proc = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Copied to clipboard."}
            return {"success": False, "error": "No clipboard utility found (wl-copy, xclip, pbcopy)."}

        elif action == "read":
            content = self._read_clipboard()
            if content is None:
                return {"success": False, "error": "No clipboard utility found or clipboard is empty."}
            return {"success": True, "output": content}

        elif action == "history":
            content = self._read_clipboard()
            if content is None:
                return {"success": True, "output": {"history": [], "count": 0}}
            return {"success": True, "output": {"history": [content], "count": 1, "note": "Full history not yet implemented."}}

        return {"success": False, "error": f"Unknown action: {action}"}

    @staticmethod
    def _read_clipboard() -> str | None:
        for cmd in (["wl-paste"], ["xclip", "-selection", "clipboard", "-o"], ["pbpaste"]):
            if shutil.which(cmd[0]):
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout
        return None


def get_tools() -> list[BaseTool]:
    return [ClipboardTool()]
