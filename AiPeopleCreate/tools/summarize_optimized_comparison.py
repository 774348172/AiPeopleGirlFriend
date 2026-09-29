"""Add explicit assistant inspection to completed experiment, preserve model verdicts."""
import argparse
import html
import json
from pathlib import Path

from pilot_pipeline import persist


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args(); root=args.root
    def read(name): return json.loads((root/name).read_text(encoding='utf-8'))
    if read('batch_status.json')['status']!='complete': raise ValueError('experiment incomplete')
    report=read('comparison_report.json')
    inspected=read('assistant_inspection.json')['records']
    candidates=[json.loads(x) for x in (root/'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    newly_flagged=[]; remaining=[]
    for row in candidates:
        note=inspected.get(row['sample_id'])
        if row.get('review',{}).get('approved'):
            if not note or note['decision']!='provisional_ok': newly_flagged.append(row['sample_id'])
            else: remaining.append(row['sample_id'])
    old=report['baseline']['recorded_production_cost']; new=report['optimized'].get('final_path_cost',report['optimized']['production_cost'])
    result={**report,'assistant_inspection':inspected,'model_pass_routed_to_review':newly_flagged,
            'provisional_candidates_after_both_checks':remaining,
            'model_time_ratio_old_over_new':old['model_seconds']/new['model_seconds'] if new['model_seconds'] else None,
            'automatically_released_training_rows':0}
    persist(root/'final_report.json',result)
    page=(root/'comparison.html').read_text(encoding='utf-8')
    header_start=page.index('<pre>')
    header_end=page.index('</pre>',header_start)+len('</pre>')
    o=report['optimized']; b=report['baseline']
    rows=[('尝试种子',40,40),('生产调用次数',old['calls'],new['calls']),
          ('生产记录模型耗时（分钟）',round(old['model_seconds']/60,1),round(new['model_seconds']/60,1)),
          ('其中模型加载（分钟）',round(old['load_seconds']/60,1),round(new['load_seconds']/60,1)),
          ('格式有效回复',b['valid_replies'],o['valid_replies']),
          ('共同审核自动通过（非人工正确率）',b['same_audit_auto_pass'],o['auto_pass'])]
    header='<table cellpadding="8"><tr><th>指标</th><th>旧流程</th><th>优化流程</th></tr>'
    header+=''.join('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in row)+'</tr>' for row in rows)+'</table>'
    header+='<p>计时采用最终流程的记录模型耗时；另做的旧数据复审和被替代审核开销另计，见 <a href="comparison_report.md">完整报告</a>。不是整个实验的墙钟耗时。</p>'
    header+='<p>优化流程自动通过中仍有 '+str(len(newly_flagged))+' 条被 Codex 复看转为待复核；两道检查均通过的候选 '+str(len(remaining))+' 条。未自动发布训练数据。</p>'
    page=page[:header_start]+header+page[header_end:]
    for sid,note in inspected.items():
        needle=html.escape(sid)+'</h2>'
        label={'quarantine':'需要隔离','human_review':'需要复核','provisional_ok':'复看暂未发现明确问题'}[note['decision']]
        block='<aside style="padding:12px;background:#fff0dc"><strong>优化候选的 Codex 复看：'+label+'</strong><p>'+html.escape('；'.join(note['reasons']))+'</p></aside>'
        page=page.replace(needle,needle+block)
    (root/'comparison_checked.html').write_text(page,encoding='utf-8')
    o=report['optimized']; b=report['baseline']
    new_audit_failures=sum(any(reason.startswith('audit failed:') for reason in row.get('review',{}).get('reasons',[])) for row in candidates)
    old_audit_failures=sum(any(reason.startswith('audit failed:') for reason in json.loads(path.read_text(encoding='utf-8')).get('comparison_review',{}).get('reasons',[])) for path in (root/'baseline').glob('*.json'))
    common_cost=b.get('final_common_reaudit_cost',b['common_reaudit_cost'])
    superseded=o.get('superseded_audit_cost',{'wall_seconds':0})
    md=f'''# 40 条优化流程速度与质量对照

使用相同的前 40 条原始种子（家庭、街角咖啡、便利店各10条，逛街、心愿各5条），相同生成/审核模型及完整 GAME_REPLY_CONTEXT_V1。旧数据不重写；优化输出全部保留。第一轮缺失权威输入和旧记忆审核的测试已作废，不用于以下数字。

| 项目 | 旧流程 | 优化流程 |
|---|---:|---:|
| 尝试种子 | 40 | 40 |
| 生产模型调用次数（含记录的重试） | {old['calls']} | {new['calls']} |
| 记录的模型耗时 | {old['model_seconds']/60:.1f} 分钟 | {new['model_seconds']/60:.1f} 分钟 |
| 其中记录的模型加载 | {old['load_seconds']:.1f} 秒 | {new['load_seconds']:.1f} 秒 |
| 格式有效回复 | {b['valid_replies']} | {o['valid_replies']} |
| 共同完整审核自动通过（含原有历史疑点规则） | {b['same_audit_auto_pass']} | {o['auto_pass']} |
| 共同审核格式/截断失败，不能判定质量 | {old_audit_failures} | {new_audit_failures} |

优化流程另外记录的实际请求等待为 {new['wall_seconds']/60:.1f} 分钟。为公平检查质量，对旧回复补做共同审核的开销另计 {common_cost['wall_seconds']/60:.1f} 分钟，不冒充新流程生产成本。审核最初使用重复长编号，曾挤占上下文导致截断；修正后改用可反查的短证据编号，保留事实及原话原文。被替代审核实现额外消耗 {superseded['wall_seconds']/60:.1f} 分钟，也保留原始记录，不计入最终流程的生产计时。最终审核复用了已加载模型，因此上表不是从冷启动开始的完整连续测速。整个实验墙钟耗时包括失败实现、补充评测和离线转换，不能全部算到新流程生产上。

采用按模型集中处理和合并请求后，生产记录模型耗时的旧/新比为 {result['model_time_ratio_old_over_new']:.2f}。这是这一批任务的实测，不是所有负载的保证。旧流程有些异常请求未留下响应计时；本次结构重试最多两次，旧流程最多三次；原先历史预审在本实验中后移并合并审核，失败候选可能先产生回复再被隔离。因此不能只看有效回复数量就认定质量改善。

## 质量判读

旧流水线原本自动通过 {b['original_auto_pass']} 条；共同审核与原审核可能不一致，两者均为模型判断，不是人工真值。

Codex 对新候选另做逐条复看。自动通过中有 {len(newly_flagged)} 条仍需复核；两道检查后暂未发现问题的候选为 {len(remaining)} 条，也未经用户批准。没有任何数据被自动发布到训练集。

完整旧/新历史、原话、事件、当前状态、最终模型输入与具体复看意见在 comparison_checked.html。请求和模型原始响应在 requests/；不要从自然流畅的最终回复推断输入历史也正确。

## 结论边界

分阶段运行能减少频繁加载模型，值得保留。本批合并创作仍出现无依据往事、旧新时间混淆、角色错位和重复对白；尚未用单因素实验确认各项改动的影响，也未逐条复看旧样本，因此不能断言整体正确率比旧流程更高或更低。建议保留模型集中处理与证据短编号，恢复分离的世界/事件规划和历史生成，并在生成最终回复前检查历史；不能以减少审核覆盖来换取通过率。此次试验不修改正式 1000 条批次、不训练、不自动扩大下一批。
'''
    (root/'comparison_report.md').write_text(md,encoding='utf-8')
    print(json.dumps({'model_time_ratio':result['model_time_ratio_old_over_new'],
        'new_auto_pass':o['auto_pass'],'old_same_audit_pass':b['same_audit_auto_pass'],
        'remaining_provisional':len(remaining)},ensure_ascii=False))


if __name__=='__main__': main()
