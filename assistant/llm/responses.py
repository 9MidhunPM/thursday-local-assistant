"""Stateless Responses transport; replay output items rather than hidden server state."""

from __future__ import annotations

# Error messages are surfaced directly as task pause reasons.
# ruff: noqa: TRY003
import asyncio
import contextlib
import json
import threading
import time
from typing import Any

import httpx

from assistant.llm.client import (
    InferenceStats,
    LlmResponse,
    OpenAICompatibleClient,
    ToolCall,
)


class ResponseIncomplete(RuntimeError):
    """A partial response must never execute partial function arguments."""


class ResponsesClient(OpenAICompatibleClient):
    def __init__(self, *args: Any, reasoning_effort: str = "low", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reasoning_effort = reasoning_effort

    def _responses_url(self) -> str:
        base = self.base_url.rstrip("/")
        return f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"

    def _response_payload(
        self, messages, tools=None, *, stream=False, effort=None, max_output_tokens=None
    ):
        items = []
        for message in messages:
            if message.response_items:
                # Includes encrypted reasoning, phase, and complete function calls.
                items.extend(message.response_items)
                continue
            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content or "",
                    }
                )
                continue
            if message.content:
                content = message.content
                if isinstance(content, list):
                    content = [
                        {
                            "type": "input_image",
                            "image_url": part["image_url"]["url"],
                            "detail": part["image_url"].get("detail", "auto"),
                        }
                        if part.get("type") == "image_url"
                        else {"type": "input_text", "text": part.get("text", "")}
                        for part in content
                    ]
                items.append(
                    {
                        "role": "developer" if message.role == "system" else message.role,
                        "content": content,
                    }
                )
            items.extend(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(call.arguments),
                }
                for call in message.tool_calls or []
            )
        payload = {
            "model": self.model,
            "input": items,
            "store": False,
            "stream": stream,
            "reasoning": {"effort": effort or self.reasoning_effort},
            "include": ["reasoning.encrypted_content"],
        }
        limit = max_output_tokens if max_output_tokens is not None else self._max_tokens
        if limit:
            payload["max_output_tokens"] = limit
        if tools:
            payload["tools"] = [
                {"type": "function", **tool["function"], "strict": False} for tool in tools
            ]
        return payload

    def _parse_responses(self, raw, elapsed=0.0, first_token=None):
        if raw.get("status") != "completed":
            raise ResponseIncomplete(
                "The provider response did not complete. Resume with a larger output budget."
            )
        text, calls = [], []
        for item in raw.get("output", []):
            if item.get("type") == "message":
                for part in item.get("content", []):
                    if part.get("type") == "output_text":
                        text.append(part.get("text", ""))
                    elif part.get("type") == "refusal":
                        text.append(part.get("refusal", ""))
            elif item.get("type") == "function_call":
                arguments = json.loads(item.get("arguments") or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("Function arguments must be an object.")
                calls.append(ToolCall(item["call_id"], item["name"], arguments))
        usage = raw.get("usage") or {}
        details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        stats = InferenceStats(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            total_time=elapsed,
            time_to_first_token=first_token,
            backend="openai-responses",
            cached_tokens=details.get("cached_tokens", 0),
            reasoning_tokens=output_details.get("reasoning_tokens", 0),
        )
        return LlmResponse("".join(text) or None, calls, raw, stats)

    def chat(
        self,
        messages,
        tools=None,
        use_response_format=True,
        reasoning_effort=None,
        max_output_tokens=None,
        cancel_event=None,
    ):
        del use_response_format  # Compatibility argument; Responses uses native output items.
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Task cancelled.")
        if cancel_event is not None:
            return self.chat_stream(
                messages,
                tools,
                cancel_event=cancel_event,
                reasoning_effort=reasoning_effort,
                max_output_tokens=max_output_tokens,
            )
        start = time.perf_counter()
        response = self._client.post(
            self._responses_url(),
            json=self._response_payload(
                messages, tools, effort=reasoning_effort, max_output_tokens=max_output_tokens
            ),
        )
        self._raise_for_status(response)
        return self._parse_responses(response.json(), time.perf_counter() - start)

    @contextlib.contextmanager
    def _interruptible_stream(self, payload, cancel_event):
        done = threading.Event()
        with self._client.stream("POST", self._responses_url(), json=payload) as response:

            def watch():
                while not done.wait(0.1):
                    if cancel_event.is_set():
                        response.close()
                        return

            if cancel_event is not None:
                threading.Thread(target=watch, daemon=True, name="thursday-model-stop").start()
            try:
                yield response
            except (httpx.TransportError, httpx.StreamError):
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Task cancelled.") from None
                raise
            finally:
                done.set()

    def chat_stream(
        self,
        messages,
        tools=None,
        on_token=None,
        on_tool_chunk=None,
        use_response_format=False,
        cancel_event=None,
        reasoning_effort=None,
        max_output_tokens=None,
    ):
        del use_response_format
        start, first, completed = time.perf_counter(), None, None
        names = {}
        payload = self._response_payload(
            messages,
            tools,
            stream=True,
            effort=reasoning_effort,
            max_output_tokens=max_output_tokens,
        )
        with self._interruptible_stream(payload, cancel_event) as response:
            self._raise_for_status(response)
            for line in response.iter_lines():
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError("Task cancelled.")
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                kind = event.get("type")
                if kind == "response.output_text.delta":
                    if first is None:
                        first = time.perf_counter() - start
                    if on_token:
                        on_token(event.get("delta", ""))
                elif kind == "response.output_item.added":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        names[event["output_index"]] = item["name"]
                elif kind == "response.function_call_arguments.delta" and on_tool_chunk:
                    name = names.get(event.get("output_index"))
                    if name:
                        on_tool_chunk(name, event.get("delta", ""))
                elif kind == "response.completed":
                    completed = event["response"]
                elif kind in {"response.failed", "response.incomplete", "error"}:
                    raise ResponseIncomplete(
                        "The provider interrupted the response. No partial tool call was executed."
                    )
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Task cancelled.")
        if completed is None:
            raise ResponseIncomplete("The provider stream ended before completion.")
        return self._parse_responses(completed, time.perf_counter() - start, first)

    async def achat(self, messages, tools=None, use_response_format=True):
        del use_response_format
        start = time.perf_counter()
        response = await self._get_async_client().post(
            self._responses_url(), json=self._response_payload(messages, tools)
        )
        self._raise_for_status(response)
        return self._parse_responses(response.json(), time.perf_counter() - start)

    async def achat_stream(
        self, messages, tools=None, on_token=None, on_tool_chunk=None, use_response_format=False
    ):
        # Keep async callers on the same Responses implementation and event rules.
        return await asyncio.to_thread(
            self.chat_stream, messages, tools, on_token, on_tool_chunk, use_response_format
        )
