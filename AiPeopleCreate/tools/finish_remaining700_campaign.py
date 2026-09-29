"""Finish an already-running sealed 04--10 campaign without another GPU queue.

Wait for the existing worker, perform only its allowed missing-target retries,
then aggregate provenance, actual local usage, and machine-only pending review.
Does not author, semantically rejudge replies, export training, or train.
"""
import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

from run_reviewed_campaign_queue import ready
from run_candidate_campaign_batch import read, persist, now, sync_completed_status
from retry_curated_format_failures import retry
from summarize_curated_reply_usage import summarize
from build_pending_human_review import build


def finish(root):
    status = root / 'remaining700_finish_status.json'
    previous = read(status) if status.exists() else {}
    if previous.get('status') == 'complete':
        print('Completion already recorded; no rerun.', flush=True)
        return
    if previous.get('pid') and previous.get('status') not in ('failed', 'complete'):
        try:
            os.kill(previous['pid'], 0)
        except OSError:
            pass
        else:
            raise RuntimeError('Another completion worker is active')
    batches = [root / f'batch{n:02d}' for n in range(4, 11)]
    if not all(ready(b) for b in batches):
        raise ValueError('All 700 inputs must already be sealed')
    if any(len(read(b/'approved_components.json')) != 100 for b in batches):
        raise ValueError('Expected seven sealed 100-input batches')
    def state(stage, **extra):
        persist(status, dict(status=stage, pid=os.getpid(), updated_at=now(), **extra))
    state('waiting_existing_queue')
    try:
        last_log = 0
        while True:
            q = read(root/'queue_status.json')
            if q.get('status') == 'failed':
                raise RuntimeError('Existing queue failed: ' + q.get('error', 'unknown'))
            if q.get('status') == 'complete':
                if q.get('last_batch') != 10:
                    raise ValueError('Existing queue did not finish batch10')
                break
            if time.monotonic() - last_log >= 60:
                active = q.get('active_batch')
                p = root/f'batch{active:02d}'/'formal_run/progress.json' if active else None
                progress = read(p) if p and p.exists() else {}
                print(json.dumps(dict(queue=q.get('status'), batch=active,
                    phase=progress.get('phase'), processed=progress.get('processed'),
                    sample=progress.get('active_sample')), ensure_ascii=False), flush=True)
                state('waiting_existing_queue', active_batch=active, phase=progress.get('phase'),
                      processed=progress.get('processed'))
                last_log = time.monotonic()
            time.sleep(15)
        for b in batches:
            if read(b/'execution_status.json').get('status') != 'machine_processing_complete':
                raise ValueError('Batch not complete: ' + b.name)
        for b in batches:
            out = b/'formal_run'
            records = read(out/'results.json')
            missing = [r for r in records if r.get('status') == 'generation_failed'
                       and not r.get('target') and not r.get('format_retry_attempted')]
            state('format_repair_and_accounting', active_batch=b.name, missing_targets=len(missing))
            if missing:
                retry(out)
            summarize(out)
            sync_completed_status(b)
        state('aggregating')
        records, sources, by_batch = [], [], []
        for b in batches:
            path = b/'formal_run/results.json'
            rows = read(path)
            if len(rows) != 100:
                raise ValueError('Result count mismatch: ' + b.name)
            records.extend(rows)
            usage = read(b/'formal_run/token_usage.json')
            sources.append(dict(batch=b.name, path=str(path.resolve()),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            by_batch.append(dict(batch=b.name, frozen=100, processed=len(rows),
                valid_replies=sum(bool(r.get('target', {}).get('reply', '').strip()) for r in rows),
                statuses=dict(Counter(r['status'] for r in rows)),
                auto_pass=sum(bool(r.get('report', {}).get('approved')) for r in rows),
                format_retry_slots=sum(bool(r.get('format_retry_attempted')) for r in rows),
                local_tokens=usage['total_recorded_tokens']))
        if len({r['sample_id'] for r in records}) != 700:
            raise ValueError('Duplicate or missing result IDs')
        aggregate = root/'remaining700_results.json'
        persist(aggregate, records)
        queue = root/'remaining700_human_review'
        if queue.exists():
            if read(queue/'queue_manifest.json')['source_sha256'] != hashlib.sha256(aggregate.read_bytes()).hexdigest():
                raise ValueError('Existing human queue source changed; preserve and choose a new directory')
        else:
            build(aggregate, queue)
        summary = dict(status='complete', completed_at=now(), scope='batch04--batch10',
            requested=700, frozen=700, processed=700,
            valid_replies=sum(b['valid_replies'] for b in by_batch),
            auto_pass=sum(b['auto_pass'] for b in by_batch),
            statuses=dict(Counter(r['status'] for r in records)),
            format_retry_slots=sum(b['format_retry_slots'] for b in by_batch),
            local_recorded_tokens=sum(b['local_tokens'] for b in by_batch),
            codex_tokens=None, codex_tokens_note='No verifiable per-batch billing interface; not zero.',
            pending_human_count=read(queue/'queue_manifest.json')['pending_count'],
            human_review=str((queue/'review.html').resolve()),
            training_released=0, sources=sources, batches=by_batch)
        persist(root/'remaining700_summary.json', summary)
        lines = ['# 剩余700条候选生产完成记录', '',
            '范围：batch04–batch10；旧300条和更早200条未混入本报告。',
            'Codex仅编排及自审输入；本地Qwen回复，Gemma审核，无新增Codex回复复核。', '',
            f"已冻结700条，处理700条，有效回复{summary['valid_replies']}条，机器通过{summary['auto_pass']}条。",
            '状态：'+json.dumps(summary['statuses'], ensure_ascii=False),
            f"本地记录token：{summary['local_recorded_tokens']}；Codex精确计费用量未知，未记为零。",
            '仅候选，不等于训练放行。已有人工裁决目录未覆盖。', '',
            '[仅机器或程序未通过项审核页](remaining700_human_review/review.html)', '',
            '| 批次 | 有效回复 | 机器通过 | 格式重试槽位 | 本地token |',
            '|---|---:|---:|---:|---:|']
        lines += [f"| {b['batch']} | {b['valid_replies']} | {b['auto_pass']} | {b['format_retry_slots']} | {b['local_tokens']} |" for b in by_batch]
        (root/'remaining700_completion.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        state('complete', summary='remaining700_summary.json', training_released=0)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    except Exception as error:
        state('failed', error=str(error))
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    finish(p.parse_args().root)
