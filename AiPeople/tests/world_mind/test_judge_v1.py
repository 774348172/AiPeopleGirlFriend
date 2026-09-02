from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from runtime.contracts import Completed, Failed
from runtime.world_mind import (
    CharacterPackagePromptComposer,
    FakeWorldMindModel,
    HeroineRuntime,
    ModelReadableWorldState,
    TurnRequest,
    TurnWorldSnapshot,
    WorldMindModelOutputError,
)
from runtime.world_mind.model_gateway import (
    HeroineDiegeticAction,
    JudgeRequest,
    JudgeResult,
)
from runtime.world_mind.model_payloads import judge_payload
from runtime.world_mind.real_model_gateway import (
    LlamaCppWorldMindModel,
    WorldMindModelIdentity,
    _checked_judge_result,
)
from runtime.world_mind.state import HeroineMindPatch, LivingMindPatch
from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    runtime_config,
    scene,
    session,
)

JUDGE_OUTPUT_KEYS = {
    "schema_version",
    "mode",
    "snapshot_id",
    "reply",
    "mind_patch",
    "actions",
}


def _judge_output(snapshot: TurnWorldSnapshot, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "mode": "JUDGE_TURN",
        "snapshot_id": snapshot.snapshot_id,
        "reply": "我去做饭。",
        "mind_patch": {
            "living_mind_patch": {
                "form": None,
                "body": None,
                "emotion": "有点饿了",
                "attention": None,
                "current_activity": None,
                "immediate_intent": None,
            },
            "relationship_patch": {
                "stage": None,
                "trust": None,
                "unresolved_tension": None,
            },
            "motive_updates": {},
            "knowledge_updates": {},
            "evidence_refs": [snapshot.snapshot_id],
        },
        "actions": [{"action_id": "cook_meal", "params": None}],
    }
    value.update(overrides)
    return value


def _snapshot(snapshot_id: str = "snapshot-judge") -> TurnWorldSnapshot:
    active_session = session()
    composer = CharacterPackagePromptComposer(runtime_config())
    heroine = HeroineRuntime.from_seed(
        active_session,
        composer.initial_runtime_seed("baiweixi"),
    )
    return TurnWorldSnapshot(
        snapshot_id=snapshot_id,
        request_id="request-judge",
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
        protagonist_utterance="我饿了",
        protagonist_utterance_event_id="event-user",
    )


def test_judge_result_parses_full_output() -> None:
    snapshot = _snapshot()
    result = _checked_judge_result(_judge_output(snapshot), snapshot.snapshot_id)
    assert result.reply == "我去做饭。"
    assert result.snapshot_id == snapshot.snapshot_id
    assert result.degraded is False
    assert result.mind_patch is not None
    assert result.mind_patch.living_mind.emotion == "有点饿了"
    assert len(result.actions) == 1
    assert result.actions[0].action_id == "cook_meal"
    assert result.actions[0].description == "走到厨房做饭，双手被占用，大约需要 20 分钟"


def test_judge_result_parses_reply_only() -> None:
    snapshot = _snapshot()
    output = _judge_output(snapshot)
    output["mind_patch"] = None
    output["actions"] = []
    result = _checked_judge_result(output, snapshot.snapshot_id)
    assert result.mind_patch is None
    assert result.actions == ()


def test_judge_result_rejects_invalid_action_id() -> None:
    snapshot = _snapshot()
    output = _judge_output(snapshot)
    output["actions"] = [{"action_id": "order_food", "params": None}]
    with pytest.raises(WorldMindModelOutputError, match="whitelisted"):
        _checked_judge_result(output, snapshot.snapshot_id)


def test_judge_result_rejects_unknown_keys_and_drift() -> None:
    snapshot = _snapshot()
    output = _judge_output(snapshot)
    output["extra"] = 1
    with pytest.raises(WorldMindModelOutputError, match="do not match schema"):
        _checked_judge_result(output, snapshot.snapshot_id)
    drifted = _judge_output(snapshot)
    drifted["snapshot_id"] = "other-snapshot"
    with pytest.raises(WorldMindModelOutputError, match="mismatch"):
        _checked_judge_result(drifted, snapshot.snapshot_id)


def test_judge_payload_contains_context_blocks() -> None:
    snapshot = _snapshot()
    payload = judge_payload(snapshot, ("轮1", "轮2", "轮3", "轮4", "轮5", "轮6", "轮7"))
    assert "cook_meal" in payload["available_actions"]
    assert "走到厨房做饭" in payload["available_actions"]
    assert payload["pending_actions"] == []
    assert payload["current_protagonist_utterance"] == "我饿了"
    assert len(payload["recent_dialogue"]) == 6
    assert payload["snapshot"]["protagonist"]["location_id"] == "apartment_table"


def _judge_with_action_factory(action_id: str) -> object:
    def factory(request: JudgeRequest) -> JudgeResult:
        return JudgeResult(
            reply="我去煮面。",
            actions=(
                HeroineDiegeticAction(
                    description="走到厨房做饭，双手被占用，大约需要 20 分钟",
                    evidence_refs=(request.snapshot.snapshot_id,),
                    action_id=action_id,
                ),
            ),
            snapshot_id=request.snapshot.snapshot_id,
        )

    return factory


def test_judge_v1_review_enabled_approves_with_actions(tmp_path) -> None:
    async def scenario() -> None:
        fake = FakeWorldMindModel(
            judge_factory=_judge_with_action_factory("cook_meal")
        )
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.config = replace(
            harness.runtime.config,
            foreground_protocol="judge_v1",
            judge_review_enabled=True,
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("judge-review-1", session(), "我饿了")
            )
            assert isinstance(result, Completed)
            assert result.text == "我去煮面。"
            # 开关开启 + 有动作提议 → 触发一次定向审查
            assert len(fake.review_requests) == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_judge_v1_review_enabled_rejects_turn(tmp_path) -> None:
    async def scenario() -> None:
        fake = FakeWorldMindModel(
            judge_factory=_judge_with_action_factory("cook_meal"),
            continuity_approved=False,
        )
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.config = replace(
            harness.runtime.config,
            foreground_protocol="judge_v1",
            judge_review_enabled=True,
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("judge-review-2", session(), "我饿了")
            )
            assert isinstance(result, Failed)
            assert result.code == "continuity_rejected"
            assert len(fake.review_requests) == 1
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_judge_v1_review_disabled_by_default(tmp_path) -> None:
    async def scenario() -> None:
        fake = FakeWorldMindModel(
            judge_factory=_judge_with_action_factory("cook_meal")
        )
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("judge-review-3", session(), "我饿了")
            )
            assert isinstance(result, Completed)
            assert fake.review_requests == []
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_judge_v1_turn_end_to_end(tmp_path) -> None:
    async def scenario() -> None:
        def judge(request: JudgeRequest) -> JudgeResult:
            return JudgeResult(
                reply="我去给你做饭，等着。",
                mind_patch=HeroineMindPatch(
                    living_mind=LivingMindPatch(emotion="有点心疼"),
                    evidence_refs=(request.snapshot.snapshot_id,),
                ),
                actions=(
                    HeroineDiegeticAction(
                        description="走到厨房做饭，双手被占用，大约需要 20 分钟",
                        evidence_refs=(request.snapshot.snapshot_id,),
                        action_id="cook_meal",
                    ),
                ),
                snapshot_id=request.snapshot.snapshot_id,
            )

        harness = build_runtime_harness(
            tmp_path, model=None
        )
        from runtime.world_mind import FakeWorldMindModel

        fake = FakeWorldMindModel(judge_factory=judge)
        harness.model = fake
        harness.runtime.model = fake
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("judge-e2e", session(), "我饿了")
            )
            assert isinstance(result, Completed)
            assert result.text == "我去给你做饭，等着。"
            # 单次判断：不触发独立的 M2/Critic/GAME_REPLY 调用
            assert len(fake.judge_requests) == 1
            assert len(fake.reply_requests) == 0
            assert len(fake.review_requests) == 0
            # 心智 patch 已应用
            current = harness.store.load_heroine_runtime(session())
            assert current.living_mind.emotion == "有点心疼"
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


class _ScriptedBackend:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def complete_chat(
        self,
        *,
        request_id,
        messages,
        options,
        response_format=None,
    ) -> str:
        self.calls.append(
            {
                "request_id": request_id,
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


def test_judge_turn_degrades_to_plain_text_on_parse_failure() -> None:
    async def scenario() -> None:
        snapshot = _snapshot()
        composer = CharacterPackagePromptComposer(runtime_config())
        prompt = composer.compose(session())
        backend = _ScriptedBackend(
            [
                "not-json",
                "still-not-json",  # _run_parsed retries=1 → 两次失败
                "行吧，我去给你煮碗面。",  # 降级文本调用
            ]
        )
        model = LlamaCppWorldMindModel(
            backend,
            identity=WorldMindModelIdentity(
                model_id="test/judge",
                revision="test",
                artifact_sha256="a" * 64,
                character_id="baiweixi",
                world_id="songjiangfu",
                protagonist_id="protagonist",
            ),
        )
        result = await model.judge_turn(
            JudgeRequest(prompt=prompt, snapshot=snapshot)
        )
        assert result.degraded is True
        assert result.reply == "行吧，我去给你煮碗面。"
        assert result.mind_patch is None
        assert result.actions == ()
        # 前两次是结构化调用（带 json_schema），第三次是文本降级
        assert len(backend.calls) == 3
        assert backend.calls[0]["response_format"] is not None
        assert backend.calls[2]["response_format"] is None

    asyncio.run(scenario())
