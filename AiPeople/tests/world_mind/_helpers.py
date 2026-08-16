from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from runtime.world_mind import (
    ActiveSceneState,
    FakeWorldMindModel,
    GameClockService,
    InMemoryWorldStateProvider,
    ProtagonistLiveState,
    RuntimeSessionIdentity,
    WorldMindRuntime,
    WorldMindRuntimeConfig,
    WorldMindStore,
)


ROOT = Path(__file__).resolve().parents[2]
INITIAL_GAME_TIME = datetime(1, 10, 11, 18, 0, 0)


class MutableMonotonic:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def runtime_config() -> WorldMindRuntimeConfig:
    return WorldMindRuntimeConfig(
        expected_world_id="songjiangfu",
        expected_protagonist_id="protagonist",
        world_canon_dir=ROOT / "世界设定" / "松江府",
        protagonist_canon_dir=ROOT / "人物设定" / "主角",
        character_package_dirs={"baiweixi": ROOT / "人物设定" / "白未晞"},
        p0_allowed_character_ids=("baiweixi",),
        foreground_protocol="legacy_v1",
    )


def session(
    *,
    save_id: str = "save_001",
    conversation_id: str = "conversation-a",
    world_id: str = "songjiangfu",
    protagonist_id: str = "protagonist",
    active_character_id: str = "baiweixi",
) -> RuntimeSessionIdentity:
    return RuntimeSessionIdentity(
        save_id=save_id,
        world_id=world_id,
        protagonist_id=protagonist_id,
        active_character_id=active_character_id,
        conversation_id=conversation_id,
    )


def protagonist(
    *,
    location_id: str = "apartment_table",
    location_label: str = "出租屋餐桌旁",
    activity: str = "吃面",
) -> ProtagonistLiveState:
    return ProtagonistLiveState(
        protagonist_id="protagonist",
        location_id=location_id,
        location_label=location_label,
        activity=activity,
        body_state={"fatigue": "轻微疲惫"},
        held_item_ids=("chopsticks",) if activity == "吃面" else (),
    )


def scene(
    *,
    scene_id: str = "apartment_table",
    location_label: str = "出租屋餐桌旁",
) -> ActiveSceneState:
    return ActiveSceneState(
        scene_id=scene_id,
        location_label=location_label,
        present_character_ids=("protagonist", "baiweixi"),
        item_states={"noodle_bowl": "放在桌上"},
    )


@dataclass(slots=True)
class RuntimeHarness:
    store: WorldMindStore
    monotonic: MutableMonotonic
    clock: GameClockService
    provider: InMemoryWorldStateProvider
    model: FakeWorldMindModel
    runtime: WorldMindRuntime


def build_runtime_harness(
    tmp_path: Path,
    *,
    model: FakeWorldMindModel | None = None,
    before_turn_commit=None,
    before_reconcile_commit=None,
    periodic_reconcile_seconds: float = 300.0,
) -> RuntimeHarness:
    store = WorldMindStore.open(
        tmp_path / "world_mind.sqlite3",
        before_turn_commit=before_turn_commit,
        before_reconcile_commit=before_reconcile_commit,
    )
    monotonic = MutableMonotonic()
    clock = GameClockService(
        store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=monotonic,
    )
    provider = InMemoryWorldStateProvider()
    fake = model or FakeWorldMindModel()
    runtime = WorldMindRuntime(
        config=runtime_config(),
        store=store,
        world_state_provider=provider,
        game_clock=clock,
        model=fake,
        periodic_reconcile_seconds=periodic_reconcile_seconds,
    )
    return RuntimeHarness(
        store=store,
        monotonic=monotonic,
        clock=clock,
        provider=provider,
        model=fake,
        runtime=runtime,
    )
