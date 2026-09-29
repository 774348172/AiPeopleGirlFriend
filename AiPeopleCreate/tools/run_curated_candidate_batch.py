"""Explicit curated-input mode of the formal component/model_view pipeline.

Input review is a separate Codex decision, not a fabricated model verdict.
Only final replies and independent audits invoke local models. No author fallback.
"""
import argparse
import copy
import hashlib
import json
import subprocess
import sys
import httpx
from pathlib import Path

from build_v5_pilot_dataset import _load_baiweixi_render_context
from convert_component_to_game_reply import convert, validate_final_messages
from joint_scene_planning import validate_requirements
from run_component_game_reply15_pilot import build_messages
from run_mixed_candidate_batch import duplicate_reason, write_jsonl, norm
from run_optimized_comparison import (
    Heartbeat, call_logged, digest, read, now, metrics, evaluate, AUDIT_INSTRUCTION,
    validate_conversational_target,
)
from finish_optimized_comparison_audit import compact_audit_material
from audit_requests import audit_messages
from pilot_pipeline import persist, REPLY_SCHEMA
from human_calibrated_review import CALIBRATION, VERSION as CALIBRATION_VERSION, apply_candidate_dedup
from audit_consistency import CLARIFICATION, VERSION as AUDIT_VALIDATION_VERSION

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'curated_input_formal_reply_v1'
DEFAULT_AUDITOR = 'gemma4:31b-it-qat'

def validate_frozen_provenance(component):
    """Facts come from the frozen snapshot/events, never from dialogue text."""
    if 'history_claim_plan' in component:
        from history_claim_plan import validate_claim_plan
        validate_claim_plan(component, component['history_claim_plan'],
                            convert(component)['model_view']['authoritative_world']['facts'])
    events = component.get('historical_events', [])
    if not isinstance(events, list):
        raise ValueError('historical_events must be a list')
    for event in events:
        if not isinstance(event, dict) or not event.get('current_fact_ids'):
            raise ValueError('historical event lacks independent current_fact_ids')
        if not event.get('known_by') or not event.get('actors'):
            raise ValueError('historical event lacks actors/known_by provenance')
    # A factual follow-up must name the required snapshot facts in the
    # approval record; the preceding dialogue is never accepted as evidence.
    return True


def prepare(components, approvals, anchor):
    prepared = []; previous = []; ids = set()
    for component in components:
        sid = component['sample_id']
        if sid in ids or not sid or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-' for c in sid):
            raise ValueError('invalid or duplicate sample_id')
        ids.add(sid)
        approval = approvals.get(sid, {})
        if (approval.get('reviewer') != 'Codex' or approval.get('decision') != 'approve'
                or approval.get('component_sha256') != digest(component)
                or not approval.get('notes')):
            raise ValueError('missing or stale explicit Codex input approval: ' + sid)
        reason = duplicate_reason(component, previous)
        if reason:
            raise ValueError(reason)
        validate_frozen_provenance(component)
        previous.append(component)
        scenario = convert(component)
        validate_requirements(component, {'pass': True, 'reasons': [],
                                         'requirements': approval['requirements']})
        messages = build_messages(scenario, anchor)
        coverage = validate_final_messages(scenario, messages)
        prepared.append({'sample_id': sid, 'scenario': scenario, 'generation_messages': messages,
                         'input_review': approval, 'input_coverage': coverage,
                         'frozen_input_sha256': digest({'component': component, 'messages': messages})})
    if not prepared:
        raise ValueError('empty batch')
    return prepared


def verify_frozen(record):
    actual = digest({'component': record['scenario']['component_source'],
                     'messages': record['generation_messages']})
    if actual != record['frozen_input_sha256']:
        raise ValueError('frozen input changed')
    validate_final_messages(record['scenario'], record['generation_messages'])


def run(out, components, approvals, *, preflight_only=False, input_review_summary=None,
        auditor=DEFAULT_AUDITOR):
    _, _, anchor = _load_baiweixi_render_context()
    prepared = prepare(components, approvals, anchor)
    # Expansion is allowed only when the external input review ledger records
    # the whole attempted batch. Passing only the approved rows must not make
    # the approval rate appear to be 100%.
    gate = {'required': 0.90, 'attempted': len(prepared), 'approved': len(prepared), 'rate': 1.0}
    if len(prepared) >= 20:
        if input_review_summary is None:
            raise ValueError('batches over 20 require an external input-review summary')
        summary = read(input_review_summary)
        run_id = summary.get('review_run_id')
        approved_ids = set(summary.get('approved_ids', []))
        component_ids = {r['sample_id'] for r in prepared}
        approval_run_ids = {approvals[r['sample_id']].get('review_run_id') for r in prepared}
        if not run_id or approval_run_ids != {run_id} or approved_ids != component_ids:
            raise ValueError('input-review summary is not bound to this exact review run and component set')
        attempted = int(summary.get('attempted', 0)); approved = int(summary.get('approved', 0))
        if attempted <= 0 or approved < 0 or approved > attempted:
            raise ValueError('invalid external input-review summary')
        gate = {'required': 0.90, 'attempted': attempted, 'approved': approved, 'rate': approved / attempted}
        if gate['rate'] < gate['required']:
            raise ValueError(f'input review pass rate {gate["rate"]:.3f} below 0.900 gate')
        if approved != len(prepared):
            raise ValueError('approved input count does not match frozen component count')
        for component in components:
            approval = approvals[component['sample_id']]
            if approval.get('provenance') != 'external_codex_review' or not approval.get('review_run_id'):
                raise ValueError('large batches require external_codex_review provenance and review_run_id')
    config = {'version': VERSION, 'prepared_sha256': digest(prepared), 'input_review_gate': gate,
              'generator': 'qwen3.5:27b', 'auditor': auditor, 'count': len(prepared),
              'calibration_version': CALIBRATION_VERSION,
              'audit_validation_version': AUDIT_VALIDATION_VERSION,
              'authoring_model_calls': 0, 'training_started': False,
              'code_hashes': {n: digest((ROOT/'tools'/n).read_text(encoding='utf-8')) for n in
                  ('run_curated_candidate_batch.py', 'convert_component_to_game_reply.py',
                   'run_component_game_reply15_pilot.py', 'run_optimized_comparison.py',
                   'finish_optimized_comparison_audit.py', 'audit_generated_history.py',
                   'human_calibrated_review.py')}}
    out.mkdir(parents=True, exist_ok=True)
    if (out/'config.json').exists() and read(out/'config.json') != config:
        raise ValueError('resume configuration or full inputs changed; use a new output directory')
    persist(out/'config.json', config)
    for folder in ('requests', 'checkpoints', 'inputs', 'evidence_maps'):
        (out/folder).mkdir(exist_ok=True)
    persist(out/'inputs/frozen_inputs.json', prepared)
    persist(out/'preflight_report.json', {'status': 'passed', 'count': len(prepared), 'input_review_gate': gate,
        'input_reviewer': 'Codex', 'machine_input_approval_claimed': False,
        'samples': [{'sample_id': r['sample_id'], 'coverage': r['input_coverage'],
                     'input_sha256': r['frozen_input_sha256']} for r in prepared]})
    if preflight_only:
        print('curated preflight passed: ' + str(len(prepared)), flush=True)
        return
    records = []
    for initial in prepared:
        path = out/'checkpoints'/f"{initial['sample_id']}.json"
        record = read(path) if path.exists() else copy.deepcopy(initial)
        verify_frozen(record)
        if record['frozen_input_sha256'] != initial['frozen_input_sha256']:
            raise ValueError('checkpoint belongs to another input')
        records.append(record)
    with Heartbeat(out) as heartbeat:
        # All Qwen replies first, then all Gemma audits: one model switch.
        for phase in ('reply', 'audit'):
            if any(not r.get(phase+'_attempted') for r in records):
                heartbeat.update(phase=phase+'_model_load')
                model = config['generator'] if phase == 'reply' else config['auditor']
                loaded = httpx.post('http://127.0.0.1:11434/api/generate', timeout=300,
                    json={'model': model, 'prompt': '', 'stream': False, 'think': False,
                          'keep_alive': '10m', 'options': {'num_ctx': 16384}})
                loaded.raise_for_status()
                persist(out/(phase+'_model_load.json'), loaded.json())
            for i, record in enumerate(records, 1):
                sid = record['sample_id']; path = out/'checkpoints'/f'{sid}.json'
                if record.get(phase+'_attempted'):
                    continue
                if phase == 'audit' and not record.get('target'):
                    continue
                heartbeat.update(phase=phase, active_sample=sid, processed=i-1)
                verify_frozen(record)
                record.update(model=config['generator'], audit_model=config['auditor'])
                try:
                    if phase == 'reply':
                        record['target'] = call_logged(out, sid+'.reply', config['generator'],
                            record['generation_messages'], REPLY_SCHEMA, 500, .55,
                            validate_conversational_target, retries=1)
                        record['status'] = 'generated'
                    else:
                        payload, schema, catalog, independent, mapping = compact_audit_material(
                            record['scenario'], record['target'], record['scenario']['component_source']['topic'], anchor)
                        persist(out/'evidence_maps'/f'{sid}.json', mapping)
                        verdict = call_logged(out, sid+'.audit', config['auditor'],
                            audit_messages(AUDIT_INSTRUCTION + CALIBRATION + CLARIFICATION, payload), schema, 3400, 0, retries=1)
                        record['audit'] = verdict
                        record['report'] = evaluate(record['scenario'], record['target'], verdict, catalog, independent)
                        record['status'] = record['report']['decision']
                except Exception as error:
                    record.update(status='generation_failed' if phase=='reply' else 'audit_failed',
                                  report={'approved': False, 'reasons': [str(error).split('\n')[0]]})
                record[phase+'_attempted'] = True
                record['quarantined'] = not record.get('report', {}).get('approved', False)
                persist(path, record)
                print(f'{phase} {i}/{len(records)} {sid}: {record["status"]}', flush=True)
        apply_candidate_dedup(records)
        for record in records:
            persist(out/'checkpoints'/f"{record['sample_id']}.json", record)
        write_jsonl(out/'inputs/all_for_review.jsonl', records)
        persist(out/'results.json', records)
        report = {'count': len(records), 'valid_replies': sum(bool(r.get('target')) for r in records),
                  'auto_pass': sum(r.get('report', {}).get('approved', False) for r in records),
                  'audit_failed': sum(r['status']=='audit_failed' for r in records),
                  'audit_invalid': sum(r['status']=='audit_invalid' for r in records),
                  'content_rejected': sum(r['status']=='content_rejected' for r in records),
                  'issue_counts': {c:sum(c in r.get('report',{}).get('issue_categories',[]) for r in records)
                      for c in ('audit_invalid','history_input_issue','question_input_issue','reply_content_issue','reply_needs_review')},
                  'authoring_model_calls': 0, 'released_training_rows': 0,
                  'generation': metrics(out, '*.reply'), 'audit': metrics(out, '*.audit'), 'finished_at': now()}
        persist(out/'report.json', report)
        persist(out/'batch_status.json', {'status': 'complete', 'expected': len(records),
            'review_sha256': hashlib.sha256((out/'inputs/all_for_review.jsonl').read_bytes()).hexdigest()})
        heartbeat.update(status='complete', phase='complete', active_sample=None, processed=len(records))
    subprocess.run([sys.executable, '-X', 'utf8', str(ROOT/'tools/build_conversational_review_page.py'),
        '--input', str(out/'inputs/all_for_review.jsonl'), '--output', str(out/'review.html'),
        '--expected', str(len(records)), '--group', 'light_teasing', '--group', 'follow_up_question',
        '--title', f'提前编排完整输入 · 本地 Qwen {len(records)} 条新候选'], check=True)
    from build_pending_human_review import build as build_pending_review
    if not (out/'human_review_pending').exists():
        build_pending_review(out/'results.json', out/'human_review_pending')
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--approvals', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--auditor', default=DEFAULT_AUDITOR,
                        help='Exact local auditor tag; recorded in immutable batch config')
    parser.add_argument('--input-review-summary', type=Path,
                        help='External whole-batch input review summary for batches over 20')
    args = parser.parse_args()
    run(args.output_root, read(args.input), read(args.approvals),
        preflight_only=args.preflight_only, input_review_summary=args.input_review_summary,
        auditor=args.auditor)
