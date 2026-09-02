from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

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
from runtime.contracts import Completed
from runtime.world_mind import (
    FakeWorldMindModel,
    GameClockService,
    HeroineMemoryRepositoryFactory,
    InMemoryWorldStateProvider,
    R1MemoryJob,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindRuntime,
    WorldMindStore,
    WorldMindStoreError,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    MutableMonotonic,
    protagonist,
    runtime_config,
    scene,
    session,
)
from tests.world_mind.test_wmr07_long_trajectory import (
    SECOND_CHARACTER_ID,
    _config as multi_character_config,
)


@dataclass(slots=True)
class _Encoder:
    dimension: int = 8

    @property
    def identity(self) -> EncoderIdentity:
        return EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision="sys12r-test-v1",
            artifact_sha256=hashlib.sha256(b"sys12r-encoder").hexdigest(),
            dimension=self.dimension,
        )

    def encode(self, texts):
        return [
            [float(value + 1) for value in hashlib.sha256(text.encode()).digest()[:8]]
            for text in texts
        ]


class _Proposer:
    def __init__(self, *, failures: int = 0, count: int = 1) -> None:
        self.failures = failures
        self.count = count
        self.calls = 0

    async def propose(self, request):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("injected R1 timeout")
        protagonist_event = next(
            event for event in request.source_events if event.actor == "protagonist"
        )
        return tuple(
            MemoryProposalDraft(
                kind="player_fact",
                statement=(
                    f"{request.owner_character_id}记得男主说："
                    f"{protagonist_event.text}#{index}"
                ),
                subject=MemorySubject("player", "protagonist", "男主"),
                epistemic=MemoryEpistemic(
                    "affirmed", "asserted", "speaker_report"
                ),
                temporal=MemoryTemporal(
                    "atemporal",
                    "not_applicable",
                    None,
                    None,
                    None,
                    None,
                    None,
                    "not_applicable",
                ),
                evidence_quotes=(
                    EvidenceQuote(
                        event_id=protagonist_event.event_id,
                        role="support",
                        quote=protagonist_event.text,
                        start_hint=0,
                    ),
                ),
                semantic_reason="SYS-12R R1 test",
                confidence=1.0,
                relation_suggestions=MemoryRelations(None, (), (), ()),
            )
            for index in range(self.count)
        )


class _BrokenSecondEncoder(_Encoder):
    def encode(self, texts):
        if len(texts) >= 2:
            raise RuntimeError("injected index failure")
        return super().encode(texts)


def _build_runtime(
    database: Path,
    *,
    proposer_provider,
    encoder=None,
    config=None,
    background_execution_enabled: bool = True,
):
    store = WorldMindStore.open(database)
    monotonic = MutableMonotonic()
    clock = GameClockService(
        store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=monotonic,
    )
    provider = InMemoryWorldStateProvider()
    memory_factory = HeroineMemoryRepositoryFactory(
        store,
        encoder=encoder or _Encoder(),
        reranker=FakeMemoryReranker(threshold=0.0),
    )
    runtime = WorldMindRuntime(
        config=config or runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=FakeWorldMindModel(),
        memory_repository_factory=memory_factory,
        memory_proposer_provider=proposer_provider,
        periodic_reconcile_seconds=86400.0,
        background_execution_enabled=background_execution_enabled,
    )
    return runtime, store, provider, memory_factory


async def _put_world(provider, active_session, *, multi_character=False):
    active_scene = scene()
    if multi_character:
        active_scene = type(active_scene)(
            scene_id=active_scene.scene_id,
            location_label=active_scene.location_label,
            present_character_ids=(
                "protagonist",
                "baiweixi",
                SECOND_CHARACTER_ID,
            ),
            item_states=active_scene.item_states,
        )
    await provider.update_latest(
        active_session,
        protagonist(),
        active_scene,
        INITIAL_GAME_TIME,
    )


def test_r1_runs_automatically_after_committed_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        proposer = _Proposer()
        runtime, store, provider, factory = _build_runtime(
            tmp_path / "automatic.sqlite3",
            proposer_provider=lambda _session: proposer,
        )
        await runtime.start()
        await _put_world(provider, session())
        try:
            result = await runtime.handle_turn(
                TurnRequest("r1-auto", session(), "我很喜欢雨夜。")
            )
            assert isinstance(result, Completed)
            await runtime.wait_background_idle(session())
            memories = factory.open(session()).list_memories()
            assert [memory.statement for memory in memories] == [
                "baiweixi记得男主说：我很喜欢雨夜。#0"
            ]
            assert store.count_r1_memory_jobs(
                "save_001", character_id="baiweixi", state="completed"
            ) == 1
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_deferred_background_keeps_r1_queued_without_model_execution(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        proposer = _Proposer()
        runtime, store, provider, factory = _build_runtime(
            tmp_path / "deferred.sqlite3",
            proposer_provider=lambda _session: proposer,
            background_execution_enabled=False,
        )
        await runtime.start()
        await _put_world(provider, session())
        try:
            result = await runtime.handle_turn(
                TurnRequest("r1-deferred", session(), "这条只应进入长期记忆队列。")
            )
            assert isinstance(result, Completed)
            await asyncio.sleep(0)
            assert proposer.calls == 0
            assert factory.open(session()).list_memories() == ()
            assert store.count_r1_memory_jobs(
                "save_001", state="pending"
            ) == 1
            assert store.reconcile_queue_snapshot("save_001")["pending"] == 1
            assert runtime.background_scheduler.snapshot_metrics(session())["jobs_started"] == 0
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_r1_failure_retries_without_blocking_next_foreground(tmp_path: Path) -> None:
    async def scenario() -> None:
        proposer = _Proposer(failures=1)
        runtime, store, provider, factory = _build_runtime(
            tmp_path / "retry.sqlite3",
            proposer_provider=lambda _session: proposer,
        )
        await runtime.start()
        await _put_world(provider, session())
        try:
            first = await runtime.handle_turn(
                TurnRequest("r1-retry-1", session(), "第一句话。")
            )
            second = await runtime.handle_turn(
                TurnRequest("r1-retry-2", session(), "第二句话。")
            )
            assert isinstance(first, Completed)
            assert isinstance(second, Completed)
            await runtime.wait_background_idle(session())
            assert proposer.calls >= 3
            assert store.count_r1_memory_jobs("save_001", state="completed") == 2
            assert len(factory.open(session()).list_memories()) == 2
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_running_r1_job_recovers_after_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "restart.sqlite3"
        proposer = _Proposer()
        first_runtime, first_store, provider, _factory = _build_runtime(
            database,
            proposer_provider=lambda _session: proposer,
        )
        await first_runtime.start()
        await _put_world(provider, session())
        result = await first_runtime.handle_turn(
            TurnRequest("r1-restart", session(), "记住这句话。")
        )
        assert isinstance(result, Completed)
        await first_runtime.close()
        row = first_store._connection.execute(
            "SELECT job_id FROM world_mind_r1_memory_jobs LIMIT 1"
        ).fetchone()
        first_store._connection.execute(
            "UPDATE world_mind_r1_memory_jobs SET state = 'running' WHERE job_id = ?",
            (str(row[0]),),
        )
        first_store.close()

        second_runtime, second_store, second_provider, second_factory = _build_runtime(
            database,
            proposer_provider=lambda _session: proposer,
        )
        await second_runtime.start()
        await _put_world(second_provider, session())
        try:
            await second_runtime.activate_session(session())
            await second_runtime.wait_background_idle(session())
            assert second_store.count_r1_memory_jobs(
                "save_001", state="completed"
            ) == 1
            assert len(second_factory.open(session()).list_memories()) == 1
        finally:
            await second_runtime.close()
            second_store.close()

    asyncio.run(scenario())


def test_r1_index_failure_commits_no_partial_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        proposer = _Proposer(count=2)
        runtime, store, provider, factory = _build_runtime(
            tmp_path / "atomic.sqlite3",
            proposer_provider=lambda _session: proposer,
            encoder=_BrokenSecondEncoder(),
        )
        await runtime.start()
        await _put_world(provider, session())
        try:
            result = await runtime.handle_turn(
                TurnRequest("r1-atomic", session(), "这会生成两条候选。")
            )
            assert isinstance(result, Completed)
            await runtime.wait_background_idle(session())
            assert factory.open(session()).list_memories() == ()
            assert store.count_r1_memory_jobs("save_001", state="failed") == 1
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_r1_same_save_keeps_heroine_repositories_isolated(tmp_path: Path) -> None:
    async def scenario() -> None:
        proposers = {
            "baiweixi": _Proposer(),
            SECOND_CHARACTER_ID: _Proposer(),
        }
        runtime, store, provider, factory = _build_runtime(
            tmp_path / "isolated.sqlite3",
            proposer_provider=lambda value: proposers[value.active_character_id],
            config=multi_character_config(multi_character=True),
        )
        bai = session(
            save_id="save_001",
            active_character_id="baiweixi",
            conversation_id="bai-conversation",
        )
        second = RuntimeSessionIdentity(
            save_id="save_001",
            world_id="songjiangfu",
            protagonist_id="protagonist",
            active_character_id=SECOND_CHARACTER_ID,
            conversation_id="second-conversation",
        )
        await runtime.start()
        await _put_world(provider, bai, multi_character=True)
        try:
            assert isinstance(
                await runtime.handle_turn(TurnRequest("bai-r1", bai, "只告诉白未晞。")),
                Completed,
            )
            assert isinstance(
                await runtime.handle_turn(
                    TurnRequest("second-r1", second, "只告诉第二位女主。")
                ),
                Completed,
            )
            await runtime.wait_background_idle(bai)
            await runtime.wait_background_idle(second)
            bai_memories = factory.open(bai).list_memories()
            second_memories = factory.open(second).list_memories()
            assert len(bai_memories) == len(second_memories) == 1
            assert bai_memories[0].owner_character_id == "baiweixi"
            assert second_memories[0].owner_character_id == SECOND_CHARACTER_ID
            assert "只告诉白未晞" in bai_memories[0].statement
            assert "只告诉第二位女主" in second_memories[0].statement
            assert store.count_r1_memory_jobs(
                "save_001", character_id="baiweixi", state="completed"
            ) == 1
            assert store.count_r1_memory_jobs(
                "save_001", character_id=SECOND_CHARACTER_ID, state="completed"
            ) == 1
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())


def test_r1_job_rejects_another_heroine_event_in_same_save(tmp_path: Path) -> None:
    async def scenario() -> None:
        proposers = {
            "baiweixi": _Proposer(),
            SECOND_CHARACTER_ID: _Proposer(),
        }
        runtime, store, provider, _factory = _build_runtime(
            tmp_path / "cross-event.sqlite3",
            proposer_provider=lambda value: proposers[value.active_character_id],
            config=multi_character_config(multi_character=True),
        )
        bai = session(conversation_id="bai-conversation")
        second = RuntimeSessionIdentity(
            save_id="save_001",
            world_id="songjiangfu",
            protagonist_id="protagonist",
            active_character_id=SECOND_CHARACTER_ID,
            conversation_id="second-conversation",
        )
        await runtime.start()
        await _put_world(provider, bai, multi_character=True)
        try:
            result = await runtime.handle_turn(
                TurnRequest("bai-private-event", bai, "白未晞的私密证据。")
            )
            assert isinstance(result, Completed)
            await runtime.wait_background_idle(bai)
            with store._lock:
                row = store._connection.execute(
                        """
                        SELECT user_event_id, assistant_event_id
                        FROM turn_transactions
                        WHERE save_id = ? AND request_id = ?
                        """,
                        ("save_001", "bai-private-event"),
                    ).fetchone()
                user_event_id = str(row[0])
                assistant_event_id = str(row[1])
            second_job = R1MemoryJob(
                job_id="cross-source-probe",
                dedupe_key="cross-source-probe",
                session=second,
                source_request_id="bai-private-event",
                user_event_id=user_event_id,
                assistant_event_id=assistant_event_id,
                state="running",
                attempt=1,
                failure_code=None,
                created_game_time=INITIAL_GAME_TIME,
            )
            try:
                store.validate_r1_memory_job_sources(second_job)
            except WorldMindStoreError as error:
                assert str(error) == "R1 memory job source escaped its heroine turn"
            else:
                raise AssertionError("cross-heroine R1 source must be rejected")
        finally:
            await runtime.close()
            store.close()

    asyncio.run(scenario())
