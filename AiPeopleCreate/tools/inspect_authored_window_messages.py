"""Read-only integrity inspection of authored windows; does not approve semantics."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def inspect(root):
    rows = json.loads((root / 'draft_inputs.json').read_text(encoding='utf-8'))
    families = defaultdict(list)
    facts = []
    for row in rows:
        c = row['scenario']['component_source']
        m = row['generation_messages']
        assert row['input_coverage']['coverage'] == 1, row['sample_id']
        assert '：None' not in m[0]['content'], row['sample_id']
        assert len(m) == 6 and m[-1]['content'] == c['current_user'], row['sample_id']
        for i in range(4):
            assert m[i+1]['content'] == c['recent_turns'][i]['text'], row['sample_id']
            assert c['memory_turns'][i]['text'] in m[0]['content'], row['sample_id']
        families[c['component_ids']['family_id']].append(c)
        if c['task_type'] == 'follow_up_question':
            facts.append(dict(id=row['sample_id'], question=c['current_user'], states=c['snapshot']['states']))
    for group in families.values():
        assert len(group) <= 4
        for a, b in zip(group, group[1:]):
            assert a['current_user'] == b['recent_turns'][2]['text']
            assert a['recent_turns'][2]['time'] == b['recent_turns'][0]['time']
            assert a['memory_turns'][0]['time'] == b['memory_turns'][0]['time']
    return dict(actual_messages_checked=len(rows), families=len(families), facts=facts,
                scope='Structural integrity only; no semantic approvals inferred')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, required=True)
    print(json.dumps(inspect(p.parse_args().root), ensure_ascii=False))
