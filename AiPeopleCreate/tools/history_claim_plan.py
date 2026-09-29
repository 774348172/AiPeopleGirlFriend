"""Check explicit author-owned history scopes before freezing."""
from datetime import datetime

KINDS = {'subjective', 'self_report', 'knowledge_limit', 'world', 'event', 'conversation'}


def validate_claim_plan(row, plan, facts):
    turns={f'{kind}:{i}':t for kind in ('memory_turns','recent_turns') for i,t in enumerate(row[kind])}
    if not isinstance(plan,dict) or set(plan)!=set(turns):
        raise ValueError('历史编排须逐句登记声明类型，不得遗漏或多填')
    by_fact={f['fact_id']:f for f in facts}
    events={e['source']:e for e in row.get('historical_events',[])}
    for key,p in plan.items():
        if not isinstance(p,dict) or set(p)!={'kind','scope','evidence_ids'} or p['kind'] not in KINDS:
            raise ValueError('历史声明类型或字段不合法：'+key)
        if not isinstance(p['scope'],str) or len(p['scope'].strip())<8:
            raise ValueError('须具体说明声明范围和不证明什么：'+key)
        refs=p['evidence_ids']
        if not isinstance(refs,list) or not all(isinstance(x,str) for x in refs):
            raise ValueError('证据编号须为字符串列表：'+key)
        kind=p['kind']; turn=turns[key]; at=datetime.fromisoformat(turn['time'])
        if kind in ('subjective','self_report','knowledge_limit'):
            if refs:raise ValueError('主观或知情范围不是外部事实证据：'+key)
            continue
        if not refs:raise ValueError('客观历史声明须绑定独立证据：'+key)
        for ref in refs:
            if kind=='world':
                f=by_fact.get(ref)
                if not f or f['value'] is None:raise ValueError('未知状态不能证明客观断言：'+key)
                if key.startswith('memory_turns:') and not f.get('known_since'):
                    raise ValueError('当前快照不能证明旧会话事实：'+key)
                if f.get('known_since') and datetime.fromisoformat(f['known_since'])>at:
                    raise ValueError('资料获知晚于原话：'+key)
            elif kind=='event':
                e=events.get(ref)
                if not e or datetime.fromisoformat(e['time'])>at or turn['speaker'] not in e['known_by']:
                    raise ValueError('历史事件缺失、晚于原话或说话者不知情：'+key)
            elif kind=='conversation':
                t=turns.get(ref)
                if not t or datetime.fromisoformat(t['time'])>=at:
                    raise ValueError('引用对白必须更早，且只证明说过：'+key)
    return plan
