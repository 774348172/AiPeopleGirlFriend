"""Attach explicit Codex notes without overwriting any machine verdict."""
import argparse
import hashlib
import subprocess
import sys
from collections import Counter
from pathlib import Path

from run_curated_candidate_batch import ROOT, read, persist, write_jsonl, digest


def export(out):
    rows=read(out/'results.json'); review=read(out/'assistant_review.json')
    notes={r['sample_id']:r for r in review['rows']}
    if len(notes)!=len(rows) or set(notes)!={r['sample_id'] for r in rows}:
        raise ValueError('assistant review does not cover the batch exactly')
    page_notes={}
    for row in rows:
        decision=notes[row['sample_id']]
        if decision['decision']=='approve' and not row.get('target'):
            raise ValueError('cannot approve a missing target')
        row['assistant_review']={**decision,'reviewed_input_sha256':row['frozen_input_sha256'],
                                 'reviewed_target_sha256':digest(row.get('target'))}
        row['quarantined']=not row.get('report',{}).get('approved',False) or decision['decision']!='approve'
        row['training_released']=False
        page_notes[row['sample_id']]=['Codex逐条复核：'+{'approve':'建议通过','reject':'不通过','human_review':'请用户裁决'}[decision['decision']],*decision['notes']]
        persist(out/'checkpoints'/f"{row['sample_id']}.json",row)
    persist(out/'results.json',rows)
    persist(out/'independent_review.json',page_notes)
    report=read(out/'report.json')
    report['assistant_review_counts']=dict(Counter(r['decision'] for r in review['rows']))
    report['machine_verdicts_preserved']=True
    persist(out/'report.json',report)
    write_jsonl(out/'inputs/all_for_review.jsonl',rows)
    persist(out/'batch_status.json',{'status':'complete','expected':len(rows),
        'review_sha256':hashlib.sha256((out/'inputs/all_for_review.jsonl').read_bytes()).hexdigest()})
    subprocess.run([sys.executable,str(ROOT/'tools/build_conversational_review_page.py'),
        '--input',str(out/'inputs/all_for_review.jsonl'),'--output',str(out/'review.html'),
        '--expected',str(len(rows)),'--group','light_teasing','--group','follow_up_question',
        '--title',f'提前编排完整输入 · 本地 Qwen {len(rows)} 条新候选'],check=True)
    print(report)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--notes',type=Path,help='Explicitly authored index|decision|reason notes; no inferred approval')
    args=parser.parse_args()
    if args.notes:
        records=read(args.output_root/'results.json')
        by_index={str(int(r['sample_id'].rsplit('.',1)[1])):r['sample_id'] for r in records}
        reviewed=[]
        for line in args.notes.read_text(encoding='utf-8').splitlines():
            if not line.strip(): continue
            index,decision,reason=line.split('|',2)
            if decision not in ('approve','reject','human_review') or not reason.strip():
                raise ValueError('invalid explicit reviewer note')
            reviewed.append({'sample_id':by_index[str(int(index))],'decision':decision,'notes':[reason]})
        if len(reviewed)!=len(records) or len({r['sample_id'] for r in reviewed})!=len(records):
            raise ValueError('every sample requires one explicit note')
        persist(args.output_root/'assistant_review.json',{'reviewer':'Codex',
            'scope':'逐条对照冻结输入与本地实际回复；独立于机器结果，未经用户批准',
            'training_release':False,'rows':reviewed})
    export(args.output_root)
