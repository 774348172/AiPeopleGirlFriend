"""Expand explicitly authored dialogue episodes; no text generation or approval inference."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import yaml


def expand(root, prefix):
    if (root / 'input_approvals.json').exists():
        raise ValueError('Frozen batch is immutable')
    data = yaml.safe_load((root / 'authored_episodes.yaml').read_text(encoding='utf-8'))
    episodes = data['episodes']
    if len(episodes) != 25:
        raise ValueError('Exactly 25 authored episodes are required for 100 windows')
    rows, notes = [], []
    meta = dict(groups=[[data['filename'], data['scene'], data['area'], 100]],
                snapshot_schedule=dict(start=data['start'], step_minutes=1),
                types={}, facts={}, fact_questions=[], emotion=[],
                dialogue_family_ids={}, memory_minutes_ago={}, recent_minutes_ago=2)
    for family, episode in enumerate(episodes, 1):
        memory = episode['memory'].split('~')
        chain = episode['chain'].split('~')
        types = episode.get('types', data['authored_default_types'])
        if len(memory) != 4 or len(chain) != 11 or len(types) != 15:
            raise ValueError(f'Episode {family}: need 4 memory and 11 continuous turns with 15 authored types')
        if any('|' in t or not t.strip() for t in memory + chain):
            raise ValueError('Invalid authored utterance')
        reviews = episode['reviews']
        tasks = episode.get('tasks', data['authored_tasks'])
        if len(reviews) != 4 or len(tasks) != 4:
            raise ValueError('Each window requires its own explicit review and task')
        for slot in range(4):
            n = len(rows) + 1
            start = 2 * slot
            rows.append('|'.join([episode['topic'], '~'.join(memory), '~'.join(chain[start:start+4]), chain[start+4]]))
            review = reviews[slot]
            if len(review) != 4 or review[0] not in ('approve', 'reject') or any(len(s.strip()) < 8 for s in review[1:]):
                raise ValueError(f'Episode {family}, window {slot+1}: explicit review missing or too short')
            notes.append('|'.join([str(n), *review]))
            meta['types'][n] = types[:4] + types[4+start:4+start+4]
            meta['facts'][n] = episode.get('states', [])
            meta['dialogue_family_ids'][n] = f'{prefix}.family{family:02d}'
            meta['memory_minutes_ago'][n] = 2880 + slot
            task = tasks[slot]
            if task == 'F':
                meta['fact_questions'].append(n)
            elif task == 'E':
                meta['emotion'].append(n)
            elif task != 'C':
                raise ValueError('Unknown explicitly authored task')
    source = root / 'source'
    source.mkdir(exist_ok=True)
    (source / data['filename']).write_text('\n'.join(rows)+'\n', encoding='utf-8')
    (root / 'authoring_metadata.yaml').write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding='utf-8')
    (root / 'input_review_notes.txt').write_text('\n'.join(notes)+'\n', encoding='utf-8')
    print(json.dumps(dict(authored_episodes=25, windows=100, explicit_individual_reviews=100, inferred_reviews=0)))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--prefix', required=True)
    a = p.parse_args()
    expand(a.root, a.prefix)
