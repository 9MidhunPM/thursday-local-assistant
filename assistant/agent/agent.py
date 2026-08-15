from __future__ import annotations

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from assistant.agent.confirm import confirm_broker
from assistant.agent.context import ExecutionContext
from assistant.llm.client import ChatMessage, LlamaCppClient, LlmResponse, ToolCall
from assistant.llm.parser import InvalidModelOutput
from assistant.logging_utils import (
    Loggers,
    log_model_interaction,
    log_tool_call,
    log_tool_result,
    redact_private_request_text,
)
from assistant.memory.long_term import LongTermMemory
from assistant.memory.short_term import Message, ShortTermMemory
from assistant.tools.groups import filter_tools_payload
from assistant.tools.registry import ToolRegistry


def _tc_field_safe(tc: Any, field: str, default: Any = None) -> Any:
    """Read a field from a tool call that may be a ToolCall object or a dict."""
    if isinstance(tc, dict):
        return tc.get(field, default)
    return getattr(tc, field, default)


def _is_retry_follow_up(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split()).strip(".!?")
    return normalized in {
        "again",
        "do it again",
        "retry",
        "retry now",
        "try again",
        "try again now",
    }


def _is_contextual_action_follow_up(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split()).strip(".!?")
    return normalized in {
        "open it",
        "show it",
        "list them",
        "do it",
        "use it",
        "use calendar agenda",
        "use calender agenda",
    }


def _parse_codex_launch(text: str) -> dict[str, str] | None:
    """Read the UI-only launch envelope without asking the chat model to relay it."""
    prefix = "[codex-launch]"
    if not text.casefold().startswith(prefix):
        return None
    try:
        payload = json.loads(text[len(prefix) :].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    project_name = payload.get("project_name")
    brief = payload.get("brief")
    model = payload.get("model")
    if not isinstance(project_name, str) or not isinstance(brief, str):
        return None
    if model is not None and not isinstance(model, str):
        return None
    return {
        "project_name": project_name,
        "brief": brief,
        "model": model or "",
    }


def _build_codex_project_prompt(brief: str) -> str:
    return (
        "You are the lead implementation agent for Thursday. "
        "Work only in the current workspace.\n\n"
        "Project brief:\n"
        f"{brief.strip()}\n\n"
        "Execution contract:\n"
        "1. Inspect the existing project before editing.\n"
        "2. If the brief is materially ambiguous, ask the user concise questions in this terminal "
        "before choosing a product direction.\n"
        "3. Implement the agreed scope with a polished, accessible interface.\n"
        "4. Run relevant tests or a local verification path.\n"
        "5. Finish with changed files, how to run the project, and verification results.\n"
        "Do not access parent directories or delete unrelated files."
    )


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
        self._loggers.user.info(redact_private_request_text(user_text))
        self._short_term.add(Message(role="user", content=user_text))

        launch = _parse_codex_launch(user_text)
        if launch:
            arguments: dict[str, Any] = {
                "task": _build_codex_project_prompt(launch["brief"]),
                "project_name": launch["project_name"],
            }
            if launch["model"]:
                arguments["model"] = launch["model"]
            if on_tool_call:
                on_tool_call("codex_orchestrate", arguments)
            result = self._execute_tool("codex_orchestrate", arguments, on_tool_chunk)
            if on_tool_result:
                on_tool_result(result)
            output = str(result.get("output") or "").strip()
            if result.get("success"):
                workspace = result.get("workspace", "the Codex workspace")
                final = f"Codex started an interactive project session in {workspace}."
                if output:
                    final += f"\n\n{output}"
            else:
                error = result.get("error", "Unknown error.")
                final = f"Codex could not start the project session: {error}"
                if output:
                    final += f"\n\n{output}"
            self._short_term.add(Message(role="assistant", content=final))
            log_model_interaction(self._loggers.model, user_text, final)
            self._maybe_extract(user_text, final)
            return final

        # Cache a per-turn filtered tool list for smarter / smaller prompts.
        selection_text = user_text
        if _is_retry_follow_up(user_text) or _is_contextual_action_follow_up(user_text):
            # A terse retry must keep the domain tool from the failed request.
            # Scan past earlier retry-only turns (which may have selected only
            # always-on tools) to the last substantive user instruction.
            for message in reversed(self._short_term.as_list()[:-1]):
                if (
                    message.role == "user"
                    and isinstance(message.content, str)
                    and message.content.strip()
                    and not _is_retry_follow_up(message.content)
                ):
                    selection_text = f"{message.content}\n{user_text}"
                    break
        turn_tools = filter_tools_payload(
            self._tools_payload,
            selection_text,
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

            results = self._execute_tools_parallel(tool_calls, on_tool_chunk)

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
                if tc.name == "find_file_system":
                    output = tool_result.get("output")
                    if isinstance(output, dict):
                        compact_output = {
                            "query": output.get("query"),
                            "source": output.get("source"),
                            "results": [
                                {
                                    "index": item.get("index"),
                                    "type": item.get("type"),
                                    "path": item.get("path"),
                                }
                                for item in output.get("results", [])
                                if isinstance(item, dict)
                            ],
                            "result_count": output.get("result_count"),
                            "truncated": output.get("truncated"),
                            **({"warning": output["warning"]} if output.get("warning") else {}),
                        }
                        model_result = {
                            "tool": tool_result.get("tool"),
                            "success": tool_result.get("success", False),
                            "output": compact_output,
                        }
                    else:
                        model_result = tool_result
                    max_tool_chars = 6000
                elif tc.name == "analyze_website":
                    model_result = {
                        "tool": tool_result.get("tool"),
                        "success": tool_result.get("success", False),
                        "url": tool_result.get("url"),
                        "final_url": tool_result.get("final_url"),
                        "page_title": tool_result.get("page_title"),
                        "viewports": tool_result.get("viewports"),
                        "analysis": tool_result.get("analysis"),
                        **({"error": tool_result["error"]} if tool_result.get("error") else {}),
                    }
                    max_tool_chars = 8000
                elif tc.name == "search_and_fetch":
                    model_result = tool_result
                    max_tool_chars = 12000 if not getattr(self._llm, "is_local", True) else 4000
                else:
                    model_result = tool_result
                    max_tool_chars = 2000 if not getattr(self._llm, "is_local", True) else 1200
                tool_content = json.dumps(model_result, ensure_ascii=True)
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

            # WebsiteAnalysisTool's nested vision request already produces a
            # complete user-facing audit. Returning it directly is both more
            # reliable and faster than asking the outer model to paraphrase it.
            if len(tool_calls) == 1 and tool_calls[0].name == "analyze_website":
                analysis = results[0].get("analysis") if results else None
                if results and results[0].get("success") and isinstance(analysis, str):
                    final_analysis = analysis.strip()
                    if final_analysis:
                        self._short_term.add(Message(role="assistant", content=final_analysis))
                        log_model_interaction(self._loggers.model, user_text, final_analysis)
                        self._maybe_extract(user_text, final_analysis)
                        return final_analysis

            # The nested private summarizer already returns the final inbox
            # brief. Avoid a second model pass that could distort it.
            if len(tool_calls) == 1 and tool_calls[0].name == "summarize_inbox":
                summary = results[0].get("summary") if results else None
                if results and results[0].get("success") and isinstance(summary, str):
                    final_summary = summary.strip()
                    if final_summary:
                        self._short_term.add(Message(role="assistant", content=final_summary))
                        log_model_interaction(self._loggers.model, user_text, final_summary)
                        return final_summary

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
    def _estimate_tokens(content: str | list[dict[str, Any]] | None) -> int:
        if not content:
            return 0
        if isinstance(content, str):
            return len(content) // 4 + 4
        # Image token use is model-dependent. This conservative fixed allowance
        # is only used by transient vision calls, not stored conversation history.
        text_chars = sum(
            len(str(part.get("text", "")))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
        image_count = sum(
            1
            for part in content
            if isinstance(part, dict) and part.get("type") == "image_url"
        )
        return text_chars // 4 + image_count * 1200 + 4

    def _analyze_images(self, prompt: str, image_paths: list[Path]) -> str:
        if self._llm.is_local:
            raise RuntimeError(
                "Website visual analysis requires a configured image-capable cloud model."
            )
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            image_bytes = path.read_bytes()
            if len(image_bytes) > 10 * 1024 * 1024:
                raise RuntimeError(f"Screenshot is too large to analyze: {path.name}")
            mime = "image/jpeg" if path.suffix.casefold() in {".jpg", ".jpeg"} else "image/png"
            encoded = base64.b64encode(image_bytes).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{encoded}",
                        "detail": "high",
                    },
                }
            )
        response = self._llm.chat(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "You are Thursday's website reviewer. Base every observation on the "
                        "provided screenshots and extracted page evidence. Clearly label anything "
                        "that cannot be verified."
                    ),
                ),
                ChatMessage(role="user", content=parts),
            ],
            tools=None,
            use_response_format=False,
            reasoning_effort="none",
        )
        if not response.content:
            raise RuntimeError("The vision model returned an empty website review.")
        return response.content.strip()

    def _summarize_private_text(self, prompt: str) -> str:
        response = self._llm.chat(
            [
                ChatMessage(
                    role="system",
                    content=(
                        "You summarize private inbox content for its owner. Use only the supplied "
                        "email evidence, retain exact dates and requested actions, do not invent "
                        "facts, and do not reveal internal message identifiers."
                    ),
                ),
                ChatMessage(role="user", content=prompt),
            ],
            tools=None,
            use_response_format=False,
            reasoning_effort="none",
        )
        if not response.content:
            raise RuntimeError("The model returned an empty inbox summary.")
        return response.content.strip()

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

    def warmup_payload(self) -> tuple[list[ChatMessage], list[dict[str, Any]]]:
        """Minimal request mirroring a typical turn's stable prompt prefix.

        Used once at startup to pre-warm llama.cpp's prefix cache (and GPU
        graphs), so the user's first real turn only prefills the delta.
        """
        user_text = "Hello"
        tools = filter_tools_payload(
            self._tools_payload,
            user_text,
            enabled_groups=list(self._config.enabled_tool_groups) or None,
            smart=self._config.smart_tool_filter,
        )
        messages = [
            ChatMessage(role="system", content=self._base_system_message),
            ChatMessage(role="user", content=user_text),
        ]
        return messages, tools

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

    def _execute_tools_parallel(
        self,
        tool_calls: list[ToolCall],
        on_tool_chunk: Callable[[str, str], None] | None = None,
    ) -> list[dict[str, Any]]:
        if not tool_calls:
            return []
        if len(tool_calls) == 1:
            return [
                self._execute_tool(
                    tool_calls[0].name,
                    tool_calls[0].arguments,
                    on_tool_chunk,
                )
            ]
        workers = min(len(tool_calls), max(1, self._config.max_parallel_tools))
        if workers <= 1:
            return [
                self._execute_tool(tc.name, tc.arguments, on_tool_chunk) for tc in tool_calls
            ]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(
                ex.map(
                    lambda tc: self._execute_tool(tc.name, tc.arguments, on_tool_chunk),
                    tool_calls,
                )
            )

    def _execute_tool(
        self,
        tool_name: str | None,
        arguments: dict[str, Any],
        on_tool_chunk: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
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
            analyze_images=self._analyze_images,
            summarize_private_text=self._summarize_private_text,
            report_progress=(
                (lambda chunk: on_tool_chunk(tool_name, chunk))
                if on_tool_chunk is not None
                else None
            ),
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
