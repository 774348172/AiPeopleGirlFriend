"""Bounded isolated validation: freeze world/events, author history, gate, then reply.

Each phase batches one local model. Semantic rejection never triggers rewriting
evidence, and no automatic result is published as training data.
"""
import argparse
import copy
import json
from pathlib import Path

from run_optimized_comparison import (
    Heartbeat, call_logged, digest, now, read, metrics, evaluate,
    AUDIT_INSTRUCTION, validate_conversational_target,
)
from finish_optimized_comparison_audit import compact_audit_material
from run_mixed_candidate_batch import (
    world_schema, normalize_world_plan, duplicate_reason,
)
from build_v5_pilot_dataset import _load_baiweixi_render_context
from build_authority_component10 import build_components
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages
from joint_scene_planning import allowed_fields, apply_plan, freeze, validate_evidence_requirements
from synthetic_history import HISTORY_INSTRUCTION, history_schema, apply_history_plan
from audit_generated_history import audit_history
from audit_requests import audit_messages, coverage_schema, COVERAGE_INSTRUCTION
from pilot_pipeline import persist, REPLY_SCHEMA
from data_gen_v4.runtime_grounded.persona_projection import author_persona_material
from dialogue_authoring import character_evidence, named_chain_schema, decode_named_chain, slot_material, AUTHOR_INSTRUCTION

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'persona_owned_authoring_history_pregate_v3'
WORLD_INSTRUCTION = '''你是隔离样本的世界规划器，不写对白或答案。保留原主题与固定场景，按意图规划0至4项必要动态状态，只能使用允许字段。未知用null。visible表示本轮角色可知，hidden仅作者知道。偏好不能代替经历。闲聊可不写任何状态，不将主观话题改成查询设备。不得改变人物位置或编造互相矛盾的同义状态。'''


def make_seeds():
    external = [json.loads(x) for x in (ROOT/'训练数据/external_chat16_20260914.jsonl').read_text(encoding='utf-8').splitlines()]
    templates = [build_components()[0]]
    for scene in ('songjiangfu.neighborhood_cafe', 'songjiangfu.neighborhood_convenience_store',
                  'songjiangfu.shopping_walk', 'songjiangfu.protagonist_cafe'):
        templates.append(next(r for r in external if r['component_ids']['scene_id'] == scene))
    topics = [
        '聊独处时愿意做什么小消遣，最近对话自然追问选择理由，不安排已完成活动',
        '聊故事的两种假想结尾，意见可以不同，不冒充看过某部真实影片',
        '聊尝新口味时愿意先试一点还是直接买，保持假想，不宣称已有库存或购买',
        '男主首次表达喜欢观察招牌布局，白未晞获知后讨论舒服和气派，不能先说早已记得',
        '男主首次谈希望心愿稳定经营并留点生活时间，白未晞接着聊休息，不宣布新营业决定',
        '闲聊黑白两种假想花的审美，白未晞可当下选择，不宣称眼前存在这些花',
        '以不安排事情的假期为假设，双方接善意玩笑，不说已经订票或休假',
        '延续昨天吃什么的话题，当前追问昨日晚饭；没有饮食记录必须承认未知，不以偏好代替吃过',
        '男主说今天累了，白未晞自然接话，允许直接关心，不虚构递水等已完成动作',
        '自然接续当前店内咖啡机是否清洁完成的询问，设备存在不证明已清洁，不编清洁者或时间',
    ]
    result = []
    for i, topic in enumerate(topics):
        row = copy.deepcopy(templates[i % 5]); sid = f'persona20260915.{i+1:02d}'
        row.update(sample_id=sid, topic=topic, known_profile={}, current_user='待编剧生成自然承接的当前发言',
                   task_type='follow_up_question' if i in (7,9) else 'idle_chat')
        for key in ('snapshot_id', 'family_id', 'relation_id', 'utterance_id'):
            row['component_ids'][key] = sid + '.' + key
        row['snapshot']['states'] = []
        row.pop('fact_contract', None); row.pop('historical_events', None)
        for kind in ('memory_turns','recent_turns'):
            for j, turn in enumerate(row[kind]):
                turn['source'] = f'{sid}.{kind}.{j}'
        result.append(row)
    return result


def run(out, seeds):
    out.mkdir(parents=True, exist_ok=True)
    for folder in ('requests','checkpoints','evidence_maps'): (out/folder).mkdir(exist_ok=True)
    _, _, anchor = _load_baiweixi_render_context()
    config = {'version':VERSION,'seed_hash':digest(seeds),'anchor_hash':digest(anchor),
              'pipeline_code_hashes':{name:digest((ROOT/'tools'/name).read_text(encoding='utf-8')) for name in
                                     ('dialogue_authoring.py','audit_generated_history.py','joint_scene_planning.py',
                                      'finish_optimized_comparison_audit.py','run_optimized_comparison.py')},
              'source_hashes':{s['sample_id']:convert(s)['model_view']['authoritative_world']['source_hashes'] for s in seeds},
              'runner_sha256':digest(Path(__file__).read_text(encoding='utf-8')),
              'generator':'qwen3.5:27b','auditor':'gemma4:12b','count':len(seeds),'training_started':False}
    if (out/'config.json').exists() and read(out/'config.json') != config:
        raise ValueError('resume inputs changed; preserve previous run and use a new directory')
    persist(out/'config.json',config)
    rows = {}; previous=[]
    with Heartbeat(out) as hb:
        for phase in ('author','pregate','reply','final_audit'):
            for index, seed in enumerate(seeds):
                sid=seed['sample_id']; path=out/'checkpoints'/f'{sid}.json'
                rec=rows.get(sid) or (read(path) if path.exists() else {'sample_id':sid})
                rows[sid]=rec
                hb.update(phase=phase,active_sample=sid,processed=index,expected=len(seeds))
                if rec.get('error'): continue
                if rec.get(phase+'_complete'):
                    if phase=='author': previous.append(rec['scenario']['component_source'])
                    continue
                try:
                    if phase=='author':
                        view=convert(seed)['model_view']; persona=author_persona_material(view)
                        world=call_logged(out,sid+'.world',config['generator'],audit_messages(WORLD_INSTRUCTION,
                            {'人物正典（第三人称）':character_evidence(anchor),'人设资料':persona,'主题':seed['topic'],'当前完整事实':view['authoritative_world']['facts'],
                             '允许字段':allowed_fields(seed)}),world_schema(allowed_fields(seed),4),1300,.65)
                        planned=apply_plan(seed,normalize_world_plan(world))
                        event_facts=convert(planned)['model_view']['authoritative_world']['facts']
                        events=call_logged(out,sid+'.events',config['generator'],audit_messages(HISTORY_INSTRUCTION,
                            {'人物正典（第三人称）':character_evidence(anchor),'人设资料':persona,'主题':seed['topic'],'当前事实':event_facts,
                             '允许时刻':{k:seed[k][0]['time'] for k in ('memory_turns','recent_turns')}}),
                            history_schema(planned,event_facts),1100,.6)
                        planned=apply_history_plan(planned,events,event_facts)
                        rec['frozen_before_dialogue']=copy.deepcopy(planned); persist(path,rec)
                        chain=call_logged(out,sid+'.chain',config['generator'],audit_messages(AUTHOR_INSTRUCTION,
                            {'人物正典（第三人称）':character_evidence(anchor),'人设资料':persona,'主题':seed['topic'],'冻结事实':event_facts,
                             '冻结过去事件':planned['historical_events'],
                             '各句时间与主体':slot_material(seed)}),
                            named_chain_schema(seed),2000,.65,lambda v: decode_named_chain(v,seed))
                        chain=decode_named_chain(chain,seed)
                        for kind,key in (('memory_turns','memory'),('recent_turns','recent')):
                            for turn,text in zip(planned[kind],chain[key]): turn['text']=text
                        planned['current_user']=chain['current_user']
                        reason=duplicate_reason(planned,previous)
                        if reason: raise ValueError(reason)
                        previous.append(planned)
                        scenario=convert(planned); messages=build_messages(scenario,anchor)
                        rec.update(scenario=scenario,generation_messages=messages,
                                   input_coverage=validate_final_messages(scenario,messages),frozen_hash=freeze(planned))
                        if scenario['model_view']['authoritative_world']['source_hashes'] != config['source_hashes'][sid]:
                            raise ValueError('authoritative sources changed since preflight')
                    elif phase=='pregate':
                        row=rec['scenario']['component_source']
                        rec['history_review']=audit_history(row,'http://127.0.0.1:11434/v1',config['auditor'],out/'requests'/f'{sid}.history.json',anchor)
                        payload,_,catalog,_,mapping=compact_audit_material(rec['scenario'],{},seed['topic'],anchor)
                        payload.pop('候选回复',None)
                        persist(out/'evidence_maps'/f'{sid}.json',mapping)
                        coverage=call_logged(out,sid+'.coverage',config['auditor'],audit_messages(COVERAGE_INSTRUCTION,payload),coverage_schema(catalog),1400,0)
                        validate_evidence_requirements(row,coverage,catalog); rec['coverage_review']=coverage
                    elif phase=='reply':
                        if not rec.get('pregate_complete'): raise ValueError('history and coverage gate required before reply')
                        if freeze(rec['scenario']['component_source']) != rec['frozen_hash']: raise ValueError('frozen world/events changed')
                        validate_final_messages(rec['scenario'],rec['generation_messages'])
                        rec['target']=call_logged(out,sid+'.reply',config['generator'],rec['generation_messages'],REPLY_SCHEMA,500,.55,validate_conversational_target)
                    else:
                        payload,schema,catalog,independent,mapping=compact_audit_material(rec['scenario'],rec['target'],seed['topic'],anchor)
                        verdict=call_logged(out,sid+'.final',config['auditor'],audit_messages(AUDIT_INSTRUCTION,payload),schema,3400,0)
                        rec['audit']=verdict; rec['review']=evaluate(rec['scenario'],rec['target'],verdict,catalog,independent)
                        rec['status']='human_review' if rec['review']['approved'] else 'quarantined'
                    rec[phase+'_complete']=True
                except Exception as error:
                    rec.update(error=str(error).split('\n')[0],failed_phase=phase,status='quarantined')
                persist(path,rec)
                print(f'{phase} {index+1}/{len(seeds)}: {rec.get("status","ok")}',flush=True)
        persist(out/'results.json',list(rows.values()))
        report={'count':len(seeds),'author_complete':sum(r.get('author_complete',False) for r in rows.values()),
                'pregate_pass':sum(r.get('pregate_complete',False) for r in rows.values()),
                'valid_replies':sum(bool(r.get('target')) for r in rows.values()),
                'auto_pass':sum(r.get('review',{}).get('approved',False) for r in rows.values()),
                'released_training_rows':0,'finished_at':now()}
        persist(out/'report.json',report); persist(out/'batch_status.json',{'status':'complete',**report})
        hb.update(status='complete',phase='complete',active_sample=None,processed=len(seeds))
        print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output-root',type=Path,required=True)
    args=p.parse_args(); run(args.output_root,make_seeds())
