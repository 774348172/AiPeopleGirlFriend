import copy
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def no_real_model_loading(monkeypatch):
    from types import SimpleNamespace
    import httpx
    monkeypatch.setattr(httpx, 'post', lambda *a, **k: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'test_model_load': True}))

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'tools'))
import run_curated_candidate_batch as runner
from prepare_curated15 import build
from build_v5_pilot_dataset import _load_baiweixi_render_context


def test_all_inputs_have_full_authority_and_linked_dialogue():
    rows, approvals = build()
    prepared = runner.prepare(rows, approvals, _load_baiweixi_render_context()[2])
    assert len(prepared) == 15
    assert len({r['current_user'] for r in rows}) == 15
    for record in prepared:
        runner.verify_frozen(record)
        text = json.dumps(record['generation_messages'], ensure_ascii=False)
        assert '[当前权威事实]' in text and '[明确未知]' in text
        for turn in record['scenario']['component_source']['memory_turns']:
            assert turn['text'] in text
            assert turn['source'] in text
        assert record['generation_messages'][-1]['content'] == record['scenario']['component_source']['current_user']


@pytest.mark.parametrize('mutation', ['approval', 'question', 'history', 'state'])
def test_missing_or_stale_input_review_blocks_model_calls(mutation):
    rows, approvals = build(); rows=rows[:1]
    if mutation == 'approval': approvals={}
    elif mutation == 'question': rows[0]['current_user'] += '变更'
    elif mutation == 'history': rows[0]['memory_turns'][0]['text'] += '变更'
    else: rows[0]['snapshot']['weather'] = '小雨'
    with pytest.raises(ValueError, match='approval'):
        runner.prepare(rows, approvals, _load_baiweixi_render_context()[2])


def test_cannot_rewrite_messages_after_approval():
    rows, approvals=build()
    record=runner.prepare(rows[:1], approvals, _load_baiweixi_render_context()[2])[0]
    record['generation_messages'][-1]['content']='改成不相关的问题'
    with pytest.raises(ValueError,match='frozen input changed'): runner.verify_frozen(record)


def test_local_calls_only_reply_then_audit_and_preserve_rejection(tmp_path, monkeypatch):
    rows, approvals=build(); rows=rows[:2]; calls=[]
    def invoke(out, name, model, messages, schema, tokens, temp, validator=None, retries=2):
        calls.append((name, model))
        result={'reply':'测试中的模拟回复 '+name} if name.endswith('.reply') else {'pass':False,'reasons':['测试拒绝']}
        if validator: validator(result)
        return result
    monkeypatch.setattr(runner,'call_logged',invoke)
    monkeypatch.setattr(runner,'compact_audit_material',lambda *args: ({},{},[],[],{}))
    monkeypatch.setattr(runner,'evaluate',lambda *args: {'approved':False,'decision':'content_rejected','reasons':['测试拒绝']})
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: None)
    runner.run(tmp_path, rows, approvals)
    assert [name.rsplit('.',1)[1] for name,_ in calls] == ['reply','reply','audit','audit']
    results=runner.read(tmp_path/'results.json')
    assert all(r['audit']=={'pass':False,'reasons':['测试拒绝']} and r['quarantined'] for r in results)
    assert runner.read(tmp_path/'report.json')['released_training_rows']==0
    runner.run(tmp_path, rows, approvals)
    assert len(calls)==4  # Resume does not regenerate until the auditor likes it.
    rows[0]['current_user']+='改动'; approvals[rows[0]['sample_id']]['component_sha256']=runner.digest(rows[0])
    with pytest.raises(ValueError,match='resume configuration'): runner.run(tmp_path,rows,approvals)


def test_empty_reply_never_reaches_audit_or_training(tmp_path, monkeypatch):
    rows,approvals=build(); calls=[]
    def invoke(*args, **kwargs):
        calls.append(args[1]); args[7]({'reply':''})
    monkeypatch.setattr(runner,'call_logged',invoke)
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: None)
    runner.run(tmp_path,rows[:1],approvals)
    record=runner.read(tmp_path/'results.json')[0]
    assert len(calls)==1 and calls[0].endswith('.reply')
    assert 'target' not in record and record['status']=='generation_failed'


def test_format_repair_keeps_inputs_and_never_resamples_content_rejection(tmp_path, monkeypatch):
    import retry_curated_format_failures as repair
    rows, approvals=build(); rows=rows[:2]
    def initial(out,name,model,messages,schema,tokens,temp,validator=None,**kwargs):
        if name.endswith('02.reply'): raise ValueError('empty reply')
        value={'reply':'保留首轮有效回复'} if name.endswith('.reply') else {'pass':False}
        if validator: validator(value)
        return value
    monkeypatch.setattr(runner,'call_logged',initial)
    monkeypatch.setattr(runner,'compact_audit_material',lambda *a: ({},{},[],[],{}))
    monkeypatch.setattr(runner,'evaluate',lambda *a: {'approved':False,'decision':'content_rejected','reasons':['内容拒绝']})
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k: None)
    runner.run(tmp_path,rows,approvals)
    frozen=runner.read(tmp_path/'inputs/frozen_inputs.json'); calls=[]
    def invoke(out,name,model,messages,schema,tokens,temp,validator=None,**kwargs):
        calls.append(name)
        if 'reply_format_retry' in name:
            assert messages == frozen[1]['generation_messages']
            value={'reply':'这是补试的模拟回复'}
        else: value={'pass':False}
        if validator: validator(value)
        return value
    monkeypatch.setattr(repair,'call_logged',invoke)
    monkeypatch.setattr(repair,'compact_audit_material',lambda *a: ({},{},[],[],{}))
    monkeypatch.setattr(repair,'evaluate',lambda *a: {'approved':False,'reasons':['仍待复核']})
    repair.retry(tmp_path)
    repair.retry(tmp_path)
    assert len(calls)==2 and all('02.' in name for name in calls)
    assert runner.read(tmp_path/'first_pass_report.json')['valid_replies']==1
    assert runner.read(tmp_path/'report.json')['valid_replies']==2
    assert runner.read(tmp_path/'results.json')[0]['target']=={'reply':'保留首轮有效回复'}
