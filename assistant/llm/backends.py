from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterator, List, Optional

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
class LlmResponse:
    content: str | None
    tool_calls: list[ToolCall]
    raw: dict[str, Any]
    stats: "InferenceStats"


@dataclass(frozen=True)
class InferenceStats:
    """Performance metrics for a single inference request."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    time_to_first_token: float | None = None  # seconds
    tokens_per_second: float | None = None
    total_time: float = 0.0
    backend: str = "unknown"


def _tc_field(tc: Any, field: str, default: Any = None) -> Any:
    """Read a field from a tool call that may be a ToolCall object or a dict."""
    if isinstance(tc, dict):
        return tc.get(field, default)
    return getattr(tc, field, default)


class LLMBackend(abc.ABC):
    """Abstract base class for LLM backends."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Backend identifier (e.g., 'llama.cpp', 'ipex-llm')."""
        pass

    @abc.abstractmethod
    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        use_response_format: bool = True,
        **kwargs,
    ) -> LlmResponse:
        """Non-streaming chat completion."""
        pass

    @abc.abstractmethod
    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        use_response_format: bool = False,
        **kwargs,
    ) -> LlmResponse:
        """Streaming chat completion with optional callbacks."""
        pass

    @abc.abstractmethod
    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LlmResponse:
        """Async non-streaming chat completion."""
        pass

    @abc.abstractmethod
    async def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        **kwargs,
    ) -> LlmResponse:
        """Async streaming chat completion."""
        pass

    @abc.abstractmethod
    def health_check(self) -> dict[str, Any]:
        """Return backend health status."""
        pass

    @abc.abstractmethod
    def close(self) -> None:
        """Cleanup resources."""
        pass


class LlamaCppBackend(LLMBackend):
    """llama.cpp HTTP server backend (current implementation)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout_sec: int = 60,
        response_format: str | None = None,
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

    @property
    def name(self) -> str:
        return "llama.cpp"

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
                                            else str(a)
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

    def _parse_response(self, raw: dict[str, Any]) -> LlmResponse:
        choice = raw["choices"][0]["message"]
        content = choice.get("content")
        tool_calls = []
        for call in choice.get("tool_calls", []) or []:
            arguments = call["function"].get("arguments")
            parsed_args: dict[str, Any]
            if isinstance(arguments, str):
                try:
                    import json
                    parsed_args = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}
            else:
                parsed_args = arguments or {}
            tool_calls.append(
                ToolCall(
                    name=call["function"]["name"],
                    arguments=parsed_args,
                )
            )
        usage = raw.get("usage", {})
        return LlmResponse(
            content=content,
            tool_calls=tool_calls,
            raw=raw,
            stats=InferenceStats(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
                backend=self.name,
            ),
        )

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        use_response_format: bool = True,
        **kwargs,
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
        result = self._parse_response(raw)
        return LlmResponse(
            content=result.content,
            tool_calls=result.tool_calls,
            raw=result.raw,
            stats=InferenceStats(
                prompt_tokens=result.stats.prompt_tokens,
                completion_tokens=result.stats.completion_tokens,
                total_tokens=result.stats.total_tokens,
                total_time=elapsed,
                tokens_per_second=result.stats.completion_tokens / elapsed if elapsed > 0 else 0,
                backend=self.name,
            ),
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        use_response_format: bool = False,
        **kwargs,
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
                    import json
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
                import json
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parsed_tool_calls.append(
                ToolCall(id=entry.get("id", f"call_{index}"), name=entry["name"], arguments=args)
            )

        return LlmResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            raw={},
            stats=InferenceStats(
                prompt_tokens=0,  # Not available in streaming
                completion_tokens=len(content_parts) if content_parts else 0,
                total_tokens=0,
                time_to_first_token=first_token_time,
                tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
                total_time=elapsed,
                backend=self.name,
            ),
        )

    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LlmResponse:
        client = self._get_async_client()
        payload = self._build_payload(messages, tools, True)
        start = time.perf_counter()
        response = await client.post(
            f"{self._base_url}/v1/chat/completions",
            json=payload,
        )
        response.raise_for_status()
        elapsed = time.perf_counter() - start
        raw = response.json()
        result = self._parse_response(raw)
        return LlmResponse(
            content=result.content,
            tool_calls=result.tool_calls,
            raw=result.raw,
            stats=InferenceStats(
                prompt_tokens=result.stats.prompt_tokens,
                completion_tokens=result.stats.completion_tokens,
                total_tokens=result.stats.total_tokens,
                total_time=elapsed,
                tokens_per_second=result.stats.completion_tokens / elapsed if elapsed > 0 else 0,
                backend=self.name,
            ),
        )

    async def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        **kwargs,
    ) -> LlmResponse:
        client = self._get_async_client()
        payload = self._build_payload(messages, tools, True, stream=True)

        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        saw_tool_calls = False
        first_token_time: float | None = None
        start = time.perf_counter()

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
                    import json
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
                import json
                args = json.loads(args_str) if args_str else {}
            except json.JSONDecodeError:
                args = {}
            parsed_tool_calls.append(
                ToolCall(id=entry.get("id", f"call_{index}"), name=entry["name"], arguments=args)
            )

        return LlmResponse(
            content=content,
            tool_calls=parsed_tool_calls,
            raw={},
            stats=InferenceStats(
                prompt_tokens=0,
                completion_tokens=len(content_parts) if content_parts else 0,
                total_tokens=0,
                time_to_first_token=first_token_time,
                tokens_per_second=len(content_parts) / elapsed if elapsed > 0 else 0,
                total_time=elapsed,
                backend=self.name,
            ),
        )

    def health_check(self) -> dict[str, Any]:
        try:
            resp = self._client.get(f"{self._base_url}/health", timeout=2.0)
            return {"status": "healthy" if resp.status_code == 200 else "unhealthy", "backend": self.name}
        except Exception as e:
            return {"status": "error", "backend": self.name, "error": str(e)}

    def close(self) -> None:
        self._client.close()
        if self._async_client and not self._async_client.is_closed:
            import asyncio
            try:
                asyncio.get_event_loop().run_until_complete(self._async_client.aclose())
            except RuntimeError:
                pass

    def __enter__(self) -> "LlamaCppBackend":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    async def __aenter__(self) -> "LlamaCppBackend":
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()


class IPEXLLMBackend(LLMBackend):
    """IPEX-LLM backend (Intel-optimized PyTorch). Requires compatible transformers version."""

    def __init__(
        self,
        model_path: str,
        device: str = "xpu",
        load_in_4bit: bool = True,
        **kwargs,
    ) -> None:
        self._model_path = model_path
        self._device = device
        self._load_in_4bit = load_in_4bit
        self._model = None
        self._tokenizer = None

    @property
    def name(self) -> str:
        return "ipex-llm"

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from ipex_llm import optimize_model
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch

            self._tokenizer = AutoTokenizer.from_pretrained(self._model_path, trust_remote_code=True)

            self._model = AutoModelForCausalLM.from_pretrained(
                self._model_path,
                torch_dtype=torch.float16,
                load_in_4bit=self._load_in_4bit,
                device_map=self._device,
                trust_remote_code=True,
            )
            self._model = optimize_model(self._model, device=self._device)
            self._model.eval()
        except Exception as e:
            raise RuntimeError(f"Failed to load IPEX-LLM model: {e}")

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        use_response_format: bool = True,
        **kwargs,
    ) -> LlmResponse:
        self._load_model()
        import torch
        from transformers import TextIteratorStreamer
        import threading

        # Convert messages to chat format
        prompt = self._tokenizer.apply_chat_template(
            [{"role": m.role, "content": m.content} for m in messages],
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        start = time.perf_counter()

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=kwargs.get("max_tokens", 512),
                temperature=kwargs.get("temperature", 0.2),
                do_sample=kwargs.get("temperature", 0.2) > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        elapsed = time.perf_counter() - start
        generated = outputs[0][inputs.input_ids.shape[1]:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)

        prompt_tokens = inputs.input_ids.shape[1]
        completion_tokens = generated.shape[0]

        return LlmResponse(
            content=text,
            tool_calls=[],
            raw={},
            stats=InferenceStats(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                total_time=elapsed,
                tokens_per_second=completion_tokens / elapsed if elapsed > 0 else 0,
                backend=self.name,
            ),
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        use_response_format: bool = False,
        **kwargs,
    ) -> LlmResponse:
        self._load_model()
        import torch
        from transformers import TextIteratorStreamer
        import threading

        prompt = self._tokenizer.apply_chat_template(
            [{"role": m.role, "content": m.content} for m in messages],
            tokenize=False,
            add_generation_prompt=True,
        )

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        streamer = TextIteratorStreamer(self._tokenizer, skip_special_tokens=True)

        generation_kwargs = dict(
            **inputs,
            max_new_tokens=kwargs.get("max_tokens", 512),
            temperature=kwargs.get("temperature", 0.2),
            do_sample=kwargs.get("temperature", 0.2) > 0,
            pad_token_id=self._tokenizer.eos_token_id,
            streamer=streamer,
        )

        start = time.perf_counter()
        first_token_time: float | None = None
        content_parts: list[str] = []

        thread = threading.Thread(target=self._model.generate, kwargs=generation_kwargs)
        thread.start()

        for token in streamer:
            if token:
                if first_token_time is None:
                    first_token_time = time.perf_counter() - start
                content_parts.append(token)
                if on_token:
                    on_token(token)

        thread.join()
        elapsed = time.perf_counter() - start

        content = "".join(content_parts)
        prompt_tokens = inputs.input_ids.shape[1]
        completion_tokens = len(content_parts)

        return LlmResponse(
            content=content,
            tool_calls=[],
            raw={},
            stats=InferenceStats(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                time_to_first_token=first_token_time,
                tokens_per_second=completion_tokens / elapsed if elapsed > 0 else 0,
                total_time=elapsed,
                backend=self.name,
            ),
        )

    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs,
    ) -> LlmResponse:
        # Run sync version in executor for now
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(None, self.chat, messages, tools, True, **kwargs)

    async def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]] | None = None,
        on_token: Optional[callable] = None,
        on_tool_chunk: Optional[callable] = None,
        **kwargs,
    ) -> LlmResponse:
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, self.chat_stream, messages, tools, on_token, on_tool_chunk, True, **kwargs
        )

    def health_check(self) -> dict[str, Any]:
        try:
            self._load_model()
            return {"status": "healthy", "backend": self.name, "device": self._device}
        except Exception as e:
            return {"status": "error", "backend": self.name, "error": str(e)}

    def close(self) -> None:
        if self._model is not None:
            import torch
            del self._model
            del self._tokenizer
            torch.xpu.empty_cache() if hasattr(torch, "xpu") else None
            self._model = None
            self._tokenizer = None


def create_backend(backend_type: str, **kwargs) -> LLMBackend:
    """Factory function to create backend instances."""
    if backend_type == "llama.cpp":
        return LlamaCppBackend(**kwargs)
    elif backend_type == "ipex-llm":
        return IPEXLLMBackend(**kwargs)
    else:
        raise ValueError(f"Unknown backend: {backend_type}")