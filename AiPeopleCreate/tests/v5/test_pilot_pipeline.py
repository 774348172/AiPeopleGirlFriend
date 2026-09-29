import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from pilot_pipeline import run_stages


def response(value):
    return {'message': {'content': json.dumps(value)}, 'done_reason': 'stop'}


def test_audit_failure_retains_saved_answer_and_raw_attempts(tmp_path):
    path = tmp_path / 'row.json'
    calls = []
    def generate():
        calls.append('generate')
        return response({'reply': '我不知道。'})
    def audit(target, attempt):
        saved = json.loads(path.read_text(encoding='utf-8'))
        assert saved['target'] == target
        calls.append('audit')
        return response({'reply': 'wrong schema'})
    row = run_stages({}, path, generate, audit, lambda *args: pytest.fail('must not evaluate'), json.loads)
    assert calls == ['generate', 'audit', 'audit', 'audit']
    assert row['status'] == 'audit_failed'
    assert row['target']['reply'] == '我不知道。'
    assert len(row['attempts']) == 3
    assert all('response' in a and 'error' in a for a in row['attempts'])
    assert not row['report']['approved']


def test_empty_generation_never_enters_audit(tmp_path):
    row = run_stages({}, tmp_path / 'row.json', lambda: response({'reply': ''}),
        lambda *args: pytest.fail('must not audit'), lambda *args: None, json.loads)
    assert row['status'] == 'generation_failed'
    assert 'generation_response' in row


def test_audit_retry_recovers_without_regeneration(tmp_path):
    valid = {'verdicts': {k: True for k in ('voice', 'turn_relevance', 'naturalness', 'unsupported_expansion')},
             'decision': 'approve', 'reasons': []}
    row = run_stages({}, tmp_path / 'row.json', lambda: response({'reply': '在玄关。'}),
        lambda target, attempt: response({} if attempt == 0 else valid),
        lambda *args: {'approved': True}, json.loads)
    assert row['status'] == 'approved'
    assert len(row['attempts']) == 2


def test_page_refuses_incomplete_batch(tmp_path, monkeypatch):
    import build_conversational_review_page as page
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    source = inputs / 'data.jsonl'
    source.write_text('', encoding='utf-8')
    (tmp_path / 'batch_status.json').write_text('{"status":"running"}', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['page', '--input', str(source), '--output', str(tmp_path / 'page.html')])
    with pytest.raises(SystemExit, match='Batch incomplete'):
        page.main()
