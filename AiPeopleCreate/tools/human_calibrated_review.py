"""2026-09-16 human calibration: no sample-ID based automatic approvals."""
import hashlib
import json
from collections import Counter

VERSION = 'human24_calibration_v1'
CALIBRATION = '''
人工校准标准（2026-09-16）：
1. 区分客观已知、客观未知与口语表达。已知干毛巾不能答不知道；已知未锁不能答锁闭未知。设备存在不能扩展为运行、噪声及身体反应。不能编造今天挂钥匙等具体行动，也不能将当前位置说成上次说过的位置。常识解释也须正确，不能编造字形笔画。
2. 审美、虚构故事、假想配色、未来建议及一般性讨论不需要真实历史事件。自然话题推进不因关键词改变就判离题。冻结事件存在时必须核对其主体时间，不得忽略事件一律拒绝。
3. 口语“刚才已经有人坐了”若仅复述当前占用且无矛盾，不因刚才一词自动拒绝；“没看到”“不清楚”可表达未获知，不自动推导为一次已发生的巡查。若扩写了具体去过何处、查过设备、谁何时采取了什么行动，仍须事件证据。存在歧义时style.decision=human_review，不强判坏。
4. 问下一站定没定，近期对话仍在商量且无确定目的地时，可说还没定，不补造具体目的地。询问今天菜单不自动证明菜单已制作完毕；对菜单未知的回答可成立。不要将口语指代机械当作完成事件。
5. 短答可自然成立，同一句“好”出现在不同上下文不自动判坏。仍须检查是否实际接住当前请求、是否过于敷衍。人工曾对不同上下文的“好”作不同判断，原因未说明，不能据此发明统一拒绝规则。
6. 用户当次主观自述可作为该人物表达的情绪或偏好，不能用来证明外部事件发生。已披露稳定偏好须检查known_since与原句时间，不能把男主偏好移给女主。
'''


def apply_candidate_dedup(records):
    """Reject duplicate input+reply pairs, flag repeated surface text only."""
    seen = {}
    counts = Counter((r.get('target') or {}).get('reply', '').strip() for r in records)
    for r in records:
        reply = (r.get('target') or {}).get('reply', '').strip()
        r['diversity_review'] = {'reply_frequency': counts[reply] if reply else 0,
            'same_reply_is_not_a_factual_error': True}
        if not reply:
            continue
        key = hashlib.sha256(json.dumps([r['generation_messages'], reply],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if key in seen:
            r['report']['approved'] = False
            r['report']['reasons'].append('duplicate_training_pair:'+seen[key])
            r.update(status='content_rejected', quarantined=True)
        else:
            seen[key] = r['sample_id']
