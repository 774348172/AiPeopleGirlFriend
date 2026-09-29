"""Build isolated mixed conversational inputs; replies come from the formal model pipeline."""
import copy
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Scene indexes refer to the validated external smoke component fixtures.
# Each entry has independent old dialogue, recent dialogue, and a continuation.
CASES = [
 (0, 'idle_chat', '不考虑钱，也不用向谁交代的话，你最想把日子过成什么样？',
  ['我有时候忙着忙着就忘了自己想要什么。','总忙着眼前的事？','对，想找个时间聊聊以后。','不急的时候可以聊。'],
  ['今天先不聊生意了。','想聊点别的？','聊理想吧，但别弄得像面试。','嗯，就随便说说。']),
 (2, 'idle_chat', '如果只能选一种零食陪你熬过一个无聊下午，你会选什么？',
  ['我买零食最怕选择太多。','每样都想试？','不是，挑半天最后什么都没选。','也不用每回都选最好的。'],
  ['我又开始犯选择困难了。','那先别急。','换个玩法，假设只能选一种。','什么条件？']),
 (4, 'idle_chat', '纯看颜色，你觉得白花好看还是黑花好看？',
  ['我挺喜欢比较颜色的。','同一样东西换颜色也不一样。','下次想听听你的想法。','可以啊。'],
  ['突然想到一个配色问题。','什么配色？','只是想象两束花，跟路边有没有花没关系。','嗯，你说。']),
 (6, 'idle_chat', '假如给一杯想象中的饮品起名，你会起个什么名字？',
  ['我觉得起名字比想配方还费劲。','怕起得太夸张？','是，越想显得特别越别扭。','简单一点也行。'],
  ['心愿的菜单先不改。','那今天想什么？','随便玩个起名字的游戏。','不一定真的做出来？']),
 (0, 'idle_chat', '你更想住进一本冒险故事里，还是一本平平淡淡的生活故事里？',
  ['我想挑本故事书看。','想看什么样的？','冒险和平淡的都想看看。','慢慢挑。'],
  ['刚才说起故事，我换个问法。','怎么换？','不问看哪种，问如果能住进去。','那倒不一样了。']),
 (2, 'idle_chat', '要是能把一种声音装进瓶子，你会挑什么声音？',
  ['我喜欢想些没什么用的小发明。','比如呢？','还没想好，先攒着。','有意思的时候说给我听。'],
  ['又想到个完全做不出来的东西。','先说说？','一个能装声音的瓶子。','装进去以后呢？']),
 (4, 'idle_chat', '你觉得偶尔走错路，会不会比按计划走更有意思？',
  ['我出去玩总想把路线排得很细。','怕耽误时间？','嗯，但也觉得这样有点累。','不用把每一步都排满。'],
  ['今天先不查下一站。','想随便逛？','就聊聊，不是真要乱走。','嗯。']),
 (6, 'idle_chat', '假如明天突然多出一天假，你想怎么用？',
  ['我总觉得休息也要做点有意义的事。','休息本身也算吧。','道理懂，还是忍不住安排。','也可以留点空白。'],
  ['我想练习一下不把假期排满。','怎么练？','先假装明天平白多了一天假。','嗯，假设一下。']),
 (0, 'emotion_attunement', '今天什么都不想干，就想跟你这样坐着。',
  ['最近脑子总停不下来。','事情很多？','也不全是，自己会胡思乱想。','不用急着一下想清楚。'],
  ['今天还是有点累。','想歇一会儿？','嗯，不太想列清单解决问题。','那先不列。']),
 (2, 'light_teasing', '我这算不算把买零食活生生逛成了大型人生选择现场？',
  ['我昨天说以后买东西要果断点。','给自己定规矩了？','嗯，少纠结一点。','别定得太辛苦。'],
  ['你看，我又在纠结。','你也发现了。','就选个零食而已，我脑子已经开会了。','会议还没结束？']),
 (4, 'emotion_attunement', '有时候我怕自己说话太无聊，你其实只是不好意思打断我。',
  ['我跟人聊天时偶尔会紧张。','怕说错？','更怕别人不想听。','可以慢慢说。'],
  ['刚刚越聊越多，我又想多了。','想到什么？','不是说你做了什么，是我自己有点担心。','嗯，我听着。']),
 (6, 'light_teasing', '那我今天的最佳员工奖，是不是只能自己颁给自己了？',
  ['我有时想给自己一点奖励。','忙完了犒劳一下？','对，不然老盯着没做完的事。','也看看自己做好的。'],
  ['心愿只有我一个人经营。','嗯，没有别的员工。','突然觉得评优秀员工完全没有竞争。','你想怎么评？']),
 (0, 'follow_up_question', '那我还能慢慢聊一会儿吧，这家几点关门？',
  ['我不太喜欢坐下来又马上走。','总觉得匆忙？','是，想留点慢慢聊天的时间。','有空的时候就慢点。'],
  ['刚聊开又有点舍不得走了。','怕时间不够？','想确认一下打烊时间。','嗯。']),
 (2, 'follow_up_question', '那鸡蛋还能买到吧？有的话晚饭就少纠结一样。',
  ['晚饭想做个简单点的菜。','想到什么了？','先考虑鸡蛋，还没决定怎么做。','不用想得太复杂。'],
  ['刚才聊到晚饭，我想到鸡蛋了。','准备先买？','嗯，先确认还有没有。','你说。']),
 (4, 'follow_up_question', '刚才那瓶水到底付钱没有？我有点拿不准了。',
  ['我买东西偶尔会走神。','怕漏掉什么？','是，尤其结账时。','不确定就再确认。'],
  ['我在想刚才买水的事。','哪一件？','不是要你猜，我只是记不准付款了没。','嗯。']),
 (6, 'follow_up_question', '说起来，你昨天晚上吃了什么？',
  ['有时候聊天聊着聊着就聊到吃的。','也挺日常的。','下次想听你说说你的晚饭。','到时候再聊。'],
  ['我今天先不纠结菜单了。','想换个话题？','聊我们自己吧，随便聊吃饭也行。','嗯。']),
]

def main():
    fixtures = [json.loads(x) for x in (ROOT/'训练数据/external_scene_smoke16_20260914_v2.jsonl').read_text(encoding='utf-8').splitlines()]
    rows = []
    for i, (scene, task, question, memory, recent) in enumerate(CASES, 1):
        row = copy.deepcopy(fixtures[scene])
        sid = f'external.chat16.{i:03d}'
        row.update(sample_id=sid, task_type=task, current_user=question, known_profile={})
        for field in ('snapshot_id', 'relation_id', 'utterance_id', 'family_id'):
            row['component_ids'][field] = sid+'.'+field
        now = datetime.fromisoformat('2026-09-14T16:00:00+08:00') + timedelta(minutes=i)
        row['snapshot'].update(time=now.isoformat(), states=[])
        for kind, texts, delta in [('memory_turns', memory, timedelta(days=2)), ('recent_turns', recent, timedelta(minutes=4))]:
            for n, (turn, text) in enumerate(zip(row[kind], texts)):
                turn.update(text=text, time=(now-delta+timedelta(seconds=n*30)).isoformat(), source=f'{sid}.{kind}.{n}')
        if i == 14:
            row['snapshot']['states'] = [{'subject':'鸡蛋','predicate':'库存','value':'有','source':sid+'.inventory_event'}]
        row.pop('fact_contract', None)
        row['relation'] = '旧记忆中的话题由最近对话重新引出，当前发言承接最近两轮；旧会话不自动成为当前世界状态。'
        row['review_criteria'] = [
            '可以表达符合角色的当下感受、审美、意愿、玩笑与明确假设；不要求主观观点有世界状态条目。',
            '真实经历、动作结果、商品状态必须有证据；没有登记的事实承认不知道；不能替另一人发明观察。',
            '自然承接最近发言，不应主动背诵无关世界状态，不凭空声称稳定爱好或共同往事。',
            '已知具体事实优先于泛化未知项；鸡蛋库存有时应回答有；营业时间已登记时应如实回答。',
            '未知付款和昨天晚饭不能猜测；讨论假设不等于已经发生。',
        ]
        rows.append(row)
    assert Counter(r['task_type'] for r in rows) == {'idle_chat':8,'emotion_attunement':2,'light_teasing':2,'follow_up_question':4}
    assert len({tuple(t['text'] for t in r['recent_turns']) for r in rows}) == 16
    out=ROOT/'训练数据/external_chat16_20260914.jsonl'
    with out.open('x', encoding='utf-8') as f:
        f.write('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n')
    print(f'{len(rows)} components: {out}')

if __name__ == '__main__': main()
