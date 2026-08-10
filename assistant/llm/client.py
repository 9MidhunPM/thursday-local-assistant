from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

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
    backend: str = "openai-compatible"


@dataclass(frozen=True)
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall]
    raw: dict[str, Any]
    stats: InferenceStats


# Provider presets: base_url, default model, auth header style.
PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "local": {
        "base_url": "http://127.0.0.1:8080",
        "model": "local-model",
        "api_key_env": "",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "model": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "api_key_env": "TOGETHER_API_KEY",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "custom": {
        "base_url": "http://127.0.0.1:8080",
        "model": "custom-model",
        "api_key_env": "LLM_API_KEY",
    },
}


def _tc_field(tc: Any, field: str, default: Any = None) -> Any:
    """Read a field from a tool call that may be a ToolCall object or a dict."""
    if isinstance(tc, dict):
        return tc.get(field, default)
    return getattr(tc, field, default)


def resolve_provider_settings(
    provider: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Resolve LLM provider from env + explicit overrides.

    Priority:
      1. Explicit function args
      2. Env: LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY (+ provider keys)
      3. Local llama via LLAMA_HOST / LLAMA_PORT when provider is local
    """
    raw_provider = (provider or os.getenv("LLM_PROVIDER") or "local").strip().lower()
    if raw_provider in {"llama", "llamacpp", "llama.cpp"}:
        raw_provider = "local"

    preset = PROVIDER_PRESETS.get(raw_provider, PROVIDER_PRESETS["custom"])
    resolved_provider = raw_provider if raw_provider in PROVIDER_PRESETS else "custom"

    # Base URL
    env_base = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if base_url:
        resolved_url = base_url.rstrip("/")
    elif env_base:
        resolved_url = env_base.rstrip("/")
    elif resolved_provider == "local":
        host = os.getenv("LLAMA_HOST", "127.0.0.1")
        port = os.getenv("LLAMA_PORT", "8080")
        resolved_url = f"http://{host}:{port}"
    else:
        resolved_url = preset["base_url"].rstrip("/")

    # Ensure .../v1 for known cloud APIs if user passed host only
    if resolved_provider != "local" and not resolved_url.rstrip("/").endswith("/v1"):
        # Local llama.cpp often uses /v1/chat/completions on the root server.
        # Cloud OpenAI-compat usually expects /v1.
        if "localhost" not in resolved_url and "127.0.0.1" not in resolved_url:
            if "/api/v1" not in resolved_url:
                resolved_url = resolved_url.rstrip("/") + "/v1"

    # Model
    resolved_model = (
        model
        or os.getenv("LLM_MODEL")
        or os.getenv("OPENAI_MODEL")
        or preset["model"]
    )

    # API key
    key_env = preset.get("api_key_env") or "LLM_API_KEY"
    resolved_key = (
        api_key
        or os.getenv("LLM_API_KEY")
        or (os.getenv(key_env) if key_env else None)
        or os.getenv("OPENAI_API_KEY")
        or ""
    )

    is_local = resolved_provider == "local" or _is_loopback_url(resolved_url)
    return {
        "provider": resolved_provider,
        "base_url": resolved_url,
        "model": resolved_model,
        "api_key": resolved_key,
        "is_local": is_local,
    }


def _is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "::1"}


class OpenAICompatibleClient:
    """OpenAI-compatible chat client (local llama.cpp, OpenAI, OpenRouter, Groq, …)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float,
        max_tokens: int | None,
        timeout_sec: int,
        response_format: str | None,
        api_key: str | None = None,
        provider: str = "local",
        max_connections: int = 10,
        max_keepalive_connections: int = 5,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout_sec = timeout_sec
        self._response_format = response_format
        self._api_key = (api_key or "").strip()
        self._provider = provider
        self._backend_name = provider if provider != "local" else "llama.cpp"

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if provider == "openrouter":
            headers.setdefault("HTTP-Referer", "https://github.com/thursday-ai/thursday")
            headers.setdefault("X-Title", "Thursday AI Assistant")
        if extra_headers:
            headers.update(extra_headers)

        self._headers = headers
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_sec, connect=10.0),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive_connections,
            ),
            headers=headers,
        )
        self._async_client: httpx.AsyncClient | None = None

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def is_local(self) -> bool:
        return self._provider == "local" or _is_loopback_url(self._base_url)

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_sec, connect=10.0),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
                headers=self._headers,
            )
        return self._async_client

    def close(self) -> None:
        self._client.close()
        if self._async_client and not self._async_client.is_closed:
            import asyncio

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._async_client.aclose())
                else:
                    loop.run_until_complete(self._async_client.aclose())
            except RuntimeError:
                pass

    def __enter__(self) -> OpenAICompatibleClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> OpenAICompatibleClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        self._client.close()
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

    def _chat_url(self) -> str:
        base = self._base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _build_payload(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None,
        use_response_format: bool,
        stream: bool = False,
    ) -> dict[str, Any]:
        openai_reasoning_model = self._provider == "openai" and (
            str(self._model).startswith("o") or str(self._model).startswith("gpt-5")
        )
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
            "stream": stream,
        }
        # GPT-5 and o-series Chat Completions requests only accept the default
        # temperature. Omitting it preserves that default; sending the app's
        # legacy 0.2 setting produces a 400 response.
        if not openai_reasoning_model:
            payload["temperature"] = self._temperature
        if self._max_tokens is not None:
            # OpenAI reasoning models and the GPT-5 family use this parameter;
            # most OpenAI-compatible servers still expect max_tokens.
            if openai_reasoning_model:
                payload["max_completion_tokens"] = self._max_tokens
            else:
                payload["max_tokens"] = self._max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if self._response_format and use_response_format:
            payload["response_format"] = {"type": self._response_format}
        # GPT-5.6 Luna supports Chat Completions function tools only with
        # reasoning disabled. Thursday keeps its own tool-execution loop, so
        # the Responses API is not required for this compatibility path.
        if self._provider == "openai" and str(self._model).startswith("gpt-5") and tools:
            payload["reasoning_effort"] = "none"
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
        completion = usage.get("completion_tokens", 0)
        stats = InferenceStats(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=completion,
            total_tokens=usage.get("total_tokens", 0),
            total_time=elapsed,
            tokens_per_second=completion / elapsed if elapsed > 0 and completion else 0,
            backend=self._backend_name,
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
        response = self._client.post(self._chat_url(), json=payload)
        response.raise_for_status()
        elapsed = time.perf_counter() - start
        return self._parse_response(response.json(), elapsed)

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

        with self._client.stream("POST", self._chat_url(), json=payload) as response:
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
                ToolCall(
                    id=entry.get("id", f"call_{index}"),
                    name=entry["name"],
                    arguments=args,
                )
            )

        stats = InferenceStats(
            prompt_tokens=0,
            completion_tokens=len(content_parts) if content_parts else 0,
            total_tokens=0,
            time_to_first_token=first_token_time,
            tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
            total_time=elapsed,
            backend=self._backend_name,
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
        response = await client.post(self._chat_url(), json=payload)
        response.raise_for_status()
        elapsed = time.perf_counter() - start
        return self._parse_response(response.json(), elapsed)

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
        async with client.stream("POST", self._chat_url(), json=payload) as response:
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
                ToolCall(
                    id=entry.get("id", f"call_{index}"),
                    name=entry["name"],
                    arguments=args,
                )
            )

        stats = InferenceStats(
            prompt_tokens=0,
            completion_tokens=len(content_parts) if content_parts else 0,
            total_tokens=0,
            time_to_first_token=first_token_time,
            tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
            total_time=elapsed,
            backend=self._backend_name,
        )
        return LlmResponse(content=content, tool_calls=parsed_tool_calls, raw={}, stats=stats)

    def health_check(self) -> dict[str, Any]:
        """Check backend readiness. Cloud providers are ready if a key is set."""
        if not self.is_local:
            if self._api_key:
                return {
                    "status": "healthy",
                    "backend": self._backend_name,
                    "model": self._model,
                    "mode": "api",
                }
            return {
                "status": "error",
                "backend": self._backend_name,
                "error": "API key missing",
                "mode": "api",
            }
        try:
            # llama.cpp exposes /health; some servers only have /v1/models
            for path in ("/health", "/v1/models", "/models"):
                try:
                    resp = self._client.get(f"{self._base_url}{path}", timeout=2.0)
                    if resp.status_code == 200:
                        return {
                            "status": "healthy",
                            "backend": self._backend_name,
                            "model": self._model,
                            "mode": "local",
                        }
                except Exception:
                    continue
            return {
                "status": "unhealthy",
                "backend": self._backend_name,
                "mode": "local",
            }
        except Exception as e:
            return {
                "status": "error",
                "backend": self._backend_name,
                "error": str(e),
                "mode": "local",
            }


# Backward-compatible alias used throughout the codebase.
LlamaCppClient = OpenAICompatibleClient


def build_llm_client(
    *,
    base_url: str,
    model: str,
    temperature: float,
    max_tokens: int | None,
    timeout_sec: int,
    response_format: str | None,
    provider: str | None = None,
    api_key: str | None = None,
) -> OpenAICompatibleClient:
    """Factory: merge config + env and construct the right client."""
    settings = resolve_provider_settings(
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
    )
    # Prefer explicit config model when env did not override and provider is local.
    final_model = settings["model"]
    if settings["provider"] == "local" and not os.getenv("LLM_MODEL"):
        final_model = model or settings["model"]
    final_url = settings["base_url"]
    # Config base_url wins for local when LLM_BASE_URL not set and LLAMA_* not set.
    if settings["provider"] == "local" and not os.getenv("LLM_BASE_URL"):
        if not (os.getenv("LLAMA_HOST") or os.getenv("LLAMA_PORT")):
            final_url = base_url.rstrip("/")

    return OpenAICompatibleClient(
        base_url=final_url,
        model=final_model,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_sec=timeout_sec,
        response_format=response_format,
        api_key=settings["api_key"],
        provider=settings["provider"],
    )
