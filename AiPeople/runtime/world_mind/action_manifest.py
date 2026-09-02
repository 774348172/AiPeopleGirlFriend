"""P0 动作白名单（程序固定，模型只从枚举中提议，不参与定义）。

设计依据：《Harness组装与单次判断设计_20260827.md》§二 区块 8、
《程序施工计划_单次判断Harness落地_20260827.md》步骤 1。

语义约束（占用什么、可不可打断、在哪发生）写在 description 中由模型读取判断，
程序不解析语义；程序只做机械记账（存在性、参数结构、时长、完成效果）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ActionDef:
    description: str
    duration_game_seconds: int
    effects: Mapping[str, Mapping[str, str]] | None = None
    params: Mapping[str, object] | None = None


ACTIONS: Mapping[str, ActionDef] = {
    "cook_meal": ActionDef(
        description="走到厨房做饭，双手被占用，大约需要 20 分钟",
        duration_game_seconds=1200,
        effects={"item_states": {"桌上": "一碗热汤面"}},
    ),
    "move_to": ActionDef(
        description="走向某处，路上可以被叫住",
        duration_game_seconds=60,
        params={"target_location_id": {"required": True}},
    ),
    "pick_up_item": ActionDef(
        description="拿起场景中的某个物品",
        duration_game_seconds=60,
        params={"item_id": {"required": True}},
    ),
}


def action_ids() -> tuple[str, ...]:
    return tuple(ACTIONS)


def is_valid_action_id(action_id: str) -> bool:
    return action_id in ACTIONS


def get_action(action_id: str) -> ActionDef | None:
    return ACTIONS.get(action_id)


def render_action_list() -> str:
    """区块 8 渲染：动作 id + 描述 + 时长（供判断 prompt 注入）。"""
    lines = []
    for action_id, action in ACTIONS.items():
        minutes = max(1, round(action.duration_game_seconds / 60))
        lines.append(f"- {action_id}：{action.description}（约 {minutes} 分钟）")
    return "\n".join(lines)
