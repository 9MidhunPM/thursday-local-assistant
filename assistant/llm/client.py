from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str | None
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class InferenceStats:
    """Performance metrics for a single inference request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    time_to_first_token: float | None = None  # seconds
    tokens_per_second: float | None = None
    total_time: float = 0.0
    backend: str = "llama.cpp"


@dataclass(frozen=True)
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall]
    raw: dict[str, Any]
    stats: InferenceStats


def _tc_field(tc: Any, field: str, default: Any = None) -> Any:
    """Read a field from a tool call that may be a ToolCall object or a dict."""
    if isinstance(tc, dict):
        return tc.get(field, default)
    return getattr(tc, field, default)


class LlamaCppClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
        timeout_sec: int,
        response_format: str | None,
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_sec = timeout_sec
        self._response_format = response_format

        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_sec, connect=5.0),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
            ),
        )
        self._async_client: httpx.AsyncClient | None = None

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_sec, connect=5.0),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._async_client

    def close(self) -> None:
        self._client.close()
        if self._async_client and not self._async_client.is_closed:
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(self._async_client.aclose())
            except RuntimeError:
                pass

    def __enter__(self) -> "LlamaCppClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "LlamaCppClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._client.close()
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def _build_payload(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        use_response_format: bool,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    **({"name": msg.name} if msg.name else {}),
                    **({"tool_call_id": msg.tool_call_id} if msg.tool_call_id else {}),
                    **(
                        {
                            "tool_calls": [
                                {
                                    "id": _tc_field(tc, "id", f"call_{i}"),
                                    "type": "function",
                                    "function": {
                                        "name": _tc_field(tc, "name", ""),
                                        "arguments": (
                                            lambda a: a
                                            if isinstance(a, str)
                                            else json.dumps(a)
                                        )(_tc_field(tc, "arguments", {})),
                                    },
                                }
                                for i, tc in enumerate(msg.tool_calls)
                            ]
                        }
                        if msg.tool_calls
                        else {}
                    ),
                }
                for msg in messages
            ],
            "temperature": self._temperature,
            "stream": stream,
        }
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self._response_format and use_response_format:
            payload["response_format"] = {"type": self._response_format}
        return payload

    def _parse_response(self, raw: dict[str, Any], elapsed: float = 0.0) -> LlmResponse:
        choice = raw["choices"][0]["message"]
        content = choice.get("content")
        tool_calls = []
        for call in choice.get("tool_calls", []) or []:
            arguments = call["function"].get("arguments")
            parsed_args: dict[str, Any]
            if isinstance(arguments, str):
                try:
                    parsed_args = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}
            else:
                parsed_args = arguments or {}
            tool_calls.append(
                ToolCall(
                    id=call.get("id", "call_0"),
                    name=call["function"]["name"],
                    arguments=parsed_args,
                )
            )
        usage = raw.get("usage", {})
        stats = InferenceStats(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            total_time=elapsed,
            tokens_per_second=usage.get("completion_tokens", 0) / elapsed if elapsed > 0 else 0,
            backend="llama.cpp",
        )
        return LlmResponse(content=content, tool_calls=tool_calls, raw=raw, stats=stats)

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        use_response_format: bool = True,
    ) -> LlmResponse:
        payload = self._build_payload(messages, tools, use_response_format)
        start = time.perf_counter()
        response = self._client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        elapsed = time.perf_counter() - start
        raw = response.json()
        return self._parse_response(raw, elapsed)

    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool_chunk: Callable[[str, str], None] | None = None,
        use_response_format: bool = False,
    ) -> LlmResponse:
        payload = self._build_payload(messages, tools, use_response_format, stream=True)

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        saw_tool_calls = False
        first_token_time: float | None = None
        start = time.perf_counter()

        with self._client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if "tool_calls" in delta:
                    saw_tool_calls = True
                    for call in delta["tool_calls"]:
                        index = call.get("index", 0)
                        entry = tool_calls.setdefault(
                            index, {"id": None, "name": None, "arguments": ""}
                        )
                        if "id" in call:
                            entry["id"] = call["id"]
                        function = call.get("function", {})
                        if "name" in function:
                            entry["name"] = function["name"]
                            if on_tool_chunk:
                                on_tool_chunk(entry["name"], "")
                        if "arguments" in function:
                            args_chunk = function["arguments"]
                            entry["arguments"] += args_chunk
                            if on_tool_chunk and entry["name"]:
                                on_tool_chunk(entry["name"], args_chunk)
                if "content" in delta and not saw_tool_calls:
                    chunk_text = delta["content"]
                    if chunk_text:
                        if first_token_time is None:
                            first_token_time = time.perf_counter() - start
                        content_parts.append(chunk_text)
                        if on_token:
                            on_token(chunk_text)

        elapsed = time.perf_counter() - start
        content = "".join(content_parts) if content_parts else None
        parsed_tool_calls: list[ToolCall] = []
        for index in sorted(tool_calls.keys()):
            entry = tool_calls[index]
            args_str = entry["arguments"]
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parsed_tool_calls.append(
                ToolCall(id=entry.get("id", f"call_{index}"), name=entry["name"], arguments=args)
            )

        stats = InferenceStats(
            prompt_tokens=0,
            completion_tokens=len(content_parts) if content_parts else 0,
            total_tokens=0,
            time_to_first_token=first_token_time,
            tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
            total_time=elapsed,
            backend="llama.cpp",
        )
        return LlmResponse(content=content, tool_calls=parsed_tool_calls, raw={}, stats=stats)

    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        use_response_format: bool = True,
    ) -> LlmResponse:
        payload = self._build_payload(messages, tools, use_response_format)
        client = self._get_async_client()
        start = time.perf_counter()
        response = await client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        elapsed = time.perf_counter() - start
        raw = response.json()
        return self._parse_response(raw, elapsed)

    async def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool_chunk: Callable[[str, str], None] | None = None,
        use_response_format: bool = False,
    ) -> LlmResponse:
        payload = self._build_payload(messages, tools, use_response_format, stream=True)

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        saw_tool_calls = False
        first_token_time: float | None = None
        start = time.perf_counter()

        client = self._get_async_client()
        async with client.stream(
            "POST",
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue

                delta = chunk.get("choices", [{}])[0].get("delta", {})
                if "tool_calls" in delta:
                    saw_tool_calls = True
                    for call in delta["tool_calls"]:
                        index = call.get("index", 0)
                        entry = tool_calls.setdefault(
                            index, {"id": None, "name": None, "arguments": ""}
                        )
                        if "id" in call:
                            entry["id"] = call["id"]
                        function = call.get("function", {})
                        if "name" in function:
                            entry["name"] = function["name"]
                            if on_tool_chunk:
                                on_tool_chunk(entry["name"], "")
                        if "arguments" in function:
                            args_chunk = function["arguments"]
                            entry["arguments"] += args_chunk
                            if on_tool_chunk and entry["name"]:
                                on_tool_chunk(entry["name"], args_chunk)
                if "content" in delta and not saw_tool_calls:
                    chunk_text = delta["content"]
                    if chunk_text:
                        if first_token_time is None:
                            first_token_time = time.perf_counter() - start
                        content_parts.append(chunk_text)
                        if on_token:
                            on_token(chunk_text)

        elapsed = time.perf_counter() - start
        content = "".join(content_parts) if content_parts else None
        parsed_tool_calls: list[ToolCall] = []
        for index in sorted(tool_calls.keys()):
            entry = tool_calls[index]
            args_str = entry["arguments"]
            try:
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parsed_tool_calls.append(
                ToolCall(id=entry.get("id", f"call_{index}"), name=entry["name"], arguments=args)
            )

        stats = InferenceStats(
            prompt_tokens=0,
            completion_tokens=len(content_parts) if content_parts else 0,
            total_tokens=0,
            time_to_first_token=first_token_time,
            tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
            total_time=elapsed,
            backend="llama.cpp",
        )
        return LlmResponse(content=content, tool_calls=parsed_tool_calls, raw={}, stats=stats)

    def health_check(self) -> dict[str, Any]:
        try:
            resp = self._client.get(f"{self._base_url}/health", timeout=2.0)
            return {"status": "healthy" if resp.status_code == 200 else "unhealthy", "backend": "llama.cpp"}
        except Exception as e:
            return {"status": "error", "backend": "llama.cpp", "error": str(e)}