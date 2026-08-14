from __future__ import annotations

from assistant.llm.client import ToolCall
from assistant.memory.short_term import Message, ShortTermMemory


def _call(call_id: str = "call_1") -> ToolCall:
    return ToolCall(id=call_id, name="spotify_search_play", arguments={"query": "Feels"})


def test_pruning_never_leaves_a_leading_orphan_tool_message() -> None:
    memory = ShortTermMemory(max_tokens=45)
    memory.add(Message(role="user", content="x" * 120))
    memory.add(Message(role="assistant", content=None, tool_calls=[_call()]))
    memory.add(
        Message(
            role="tool",
            content="x" * 160,
            name="spotify_search_play",
            tool_call_id="call_1",
        )
    )
    memory.add(Message(role="assistant", content="Done."))

    history = memory.as_list()

    assert not history or history[0].role != "tool"


def test_history_filters_orphaned_and_incomplete_tool_exchanges() -> None:
    memory = ShortTermMemory(max_tokens=10_000)
    memory.messages.extend(
        [
            Message(role="tool", content="orphan", tool_call_id="old_call"),
            Message(role="assistant", content=None, tool_calls=[_call("incomplete")]),
            Message(role="user", content="Play Feels feat Williams"),
        ]
    )

    assert memory.as_list() == [Message(role="user", content="Play Feels feat Williams")]


def test_history_preserves_complete_tool_exchange() -> None:
    assistant = Message(role="assistant", content=None, tool_calls=[_call()])
    tool = Message(
        role="tool",
        content='{"success": true}',
        name="spotify_search_play",
        tool_call_id="call_1",
    )
    memory = ShortTermMemory(messages=[assistant, tool], max_tokens=10_000)

    assert memory.as_list() == [assistant, tool]
