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
        name="clipboard_tool",
        description="Read from or write to the system clipboard.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "Action to perform: read from clipboard or write to clipboard."
                },
                "text": {
                    "type": "string",
                    "description": "The text to write to the clipboard. Required if action is 'write'."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        text = arguments.get("text", "")
        
        if action == "write":
            if shutil.which("wl-copy"):
                proc = subprocess.run(["wl-copy"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Text copied to clipboard (Wayland)."}
            
            if shutil.which("xclip"):
                proc = subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Text copied to clipboard (X11)."}
                    
            if shutil.which("pbcopy"): # MacOS fallback just in case
                proc = subprocess.run(["pbcopy"], input=text, text=True, capture_output=True)
                if proc.returncode == 0:
                    return {"success": True, "output": "Text copied to clipboard (MacOS)."}
                    
            return {"success": False, "error": "No supported clipboard utility (wl-copy, xclip, pbcopy) found."}
            
        elif action == "read":
            if shutil.which("wl-paste"):
                proc = subprocess.run(["wl-paste"], capture_output=True, text=True)
                if proc.returncode == 0:
                    return {"success": True, "output": proc.stdout}
            
            if shutil.which("xclip"):
                proc = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True)
                if proc.returncode == 0:
                    return {"success": True, "output": proc.stdout}
                    
            if shutil.which("pbpaste"):
                proc = subprocess.run(["pbpaste"], capture_output=True, text=True)
                if proc.returncode == 0:
                    return {"success": True, "output": proc.stdout}
                    
            return {"success": False, "error": "No supported clipboard utility (wl-paste, xclip, pbpaste) found."}
            
        return {"success": False, "error": f"Unknown action: {action}"}


def get_tools() -> list[BaseTool]:
    return [ClipboardTool()]
