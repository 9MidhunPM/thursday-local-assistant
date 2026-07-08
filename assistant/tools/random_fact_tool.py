"""Random fact tool – fetches an interesting fact from a public API.

Provides `random_fact` function returning a short fact string.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class RandomFactTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="random_fact",
        description="Retrieve a random fact from a public API.",
        parameters={"type": "object", "properties": {}, "required": []},
    )

    _API_URL = "https://uselessfacts.jsph.pl/random.json?language=en"

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as client:
                resp = client.get(self._API_URL)
                resp.raise_for_status()
                data = resp.json()
                fact = data.get("text")
                if fact:
                    return {"success": True, "fact": fact}
                else:
                    return {"success": False, "error": "No fact returned"}
        except Exception as e:
            return {"success": False, "error": f"Failed to fetch fact: {e}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [RandomFactTool()]
