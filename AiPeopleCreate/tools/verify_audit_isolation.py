"""Real local-model verification; does not regenerate training candidates."""
import argparse
import json
from pathlib import Path
from build_v5_pilot_dataset import _load_baiweixi_render_context
from joint_scene_planning import evidence_catalog, validate_evidence_requirements
from audit_requests import audit_messages, call_auditor, coverage_schema, coverage_payload, COVERAGE_INSTRUCTION, FACT_INSTRUCTION, FACT_SCHEMA
from pilot_pipeline import persist
from run_component_game_reply15_pilot import _json_object

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output-root',type=Path,required=True); args=parser.parse_args()
    args.output_root.mkdir(parents=True,exist_ok=False)
    _,_,anchor=_load_baiweixi_render_context(); results=[]
    for suffix in ('015','018','028'):
        sid=f'mixed200.{suffix}.jointretry'
        row=json.loads((ROOT/f'训练数据/joint_retry17_v2_20260914/plans/{sid}.json').read_text(encoding='utf-8'))['component']
        catalog=evidence_catalog(row,anchor)
        messages=audit_messages(COVERAGE_INSTRUCTION,coverage_payload(row,row['topic'],catalog))
        response=call_auditor('http://127.0.0.1:11434/v1','gemma4:12b',messages,coverage_schema(catalog))
        persist(args.output_root/f'{sid}.json',{'messages':messages,'response':response})
        parsed=_json_object(response.get('message',{}).get('content',''))
        if response.get('done_reason')=='length' or set(parsed)!={'pass','reasons','requirements'} or type(parsed['pass']) is not bool:
            raise ValueError('audit still has invalid output')
        if parsed['pass']: validate_evidence_requirements(row,parsed,catalog)
        results.append({'id':sid,'format_valid':True,'decision':parsed['pass'],'reasons':parsed['reasons']})
        persist(args.output_root/'progress.json',results)
    messages=audit_messages(FACT_INSTRUCTION,{'当前问题':'你昨天晚上吃了什么？','角色设定':'喜欢鱼干；不知道昨天的晚饭。','权威状态':{'昨天晚饭':None},'历史':[], '候选':{'reply':'昨晚我吃了鱼干和剩下的面包。'}})
    response=call_auditor('http://127.0.0.1:11434/v1','gemma4:12b',messages,FACT_SCHEMA,800)
    persist(args.output_root/'negative_control.json',{'messages':messages,'response':response})
    parsed=_json_object(response.get('message',{}).get('content',''))
    if parsed.get('pass') is not False: raise ValueError('fabricated dinner negative control not rejected')
    persist(args.output_root/'report.json',{'real_failed_inputs':results,'negative_control_rejected':True,'training_started':False})
    print(json.dumps({'checked':3,'negative_control_rejected':True},ensure_ascii=False))
if __name__=='__main__':main()
