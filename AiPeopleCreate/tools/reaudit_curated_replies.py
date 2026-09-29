"""Matched auditor replacement: replay saved audit messages/schema, no new replies.

The baseline stays read-only. Refuse changed prompts, evidence, or candidates.
"""
import argparse
import copy
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import httpx

from run_curated_candidate_batch import ROOT, DEFAULT_AUDITOR, verify_frozen
from run_optimized_comparison import (
    read, digest, now, Heartbeat, call_logged, evaluate, metrics, AUDIT_INSTRUCTION,
    _load_baiweixi_render_context,
)
from finish_optimized_comparison_audit import compact_audit_material
from audit_requests import audit_messages
from pilot_pipeline import persist
from run_mixed_candidate_batch import write_jsonl, norm
from summarize_curated_reply_usage import summarize


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_replay_material(record, saved, messages, schema):
    if saved['messages'] != messages or saved['schema'] != schema:
        raise ValueError('Auditor prompt/schema/evidence changed: '+record['sample_id'])
    payload = json.loads(saved['messages'][-1]['content'])['待审数据']
    if payload['候选回复'] != record['target']:
        raise ValueError('Saved auditor request belongs to another reply')
    if not record.get('target', {}).get('reply', '').strip():
        raise ValueError('Cannot reaudit an empty reply')


def run(source, out, model):
    if source.resolve() == out.resolve() or source.resolve() in out.resolve().parents:
        raise ValueError('Use a separate sibling output directory, not the baseline directory')
    baseline = read(source/'results.json')
    if len(baseline) != 100 or len({r['sample_id'] for r in baseline}) != 100:
        raise ValueError('This matched experiment requires exactly 100 distinct baseline replies')
    if read(source/'batch_status.json')['status'] != 'complete':
        raise ValueError('Baseline is incomplete')
    anchor = _load_baiweixi_render_context()[2]
    materials = {}
    source_hashes = {'results.json':file_hash(source/'results.json')}
    for rec in baseline:
        verify_frozen(rec)
        sid = rec['sample_id']
        saved_path = source/'requests'/f'{sid}.audit.json'
        saved = read(saved_path)
        payload, schema, catalog, independent, mapping = compact_audit_material(
            rec['scenario'], rec['target'], rec['scenario']['component_source']['topic'], anchor)
        verify_replay_material(rec, saved, audit_messages(AUDIT_INSTRUCTION, payload), schema)
        materials[sid] = (saved, catalog, independent, mapping)
        source_hashes['requests/'+saved_path.name] = file_hash(saved_path)
    with httpx.Client(timeout=30) as client:
        tags = client.get('http://127.0.0.1:11434/api/tags').raise_for_status().json()['models']
        identity = next((m for m in tags if m['name']==model), None)
        if identity is None:
            raise ValueError('Requested auditor is not installed: '+model)
        info = client.post('http://127.0.0.1:11434/api/show', json={'model':model}).raise_for_status().json()
    config = dict(version='exact_audit_replay.v1', source=str(source.resolve()),
        source_hashes=source_hashes, auditor=model, auditor_digest=identity['digest'], count=100,
        generator_calls=0, replies_reused=100, released_training_rows=0,
        think=False, num_ctx=16384, num_predict=3400, temperature=0, retries=1,
        code_hashes={name:file_hash(ROOT/'tools'/name) for name in (
            'reaudit_curated_replies.py','run_optimized_comparison.py','pilot_pipeline.py',
            'audit_generated_history.py','audit_requests.py','finish_optimized_comparison_audit.py')})
    out.mkdir(parents=True, exist_ok=True)
    if (out/'config.json').exists() and read(out/'config.json') != config:
        raise ValueError('Resume inputs/model/code changed; use a new output directory')
    persist(out/'config.json', config)
    persist(out/'model_identity.json', {'tag':identity,'details':info['details'],
        'parameters':info.get('parameters'),'model_info':info.get('model_info')})
    for folder in ('requests','checkpoints','inputs','evidence_maps'):
        (out/folder).mkdir(exist_ok=True)
    persist(out/'preflight_report.json', {'status':'passed','count':100,
        'same_audit_messages_and_schemas':True,'same_replies':True,
        'same_generation_messages':True,'generation_calls':0})
    # Load weights/context before the first 90-second audited request. This is
    # a model-load request, not an unrecorded candidate or grading call.
    print('Preflight passed: 100 exact audit requests; loading '+model, flush=True)
    records = []
    for base in baseline:
        path = out/'checkpoints'/f"{base['sample_id']}.json"
        if path.exists():
            rec = read(path)
            if rec['target'] != base['target'] or rec['generation_messages'] != base['generation_messages']:
                raise ValueError('Checkpoint changed candidate/input')
        else:
            rec = copy.deepcopy(base)
            for key in ('audit','audit_attempted','report','quarantined'):
                rec.pop(key,None)
            rec.update(status='generated',audit_model=model,baseline_status=base['status'],
                       baseline_audit_model=base.get('audit_model'))
        records.append(rec)
    with Heartbeat(out) as heartbeat:
        heartbeat.update(phase='model_load',expected=100,processed=0)
        response = httpx.post('http://127.0.0.1:11434/api/generate',timeout=300,
            json={'model':model,'prompt':'','stream':False,'keep_alive':'10m','think':False,
                  'options':{'num_ctx':16384}})
        response.raise_for_status()
        persist(out/'model_load.json', response.json())
        consecutive_failures = 0
        for i, rec in enumerate(records,1):
            sid = rec['sample_id']
            if rec.get('audit_attempted'):
                continue
            heartbeat.update(phase='audit',active_sample=sid,processed=i-1)
            saved, catalog, independent, mapping = materials[sid]
            persist(out/'evidence_maps'/f'{sid}.json',mapping)
            try:
                verdict = call_logged(out,sid+'.audit',model,saved['messages'],saved['schema'],
                                      3400,0,retries=1)
                rec['audit'] = verdict
                rec['report'] = evaluate(rec['scenario'],rec['target'],verdict,catalog,independent)
                rec['status'] = 'approved' if rec['report']['approved'] else 'content_rejected'
                consecutive_failures = 0
            except Exception as error:
                rec.update(status='audit_failed',report={'approved':False,'reasons':[str(error).split('\n')[0]]})
                consecutive_failures += 1
            rec['audit_attempted'] = True
            rec['quarantined'] = not rec['report']['approved']
            persist(out/'checkpoints'/f'{sid}.json',rec)
            print(f'audit {i}/100 {sid}: {rec["status"]}',flush=True)
            if consecutive_failures >= 3:
                heartbeat.update(status='failed',phase='consecutive_audit_failures',processed=i)
                raise RuntimeError('Three consecutive transport/format failures; stopped without altering candidates')
        # Preserve the baseline postprocessing rule for a matched comparison.
        seen = set()
        for rec in records:
            reply = norm(rec['target']['reply'])
            if reply in seen:
                rec.update(status='content_rejected',quarantined=True)
                rec['report']['approved'] = False
                if 'duplicate_reply' not in rec['report']['reasons']:
                    rec['report']['reasons'].append('duplicate_reply')
            seen.add(reply)
            persist(out/'checkpoints'/f"{rec['sample_id']}.json",rec)
        # Verify read-only baseline integrity before publishing the experiment.
        if any(file_hash(source/name)!=sha for name,sha in source_hashes.items()):
            raise ValueError('Baseline changed during reaudit')
        persist(out/'results.json',records)
        write_jsonl(out/'inputs/all_for_review.jsonl',records)
        transitions = Counter(base['status']+' -> '+new['status'] for base,new in zip(baseline,records))
        changes = [dict(sample_id=r['sample_id'],reply=r['target']['reply'],
            old_status=b['status'],new_status=r['status'],old_reasons=b['report']['reasons'],
            new_reasons=r['report']['reasons']) for b,r in zip(baseline,records)
            if b['status']!=r['status'] or b['report']['reasons']!=r['report']['reasons']]
        report = dict(count=100,valid_replies=100,auditor=model,replies_reused=100,generation_calls=0,
            auto_pass=sum(r['report']['approved'] for r in records),
            audit_failed=sum(r['status']=='audit_failed' for r in records),released_training_rows=0,
            raw_facts_pass=sum(r.get('audit',{}).get('facts',{}).get('pass') is True for r in records),
            raw_style_approve=sum(r.get('audit',{}).get('style',{}).get('decision')=='approve' for r in records),
            old_auto_pass=sum(r['report']['approved'] for r in baseline),transitions=dict(transitions),
            audit=metrics(out,'*.audit'),finished_at=now())
        persist(out/'report.json',report)
        persist(out/'comparison.json',{'summary':report,'changed_items':changes,
            'known_missed_cases':[{k:r[k] for k in ('sample_id','target','status','report','audit') if k in r}
                for r in records if r['sample_id'].endswith(('.011','.017','.091'))]})
        persist(out/'batch_status.json',{'status':'complete','expected':100,
            'review_sha256':file_hash(out/'inputs/all_for_review.jsonl')})
        heartbeat.update(status='complete',phase='complete',active_sample=None,processed=100)
    subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/build_conversational_review_page.py'),
        '--input',str(out/'inputs/all_for_review.jsonl'),'--output',str(out/'review.html'),
        '--expected','100','--group','follow_up_question','--group','light_teasing',
        '--title','同一批100条原始回复 · Gemma 4 31B QAT 重审'],check=True)
    summarize(out)
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--auditor',default=DEFAULT_AUDITOR)
    args=parser.parse_args()
    run(args.source,args.output_root,args.auditor)
