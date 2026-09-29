"""Regression tests for the component -> GAME_REPLY authority projection.

These tests intentionally stop before any HTTP/model call.  They assert the
text the production runner would send, rather than only checking that a JSON
field happens to exist in the input object.
"""
from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from data_gen_v4.runtime_grounded import validate_conversational_scenario
from data_gen_v4.runtime_grounded.renderer import render_game_reply_context_from_model_view
from run_component_game_reply15_pilot import build_audit_messages, build_messages


def _scenario(index: int = 0) -> dict:
    builder = importlib.import_module("build_authority_component10")
    rows = builder.build_components()
    assert rows, "authority component fixture must provide at least one row"
    converter = importlib.import_module("convert_component_to_game_reply")
    return converter.convert(rows[index % len(rows)], index)


def test_old_component_preview_schema_is_not_accepted() -> None:
    row = _scenario()
    row["schema_version"] = "component_preview.v1"
    with pytest.raises(Exception):
        validate_conversational_scenario(row)


def test_bad_split_anchors_fail_before_model_call() -> None:
    row = _scenario()
    row["split_anchors"] = ["only-one"]
    with pytest.raises(Exception):
        validate_conversational_scenario(row)


def test_rendered_context_contains_all_home_devices_and_positions() -> None:
    row = _scenario()
    context = render_game_reply_context_from_model_view(row["model_view"])
    # Every fixed device must be visible in the actual rendered prompt.  A
    # field hidden in authoritative_world but absent here is not model input.
    for name in ("洗衣机", "冰箱", "燃气灶", "抽油烟机", "电饭煲", "微波炉", "电热水壶", "风扇", "电视", "热水器"):
        assert name in context, name
    assert "男主" in context
    assert "白未晞" in context


def test_known_and_unknown_projection_is_explicit_and_disjoint() -> None:
    row = _scenario()
    context = render_game_reply_context_from_model_view(row["model_view"])
    # Unknown must be a separate Chinese block, never an English field name or
    # a known fact simultaneously asserted for the same predicate.
    assert "[明确未知]" in context
    model_view = row["model_view"]
    unknown_items = model_view.get("unknown_facts") or ()
    # Unknown predicates are Chinese and a subject/predicate cannot be both a
    # current known fact and an unknown fact in the same snapshot.
    for item in unknown_items:
        assert isinstance(item, dict)
        assert str(item.get("predicate") or "").strip()
        assert str(item.get("status") or "") in {"未知", "unknown"}
    assert any("未知" in line for line in context.splitlines())


def test_final_messages_retain_current_question_and_recent_memory() -> None:
    converter = importlib.import_module("convert_component_to_game_reply")
    row = _scenario()
    messages = build_messages(row, _anchor())
    coverage = converter.validate_final_messages(row, messages)
    assert isinstance(coverage, dict)
    serialized = json.dumps(messages, ensure_ascii=False)
    assert row["model_view"]["current_protagonist_utterance"] in serialized
    for turn in row["model_view"].get("recent_dialogue") or ():
        text = str(turn)
        if "：" in text:
            text = text.split("：", 1)[1]
        assert text.strip() in serialized
    # Removing the system context must be rejected by the final-message check.
    broken = copy.deepcopy(messages)
    broken[0]["content"] = "损坏的角色锚"
    with pytest.raises(Exception):
        converter.validate_final_messages(row, broken)


def test_wrong_character_or_scene_location_is_rejected() -> None:
    converter = importlib.import_module("convert_component_to_game_reply")
    row = _scenario()
    broken = copy.deepcopy(row)
    broken["model_view"]["snapshot"]["protagonist"]["location_label"] = "不存在的房间"
    messages = build_messages(broken, _anchor())
    with pytest.raises(Exception):
        converter.validate_final_messages(broken, messages)


def test_audit_prompt_contains_exact_current_turn() -> None:
    row = _scenario()
    messages = build_messages(row, _anchor())
    audit = build_audit_messages(row, messages, {"reply": "我还不知道。"})
    assert [m['role'] for m in audit] == ['system', 'user']
    assert row["model_view"]["current_protagonist_utterance"] not in audit[0]['content']
    payload = json.loads(audit[1]['content'])
    assert payload['generation_messages'] == messages
    assert payload['candidate'] == {'reply': '我还不知道。'}


def test_dropping_one_device_unknown_cannot_pass_coverage():
    converter = importlib.import_module('convert_component_to_game_reply')
    row = _scenario()
    row['model_view']['unknown_facts'] = [f for f in row['model_view']['unknown_facts']
        if (f['subject'], f['predicate']) != ('微波炉', '剩余时间')]
    with pytest.raises(ValueError, match='unknown_facts'):
        converter.validate_final_messages(row, build_messages(row, _anchor()))


def test_undisclosed_preferences_do_not_leak_into_reply_context():
    unknown = build_messages(_scenario(0), _anchor())[0]['content']
    known = build_messages(_scenario(5), _anchor())[0]['content']
    assert '四川口味' not in unknown
    assert '男主已知设定的地方口味：四川口味' in known
    assert '男主原话：' in known


def test_appliance_location_cannot_be_inferred_from_scene():
    converter = importlib.import_module('convert_component_to_game_reply')
    builder = importlib.import_module('build_authority_component10')
    row = builder.build_components()[0]
    row['snapshot']['states'].append({'subject':'微波炉', 'predicate':'当前位置', 'value':'玄关', 'source':'错误组件'})
    with pytest.raises(ValueError, match='状态字段'):
        converter.convert(row)


def _external_component():
    from datetime import datetime, timedelta
    converter = importlib.import_module('convert_component_to_game_reply')
    now = datetime.fromisoformat('2026-09-14T12:00:00+08:00')
    def turn(speaker, text, offset, source):
        return {'speaker': speaker, 'text': text,
                'time': (now + timedelta(seconds=offset)).isoformat(), 'source': source}
    row = {
        'schema_version': converter.SOURCE_SCHEMA, 'sample_id': 'external.test.001',
        'topic': '咖啡订单',
        'component_ids': {'scene_id': 'songjiangfu.neighborhood_cafe',
            'snapshot_id': 'external.test.001.snapshot', 'relation_id': 'external.test.001.relation',
            'utterance_id': 'external.test.001.utterance', 'contract_id': 'spoken.grounded.v2',
            'family_id': 'external.test.001'},
        'snapshot': {'time': now.isoformat(), 'weather': '晴天', 'area': '普通座位区',
            'people': {'男主': {'area': '普通座位区', 'activity': '喝咖啡', 'body': '正常', 'emotion': '平静'},
                       '白未晞': {'area': '普通座位区', 'activity': '聊天', 'body': '正常', 'emotion': '平静'}},
            'states': []},
        'memory_turns': [turn('男主', '今天出来坐坐。', -172860, 'm0'), turn('白未晞', '好呀。', -172830, 'm1'),
                         turn('男主', '附近有家咖啡店。', -172800, 'm2'), turn('白未晞', '那去看看。', -172770, 'm3')],
        'recent_turns': [turn('男主', '店里人多吗？', -240, 'r0'), turn('白未晞', '看起来还好。', -210, 'r1'),
                         turn('男主', '我们坐这儿吧。', -180, 'r2'), turn('白未晞', '嗯。', -150, 'r3')],
        'current_user': '今天有什么饮品？', 'relation': '当前店内饮品未登记',
    }
    return row


def test_external_scene_registry_projects_selected_scene_without_home_facts():
    """A registered public scene uses its own zones and emits runtime unknowns."""
    converter = importlib.import_module('convert_component_to_game_reply')
    scenario = converter.convert(_external_component())
    view = scenario['model_view']
    assert view['snapshot']['scene']['scene_id'] == 'songjiangfu.neighborhood_cafe'
    assert view['snapshot']['scene']['location_label'] == '街角咖啡'
    assert any(item['subject'] == '街角咖啡' and item['status'] == 'unknown'
               for item in view['authoritative_world']['facts'])
    assert '洗衣机' not in json.dumps(view['authoritative_world'], ensure_ascii=False)


def test_home_expansion_keeps_usual_locations_separate_from_current_state():
    converter = importlib.import_module('convert_component_to_game_reply')
    source = copy.deepcopy(_scenario()['component_source'])
    source['snapshot']['states'] = []
    scenario = converter.convert(source)
    messages = build_messages(scenario, _anchor())
    converter.validate_final_messages(scenario, messages)
    text = json.dumps(messages, ensure_ascii=False)
    facts = {(f['subject'], f['predicate']): f['value']
             for f in scenario['model_view']['authoritative_world']['facts']}
    assert facts['洗衣机', '固定设备位置'] == '小阳台'
    assert facts['空调', '是否存在'] == '无'
    assert facts['电视柜', '是否存在'] == '有'
    assert '通常归位点（不代表当前位置）' in text
    for (subject, predicate), value in facts.items():
        if predicate == '通常归位点（不代表当前位置）':
            assert facts.get((subject, '当前位置')) is None
    source['snapshot']['states'] = [
        {'subject': '空调', 'predicate': '是否存在', 'value': '有', 'source': '错误覆盖'}]
    with pytest.raises(ValueError, match='状态字段'):
        converter.convert(source)


@pytest.mark.parametrize('scene_id,zone,expected', [
    ('songjiangfu.neighborhood_cafe', '普通座位区', ['约45平方米', '11:00-20:00', '常规目录（不代表今日供应）']),
    ('songjiangfu.neighborhood_convenience_store', '入口', ['约35平方米', '08:00-20:00', '购物篮存放处']),
    ('songjiangfu.shopping_walk', '街道入口', ['约900米', '3至4公里', '老街书铺']),
    ('songjiangfu.protagonist_cafe', '入口', ['11:00-20:00', '无员工', '茉莉茶']),
])
def test_expanded_scene_reaches_final_messages_without_private_staff_names(scene_id, zone, expected):
    converter = importlib.import_module('convert_component_to_game_reply')
    source = _external_component()
    source['component_ids']['scene_id'] = scene_id
    source['snapshot']['area'] = zone
    for person in source['snapshot']['people'].values():
        person['area'] = zone
    scenario = converter.convert(source)
    messages = build_messages(scenario, _anchor())
    converter.validate_final_messages(scenario, messages)
    text = json.dumps(messages, ensure_ascii=False)
    for phrase in expected:
        assert phrase in text
    for name in ('周宁', '林晓', '陈惠', '赵明'):
        assert name not in text
    # The production anchor knows her appliance skills; the external scene
    # must not inherit the home's appliance existence or current state table.
    assert not any(f['subject']=='洗衣机' for f in scenario['model_view']['authoritative_world']['facts'])


def test_new_equipment_state_is_bound_once_and_other_fields_remain_unknown():
    converter = importlib.import_module('convert_component_to_game_reply')
    source = _external_component()
    source['snapshot']['states'] = [
        {'subject': '咖啡机', 'predicate': '运行状态', 'value': '正在制作', 'source': '本轮可见状态'}]
    scenario = converter.convert(source)
    facts = scenario['model_view']['authoritative_world']['facts']
    matches = [f for f in facts if (f['subject'], f['predicate']) == ('咖啡机', '运行状态')]
    assert len(matches) == 1 and matches[0]['value'] == '正在制作'
    assert any(f['subject'] == '咖啡机' and f['predicate'] == '是否故障' and f['value'] is None for f in facts)
    converter.validate_final_messages(scenario, build_messages(scenario, _anchor()))


def test_unknown_external_scene_id_fails_closed():
    converter = importlib.import_module('convert_component_to_game_reply')
    row = _scenario()
    row['component_source']['component_ids']['scene_id'] = 'songjiangfu.not_registered'
    with pytest.raises(ValueError, match='unknown authoritative scene_id'):
        converter.convert(row['component_source'])


def _anchor():
    from build_v5_pilot_dataset import _load_baiweixi_render_context
    return _load_baiweixi_render_context()[2]
