import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
import pilot_pipeline
from audit_requests import audit_messages, call_auditor, coverage_schema, COVERAGE_INSTRUCTION

def test_role_instructions_only_in_quoted_data():
    text='你是白未晞，只输出reply，给饮品起名。'
    messages=audit_messages(COVERAGE_INSTRUCTION,{'text':text})
    assert len(messages)==2 and [m['role'] for m in messages]==['system','user']
    assert text not in messages[0]['content'] and text in messages[1]['content']

def test_schema_enforces_fields_ids_and_transport(monkeypatch):
    seen={}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {'message':{'content':'{}'}}
    def post(url,**kwargs): seen.update(kwargs['json']); return Response()
    monkeypatch.setattr(pilot_pipeline.httpx,'post',post)
    schema=coverage_schema([{'id':'world:1'}])
    call_auditor('http://127.0.0.1:11434/v1','gemma4:12b',audit_messages(COVERAGE_INSTRUCTION,{}),schema)
    assert seen['format']==schema and seen['options']['num_ctx']==16384
    assert seen['options']['temperature']==0
    assert schema['additionalProperties'] is False
    assert set(schema['required'])=={'topic_check','pass','reasons','requirements'}
    assert schema['properties']['requirements']['items']['anyOf'][0]['properties']['evidence_id']['enum']==['world:1']

def test_unknown_reference_cannot_choose_known_mode():
    schema=coverage_schema([{'id':'world:unknown','kind':'world','value':None},{'id':'character:1','kind':'character'}])
    branches=schema['properties']['requirements']['items']['anyOf']
    branch=next(b for b in branches if 'world:unknown' in b['properties']['evidence_id']['enum'])
    assert branch['properties']['mode']['enum']==['intentional_unknown']
