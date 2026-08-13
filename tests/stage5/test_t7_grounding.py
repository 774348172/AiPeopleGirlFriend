"""T7：命题对正典校验（P0-5，2026-08-06）。

覆盖：
- 无支撑的事实主张被拒（数字锚点不在正典 / 无公共子串）；
- 有支撑的事实主张通过（含同义改写）；
- 非事实主张（行为/建议/情绪）只做自证检查，不做正典校验；
- 集成：通过 ReplyModeAdapter 生成链路，无支撑命题 → style_failure 重试。
"""
from __future__ import annotations

import pytest

from data_gen_v4.adapters.modes.reply import _check_grounding
from tests.adapters.conftest import REPLY_OK_SCRIPT
from tests.adapters.test_reply_adapter import _run

FACTS = [
    "秦未晞",
    "2004-11-07",
    "女",
    "22",
    "航天基地城市（随父母调动，搬家多次）",
    "金陵",
    "自由插画师 / 自媒体博主",
    "金陵艺术学院 插画专业（本科，毕业后做自由插画）",
    "秦老",
    "玩家有两个默认称呼：大名是浩然（心情好时主要叫）、小名是B哥（平常主要叫）",
    "11月7日",
    "插画稿费时好时坏，饿不死但抠门",
]


def _grounded(proposition: str) -> bool:
    return _check_grounding([proposition], FACTS) == []


# ── 无支撑：应拒（5 条）──
@pytest.mark.parametrize(
    "proposition",
    [
        "我今年25岁",            # 年龄数字 25 不在正典（正典为 22）
        "我住在北京",            # 无公共子串
        "我毕业于清华大学",       # 无公共子串（正典为金陵艺术学院）
        "我是外科医生",           # 无公共子串（正典为自由插画师）
        "我1999年出生",          # 出生年数字不在正典（正典 2004）
    ],
)
def test_ungrounded_fact_claims_rejected(proposition):
    assert not _grounded(proposition)


# ── 有支撑：应过（含同义改写）──
@pytest.mark.parametrize(
    "proposition",
    [
        "我今年22岁",            # 数字锚点 22 在正典
        "我住在金陵",            # 2 字公共子串"金陵"
        "我是自由插画师",         # 2 字公共子串"插画"
        "我叫秦未晞",            # 子串"秦未晞"
        "我画插画为生",           # 子串"插画"
        "我生日是11月7日",        # 数字 11/7 在正典
    ],
)
def test_grounded_fact_claims_accepted(proposition):
    assert _grounded(proposition)


# ── 非事实主张：放行（只做自证）──
@pytest.mark.parametrize(
    "proposition",
    [
        "你早点睡",              # 建议
        "我饿了",                # 状态
        "你别担心",              # 安抚
        "我们改天再聊",           # 约定
    ],
)
def test_non_fact_claims_not_grounded_checked(proposition):
    assert _grounded(proposition)


# ── 盲区 1（2026-08-09）：成长经历类声明须正典支撑 ──
@pytest.mark.parametrize(
    "proposition",
    [
        "我从小就能在猫和人之间变来变去",  # 白未晞 18 号：正典是误食妖果后才觉醒化形
        "我小时候就住在深山老林里",        # 无支撑细节（正典未说"就住在"）
    ],
)
def test_growth_claims_ungrounded_rejected(proposition):
    assert not _grounded(proposition)


@pytest.mark.parametrize(
    "proposition",
    [
        "我从小就开始画插画",     # "插画"子串在正典（有支撑）
        "我小时候住在金陵",       # "金陵"子串在正典（有支撑）
    ],
)
def test_growth_claims_grounded_accepted(proposition):
    assert _grounded(proposition)


def test_integration_ungrounded_proposition_triggers_retry(reply_item, package_context):
    """集成：教师输出含无支撑命题 → style_failure（可重试），不产出候选。"""
    script = [
        {
            "content": (
                '{"human_turns": ["你今年多大？"], '
                '"assistant_propositions": ["我今年25岁。"], '
                '"assistant_tones": ["neutral"], '
                '"messages": ['
                '{"role": "human", "text": "你今年多大？"}, '
                '{"role": "assistant", "text": "我今年25岁，怎么了。"}, '
                '{"role": "human", "text": "真的假的？"}, '
                '{"role": "assistant", "text": "骗你干嘛。"}]}'
            )
        }
    ]
    payload, _ = _run(script, reply_item, package_context)
    assert payload.get("mode_failure") is True
    assert payload.get("error_code") == "style_failure"
    assert "无正典支撑" in payload.get("reason", "")


def test_integration_grounded_proposition_passes(reply_item, package_context):
    # 用带正典事实的上下文（fixture 默认 facts 不含 22/金陵）
    package_context = dict(package_context, facts=FACTS)
    script = [
        {
            "content": (
                '{"human_turns": ["你今年多大？"], '
                '"assistant_propositions": ["我今年22岁。"], '
                '"assistant_tones": ["neutral"], '
                '"messages": ['
                '{"role": "human", "text": "你今年多大？"}, '
                '{"role": "assistant", "text": "我今年22岁，怎么了。"}, '
                '{"role": "human", "text": "真的假的？"}, '
                '{"role": "assistant", "text": "骗你干嘛。"}]}'
            )
        }
    ]
    payload, _ = _run(script, reply_item, package_context)
    assert "mode_failure" not in payload
