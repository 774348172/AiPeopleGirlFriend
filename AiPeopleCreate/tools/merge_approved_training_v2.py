"""Stage/publish an authorized additive V2 release without rewriting prompts."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from export_candidate1000_training_v2 import read, sha, digest, write, jsonl


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def stage(base, source, out, expected, release_label, authorization, scope):
    if out.exists():
        raise ValueError('Use a new staging directory')
    previous = read(base / 'manifest.json')
    for name in ('train', 'metadata'):
        if sha(base / (name + '.jsonl')) != previous[name + '_sha256']:
            raise ValueError('Existing V2 file hash mismatch: ' + name)
    train, metadata = lines(base / 'train.jsonl'), lines(base / 'metadata.jsonl')
    if len(train) != len(metadata) or len(train) != previous['released_count']:
        raise ValueError('Existing V2 count mismatch')
    seen = {}
    ids = set()
    for row, meta in zip(train, metadata):
        key = digest(row)
        if key != meta['training_pair_sha256'] or key in seen or meta['sample_id'] in ids:
            raise ValueError('Existing V2 identity or pair mismatch')
        seen[key] = meta['sample_id']
        ids.add(meta['sample_id'])
    additions, exclusions = [], []
    records = read(source)
    if len({r['sample_id'] for r in records}) != len(records):
        raise ValueError('Duplicate incoming sample IDs')
    for r in records:
        sid = r['sample_id']
        if r['status'] != 'approved' or r.get('report', {}).get('approved') is not True:
            exclusions.append(dict(sample_id=sid, reason='not_machine_approved', status=r['status']))
            continue
        reply = (r.get('target') or {}).get('reply')
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError('Approved empty target: ' + sid)
        messages = r['generation_messages']
        if messages[0]['role'] != 'system' or messages[-1]['role'] != 'user':
            raise ValueError('Malformed saved input: ' + sid)
        for heading in ('[当前权威事实]', '[相关记忆]', '[明确未知]'):
            if heading not in messages[0]['content']:
                raise ValueError('Missing full context: ' + sid)
        row = dict(prompt=messages, completion=[dict(role='assistant', content=json.dumps({'reply': reply}, ensure_ascii=False))])
        key = digest(row)
        if sid in ids:
            raise ValueError('Incoming sample ID already released: ' + sid)
        if key in seen:
            exclusions.append(dict(sample_id=sid, reason='duplicate_pair', duplicate_of=seen[key]))
            continue
        train.append(row)
        metadata.append(dict(line=len(train), sample_id=sid,
            acceptance='machine_approved_user_released', human_review=None,
            original_machine_status=r['status'], scene=r['scenario']['component_source']['component_ids']['scene_id'],
            task=r['scenario']['task_type'], source_record_sha256=digest(r), prompt_sha256=digest(messages),
            training_pair_sha256=key, frozen_input_sha256=r['frozen_input_sha256'], source_file=str(source.resolve())))
        additions.append(r)
        seen[key] = sid
        ids.add(sid)
    if expected is not None and len(train) != expected:
        raise ValueError(f'Expected {expected} after dedup; found {len(train)}. Nothing published.')
    shutil.copytree(base, out)
    audit = out / 'audit' / ('release_' + release_label)
    audit.mkdir()
    write(audit / 'previous_manifest.json', previous)
    write(audit / 'accepted_source_records.json', additions)
    write(audit / 'exclusions.json', exclusions)
    jsonl(out / 'train.jsonl', train)
    jsonl(out / 'metadata.jsonl', metadata)
    manifest = dict(dataset='训练数据V2', released_count=len(train),
        previous_released_count=previous['released_count'], new_released_count=len(additions),
        incoming_source_count=len(records), incoming_excluded_count=len(exclusions),
        release_label=release_label, scope=scope, authorization=authorization,
        previous_manifest_sha256=sha(base / 'manifest.json'),
        source_files=[dict(path=str(source.resolve()), sha256=sha(source))],
        acceptance_counts=dict(Counter(m['acceptance'] for m in metadata)),
        scene_counts=dict(Counter(m['scene'] for m in metadata)),
        task_counts=dict(Counter(m['task'] for m in metadata)),
        duplicate_pairs=[r for r in exclusions if r['reason'] == 'duplicate_pair'],
        train_sha256=sha(out / 'train.jsonl'), metadata_sha256=sha(out / 'metadata.jsonl'),
        format=previous['format'], loss_scope=previous['loss_scope'], input_transform=previous['input_transform'],
        target_format=previous['target_format'], trained=False, evaluation_split_created=False)
    write(out / 'manifest.json', manifest)
    human_count = manifest['acceptance_counts'].get('human_approved', 0)
    machine_count = manifest['acceptance_counts'].get('machine_approved_user_released', 0)
    (out / 'README.md').write_text(
        f'# 训练数据V2\n\n{release_label}追加发布，共{len(train)}条：保留原{previous["released_count"]}条，'
        f'追加本批{len(additions)}条机器通过回复。\n\n'
        f'累计人工逐条好评{human_count}条；累计机器通过后由用户授权入库{machine_count}条。'
        '机器通过不冒称逐条人工审核。\n\n'
        f'本批{len(exclusions)}条未放行或重复项未入库。原始实际模型输入逐条保留，'
        '包含人物设定、权威状态、未知、记忆、最近对白和当前问题。\n\n'
        'train.jsonl采用prompt/completion格式，只对最后completion计算loss；目标仍是原合同要求的JSON回复。'
        'metadata.jsonl逐行对应，保留来源、审核类别和哈希。未启动训练，未建立训练/评测划分。\n\n'
        f'audit目录保留历次审核档案；audit/release_{release_label}保存本批接收记录、排除记录和旧manifest。'
        '完整输入加目标去重结果见manifest.json。\n', encoding='utf-8')
    print(json.dumps(dict(staged=len(train), previous=previous['released_count'], added=len(additions), excluded=len(exclusions)), ensure_ascii=False))


def publish(base, staged, archive):
    base, staged, archive = base.resolve(), staged.resolve(), archive.resolve()
    if base.name != '训练数据V2' or staged.parent != base.parent or archive.parent.parent != base.parent:
        raise ValueError('Unexpected publish paths')
    if archive.exists():
        raise ValueError('Archive already exists; do not overwrite it')
    manifest = read(staged / 'manifest.json')
    if sha(base / 'manifest.json') != manifest['previous_manifest_sha256']:
        raise ValueError('Base changed after staging')
    for name in ('train', 'metadata'):
        if sha(staged / (name + '.jsonl')) != manifest[name + '_sha256']:
            raise ValueError('Staged file hash mismatch')
    archive.parent.mkdir(exist_ok=True)
    base.rename(archive)
    try:
        staged.rename(base)
    except Exception:
        archive.rename(base)
        raise
    print(json.dumps(dict(released_count=manifest['released_count'], archived_previous=str(archive)), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=['stage', 'publish'])
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--staged', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--expected', type=int)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--release-label')
    parser.add_argument('--authorization')
    parser.add_argument('--scope')
    args = parser.parse_args()
    if args.phase == 'stage':
        if not args.source or not args.release_label or not args.authorization or not args.scope:
            parser.error('stage requires --source, --release-label, --authorization and --scope')
        stage(args.base, args.source, args.staged, args.expected, args.release_label,
              args.authorization, args.scope)
    else:
        publish(args.base, args.staged, args.archive)
