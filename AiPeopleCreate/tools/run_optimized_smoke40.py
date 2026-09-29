"""40-slot smoke test with fewer model round trips.

One Qwen call authors state, frozen events and the dialogue chain; one Qwen call
produces the reply; one Gemma call audits style and grounding together. This is
an experiment entry, not a replacement for the formal 200/1000 runner yet.
"""
import argparse, copy, json, sys
from pathlib import Path
import jsonschema
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_expanded_batch_seeds import build_seeds
from build_v5_pilot_dataset import _load_baiweixi_render_context
from build_authority_component10 import build_components
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages
from run_mixed_candidate_batch import (CHAIN_SCHEMA, world_schema, validate_chain,
    normalize_world_plan, apply_plan, duplicate_reason)
from joint_scene_planning import allowed_fields
from synthetic_history import history_schema, apply_history_plan, HISTORY_INSTRUCTION
from audit_requests import audit_messages, call_auditor, STYLE_SCHEMA, COVERAGE_INSTRUCTION
from pilot_pipeline import native_response, persist, REPLY_SCHEMA
from data_gen_v4.runtime_grounded.conversational import validate_conversational_target

ROOT=Path(__file__).resolve().parents[1]; URL='http://127.0.0.1:11434/v1'
COMBINED_SCHEMA={'type':'object','additionalProperties':False,'required':['world','events','chain'],
 'properties':{'world':world_schema([('备用钥匙','当前位置'),('洗衣机','运行状态'),('冰箱','内部食物'),('微波炉','运行状态'),('街角咖啡','店员是否在场'),('邻里便利店','收银负责人')],8),
 'events':history_schema(build_components()[0],[]), 'chain':CHAIN_SCHEMA}}

def main():
 p=argparse.ArgumentParser();p.add_argument('--output-root',type=Path,required=True);p.add_argument('--batch-id',default='optimized_smoke40_20260915');a=p.parse_args();out=a.output_root;out.mkdir(parents=True,exist_ok=False)
 seeds=build_seeds(a.batch_id,200)[:40]; _,_,anchor=_load_baiweixi_render_context(); previous=[]; results=[]
 for n,seed in enumerate(seeds,1):
  sid=seed['sample_id']; path=out/f'{sid}.json'; rec={'sample_id':sid,'status':'generating','model':'qwen3.5:27b','audit_model':'gemma4:12b','attempts':[]}
  try:
   prompt='你是合成训练数据编剧。一次性输出world、events、chain三个对象，严格遵守各自JSON格式。world规划当前必要状态；events规划历史证据（事实问题至少一条，按时段选择）；chain为四句memory、四句recent和当前男主发言。双方发言只能是直接说出口的内容，不写动作旁白、不交换角色、不复制上一句。历史与当前问题必须同一主题，不能把当前状态倒推成过去。不要输出最终回复。\n输入种子：'+json.dumps({'类型':seed['task_type'],'主题':seed['topic'],'当前快照':seed['snapshot'],'角色设定':anchor},ensure_ascii=False)
   schema=copy.deepcopy(COMBINED_SCHEMA); schema['properties']['world']=world_schema(allowed_fields(seed),8)
   schema['properties']['events']=history_schema(seed,[]); schema['properties']['events']['properties']['events']['items']['properties']['current_fact_ids']['items']={'type':'string'}
   response=native_response(URL,'qwen3.5:27b',[{'role':'system','content':prompt}],2600,response_schema=schema,num_ctx=16384,temperature=.7)
   parsed=json.loads(response['message']['content']); jsonschema.validate(parsed,schema)
   planned=apply_plan(seed,normalize_world_plan(parsed['world'])); planned=apply_history_plan(planned,parsed['events'],convert(planned)['model_view']['authoritative_world']['facts'])
   validate_chain(parsed['chain'])
   for kind,key in [('memory_turns','memory'),('recent_turns','recent')]:
    for t,txt in zip(planned[kind],parsed['chain'][key]): t['text']=txt
   planned['current_user']=parsed['chain']['current_user']; planned['history_evidence_checked']=True
   reason=duplicate_reason(planned,previous)
   if reason: raise ValueError(reason)
   previous.append(planned); scenario=convert(planned); messages=build_messages(scenario,anchor); validate_final_messages(scenario,messages)
   reply=native_response(URL,'qwen3.5:27b',messages,500,response_schema=REPLY_SCHEMA,num_ctx=16384,temperature=.55)
   target=json.loads(reply['message']['content']); jsonschema.validate(target,REPLY_SCHEMA); validate_conversational_target(target)
   audit_payload={'原主题':seed['topic'],'当前问题':planned['current_user'],'冻结权威事实':scenario['model_view']['authoritative_world']['facts'],'历史事件':planned.get('historical_events',[]),'最近对话':planned['recent_turns'],'候选回复':target}
   audit=call_auditor(URL,'gemma4:12b',audit_messages(COVERAGE_INSTRUCTION+'\n同时审核候选回复是否自然、承接当前问题且没有事实扩展；只返回verdicts/decision/reasons。',audit_payload),STYLE_SCHEMA,1200)
   verdict=json.loads(audit['message']['content']); jsonschema.validate(verdict,STYLE_SCHEMA)
   rec.update(status='approved' if verdict['decision']=='approve' else 'quarantined',scenario=scenario,target=target,audit=verdict,model_calls=3)
  except Exception as e: rec.update(status='failed',error=str(e),model_calls=rec.get('model_calls',1))
  persist(path,rec); results.append(rec); print(f'{sid}: {rec["status"]} {n}/40',flush=True)
 persist(out/'report.json',{'attempted':40,'candidates':sum('target' in r for r in results),'approved':sum(r['status']=='approved' for r in results),'quarantined':sum(r['status']=='quarantined' for r in results),'failed':sum(r['status']=='failed' for r in results),'model_calls_total':sum(r.get('model_calls',0) for r in results),'training_started':False})
 (out/'inputs.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in results),encoding='utf-8')
 print(json.dumps(json.loads((out/'report.json').read_text(encoding='utf-8')),ensure_ascii=False))
if __name__=='__main__':
 raise SystemExit('This experiment is invalid: missing authoritative authoring facts and old-memory audit. Use run_optimized_comparison.py with --source-root and --output-root instead.')
