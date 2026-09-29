"""Author-only male defaults and self-known heroine traits from canonical files."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
CHARACTERS = REPO / 'AiPeopleCreate' / '人物设定'
MALE_CANON = CHARACTERS / '主角' / 'canon.json'
FEMALE_CANON = CHARACTERS / '白未晞' / 'canon.json'
BASE = REPO / '世界设定' / '松江府'


def canon_facts(path):
    return json.loads(path.read_text(encoding='utf-8'))['facts']


def dialogue_facts(path, prefix, count):
    facts = canon_facts(path)
    keys = [f'dialogue_{prefix}{i:02d}' for i in range(1, count + 1)]
    if any(not isinstance(facts.get(key, {}).get('value'), str) or not facts[key]['value'].strip() for key in keys):
        raise ValueError('approved dialogue defaults missing from canonical source')
    return {key: facts[key] for key in keys}


def load_persona_profiles():
    profiles = []
    for filename, path, prefix, count in (
        ('男主基础设定.yaml', MALE_CANON, 'M', 10),
        ('白未晞基础设定.yaml', FEMALE_CANON, 'F', 12),
    ):
        base = yaml.safe_load((BASE / filename).read_text(encoding='utf-8'))
        if (REPO / base['canonical_dialogue_source']).resolve() != path.resolve():
            raise ValueError('persona projection must reference its canonical source')
        base['daily_dialogue'] = {key: f['value'] for key, f in dialogue_facts(path, prefix, count).items()}
        if prefix == 'M':
            facts = canon_facts(path)
            base['background'] = {key: facts[key]['value'] for key in (
                'education', 'former_job', 'former_job_duration', 'job_loss', 'current_job')}
        profiles.append(base)
    return tuple(profiles)


def male_profile_labels():
    labels = {'background.' + k: label for k, label in (
        ('education', '学历'), ('former_job', '以前的工作'), ('former_job_duration', '以前工作时长'),
        ('job_loss', '离职经过'), ('current_job', '目前职业'))}
    labels.update({'daily_dialogue.' + key: f['label'] for key, f in dialogue_facts(MALE_CANON, 'M', 10).items()})
    return labels


def persona_source_hashes():
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in (
        ('male_canonical', MALE_CANON), ('female_canonical', FEMALE_CANON),
        ('female_profile', BASE / '白未晞基础设定.yaml'))}


def author_persona_material(view):
    # ONLY for authoring. Never inject this complete object into role replies.
    return {'男主默认档案（作者可见，不自动成为白未晞知识）': view['protagonist_profile'],
            '白未晞自身设定': view['female_character_profile'],
            '知情规则': '男主可在对白首次表达自身偏好；白未晞引用须有可追溯告知，不得在旧记忆提前知道后来才说的事。'}
