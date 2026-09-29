"""Run one explicitly reviewed campaign batch and persist usage on exit.

Never authors or approves inputs and never advances to an unreviewed batch.
"""
import argparse
import json
from pathlib import Path
from run_curated_candidate_batch import run, read, persist, now
from summarize_curated_reply_usage import summarize


def sync_completed_status(root):
    out = root/'formal_run'
    if read(out/'batch_status.json')['status'] != 'complete':
        raise ValueError('Cannot mark an incomplete batch complete')
    state=root/'execution_status.json'
    if state.exists():
        previous=read(state)
        archive=root/'execution_status_before_flow_sync.json'
        if previous.get('status')=='awaiting_codex_quality_review' and not archive.exists():
            persist(archive,previous)
    persist(state, {'status':'machine_processing_complete',
        'report':read(out/'report.json'),'updated_at':now(),
        'codex_reply_review_required':False,'human_queue_blocks_next_batch':False,
        'next_batch_started':False,'training_released':0})


def execute(root):
    out = root / 'formal_run'
    state = root / 'execution_status.json'
    persist(state, {'status': 'running', 'started_at': now(),
                    'scope': 'one_frozen_batch', 'training_released': 0})
    try:
        run(out, read(root/'approved_components.json'), read(root/'input_approvals.json'),
            input_review_summary=root/'input_review_summary.json')
    except Exception as error:
        persist(state, {'status': 'failed', 'error': str(error), 'updated_at': now(),
                        'training_released': 0})
        raise
    else:
        sync_completed_status(root)
    finally:
        if (out/'requests').exists():
            summarize(out)


if __name__ == '__main__':
    p=argparse.ArgumentParser();p.add_argument('--batch-root',type=Path,required=True)
    p.add_argument('--sync-status-only',action='store_true')
    a=p.parse_args()
    (sync_completed_status if a.sync_status_only else execute)(a.batch_root)
