"""YouTube search tool that opens the search results page in the default web browser.

Provides an OpenAI tool function "youtube_search" that takes a query string, constructs the YouTube search URL, opens it, and returns the URL.
"""

from __future__ import annotations

import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class YouTubeSearchTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="youtube_search",
        description="Search YouTube for a query and open the search results page in the default web browser.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms for YouTube."
                }
            },
            "required": ["query"]
        }
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = arguments.get("query", "").strip()
        if not query:
            return {"success": False, "error": "Query is required"}
        # Encode query for URL (spaces become +, special characters percent‑encoded)
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.youtube.com/results?search_query={encoded}"
        try:
            webbrowser.open(url)
            return {"success": True, "url": url}
        except Exception as e:
            return {"success": False, "error": str(e)}


def get_tools() -> list[BaseTool]:
    return [YouTubeSearchTool()]
