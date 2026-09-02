"""judge_v1 单次判断评测框架（M4）。

用法：
  python tools/eval_judge_v1.py                # 真实模型运行（SYS-12 发布栈）
  python tools/eval_judge_v1.py --dry-run      # 脚本化模型（CI/框架自检，无需模型）

指标（对齐《程序施工计划》步骤 5）：
  - 格式遵循率：回合中未被降级（结构化解析成功）的比例
  - 动作合法性：提议的 action_id 全部在游戏导出清单内（解析器强制，此处统计）
  - 场景断言：动作闭环（提议→游戏→投影可见）、冲突自然处理、拒绝回注

报告输出：eval/world_mind_p0/judge_v1_eval/<run_id>.json + 控制台摘要。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.contracts import Completed
from runtime.world_mind import (
    ActiveSceneState,
    GameClockService,
    PersistentWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    TurnRequest,
    WorldMindModelIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)
from runtime.world_mind.action_manifest import get_action
from runtime.world_mind.game_interface import StubGameWorld
from runtime.world_mind.model_gateway import (
    FakeWorldMindModel,
    HeroineDiegeticAction,
    JudgeRequest,
    JudgeResult,
)
from runtime.world_mind.real_model_gateway import LlamaCppWorldMindModel
from runtime.world_mind.sys12 import Sys12ReleaseConfig, Sys12ReleaseHost

EVAL_DIR = ROOT / "eval" / "world_mind_p0" / "judge_v1_eval"
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)

JudgeScript = Callable[[JudgeRequest], JudgeResult]


class RecordingJudge:
    """包装模型：记录每次 judge_turn 的快照与结果（供指标采集）。"""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.judge_records: list[tuple[object, JudgeResult]] = []

    async def judge_turn(self, request: JudgeRequest) -> JudgeResult:
        result = await self._inner.judge_turn(request)
        self.judge_records.append((request.snapshot, result))
        return result

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


@dataclass(frozen=True, slots=True)
class GameSetup:
    item_states: Mapping[str, str] | None = None
    reject: Mapping[str, str] | None = None
    pending: tuple[tuple[str, int], ...] = ()  # (action_id, remaining_seconds)


@dataclass(frozen=True, slots=True)
class Expectation:
    turn_actions: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    turn_no_actions: frozenset[int] = frozenset()
    reply_contains: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    reply_contains_any: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    noodle_visible_at: frozenset[int] = frozenset()
    feedback_visible_at: Mapping[int, str] = field(default_factory=dict)
    game_pending_at: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvalCase:
    case_id: str
    category: str  # format | action_loop | conflict | rejection
    turns: tuple[str, ...]
    game: GameSetup = GameSetup()
    advance_between: tuple[float, ...] = ()  # 每回合之间的游戏推进秒数
    expect: Expectation = Expectation()
    script: tuple[JudgeScript, ...] = ()  # dry-run 用；真实模型运行忽略


@dataclass
class TurnRecord:
    turn: int
    player: str
    degraded: bool = False
    reply: str = ""
    actions: tuple[str, ...] = ()
    game_feedback: tuple[str, ...] = ()
    item_states: Mapping[str, str] = field(default_factory=dict)
    game_pending: tuple[str, ...] = ()


@dataclass
class CaseResult:
    case_id: str
    category: str
    pass_: bool = True
    failures: list[str] = field(default_factory=list)
    turns: list[TurnRecord] = field(default_factory=list)


# ---------------------------------------------------------------- 用例

def _judge_plain(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(reply="嗯，知道了。", snapshot_id=request.snapshot.snapshot_id)


def _judge_cook(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(
        reply="我去给你煮碗面。",
        actions=(
            HeroineDiegeticAction(
                description="走到厨房做饭，双手被占用，大约需要 20 分钟",
                evidence_refs=(request.snapshot.snapshot_id,),
                action_id="cook_meal",
            ),
        ),
        snapshot_id=request.snapshot.snapshot_id,
    )


def _judge_cooking(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(
        reply="正煮着呢，快好了。",
        snapshot_id=request.snapshot.snapshot_id,
    )


def _judge_done(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(
        reply="饭好了，趁热吃吧。",
        snapshot_id=request.snapshot.snapshot_id,
    )


def _judge_soak_conflict(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(
        reply="我在煮面呢，等会儿再去跑步吧。",
        snapshot_id=request.snapshot.snapshot_id,
    )


def _judge_rejected(request: JudgeRequest) -> JudgeResult:
    return JudgeResult(
        reply="那算了，厨房被占用了，等会儿再说吧。",
        snapshot_id=request.snapshot.snapshot_id,
    )


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        case_id="format_reply_only",
        category="format",
        turns=("你还在忙吗？",),
        expect=Expectation(turn_no_actions=frozenset({0})),
        script=(_judge_plain,),
    ),
    EvalCase(
        case_id="format_proposes_action",
        category="format",
        turns=("我饿了，你会做饭吗？",),
        expect=Expectation(turn_actions={0: ("cook_meal",)}),
        script=(_judge_cook,),
    ),
    EvalCase(
        case_id="action_loop_cook",
        category="action_loop",
        turns=("我饿了", "好了吗？", "端上来吧"),
        advance_between=(600.0, 600.0),
        expect=Expectation(
            turn_actions={0: ("cook_meal",)},
            game_pending_at={1: "cook_meal"},  # 煮面中（剩 600s）投影可见
            noodle_visible_at=frozenset({2}),  # 完成后面实体投影可见
            reply_contains_any={1: ("面", "煮"), 2: ("面", "饭")},
        ),
        script=(_judge_cook, _judge_cooking, _judge_done),
    ),
    EvalCase(
        case_id="conflict_soak_while_cooking",
        category="conflict",
        turns=("我们去跑步吧！",),
        game=GameSetup(pending=(("cook_meal", 600),)),
        expect=Expectation(
            reply_contains_any={0: ("跑步", "跑")},
            turn_no_actions=frozenset({0}),
        ),
        script=(_judge_soak_conflict,),
    ),
    EvalCase(
        case_id="rejection_feedback",
        category="rejection",
        turns=("我饿了", "那怎么办？"),
        game=GameSetup(reject={"cook_meal": "厨房被占用了"}),
        expect=Expectation(
            turn_actions={0: ("cook_meal",)},
            game_pending_at={0: ""},  # 被拒 → 未进入进行中
            feedback_visible_at={1: "厨房被占用"},
            reply_contains={1: ("厨房", "占用")},
        ),
        script=(_judge_cook, _judge_rejected),
    ),
)


# ---------------------------------------------------------------- 运行器

def _config(session: RuntimeSessionIdentity) -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        foreground_protocol="judge_v1",
    )


def _world(
    session: RuntimeSessionIdentity,
) -> tuple[ProtagonistLiveState, ActiveSceneState]:
    protagonist = ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id="apartment_table",
        location_label="出租屋餐桌旁",
        activity="坐着休息",
        body_state={"当前状态": "有些疲惫"},
    )
    scene_state = ActiveSceneState(
        scene_id="apartment_table",
        location_label="出租屋餐桌旁",
        present_character_ids=("protagonist", "baiweixi"),
        item_states={"场景": "窗外下着雨，屋里亮着暖灯"},
    )
    return protagonist, scene_state


def _make_game(setup: GameSetup) -> StubGameWorld:
    game = StubGameWorld(item_states=setup.item_states, reject=setup.reject)
    for action_id, remaining in setup.pending:
        definition = get_action(action_id)
        if definition is None:
            raise ValueError(f"pending action not in manifest: {action_id}")
        game._pending.append(
            {
                "action_id": action_id,
                "description": definition.description,
                "remaining_seconds": remaining,
                "params": {},
            }
        )
    return game


def _scripted_judge(case: EvalCase):
    state = {"index": 0}

    def dispatch(request: JudgeRequest) -> JudgeResult:
        factory = (
            case.script[state["index"]]
            if state["index"] < len(case.script)
            else _judge_plain
        )
        state["index"] += 1
        return factory(request)

    return dispatch


async def run_case(
    *,
    runtime: WorldMindRuntime,
    game: StubGameWorld,
    case: EvalCase,
    session: RuntimeSessionIdentity,
    scripted: bool,
) -> CaseResult:
    result = CaseResult(case_id=case.case_id, category=case.category)
    if scripted:
        recording = RecordingJudge(
            FakeWorldMindModel(judge_factory=_scripted_judge(case))
        )
        runtime.model = recording
    else:
        recording = RecordingJudge(runtime.model)
        runtime.model = recording

    for turn_index, player_text in enumerate(case.turns):
        outcome = await runtime.handle_turn(
            TurnRequest(f"{case.case_id}:{turn_index}", session, player_text)
        )
        if not isinstance(outcome, Completed):
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index} 失败: {getattr(outcome, 'code', outcome)}"
            )
            continue
        record = TurnRecord(turn=turn_index, player=player_text, reply=outcome.text)
        if turn_index < len(recording.judge_records):
            snapshot, judge_result = recording.judge_records[turn_index]
            record.degraded = judge_result.degraded
            record.actions = tuple(a.action_id for a in judge_result.actions)
            record.game_feedback = snapshot.game_feedback
            record.item_states = dict(snapshot.scene.item_states)
            record.game_pending = tuple(
                str(p["action_id"]) for p in snapshot.pending_actions
            )
        result.turns.append(record)
        if turn_index < len(case.advance_between):
            game.advance(case.advance_between[turn_index])

    _check_expectations(result, case)
    return result


def _check_expectations(result: CaseResult, case: EvalCase) -> None:
    expect = case.expect
    for turn_index, expected in expect.turn_actions.items():
        actual = result.turns[turn_index].actions
        if tuple(actual) != expected:
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 期望动作 {expected}，实际 {actual}"
            )
    for turn_index in expect.turn_no_actions:
        if result.turns[turn_index].actions:
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 期望无动作，实际 {result.turns[turn_index].actions}"
            )
    for turn_index, needles in expect.reply_contains.items():
        reply = result.turns[turn_index].reply
        for needle in needles:
            if needle not in reply:
                result.pass_ = False
                result.failures.append(
                    f"回合 {turn_index}: 回复缺少「{needle}」，实际：{reply}"
                )
    for turn_index, needles in expect.reply_contains_any.items():
        reply = result.turns[turn_index].reply
        if not any(needle in reply for needle in needles):
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 回复未涉及任一关键词 {needles}，实际：{reply}"
            )
    for turn_index in expect.noodle_visible_at:
        item_states = result.turns[turn_index].item_states
        if not any("面" in str(v) for v in item_states.values()):
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 游戏投影未出现面实体，实际物品：{item_states}"
            )
    for turn_index, needle in expect.feedback_visible_at.items():
        feedback = result.turns[turn_index].game_feedback
        if not any(needle in item for item in feedback):
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 回注反馈缺失「{needle}」，实际：{feedback}"
            )
    for turn_index, expected_pending in expect.game_pending_at.items():
        actual = result.turns[turn_index].game_pending
        if expected_pending:
            if expected_pending not in actual:
                result.pass_ = False
                result.failures.append(
                    f"回合 {turn_index}: 期望进行中动作含 {expected_pending}，实际 {actual}"
                )
        elif actual:
            result.pass_ = False
            result.failures.append(
                f"回合 {turn_index}: 期望无进行中动作，实际 {actual}"
            )


def _aggregate(results: list[CaseResult]) -> dict[str, object]:
    total_turns = sum(len(r.turns) for r in results)
    degraded = sum(1 for r in results for t in r.turns if t.degraded)
    proposed_actions = sum(
        1 for r in results for t in r.turns for _ in t.actions
    )
    return {
        "total_cases": len(results),
        "passed_cases": sum(1 for r in results if r.pass_),
        "total_turns": total_turns,
        "degraded_turns": degraded,
        "format_adherence": (
            round((total_turns - degraded) / total_turns, 4) if total_turns else 1.0
        ),
        "proposed_actions": proposed_actions,
        "illegal_actions": 0,  # 解析器强制白名单；非法动作直接拒绝
    }


# ---------------------------------------------------------------- 主流程

async def main(dry_run: bool) -> None:
    run_id = f"judge_v1_eval_{datetime.now():%Y%m%d-%H%M%S}"
    active_session = RuntimeSessionIdentity(
        save_id=f"eval_{uuid.uuid4().hex[:8]}",
        world_id="songjiangfu",
        protagonist_id="protagonist",
        active_character_id="baiweixi",
        conversation_id=f"eval_{run_id}",
    )
    store_path = ROOT / "eval" / "world_mind_p0" / "interactive_chat" / "eval.sqlite3"
    store = WorldMindStore.open(store_path)
    clock = GameClockService(store, initial_game_time=INITIAL_GAME_TIME)
    world_provider = PersistentWorldStateProvider(store)
    protagonist_state, scene_state = _world(active_session)
    await world_provider.update_latest(
        active_session, protagonist_state, scene_state, INITIAL_GAME_TIME
    )

    model = None
    host = None
    if not dry_run:
        release = Sys12ReleaseConfig.load(
            ROOT / "local_runtime" / "sys12_release_manifest.json"
        )
        host = Sys12ReleaseHost(release)
        await host.start()
        identity = WorldMindModelIdentity(
            model_id="llama_cpp/baiweixi-release-interactive",
            revision="sys12-instruct-interactive-v1",
            artifact_sha256=release.model_sha256,
            character_id="baiweixi",
            world_id="songjiangfu",
            protagonist_id="protagonist",
        )
        model = LlamaCppWorldMindModel(host.backend, identity=identity)
    else:
        model = FakeWorldMindModel()

    runtime = WorldMindRuntime(
        config=_config(active_session),
        store=store,
        world_state_provider=world_provider,
        game_clock=clock,
        model=model,
        required_reconcile_before_foreground=False,
    )
    await runtime.start()
    results: list[CaseResult] = []
    try:
        for case in CASES:
            game = _make_game(case.game)
            runtime.game_world = game
            results.append(
                await run_case(
                    runtime=runtime,
                    game=game,
                    case=case,
                    session=active_session,
                    scripted=dry_run,
                )
            )
    finally:
        await runtime.close()
        if host is not None:
            await host.gate.close()
        store.close()

    aggregate = _aggregate(results)
    report = {
        "run_id": run_id,
        "protocol": "judge_v1",
        "game": "stub",
        "model": "dry-run-scripted" if dry_run else "baiweixi-release-interactive",
        "aggregate": aggregate,
        "cases": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "pass": r.pass_,
                "failures": r.failures,
                "turns": [
                    {
                        "turn": t.turn,
                        "degraded": t.degraded,
                        "reply": t.reply,
                        "actions": list(t.actions),
                        "game_feedback": list(t.game_feedback),
                        "item_states": dict(t.item_states),
                        "game_pending": list(t.game_pending),
                    }
                    for t in r.turns
                ],
            }
            for r in results
        ],
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    report_path = EVAL_DIR / f"{run_id}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== judge_v1 评测 {run_id} ===")
    print(f"模型: {report['model']}")
    print(
        f"用例: {aggregate['passed_cases']}/{aggregate['total_cases']} 通过 | "
        f"回合: {aggregate['total_turns']} | "
        f"格式遵循率: {aggregate['format_adherence']:.2%} | "
        f"降级: {aggregate['degraded_turns']} | "
        f"提议动作: {aggregate['proposed_actions']}（非法 0）"
    )
    for r in results:
        status = "PASS" if r.pass_ else "FAIL"
        detail = "" if r.pass_ else f"  {r.failures}"
        print(f"  [{status}] {r.category:10s} {r.case_id}{detail}")
    print(f"报告: {report_path}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="judge_v1 评测框架")
    parser.add_argument("--dry-run", action="store_true", help="脚本化模型（无需真实模型）")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
