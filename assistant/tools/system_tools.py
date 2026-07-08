from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


def _read_battery_info() -> dict[str, Any]:
    base = Path("/sys/class/power_supply")
    if not base.exists():
        return {"available": False, "detail": "Battery data not available."}
    batteries = list(base.glob("BAT*"))
    if not batteries:
        return {"available": False, "detail": "Battery not detected."}
    bat = batteries[0]
    capacity = (bat / "capacity").read_text(encoding="utf-8", errors="ignore").strip()
    status = (bat / "status").read_text(encoding="utf-8", errors="ignore").strip()
    return {"available": True, "capacity": capacity, "status": status}


def _read_meminfo() -> dict[str, Any]:
    meminfo = {}
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        for line in handle:
            key, value = line.split(":", 1)
            meminfo[key.strip()] = value.strip()
    return meminfo


def _read_cpu_usage() -> dict[str, float]:
    def snapshot() -> tuple[int, int]:
        with open("/proc/stat", "r", encoding="utf-8") as handle:
            parts = handle.readline().split()
        values = [int(x) for x in parts[1:]]
        idle = values[3]
        total = sum(values)
        return idle, total

    idle1, total1 = snapshot()
    time.sleep(0.1)
    idle2, total2 = snapshot()
    idle_delta = idle2 - idle1
    total_delta = total2 - total1
    if total_delta == 0:
        return {"usage_percent": 0.0}
    usage = 100.0 * (1 - idle_delta / total_delta)
    return {"usage_percent": round(usage, 2)}


@dataclass
class CurrentTimeTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="current_time",
        description="Get the current local time.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        return {"success": True, "output": datetime.now().strftime("%H:%M:%S")}


@dataclass
class CurrentDateTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="current_date",
        description="Get the current local date.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        return {"success": True, "output": datetime.now().strftime("%Y-%m-%d")}


@dataclass
class SystemInfoTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="system_info",
        description="Get basic system information.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        info = platform.uname()
        return {
            "success": True,
            "output": {
                "system": info.system,
                "node": info.node,
                "release": info.release,
                "version": info.version,
                "machine": info.machine,
                "processor": info.processor,
            },
        }


@dataclass
class BatteryInfoTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="battery_info",
        description="Get battery status and capacity.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        return {"success": True, "output": _read_battery_info()}


@dataclass
class MemoryUsageTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="memory_usage",
        description="Get memory usage from /proc/meminfo.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        return {"success": True, "output": _read_meminfo()}


@dataclass
class CpuUsageTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="cpu_usage",
        description="Get CPU usage percentage.",
        parameters={"type": "object", "properties": {}},
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        return {"success": True, "output": _read_cpu_usage()}


@dataclass
class ScreenshotTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="take_screenshot",
        description="Take a screenshot and save it to a file.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to save the screenshot as (e.g., screenshot.png). Will be saved in your home directory."
                }
            },
            "required": ["filename"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        filename = arguments.get("filename", "screenshot.png")
        save_path = Path.home() / filename
        
        import shutil
        import subprocess
        
        if shutil.which("gnome-screenshot"):
            proc = subprocess.run(["gnome-screenshot", "-f", str(save_path)], capture_output=True)
            if proc.returncode == 0:
                return {"success": True, "output": f"Screenshot saved to {save_path}"}
                
        if shutil.which("scrot"):
            proc = subprocess.run(["scrot", str(save_path)], capture_output=True)
            if proc.returncode == 0:
                return {"success": True, "output": f"Screenshot saved to {save_path}"}
                
        if shutil.which("grim"):
            proc = subprocess.run(["grim", str(save_path)], capture_output=True)
            if proc.returncode == 0:
                return {"success": True, "output": f"Screenshot saved to {save_path}"}
                
        return {"success": False, "error": "No supported screenshot utility found (gnome-screenshot, scrot, or grim)."}


@dataclass
class ProcessKillerTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="kill_process",
        description="Kill a running process by name or PID.",
        parameters={
            "type": "object",
            "properties": {
                "process_name": {
                    "type": "string",
                    "description": "Name of the process to kill (e.g. 'firefox')."
                },
                "pid": {
                    "type": "integer",
                    "description": "Process ID to kill."
                }
            }
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        process_name = arguments.get("process_name")
        pid = arguments.get("pid")
        
        import subprocess
        
        if pid:
            res = subprocess.run(["kill", "-9", str(pid)], capture_output=True, text=True)
            if res.returncode == 0:
                return {"success": True, "output": f"Successfully killed PID {pid}."}
            return {"success": False, "error": f"Failed to kill PID {pid}: {res.stderr.strip()}"}
            
        if process_name:
            res = subprocess.run(["pkill", "-9", "-f", process_name], capture_output=True, text=True)
            if res.returncode == 0:
                return {"success": True, "output": f"Successfully killed processes matching '{process_name}'."}
            return {"success": False, "error": f"Failed to kill '{process_name}'. Process may not exist."}
            
        return {"success": False, "error": "Must provide either process_name or pid."}


def get_tools() -> list[BaseTool]:
    return [
        CurrentTimeTool(),
        CurrentDateTool(),
        SystemInfoTool(),
        BatteryInfoTool(),
        MemoryUsageTool(),
        CpuUsageTool(),
        ScreenshotTool(),
        ProcessKillerTool(),
    ]
