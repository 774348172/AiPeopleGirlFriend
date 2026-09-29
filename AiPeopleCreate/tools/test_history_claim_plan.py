import copy
import unittest
from history_claim_plan import validate_claim_plan


class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.row={'memory_turns':[{'speaker':'男主','time':'2026-09-17T09:00:00+08:00','text':'我容易忘记问价格。'}],
                  'recent_turns':[{'speaker':'男主','time':'2026-09-17T10:00:00+08:00','text':'我不知道还要等多久。'}]}
        self.plan={'memory_turns:0':{'kind':'self_report','scope':'只表示男主自述的习惯，不保证每次如此','evidence_ids':[]},
                   'recent_turns:0':{'kind':'knowledge_limit','scope':'仅表示未知，不证明服务员从未告知','evidence_ids':[]}}

    def test_self_report_and_unknown_need_no_fabricated_event(self):
        validate_claim_plan(self.row,self.plan,[])

    def test_external_claim_cannot_use_unknown_as_proof(self):
        self.plan['recent_turns:0'].update(kind='world',evidence_ids=['f1'])
        with self.assertRaisesRegex(ValueError,'未知状态'):
            validate_claim_plan(self.row,self.plan,[{'fact_id':'f1','value':None}])

    def test_no_self_reference(self):
        self.plan['recent_turns:0'].update(kind='conversation',evidence_ids=['recent_turns:0'])
        with self.assertRaisesRegex(ValueError,'更早'):validate_claim_plan(self.row,self.plan,[])

    def test_no_missing_turn(self):
        self.plan.pop('memory_turns:0')
        with self.assertRaisesRegex(ValueError,'逐句'):validate_claim_plan(self.row,self.plan,[])

    def test_event_requires_known_speaker_and_earlier_time(self):
        self.plan['recent_turns:0'].update(kind='event',evidence_ids=['e1'])
        self.row['historical_events']=[{'source':'e1','time':'2026-09-17T09:30:00+08:00','known_by':['男主']}]
        validate_claim_plan(self.row,self.plan,[])
        self.row['historical_events'][0]['known_by']=[]
        with self.assertRaises(ValueError):validate_claim_plan(self.row,self.plan,[])


if __name__=='__main__':unittest.main()
