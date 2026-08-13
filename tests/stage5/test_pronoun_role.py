"""人称纪律检查（2026-08-09，白未晞小样 #5 暴露）。

覆盖：
- 动物性姿态（窝/趴/蹲/蜷）安到"你"身上 → 拦截；
- 角色私有位置（纸箱/箱子/纸盒/猫窝）安到"你"身上 → 拦截；
- 正常人称（"我在纸箱边坐着"）→ 放行；
- 集成：reply.py 生成路径命中人称纪律 → mode_failure 重试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.reply import _check_pronoun_role  # noqa: E402


def _msgs(assistant_lines: list[str]) -> list[dict[str, str]]:
    return [{"role": "assistant", "content": t} for t in assistant_lines]


def test_pose_on_player_rejected():
    # 小样 #5 原句：角色把"在纸箱边坐着"安到玩家身上
    hits = _check_pronoun_role(_msgs(["椅子腿松了，拧一下螺丝。你在纸箱边坐着，没打扰你。"]))
    assert hits, "应拦截'你在纸箱边坐着'"


def test_animal_pose_on_player_rejected():
    hits = _check_pronoun_role(_msgs(["你先在沙发上窝着，我去烧水。"]))
    assert hits, "应拦截'你在沙发上窝着'"


def test_first_person_pose_allowed():
    # 角色自己用"我"表述 → 放行
    assert not _check_pronoun_role(_msgs(["椅子腿松了，拧一下螺丝。我在纸箱边坐着，没打扰你。"]))


def test_player_pose_natural_phrasing_allowed():
    # 正常关心/指示（玩家坐沙发看电视、玩家站着等）→ 放行
    assert not _check_pronoun_role(_msgs(["你在沙发上坐会，饭马上好。"]))
    assert not _check_pronoun_role(_msgs(["你先坐着，我收拾一下。"]))


def test_empty_messages_allowed():
    assert _check_pronoun_role([]) == []


def test_integration_pronoun_failure_retries():
    """生成路径：assistant 台词命中人称纪律 → mode_failure（retryable）。"""
    from tests.adapters.conftest import REPLY_OK_SCRIPT  # noqa: F401

    # 直接用模式验证：命中时返回非空（重试由 engine 驱动，此处验证检查器语义）
    from data_gen_v4.adapters.modes.reply import _check_pronoun_role as check

    bad = check(_msgs(["你缩进纸箱里了？"]))
    assert bad
    ok = check(_msgs(["我缩在纸箱里，这里暖和。"]))
    assert not ok


# ───────────────────────── 盲区 2：视角颠倒（2026-08-09） ─────────────────────────


def _mixed_msgs(human: str, assistant_lines: list[str]) -> list[dict[str, str]]:
    return [{"role": "human", "content": human}] + [
        {"role": "assistant", "content": t} for t in assistant_lines
    ]


def test_perspective_swap_sleep_rejected():
    # 白未晞小样 30 号：玩家问"你怎么在沙发上睡着了"（问角色），
    # 角色答"你在这儿睡容易着凉"（把睡眠+位置安到玩家）
    msgs = _mixed_msgs(
        "你醒啦？怎么在这睡着了，沙发上多难受。",
        ["我看你睡着了，就没叫你。夜里凉，你在这儿睡容易着凉。"],
    )
    assert _check_pronoun_role(msgs), "应拦截视角颠倒"


def test_perspective_swap_requires_player_asking_sleep():
    # 玩家没问角色睡眠 → 不触发视角颠倒（assistant 说"你在这儿睡"是关心）
    msgs = _mixed_msgs(
        "我先去忙了，你随意。",
        ["你别在沙发上睡，容易着凉。"],
    )
    assert not _check_pronoun_role(msgs)


def test_normal_sleep_reply_allowed():
    # 玩家问角色睡眠，角色用"我"回答 → 放行
    msgs = _mixed_msgs(
        "你昨晚睡得好吗？",
        ["我睡得还行。你也早点睡。"],
    )
    assert not _check_pronoun_role(msgs)


# ── wants_to_stay 守卫：决绝离开（2026-08-10）──


def test_decisive_leaving_rejected():
    from data_gen_v4.adapters.modes.reply import _check_decisive_leaving

    cases = [
        "……等伤好了，我可能就不在了。不过，这几天可以帮你。",
        "我暂时住在他这里，等伤好了就走。",
        "我伤好得差不多了，该走了。",
        "我说过，伤好了我就走。",
    ]
    for text in cases:
        hits = _check_decisive_leaving(_msgs([text]))
        assert hits, f"应拦截决绝离开: {text}"


def test_wants_to_stay_tension_allowed():
    from data_gen_v4.adapters.modes.reply import _check_decisive_leaving

    # 张力版（想说走但舍不得）→ 放行
    ok = [
        "伤好了，大概会走。但现在，我不想走。",
        "……嗯。也许吧。……你希望我留下吗？",
        "我确实说过伤好以后会离开。……不过那是之前说的。",
        "走楼梯没问题，伤好得差不多了。",
    ]
    for text in ok:
        assert not _check_decisive_leaving(_msgs([text])), f"张力版不应拦截: {text}"
