from __future__ import annotations

import asyncio
import json

from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    ContinuityReviewResult,
    FakeWorldMindModel,
    HeroineMindPatch,
    LivingMindPatch,
    RelationshipPatch,
    TurnRequest,
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


def _request(request_id: str) -> TurnRequest:
    return TurnRequest(
        request_id=request_id,
        session=session(),
        text="你是不是有点担心我？",
    )


def test_critic_revision_is_the_state_used_by_reply_and_atomic_commit(tmp_path) -> None:
    async def scenario() -> None:
        def mind_patch(request):
            return HeroineMindPatch(
                living_mind=LivingMindPatch(emotion="过度激动"),
                relationship=RelationshipPatch(trust="立刻完全信任"),
                motive_updates={"care_for_protagonist": "想照顾男主"},
                knowledge_updates={"current_fact": "男主正在吃面"},
                evidence_refs=(request.snapshot.snapshot_id,),
            )

        def continuity(request):
            return ContinuityReviewResult(
                decision="revise",
                reason="普通关心不足以造成关系跳级",
                revision_patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(emotion="克制的担心"),
                    relationship=RelationshipPatch(
                        trust=request.previous_state.relationship.trust
                    ),
                    evidence_refs=(request.snapshot.snapshot_id,),
                ),
                snapshot_id=request.snapshot.snapshot_id,
            )

        def reply(request):
            assert request.approved_state.living_mind.emotion == "克制的担心"
            assert request.approved_state.relationship.trust != "立刻完全信任"
            return "白未晞移开视线。‘只是提醒你吃慢一点。’"

        model = FakeWorldMindModel(
            mind_patch_factory=mind_patch,
            continuity_factory=continuity,
            reply_factory=reply,
        )
        harness = build_runtime_harness(tmp_path, model=model)
        await harness.runtime.start()
        await _initialize(harness)
        try:
            result = await harness.runtime.handle_turn(_request("wmr05-revise"))
            assert isinstance(result, Completed)
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.living_mind.emotion == "克制的担心"
            assert current.motive_state["care_for_protagonist"] == "想照顾男主"
            assert current.knowledge_state["current_fact"] == "男主正在吃面"
            assert harness.store.count_model_decisions("save_001") == 1
            row = harness.store._connection.execute(
                """
                SELECT continuity_review_json, approved_runtime_json
                FROM turn_model_decisions AS d
                JOIN turn_transactions AS t
                  ON t.save_id = d.save_id AND t.request_id = d.request_id
                WHERE d.save_id = ? AND d.request_id = ?
                """,
                ("save_001", "wmr05-revise"),
            ).fetchone()
            assert json.loads(str(row[0]))["decision"] == "revise"
            assert (
                json.loads(str(row[1]))["living_mind"]["emotion"]
                == "克制的担心"
            )
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_each_model_phase_failure_leaves_no_formal_turn_or_decision(tmp_path) -> None:
    async def scenario(phase: str) -> None:
        harness = build_runtime_harness(
            tmp_path / phase,
            model=FakeWorldMindModel(fail_phase=phase),
        )
        await harness.runtime.start()
        await _initialize(harness)
        try:
            result = await harness.runtime.handle_turn(_request(f"fail-{phase}"))
            assert isinstance(result, Failed)
            assert harness.store.count_turn_events("save_001") == 0
            assert harness.store.count_turn_transactions("save_001") == 0
            assert harness.store.count_model_decisions("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    for phase in ("advance", "review", "reply"):
        asyncio.run(scenario(phase))


def test_critic_rejection_is_not_committed_to_formal_world(tmp_path) -> None:
    async def scenario() -> None:
        harness = build_runtime_harness(
            tmp_path,
            model=FakeWorldMindModel(continuity_approved=False),
        )
        await harness.runtime.start()
        await _initialize(harness)
        try:
            result = await harness.runtime.handle_turn(_request("wmr05-reject"))
            assert isinstance(result, Failed)
            assert result.code == "continuity_rejected"
            assert harness.store.count_model_decisions("save_001") == 0
            assert harness.store.count_turn_events("save_001") == 0
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
