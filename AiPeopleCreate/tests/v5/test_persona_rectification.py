import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_authority_component10 import build_components
from build_v5_pilot_dataset import _load_baiweixi_render_context
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages
from data_gen_v4.runtime_grounded.persona_projection import (
    load_persona_profiles, MALE_CANON, FEMALE_CANON, CHARACTERS, author_persona_material,
)
from dialogue_integrity import validate_spoken_text


def test_adopted_traits_reach_production_anchor_but_private_male_values_do_not():
    male, female = load_persona_profiles()
    assert len(male['daily_dialogue']) == 10
    assert len(female['daily_dialogue']) == 12
    anchor = _load_baiweixi_render_context()[2]
    scenario = convert(build_components()[0]); messages = build_messages(scenario, anchor)
    assert all(value in messages[0]['content'] for value in female['daily_dialogue'].values())
    assert all(value not in messages[0]['content'] for value in male['daily_dialogue'].values())
    author = author_persona_material(scenario['model_view'])
    assert male['daily_dialogue']['dialogue_M01'] in str(author)
    assert validate_final_messages(scenario, messages)['coverage'] == 1
    messages[0]['content'] = messages[0]['content'].replace(female['daily_dialogue']['dialogue_F01'], '')
    with pytest.raises(ValueError, match='日常设定'):
        validate_final_messages(scenario, messages)


def test_new_male_fact_needs_original_male_speech_and_preserves_disclosure_time():
    row = build_components()[0]; male, _ = load_persona_profiles()
    key = 'daily_dialogue.dialogue_M01'; value = male['daily_dialogue']['dialogue_M01']
    row['known_profile'] = {key:row['recent_turns'][0]['source']}
    with pytest.raises(ValueError, match='原话'):
        convert(row)
    row['recent_turns'][0]['text'] = value
    scenario=convert(row)
    fact=next(f for f in scenario['model_view']['authoritative_world']['facts'] if f['subject']=='男主已知设定' and f['predicate']=='开店愿望')
    assert fact['known_since'] == row['recent_turns'][0]['time']
    assert value in build_messages(scenario,_load_baiweixi_render_context()[2])[0]['content']
    row['known_profile'][key] = row['recent_turns'][1]['source']
    row['recent_turns'][1]['text'] = value
    with pytest.raises(ValueError, match='原话'): convert(row)


@pytest.mark.parametrize('text', ['白未晞：', '男主：我喜欢这个。', '[', '"recent_turns": [', '{"role":"assistant"}'])
def test_known_empty_or_serialized_history_failures_are_blocked(text):
    row=build_components()[0]; row['recent_turns'][-1]['text']=text
    with pytest.raises(ValueError): convert(row)


def test_valid_short_subjective_reply_is_not_rejected():
    for text in ('嗯。','黑的，少见。','不知道昨天吃了什么。','要不想象一下？'):
        validate_spoken_text(text)


def test_hidden_emotion_cannot_be_read_and_explicit_visible_emotion_requires_source():
    row=build_components()[0]
    row['snapshot']['people']['男主']['emotion']='担心经营失败但没有说出口'
    text=build_messages(convert(row),_load_baiweixi_render_context()[2])[0]['content']
    assert '担心经营失败但没有说出口' not in text
    row['snapshot']['people']['男主']['emotion_visibility']='visible'
    with pytest.raises(ValueError, match='source'): convert(row)
    row['snapshot']['people']['男主'].update(emotion='有些焦虑',emotion_source='本轮男主明确告知')
    text = build_messages(convert(row),_load_baiweixi_render_context()[2])[0]['content']
    assert '有些焦虑' in text and '本轮男主明确告知' in text


def test_adoption_documents_and_canonical_references_are_consistent():
    male=yaml.safe_load((CHARACTERS/'主角/bible.yaml').read_text(encoding='utf-8'))
    female=yaml.safe_load((CHARACTERS/'白未晞/bible.yaml').read_text(encoding='utf-8'))
    assert '11:00-20:00' in male['daily_life']['cafe_hours'] and '周二' in male['daily_life']['cafe_hours']
    assert '作者定' not in male['social']['family'] + male['family_background']['hometown']
    assert '未知' in male['health']['allergies']
    for bible,path in ((male,MALE_CANON),(female,FEMALE_CANON)):
        facts=json.loads(path.read_text(encoding='utf-8'))['facts']
        assert all(k in facts for k in bible['daily_dialogue_defaults']['fact_keys'])
    assert 'intimate_moments' not in female and '创作素材' in female['creative_scene_materials']['status']
    timeline=yaml.safe_load((CHARACTERS/'白未晞/timeline.yaml').read_text(encoding='utf-8'))
    current=next(e for e in timeline['events'] if e['id']=='ev:bwx_current_anchor')
    assert '嘴硬说以后会走' not in current['summary']


def test_later_male_disclosure_cannot_certify_old_memory():
    from audit_generated_history import validate_history_review
    row=build_components()[0]
    checks={f'{kind}:{i}':{'kind':'subjective','evidence_ids':[], 'claim':'主观讨论','speaker_consistent':True,'spoken_only':True,'supported':True}
            for kind in ('memory_turns','recent_turns') for i in range(4)}
    checks['memory_turns:0'].update(kind='canonical',evidence_ids=['later'])
    catalog=[{'id':'later','kind':'world','fixed':True,'known_since':row['recent_turns'][0]['time']}]
    with pytest.raises(ValueError,match='later disclosure'):
        validate_history_review(row,{'checks':checks,'continuity':True,'events_consistent':True,'pass':True,'reasons':[]},catalog)


def test_fifth_variant_of_same_history_family_is_blocked():
    from run_mixed_candidate_batch import duplicate_reason
    row=build_components()[0]
    assert duplicate_reason(row,[copy.deepcopy(row) for _ in range(4)])=='history_family_exceeds_four_variants'


def test_rejected_precheck_never_invokes_reply_or_changes_frozen_events(tmp_path,monkeypatch):
    import run_persona_phased_validation as runner
    calls=[]
    def fake_call(out,name,model,messages,schema,tokens,temp,validator=None,**kwargs):
        calls.append(name)
        if name.endswith('.world'): return {'intent':'想象休息','states':[]}
        if name.endswith('.events'): return {'events':[]}
        if name.endswith('.chain'):
            return {**{key:'想象休息一天吧。' + str(i) for i,key in enumerate(schema['properties'])},
                    'current_user_男主':'那你呢？'}
        pytest.fail('A rejected history must not request reply or final audit')
    def reject(*args,**kwargs): raise ValueError('independent history rejection')
    monkeypatch.setattr(runner,'call_logged',fake_call)
    monkeypatch.setattr(runner,'audit_history',reject)
    seed=runner.make_seeds()[0]
    runner.run(tmp_path,[seed])
    result=json.loads((tmp_path/'results.json').read_text(encoding='utf-8'))[0]
    assert len(calls)==3 and result['failed_phase']=='pregate' and not result.get('target')
    assert result['frozen_before_dialogue']['historical_events']==[]
    assert result['scenario']['component_source']['historical_events']==[]
