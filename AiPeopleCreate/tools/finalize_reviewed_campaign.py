"""Finish an already running frozen campaign; never author or approve replies.

Wait for the existing queue, perform the single authorized format-only repair,
then aggregate actual results/usage and build a machine-failure review page.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time

from run_reviewed_campaign_queue import ready
from run_curated_candidate_batch import read, persist, now


def inspect(root, count):
    batches = [root / f'batch{i:02d}' for i in range(1, count + 1)]
    ids = []
    for batch in batches:
        if not ready(batch):
            raise ValueError(f'Input not completely sealed: {batch.name}')
        rows = read(batch / 'approved_components.json')
        if len(rows) != 100:
            raise ValueError(f'Expected 100 frozen inputs: {batch.name}')
        ids.extend(row['sample_id'] for row in rows)
    if len(set(ids)) != len(ids):
        raise ValueError('Cross-batch sample ID collision')
    return batches, set(ids)


def command(script, *args):
    subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name(script)), *map(str, args)], check=True)


def finish(root, count, wait_seconds):
    batches, expected_ids = inspect(root, count)
    out = root / 'campaign_finalization'
    out.mkdir(exist_ok=True)
    state = out / 'status.json'
    if state.exists() and read(state).get('status') == 'complete':
        print('Already finalized; no model calls repeated.', flush=True)
        return
    start = time.monotonic()
    last_queue = None
    while True:
        queue = read(root / 'queue_status.json')
        if queue.get('status') == 'failed':
            raise RuntimeError(f'Existing queue failed: {queue.get("error")}')
        if queue.get('status') == 'complete':
            if queue.get('last_batch') != count:
                raise ValueError('Queue completed a different batch range')
            break
        if queue != last_queue:
            persist(state, dict(status='waiting_existing_queue', queue=queue, updated_at=now(),
                                frozen_candidates=len(expected_ids), released_training_rows=0))
            print(json.dumps(queue, ensure_ascii=False), flush=True)
            last_queue = queue
        if time.monotonic() - start > wait_seconds:
            raise TimeoutError('Queue did not complete within the configured wait; inspect progress before restarting')
        time.sleep(30)
    for batch in batches:
        formal = batch / 'formal_run'
        if read(batch / 'execution_status.json').get('status') != 'machine_processing_complete':
            raise ValueError(f'Missing completed machine processing: {batch.name}')
        records = read(formal / 'results.json')
        if len(records) != 100:
            raise ValueError(f'Incomplete results: {batch.name}')
        failures = [r for r in records if r.get('status') == 'generation_failed'
                    and not r.get('target') and not r.get('format_retry_attempted')]
        if failures:
            persist(state, dict(status='format_only_retry', batch=batch.name,
                                sample_ids=[r['sample_id'] for r in failures], updated_at=now()))
            command('retry_curated_format_failures.py', '--output-root', formal)
        command('summarize_curated_reply_usage.py', '--output-root', formal)
        command('run_candidate_campaign_batch.py', '--batch-root', batch, '--sync-status-only')
    records = [r for batch in batches for r in read(batch / 'formal_run/results.json')]
    if {r['sample_id'] for r in records} != expected_ids or len(records) != len(expected_ids):
        raise ValueError('Final result IDs differ from the sealed inputs')
    usage = [read(batch / 'formal_run/token_usage.json') for batch in batches]
    statuses = Counter(r['status'] for r in records)
    report = dict(target_candidates=len(expected_ids), processed_candidates=len(records),
                  valid_replies=sum(bool((r.get('target') or {}).get('reply', '').strip()) for r in records),
                  machine_pass=sum(r['status'] == 'approved' and r.get('report', {}).get('approved', False) for r in records),
                  statuses=dict(statuses), format_retry_slots=sum(bool(r.get('format_retry_attempted')) for r in records),
                  local_recorded_tokens=sum(u['total_recorded_tokens'] for u in usage),
                  codex_tokens=None, codex_tokens_note='No verified per-batch billed Codex usage is available; not zero.',
                  training_released=0, finished_at=now())
    persist(out / 'results.json', records)
    persist(out / 'report.json', report)
    persist(out / 'token_usage.json', dict(batches=usage, total_recorded_tokens=report['local_recorded_tokens'], codex_tokens=None))
    pending = out / 'human_review_pending'
    # An interrupted page build is preserved. A restart gets a new directory.
    suffix = 1
    while pending.exists():
        suffix += 1
        pending = out / f'human_review_pending_{suffix}'
    command('build_pending_human_review.py', '--results', out / 'results.json', '--output-root', pending)
    campaign = read(root / 'campaign.json')
    campaign.update(status='candidate_processing_complete', authored_candidates=len(records),
                    frozen_candidates=len(records), remaining_unwritten_candidates=0,
                    final_report=str((out / 'report.json').resolve()),
                    human_review_page=str((pending / 'review.html').resolve()), training_released=0)
    persist(root / 'campaign.json', campaign)
    persist(state, dict(status='complete', report=report, human_review_page=str((pending / 'review.html').resolve()), updated_at=now()))
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--batches', type=int, default=10)
    parser.add_argument('--wait-seconds', type=int, default=86400)
    parser.add_argument('--check', action='store_true', help='Read-only sealed-input validation; no model calls or writes')
    args = parser.parse_args()
    if args.check:
        batches, ids = inspect(args.root, args.batches)
        print(json.dumps(dict(sealed_batches=len(batches), frozen_candidates=len(ids), model_calls=0)))
    else:
        try:
            finish(args.root, args.batches, args.wait_seconds)
        except Exception as error:
            persist(args.root / 'campaign_finalization/status.json', dict(status='failed', error=str(error), updated_at=now()))
            raise
