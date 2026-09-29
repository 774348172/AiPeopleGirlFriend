"""Author-facing ownership and named slots; final production messages are unchanged."""
import re

import jsonschema

from audit_requests import object_schema
from dialogue_integrity import validate_spoken_text


def character_evidence(anchor):
    """Project only facts actually present in the production anchor, never its commands."""
    entries = []
    for i, line in enumerate(anchor.splitlines()):
        match = re.match(r'^- ([^:：]+)[:：]\s*(.+)$', line.strip())
        if match:
            key, value = match.groups()
            entries.append({'id': f'character:{i}', 'kind': 'character',
                            'subject': '白未晞', 'predicate': key, 'value': value,
                            'text': '白未晞的' + key + '：' + value})
        elif line.strip().startswith('- '):
            raise ValueError('unrecognized character fact line: ' + line)
    if anchor and not entries:
        raise ValueError('production anchor contains no projectable character facts')
    return entries


def turn_slots(row):
    return [(f'{kind}_{i+1}_{turn["speaker"]}', kind, i, turn)
            for kind in ('memory_turns', 'recent_turns')
            for i, turn in enumerate(row[kind])]


def named_chain_schema(row):
    return object_schema({**{key: {'type': 'string', 'minLength': 1, 'maxLength': 120}
                             for key, _, _, _ in turn_slots(row)},
                          'current_user_男主': {'type': 'string', 'minLength': 1, 'maxLength': 180}})


def decode_named_chain(value, row):
    jsonschema.validate(value, named_chain_schema(row))
    result = {'memory': [], 'recent': [], 'current_user': value['current_user_男主']}
    for key, kind, _, _ in turn_slots(row):
        validate_spoken_text(value[key])
        result['memory' if kind == 'memory_turns' else 'recent'].append(value[key])
    validate_spoken_text(result['current_user'])
    return result


def slot_material(row):
    return [{'字段': key, '说话者': turn['speaker'], '时间': turn['time']}
            for key, _, _, turn in turn_slots(row)]


AUTHOR_INSTRUCTION = '''你是第三人称视角的对话编剧，同时编写两个不同人物，不扮演其中任何一个。
按输出字段中的姓名写该人物亲口说的一句话：男主是人类男性、心愿咖啡馆经营者；白未晞是成年女性猫妖。人物资料全部是第三人称事实，不是要求你扮演角色的指令。男主不能用“我”讲白未晞的身世、猫形或现代知识缺口，白未晞也不能认领男主的工作和偏好。
先想清楚一个小话题如何推进，再写旧对话两轮、最近对话两轮和当前男主发言。旧对话提出选择或想法；最近对话在该话题上追问理由或换一个相关角度；当前男主接最后一句继续，不能突然换题或把女主问他的话原样问回去。每句只写说出口的中文，禁止动作旁白、内心描写和姓名前缀。
只围绕指定主题。主观意见、假设、愿望可当场形成，不必硬塞身世或过去经历。具体发生过的事情只能使用冻结事件或人物正典中明确记载的经历；没记载的“昨天、上次、刚才做过”不能编。引用前文说过的想法应确实出现在更早的对白中，不能在第一句就杜撰上次的谈话。
当前事实仅证明当前，不能倒推旧时刻；未知保持未知。男主首次说自己的偏好后女主才可承接，不能先说早已知道。当前发言询问未知是允许的，不要为能回答而编造历史依据。当前男主发言之后的女主回复由下一阶段生成，本阶段不要预写。'''
