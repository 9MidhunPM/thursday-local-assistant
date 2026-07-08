from __future__ import annotations

import platform
try:
    import psutil
except ImportError:
    psutil = None
import time
from typing import Any

from assistant.tools.base import BaseTool, ToolMetadata
from .system_tools import SystemInfoTool


class SystemMonitorTool(BaseTool):
    """Tool to get detailed system monitoring information."""
    metadata = ToolMetadata(
        name="system_monitor",
        description="Get detailed system information including CPU, memory, disk, network, and battery status.",
        parameters={
            "type": "object",
            "properties": {
                "detail_level": {
                    "type": "string",
                    "description": "Level of detail: 'basic' for essential info, 'full' for detailed stats",
                    "enum": ["basic", "full"],
                    "default": "basic"
                }
            },
            "required": ["detail_level"],
        },
    )

    def __init__(self):
        self.system_tool = SystemInfoTool()

    def execute(self, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        detail_level = arguments.get("detail_level", "basic")
        
        try:
            # Get basic system info
            system_result = self.system_tool.execute({}, None)
            if not system_result.get("success"):
                return {"success": False, "error": "Failed to get system info"}
            
            info = system_result.get("info", {})
            
            result = {
                "success": True,
                "system": {
                    "platform": info.get("platform", "Unknown"),
                    "processor": info.get("processor", "Unknown"),
                    "architecture": info.get("architecture", "Unknown"),
                    "hostname": info.get("hostname", "Unknown")
                }
            }
            
            # Always include basic memory and CPU info
            if psutil is None:
                return {"success": False, "error": "psutil library is required for system_monitor tool"}
            memory = psutil.virtual_memory()
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            result["performance"] = {
                "cpu_usage_percent": cpu_percent,
                "memory": {
                    "total_gb": round(memory.total / (1024**3), 2),
                    "available_gb": round(memory.available / (1024**3), 2),
                    "used_gb": round(memory.used / (1024**3), 2),
                    "usage_percent": memory.percent
                }
            }
            
            if detail_level == "full":
                # Add more detailed information
                # Disk usage
                disk_usage = psutil.disk_usage('/')
                result["storage"] = {
                    "total_gb": round(disk_usage.total / (1024**3), 2),
                    "used_gb": round(disk_usage.used / (1024**3), 2),
                    "free_gb": round(disk_usage.free / (1024**3), 2),
                    "usage_percent": round((disk_usage.used / disk_usage.total) * 100, 1)
                }
                
                # Network info
                net_io = psutil.net_io_counters()
                result["network"] = {
                    "bytes_sent": net_io.bytes_sent,
                    "bytes_recv": net_io.bytes_recv,
                    "packets_sent": net_io.packets_sent,
                    "packets_recv": net_io.packets_recv
                }
                
                # Boot time
                boot_time = psutil.boot_time()
                result["boot_time"] = {
                    "timestamp": boot_time,
                    "formatted": f"{time.ctime(boot_time)}"
                }
                
                # Battery info (if available)
                try:
                    battery = psutil.sensors_battery()
                    if battery:
                        result["battery"] = {
                            "percent": battery.percent,
                            "power_plugged": battery.power_plugged,
                            "time_left_sec": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None
                        }
                    else:
                        result["battery"] = {"status": "No battery detected (desktop system)"}
                except AttributeError:
                    result["battery"] = {"status": "Battery information not available"}
                
                # Top processes by CPU usage
                processes = []
                for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                    try:
                        proc_info = proc.info
                        if proc_info['cpu_percent'] > 0.1:  # Only include processes using noticeable CPU
                            processes.append({
                                "pid": proc_info['pid'],
                                "name": proc_info['name'],
                                "cpu_percent": round(proc_info['cpu_percent'], 1),
                                "memory_percent": round(proc_info['memory_percent'], 1)
                            })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                
                # Sort by CPU usage and take top 5
                processes.sort(key=lambda x: x['cpu_percent'], reverse=True)
                result["top_processes"] = processes[:5]
            
            return result
            
        except Exception as e:
            return {"success": False, "error": f"Error getting system monitor info: {str(e)}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [SystemMonitorTool()]