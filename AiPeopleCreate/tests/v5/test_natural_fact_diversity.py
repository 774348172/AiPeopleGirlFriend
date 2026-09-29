import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from natural_fact_scenarios import build_scenarios, validate_unique


def test_capacity_overflow_refuses_repetition():
    with pytest.raises(ValueError, match='不能循环'):
        build_scenarios(161)


def test_id_or_punctuation_change_cannot_hide_duplicate():
    original = build_scenarios(1)[0]
    duplicate = copy.deepcopy(original)
    duplicate['scenario_id'] = 'different'
    duplicate['scenario_family'] = 'different'
    duplicate['model_view']['current_protagonist_utterance'] += '！'
    with pytest.raises(ValueError, match='重复场景'):
        validate_unique([original, duplicate])


def test_expansion_keeps_authority_and_explicit_unknowns():
    rows = build_scenarios(32)
    assert validate_unique(rows)['unique_questions'] == 29
    for row in rows:
        view = row['model_view']
        assert view['authoritative_world']['scene_canon']
        assert any('备用钥匙' in fact for fact in view['game_feedback'])
        if row['evidence_case'] == 'unknown':
            assert view['unknown_facts'] == view['authoritative_world']['unknown_facts']
            assert '没有直接证据' == view['unknown_facts'][-1]['reason'][-6:]
            assert '不知道' in row['style_contract']['style_intent']


def test_time_only_variant_reaches_rendered_context():
    import json
    from natural_fact_scenarios import apply_world_variant, _base_scenarios
    from data_gen_v4.runtime_grounded.renderer import render_game_reply_context_from_model_view
    config = json.loads((Path(__file__).resolve().parents[2] / 'tools/natural_fact_world_variants.json').read_text(encoding='utf-8'))
    source = _base_scenarios()[0]
    morning = apply_world_variant(source, config, config['变体'][0])
    later = apply_world_variant(source, config, config['变体'][1])
    assert morning['model_view']['recent_dialogue'] == later['model_view']['recent_dialogue']
    assert morning['world_variant']['weather'] == later['world_variant']['weather']
    assert '11:00' in render_game_reply_context_from_model_view(later['model_view'])
    assert validate_unique([morning, later])['unique_fingerprints'] == 2


def test_location_override_removes_old_fact_and_updates_contract():
    import json
    from natural_fact_scenarios import apply_world_variant, _base_scenarios
    config = json.loads((Path(__file__).resolve().parents[2] / 'tools/natural_fact_world_variants.json').read_text(encoding='utf-8'))
    row = apply_world_variant(_base_scenarios()[30], config, config['变体'][3])
    view = row['model_view']
    assert not any('备用钥匙在玄关挂钩' in f or '备用钥匙的当前位置是玄关挂钩' in f for f in view['game_feedback'])
    assert row['fact_contract']['required_phrases'] == ['客厅茶几']


def test_future_history_rejected():
    from natural_fact_scenarios import apply_world_variant, _base_scenarios
    config = {'基准': {'时间': '2026-09-12T09:00:00+08:00', '天气': '晴天', '历史': [
        {'时间': '2026-09-12T10:00:00+08:00', '说话人': '男主', '来源': '记录', '原话': '你好'}]}}
    with pytest.raises(ValueError, match='晚于'):
        apply_world_variant(_base_scenarios()[0], config, {})
