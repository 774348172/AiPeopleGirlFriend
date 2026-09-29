import copy
import unittest
from build_pending_human_review import select_pending


def row(sid, approved=True):
    return dict(sample_id=sid, status='approved' if approved else 'content_rejected',
        report={'approved': approved, 'reasons': []}, target={'reply': '好。'},
        generation_messages=[{'role': 'user', 'content': sid}], scenario={'id': sid})


class QueueTests(unittest.TestCase):
    def test_union_includes_passed_leak_and_passed_borderline(self):
        machine = [row(str(i), i != 0) for i in range(4)]
        semantic = copy.deepcopy(machine)
        for r in semantic:
            r.update(semantic_approved=True, borderline=False)
        semantic[1]['semantic_approved'] = False
        semantic[2]['borderline'] = True
        before = copy.deepcopy(machine)
        selected, reasons = select_pending(machine, semantic)
        self.assertEqual([r['sample_id'] for r in selected], ['0', '1', '2'])
        self.assertEqual(machine, before)
        self.assertTrue(reasons['1'])

    def test_mismatched_reply_or_context_is_rejected(self):
        a = [row('1')]
        for field in ('target', 'scenario', 'generation_messages'):
            b = copy.deepcopy(a)
            b[0][field] = {}
            with self.assertRaises(ValueError):
                select_pending(a, b)

    def test_missing_or_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            select_pending([row('1')], [])
        with self.assertRaises(ValueError):
            select_pending([row('1'), row('1')])


if __name__ == '__main__':
    unittest.main()
