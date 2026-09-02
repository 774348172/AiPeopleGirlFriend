from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from runtime._selected_memory import (
    SelectedMemory,
    SelectedMemoryEvidence,
    SelectedMemoryFrame,
)
from runtime.contracts import Completed
from runtime.world_mind import (
    CharacterPackagePromptComposer,
    GameReplyRequest,
    HardInvariantError,
    HardInvariantValidator,
    HeroineRuntime,
    LlamaCppWorldMindModel,
    MindAdvanceRequest,
    MindPatchV2Request,
    ModelReadableWorldState,
    ReconcileMindRequest,
    ReconcileReviewRequest,
    ReconcileSourceTurn,
    ReconcileWorldSnapshot,
    TurnRequest,
    TurnWorldSnapshot,
    WorldMindModelError,
    WorldMindModelIdentity,
    WorldMindModelOutputError,
)
from runtime.world_mind.model_gateway import (
    POST_REPLY_WORLD_MIND_RECONCILE,
    ContinuityReviewRequest,
    HeroineDiegeticAction,
)
from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    runtime_config,
    scene,
    session,
)


class ScriptedChatBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.start_calls = 0
        self.close_calls = 0

    async def start(self):
        self.start_calls += 1

    async def close(self):
        self.close_calls += 1

    async def complete_chat(
        self,
        *,
        request_id,
        messages,
        options,
        response_format=None,
    ):
        self.calls.append(
            {
                "request_id": request_id,
                "messages": tuple(messages),
                "options": options,
                "response_format": response_format,
            }
        )
        response = self.responses.pop(0)
        if callable(response):
            response = response(self.calls[-1])
        if isinstance(response, BaseException):
            raise response
        if isinstance(response, str):
            return response
        return json.dumps(response, ensure_ascii=False)


def _identity() -> WorldMindModelIdentity:
    return WorldMindModelIdentity(
        model_id="Qwen/Qwen3.5-4B",
        revision="wmr05-test-v1",
        artifact_sha256="a" * 64,
        character_id="baiweixi",
        world_id="songjiangfu",
        protagonist_id="protagonist",
    )


def _requests():
    composer = CharacterPackagePromptComposer(runtime_config())
    active_session = session()
    prompt = composer.compose(active_session)
    heroine = HeroineRuntime.from_seed(
        active_session,
        composer.initial_runtime_seed("baiweixi"),
    )
    snapshot = TurnWorldSnapshot(
        snapshot_id="snapshot-wmr05",
        request_id="request-wmr05",
        session=active_session,
        captured_game_time=INITIAL_GAME_TIME,
        live_world_version=1,
        mind_state_version=heroine.version,
        world_state=ModelReadableWorldState(
            live_world_version=1,
            scene_text="白未晞和男主都在出租屋餐桌旁。",
            protagonist_text="男主正在吃面。",
        ),
        protagonist=protagonist(),
        scene=scene(),
        heroine_runtime=heroine,
        protagonist_utterance="你是不是有点担心我？",
    )
    return prompt, heroine, snapshot


def test_real_gateway_m2_uses_one_patch_only_model_call() -> None:
    async def scenario() -> None:
        prompt, _heroine, snapshot = _requests()
        snapshot = replace(
            snapshot,
            protagonist_utterance_event_id="event-preallocated-user",
        )
        backend = ScriptedChatBackend(
            [{"c": [[5, "提醒男主慢一点吃", [0, 1]]]}]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        result = await model.propose_mind_patch_v2(
            MindPatchV2Request(prompt, snapshot)
        )
        assert result.mind_result.patch.living_mind.immediate_intent == (
            "提醒男主慢一点吃"
        )
        assert result.mind_result.patch.evidence_refs == (
            snapshot.snapshot_id,
            "event-preallocated-user",
        )
        assert len(backend.calls) == 1
        call = backend.calls[0]
        assert call["request_id"].endswith(":MIND_PATCH_V2:0")
        assert call["options"].max_tokens == 180
        assert call["response_format"]["json_schema"]["name"] == (
            "mind_patch_m2"
        )
        payload = json.loads(call["messages"][-1]["content"])
        assert payload["p"] == "M2"
        assert payload["u"] == snapshot.protagonist_utterance
        assert len(payload["w"]) == 6
        assert "四川" not in call["messages"][0]["content"]
        assert "表达风格" not in call["messages"][0]["content"]
        assert "save_id" not in call["messages"][0]["content"]
        assert "snapshot_id" not in call["messages"][-1]["content"]
        assert "live_world_version" not in call["messages"][-1]["content"]

    asyncio.run(scenario())


def test_real_gateway_reply_uses_only_semantic_context_and_dialogue() -> None:
    async def scenario() -> None:
        prompt, heroine, snapshot = _requests()
        evidence = SelectedMemoryEvidence("event-memory", "protagonist", "我喜欢雨夜")
        memory = SelectedMemory(
            memory_id="memory-private-001",
            memory_version=3,
            kind="preference_boundary",
            statement="男主喜欢安静的雨夜",
            subject_type="protagonist",
            subject_display_name="男主",
            temporal_relation="atemporal",
            temporal_source_text=None,
            temporal_start_at=None,
            temporal_end_at=None,
            temporal_timezone=None,
            epistemic_polarity="positive",
            epistemic_modality="asserted",
            evidence=(evidence,),
        )
        snapshot = replace(
            snapshot,
            selected_memory_frame=SelectedMemoryFrame(
                selected_memories=(memory,),
                source_memory_ids=(memory.memory_id,),
                source_event_ids=(evidence.event_id,),
                selector_version="selector-secret-v7",
                token_count=12,
                truncated=False,
            ),
        )
        backend = ScriptedChatBackend(["记得。今晚的雨很安静。"])
        model = LlamaCppWorldMindModel(backend, identity=_identity())

        await model.generate_reply(
            GameReplyRequest(
                prompt=prompt,
                snapshot=snapshot,
                approved_state=heroine,
                reply_intent="直接回应男主",
                fact_assertions=heroine_assertions(snapshot),
                approved_actions=(
                    HeroineDiegeticAction("抬眼看向男主", (snapshot.snapshot_id,)),
                ),
                recent_dialogue=(
                    ("protagonist", "你还记得我喜欢什么天气吗？"),
                    ("heroine", "记得。"),
                ),
            )
        )

        call = backend.calls[0]
        messages = call["messages"]
        context = messages[0]["content"]
        assert "男主喜欢安静的雨夜" in context
        assert "抬眼看向男主" in context
        assert "memory-private-001" not in context
        assert "event-memory" not in context
        assert "selector-secret-v7" not in context
        assert "男主本轮明确告知或纠正的新事实优先于冲突的旧记忆" in context
        assert "男主的提问、猜测或反问不能覆盖已有事实" in context
        assert context.endswith(
            "不得用常识、猜测、角色设定或看似合理的细节补全答案，"
            "也不得声称自己查询、查看或确认过。"
        )
        assert messages[1:3] == (
            {"role": "user", "content": "你还记得我喜欢什么天气吗？"},
            {"role": "assistant", "content": "记得。"},
        )
        assert messages[-1] == {
            "role": "user",
            "content": snapshot.protagonist_utterance,
        }
        assert call["response_format"] is None
        assert call["options"].temperature == 0.0

    asyncio.run(scenario())


def heroine_assertions(snapshot):
    from runtime.world_mind import ReplyFactAssertions

    return ReplyFactAssertions(
        protagonist_location_id=snapshot.protagonist.location_id,
        protagonist_activity=snapshot.protagonist.activity,
        live_world_version=snapshot.live_world_version,
        captured_game_time=snapshot.captured_game_time,
    )


def _assertions(snapshot):
    return {
        "protagonist_location_id": snapshot.protagonist.location_id,
        "protagonist_activity": snapshot.protagonist.activity,
        "live_world_version": snapshot.live_world_version,
        "captured_game_time": snapshot.captured_game_time.isoformat(
            timespec="microseconds"
        ),
    }


def _patch(snapshot, *, emotion, evidence=True):
    return {
        "living_mind_patch": {
            "form": None,
            "body": None,
            "emotion": emotion,
            "attention": "男主吃面的速度",
            "current_activity": None,
            "immediate_intent": "提醒他慢一点",
        },
        "relationship_patch": {
            "stage": None,
            "trust": None,
            "unresolved_tension": None,
        },
        "motive_updates": {"care_for_protagonist": "克制地关心男主"},
        "knowledge_updates": {"protagonist_likes_rain": "男主喜欢雨夜"},
        "evidence_refs": [snapshot.snapshot_id] if evidence else ["unknown"],
    }


def _mind_output(snapshot):
    return {
        "schema_version": 1,
        "mode": "TURN_MIND_ADVANCE",
        "parent_mind_state_version": snapshot.mind_state_version,
        "snapshot_id": snapshot.snapshot_id,
        "transition_basis": [snapshot.snapshot_id],
        "heroine_patch": _patch(snapshot, emotion="明显担心"),
        "heroine_diegetic_actions": [
            {
                "description": "轻轻看向男主手里的筷子",
                "evidence_refs": [snapshot.snapshot_id],
            }
        ],
        "reply_intent": "提醒男主慢一点吃，同时掩饰关心",
        "reply_state_assertions": _assertions(snapshot),
    }


def _review_output(snapshot, decision="revise"):
    return {
        "schema_version": 1,
        "mode": "WORLD_CONTINUITY_REVIEW",
        "snapshot_id": snapshot.snapshot_id,
        "decision": decision,
        "reason": "普通关心应保持白未晞克制的表达强度",
        "revision_patch": (
            _patch(snapshot, emotion="克制的担心")
            if decision == "revise"
            else None
        ),
    }


def _reply_output(snapshot, approved_version):
    del snapshot, approved_version
    return "吃慢一点。"


def _reconcile_output(snapshot, mode=POST_REPLY_WORLD_MIND_RECONCILE):
    del snapshot, mode
    return {
        "c": [[2, "克制的关心", [2]]],
        "m": ["男主回应了她的关心"],
        "t": ["她在回复后继续观察男主"],
    }


def test_real_gateway_runs_post_reply_reconcile_and_critic_modes() -> None:
    async def scenario() -> None:
        prompt, heroine, turn_snapshot = _requests()
        snapshot = ReconcileWorldSnapshot(
            snapshot_id="snapshot-reconcile",
            job_id="job-reconcile",
            session=turn_snapshot.session,
            captured_game_time=turn_snapshot.captured_game_time,
            live_world_version=turn_snapshot.live_world_version,
            mind_state_version=heroine.version,
            world_state=turn_snapshot.world_state,
            protagonist=turn_snapshot.protagonist,
            scene=turn_snapshot.scene,
            heroine_runtime=heroine,
            source_event_ids=("event-user", "event-reply"),
        )
        backend = ScriptedChatBackend(
            [_reconcile_output(snapshot), _review_output(snapshot, "approve")]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        source_turn = ReconcileSourceTurn(
            request_id="turn-source",
            user_event_id="event-user",
            assistant_event_id="event-reply",
            protagonist_utterance="你是不是有点担心我？",
            heroine_reply="吃慢一点。",
        )
        result = await model.reconcile_mind(
            ReconcileMindRequest(
                mode=POST_REPLY_WORLD_MIND_RECONCILE,
                prompt=prompt,
                snapshot=snapshot,
                elapsed_game_seconds=0.0,
                source_turn=source_turn,
            )
        )
        proposed = result.patch.apply(heroine)
        review = await model.review_reconciliation(
            ReconcileReviewRequest(
                mode=POST_REPLY_WORLD_MIND_RECONCILE,
                prompt=prompt,
                snapshot=snapshot,
                previous_state=heroine,
                proposed_state=proposed,
                reconcile_result=result,
            )
        )
        assert result.operation == "update"
        assert result.patch.living_mind.emotion == "克制的关心"
        assert review.decision == "approve"
        assert len(backend.calls) == 2
        reconcile_call, review_call = backend.calls
        assert "POST_REPLY_WORLD_MIND_RECONCILE" in reconcile_call["request_id"]
        assert reconcile_call["response_format"]["json_schema"]["name"] == (
            "background_mind_patch_b1"
        )
        reconcile_schema = reconcile_call["response_format"]["json_schema"]["schema"]
        assert reconcile_schema["properties"]["c"]["maxItems"] == 5
        reconcile_payload = json.loads(reconcile_call["messages"][-1]["content"])
        assert reconcile_payload["p"] == "B1"
        assert "snapshot_id" not in reconcile_call["messages"][-1]["content"]
        assert reconcile_call["options"].max_tokens == 320
        review_payload = json.loads(review_call["messages"][-1]["content"])
        assert review_payload["review_target_mode"] == POST_REPLY_WORLD_MIND_RECONCILE
        assert reconcile_call["options"].temperature > review_call["options"].temperature

    asyncio.run(scenario())


def test_real_gateway_runs_three_isolated_modes_and_critic_revision() -> None:
    async def scenario() -> None:
        prompt, previous, snapshot = _requests()
        backend = ScriptedChatBackend(
            [
                _mind_output(snapshot),
                _review_output(snapshot),
                _reply_output(snapshot, previous.version + 1),
            ]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        await model.start()
        try:
            mind = await model.advance_mind(MindAdvanceRequest(prompt, snapshot))
            proposed = mind.patch.apply(previous)
            review = await model.review_continuity(
                ContinuityReviewRequest(
                    prompt,
                    snapshot,
                    previous,
                    proposed,
                    mind,
                )
            )
            approved = review.revision_patch.revise(proposed)
            reply = await model.generate_reply(
                GameReplyRequest(
                    prompt=prompt,
                    snapshot=snapshot,
                    approved_state=approved,
                    reply_intent=mind.reply_intent,
                    fact_assertions=mind.fact_assertions,
                    approved_actions=mind.heroine_diegetic_actions,
                )
            )
        finally:
            await model.close()

        assert mind.patch.motive_updates["care_for_protagonist"]
        assert mind.patch.knowledge_updates["protagonist_likes_rain"]
        assert review.decision == "revise"
        assert approved.living_mind.emotion == "克制的担心"
        assert reply.approved_mind_state_version == approved.version
        assert backend.start_calls == 1
        assert backend.close_calls == 1
        assert len(backend.calls) == 3
        assert backend.calls[2]["request_id"].endswith(":GAME_REPLY:0")
        assert backend.calls[2]["messages"][-1] == {
            "role": "user",
            "content": snapshot.protagonist_utterance,
        }
        mind_payload = json.loads(backend.calls[0]["messages"][-1]["content"])
        review_payload = json.loads(backend.calls[1]["messages"][-1]["content"])
        expected_refs = [
            snapshot.snapshot_id,
            *snapshot.heroine_runtime.evidence_refs[-8:],
        ]
        assert (
            mind_payload["evidence_contract"]["allowed_evidence_refs"]
            == expected_refs
        )
        assert (
            review_payload["evidence_contract"]["allowed_evidence_refs"]
            == expected_refs
        )
        assert "禁止写 snapshot" in backend.calls[0]["messages"][0]["content"]
        mind_schema = backend.calls[0]["response_format"]["json_schema"]["schema"]
        assert "enum" not in mind_schema["properties"]["transition_basis"]["items"]
        assert "enum" not in mind_schema["$defs"]["patch"]["properties"][
            "evidence_refs"
        ]["items"]
        assert "enum" not in mind_schema["$defs"]["action"]["properties"][
            "evidence_refs"
        ]["items"]
        assert mind_schema["properties"]["snapshot_id"]["const"] == (
            snapshot.snapshot_id
        )
        assert mind_schema["properties"]["parent_mind_state_version"]["const"] == (
            snapshot.mind_state_version
        )
        mind_assertions = mind_schema["$defs"]["assertions"]["properties"]
        assert mind_assertions["protagonist_activity"]["const"] == (
            snapshot.protagonist.activity
        )
        assert mind_assertions["captured_game_time"]["const"] == (
            snapshot.captured_game_time.isoformat(timespec="microseconds")
        )
        assert backend.calls[2]["response_format"] is None
        reply_context = backend.calls[2]["messages"][0]["content"]
        assert "[当前世界]" in reply_context
        assert "正在吃面" in reply_context
        assert "[你此刻的状态]" in reply_context
        assert "情绪：克制的担心" in reply_context
        assert "轻轻看向男主手里的筷子" in reply_context
        for forbidden in (
            "approved_heroine_runtime",
            "save_id",
            "snapshot_id",
            "live_world_version",
            "mind_state_version",
            "selector_version",
            "projection_counts",
            "evidence_refs",
        ):
            assert forbidden not in reply_context
        assert len({call["options"].temperature for call in backend.calls}) == 3
        assert all(
            call["response_format"]["type"] == "json_schema"
            for call in backend.calls[:2]
        )

    asyncio.run(scenario())


def test_real_gateway_retries_invalid_json_once() -> None:
    async def scenario() -> None:
        prompt, _, snapshot = _requests()
        backend = ScriptedChatBackend(
            [RuntimeError("temporary failure"), _mind_output(snapshot)]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        result = await model.advance_mind(MindAdvanceRequest(prompt, snapshot))
        assert result.snapshot_id == snapshot.snapshot_id
        assert len(backend.calls) == 2

    asyncio.run(scenario())


def test_real_gateway_exposes_classified_failure_and_attempt_audit() -> None:
    async def scenario() -> None:
        prompt, _, snapshot = _requests()
        audits = []
        backend = ScriptedChatBackend(["not-json", "still-not-json"])
        model = LlamaCppWorldMindModel(
            backend,
            identity=_identity(),
            attempt_observer=audits.append,
        )
        with pytest.raises(WorldMindModelError) as captured:
            await model.advance_mind(MindAdvanceRequest(prompt, snapshot))
        assert captured.value.code == "model_invalid_json"
        assert captured.value.mode == "TURN_MIND_ADVANCE"
        assert [item.failure_code for item in audits] == [
            "model_invalid_json",
            "model_invalid_json",
        ]

    asyncio.run(scenario())


def test_real_gateway_rejects_snapshot_drift() -> None:
    async def scenario() -> None:
        prompt, _, snapshot = _requests()
        output = _mind_output(snapshot)
        output["snapshot_id"] = "other-snapshot"
        backend = ScriptedChatBackend([output, output])
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        with pytest.raises(WorldMindModelError) as captured:
            await model.advance_mind(MindAdvanceRequest(prompt, snapshot))
        assert isinstance(captured.value.__cause__, WorldMindModelOutputError)
        assert "snapshot_id" in str(captured.value.__cause__)

    asyncio.run(scenario())


def test_real_gateway_keeps_large_evidence_allow_list_out_of_schema() -> None:
    async def scenario() -> None:
        prompt, heroine, snapshot = _requests()
        historical_refs = tuple(f"memory-{index:04d}" for index in range(500))
        heroine = replace(heroine, evidence_refs=historical_refs)
        snapshot = replace(snapshot, heroine_runtime=heroine)
        backend = ScriptedChatBackend([_mind_output(snapshot)])
        model = LlamaCppWorldMindModel(backend, identity=_identity())

        await model.advance_mind(MindAdvanceRequest(prompt, snapshot))

        payload = json.loads(backend.calls[0]["messages"][-1]["content"])
        schema = backend.calls[0]["response_format"]["json_schema"]["schema"]
        encoded_schema = json.dumps(schema)
        assert payload["evidence_contract"]["allowed_evidence_refs"] == [
            snapshot.snapshot_id,
            *historical_refs[-8:],
        ]
        assert payload["previous_heroine_runtime"]["evidence_refs"] == list(
            historical_refs[-8:]
        )
        assert "memory-0000" not in encoded_schema
        assert "memory-0499" not in encoded_schema
        assert len(encoded_schema) < 10_000

    asyncio.run(scenario())


def test_hard_validator_rejects_unknown_evidence_without_schema_enum() -> None:
    async def scenario() -> None:
        prompt, heroine, snapshot = _requests()
        backend = ScriptedChatBackend([_mind_output(snapshot) | {
            "heroine_patch": _patch(snapshot, emotion="明显担心", evidence=False)
        }])
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        result = await model.advance_mind(MindAdvanceRequest(prompt, snapshot))
        proposed = result.patch.apply(heroine)

        with pytest.raises(HardInvariantError, match="unknown IDs"):
            HardInvariantValidator().validate_mind_advance(
                snapshot,
                heroine,
                proposed,
                result,
            )

    asyncio.run(scenario())


def test_real_gateway_runs_inside_world_mind_runtime_and_commits_all_modes(
    tmp_path,
) -> None:
    async def scenario() -> None:
        def response_for(call):
            if call["request_id"].endswith(":GAME_REPLY:0"):
                return "吃慢一点。"
            payload = json.loads(call["messages"][-1]["content"])
            assert payload["p"] == "M2"
            return {
                "c": [
                    [2, "克制的关心", [0, 1]],
                    [5, "提醒男主慢一点", [0, 1]],
                ]
            }

        backend = ScriptedChatBackend([response_for, response_for])
        real_model = LlamaCppWorldMindModel(backend, identity=_identity())
        harness = build_runtime_harness(tmp_path, model=real_model)
        harness.runtime.config = replace(
            harness.runtime.config,
            foreground_protocol="mind_patch_v2",
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest(
                    request_id="real-gateway-runtime",
                    session=session(),
                    text="你是不是有点担心我？",
                )
            )
            assert isinstance(result, Completed)
            assert result.text == "吃慢一点。"
            current = harness.store.load_heroine_runtime(session())
            assert current is not None
            assert current.living_mind.emotion == "克制的关心"
            assert harness.store.count_model_decisions("save_001") == 1
            assert len(backend.calls) == 2
            assert backend.calls[0]["response_format"] is not None
            assert backend.calls[1]["response_format"] is None
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def _m2_keep_result_fields(snapshot):
    """构造与 compile_m2 输出一致的合法 M2 结果（用于降级后 GAME_REPLY 正常）。"""
    return {
        "c": [],
    }


def test_real_gateway_m2_duplicate_alias_falls_back_to_keep() -> None:
    """M2 重复证据别名（语义非法 Patch）重试耗尽后降级为空 Patch，不整轮失败。"""
    async def scenario() -> None:
        prompt, _heroine, snapshot = _requests()
        snapshot = replace(
            snapshot,
            protagonist_utterance_event_id="event-preallocated-user",
        )
        backend = ScriptedChatBackend(
            [
                {"c": [[5, "提醒男主慢一点吃", [1, 1]]]},  # duplicate aliases
                {"c": [[5, "提醒男主慢一点吃", [1, 1]]]},
            ]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        result = await model.propose_mind_patch_v2(
            MindPatchV2Request(prompt, snapshot)
        )
        # 降级结果：空 Patch，不改变字段
        assert result.changed_field_codes == ()
        assert result.mind_result.patch.living_mind.immediate_intent is None
        assert result.mind_result.patch.evidence_refs == (snapshot.snapshot_id,)
        assert result.mind_result.snapshot_id == snapshot.snapshot_id
        assert result.mind_result.parent_mind_state_version == snapshot.mind_state_version
        assert result.raw_output is not None
        assert result.raw_output["fallback"] == "m2_protocol_keep"
        # 不触发 Critic
        assert result.review_recommended is False
        assert len(backend.calls) == 2

    asyncio.run(scenario())


def test_real_gateway_m2_field_name_as_value_falls_back_to_keep() -> None:
    """M2 把字段名当成字段值（README 记录的 4B 长测失败模式）降级为空 Patch。"""
    async def scenario() -> None:
        prompt, _heroine, snapshot = _requests()
        snapshot = replace(
            snapshot,
            protagonist_utterance_event_id="event-preallocated-user",
        )
        # 编译错误：code 5 但 change 长度不是 3（字段名被当作字段值导致结构错误）
        backend = ScriptedChatBackend(
            [
                {"c": [[5, "immediate_intent", "提醒男主慢一点吃", [0, 1]]]},
                {"c": [[5, "immediate_intent", "提醒男主慢一点吃", [0, 1]]]},
            ]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        result = await model.propose_mind_patch_v2(
            MindPatchV2Request(prompt, snapshot)
        )
        assert result.changed_field_codes == ()
        assert result.raw_output["fallback"] == "m2_protocol_keep"

    asyncio.run(scenario())


def test_real_gateway_m2_service_failure_still_fails() -> None:
    """服务/传输故障（非协议错误）不降级，保持整轮失败语义。"""
    async def scenario() -> None:
        prompt, _heroine, snapshot = _requests()
        backend = ScriptedChatBackend(
            [TimeoutError("M2 timed out"), TimeoutError("M2 timed out")]
        )
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        with pytest.raises(WorldMindModelError) as captured:
            await model.propose_mind_patch_v2(
                MindPatchV2Request(prompt, snapshot)
            )
        assert captured.value.code == "model_timeout"

    asyncio.run(scenario())


def test_real_gateway_m2_json_garbage_still_fails() -> None:
    """模型输出不可解析 JSON（截断/乱码）不降级——README 冻结决策要求
    截断文本按协议失败关闭，不得当作成功。"""
    async def scenario() -> None:
        prompt, _heroine, snapshot = _requests()
        backend = ScriptedChatBackend(["not-json", "still-not-json"])
        model = LlamaCppWorldMindModel(backend, identity=_identity())
        with pytest.raises(WorldMindModelError) as captured:
            await model.propose_mind_patch_v2(
                MindPatchV2Request(prompt, snapshot)
            )
        assert captured.value.code == "model_invalid_json"

    asyncio.run(scenario())
