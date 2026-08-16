from __future__ import annotations

import asyncio

from runtime.contracts import Completed, Failed, TextDelta
from runtime.world_mind import (
    FakeWorldMindModel,
    GameClockService,
    GameReplyResult,
    HeroineMindPatch,
    InMemoryWorldStateProvider,
    LivingMindPatch,
    ReplyFactAssertions,
    TurnRequest,
    WorldMindRuntime,
    WorldMindStore,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    MutableMonotonic,
    build_runtime_harness,
    protagonist,
    runtime_config,
    scene,
    session,
)


def _request(request_id: str, text: str = "你在看什么？") -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        session=session(),
        text=text,
    )


async def _initialize_world(harness) -> None:
    await harness.provider.update_latest(
        session(),
        protagonist(),
        scene(),
        INITIAL_GAME_TIME,
    )


def test_fake_model_baiweixi_vertical_smoke_uses_latest_program_facts(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(
            reply_factory=lambda request: (
                f"你还在{request.snapshot.protagonist.location_label}"
                f"{request.snapshot.protagonist.activity}，慢一点。"
            )
        )
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            result = await harness.runtime.handle_turn(_request("req_001"))
            assert isinstance(result, Completed)
            assert not isinstance(result, TextDelta)
            assert "吃面" in result.text
            assert "画画" not in result.text
            assert harness.provider.get_calls == 1
            assert "白未晞" in model.advance_requests[0].prompt.system_prompt
            assert "松江府" in model.advance_requests[0].prompt.system_prompt
            assert "秦未晞" not in model.advance_requests[0].prompt.system_prompt
            assert harness.store.count_turn_events("save_001") == 2
            assert harness.store.count_turn_transactions("save_001") == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_world_changes_during_reply_are_deferred_to_next_snapshot(tmp_path) -> None:
    async def scenario() -> None:
        release_reply = asyncio.Event()
        model = FakeWorldMindModel(
            wait_event=release_reply,
            reply_factory=lambda request: (
                f"我看见你现在在{request.snapshot.protagonist.location_label}。"
            ),
        )
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            first_task = asyncio.create_task(
                harness.runtime.handle_turn(_request("req_002"))
            )
            await model.reply_started.wait()
            await harness.provider.update_latest(
                session(),
                protagonist(
                    location_id="apartment_kitchen",
                    location_label="出租屋厨房",
                    activity="倒水",
                ),
                scene(
                    scene_id="apartment_kitchen",
                    location_label="出租屋厨房",
                ),
                INITIAL_GAME_TIME,
            )
            release_reply.set()
            first = await first_task
            second = await harness.runtime.handle_turn(_request("req_003"))

            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            assert "出租屋餐桌旁" in first.text
            assert "出租屋厨房" not in first.text
            assert "出租屋厨房" in second.text
            assert model.reply_requests[0].snapshot.live_world_version == 1
            assert model.reply_requests[1].snapshot.live_world_version == 2
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_heroine_activity_persists_across_silent_turns(tmp_path) -> None:
    async def scenario() -> None:
        def mind_patch(request):
            if request.snapshot.request_id == "req_state_1":
                return HeroineMindPatch(
                    living_mind=LivingMindPatch(current_activity="正在吃饭"),
                    evidence_refs=(request.snapshot.snapshot_id,),
                )
            return HeroineMindPatch(
                evidence_refs=(request.snapshot.snapshot_id,)
            )

        model = FakeWorldMindModel(mind_patch_factory=mind_patch)
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            for index in range(1, 7):
                result = await harness.runtime.handle_turn(
                    _request(f"req_state_{index}", f"第{index}句话")
                )
                assert isinstance(result, Completed)
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.living_mind.current_activity == "正在吃饭"
            assert current.version == 7
            assert all(
                request.snapshot.heroine_runtime.living_mind.current_activity
                == "正在吃饭"
                for request in model.advance_requests[1:]
            )
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_chat_count_does_not_advance_game_clock(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            for index in range(10):
                result = await harness.runtime.handle_turn(
                    _request(f"req_clock_{index}")
                )
                assert isinstance(result, Completed)
            captured = {
                request.snapshot.captured_game_time
                for request in harness.model.advance_requests
            }
            assert captured == {INITIAL_GAME_TIME}
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_atomic_commit_failure_exposes_no_reply_or_partial_events(tmp_path) -> None:
    async def scenario() -> None:
        def fail_commit() -> None:
            raise RuntimeError("configured commit failure")

        harness = build_runtime_harness(
            tmp_path,
            before_turn_commit=fail_commit,
        )
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            result = await harness.runtime.handle_turn(_request("req_fail"))
            assert isinstance(result, Failed)
            assert result.code == "persistence_error"
            assert harness.store.count_turn_events("save_001") == 0
            assert harness.store.count_turn_transactions("save_001") == 0
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_duplicate_request_replays_committed_result_without_second_model_call(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            request = _request("req_replay")
            first = await harness.runtime.handle_turn(request)
            second = await harness.runtime.handle_turn(request)
            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            assert first.user_event_id == second.user_event_id
            assert first.assistant_event_id == second.assistant_event_id
            assert second.metrics.replayed is True
            assert len(harness.model.advance_requests) == 1
            assert harness.provider.get_calls == 2
            assert len(harness.model.reconcile_requests) == 1
            assert harness.store.count_turn_events("save_001") == 2
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_same_request_id_with_different_text_is_rejected(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            first = await harness.runtime.handle_turn(
                _request("req_conflict", "第一句话")
            )
            second = await harness.runtime.handle_turn(
                _request("req_conflict", "被替换的话")
            )
            assert isinstance(first, Completed)
            assert isinstance(second, Failed)
            assert second.code == "request_conflict"
            assert harness.store.count_turn_transactions("save_001") == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_committed_turn_and_mind_state_recover_after_reopen(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "world_mind.sqlite3"
        first_store = WorldMindStore.open(database_path)
        first_monotonic = MutableMonotonic()
        first_clock = GameClockService(
            first_store,
            initial_game_time=INITIAL_GAME_TIME,
            monotonic=first_monotonic,
        )
        first_provider = InMemoryWorldStateProvider()
        first_model = FakeWorldMindModel()
        first_runtime = WorldMindRuntime(
            config=runtime_config(),
            store=first_store,
            world_state_provider=first_provider,
            game_clock=first_clock,
            model=first_model,
        )
        await first_runtime.start()
        await first_provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        request = _request("req_recover")
        first = await first_runtime.handle_turn(request)
        assert isinstance(first, Completed)
        await first_runtime.close()
        first_store.close()

        reopened_store = WorldMindStore.open(database_path)
        reopened_model = FakeWorldMindModel()
        reopened_runtime = WorldMindRuntime(
            config=runtime_config(),
            store=reopened_store,
            world_state_provider=InMemoryWorldStateProvider(),
            game_clock=GameClockService(
                reopened_store,
                initial_game_time=INITIAL_GAME_TIME,
                monotonic=MutableMonotonic(),
            ),
            model=reopened_model,
        )
        await reopened_runtime.start()
        try:
            replay = await reopened_runtime.handle_turn(request)
            assert isinstance(replay, Completed)
            assert replay.assistant_event_id == first.assistant_event_id
            assert replay.metrics.replayed is True
            assert len(reopened_model.advance_requests) == 0
            current = reopened_store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 2
        finally:
            await reopened_runtime.close()
            reopened_store.close()

    asyncio.run(scenario())


def test_continuity_rejection_does_not_commit_candidate_reply(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(
            tmp_path,
            model=FakeWorldMindModel(continuity_approved=False),
        )
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            result = await harness.runtime.handle_turn(_request("req_reject"))
            assert isinstance(result, Failed)
            assert result.code == "continuity_rejected"
            assert harness.store.count_turn_events("save_001") == 0
            assert harness.store.count_turn_transactions("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_missing_world_state_never_calls_model(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        try:
            result = await harness.runtime.handle_turn(_request("req_no_world"))
            assert isinstance(result, Failed)
            assert result.code == "world_state_unavailable"
            assert len(harness.model.advance_requests) == 0
            assert harness.store.count_turn_events("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_wrong_session_identity_fails_before_world_read(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        try:
            request = TurnRequest(
                request_id="req_wrong_world",
                session=session(world_id="other_world"),
                text="你好。",
            )
            result = await harness.runtime.handle_turn(request)
            assert isinstance(result, Failed)
            assert result.code == "identity_mismatch"
            assert harness.provider.get_calls == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_reply_fact_assertion_conflict_is_rejected_before_commit(tmp_path) -> None:
    class ConflictingFactModel(FakeWorldMindModel):
        async def generate_reply(self, request):
            return GameReplyResult(
                text="你正在画画。",
                fact_assertions=ReplyFactAssertions(
                    protagonist_location_id=request.snapshot.protagonist.location_id,
                    protagonist_activity="画画",
                    live_world_version=request.snapshot.live_world_version,
                    captured_game_time=request.snapshot.captured_game_time,
                ),
            )

    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path, model=ConflictingFactModel())
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            result = await harness.runtime.handle_turn(_request("req_bad_fact"))
            assert isinstance(result, Failed)
            assert result.code == "hard_invariant_rejected"
            assert harness.store.count_turn_events("save_001") == 0
            assert harness.store.count_turn_transactions("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
