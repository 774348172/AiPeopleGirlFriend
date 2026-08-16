from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

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
    HeroineMemoryEvidenceError,
    HeroineMemoryOwnershipError,
    HeroineMemoryRepository,
    HeroineMemoryRepositoryFactory,
    TurnRequest,
)
from runtime.world_mind.memory_contracts import (
    canonical_heroine_memory_json,
    heroine_memory_from_json,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    scene,
    session,
)


@dataclass(slots=True)
class FakeEncoder:
    dimension: int = 8

    @property
    def identity(self) -> EncoderIdentity:
        return EncoderIdentity(
            model_id=BGE_MODEL_ID,
            revision="wmr04-test-v1",
            artifact_sha256=hashlib.sha256(b"wmr04-test-encoder").hexdigest(),
            dimension=self.dimension,
        )

    def encode(self, texts):
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vectors.append(
                [float(digest[index] + 1) for index in range(self.dimension)]
            )
        return vectors


class OwnerAwareProposer:
    def __init__(self, statements: dict[str, str], *, supersedes=()) -> None:
        self._statements = statements
        self._supersedes = tuple(supersedes)
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        event = next(
            item for item in request.source_events if item.actor == "protagonist"
        )
        return (
            MemoryProposalDraft(
                kind="player_fact",
                statement=self._statements[request.owner_character_id],
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
                    source_text="这个雨夜",
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
                semantic_reason="这件事会影响女主之后对主角偏好的理解",
                confidence=0.95,
                relation_suggestions=MemoryRelations(
                    semantic_slot="protagonist.preference.rainy_night",
                    supersedes=self._supersedes,
                    contradicts=(),
                    refines=(),
                ),
            ),
        )


async def _committed_turn(harness, request_id="wmr04-request"):
    await harness.runtime.start()
    await harness.provider.update_latest(
        session(), protagonist(), scene(), INITIAL_GAME_TIME
    )
    result = await harness.runtime.handle_turn(
        TurnRequest(
            request_id=request_id,
            session=session(),
            text="我其实很喜欢这样的雨夜。",
        )
    )
    assert isinstance(result, Completed)


def _repository(harness, owner_character_id: str) -> HeroineMemoryRepository:
    return HeroineMemoryRepository.from_world_mind_store(
        harness.store,
        save_id="save_001",
        world_id="songjiangfu",
        owner_character_id=owner_character_id,
        encoder=FakeEncoder(),
        reranker=FakeMemoryReranker(threshold=0.0),
    )


def test_baiweixi_vertical_memory_v2_flow_uses_committed_world_events(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await _committed_turn(harness)
        try:
            repository = _repository(harness, "baiweixi")
            proposer = OwnerAwareProposer(
                {"baiweixi": "主角亲口说自己喜欢雨夜"}
            )
            proposals = await repository.propose_from_request(
                "wmr04-request", proposer
            )
            assert len(proposals) == 1
            memory = repository.materialize(proposals[0])
            committed = repository.commit(memory)
            assert repository.commit(memory) == committed
            encoded = canonical_heroine_memory_json(committed)
            assert heroine_memory_from_json(encoded) == committed
            schema = json.loads(
                (
                    Path(__file__).resolve().parents[2]
                    / "runtime"
                    / "schemas"
                    / "memory_representation_v2.schema.json"
                ).read_text(encoding="utf-8")
            )
            Draft202012Validator(schema).validate(json.loads(encoded))

            indexed = repository.rebuild()
            recalled = await repository.recall(
                "雨夜会让你觉得舒服吗？",
                game_time=INITIAL_GAME_TIME,
            )

            assert indexed.memory_count == 1
            assert indexed.view_count >= 2
            assert recalled.frame.source_memory_ids == (committed.memory_id,)
            assert recalled.frame.source_event_ids == (
                committed.evidence[0].event_id,
            )
            assert recalled.frame.selected_memories[0].statement == committed.statement
            assert repository.working_activation()[0].memory_id == committed.memory_id
            assert repository.self_timeline()[0].memory_id == committed.memory_id
            assert committed.save_id == "save_001"
            assert committed.world_id == "songjiangfu"
            assert committed.owner_character_id == "baiweixi"
            assert committed.game_time == INITIAL_GAME_TIME
            assert committed.conversation_id == "conversation-a"
            assert proposer.requests[0].owner_character_id == "baiweixi"
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_repository_public_methods_cannot_override_bound_owner() -> None:
    for method_name in (
        "propose",
        "propose_from_request",
        "materialize",
        "commit",
        "recall",
        "rebuild",
        "get_memory",
        "list_memories",
        "working_activation",
        "self_timeline",
    ):
        parameters = inspect.signature(
            getattr(HeroineMemoryRepository, method_name)
        ).parameters
        assert "owner_character_id" not in parameters
        assert "save_id" not in parameters


def test_empty_repository_recall_does_not_call_reranker(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        reranker = FakeMemoryReranker(threshold=0.0)
        repository = HeroineMemoryRepository.from_world_mind_store(
            harness.store,
            save_id="save_001",
            world_id="songjiangfu",
            owner_character_id="baiweixi",
            encoder=FakeEncoder(),
            reranker=reranker,
        )
        try:
            result = await repository.recall(
                "空库查询",
                game_time=INITIAL_GAME_TIME,
            )
            assert result.frame.selected_memories == ()
            assert result.candidates.candidates == ()
            assert reranker.calls == []
            assert repository.working_activation() == ()
        finally:
            harness.store.close()

    asyncio.run(scenario())


def test_normal_world_mind_turn_opens_active_heroine_repository_before_model(
    tmp_path,
) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        reranker = FakeMemoryReranker(threshold=0.0)
        factory = HeroineMemoryRepositoryFactory(
            harness.store,
            encoder=FakeEncoder(),
            reranker=reranker,
        )
        harness.runtime.memory_repository_factory = factory
        await _committed_turn(harness, "memory-seed-request")
        try:
            repository = factory.open(session())
            proposal = (
                await repository.propose_from_request(
                    "memory-seed-request",
                    OwnerAwareProposer(
                        {"baiweixi": "主角亲口说自己喜欢雨夜"}
                    ),
                )
            )[0]
            memory = repository.commit(repository.materialize(proposal))

            result = await harness.runtime.handle_turn(
                TurnRequest(
                    request_id="memory-aware-turn",
                    session=session(),
                    text="你还记得我喜欢什么天气吗？",
                )
            )

            assert isinstance(result, Completed)
            snapshot = harness.model.advance_requests[-1].snapshot
            assert snapshot.selected_memory_frame.source_memory_ids == (
                memory.memory_id,
            )
            assert factory.open(session()) is repository
            assert reranker.start_calls == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_two_heroine_repositories_isolate_store_vectors_activation_and_timeline(
    tmp_path,
) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await _committed_turn(harness, "shared-public-request")
        try:
            bai = _repository(harness, "baiweixi")
            second = _repository(harness, "synthetic_second_heroine")
            proposer = OwnerAwareProposer(
                {
                    "baiweixi": "白未晞记得主角说自己喜欢雨夜",
                    "synthetic_second_heroine": "另一位女主只把这理解为主角不讨厌下雨",
                }
            )
            bai_proposal = (
                await bai.propose_from_request("shared-public-request", proposer)
            )[0]
            second_proposal = (
                await second.propose_from_request("shared-public-request", proposer)
            )[0]
            bai_memory = bai.commit(bai.materialize(bai_proposal))
            second_memory = second.commit(second.materialize(second_proposal))

            bai.rebuild()
            second.rebuild()
            bai_recall = await bai.recall("你喜欢雨夜吗", game_time=INITIAL_GAME_TIME)
            second_recall = await second.recall(
                "你喜欢雨夜吗", game_time=INITIAL_GAME_TIME
            )

            assert {item.memory_id for item in bai_recall.candidates.candidates} == {
                bai_memory.memory_id
            }
            assert {item.memory_id for item in second_recall.candidates.candidates} == {
                second_memory.memory_id
            }
            assert bai.get_memory(second_memory.memory_id) is None
            assert second.get_memory(bai_memory.memory_id) is None
            assert bai.working_activation()[0].memory_id == bai_memory.memory_id
            assert second.working_activation()[0].memory_id == second_memory.memory_id
            assert bai.self_timeline()[0].statement != second.self_timeline()[0].statement
            assert bai_memory.source_event_ids == second_memory.source_event_ids
            assert bai_memory.owner_character_id != second_memory.owner_character_id

            owners = {
                str(row[0])
                for row in harness.store._connection.execute(
                    "SELECT DISTINCT owner_character_id FROM memory_v2_selector_views"
                ).fetchall()
            }
            assert owners == {"baiweixi", "synthetic_second_heroine"}
            with pytest.raises(HeroineMemoryOwnershipError):
                second.commit(bai_memory)
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_cross_owner_relation_target_cannot_escape_repository(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await _committed_turn(harness, "relation-request")
        try:
            bai = _repository(harness, "baiweixi")
            second = _repository(harness, "synthetic_second_heroine")
            bai_memory = bai.commit(
                bai.materialize(
                    (
                        await bai.propose_from_request(
                            "relation-request",
                            OwnerAwareProposer(
                                {"baiweixi": "白未晞保存的私有记忆"}
                            ),
                        )
                    )[0]
                )
            )
            proposal = (
                await second.propose_from_request(
                    "relation-request",
                    OwnerAwareProposer(
                        {"synthetic_second_heroine": "试图跨库覆盖记忆"},
                        supersedes=(bai_memory.memory_id,),
                    ),
                )
            )[0]
            with pytest.raises(HeroineMemoryOwnershipError):
                second.commit(second.materialize(proposal))
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_repository_rejects_source_event_from_another_save(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(tmp_path)
        await _committed_turn(harness, "save-bound-request")
        try:
            event_id = str(
                harness.store._connection.execute(
                    """
                    SELECT event_id FROM world_mind_events
                    WHERE save_id = 'save_001' ORDER BY sequence_no LIMIT 1
                    """
                ).fetchone()[0]
            )
            other_save = HeroineMemoryRepository.from_world_mind_store(
                harness.store,
                save_id="save_002",
                world_id="songjiangfu",
                owner_character_id="baiweixi",
                encoder=FakeEncoder(),
                reranker=FakeMemoryReranker(threshold=0.0),
            )
            with pytest.raises(HeroineMemoryEvidenceError):
                await other_save.propose(
                    (event_id,),
                    OwnerAwareProposer(
                        {"baiweixi": "不应读取到另一个存档的事件"}
                    ),
                    participant_ids=("protagonist", "baiweixi"),
                )
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
