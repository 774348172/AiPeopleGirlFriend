"""Bounded local regression of recorded failures, without modifying the source batch."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema

from audit_requests import (audit_messages, call_auditor, coverage_schema,
                            COVERAGE_INSTRUCTION, FACT_SCHEMA)
from audit_generated_history import audit_history
from build_v5_pilot_dataset import _load_baiweixi_render_context
from joint_scene_planning import apply_plan
from pilot_pipeline import native_response, persist, REPLY_SCHEMA
from data_gen_v4.runtime_grounded.conversational import validate_conversational_target

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:11434/v1'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--reuse-format-results',type=Path,
                        help='Reuse unchanged reply-schema checks from an earlier regression')
    parser.add_argument('--finalize-inspection',type=Path,
                        help='Apply separate assistant inspection after completion; preserve raw candidates')
    args=parser.parse_args()
    out=args.output_root
    out.mkdir(parents=True,exist_ok=True)
    if args.finalize_inspection:
        if read(out/'full_pipeline/batch_status.json')['status']!='complete':
            raise ValueError('cannot finalize an unfinished batch')
        inspection=read(args.finalize_inspection)
        source=out/'full_pipeline/inputs/all_candidates.jsonl'
        rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines()]
        for row in rows:
            review=inspection.get(row['sample_id'])
            if review:
                row['assistant_inspection']=review
                if review['decision']=='quarantine': row['quarantined']=True
        target=out/'assistant_checked_candidates.jsonl'
        target.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        report=read(out/'summary.json')
        report['assistant_inspection']=inspection
        report['after_inspection_quarantined']=sum(row['quarantined'] for row in rows)
        persist(out/'final_summary.json',report)
        print(json.dumps({'candidates':len(rows),'quarantined':report['after_inspection_quarantined']},ensure_ascii=False))
        return
    old=ROOT/'训练数据/expanded200_b01_20260914'
    results=[]
    # Reuse exactly the final old answer inputs for four formatting failures.
    for number in (146,156,177,190):
        sid=f'expanded200_b01.{number:03d}'
        original=read(old/'checkpoints'/f'{sid}.json')
        target=out/f'{sid}.format.json'
        if target.exists():
            results.append(read(target)); continue
        record={'sample_id':sid,'check':'reply_format','attempts':[], 'passed':False}
        if args.reuse_format_results:
            source=args.reuse_format_results/f'{sid}.format.json'
            record={**read(source),'reused_from':str(source.resolve())}
            persist(target,record); results.append(record); continue
        for attempt in range(3):
            item={'attempt':attempt+1}; record['attempts'].append(item)
            try:
                response=native_response(URL,'qwen3.5:27b',original['generation_messages'],500,
                    response_schema=REPLY_SCHEMA,num_ctx=16384)
                item['response']=response
                value=json.loads(response['message']['content'])
                jsonschema.validate(value,REPLY_SCHEMA)
                validate_conversational_target(value)
                if response.get('done_reason')=='length': raise ValueError('truncated')
                record.update(passed=True,target=value)
                break
            except Exception as error: item['error']=str(error)
            finally: persist(target,record)
        results.append(record)
        print(f'{sid}: format={record["passed"]}',flush=True)
    # Replay the actual over-rejected history with no new supporting events.
    sid='expanded200_b01.006'
    old_audit=sorted((old/'plans').glob(sid+'.history_audit.*.json'))[-1]
    data=json.loads(read(old_audit)['messages'][1]['content'])['待审数据']
    source_seeds=[json.loads(line) for line in (old/'seeds.jsonl').read_text(encoding='utf-8').splitlines()]
    row=apply_plan(next(r for r in source_seeds if r['sample_id']==sid),read(old/'plans'/f'{sid}.world.json')['plan'])
    row.update(memory_turns=data['旧对话'],recent_turns=data['最近对话'],current_user=data['当前男主发言'])
    _,_,anchor=_load_baiweixi_render_context()
    history_cases=[(sid,row)]
    if args.reuse_format_results:
        # Re-audit the new histories which the prior Boolean reviewer missed.
        for checkpoint in sorted((args.reuse_format_results/'full_pipeline/checkpoints').glob('*.json')):
            candidate=read(checkpoint)
            history_cases.append((candidate['sample_id'],candidate['scenario']['component_source']))
    for history_id,history_row in history_cases:
        path=out/f'{history_id}.history.json'
        record={'sample_id':history_id,'check':'history','passed':False}
        try:
            verdict=audit_history(history_row,URL,'qwen3.5:27b',out/f'{history_id}.history_raw.json',anchor)
            record.update(passed=True,verdict=verdict)
        except Exception as error: record['error']=str(error)
        persist(path,record); results.append(record)
        print(f'{history_id}: history={record.get("verdict",record.get("error"))}',flush=True)
    requests=[]
    # 191 tests known + unknown; 194 tests audit-output truncation. 093 and 157
    # are negative controls: an unrelated question must still be rejected.
    for number in (191,194,93,157):
        sid=f'expanded200_b01.{number:03d}'
        attempt=read(old/'plans'/f'{sid}.coverage_attempts.json')['attempts'][-1]
        data=json.loads(attempt['messages'][1]['content'])['待审数据']
        requests.append((sid,'coverage',data,COVERAGE_INSTRUCTION,coverage_schema(data['证据目录']),'gemma4:12b'))
    for sid,kind,data,instruction,schema,model in requests:
        path=out/f'{sid}.{kind}.json'
        if path.exists():
            results.append(read(path)); continue
        record={'sample_id':sid,'check':kind,'messages':audit_messages(instruction,data),'passed':False}
        try:
            response=call_auditor(URL,model,record['messages'],schema,1800)
            record['response']=response
            verdict=json.loads(response['message']['content'])
            jsonschema.validate(verdict,schema)
            if response.get('done_reason')=='length': raise ValueError('truncated')
            record.update(passed=True,verdict=verdict)
        except Exception as error: record['error']=str(error)
        persist(path,record); results.append(record)
        print(f'{sid}: {kind}={record.get("verdict",record.get("error"))}',flush=True)
    persist(out/'replayed_failures.json',results)
    # Full production path for 3 original slots contaminated by negative examples.
    selected={'expanded200_b01.009','expanded200_b01.014','expanded200_b01.018'}
    seeds=[]
    for line in (old/'seeds.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        if row['sample_id'] in selected:
            row=copy.deepcopy(row)
            row['retry_of']=row['sample_id']
            row['sample_id']+='.fixregression'
            for key in ('family_id','snapshot_id','relation_id','utterance_id'):
                row['component_ids'][key]=row['sample_id']+'.'+key
            seeds.append(row)
    if len(seeds)!=3: raise ValueError('missing regression source seeds')
    seedpath=out/'seeds.jsonl'
    seedpath.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in seeds),encoding='utf-8')
    command=[sys.executable,str(ROOT/'tools/run_mixed_candidate_batch.py'),'--count','3',
        '--seed-input',str(seedpath),'--output-root',str(out/'full_pipeline')]
    for path in read(old/'config.json')['history_source_sha256']:
        command+=['--history-input',path]
    subprocess.run(command,check=True)
    persist(out/'summary.json',{'replayed_checks':len(results),
        'format_valid':sum(r['check']=='reply_format' and r['passed'] for r in results),
        'audits':[{k:v for k,v in r.items() if k in ('sample_id','check','passed','verdict','error')}
                  for r in results if r['check']!='reply_format'],
        'full_pipeline':read(out/'full_pipeline/pilot_report.json'),'training_started':False})


if __name__=='__main__': main()
