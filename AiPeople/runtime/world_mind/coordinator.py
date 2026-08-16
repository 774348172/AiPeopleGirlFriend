from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class _SaveSlot:
    foreground_turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    mind_commit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_required_jobs: set[str] = field(default_factory=set)
    lifecycle_state: str = "running"


class SaveTurnCoordinator:
    def __init__(self) -> None:
        self._slots: dict[str, _SaveSlot] = {}
        self._map_lock = asyncio.Lock()
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("save turn coordinator is closed")
        self._started = True

    async def close(self) -> None:
        async with self._map_lock:
            for slot in self._slots.values():
                slot.lifecycle_state = "closed"
            self._closed = True
            self._started = False

    @asynccontextmanager
    async def foreground_turn(self, save_id: str) -> AsyncIterator[None]:
        if not self._started or self._closed:
            raise RuntimeError("save turn coordinator is not running")
        slot = await self._slot(save_id)
        async with slot.foreground_turn_lock:
            if slot.lifecycle_state != "running":
                raise RuntimeError("save lifecycle is not running")
            yield

    @asynccontextmanager
    async def mind_commit(self, save_id: str) -> AsyncIterator[None]:
        if not self._started or self._closed:
            raise RuntimeError("save turn coordinator is not running")
        slot = await self._slot(save_id)
        async with slot.mind_commit_lock:
            if slot.lifecycle_state != "running":
                raise RuntimeError("save lifecycle is not running")
            yield

    async def _slot(self, save_id: str) -> _SaveSlot:
        async with self._map_lock:
            return self._slots.setdefault(save_id, _SaveSlot())


class WorldUpdateCoordinator(SaveTurnCoordinator):
    pass
