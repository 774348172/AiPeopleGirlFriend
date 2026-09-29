import asyncio
import copy
import time
from types import SimpleNamespace
from datetime import datetime
import pytest
from pydantic import ValidationError
from fastapi import HTTPException
from runtime.world_mind.unity_snapshot import UnityGameSnapshot, SubmittedGameWorld
from runtime.world_mind.unity_freshness import UnityFreshness
from runtime.world_mind import WorldMindStore, PersistentWorldStateProvider, GameClockService
from tools.baiweixi_chat_app import BaiWeixiChatService, WorldInput, _session
from tools import baiweixi_chat_app as chat_app


def test_status_world_write_waits_for_chat_but_read_only_poll_does_not(monkeypatch):
    async def scenario():
        lock = asyncio.Lock()
        writes = []
        async def ensure(session, world):
            assert lock.locked()
            writes.append(world)
        service = SimpleNamespace(send_lock=lock, ensure_world=ensure,
            store=SimpleNamespace(load_live_world=lambda session: object()),
            status=lambda session: {'ok': True})
        monkeypatch.setattr(chat_app, '_service', lambda: service)
        await lock.acquire()
        writer = asyncio.create_task(chat_app.status(chat_app.SaveInput(
            save_id='snapshot_test', world=WorldInput())))
        try:
            await asyncio.sleep(0)
            assert not writer.done() and not writes
            result = await asyncio.wait_for(chat_app.status(chat_app.SaveInput(
                save_id='snapshot_test')), timeout=1)
            assert result == {'ok': True}
        finally:
            lock.release()
        await writer
        assert len(writes) == 1
    asyncio.run(scenario())


def sample():
    now = int(time.time()*1000)
    return dict(schema_version=4, devices=[], game_clock=dict(ready=True,elapsed_seconds=0,time_scale=1,paused=False),
                source='unity_main_thread', instance_started_at_ms=now-1000,
                observed_at_ms=now, save_id="snapshot_test", world_id="songjiangfu", instance_id="unity_test", sequence=1, actors=[
        dict(actor_id="protagonist", location_id="apartment_living_room", location_label="客厅", motion="idle", x=-2,y=0,z=-1),
        dict(actor_id="baiweixi", location_id="apartment_living_room", location_label="客厅", motion="blocked", x=-2,y=0,z=0),
    ], entities=[dict(entity_id="water_cup",label="水杯",owner_id="protagonist",state="在男主手中",
                       location_id="apartment_living_room",location_label="客厅",x=-2,y=0,z=-1)],
    actions=[dict(instance_id="action_1",action_id="move_to",status="blocked",target_location_id="apartment_kitchen")], receipts=[])


def v5_sample():
    data = sample()
    data['schema_version'] = 5
    observed = data['observed_at_ms']
    data['facts'] = [
        dict(key='interior_light', label='室内灯光', state='current', value='灯亮着',
             source='unity_main_thread', version=1, observed_at_ms=observed),
        dict(key='living_room_window', label='客厅窗户', state='unknown', value='',
             source='unity_main_thread', version=1, observed_at_ms=observed),
    ]
    return data


def test_per_fact_current_unknown_and_legacy_text_boundary():
    snapshot = UnityGameSnapshot(**v5_sample())
    world = WorldInput(game_snapshot=snapshot, scene_facts=[
        {'label': '客厅窗户', 'value': '已打开'},
        {'label': '室内灯光', 'value': '灯关着'},
    ])
    facts = world.item_states()
    assert facts['室内灯光'] == '灯亮着'
    assert '客厅窗户' not in facts
    assert snapshot.unknown_facts()[0]['subject'] == '客厅窗户'


@pytest.mark.parametrize('change', ['missing_value', 'unknown_value', 'old_current',
                                     'recent_stale', 'wrong_version', 'spoof_label',
                                     'duplicate_key', 'missing_facts'])
def test_per_fact_contract_rejects_inconsistent_records(change):
    data = v5_sample()
    fact = data['facts'][0]
    if change == 'missing_value': fact['value'] = ''
    elif change == 'unknown_value': data['facts'][1]['value'] = '已打开'
    elif change == 'old_current': fact['observed_at_ms'] -= 31_000
    elif change == 'recent_stale': fact.update(state='stale', value='')
    elif change == 'wrong_version': fact['version'] = 2
    elif change == 'spoof_label': fact['label'] = '男主实际位置'
    elif change == 'duplicate_key': data['facts'].append(copy.deepcopy(fact))
    elif change == 'missing_facts': data['facts'] = []
    with pytest.raises(ValidationError): UnityGameSnapshot(**data)


def test_per_fact_unknown_reaches_model_projection():
    async def scenario():
        bridge = SubmittedGameWorld()
        with bridge.bind(UnityGameSnapshot(**v5_sample())):
            projection = await bridge.project()
        assert projection.unknown_facts[0]['subject'] == '客厅窗户'
    asyncio.run(scenario())


def test_per_fact_status_redacts_expired_values_and_rejects_protocol_downgrade(tmp_path):
    async def scenario():
        service = BaiWeixiChatService.__new__(BaiWeixiChatService)
        service.store = WorldMindStore.open(tmp_path / 'facts.sqlite3')
        service.world = PersistentWorldStateProvider(service.store)
        service.clock = GameClockService(service.store, initial_game_time=datetime(1, 10, 11, 18))
        session = _session('snapshot_test')
        data = v5_sample()
        now = data['observed_at_ms'] / 1000
        service._unity_freshness = UnityFreshness(wall=lambda: now, monotonic=lambda: now)
        try:
            await service.ensure_world(session, WorldInput(game_snapshot=UnityGameSnapshot(**data)))
            current = service.status(session)['fact_observations']
            assert current[0]['state'] == 'current' and current[0]['value'] == '灯亮着'
            assert current[1]['state'] == 'unknown' and current[1]['value'] == ''
            old = sample()
            old['sequence'] = 2
            with pytest.raises(HTTPException) as error:
                await service.ensure_world(session, WorldInput(game_snapshot=UnityGameSnapshot(**old)))
            assert error.value.status_code == 409
            service._unity_freshness.wall = lambda: now + 31
            expired = service.status(session)['fact_observations']
            assert all(fact['state'] == 'stale' and fact['value'] == '' for fact in expired)
        finally:
            service.store.close()
    asyncio.run(scenario())


def test_snapshot_keeps_actual_location_target_and_possession_separate():
    snapshot = UnityGameSnapshot(**sample())
    world = WorldInput(held_item="假的物品", location_id="apartment_kitchen", game_snapshot=snapshot)
    facts = world.item_states()
    assert facts["白未晞实际位置"] == "客厅"
    assert "受阻" in facts["当前动作"] and "apartment_kitchen" in facts["当前动作"]
    assert facts["男主手持物"] == "水杯"
    assert "水杯" in facts["实体:water_cup"]


@pytest.mark.parametrize("case", ["duplicate_actor","duplicate_entity","bad_owner","terminal_pending","unfinished_receipt","nan"])
def test_rejects_inconsistent_snapshots(case):
    data = sample()
    if case == "duplicate_actor": data['actors'][1] = data['actors'][0]
    if case == "duplicate_entity": data['entities'].append(copy.deepcopy(data['entities'][0]))
    if case == "bad_owner": data['entities'][0]['location_id'] = 'apartment_kitchen'
    if case == "terminal_pending": data['actions'][0]['status'] = 'completed'
    if case == "unfinished_receipt": data['receipts'], data['actions'] = data['actions'], []
    if case == "nan": data['actors'][0]['x'] = float('nan')
    with pytest.raises(ValidationError): UnityGameSnapshot(**data)


def test_adapter_is_scoped_read_only_and_does_not_leak_between_requests():
    async def scenario():
        bridge = SubmittedGameWorld()
        async def one():
            with bridge.bind(UnityGameSnapshot(**sample())):
                await asyncio.sleep(0)
                projection = await bridge.project()
                assert projection.pending_actions[0]['status'] == 'blocked'
                assert '客厅' in projection.heroine_activity
                assert projection.item_states is None
                assert not (await bridge.execute_action('move_to')).accepted
        async def two():
            await asyncio.sleep(0)
            assert (await bridge.project()).pending_actions == ()
        await asyncio.gather(one(),two())
        assert (await bridge.project()).heroine_activity is None
    asyncio.run(scenario())


def test_production_world_write_uses_snapshot_entities_and_rejects_stale_sequence(tmp_path):
    async def scenario():
        service = BaiWeixiChatService.__new__(BaiWeixiChatService)
        service.store = WorldMindStore.open(tmp_path/'world.sqlite3')
        service.world = PersistentWorldStateProvider(service.store)
        service.clock = GameClockService(service.store, initial_game_time=datetime(1,10,11,18))
        session = _session('snapshot_test')
        try:
            data = sample()
            world = WorldInput(location_id="wrong_place",game_snapshot=UnityGameSnapshot(**data))
            with pytest.raises(HTTPException):
                await service.ensure_world(_session('different_save'),world)
            assert service.store.load_live_world(_session('different_save')) is None
            await service.ensure_world(session,world)
            live = service.store.load_live_world(session)
            assert live.protagonist.location_id == 'apartment_living_room'
            assert live.protagonist.held_item_ids == ('water_cup',)
            with pytest.raises(HTTPException) as error: await service.ensure_world(session,world)
            assert error.value.status_code == 409
            data['sequence']=2
            data['entities'][0]['owner_id']=''
            data['entities'][0]['state']='在餐桌上'
            await service.ensure_world(session,WorldInput(game_snapshot=UnityGameSnapshot(**data)))
            live = service.store.load_live_world(session)
            assert live.protagonist.held_item_ids == ()
            assert live.scene.item_states['男主手持物'] == '手里没有拿物品'
        finally: service.store.close()
    asyncio.run(scenario())
