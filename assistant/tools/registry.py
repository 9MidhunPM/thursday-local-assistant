from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Iterable

from assistant.config.loader import ToolConfig
from assistant.tools.base import BaseTool


@dataclass
class ToolRegistry:
    _tools: dict[str, BaseTool] = field(default_factory=dict)

    def register(self, tool: BaseTool) -> None:
        self._tools[tool.name] = tool

    def tools(self) -> Iterable[BaseTool]:
        return list(self._tools.values())

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def as_openai_tools(self) -> list[dict[str, object]]:
        return [tool.as_openai_tool() for tool in self._tools.values()]

    @classmethod
    def load_builtin(cls, config: ToolConfig) -> "ToolRegistry":
        registry = cls()
        package = importlib.import_module("assistant.tools")
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name in {"base", "registry", "__init__", "groups"}:
                continue
            module = importlib.import_module(f"assistant.tools.{module_info.name}")
            if hasattr(module, "get_tools"):
                try:
                    tools = module.get_tools(config)
                except TypeError:
                    tools = module.get_tools()
                for tool in tools:
                    registry.register(tool)
            elif hasattr(module, "TOOL_CLASS"):
                registry.register(module.TOOL_CLASS())
        return registry
