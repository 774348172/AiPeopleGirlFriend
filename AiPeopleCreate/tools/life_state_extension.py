"""Versioned opt-in state vocabulary; old frozen inputs keep their projection."""
import copy
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
import yaml

PATH = Path(__file__).resolve().parents[2]/'世界设定/松江府/时间生活状态扩展_20260917.yaml'
VERSION = 'life_state_20260917'


def extend_canon(row, canon):
    version = row.get('world_extension')
    if version is None:
        return canon
    if version != VERSION:
        raise ValueError('Unknown world extension')
    config = yaml.safe_load(PATH.read_text(encoding='utf-8'))
    if config.get('status') != 'approved':
        raise ValueError('Unapproved world extension')
    result = copy.deepcopy(canon)
    fields = result.setdefault('runtime_fields', {})
    scene = row['component_ids']['scene_id']
    key = 'home' if scene.startswith('home.') else scene
    for additions in (config['common_runtime_fields'], config['scene_runtime_fields'].get(key, {})):
        for subject, predicates in additions.items():
            fields[subject] = sorted(set(fields.get(subject, [])) | set(predicates))
    return result


def project_calendar(result, row):
    if not row.get('world_extension'):
        return
    now = datetime.fromisoformat(row['snapshot']['time'])
    if now.utcoffset() != timedelta(hours=8):
        raise ValueError('Life snapshot must use Shanghai +08:00 time')
    for state in row['snapshot']['states']:
        observed = state.get('observed_at')
        expires = state.get('valid_until')
        if expires and not observed:
            raise ValueError('State validity requires observation time')
        if observed:
            observed = datetime.fromisoformat(observed)
            if observed.utcoffset() is None or observed > now:
                raise ValueError('Observation must have timezone and cannot be future')
        if expires:
            expires = datetime.fromisoformat(expires)
            if expires.utcoffset() is None or expires < observed:
                raise ValueError('Invalid state validity interval')
            if expires <= now and state.get('value') is not None:
                raise ValueError('Expired observation must not be a known current state')
    states = {(s['subject'],s['predicate']):s['value'] for s in row['snapshot']['states']}
    times = {}
    for key in ('开始时间','预计结束时间','实际结束时间'):
        value = states.get(('当前生活事件',key))
        if value is not None:
            at = datetime.fromisoformat(value)
            if at.utcoffset() is None:
                raise ValueError('Event time requires timezone')
            if key != '预计结束时间' and at > now:
                raise ValueError('Actual event time is in the future')
            times[key] = at
    for end in ('预计结束时间','实际结束时间'):
        if end in times and '开始时间' in times and times[end] < times['开始时间']:
            raise ValueError('Event ends before it starts')
    if states.get(('当前生活事件','当前阶段')) in ('已结束','已完成') and '实际结束时间' not in times:
        raise ValueError('Completed event needs actual end time')
    if states.get(('当前生活事件','当前阶段')) == '进行中' and '实际结束时间' in times:
        raise ValueError('Ongoing event cannot have actual end time')
    view = result['model_view']
    facts = view['authoritative_world']['facts']
    values = [('日期', now.strftime('%Y年%m月%d日')), ('星期','星期'+'一二三四五六日'[now.weekday()]),
              ('时段', '凌晨' if now.hour < 6 else '早晨' if now.hour < 9 else '上午' if now.hour < 12 else '中午' if now.hour < 14 else '下午' if now.hour < 18 else '晚上')]
    for predicate, value in values:
        f = dict(fact_id=row['component_ids']['snapshot_id']+f'.calendar.{predicate}', subject='日历',
                 predicate=predicate, value=value, source='带时区快照时间确定性换算', status='current')
        facts.append(f)
        view['game_feedback'].append(f"日历的{predicate}：{value}（来源：{f['source']}）")
    view['authoritative_world']['source_hashes']['life_state_extension'] = hashlib.sha256(PATH.read_bytes()).hexdigest()
