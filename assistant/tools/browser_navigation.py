"""Explicit browser navigation, separate from read-only search."""

from __future__ import annotations

import urllib.parse
import webbrowser
from typing import TYPE_CHECKING, Any

from assistant.tools.base import BaseTool, ToolMetadata

if TYPE_CHECKING:
    from assistant.agent.context import ExecutionContext


def _navigation_url(value: Any) -> str | None:
    if not isinstance(value, str) or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None or "\\" in value:
            return None
        _ = parsed.port  # Validate malformed ports before dispatch.
        return urllib.parse.urlunsplit(
            (
                parsed.scheme,
                parsed.netloc.casefold(),
                parsed.path or "/",
                parsed.query,
                parsed.fragment,
            )
        )
    except ValueError:
        return None


class OpenUrlTool(BaseTool):
    metadata = ToolMetadata(
        name="open_url",
        description=(
            "Open an exact HTTP(S) URL in the default browser once per task. "
            "For a named site or YouTube channel, first resolve its URL with read-only "
            "web_search/youtube_search, then call this once. This dispatches navigation; "
            "it does not verify page loading. Never retry a dispatched or uncertain navigation."
        ),
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
            "additionalProperties": False,
        },
        effect="desktop",
        resource="desktop",
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        url = _navigation_url(arguments.get("url"))
        if not url:
            return {
                "success": False,
                "error": "Use an absolute HTTP(S) URL without credentials or control characters.",
                "delivery": "not_delivered",
            }
        task = getattr(context, "task", None)
        attempts = getattr(task, "_browser_navigations", None)
        if attempts is None:
            attempts = {}
            if task is not None:
                task._browser_navigations = attempts
        if url in attempts:
            return {
                **attempts[url],
                "already_dispatched": attempts[url]["delivery"] != "not_delivered",
                "reused_attempt": True,
            }
        try:
            dispatched = bool(webbrowser.open(url, new=2))
            result = {
                "success": dispatched,
                "url": url,
                "page_verified": False,
                "delivery": "dispatched" if dispatched else "not_delivered",
                "output": "Navigation sent to the browser; page loading is not verified."
                if dispatched
                else "",
            }
            if not dispatched:
                result["error"] = (
                    "The default browser did not accept navigation. "
                    "Check the browser configuration."
                )
        except Exception:
            result = {
                "success": False,
                "url": url,
                "page_verified": False,
                "delivery": "uncertain_delivery",
                "pause": True,
                "error": (
                    "Browser navigation could not be confirmed. "
                    "Inspect the browser before trying again."
                ),
            }
        attempts[url] = result
        return dict(result)


def get_tools() -> list[BaseTool]:
    return [OpenUrlTool()]
