from __future__ import annotations

import asyncio
from datetime import timedelta

from runtime.world_mind import (
    GameClockService,
    InMemoryWorldStateProvider,
    MindAdvanceResult,
    PersistentWorldStateProvider,
    SaveTurnCoordinator,
    WorldMindStore,
)

from tests.world_mind._helpers import (
    INITIAL_GAME_TIME,
    MutableMonotonic,
    protagonist,
    scene,
    session,
)


def test_game_clock_advances_without_chat_and_recovers_after_close(tmp_path) -> None:
    database_path = tmp_path / "clock.sqlite3"
    store = WorldMindStore.open(database_path)
    monotonic = MutableMonotonic()
    clock = GameClockService(
        store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=monotonic,
    )
    current_session = session()

    assert clock.current_time(current_session) == INITIAL_GAME_TIME
    monotonic.advance(600)
    assert clock.current_time(current_session) == INITIAL_GAME_TIME + timedelta(minutes=10)
    clock.close()
    store.close()

    reopened_store = WorldMindStore.open(database_path)
    reopened_monotonic = MutableMonotonic()
    reopened_clock = GameClockService(
        reopened_store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=reopened_monotonic,
    )
    assert reopened_clock.current_time(current_session) == INITIAL_GAME_TIME + timedelta(minutes=10)
    reopened_monotonic.advance(60)
    assert reopened_clock.current_time(current_session) == INITIAL_GAME_TIME + timedelta(minutes=11)
    reopened_clock.close()
    reopened_store.close()


def test_game_clock_checkpoint_survives_unclean_reopen(tmp_path) -> None:
    database_path = tmp_path / "clock-checkpoint.sqlite3"
    store = WorldMindStore.open(database_path)
    monotonic = MutableMonotonic()
    clock = GameClockService(
        store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=monotonic,
    )
    current_session = session()
    assert clock.current_time(current_session) == INITIAL_GAME_TIME
    monotonic.advance(300)
    assert clock.checkpoint(current_session) == INITIAL_GAME_TIME + timedelta(minutes=5)
    store.close()

    reopened_store = WorldMindStore.open(database_path)
    reopened_clock = GameClockService(
        reopened_store,
        initial_game_time=INITIAL_GAME_TIME,
        monotonic=MutableMonotonic(),
    )
    assert reopened_clock.current_time(current_session) == INITIAL_GAME_TIME + timedelta(minutes=5)
    reopened_clock.close()
    reopened_store.close()


def test_in_memory_world_state_updates_location_immediately() -> None:
    async def scenario() -> None:
        provider = InMemoryWorldStateProvider()
        current_session = session()
        await provider.update_latest(
            current_session,
            protagonist=protagonist(),
            scene=scene(),
            game_time=INITIAL_GAME_TIME,
        )
        first = await provider.get_latest(current_session)
        assert first.protagonist.location_id == "apartment_table"

        await provider.update_latest(
            current_session,
            protagonist=protagonist(
                location_id="apartment_kitchen",
                location_label="出租屋厨房",
                activity="倒水",
            ),
            scene=scene(
                scene_id="apartment_kitchen",
                location_label="出租屋厨房",
            ),
            game_time=INITIAL_GAME_TIME + timedelta(seconds=1),
        )
        latest = await provider.get_latest(current_session)
        assert latest.protagonist.location_id == "apartment_kitchen"
        assert latest.protagonist.activity == "倒水"
        assert latest.version == 2

    asyncio.run(scenario())


def test_persistent_world_state_only_versions_actual_changes(tmp_path) -> None:
    async def scenario() -> None:
        store = WorldMindStore.open(tmp_path / "world.sqlite3")
        provider = PersistentWorldStateProvider(store)
        current_session = session()
        first, first_delta = await provider.update_latest(
            current_session,
            protagonist(),
            scene(),
            INITIAL_GAME_TIME,
        )
        repeated, repeated_delta = await provider.update_latest(
            current_session,
            protagonist(),
            scene(),
            INITIAL_GAME_TIME + timedelta(seconds=1),
        )
        moved, moved_delta = await provider.update_latest(
            current_session,
            protagonist(
                location_id="apartment_kitchen",
                location_label="出租屋厨房",
                activity="倒水",
            ),
            scene(
                scene_id="apartment_kitchen",
                location_label="出租屋厨房",
            ),
            INITIAL_GAME_TIME + timedelta(seconds=2),
        )
        assert first.version == 1
        assert first_delta is not None
        assert repeated.version == 1
        assert repeated_delta is None
        assert moved.version == 2
        assert moved_delta is not None
        assert moved_delta.changed_fields == ("protagonist", "scene")
        store.close()

    asyncio.run(scenario())


def test_save_turn_coordinator_serializes_same_save_but_not_other_saves() -> None:
    async def scenario() -> None:
        coordinator = SaveTurnCoordinator()
        await coordinator.start()
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        same_save_entered = asyncio.Event()
        other_save_entered = asyncio.Event()

        async def first() -> None:
            async with coordinator.foreground_turn("save_001"):
                first_entered.set()
                await release_first.wait()

        async def same_save() -> None:
            async with coordinator.foreground_turn("save_001"):
                same_save_entered.set()

        async def other_save() -> None:
            async with coordinator.foreground_turn("save_002"):
                other_save_entered.set()

        first_task = asyncio.create_task(first())
        await first_entered.wait()
        same_task = asyncio.create_task(same_save())
        other_task = asyncio.create_task(other_save())
        await asyncio.wait_for(other_save_entered.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not same_save_entered.is_set()
        release_first.set()
        await asyncio.gather(first_task, same_task, other_task)
        assert same_save_entered.is_set()
        await coordinator.close()

    asyncio.run(scenario())


def test_mind_advance_contract_has_no_program_fact_patch_fields() -> None:
    assert "protagonist_patch" not in MindAdvanceResult.__dataclass_fields__
    assert "game_clock_patch" not in MindAdvanceResult.__dataclass_fields__
