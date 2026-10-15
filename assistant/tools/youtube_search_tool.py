"""Read-only YouTube discovery. Opening a chosen result is a separate action."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any

from assistant.tools.base import BaseTool, ToolMetadata
from assistant.tools.browser_tools import WebSearchTool

if TYPE_CHECKING:
    from assistant.agent.context import ExecutionContext


class YouTubeSearchTool(BaseTool):
    metadata = ToolMetadata(
        name="youtube_search",
        description=(
            "Search YouTube and return candidate URLs without opening any browser tabs. "
            "Use kind=channel for a creator's channel. Resolve the requested result from "
            "the evidence, then use open_url once; search results alone do not open a channel."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Creator, channel, or video search terms",
                },
                "kind": {"type": "string", "enum": ["all", "channel", "video"], "default": "all"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        effect="read",
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        kind = arguments.get("kind", "all")
        if not query or kind not in {"all", "channel", "video"}:
            return {
                "success": False,
                "error": "A query and valid result kind are required.",
                "browser_opened": False,
            }
        task = getattr(context, "task", None)
        cache = getattr(task, "_youtube_searches", None)
        if cache is None:
            cache = {}
            if task is not None:
                task._youtube_searches = cache
        key = (" ".join(query.casefold().split()), kind)
        if key in cache:
            return dict(cache[key])
        search_query = f"{query} {'official channel ' if kind == 'channel' else ''}site:youtube.com"
        result = WebSearchTool(timeout=5, preferred_timeout=2).execute(
            {"query": search_query, "max_results": 5}, context
        )
        matches = []
        for item in result.get("results", []):
            try:
                parsed = urllib.parse.urlsplit(item.get("url", ""))
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
                "youtube.com",
                "www.youtube.com",
                "m.youtube.com",
                "youtu.be",
            }:
                continue
            channel = parsed.path.startswith(("/@", "/channel/", "/c/", "/user/"))
            video = (
                parsed.path == "/watch"
                or parsed.path.startswith("/shorts/")
                or parsed.hostname == "youtu.be"
            )
            if (kind == "channel" and not channel) or (kind == "video" and not video):
                continue
            matches.append(item)
        response = {
            **result,
            "success": bool(matches),
            "query": query,
            "results": matches,
            "browser_opened": False,
            "next_step": (
                "Choose the matching URL from these results and call open_url once. "
                "If ambiguous, ask which result; do not guess a channel URL."
            ),
        }
        if not matches and result.get("success"):
            response["error"] = "No matching YouTube result was found. No browser tab was opened."
        cache[key] = response
        return dict(response)


def get_tools() -> list[BaseTool]:
    return [YouTubeSearchTool()]
