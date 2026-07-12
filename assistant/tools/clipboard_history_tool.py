from __future__ import annotations

from typing import Any

from assistant.tools.base import BaseTool


def get_tools(config: Any | None = None) -> list[BaseTool]:
    # Merged into clipboard tool in clipboard_tools.py
    return []
