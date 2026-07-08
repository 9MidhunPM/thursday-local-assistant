from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class WindowManagementTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="window_management",
        description="List open windows or focus a specific window by name (Requires X11/wmctrl).",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "focus"]
                },
                "window_name": {
                    "type": "string",
                    "description": "Name or partial name of the window to focus. Required for 'focus'."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        window_name = arguments.get("window_name")
        
        has_wmctrl = shutil.which("wmctrl") is not None
        has_hyprctl = shutil.which("hyprctl") is not None
        
        if not has_wmctrl and not has_hyprctl:
            return {"success": False, "error": "Neither wmctrl (X11) nor hyprctl (Wayland/Hyprland) are installed."}

        if action == "list":
            if has_hyprctl:
                res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True)
                if res.returncode == 0:
                    import json
                    try:
                        clients = json.loads(res.stdout)
                        windows = [f"{c.get('class', '')} - {c.get('title', '')}" for c in clients if c.get('mapped') and (c.get('title') or c.get('class'))]
                        return {"success": True, "output": "Open windows:\n" + "\n".join(windows)}
                    except json.JSONDecodeError:
                        pass
                        
            if has_wmctrl:
                res = subprocess.run(["wmctrl", "-l"], capture_output=True, text=True)
                if res.returncode == 0:
                    windows = []
                    for line in res.stdout.splitlines():
                        parts = line.split(maxsplit=3)
                        if len(parts) >= 4:
                            windows.append(parts[3])
                    return {"success": True, "output": "Open windows:\n" + "\n".join(windows)}
            return {"success": False, "error": "Failed to list windows."}
            
        elif action == "focus":
            if not window_name:
                return {"success": False, "error": "window_name is required for focus action."}
                
            if has_hyprctl:
                res = subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True)
                if res.returncode == 0:
                    import json
                    try:
                        clients = json.loads(res.stdout)
                        target_addr = None
                        target_name_lower = window_name.lower()
                        for c in clients:
                            if not c.get('mapped'):
                                continue
                            c_class = c.get('class', '').lower()
                            c_title = c.get('title', '').lower()
                            if target_name_lower in c_class or target_name_lower in c_title:
                                target_addr = c.get('address')
                                break
                        if target_addr:
                            f_res = subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{target_addr}"], capture_output=True, text=True)
                            if f_res.returncode == 0:
                                return {"success": True, "output": f"Focused window matching '{window_name}' (Hyprland)."}
                    except json.JSONDecodeError:
                        pass
                        
            if has_wmctrl:
                res = subprocess.run(["wmctrl", "-a", window_name], capture_output=True, text=True)
                if res.returncode == 0:
                    return {"success": True, "output": f"Focused window matching '{window_name}' (X11)."}
            return {"success": False, "error": f"Failed to focus window '{window_name}'. It might not exist."}
            
        return {"success": False, "error": f"Unknown action: {action}"}


def get_tools() -> list[BaseTool]:
    return [WindowManagementTool()]
