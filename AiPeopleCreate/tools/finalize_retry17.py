"""Repair serialization failures with new model calls and publish a verified 17-row batch."""
import copy,hashlib,json,subprocess,sys
from pathlib import Path
from pilot_pipeline import persist,native_response,run_stages
from audit_requests import object_schema,call_auditor,FACT_INSTRUCTION,FACT_SCHEMA,STYLE_SCHEMA,audit_messages,coverage_payload
from run_component_game_reply15_pilot import build_audit_messages,_json_object
from joint_scene_planning import evidence_catalog
from build_v5_pilot_dataset import _load_baiweixi_render_context
from data_gen_v4.runtime_grounded.conversational import validate_conversational_target,evaluate_conversational_candidate

ROOT=Path(__file__).resolve().parents[1]
def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();root=a.root
    report=json.loads((root/'pilot_report.json').read_text(encoding='utf-8'))
    if report['failures'] or report['candidates']!=17: raise ValueError('17 inputs must complete coverage before finalization')
    rows=[json.loads(x) for x in (root/'inputs/all_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[json.loads((root/'checkpoints'/f"{r['sample_id']}.json").read_text(encoding='utf-8')) for r in rows]
    _,_,anchor=_load_baiweixi_render_context(); repaired=[]
    schema=object_schema({'reply':{'type':'string','minLength':1,'maxLength':400,'pattern':'^[^{}<>]*$'}})
    for i,row in enumerate(rows):
        try: validate_conversational_target(row.get('target',{})); continue
        except Exception: pass
        parent=root/'format_retries'/row['sample_id'];parent.mkdir(parents=True,exist_ok=True)
        archive=parent/f'round{len(list(parent.glob("round*")))+1}';archive.mkdir()
        persist(archive/'original.json',row)
        scenario=row['scenario'];messages=row['generation_messages'];sid=row['sample_id'];url=row['base_url']
        for attempt in range(1,4):
            result=run_stages({'sample_id':sid,'scenario':scenario,'generation_messages':messages,'base_url':url,'model':row['model']},archive/f'attempt{attempt}.json',
                lambda:native_response(url,row['model'],messages,300,response_schema=schema),
                lambda target,n:call_auditor(url,'gemma4:12b',build_audit_messages(scenario,messages,target),STYLE_SCHEMA,1000),
                lambda target,audit:evaluate_conversational_candidate(scenario,target,audit),_json_object)
            if result['status']!='generation_failed': break
        validate_conversational_target(result.get('target',{}))
        if result['status']=='generation_failed':raise ValueError('format repair failed '+sid)
        payload={**coverage_payload(scenario['component_source'],scenario['component_source']['topic'],evidence_catalog(scenario['component_source'],anchor)),'候选':result['target']}
        fact_messages=audit_messages(FACT_INSTRUCTION,payload)
        response=call_auditor(url,'gemma4:12b',fact_messages,FACT_SCHEMA,1200)
        persist(archive/'fact_audit.json',{'messages':fact_messages,'response':response})
        try:
            fact=_json_object(response['message']['content'])
            if set(fact)!={'pass','reasons'} or type(fact['pass']) is not bool or not isinstance(fact['reasons'],list) or response.get('done_reason')=='length':raise ValueError('invalid audit')
        except Exception as e:fact={'pass':False,'reasons':['fact audit failed: '+str(e)]}
        result.update(fact_review=fact,fact_review_complete=True,quarantined=result['status']!='approved' or not fact['pass'],format_retry_archive=str(archive))
        rows[i]=result;repaired.append(sid);persist(root/'checkpoints'/f'{sid}.json',result)
    if len(rows)!=17 or len({r['sample_id'] for r in rows})!=17:raise ValueError('not 17 unique rows')
    for r in rows:validate_conversational_target(r['target'])
    repaired=[r['sample_id'] for r in rows if r.get('format_retry_archive')]
    # Archive the completed run's source files before publishing repaired versions.
    audit_root=root/'before_format_finalization';audit_root.mkdir(exist_ok=True)
    for name in ('all_candidates.jsonl','all_for_review.jsonl'):
        src=root/'inputs'/name
        if not (audit_root/name).exists():(audit_root/name).write_bytes(src.read_bytes())
        src.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    persist(root/'independent_review.json',{r['sample_id']:['隔离待人工审核' if r['quarantined'] else '自动审核通过，仍需人工确认',*r['fact_review']['reasons'],*(['格式不合格的原回复已归档，本条为重新调用模型生成'] if r['sample_id'] in repaired else [])] for r in rows})
    report.update(valid_replies=17,quarantined=sum(r['quarantined'] for r in rows),format_repaired=repaired,human_review_count=17)
    persist(root/'pilot_report.json',report)
    persist(root/'batch_status.json',{'status':'complete','expected':17,'failed_slots':0,'review_sha256':hashlib.sha256((root/'inputs/all_for_review.jsonl').read_bytes()).hexdigest()})
    subprocess.run([sys.executable,str(ROOT/'tools/build_conversational_review_page.py'),'--input',str(root/'inputs/all_for_review.jsonl'),'--output',str(root/'review.html'),'--expected','17','--group','light_teasing','--group','follow_up_question','--title','原隔离17条完整重生成审核'],check=True)
    print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
