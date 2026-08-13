"""盲区 3：感知能力编造检查（2026-08-09，白未晞小样 39 号暴露）。

覆盖：
- 超自然感知断言玩家身体（"我闻到你身上有股疲乏的味道""你额头发烫"）→ 拦截；
- 正常猫系嗅觉（"我闻到饭香了"）→ 放行；
- 玩家提供的状态（"我头疼"）→ 正常关心放行；
- 正典能力（"我感知到灵气"）→ 放行（不在玩家身体断言模式内）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_gen_v4.adapters.modes.reply import _check_perception_claims  # noqa: E402


def _msgs(assistant_lines: list[str]) -> list[dict[str, str]]:
    return [{"role": "assistant", "content": t} for t in assistant_lines]


def test_perception_claim_on_player_body_rejected():
    # 白未晞小样 39 号原句：用"闻到"断言玩家身体状态
    hits = _check_perception_claims(
        _msgs(["我闻到你身上有股疲乏的味道。你额头有点发烫。"])
    )
    assert hits, "应拦截'我闻到你身上…'"


def test_perception_claim_on_player_face_rejected():
    hits = _check_perception_claims(_msgs(["我察觉到你的脸色不太好。"]))
    assert hits, "应拦截'我察觉到你的脸色…'"


def test_normal_cat_smell_allowed():
    # 猫系嗅觉的正常表达（对象不是玩家身体）→ 放行
    assert not _check_perception_claims(_msgs(["我闻到饭香了，是不是煮好了？"]))
    assert not _check_perception_claims(_msgs(["我嗅到楼下有烤红薯的味道。"]))


def test_player_provided_state_care_allowed():
    # 玩家自己说了状态，正常关心 → 放行
    assert not _check_perception_claims(_msgs(["头疼的话先坐下，我给你倒杯水。"]))


def test_canon_ability_allowed():
    # 正典能力（感知灵气/妖气）→ 放行（不在玩家身体断言模式内）
    assert not _check_perception_claims(_msgs(["我感知到附近有妖气。"]))


def test_empty_messages_allowed():
    assert _check_perception_claims([]) == []
