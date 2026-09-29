"""Sequential local execution of explicitly frozen campaign batches.

Waiting for authored input is reported honestly; this worker never authors it.
"""
import argparse
import hashlib
import time
from pathlib import Path
from run_candidate_campaign_batch import execute, read, persist, now


def ready(batch):
    marker=batch/'sealed_ready.json'
    if not marker.exists():return False
    sealed=read(marker)
    required={'approved_components.json','input_approvals.json','input_review_summary.json',
              'formal_preflight/inputs/frozen_inputs.json'}
    if sealed.get('status')!='sealed' or set(sealed.get('files',{}))!=required:
        raise ValueError('Invalid complete-seal marker')
    for name, expected in sealed['files'].items():
        path=batch/name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Frozen file changed after sealing: '+name)
    return True


def queue(root, first, last, wait_for):
    state=root/'queue_status.json'
    for n in range(first,last+1):
        batch=root/f'batch{n:02d}'
        if n>1 and (n>first or wait_for):
            prior=root/f'batch{n-1:02d}'/'execution_status.json'
            while True:
                previous=read(prior) if prior.exists() else {}
                if previous.get('status')=='machine_processing_complete':break
                if previous.get('status')=='failed':raise RuntimeError('Previous batch failed; inspect logs')
                persist(state,dict(status='waiting_previous_batch',next_batch=n,updated_at=now()))
                time.sleep(15)
        existing=read(batch/'execution_status.json') if (batch/'execution_status.json').exists() else {}
        if existing.get('status')=='machine_processing_complete':continue
        while not ready(batch):
            persist(state,dict(status='waiting_authored_input',next_batch=n,
                note='No automatic authoring; this batch has not entered model generation',updated_at=now()))
            time.sleep(15)
        existing=read(batch/'execution_status.json') if (batch/'execution_status.json').exists() else {}
        if existing.get('status')=='machine_processing_complete':continue
        if existing.get('status') in ('running','failed'):raise RuntimeError('Batch already running or failed; inspect before resuming')
        persist(state,dict(status='running',active_batch=n,updated_at=now()))
        execute(batch)
    persist(state,dict(status='complete',last_batch=last,updated_at=now(),training_released=0))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--first',type=int,required=True);p.add_argument('--last',type=int,required=True)
    p.add_argument('--wait-for-previous',action='store_true');a=p.parse_args()
    try:queue(a.root,a.first,a.last,a.wait_for_previous)
    except Exception as e:
        persist(a.root/'queue_status.json',dict(status='failed',error=str(e),updated_at=now()))
        raise
