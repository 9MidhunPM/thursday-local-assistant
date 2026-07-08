from __future__ import annotations

from typing import Any, Iterable

from assistant.memory.conversation_store import ConversationStore
from assistant.memory.short_term import Message, ShortTermMemory


def _tool_calls_to_dicts(tool_calls: list[Any] | None) -> list[dict[str, Any]] | None:
    if not tool_calls:
        return None
    out: list[dict[str, Any]] = []
    for tc in tool_calls:
        out.append(
            {
                "id": getattr(tc, "id", None),
                "name": getattr(tc, "name", None),
                "arguments": getattr(tc, "arguments", None),
            }
        )
    return out


def _dict_to_message(raw: dict[str, Any]) -> Message:
    """Reconstruct a Message from its DB-serialized dict form.

    Tool calls are stored in the DB as plain dicts; the LLM client's
    ``_build_payload`` expects objects with ``.id`` / ``.name`` / ``.arguments``
    attributes (``ToolCall`` dataclasses).  We convert them back here so the
    round-trip through persistence is transparent to the agent.
    """
    from assistant.llm.client import ToolCall

    tool_calls = raw.get("tool_calls")
    if tool_calls:
        converted: list[Any] = []
        for i, tc in enumerate(tool_calls):
            if isinstance(tc, dict):
                args = tc.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}
                converted.append(
                    ToolCall(
                        id=tc.get("id", f"call_{i}"),
                        name=tc.get("name", ""),
                        arguments=args,
                    )
                )
            else:
                converted.append(tc)
        tool_calls = converted
    return Message(
        role=raw["role"],
        content=raw.get("content"),
        name=raw.get("tool_name") or raw.get("name"),
        tool_call_id=raw.get("tool_call_id"),
        tool_calls=tool_calls,
    )


class SessionMemory:
    """Conversation-aware working memory.

    Wraps an in-memory :class:`ShortTermMemory` buffer (kept pruned to fit the
    model context window) and transparently persists every message to the
    backing :class:`ConversationStore` so that conversations survive restarts.
    """

    def __init__(self, store: ConversationStore, max_tokens: int = 1500) -> None:
        self._store = store
        self._max_tokens = max_tokens
        self._buffer = ShortTermMemory(max_tokens=max_tokens)
        self._conversation_id: int | None = None

    @property
    def store(self) -> ConversationStore:
        return self._store

    @property
    def conversation_id(self) -> int | None:
        return self._conversation_id

    def start_conversation(self, title: str = "New Chat") -> int:
        cid = self._store.create_conversation(title)
        self.set_conversation(cid)
        return cid

    def set_conversation(self, conversation_id: int | None) -> None:
        self._conversation_id = conversation_id
        self._buffer = ShortTermMemory(max_tokens=self._max_tokens)
        if conversation_id is not None:
            for raw in self._store.get_messages(conversation_id):
                self._buffer.add(_dict_to_message(raw))

    def clear(self) -> None:
        self._conversation_id = None
        self._buffer = ShortTermMemory(max_tokens=self._max_tokens)

    def add(self, message: Message) -> None:
        self._buffer.add(message)
        if self._conversation_id is not None:
            self._store.add_message(
                self._conversation_id,
                role=message.role,
                content=message.content,
                tool_name=message.name,
                tool_call_id=message.tool_call_id,
                tool_calls=_tool_calls_to_dicts(message.tool_calls),
            )

    def extend(self, messages: Iterable[Message]) -> None:
        for message in messages:
            self.add(message)

    def as_list(self) -> list[Message]:
        return self._buffer.as_list()
