from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx

from .llama_cpp import GenerationOptions


class OllamaError(RuntimeError):
    pass


class OllamaNotReadyError(OllamaError):
    pass


class OllamaProtocolError(OllamaError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaGenerationMetrics:
    request_id: str
    first_content_ms: float | None
    total_ms: float
    prompt_eval_count: int | None
    eval_count: int | None
    load_duration_ms: float | None
    prompt_eval_duration_ms: float | None
    eval_duration_ms: float | None


@dataclass(frozen=True, slots=True)
class OllamaConfig:
    model_name: str
    base_url: str = "http://127.0.0.1:11434"
    context_size: int = 8192
    keep_alive: str = "10m"
    request_timeout_seconds: float = 180.0
    disable_thinking: bool = True
    grammar_max_string_length: int = 1000
    stream_response: bool = False
    num_gpu: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise ValueError("model_name cannot be empty")
        if not isinstance(self.base_url, str) or not self.base_url.strip():
            raise ValueError("base_url cannot be empty")
        if self.context_size <= 0:
            raise ValueError("context_size must be positive")
        if not isinstance(self.keep_alive, str) or not self.keep_alive.strip():
            raise ValueError("keep_alive cannot be empty")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.grammar_max_string_length <= 0:
            raise ValueError("grammar_max_string_length must be positive")
        if self.num_gpu is not None and self.num_gpu < 0:
            raise ValueError("num_gpu must be non-negative")


class OllamaWorldMindBackend:
    def __init__(
        self,
        config: OllamaConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not isinstance(config, OllamaConfig):
            raise TypeError("config must be OllamaConfig")
        self.config = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._generation_lock = asyncio.Lock()
        self.last_generation_metrics: OllamaGenerationMetrics | None = None

    async def start(self) -> None:
        if self._client is not None:
            return
        timeout = httpx.Timeout(self.config.request_timeout_seconds)
        client = httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=timeout,
            transport=self._transport,
        )
        try:
            version_response = await client.get("/api/version")
            version_response.raise_for_status()
            tags_response = await client.get("/api/tags")
            tags_response.raise_for_status()
            tags = tags_response.json()
            names = {
                str(item.get("name", ""))
                for item in tags.get("models", [])
                if isinstance(item, dict)
            }
            expected = self.config.model_name
            if expected not in names and f"{expected}:latest" not in names:
                raise OllamaNotReadyError(
                    f"Ollama model is not installed: {self.config.model_name}"
                )
        except OllamaError:
            await client.aclose()
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            await client.aclose()
            raise OllamaNotReadyError("Ollama service is not ready") from error
        self._client = client

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()

    async def complete_chat(
        self,
        *,
        request_id: str,
        messages: Sequence[Mapping[str, str]],
        options: GenerationOptions,
        response_format: Mapping[str, object] | None = None,
    ) -> str:
        client = self._client
        if client is None:
            raise OllamaNotReadyError("Ollama backend is not started")
        normalized_messages = _normalize_messages(messages)
        body: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": normalized_messages,
            "stream": self.config.stream_response,
            "think": not self.config.disable_thinking,
            "keep_alive": self.config.keep_alive,
            "options": {
                "num_ctx": self.config.context_size,
                "num_predict": options.max_tokens,
                "temperature": options.temperature,
                "top_p": options.top_p,
                "repeat_penalty": options.repeat_penalty,
            },
        }
        if self.config.num_gpu is not None:
            body["options"]["num_gpu"] = self.config.num_gpu
        if options.seed is not None:
            body["options"]["seed"] = options.seed
        format_value = _ollama_format(
            response_format,
            max_string_length=self.config.grammar_max_string_length,
        )
        if format_value is not None:
            body["format"] = format_value
        started_ns = time.perf_counter_ns()
        try:
            async with self._generation_lock:
                if self.config.stream_response:
                    return await self._complete_streaming(
                        client,
                        request_id=request_id,
                        body=body,
                        started_ns=started_ns,
                    )
                response = await client.post(
                    "/api/chat",
                    json=body,
                    headers={"X-Request-ID": request_id},
                )
                response.raise_for_status()
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as error:
            response_text = error.response.text.strip()
            detail = response_text[:1000] if response_text else "empty response body"
            raise OllamaError(
                "Ollama chat request failed with "
                f"HTTP {error.response.status_code}: {detail}"
            ) from error
        except httpx.HTTPError as error:
            raise OllamaError(f"Ollama chat request failed: {error}") from error
        try:
            payload = response.json()
            message = payload["message"]
            content = message["content"]
        except (ValueError, KeyError, TypeError) as error:
            raise OllamaProtocolError("Ollama returned an invalid chat payload") from error
        if not isinstance(content, str) or not content.strip():
            raise OllamaProtocolError("Ollama returned empty chat content")
        self.last_generation_metrics = _generation_metrics(
            request_id,
            payload,
            started_ns=started_ns,
            first_content_ns=None,
        )
        return content

    async def _complete_streaming(
        self,
        client: httpx.AsyncClient,
        *,
        request_id: str,
        body: Mapping[str, Any],
        started_ns: int,
    ) -> str:
        chunks: list[str] = []
        first_content_ns: int | None = None
        terminal_payload: dict[str, Any] | None = None
        async with client.stream(
            "POST",
            "/api/chat",
            json=body,
            headers={"X-Request-ID": request_id},
        ) as response:
            if response.is_error:
                await response.aread()
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as error:
                    raise OllamaProtocolError(
                        "Ollama returned an invalid streaming payload"
                    ) from error
                if not isinstance(payload, dict):
                    raise OllamaProtocolError(
                        "Ollama streaming payload must be an object"
                    )
                terminal_payload = payload
                message = payload.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content:
                    if first_content_ns is None:
                        first_content_ns = time.perf_counter_ns()
                    chunks.append(content)
        text = "".join(chunks)
        if not text.strip():
            raise OllamaProtocolError("Ollama returned empty chat content")
        self.last_generation_metrics = _generation_metrics(
            request_id,
            terminal_payload or {},
            started_ns=started_ns,
            first_content_ns=first_content_ns,
        )
        return text


def _normalize_messages(
    messages: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise TypeError("messages must be a sequence")
    normalized = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise TypeError("message must be a mapping")
        role = message.get("role")
        content = message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise ValueError("unsupported message role")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("message content cannot be empty")
        normalized.append({"role": role, "content": content})
    if not normalized:
        raise ValueError("messages cannot be empty")
    return normalized


def _ollama_format(
    response_format: Mapping[str, object] | None,
    *,
    max_string_length: int,
) -> object | None:
    if response_format is None:
        return None
    if not isinstance(response_format, Mapping):
        raise TypeError("response_format must be a mapping")
    format_type = response_format.get("type")
    if format_type == "json_object":
        return "json"
    if format_type != "json_schema":
        raise ValueError("unsupported response_format type")
    wrapper = response_format.get("json_schema")
    if not isinstance(wrapper, Mapping):
        raise ValueError("json_schema response format is invalid")
    schema = wrapper.get("schema")
    if not isinstance(schema, Mapping):
        raise ValueError("json_schema schema must be an object")
    normalized = deepcopy(dict(schema))

    def clamp(value: object) -> None:
        if isinstance(value, dict):
            value.pop("pattern", None)
            current = value.get("maxLength")
            if isinstance(current, int) and current > max_string_length:
                value["maxLength"] = max_string_length
            for child in value.values():
                clamp(child)
        elif isinstance(value, list):
            for child in value:
                clamp(child)

    clamp(normalized)
    return normalized


def _generation_metrics(
    request_id: str,
    payload: Mapping[str, Any],
    *,
    started_ns: int,
    first_content_ns: int | None,
) -> OllamaGenerationMetrics:
    finished_ns = time.perf_counter_ns()
    return OllamaGenerationMetrics(
        request_id=request_id,
        first_content_ms=(
            None
            if first_content_ns is None
            else (first_content_ns - started_ns) / 1_000_000
        ),
        total_ms=(finished_ns - started_ns) / 1_000_000,
        prompt_eval_count=_optional_int(payload.get("prompt_eval_count")),
        eval_count=_optional_int(payload.get("eval_count")),
        load_duration_ms=_duration_ms(payload.get("load_duration")),
        prompt_eval_duration_ms=_duration_ms(payload.get("prompt_eval_duration")),
        eval_duration_ms=_duration_ms(payload.get("eval_duration")),
    )


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _duration_ms(value: object) -> float | None:
    if not isinstance(value, int) or value < 0:
        return None
    return value / 1_000_000
