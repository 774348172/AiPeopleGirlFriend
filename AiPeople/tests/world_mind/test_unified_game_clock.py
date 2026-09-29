import asyncio
from datetime import timedelta
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from runtime.world_mind.unity_snapshot import GAME_EPOCH, UnityGameClock
from runtime.world_mind.game_clock import GameClockService
from runtime.world_mind import WorldMindStore
from tools.baiweixi_chat_app import WorldInput, _session
from test_unity_freshness import service, snapshot, NOW


def test_unity_time_is_atomic_persistent_and_never_extrapolated_by_backend(tmp_path):
    async def scenario():
        now=[NOW]; db=tmp_path/'clock.sqlite3'; current=service(db,now); session=_session('snapshot_test')
        try:
            snap=snapshot(game_clock=dict(ready=True,elapsed_seconds=1200,time_scale=4,paused=True))
            await current.ensure_world(session,WorldInput(game_snapshot=snap))
            assert '游戏已暂停' in snap.facts()['当前动作']
            expected=GAME_EPOCH+timedelta(seconds=1200)
            assert current.clock.current_time(session)==expected
            assert current.store.load_live_world(session).last_changed_game_time==expected
            now[0]+=1000
            assert current.clock.current_time(session)==expected
            assert current.status(session)['game_clock']['paused']
            current.clock.close(); current.store.close(); current=service(db,now)
            assert current.clock.current_time(session)==expected
            backwards=snapshot(sequence=2,observed_at_ms=int(now[0]*1000),
                               game_clock=dict(ready=True,elapsed_seconds=1199,time_scale=1,paused=False))
            with pytest.raises(HTTPException):
                await current.ensure_world(session,WorldInput(game_snapshot=backwards))
            assert current.clock.current_time(session)==expected
        finally: current.store.close()
    asyncio.run(scenario())


def test_legacy_pause_survives_queries_and_restart_and_resume_is_idempotent(tmp_path):
    now=[0.0]; db=tmp_path/'legacy.sqlite3'; store=WorldMindStore.open(db)
    clock=GameClockService(store,initial_game_time=GAME_EPOCH,monotonic=lambda:now[0]); session=_session('snapshot_test')
    clock.current_time(session); now[0]=10; paused=clock.pause(session)
    now[0]=100
    assert clock.current_time(session)==paused
    clock.close(); store.close(); store=WorldMindStore.open(db)
    clock=GameClockService(store,initial_game_time=GAME_EPOCH,monotonic=lambda:now[0])
    try:
        now[0]=200; assert clock.current_time(session)==paused
        clock.resume(session); now[0]=210
        assert clock.resume(session)==paused+timedelta(seconds=10)
        now[0]=211
        assert clock.current_time(session)==paused+timedelta(seconds=11)
    finally: clock.close();store.close()


@pytest.mark.parametrize('change',[{'ready':False},{'elapsed_seconds':-1},{'elapsed_seconds':float('nan')},
                                  {'time_scale':0},{'time_scale':9}])
def test_invalid_clock_never_becomes_world_time(change):
    data=dict(ready=True,elapsed_seconds=0,time_scale=1,paused=False);data.update(change)
    with pytest.raises(ValidationError):UnityGameClock(**data)
