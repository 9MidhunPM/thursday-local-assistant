"""Dictionary lookup tool – fetches definitions for a word.

Provides `define_word` function using the free dictionaryapi.dev endpoint.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any, List

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class DictionaryLookupTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="define_word",
        description="Get definitions for an English word.",
        parameters={
            "type": "object",
            "properties": {
                "word": {
                    "type": "string",
                    "description": "The English word to define."
                }
            },
            "required": ["word"]
        },
    )

    _BASE_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/"

    def _extract_definitions(self, data: List[dict]) -> List[str]:
        defs: List[str] = []
        for entry in data:
            meanings = entry.get("meanings", [])
            for meaning in meanings:
                part = meaning.get("partOfSpeech", "")
                for definition in meaning.get("definitions", []):
                    text = definition.get("definition", "")
                    if text:
                        defs.append(f"{part}: {text}" if part else text)
        return defs

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        word = arguments.get("word", "").strip()
        if not word:
            return {"success": False, "error": "Word is required"}
        url = f"{self._BASE_URL}{word}"
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url)
                if resp.status_code == 404:
                    return {"success": False, "error": f"No definition found for '{word}'"}
                resp.raise_for_status()
                data = resp.json()
                definitions = self._extract_definitions(data)
                if not definitions:
                    return {"success": False, "error": "No definitions parsed"}
                return {"success": True, "word": word, "definitions": definitions}
        except Exception as e:
            return {"success": False, "error": f"Definition lookup failed: {e}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [DictionaryLookupTool()]
