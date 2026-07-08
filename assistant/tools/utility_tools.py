from __future__ import annotations

import json
import urllib.parse
import urllib.request
import subprocess
import shlex
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class WeatherTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="weather_check",
        description="Check the current weather for a given location.",
        parameters={
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or location name. Use an empty string to guess location by IP."
                }
            },
            "required": ["location"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        location = arguments.get("location", "")
        url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                current = data.get('current_condition', [{}])[0]
                temp = current.get('temp_C', 'Unknown')
                feels_like = current.get('FeelsLikeC', 'Unknown')
                desc = current.get('weatherDesc', [{}])[0].get('value', 'Unknown')
                humidity = current.get('humidity', 'Unknown')
                
                location_name = location if location else "your location"
                
                report = (
                    f"Weather for {location_name}:\n"
                    f"Condition: {desc}\n"
                    f"Temperature: {temp}°C (Feels like {feels_like}°C)\n"
                    f"Humidity: {humidity}%"
                )
                return {"success": True, "output": report}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch weather: {e}"}


@dataclass
class TimerTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="set_timer",
        description="Set a background timer that will send a desktop notification when finished.",
        parameters={
            "type": "object",
            "properties": {
                "minutes": {
                    "type": "number",
                    "description": "Number of minutes for the timer."
                },
                "message": {
                    "type": "string",
                    "description": "Message to display when the timer finishes."
                }
            },
            "required": ["minutes", "message"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        minutes = arguments.get("minutes")
        message = arguments.get("message", "Timer is up!")
        
        if minutes is None or minutes <= 0:
            return {"success": False, "error": "A positive number of minutes is required."}
            
        seconds = int(float(minutes) * 60)
        safe_msg = shlex.quote(message)
        
        cmd = f"sleep {seconds} && notify-send 'Thursday Timer' {safe_msg}"
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        return {"success": True, "output": f"Timer set for {minutes} minutes. You will receive a desktop notification."}


@dataclass
class NetworkSpeedTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="network_speed_test",
        description="Run a network speed test. This takes about 15-30 seconds to complete.",
        parameters={
            "type": "object",
            "properties": {}
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        import shutil
        if not shutil.which("speedtest-cli") and not shutil.which("speedtest"):
            return {"success": False, "error": "Neither 'speedtest-cli' nor 'speedtest' is installed on the system."}
            
        cmd = "speedtest-cli" if shutil.which("speedtest-cli") else "speedtest"
        res = subprocess.run([cmd, "--simple"], capture_output=True, text=True)
        if res.returncode == 0:
            return {"success": True, "output": f"Speed Test Results:\n{res.stdout.strip()}"}
        return {"success": False, "error": f"Failed to run speed test: {res.stderr.strip()}"}


def get_tools() -> list[BaseTool]:
    return [WeatherTool(), TimerTool(), NetworkSpeedTool()]
