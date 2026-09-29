"""Build ten new component-combination records for human structural review."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '训练数据' / 'component_preview15_20260913'
SCENES = [('客厅','晚上'),('厨房','早晨'),('阳台','午后'),('玄关','傍晚'),('卧室','夜里')]
TOPICS = [('钥匙','位置'),('洗衣机','运行状态'),('电视','频道'),('水果','位置'),('热水器','剩余热水量'),('借来的书','位置'),('快递','送达状态'),('风扇','电源状态'),('窗户','是否关闭'),('晚饭','具体内容'),('白围巾','位置'),('冰箱','运行模式'),('燃气灶','电源状态'),('微波炉','剩余时间'),('充电器','位置')]
RELATIONS = ['旧事实被新事实替代','当前状态未知','计划尚未发生','第三方旧信息','自然闲聊延续']
QUESTIONS = ['我刚才找了半天，现在到底在哪儿？','这个现在是什么状态，你知道吗？','那件事后来真的做了吗？','之前有人这么说过，现在还算数吗？','突然想起这个，你怎么看？']
rows=[]
for i,(topic,predicate) in enumerate(TOPICS):
    area,time = SCENES[i%len(SCENES)]; relation=RELATIONS[i%len(RELATIONS)]
    known = f'{topic}的当前位置是{("客厅茶几" if topic=="钥匙" else "餐桌果盘" if topic=="水果" else "书架第二层" if topic=="借来的书" else area)}。'
    if relation=='当前状态未知': known=''
    rows.append({'sample_id':f'component.preview.{i+1:03d}','component_ids':{'scene_id':f'home.{area}','snapshot_id':f'snapshot.{i+1:03d}','relation_id':f'relation.{i%5}','utterance_id':f'casual.{i%5}','contract_id':'reply.unknown.safe.v1'},'topic':topic,'scene':{'区域':area,'时间':time,'天气来源':'权威场景快照'},'authoritative_state':{'人物位置':{'男主':area,'白未晞':area},'事实':known or None,'明确未知':[f'{topic}的{predicate}'] if not known else [],'男主基础设定引用':'protagonist.sichuan.v1'},'old_memory':[f'男主：之前聊过{topic}。','白未晞：我记得那次的说法。'],'recent_dialogue':[f'男主：我又想起{topic}了。','白未晞：嗯，你刚才还提到它。'],'relation':relation,'current_user':QUESTIONS[i%5],'target_contract':{'must_use_authority':True,'unknown_policy':'无权威状态时明确说不知道','forbidden':['虚构动作','补充未登记事实','空回复']}})
OUT.mkdir(parents=True,exist_ok=False); (OUT/'component_preview15.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8'); (OUT/'report.json').write_text(json.dumps({'total':15,'unique_topics':15,'status':'structural_preview_only','training_started':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(str(OUT))
