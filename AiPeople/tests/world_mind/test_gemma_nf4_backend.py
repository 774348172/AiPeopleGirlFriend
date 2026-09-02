from __future__ import annotations

import asyncio
import threading

from runtime.adapters.gemma_nf4 import (
    GemmaNF4Config,
    GemmaNF4GenerationResult,
    GemmaNF4WorldMindBackend,
)
from runtime.adapters.llama_cpp import GenerationOptions

from tests.real_select_e2e.test_gemma_nf4_assets import make_asset
from runtime.gemma_nf4_assets import LocalGemmaNF4Package


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
