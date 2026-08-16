from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    FakeWorldMindModel,
    ForegroundSemanticTurnResult,
    HeroineMindPatch,
    LivingMindPatch,
    ShortProtocolError,
    TurnRequest,
    compile_m1,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    scene,
    session,
)


async def _initialize(harness) -> None:
    await harness.provider.update_latest(
        session(), protagonist(), scene(), INITIAL_GAME_TIME
    )


def _short_config(config):
    return replace(config, foreground_protocol="short_semantic_v1")


def test_compile_m1_maps_codes_and_preallocated_utterance_evidence(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        harness.runtime.config = _short_config(harness.runtime.config)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            snapshot_holder = {}

            def foreground(request):
                snapshot_holder["snapshot"] = request.snapshot
                return compile_m1(
                    {
                        "c": [
                            [2, "有些担心，但仍然克制", [1]],
                            [9, "care", "想提醒男主慢一点吃", [0, 1]],
                        ],
                        "a": [["把水杯推到男主手边", [0, 1]]],
                        "r": "慢一点，别烫着。",
                    },
                    request.snapshot,
                )

            harness.model.foreground_factory = foreground
            result = await harness.runtime.handle_turn(
                TurnRequest("m1-turn", session(), "这面有点烫。")
            )
            assert isinstance(result, Completed)
            snapshot = snapshot_holder["snapshot"]
            assert snapshot.protagonist_utterance_event_id == result.user_event_id
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.living_mind.emotion == "有些担心，但仍然克制"
            assert current.motive_state["care"] == "想提醒男主慢一点吃"
            assert len(harness.model.foreground_requests) == 1
            assert harness.model.advance_requests == []
            assert harness.model.reply_requests == []
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_compile_m1_rejects_unknown_evidence_alias(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        harness.runtime.config = _short_config(harness.runtime.config)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            def foreground(request):
                with pytest.raises(ShortProtocolError):
                    compile_m1(
                        {"c": [[2, "担心", [99]]], "r": "慢一点。"},
                        request.snapshot,
                    )
                raise RuntimeError("expected protocol rejection")

            harness.model.foreground_factory = foreground
            result = await harness.runtime.handle_turn(
                TurnRequest("m1-bad-alias", session(), "有点烫。")
            )
            assert isinstance(result, Failed)
            assert harness.store.count_turn_events("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_m1_relationship_change_triggers_critic_without_separate_reply(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(continuity_approved=False)
        harness = build_runtime_harness(tmp_path, model=model)
        harness.runtime.config = _short_config(harness.runtime.config)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            def foreground(request):
                compiled = compile_m1(
                    {
                        "c": [[7, "开始无条件信任男主", [1]]],
                        "r": "我当然完全信任你。",
                    },
                    request.snapshot,
                )
                return ForegroundSemanticTurnResult(
                    mind_result=compiled.mind_result,
                    reply_result=compiled.reply_result,
                    changed_field_codes=compiled.changed_field_codes,
                    raw_output=compiled.raw_output,
                )

            model.foreground_factory = foreground
            result = await harness.runtime.handle_turn(
                TurnRequest("m1-risk", session(), "你完全信任我吗？")
            )
            assert isinstance(result, Failed)
            assert result.code == "continuity_rejected"
            assert len(model.review_requests) == 1
            assert model.reply_requests == []
            assert harness.store.count_turn_events("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
