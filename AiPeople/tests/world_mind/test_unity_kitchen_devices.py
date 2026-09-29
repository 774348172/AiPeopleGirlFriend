import copy

import pytest
from pydantic import ValidationError

from runtime.world_mind.unity_snapshot import UnityGameSnapshot
from tools.baiweixi_chat_app import WorldInput
from test_unity_game_snapshot import sample


def device(device_id, label, operating='unknown', door='unknown'):
    return dict(device_id=device_id, label=label, operating_state=operating,
                door_state=door, location_id='apartment_kitchen',
                location_label='出租屋厨房', x=-4.7, y=0, z=2.8)


def test_operating_devices_and_unknown_details_stay_distinct():
    data=sample()
    data['devices']=[device('kitchen_stove','燃气灶','running'),
                     device('kitchen_hood','抽油烟机','stopped'),
                     device('kitchen_fridge','冰箱')]
    facts=WorldInput(game_snapshot=UnityGameSnapshot(**data)).item_states()
    assert facts['设备:kitchen_stove']=='燃气灶；出租屋厨房；正在运行'
    assert facts['设备:kitchen_hood']=='抽油烟机；出租屋厨房；已关闭'
    assert '运行状态未知' in facts['设备:kitchen_fridge']
    assert '门状态和内部温度未知' in facts['设备:kitchen_fridge']
    data['devices'][0]['operating_state']='stopped'
    after=WorldInput(game_snapshot=UnityGameSnapshot(**data)).item_states(facts)
    assert '旧记录' in after['本轮游戏状态变化']
    assert after['设备:kitchen_stove'].endswith('已关闭')


def test_unstructured_scene_text_cannot_claim_device_or_entity_state():
    with pytest.raises(ValidationError):
        WorldInput(scene_facts=[{'label':'设备:kitchen_stove','value':'正在运行'}])
    with pytest.raises(ValidationError):
        WorldInput(scene_facts=[{'label':'实体:water_cup','value':'在我手里'}])


@pytest.mark.parametrize('change', ['duplicate','fridge_runs','stove_door','invalid_state','too_many'])
def test_rejects_fake_device_observations(change):
    data=sample()
    data['devices']=[device('kitchen_stove','燃气灶','stopped')]
    if change=='duplicate':data['devices'].append(copy.deepcopy(data['devices'][0]))
    elif change=='fridge_runs':data['devices'].append(device('kitchen_fridge','冰箱','running'))
    elif change=='stove_door':data['devices'][0]['door_state']='open'
    elif change=='invalid_state':data['devices'][0]['operating_state']='heating'
    elif change=='too_many':data['devices']*=7
    with pytest.raises(ValidationError):UnityGameSnapshot(**data)
