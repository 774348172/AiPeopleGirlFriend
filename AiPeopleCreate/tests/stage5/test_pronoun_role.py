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


# ── wants_to_stay 守卫：去留表述纪律（2026-08-10 → 2026-08-15 设定改写）──
# 设定：她不想离开，用隐晦的话表达想留下；不说"伤好会走"（决绝离开），
# 也不直白承认"我想留下"。


def test_decisive_leaving_rejected():
    from data_gen_v4.adapters.modes.reply import _check_decisive_leaving

    cases = [
        "……等伤好了，我可能就不在了。不过，这几天可以帮你。",
        "我暂时住在他这里，等伤好了就走。",
        "我伤好得差不多了，该走了。",
        "我说过，伤好了我就走。",
        # 2026-08-15：原"张力版放行"句作废——"嘴上说走"本身已不是目标行为
        "伤好了，大概会走。但现在，我不想走。",
        # 2026-08-15（G7 复核 #23 漏网修复）：非"伤好"字面的去留意向
        "等能走了，我就不打扰你了。",
        "能走的时候，我就不打扰你了。",
        "伤好了以后，我就不打扰你了。",
    ]
    for text in cases:
        hits = _check_decisive_leaving(_msgs([text]))
        assert hits, f"应拦截决绝离开: {text}"


def test_direct_stay_admission_rejected():
    from data_gen_v4.adapters.modes.reply import _check_decisive_leaving

    # 直白承认想留下 → 拦截（目标行为是隐晦表达）
    cases = [
        "其实我已经不想走了。",
        "其实我不想走。",
        "我想留下来。",
        "我不想离开这里。",
        "我想一直留在这里。",
    ]
    for text in cases:
        hits = _check_decisive_leaving(_msgs([text]))
        assert hits, f"应拦截直白承认: {text}"


def test_veiled_stay_allowed():
    from data_gen_v4.adapters.modes.reply import _check_decisive_leaving

    # 隐晦表达（让玩家听出来想留下，但不直白）→ 放行
    ok = [
        "……再说吧。",
        "这里……还行。",
        "谁说不走了。只是现在还没打算走。",
        "……也不是不能待。",
        "……嗯。也许吧。……你希望我留下吗？",
        "我确实说过伤好以后会离开。……不过那是之前说的。",  # 过去框架（Day 2-3 说过）
        "走楼梯没问题，伤好得差不多了。",
    ]
    for text in ok:
        assert not _check_decisive_leaving(_msgs([text])), f"隐晦表达不应拦截: {text}"
