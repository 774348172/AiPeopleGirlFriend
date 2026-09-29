"""Plan history before dialogue; project only events known to the character."""
import copy
from datetime import datetime

from audit_requests import object_schema

HISTORY_INSTRUCTION = '''你是隔离训练样本的历史事件规划器，不写对白或最终答案。当前快照已给定，先为同一聊天意图规划0至3件相关过去事件。闲聊可以没有具体过去事件，只通过主观讨论衔接。
编排前区分人物自述、知情范围和外部事实。自述习惯只表示人物如何描述自己，不编成每次如此的事件；不知道上菜时间不证明服务员没有说过。若没有告知事件证据，应把最近对白安排为“我还不知道要等多久”，不得虚构店员行为，也不得在看到回复后补事件。
不得修改角色身份、基础设定、场景正典或当前状态；不得把男主私密资料写成白未晞已知。人物经历归属要明确。事件text写具体谁在何时做了什么或获知什么，不能预写当前问题答案。actors只登记实际参与者，known_by单独登记确实知情者；参与和获知不可互相冒充。
time只能使用允许的旧会话时刻或最近会话时刻。若需要旧经历与刚才观察的衔接，分别规划对应时段的事件；不能把几天前的事件当成刚才发生。事件只证明该时刻，不证明现在、持续时间或其他人的知情。若过去物品状态不同于当前状态，必须通过current_fact_ids指出当前对应事实；旧值不覆盖新值。text不超过120字；current_fact_ids最多2个，无关的不要引用。严禁根据尚未生成的对白或回复补写依据。只输出events。'''


def history_schema(row, facts):
    times = [row[kind][0]['time'] for kind in ('memory_turns','recent_turns')]
    return object_schema({'events': {'type': 'array',
        'minItems': 1 if row.get('task_type')=='follow_up_question' else 0,
        'maxItems': 3, 'items': object_schema({
        'time': {'type': 'string', 'enum': times},
        'text': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        'actors': {'type': 'array', 'minItems': 1, 'maxItems': 2, 'uniqueItems': True,
                   'items': {'type': 'string', 'enum': ['男主', '白未晞']}},
        'known_by': {'type': 'array', 'maxItems': 2, 'uniqueItems': True,
                     'items': {'type': 'string', 'enum': ['男主', '白未晞']}},
        'current_fact_ids': {'type': 'array', 'maxItems': 2, 'uniqueItems': True,
                             'items': {'type': 'string', 'enum': [f['fact_id'] for f in facts]}},
    })}})


def apply_history_plan(row, plan, facts):
    import jsonschema
    jsonschema.validate(plan, history_schema(row, facts))
    result = copy.deepcopy(row)
    result['historical_events'] = []
    for i, event in enumerate(plan['events']):
        at = datetime.fromisoformat(event['time'])
        if at.utcoffset() is None or at >= datetime.fromisoformat(row['snapshot']['time']):
            raise ValueError('historical event must precede current snapshot')
        # Raw hidden events remain only in the planner audit artifact.
        if '白未晞' not in event['known_by']:
            continue
        result['historical_events'].append({**copy.deepcopy(event),
            'source': f"{row['sample_id']}.synthetic_event.{i}",
            'relation': '只证明过去指定时刻；当前值以本轮权威快照为准，不推断持续未变'})
    return result


def event_text(event):
    return (f"模拟历史事件（{event['time']}；来源：{event['source']}；"
            f"参与者：{'、'.join(event['actors'])}；知情者：{'、'.join(event['known_by'])}）："
            f"{event['text']}。旧新关系：{event['relation']}。"
            f"对应当前事实编号：{'、'.join(event['current_fact_ids']) or '无'}")


def project_history(view, row):
    """Same evidence is visible in final messages and auditing, never audit-only."""
    for event in row.get('historical_events', []):
        if '白未晞' not in event['known_by']:
            raise ValueError('hidden historical event must not enter model view')
        if datetime.fromisoformat(event['time']) >= datetime.fromisoformat(row['snapshot']['time']):
            raise ValueError('future event cannot be historical evidence')
        view['selected_memory_frame']['selected_memories'].append({
            'statement': event_text(event), 'temporal_relation': event['relation']})
