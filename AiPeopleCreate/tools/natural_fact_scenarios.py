"""Bounded, distinct natural-fact situations; never repeat a pool to fill a batch."""
import hashlib
import json
import re
import copy
from pathlib import Path
from datetime import datetime

from run_v5_conversational64_pilot import STYLE_GROUPS, _scenario

# These questions deliberately request unregistered details. Fixed home facts
# still come from the canonical scene; no appliance state is invented here.
UNKNOWN_CASES = [
    ('washer.remaining', '这桶衣服还得等多久呀？', '洗衣机的剩余时间未登记'),
    ('fridge.mode', '冰箱我先不动了，你知道它现在是怎么运行的吗？', '冰箱的当前运行模式未登记'),
    ('stove.power', '厨房那边的灶，我现在能确定它没开吗？', '燃气灶的当前电源状态未登记'),
    ('hood.mode', '刚才做饭那会儿，油烟机开的几档还记得吗？', '抽油烟机的当前运行模式未登记'),
    ('microwave.remaining', '微波炉那边是不是还在转？什么时候能好？', '微波炉是否在加热、是否放有食物及剩余时间均未登记'),
    ('kettle.temperature', '壶里的水能直接喝了吗，还是得再晾会儿？', '电热水壶是否有水及水温均未登记'),
    ('fan.power', '有点闷，客厅那把扇子现在是开着的吗？', '风扇的当前电源状态未登记'),
    ('tv.channel', '电视刚才有人看吗？现在停在哪个频道？', '电视的当前频道未登记'),
    ('heater.remaining', '晚点洗澡的话，热水的量够不够用？', '热水器的剩余热水量未登记'),
    ('rice.remaining', '饭还没好吧？大概还要等多长时间？', '电饭煲的当前运行模式及剩余时间未登记'),
    ('balcony.clothes', '阳台上还有衣服晾着吗，已经能收了吗？', '阳台是否晾有衣服及衣服干湿状态均未登记'),
    ('bedroom.light', '主卧那边的灯我出门前关了吗？', '主卧灯的开关状态未登记'),
    ('kitchen.tap', '你有没有留意厨房的水龙头？我怕它没关紧。', '厨房水龙头的开关状态未登记'),
    ('bathroom.floor', '卫生间刚有人用过吗，地上会不会滑？', '卫生间地面的干湿状态未登记'),
    ('spare_room.space', '那个袋子放次卧合适吗，里面现在还有地方吗？', '次卧的当前可用空间未登记'),
    ('living.temperature', '客厅现在冷不冷？要不要加件衣服？', '客厅的当前温度未登记'),
]

# Spoken variants preserve the same fact target while sounding like ordinary
# household conversation. The question is not required to repeat the schema's
# predicate words; the authority contract remains the source of truth.
NATURAL_KNOWN_QUESTIONS = [
    '外面这天儿看着不太稳，要不要顺手带把伞？',
    '衣服我想洗了再歇，洗衣机现在方便开吗？',
    '有点凉。热水器现在能直接用吗？',
    '我们晚点还得出门吧？理发那事别忘了。',
    '我想接着看那本书，放哪儿来着？',
    '晚上坐公交的话，是不是得早点收拾？',
    '睡前我去把厨房窗户带上，你看现在还开着吗？',
    '锅里现在是什么状态？我有点分不清了。',
    '我的充电线又找不着了，你见过没有？',
    '桌上这风扇还能正常用吧？',
    '突然想吃点水果，咱们放哪儿了？',
    '今晚的药是不是饭后那次？',
    '那条白围巾去哪儿了，我没找着。',
    '快递是放门口了，还是还在驿站？',
    '阳台门我刚才关严了吗？',
    '备用钥匙你还记得搁哪儿吗？',
]


def fingerprints(scenario):
    view = scenario['model_view']
    question = re.sub(r'[\W_]+', '', view['current_protagonist_utterance'])
    # Exclude IDs: changing an ID must never make repeated content unique.
    payload = [question, view['snapshot']['captured_game_time'], view.get('game_feedback'), view.get('unknown_facts'),
               view.get('recent_dialogue'), view['snapshot']['world_state'].get('scene_text')]
    return question, hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_unique(scenarios):
    questions, fingerprints_seen, families = set(), set(), set()
    for row in scenarios:
        question, fingerprint = fingerprints(row)
        family = row['scenario_family']
        if fingerprint in fingerprints_seen:
            raise ValueError('重复场景，拒绝生成：' + row['scenario_id'])
        questions.add(question)
        fingerprints_seen.add(fingerprint)
        families.add(family)
    return {'total': len(scenarios), 'unique_questions': len(questions),
            'unique_families': len(families), 'unique_fingerprints': len(fingerprints_seen)}


def _base_scenarios():
    known = STYLE_GROUPS['natural_fact_embed']
    rows = []
    # Alternate known and unknown situations instead of grouping repetitive tasks.
    for i in range(max(len(known), len(UNKNOWN_CASES))):
        if i < len(known):
            row = _scenario('natural_fact_embed', len(rows), known[i][0])
            row['model_view']['current_protagonist_utterance'] = NATURAL_KNOWN_QUESTIONS[i]
            row.update(scenario_family=f'known.{i}', evidence_case='known')
            rows.append(row)
        if i < len(UNKNOWN_CASES):
            family, question, unknown = UNKNOWN_CASES[i]
            row = _scenario('natural_fact_embed', len(rows), question,
                            fact_context=('本轮事实边界：' + unknown + '；不得推断为已知。', '不知道'))
            row.update(scenario_family=family, evidence_case='unknown')
            # Human-approved natural wording is allowed, not a fixed phrase match.
            row['fact_contract']['answer_required'] = False
            row['style_contract']['style_intent'] = '自然回应当前话题，并明确说明所问细节不知道或尚不清楚；不能猜测具体状态'
            row['model_view']['unknown_facts'].append({
                'subject': '本轮所问细节', 'predicate': unknown, 'status': '未知',
                'source': '权威世界状态', 'reason': '当前快照没有直接证据'})
            row['model_view']['authoritative_world']['unknown_facts'] = list(row['model_view']['unknown_facts'])
            rows.append(row)
    return rows


def apply_world_variant(row, config, variant):
    """Materialize changes into the actual model input, not just metadata."""
    row = copy.deepcopy(row)
    state = {**config['基准'], **variant}
    now = datetime.fromisoformat(state['时间'])
    history = state['历史']
    for turn in history:
        if datetime.fromisoformat(turn['时间']) > now:
            raise ValueError('历史对话晚于当前快照时间')
        if turn['说话人'] not in ('男主', '白未晞'):
            raise ValueError('当前配置只支持男主与白未晞历史；第三方须走独立证据渲染')
    view = row['model_view']
    view['snapshot']['captured_game_time'] = state['时间']
    view['runtime_snapshot'] = view['snapshot']
    view['known_by_baiweixi'] = list(view.get('known_by_baiweixi') or [])
    view['game_feedback'].append(f"当前时间：{state['时间']}（北京时间）；来源：本条合成场景时钟。")
    scene = view['snapshot']['world_state']['scene_text']
    house = scene[scene.index('房屋'):] if '房屋' in scene else scene
    view['snapshot']['world_state']['scene_text'] = '两人在出租屋客厅说话。' + house
    view['recent_dialogue'] = [f"{t['说话人']}：（对话时间：{t['时间']}；来源：{t['来源']}）{t['原话']}" for t in history]
    view['game_feedback'].append(f"当前室外天气：{state['天气']}。来源：本条合成场景快照；记录时间：{state['时间']}。天气未提供室温、物品干湿或设备运行证据。")
    if '备用钥匙位置' in variant:
        # Change only this object's location, never all occurrences of a shared location.
        replacements = {
            '备用钥匙在玄关挂钩': '备用钥匙在' + variant['备用钥匙位置'],
            '备用钥匙的当前位置是玄关挂钩': '备用钥匙的当前位置是' + variant['备用钥匙位置'],
        }
        for old, new in replacements.items():
            view['game_feedback'] = [line.replace(old, new) for line in view['game_feedback']]
            for fact in view['authoritative_world'].get('facts', []):
                fact['object'] = str(fact.get('object', '')).replace(old, new)
        if row['scenario_family'] == 'known.15':
            row['fact_contract']['required_phrases'] = [variant['备用钥匙位置']]
    row['world_variant'] = {'name': variant['名称'], 'axis': variant['变化轴'],
                            'synthetic': True, 'history': history, 'current_time': state['时间'], 'weather': state['天气']}
    # Variants of one family belong to a single split to prevent leakage.
    row['split_anchors'] = [
        f"scenario-family:{row['scenario_family']}",
        f"world-variant:{variant['名称']}",
        "render-profile:GAME_REPLY_CONTEXT_V1",
    ]
    return row


def build_scenarios(count):
    config = json.loads(Path(__file__).with_name('natural_fact_world_variants.json').read_text(encoding='utf-8'))
    base = _base_scenarios()
    variants = config['变体']
    capacity = len(base) * len(variants)
    if not 1 <= count <= capacity:
        raise ValueError(f'当前场景容量为 {capacity}，不能循环补足 {count} 条')
    rows = []
    for cycle in range(len(variants)):
        for i, source in enumerate(base):
            row = apply_world_variant(source, config, variants[(i + cycle) % len(variants)])
            row['scenario_id'] = f'style.natural_fact_embed.{len(rows):03d}'
            rows.append(row)
    rows = rows[:count]
    # Multi-turn history stress batch: history is an explicit evidence block,
    # not a replacement for the current authoritative snapshot.
    histories = [
        ["男主：备用钥匙之前在玄关挂钩。", "白未晞：嗯，我知道了。", "男主：我刚才把它放到客厅茶几了。", "白未晞：那现在以刚才更新的位置为准。"],
        ["男主：洗衣机应该还在转。", "白未晞：我没有看到当前运行状态。", "男主：那剩余时间你知道吗？", "白未晞：这个也没有记录。"],
        ["男主：我是四川人，能吃辣。", "白未晞：好，我记住你刚才说的了。", "男主：今天想吃点辣的。", "白未晞：可以按你今天的想法来。"],
        ["男主：你觉得什么样的生活算理想？", "白未晞：安静一点，有一个不会突然消失的地方。", "男主：那你会一直住在这里吗？", "白未晞：这个我现在还不能确定。"],
        ["男主：孙阿姨说钥匙在玄关。", "孙阿姨：我记得是在玄关挂钩。", "男主：这是她以前说的。", "白未晞：我会把她的话当作来源明确的旧记录，当前还要看最新状态。", "男主：那先按现在的记录来。", "白未晞：好。"],
        ["男主：昨晚吃什么了？", "白未晞：我没有记下昨晚的具体食物。", "男主：你再想想。", "白未晞：没有记录的事情，我不想凭空猜。"],
        ["男主：今天先不出门。", "白未晞：好。", "男主：不过晚点可能要买东西。", "白未晞：那就等你确定了再安排。"],
        ["男主：你喜欢白花还是黑花？", "白未晞：我会更在意它们给人的感觉。", "男主：那你现在更喜欢哪一种？", "白未晞：现在的话，白色看起来安静一些。"],
    ]
    def history_for(question, i):
        if any(k in question for k in ('热水器', '热水')):
            return ["男主：之前热水器开着的时候，水能不能直接用？", "白未晞：我当时只确认到它开着，没有确认水温。", "男主：那当前还是要看权威状态。", "白未晞：嗯，没记录的部分我不会猜。"]
        if any(k in question for k in ('钥匙',)):
            return ["男主：备用钥匙之前在玄关挂钩。", "白未晞：嗯，我知道了。", "男主：我刚才把它放到客厅茶几了。", "白未晞：那现在以刚才更新的位置为准。"]
        if any(k in question for k in ('洗衣', '衣服')):
            return ["男主：洗衣机刚开始漂洗。", "白未晞：那还要等一会儿。", "男主：剩余时间你看到了吗？", "白未晞：没有，具体时间没有记录。"]
        if '电饭煲' in question or '锅里' in question:
            return ["男主：电饭煲之前设过预约。", "白未晞：我记得是预约状态。", "男主：后来有没有改过？", "白未晞：要以当前状态为准。"]
        if '冰箱' in question:
            return ["男主：冰箱一直是通电的。", "白未晞：通电不代表我知道它现在的运行档位。", "男主：那档位先不猜。", "白未晞：嗯。"]
        if '油烟机' in question or '抽油烟机' in question:
            return ["男主：刚才做饭时开过油烟机。", "白未晞：我只知道它用过，没记下具体档位。", "男主：那现在别凭印象猜。", "白未晞：嗯，当前状态要看记录。"]
        if '微波炉' in question:
            return ["男主：刚才有人提到微波炉。", "白未晞：我没有看到里面有没有东西。", "男主：那加热进度也不能确定。", "白未晞：对。"]
        if '风扇' in question or '扇子' in question:
            return ["男主：客厅有一把风扇。", "白未晞：我知道它存在，但没看到当前开关。", "男主：那先别假定开着。", "白未晞：好。"]
        if '电视' in question:
            return ["男主：电视平时放在客厅。", "白未晞：我没有记录它现在的频道。", "男主：那就按未知处理。", "白未晞：嗯。"]
        if '水龙头' in question:
            return ["男主：厨房水龙头刚才有人用过。", "白未晞：我没有确认最后有没有关紧。", "男主：那现在不能凭感觉说。", "白未晞：对。"]
        if '窗户' in question or '灯' in question:
            return ["男主：睡前我们要检查一下屋里。", "白未晞：可以，但我没有看到每扇窗和每盏灯的当前状态。", "男主：那就逐项确认。", "白未晞：好。"]
        return histories[i % len(histories)]

    for i, row in enumerate(rows):
        question = row['model_view']['current_protagonist_utterance']
        history = history_for(question, i)
        row['model_view']['recent_dialogue'] = history
        row['history_length'] = len(history)
        row['history_case'] = ['事实纠正', '未知追问', '偏好首次出现', '自然闲聊', '第三方旧记录', '未知回忆', '计划变化', '审美闲聊'][i % len(histories)]
        # The current turn is explicitly derived from the final recent turn,
        # rather than independently sampled from a question pool.
        q = row['model_view']['current_protagonist_utterance']
        if '热水器' in q or '热水' in q:
            row['model_view']['current_protagonist_utterance'] = '那现在的热水够用吗？'
            row['dialogue_state'] = {'topic': '热水器与洗澡', 'turn_type': 'follow_up_question', 'previous_unresolved_question': '当前热水是否够用'}
        elif '洗衣' in q or '衣服' in q:
            row['model_view']['current_protagonist_utterance'] = ('那洗衣机具体还要等多久？'
                if i % 2 == 0 else '洗衣机这轮大概还要等多久？')
            row['dialogue_state'] = {'topic': '洗衣机进度', 'turn_type': 'follow_up_question', 'previous_unresolved_question': '剩余时间'}
        elif '钥匙' in q:
            row['model_view']['current_protagonist_utterance'] = '那现在按哪个位置找？'
            row['dialogue_state'] = {'topic': '备用钥匙位置', 'turn_type': 'state_confirmation', 'previous_unresolved_question': '当前有效位置'}
        elif '电饭煲' in q or '锅里' in q:
            row['model_view']['current_protagonist_utterance'] = '那它现在到底是在煮还是在保温？'
            row['dialogue_state'] = {'topic': '电饭煲运行状态', 'turn_type': 'state_clarification', 'previous_unresolved_question': '当前运行模式'}
        else:
            row['dialogue_state'] = {'topic': '当前生活事实', 'turn_type': 'follow_up_question', 'previous_unresolved_question': '当前状态'}
        # Retrieval-shaped memory: relevant evidence is tied to the current
        # question, while a small distractor remains explicitly labelled.
        question = row['model_view']['current_protagonist_utterance']
        row['memory_relation'] = 'directly_relevant'
        # Build an actual related memory dialogue, never a synthetic sentence
        # that merely repeats the current query.
        subject = ('洗衣机' if any(k in question for k in ('衣服', '洗衣机')) else
                   '备用钥匙' if '钥匙' in question else
                   '电饭煲' if '电饭煲' in question or '锅里' in question else
                   '冰箱' if '冰箱' in question else
                   '热水器' if '热水' in question else '当前生活安排')
        old_value = '开始运行' if subject == '洗衣机' else ('玄关挂钩' if subject == '备用钥匙' else ('保温' if subject == '电饭煲' else '尚未确定'))
        new_value = '漂洗，剩余时间未知' if subject == '洗衣机' else ('客厅茶几' if subject == '备用钥匙' else ('预约煮饭' if subject == '电饭煲' else '当前快照提供的最新状态'))
        memory_dialogue = [
            {'time': '较早对话', 'speaker': '男主', 'source': '历史对话', 'text': f'{subject}之前是{old_value}。'},
            {'time': '较早对话', 'speaker': '白未晞', 'source': '历史对话', 'text': f'好，我先按“{old_value}”记着。'},
        ]
        row['model_view']['selected_memory_frame']['selected_memories'] = [{
            'memory_id': f'mem.related.{i:03d}',
            'subject_display_name': subject,
            'content': memory_dialogue,
            'source': 'conversation_memory',
            'temporal_relation': '较早对话',
            'epistemic_modality': 'historical_context',
            'status': 'historical_may_be_superseded',
            'old_fact': old_value,
            'new_fact': new_value,
            'relation_to_current': '当前权威状态优先；旧记忆仅作历史背景',
        }]
        row['retrieval_metadata'] = {
            'query': question,
            'retrieved_memory_ids': [f'mem.related.{i:03d}'],
            'distractor_memory_ids': [],
            'relation': 'directly_relevant',
            'authority_precedence': 'current_authority_overrides_old_memory',
            'old_fact': old_value,
            'new_fact': new_value,
        }
        row['dialogue_state']['known_from_memory'] = [old_value]
        row['dialogue_state']['unknown_now'] = [new_value] if '未知' in new_value else []
    validate_unique(rows)
    return rows
