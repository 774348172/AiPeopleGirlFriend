from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from runtime._memory_contracts import (
    EvidenceQuote,
    MemoryEpistemic,
    MemoryProposalDraft,
    MemoryRelations,
    MemorySubject,
    MemoryTemporal,
)
from runtime._memory_vectors import BGE_MODEL_ID, EncoderIdentity
from runtime._reranker import FakeMemoryReranker
from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ActiveSceneState,
    FakeWorldMindModel,
    GameClockService,
    HeroineMemoryRepositoryFactory,
    HeroineMindPatch,
    InMemoryWorldStateProvider,
    LivingMindPatch,
    PersistentWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    POST_REPLY_WORLD_MIND_RECONCILE,
)
from runtime.world_mind.persistence import _heroine_runtime_payload

from tests.world_mind._helpers import INITIAL_GAME_TIME, MutableMonotonic, ROOT


SECOND_CHARACTER_ID = "synthetic_second_heroine"


def _config(*, multi_character: bool = False) -> WorldMindRuntimeConfig:
    packages = {"baiweixi": ROOT / "人物设定" / "白未晞"}
    allowed = ("baiweixi",)
    if multi_character:
        packages[SECOND_CHARACTER_ID] = (
            ROOT / "tests" / "fixtures" / "world_mind" / SECOND_CHARACTER_ID
        )
        allowed = ("baiweixi", SECOND_CHARACTER_ID)
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs=packages,
        p0_allowed_character_ids=allowed,
        foreground_protocol="legacy_v1",
    )


def _session(
    character_id: str = "baiweixi",
    *,
    conversation_id: str | None = None,
) -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id="wmr07_save",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id=character_id,
        conversation_id=conversation_id or f"wmr07_{character_id}",
    )


def _protagonist(
    location_id: str = "apartment_window",
    location_label: str = "出租屋窗边",
    activity: str = "吃面",
) -> ProtagonistLiveState:
    return ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id=location_id,
        location_label=location_label,
        activity=activity,
        body_state={"fatigue": "轻微疲惫"},
        held_item_ids=("chopsticks",) if activity == "吃面" else (),
    )


def _scene(
    scene_id: str = "apartment_window",
    location_label: str = "出租屋窗边",
    *,
    multi_character: bool = False,
) -> ActiveSceneState:
    characters = ("protagonist", "baiweixi")
    if multi_character:
        characters = ("protagonist", "baiweixi", SECOND_CHARACTER_ID)
    return ActiveSceneState(
        scene_id=scene_id,
        location_label=location_label,
        present_character_ids=characters,
        item_states={"rain_window": "窗外正在下雨"},
    )


def _runtime(
    tmp_path: Path,
    model: FakeWorldMindModel,
    *,
    config: WorldMindRuntimeConfig | None = None,
    store: WorldMindStore | None = None,
    provider=None,
    monotonic: MutableMonotonic | None = None,
    memory_factory: HeroineMemoryRepositoryFactory | None = None,
    before_turn_commit=None,
) -> tuple[WorldMindRuntime, WorldMindStore, object, MutableMonotonic]:
    active_store = store or WorldMindStore.open(
        tmp_path / "world_mind.sqlite3",
        before_turn_commit=before_turn_commit,
    )
    active_monotonic = monotonic or MutableMonotonic()
    active_provider = provider or InMemoryWorldStateProvider()
    clock = GameClockService(
        active_store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=active_monotonic,
    )
    runtime = WorldMindRuntime(
        config=config or _config(),
        store=active_store,
        world_state_provider=active_provider,
        game_clock=clock,
        model=model,
        memory_repository_factory=memory_factory,
        periodic_reconcile_seconds=300.0,
    )
    return runtime, active_store, active_provider, active_monotonic


def test_wmr07_sixty_turn_one_hour_trajectory_is_continuous(tmp_path: Path) -> None:
    async def scenario() -> None:
        def mind_patch(request) -> HeroineMindPatch:
            text = request.snapshot.protagonist_utterance
            changes = {}
            if text == "陪我看一会儿雨吧。":
                changes = {
                    "current_activity": "坐在窗边看雨",
                    "emotion": "安静地陪着男主",
                    "attention": "窗外的雨和男主吃面的动静",
                }
            elif text == "雨停了，别看了，去厨房倒杯水吧。":
                changes = {
                    "current_activity": "去厨房倒水",
                    "attention": "厨房里的水杯",
                }
            elif text == "你累了就去沙发上休息。":
                changes = {
                    "current_activity": "蜷在沙发上休息",
                    "body": "伤处仍有轻微酸痛，需要休息",
                }
            elif text == "你的伤口已经恢复得差不多了。":
                changes = {"body": "外伤已经恢复大半，可以正常缓慢活动"}
            elif text == "我从咖啡厅回来了。":
                changes = {
                    "emotion": "见到男主回来后明显放松",
                    "attention": "刚回到出租屋的男主",
                }
            return HeroineMindPatch(
                living_mind=LivingMindPatch(**changes),
                evidence_refs=(request.snapshot.snapshot_id,),
            )

        model = FakeWorldMindModel(
            mind_patch_factory=mind_patch,
            reply_factory=lambda request: (
                f"我现在在{request.approved_state.living_mind.current_activity}。"
            ),
        )
        runtime, store, provider, monotonic = _runtime(tmp_path, model)
        active_session = _session()
        await runtime.start()
        await provider.update_latest(
            active_session,
            _protagonist(),
            _scene(),
            runtime.game_clock.current_time(active_session),
        )
        snapshot_activity_after_ten = None
        try:
            for turn_index in range(60):
                monotonic.advance(60.0)
                text = f"普通对话第{turn_index + 1}轮。"
                if turn_index == 0:
                    text = "陪我看一会儿雨吧。"
                elif turn_index == 11:
                    text = "雨停了，别看了，去厨房倒杯水吧。"
                elif turn_index == 25:
                    text = "你累了就去沙发上休息。"
                elif turn_index == 40:
                    text = "你的伤口已经恢复得差不多了。"
                elif turn_index == 50:
                    text = "我从咖啡厅回来了。"

                if turn_index == 15:
                    await provider.update_latest(
                        active_session,
                        _protagonist("lane_corner", "街角", "步行"),
                        _scene("lane_corner", "街角"),
                        runtime.game_clock.current_time(active_session),
                    )
                elif turn_index == 30:
                    await provider.update_latest(
                        active_session,
                        _protagonist("coffee_shop", "咖啡厅", "整理桌面"),
                        _scene("coffee_shop", "咖啡厅"),
                        runtime.game_clock.current_time(active_session),
                    )
                elif turn_index == 45:
                    await provider.update_latest(
                        active_session,
                        _protagonist("apartment_door", "出租屋门口", "换鞋"),
                        _scene("apartment_door", "出租屋门口"),
                        runtime.game_clock.current_time(active_session),
                    )

                result = await runtime.handle_turn(
                    TurnRequest(
                        request_id=f"trajectory_{turn_index:02d}",
                        session=active_session,
                        text=text,
                    )
                )
                assert isinstance(result, Completed)
                if turn_index == 10:
                    snapshot_activity_after_ten = model.advance_requests[-1].snapshot

                if (turn_index + 1) % 5 == 0:
                    await runtime.trigger_periodic_reconcile(active_session)
                    await runtime.wait_background_idle(
                        active_session,
                        timeout_seconds=5.0,
                    )

            await runtime.wait_background_idle(active_session, timeout_seconds=5.0)
            current = store.load_heroine_runtime(active_session)
            assert current is not None
            assert snapshot_activity_after_ten is not None
            assert (
                snapshot_activity_after_ten.heroine_runtime.living_mind.current_activity
                == "坐在窗边看雨"
            )
            assert current.living_mind.current_activity == "蜷在沙发上休息"
            assert current.living_mind.body == "外伤已经恢复大半，可以正常缓慢活动"
            assert current.living_mind.emotion == "见到男主回来后明显放松"
            assert model.advance_requests[16].snapshot.protagonist.location_id == (
                "lane_corner"
            )
            assert model.advance_requests[31].snapshot.protagonist.location_id == (
                "coffee_shop"
            )
            assert model.advance_requests[46].snapshot.protagonist.location_id == (
                "apartment_door"
            )
            assert runtime.game_clock.current_time(active_session) == (
                INITIAL_GAME_TIME + timedelta(hours=1)
            )
            assert store.count_turn_transactions(active_session.save_id) == 60
            assert store.count_turn_events(active_session.save_id) == 120
            assert store.count_model_decisions(active_session.save_id) == 60
            modes = [request.mode for request in model.reconcile_requests]
            assert modes.count(POST_REPLY_WORLD_MIND_RECONCILE) == 60
            assert modes.count(FIVE_MINUTE_WORLD_MIND_RECONCILE) == 12
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_wmr07_consistency_and_queue_audit_report_atomic_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, store, provider, _monotonic = _runtime(
            tmp_path,
            FakeWorldMindModel(),
        )
        active_session = _session()
        await runtime.start()
        await provider.update_latest(
            active_session,
            _protagonist(),
            _scene(),
            INITIAL_GAME_TIME,
        )
        try:
            result = await runtime.handle_turn(
                TurnRequest(
                    request_id="audit_turn",
                    session=active_session,
                    text="检查一次完整事务。",
                )
            )
            assert isinstance(result, Completed)
            await runtime.wait_background_idle(active_session)
            assert store.consistency_audit(active_session.save_id) == {
                "orphan_turns": 0,
                "orphan_events": 0,
                "duplicate_requests": 0,
                "event_count_mismatch": 0,
                "dangling_model_decisions": 0,
            }
            queue = store.reconcile_queue_snapshot(active_session.save_id)
            assert queue["pending"] == 0
            assert queue["running"] == 0
            metrics = runtime.background_scheduler.snapshot_metrics(active_session)
            assert metrics["jobs_completed"] >= 1
            assert metrics["peak_pending_jobs"] >= 1
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_wmr07_cancel_replay_and_request_conflict_leave_no_partial_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        model = FakeWorldMindModel(wait_event=release)
        runtime, store, provider, _monotonic = _runtime(tmp_path, model)
        active_session = _session()
        await runtime.start()
        await provider.update_latest(
            active_session,
            _protagonist(),
            _scene(),
            INITIAL_GAME_TIME,
        )
        request = TurnRequest(
            request_id="cancel_then_retry",
            session=active_session,
            text="这一轮先取消。",
        )
        try:
            task = asyncio.create_task(runtime.handle_turn(request))
            await model.reply_started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert store.count_turn_transactions(active_session.save_id) == 0
            assert store.count_turn_events(active_session.save_id) == 0

            release.set()
            completed = await runtime.handle_turn(request)
            replayed = await runtime.handle_turn(request)
            conflict = await runtime.handle_turn(
                TurnRequest(
                    request_id=request.request_id,
                    session=active_session,
                    text="相同请求号但内容不同。",
                )
            )
            assert isinstance(completed, Completed)
            assert isinstance(replayed, Completed)
            assert replayed.metrics.replayed is True
            assert isinstance(conflict, Failed)
            assert conflict.code == "request_conflict"
            assert store.count_turn_transactions(active_session.save_id) == 1
            assert store.count_turn_events(active_session.save_id) == 2
            assert len(model.advance_requests) == 2
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_wmr07_mind_version_conflict_rejects_the_whole_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = WorldMindStore.open(tmp_path / "version_conflict.sqlite3")

        def conflict_reply(request) -> str:
            payload = json.dumps(
                _heroine_runtime_payload(request.approved_state),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            store._connection.execute(
                """
                UPDATE heroine_runtime_current
                SET version = ?, payload_json = ?
                WHERE save_id = ? AND character_id = ?
                """,
                (
                    request.approved_state.version,
                    payload,
                    request.snapshot.session.save_id,
                    request.snapshot.session.active_character_id,
                ),
            )
            return "这条回复不应提交。"

        model = FakeWorldMindModel(reply_factory=conflict_reply)
        runtime, _store, provider, _monotonic = _runtime(
            tmp_path,
            model,
            store=store,
        )
        active_session = _session()
        await runtime.start()
        await provider.update_latest(
            active_session,
            _protagonist(),
            _scene(),
            INITIAL_GAME_TIME,
        )
        try:
            result = await runtime.handle_turn(
                TurnRequest(
                    request_id="version_conflict",
                    session=active_session,
                    text="制造一次版本冲突。",
                )
            )
            assert isinstance(result, Failed)
            assert result.code == "mind_state_conflict"
            assert store.count_turn_transactions(active_session.save_id) == 0
            assert store.count_turn_events(active_session.save_id) == 0
            assert store.count_model_decisions(active_session.save_id) == 0
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_wmr07_rebuilds_deleted_projections_and_recovers_last_complete_turn(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "projection_rebuild.sqlite3"
        active_session = _session()
        first_model = FakeWorldMindModel(
            mind_patch_factory=lambda request: HeroineMindPatch(
                living_mind=LivingMindPatch(current_activity="整理自己的纸箱"),
                evidence_refs=(request.snapshot.snapshot_id,),
            )
        )
        first_store = WorldMindStore.open(database_path)
        first_provider = PersistentWorldStateProvider(first_store)
        first_runtime, _store, _provider, _monotonic = _runtime(
            tmp_path,
            first_model,
            store=first_store,
            provider=first_provider,
        )
        await first_runtime.start()
        await first_provider.update_latest(
            active_session,
            _protagonist("coffee_shop", "咖啡厅", "擦桌子"),
            _scene("coffee_shop", "咖啡厅"),
            INITIAL_GAME_TIME,
        )
        first = await first_runtime.handle_turn(
            TurnRequest(
                request_id="complete_before_crash",
                session=active_session,
                text="你整理一下纸箱吧。",
            )
        )
        assert isinstance(first, Completed)
        await first_runtime.wait_background_idle(active_session, timeout_seconds=5.0)
        await first_runtime.close()
        first_store.close()

        def fail_commit() -> None:
            raise RuntimeError("simulated crash before commit")

        failing_store = WorldMindStore.open(
            database_path,
            before_turn_commit=fail_commit,
        )
        failing_provider = PersistentWorldStateProvider(failing_store)
        failing_runtime, _store, _provider, _monotonic = _runtime(
            tmp_path,
            FakeWorldMindModel(),
            store=failing_store,
            provider=failing_provider,
        )
        await failing_runtime.start()
        failed = await failing_runtime.handle_turn(
            TurnRequest(
                request_id="crashed_turn",
                session=active_session,
                text="这一轮在提交前崩溃。",
            )
        )
        assert isinstance(failed, Failed)
        assert failed.code == "persistence_error"
        await failing_runtime.close()
        assert failing_store.count_turn_transactions(active_session.save_id) == 1
        failing_store.close()

        connection = sqlite3.connect(database_path)
        try:
            connection.execute("DELETE FROM protagonist_live_states")
            connection.execute("DELETE FROM active_scene_states")
            connection.execute("DELETE FROM heroine_runtime_current")
            connection.execute("DELETE FROM save_runtime_versions")
            connection.commit()
        finally:
            connection.close()

        recovered_store = WorldMindStore.open(database_path)
        try:
            rebuilt = recovered_store.rebuild_runtime_projections(active_session)
            assert rebuilt.live_world.version == 1
            assert rebuilt.live_world.protagonist.location_id == "coffee_shop"
            assert rebuilt.heroine_runtime.version == 2
            assert (
                rebuilt.heroine_runtime.living_mind.current_activity
                == "整理自己的纸箱"
            )
            assert recovered_store.count_turn_transactions(active_session.save_id) == 1
            assert recovered_store.count_turn_events(active_session.save_id) == 2
            assert recovered_store.count_model_decisions(active_session.save_id) == 1
            assert recovered_store.load_live_world(active_session) == rebuilt.live_world
            assert (
                recovered_store.load_heroine_runtime(active_session)
                == rebuilt.heroine_runtime
            )
        finally:
            recovered_store.close()

    asyncio.run(scenario())


@dataclass(slots=True)
class _FakeEncoder:
    dimension: int = 8

    @property
    def identity(self) -> EncoderIdentity:
        return EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision="wmr07-test-v1",
            artifact_sha256=hashlib.sha256(b"wmr07-encoder").hexdigest(),
            dimension=self.dimension,
        )

    def encode(self, texts):
        return [
            [float(value + 1) for value in hashlib.sha256(text.encode()).digest()[:8]]
            for text in texts
        ]


class _OwnerMemoryProposer:
    def __init__(self, statements: dict[str, str]) -> None:
        self.statements = statements

    async def propose(self, request):
        event = next(item for item in request.source_events if item.actor == "protagonist")
        return (
            MemoryProposalDraft(
                kind="player_fact",
                statement=self.statements[request.owner_character_id],
                subject=MemorySubject(
                    subject_type="player",
                    entity_id="protagonist",
                    display_name="主角",
                ),
                epistemic=MemoryEpistemic(
                    polarity="affirmed",
                    modality="asserted",
                    grounding="speaker_report",
                ),
                temporal=MemoryTemporal(
                    relation="present",
                    resolution="resolved",
                    source_text="当前回合",
                    anchor_event_id=event.event_id,
                    start_at=None,
                    end_at=None,
                    timezone=None,
                    precision="minute",
                ),
                evidence_quotes=(
                    EvidenceQuote(
                        event_id=event.event_id,
                        role="support",
                        quote=event.text,
                        start_hint=0,
                    ),
                ),
                semantic_reason="WMR-07 多角色私密记忆隔离",
                confidence=0.95,
                relation_suggestions=MemoryRelations(
                    semantic_slot=f"wmr07.{request.owner_character_id}.private",
                    supersedes=(),
                    contradicts=(),
                    refines=(),
                ),
            ),
        )


def test_wmr07_same_save_switches_characters_with_isolated_state_and_memory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        def character_patch(request) -> HeroineMindPatch:
            character_id = request.snapshot.session.active_character_id
            activity = (
                "守着自己的白色纸箱"
                if character_id == "baiweixi"
                else "整理自己的测试笔记"
            )
            return HeroineMindPatch(
                living_mind=LivingMindPatch(current_activity=activity),
                knowledge_updates={"private_marker": f"{character_id}_only"},
                evidence_refs=(request.snapshot.snapshot_id,),
            )

        model = FakeWorldMindModel(mind_patch_factory=character_patch)
        store = WorldMindStore.open(tmp_path / "multi_character.sqlite3")
        encoder = _FakeEncoder()
        reranker = FakeMemoryReranker(threshold=0.0)
        memory_factory = HeroineMemoryRepositoryFactory(
            store,
            encoder=encoder,
            reranker=reranker,
        )
        provider = InMemoryWorldStateProvider()
        runtime, _store, _provider, _monotonic = _runtime(
            tmp_path,
            model,
            config=_config(multi_character=True),
            store=store,
            provider=provider,
            memory_factory=memory_factory,
        )
        bai = _session("baiweixi", conversation_id="bai_conversation")
        second = _session(SECOND_CHARACTER_ID, conversation_id="second_conversation")
        await runtime.start()
        await provider.update_latest(
            bai,
            _protagonist(),
            _scene(multi_character=True),
            INITIAL_GAME_TIME,
        )
        try:
            bai_turn = await runtime.handle_turn(
                TurnRequest(
                    request_id="bai_private_turn",
                    session=bai,
                    text="我只告诉你，我喜欢雨夜。",
                )
            )
            second_turn = await runtime.handle_turn(
                TurnRequest(
                    request_id="second_private_turn",
                    session=second,
                    text="我只告诉你，备用钥匙在花盆下面。",
                )
            )
            assert isinstance(bai_turn, Completed)
            assert isinstance(second_turn, Completed)
            await runtime.wait_background_idle(bai, timeout_seconds=5.0)
            await runtime.wait_background_idle(second, timeout_seconds=5.0)

            proposer = _OwnerMemoryProposer(
                {
                    "baiweixi": "男主只对白未晞说自己喜欢雨夜",
                    SECOND_CHARACTER_ID: "男主只对测试女主乙说备用钥匙在花盆下面",
                }
            )
            bai_repository = memory_factory.open(bai)
            second_repository = memory_factory.open(second)
            bai_proposal = (
                await bai_repository.propose_from_request("bai_private_turn", proposer)
            )[0]
            second_proposal = (
                await second_repository.propose_from_request(
                    "second_private_turn",
                    proposer,
                )
            )[0]
            bai_memory = bai_repository.commit(
                bai_repository.materialize(bai_proposal)
            )
            second_memory = second_repository.commit(
                second_repository.materialize(second_proposal)
            )
            bai_repository.rebuild()
            second_repository.rebuild()

            bai_query = await runtime.handle_turn(
                TurnRequest(
                    request_id="bai_memory_query",
                    session=bai,
                    text="你记得我只告诉你的事情吗？",
                )
            )
            bai_snapshot = model.advance_requests[-1].snapshot
            second_query = await runtime.handle_turn(
                TurnRequest(
                    request_id="second_memory_query",
                    session=second,
                    text="你记得我只告诉你的事情吗？",
                )
            )
            second_snapshot = model.advance_requests[-1].snapshot
            assert isinstance(bai_query, Completed)
            assert isinstance(second_query, Completed)
            assert bai_snapshot.selected_memory_frame.source_memory_ids == (
                bai_memory.memory_id,
            )
            assert second_snapshot.selected_memory_frame.source_memory_ids == (
                second_memory.memory_id,
            )
            assert second_memory.memory_id not in (
                bai_snapshot.selected_memory_frame.source_memory_ids
            )
            assert bai_memory.memory_id not in (
                second_snapshot.selected_memory_frame.source_memory_ids
            )

            bai_runtime = store.load_heroine_runtime(bai)
            second_runtime = store.load_heroine_runtime(second)
            assert bai_runtime is not None
            assert second_runtime is not None
            assert bai_runtime.knowledge_state["private_marker"] == "baiweixi_only"
            assert second_runtime.knowledge_state["private_marker"] == (
                f"{SECOND_CHARACTER_ID}_only"
            )
            assert bai_runtime.living_mind.current_activity != (
                second_runtime.living_mind.current_activity
            )
            assert set(runtime.background_scheduler._slots) == {
                (bai.save_id, bai.active_character_id),
                (second.save_id, second.active_character_id),
            }
            event_characters = {
                str(row[0])
                for row in store._connection.execute(
                    "SELECT DISTINCT character_id FROM world_mind_events"
                ).fetchall()
            }
            assert event_characters == {"baiweixi", SECOND_CHARACTER_ID}
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())
