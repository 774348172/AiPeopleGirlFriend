from __future__ import annotations

import asyncio
from dataclasses import replace

from runtime.contracts import Completed
from runtime.world_mind import FakeWorldMindModel
from runtime.world_mind.game_interface import (
    ActionOutcome,
    GameWorldProjection,
    StubGameWorld,
)
from runtime.world_mind.model_gateway import (
    HeroineDiegeticAction,
    JudgeRequest,
    JudgeResult,
)
from runtime.world_mind.model_payloads import judge_payload
from runtime.world_mind.turn_request import TurnRequest
from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    build_runtime_harness,
    protagonist,
    scene,
    session,
)


def test_stub_execute_and_complete() -> None:
    async def scenario() -> None:
        game = StubGameWorld()
        outcome = await game.execute_action("cook_meal", None)
        assert outcome == ActionOutcome(accepted=True)
        assert len(game.pending) == 1
        assert game.pending[0]["action_id"] == "cook_meal"
        assert game.pending[0]["remaining_seconds"] == 1200

        game.advance(600)
        assert game.pending[0]["remaining_seconds"] == 600

        game.advance(600)
        assert game.pending == []
        assert game.item_states == {"桌上": "一碗热汤面"}

        projection = await game.project()
        assert projection.item_states == {"桌上": "一碗热汤面"}
        assert projection.pending_actions == ()
        assert projection.heroine_activity is None

    asyncio.run(scenario())


def test_stub_rejects_unknown_configured_and_missing_params() -> None:
    async def scenario() -> None:
        game = StubGameWorld(reject={"move_to": "男主正在使用厨房"})

        unknown = await game.execute_action("fly_away", None)
        assert unknown.accepted is False
        assert "不在游戏清单内" in unknown.reason

        configured = await game.execute_action("move_to", {"target": "bedroom"})
        assert configured.accepted is False
        assert configured.reason == "男主正在使用厨房"

        missing = await game.execute_action("pick_up_item", None)
        assert missing.accepted is False
        assert "缺少必要参数" in missing.reason

        projection = await game.project()
        assert len(projection.recent_feedback) == 3
        assert any("不在游戏清单内" in item for item in projection.recent_feedback)
        assert any("男主正在使用厨房" in item for item in projection.recent_feedback)
        assert any("缺少必要参数" in item for item in projection.recent_feedback)

    asyncio.run(scenario())


def _judge_with_action(action_id: str, captured: list) -> object:
    def factory(request: JudgeRequest) -> JudgeResult:
        captured.append(request.snapshot)
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


def test_runtime_projects_game_state_into_snapshot(tmp_path) -> None:
    async def scenario() -> None:
        captured = []
        game = StubGameWorld(item_states={"桌上": "一碗热汤面"})
        fake = FakeWorldMindModel(judge_factory=_judge_with_action("cook_meal", captured))
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.game_world = game
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("game-proj-1", session(), "我饿了")
            )
            assert isinstance(result, Completed)
            snapshot = captured[0]
            # 区块 2：物品账来自游戏投影
            assert snapshot.scene.item_states == {"桌上": "一碗热汤面"}
            # 区块 6：进行中动作投影（本回合游戏无进行中动作）
            assert snapshot.pending_actions == ()
            assert snapshot.game_feedback == ()
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_runtime_forwards_actions_and_next_turn_sees_completion(tmp_path) -> None:
    async def scenario() -> None:
        captured = []
        game = StubGameWorld()
        fake = FakeWorldMindModel(judge_factory=_judge_with_action("cook_meal", captured))
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.game_world = game
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            # 回合 1：模型提议煮面 → 转发游戏 → 游戏登记进行中
            result1 = await harness.runtime.handle_turn(
                TurnRequest("game-fwd-1", session(), "我饿了")
            )
            assert isinstance(result1, Completed)
            assert game.execute_calls == [("cook_meal", None)]
            assert len(game.pending) == 1
            # 回合 1 快照：投影看到进行中动作（本回合开始时已登记前的状态为空）
            assert captured[0].pending_actions == ()

            # 游戏推进到完成 → 面实体生成
            game.advance(1200)
            assert game.item_states == {"桌上": "一碗热汤面"}

            # 回合 2：快照投影看到面实体
            captured.clear()
            result2 = await harness.runtime.handle_turn(
                TurnRequest("game-fwd-2", session(), "面好了吗")
            )
            assert isinstance(result2, Completed)
            assert captured[0].scene.item_states == {"桌上": "一碗热汤面"}
            assert captured[0].pending_actions == ()
            # judge payload 区块 2 含面实体
            payload = judge_payload(captured[0])
            assert payload["snapshot"]["scene"]["item_states"] == {
                "桌上": "一碗热汤面"
            }
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_runtime_rejection_feedback_roundtrip(tmp_path) -> None:
    async def scenario() -> None:
        captured = []
        game = StubGameWorld(reject={"cook_meal": "厨房被占用了"})
        fake = FakeWorldMindModel(judge_factory=_judge_with_action("cook_meal", captured))
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.game_world = game
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result1 = await harness.runtime.handle_turn(
                TurnRequest("game-rej-1", session(), "我饿了")
            )
            assert isinstance(result1, Completed)
            assert game.pending == []  # 被拒，未进入进行中

            # 回合 2：快照携带回注反馈
            captured.clear()
            result2 = await harness.runtime.handle_turn(
                TurnRequest("game-rej-2", session(), "那算了")
            )
            assert isinstance(result2, Completed)
            assert captured[0].game_feedback == ("动作被拒绝：厨房被占用了",)
            payload = judge_payload(captured[0])
            assert payload["game_feedback"] == ["动作被拒绝：厨房被占用了"]
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())


def test_runtime_projects_heroine_activity_during_action(tmp_path) -> None:
    async def scenario() -> None:
        captured = []
        game = StubGameWorld()
        await game.execute_action("cook_meal", None)
        game.advance(600)  # 煮面中，剩 10 分钟
        fake = FakeWorldMindModel(judge_factory=_judge_with_action("cook_meal", captured))
        harness = build_runtime_harness(tmp_path, model=fake)
        harness.runtime.game_world = game
        harness.runtime.config = replace(
            harness.runtime.config, foreground_protocol="judge_v1"
        )
        await harness.runtime.start()
        await harness.provider.update_latest(
            session(), protagonist(), scene(), INITIAL_GAME_TIME
        )
        try:
            result = await harness.runtime.handle_turn(
                TurnRequest("game-act-1", session(), "好了吗")
            )
            assert isinstance(result, Completed)
            snapshot = captured[0]
            # 区块 6：进行中动作投影（含剩余时间）
            assert snapshot.pending_actions[0]["action_id"] == "cook_meal"
            assert snapshot.pending_actions[0]["remaining_seconds"] == 600
            # 区块 4：current_activity 由游戏投影（煮面中）
            assert (
                snapshot.heroine_runtime.living_mind.current_activity
                == "走到厨房做饭，双手被占用，大约需要 20 分钟"
            )
            assert game.pending[0]["remaining_seconds"] == 600
        finally:
            await harness.runtime.close()
            harness.store.close()

    asyncio.run(scenario())
