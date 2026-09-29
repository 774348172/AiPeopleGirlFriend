import copy
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools'))
from joint_scene_planning import apply_plan, freeze, validate_requirements
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages
from joint_scene_planning import evidence_catalog, validate_evidence_requirements

@pytest.fixture
def store():
    root=Path(__file__).resolve().parents[2]
    rows=[json.loads(x) for x in (root/'训练数据/external_chat16_20260914.jsonl').read_text(encoding='utf-8').splitlines()]
    row=copy.deepcopy(rows[1]); row['task_type']='follow_up_question'
    return row

def plan(value='有',visibility='visible'):
    return {'intent':'买鸡蛋顺便聊晚饭','states':[{'subject':'鸡蛋','predicate':'库存','value':value,'visibility':visibility}]}

def check(subject,predicate,mode):
    return {'pass':True,'reasons':[],'requirements':[{'subject':subject,'predicate':predicate,'mode':mode}]}

def test_inventory_does_not_supply_payment(store):
    row=apply_plan(store,plan())
    validate_requirements(row,check('鸡蛋','库存','known'))
    with pytest.raises(ValueError): validate_requirements(row,check('邻里便利店','付款结果','known'))
    validate_requirements(row,check('邻里便利店','付款结果','intentional_unknown'))

def test_hidden_value_never_reaches_final_messages(store):
    row=apply_plan(store,plan('秘密库存2718','hidden'))
    from build_v5_pilot_dataset import _load_baiweixi_render_context
    s=convert(row); messages=build_messages(s,_load_baiweixi_render_context()[2])
    validate_final_messages(s,messages)
    assert '秘密库存2718' not in json.dumps(s,ensure_ascii=False)
    assert '秘密库存2718' not in json.dumps(messages,ensure_ascii=False)
    with pytest.raises(ValueError): validate_requirements(row,check('鸡蛋','库存','known'))

def test_fixed_or_unregistered_field_rejected(store):
    p=plan(); p['states'][0].update(subject='邻里便利店',predicate='营业时间')
    with pytest.raises(ValueError): apply_plan(store,p)
    p['states'][0]['predicate']='凭空的新字段'
    with pytest.raises(ValueError): apply_plan(store,p)

def test_missing_reference_and_fact_free_pass_rejected(store):
    row=apply_plan(store,plan())
    with pytest.raises(ValueError): validate_requirements(row,check('收银员小王','当前动作','known'))
    with pytest.raises(ValueError): validate_requirements(row,{'pass':True,'reasons':[],'requirements':[]})
    with pytest.raises(ValueError): validate_requirements(row,{'pass':False,'reasons':['前提不成立'],'requirements':[]})
    row['task_type']='idle_chat'
    validate_requirements(row,{'pass':True,'reasons':[],'requirements':[]})

def test_null_not_absence_and_freeze_changes(store):
    row=apply_plan(store,plan(None))
    with pytest.raises(ValueError): validate_requirements(row,check('鸡蛋','库存','known'))
    original=freeze(row); row['snapshot']['states'][0]['value']='没有'
    assert freeze(row)!=original

def test_duplicate_state_rejected(store):
    p=plan(); p['states']*=2
    with pytest.raises(ValueError): apply_plan(store,p)

@pytest.mark.parametrize('value',['visible','hidden','null','unknown'])
def test_metadata_is_not_a_fact_value(store,value):
    with pytest.raises(ValueError): apply_plan(store,plan(value))

def test_character_reference_and_world_mode(store):
    row=apply_plan(store,plan(None)); catalog=evidence_catalog(row,'- food_habit_boundary: 喜欢鱼干')
    review={'topic_check':{'original_focus':'话题','current_focus':'话题','aligned':True},'pass':True,'reasons':[],'requirements':[{'evidence_id':'character:0','mode':'known'}]}
    validate_evidence_requirements(row,review,catalog)
    review['requirements'][0]['evidence_id']='白未晞.性格'
    with pytest.raises(ValueError): validate_evidence_requirements(row,review,catalog)
    unknown=next(e for e in catalog if e['kind']=='world' and e['value'] is None)
    review['requirements'][0]['evidence_id']=unknown['id']
    with pytest.raises(ValueError): validate_evidence_requirements(row,review,catalog)
    review['requirements'][0]['mode']='intentional_unknown'
    validate_evidence_requirements(row,review,catalog)
