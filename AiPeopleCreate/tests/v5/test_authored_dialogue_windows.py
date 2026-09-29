"""Regressions for lossless window slicing and explicit-review requirements."""
import copy
import json
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
from expand_authored_dialogue_windows import expand
from assemble_authored_campaign_metadata import assemble


def author_fixture(root):
    data = dict(filename='dialogue.txt', scene='home.living_room', area='living_room',
                start='2026-10-07T19:00:00+08:00', authored_default_types='SSSSSSSSSSSSSSS',
                authored_tasks=['C', 'E', 'C', 'F'], episodes=[])
    for n in range(25):
        data['episodes'].append(dict(topic=f'测试片段{n}',
            memory='~'.join(f'旧句{n}.{i}' for i in range(4)),
            chain='~'.join(f'新句{n}.{i}' for i in range(11)),
            states=[['钱包', '当前位置', None]],
            reviews=[['approve', f'第{n}链第{i}窗连续性人工测试理由',
                      f'第{n}链第{i}窗证据边界人工测试理由',
                      f'第{n}链第{i}窗人物归属人工测试理由'] for i in range(4)]))
    save(root, data)
    return data


def save(root, data):
    (root / 'authored_episodes.yaml').write_text(yaml.safe_dump(data, allow_unicode=True), encoding='utf-8')


def test_windows_preserve_authored_utterances_reviews_and_temporal_offsets(tmp_path):
    data = author_fixture(tmp_path)
    expand(tmp_path, 'example')
    assemble(tmp_path, 'example', '2026-10-07')
    lines = (tmp_path / 'source/dialogue.txt').read_text(encoding='utf-8').splitlines()
    notes = (tmp_path / 'input_review_notes.txt').read_text(encoding='utf-8').splitlines()
    plan = json.loads((tmp_path / 'source/world_plan.json').read_text(encoding='utf-8'))
    assert len(lines) == len(notes) == 100
    for i, line in enumerate(lines):
        family, slot = divmod(i, 4)
        episode = data['episodes'][family]
        _, old, recent, current = line.split('|')
        chain = episode['chain'].split('~')
        assert old == episode['memory']
        assert recent.split('~') == chain[slot*2:slot*2+4]
        assert current == chain[slot*2+4]
        assert notes[i].split('|')[1:] == episode['reviews'][slot]
    from datetime import datetime, timedelta
    t0 = datetime.fromisoformat(plan['snapshot_times']['1'])
    t1 = datetime.fromisoformat(plan['snapshot_times']['2'])
    assert t0 - timedelta(minutes=plan['memory_minutes_ago']['1']) == t1 - timedelta(minutes=plan['memory_minutes_ago']['2'])
    assert t0 == t1 - timedelta(minutes=plan['recent_minutes_ago']) + timedelta(seconds=60)
    assert plan['facts']['4'][0][2] is None
    assert not (tmp_path / 'input_approvals.json').exists()
    assert not (tmp_path / 'sealed_ready.json').exists()


def test_missing_review_or_short_criteria_do_not_get_auto_approved(tmp_path):
    original = author_fixture(tmp_path)
    for review in ([], [['approve', '通用通过', '通用通过', '通用通过']] * 4):
        data = copy.deepcopy(original)
        data['episodes'][0]['reviews'] = review
        save(tmp_path, data)
        with pytest.raises(ValueError):
            expand(tmp_path, 'example')
        assert not (tmp_path / 'input_review_notes.txt').exists()


def test_frozen_batch_cannot_be_reexpanded(tmp_path):
    author_fixture(tmp_path)
    (tmp_path / 'input_approvals.json').write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='immutable'):
        expand(tmp_path, 'example')
    assert not (tmp_path / 'source').exists()
