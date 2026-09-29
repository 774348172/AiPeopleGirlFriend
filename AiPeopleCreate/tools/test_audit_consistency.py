import copy
import unittest
from pathlib import Path
from run_optimized_comparison import read, evaluate, _load_baiweixi_render_context
from finish_optimized_comparison_audit import compact_audit_material


class ConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows=read(Path(__file__).resolve().parents[1]/'训练数据/new_life_scenes15_20260917/formal_run/results.json')
        cls.anchor=_load_baiweixi_render_context()[2]

    def assess(self,index,change=None):
        r=self.rows[index];v=copy.deepcopy(r['audit'])
        if change:change(v)
        _,_,cat,ind,_=compact_audit_material(r['scenario'],r['target'],r['scenario']['component_source']['topic'],self.anchor)
        return evaluate(r['scenario'],r['target'],v,cat,ind)

    def test_contradiction_is_not_content_rejection(self):
        self.assertEqual(self.assess(2)['decision'],'audit_invalid')

    def test_self_reference_is_invalid(self):
        self.assertEqual(self.assess(1)['decision'],'audit_invalid')

    def test_consistent_content_rejection_stays_rejected(self):
        def reject(v):
            v['history']['checks']['memory_turns:0']['supported']=False
            v['history']['pass']=False
            v['history']['reasons']=['具体断言缺少依据']
        result=self.assess(0,reject)
        self.assertEqual(result['decision'],'content_rejected')
        self.assertEqual(result['dimensions']['reply'],'passed')
        self.assertIn('history_input_issue',result['issue_categories'])
        self.assertIn('证据不足',str(result['reasons']))

    def test_valid_approval_stays_approved(self):
        self.assertEqual(self.assess(0)['decision'],'approved')

    def test_self_report_and_knowledge_limit_are_valid_categories(self):
        for kind in ('self_report','knowledge_limit'):
            def change(v):
                v['history']['checks']['memory_turns:0'].update(kind=kind,evidence_ids=[])
            self.assertEqual(self.assess(0,change)['decision'],'approved')

    def test_reply_rejection_does_not_label_history_bad(self):
        def change(v):
            v['facts'].update({'pass':False,'reasons':['回复捏造了未登记价格']})
        result=self.assess(0,change)
        self.assertEqual(result['dimensions']['history'],'passed')
        self.assertEqual(result['dimensions']['reply'],'rejected')
        self.assertEqual(result['issue_categories'],['reply_content_issue'])

    def test_schema_error_cannot_pass(self):
        self.assertEqual(self.assess(0,lambda v:v.pop('facts'))['decision'],'audit_invalid')


if __name__=='__main__':unittest.main()
