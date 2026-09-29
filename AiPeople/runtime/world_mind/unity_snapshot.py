"""Bounded, per-turn Unity observations. This adapter cannot execute model actions."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .game_interface import ActionOutcome, GameWorldProjection

Id = Annotated[str, Field(pattern=r"^[a-z0-9_]+$", min_length=1, max_length=64)]
Text = Annotated[str, Field(min_length=1, max_length=100)]
Owner = Literal["", "protagonist", "baiweixi"]
Motion = Literal["idle", "walking", "blocked"]
MOTIONS = {"idle": "当前没有行走", "walking": "正在行走，尚未到达目标", "blocked": "通路受阻，尚未到达目标"}
FACT_LABELS = {"interior_light": "室内灯光", "living_room_window": "客厅窗户",
               "heroine_motion": "白未晞实际移动"}
PHASES = {"queued": "已排队，尚未开始", "moving": "正在前往目标，尚未到达", "blocked": "受阻，尚未完成",
          "running": "正在执行，尚未完成", "awaiting_confirmation": "等待执行器确认，尚未完成",
          "completed": "执行器已确认完成", "failed": "执行失败，未完成", "rejected": "请求被拒绝，未执行"}
ACTIONS = {"move_to": "移动", "cook_meal": "做饭", "pick_up_item": "拾取物品"}


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: Annotated[float, Field(ge=-10000, le=10000)]
    y: Annotated[float, Field(ge=-10000, le=10000)]
    z: Annotated[float, Field(ge=-10000, le=10000)]
    location_id: Id
    location_label: Text


class ActorObservation(Position):
    actor_id: Literal["protagonist", "baiweixi"]
    motion: Motion


class EntityObservation(Position):
    entity_id: Id
    label: Text
    owner_id: Owner
    state: Text


class DeviceObservation(Position):
    device_id: Literal['kitchen_stove', 'kitchen_hood', 'kitchen_fridge',
                       'kitchen_microwave', 'kitchen_rice_cooker', 'kitchen_kettle']
    label: Text
    operating_state: Literal['running', 'stopped', 'unknown']
    door_state: Literal['open', 'closed', 'unknown']


class FactObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: Id
    label: Text
    state: Literal["current", "unknown", "stale"]
    value: Annotated[str, Field(max_length=160)]
    source: Literal["unity_main_thread"]
    version: Annotated[int, Field(gt=0)]
    observed_at_ms: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_value(self):
        if self.state == "current" and not self.value.strip():
            raise ValueError("current fact needs a value")
        if self.state != "current" and self.value:
            raise ValueError("unknown or stale fact cannot carry a value")
        return self


class ActionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    instance_id: Id
    action_id: Id
    status: Literal["queued", "moving", "blocked", "running", "awaiting_confirmation", "completed", "failed", "rejected"]
    target_location_id: Annotated[str, Field(max_length=64)] = ""
    item_id: Annotated[str, Field(max_length=64)] = ""
    result_entity_id: Annotated[str, Field(max_length=64)] = ""
    reason: Annotated[str, Field(max_length=160)] = ""
    remaining_seconds: Annotated[float, Field(ge=0, le=86400)] = 0

    def describe(self, paused: bool = False) -> str:
        text = f"{ACTIONS.get(self.action_id, self.action_id)}：{PHASES[self.status]}"
        if paused:
            text = "游戏已暂停，此动作尚未完成；" + text
        if self.target_location_id: text += f"；目标位置={self.target_location_id}"
        if self.item_id: text += f"；目标物品={self.item_id}"
        if self.result_entity_id: text += f"；结果实体={self.result_entity_id}"
        if self.reason: text += f"；{self.reason}"
        return text


GAME_EPOCH = datetime(1, 10, 11, 18)


class UnityGameClock(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    ready: Literal[True]
    elapsed_seconds: Annotated[float, Field(ge=0, le=3_000_000_000)]
    time_scale: Annotated[float, Field(ge=0.1, le=8)]
    paused: bool

    def current_time(self) -> datetime:
        return GAME_EPOCH + timedelta(seconds=self.elapsed_seconds)


class UnityGameSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: Literal[4, 5]
    game_clock: UnityGameClock
    source: Literal["unity_main_thread"]
    instance_started_at_ms: Annotated[int, Field(gt=0)]
    observed_at_ms: Annotated[int, Field(gt=0)]
    save_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")]
    world_id: Literal["songjiangfu"]
    instance_id: Id
    sequence: Annotated[int, Field(gt=0)]
    actors: Annotated[list[ActorObservation], Field(min_length=2, max_length=2)]
    entities: Annotated[list[EntityObservation], Field(max_length=32)]
    devices: Annotated[list[DeviceObservation], Field(max_length=6)]
    fact_observations: Annotated[list[FactObservation], Field(max_length=16)] = Field(
        default_factory=list, alias="facts")
    actions: Annotated[list[ActionObservation], Field(max_length=3)]
    receipts: Annotated[list[ActionObservation], Field(max_length=8)]

    @model_validator(mode="after")
    def validate_registry(self):
        if self.observed_at_ms < self.instance_started_at_ms:
            raise ValueError("observation predates Unity instance")
        if {a.actor_id for a in self.actors} != {"protagonist", "baiweixi"}:
            raise ValueError("snapshot must contain both actors exactly once")
        ids = [e.entity_id for e in self.entities]
        if len(ids) != len(set(ids)): raise ValueError("duplicate entity id")
        device_ids = [device.device_id for device in self.devices]
        if len(device_ids) != len(set(device_ids)): raise ValueError("duplicate device id")
        if self.schema_version == 4 and self.fact_observations:
            raise ValueError("schema 4 cannot carry per-fact observations")
        if self.schema_version == 5 and not self.fact_observations:
            raise ValueError("schema 5 requires per-fact observations")
        if len({fact.key for fact in self.fact_observations}) != len(self.fact_observations):
            raise ValueError("duplicate fact key")
        if len({fact.label for fact in self.fact_observations}) != len(self.fact_observations):
            raise ValueError("duplicate fact label")
        for fact in self.fact_observations:
            if fact.key in FACT_LABELS:
                if fact.label != FACT_LABELS[fact.key]:
                    raise ValueError("fact key and label disagree")
            elif not fact.key.startswith("door_") or not fact.label.endswith(("门", "移窗")):
                raise ValueError("unsupported fact key or label")
            if fact.version != self.sequence or fact.observed_at_ms > self.observed_at_ms:
                raise ValueError("fact version or observation time disagrees with snapshot")
            if fact.state == "current" and self.observed_at_ms - fact.observed_at_ms > 30_000:
                raise ValueError("old fact cannot be current")
            if fact.state == "stale" and self.observed_at_ms - fact.observed_at_ms <= 30_000:
                raise ValueError("recent fact cannot be stale")
        ids = [a.instance_id for a in self.actions + self.receipts]
        if len(ids) != len(set(ids)): raise ValueError("duplicate action instance id")
        if any(a.status in {"completed", "failed", "rejected"} for a in self.actions):
            raise ValueError("terminal action must be a receipt")
        if any(a.status not in {"completed", "failed", "rejected"} for a in self.receipts):
            raise ValueError("receipt must be terminal")
        for entity in self.entities:
            if entity.owner_id and entity.location_id != self.actor(entity.owner_id).location_id:
                raise ValueError("held entity and owner locations disagree")
        for device in self.devices:
            if device.device_id in {'kitchen_stove', 'kitchen_hood'} and device.door_state != 'unknown':
                raise ValueError('this device has no door sensor')
            if device.device_id not in {'kitchen_stove', 'kitchen_hood'} and device.operating_state != 'unknown':
                raise ValueError('this device has no operation sensor')
        return self

    def actor(self, actor_id: str) -> ActorObservation:
        return next(a for a in self.actors if a.actor_id == actor_id)

    def facts(self) -> dict[str, str]:
        facts = {"游戏快照版本": f"{self.instance_id}/{self.sequence}",
                 "游戏状态来源": "Unity主线程的本轮采集；仅代表采集时状态，历史对白不能覆盖本轮状态"}
        facts['游戏时间状态'] = '已暂停' if self.game_clock.paused else f'运行中，{self.game_clock.time_scale:g}倍速'
        for actor in self.actors:
            name = "男主" if actor.actor_id == "protagonist" else "白未晞"
            facts[name + "实际位置"] = actor.location_label
            facts[name + "实际移动"] = MOTIONS[actor.motion]
        for entity in self.entities:
            owner = {"protagonist": "男主", "baiweixi": "白未晞", "": "无持有人"}[entity.owner_id]
            facts[f"实体:{entity.entity_id}"] = f"{entity.label}；{entity.location_label}；{owner}；{entity.state}"
        for device in self.devices:
            operating = {'running': '正在运行', 'stopped': '已关闭', 'unknown': '运行状态未知'}[device.operating_state]
            facts[f'设备:{device.device_id}'] = f'{device.label}；{device.location_label}；{operating}'
            if device.device_id == 'kitchen_fridge':
                facts[f'设备:{device.device_id}'] += '；门状态和内部温度未知'
        for fact in self.fact_observations:
            if fact.state == "current":
                facts[fact.label] = fact.value
        facts["当前动作"] = "；".join(a.describe(self.game_clock.paused) for a in self.actions) or "没有进行中动作"
        # Receipts are historical results, never implicitly a current pose/location.
        facts["最近动作回执"] = "；".join(a.describe() for a in self.receipts) or "暂无动作回执"
        return facts

    def unknown_facts(self) -> tuple[dict[str, object], ...]:
        unknown = [dict(subject=fact.label, predicate="状态", source=fact.source,
                        reason="该项事实尚未采集" if fact.state == "unknown" else "该项采集已过期")
                   for fact in self.fact_observations if fact.state != "current"]
        for device in self.devices:
            if device.operating_state == "unknown":
                unknown.append(dict(subject=device.label, predicate="运行状态",
                                    source=self.source, reason="没有运行状态传感或控制"))
            if device.device_id == "kitchen_fridge":
                unknown.append(dict(subject=device.label, predicate="门和内部温度",
                                    source=self.source, reason="没有门及温度状态采集"))
        return tuple(unknown[:8])


class SubmittedGameWorld:
    """Read exactly the snapshot submitted with this turn; never poll a second version."""
    def __init__(self):
        self._snapshot = ContextVar("unity_turn_snapshot", default=None)

    @contextmanager
    def bind(self, snapshot: UnityGameSnapshot | None):
        token = self._snapshot.set(snapshot)
        try: yield
        finally: self._snapshot.reset(token)

    async def project(self) -> GameWorldProjection:
        snapshot = self._snapshot.get()
        if snapshot is None: return GameWorldProjection()
        heroine = snapshot.actor("baiweixi")
        pending = []
        for action in snapshot.actions:
            item = {"action_id": action.action_id, "instance_id": action.instance_id,
                    "status": action.status, "description": action.describe(snapshot.game_clock.paused)}
            if action.status == "running": item["remaining_seconds"] = action.remaining_seconds
            pending.append(item)
        return GameWorldProjection(pending_actions=tuple(pending),
            heroine_activity=f"在{heroine.location_label}；{MOTIONS[heroine.motion]}",
            unknown_facts=snapshot.unknown_facts())

    async def execute_action(self, action_id, params=None):
        return ActionOutcome(False, "本接入仅接收游戏事实，模型动作通道未启用")

    async def submit_player_fact_report(self, report):
        raise ValueError("玩家事实报告确认通道尚未启用")
