import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
from run_mixed_candidate_batch import duplicate_reason, validate_chain, sample_review


def test_expanded_schedule_is_balanced_deterministic_and_changes_between_batches():
    from collections import Counter
    from prepare_expanded_batch_seeds import build_seeds, SCENES
    rows=build_seeds('batch_a'); other=build_seeds('batch_b')
    assert rows==build_seeds('batch_a')
    assert len(rows)==len({r['sample_id'] for r in rows})==200
    assert not {r['sample_id'] for r in rows} & {r['sample_id'] for r in other}
    assert [r['snapshot'] for r in rows] != [r['snapshot'] for r in other]
    for scene in SCENES:
        selected=[r for r in rows if r['component_ids']['scene_id']==scene]
        assert len(selected)==40
        assert len({r['topic'] for r in selected})==40
        assert Counter(r['task_type'] for r in selected)=={'idle_chat':20,'emotion_attunement':5,'light_teasing':5,'follow_up_question':10}
    assert len({(r['snapshot']['time'],r['snapshot']['area'],r['snapshot']['weather']) for r in rows})>190
    assert all(not r['snapshot']['states'] and 'required_current_user' not in r for r in rows)


def test_cross_batch_history_uses_actual_prior_dialogue(tmp_path):
    import json
    from run_mixed_candidate_batch import load_history_inputs
    row={'memory_turns':[{'text':'之前想养一株假想中的树'}],'recent_turns':[{'text':'今天继续想它的名字'}],'current_user':'叫什么好呢？'}
    source=tmp_path/'prior.jsonl'
    source.write_text(json.dumps({'scenario':{'component_source':row},'target':{'reply':'小芽。'}},ensure_ascii=False),encoding='utf-8')
    previous,replies=load_history_inputs([source])
    assert duplicate_reason({**row,'current_user':'换个名字呢？'},previous)=='history_duplicate_or_near_duplicate'
    assert replies==['小芽']


def test_thousand_schedule_balances_scenes_without_copying_authored_dialogue():
    from collections import Counter
    from prepare_expanded_batch_seeds import build_seeds
    rows=build_seeds('thousand_test',1000)
    assert len(rows)==len({r['sample_id'] for r in rows})==1000
    assert set(Counter(r['component_ids']['scene_id'] for r in rows).values())=={200}
    assert Counter(r['task_type'] for r in rows)=={
        'idle_chat':500,'emotion_attunement':125,'light_teasing':125,'follow_up_question':250}
    assert len({r['component_ids']['family_id'] for r in rows})==1000
    assert all(r['current_user']=='待编剧生成自然承接的当前发言' for r in rows)
    assert len({r['topic'] for r in rows})==1000
    assert rows==build_seeds('thousand_test',1000)


def test_world_schema_binds_subject_to_its_own_runtime_predicates():
    import jsonschema
    from run_mixed_candidate_batch import world_schema
    schema=world_schema([('备用钥匙','当前位置'),('常用钥匙','持有人')])
    value={'intent':'找钥匙','states':[{'subject':'备用钥匙','predicate':'当前位置','value':'茶几','visibility':'visible'}]}
    jsonschema.validate(value,schema)
    for wrong in ('持有人','通常归位点（不代表当前位置）'):
        value['states'][0]['predicate']=wrong
        with pytest.raises(jsonschema.ValidationError): jsonschema.validate(value,schema)


def test_plan_normalization_only_collapses_identical_rows():
    from run_mixed_candidate_batch import normalize_world_plan
    state={'subject':'备用钥匙','predicate':'当前位置','value':'茶几','visibility':'visible'}
    source={'intent':'找钥匙','states':[state,state,{**state,'value':'门口'}]}
    normalized=normalize_world_plan(source)
    assert len(normalized['states'])==2
    assert len(source['states'])==3
    assert normalized['states'][0]['value']=='茶几'
    assert normalized['states'][1]['value']=='门口'  # conflict remains for apply_plan to reject


def test_history_risk_routes_to_review_without_falsifying_model_verdict():
    from run_mixed_candidate_batch import add_history_risk_flags
    result={'scenario':{'component_source':{'memory_turns':[{'text':'上次好像看见钥匙在这里。'}],'recent_turns':[]}},'fact_review':{'pass':True,'reasons':[]},'quarantined':False}
    add_history_risk_flags(result,enabled=True)
    assert result['quarantined'] is True
    assert result['fact_review']['pass'] is True
    assert '需人工判定' in result['history_risk_flags'][0]

def test_empty_or_wrong_history_rejected():
    with pytest.raises(ValueError):
        validate_chain({'memory':['a']*4,'recent':['']*4,'current_user':'test'})
    with pytest.raises(ValueError):
        validate_chain({'memory':['a']*3,'recent':['b']*4,'current_user':'test'})
    with pytest.raises(ValueError,match='copied'):
        validate_chain({'memory':['a']*4,'recent':['第一句','第二句','第三句','第四句'],'current_user':'第一句\n第二句\n第三句\n第四句'})

def test_relabelled_history_cannot_bypass_dedup():
    row={'memory_turns':[{'text':'以前聊过想象中的花园'}], 'recent_turns':[{'text':'今天继续聊喜欢什么颜色'}], 'current_user':'喜欢什么颜色？'}
    other={**row,'sample_id':'different-id','current_user':'最喜欢哪一种颜色呢？'}
    assert duplicate_reason(other,[row])=='history_duplicate_or_near_duplicate'

def test_stratified_sample_covers_every_scene_and_type():
    rows=[{'sample_id':f'{s}.{t}.{i}','scenario':{'component_ids':{'scene_id':s},'task_type':t}} for s in range(5) for t in range(4) for i in range(3)]
    chosen=sample_review(rows)
    assert len(chosen)==32
    assert len({(r['scenario']['component_ids']['scene_id'],r['scenario']['task_type']) for r in chosen})==20
    assert chosen==sample_review(rows)
