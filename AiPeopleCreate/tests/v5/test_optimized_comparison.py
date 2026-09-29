import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import run_optimized_comparison as opt
from build_authority_component10 import build_components
from build_v5_pilot_dataset import _load_baiweixi_render_context
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages


def fixture():
    seed=build_components()[0]
    seed['task_type']='idle_chat'
    chain={'memory':['想象一天没有时钟会怎样？','也许我会慢一点。','不必急着赶路。','那就随心一点。'],
           'recent':['刚才的假设真有趣。','你想怎么过？','随便走走吧。','我也想慢一点。'],'current_user':'那你想先做什么？'}
    return seed,{'world':{'intent':'慢一点的假想日子','states':[]},'events':[],'chain':chain}


def test_authoring_contains_authority_without_placeholder_history():
    seed,_=fixture(); _,_,anchor=_load_baiweixi_render_context()
    payload=opt.author_payload(seed,anchor)
    assert payload['完整权威事实和未知']==convert(seed)['model_view']['authoritative_world']['facts']
    assert all(set(t)=={'time','speaker'} for values in payload['对话时刻和说话者'].values() for t in values)
    assert any(f['value'] is None for f in payload['完整权威事实和未知'])
    assert '角色设定' in payload


def test_bound_event_keys_compile_and_hidden_event_never_enters_reply_input():
    seed,value=fixture()
    fact=convert(seed)['model_view']['authoritative_world']['facts'][0]
    event={'time':seed['memory_turns'][0]['time'],'text':'两人聊过慢一点的假想日子',
           'actors':['男主','白未晞'],'known_by':['白未晞'],
           'current_fact_keys':[{'subject':fact['subject'],'predicate':fact['predicate']}]}
    value['events']=[event,{**event,'text':'秘密礼物','known_by':['男主']}]
    jsonschema.validate(value,opt.author_schema(seed))
    row=opt.assemble(seed,value)
    assert len(row['historical_events'])==1
    assert row['historical_events'][0]['current_fact_ids']==[fact['fact_id']]
    _,_,anchor=_load_baiweixi_render_context()
    s=convert(row); messages=build_messages(s,anchor); validate_final_messages(s,messages)
    assert '秘密礼物' not in str(messages)
    assert row['recent_turns'][1]['speaker']=='白未晞'
    value['events'][0]['current_fact_keys'][0]['predicate']='错误字段'
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(value,opt.author_schema(seed))


def test_complete_merged_audit_sees_old_history_and_rejects_narration_even_with_approve():
    seed,value=fixture(); row=opt.assemble(seed,value)
    _,_,anchor=_load_baiweixi_render_context(); scenario=convert(row)
    payload,schema,catalog,independent=opt.audit_material(scenario,{'reply':'慢慢走走吧。'},seed['topic'],anchor)
    assert payload['旧记忆']==row['memory_turns']
    assert payload['最近对话']==row['recent_turns']
    assert any(e['kind']=='character' for e in catalog)
    assert all(e.get('check_key') for e in independent if e['kind']=='dialogue')
    checks={f'{kind}:{i}':{'kind':'subjective','evidence_ids':[],'claim':'主观讨论','speaker_consistent':True,'spoken_only':True,'supported':True}
            for kind in ('memory_turns','recent_turns') for i in range(4)}
    verdict={'history':{'checks':checks,'continuity':True,'events_consistent':True,'pass':True,'reasons':[]},
             'coverage':{'topic_check':{'original_focus':'假想日子','current_focus':'假想日子','aligned':True},'requirements':[],'reasons':[],'pass':True},
             'style':{'verdicts':{k:True for k in ('voice','turn_relevance','naturalness','unsupported_expansion')},'decision':'approve','reasons':[]},
             'facts':{'pass':True,'reasons':[]}}
    jsonschema.validate(verdict,schema)
    assert opt.evaluate(scenario,{'reply':'慢慢走走吧。'},verdict,catalog,independent)['model_approved']
    checks['memory_turns:0']['spoken_only']=False
    result=opt.evaluate(scenario,{'reply':'慢慢走走吧。'},verdict,catalog,independent)
    assert not result['approved'] and result['decision']=='audit_invalid'
    assert any('历史' in r for r in result['reasons'])
    checks['memory_turns:0']['spoken_only']=True
    verdict['coverage']['topic_check']['aligned']=False
    assert not opt.evaluate(scenario,{'reply':'慢慢走走吧。'},verdict,catalog,independent)['approved']


def test_logged_call_rejects_truncated_target_and_counts_retries(tmp_path,monkeypatch):
    (tmp_path/'requests').mkdir()
    replies=iter([{'message':{'content':'{"reply":"好。"}'},'done_reason':'length'},
                  {'message':{'content':'{"reply":"好。"}'},'done_reason':'stop'}])
    monkeypatch.setattr(opt,'native_response',lambda *a,**k:next(replies))
    result=opt.call_logged(tmp_path,'new_test','qwen',[],opt.REPLY_SCHEMA,500,.55,opt.validate_conversational_target)
    assert result['reply']=='好。'
    log=opt.read(tmp_path/'requests/new_test.json')
    assert len(log['attempts'])==2 and log['attempts'][0]['error']=='output truncated'
    assert opt.metrics(tmp_path,'new_')['calls']==2
    # A completed checkpoint is reused without another model invocation.
    assert opt.call_logged(tmp_path,'new_test','qwen',[],opt.REPLY_SCHEMA,500,.55)==result


def test_heartbeat_marks_exception_and_preserves_phase(tmp_path):
    with pytest.raises(ValueError):
        with opt.Heartbeat(tmp_path) as hb:
            hb.update(phase='generate_all_qwen',active_sample='one')
            raise ValueError('test interruption')
    progress=opt.read(tmp_path/'progress.json')
    assert progress['status']=='interrupted'
    assert progress['phase']=='generate_all_qwen' and progress['pid']>0
