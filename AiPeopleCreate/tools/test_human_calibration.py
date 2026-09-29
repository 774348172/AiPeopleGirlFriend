import copy
import unittest
import json
from pathlib import Path
from audit_generated_history import validate_history_review
from human_calibrated_review import apply_candidate_dedup


def row(sid, question):
    return dict(sample_id=sid,generation_messages=[{'role':'user','content':question}],
        target={'reply':'好。'},report={'approved':True,'reasons':[]},status='approved')


class DedupTests(unittest.TestCase):
    def test_distinct_questions_can_have_same_short_answer(self):
        rows=[row('1','等一会好吗'),row('2','拿不准再问好吗')]
        apply_candidate_dedup(rows)
        self.assertTrue(all(r['report']['approved'] for r in rows))
        self.assertEqual(rows[0]['diversity_review']['reply_frequency'],2)

    def test_identical_input_and_answer_is_duplicate(self):
        rows=[row('1','等一会好吗'),row('2','等一会好吗')]
        apply_candidate_dedup(rows)
        self.assertFalse(rows[1]['report']['approved'])

    def test_empty_reply_not_approved_by_dedup(self):
        r=row('1','问句');r.update(target={},status='generation_failed',report={'approved':False,'reasons':['empty']})
        apply_candidate_dedup([r])
        self.assertFalse(r['report']['approved'])


class DisclosureTests(unittest.TestCase):
    def test_timestamped_profile_allowed_but_untimed_world_not_allowed(self):
        root=Path(__file__).resolve().parents[1]/'训练数据/codex_authored100_20260916'
        r=json.loads((root/'gpt6_astra_reaudit/results.json').read_text(encoding='utf-8'))[76]
        # An archived verdict's compact IDs belong to its archived request,
        # not to a regenerated catalog after the live scene registry changes.
        request=json.loads((root/'gpt6_astra_reaudit/inputs'/f"{r['sample_id']}.json").read_text(encoding='utf-8'))
        payload=json.loads(request['messages'][-1]['content'])['待审数据']
        catalog=[e for e in payload['证据目录'] if not(e['kind']=='world' and e['value'] is None)]
        for section,kind in [('旧记忆','memory_turns'),('最近对话','recent_turns')]:
            for i,turn in enumerate(payload[section]):
                catalog.append({**turn,'id':turn['evidence_id'],'kind':'dialogue','check_key':f'{kind}:{i}'})
        validate_history_review(r['scenario']['component_source'],r['audit']['history'],catalog)
        refs=r['audit']['history']['checks']['memory_turns:2']['evidence_ids']
        for e in catalog:
            if e['id'] in refs:e.pop('known_since',None)
        with self.assertRaisesRegex(ValueError,'current snapshot'):
            validate_history_review(r['scenario']['component_source'],r['audit']['history'],catalog)


if __name__=='__main__':unittest.main()
