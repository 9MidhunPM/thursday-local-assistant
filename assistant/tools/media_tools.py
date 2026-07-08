from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class YouTubePlayTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="youtube_play",
        description="Play YouTube audio in the background or stop it. ONLY use this if the user EXPLICITLY requests YouTube. Default to Spotify for all other music requests.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["play", "stop"],
                    "description": "Action to perform. Use 'stop' to pause/kill the YouTube player."
                },
                "query": {
                    "type": "string",
                    "description": "The song or video to search for and play. Required for 'play'."
                }
            },
            "required": ["action"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        action = arguments.get("action", "play")
        query = arguments.get("query")
        
        if not shutil.which("mpv"):
            return {"success": False, "error": "'mpv' must be installed."}
            
        if action == "stop":
            res = subprocess.run(["pkill", "-f", "mpv --no-video"], capture_output=True)
            if res.returncode == 0:
                return {"success": True, "output": "Stopped YouTube background player."}
            return {"success": False, "error": "No YouTube player is currently running."}

        if not query:
            return {"success": False, "error": "Query is required for play action."}
            
        if not shutil.which("yt-dlp"):
            return {"success": False, "error": "'yt-dlp' must be installed for playing."}
            
        try:
            # ytsearch: queries youtube and plays the first result
            search_query = f"ytdl://ytsearch:{query}"
            subprocess.Popen(
                ["mpv", "--no-video", search_query],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return {"success": True, "output": f"Started playing '{query}' in the background via YouTube."}
        except Exception as e:
            return {"success": False, "error": f"Failed to start mpv: {e}"}


def get_tools() -> list[BaseTool]:
    return [YouTubePlayTool()]
