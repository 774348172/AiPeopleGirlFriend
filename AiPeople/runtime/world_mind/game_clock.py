from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from .contracts import RuntimeSessionIdentity
from .persistence import WorldMindStore, WorldMindStoreError
from .unity_snapshot import GAME_EPOCH


@dataclass(slots=True)
class _RunningClock:
    world_id: str
    anchor_game_time: datetime
    started_monotonic: float
    time_scale: float
    running: bool = True


class GameClockService:
    def __init__(
        self,
        store: WorldMindStore,
        *,
        initial_game_time: datetime,
        default_time_scale: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if initial_game_time.tzinfo is not None:
            raise ValueError("initial_game_time must not use a real-world timezone")
        if default_time_scale <= 0:
            raise ValueError("default_time_scale must be positive")
        self._store = store
        self._initial_game_time = initial_game_time
        self._default_time_scale = default_time_scale
        self._monotonic = monotonic
        self._running: dict[str, _RunningClock] = {}
        self._lock = threading.RLock()
        self._closed = False

    def current_time(self, session: RuntimeSessionIdentity) -> datetime:
        with self._lock:
            if self._closed:
                raise RuntimeError("game clock service is closed")
            observed = self._store.load_unity_observation(session.save_id)
            if observed and 'game_clock' in observed:
                if observed.get('world_id', 'songjiangfu') != session.world_id:
                    raise WorldMindStoreError('game clock world identity mismatch')
                self._running.pop(session.save_id, None)
                return GAME_EPOCH + timedelta(seconds=observed['game_clock']['elapsed_seconds'])
            running = self._running.get(session.save_id)
            if running is None:
                running = self._start_save(session)
            if running.world_id != session.world_id:
                raise WorldMindStoreError("game clock world identity mismatch")
            elapsed = max(0.0, self._monotonic() - running.started_monotonic) if running.running else 0
            return running.anchor_game_time + timedelta(
                seconds=elapsed * running.time_scale
            )

    def pause(self, session: RuntimeSessionIdentity) -> datetime:
        with self._lock:
            current = self.current_time(session)
            if session.save_id not in self._running:
                raise ValueError('Unity owns this game clock; use the game clock control')
            running = self._running[session.save_id]
            running.anchor_game_time = current
            running.started_monotonic = self._monotonic()
            running.running = False
            self._store.save_clock_anchor(
                save_id=session.save_id,
                world_id=session.world_id,
                anchor_game_time=current,
                time_scale=running.time_scale,
                running=False,
            )
            return current

    def snapshot(self, session: RuntimeSessionIdentity) -> dict:
        with self._lock:
            current = self.current_time(session)
            observed = self._store.load_unity_observation(session.save_id)
            if observed and 'game_clock' in observed:
                return dict(observed['game_clock'])
            running = self._running[session.save_id]
            return dict(ready=True, elapsed_seconds=max(0, (current-GAME_EPOCH).total_seconds()),
                        time_scale=running.time_scale, paused=not running.running)

    def resume(self, session: RuntimeSessionIdentity) -> datetime:
        with self._lock:
            current = self.current_time(session)
            if session.save_id not in self._running:
                raise ValueError('Unity owns this game clock; use the game clock control')
            running = self._running[session.save_id]
            running.anchor_game_time = current
            running.started_monotonic = self._monotonic()
            running.running = True
            self.checkpoint(session)
            return current

    def checkpoint(self, session: RuntimeSessionIdentity) -> datetime:
        with self._lock:
            current = self.current_time(session)
            if session.save_id not in self._running:
                return current  # Unity observation is already durably checkpointed.
            running = self._running[session.save_id]
            running.anchor_game_time = current
            running.started_monotonic = self._monotonic()
            self._store.save_clock_anchor(
                save_id=session.save_id,
                world_id=session.world_id,
                anchor_game_time=current,
                time_scale=running.time_scale,
                running=running.running,
            )
            return current

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            now = self._monotonic()
            for save_id, running in tuple(self._running.items()):
                current = running.anchor_game_time + timedelta(
                    seconds=(max(0.0, now - running.started_monotonic) if running.running else 0)
                    * running.time_scale
                )
                self._store.save_clock_anchor(
                    save_id=save_id,
                    world_id=running.world_id,
                    anchor_game_time=current,
                    time_scale=running.time_scale,
                    running=running.running,
                )
            self._running.clear()
            self._closed = True

    def _start_save(self, session: RuntimeSessionIdentity) -> _RunningClock:
        anchor = self._store.load_clock_anchor(session.save_id)
        if anchor is None:
            game_time = self._initial_game_time
            time_scale = self._default_time_scale
        else:
            if anchor.world_id != session.world_id:
                raise WorldMindStoreError("game clock world identity mismatch")
            game_time = anchor.anchor_game_time
            time_scale = anchor.time_scale
        running = _RunningClock(
            world_id=session.world_id,
            anchor_game_time=game_time,
            started_monotonic=self._monotonic(),
            time_scale=time_scale,
            running=anchor.running if anchor is not None else True,
        )
        self._running[session.save_id] = running
        self._store.save_clock_anchor(
            save_id=session.save_id,
            world_id=session.world_id,
            anchor_game_time=game_time,
            time_scale=time_scale,
            running=running.running,
        )
        return running
