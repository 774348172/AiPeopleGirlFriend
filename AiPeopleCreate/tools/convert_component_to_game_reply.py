"""Project typed component snapshots into the existing GAME_REPLY renderer inputs.

No location/answer inference from topic strings. Old placeholder previews fail closed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from build_v5_pilot_dataset import (
    ROOT, WORLD_SCENE_CANON_PATH, _base_model_view, _load_home_scene_canon,
    _load_scene_canon,
)
from data_gen_v4.runtime_grounded.conversational import validate_conversational_scenario
from data_gen_v4.runtime_grounded.persona_projection import male_profile_labels, persona_source_hashes
from dialogue_integrity import validate_dialogue_structure
from data_gen_v4.runtime_grounded.renderer import (
    render_game_reply_context_from_model_view, build_game_reply_chat_messages,
    parse_game_reply_dialogue_lines,
)

SOURCE_SCHEMA = 'aip.home_component_episode.v2'
PROFILE_PATH = ROOT.parent / '世界设定/松江府/男主基础设定.yaml'
FIELDS = {
    'electric_storage_water_heater': ('电源状态', '水温', '剩余热水量'),
    'washing_machine': ('电源状态', '运行模式', '剩余时间'),
    'refrigerator': ('电源状态', '运行模式', '现有食材'),
    'gas_stove': ('点火状态', '火力'),
    'range_hood': ('电源状态', '运行档位'),
    'rice_cooker': ('电源状态', '运行模式', '剩余时间'),
    'microwave_oven': ('电源状态', '运行模式', '剩余时间', '内部食物'),
    'electric_kettle': ('电源状态', '运行模式', '水温', '是否有水'),
    'fan': ('电源状态', '运行档位'),
    'television': ('电源状态', '当前频道'),
}
FIXTURES = {'sofa': '沙发', 'small_table': '小茶几', 'basic_storage': '基本收纳设施',
            'television': '电视', 'fan': '风扇', 'sink': '水槽', 'gas_stove': '燃气灶',
            'range_hood': '抽油烟机', 'refrigerator': '冰箱', 'basic_cabinets': '基础橱柜',
            'toilet': '马桶', 'washbasin': '洗手池', 'shower_area': '淋浴区'}
FIXTURES.update(dict(shoe_cabinet='窄鞋柜',key_hooks='分开的常用与备用钥匙挂钩',umbrella_bucket='雨伞桶',
    television_cabinet='小电视柜',dining_table='双人餐桌',two_chairs='两把椅子',floor_lamp='立式暖光灯',
    bed='床',wardrobe='衣柜',desk='书桌',desk_outlet='桌边插座',bookshelf='书架',storage_shelf='储物架',
    spare_bedding='备用被褥',cup_rack='杯架',rice_bin='米桶',spice_rack='调料架',cutting_board='砧板',
    two_bowls='两只常用碗',towel_rack='毛巾架',mirror='洗漱镜',mop_corner='拖把收纳角',drying_rod='晾衣杆',laundry_basket='洗衣篮'))
ITEMS = ('备用钥匙', '借来的书', '手机充电器', '白围巾', '洗好的水果', '折叠伞', '白未晞的纸箱')
OTHER = {'厨房窗户': ('开关状态',), '阳台门': ('开关状态',), '玄关门': ('锁闭状态',),
         '主卧灯': ('开关状态',), '客厅灯': ('开关状态',), '厨房水龙头': ('开关状态',),
         '卫生间地面': ('干湿状态',), '小阳台衣物': ('是否晾衣', '干湿状态'),
         '快递': ('送达状态',), '昨天晚饭': ('地点与菜品',),
         '空调': ('是否存在',), '电视柜': ('是否存在',), '今天买菜': ('付款结果',)}
PROFILE_LABELS = {
    'identity.hometown': '籍贯', 'identity.nationality': '国籍',
    'preferences.food.regional_taste': '地方口味', 'preferences.food.spicy_tolerance': '吃辣偏好',
    'preferences.food.richness': '油腻偏好', 'preferences.drinks': '饮品偏好',
    'preferences.environment': '环境偏好', 'preferences.organization': '收纳习惯',
    'preferences.leisure': '休闲偏好', 'preferences.companionship': '陪伴偏好',
    'preferences.low_mood': '低落时的交流偏好', 'preferences.reminders': '提醒偏好',
    'preferences.privacy': '隐私边界', 'preferences.physical_contact': '身体接触边界',
}


PROFILE_LABELS.update(male_profile_labels())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def field_value(obj, path):
    for key in path.split('.'):
        obj = obj[key]
    return obj


def validate_source(row, canon):
    if row.get('schema_version') != SOURCE_SCHEMA:
        raise ValueError('旧占位预览不合格：需要带完整会话和结构化状态的 component v2')
    for key in ('sample_id', 'component_ids', 'topic', 'snapshot', 'memory_turns', 'recent_turns', 'current_user', 'relation'):
        if not row.get(key):
            raise ValueError('组件缺失：' + key)
    for key in ('scene_id', 'snapshot_id', 'relation_id', 'utterance_id', 'contract_id', 'family_id'):
        if not row['component_ids'].get(key):
            raise ValueError('组件引用缺失：' + key)
    snap = row['snapshot']
    validate_dialogue_structure(row)
    now = datetime.fromisoformat(snap['time'])
    if now.utcoffset() is None:
        raise ValueError('快照时间缺少时区')
    # Home fixtures use stable room keys.  External scenes use the fixed zone
    # labels from the selected scene registry entry; never accept an arbitrary
    # location string that would bypass the authoritative scene.
    if 'spaces' in canon.get('layout', {}):
        valid_areas = set(canon['layout']['spaces'])
    else:
        valid_areas = set(canon.get('layout', {}).get('zones') or ())
    if snap['area'] not in valid_areas or any(snap['people'][p]['area'] not in valid_areas for p in ('男主', '白未晞')):
        raise ValueError('人物位置不在所选场景正典内')
    # This pilot is face-to-face; remote/separate-room chat needs its own channel contract.
    if any(snap['people'][p]['area'] != snap['area'] for p in ('男主', '白未晞')):
        raise ValueError('面对面会话的人物位置与场景不一致')
    previous = None
    for kind in ('memory_turns', 'recent_turns'):
        turns = row[kind]
        if len(turns) < 4 or len(turns) % 2:
            raise ValueError('记忆和最近对话分别至少两个完整来回')
        for n, turn in enumerate(turns):
            if turn['speaker'] != ('男主' if n % 2 == 0 else '白未晞'):
                raise ValueError('本组件只支持有明确角色的双人会话')
            if not turn.get('text') or not turn.get('source'):
                raise ValueError('会话缺少原话或来源')
            if '之前聊过' in turn['text'] or '我又想起' == turn['text']:
                raise ValueError('占位历史不得进入生成')
            at = datetime.fromisoformat(turn['time'])
            if at.utcoffset() is None or at >= now or (previous and at <= previous):
                raise ValueError('历史时间必须严格递增且早于本轮')
            previous = at


def convert(row: dict, i: int = 0) -> dict:
    from synthetic_history import project_history
    from life_state_extension import project_calendar
    result = _convert(row, i)
    project_calendar(result, row)
    project_history(result['model_view'], row)
    return result


def _convert(row: dict, i: int = 0) -> dict:
    scene_ref = row.get('component_ids', {}).get('scene_id') if isinstance(row.get('component_ids'), dict) else None
    canon, scene_source_path, is_home = _load_scene_canon(scene_ref)
    from life_state_extension import extend_canon
    canon = extend_canon(row, canon)
    validate_source(row, canon)
    if not is_home:
        return _convert_external(row, i, canon, scene_source_path)
    view = _base_model_view(8000)
    snap = row['snapshot']; spaces = canon['layout']['spaces']
    area = spaces[snap['area']]; snapshot_id = row['component_ids']['snapshot_id']
    view['snapshot'].update(captured_game_time=snap['time'], snapshot_id=snapshot_id)
    view['snapshot']['scene'].update(scene_id=area['id'], location_label=area['label'], item_states={})
    person = snap['people']['男主']
    view['snapshot']['protagonist'].update(location_id=spaces[person['area']]['id'],
        location_label=spaces[person['area']]['label'], activity=person['activity'], body_state={'身体': person['body']})
    heroine = snap['people']['白未晞']
    view['previous_heroine_runtime']['living_mind'].update(current_activity=heroine['activity'],
        emotion=heroine['emotion'], body=heroine['body'], attention=row['topic'])
    # Heroine location uses the existing game-feedback channel, never a fabricated snapshot.heroine key.
    view['snapshot']['world_state']['scene_text'] = f"两人在{area['label']}当面说话。"
    view['scene_canon'] = copy.deepcopy(canon)
    facts = []
    def fact(subject, predicate, value, source='当前快照'):
        facts.append({'fact_id': f'{snapshot_id}.{len(facts):03d}', 'subject': subject,
                      'predicate': predicate, 'value': value, 'source': source,
                      'status': 'unknown' if value is None else 'current'})
    b = canon['building']
    fact('家庭', '固定场景', f"{b['district']}，{b['floor']}楼，无电梯，{b['condition']}，两室一厅", '家庭正典')
    fact('家庭', '空间', '、'.join(s['label'] for s in spaces.values()), '家庭正典')
    for note in b['notes']:
        fact('楼房', '环境边界', note, '家庭正典')
    fact('主卧', '用途', '男主居住', '家庭正典')
    fact('次卧', '用途', '杂物和备用空间，尚未分配给白未晞', '家庭正典')
    fact('白未晞', '空间归属', canon['occupancy']['baiweixi']['rule'], '家庭正典')
    fact('白未晞的纸箱', '默认位置规则', canon['fixed_objects']['baiweixi_cardboard_box']['rule'], '家庭正典')
    for room in spaces.values():
        if room.get('fixtures'):
            fact(room['label'], '固定设施', '、'.join(FIXTURES[f] for f in room['fixtures']), '家庭正典')
        if room.get('connected_to'):
            fact(room['label'], '连接空间', '、'.join(spaces[f]['label'] for f in room['connected_to']), '家庭正典')
    fact('世界', '当前时间', snap['time'].replace('T', ' ') + '（上海时区）')
    fact('室外', '天气', snap.get('weather'))
    for name, data in snap['people'].items():
        for pred, val in [('当前位置', spaces[data['area']]['label']), ('当前活动', data['activity']),
                          ('当前身体状态', data['body']), ('当前情绪', visible_emotion(name, data))]:
            source = data['emotion_source'] if name=='男主' and pred=='当前情绪' and val is not None else '当前快照'
            fact(name, pred, val, source)
    table = {}
    for device in canon['equipment']:
        loc = device['location']
        label = spaces[loc]['label'] if loc in spaces else '卫生间或小阳台区域（具体安装点未登记）'
        fact(device['label'], '固定设备位置', label, '家庭正典')
        fact(device['label'], '存在性', '家中固定存在', '家庭正典')
        if device.get('capacity'):
            fact(device['label'], '额定容量', device['capacity'], '家庭正典')
        for pred in FIELDS[device['id']]:
            table[(device['label'], pred)] = None
    table.update({(x, '当前位置'): None for x in ITEMS})
    table.update({(obj, pred): None for obj, preds in OTHER.items() for pred in preds})
    for subject,predicates in canon.get('runtime_fields',{}).items():
        for predicate in predicates: table[(subject,predicate)]=None
    for subject,value in canon.get('fixed_presence',{}).items():
        table.pop((subject,'是否存在'),None)
        fact(subject,'是否存在',value,'家庭正典')
    for predicate,value in canon.get('fixed_details',{}).items(): fact('家庭',predicate,value,'家庭正典')
    for subject,value in canon.get('routine_locations',{}).items():
        fact(subject,'通常归位点（不代表当前位置）',value,'家庭正典生活资料')
    overrides = {}
    for update in snap.get('states', []):
        key = (update['subject'], update['predicate'])
        if key not in table or key in overrides:
            raise ValueError(f'未定义或重复的状态字段：{key}')
        if not update.get('source') or not isinstance(update['value'], (str, type(None))):
            raise ValueError('状态必须包含来源及字符串/未知值')
        if update['value'] in ('', 'unknown', '未知'):
            raise ValueError('未知应使用 null，不能伪装成已知字符串')
        overrides[key] = update
    for (subject, predicate), default in table.items():
        override = overrides.get((subject, predicate), {})
        fact(subject, predicate, override.get('value', default), override.get('source', '当前快照未登记'))
    sources = {t['source']: t for t in row['memory_turns'] + row['recent_turns']}
    known = row.get('known_profile', {})
    if set(known) - set(PROFILE_LABELS):
        raise ValueError('未定义的男主设定路径')
    visible_profile, withheld = [], []
    for path, label in PROFILE_LABELS.items():
        value = field_value(view['protagonist_profile'], path)
        if path in known:
            evidence = sources.get(known[path])
            if not evidence or evidence['speaker'] != '男主' or value not in evidence['text']:
                raise ValueError('男主已知设定缺少逐字可追溯的男主原话：' + path)
            fact('男主已知设定', label, value, '男主原话：' + known[path])
            facts[-1]['known_since'] = evidence['time']
            visible_profile.append(path)
        else:
            withheld.append(path)
    fact('男主资料', '知情边界', '基础设定不自动成为白未晞的记忆；只有明确获知的资料可用于回答')
    fact('男主资料', '尚未获知的项目', '、'.join(PROFILE_LABELS[p] for p in withheld) or '无')
    for unknown in view['protagonist_profile']['unknown']:
        fact('男主', unknown, None, '男主基础设定未确认')
    view['known_by_baiweixi'] = [{'path': p, 'source': known[p]} for p in visible_profile]
    view['authoritative_world'].update(facts=facts, scene_canon=copy.deepcopy(canon),
        source_hashes={'scene': digest(WORLD_SCENE_CANON_PATH), 'protagonist': digest(PROFILE_PATH), **persona_source_hashes()})
    view['game_feedback'] = [fact_text(f) for f in facts if f['value'] is not None]
    view['unknown_facts'] = [{'subject': f['subject'], 'predicate': f['predicate'], 'source': f['source'],
                              'reason': '未登记可靠值', 'status': 'unknown'} for f in facts if f['value'] is None]
    view['authoritative_world']['unknown_facts'] = copy.deepcopy(view['unknown_facts'])
    view['runtime_snapshot'] = copy.deepcopy(snap)
    memory = '\n'.join(f"{t['speaker']}（{t['time']}；来源：{t['source']}）：{t['text']}" for t in row['memory_turns'])
    view['selected_memory_frame'] = {'selected_memories': [{'statement': memory, 'temporal_relation': '旧会话，不代表当前状态'}]}
    view['recent_dialogue'] = [f"{t['speaker']}：{t['text']}" for t in row['recent_turns']]
    view['current_protagonist_utterance'] = row['current_user']
    ids = row['component_ids']
    return {'schema_version': 'aip.conversational_reply_scenario.v1', 'scenario_id': row['sample_id'],
            'profile_id': 'baiweixi', 'render_profile_id': 'GAME_REPLY_CONTEXT_V1', 'task_type': row.get('task_type', 'follow_up_question'),
            'model_view': view, 'component_source': copy.deepcopy(row), 'component_ids': ids,
            'topic': row['topic'], 'dialogue_relation': row['relation'], 'review_criteria': row.get('review_criteria', []),
            'selection_method': 'curated_fixture_selection', 'withheld_profile_paths': withheld,
            'style_contract': {'style_intent': '根据当前状态自然承接最近对话', 'reply_shape': 'spoken_only_natural_dialogue',
                               'forbidden_styles': ['action_narration', 'unsupported_specific_detail'], 'unsupported_expansion_policy': 'reject'},
            'split_anchors': ['episode:'+ids['family_id'], 'memory:'+ids['family_id'], 'variation:'+ids['family_id']]}


def fact_text(f):
    known = f"；获知时间：{f['known_since']}" if f.get('known_since') else ''
    return f"{f['subject']}的{f['predicate']}：{f['value']}（来源：{f['source']}）{known}"


def visible_emotion(name, data):
    if name == '白未晞':
        return data['emotion']
    visibility = data.get('emotion_visibility', 'hidden')
    if visibility not in ('hidden', 'visible'):
        raise ValueError('emotion_visibility must be hidden or visible')
    if visibility == 'visible' and not data.get('emotion_source'):
        raise ValueError('visible protagonist emotion needs observation/report source')
    return data['emotion'] if visibility == 'visible' else None


def _convert_external(row: dict, i: int, canon: dict, scene_source_path: Path) -> dict:
    """Project a registered public/outdoor scene into the same model view.

    External scenes intentionally have no home-appliance table.  Their fixed
    zones/fixtures/business facts come from the scene registry, while every
    runtime-only item is emitted as explicit ``unknown`` unless the component
    snapshot supplies a structured update for that exact subject/predicate.
    """
    snap = row['snapshot']; scene_id = row['component_ids']['scene_id']
    display = str(canon.get('display_name') or scene_id)
    zones = list((canon.get('layout') or {}).get('zones') or ())
    if not zones:
        raise ValueError(f'外出场景缺少固定区域：{scene_id}')
    area = str(snap['area'])
    zone_id = ''.join(ch if ch.isalnum() else '_' for ch in area).strip('_') or 'zone'
    view = _base_model_view(8000)
    view['snapshot'].update(captured_game_time=snap['time'], snapshot_id=row['component_ids']['snapshot_id'])
    view['snapshot']['scene'].update(scene_id=scene_id, location_label=display, item_states={})
    person = snap['people']['男主']
    view['snapshot']['protagonist'].update(location_id=zone_id, location_label=area,
        activity=person['activity'], body_state={'身体': person['body']})
    heroine = snap['people']['白未晞']
    view['previous_heroine_runtime']['living_mind'].update(current_activity=heroine['activity'],
        emotion=heroine['emotion'], body=heroine['body'], attention=row['topic'])
    view['snapshot']['world_state']['scene_text'] = f"两人在{display}的{area}当面说话。"
    view['scene_canon'] = copy.deepcopy(canon)
    facts = []
    def fact(subject, predicate, value, source='当前快照'):
        facts.append({'fact_id': f"{row['component_ids']['snapshot_id']}.{len(facts):03d}",
                      'subject': subject, 'predicate': predicate, 'value': value,
                      'source': source, 'status': 'unknown' if value is None else 'current'})
    location = canon.get('location') or {}
    fact(display, '场景编号', scene_id, '外出场景正典')
    fact(display, '固定区域', '、'.join(map(str, zones)), '外出场景正典')
    if location.get('distance_from_home'):
        distance = location['distance_from_home']
        if isinstance(distance, dict):
            distance = (f"{distance['min']}至{distance['max']}公里" if 'min' in distance
                        else f"{distance['max']}公里以内")
        fact(display, '离家距离', distance, '外出场景正典')
    if location.get('area_sqm'):
        size = location['area_sqm']
        if isinstance(size, dict) and {'min', 'max'} <= size.keys():
            area_text = f"约{size['min']}至{size['max']}平方米"
        elif isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
            area_text = f"约{size:g}平方米"
        else:
            raise ValueError(f'场景面积必须是正数或 min/max 范围：{scene_id}')
        fact(display, '面积', area_text, '外出场景正典')
    if canon.get('business', {}).get('menu_size'):
        fact(display, '菜单规模', canon['business']['menu_size'], '外出场景正典')
    district = canon.get('district_scale') or {}
    for key, label in [('shop_count','商铺规模'), ('fixed_character','街区特点')]:
        if district.get(key):
            fact(display, label, district[key], '外出场景正典')
    if district.get('fixed_shop_anchors'):
        fact(display, '固定店铺', '、'.join(district['fixed_shop_anchors']), '外出场景正典')
    if canon.get('business', {}).get('opening_hours'):
        fact(display, '营业时间', canon['business']['opening_hours'], '外出场景正典')
    for predicate,value in canon.get('fixed_details',{}).items(): fact(display,predicate,value,'外出场景正典')
    business=canon.get('business_records',{})
    if business.get('public_catalog'):
        fact(display,'常规目录（不代表今日供应）','、'.join(business['public_catalog']),'公开经营资料')
    if business.get('public_reference_prices'):
        fact(display,'常规价目（具体规格和实售价另记）','；'.join(k+'：'+v for k,v in business['public_reference_prices'].items()),'公开经营资料')
    # Staff names are audit-only roster data; do not invent an introduction to the character.
    if business.get('staff_roster'):
        fact(display,'常规人员岗位（不代表当前在场）','、'.join(p['role'] for p in business['staff_roster']),'外出场景正典')
    for device in canon.get('equipment',[]): fact(device,'存在性','固定设备','外出场景正典')
    for key in ('fixed_fixtures', 'variable_items'):
        values = canon.get(key)
        if values:
            fact(display, '固定设施' if key == 'fixed_fixtures' else '可变项目', '、'.join(map(str, values)), '外出场景正典')
    ownership = canon.get('ownership') or {}
    for key, label in (('owner', '所有者'), ('operator', '经营者'), ('staff', '员工')):
        if key in ownership:
            fact(display, label, {'protagonist':'男主', 'none':'无员工'}.get(str(ownership[key]), str(ownership[key])), '外出场景正典')
    relation = canon.get('character_relation') or {}
    if relation.get('baiweixi_status'):
        fact('白未晞', '与场景关系', relation['baiweixi_status'], '外出场景正典')
    fact('世界', '当前时间', snap['time'].replace('T', ' ') + '（上海时区）')
    fact('室外', '天气', snap.get('weather'))
    for name, data in snap['people'].items():
        fact(name, '当前位置', data['area']); fact(name, '当前活动', data['activity'])
        fact(name, '当前身体状态', data['body'])
        emotion = visible_emotion(name, data)
        fact(name, '当前情绪', emotion, data['emotion_source'] if name=='男主' and emotion is not None else '当前快照')
    # Apply exact runtime updates and mark all other declared runtime facts unknown.
    registered = {(display, str(p)) for p in [*(canon.get('runtime_only_facts') or []), *(canon.get('unknown_by_default') or [])]}
    for fields in (canon.get('shared_rules', {}).get('common_runtime_fields', {}), canon.get('runtime_fields', {})):
        registered.update((s, p) for s, predicates in fields.items() for p in predicates)
    registered.update((device, p) for device in canon.get('equipment', [])
                      for p in ('电源状态','运行状态','是否故障','是否清洁完成'))
    updates = {}
    for update in snap.get('states', []):
        if not isinstance(update, dict) or not update.get('subject') or not update.get('predicate'):
            raise ValueError('外出场景状态缺少主体或字段')
        key = (update['subject'], update['predicate'])
        if key in updates or key not in registered:
            raise ValueError(f'未定义或重复的状态字段：{key}')
        updates[key] = update
    for key, update in updates.items():
        if not update.get('source') or not isinstance(update.get('value'), (str, type(None))):
            raise ValueError('状态必须包含来源及字符串/未知值')
        if update['value'] in ('', 'unknown', '未知'):
            raise ValueError('未知应使用 null，不能伪装成已知字符串')
        fact(key[0], key[1], update['value'], update['source'])
        view['snapshot']['scene']['item_states'][f'{key[0]}.{key[1]}'] = update['value']
    for runtime_fact in canon.get('runtime_only_facts') or ():
        # Registry entries are intentionally descriptive (e.g. “是否营业”);
        # use the scene as subject to keep unknowns explicit and stable.
        if not any(f['subject'] == display and f['predicate'] == runtime_fact for f in facts):
            fact(display, str(runtime_fact), None, '当前快照未登记')
    for unknown_item in canon.get('unknown_by_default') or ():
        if not any(f['subject'] == display and f['predicate'] == unknown_item for f in facts):
            fact(display, str(unknown_item), None, '场景正典默认未知')
    typed=dict(canon.get('shared_rules',{}).get('common_runtime_fields',{}))
    for subject,predicates in canon.get('runtime_fields',{}).items(): typed[subject]=list(set(typed.get(subject,[]))|set(predicates))
    for device in canon.get('equipment',[]): typed[device]=['电源状态','运行状态','是否故障','是否清洁完成']
    for subject,predicates in typed.items():
        for predicate in sorted(predicates):
            if not any(f['subject']==subject and f['predicate']==predicate for f in facts): fact(subject,predicate,None,'当前快照未登记')
    # Reuse the exact male-profile disclosure policy from home conversion.
    sources = {t['source']: t for t in row['memory_turns'] + row['recent_turns']}
    known = row.get('known_profile', {})
    if set(known) - set(PROFILE_LABELS):
        raise ValueError('未定义的男主设定路径')
    visible_profile, withheld = [], []
    for path, label in PROFILE_LABELS.items():
        value = field_value(view['protagonist_profile'], path)
        if path in known:
            evidence = sources.get(known[path])
            if not evidence or evidence['speaker'] != '男主' or value not in evidence['text']:
                raise ValueError('男主已知设定缺少逐字可追溯的男主原话：' + path)
            fact('男主已知设定', label, value, '男主原话：' + known[path]); visible_profile.append(path)
            facts[-1]['known_since'] = evidence['time']
        else:
            withheld.append(path)
    fact('男主资料', '知情边界', '基础设定不自动成为白未晞的记忆；只有明确获知的资料可用于回答')
    fact('男主资料', '尚未获知的项目', '、'.join(PROFILE_LABELS[p] for p in withheld) or '无')
    for unknown in view['protagonist_profile']['unknown']:
        fact('男主', unknown, None, '男主基础设定未确认')
    view['known_by_baiweixi'] = [{'path': p, 'source': known[p]} for p in visible_profile]
    bundle_hash = digest(scene_source_path)
    view['authoritative_world'].update(facts=facts, scene_canon=copy.deepcopy(canon),
        source_hashes={'scene': bundle_hash, 'protagonist': digest(PROFILE_PATH), **persona_source_hashes()})
    view['game_feedback'] = [fact_text(f) for f in facts if f['value'] is not None]
    view['unknown_facts'] = [{'subject': f['subject'], 'predicate': f['predicate'], 'source': f['source'],
                              'reason': '未登记可靠值', 'status': 'unknown'} for f in facts if f['value'] is None]
    view['authoritative_world']['unknown_facts'] = copy.deepcopy(view['unknown_facts'])
    view['runtime_snapshot'] = copy.deepcopy(snap)
    memory = '\n'.join(f"{t['speaker']}（{t['time']}；来源：{t['source']}）：{t['text']}" for t in row['memory_turns'])
    view['selected_memory_frame'] = {'selected_memories': [{'statement': memory, 'temporal_relation': '旧会话，不代表当前状态'}]}
    view['recent_dialogue'] = [f"{t['speaker']}：{t['text']}" for t in row['recent_turns']]
    view['current_protagonist_utterance'] = row['current_user']
    ids = row['component_ids']
    return {'schema_version': 'aip.conversational_reply_scenario.v1', 'scenario_id': row['sample_id'],
            'profile_id': 'baiweixi', 'render_profile_id': 'GAME_REPLY_CONTEXT_V1', 'task_type': row.get('task_type', 'follow_up_question'),
            'model_view': view, 'component_source': copy.deepcopy(row), 'component_ids': ids,
            'topic': row['topic'], 'dialogue_relation': row['relation'], 'review_criteria': row.get('review_criteria', []),
            'selection_method': 'curated_fixture_selection', 'withheld_profile_paths': withheld,
            'style_contract': {'style_intent': '根据当前状态自然承接最近对话', 'reply_shape': 'spoken_only_natural_dialogue',
                               'forbidden_styles': ['action_narration', 'unsupported_specific_detail'], 'unsupported_expansion_policy': 'reject'},
            'split_anchors': ['episode:'+ids['family_id'], 'memory:'+ids['family_id'], 'variation:'+ids['family_id']]}


def validate(s):
    validate_conversational_scenario(s)
    expected = convert(s['component_source'])
    v, e = s['model_view'], expected['model_view']
    for field in ('game_feedback', 'unknown_facts', 'scene_canon', 'protagonist_profile', 'female_character_profile', 'known_by_baiweixi',
                  'selected_memory_frame', 'recent_dialogue', 'current_protagonist_utterance', 'runtime_snapshot'):
        if v.get(field) != e[field]:
            raise ValueError('权威投影与组件/正典不一致：' + field)
    for field in ('scene', 'protagonist', 'captured_game_time', 'world_state'):
        if v['snapshot'][field] != e['snapshot'][field]:
            raise ValueError('场景/人物快照不一致：' + field)
    if v['authoritative_world'] != e['authoritative_world']:
        # Versions come from the base fixture index; compare only authoritative payload fields.
        for key in ('facts', 'unknown_facts', 'scene_canon', 'source_hashes'):
            if v['authoritative_world'][key] != e['authoritative_world'][key]:
                raise ValueError('权威来源与投影不一致：' + key)
    return expected


def validate_final_messages(s, messages):
    expected = validate(s)
    view = expected['model_view']
    text = messages[0]['content']
    context = render_game_reply_context_from_model_view(view)
    if context not in text:
        raise ValueError('最终消息未完整包含从源组件重建的权威上下文')
    for value in view['female_character_profile']['daily_dialogue'].values():
        if value not in text:
            raise ValueError('最终角色锚丢失权威日常设定：' + value)
    from synthetic_history import event_text
    for event in s['component_source'].get('historical_events', []):
        if event_text(event) not in text:
            raise ValueError('最终消息丢失冻结的历史事件：' + event['source'])
    for f in view['authoritative_world']['facts']:
        needle = fact_text(f) if f['value'] is not None else f"{f['subject']}的{f['predicate']}：未知"
        if needle not in text:
            raise ValueError('最终消息丢失事实/未知：' + needle)
    dialogue = build_game_reply_chat_messages(parse_game_reply_dialogue_lines(view['recent_dialogue']),
        character_display_name='白未晞', current_protagonist_utterance=view['current_protagonist_utterance'])
    if messages[1:] != [{'role': r, 'content': t} for r, t in dialogue]:
        raise ValueError('最终消息丢失或错置最近对话')
    facts = view['authoritative_world']['facts']
    return {'facts_total': len(facts), 'facts_visible': len(facts), 'coverage': 1.0,
            'known': sum(f['value'] is not None for f in facts), 'unknown': sum(f['value'] is None for f in facts),
            'profile_known': len(view['known_by_baiweixi']), 'profile_withheld': len(s['withheld_profile_paths']),
            'memory_turns': len(s['component_source']['memory_turns']), 'recent_turns': len(s['component_source']['recent_turns']),
            'context_sha256': hashlib.sha256(context.encode()).hexdigest(), 'input_chars': len(text)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
    converted = [convert(r) for r in rows]
    for s in converted:
        validate(s)
    args.output_root.mkdir(parents=True, exist_ok=False)
    (args.output_root/'game_reply_scenarios.jsonl').write_text('\n'.join(json.dumps(s,ensure_ascii=False) for s in converted)+'\n',encoding='utf-8')
    (args.output_root/'conversion_report.json').write_text(json.dumps({'converted':len(converted), 'source_validation':True,
        'training_started':False, 'final_message_preflight_required':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'converted':len(converted),'output':str(args.output_root)},ensure_ascii=False))


if __name__ == '__main__':
    main()
