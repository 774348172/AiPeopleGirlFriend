from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from runtime.adapters.llama_cpp import GenerationOptions
from runtime.adapters.ollama import OllamaConfig, OllamaError, OllamaWorldMindBackend


def test_ollama_backend_maps_openai_json_schema_to_native_format() -> None:
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qinweixi-qwen35:latest"}]},
            )
        payload = json.loads(request.content)
        captured.append((request, payload))
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": '{"ok":true}'},
                "done": True,
            },
        )

    async def scenario() -> None:
        backend = OllamaWorldMindBackend(
            OllamaConfig(model_name="qinweixi-qwen35"),
            transport=httpx.MockTransport(handler),
        )
        await backend.start()
        try:
            result = await backend.complete_chat(
                request_id="ollama-test",
                messages=(
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": "user"},
                ),
                options=GenerationOptions(100, 0.2, 0.8, 1.05),
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "test_v1",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["ok", "text"],
                            "properties": {
                                "ok": {"type": "boolean"},
                                "text": {
                                    "type": "string",
                                    "maxLength": 2000,
                                    "pattern": "^\\S+$",
                                },
                            },
                        },
                    },
                },
            )
            assert result == '{"ok":true}'
        finally:
            await backend.close()

    asyncio.run(scenario())
    request, payload = captured[0]
    assert request.headers["X-Request-ID"] == "ollama-test"
    assert payload["model"] == "qinweixi-qwen35"
    assert payload["think"] is False
    assert payload["stream"] is False
    assert payload["options"]["num_ctx"] == 8192
    assert payload["format"]["properties"]["ok"]["type"] == "boolean"
    assert payload["format"]["properties"]["text"]["maxLength"] == 1000
    assert "pattern" not in payload["format"]["properties"]["text"]


def test_ollama_backend_preserves_http_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qinweixi-qwen35:latest"}]},
            )
        return httpx.Response(400, text="invalid structured output schema")

    async def scenario() -> None:
        backend = OllamaWorldMindBackend(
            OllamaConfig(model_name="qinweixi-qwen35"),
            transport=httpx.MockTransport(handler),
        )
        await backend.start()
        try:
            with pytest.raises(
                OllamaError,
                match="HTTP 400: invalid structured output schema",
            ):
                await backend.complete_chat(
                    request_id="ollama-error",
                    messages=({"role": "user", "content": "test"},),
                    options=GenerationOptions(20, 0.2, 0.8, 1.05),
                )
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_streaming_ollama_backend_preserves_http_error_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qinweixi-qwen35:latest"}]},
            )
        return httpx.Response(400, text="invalid streaming structured output schema")

    async def scenario() -> None:
        backend = OllamaWorldMindBackend(
            OllamaConfig(
                model_name="qinweixi-qwen35",
                stream_response=True,
            ),
            transport=httpx.MockTransport(handler),
        )
        await backend.start()
        try:
            with pytest.raises(
                OllamaError,
                match="HTTP 400: invalid streaming structured output schema",
            ):
                await backend.complete_chat(
                    request_id="ollama-stream-error",
                    messages=({"role": "user", "content": "test"},),
                    options=GenerationOptions(20, 0.2, 0.8, 1.05),
                )
        finally:
            await backend.close()

    asyncio.run(scenario())


def test_ollama_backend_streams_and_records_generation_metrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "test"})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qinweixi-qwen35:latest"}]},
            )
        payload = json.loads(request.content)
        assert payload["stream"] is True
        lines = (
            '{"message":{"content":"{\\"ok\\":"},"done":false}\n'
            '{"message":{"content":"true}"},"done":false}\n'
            '{"message":{"content":""},"done":true,"prompt_eval_count":12,'
            '"eval_count":4,"load_duration":1000000,'
            '"prompt_eval_duration":2000000,"eval_duration":3000000}\n'
        )
        return httpx.Response(200, content=lines.encode("utf-8"))

    async def scenario() -> None:
        backend = OllamaWorldMindBackend(
            OllamaConfig(model_name="qinweixi-qwen35", stream_response=True),
            transport=httpx.MockTransport(handler),
        )
        await backend.start()
        try:
            result = await backend.complete_chat(
                request_id="ollama-stream-test",
                messages=({"role": "user", "content": "test"},),
                options=GenerationOptions(20, 0.2, 0.8, 1.05),
            )
            assert result == '{"ok":true}'
            metrics = backend.last_generation_metrics
            assert metrics is not None
            assert metrics.request_id == "ollama-stream-test"
            assert metrics.first_content_ms is not None
            assert metrics.total_ms >= metrics.first_content_ms
            assert metrics.prompt_eval_count == 12
            assert metrics.eval_count == 4
            assert metrics.load_duration_ms == 1.0
            assert metrics.prompt_eval_duration_ms == 2.0
            assert metrics.eval_duration_ms == 3.0
        finally:
            await backend.close()

    asyncio.run(scenario())
