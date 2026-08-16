from __future__ import annotations

import asyncio
from typing import Protocol

from .contracts import RuntimeSessionIdentity
from .persistence import WorldMindStore
from .state import (
    ActiveSceneState,
    LiveWorldState,
    ModelReadableWorldState,
    ProtagonistLiveState,
    WorldEventDelta,
)


class WorldStateUnavailableError(RuntimeError):
    pass


class WorldStateProvider(Protocol):
    async def get_latest(
        self,
        session: RuntimeSessionIdentity,
    ) -> LiveWorldState: ...


class InMemoryWorldStateProvider:
    def __init__(self) -> None:
        self._states: dict[str, LiveWorldState] = {}
        self._lock = asyncio.Lock()
        self.get_calls = 0

    async def update_latest(
        self,
        session: RuntimeSessionIdentity,
        protagonist: ProtagonistLiveState,
        scene: ActiveSceneState,
        game_time,
    ) -> LiveWorldState:
        async with self._lock:
            current = self._states.get(session.save_id)
            if current is not None:
                _require_world_identity(current, session)
            version = 1 if current is None else current.version + 1
            state = LiveWorldState(
                save_id=session.save_id,
                world_id=session.world_id,
                protagonist_id=session.protagonist_id,
                version=version,
                protagonist=protagonist,
                scene=scene,
                last_changed_game_time=game_time,
            )
            self._states[session.save_id] = state
            return state

    async def get_latest(
        self,
        session: RuntimeSessionIdentity,
    ) -> LiveWorldState:
        async with self._lock:
            self.get_calls += 1
            state = self._states.get(session.save_id)
            if state is None:
                raise WorldStateUnavailableError(
                    f"live world state is not initialized: {session.save_id}"
                )
            _require_world_identity(state, session)
            return state


class PersistentWorldStateProvider:
    def __init__(self, store: WorldMindStore) -> None:
        self._store = store
        self.get_calls = 0

    async def update_latest(
        self,
        session: RuntimeSessionIdentity,
        protagonist: ProtagonistLiveState,
        scene: ActiveSceneState,
        game_time,
    ) -> tuple[LiveWorldState, WorldEventDelta | None]:
        return self._store.put_live_world(session, protagonist, scene, game_time)

    async def get_latest(
        self,
        session: RuntimeSessionIdentity,
    ) -> LiveWorldState:
        self.get_calls += 1
        state = self._store.load_live_world(session)
        if state is None:
            raise WorldStateUnavailableError(
                f"live world state is not initialized: {session.save_id}"
            )
        return state


class WorldStateProjection:
    def project(self, state: LiveWorldState) -> ModelReadableWorldState:
        protagonist = state.protagonist
        body = "；".join(
            f"{key}={value}" for key, value in protagonist.body_state.items()
        ) or "无额外身体状态"
        held = "、".join(protagonist.held_item_ids) or "无"
        scene_items = "；".join(
            f"{key}={value}" for key, value in state.scene.item_states.items()
        ) or "无重要物品变化"
        return ModelReadableWorldState(
            live_world_version=state.version,
            scene_text=(
                f"当前场景是{state.scene.location_label}；"
                f"场景物品状态：{scene_items}。"
            ),
            protagonist_text=(
                f"男主角位于{protagonist.location_label}，"
                f"当前正在{protagonist.activity}；"
                f"身体状态：{body}；手持物：{held}。"
            ),
        )


def _require_world_identity(
    state: LiveWorldState,
    session: RuntimeSessionIdentity,
) -> None:
    if state.save_id != session.save_id:
        raise WorldStateUnavailableError("live world save identity mismatch")
    if state.world_id != session.world_id:
        raise WorldStateUnavailableError("live world world identity mismatch")
    if state.protagonist_id != session.protagonist_id:
        raise WorldStateUnavailableError("live world protagonist identity mismatch")
