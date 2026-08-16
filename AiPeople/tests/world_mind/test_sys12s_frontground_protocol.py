from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    FakeWorldMindModel,
    ShortProtocolError,
    TurnRequest,
    compile_m2,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    scene,
    session,
)


def _m2_config(config):
    return replace(config, foreground_protocol="mind_patch_v2")


async def _initialize(harness) -> None:
    await harness.provider.update_latest(
        session(), protagonist(), scene(), INITIAL_GAME_TIME
    )


def test_compile_m2_rejects_any_embedded_reply(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            snapshot = harness.provider
            request = TurnRequest("m2-reply-forbidden", session(), "你是谁？")
            live_world = await snapshot.get_latest(request.session)
            heroine = harness.store.get_or_create_heroine_runtime(
                request.session,
                harness.runtime.prompt_composer.initial_runtime_seed("baiweixi"),
            )
            from runtime.world_mind import TurnWorldSnapshot, WorldStateProjection

            turn_snapshot = TurnWorldSnapshot(
                snapshot_id="snapshot-m2",
                request_id=request.request_id,
                session=request.session,
                captured_game_time=INITIAL_GAME_TIME,
                live_world_version=live_world.version,
                mind_state_version=heroine.version,
                world_state=WorldStateProjection().project(live_world),
                protagonist=live_world.protagonist,
                scene=live_world.scene,
                heroine_runtime=heroine,
                protagonist_utterance=request.text,
                protagonist_utterance_event_id="event-user",
            )
            with pytest.raises(ShortProtocolError, match="cannot contain reply"):
                compile_m2({"c": [], "r": "我是白未晞。"}, turn_snapshot)
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_m2_reply_failure_commits_neither_state_nor_dialogue(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(fail_phase="reply")
        harness = build_runtime_harness(tmp_path, model=model)
        harness.runtime.config = _m2_config(harness.runtime.config)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            before = harness.store.get_or_create_heroine_runtime(
                session(),
                harness.runtime.prompt_composer.initial_runtime_seed("baiweixi"),
            )
            result = await harness.runtime.handle_turn(
                TurnRequest("m2-reply-fails", session(), "你是谁？")
            )
            after = harness.store.load_heroine_runtime(session())
            assert isinstance(result, Failed)
            assert after == before
            assert harness.store.count_turn_events("save_001") == 0
            assert harness.store.count_turn_transactions("save_001") == 0
            assert len(model.foreground_requests) == 1
            assert len(model.reply_requests) == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_m2_reply_receives_recent_committed_dialogue_in_order(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(
            reply_factory=lambda request: (
                "我是白未晞。" if not request.recent_dialogue else "还记得。"
            )
        )
        harness = build_runtime_harness(tmp_path, model=model)
        harness.runtime.config = _m2_config(harness.runtime.config)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            first = await harness.runtime.handle_turn(
                TurnRequest("m2-first", session(), "你是谁？")
            )
            second = await harness.runtime.handle_turn(
                TurnRequest("m2-second", session(), "还记得刚才吗？")
            )
            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            assert model.reply_requests[0].recent_dialogue == ()
            assert model.reply_requests[1].recent_dialogue == (
                ("protagonist", "你是谁？"),
                ("heroine", "我是白未晞。"),
            )
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
