"""Durable generation and audit stages for isolated candidates."""
import json
import httpx
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from data_gen_v4.runtime_grounded.conversational import validate_conversational_target, validate_conversational_audit

REPLY_SCHEMA = {'type': 'object', 'properties': {'reply': {
    'type': 'string', 'minLength': 1, 'maxLength': 400}},
    'required': ['reply'], 'additionalProperties': False}


def persist(path, row):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError as error:
            # Windows readers/indexers may briefly hold the destination open.
            if getattr(error, 'winerror', None) not in (5, 32) or attempt == 4:
                raise
            time.sleep(0.05 * (2 ** attempt))


def native_response(base_url, model, messages, max_tokens, *, response_schema=None, num_ctx=8192, temperature=0.55):
    native = base_url.rstrip('/').removesuffix('/v1')
    response = httpx.post(native + '/api/chat', json={
        'model': model, 'messages': messages, 'stream': False, 'format': response_schema or 'json',
        'keep_alive': '10m', 'think': False,
        'options': {'num_ctx': num_ctx, 'num_predict': max_tokens, 'temperature': temperature},
    }, timeout=90.0)
    response.raise_for_status()
    return response.json()


def run_stages(row, path, generate, audit, evaluate, parse, *, generation_retries=1):
    """Retry invalid generation only; never replace a valid answer after audit rejection."""
    if not 1 <= generation_retries <= 3:
        raise ValueError('generation_retries must be 1..3')
    row.update(status='generating', attempts=[], generation_attempts=[])
    row.pop('target', None)
    persist(path, row)
    for attempt in range(generation_retries):
        item = {'attempt': attempt + 1}
        row['generation_attempts'].append(item)
        try:
            response = generate()
            item['response'] = row['generation_response'] = response
            persist(path, row)
            target = parse(response.get('message', {}).get('content', ''))
            validate_conversational_target(target)
            if response.get('done_reason') == 'length':
                raise ValueError('generation truncated')
            row['target'] = target
            break
        except Exception as error:
            item['error'] = str(error)
            persist(path, row)
    else:
        error = row['generation_attempts'][-1]['error']
        row.update(status='generation_failed', error=error, report={'approved': False, 'reasons': [error]})
        persist(path, row)
        return row
    row['status'] = 'generated'
    persist(path, row)
    for attempt in range(3):
        record = {'attempt': attempt + 1}
        row['attempts'].append(record)
        try:
            response = audit(row['target'], attempt)
            record['response'] = response
            persist(path, row)
            parsed = parse(response.get('message', {}).get('content', ''))
            validate_conversational_audit(parsed)
            if response.get('done_reason') == 'length':
                raise ValueError('audit truncated')
            row['audit'] = parsed
        except Exception as error:
            record['error'] = str(error)
            persist(path, row)
            continue
        try:
            row['report'] = evaluate(row['target'], parsed)
            row['status'] = 'approved' if row['report']['approved'] else 'content_rejected'
        except Exception as error:
            row.update(status='processing_failed', error=str(error), report={'approved': False, 'reasons': [str(error)]})
        persist(path, row)
        return row
    row.update(status='audit_failed', report={'approved': False, 'reasons': ['自动审核失败，原回复已保留，等待人工判断']})
    persist(path, row)
    return row
