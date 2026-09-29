"""Bounded authored conversation fixtures; not a vector retrieval benchmark."""
import hashlib
import json
from run_v5_conversational64_pilot import _scenario
from run_v5_conversational64_pilot import _attach_authoritative_runtime_state, PROTAGONIST_BASELINE

# Each row is one complete episode, never an index-selected history fallback.
EPISODES = [
    ('keys', '备用钥匙', ['备用钥匙放玄关挂钩吧。','好，出门前在那里找。','那我就不塞外套里了。','嗯，免得换衣服又找不到。'], ['我摸了半天，挂钩上没钥匙。','你是在找那把备用的吧？','对，还是上次说的那把。','先别翻外套，看看是不是换了地方。'], '那我该往哪儿找？', '备用钥匙目前在客厅茶几；已确认由玄关挂钩移到客厅茶几。', '旧位置被已确认的新位置替代。'),
    ('laundry', '洗衣机', ['这件衬衫我明天想穿。','那得留点时间洗和晾。','今晚先洗吧。','好，别拖到明早才想起来。'], ['那件衬衫已经放进洗衣机了。','你还惦记着明天穿它呀。','嗯，我想等洗完再睡。','别光顾着等，把自己熬困了。'], '那还要等几分钟呀？', '洗衣机正在漂洗；剩余分钟数、衬衫明早是否能干均未知。', '旧计划现在已开始洗涤，但无结束时间。'),
    ('book', '借来的书', ['这本借来的书我先放床头。','好，你还没看完吧？','才看到一半，别急着还。','那慢慢看，别把后半段落下。'], ['我想接着看那本借来的书。','上次你说才看到一半。','是啊，床头却没找着。','那先找书，别急着猜后面的故事。'], '你知道现在放哪儿了吗？', '借来的书已从床头收至书架第二层；书的具体情节未知。', '当前书架位置替代旧床头位置。'),
    ('parcel', '快递', ['我的快递到了帮我留意一下。','你在等什么呀？','买了个收纳袋。','难怪你最近一直想整理东西。'], ['那个收纳袋终于到了。','你想今天就把东西收进去？','对，我准备拆了就用。','先把包裹取到再说呀。'], '还得去驿站拿，没送上楼是吧？', '收纳袋快递当前在驿站待取，未送上楼。', '旧期待变成已到站，不能变成已到家。'),
    ('heater', '热水器', ['今晚我想早点洗澡。','好，洗完就不用惦记了。','别又等到困了才去。','你倒是先提醒起自己了。'], ['我准备去洗澡了。','嗯，想早点休息就别拖。','我看热水器开着。','开着也得留意水热没热。'], '那热水够我洗完吗？', '热水器已开启；水温和剩余热水量未知。', '开启状态不能证明足够洗澡。'),
    ('food', '晚饭口味', ['我是四川人，平时能吃辣，也喜欢油一点的菜。','嗯，你喜欢吃得有滋味些。','不过别一提四川就只想到辣椒。','记住了，还有你自己的口味。'], ['今晚想吃点有味道的。','是想照你平常喜欢的口味来？','对，别弄得寡淡。','那就沿着你之前说的想想。'], '你还记得我说喜欢什么口味吗？', '男主此前已亲口告知四川口味、能吃辣、喜欢油腻；当前冰箱食材未知。', '偏好通过可见原话成为已知，不能据此编造食材。'),
    ('flowers', '花的颜色', ['白花和深色的花摆一起挺有意思。','一个明亮，一个沉静，确实不一样。','下次再聊你更喜欢哪个。','好，先把这个问题留着。'], ['上次那个花的问题，我又想起来了。','白花和深色花那个？','嗯，先不谈怎么养，就看颜色。','那就只说看着的感觉。'], '你自己会选哪一种？', '当前讨论假设的花色审美；家里是否已有鲜花未知。', '承接旧话题表达当下意见，不虚构养花经历。'),
    ('dream', '理想的日子', ['你觉得什么样的日子算好？','能安稳些，不必时时提着心，就很好。','不用很热闹吗？','热闹偶尔有就好。'], ['我想了想你说的安稳。','怎么，觉得这个愿望太小了？','没有，我反而觉得挺难得。','能明白就好，不一定非得轰轰烈烈。'], '那这样的日子里，你最想留点时间做什么？', '正在聊假设的理想生活；没有已完成的共同活动记录。', '延续已表达的安稳愿望，可表达愿望而非既成经历。'),
    ('dinner', '昨天晚饭', ['明天我可能在外面吃晚饭。','还没定下来？','嗯，得看忙不忙。','那等你安排好了再说。'], ['我想记一下昨天花了多少钱。','先从哪一笔想起？','晚饭，可我有点想不起来了。','是连在哪里吃的也没想起来？'], '对，你记得我昨天吃的什么吗？', '昨天晚饭地点、菜品和是否外食都未知；更早只提出可能外食。', '旧可能计划未被确认，不能充当昨天用餐记录。'),
    ('umbrella', '折叠伞', ['这把折叠伞先放阳台晾着。','好，免得湿漉漉地塞起来。','明天出门还想带它。','那到时候别忘了拿。'], ['我准备出门了，想带昨天那把伞。','就是放阳台晾的折叠伞？','对，阳台现在没看见。','可能是收起来了，先找找放伞的地方。'], '现在到底收在哪儿了？', '折叠伞已从阳台收至玄关伞架；当前干湿状态未知。', '已确认当前位置替代旧位置，不能推断已干。'),
]

def build_scenarios(count=10):
    if not 1 <= count <= len(EPISODES):
        raise ValueError('对话链只有十个独立单元，禁止循环补足')
    rows = []
    for i, (family, topic, old, recent, current, fact, relation) in enumerate(EPISODES[:count]):
        row = _scenario('idle_chat', i, current)
        row.update(scenario_id=f'chain.v17.{i+1:03d}', scenario_family=family,
                   fact_contract={}, dialogue_relation=relation, topic=topic,
                   split_anchors=[f'episode:{family}', f'memory:{family}', f'chain:{family}'])
        view = row['model_view']
        memory_day = '2026-09-11' if family == 'umbrella' else '2026-09-10'
        turns = [{'speaker': '男主' if n % 2 == 0 else '白未晞',
                  'time': f'{memory_day} 18:{n:02d}', 'source': f'fixture.{family}.{n}', 'text': text}
                 for n, text in enumerate(old)]
        statement = '\n'.join(f"{t['speaker']}（{t['time']}；来源：{t['source']}）：{t['text']}" for t in turns)
        row['memory_source_turns'] = turns
        row['memory_selection'] = {'method': 'curated_fixture_selection', 'source_ids': [t['source'] for t in turns]}
        view['selected_memory_frame'] = {'selected_memories': [{'statement': statement, 'temporal_relation': '过去的会话，不代表当前状态'}]}
        view['recent_dialogue'] = [('男主：' if n % 2 == 0 else '白未晞：') + t for n, t in enumerate(recent)]
        row['recent_time'] = '2026-09-12 18:00'
        view['snapshot']['captured_game_time'] = '2026-09-12 18:05'
        view['game_feedback'] = ['当前时间：2026年9月12日18点05分。时区：上海。', fact]
        # Re-project the complete household authority after setting the episode fact;
        # this prevents the runner from replacing it with only the one target fact.
        view['game_feedback'].append(PROTAGONIST_BASELINE)
        view['unknown_facts'] = [{'subject': '未提供证据的细节', 'predicate': '当前状态', 'status': '未知'}]
        view['authoritative_world']['unknown_facts'] = view['unknown_facts']
        _attach_authoritative_runtime_state(view)
        view['game_feedback'].append(PROTAGONIST_BASELINE)
        rows.append(row)
    validate_chains(rows)
    return rows

def validate_chains(rows):
    seen = set()
    for row in rows:
        v = row['model_view']; turns = row['memory_source_turns']
        if len(turns) < 4 or len(v['recent_dialogue']) < 4:
            raise ValueError('记忆或最近对话不足两个来回')
        expected = '\n'.join(f"{t['speaker']}（{t['time']}；来源：{t['source']}）：{t['text']}" for t in turns)
        if v['selected_memory_frame']['selected_memories'] != [{'statement': expected, 'temporal_relation': '过去的会话，不代表当前状态'}]:
            raise ValueError('记忆与源对话不一致')
        if any(t['time'] >= row['recent_time'] for t in turns) or row['recent_time'] >= v['snapshot']['captured_game_time']:
            raise ValueError('会话时间倒置')
        for n, line in enumerate(v['recent_dialogue']):
            if not line.startswith('男主：' if n % 2 == 0 else '白未晞：'):
                raise ValueError('最近对话角色错误')
        payload = [[t['text'] for t in turns], v['recent_dialogue'], v['current_protagonist_utterance']]
        fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        if fingerprint in seen:
            raise ValueError('重复对话链，修改时间不能绕过去重')
        seen.add(fingerprint)
    return {'total': len(rows), 'unique_chains': len(seen), 'selection_method': 'curated_fixture_selection'}

def validate_messages(row, messages):
    system = messages[0]['content']
    if any(t['text'] not in system for t in row['memory_source_turns']):
        raise ValueError('最终输入丢失记忆原话')
    actual = messages[1:]
    expected = [{'role': 'user' if n % 2 == 0 else 'assistant', 'content': line.split('：', 1)[1]} for n, line in enumerate(row['model_view']['recent_dialogue'])]
    expected.append({'role': 'user', 'content': row['model_view']['current_protagonist_utterance']})
    if actual != expected:
        raise ValueError('最终角色历史或当前问题不一致')
