"""Translation tool using Google Translate public endpoint.

Provides `translate_text` function for translating arbitrary text to a target language.
No external dependencies beyond `httpx` which is already in the project.
"""

from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class TranslateTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="translate_text",
        description="Translate a piece of text to a target language using Google Translate.",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to translate."
                },
                "target_language": {
                    "type": "string",
                    "description": "Target language code (ISO 639-1), e.g., 'en', 'es', 'fr'."
                },
                "source_language": {
                    "type": "string",
                    "description": "Source language code (ISO 639-1). Optional; defaults to auto-detect.",
                },
            },
            "required": ["text", "target_language"],
        },
    )

    _BASE_URL = "https://translate.googleapis.com/translate_a/single"

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        text = arguments.get("text", "").strip()
        target = arguments.get("target_language", "").strip()
        source = arguments.get("source_language", "auto").strip() or "auto"
        if not text or not target:
            return {"success": False, "error": "Both 'text' and 'target_language' are required"}
        params = {
            "client": "gtx",
            "sl": source,
            "tl": target,
            "dt": "t",
            "q": text,
        }
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(self._BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
                # The translation is in data[0]; each element is [translated, original, ...]
                translated = "".join(part[0] for part in data[0] if part and part[0])
                return {"success": True, "translated_text": translated}
        except Exception as e:
            return {"success": False, "error": f"Translation failed: {e}"}


def get_tools(config: Any | None = None) -> list[BaseTool]:
    return [TranslateTool()]
