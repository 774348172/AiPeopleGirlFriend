"""Expand explicit author annotations into the existing formal compiler format.

No dialogue writing, type inference, approvals or model calls.
"""
import argparse
import json
from pathlib import Path
import yaml
from datetime import datetime, timedelta


def assemble(root, prefix, date):
    if (root/'input_approvals.json').exists():raise ValueError('Frozen batch is immutable')
    meta=yaml.safe_load((root/'authoring_metadata.yaml').read_text(encoding='utf-8'))
    plan=dict(sample_prefix=prefix,snapshot_date=date,world_extension='life_state_20260917',legacy_mood_case=False,
        require_history_claims=True,scene_groups=[g[:3] for g in meta['groups']],
        group_counts={g[0]:g[3] for g in meta['groups']},memory_minutes_ago={},known_profile={},
        relation_overrides={},facts={str(k):v for k,v in meta['facts'].items()},events={},
        history_claims={},task_types={},additional_prior_components=[])
    for key in ('snapshot_times', 'weather_by_sample'):
        if key in meta:
            plan[key] = {str(k): v for k, v in meta[key].items()}
    for key in ('dialogue_family_ids', 'memory_minutes_ago'):
        if key in meta:
            plan[key] = {str(k): v for k, v in meta[key].items()}
    if 'recent_minutes_ago' in meta:
        plan['recent_minutes_ago'] = meta['recent_minutes_ago']
    if 'snapshot_schedule' in meta:
        schedule = meta['snapshot_schedule']
        start = datetime.fromisoformat(schedule['start'])
        total = sum(g[3] for g in meta['groups'])
        plan['snapshot_times'] = {str(i + 1): (start + timedelta(minutes=i * schedule['step_minutes'])).isoformat() for i in range(total)}
    previous=Path(__file__).resolve().parents[1]/'训练数据'
    folders = list(dict.fromkeys(['candidate1000_20260916', 'nine_scene1000_20260917', root.parent.name,
                                 *meta.get('additional_prior_campaigns', [])]))
    if any(Path(folder).name != folder for folder in folders):
        raise ValueError('Prior campaign names must be direct directory names')
    for folder in folders:
        plan['additional_prior_components'] += [str(p.relative_to(previous.parent)) for p in sorted((previous/folder).glob('batch*/components.json')) if p.parent.resolve()!=root.resolve()]
    plan['additional_prior_components'].append('训练数据/new_life_scenes15_20260917/components.json')
    descriptions={'S':('subjective','只表达主观、假设或计划，不证明已发生事件'),
                  'R':('self_report','只证明该人物如此描述自身，不保证每次如此'),
                  'K':('knowledge_limit','仅表达本人尚未知情，不证明外部行为未发生')}
    n=0
    for filename,scene,area,count in meta['groups']:
        lines=(root/'source'/filename).read_text(encoding='utf-8').splitlines()
        if len(lines)!=count:raise ValueError('Authored line count mismatch')
        for line in lines:
            n+=1;topic,old,recent,current=line.split('|');turns=old.split('~')+recent.split('~')
            types=meta['types'].get(n, meta.get('authored_default_types'))
            if not isinstance(types, str):raise ValueError('Missing explicit author type annotation')
            if len(turns)!=8 or len(types)!=8:raise ValueError('Need eight explicit types')
            claims={}
            for i,(text,code) in enumerate(zip(turns,types)):
                kind,scope=descriptions[code]
                key=('memory_turns:' if i<4 else 'recent_turns:')+str(i%4)
                claims[key]=dict(kind=kind,scope=f'{topic}：{text}；{scope}',evidence_ids=[])
            plan['history_claims'][str(n)]=claims
            plan['task_types'][str(n)]='follow_up_question' if n in meta['fact_questions'] else 'emotion_attunement' if n in meta['emotion'] else 'idle_chat'
    if set(meta['types'])-set(range(1,n+1)):raise ValueError('Surplus type annotations')
    (root/'source'/'world_plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Assembled {n} authored chains; approvals created: 0')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--prefix',required=True);p.add_argument('--date',required=True)
    a=p.parse_args();assemble(a.root,a.prefix,a.date)
