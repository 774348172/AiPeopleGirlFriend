"""Account for actual local reply/audit requests; never estimate billed Codex usage."""
import argparse
import json
from collections import Counter
from pathlib import Path

from pilot_pipeline import persist


def summarize(root):
    phases = {}
    for phase in ('reply', 'audit'):
        paths = [*(root/'requests').glob(f'*.{phase}.json'), *(root/'requests').glob(f'*.{phase}_format_retry.json')]
        logs = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
        attempts = [a for r in logs for a in r.get('attempts', [])]
        responses = [a['response'] for a in attempts if a.get('response')]
        phases[phase] = {
            'request_records':len(logs), 'attempts':len(attempts),
            'valid_requests':sum(r.get('valid', False) for r in logs),
            'responses_with_usage':len(responses),
            'attempts_without_response_usage':sum('response' not in a for a in attempts),
            'prompt_tokens':sum(r.get('prompt_eval_count', 0) for r in responses),
            'completion_tokens':sum(r.get('eval_count', 0) for r in responses),
            'wall_seconds':round(sum(a.get('wall_seconds', 0) for a in attempts), 3),
            'models':dict(Counter(r['model'] for r in logs)),
        }
    checkpoints = [json.loads(p.read_text(encoding='utf-8')) for p in (root/'checkpoints').glob('*.json')]
    checkpoints.sort(key=lambda r:r['sample_id'])
    # Stable reply snapshot: later audits never replace these original targets.
    # This artifact is explicitly not a released SFT dataset.
    candidates = [{'sample_id':r['sample_id'], 'status':'candidate_not_released',
                   'input_sha256':r['frozen_input_sha256'],
                   'messages':r['generation_messages'], 'target':r.get('target'),
                   'generator':r.get('model')} for r in checkpoints if r.get('reply_attempted')]
    if len(candidates) == 100:
        persist(root/'reply_candidates.json', {'count':len(candidates), 'released_training_rows':0,
            'description':'原始回复候选快照；未声称审核通过，不是训练包', 'candidates':candidates})
    report_path = root/'report.json'
    usage = {'scope':'本批本地回复生成及自动审核，基于 Ollama 原始响应用量',
        'batch_complete':report_path.exists(), 'phases':phases,
        'total_recorded_tokens':sum(r['prompt_tokens']+r['completion_tokens'] for r in phases.values()),
        'valid_replies':sum(bool(r.get('target', {}).get('reply', '').strip()) for r in checkpoints),
        'statuses':dict(Counter(r['status'] for r in checkpoints)),
        'codex_tokens':None, 'codex_tokens_status':'本会话无可核验的逐批计费用量，不标为零',
        'local_model_external_api_calls':0, 'released_training_rows':0}
    persist(root/'token_usage.json', usage)
    print(json.dumps(usage, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-root', type=Path, required=True)
    summarize(parser.parse_args().output_root)
