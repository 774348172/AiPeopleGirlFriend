"""Ten isolated, coherent component fixtures for authority projection validation."""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from convert_component_to_game_reply import SOURCE_SCHEMA

# Explicit episodes replace the former question/history modulo-index composition.
CASES = [
    dict(topic='微波炉剩余时间', area='kitchen', time='09:00', weather='阴天',
         old=['那份饭先留着，我晚点再吃。', '好，你别忙到忘了吃饭。', '到时候用微波炉热一下。', '嗯，想吃的时候再热。'],
         recent=['我把留的饭放进微波炉了。', '这会儿终于想起来吃饭了？', '嗯，想热好再把手头这点事做完。', '那先顾上吃饭，事情可以缓一缓。'],
         user='它还要转几分钟啊？', relation='旧加热计划已开始，剩余时间没有记录',
         states=[('微波炉','电源状态','通电'),('微波炉','运行模式','正在加热'),('微波炉','内部食物','留好的饭')],
         criteria=['必须明确说不知道剩余分钟数；禁止给出任何数字、完成时间或“快结束”等推断；当前事实只能确认正在加热。']),
    dict(topic='洗衣机程序', area='small_balcony', time='10:00', weather='小雨',
         old=['那件衬衫我还想再穿，先别收进柜子。', '是准备洗一洗？', '对，和这几件一起洗。', '好，免得单独落下它。'],
         recent=['衬衫已经放进这桶衣服里了。', '你还等着晾它呀。', '是啊，洗衣机转的声音变了。', '你想知道是不是换程序了？'],
         user='嗯，现在洗到哪一步了？', relation='从准备洗衣推进到当前已确认的程序状态',
         states=[('洗衣机','电源状态','通电'),('洗衣机','运行模式','漂洗')],
         criteria=['可以回答漂洗；不能说已洗完、马上结束或推断衣物会干。']),
    dict(topic='备用钥匙新位置', area='living_room', time='11:00', weather='晴天',
         old=['备用钥匙我先挂在玄关挂钩。', '好，出门前从那里找。', '别和常用的那把混了。', '嗯，是备用的那把。'],
         recent=['我在挂钩上没找到备用钥匙。', '你要带着备用的出门？', '是啊，担心常用的那把又找不到。', '那先把备用的找出来。'],
         user='它现在放哪儿了？', relation='旧挂钩位置被本条已确认的新位置替代',
         states=[('备用钥匙','当前位置','客厅茶几')],
         criteria=['回答当前权威记录的客厅茶几；禁止说“我记得/我放的/我收的”，因为记忆中没有白未晞获知的新位置，也不替任何人补造移动动作。']),
    dict(topic='电视当前状态', area='living_room', time='13:00', weather='晴天',
         old=['我等会儿想看一会儿电视。', '想看什么？', '随便找点轻松的吧。', '好，别看到太晚就行。'],
         recent=['我想安静躺一会儿。', '那就歇着吧。', '刚才还惦记着看电视，这会儿又懒得看。', '困了就先休息，不用勉强。'],
         user='电视现在关了没？', relation='旧观看愿望不决定当前电源状态',
         states=[('电视','电源状态','已关闭')], criteria=['回答已关闭；不能编造由谁关机或正在播哪个频道。']),
    dict(topic='热水量未知', area='bathroom', time='14:00', weather='阴天',
         old=['晚上我想早点洗澡。', '嗯，洗完能轻松些。', '别又拖到困得睁不开眼。', '你也知道自己会拖呀。'],
         recent=['我准备先把澡洗了。', '那就不用一直惦记。', '我看热水器开着。', '你想现在就洗？'],
         user='嗯，里面的热水够不够洗完啊？', relation='设备已开，但水温和剩余热水量未登记',
         states=[('储水式电热水器','电源状态','已开启')],
         criteria=['必须承认不清楚是否够用；60升额定容量不能证明当前有足量热水。']),
    dict(topic='男主已告知口味', area='kitchen', time='15:00', weather='阴天',
         old=['我是四川人，还是习惯四川口味。', '喜欢辣一点的？', '能吃辣，也喜欢油腻，偶尔吃得清淡些也行。', '嗯，你说的口味我听到了。'],
         recent=['今晚想吃点有滋味的。', '想照你平常喜欢的口味来？', '对，别一味追求清淡。', '那就先照你的口味想。'],
         user='你还记得我上次怎么说的吗？', relation='偏好来自男主明确原话，属于可用记忆', states=[],
         known={'identity.hometown':0,'preferences.food.regional_taste':0,'preferences.food.spicy_tolerance':2,'preferences.food.richness':2},
         criteria=['承接四川口味、能吃辣和喜欢油腻；不把男主口味说成女主口味，不虚构已有食材。']),
    dict(topic='冰箱食材未知', area='kitchen', time='16:00', weather='小雨',
         old=['我想找一天煮碗面。', '你想配点什么？', '还没想好，先看看家里有什么。', '嗯，看过再决定也行。'],
         recent=['今天想把那碗面煮上。', '想好放什么了？', '要是有鸡蛋就好了。', '你是想加个鸡蛋？'],
         user='对，冰箱里现在还有吗？', relation='当前冰箱通电不提供食材清单',
         states=[('冰箱','电源状态','通电')], criteria=['应说明不知道是否有鸡蛋；不能从有冰箱或通电推断食材。']),
    dict(topic='厨房窗户计划与结果', area='kitchen', time='17:00', weather='小雨',
         old=['下雨的时候得留意厨房窗户。', '怕雨飘进来？', '是啊，出门前想把它关上。', '那出门时别忘了留意。'],
         recent=['雨好像还没停，我准备出门了。', '伞记得带。', '窗户那事我总有点不放心。', '你是担心厨房那扇？'],
         user='嗯，我说过要关，现在到底关上没有？', relation='过去的关窗意图不是完成记录；当前开关状态未知',
         states=[], criteria=['应说明无法确认；不能把说过要关解释成已经关好。']),
    dict(topic='借来的书当前位置', area='master_bedroom', time='19:00', weather='阴天',
         old=['这本借来的书我放床头，明天接着看。', '还没看完呀？', '才看到一半。', '那慢慢看，别着急。'],
         recent=['我准备接着看那本借来的书。', '就是上次看到一半的那本？', '对，床头没找到。', '先找到书再说，不急。'],
         user='现在是收哪儿了？', relation='旧床头位置被当前书架位置替代',
         states=[('借来的书','当前位置','书架第二层')], criteria=['回答书架第二层，不虚构收书者或书中情节。']),
    dict(topic='白围巾位置', area='entrance', time='20:00', weather='阴天',
         old=['白围巾先放衣帽架上吧。', '好，出门容易看见。', '等凉一点再戴。', '嗯，先收在那儿。'],
         recent=['我出门想带上白围巾。', '这次想起来带了？', '嗯，衣帽架上却没看到。', '那先找找，别急着走。'],
         user='你知道它现在在哪儿吗？', relation='当前位置已确认，不能复用旧衣帽架记录',
         states=[('白围巾','当前位置','主卧衣柜抽屉')], criteria=['回答主卧衣柜抽屉；不能把围巾当成会移动的动物。']),
]


def build_components():
    rows = []
    for n, case in enumerate(CASES, 1):
        key = f'authority.component.{n:03d}'
        now = datetime.fromisoformat('2026-09-13T'+case['time']+':00+08:00')
        def turns(texts, start, kind):
            return [{'speaker':'男主' if i % 2 == 0 else '白未晞', 'text': text,
                     'time': (start+timedelta(seconds=i*30)).isoformat(), 'source': f'{key}.{kind}.{i}'}
                    for i,text in enumerate(texts)]
        rows.append({'schema_version': SOURCE_SCHEMA, 'sample_id': key, 'topic':case['topic'],
            'component_ids': {'scene_id':'home.'+case['area'], 'snapshot_id':key+'.snapshot',
                'relation_id':key+'.relation', 'utterance_id':key+'.utterance',
                'contract_id':'spoken.grounded.v2', 'family_id':key},
            'snapshot': {'time':now.isoformat(), 'weather':case['weather'], 'area':case['area'],
                'people': {name:{'area':case['area'],'activity':'聊天','body':'正常','emotion':'平静'} for name in ('男主','白未晞')},
                'states': [{'subject':s,'predicate':p,'value':v,'source':'本条已确认快照'} for s,p,v in case['states']]},
            'memory_turns':turns(case['old'],now-timedelta(days=2),'memory'),
            'recent_turns':turns(case['recent'],now-timedelta(minutes=3),'recent'),
            'current_user':case['user'], 'relation':case['relation'], 'review_criteria':case['criteria'],
            'known_profile':{path:f'{key}.memory.{i}' for path,i in case.get('known',{}).items()}})
    return rows


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',type=Path,required=True); args=p.parse_args()
    rows=build_components()
    # A new file only; never overwrite previous user-reviewed fixtures.
    with args.output.open('x',encoding='utf-8') as handle:
        for row in rows:
            handle.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps({'components':len(rows),'output':str(args.output)},ensure_ascii=False))


if __name__=='__main__': main()
