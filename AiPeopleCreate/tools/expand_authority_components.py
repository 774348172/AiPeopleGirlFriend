"""Expand ten authored episodes into 50 families and 200 controlled variants."""
from __future__ import annotations
import copy, json
from datetime import datetime, timedelta
from pathlib import Path
from build_authority_component10 import build_components

OUT=Path(__file__).resolve().parents[1]/'训练数据/authority_components200_20260913'
WEATHER=['晴天','阴天','小雨','多云','闷热']
AREA=['living_room','kitchen','small_balcony','entrance','master_bedroom']

# Each source episode gets four genuinely different, topic-bound utterances.
# Punctuation-only suffixes previously produced near-duplicates and allowed
# questions to drift away from the facts in the snapshot.
QUESTION_VARIANTS = {
    '微波炉剩余时间': ['它还要转几分钟啊？', '这次加热大概还剩多久？', '我现在能把饭取出来了吗？', '微波炉还在运行，剩余时间有记录吗？'],
    '洗衣机程序': ['嗯，现在洗到哪一步了？', '这桶衣服是在漂洗吗？', '它还没洗完，对吧？', '当前的洗衣程序能确认吗？'],
    '备用钥匙新位置': ['备用钥匙现在放哪儿了？', '我出门前该去哪里找那把备用钥匙？', '茶几上的是备用钥匙吗？', '备用钥匙的新位置有记录吗？'],
    '电视当前状态': ['电视现在是关着的吗？', '我想看会儿电视，需要先开机吗？', '屏幕这会儿有画面吗？', '现在能确认电视的电源状态吗？'],
    '热水量未知': ['里面的热水够不够洗完啊？', '现在洗澡的话，热水量能撑住吗？', '热水器开着就代表热水够用吗？', '当前剩余热水量有记录吗？'],
    '男主已告知口味': ['你还记得我平常喜欢什么口味吗？', '今晚按我说过的口味来做，可以吗？', '我能吃辣、喜欢油腻这点你记得吗？', '你记得我是哪里人、平时吃什么口味吗？'],
    '冰箱食材未知': ['冰箱里现在还有鸡蛋吗？', '要煮面的话，现有食材能确认吗？', '冰箱通着电就能说明里面有什么吗？', '你知道冰箱里此刻有哪些食材吗？'],
    '厨房窗户计划与结果': ['厨房窗户现在关上了吗？', '我之前说要关窗，完成情况有记录吗？', '出门前能确认厨房窗户的状态吗？', '只知道我计划关窗，能当作已经关了吗？'],
    '借来的书当前位置': ['那本借来的书现在收在哪儿？', '我去书架第二层找得到它吗？', '它已经从床头挪到书架了吗？', '借来的书当前有明确位置记录吗？'],
    '白围巾位置': ['白围巾现在在哪儿？', '我出门要带围巾，应该去哪里拿？', '主卧衣柜抽屉里的是那条白围巾吗？', '白围巾的当前位置有记录吗？'],
}

def make_row(base, family, variant, now):
    row=copy.deepcopy(base); row['sample_id']=f'authority.component.{family:03d}.v{variant+1}'
    ids=row['component_ids']; ids['family_id']=f'authority.family.{family:03d}'; ids['snapshot_id']=row['sample_id']+'.snapshot'; ids['relation_id']=row['sample_id']+'.relation'; ids['utterance_id']=row['sample_id']+'.utterance'
    row['snapshot']['time']=now.isoformat(); row['snapshot']['weather']=WEATHER[(family+variant)%len(WEATHER)]
    # Keep the episode's physical area tied to its authored equipment/object
    # facts. Time and weather provide independent world variants without
    # moving a microwave, key, or person into an unrelated room.
    row['snapshot']['area']=base['snapshot']['area']
    for person in row['snapshot']['people'].values(): person['area']=row['snapshot']['area']
    for kind in ('memory_turns','recent_turns'):
        start=now-timedelta(days=2 if kind=='memory_turns' else 0, minutes=3 if kind=='recent_turns' else 0)
        for n,t in enumerate(row[kind]): t['time']=(start+timedelta(seconds=n*30)).isoformat(); t['source']=f"{row['sample_id']}.{kind}.{n}"
    # ``known_profile`` points to exact male utterance source IDs.  Repoint
    # those references after rewriting turn IDs; leaving the base fixture IDs
    # here makes conversion fail (or, worse, detach a profile fact from its
    # evidence) for every expanded variant.
    if row.get('known_profile'):
        memory_by_index = {i: t['source'] for i, t in enumerate(row['memory_turns'])}
        for path, source in list(row['known_profile'].items()):
            try:
                index = int(source.rsplit('.', 1)[-1])
            except (TypeError, ValueError):
                raise ValueError(f'known_profile 来源必须指向 memory_turns 索引：{path}={source}')
            if index not in memory_by_index:
                raise ValueError(f'known_profile 来源索引越界：{path}={source}')
            row['known_profile'][path] = memory_by_index[index]
    questions = QUESTION_VARIANTS.get(base['topic'])
    if not questions:
        raise ValueError(f'no curated question variants for {base["topic"]}')
    row['current_user']=questions[variant % len(questions)]
    row['review_criteria']=base['review_criteria']+[f'本变体时间为{now.isoformat()}、天气为{row["snapshot"]["weather"]}；天气不得改变未登记设备状态。']
    row['topic']=base['topic']+f'（场景变体{variant+1}）'
    return row

def build_expanded():
    bases=build_components(); rows=[]; family=1
    for base in bases:
        for variant in range(5):
            rows.append(make_row(base,family,variant,datetime.fromisoformat(f'2026-09-13T{9+family%12:02d}:{(variant*13)%60:02d}:00+08:00'))); family+=1
    # Four expression/state passes make 200 candidates while retaining 50 family IDs.
    expanded=[]
    for row in rows:
        for variant in range(4):
            clone=copy.deepcopy(row); clone['sample_id']=row['sample_id']+f'.p{variant+1}'
            clone['component_ids']['utterance_id']=clone['sample_id']+'.utterance'
            # Keep the same family/context while selecting a distinct,
            # semantically valid utterance for this candidate.
            base_topic = row['topic'].split('（', 1)[0]
            clone['current_user']=QUESTION_VARIANTS[base_topic][variant]
            clone['topic']=row['topic']+f'·问法{variant+1}'
            clone['variant_kind']='question_form'
            expanded.append(clone)
    validate_expansion(expanded)
    return expanded


def validate_expansion(rows):
    """Fail closed before writing a batch that is structurally repetitive or
    has broken evidence links.  This runs before any model invocation."""
    if len(rows) != 200:
        raise ValueError(f'扩展结果必须为 200 条，实际 {len(rows)} 条')
    sample_ids = [r.get('sample_id') for r in rows]
    if any(not x for x in sample_ids) or len(set(sample_ids)) != len(sample_ids):
        raise ValueError('sample_id 缺失或重复')
    families = {}
    for row in rows:
        family = row.get('component_ids', {}).get('family_id')
        if not family:
            raise ValueError(f"样本 {row.get('sample_id')} 缺少 family_id")
        families.setdefault(family, []).append(row)
        if row.get('variant_kind') != 'question_form':
            raise ValueError(f"样本 {row['sample_id']} 缺少 question_form 变体标记")
    if len(families) != 50 or any(len(group) != 4 for group in families.values()):
        raise ValueError('必须是 50 个 family、每个 family 恰好 4 条问法变体')
    # Four questions in a family must be distinct and drawn from its topic's
    # curated set; punctuation/suffix-only clones are rejected here.
    for family, group in families.items():
        topic = group[0]['topic'].split('（', 1)[0]
        allowed = set(QUESTION_VARIANTS.get(topic, ()))
        users = [r['current_user'] for r in group]
        if len(set(users)) != 4 or not set(users).issubset(allowed):
            raise ValueError(f'{family} 的问法未覆盖四种独立候选')
        context_key = lambda r: (r['snapshot']['area'], r['snapshot']['time'],
                                 r['snapshot']['weather'], r['relation'])
        if len({context_key(r) for r in group}) != 1:
            raise ValueError(f'{family} 的四条问法不共享同一权威快照')
    # Logical family contexts must be unique; this catches accidental loops
    # that merely copy a base episode with a new identifier.
    logical = {(r['topic'].split('（', 1)[0], r['snapshot']['area'],
               r['snapshot']['time'], r['snapshot']['weather'], r['relation'])
              for r in rows}
    if len(logical) != 50:
        raise ValueError(f'逻辑场景重复：应有 50 个，实际 {len(logical)} 个')

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args(); rows=build_expanded()
    validate_expansion(rows)
    with a.output.open('x',encoding='utf-8') as f:
        for r in rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
    print(json.dumps({'families':50,'rows':len(rows),'output':str(a.output)},ensure_ascii=True))
if __name__=='__main__':main()
