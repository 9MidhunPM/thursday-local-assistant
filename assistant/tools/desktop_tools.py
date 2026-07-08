from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class VolumeControlTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="volume_control",
        description="Control system audio volume (up, down, mute, or a specific percentage).",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["up", "down", "mute", "unmute", "toggle", "set", "status"]
                },
                "percent": {
                    "type": "integer",
                    "description": "Percentage to set the volume to (0-100). Required if action is 'set'."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        percent = arguments.get("percent")
        
        has_pactl = shutil.which("pactl") is not None
        has_amixer = shutil.which("amixer") is not None
        
        if not has_pactl and not has_amixer:
            return {"success": False, "error": "Neither pactl nor amixer is available."}

        def run_vol(cmd_args: list[str]) -> bool:
            return subprocess.run(cmd_args, capture_output=True).returncode == 0

        if action == "status":
            if has_pactl:
                res = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"], capture_output=True, text=True)
                mute_res = subprocess.run(["pactl", "get-sink-mute", "@DEFAULT_SINK@"], capture_output=True, text=True)
                return {"success": True, "output": f"Volume status:\n{res.stdout.strip()}\nMute status:\n{mute_res.stdout.strip()}"}
            else:
                res = subprocess.run(["amixer", "sget", "Master"], capture_output=True, text=True)
                return {"success": True, "output": res.stdout.strip()}

        if action == "up":
            if has_pactl:
                success = run_vol(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"])
            else:
                success = run_vol(["amixer", "sset", "Master", "10%+"])
        elif action == "down":
            if has_pactl:
                success = run_vol(["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"])
            else:
                success = run_vol(["amixer", "sset", "Master", "10%-"])
        elif action in ("mute", "unmute", "toggle"):
            if has_pactl:
                arg = "toggle" if action == "toggle" else ("1" if action == "mute" else "0")
                success = run_vol(["pactl", "set-sink-mute", "@DEFAULT_SINK@", arg])
            else:
                arg = "toggle" if action == "toggle" else action
                success = run_vol(["amixer", "sset", "Master", arg])
        elif action == "set" and percent is not None:
            if has_pactl:
                success = run_vol(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"])
            else:
                success = run_vol(["amixer", "sset", "Master", f"{percent}%"])
        else:
            return {"success": False, "error": f"Invalid action or missing percent for action: {action}"}
            
        if success:
            return {"success": True, "output": f"Volume {action} executed."}
        return {"success": False, "error": f"Failed to execute volume action {action}."}


@dataclass
class BrightnessControlTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="brightness_control",
        description="Control screen brightness (up, down, set).",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["up", "down", "set", "status"]
                },
                "percent": {
                    "type": "integer",
                    "description": "Percentage (0-100) to set brightness to. Required for 'set'."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action")
        percent = arguments.get("percent")
        
        if not shutil.which("brightnessctl"):
            return {"success": False, "error": "brightnessctl is not installed."}

        if action == "status":
            res = subprocess.run(["brightnessctl", "i"], capture_output=True, text=True)
            return {"success": True, "output": res.stdout.strip()}

        if action == "up":
            cmd = ["brightnessctl", "set", "10%+"]
        elif action == "down":
            cmd = ["brightnessctl", "set", "10%-"]
        elif action == "set" and percent is not None:
            cmd = ["brightnessctl", "set", f"{percent}%"]
        else:
            return {"success": False, "error": f"Invalid action or missing percent."}

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            return {"success": True, "output": f"Brightness {action} executed. {res.stdout.strip()}"}
        return {"success": False, "error": res.stderr.strip() or "Failed to set brightness."}


@dataclass
class NotificationTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="send_notification",
        description="Send a desktop notification to the user.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "message": {"type": "string"}
            },
            "required": ["title", "message"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        title = arguments.get("title", "Notification")
        message = arguments.get("message", "")
        
        if not shutil.which("notify-send"):
            return {"success": False, "error": "notify-send is not installed."}

        res = subprocess.run(["notify-send", title, message], capture_output=True)
        if res.returncode == 0:
            return {"success": True, "output": "Notification sent successfully."}
        return {"success": False, "error": "Failed to send notification."}


def get_tools() -> list[BaseTool]:
    return [VolumeControlTool(), BrightnessControlTool(), NotificationTool()]
