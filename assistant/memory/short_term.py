from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        value = tool_call.get("id")
    else:
        value = getattr(tool_call, "id", None)
    return str(value) if value else None

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
            # A tool result is only legal immediately after the assistant
            # tool_calls message that requested it. If that parent was pruned,
            # prune every newly orphaned result with it.
            while self.messages and self.messages[0].role == "tool":
                self.messages.pop(0)

    def as_list(self) -> list[Message]:
        return self._valid_tool_history()

    def _valid_tool_history(self) -> list[Message]:
        """Return only complete Chat Completions tool-call exchanges."""
        valid: list[Message] = []
        index = 0
        while index < len(self.messages):
            message = self.messages[index]
            if message.role == "tool":
                index += 1
                continue
            if message.role != "assistant" or not message.tool_calls:
                valid.append(message)
                index += 1
                continue

            expected = {
                call_id
                for call in message.tool_calls
                if (call_id := _tool_call_id(call)) is not None
            }
            group: list[Message] = []
            seen: set[str] = set()
            cursor = index + 1
            while cursor < len(self.messages) and self.messages[cursor].role == "tool":
                tool_message = self.messages[cursor]
                if tool_message.tool_call_id in expected and tool_message.tool_call_id not in seen:
                    group.append(tool_message)
                    seen.add(tool_message.tool_call_id)
                cursor += 1

            if expected and seen == expected:
                valid.append(message)
                valid.extend(group)
            index = cursor
        return valid
