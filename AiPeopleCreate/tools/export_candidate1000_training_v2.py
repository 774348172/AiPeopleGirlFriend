"""Release the user-authorized effective campaign pool, preserving full inputs."""
import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def jsonl(p, rows):
    p.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')


def export(root, review, out):
    applied = root/'human93_applied_20260917'
    sample_manifest = read(applied/'spotcheck15/sampling_manifest.json')
    effective_path = applied/'effective_candidates.json'
    if sha(effective_path) != sample_manifest['source_sha256']:
        raise ValueError('Effective pool changed after spot-check sampling')
    judgments = read(review)
    by_review = {r['sample_id']: r for r in judgments}
    if len(by_review) != len(judgments) or set(by_review) != set(sample_manifest['selected_ids']):
        raise ValueError('Spot-check review does not match exact sampled IDs')
    if any(r.get('verdict') not in ('good', 'bad') for r in judgments):
        raise ValueError('Incomplete spot-check decisions')
    human93 = {r['sample_id']:r for r in read(applied/'original_human_review.json')}
    original700 = {r['sample_id']:r for r in read(root/'remaining700_results.json')}
    selected = []
    sources = []
    all_records = []
    for n in range(1, 11):
        p = root/f'batch{n:02d}'/'formal_run/results.json'
        records = read(p)
        sources.append(dict(path=str(p.resolve()), sha256=sha(p)))
        all_records.extend(records)
        if n <= 3:
            selected.extend(r for r in records if r['status'] == 'approved' and r['report']['approved'])
    effective = read(effective_path)
    for r in effective:
        if r != original700[r['sample_id']]:
            raise ValueError('Effective record altered')
        h = human93.get(r['sample_id'])
        if h is not None and h.get('verdict') != 'good':
            raise ValueError('Excluded human row entered effective pool')
        if h is None and (r['status'] != 'approved' or not r['report']['approved']):
            raise ValueError('Unapproved record entered pool')
    selected.extend(effective)
    selected = [r for r in selected if by_review.get(r['sample_id'], {}).get('verdict') != 'bad']
    selected.sort(key=lambda r:r['sample_id'])
    if len({r['sample_id'] for r in selected}) != len(selected):
        raise ValueError('Duplicate sample IDs')
    train, metadata, duplicate_rows, seen = [], [], [], {}
    for r in selected:
        sid = r['sample_id']
        reply = (r.get('target') or {}).get('reply')
        if not isinstance(reply, str) or not reply.strip() or r['status'] == 'generation_failed':
            raise ValueError('Invalid target: '+sid)
        messages = r['generation_messages']
        if messages[-1]['role'] != 'user' or messages[0]['role'] != 'system':
            raise ValueError('Invalid conversation roles')
        system = '\n'.join(m['content'] for m in messages if m['role'] == 'system')
        for heading in ('[当前权威事实]', '[相关记忆]', '[明确未知]'):
            if heading not in system:
                raise ValueError('Incomplete context: '+sid)
        # Retain the original JSON reply contract; only the last answer is a target.
        completion = [dict(role='assistant', content=json.dumps({'reply':reply}, ensure_ascii=False))]
        row = dict(prompt=messages, completion=completion)
        key = digest(row)
        if key in seen:
            duplicate_rows.append(dict(sample_id=sid, duplicate_of=seen[key]))
            continue
        seen[key] = sid
        train.append(row)
        human = by_review.get(sid) or human93.get(sid)
        metadata.append(dict(line=len(train), sample_id=sid,
            acceptance='human_approved' if human and human.get('verdict') == 'good' else 'machine_approved_user_released',
            human_review=human, original_machine_status=r['status'],
            scene=r['scenario']['component_source']['component_ids']['scene_id'], task=r['scenario']['task_type'],
            source_record_sha256=digest(r), prompt_sha256=digest(messages), training_pair_sha256=key,
            frozen_input_sha256=r['frozen_input_sha256']))
    out.mkdir(parents=True, exist_ok=False)
    (out/'audit').mkdir()
    shutil.copyfile(review, out/'audit/spotcheck15_user_review.json')
    shutil.copyfile(applied/'original_human_review.json', out/'audit/quarantined93_user_review.json')
    shutil.copyfile(applied/'dispositions.json', out/'audit/remaining700_dispositions.json')
    accepted_ids = {m['sample_id'] for m in metadata}
    jsonl(out/'train.jsonl', train)
    jsonl(out/'metadata.jsonl', metadata)
    write(out/'audit/accepted_source_records.json', [r for r in selected if r['sample_id'] in accepted_ids])
    excluded = []
    for r in all_records:
        sid = r['sample_id']
        if sid in accepted_ids:
            continue
        human = by_review.get(sid) or human93.get(sid)
        reason = ('human_rejected' if human and human.get('verdict') == 'bad' else
                  'discarded_unreviewed' if human is not None and human.get('verdict') != 'good' else
                  'not_approved_or_duplicate')
        excluded.append(dict(sample_id=sid, reason=reason, original_status=r['status']))
    write(out/'audit/exclusions.json', excluded)
    manifest = dict(dataset='训练数据V2', source_campaign=str(root.resolve()),
        scope='当前candidate1000任务全部有效集合；不混入更早试验批次',
        authorization='用户明确要求所有有效回复纳入训练数据V2；未授权启动训练',
        source_count=len(all_records), released_count=len(train), excluded_count=len(excluded),
        duplicate_pairs=duplicate_rows, review_counts=dict(Counter(r['verdict'] for r in judgments)),
        acceptance_counts=dict(Counter(m['acceptance'] for m in metadata)),
        scene_counts=dict(Counter(m['scene'] for m in metadata)), task_counts=dict(Counter(m['task'] for m in metadata)),
        source_files=sources, effective_pool_sha256=sha(effective_path), spotcheck_review_sha256=sha(review),
        train_sha256=sha(out/'train.jsonl'), metadata_sha256=sha(out/'metadata.jsonl'),
        format='conversational prompt/completion JSONL', loss_scope='completion_only',
        input_transform='none; exact saved generation_messages',
        target_format='JSON object serialized as assistant content, matching original reply contract',
        trained=False, evaluation_split_created=False)
    write(out/'manifest.json', manifest)
    print(json.dumps({k:manifest[k] for k in ('released_count','excluded_count','acceptance_counts','duplicate_pairs')}, ensure_ascii=False))


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a=p.parse_args()
    export(a.root,a.review,a.output)
