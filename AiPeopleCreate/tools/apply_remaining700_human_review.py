"""Apply an explicit human queue decision and build a reproducible spot-check.

Original model results and human decisions remain immutable audit evidence.
Unjudged queue rows are excluded only when this explicitly requested tool runs.
"""
import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def main(root, review, out):
    source = root/'remaining700_results.json'
    queue = read(root/'remaining700_human_review/queue_manifest.json')
    if sha(source) != queue['source_sha256']:
        raise ValueError('Reviewed queue source changed')
    records, judgments = read(source), read(review)
    by_id = {r['sample_id']: r for r in records}
    decisions = {r['sample_id']: r for r in judgments}
    if len(by_id) != len(records) or len(decisions) != len(judgments):
        raise ValueError('Duplicate IDs')
    if set(decisions) != set(queue['selected_ids']):
        raise ValueError('Review IDs do not match the exact 93-row queue')
    accepted, excluded, ledger = [], [], []
    for r in records:
        sid = r['sample_id']
        human = decisions.get(sid)
        verdict = human.get('verdict') if human else None
        if verdict not in (None, '', 'good', 'bad'):
            raise ValueError('Unexpected verdict: '+sid)
        valid = bool((r.get('target') or {}).get('reply', '').strip()) and r['status'] != 'generation_failed'
        if human is not None:
            outcome = 'human_approved' if verdict == 'good' else 'human_rejected' if verdict == 'bad' else 'discarded_unreviewed'
            if verdict == 'good' and not valid:
                raise ValueError('Cannot approve invalid reply: '+sid)
        elif valid and r['status'] == 'approved' and r.get('report', {}).get('approved'):
            outcome = 'machine_approved'
        else:
            raise ValueError('Non-approved row missing from queue: '+sid)
        (accepted if outcome in ('human_approved', 'machine_approved') else excluded).append(r)
        ledger.append(dict(sample_id=sid, disposition=outcome, human_verdict=verdict,
                           note=(human or {}).get('note', ''), original_machine_status=r['status']))
    # Draw new checks only: one of each task in each scene, from machine approvals.
    rng = random.Random(202609170015)
    eligible = [r for r in accepted if r['sample_id'] not in decisions]
    scenes = sorted({r['scenario']['component_source']['component_ids']['scene_id'] for r in eligible})
    tasks = ['idle_chat', 'emotion_attunement', 'follow_up_question']
    selected = []
    for scene in scenes:
        for task in tasks:
            pool = sorted([r for r in eligible if r['scenario']['task_type'] == task and
                r['scenario']['component_source']['component_ids']['scene_id'] == scene], key=lambda r:r['sample_id'])
            if not pool:
                raise ValueError('Empty sampling stratum')
            selected.append(rng.choice(pool))
    if len(selected) != 15 or len({r['sample_id'] for r in selected}) != 15:
        raise ValueError('Incorrect sample count')
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(review, out/'original_human_review.json')
    write(out/'dispositions.json', ledger)
    write(out/'effective_candidates.json', accepted)
    write(out/'excluded_candidates.json', excluded)
    summary = dict(scope='batch04--batch10 only; prior 300 unchanged', source_count=len(records),
        source_sha256=sha(source), human_review_source=str(review.resolve()), human_review_sha256=sha(review),
        counts=dict(Counter(r['disposition'] for r in ledger)), effective_count=len(accepted),
        excluded_count=len(excluded), exclusion_is_recoverable=True, training_released=0,
        description='有效候选池：机器通过及人工通过；不等于全量人工认可或训练导出',
        sample_count=15, sampling_seed=202609170015,
        sampling_method='五场景各三条，每场景闲聊/情绪/事实各一条；排除已人工判断样本',
        sampled_ids=[r['sample_id'] for r in selected])
    write(out/'summary.json', summary)
    spot = out/'spotcheck15'
    (spot/'inputs').mkdir(parents=True)
    data = spot/'inputs/all_for_review.jsonl'
    data.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in selected), encoding='utf-8')
    write(spot/'batch_status.json', dict(status='complete', expected=15, review_sha256=sha(data)))
    write(spot/'sampling_manifest.json', dict(source=str((out/'effective_candidates.json').resolve()),
        source_sha256=sha(out/'effective_candidates.json'), selected_ids=summary['sampled_ids'],
        seed=summary['sampling_seed'], method=summary['sampling_method']))
    subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('build_conversational_review_page.py')),
        '--input', str(data), '--output', str(spot/'review.html'), '--expected', '15',
        '--group', 'follow_up_question', '--title', '有效候选池619条 · 五场景分层抽检15条'], check=True)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    main(a.root, a.review, a.output)
