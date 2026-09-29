import copy
import json
import sys
from pathlib import Path

import jsonschema
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from build_authority_component10 import build_components
from build_v5_pilot_dataset import _load_baiweixi_render_context
from dialogue_authoring import character_evidence, named_chain_schema, decode_named_chain
from audit_generated_history import validate_history_review


def review(row):
    return {'checks': {f'{kind}:{i}': {'claim':'男主与白未晞讨论假设',
                'speaker_consistent':True, 'kind':'subjective', 'evidence_ids':[],
                'spoken_only':True, 'supported':True}
            for kind in ('memory_turns','recent_turns') for i,_ in enumerate(row[kind])},
            'continuity':True, 'events_consistent':True, 'pass':True, 'reasons':[]}


def test_production_facts_keep_ownership_without_role_instructions():
    anchor=_load_baiweixi_render_context()[2]
    facts=character_evidence(anchor)
    assert len(facts)==sum(line.startswith('- ') for line in anchor.splitlines())
    assert all(f['subject']=='白未晞' for f in facts)
    assert '你是白未晞' not in json.dumps(facts,ensure_ascii=False)
    assert any(f['predicate']=='childhood' and '深山' in f['value'] for f in facts)
    assert sum(f['predicate']=='日常交流倾向' for f in facts)==12


def test_named_slots_reject_wrong_or_missing_speaker_field():
    row=build_components()[0]
    value={key:'想象一下吧。'+str(i) for i,key in enumerate(named_chain_schema(row)['properties'])}
    result=decode_named_chain(value,row)
    assert result['memory'][0]==value['memory_turns_1_男主']
    assert result['recent'][3]==value['recent_turns_4_白未晞']
    value['memory_turns_1_白未晞']=value.pop('memory_turns_1_男主')
    with pytest.raises(jsonschema.ValidationError): decode_named_chain(value,row)


def test_role_mismatch_cannot_be_overridden_by_overall_approval():
    row=build_components()[0]; verdict=review(row)
    verdict['checks']['memory_turns:0'].update(
        claim='男主说自己醒来叫白未晞',speaker_consistent=False)
    with pytest.raises(ValueError,match='ownership mismatch'):
        validate_history_review(row,verdict,[])


def test_canon_past_allowed_but_not_an_unregistered_event():
    row=build_components()[0]; verdict=review(row)
    catalog=[{'id':'C1','kind':'character','subject':'白未晞','text':'幼年在深山流浪'}]
    verdict['checks']['memory_turns:1'].update(kind='canonical',evidence_ids=['C1'])
    validate_history_review(row,verdict,catalog)
    verdict['checks']['memory_turns:1']['kind']='past'
    with pytest.raises(ValueError,match='frozen event'):
        validate_history_review(row,verdict,catalog)


def test_prior_speech_proves_only_that_speech_not_an_event_or_future_claim():
    row=build_components()[0]; verdict=review(row)
    source={'id':'D1','kind':'dialogue','check_key':'memory_turns:0',**row['memory_turns'][0]}
    verdict['checks']['recent_turns:0'].update(kind='conversation',evidence_ids=['D1'])
    validate_history_review(row,verdict,[source])
    verdict['checks']['recent_turns:0']['kind']='past'
    with pytest.raises(ValueError,match='self-prove'):
        validate_history_review(row,verdict,[source])
    verdict['checks']['recent_turns:0']['kind']='conversation'
    source['time']=row['snapshot']['time']
    with pytest.raises(ValueError,match='must precede'):
        validate_history_review(row,verdict,[source])


@pytest.mark.parametrize('flag',['continuity','events_consistent'])
def test_overall_approval_cannot_override_failed_independent_check(flag):
    row=build_components()[0]; verdict=review(row); verdict[flag]=False
    with pytest.raises(ValueError,match='consistency failed'):
        validate_history_review(row,verdict,[])


def test_surface_rejection_stops_before_full_fact_review(tmp_path,monkeypatch):
    import audit_generated_history as audit
    row=build_components()[0]
    row['memory_turns'][0]['text']='我点点头，觉得这样也不错。'
    calls=[]
    def response(*args):
        calls.append(args)
        return {'message':{'content':json.dumps({'issues':[{
            'turn_id':'memory_turns:0','kind':'narration',
            'quote':row['memory_turns'][0]['text'],'reason':'动作与内心叙述'}]},ensure_ascii=False)},'done_reason':'stop'}
    monkeypatch.setattr(audit,'call_auditor',response)
    with pytest.raises(ValueError,match='surface rejection'):
        audit.audit_history(row,'local','test',tmp_path/'history.json')
    assert len(calls)==1
    assert (tmp_path/'history.surface.json').exists()
    assert not (tmp_path/'history.json').exists()


def test_surface_reviewer_cannot_rewrite_quoted_evidence(tmp_path,monkeypatch):
    import audit_generated_history as audit
    row=build_components()[0]
    monkeypatch.setattr(audit,'call_auditor',lambda *args:{'message':{'content':json.dumps({'issues':[{
        'turn_id':'memory_turns:0','kind':'narration','quote':'审核模型自己编的句子','reason':'旁白'}]},ensure_ascii=False)}})
    with pytest.raises(ValueError,match='different utterance'):
        audit.audit_surface(row,'local','test',tmp_path/'surface.json')
