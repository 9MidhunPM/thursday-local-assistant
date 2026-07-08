from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

from assistant.agent.context import ExecutionContext
from assistant.llm.client import ChatMessage, LlamaCppClient, LlmResponse, ToolCall
from assistant.llm.parser import InvalidModelOutput
from assistant.memory.long_term import LongTermMemory
from assistant.memory.short_term import Message, ShortTermMemory
from assistant.tools.registry import ToolRegistry
from assistant.logging_utils import Loggers, log_tool_call, log_tool_result, log_model_interaction


def _tc_field_safe(tc: Any, field: str, default: Any = None) -> Any:
    """Read a field from a tool call that may be a ToolCall object or a dict."""
    if isinstance(tc, dict):
        return tc.get(field, default)
    return getattr(tc, field, default)


@dataclass(frozen=True)
class AgentConfig:
    max_tool_steps: int
    system_prompt: str
    json_retries: int
    stream_responses: bool
    max_parallel_tools: int = 4
    auto_extract: bool = True
    extract_max_tokens: int = 256


class Agent:
    def __init__(
        self,
        llm: LlamaCppClient,
        tool_registry: ToolRegistry,
        memory: LongTermMemory,
        short_term: ShortTermMemory,
        loggers: Loggers,
        config: AgentConfig,
    ) -> None:
        self._llm = llm
        self._tool_registry = tool_registry
        self._memory = memory
        self._short_term = short_term
        self._loggers = loggers
        self._config = config
        self._tools_payload = tool_registry.as_openai_tools()
        self._base_system_message = self._build_base_system_message()

    # ------------------------------------------------------------------ public

    def handle_message(
        self,
        user_text: str,
        on_stream: Callable[[str], None] | None = None,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_chunk: Callable[[str, str], None] | None = None,
        on_tool_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        self._loggers.user.info(user_text)
        self._short_term.add(Message(role="user", content=user_text))

        for _ in range(self._config.max_tool_steps):
            try:
                response = self._request_llm(user_text, on_stream, on_tool_chunk)
            except InvalidModelOutput as exc:
                error_text = f"Model output error: {exc}"
                self._loggers.error.error(error_text)
                self._short_term.add(Message(role="assistant", content=error_text))
                return error_text

            # No tool calls -> final streamed/text response.
            if not response.tool_calls:
                content = response.content or ""
                self._short_term.add(Message(role="assistant", content=content))
                log_model_interaction(self._loggers.model, user_text, content)
                self._maybe_extract(user_text, content)
                return content

            # One or more tool calls this turn. Record the assistant's tool-call
            # message (with ALL calls) so the model remembers what it requested.
            tool_calls: list[ToolCall] = list(response.tool_calls)
            self._short_term.add(Message(role="assistant", content=None, tool_calls=tool_calls))

            # Notify the UI of every call up-front so cards appear immediately.
            for tc in tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.arguments)
                log_tool_call(self._loggers.tool, tc.name, tc.arguments)

            # Execute all requested tools concurrently (async/threadpool).
            results = self._execute_tools_parallel(tool_calls)

            # Append tool results to memory in the original call order so that
            # tool_call_id pairing stays coherent for the next model turn.
            for tc, tool_result in zip(tool_calls, results):
                if on_tool_result:
                    on_tool_result(tool_result)
                log_tool_result(
                    self._loggers.tool,
                    tool_result["tool"],
                    tool_result.get("success", False),
                    tool_result,
                    tool_result.get("error"),
                )
                tool_content = json.dumps(tool_result, ensure_ascii=True)
                # Truncate large tool outputs to protect the context window.
                if len(tool_content) > 1200:
                    tool_content = tool_content[:1200] + "... [TRUNCATED]"
                self._short_term.add(
                    Message(
                        role="tool",
                        name=tool_result["tool"],
                        content=tool_content,
                        tool_call_id=tc.id,
                    )
                )

        final = "I couldn't complete the request within the tool step limit."
        self._short_term.add(Message(role="assistant", content=final))
        log_model_interaction(self._loggers.model, user_text, final)
        return final

    # ------------------------------------------------------------------ session

    @property
    def conversation_id(self) -> int | None:
        return getattr(self._short_term, "conversation_id", None)

    def start_conversation(self, title: str = "New Chat") -> int | None:
        if hasattr(self._short_term, "start_conversation"):
            return self._short_term.start_conversation(title)
        return None

    def set_conversation(self, conversation_id: int | None) -> None:
        if hasattr(self._short_term, "set_conversation"):
            self._short_term.set_conversation(conversation_id)

    def get_conversation_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        store = getattr(self._short_term, "store", None)
        if store is not None:
            return store.get_messages(conversation_id)
        return []

    # ------------------------------------------------------------------ context budget

    # Conservative budget for the entire prompt (system + history + RAG + tools payload).
    # The model has an 8192-token context window; we leave room for the model's
    # response and a safety margin.
    _CONTEXT_BUDGET_TOKENS = 7000

    @staticmethod
    def _estimate_tokens(text: str | None) -> int:
        if not text:
            return 0
        return len(text) // 4 + 4

    def _estimate_messages_tokens(self, messages: list[ChatMessage]) -> int:
        total = 0
        for msg in messages:
            total += self._estimate_tokens(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += self._estimate_tokens(
                        _tc_field_safe(tc, "name", "") + " "
                        + json.dumps(_tc_field_safe(tc, "arguments", {}), ensure_ascii=True)
                    )
            total += 4  # per-message overhead
        # Add the tools payload cost (sent alongside messages).
        total += len(json.dumps(self._tools_payload, ensure_ascii=True)) // 4
        return total

    def _guard_context_budget(self, messages: list[ChatMessage]) -> None:
        """If the estimated prompt size exceeds the budget, drop older history messages
        (keeping the system message and the most recent turns) until it fits."""
        estimated = self._estimate_messages_tokens(messages)
        if estimated <= self._CONTEXT_BUDGET_TOKENS:
            return
        self._loggers.error.warning(
            f"Context budget exceeded: ~{estimated} tokens > {self._CONTEXT_BUDGET_TOKENS}. Pruning history."
        )
        # Keep index 0 (system message) and the last few messages; drop from index 1.
        while len(messages) > 3 and self._estimate_messages_tokens(messages) > self._CONTEXT_BUDGET_TOKENS:
            messages.pop(1)

    # ------------------------------------------------------------------ LLM

    def _build_base_system_message(self) -> str:
        # Tool definitions are sent via the `tools` API parameter as JSON schemas;
        # repeating them as text here wastes ~1200+ tokens and causes context overflow.
        return (
            f"{self._config.system_prompt}\n\n"
            f"CRITICAL INSTRUCTIONS:\n"
            f"1. When asked to perform an action (e.g., play a song), invoke the correct tool.\n"
            f"2. After a tool succeeds, DO NOT call the tool again. You MUST return a 'final' "
            f"response confirming the result to the user.\n"
            f"3. For playing single songs, use search_play. Do NOT use playlist tools.\n"
            f"4. You may call multiple independent tools in a single turn when it is more "
            f"efficient to do so (e.g. fetching the time and system info together)."
        )

    def _get_dynamic_context(self, user_text: str) -> str | None:
        memory_context = self._memory.build_context(user_text)
        if any(memory_context.values()):
            context_json = json.dumps(memory_context, ensure_ascii=True)
            # Cap the RAG context to ~800 chars to protect the context window.
            if len(context_json) > 800:
                context_json = context_json[:800] + "...}"
            return (
                "Relevant personal context:\n"
                + context_json
                + "\nUse this personal context when it is relevant, but do not mention it unless it helps the user."
            )
        return None

    def _request_llm(
        self,
        user_text: str,
        on_stream: Callable[[str], None] | None,
        on_tool_chunk: Callable[[str, str], None] | None = None,
    ) -> LlmResponse:
        # 1. Static base prompt (instructions only; tool defs are in the `tools` payload).
        messages: list[ChatMessage] = [ChatMessage(role="system", content=self._base_system_message)]

        # 2. Conversation history.
        for msg in self._short_term.as_list():
            messages.append(
                ChatMessage(
                    role=msg.role,
                    content=msg.content,
                    name=msg.name,
                    tool_call_id=msg.tool_call_id,
                    tool_calls=msg.tool_calls,
                )
            )

        # 3. Dynamic RAG context at the end so the static prefix cache stays valid.
        dynamic_context = self._get_dynamic_context(user_text)
        if dynamic_context:
            messages.append(ChatMessage(role="system", content=dynamic_context))

        # 4. Context budget guard: estimate total prompt size and prune if needed.
        self._guard_context_budget(messages)

        for attempt in range(self._config.json_retries + 1):
            if on_stream:
                response = self._llm.chat_stream(
                    messages,
                    tools=self._tools_payload,
                    on_token=on_stream,
                    on_tool_chunk=on_tool_chunk,
                )
            else:
                response = self._llm.chat(messages, tools=self._tools_payload)
            if response.tool_calls:
                log_model_interaction(
                    self._loggers.model,
                    user_text,
                    tool_calls=[
                        {"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls
                    ],
                )
                return response

            content = response.content or ""
            log_model_interaction(self._loggers.model, user_text, content)
            if content.strip():
                return response

            if attempt >= self._config.json_retries:
                raise InvalidModelOutput("Empty response with no tool calls.")

            messages.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Your last response was not a valid tool call. "
                        "Please use the standard tool calling format to invoke the tool properly. "
                        "Do NOT output raw JSON strings for tool calls."
                    ),
                )
            )

        raise InvalidModelOutput("Model output could not be parsed.")

    # ------------------------------------------------------------------ tools

    def _execute_tools_parallel(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        if not tool_calls:
            return []
        # Single call: skip the pool overhead entirely.
        if len(tool_calls) == 1:
            return [self._execute_tool(tool_calls[0].name, tool_calls[0].arguments)]
        workers = min(len(tool_calls), max(1, self._config.max_parallel_tools))
        if workers <= 1:
            return [self._execute_tool(tc.name, tc.arguments) for tc in tool_calls]
        # ThreadPoolExecutor gives real concurrency for I/O-bound tools
        # (web_search, spotify, file reads) while preserving result order via map().
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(lambda tc: self._execute_tool(tc.name, tc.arguments), tool_calls))

    def _execute_tool(self, tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if not tool_name:
            return {"tool": "unknown", "success": False, "error": "Tool name missing."}

        tool = self._tool_registry.get(tool_name)
        if tool is None:
            result = {
                "tool": tool_name,
                "success": False,
                "error": "Requested tool is not available.",
            }
            log_tool_result(self._loggers.tool, tool_name, False, error="Requested tool is not available.")
            return result

        context = ExecutionContext(
            confirm=self._confirm_action,
            loggers=self._loggers,
            memory=self._memory,
            now=lambda: datetime.now(timezone.utc),
        )
        log_tool_call(self._loggers.tool, tool_name, arguments)
        try:
            result = tool.execute(arguments, context)
            return {"tool": tool_name, **result}
        except Exception as exc:  # noqa: BLE001 - surface errors
            error_result = {"tool": tool_name, "success": False, "error": str(exc)}
            log_tool_result(self._loggers.tool, tool_name, False, error=str(exc))
            return error_result

    # ------------------------------------------------------------------ memory extraction

    def _maybe_extract(self, user_text: str, assistant_text: str) -> None:
        if not self._config.auto_extract:
            return
        try:
            from assistant.memory.auto_extract import run_extraction_async

            run_extraction_async(
                self._llm,
                self._memory,
                user_text,
                assistant_text,
            )
        except Exception as exc:  # noqa: BLE001 - best-effort
            self._loggers.error.debug("Memory extraction skipped: %s", exc) if hasattr(
                self._loggers.error, "debug"
            ) else None

    # ------------------------------------------------------------------ misc

    @staticmethod
    def _confirm_action(prompt: str) -> bool:
        if not sys.stdin.isatty():
            return False
        reply = input(f"{prompt} [y/N]: ").strip().lower()
        return reply in {"y", "yes"}
