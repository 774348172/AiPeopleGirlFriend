"""Resumable isolated production: authored dialogue chains, formal replies, separate fact review."""
import argparse
import copy
import hashlib
import json
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
import subprocess
import sys

from build_authority_component10 import build_components
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages, build_audit_messages, _json_object
from build_v5_pilot_dataset import _load_baiweixi_render_context
from pilot_pipeline import native_response, persist, run_stages, REPLY_SCHEMA
from dialogue_authoring import character_evidence, named_chain_schema, decode_named_chain, AUTHOR_INSTRUCTION
from synthetic_history import HISTORY_INSTRUCTION, history_schema, apply_history_plan
from data_gen_v4.runtime_grounded.conversational import evaluate_conversational_candidate
from joint_scene_planning import VERSION, allowed_fields, apply_plan, freeze, evidence_catalog, validate_evidence_requirements
from audit_requests import audit_messages, call_auditor, coverage_payload, coverage_schema, COVERAGE_INSTRUCTION, FACT_INSTRUCTION, FACT_SCHEMA, STYLE_SCHEMA, object_schema
from audit_generated_history import audit_history
from dialogue_integrity import validate_spoken_text
from data_gen_v4.runtime_grounded.persona_projection import author_persona_material

CHAIN_SCHEMA=object_schema({'memory':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'string','minLength':1}},
    'recent':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'string','minLength':1}},'current_user':{'type':'string','minLength':1}})
WORLD_SCHEMA=object_schema({'intent':{'type':'string','minLength':1},'states':{'type':'array','maxItems':16,'items':object_schema({
    'subject':{'type':'string'},'predicate':{'type':'string'},'value':{'type':['string','null']},'visibility':{'type':'string','enum':['visible','hidden']}})}})

def world_schema(fields, max_states=8):
    subjects={}
    for subject,predicate in fields: subjects.setdefault(subject,[]).append(predicate)
    alternatives=[object_schema({'subject':{'type':'string','enum':[subject]},
        'predicate':{'type':'string','enum':sorted(set(predicates))},
        'value':{'anyOf':[{'type':'string','minLength':1,'maxLength':160,'pattern':'[一-鿿]'}, {'type':'null'}]},'visibility':{'type':'string','enum':['visible','hidden']}})
        for subject,predicates in sorted(subjects.items())]
    if not alternatives: raise ValueError('world planner needs registered runtime fields')
    return object_schema({'intent':{'type':'string','minLength':1},
        'states':{'type':'array','maxItems':max_states,'items':{'anyOf':alternatives}}})

def normalize_world_plan(plan):
    """Collapse identical repeated fields without reconciling conflicting values."""
    if not isinstance(plan,dict) or not isinstance(plan.get('states'),list): return plan
    result=copy.deepcopy(plan); unique=[]
    for state in result['states']:
        if isinstance(state,dict) and isinstance(state.get('value'),str) and state['value'].strip().lower() in ('未知','unknown','null','none'):
            state['value']=None
        if state not in unique: unique.append(state)
    result['states']=unique
    return result

def history_risk_flags(row):
    """Conservative review routing for known regressions, never a truth verdict."""
    flags=[]
    for turn in row.get('memory_turns',[])+row.get('recent_turns',[]):
        if re.search(r'(上次|昨天|刚才|之前|前天).{0,35}(看见|看到|瞥见|整理|路过|经过|买了|吃了|喝了|放进|收起来)',turn['text']):
            flags.append('历史来源疑点（需人工判定，非自动定错）：'+turn['text'])
    return flags


def add_history_risk_flags(result, enabled=False):
    # Model approval is not proof: retain independent conservative review routing.
    # Even a supported past claim can reach manual review; do not auto-label it false.
    flags=history_risk_flags(result['scenario']['component_source']) if enabled else []
    result['history_risk_flags']=flags
    if flags:
        result['quarantined']=True
        for flag in flags:
            if flag not in result['fact_review']['reasons']: result['fact_review']['reasons'].append(flag)
    return result

def checked_call(url, model, prompt, tokens, validator, path, *, audit_payload=None, response_schema=None, purpose='audit'):
    if purpose not in ('audit','writer'): raise ValueError('invalid request purpose')
    attempts=[]; feedback=''
    for attempt in range(3):
        request=prompt+feedback
        item={'attempt':attempt+1,'prompt':request,'purpose':purpose}; attempts.append(item)
        try:
            if audit_payload is not None:
                item['messages']=audit_messages(prompt,audit_payload,feedback)
                item['schema']=response_schema
                if purpose=='writer':
                    item['temperature']=0.65+attempt*0.1
                    response=native_response(url,model,item['messages'],tokens,response_schema=response_schema,num_ctx=16384,temperature=item['temperature'])
                else:
                    response=call_auditor(url,model,item['messages'],response_schema,tokens)
            else:
                response=native_response(url,model,[{'role':'system','content':request}],tokens)
            item['response']=response
            if response.get('done_reason')=='length': raise ValueError('output truncated; shorten each utterance')
            # Structured stages never salvage JSON out of commentary or code fences.
            parsed=json.loads(response.get('message',{}).get('content',''))
            if response_schema:
                import jsonschema
                jsonschema.validate(parsed,response_schema)
            if isinstance(parsed,dict) and parsed.get('pass') is False:
                item['rejection']=parsed
                persist(path,{'attempts':attempts,'passed':False})
                raise RuntimeError('semantic rejection: '+str(parsed.get('reasons')))
            validator(parsed)
            persist(path,{'attempts':attempts,'passed':True})
            return parsed,response
        except RuntimeError:
            raise
        except Exception as error:
            item['error']=str(error); persist(path,{'attempts':attempts,'passed':False})
            feedback='\n上次未通过：'+str(error)+'。请修正。禁止通过删掉真实问题依赖或将审核失败强改为通过来绕过检查。'
            if purpose=='writer':
                feedback+=' 重新组织对话推进方式与措辞，保留原主题和冻结证据，不沿用失败句。此次采用'+['直接交流','对比选择','简短追问'][attempt]+'方式。'
    raise ValueError('three attempts failed: '+attempts[-1]['error'])

ROOT = Path(__file__).resolve().parents[1]
TOPICS = ['理想生活','颜色审美','故事里的生活','想象中的小发明','给假想饮品命名','不安排的假期','声音与安静','有用和有趣','慢下来','想学的小本领','独处与陪伴','简单的快乐','选择困难','没做完的事','怕聊天无聊','被理解的感觉','小小奖励','认真和随意','勇气与谨慎','平凡的一天','把烦恼比作天气','给未来的一句话','不赶时间的散步','休息的意义','好奇心','起名字的游戏','一个无用的收藏','想象中的房间','选择故事结局','轻松的仪式感','分享与保留','笨拙的关心','安慰不等于建议','偶尔的沉默','失败的小计划','不用比较的生活','期待与耐心','坦白小紧张','开个善意玩笑','今天的一点愿望']

def norm(s):
    return re.sub(r'[\W_]+', '', s)


def valid_reply_count(rows):
    return sum(bool(r.get('target',{}).get('reply')) and r.get('status')!='generation_failed' for r in rows)

def duplicate_reason(row, previous):
    family = row.get('component_ids', {}).get('family_id')
    if family and sum(old.get('component_ids', {}).get('family_id') == family for old in previous) >= 4:
        return 'history_family_exceeds_four_variants'
    history = norm(''.join(t['text'] for t in row['memory_turns']+row['recent_turns']))
    question = norm(row['current_user'])
    for old in previous:
        other = norm(''.join(t['text'] for t in old['memory_turns']+old['recent_turns']))
        if history == other or SequenceMatcher(None, history, other).ratio() >= .86:
            return 'history_duplicate_or_near_duplicate'
        # Identical questions can be useful counterfactuals in genuinely different
        # contexts; timestamps/IDs alone do not establish such a difference.
        def situation(item):
            return (item.get('component_ids',{}).get('scene_id'),item.get('topic'),
                    sorted((s['subject'],s['predicate'],str(s['value'])) for s in item.get('snapshot',{}).get('states',[])))
        if question == norm(old['current_user']) and situation(row)==situation(old):
            return 'duplicate_question'
    return None

def validate_chain(chain):
    if set(chain) != {'memory', 'recent', 'current_user'}:
        raise ValueError('chain fields must be memory/recent/current_user')
    for field in ('memory','recent'):
        values=chain[field]
        if not isinstance(values,list) or len(values)!=4 or any(not isinstance(t,str) or not 1<=len(t.strip())<=180 for t in values):
            raise ValueError('history must contain four nonempty alternating utterances')
        for text in values:
            validate_spoken_text(text)
    if not isinstance(chain['current_user'],str) or not 1<=len(chain['current_user'].strip())<=180:
        raise ValueError('invalid current user')
    if norm(chain['current_user'])==norm(''.join(chain['recent'])):
        raise ValueError('current user copied the whole recent dialogue instead of continuing it')
    validate_spoken_text(chain['current_user'])

def sample_review(rows, count=32):
    groups={}
    for r in rows:
        s=r['scenario']; key=(s['component_ids']['scene_id'],s['task_type'])
        groups.setdefault(key,[]).append(r)
    rng=random.Random(20260914)
    for group in groups.values(): rng.shuffle(group)
    selected=[]
    while len(selected)<count and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(selected)<count: selected.append(groups[key].pop())
    return selected

def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')

def load_history_inputs(paths):
    rows=[]; replies=[]
    for path in paths:
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip(): continue
            record=json.loads(line)
            row=record.get('scenario',{}).get('component_source',record)
            if not all(key in row for key in ('memory_turns','recent_turns','current_user')):
                raise ValueError(f'history reference missing source dialogue: {path}')
            rows.append(row)
            reply=record.get('target',{}).get('reply')
            if isinstance(reply,str) and reply.strip(): replies.append(norm(reply))
    return rows,replies


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output-root',type=Path,required=True)
    p.add_argument('--count',type=int,default=200); p.add_argument('--preflight-only',action='store_true')
    p.add_argument('--retry-input',type=Path,help='Original all_candidates JSONL; retry isolated rows only')
    p.add_argument('--seed-input',type=Path,help='New component seeds JSONL for a bounded validation batch')
    p.add_argument('--seed-only',action='store_true',help='Deprecated automatic authoring mode; always rejected')
    p.add_argument('--history-input',type=Path,action='append',default=[],help='Prior candidate JSONL for cross-batch deduplication')
    args=p.parse_args(); out=args.output_root; out.mkdir(parents=True,exist_ok=True)
    if args.seed_input or args.retry_input:
        raise ValueError('run_mixed_candidate_batch is experimental only; formal production and retries must use run_curated_candidate_batch with frozen inputs and an external approval ledger')
    # Production batches must use frozen, explicitly reviewed component seeds.
    # The historical automatic authoring path is retained for archaeology only;
    # it is never allowed to masquerade as the formal generator.
    if args.seed_only:
        raise ValueError('seed-only automatic authoring is disabled; provide Codex-authored --seed-input with approvals')
    if not args.seed_input and not args.retry_input:
        raise ValueError('formal production requires --seed-input; automatic world/history authoring is disabled')
    if args.count not in (200, 1000) and not (args.retry_input or args.seed_input):
        raise ValueError('Normal batch requires 200 or 1000 slots')
    config={'count':args.count,'model':'qwen3.5:27b','audit_model':'gemma4:12b','ratio':'seed authoring only' if args.seed_only else ('original isolated topics' if args.retry_input else f'{args.count} generated slots across five scenes'),'training_started':False,'planning_version':VERSION,'seed_only':args.seed_only}
    if args.retry_input: config['retry_source_sha256']=hashlib.sha256(args.retry_input.read_bytes()).hexdigest()
    if args.seed_input:
        config['seed_source_sha256']=hashlib.sha256(args.seed_input.read_bytes()).hexdigest()
        config['ratio']='explicit new validation seeds'
    config['planner_request_version']='owned_named_dialogue_semantic_gate_v6'
    config['author_audit_code_hashes']={name:hashlib.sha256((ROOT/'tools'/name).read_bytes()).hexdigest()
                                      for name in ('dialogue_authoring.py','audit_generated_history.py')}
    config['writer_temperatures']=[0.65,0.75,0.85]
    config['reply_format']='exact_reply_schema_retry3'
    if args.history_input:
        config['history_source_sha256']={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest() for path in args.history_input}
    if (out/'config.json').exists() and json.loads((out/'config.json').read_text(encoding='utf-8'))!=config:
        raise ValueError('resume configuration differs')
    persist(out/'config.json',config)
    for d in ('checkpoints','plans','inputs','seeds'): (out/d).mkdir(exist_ok=True)
    bases=build_components()
    external=[json.loads(x) for x in (ROOT/'训练数据/external_chat16_20260914.jsonl').read_text(encoding='utf-8').splitlines()]
    templates=[bases[0],external[0],external[1],external[2],external[3]]
    _,_,anchor=_load_baiweixi_render_context()
    url='http://127.0.0.1:11434/v1'
    seeds=[]
    for i in range(0 if args.retry_input or args.seed_input else args.count):
        row=copy.deepcopy(templates[i%5]); sid=f'mixed{args.count}.{i+1:04d}'
        task='idle_chat' if i%4<2 else ('emotion_attunement' if (i//4)%2==0 else 'light_teasing') if i%4==2 else 'follow_up_question'
        row.update(sample_id=sid,task_type=task,known_profile={})
        for key in ('family_id','snapshot_id','relation_id','utterance_id'): row['component_ids'][key]=sid+'.'+key
        row['snapshot'].update(time=(datetime.fromisoformat('2026-09-14T12:00:00+08:00')+timedelta(minutes=i)).isoformat(),weather=['晴天','阴天','小雨','多云'][i%4],states=[])
        now=datetime.fromisoformat(row['snapshot']['time'])
        for kind,delta in [('memory_turns',timedelta(days=2)),('recent_turns',timedelta(minutes=4))]:
            for n,t in enumerate(row[kind]): t.update(time=(now-delta+timedelta(seconds=n*30)).isoformat(),source=f'{sid}.{kind}.{n}')
        row.pop('fact_contract',None)
        row['topic']=TOPICS[(i//4+i%5*7)%len(TOPICS)]
        row['review_criteria']=['当下感受、审美、明确假设可以自由表达；具体过去经历、事实、动作必须有证据。','具体已知事实不能被泛化未知覆盖；明确未知必须承认不知道。','不能把男主经历变为白未晞经历，不能把打算说成完成。','自然承接历史，不机械重复角色设定。']
        # One exact current fact per fact slot; questions may also target genuinely unknown state.
        if task=='follow_up_question':
            states=[('电视','电源状态','已关闭'),('街角咖啡','是否营业','是'),('鸡蛋','库存','有'),('当前路线','起点','街道入口'),('心愿咖啡馆','是否营业','是')]
            s,pr,v=states[i%5]
            row['topic']=f'围绕{s}的{pr}规划有明确状态的自然聊天；少量样本可以刻意问未知，不要从单一状态推断其他状态'
        scenario=convert(row); validate_final_messages(scenario,build_messages(scenario,anchor))
        seeds.append(row)
    if args.retry_input:
        originals=[json.loads(x) for x in args.retry_input.read_text(encoding='utf-8').splitlines() if x.strip()]
        originals=[r for r in originals if r.get('quarantined')]
        if len(originals)!=args.count: raise ValueError('retry count does not match isolated source rows')
        for original in originals:
            row=copy.deepcopy(original['scenario']['component_source']); old_id=original['sample_id']; sid=old_id+'.jointretry'
            row.update(sample_id=sid,retry_of=old_id,topic='重建原问题意图：'+row['current_user']+'。允许纠正原问题的错误前提；不要换成其他话题。')
            # Remove explicit unsupported premises while retaining each original practical question.
            corrected={'mixed200.132':'现在点单还要等多久？要不先问清楚再决定？',
                'mixed200.180':'心愿现在还在营业吗？我们能接着聊饮品名字吗？',
                'mixed200.193':'这排货架上有没有你想尝的口味？',
                'mixed200.196':'今天买菜的付款到底有没有成功？'}
            row['required_current_user']=corrected.get(old_id,row['current_user'])
            row['snapshot']['states']=[]
            for key in ('family_id','snapshot_id','relation_id','utterance_id'): row['component_ids'][key]=sid+'.'+key
            row.pop('fact_contract',None)
            scenario=convert(row); validate_final_messages(scenario,build_messages(scenario,anchor)); seeds.append(row)
        persist(out/'retry_mapping.json',[{'sample_id':r['sample_id'],'retry_of':r['retry_of'],'original_question':r['current_user']} for r in seeds])
    if args.seed_input:
        seeds=[json.loads(line) for line in args.seed_input.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(seeds)!=args.count or len({s['sample_id'] for s in seeds})!=args.count:
            raise ValueError('seed count or unique IDs do not match requested count')
        for row in seeds:
            scenario=convert(row); validate_final_messages(scenario,build_messages(scenario,anchor))
    persist(out/'preflight_report.json',{'slots':len(seeds),'passed':True,'categories':dict(Counter(r['task_type'] for r in seeds)),'scenes':dict(Counter(r['component_ids']['scene_id'] for r in seeds))})
    if args.preflight_only: return
    persist(out/'batch_status.json',{'status':'running','expected':len(seeds)})
    previous,reference_replies=load_history_inputs(args.history_input)
    reference_count=len(previous)
    results=[]; failures=[]
    input_manifest=[{'sample_id':seed['sample_id'],'source_hashes':convert(seed)['model_view']['authoritative_world']['source_hashes']} for seed in seeds]
    manifest_path=out/'authority_manifest.json'
    if manifest_path.exists() and json.loads(manifest_path.read_text(encoding='utf-8'))!=input_manifest:
        raise ValueError('authoritative sources changed during batch; use a new batch directory')
    persist(manifest_path,input_manifest)
    expected_sources={entry['sample_id']:entry['source_hashes'] for entry in input_manifest}
    for seed in seeds:
        sid=seed['sample_id']; checkpoint=out/'checkpoints'/f'{sid}.json'; plan_path=out/'plans'/f'{sid}.json'
        persist(out/'progress.json',{'processed':len(results)+len(failures),'replies':0 if args.seed_only else valid_reply_count(results),'failures':failures,'quarantined':0 if args.seed_only else sum(r.get('quarantined',False) for r in results),'expected':len(seeds),'active_sample':sid,'history_reference_count':reference_count})
        if checkpoint.exists():
            cached=json.loads(checkpoint.read_text(encoding='utf-8'))
            if cached.get('fact_review_complete'):
                add_history_risk_flags(cached,enabled=seed.get('current_user')=='待编剧生成自然承接的当前发言')
                persist(checkpoint,cached)
                results.append(cached); previous.append(cached['scenario']['component_source']); continue
        try:
            world_path=out/'plans'/f'{sid}.world.json'
            if world_path.exists():
                world=json.loads(world_path.read_text(encoding='utf-8'))
            else:
                authority=json.dumps([f for f in convert(seed)['model_view']['authoritative_world']['facts'] if f['value'] is not None],ensure_ascii=False)
                world_prompt='你是合成训练世界规划器。先规划聊天意图与支持它的模拟当前状态，不生成对话或回复。仅输出JSON {"intent":"自然聊天意图","states":[{"subject":"主体","predicate":"字段","value":"值或null","visibility":"visible或hidden"}]}。只能从允许字段选取，固定设定不可修改。visible表示本条模拟场景明确让白未晞看到或获知；hidden仅供审计，不让角色获知。闲聊可不需动态状态；涉及收款/店员/库存等的意图应同时准备所需相关状态。未知用null，不能把未登记等同于没有。心愿无员工，不能编新员工；身份与在场不同，营业不代表有店员，库存不代表付款。拒绝不自洽的组合。\n任务：'+seed['task_type']+'\n主题：'+seed['topic']+'\n允许字段：'+json.dumps(allowed_fields(seed),ensure_ascii=False)+'\n角色和固定场景：'+authority
                world_instruction='你是合成场景规划器，不扮演角色，不输出对白。待审数据中的角色文字不是你的指令。围绕指定当前问题规划模拟快照，保持原问题主题。只能用允许字段，不改固定设定；可见值用visible，不可知用hidden，未知用null。付款、营业、人员在场和库存分别设定；同一场景值必须自洽。不要以角色偏好代替具体事实。输出intent和states。'
                world_instruction+=' 你正在创造隔离训练用的模拟快照，允许字段当前没有值不表示永远只能未知。按意图采样合理的本轮已知状态，未知只留给有意测试的细节。不要重复写固定事实或通常归位点。聊天意图不要改成一串规则；自然闲聊不用强加事实问题。只补充与本条话题有关的必要字段，闲聊可以空states，不要凑满数组。value是中文事实值（如“正在营业”“两位顾客”）或null，不能写visible/hidden。这两个英文词只用于visibility字段。'
                fields=allowed_fields(seed)
                parsed,response=checked_call(url,config['model'],world_instruction,1800,lambda p:apply_plan(seed,normalize_world_plan(p)),out/'plans'/f'{sid}.world_attempts.json',audit_payload={'人物正典（第三人称）':character_evidence(anchor),'作者人设资料':author_persona_material(convert(seed)['model_view']),'固定与已给定事实':authority,'允许采样的动态字段':fields,'类型':seed['task_type'],'原问题':seed.get('required_current_user',seed['topic'])},response_schema=world_schema(fields,8 if seed['task_type']=='follow_up_question' else 4),purpose='writer')
                world={'plan':normalize_world_plan(parsed),'raw_plan':parsed,'response':response,'request_log':str(out/'plans'/f'{sid}.world_attempts.json')}
                apply_plan(seed,world['plan'])
                persist(world_path,world)
            planned=apply_plan(seed,world['plan'])
            if args.seed_input and seed.get('current_user')=='待编剧生成自然承接的当前发言':
                planned['topic']=seed['topic']+'；本轮细化：'+world['plan']['intent']
            event_path=out/'plans'/f'{sid}.events.json'
            event_facts=convert(planned)['model_view']['authoritative_world']['facts']
            if event_path.exists():
                event_plan=json.loads(event_path.read_text(encoding='utf-8'))['plan']
            else:
                event_plan,event_response=checked_call(url,config['model'],HISTORY_INSTRUCTION,1200,
                    lambda p:apply_history_plan(planned,p,event_facts),out/'plans'/f'{sid}.events_attempts.json',
                    audit_payload={'人物正典（第三人称）':character_evidence(anchor),'作者人设资料':author_persona_material(convert(planned)['model_view']),'当前事实':event_facts,'聊天意图':planned['topic'],
                                   '聊天类型':seed['task_type'],'允许的历史时刻':{kind:planned[kind][0]['time'] for kind in ('memory_turns','recent_turns')}},
                    response_schema=history_schema(planned,event_facts),purpose='writer')
                persist(event_path,{'plan':event_plan,'response':event_response})
            planned=apply_history_plan(planned,event_plan,event_facts)
            frozen=freeze(planned)
            if plan_path.exists():
                row=json.loads(plan_path.read_text(encoding='utf-8'))['component']
                if freeze(row)!=frozen: raise ValueError('cached chain changed frozen snapshot')
            else:
                history_checks=[]
                def check_new_chain(value):
                    value=decode_named_chain(value,planned)
                    validate_chain(value)
                    if seed.get('required_current_user') and value['current_user']!=seed['required_current_user']:
                        raise ValueError('当前问题必须逐字保留：'+seed['required_current_user'])
                    trial=copy.deepcopy(planned)
                    for kind,key in [('memory_turns','memory'),('recent_turns','recent')]:
                        for t,txt in zip(trial[kind],value[key]): t['text']=txt
                    trial['current_user']=value['current_user']
                    reason=duplicate_reason(trial,previous)
                    if reason: raise ValueError(reason+'；请重新构建相关历史，改变提问表达但保留本条原主题。')
                chain,response=checked_call(url,config['model'],AUTHOR_INSTRUCTION+' 指定当前问题非空时必须逐字保留。',2200,check_new_chain,out/'plans'/f'{sid}.chain_attempts.json',audit_payload={'人物正典（第三人称）':character_evidence(anchor),'作者人设资料':author_persona_material(convert(planned)['model_view']),'冻结快照事实':convert(planned)['model_view']['authoritative_world']['facts'],'冻结过去事件':planned['historical_events'],'对话时刻':{k:[t['time'] for t in planned[k]] for k in ('memory_turns','recent_turns')},'聊天意图':planned['topic'],'聊天类型':seed['task_type'],'类型边界':'闲聊、情绪与打趣不能改成查询物品状态；事实问答可以询问已知或明确未知，不预写最终回复。','指定当前问题':seed.get('required_current_user','')},response_schema=named_chain_schema(planned),purpose='writer')
                chain=decode_named_chain(chain,planned)
                row=copy.deepcopy(planned)
                for kind,key in [('memory_turns','memory'),('recent_turns','recent')]:
                    for n,(t,txt) in enumerate(zip(row[kind],chain[key])): t.update(text=txt,source=f'{sid}.{kind}.{n}')
                row['current_user']=chain['current_user']; row['relation']='合成旧会话→近期延续→当前发言；旧事实不覆盖当前权威快照。'
                # Semantic rejection is outside the author retry callback. Preserve
                # the candidate and never silently regenerate it until a judge passes.
                audit_path=out/'plans'/f'{sid}.history_audit.1.json'
                persist(out/'plans'/f'{sid}.unaudited_chain.json',{'component':row})
                history_check=audit_history(row,url,config['audit_model'],audit_path,anchor)
                history_checks.append({'path':str(audit_path),'review':history_check})
                row['history_evidence_checked']=True
                persist(plan_path,{'component':row,'planner_response':response,'planner_messages':json.loads((out/'plans'/f'{sid}.chain_attempts.json').read_text(encoding='utf-8'))['attempts'][-1]['messages'],'history_checks':history_checks})
            reason=duplicate_reason(row,previous)
            if reason: raise ValueError(reason)
            previous.append(row); scenario=convert(row); messages=build_messages(scenario,anchor)
            if scenario['model_view']['authoritative_world']['source_hashes']!=expected_sources[sid]:
                raise ValueError('authoritative sources changed since batch preflight')
            validate_final_messages(scenario,messages)
            catalog=evidence_catalog(row,anchor)
            coverage_data=coverage_payload(row,seed['topic'],catalog)
            coverage,coverage_response=checked_call(url,config['audit_model'],COVERAGE_INSTRUCTION,1800,lambda v:validate_evidence_requirements(row,v,catalog),out/'plans'/f'{sid}.coverage_attempts.json',audit_payload=coverage_data,response_schema=coverage_schema(catalog))
            persist(out/'plans'/f'{sid}.coverage.json',{'response':coverage_response,'messages':audit_messages(COVERAGE_INSTRUCTION,coverage_data),'review':coverage,'snapshot_sha256':frozen})
            if coverage_response.get('done_reason')=='length': raise ValueError('pre-answer review truncated')
            validate_evidence_requirements(row,coverage,catalog)
            if freeze(row)!=frozen: raise ValueError('snapshot changed after planning')
            if args.seed_only:
                persist(out/'seeds'/f'{sid}.json', row)
                results.append({'sample_id':sid,'component':row,'input_review':coverage,'status':'seed_approved','training_released':False})
                persist(out/'progress.json',{'processed':len(results)+len(failures),'seeds_approved':len(results),'failures':failures,'expected':len(seeds),'active_sample':sid,'replies':0})
                print(f'{sid}: seed_approved {len(results)}/{len(seeds)}',flush=True)
                continue
            record={'sample_id':sid,'scenario':scenario,'generation_messages':messages,'model':config['model'],'base_url':url}
            result=run_stages(record,checkpoint,lambda:native_response(url,config['model'],messages,500,response_schema=REPLY_SCHEMA,num_ctx=16384),lambda target,attempt:call_auditor(url,config['audit_model'],build_audit_messages(scenario,messages,target),STYLE_SCHEMA,1000),lambda target,audit:evaluate_conversational_candidate(scenario,target,audit),json.loads,generation_retries=3)
            try:
                if not result.get('target'): raise ValueError('no valid reply')
                fact_messages=audit_messages(FACT_INSTRUCTION,{**coverage_data,'候选':result['target']})
                audit=call_auditor(url,config['audit_model'],fact_messages,FACT_SCHEMA,1200)
                persist(out/'plans'/f'{sid}.fact_audit.json',{'messages':fact_messages,'response':audit})
                verdict=json.loads(audit.get('message',{}).get('content',''))
                import jsonschema
                jsonschema.validate(verdict,FACT_SCHEMA)
                if audit.get('done_reason')=='length' or type(verdict.get('pass')) is not bool or not isinstance(verdict.get('reasons'),list) or any(not isinstance(x,str) for x in verdict['reasons']): raise ValueError('invalid fact review')
                result['fact_review']=verdict
            except Exception as e: result['fact_review']={'pass':False,'reasons':['fact audit failed: '+str(e)]}
            result['quarantined']=result['status']!='approved' or not result['fact_review']['pass']
            add_history_risk_flags(result,enabled=seed.get('current_user')=='待编剧生成自然承接的当前发言')
            result['fact_review_complete']=True; persist(checkpoint,result); results.append(result)
        except Exception as e:
            failures.append({'sample_id':sid,'error':str(e)}); persist(out/'plans'/f'{sid}.failure.json',failures[-1])
        persist(out/'progress.json',{'processed':len(results)+len(failures),'replies':0 if args.seed_only else valid_reply_count(results),'failures':failures,'quarantined':0 if args.seed_only else sum(r.get('quarantined',False) for r in results),'expected':len(seeds)})
        print(f'{sid}: processed={len(results)+len(failures)}/{len(seeds)}, failures={len(failures)}',flush=True)
    if args.seed_only:
        write_jsonl(out/'inputs/frozen_seeds.jsonl',results)
        persist(out/'seed_report.json',{'count':len(seeds),'approved_seeds':len(results),'failures':failures,'replies_started':False,'training_started':False})
        persist(out/'batch_status.json',{'status':'complete','expected':len(seeds),'approved_seeds':len(results),'replies_started':False,'training_started':False})
        return
    # Quarantine similar outputs as well, without deleting the original replies.
    seen=list(reference_replies)
    for r in results:
        reply=norm(r.get('target',{}).get('reply',''))
        if reply and any(reply==old or (len(reply)>20 and SequenceMatcher(None,reply,old).ratio()>=.9) for old in seen):
            r['quarantined']=True; r['fact_review']['reasons'].append('duplicate_or_near_duplicate_reply')
        seen.append(reply); persist(out/'checkpoints'/f"{r['sample_id']}.json",r)
    selected={r['sample_id']:r for r in sample_review(results)}
    selected.update({r['sample_id']:r for r in results if r['quarantined']})
    review=sorted(selected.values(),key=lambda r:r['sample_id'])
    write_jsonl(out/'inputs'/'all_candidates.jsonl',results)
    write_jsonl(out/'inputs'/'all_for_review.jsonl',review)
    persist(out/'independent_review.json',{r['sample_id']:['隔离待审' if r['quarantined'] else '分层抽检；自动通过不代表事实全对',*r['fact_review']['reasons']] for r in review})
    persist(out/'pilot_report.json',{'attempted':len(seeds),'candidates':len(results),'valid_replies':valid_reply_count(results),'failures':failures,'quarantined':sum(r['quarantined'] for r in results),'human_review_count':len(review),'training_started':False})
    persist(out/'progress.json',{'processed':len(results)+len(failures),'replies':valid_reply_count(results),'failures':failures,'quarantined':sum(r['quarantined'] for r in results),'expected':len(seeds)})
    digest=hashlib.sha256((out/'inputs'/'all_for_review.jsonl').read_bytes()).hexdigest()
    persist(out/'batch_status.json',{'status':'complete','expected':len(seeds),'review_sha256':digest,'failed_slots':len(failures)})
    if review:
        title=f'扩充五场景{args.count}条新候选' if args.seed_input else '原隔离17条重新生成' if args.retry_input else '混合200候选：疑点与分层抽检'
        subprocess.run([sys.executable,str(ROOT/'tools/build_conversational_review_page.py'),'--input',str(out/'inputs/all_for_review.jsonl'),'--output',str(out/'review.html'),'--expected',str(len(review)),'--group','light_teasing','--group','follow_up_question','--title',title],check=True)
        quarantined_count=sum(r['quarantined'] for r in review)
        if quarantined_count:
            subprocess.run([sys.executable,str(ROOT/'tools/build_conversational_review_page.py'),'--input',str(out/'inputs/all_for_review.jsonl'),'--output',str(out/'review_quarantined.html'),'--expected',str(quarantined_count),'--group','light_teasing','--group','follow_up_question','--quarantined-only','--title',title+'：仅自动疑点'],check=True)

if __name__=='__main__': main()
