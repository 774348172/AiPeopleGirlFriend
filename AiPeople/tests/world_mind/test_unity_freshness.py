import asyncio
import json
from datetime import datetime
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from runtime.world_mind import WorldMindStore, PersistentWorldStateProvider, GameClockService
from runtime.world_mind.unity_freshness import UnityFreshness, SnapshotRejected
from runtime.world_mind.unity_snapshot import UnityGameSnapshot
from tools import baiweixi_chat_app as app
from test_unity_game_snapshot import sample


NOW = 1_800_000_000.0


def snapshot(**changes):
    data = sample()
    data.update(instance_started_at_ms=int(NOW*1000)-1000, observed_at_ms=int(NOW*1000))
    data.update(changes)
    return UnityGameSnapshot(**data)


@pytest.mark.parametrize('change', [
    {'observed_at_ms': int(NOW*1000)-30001, 'instance_started_at_ms': int(NOW*1000)-40000},
    {'observed_at_ms': int(NOW*1000)+5001},
])
def test_delayed_or_future_capture_rejected(change):
    with pytest.raises(SnapshotRejected):
        UnityFreshness(wall=lambda: NOW).accept(snapshot(**change), None)


def test_instance_retirement_and_sequence_cannot_be_reset():
    guard = UnityFreshness(wall=lambda: NOW)
    first = guard.accept(snapshot(), None)
    second = guard.accept(snapshot(instance_id='new_unity', instance_started_at_ms=int(NOW*1000)), first)
    with pytest.raises(SnapshotRejected):
        guard.accept(snapshot(sequence=9999), second)
    with pytest.raises(SnapshotRejected):
        guard.accept(snapshot(), first)
    with pytest.raises(SnapshotRejected):
        guard.accept(snapshot(sequence=2, instance_started_at_ms=int(NOW*1000)-999), first)
    with pytest.raises(SnapshotRejected):
        guard.accept(snapshot(sequence=2, observed_at_ms=int(NOW*1000)-1), first)


def test_monotonic_expiry_cannot_be_extended_by_wall_clock_rollback():
    ticks = [0.0]
    guard = UnityFreshness(wall=lambda: NOW, monotonic=lambda: ticks[0])
    record = guard.accept(snapshot(), None)
    assert guard.status(record)['status'] == 'current'
    ticks[0] = 31
    assert guard.status(record)['status'] == 'stale'
    assert UnityFreshness(wall=lambda: NOW).status(record)['status'] == 'stale'


def service(path, now):
    value = app.BaiWeixiChatService.__new__(app.BaiWeixiChatService)
    value.store = WorldMindStore.open(path)
    value.world = PersistentWorldStateProvider(value.store)
    value.clock = GameClockService(value.store, initial_game_time=datetime(1,10,11,18))
    value.send_lock = asyncio.Lock()
    value._unity_freshness = UnityFreshness(wall=lambda: now[0], monotonic=lambda: now[0])
    return value


def test_api_expiry_restart_recovery_and_no_legacy_downgrade(tmp_path, monkeypatch):
    now = [NOW]
    db = tmp_path/'world.sqlite3'
    current = service(db, now)
    monkeypatch.setattr(app, '_service', lambda: current)
    client = TestClient(app.app)
    def post(s):
        return client.post('/api/status', json={'save_id':'snapshot_test', 'world':{'game_snapshot':s.model_dump()}})
    def read():
        return client.post('/api/status', json={'save_id':'snapshot_test'}).json()
    try:
        assert post(snapshot()).status_code == 200
        assert read()['game_observation']['status'] == 'current'
        assert read()['world']['location_id'] == 'apartment_living_room'
        now[0] += 31
        expired = read()
        assert expired['game_observation']['status'] == 'stale'
        assert expired['world']['location_id'] == 'unknown'
        assert '水杯' not in json.dumps(expired, ensure_ascii=False)
        assert current.store.load_unity_observation('snapshot_test')['sequence'] == 1
        assert post(snapshot()).status_code == 409
        assert client.post('/api/status', json={'save_id':'snapshot_test','world':{}}).status_code == 409
        fresh = snapshot(sequence=2, observed_at_ms=int(now[0]*1000))
        assert post(fresh).status_code == 200
        current.store.close()
        current = service(db, now)
        assert read()['game_observation']['status'] == 'stale'
        assert read()['world']['location_id'] == 'unknown'
        assert post(fresh).status_code == 409
        fresh = fresh.model_copy(update={'sequence':3})
        assert post(fresh).status_code == 200
        replacement = snapshot(instance_id='replacement', instance_started_at_ms=int(now[0]*1000), observed_at_ms=int(now[0]*1000))
        assert post(replacement).status_code == 200
        assert post(fresh.model_copy(update={'sequence':999})).status_code == 409
        assert current.store.load_unity_observation('snapshot_test')['instance_id'] == 'replacement'
    finally:
        current.store.close()


def test_waiting_chat_rechecks_age_after_lock_and_never_calls_model(tmp_path):
    async def scenario():
        now = [NOW]
        current = service(tmp_path/'world.sqlite3', now)
        # No runtime attached: reaching a model would fail this test.
        await current.send_lock.acquire()
        request = app.ChatInput(save_id='snapshot_test',text='我在哪？',world=app.WorldInput(game_snapshot=snapshot()))
        task = asyncio.create_task(current.chat(request))
        await asyncio.sleep(0)
        now[0] += 31
        current.send_lock.release()
        try:
            with pytest.raises(HTTPException) as error:
                await task
            assert error.value.status_code == 409
            assert current.store.load_live_world(app._session('snapshot_test')) is None
        finally:
            current.store.close()
    asyncio.run(scenario())


def test_world_and_observation_roll_back_together(tmp_path):
    async def scenario():
        current = service(tmp_path/'world.sqlite3', [NOW])
        session = app._session('snapshot_test')
        try:
            await current.ensure_world(session,app.WorldInput(game_snapshot=snapshot()))
            current.store._connection.execute("CREATE TRIGGER fail_scene BEFORE UPDATE ON active_scene_states BEGIN SELECT RAISE(ABORT, 'test failure'); END")
            with pytest.raises(Exception, match='live world update failed'):
                await current.ensure_world(session,app.WorldInput(game_snapshot=snapshot(sequence=2)))
            assert current.store.load_unity_observation(session.save_id)['sequence'] == 1
            assert current.store.load_live_world(session).scene.item_states['游戏快照版本'].endswith('/1')
        finally:
            current.store.close()
    asyncio.run(scenario())


def test_old_database_migrates_without_claiming_old_snapshot_is_current(tmp_path):
    async def scenario():
        db = tmp_path/'old.sqlite3'
        current = service(db, [NOW])
        session = app._session('snapshot_test')
        try:
            await current.ensure_world(session,app.WorldInput(game_snapshot=snapshot()))
            version = current.store.load_live_world(session).version
            # Recreate the pre-015 schema, retaining all world/history tables.
            current.store._connection.execute('DROP TABLE unity_observations')
            current.store._connection.execute('DELETE FROM schema_migrations WHERE version=15')
            current.store.close()
            current = service(db, [NOW])
            assert current.store.load_live_world(session).version == version
            assert current.status(session)['game_observation']['status'] == 'unknown'
            assert current.status(session)['world']['location_id'] == 'unknown'
            with pytest.raises(HTTPException):
                await current.ensure_world(session,app.WorldInput())
            await current.ensure_world(session,app.WorldInput(game_snapshot=snapshot(sequence=2)))
            assert current.status(session)['game_observation']['status'] == 'current'
        finally:
            current.store.close()
    asyncio.run(scenario())


def test_expired_stream_emits_only_recoverable_error_without_model(tmp_path):
    async def scenario():
        current = service(tmp_path/'world.sqlite3', [NOW+31])
        request = app.ChatInput(save_id='snapshot_test',text='水杯在哪？',
                               world=app.WorldInput(game_snapshot=snapshot()))
        try:
            events = [json.loads(line) async for line in current.chat_stream(request)]
            assert len(events) == 1
            assert events[0]['type'] == 'error' and events[0]['status'] == 409
            assert '重新发送' in events[0]['detail']
        finally:
            current.store.close()
    asyncio.run(scenario())


def test_status_after_resync_does_not_reuse_previous_mind_activity(tmp_path, monkeypatch):
    @dataclass
    class Mind:
        current_activity: str = '旧记录：在厨房做饭'
    @dataclass
    class Relationship:
        stage: str = '共同生活'
    async def scenario():
        current = service(tmp_path/'world.sqlite3', [NOW])
        session = app._session('snapshot_test')
        mind = Mind()
        monkeypatch.setattr(current.store, 'load_heroine_runtime', lambda session:
                            SimpleNamespace(version=1, living_mind=mind, relationship=Relationship()))
        try:
            await current.ensure_world(session,app.WorldInput(game_snapshot=snapshot()))
            activity = current.status(session)['heroine']['living_mind']['current_activity']
            assert '客厅' in activity and '受阻' in activity and '厨房' not in activity
            assert mind.current_activity == '旧记录：在厨房做饭'
        finally:
            current.store.close()
    asyncio.run(scenario())
