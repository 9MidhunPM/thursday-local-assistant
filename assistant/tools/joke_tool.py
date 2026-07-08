"""Joke tool – fetches a random short joke from a public API.

Provides `joke` function returning the 'setup' and 'punchline'.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class JokeTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="joke",
        description="Get a random short joke.",
        parameters={"type": "object", "properties": {}, "required": []},
    )

    _API_URL = "https://official-joke-api.appspot.com/random_joke"

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(self._API_URL)
                resp.raise_for_status()
                data = resp.json()
                setup = data.get("setup")
                punchline = data.get("punchline")
                if setup and punchline:
                    return {"success": True, "setup": setup, "punchline": punchline}
                else:
                    return {"success": False, "error": "Incomplete joke data"}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch joke: {e}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [JokeTool()]
