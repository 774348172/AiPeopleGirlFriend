"""Route machine failures and recorded semantic concerns to human review.

This creates a separate queue, never changes machine verdicts or releases rows.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def select_pending(records, semantic=None):
    by_id = {r['sample_id']: r for r in records}
    if len(by_id) != len(records):
        raise ValueError('Duplicate machine sample IDs')
    reviews = {}
    if semantic is not None:
        reviews = {r['sample_id']: r for r in semantic}
        if len(reviews) != len(semantic) or set(reviews) != set(by_id):
            raise ValueError('Semantic review must cover the identical batch IDs')
        for sid, r in reviews.items():
            for field in ('target', 'generation_messages', 'scenario'):
                if r[field] != by_id[sid][field]:
                    raise ValueError(f'Semantic review changed {field}: {sid}')
    selected, reasons = [], {}
    for r in records:
        sid = r['sample_id']
        why = []
        if r.get('status') != 'approved' or not r.get('report', {}).get('approved'):
            why.append('审核结果无效：审核器输出不一致或引用不合法，不能等同内容错误。' if r.get('status')=='audit_invalid'
                       else 'Gemma及程序未放行：请判断具体拒绝理由是否成立。')
        if 'duplicate_reply' in r.get('report', {}).get('reasons', []):
            why.append('重复提示：相同短答不等于错误；请按本条上下文判断，不因其他条也答“好”而直接判差。')
        s = reviews.get(sid)
        if s:
            if s.get('semantic_approved') is False:
                why.append('Codex文件复核提出内容疑点（非人工金标准）：'+s.get('review_note', ''))
                why.extend('Codex：'+v for v in s.get('report', {}).get('reasons', []))
            if s.get('borderline'):
                why.append('边界案例，请你裁决：'+s.get('review_note', ''))
            if s.get('report', {}).get('approved') != r.get('report', {}).get('approved'):
                why.append('Gemma与Codex最终结论不同，请结合完整输入判断。')
        if why:
            selected.append(r)
            reasons[sid] = list(dict.fromkeys(why))
    return selected, reasons


def build(source, out, semantic_path=None, title=None):
    if out.exists():
        raise ValueError('Use a new queue directory to preserve prior human judgments')
    records = read(source)
    semantic = read(semantic_path) if semantic_path else None
    rows, reasons = select_pending(records, semantic)
    out.mkdir(parents=True)
    (out/'inputs').mkdir()
    def write(name, value):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    source_bytes = source.read_bytes()
    manifest = {
        'status': 'awaiting_human_review' if rows else 'no_flagged_items',
        'source': str(source.resolve()), 'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'source_count': len(records), 'pending_count': len(rows),
        'selected_ids': [r['sample_id'] for r in rows],
        'auditors': sorted({r.get('audit_model', 'unknown') for r in records}),
        'selection': 'machine_or_program_not_approved OR semantic_rejection OR borderline OR disagreement',
        'semantic_review_source': str(semantic_path.resolve()) if semantic_path else None,
        'semantic_review_sha256': hashlib.sha256(semantic_path.read_bytes()).hexdigest() if semantic_path else None,
        'semantic_review_present': semantic is not None,
        'released_training_rows': 0,
        'unselected_are_not_human_approved': True,
    }
    write('queue_manifest.json', manifest)
    write('independent_review.json', reasons)
    data = ''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows)
    path = out/'inputs/all_for_review.jsonl'
    path.write_text(data, encoding='utf-8')
    write('batch_status.json', {'status': 'complete', 'expected': len(rows),
        'review_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    groups = sorted({r['scenario']['task_type'] for r in rows})
    cmd = [sys.executable, '-X', 'utf8', str(Path(__file__).with_name('build_conversational_review_page.py')),
        '--input', str(path), '--output', str(out/'review.html'), '--expected', str(len(rows)),
        '--title', title or f'Gemma 31B QAT · 待你裁决 {len(rows)} 条（原批 {len(records)} 条）']
    for group in groups:
        cmd.extend(['--group', group])
    subprocess.run(cmd, check=True)
    print(json.dumps(manifest, ensure_ascii=False))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--semantic-results', type=Path)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    build(args.results, args.output_root, args.semantic_results)
