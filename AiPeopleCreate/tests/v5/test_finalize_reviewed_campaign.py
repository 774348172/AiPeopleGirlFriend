import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import finalize_reviewed_campaign as worker


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def test_missing_seal_prevents_all_processing(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'ready', lambda _: False)
    with pytest.raises(ValueError, match='not completely sealed'):
        worker.finish(tmp_path, 10, 1)
    assert not (tmp_path / 'campaign_finalization').exists()


def test_only_empty_unretried_slot_is_repaired_and_restarts_do_not_repeat(tmp_path, monkeypatch):
    batch = tmp_path / 'batch01'
    rows = [dict(sample_id=str(i), status='approved', target={'reply': '你好'}, report={'approved': True}) for i in range(100)]
    rows[0].update(status='generation_failed', target=None)
    rows[1].update(status='content_rejected', report={'approved': False})
    rows[2].update(status='generation_failed', target=None, format_retry_attempted=True)
    write(batch / 'formal_run/results.json', rows)
    write(batch / 'execution_status.json', {'status': 'machine_processing_complete'})
    write(batch / 'formal_run/token_usage.json', {'total_recorded_tokens': 123})
    write(tmp_path / 'queue_status.json', {'status': 'complete', 'last_batch': 1})
    write(tmp_path / 'campaign.json', {})
    monkeypatch.setattr(worker, 'inspect', lambda *_: ([batch], {r['sample_id'] for r in rows}))
    calls = []

    def fake_command(script, *args):
        calls.append(script)
        if script == 'retry_curated_format_failures.py':
            rows[0].update(target={'reply': '修复后的对白'}, status='approved', format_retry_attempted=True)
            write(batch / 'formal_run/results.json', rows)

    monkeypatch.setattr(worker, 'command', fake_command)
    worker.finish(tmp_path, 1, 1)
    state = worker.read(tmp_path / 'campaign_finalization/status.json')
    assert state['status'] == 'complete'
    assert state['report']['processed_candidates'] == 100
    assert state['report']['valid_replies'] == 99
    assert state['report']['machine_pass'] == 98
    assert state['report']['training_released'] == 0
    assert state['report']['local_recorded_tokens'] == 123
    assert state['report']['codex_tokens'] is None
    saved = worker.read(tmp_path / 'campaign_finalization/results.json')
    assert saved[1]['status'] == 'content_rejected'
    assert saved[2]['target'] is None
    assert calls.count('retry_curated_format_failures.py') == 1
    before = calls.copy()
    worker.finish(tmp_path, 1, 1)
    assert calls == before


def test_queue_failure_never_starts_second_model_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, 'inspect', lambda *_: ([], set()))
    write(tmp_path / 'queue_status.json', {'status': 'failed', 'error': 'frozen hash changed'})
    monkeypatch.setattr(worker, 'command', lambda *_: pytest.fail('must not invoke models'))
    with pytest.raises(RuntimeError, match='frozen hash changed'):
        worker.finish(tmp_path, 10, 1)
    assert not (tmp_path / 'campaign_finalization/report.json').exists()
