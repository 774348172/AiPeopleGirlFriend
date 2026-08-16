from __future__ import annotations

import asyncio

from runtime.contracts import Completed
from runtime.world_mind import (
    ContinuityReviewResult,
    FakeWorldMindModel,
    HeroineMindPatch,
    LivingMindPatch,
    ReconcileMindResult,
    TurnRequest,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    POST_REPLY_WORLD_MIND_RECONCILE,
)
from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    scene,
    session,
)


async def _initialize_world(harness) -> None:
    await harness.provider.update_latest(
        session(),
        protagonist(),
        scene(),
        INITIAL_GAME_TIME,
    )


def _turn(request_id: str, text: str = "你在想什么？") -> TurnRequest:
    return TurnRequest(request_id=request_id, session=session(), text=text)


def test_post_reply_reconcile_is_required_before_next_turn(tmp_path) -> None:
    async def scenario() -> None:
        def reconcile(request):
            evidence = request.snapshot.source_event_ids[1]
            return ReconcileMindResult(
                operation="update",
                reason="上一轮回复让她确认自己仍然关心男主",
                parent_mind_state_version=request.snapshot.mind_state_version,
                snapshot_id=request.snapshot.snapshot_id,
                latest_world_version=request.snapshot.live_world_version,
                transition_basis=(evidence,),
                patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(
                        emotion="克制但持续的关心",
                        immediate_intent="继续观察男主有没有不舒服",
                    ),
                    evidence_refs=(evidence,),
                ),
                memory_candidates=("男主愿意回应她的关心",),
                timeline_candidates=("她在回复后仍然留意男主",),
            )

        model = FakeWorldMindModel(reconcile_factory=reconcile)
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            first = await harness.runtime.handle_turn(_turn("turn-1"))
            second = await harness.runtime.handle_turn(_turn("turn-2", "我没事。"))
            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            assert len(model.reconcile_requests) == 1
            assert model.reconcile_requests[0].mode == POST_REPLY_WORLD_MIND_RECONCILE
            second_snapshot_state = model.advance_requests[1].snapshot.heroine_runtime
            assert second_snapshot_state.living_mind.emotion == "克制但持续的关心"
            assert second_snapshot_state.version == 3
            assert harness.store.count_reconcile_decisions("save_001") == 1
            assert harness.store.count_reconcile_jobs("save_001", state="completed") == 1
            decision = harness.store.load_reconcile_decision(
                model.reconcile_requests[0].snapshot.job_id
            )
            assert decision is not None
            assert decision.reconcile_result["memory_candidates"] == [
                "男主愿意回应她的关心"
            ]
            assert decision.reconcile_result["timeline_candidates"] == [
                "她在回复后仍然留意男主"
            ]
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_five_minute_keep_does_not_advance_mind_version(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            await harness.runtime.activate_session(session())
            await harness.runtime.trigger_periodic_reconcile(session())
            await harness.runtime.wait_background_idle(session())
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 1
            assert len(harness.model.reconcile_requests) == 1
            assert (
                harness.model.reconcile_requests[0].mode
                == FIVE_MINUTE_WORLD_MIND_RECONCILE
            )
            assert harness.store.count_reconcile_decisions("save_001") == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_periodic_jobs_wait_for_reply_merge_and_read_latest_world(tmp_path) -> None:
    async def scenario() -> None:
        release_reply = asyncio.Event()

        def reconcile(request):
            if request.mode == POST_REPLY_WORLD_MIND_RECONCILE:
                return ReconcileMindResult(
                    operation="keep",
                    reason="回答后没有额外状态变化",
                    parent_mind_state_version=request.snapshot.mind_state_version,
                    snapshot_id=request.snapshot.snapshot_id,
                    latest_world_version=request.snapshot.live_world_version,
                    transition_basis=(request.snapshot.snapshot_id,),
                )
            return ReconcileMindResult(
                operation="update",
                reason="男主已经移动到厨房并开始洗碗",
                parent_mind_state_version=request.snapshot.mind_state_version,
                snapshot_id=request.snapshot.snapshot_id,
                latest_world_version=request.snapshot.live_world_version,
                transition_basis=(request.snapshot.snapshot_id,),
                patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(
                        attention="正在厨房洗碗的男主",
                        immediate_intent="考虑要不要过去帮忙",
                    ),
                    evidence_refs=(request.snapshot.snapshot_id,),
                ),
            )

        model = FakeWorldMindModel(
            wait_event=release_reply,
            reconcile_factory=reconcile,
        )
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            turn_task = asyncio.create_task(
                harness.runtime.handle_turn(_turn("turn-blocked"))
            )
            await model.reply_started.wait()
            first_job = await harness.runtime.trigger_periodic_reconcile(session())
            second_job = await harness.runtime.trigger_periodic_reconcile(session())
            assert first_job.job_id == second_job.job_id
            assert second_job.missed_intervals == 1
            await harness.provider.update_latest(
                session(),
                protagonist(
                    location_id="apartment_kitchen",
                    location_label="出租屋厨房",
                    activity="洗碗",
                ),
                scene(
                    scene_id="apartment_kitchen",
                    location_label="出租屋厨房",
                ),
                INITIAL_GAME_TIME,
            )
            release_reply.set()
            result = await turn_task
            assert isinstance(result, Completed)
            await harness.runtime.wait_background_idle(session())
            periodic = next(
                request
                for request in model.reconcile_requests
                if request.mode == FIVE_MINUTE_WORLD_MIND_RECONCILE
            )
            assert periodic.missed_intervals == 1
            assert periodic.snapshot.protagonist.location_id == "apartment_kitchen"
            assert periodic.snapshot.protagonist.activity == "洗碗"
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.living_mind.attention == "正在厨房洗碗的男主"
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_reconcile_rejection_recomputes_once_then_commits_keep(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(
            reconcile_factory=lambda request: ReconcileMindResult(
                operation="update",
                reason="尝试无依据改变情绪",
                parent_mind_state_version=request.snapshot.mind_state_version,
                snapshot_id=request.snapshot.snapshot_id,
                latest_world_version=request.snapshot.live_world_version,
                transition_basis=(request.snapshot.snapshot_id,),
                patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(emotion="突然极度兴奋"),
                    evidence_refs=(request.snapshot.snapshot_id,),
                ),
            ),
            reconcile_review_factory=lambda request: ContinuityReviewResult(
                decision="reject",
                reason="没有事实支持情绪跳变",
                snapshot_id=request.snapshot.snapshot_id,
            ),
        )
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            await harness.runtime.trigger_periodic_reconcile(session())
            await harness.runtime.wait_background_idle(session())
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 1
            assert current.living_mind.emotion != "突然极度兴奋"
            assert len(model.reconcile_requests) == 2
            assert model.reconcile_requests[1].review_feedback == "没有事实支持情绪跳变"
            assert len(model.reconcile_review_requests) == 2
            assert harness.store.count_reconcile_decisions("save_001") == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_reconcile_failure_keeps_stable_state_and_exhausts_retry(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(fail_phase="reconcile")
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            await harness.runtime.trigger_periodic_reconcile(session())
            await harness.runtime.wait_background_idle(session())
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 1
            assert len(model.reconcile_requests) == 3
            assert harness.store.count_reconcile_jobs("save_001", state="failed") == 1
            assert harness.store.count_reconcile_decisions("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_failed_required_post_reply_reconcile_does_not_block_next_reply(tmp_path) -> None:
    async def scenario() -> None:
        model = FakeWorldMindModel(fail_phase="reconcile")
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            first = await harness.runtime.handle_turn(_turn("failed-post-1"))
            second = await harness.runtime.handle_turn(_turn("failed-post-2"))
            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            assert len(model.reconcile_requests) == 1
            assert len(model.advance_requests) == 2
            assert harness.store.count_reconcile_jobs("save_001", state="failed") == 1
            assert harness.store.count_turn_transactions("save_001") == 2
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_reconcile_atomic_failure_rolls_back_state_and_decision(tmp_path) -> None:
    async def scenario() -> None:
        def fail_commit() -> None:
            raise RuntimeError("forced reconcile commit failure")

        def reconcile(request):
            return ReconcileMindResult(
                operation="update",
                reason="测试原子回滚",
                parent_mind_state_version=request.snapshot.mind_state_version,
                snapshot_id=request.snapshot.snapshot_id,
                latest_world_version=request.snapshot.live_world_version,
                transition_basis=(request.snapshot.snapshot_id,),
                patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(emotion="不应被提交"),
                    evidence_refs=(request.snapshot.snapshot_id,),
                ),
            )

        model = FakeWorldMindModel(reconcile_factory=reconcile)
        harness = build_runtime_harness(
            tmp_path,
            model=model,
            before_reconcile_commit=fail_commit,
        )
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            await harness.runtime.trigger_periodic_reconcile(session())
            await harness.runtime.wait_background_idle(session())
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.version == 1
            assert current.living_mind.emotion != "不应被提交"
            assert harness.store.count_reconcile_decisions("save_001") == 0
            assert harness.store.count_reconcile_jobs("save_001", state="failed") == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_periodic_loop_automatically_enqueues_reconcile(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(
            tmp_path,
            periodic_reconcile_seconds=0.02,
        )
        await harness.runtime.start()
        await _initialize_world(harness)
        try:
            await harness.runtime.activate_session(session())
            async with asyncio.timeout(1.0):
                while not harness.model.reconcile_requests:
                    await asyncio.sleep(0.005)
            assert (
                harness.model.reconcile_requests[0].mode
                == FIVE_MINUTE_WORLD_MIND_RECONCILE
            )
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_reconcile_queue_recovers_running_job_after_restart(tmp_path) -> None:
    database = tmp_path / "reconcile-recovery.sqlite3"
    store = WorldMindStore.open(database)
    job = store.enqueue_periodic_reconcile(
        session(),
        INITIAL_GAME_TIME,
        interval_seconds=300.0,
    )
    claimed = store.claim_next_reconcile_job(session())
    assert claimed is not None
    assert claimed.job_id == job.job_id
    assert claimed.state == "running"
    store.close()

    reopened = WorldMindStore.open(database)
    try:
        assert reopened.recover_reconcile_jobs() == 1
        recovered = reopened.claim_next_reconcile_job(session())
        assert recovered is not None
        assert recovered.job_id == job.job_id
        assert recovered.attempt == 2
    finally:
        reopened.close()


def test_reconcile_event_delta_query_uses_persisted_world_versions(tmp_path) -> None:
    store = WorldMindStore.open(tmp_path / "reconcile-deltas.sqlite3")
    try:
        first, first_delta = store.put_live_world(
            session(),
            protagonist(),
            scene(),
            INITIAL_GAME_TIME,
        )
        second, second_delta = store.put_live_world(
            session(),
            protagonist(
                location_id="apartment_kitchen",
                location_label="出租屋厨房",
                activity="洗碗",
            ),
            scene(
                scene_id="apartment_kitchen",
                location_label="出租屋厨房",
            ),
            INITIAL_GAME_TIME,
        )
        deltas = store.list_world_event_deltas(
            session(),
            after_version=first.version,
            through_version=second.version,
        )
        assert first_delta is not None
        assert second_delta is not None
        assert deltas == (second_delta,)
        assert deltas[0].changed_fields == ("protagonist", "scene")
    finally:
        store.close()
