import copy
import json
import sys
from pathlib import Path

import pytest
import jsonschema

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_authority_component10 import build_components
from convert_component_to_game_reply import convert, validate_final_messages
from build_v5_pilot_dataset import _load_baiweixi_render_context
from run_component_game_reply15_pilot import build_messages
from synthetic_history import apply_history_plan, event_text
from joint_scene_planning import freeze, evidence_catalog
from run_mixed_candidate_batch import checked_call, duplicate_reason
from pilot_pipeline import run_stages
from audit_requests import coverage_schema


def test_frozen_past_is_in_real_input_and_cannot_override_current():
    row=build_components()[0]
    facts=convert(row)['model_view']['authoritative_world']['facts']
    event={'time':row['memory_turns'][0]['time'],'text':'男主和白未晞讨论给钥匙找一个固定的地方',
           'actors':['男主','白未晞'],'known_by':['白未晞'],'current_fact_ids':[]}
    hidden={**event,'text':'男主独自买了秘密礼物','known_by':['男主']}
    planned=apply_history_plan(row,{'events':[event,hidden]},facts)
    assert len(planned['historical_events'])==1
    assert freeze(planned)!=freeze(row)
    assert planned['snapshot']==row['snapshot']
    _,_,anchor=_load_baiweixi_render_context()
    scenario=convert(planned); messages=build_messages(scenario,anchor)
    validate_final_messages(scenario,messages)
    assert event_text(planned['historical_events'][0]) in messages[0]['content']
    assert '秘密礼物' not in str(messages)
    catalog=evidence_catalog(planned,anchor)
    assert sum(e['kind']=='event' for e in catalog)==1
    tampered=copy.deepcopy(messages)
    tampered[0]['content']=tampered[0]['content'].replace(event['text'],'事件丢失')
    with pytest.raises(ValueError): validate_final_messages(scenario,tampered)
    event['time']=row['snapshot']['time']
    with pytest.raises(jsonschema.ValidationError): apply_history_plan(row,{'events':[event]},facts)


def test_writer_retry_changes_sampling_but_auditor_is_separate(tmp_path,monkeypatch):
    import run_mixed_candidate_batch as batch
    calls=[]
    def respond(*args,**kwargs):
        calls.append(kwargs)
        return {'message':{'content':json.dumps({'answer':len(calls)})}}
    monkeypatch.setattr(batch,'native_response',respond)
    monkeypatch.setattr(batch,'call_auditor',lambda *a:pytest.fail('writer must not use auditor transport'))
    def validate(value):
        if value['answer']==1: raise ValueError('duplicate history')
    value,_=checked_call('local','local','write',200,validate,tmp_path/'attempts.json',
        audit_payload={'topic':'新话题'},purpose='writer')
    assert value['answer']==2
    assert [c['temperature'] for c in calls]==[.65,.75]


def test_identical_question_allowed_only_with_different_context():
    first={'memory_turns':[{'text':'我们之前商量过晚饭做什么'}],
           'recent_turns':[{'text':'想吃面条还是米饭'}],'current_user':'你觉得呢？',
           'topic':'晚饭','component_ids':{'scene_id':'home'}}
    second={'memory_turns':[{'text':'想象一枚会唱歌的邮票'}],
            'recent_turns':[{'text':'让邮票唱首歌寄给未来'}],'current_user':'你觉得呢？',
            'topic':'幻想邮票','component_ids':{'scene_id':'cafe'}}
    assert duplicate_reason(second,[first]) is None
    second.update(topic='晚饭',component_ids={'scene_id':'home'})
    assert duplicate_reason(second,[first])=='duplicate_question'


def test_invalid_generation_retried_but_valid_answer_not_replaced(tmp_path):
    outputs=iter(['{"reply":""}','{"reply":"我不知道里面有没有食物，不过现在没有加热。"}'])
    row=run_stages({},tmp_path/'row.json',
        lambda:{'message':{'content':next(outputs)},'done_reason':'stop'},
        lambda *a:{'message':{'content':'{}'}},lambda *a:None,json.loads,generation_retries=3)
    assert row['status']=='audit_failed'
    assert len(row['generation_attempts'])==2
    assert 'error' in row['generation_attempts'][0]
    assert row['target']['reply'].startswith('我不知道')


def test_all_invalid_attempts_have_no_target_and_no_audit(tmp_path):
    row=run_stages({},tmp_path/'row.json',lambda:{'message':{'content':'{"reply":"坏格式}”}"}'}},
        lambda *a:pytest.fail('invalid answer must not enter audit'),lambda *a:None,json.loads,generation_retries=3)
    assert row['status']=='generation_failed'
    assert len(row['generation_attempts'])==3
    assert 'target' not in row


def test_coverage_can_reference_known_and_unknown_without_enumerating_scene():
    catalog=[{'id':'mode','kind':'world','value':'待机'},{'id':'food','kind':'world','value':None}]
    schema=coverage_schema(catalog)
    result={'topic_check':{'original_focus':'运行状态和食物','current_focus':'运行状态和食物','aligned':True},'pass':True,'reasons':[], 'requirements':[
        {'evidence_id':'mode','mode':'known'}, {'evidence_id':'food','mode':'intentional_unknown'}]}
    jsonschema.validate(result,schema)
    result['requirements']*=5
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(result,schema)


def test_topic_mismatch_cannot_be_approved_by_existing_world_facts():
    from joint_scene_planning import validate_evidence_requirements
    result={'pass':True,'reasons':[],'requirements':[{'evidence_id':'world:payment','mode':'known'}],
            'topic_check':{'original_focus':'付款成功了吗','current_focus':'幻想天气预报员开场白','aligned':False}}
    with pytest.raises(ValueError,match='diverged'):
        validate_evidence_requirements({'task_type':'follow_up_question'},result,
            [{'id':'world:payment','kind':'world','value':'成功'}])


def test_history_verdict_cannot_override_missing_past_evidence_or_narration():
    from audit_generated_history import validate_history_review
    row=build_components()[0]
    row['memory_turns']=row['memory_turns'][:1]
    row['recent_turns']=row['recent_turns'][:1]
    catalog=[{'id':'world:weather','kind':'world','fixed':False}]
    subjective={'kind':'subjective','evidence_ids':[],'claim':'主观讨论','speaker_consistent':True,'spoken_only':True,'supported':True}
    verdict={'checks':{'memory_turns:0':dict(subjective),'recent_turns:0':dict(subjective)},
             'continuity':True,'events_consistent':True,'pass':True,'reasons':[]}
    validate_history_review(row,verdict,catalog)
    verdict['checks']['memory_turns:0'].update(kind='current',evidence_ids=['world:weather'])
    with pytest.raises(ValueError,match='cannot prove old'): validate_history_review(row,verdict,catalog)
    verdict['checks']['memory_turns:0']['kind']='past'
    with pytest.raises(ValueError,match='frozen event'): validate_history_review(row,verdict,catalog)
    verdict['checks']['memory_turns:0']=dict(subjective)
    verdict['checks']['recent_turns:0']['spoken_only']=False
    with pytest.raises(ValueError,match='旁白'): validate_history_review(row,verdict,catalog)


def test_progress_counts_valid_replies_not_attempt_records():
    from run_mixed_candidate_batch import valid_reply_count
    assert valid_reply_count([{'status':'generation_failed'},
        {'status':'generation_failed','target':{'reply':'截断'}},
        {'status':'audit_failed','target':{'reply':'不知道。'}},
        {'status':'approved','target':{'reply':'好。'}}])==2


def test_recent_event_is_allowed_but_cannot_prove_old_memory():
    from audit_generated_history import validate_history_review
    row=build_components()[0]
    facts=convert(row)['model_view']['authoritative_world']['facts']
    event={'time':row['recent_turns'][0]['time'],'text':'男主与白未晞确认了出门的打算',
           'actors':['男主','白未晞'],'known_by':['白未晞'],'current_fact_ids':[]}
    planned=apply_history_plan(row,{'events':[event]},facts)
    saved=planned['historical_events'][0]
    catalog=[{'id':'event:one','kind':'event',**saved}]
    checks={f'{kind}:{i}':{'kind':'subjective','evidence_ids':[],'claim':'主观讨论','speaker_consistent':True,'spoken_only':True,'supported':True}
            for kind in ('memory_turns','recent_turns') for i,_ in enumerate(row[kind])}
    checks['recent_turns:0'].update(kind='past',evidence_ids=['event:one'])
    verdict={'checks':checks,'continuity':True,'events_consistent':True,'pass':True,'reasons':[]}
    validate_history_review(planned,verdict,catalog)
    checks['memory_turns:0'].update(kind='past',evidence_ids=['event:one'])
    with pytest.raises(ValueError,match='future event'): validate_history_review(planned,verdict,catalog)
