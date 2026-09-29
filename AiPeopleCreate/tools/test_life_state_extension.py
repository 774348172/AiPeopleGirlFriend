import copy
import json
import unittest
from pathlib import Path

from convert_component_to_game_reply import convert, validate_final_messages
from joint_scene_planning import apply_plan
from run_component_game_reply15_pilot import build_messages
from build_v5_pilot_dataset import _load_baiweixi_render_context

ROOT = Path(__file__).resolve().parents[1]


class LifeStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = json.loads((ROOT/'训练数据/candidate1000_20260916/batch10/draft_inputs.json').read_text(encoding='utf-8'))
        cls.base = cls.old[0]['scenario']['component_source']
        cls.anchor = _load_baiweixi_render_context()[2]

    def seed(self, scene='home.living_room', area='living_room'):
        row = copy.deepcopy(self.base)
        row['world_extension'] = 'life_state_20260917'
        row['component_ids']['scene_id'] = scene
        row['snapshot']['area'] = area
        row['snapshot']['states'] = []
        for p in row['snapshot']['people'].values():
            p['area'] = area
        return row

    def test_legacy_all_five_scenes_unchanged(self):
        for i in (0,20,40,60,80):
            old = self.old[i]
            messages = build_messages(convert(old['scenario']['component_source']), self.anchor)
            self.assertEqual(messages, old['generation_messages'])

    def test_new_scenes_full_renderer(self):
        for scene,area,subject,predicate in [
            ('home_downstairs','一楼门厅','楼下快递','是否已取'),
            ('community_market','蔬菜区','本次买菜','已选清单'),
            ('neighborhood_park','树荫长椅区','树荫长椅','当前占用'),
            ('home_style_restaurant','普通餐桌区','本桌餐单','已上菜品')]:
            row = self.seed('songjiangfu.'+scene,area)
            row = apply_plan(row,dict(intent='验证新增状态投影', states=[dict(subject=subject,predicate=predicate,value='验证值',visibility='visible')]))
            s = convert(row)
            messages = build_messages(s,self.anchor)
            self.assertEqual(validate_final_messages(s,messages)['coverage'],1)
            self.assertIn(subject+'的'+predicate+'：验证值',messages[0]['content'])
            self.assertIn('日历的星期：星期二',messages[0]['content'])
            self.assertIn('日历的是否节假日：未知',messages[0]['content'])

    def test_home_extension_and_hidden_information(self):
        row = apply_plan(self.seed(),dict(intent='计划晚饭',states=[dict(subject='本次做饭',predicate='计划菜品',value='回锅肉',visibility='hidden')]))
        text = build_messages(convert(row), self.anchor)[0]['content']
        self.assertIn('本次做饭的计划菜品：未知',text)
        self.assertNotIn('回锅肉',text)

    def test_reject_unregistered_state_and_fixed_override(self):
        for subject,predicate in [('编造设备','状态'),('青禾菜市场','营业时间')]:
            row=self.seed('songjiangfu.community_market','蔬菜区')
            row['snapshot']['states']=[dict(subject=subject,predicate=predicate,value='任意',source='测试')]
            with self.assertRaises(ValueError):convert(row)

    def test_completed_event_requires_real_time(self):
        row=self.seed()
        row['snapshot']['states']=[dict(subject='当前生活事件',predicate='当前阶段',value='已完成',source='测试')]
        with self.assertRaises(ValueError):convert(row)
        row['snapshot']['states'].append(dict(subject='当前生活事件',predicate='实际结束时间',value='2026-09-30T10:00:00+08:00',source='测试'))
        with self.assertRaises(ValueError):convert(row)

    def test_fact_question_stays_distinct_from_statement(self):
        row=self.seed('songjiangfu.neighborhood_park','公园入口')
        row['current_user']='今天不想赶路，就慢慢走吧。'
        messages=build_messages(convert(row),self.anchor)
        self.assertEqual(messages[-1]['content'],row['current_user'])

    def test_expired_observation_cannot_be_current_fact(self):
        row=self.seed()
        row['snapshot']['states']=[dict(subject='家务', predicate='待办事项', value='扫地', source='观察',
            observed_at='2026-09-01T10:00:00+08:00', valid_until='2026-09-02T10:00:00+08:00')]
        with self.assertRaises(ValueError): convert(row)
        row['snapshot']['states'][0]['value']=None
        convert(row)


if __name__ == '__main__':
    unittest.main()
