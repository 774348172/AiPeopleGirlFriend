"""Pre-answer synthetic world planning and evidence binding. Never backfill from replies."""
import copy
import hashlib
import json
from convert_component_to_game_reply import convert

VERSION = 'joint_scene_v4_frozen_history'

def evidence_catalog(row, anchor):
    """IDs are allocated by code from actual inputs, never invented by the reviewer."""
    entries=[]
    for f in convert(row)['model_view']['authoritative_world']['facts']:
        entries.append({'id':'world:'+f['fact_id'],'kind':'world','subject':f['subject'],
                        'predicate':f['predicate'],'value':f['value'],'source':f['source'],
                        **({'known_since':f['known_since']} if 'known_since' in f else {})})
    from dialogue_authoring import character_evidence
    entries.extend(character_evidence(anchor))
    for kind in ('memory_turns','recent_turns'):
        for i,t in enumerate(row[kind]): entries.append({'id':f'{kind}:{i}','check_key':f'{kind}:{i}','kind':'dialogue',**t})
    for event in row.get('historical_events', []):
        entries.append({'id':'event:'+event['source'], 'kind':'event', **event})
    return entries

def validate_evidence_requirements(row, review, catalog):
    if not isinstance(review,dict) or set(review)!={'pass','reasons','requirements','topic_check'}:
        raise ValueError('invalid pre-answer review schema')
    topic=review['topic_check']
    if not isinstance(topic,dict) or set(topic)!={'original_focus','current_focus','aligned'}:
        raise ValueError('missing independent intent comparison')
    if topic['aligned'] is not True:
        raise ValueError('current question diverged from planned intent: '+str(topic))
    if review['pass'] is not True or not isinstance(review['reasons'],list) or not all(isinstance(s,str) for s in review['reasons']) or not isinstance(review['requirements'],list):
        raise ValueError('pre-answer rejection: '+str(review.get('reasons')))
    if review['reasons']:
        raise ValueError('pre-answer approval contradicts rejection reasons')
    if len(review['requirements']) > 8 or len(review['reasons']) > 3:
        raise ValueError('pre-answer review exceeds bounded evidence/reason budget')
    table={e['id']:e for e in catalog}
    for req in review['requirements']:
        if not isinstance(req,dict) or set(req)!={'evidence_id','mode'}: raise ValueError('reference must use catalog evidence_id and mode')
        if req['evidence_id'] not in table: raise ValueError('unknown evidence_id')
        evidence=table[req['evidence_id']]
        expected='intentional_unknown' if evidence['kind']=='world' and evidence['value'] is None else 'known'
        if req['mode']!=expected: raise ValueError('evidence mode mismatch')
    if row['task_type']=='follow_up_question' and not review['requirements']: raise ValueError('fact question needs evidence')
    return review

def freeze(row):
    return hashlib.sha256(json.dumps({'snapshot':row['snapshot'],
        'historical_events':row.get('historical_events',[])},ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def allowed_fields(row):
    facts=convert(row)['model_view']['authoritative_world']['facts']
    allowed={(f['subject'],f['predicate']) for f in facts if f['value'] is None}
    # Concrete item states have narrower scope than scene-wide inventory unknowns.
    scene=row['component_ids']['scene_id']
    if scene.endswith('neighborhood_convenience_store'):
        allowed.add(('鸡蛋','库存'))
    if scene.endswith('neighborhood_cafe'):
        allowed.update({('街角咖啡','店员是否在场'),('街角咖啡','收银负责人'),('街角咖啡','店员当前活动'),('街角咖啡','预计等待时间')})
    if scene.endswith('neighborhood_convenience_store'):
        allowed.add(('邻里便利店','收银负责人'))
    allowed.add(('白未晞的纸箱','箱内物品'))
    # Do not let a planner turn withheld private profile facts into known facts.
    return sorted(k for k in allowed if k[0] not in ('男主','男主资料','男主已知设定'))

def apply_plan(seed, plan):
    if not isinstance(plan,dict) or set(plan)!={'intent','states'} or not isinstance(plan['intent'],str) or not plan['intent'].strip():
        raise ValueError('joint plan requires intent and states')
    if not isinstance(plan['states'],list) or len(plan['states'])>16:
        raise ValueError('invalid planned states')
    allowed=set(allowed_fields(seed)); seen=set(); row=copy.deepcopy(seed)
    row['snapshot']['states']=[]
    for state in plan['states']:
        if not isinstance(state,dict) or set(state)!={'subject','predicate','value','visibility'}:
            raise ValueError('state requires subject/predicate/value/visibility')
        key=(state['subject'],state['predicate'])
        if key not in allowed or key in seen: raise ValueError('unregistered, fixed or duplicate state: '+str(key))
        seen.add(key)
        value=state['value']; visibility=state['visibility']
        if value is not None and (not isinstance(value,str) or not value.strip() or len(value)>160 or value.strip().lower() in ('未知','unknown','visible','hidden','null','none')):
            raise ValueError('unknown must be null; values must be short strings')
        if visibility not in ('visible','hidden'): raise ValueError('invalid visibility')
        # Hidden values live only in planning audit, never in model_view/runtime_snapshot.
        row['snapshot']['states'].append({'subject':key[0],'predicate':key[1],
            'value':value if visibility=='visible' else None,
            'source':seed['sample_id']+('.synthetic_visible_snapshot' if visibility=='visible' else '.unknown_to_character')})
    row['topic']=plan['intent']
    row['joint_plan_version']=VERSION
    return row

def validate_requirements(row, review):
    if not isinstance(review,dict) or set(review)!={'pass','reasons','requirements'}:
        raise ValueError('invalid pre-answer review schema')
    if review['pass'] is not True or not isinstance(review['reasons'],list) or not isinstance(review['requirements'],list):
        raise ValueError('pre-answer coverage rejected: '+str(review.get('reasons')))
    facts=convert(row)['model_view']['authoritative_world']['facts']
    table={(f['subject'],f['predicate']):f['value'] for f in facts}
    for req in review['requirements']:
        if not isinstance(req,dict) or set(req)!={'subject','predicate','mode'}: raise ValueError('invalid requirement')
        key=(req['subject'],req['predicate'])
        if key not in table: raise ValueError('required fact not in final model context: '+str(key))
        if req['mode'] not in ('known','intentional_unknown'): raise ValueError('invalid requirement mode')
        if (table[key] is None)!=(req['mode']=='intentional_unknown'):
            raise ValueError('known/unknown requirement mismatches evidence: '+str(key))
    if row['task_type']=='follow_up_question' and not review['requirements']:
        raise ValueError('fact question requires evidence references')
    return review
