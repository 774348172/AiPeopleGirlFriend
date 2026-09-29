from __future__ import annotations

import asyncio
import threading
import sys
from types import SimpleNamespace
import pytest

import runtime.adapters.gemma_nf4 as gemma_nf4_module
from runtime.adapters.gemma_nf4 import (
    GemmaNF4Config,
    GemmaNF4GenerationResult,
    GemmaNF4WorldMindBackend,
)
from runtime.adapters.llama_cpp import GenerationOptions

from tests.real_select_e2e.test_gemma_nf4_assets import make_asset
from runtime.gemma_nf4_assets import LocalGemmaNF4Package


@pytest.mark.parametrize("fails", [False, True])
def test_workspace_release_happens_after_worker_even_on_failure(monkeypatch, fails):
    events = []
    cuda = SimpleNamespace(
        reset_peak_memory_stats=lambda: events.append("reset"),
        empty_cache=lambda: events.append("release"),
        memory_allocated=lambda: 1, memory_reserved=lambda: 2,
        max_memory_allocated=lambda: 3, max_memory_reserved=lambda: 4,
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))

    @gemma_nf4_module._bounded_cuda_workspace
    def work():
        events.append("worker")
        if fails:
            raise RuntimeError("generation failed")
        return "reply"

    if fails:
        with pytest.raises(RuntimeError, match="generation failed"):
            work()
    else:
        assert work() == "reply"
    assert events == ["reset", "worker", "release"]


def test_gemma_nf4_backend_is_lazy_serial_and_schema_aware(tmp_path) -> None:
    async def scenario() -> None:
        asset = LocalGemmaNF4Package.load(make_asset(tmp_path))
        loaded = []
        generated = []
        model = object()
        tokenizer = object()

        def loader(config):
            loaded.append(config)
            return model, tokenizer

        def runner(actual_model, actual_tokenizer, messages, options):
            generated.append((actual_model, actual_tokenizer, messages, options))
            return GemmaNF4GenerationResult("{\"c\":[]}", 12.5, 120, 8)

        backend = GemmaNF4WorldMindBackend(
            GemmaNF4Config(asset), loader=loader, generation_runner=runner
        )
        assert backend.state == "stopped"
        await backend.start()
        assert backend.state == "ready"
        result = await backend.complete_chat(
            request_id="request:MIND_PATCH_V2:0",
            messages=(
                {"role": "system", "content": "只输出JSON。"},
                {"role": "user", "content": "{}"},
            ),
            options=GenerationOptions(32, 0.0, 0.9, 1.05),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "test",
                    "strict": True,
                    "schema": {"type": "object", "properties": {"c": {"type": "array"}}},
                },
            },
        )
        assert result == '{"c":[]}'
        assert len(loaded) == 1
        assert generated[0][0:2] == (model, tokenizer)
        assert "[机器可读输出约束]" in generated[0][2][0]["content"]
        assert backend.last_generation_metrics.first_visible_ms == 12.5
        assert backend.last_generation_metrics.prompt_tokens == 120
        await backend.close()
        assert backend.state == "stopped"

    asyncio.run(scenario())


def test_gemma_nf4_timeout_does_not_release_lock_while_gpu_thread_runs(tmp_path) -> None:
    async def scenario() -> None:
        asset = LocalGemmaNF4Package.load(make_asset(tmp_path))
        runner_started = threading.Event()
        release_runner = threading.Event()
        runner_calls = 0

        def runner(actual_model, actual_tokenizer, messages, options):
            nonlocal runner_calls
            runner_calls += 1
            if runner_calls == 1:
                runner_started.set()
                release_runner.wait(timeout=2)
            return GemmaNF4GenerationResult("完成", 1.0, 10, 1)

        backend = GemmaNF4WorldMindBackend(
            GemmaNF4Config(asset, request_timeout_seconds=0.02),
            loader=lambda _: (object(), object()),
            generation_runner=runner,
        )
        await backend.start()
        first = asyncio.create_task(
            backend.complete_chat(
                request_id="first",
                messages=({"role": "user", "content": "第一条"},),
                options=GenerationOptions(8, 0.0, 0.9, 1.0),
            )
        )
        await asyncio.to_thread(runner_started.wait, 1)
        try:
            await first
        except Exception as error:
            assert "timed out" in str(error)
        else:
            raise AssertionError("the first request should time out")

        second = asyncio.create_task(
            backend.complete_chat(
                request_id="second",
                messages=({"role": "user", "content": "第二条"},),
                options=GenerationOptions(8, 0.0, 0.9, 1.0),
            )
        )
        await asyncio.sleep(0.03)
        assert runner_calls == 1
        release_runner.set()
        await second
        assert runner_calls == 2
        await backend.close()

    asyncio.run(scenario())


def test_gemma_nf4_stream_chat_forwards_chunks_before_completion(tmp_path) -> None:
    async def scenario() -> None:
        asset = LocalGemmaNF4Package.load(make_asset(tmp_path))
        original = gemma_nf4_module._generate_stream

        def runner(model, tokenizer, messages, options, emit):
            emit("你")
            emit("好")
            return GemmaNF4GenerationResult("你好", 1.0, 3, 2)

        gemma_nf4_module._generate_stream = runner
        try:
            backend = GemmaNF4WorldMindBackend(
                GemmaNF4Config(asset),
                loader=lambda _: (object(), object()),
                generation_runner=lambda *args: GemmaNF4GenerationResult(
                    "warm", 1.0, 1, 1
                ),
            )
            await backend.start()
            chunks: list[str] = []
            result = await backend.stream_chat(
                request_id="stream",
                messages=({"role": "user", "content": "你好"},),
                options=GenerationOptions(8, 0.0, 0.9, 1.0),
                on_delta=chunks.append,
            )
            assert result == "你好"
            assert chunks == ["你", "好"]
            await backend.close()
        finally:
            gemma_nf4_module._generate_stream = original

    asyncio.run(scenario())


def test_gemma_nf4_default_start_runs_short_prewarm(tmp_path, monkeypatch) -> None:
    asset = LocalGemmaNF4Package.load(make_asset(tmp_path))
    calls: list[tuple[object, object, object, GenerationOptions]] = []

    def loader(config):
        return object(), object()

    def runner(model, tokenizer, messages, options):
        calls.append((model, tokenizer, messages, options))
        return GemmaNF4GenerationResult("好", 1.0, 2, 1)

    monkeypatch.setattr(gemma_nf4_module, "_load_model", loader)
    monkeypatch.setattr(gemma_nf4_module, "_generate", runner)

    async def scenario() -> None:
        backend = GemmaNF4WorldMindBackend(GemmaNF4Config(asset))
        await backend.start()
        assert len(calls) == 1
        assert calls[0][2] == ({"role": "user", "content": "你好"},)
        assert calls[0][3].max_tokens == 4
        assert backend.prewarm_ms is not None
        await backend.close()

    asyncio.run(scenario())
