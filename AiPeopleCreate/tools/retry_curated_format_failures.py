"""One explicit format-only repair, preserving the completed first-pass batch."""
import argparse
import hashlib
import subprocess
import sys
import httpx
from pathlib import Path
from human_calibrated_review import CALIBRATION, apply_candidate_dedup
from audit_consistency import CLARIFICATION

from run_curated_candidate_batch import (
    ROOT, read, persist, prepare, verify_frozen, digest, call_logged, REPLY_SCHEMA,
    validate_conversational_target, compact_audit_material, audit_messages,
    AUDIT_INSTRUCTION, evaluate, write_jsonl, metrics, now,
    _load_baiweixi_render_context,
)


def retry(out):
    if read(out/'batch_status.json')['status'] != 'complete':
        raise ValueError('finish the initial batch first')
    config=read(out/'config.json'); frozen=read(out/'inputs/frozen_inputs.json')
    anchor=_load_baiweixi_render_context()[2]
    prepared=prepare([r['scenario']['component_source'] for r in frozen],
                     {r['sample_id']:r['input_review'] for r in frozen}, anchor)
    if digest(prepared)!=config['prepared_sha256']:
        raise ValueError('authority, anchor or approved inputs changed')
    if not (out/'first_pass_results.json').exists():
        persist(out/'first_pass_results.json',read(out/'results.json'))
        persist(out/'first_pass_report.json',read(out/'report.json'))
    records=[read(out/'checkpoints'/f"{r['sample_id']}.json") for r in read(out/'results.json')]
    for record in records:
        if record.get('target') or record['status']!='generation_failed' or record.get('format_retry_attempted'):
            continue
        sid=record['sample_id']; verify_frozen(record)
        record['first_pass_failure']=record['report']
        # Persist the attempt marker first: restarting this repair cannot loop
        # over a semantically disliked reply or silently resample again.
        record['format_retry_attempted']=True
        persist(out/'checkpoints'/f'{sid}.json',record)
        try:
            loaded=httpx.post('http://127.0.0.1:11434/api/generate',timeout=300,json={'model':config['generator'],'prompt':'','stream':False,'think':False,'options':{'num_ctx':16384}})
            loaded.raise_for_status()
            persist(out/(sid+'.reply_retry_load.json'),loaded.json())
            record['target']=call_logged(out,sid+'.reply_format_retry',config['generator'],
                record['generation_messages'],REPLY_SCHEMA,500,.55,validate_conversational_target,retries=1)
            payload,schema,catalog,independent,mapping=compact_audit_material(
                record['scenario'],record['target'],record['scenario']['component_source']['topic'],anchor)
            persist(out/'evidence_maps'/f'{sid}.json',mapping)
            loaded=httpx.post('http://127.0.0.1:11434/api/generate',timeout=300,json={'model':config['auditor'],'prompt':'','stream':False,'think':False,'options':{'num_ctx':16384}})
            loaded.raise_for_status()
            persist(out/(sid+'.audit_retry_load.json'),loaded.json())
            record['audit']=call_logged(out,sid+'.audit_format_retry',config['auditor'],
                audit_messages(AUDIT_INSTRUCTION+(CALIBRATION if config.get('calibration_version') else '')+CLARIFICATION,payload),schema,3400,0,retries=1)
            record['report']=evaluate(record['scenario'],record['target'],record['audit'],catalog,independent)
            record['status']=record['report'].get('decision', 'approved' if record['report']['approved'] else 'content_rejected')
            record['audit_attempted']=True
        except Exception as error:
            record.update(status='audit_failed' if record.get('target') else 'generation_failed',
                          report={'approved':False,'reasons':[str(error).split('\n')[0]]})
        record['quarantined']=not record['report']['approved']
        persist(out/'checkpoints'/f'{sid}.json',record)
        print(sid+': '+record['status'],flush=True)
    # Checkpoints are authoritative if the repair was interrupted.
    records=[read(out/'checkpoints'/f"{r['sample_id']}.json") for r in records]
    if config.get('calibration_version'):
        apply_candidate_dedup(records)
    persist(out/'results.json',records)
    write_jsonl(out/'inputs/all_for_review.jsonl',records)
    first=read(out/'first_pass_report.json')
    persist(out/'report.json',{'count':len(records),'valid_replies':sum(bool(r.get('target')) for r in records),
        'first_pass_valid_replies':first['valid_replies'],
        'format_retry_slots':sum(r.get('format_retry_attempted',False) for r in records),
        'auto_pass':sum(r.get('report',{}).get('approved',False) for r in records),
        'audit_failed':sum(r['status']=='audit_failed' for r in records),
        'audit_invalid':sum(r['status']=='audit_invalid' for r in records),
        'authoring_model_calls':0,'released_training_rows':0,'generation':metrics(out,'*.reply'),
        'audit':metrics(out,'*.audit'),'retry_generation':metrics(out,'*.reply_format_retry'),
        'retry_audit':metrics(out,'*.audit_format_retry'),'finished_at':now()})
    persist(out/'batch_status.json',{'status':'complete','expected':len(records),
        'review_sha256':hashlib.sha256((out/'inputs/all_for_review.jsonl').read_bytes()).hexdigest()})
    subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools/build_conversational_review_page.py'),
        '--input',str(out/'inputs/all_for_review.jsonl'),'--output',str(out/'review.html'),
        '--expected',str(len(records)),'--group','light_teasing','--group','follow_up_question',
        '--title',f'提前编排完整输入 · 本地 Qwen {len(records)} 条新候选'],check=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output-root',type=Path,required=True)
    retry(parser.parse_args().output_root)
