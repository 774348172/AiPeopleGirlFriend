import copy
import unittest

from reaudit_curated_replies import verify_replay_material


class MatchedReplayTests(unittest.TestCase):
    def setUp(self):
        self.record={'sample_id':'sample.1','target':{'reply':'已关闭。'}}
        self.messages=[{'role':'user','content':'{"待审数据":{"候选回复":{"reply":"已关闭。"}}}'}]
        self.schema={'type':'object'}
        self.saved={'messages':copy.deepcopy(self.messages),'schema':copy.deepcopy(self.schema)}

    def test_identical_saved_request_is_accepted(self):
        verify_replay_material(self.record,self.saved,self.messages,self.schema)

    def test_changed_reply_is_rejected(self):
        self.record['target']['reply']='已开启。'
        with self.assertRaises(ValueError):
            verify_replay_material(self.record,self.saved,self.messages,self.schema)

    def test_prompt_change_is_not_a_model_only_comparison(self):
        self.messages.insert(0,{'role':'system','content':'changed rules'})
        with self.assertRaises(ValueError):
            verify_replay_material(self.record,self.saved,self.messages,self.schema)

    def test_schema_change_is_rejected(self):
        self.schema['required']=['different_field']
        with self.assertRaises(ValueError):
            verify_replay_material(self.record,self.saved,self.messages,self.schema)


if __name__=='__main__':
    unittest.main()
