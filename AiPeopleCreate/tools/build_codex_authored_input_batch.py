"""Compile authored inputs, then seal separately written, hash-bound reviews.

No LLM calls, no reply generation, no implicit approvals. The compile command
deliberately works without a review file and cannot create an approval ledger.
"""
import argparse
import copy
import hashlib
import html
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from build_authority_component10 import build_components
from convert_component_to_game_reply import convert, validate_final_messages
from joint_scene_planning import apply_plan, validate_requirements
from synthetic_history import apply_history_plan
from run_component_game_reply15_pilot import build_messages
from run_curated_candidate_batch import ROOT, digest, persist, _load_baiweixi_render_context, run
from run_mixed_candidate_batch import duplicate_reason

SCENES = [
    ('home.txt', 'home.living_room', 'living_room'),
    ('corner_cafe.txt', 'songjiangfu.neighborhood_cafe', '普通座位区'),
    ('store.txt', 'songjiangfu.neighborhood_convenience_store', '食品货架区'),
    ('street.txt', 'songjiangfu.shopping_walk', '沿街店铺一侧'),
    ('xinyuan.txt', 'songjiangfu.protagonist_cafe', '顾客座位区'),
]


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_batch(out):
    if (out / 'input_approvals.json').exists():
        raise ValueError('Reviewed batch is sealed; create a new batch for revisions')
    source = out / 'source'
    plan = read(source / 'world_plan.json')
    base = build_components()[0]
    anchor = _load_baiweixi_render_context()[2]
    prior_paths = [ROOT / '训练数据' / d / 'components.json' for d in
                   ('curated15_inputs_20260915', 'curated100_inputs_20260915')]
    prior_paths += [ROOT / p for p in plan.get('additional_prior_components', [])]
    previous = [r for p in prior_paths for r in read(p)]
    prior_count = len(previous)
    rows, drafts = [], []
    groups = plan.get('scene_groups', SCENES)
    if not groups or any(len(g) != 3 or Path(g[0]).name != g[0] for g in groups):
        raise ValueError('scene_groups requires filename, registered scene_id, area')
    if len({g[0] for g in groups}) != len(groups):
        raise ValueError('Duplicate source filename')
    for group, (filename, scene, area) in enumerate(groups):
        lines = (source / filename).read_text(encoding='utf-8').splitlines()
        count = plan.get('group_counts', {}).get(filename, 20)
        if type(count) is not int or count < 1 or len(lines) != count or any(not s.strip() for s in lines):
            raise ValueError('Authored chain count does not match group_counts in ' + filename)
        for slot, line in enumerate(lines):
            n = len(rows) + 1
            index = str(n)
            sid = f"{plan.get('sample_prefix', 'codex100.20260916')}.{n:03d}"
            topic, old, recent, user = line.split('|')
            old, recent = old.split('~'), recent.split('~')
            if len(old) != 4 or len(recent) != 4:
                raise ValueError('Incomplete authored chain: ' + sid)
            if index in plan.get('snapshot_times', {}):
                at = datetime.fromisoformat(plan['snapshot_times'][index])
            else:
                at = datetime.fromisoformat(f"{plan.get('snapshot_date', '2026-09-18')}T{11+slot//3:02d}:{(slot*11)%60:02d}:00+08:00")
            task = 'idle_chat' if slot < 10 else 'emotion_attunement' if slot < 15 else 'follow_up_question'
            task = plan.get('task_types', {}).get(index, task)
            if task not in ('idle_chat', 'emotion_attunement', 'follow_up_question'):
                raise ValueError('Unsupported authored task type')
            row = copy.deepcopy(base)
            row.update(sample_id=sid, topic=topic, task_type=task, current_user=user,
                       historical_events=[], known_profile={})
            row.pop('fact_contract', None)
            if plan.get('world_extension'):
                row['world_extension'] = plan['world_extension']
            for key in ('snapshot_id', 'family_id', 'relation_id', 'utterance_id'):
                row['component_ids'][key] = sid + '.' + key
            if index in plan.get('dialogue_family_ids', {}):
                row['component_ids']['family_id'] = plan['dialogue_family_ids'][index]
            row['component_ids']['scene_id'] = scene
            row['snapshot'].update(time=at.isoformat(), weather='阴天' if n == 76 else
                                   ['多云', '晴天', '阴天'][slot % 3], area=area, states=[])
            row['snapshot']['weather'] = plan.get('weather_by_sample', {}).get(index, row['snapshot']['weather'])
            for person in row['snapshot']['people'].values():
                person.update(area=area, activity='坐着当面聊天' if scene.startswith('home.') or scene in ('songjiangfu.neighborhood_cafe','songjiangfu.protagonist_cafe','songjiangfu.home_style_restaurant') else
                              '站着当面聊天', body='正常', emotion='平静')
            if n == 11 and plan.get('legacy_mood_case', True):
                row['snapshot']['people']['男主'].update(emotion='有点闷', emotion_visibility='visible',
                    emotion_source=f'男主主动自述：{sid}.recent_turns.0')
            age = timedelta(minutes=plan['memory_minutes_ago'][index]) if index in plan['memory_minutes_ago'] else timedelta(days=2)
            for kind, texts, delta in [('memory_turns', old, age), ('recent_turns', recent, timedelta(minutes=plan.get('recent_minutes_ago', 4)))]:
                row[kind] = [dict(speaker='男主' if j % 2 == 0 else '白未晞', text=text,
                                 time=(at-delta+timedelta(seconds=30*j)).isoformat(),
                                 source=f'{sid}.{kind}.{j}') for j, text in enumerate(texts)]
            row['known_profile'] = {key: f'{sid}.memory_turns.{j}' for key, j in
                                    plan['known_profile'].get(index, {}).items()}
            relation = plan['relation_overrides'].get(index,
                f'相关旧会话以“{topic}”起题，最近两轮继续该话题，当前发言紧接最后一轮；假想和主观交流不冒充发生事件。')
            row.update(relation=relation, review_criteria=[relation,
                '具体事实仅用权威快照或知情事件；未登记状态承认不知道；可表达当下主观意见，不把男主偏好改成女主偏好。'])
            states = []
            if scene == 'songjiangfu.neighborhood_cafe':
                states = [('街角咖啡', '是否营业', '是'), ('街角咖啡', '座位使用情况', '两人坐普通座位区，其他座位情况未登记'),
                          ('街角咖啡', '噪声', '店内声音轻，两人可以正常音量聊天')]
            elif scene == 'songjiangfu.neighborhood_convenience_store':
                states = [('邻里便利店', '是否营业', '是')]
            elif scene == 'songjiangfu.protagonist_cafe':
                states = [('心愿咖啡馆', '是否营业', '是'), ('心愿咖啡馆', '是否已打烊', '否'),
                          ('心愿咖啡馆', '白未晞本轮是否帮忙', '本轮坐着聊天，未参与经营'),
                          ('心愿咖啡馆', '当班分工', '只有男主经营，白未晞不是员工')]
            table = {(s, p): v for s, p, v in states}
            table.update({(s, p): v for s, p, v in plan['facts'].get(index, [])})
            row = apply_plan(row, {'intent': topic, 'states': [dict(subject=s, predicate=p, value=v,
                visibility='visible') for (s, p), v in table.items()]})
            for metadata in plan.get('state_validity', {}).get(index, []):
                matches = [s for s in row['snapshot']['states'] if
                           (s['subject'], s['predicate']) == (metadata['subject'], metadata['predicate'])]
                if len(matches) != 1:
                    raise ValueError('Validity metadata must bind exactly one planned state')
                matches[0].update({k: metadata[k] for k in ('observed_at', 'valid_until') if k in metadata})
            facts = convert(row)['model_view']['authoritative_world']['facts']
            lookup = {(f['subject'], f['predicate']): f for f in facts}
            events = []
            for event in plan['events'].get(index, []):
                events.append(dict(time=row[event['at']][0]['time'], text=event['text'],
                    actors=event['actors'], known_by=event['known_by'],
                    current_fact_ids=[lookup[tuple(k)]['fact_id'] for k in event['current_fields']]))
            if events:
                row = apply_history_plan(row, {'events': events}, facts)
            claims = plan.get('history_claims', {}).get(index)
            if plan.get('require_history_claims') and claims is None:
                raise ValueError('Missing explicit history claim plan: '+sid)
            if claims is not None:
                from history_claim_plan import validate_claim_plan
                row['history_claim_plan'] = validate_claim_plan(row, claims, facts)
            requirements = [dict(subject=s, predicate=p, mode='intentional_unknown' if v is None else 'known')
                            for s, p, v in plan['facts'].get(index, [])]
            validate_requirements(row, {'pass': True, 'reasons': [], 'requirements': requirements})
            reason = duplicate_reason(row, previous)
            if reason:
                raise ValueError(sid + ': ' + reason)
            previous.append(row)
            scenario = convert(row)
            messages = build_messages(scenario, anchor)
            coverage = validate_final_messages(scenario, messages)
            rows.append(row)
            drafts.append(dict(sample_id=sid, scenario=scenario, generation_messages=messages,
                               input_coverage=coverage, requirements=requirements,
                               component_sha256=digest(row),
                               frozen_input_sha256=digest({'component':row, 'messages':messages})))
    persist(out / 'components.json', rows)
    persist(out / 'draft_inputs.json', drafts)
    manifest = dict(count=len(rows), author='Codex', authoring_method='逐条编写新对白与联合世界计划；脚本仅装配和检查',
                    review_status='pending_explicit_review', local_model_calls=0, final_replies=0,
                    prior_rows_compared=prior_count, exact_or_near_chain_duplicates=0,
                    unique_families=len({r['component_ids']['family_id'] for r in rows}),
                    scenes=dict(Counter(r['component_ids']['scene_id'] for r in rows)),
                    tasks=dict(Counter(r['task_type'] for r in rows)),
                    source_hashes={p.name:file_hash(p) for p in sorted(source.iterdir()) if p.is_file()},
                    prior_hashes={str(p):file_hash(p) for p in prior_paths},
                    components_sha256=file_hash(out / 'components.json'),
                    draft_inputs_sha256=file_hash(out / 'draft_inputs.json'))
    persist(out / 'compile_manifest.json', manifest)
    lines = ['# 实际渲染后的输入核对摘要', '完整 messages 保存在 draft_inputs.json；本摘要不是模型输入。']
    for r in drafts:
        c = r['scenario']['component_source']
        lines += [f"\n## {c['sample_id']} {c['topic']}", f"组件哈希：{r['component_sha256']}",
                  f"快照：{c['snapshot']['time']} {c['snapshot']['weather']} {c['snapshot']['area']}",
                  '人物：'+json.dumps(c['snapshot']['people'], ensure_ascii=False),
                  '当前登记：'+json.dumps(c['snapshot']['states'], ensure_ascii=False),
                  '历史事件：'+json.dumps(c['historical_events'], ensure_ascii=False),
                  '已披露资料：'+json.dumps(c['known_profile'], ensure_ascii=False)]
        for kind, label in [('memory_turns','相关记忆'),('recent_turns','最近对白')]:
            lines.append(label+'：'+' / '.join(t['speaker']+'：'+t['text'] for t in c[kind]))
        lines += ['当前男主：'+c['current_user'], '覆盖检查：'+json.dumps(r['input_coverage'], ensure_ascii=False)]
    (out / 'inspection.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


def seal_batch(out):
    manifest = read(out / 'compile_manifest.json')
    for key, name in [('components_sha256','components.json'),('draft_inputs_sha256','draft_inputs.json')]:
        if manifest[key] != file_hash(out / name):
            raise ValueError('Compilation changed; recompile and re-review')
    for name, expected in manifest['source_hashes'].items():
        if file_hash(out / 'source' / name) != expected:
            raise ValueError('Authored source changed after compilation: '+name)
    reviews = read(out / 'review_ledger.json')
    if reviews['reviewed_draft_sha256'] != manifest['draft_inputs_sha256']:
        raise ValueError('Review ledger is stale')
    rows, drafts = read(out / 'components.json'), read(out / 'draft_inputs.json')
    notes = reviews['items']
    ids = [r['sample_id'] for r in rows]
    if set(notes) != set(ids) or len(notes) != len(ids):
        raise ValueError('Explicit review required for every attempted row')
    approvals = {}
    for row, draft in zip(rows, drafts):
        note = notes[row['sample_id']]
        if set(note) != {'decision','continuity','evidence','persona'}:
            raise ValueError('Incomplete individual review: '+row['sample_id'])
        if any(not isinstance(note[k],str) or len(note[k].strip())<8 for k in ('continuity','evidence','persona')):
            raise ValueError('Empty individual rationale')
        if note['decision'] not in ('approve','reject'):
            raise ValueError('Unknown review decision')
        if note['decision'] != 'approve':
            continue
        approvals[row['sample_id']] = dict(reviewer='Codex', decision=note['decision'],
            review_run_id=reviews['review_run_id'], provenance='external_codex_review',
            review_method='同一 Codex 编排后的逐条自审；非独立第二模型审核',
            component_sha256=digest(row), requirements=draft['requirements'],
            notes='；'.join(note[k] for k in ('continuity','evidence','persona')))
    approved_rows = [r for r in rows if r['sample_id'] in approvals]
    summary = dict(review_run_id=reviews['review_run_id'], attempted=len(rows), approved=len(approvals),
        rejected=len(rows)-len(approvals), approved_ids=list(approvals),
        rejected_ids=[sid for sid in ids if sid not in approvals],
        review_method='Codex 逐条自审；不声称独立复核或人工审核',
        count_semantics='本版冻结输入审核；不代表初稿一次通过率或模型回复成功率',
        reviewed_draft_sha256=manifest['draft_inputs_sha256'], ledger_sha256=file_hash(out / 'review_ledger.json'))
    persist(out / 'input_approvals.json', approvals)
    persist(out / 'input_review_summary.json', summary)
    persist(out / 'approved_components.json', approved_rows)
    run(out / 'formal_preflight', approved_rows, approvals, preflight_only=True,
        input_review_summary=out / 'input_review_summary.json')
    frozen = read(out / 'formal_preflight/inputs/frozen_inputs.json')
    by_id = {r['sample_id']:r for r in drafts}
    for r in frozen:
        if r['frozen_input_sha256'] != by_id[r['sample_id']]['frozen_input_sha256']:
            raise ValueError('Formal renderer differs from reviewed draft')
    render_page(out, frozen, notes)
    persist(out / 'token_usage.json', dict(local_authoring_calls=0, local_reply_calls=0, local_audit_calls=0,
        local_model_tokens=0, codex_tokens=None, codex_tokens_status='本会话未获取可核验的逐批计费用量；不是零消耗',
        measurement='不以字符数推算实际计费用量'))
    persist(out / 'sealed_ready.json', dict(status='sealed', files={
        name:file_hash(out / name) for name in ('approved_components.json', 'input_approvals.json',
        'input_review_summary.json', 'formal_preflight/inputs/frozen_inputs.json')}))
    print(json.dumps(summary, ensure_ascii=False))


def render_page(out, records, notes):
    esc = html.escape
    parts = [f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Codex 新编排 {len(records)} 条输入</title>',
        '<style>body{font:16px/1.7 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;background:#f5f5f2}article{background:white;padding:22px;margin:18px 0;border-radius:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:12px}summary{cursor:pointer}h2{font-size:20px}.note{color:#48624b}</style>',
        f'<h1>{len(records)} 条新编排输入 · 逐条审核记录</h1><p>这是冻结输入，尚未生成最终女主回复。每条包含 4 条相关记忆对白、4 条最近对白、当前男主发言和完整权威上下文。审核为 Codex 自审，不冒称第二模型或用户审核。</p>',
        '<label>查找编号或话题 <input id="search" placeholder="例如 016 或 遥控器"></label>']
    for r in records:
        c = r['scenario']['component_source']; sid = c['sample_id']; note = notes[sid]
        parts += ['<article>', '<h2>'+esc(sid+' · '+c['topic'])+'</h2>',
                  '<p>'+esc(c['snapshot']['time']+' · '+c['snapshot']['weather']+' · '+c['snapshot']['area'])+'</p>']
        for kind,label in [('memory_turns','相关记忆对白'),('recent_turns','最近对白')]:
            parts += ['<h3>'+label+'</h3><pre>'+esc('\n'.join(t['time']+' '+t['speaker']+'：'+t['text'] for t in c[kind]))+'</pre>']
        parts += ['<p><strong>当前男主：</strong>'+esc(c['current_user'])+'</p>',
                  '<p class="note">'+esc('审核：'+note['decision']+'。'+'；'.join(note[k] for k in ('continuity','evidence','persona')))+'</p>',
                  '<details><summary>本条明确登记的快照与历史事件</summary><pre>'+esc(json.dumps({'snapshot':c['snapshot'],'historical_events':c['historical_events'],'known_profile':c['known_profile']}, ensure_ascii=False, indent=2))+'</pre></details>',
                  '<details><summary>实际将发给模型的完整消息（含全量场景、未知状态和人设边界）</summary><pre>'+esc(json.dumps(r['generation_messages'],ensure_ascii=False,indent=2))+'</pre></details>',
                  '<details><summary>冻结哈希与覆盖检查</summary><pre>'+esc(json.dumps({'sha256':r['frozen_input_sha256'],'coverage':r['input_coverage']},ensure_ascii=False,indent=2))+'</pre></details></article>']
    parts += ['<script>document.querySelector("#search").addEventListener("input",e=>{for(const a of document.querySelectorAll("article"))a.hidden=!a.textContent.includes(e.target.value)});</script></html>']
    (out / 'input_review.html').write_text('\n'.join(parts), encoding='utf-8')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('phase', choices=['compile','seal'])
    p.add_argument('--output-root', type=Path, required=True)
    args = p.parse_args()
    (compile_batch if args.phase == 'compile' else seal_batch)(args.output_root)
