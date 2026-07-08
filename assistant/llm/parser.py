from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ModelDecision:
    type: Literal["tool_call", "final"]
    tool: str | None
    arguments: dict[str, Any] | None
    response: str | None
    tool_call_id: str | None = None


class InvalidModelOutput(Exception):
    pass


import re

def parse_model_output(text: str) -> ModelDecision:
    data = None
    clean_text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(clean_text)
    except json.JSONDecodeError:
        # Try to find a JSON object in the text with non-greedy match first
        match = re.search(r"\{[\s\S]*?\}", clean_text)
        if match:
            # If there is too much extra text around the JSON, reject it to force a retry
            if len(clean_text) - len(match.group(0)) > 15:
                data = None
            else:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        
        # Fallback to greedy match if non-greedy fails
        if data is None:
            match = re.search(r"\{[\s\S]*\}", clean_text)
            if match and len(clean_text) - len(match.group(0)) <= 15:
                try:
                    data = json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
                
    if data is None:
        raise InvalidModelOutput("Model returned invalid JSON.")

    decision_type = data.get("type")
    if decision_type == "tool_call" or ("tool" in data and "arguments" in data):
        tool = data.get("tool")
        arguments = data.get("arguments")
        if not isinstance(tool, str) or not isinstance(arguments, dict):
            raise InvalidModelOutput("Tool call requires tool name and arguments.")
        return ModelDecision(type="tool_call", tool=tool, arguments=arguments, response=None)

    if decision_type == "final" or "response" in data:
        response = data.get("response")
        if not isinstance(response, str):
            raise InvalidModelOutput("Final response must include response string.")
        return ModelDecision(type="final", tool=None, arguments=None, response=response)

    raise InvalidModelOutput("Unknown response type.")
