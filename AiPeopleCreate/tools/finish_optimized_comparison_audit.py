"""Resume completed generation with compact, fully traceable evidence IDs only."""
import argparse
import copy
import json
from pathlib import Path
import time

from run_optimized_comparison import (
    read, now, audit_material, audit_messages, AUDIT_INSTRUCTION,
    call_logged, evaluate, write_outputs, old_call_metrics, metrics, Heartbeat,
    _load_baiweixi_render_context, digest,
)
from audit_requests import object_schema, coverage_schema, STYLE_SCHEMA, FACT_SCHEMA
from audit_generated_history import history_audit_schema
from pilot_pipeline import persist


def compact_audit_material(scenario, target, topic, anchor):
    payload, _, catalog, _ = audit_material(scenario, target, topic, anchor)
    aliases={e['id']:f'E{i}' for i,e in enumerate(catalog)}
    compact=copy.deepcopy(catalog)
    for entry in compact:
        original=entry['id']; entry['id']=aliases[original]
        if 'current_fact_ids' in entry:
            entry['current_fact_ids']=[aliases['world:'+ident] for ident in entry['current_fact_ids']]
    independent=[e for e in compact if not(e['kind']=='world' and e['value'] is None)]
    payload['证据目录']=compact
    # Dialogue is already in chronological sections; do not send it twice in the
    # catalog. The remaining reference IDs are attached to their actual turns.
    payload['证据目录']=[e for e in compact if e['kind']!='dialogue']
    for key,kind in [('旧记忆','memory_turns'),('最近对话','recent_turns')]:
        payload[key]=[{**turn,'evidence_id':aliases[f'{kind}:{i}']} for i,turn in enumerate(payload[key])]
    schema=object_schema({'history':history_audit_schema(scenario['component_source'],independent),
        'coverage':coverage_schema(compact),'style':STYLE_SCHEMA,'facts':FACT_SCHEMA})
    return payload,schema,compact,independent,{short:long for long,short in aliases.items()}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();out=a.root
    config=read(out/'config.json'); base=Path(config['source'])
    seeds=[json.loads(line) for line in (base/'seeds.jsonl').read_text(encoding='utf-8').splitlines()][:40]
    if digest(seeds)!=config['seed_hash']: raise ValueError('source seed hash changed')
    _,_,anchor=_load_baiweixi_render_context()
    if digest(anchor)!=config['anchor_hash']: raise ValueError('role anchor changed')
    records={seed['sample_id']:read(out/'checkpoints'/f"{seed['sample_id']}.json") for seed in seeds}
    if not all(rec.get('generation_complete') for rec in records.values()): raise ValueError('generation not complete')
    persist(out/'audit_revision.json',{'version':'compact_ids_full_content_v1','changed':'Evidence IDs shortened with external lookup; duplicated dialogue removed from catalog only; all turns still included.',
        'generation_reused':40,'old_requests_preserved':True,'started_at':now(),
        'source_code_sha256':digest(Path(__file__).read_text(encoding='utf-8'))})
    (out/'audit_reference_maps').mkdir(exist_ok=True)
    baseline={}; started=time.perf_counter()
    with Heartbeat(out) as heartbeat:
        heartbeat.update(phase='compact_audit_all_gemma',expected=40,generated_slots=40,audited_slots=0)
        for index,seed in enumerate(seeds):
            sid=seed['sample_id']; heartbeat.update(active_sample=sid)
            path=base/'checkpoints'/f'{sid}.json'
            old=read(path) if path.exists() else {'sample_id':sid};baseline[sid]=old
            for arm,rec in [('new',records[sid]),('old',old)]:
                if 'target' not in rec: continue
                payload,schema,catalog,independent,lookup=compact_audit_material(rec['scenario'],rec['target'],seed['topic'],anchor)
                persist(out/'audit_reference_maps'/f'{arm}_{sid}.json',lookup)
                try:
                    verdict=call_logged(out,arm+'_'+sid+'.audit_compact','gemma4:12b',audit_messages(AUDIT_INSTRUCTION,payload),schema,3400,0)
                    review=evaluate(rec['scenario'],rec['target'],verdict,catalog,independent)
                except Exception as error:
                    verdict=None;review={'approved':False,'model_approved':False,'reasons':['audit failed: '+str(error).split('\n')[0]]}
                if arm=='new':
                    if 'audit' in rec and 'audit_before_compaction' not in rec:
                        rec['audit_before_compaction']=rec['audit'];rec['review_before_compaction']=rec['review']
                    rec.update(audit=verdict,review=review,quarantined=not review['approved'],status='approved' if review['approved'] else 'quarantined')
                    persist(out/'checkpoints'/f'{sid}.json',rec)
                else:
                    rec['comparison_review']=review;persist(out/'baseline'/f'{sid}.json',rec)
            heartbeat.update(audited_slots=index+1)
            print(f'compact audit {index+1}/40: new={records[sid].get("status")}, old={old.get("comparison_review",{}).get("approved")}',flush=True)
        summary=write_outputs(out,seeds,records,baseline,[old_call_metrics(base,s['sample_id']) for s in seeds])
        optimized=summary['optimized'];baseline_summary=summary['baseline']
        # Also show reproducible final-path cost, excluding the superseded audit implementation.
        def selected_cost(arm,include_generation):
            calls=[]
            for path in (out/'requests').glob(arm+'_*.json'):
                if path.name.endswith('.audit_compact.json') or (include_generation and (path.name.endswith('.author.json') or path.name.endswith('.reply.json'))):
                    calls.extend(read(path)['attempts'])
            return {'calls':len(calls),'wall_seconds':sum(c.get('wall_seconds',0) for c in calls),
                'model_seconds':sum(c.get('response',{}).get('total_duration',0)/1e9 for c in calls),
                'load_seconds':sum(c.get('response',{}).get('load_duration',0)/1e9 for c in calls)}
        optimized['final_path_cost']=selected_cost('new',True)
        optimized['superseded_audit_cost']={k:optimized['production_cost'][k]-optimized['final_path_cost'][k]
            for k in ('calls','wall_seconds','model_seconds','load_seconds')}
        baseline_summary['final_common_reaudit_cost']=selected_cost('old',False)
        summary['compact_audit_wall_seconds']=time.perf_counter()-started
        persist(out/'comparison_report.json',summary)
        persist(out/'batch_status.json',{'status':'complete','count':40,'training_started':False,'completed_at':now()})
        heartbeat.update(status='complete',phase='complete',active_sample=None)
        print(json.dumps(summary,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
