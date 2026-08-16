from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from runtime.adapters import GenerationOptions
from runtime.world_mind.sys12 import (
    InferencePriorityGate,
    PrioritizedChatBackend,
    Sys12ReleaseConfig,
    Sys12ReleaseHost,
    evaluate_sys12,
)
from tests.world_mind._helpers import ROOT


def test_release_manifest_binds_local_asset_and_forbids_fallback() -> None:
    config = Sys12ReleaseConfig.load(ROOT / "local_runtime" / "sys12_release_manifest.json")
    assert config.release_path == "llama_cpp"
    assert config.diagnostic_path == "ollama"
    assert config.context_size == 4096
    assert config.batch_size == 128
    assert config.ubatch_size == 64
    assert config.gpu_layers == 28
    assert config.chat_template == "chatml"
    assert config.stop_sequences == ("<|im_end|>", "<|im_start|>")
    assert config.structured_output == "json_schema"
    assert config.foreground_protocol == "mind_patch_v2"
    assert config.world_mind_mode_timeouts_seconds["MIND_PATCH_V2"] == 30
    assert config.world_mind_mode_timeouts_seconds["GAME_REPLY"] == 90
    assert config.model_sha256 == "be1b6f203eee64e50e4bf3a026c9a8e8226b3eb63b81725153c615992290b575"
    host = Sys12ReleaseHost(config)
    assert host.inference_path == "llama_cpp"
    assert host.state == "stopped"


async def test_foreground_preempts_background_and_runs_first() -> None:
    gate = InferencePriorityGate()
    background_started = asyncio.Event()
    order: list[str] = []

    async def background() -> None:
        with pytest.raises(asyncio.CancelledError):
            async with gate.acquire("memory_index"):
                background_started.set()
                await asyncio.sleep(60)

    task = asyncio.create_task(background())
    await background_started.wait()
    async with gate.acquire("foreground_reply"):
        order.append("foreground")
    await task
    assert order == ["foreground"]
    assert gate.snapshot()["foreground_preemptions"] == 1
    assert gate.snapshot()["tasks_cancelled"] == 1


async def test_prioritized_backend_maps_model_modes_to_priority() -> None:
    class Backend:
        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

        async def complete_chat(self, **kwargs) -> str:
            return kwargs["request_id"]

    gate = InferencePriorityGate()
    backend = PrioritizedChatBackend(Backend(), gate)
    result = await backend.complete_chat(
        request_id="req:MEMORY_PROPOSE:0",
        messages=({"role": "user", "content": "test"},),
        options=GenerationOptions(1, 0.1, 0.8, 1.0),
    )
    assert result == "req:MEMORY_PROPOSE:0"
    assert gate.snapshot()["tasks_completed"] == 1


def test_sys12_gate_requires_every_formal_threshold() -> None:
    config = Sys12ReleaseConfig.load(ROOT / "local_runtime" / "sys12_release_manifest.json")
    report = {
        "release_path": {
            "name": "llama_cpp",
            "asset_sha256": config.model_sha256,
            "lifecycle_passed": True,
            "combined_gpu_peak_mib": 5000,
            "warm_first_visible_token_p95_ms": 1900,
            "reply_80_tokens_p95_ms": 4900,
            "reply_160_tokens_p95_ms": 7900,
        },
        "memory_selection_incremental_p95_ms": 299,
        "foreground_priority_passed": True,
        "stability": {"minutes": 60, "passed": True, "transaction_violations": 0},
    }
    assert evaluate_sys12(report, config)["sys12_gate"] == "passed"
    broken = deepcopy(report)
    broken["release_path"]["combined_gpu_peak_mib"] = 5120
    assert evaluate_sys12(broken, config)["sys12_gate"] == "failed"
