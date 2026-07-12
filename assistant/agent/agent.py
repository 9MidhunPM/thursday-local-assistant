from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from concurrent.futures import ThreadPoolExecutor

from assistant.agent.confirm import confirm_broker
from assistant.agent.context import ExecutionContext
from assistant.llm.client import ChatMessage, LlamaCppClient, LlmResponse, ToolCall
from assistant.llm.parser import InvalidModelOutput
from assistant.memory.long_term import LongTermMemory
from assistant.memory.short_term import Message, ShortTermMemory
from assistant.tools.registry import ToolRegistry
from assistant.tools.groups import filter_tools_payload
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
    context_budget_tokens: int = 7000
    smart_tool_filter: bool = True
    enabled_tool_groups: tuple[str, ...] = ()
    web_confirm_timeout_sec: int = 60
    user_name: str = "User"


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
        confirm_broker.default_timeout_sec = float(config.web_confirm_timeout_sec)

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

        # Cache a per-turn filtered tool list for smarter / smaller prompts.
        turn_tools = filter_tools_payload(
            self._tools_payload,
            user_text,
            enabled_groups=list(self._config.enabled_tool_groups) or None,
            smart=self._config.smart_tool_filter,
        )

        for step in range(self._config.max_tool_steps):
            try:
                response = self._request_llm(
                    user_text, on_stream, on_tool_chunk, tools=turn_tools, step=step
                )
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

            tool_calls: list[ToolCall] = list(response.tool_calls)
            self._short_term.add(Message(role="assistant", content=None, tool_calls=tool_calls))

            for tc in tool_calls:
                if on_tool_call:
                    on_tool_call(tc.name, tc.arguments)
                log_tool_call(self._loggers.tool, tc.name, tc.arguments)

            results = self._execute_tools_parallel(tool_calls)

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
                # Truncate large tool outputs; keep a bit more on cloud models.
                max_tool_chars = 2000 if not getattr(self._llm, "is_local", True) else 1200
                if len(tool_content) > max_tool_chars:
                    tool_content = tool_content[:max_tool_chars] + "... [TRUNCATED]"
                self._short_term.add(
                    Message(
                        role="tool",
                        name=tool_result["tool"],
                        content=tool_content,
                        tool_call_id=tc.id,
                    )
                )

        final = (
            "I hit the tool-step limit before finishing. "
            "Try a narrower request, or raise max_tool_steps."
        )
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

    @staticmethod
    def _estimate_tokens(text: str | None) -> int:
        if not text:
            return 0
        return len(text) // 4 + 4

    def _estimate_messages_tokens(
        self, messages: list[ChatMessage], tools: list[dict[str, object]] | None
    ) -> int:
        total = 0
        for msg in messages:
            total += self._estimate_tokens(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total += self._estimate_tokens(
                        _tc_field_safe(tc, "name", "")
                        + " "
                        + json.dumps(_tc_field_safe(tc, "arguments", {}), ensure_ascii=True)
                    )
            total += 4
        if tools:
            total += len(json.dumps(tools, ensure_ascii=True)) // 4
        return total

    def _guard_context_budget(
        self, messages: list[ChatMessage], tools: list[dict[str, object]] | None
    ) -> None:
        budget = self._config.context_budget_tokens
        estimated = self._estimate_messages_tokens(messages, tools)
        if estimated <= budget:
            return
        self._loggers.error.warning(
            "Context budget exceeded: ~%s tokens > %s. Pruning history.",
            estimated,
            budget,
        )
        while len(messages) > 3 and self._estimate_messages_tokens(messages, tools) > budget:
            messages.pop(1)

    # ------------------------------------------------------------------ LLM

    def _build_base_system_message(self) -> str:
        provider = getattr(self._llm, "provider", "local")
        model = getattr(self._llm, "model", "")
        return (
            f"{self._config.system_prompt}\n\n"
            f"Runtime: provider={provider}, model={model}, user={self._config.user_name}.\n"
            f"Tools: use them for all actions. After success, confirm briefly. "
            f"Call multiple tools in one turn when efficient. Never expose secrets."
        )

    def _get_dynamic_context(self, user_text: str) -> str | None:
        memory_context = self._memory.build_context(user_text)
        if any(memory_context.values()):
            context_json = json.dumps(memory_context, ensure_ascii=True)
            cap = 1600 if not getattr(self._llm, "is_local", True) else 800
            if len(context_json) > cap:
                context_json = context_json[:cap] + "...}"
            return (
                "Relevant personal context:\n"
                + context_json
                + "\nUse this personal context when relevant; do not mention the memory system itself."
            )
        return None

    def _request_llm(
        self,
        user_text: str,
        on_stream: Callable[[str], None] | None,
        on_tool_chunk: Callable[[str, str], None] | None = None,
        tools: list[dict[str, object]] | None = None,
        step: int = 0,
    ) -> LlmResponse:
        tools = tools if tools is not None else self._tools_payload
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=self._base_system_message)
        ]

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

        dynamic_context = self._get_dynamic_context(user_text)
        if dynamic_context:
            messages.append(ChatMessage(role="system", content=dynamic_context))

        if step > 0:
            messages.append(
                ChatMessage(
                    role="system",
                    content=(
                        "Continue from the tool results above. "
                        "If the task is complete, respond to the user. "
                        "Otherwise call the next needed tool(s)."
                    ),
                )
            )

        self._guard_context_budget(messages, tools)

        for attempt in range(self._config.json_retries + 1):
            if on_stream:
                response = self._llm.chat_stream(
                    messages,
                    tools=tools,  # type: ignore[arg-type]
                    on_token=on_stream,
                    on_tool_chunk=on_tool_chunk,
                )
            else:
                response = self._llm.chat(messages, tools=tools)  # type: ignore[arg-type]
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
                        "Your last response was empty or invalid. "
                        "Either call a tool using the tool-calling interface, "
                        "or reply with a clear final answer for the user."
                    ),
                )
            )

        raise InvalidModelOutput("Model output could not be parsed.")

    # ------------------------------------------------------------------ tools

    def _execute_tools_parallel(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        if not tool_calls:
            return []
        if len(tool_calls) == 1:
            return [self._execute_tool(tool_calls[0].name, tool_calls[0].arguments)]
        workers = min(len(tool_calls), max(1, self._config.max_parallel_tools))
        if workers <= 1:
            return [self._execute_tool(tc.name, tc.arguments) for tc in tool_calls]
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
            log_tool_result(
                self._loggers.tool, tool_name, False, error="Requested tool is not available."
            )
            return result

        context = ExecutionContext(
            confirm=confirm_broker.request,
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
