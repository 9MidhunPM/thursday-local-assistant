from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Any

@dataclass
class Message:
    role: str
    content: str | None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[Any] | None = None


@dataclass
class ShortTermMemory:
    messages: list[Message] = field(default_factory=list)
    max_tokens: int = 1500  # Leave room for the 2048 context limit

    def _estimate_tokens(self) -> int:
        # A rough but safe heuristic: 1 token ~= 4 characters
        total = 0
        for m in self.messages:
            if m.content:
                total += len(m.content) // 4 + 10
            elif m.tool_calls:
                total += 20 # fixed cost for tool calls
            else:
                total += 10
        return total

    def add(self, message: Message) -> None:
        self.messages.append(message)
        self._prune()

    def extend(self, messages: Iterable[Message]) -> None:
        self.messages.extend(messages)
        self._prune()

    def _prune(self) -> None:
        # Protect recent messages, drop the oldest ones until under the limit
        while len(self.messages) > 1 and self._estimate_tokens() > self.max_tokens:
            self.messages.pop(0)

    def as_list(self) -> list[Message]:
        return list(self.messages)
