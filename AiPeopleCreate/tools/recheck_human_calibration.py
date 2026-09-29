"""Replay the 24 labeled examples with calibrated instructions; not a holdout."""
import argparse,copy,hashlib,json,sys
from pathlib import Path
from collections import Counter
import httpx
from run_curated_candidate_batch import DEFAULT_AUDITOR,verify_frozen
from run_optimized_comparison import read,persist,Heartbeat,call_logged,evaluate,AUDIT_INSTRUCTION,_load_baiweixi_render_context,metrics
from finish_optimized_comparison_audit import compact_audit_material
from audit_requests import audit_messages
from human_calibrated_review import CALIBRATION,VERSION,apply_candidate_dedup
from summarize_curated_reply_usage import summarize


def run(source, human, out):
    labels=read(human); baseline=read(source)
    byid={r['sample_id']:r for r in baseline}
    if len(labels)!=24 or len({r['sample_id'] for r in labels})!=24:raise ValueError('Expected 24 unique human judgments')
    if any(r['verdict'] not in ('good','bad') or r['sample_id'] not in byid for r in labels):raise ValueError('Invalid human label')
    if out.exists():raise ValueError('Do not overwrite calibration run')
    out.mkdir(parents=True)
    for d in ('requests','checkpoints'): (out/d).mkdir()
    persist(out/'human_labels.json',labels)
    persist(out/'provenance.json',dict(source=str(source.resolve()),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        human_source=str(human.resolve()),human_sha256=hashlib.sha256(human.read_bytes()).hexdigest(),
        version=VERSION,not_held_out=True,model=DEFAULT_AUDITOR,training_released=0))
    anchor=_load_baiweixi_render_context()[2];rows=[]
    with Heartbeat(out) as beat:
        response=httpx.post('http://127.0.0.1:11434/api/generate',timeout=300,json={'model':DEFAULT_AUDITOR,'prompt':'','stream':False,'think':False,'keep_alive':'10m','options':{'num_ctx':16384}})
        response.raise_for_status();persist(out/'model_load.json',response.json())
        for i,label in enumerate(labels,1):
            r=copy.deepcopy(byid[label['sample_id']]);verify_frozen(r)
            beat.update(phase='calibrated_audit',processed=i-1,expected=24)
            payload,schema,cat,ind,_=compact_audit_material(r['scenario'],r['target'],r['scenario']['component_source']['topic'],anchor)
            try:
                r['audit']=call_logged(out,r['sample_id']+'.audit',DEFAULT_AUDITOR,audit_messages(AUDIT_INSTRUCTION+CALIBRATION,payload),schema,3400,0,retries=1)
                r['report']=evaluate(r['scenario'],r['target'],r['audit'],cat,ind)
                r['status']='approved' if r['report']['approved'] else 'content_rejected'
            except Exception as e:
                r.update(status='audit_failed',report={'approved':False,'reasons':[str(e)]})
            r['human_review']=label;r['quarantined']=not r['report']['approved'];rows.append(r)
            persist(out/'checkpoints'/f"{r['sample_id']}.json",r)
            print(f'calibration {i}/24 {r["sample_id"]}: {r["status"]}',flush=True)
        apply_candidate_dedup(rows)
        persist(out/'results.json',rows)
        matched=sum(r['report']['approved']==(r['human_review']['verdict']=='good') for r in rows)
        report=dict(count=24,agreement=matched,false_accept=[r['sample_id'] for r in rows if r['report']['approved'] and r['human_review']['verdict']=='bad'],
            false_reject=[r['sample_id'] for r in rows if not r['report']['approved'] and r['human_review']['verdict']=='good'],
            audit_failed=sum(r['status']=='audit_failed' for r in rows),audit=metrics(out,'*.audit'),not_held_out=True)
        persist(out/'report.json',report);beat.update(status='complete',processed=24)
    summarize(out);print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--human',type=Path,required=True);p.add_argument('--output-root',type=Path,required=True);a=p.parse_args();run(a.source,a.human,a.output_root)
