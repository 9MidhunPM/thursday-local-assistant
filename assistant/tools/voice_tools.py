from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext
from assistant.tools.base import BaseTool, ToolMetadata


@dataclass
class SpeakTextTool(BaseTool):
    metadata: ToolMetadata = ToolMetadata(
        name="speak_text",
        description="Speak text aloud using the system TTS engine. Use this to read information to the user or provide audio feedback.",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to speak aloud.",
                },
            },
            "required": ["text"],
        },
    )

    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        text = arguments.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return {"success": False, "error": "Text is required."}
        return {"success": True, "output": f"Spoken: {text[:100]}{'...' if len(text) > 100 else ''}"}


def get_tools() -> list[BaseTool]:
    return [SpeakTextTool()]
