"""One explicit audit-only pass; preserve source replies and all old judgments."""
import argparse
import copy
import hashlib
from collections import Counter
from pathlib import Path
from run_optimized_comparison import read, persist, now, Heartbeat, call_logged, evaluate, AUDIT_INSTRUCTION, _load_baiweixi_render_context
from finish_optimized_comparison_audit import compact_audit_material
from audit_requests import audit_messages
from audit_consistency import CLARIFICATION, VERSION
from human_calibrated_review import CALIBRATION
from build_pending_human_review import build
from summarize_curated_reply_usage import summarize


def run(source, queue, out):
    if out.exists():
        raise ValueError('Use a new output directory; never overwrite prior audits')
    original_bytes=source.read_bytes()
    original=read(source)
    manifest=read(queue)
    if manifest['source_sha256']!=hashlib.sha256(original_bytes).hexdigest():
        raise ValueError('Queue belongs to different source results')
    selected=set(manifest['selected_ids'])
    if not selected or len(selected)!=manifest['pending_count']:
        raise ValueError('Invalid queue selection')
    if not selected.issubset({r['sample_id'] for r in original}):
        raise ValueError('Unknown selected ID')
    _,_,anchor=_load_baiweixi_render_context()
    materials={}
    for r in original:
        if r['sample_id'] in selected:
            if not (r.get('target') or {}).get('reply','').strip():
                raise ValueError('Audit-only requires an existing reply')
            materials[r['sample_id']]=compact_audit_material(r['scenario'],r['target'],r['scenario']['component_source']['topic'],anchor)
    out.mkdir(parents=True)
    for d in ('requests','checkpoints','evidence_maps'):(out/d).mkdir()
    persist(out/'reaudit_manifest.json',dict(source=str(source.resolve()),source_sha256=manifest['source_sha256'],
        selected_ids=sorted(selected),count=len(selected),generator_calls=0,version=VERSION,
        auditor='gemma4:31b-it-qat',started_at=now(),source_queue=str(queue.resolve())))
    records=copy.deepcopy(original)
    with Heartbeat(out) as hb:
        n=0
        for r in records:
            sid=r['sample_id']
            if sid not in selected:continue
            hb.update(phase='audit',active_sample=sid,processed=n,expected=len(selected))
            r['audit_before_reaudit']=copy.deepcopy(r.get('audit'))
            r['report_before_reaudit']=copy.deepcopy(r.get('report'))
            r['status_before_reaudit']=r['status']
            payload,schema,catalog,independent,mapping=materials[sid]
            persist(out/'evidence_maps'/f'{sid}.json',mapping)
            try:
                verdict=call_logged(out,sid+'.audit','gemma4:31b-it-qat',
                    audit_messages(AUDIT_INSTRUCTION+CALIBRATION+CLARIFICATION,payload),schema,3400,0,retries=1)
                r['audit']=verdict
                r['report']=evaluate(r['scenario'],r['target'],verdict,catalog,independent)
                r['status']=r['report']['decision']
            except Exception as exc:
                r['audit']=None
                r['status']='audit_failed'
                r['report']=dict(approved=False,audit_valid=False,decision='audit_failed',reasons=['审核调用或格式失败：'+str(exc)[:500]])
            r['quarantined']=not r['report']['approved']
            r['audit_model']='gemma4:31b-it-qat'
            r['reaudit_version']=VERSION
            persist(out/'checkpoints'/f'{sid}.json',r)
            n+=1
            print(f'audit {n}/{len(selected)} {sid}: {r["status"]}',flush=True)
        for before,after in zip(original,records):
            for k in ('scenario','generation_messages','target','frozen_input_sha256'):
                if before[k]!=after[k]:raise ValueError('Original content changed: '+k)
            if before['sample_id'] not in selected and before!=after:raise ValueError('Unselected item changed')
        if source.read_bytes()!=original_bytes:raise ValueError('Source changed during reaudit')
        persist(out/'results.json',records)
        report=dict(count=len(records),reaudited=len(selected),generator_calls=0,valid_replies=len(records),
            auto_pass=sum(r['report']['approved'] for r in records),
            selected_statuses=dict(Counter(r['status'] for r in records if r['sample_id'] in selected)),
            original_reply_and_context_unchanged=True,unselected_records_unchanged=True,
            released_training_rows=0,finished_at=now())
        persist(out/'report.json',report)
        build(out/'results.json',out/'human_review_pending')
        summarize(out)
        hb.update(status='complete',phase='complete',active_sample=None,processed=n)
        print(report,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--queue',type=Path,required=True)
    p.add_argument('--output-root',type=Path,required=True)
    a=p.parse_args();run(a.source,a.queue,a.output_root)
