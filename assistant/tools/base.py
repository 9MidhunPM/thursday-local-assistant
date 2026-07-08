from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from assistant.agent.context import ExecutionContext


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    parameters: dict[str, Any]


class BaseTool(ABC):
    metadata: ToolMetadata

    @abstractmethod
    def execute(self, arguments: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.metadata.parameters

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
