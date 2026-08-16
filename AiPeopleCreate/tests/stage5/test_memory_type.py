"""记忆类型轴（T19，2026-08-14 §24）：memory_type 字段、推断表、timeline 注入与守卫。

覆盖：
- 推断表：池条目显式声明优先，未声明按 task_type 推断（persona/general/special/item）；
- PlanItemV4 序列化往返保留 memory_type；
- _distill_memory_facts：memory_pool 过滤 / profile_secret 剔除 / hint_only 门控 / 确定性；
- _check_memory_grounding：有支撑放行、无支撑拦截、无注入回忆拦截、非记忆任务不生效；
- 集成：special item 的生成 prompt 注入 MEMORY_FACTS；编造回忆 → style_failure 重试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.factory import (  # noqa: E402
    RecipeDrivenItemFactory,
    _infer_memory_type,
)
from data_gen_v4.adapters.modes.reply import (  # noqa: E402
    _check_memory_grounding,
    _distill_memory_facts,
)
from data_gen_v4.adapters.sources.registry import (  # noqa: E402
    CompositeSourceLoader,
    FilePackageRegistry,
)
from data_gen_v4.core.compiler import GenerationPlanCompiler  # noqa: E402
from data_gen_v4.core.plan import PlanItemV4, RunSpec  # noqa: E402
from gen_v4 import PROFILES_ROOT, ROOT as GEN_ROOT, build_package_set  # noqa: E402


# ───────────────────────── 推断表 ─────────────────────────


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        ("reply_identity", "persona"),
        ("reply_canon_qa", "persona"),
        ("reply_general", "general"),
        ("reply_safety", "general"),
        ("reply_memory", "special"),
        ("reply_item", "item"),
        ("reply_casual", None),
        ("reply_romance", None),
        ("reply_protective", None),
        ("unknown_future_type", None),
    ],
)
def test_inference_table(task_type, expected):
    assert _infer_memory_type(task_type) == expected


def test_pool_entry_explicit_memory_type_wins():
    factory = RecipeDrivenItemFactory()
    item = factory._item_from_sample(
        {"task_type": "reply_casual", "memory_type": "item", "topic": "t", "scene": "s",
         "player_view": "p"}, seed=1, index=0
    )
    assert item.memory_type == "item"


def test_real_compile_memory_type_distribution():
    """白未晞真实编译：memory_type 按池标注/推断表落地（15 类配额齐全）。"""
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    pools_path = PROFILES_ROOT / "baiweixi" / "pools.yaml"
    factory = RecipeDrivenItemFactory(pools_path=str(pools_path))
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(
        RunSpec(run_id="t19-baiweixi", seed=42),
        build_package_set(PROFILES_ROOT, "baiweixi"),
    )
    by_type: dict[str, list[str]] = {}
    for item in result.plan.items:
        by_type.setdefault(item.task_type, []).append(item.memory_type)
    # v0.4.0（2026-08-15）：配额 +1000 扩充后目标
    assert by_type["reply_item"] == ["item"] * 120
    assert by_type["reply_memory"] == ["special"] * 100
    assert by_type["reply_general"] == ["general"] * 80
    assert by_type["reply_identity"] == ["persona"] * 78
    assert by_type["reply_canon_qa"] == ["persona"] * 38
    # 未标注类型不参与记忆类型统计
    assert all(mt is None for mt in by_type["reply_casual"])


def test_qin_profile_no_regression():
    """秦未晞零改动回归：编译通过，无 reply_item/reply_memory，无 special 注入。

    推断表对秦同样生效（identity→persona 等属通用规则，只作标注不改变行为）；
    无回归的实质是：不新增记忆类型任务、不触发 timeline 注入。
    """
    registry = FilePackageRegistry(PROFILES_ROOT)
    loader = CompositeSourceLoader(GEN_ROOT)
    pools_path = PROFILES_ROOT / "qinweixi" / "pools.yaml"
    factory = RecipeDrivenItemFactory(
        pools_path=str(pools_path) if pools_path.exists() else None
    )
    compiler = GenerationPlanCompiler(registry, loader, factory)
    result = compiler.compile(
        RunSpec(run_id="t19-qinweixi", seed=42),
        build_package_set(PROFILES_ROOT, "qinweixi"),
    )
    types = {item.task_type for item in result.plan.items}
    assert "reply_memory" not in types and "reply_item" not in types
    assert all(item.memory_type != "special" for item in result.plan.items)


# ───────────────────────── PlanItemV4 序列化 ─────────────────────────


def _plan_item(memory_type: str | None = "item") -> PlanItemV4:
    return PlanItemV4(
        plan_id="plan-t19:0000",
        profile_id="baiweixi",
        profile_snapshot_id="snap:test",
        protocol_bundle_id="protocol:v1",
        recipe_id="recipe:t19",
        family_id="fam:1",
        question_family_id=None,
        scene_family_id=None,
        mode="REPLY",
        task_type="reply_item",
        family_role="standalone",
        knowledge_scope=["general_knowledge"],
        visibility_scope=["profile_public"],
        memory_type=memory_type,
        evidence_state="supported",
        desired_policy="answer",
        required_behaviors=[],
        forbidden_behaviors=[],
        expected_outcomes=[],
        refusal_required=False,
        fixture_id=None,
        fixture_hash=None,
        representation_ids=[],
        render_profile_id="reply-runtime-v1",
        prompt_template_version="reply-style-v1",
        config_hash="config:reply:v1",
        seed=1,
        candidate_count=2,
        max_attempts=3,
        risk_level="low",
        required_review="auto",
        split_anchor_ids=[],
    )


def test_plan_item_round_trip_preserves_memory_type():
    for mt in ("persona", "item", "general", "special", None):
        item = _plan_item(mt)
        back = PlanItemV4.from_dict(item.to_dict())
        assert back.memory_type == mt
        assert back.task_type == "reply_item"


# ───────────────────────── 快照 fixture ─────────────────────────


def _snapshots() -> dict:
    return {
        "snapshots": {
            "snap-bwx": {
                "snapshot_id": "snap-bwx",
                "units": [
                    {
                        "source_kind": "timeline_event",
                        "source_id": "ev:rain",
                        "occurred_at": "Day 0·暴雨夜",
                        "value": "在松江府过马路时被车辆擦碰，惊慌逃进小巷，最终缩在墙脚躲雨",
                        "visibility_scope": "profile_public",
                        "disclosure_policy": "direct_allowed",
                    },
                    {
                        "source_kind": "timeline_event",
                        "source_id": "ev:rescue",
                        "occurred_at": "Day 0·夜",
                        "value": "主角发现她，用外套包住她带回出租屋，安置在铺有旧衣物的纸箱中",
                        "visibility_scope": "profile_public",
                        "disclosure_policy": "direct_allowed",
                    },
                    {
                        "source_kind": "timeline_event",
                        "source_id": "ev:secret",
                        "occurred_at": "觉醒时",
                        "value": "上古妖族传承涌入意识，知道名字但不知道来源",
                        "visibility_scope": "profile_secret",
                        "disclosure_policy": "hint_only",
                    },
                    {
                        "source_kind": "timeline_event",
                        "source_id": "ev:hint",
                        "occurred_at": "Day 8-9",
                        "value": "第一次在主角仍清醒时于同一房间睡着",
                        "visibility_scope": "profile_private",
                        "disclosure_policy": "hint_only",
                    },
                ],
            }
        }
    }


# ───────────────────────── _distill_memory_facts ─────────────────────────


def test_distill_pool_filter_and_determinism():
    ctx = _snapshots()
    out1 = _distill_memory_facts(
        ctx, {"input": {"memory_pool": ["ev:rain", "ev:rescue"]}, "desired_policy": "answer", "seed": 1}
    )
    out2 = _distill_memory_facts(
        ctx, {"input": {"memory_pool": ["ev:rain", "ev:rescue"]}, "desired_policy": "answer", "seed": 1}
    )
    assert out1 == out2, "同 item 蒸馏结果必须确定"
    assert len(out1) == 2
    assert any("墙脚躲雨" in t for t in out1)
    assert any("外套包住" in t for t in out1)


def test_distill_secret_excluded():
    ctx = _snapshots()
    out = _distill_memory_facts(
        ctx, {"input": {"memory_pool": ["ev:secret"]}, "desired_policy": "answer", "seed": 1}
    )
    assert out == [], "profile_secret 事件不得注入"


def test_distill_hint_only_gated_by_policy():
    ctx = _snapshots()
    # desired=answer → hint_only 事件不注入
    out = _distill_memory_facts(
        ctx, {"input": {"memory_pool": ["ev:hint"]}, "desired_policy": "answer", "seed": 1}
    )
    assert out == []
    # desired=hint_only → 注入
    out = _distill_memory_facts(
        ctx, {"input": {"memory_pool": ["ev:hint"]}, "desired_policy": "hint_only", "seed": 1}
    )
    assert len(out) == 1 and "睡着" in out[0]


def test_distill_limited_to_three():
    ctx = _snapshots()
    out = _distill_memory_facts(
        ctx, {"input": {"memory_pool": []}, "desired_policy": "answer", "seed": 0}
    )
    # 无 pool：全库可注入（secret 剔除、hint 按 answer 门控）→ 2 条，不超过 3
    assert 1 <= len(out) <= 3


def test_distill_no_memory_for_non_special():
    # 非 special item 不调用蒸馏（memory_facts_raw 为空列表，走"（无）"分支）
    assert _distill_memory_facts({}, {"input": {}, "desired_policy": "answer", "seed": 1}) == []


# ───────────────────────── _check_memory_grounding ─────────────────────────


def _msgs(assistant_lines: list[str]) -> list[dict[str, str]]:
    return [{"role": "assistant", "content": t} for t in assistant_lines]


FACTS = ["[Day 0·暴雨夜] 在松江府过马路时被车辆擦碰，惊慌逃进小巷，最终缩在墙脚躲雨",
         "[Day 0·夜] 主角发现她，用外套包住她带回出租屋，安置在铺有旧衣物的纸箱中"]


def test_memory_grounding_grounded_allowed():
    # "那天"+"墙脚躲雨" 与注入事件有公共子串 → 放行
    assert _check_memory_grounding(_msgs(["记得，那天我缩在墙脚躲雨。"]), FACTS, "reply_memory") == []


def test_memory_grounding_ungrounded_rejected():
    # "那晚"+"打雷" 不在注入事件 → 拦截
    hits = _check_memory_grounding(_msgs(["那晚的雨特别大，还打了雷。"]), FACTS, "reply_memory")
    assert hits and "回忆无支撑" in hits[0]


def test_memory_grounding_comma_ride_rejected():
    # 2026-08-14 收紧：逗号连句时，有支撑片段不得给无支撑细节"搭车"
    # （"是你把我带回来的"有支撑，"那晚的雨确实很大"无支撑 → 整句拦截）
    hits = _check_memory_grounding(
        _msgs(["那晚的雨确实很大，是你把我带回来的。"]), FACTS, "reply_memory"
    )
    assert hits and "回忆无支撑" in hits[0]


def test_memory_grounding_comma_grounded_allowed():
    # 逗号分开的两个短句各自有支撑 → 放行
    text = "那晚我缩在墙脚躲雨，是你把我带回来的。"
    assert _check_memory_grounding(_msgs([text]), FACTS, "reply_memory") == []


def test_memory_grounding_no_injection_rejected():
    # 无注入事实却回忆 → 拦截
    hits = _check_memory_grounding(_msgs(["那天的事我都记得。"]), [], "reply_memory")
    assert hits and "无注入记忆却回忆" in hits[0]


def test_memory_grounding_only_for_reply_memory():
    # 非 reply_memory 任务不生效（含 reply_item / casual）
    assert _check_memory_grounding(_msgs(["那晚的雨特别大，还打了雷。"]), FACTS, "reply_item") == []
    assert _check_memory_grounding(_msgs(["那晚的雨特别大，还打了雷。"]), [], "reply_casual") == []


def test_memory_grounding_no_marker_allowed():
    # 无回忆标记的台词不触发检查
    assert _check_memory_grounding(_msgs(["雨停了，我去把窗户关上。"]), [], "reply_memory") == []


# ───────────────────────── 集成：生成路径注入与拦截 ─────────────────────────

from tests.adapters.conftest import REPLY_OK_SCRIPT  # noqa: E402
from tests.adapters.test_reply_adapter import _run  # noqa: E402


def _special_item(memory_pool: list[str] | None = None, desired_policy: str = "answer") -> dict:
    return {
        "plan_id": "plan-1:0000",
        "family_id": "fam:1",
        "mode": "REPLY",
        "task_type": "reply_memory",
        "memory_type": "special",
        "desired_policy": desired_policy,
        "attempt_no": 1,
        "seed": 7,
        "input": {
            "scene": "出租屋窗边，雨声顺着窗缝传进来",
            "topic": "那晚的雨",
            "player_view": "你问她还记不记得那晚的雨",
            "turn_bounds": [4, 8],
            "memory_pool": memory_pool or [],
        },
        "required_behaviors": [],
        "forbidden_behaviors": [],
        "source_event_ids": [],
        "support_spans": [],
    }


def test_integration_special_injects_memory_facts():
    item = _special_item(["ev:rain", "ev:rescue"])
    ctx = dict(_snapshots())
    ctx.update({"profile": {}, "protocol": {}, "facts": ["白未晞"], "beliefs": ""})
    _, executor = _run(REPLY_OK_SCRIPT, item, ctx)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "可回忆的记忆事实" in prompt
    assert "墙脚躲雨" in prompt
    assert "外套包住" in prompt
    # secret 事件不注入
    assert "上古妖族传承" not in prompt


def test_integration_non_special_no_memory_section(reply_item, package_context):
    _, executor = _run(REPLY_OK_SCRIPT, reply_item, package_context)
    prompt = executor.specs[0]["messages"][0]["content"]
    assert "（无）" in prompt


def test_integration_ungrounded_recall_triggers_retry():
    # 命题本身有支撑（过语义检查），但台词额外编造"打雷"细节 → 记忆守卫拦截
    script = [
        {
            "content": (
                '{"human_turns": ["你还记得那晚的雨吗？"], '
                '"assistant_propositions": ["我那天缩在墙脚躲雨。"], '
                '"assistant_tones": ["soft"], '
                '"messages": ['
                '{"role": "human", "text": "你还记得那晚的雨吗？"}, '
                '{"role": "assistant", "text": "我那天缩在墙脚躲雨。那晚的雨特别大，还打了雷。"}, '
                '{"role": "human", "text": "然后呢？"}, '
                '{"role": "assistant", "text": "然后你用外套把我包回去了。"}]}'
            )
        }
    ]
    item = _special_item(["ev:rain", "ev:rescue"])
    ctx = dict(_snapshots())
    ctx.update({"profile": {}, "protocol": {}, "facts": ["白未晞"], "beliefs": ""})
    payload, _ = _run(script, item, ctx)
    assert "mode_failure" in payload
    assert payload.get("retryable") is True
    assert "回忆无支撑" in payload.get("reason", "")


def test_integration_grounded_recall_passes():
    script = [
        {
            "content": (
                '{"human_turns": ["你还记得那晚的雨吗？"], '
                '"assistant_propositions": ["我那天缩在墙脚躲雨。"], '
                '"assistant_tones": ["soft"], '
                '"messages": ['
                '{"role": "human", "text": "你还记得那晚的雨吗？"}, '
                '{"role": "assistant", "text": "记得，我那天缩在墙脚躲雨。"}, '
                '{"role": "human", "text": "然后呢？"}, '
                '{"role": "assistant", "text": "然后你用外套把我包回去了。"}]}'
            )
        }
    ]
    item = _special_item(["ev:rain", "ev:rescue"])
    ctx = dict(_snapshots())
    ctx.update({"profile": {}, "protocol": {}, "facts": ["白未晞"], "beliefs": ""})
    payload, _ = _run(script, item, ctx)
    assert "mode_failure" not in payload
