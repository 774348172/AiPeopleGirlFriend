"""Reject malformed reviewer decisions without labeling the candidate bad."""
from datetime import datetime
import jsonschema
from audit_generated_history import history_audit_schema, validate_history_review
from audit_requests import object_schema, coverage_schema, STYLE_SCHEMA, FACT_SCHEMA

VERSION = 'audit_consistency_v2_claim_scopes'


def invalid_reasons(row, verdict, catalog, independent):
    schema = object_schema({'history':history_audit_schema(row, independent),
        'coverage':coverage_schema(catalog), 'style':STYLE_SCHEMA, 'facts':FACT_SCHEMA})
    try:
        jsonschema.validate(verdict, schema)
    except jsonschema.ValidationError as exc:
        return ['审核JSON字段不符合合同：'+str(exc.message)[:180]]
    errors=[]
    h=verdict['history']
    all_ok=h['continuity'] and h['events_consistent'] and all(
        c['speaker_consistent'] and c['spoken_only'] and c['supported'] for c in h['checks'].values())
    if h['pass'] != all_ok:
        errors.append('历史审核汇总结论与逐句标注矛盾：pass必须与主体、直接对白、证据、连续性检查一致')
    for name in ('history','coverage','facts'):
        block=verdict[name]
        if block['pass'] == bool(block['reasons']):
            errors.append(name+'审核结论与理由矛盾：通过应无拒绝理由，拒绝必须说明原因')
    cov=verdict['coverage']
    if cov['pass'] and not cov['topic_check']['aligned']:
        errors.append('话题审核同时标记通过与偏题')
    style=verdict['style']
    if style['decision']=='approve' and (not all(style['verdicts'].values()) or style['reasons']):
        errors.append('风格审核同时标记通过与失败分项或拒绝理由')
    table={e['id']:e for e in independent}
    for key,c in h['checks'].items():
        if not c['supported']:
            continue
        kind,index=key.split(':'); at=datetime.fromisoformat(row[kind][int(index)]['time'])
        refs=[table[e] for e in c['evidence_ids']]
        for e in refs:
            if e['kind']=='dialogue':
                if datetime.fromisoformat(e['time']) >= at:
                    errors.append(f'{key}：审核引用自身或未来对白 {e["id"]}，不是候选已被证实错误')
                if c['kind']!='conversation':
                    errors.append(f'{key}：审核用对白证明客观事实，证据类型不合法')
            for field in ('known_since', 'time' if e['kind']=='event' else 'known_since'):
                if e.get(field) and datetime.fromisoformat(e[field]) > at:
                    errors.append(f'{key}：审核引用晚于该句的证据 {e["id"]}')
        if c['kind']=='current' and refs and any(e['kind']!='world' for e in refs):
            errors.append(f'{key}：当前状态审核须引用权威世界证据')
    if h['pass']:
        try:
            validate_history_review(row,h,independent)
        except ValueError as exc:
            errors.append('历史审核宣称通过，但证据校验不成立：'+str(exc))
    return list(dict.fromkeys(errors))


CLARIFICATION = '''
审核字段防混淆：spoken_only=true表示原句是角色说出口的话，并不表示“只有说过却没有发生”。“东西先放这里了”“我喜欢辣一点”都是直接对白；是否有事实依据另填supported。不要把claim中的第三人称摘要当成原句来检查旁白。
每句须独立判断，不能给整组机械填同一布尔值。任意分项false时history.pass必须false并解释具体原因；不确定证据时supported=false，不得一边否定分项一边总评通过。
报告公告、菜单等当前可变状态应选current并引用world证据。conversation只用于引用更早说过的话，不能引用自身、未来句子或world证据；即使同一句带有evidence_id，也不可用其证明自身。
已披露偏好可用带known_since的男主已知设定证据，获知时间不得晚于发言；这类动态投影选current，不应选固定canonical。旧具体事件须查event。审核结论和证据必须一致，不能为了通过而修改候选或省略检查。
'''
