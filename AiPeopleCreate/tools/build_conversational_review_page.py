"""Build a human-review page for conversational pilot candidates."""

from __future__ import annotations

import argparse
import json
import hashlib
from pathlib import Path


def message_section(messages, heading):
    """Show only evidence actually present in the saved model request."""
    system = '\n'.join(m['content'] for m in messages if m.get('role') == 'system')
    start = system.find(heading)
    if start < 0:
        return heading + '\n未出现在实际模型请求中'
    end = system.find('\n[', start + len(heading))
    return system[start:end if end >= 0 else len(system)].strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", action="append", default=["idle_chat", "emotion_attunement"])
    parser.add_argument("--expected", type=int, default=18)
    parser.add_argument("--title", default=None)
    parser.add_argument("--quarantined-only", action="store_true", help="Show only isolated/failed candidates from the verified review manifest")
    args = parser.parse_args()
    source = args.input.read_bytes()
    digest = hashlib.sha256(source).hexdigest()
    self_review_path = args.input.parent.parent / 'independent_review.json'
    self_review = json.loads(self_review_path.read_text(encoding='utf-8')) if self_review_path.exists() else {}
    mapping_path = args.input.parent.parent / 'retry_mapping.json'
    retry_mapping = {r['sample_id']:r for r in json.loads(mapping_path.read_text(encoding='utf-8'))} if mapping_path.exists() else {}
    manifest_path = args.input.parent.parent / 'batch_status.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('status') != 'complete' or manifest.get('review_sha256') != digest:
            raise SystemExit('Batch incomplete or review data changed; refusing to publish')
    rows = []
    for line in source.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if args.quarantined_only and not row.get('quarantined', False):
            continue
        group = row.get("scenario", {}).get("task_type")
        if group not in set(args.group):
            continue
        scenario = row["scenario"]
        view = scenario["model_view"]
        rows.append({
            "sample_id": row["sample_id"],
            "retry": retry_mapping.get(row['sample_id'], {}),
            "group": group,
            "group_cn": {'idle_chat': '自然闲聊', 'natural_fact_embed': '自然事实嵌入', 'follow_up_question': '自然事实问答', 'light_teasing': '轻松打趣', 'emotion_attunement': '情绪交流'}.get(group, '情绪/关系承接'),
            "user": view["current_protagonist_utterance"],
            "history": view.get("recent_dialogue") or [],
            "memory": [m.get('statement', '') for m in (view.get('selected_memory_frame') or {}).get('selected_memories', [])],
            "relation": scenario.get('dialogue_relation', ''),
            "messages": row.get('generation_messages', []),
            "actual_authority": message_section(row.get('generation_messages', []), '[当前权威事实]'),
            "actual_unknown": message_section(row.get('generation_messages', []), '[明确未知]'),
            "actual_world": message_section(row.get('generation_messages', []), '[当前世界]'),
            "profile": view.get('protagonist_profile', {}),
            "known_profile": view.get('known_by_baiweixi', []),
            "input_sha256": hashlib.sha256(json.dumps(row.get('generation_messages', []),ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
            "scene": (view.get("snapshot") or {}).get("world_state", {}).get("scene_text", ""),
            "authority": view.get("game_feedback") or [],
            "unknown": view.get("unknown_facts") or [],
            "reply": (row.get("target") or {}).get("reply", "（自动生成失败，见审核原因）"),
            "audit": {"reasons": [
                *[{'history_input_issue':'历史输入依据或连续性待修正，不等于最终回复错误',
                   'question_input_issue':'当前问题与依据绑定待修正',
                   'reply_content_issue':'最终回复被模型判有内容问题',
                   'reply_needs_review':'最终回复存在歧义，待人工判断',
                   'audit_invalid':'审核输出无效，各内容结论暂不能采信'}.get(c,c)
                  for c in row.get('report',{}).get('issue_categories',[])],
                *self_review.get(row['sample_id'], []),
                {'audit_failed': '自动审核失败，原回复可人工判断',
                 'audit_invalid': '审核结果无效：审核结论或引用不一致，尚不能据此判定内容错误',
                 'generation_failed': '生成失败或回复格式无效',
                 'processing_failed': '处理失败，原回复已保留',
                 'content_rejected': '内容被自动审核拒绝（模型判断，待人工裁决）',
                 'approved': '自动审核通过，仍需人工复核'}.get(row.get('status'),
                     '自动审核通过' if row.get('report', {}).get('approved') else '未通过或审核异常'),
                *row.get('report', {}).get('reasons', []),
            ]},
            "report": row.get("report", {}),
            "reply_valid": isinstance((row.get("target") or {}).get("reply"), str)
            and bool((row.get("target") or {}).get("reply", "").strip())
            and row.get('status') != 'generation_failed',
        })
    rows.sort(key=lambda x: x["sample_id"])
    if len({row['sample_id'] for row in rows}) != len(rows):
        raise SystemExit('Duplicate sample IDs')
    if len(rows) != args.expected:
        raise SystemExit(f"expected {args.expected} conversational candidates, got {len(rows)}")
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace('<', '\\u003c')
    import html as html_module
    page_title = html_module.escape(args.title or f"白未晞自然事实候选 {args.expected} 条人工审核")
    review_key = f"baiweixi_v5_natural_fact{args.expected}_{digest[:16]}_user_review"
    if args.quarantined_only:
        review_key += '_quarantined'
    download_name = f"baiweixi_v5_natural_fact{args.expected}_user_review.json"
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title}</title>
<style>
:root{{--bg:#f3f5f7;--ink:#1f2937;--muted:#667085;--line:#d9dee7;--panel:#fff;--navy:#18263a;--green:#087f5b;--red:#b42318}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei","Noto Sans SC",sans-serif}}
header{{position:sticky;top:0;z-index:2;background:var(--navy);color:#fff;padding:18px 22px;box-shadow:0 3px 12px #0002}}
h1{{font-size:21px;margin:0}} .sub{{margin-top:6px;color:#cbd5e1;font-size:13px}}
main{{max-width:1100px;margin:20px auto;padding:0 16px 60px}} .toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:14px}}
button{{border:0;border-radius:6px;padding:9px 13px;font:inherit;cursor:pointer}} .save{{background:#2563eb;color:#fff}} .reset{{background:#e5e7eb;color:#374151}} .count{{color:var(--muted);font-size:14px}}
.item{{background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:12px 0;padding:16px;box-shadow:0 1px 3px #0000000d}}
.top{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}} .id{{font-size:13px;color:var(--muted)}} .tag{{font-size:12px;border-radius:12px;padding:4px 9px;background:#e8eefc;color:#294a8a}}
.scene,.history,.authority{{color:var(--muted);font-size:13px;margin:10px 0;white-space:pre-wrap}} .authority{{background:#f0fdf4;padding:8px;border-radius:5px;color:#166534}} .turn{{border-top:1px solid #eef0f3;padding-top:10px;line-height:1.65}}
.label{{font-size:12px;color:var(--muted);margin-right:8px}} .reply{{font-size:16px;font-weight:600;margin-top:8px;white-space:pre-wrap}}
.audit{{font-size:12px;color:#52606d;margin-top:10px;background:#f8fafc;padding:8px;border-radius:5px;white-space:pre-wrap}}
.actions{{display:flex;gap:8px;margin-top:14px}} .good{{background:#e7f6ef;color:var(--green)}} .bad{{background:#fdecec;color:var(--red)}} .selected{{outline:2px solid currentColor}} button:disabled{{opacity:.45;cursor:not-allowed}}
.note{{width:100%;min-height:42px;margin-top:8px;border:1px solid var(--line);border-radius:5px;padding:8px;font:inherit}}
</style></head><body><header><h1>{page_title}</h1><div class="sub">请审核事实依据、未知边界、角色语气和接话自然度；自动审核失败不代表回复错误。候选尚未进入训练集。</div></header>
<main><div class="toolbar"><button class="save" onclick="save()">保存审核结果</button><button class="reset" onclick="clearAll()">清空本页判断</button><span class="count" id="count"></span></div><div id="list"></div></main>
<script>const rows={data};const key='{review_key}';
function esc(s){{return String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}
function state(id){{return JSON.parse(localStorage.getItem(key)||'{{}}')[id]||{{}}}}
function setv(id,v){{const x=JSON.parse(localStorage.getItem(key)||'{{}}');x[id]=v;localStorage.setItem(key,JSON.stringify(x));render()}}
function render(){{const all=JSON.parse(localStorage.getItem(key)||'{{}}');document.getElementById('count').textContent=`已判断 ${{Object.keys(all).length}} / ${{rows.length}}`;document.getElementById('list').innerHTML=rows.map((r,i)=>{{const s=all[r.sample_id]||{{}};const disabled=!r.reply_valid;const unknown=r.unknown.map(x=>`${{x.subject||'未知主体'}}的${{x.predicate||'事实'}}：未知`).join('；');return `<section class="item"><div class="top"><b>第 ${{i+1}} 条</b><span class="tag">${{esc(r.group_cn)}}</span><span class="id">${{esc(r.sample_id)}}</span></div><div class="scene">场景：${{esc(r.scene)}}</div><div class="authority">权威输入：${{esc(r.authority.join('\\n'))}}<br>明确未知：${{esc(unknown||'无')}}</div><div class="history">历史：${{esc(r.history.join('\\n'))||'无'}}</div><div class="turn"><span class="label">男主：</span>${{esc(r.user)}}</div><div class="reply"><span class="label">白未晞：</span>${{esc(r.reply)}}</div>${{disabled?'<div class="audit" style="color:var(--red)">生成失败：没有合法回复，不可审核为“好”；请标记为“差”。</div>':''}}<div class="audit">自动审核：${{esc((r.audit.reasons||[]).join('；')||'通过')}}</div><div class="actions"><button class="good ${{s.verdict==='good'?'selected':''}}" ${{disabled?'disabled':''}} data-verdict="good" data-id="${{esc(r.sample_id)}}">好</button><button class="bad ${{s.verdict==='bad'?'selected':''}}" data-verdict="bad" data-id="${{esc(r.sample_id)}}">差</button></div><textarea class="note" data-note="${{esc(r.sample_id)}}" placeholder="可选备注">${{esc(s.note||'')}}</textarea></section>`}}).join('')}}
document.getElementById('list').addEventListener('click',event=>{{const button=event.target.closest('button[data-verdict]');if(!button)return;const note=document.querySelector(`[data-note="${{CSS.escape(button.dataset.id)}}"]`);setv(button.dataset.id,{{verdict:button.dataset.verdict,note:note?note.value:''}})}});
function save(){{const all=JSON.parse(localStorage.getItem(key)||'{{}}');const invalid=rows.filter(r=>!r.reply_valid&&all[r.sample_id]?.verdict==='good');if(invalid.length){{alert('存在生成失败样本被标记为“好”，已拒绝保存。请改为“差”。');return}}const blob=new Blob([JSON.stringify(rows.map(r=>({{sample_id:r.sample_id,...state(r.sample_id)}})),null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='{download_name}';a.click();URL.revokeObjectURL(a.href)}}
function countJudged(){{const all=JSON.parse(localStorage.getItem(key)||'{{}}');document.getElementById('count').textContent=`已判断 ${{rows.filter(r=>['good','bad'].includes(all[r.sample_id]?.verdict)).length}} / ${{rows.length}}`}}
document.getElementById('list').addEventListener('input',event=>{{const id=event.target.dataset.note;if(!id)return;const all=JSON.parse(localStorage.getItem(key)||'{{}}');all[id]={{...all[id],note:event.target.value}};localStorage.setItem(key,JSON.stringify(all));countJudged()}});
function clearAll(){{localStorage.removeItem(key);render()}}
const originalRender=render;
render=function(){{originalRender();countJudged();document.querySelectorAll('.item').forEach((item,i)=>{{
 const r=rows[i]; const history=item.querySelector('.history');
 if(r.retry.original_question){{const original=document.createElement('div');original.className='audit';original.textContent='原条目 '+r.retry.retry_of+' · 原问题：'+r.retry.original_question;item.querySelector('.top').after(original);}}
 item.querySelector('.authority').textContent='以下原文来自实际模型请求：\\n'+r.actual_authority+'\\n\\n'+r.actual_unknown;
 item.querySelector('.scene').textContent=r.actual_world;
 history.textContent='最近对话：\\n'+r.history.join('\\n');
 const memory=document.createElement('div');memory.className='history';memory.textContent='旧记忆原话（预设片段选择，非向量召回测试）：\\n'+(r.memory.join('\\n')||'无');history.before(memory);
 const relation=document.createElement('div');relation.className='audit';relation.textContent='旧新关系（仅供审核）：'+r.relation;history.before(relation);
 const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='展开实际发给模型的完整消息';const pre=document.createElement('pre');pre.style.whiteSpace='pre-wrap';pre.textContent=r.messages.map(m=>m.role+'：\\n'+m.content).join('\\n\\n');details.append(summary,pre);item.append(details);
 const profile=document.createElement('details');const ps=document.createElement('summary');ps.textContent='男主完整基础资料（审计用，未获知项不传给白未晞）';const pp=document.createElement('pre');pp.style.whiteSpace='pre-wrap';pp.textContent=JSON.stringify({{基础资料:r.profile,白未晞已知:r.known_profile}},null,2);profile.append(ps,pp);item.append(profile);
}})}}; render();</script></body></html>'''
    args.output.write_text(html, encoding="utf-8")
    print(f"review page: {args.output} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
