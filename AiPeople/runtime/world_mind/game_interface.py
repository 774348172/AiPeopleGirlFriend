"""游戏-LLM 接口契约的 runtime 侧定义与桩实现。

设计依据：《游戏-LLM 接口契约设计_20260827.md》
- 游戏是世界唯一事实源与执行者；runtime 只做投影（读）与转发（执行请求）；
- 实体投影只有存在性，不设来源属性（来源由记忆+推理承担）；
- 真实游戏接入时实现 GameWorldInterface 即可替换 StubGameWorld。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from .action_manifest import ACTIONS, ActionDef, get_action


@dataclass(frozen=True, slots=True)
class GameWorldProjection:
    """游戏世界状态投影（runtime 每回合快照时读取，只读）。"""

    pending_actions: tuple[dict[str, object], ...] = ()
    item_states: Mapping[str, str] | None = None
    heroine_activity: str | None = None
    recent_feedback: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.pending_actions, tuple) or any(
            not isinstance(item, dict) for item in self.pending_actions
        ):
            raise TypeError("pending_actions must contain dict items")
        if self.item_states is not None and not isinstance(
            self.item_states, Mapping
        ):
            raise TypeError("item_states must be a mapping or None")
        if self.heroine_activity is not None and (
            not isinstance(self.heroine_activity, str)
            or not self.heroine_activity.strip()
        ):
            raise ValueError("heroine_activity must be non-empty text")
        if not isinstance(self.recent_feedback, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.recent_feedback
        ):
            raise TypeError("recent_feedback must contain non-empty strings")


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """动作执行结果（转发给游戏后的返回）。"""

    accepted: bool
    reason: str = ""


@runtime_checkable
class GameWorldInterface(Protocol):
    """游戏世界接口契约：投影 + 执行。

    真实 2D 游戏接入时实现本协议：
    - project：返回当前世界状态投影（进行中动作/物品账/女主活动/最近反馈）；
    - execute_action：执行（或拒绝）模型提议的动作，拒绝原因由游戏记录，
      经下一轮 project 的 recent_feedback 回注给模型。
    """

    async def project(self) -> GameWorldProjection: ...

    async def execute_action(
        self, action_id: str, params: Mapping[str, object] | None
    ) -> ActionOutcome: ...


class StubGameWorld:
    """桩实现：内存状态，供联调与测试（真实游戏接入后替换）。

    行为对齐接口契约：
    - execute_action 接受白名单动作 → 进入进行中（时长取自动作清单）；
    - advance(seconds) 推进模拟时间 → 到期动作完成 → 完成效果写入 item_states
      （如煮面完成 → 桌上出现一碗热汤面）；
    - reject 配置可强制拒绝某动作（测试回注通道用）。
    """

    def __init__(
        self,
        *,
        item_states: Mapping[str, str] | None = None,
        reject: Mapping[str, str] | None = None,
    ) -> None:
        self._item_states: dict[str, str] = dict(item_states or {})
        self._pending: list[dict[str, object]] = []
        self._feedback: list[str] = []
        self._reject: dict[str, str] = dict(reject or {})
        self.execute_calls: list[tuple[str, Mapping[str, object] | None]] = []

    async def project(self) -> GameWorldProjection:
        return GameWorldProjection(
            pending_actions=tuple(dict(item) for item in self._pending),
            item_states=MappingProxyType(dict(self._item_states)),
            heroine_activity=(
                str(self._pending[0]["description"]) if self._pending else None
            ),
            recent_feedback=tuple(self._feedback[-3:]),
        )

    async def execute_action(
        self, action_id: str, params: Mapping[str, object] | None
    ) -> ActionOutcome:
        self.execute_calls.append((action_id, params))
        if action_id in self._reject:
            reason = self._reject[action_id]
            self._feedback.append(f"动作被拒绝：{reason}")
            return ActionOutcome(accepted=False, reason=reason)
        definition = get_action(action_id)
        if definition is None:
            reason = f"动作不在游戏清单内：{action_id}"
            self._feedback.append(f"动作被拒绝：{reason}")
            return ActionOutcome(accepted=False, reason=reason)
        if definition.params:
            missing = [
                name
                for name, spec in definition.params.items()
                if spec.get("required") and not (params or {}).get(name)
            ]
            if missing:
                reason = f"缺少必要参数：{', '.join(missing)}"
                self._feedback.append(f"动作被拒绝：{reason}")
                return ActionOutcome(accepted=False, reason=reason)
        self._pending.append(
            {
                "action_id": action_id,
                "description": definition.description,
                "remaining_seconds": definition.duration_game_seconds,
                "params": dict(params or {}),
            }
        )
        return ActionOutcome(accepted=True)

    def advance(self, seconds: float) -> None:
        """推进模拟时间：到期动作完成并应用效果（测试与联调用）。"""
        for item in list(self._pending):
            remaining = int(item["remaining_seconds"]) - seconds
            if remaining > 0:
                item["remaining_seconds"] = remaining
                continue
            self._pending.remove(item)
            self._apply_effects(str(item["action_id"]))

    def _apply_effects(self, action_id: str) -> None:
        definition = get_action(action_id)
        if definition is None or not definition.effects:
            return
        for target, changes in definition.effects.items():
            if target == "item_states" and isinstance(changes, Mapping):
                for key, value in changes.items():
                    self._item_states[str(key)] = str(value)

    @property
    def pending(self) -> list[dict[str, object]]:
        return list(self._pending)

    @property
    def item_states(self) -> dict[str, str]:
        return dict(self._item_states)

    @property
    def feedback(self) -> list[str]:
        return list(self._feedback)
