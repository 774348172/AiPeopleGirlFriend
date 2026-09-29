"""New authored validation inputs; dialogue answers are generated only by the local model."""
import copy
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from build_authority_component10 import build_components
from build_v5_pilot_dataset import _load_baiweixi_render_context
from convert_component_to_game_reply import convert, validate_final_messages
from joint_scene_planning import apply_plan, freeze
from pilot_pipeline import persist
from run_component_game_reply15_pilot import build_messages
from run_mixed_candidate_batch import duplicate_reason, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '训练数据/expanded_five_scene16_20260914'
# scene, area, time, task, memory, recent, question, pre-authored visible states
CASES = [
('home','living_room','09:20','follow_up_question',
 ['家里遥控器最好有个固定位置。','免得每次找半天。','就放电视柜吧。','找完也放回去？'],
 ['我想开电视听点声音。','遥控器找到了吗？','电视柜上没看见，今天是不是换地方了？','你先别把柜子翻乱了。'],
 '那它这会儿在哪儿呢？', [('遥控器','当前位置','双人餐桌上')]),
('home','small_balcony','11:10','follow_up_question',
 ['想等衣服洗好以后再出去。','别把时间安排太紧。','我也怕还没洗完就得出门。','到时候再看进度。'],
 ['我来阳台看看洗衣机。','还惦记着出门的时间？','是啊，听它还在转。','你想知道还要多久？'],
 '嗯，它现在洗到哪一步了，还剩几分钟？', [('洗衣机','运行模式','脱水'),('洗衣机','电源状态','通电'),('洗衣机','剩余时间',None)]),
('home','living_room','19:30','idle_chat',
 ['如果能给家里留一个什么都不干的角落就好了。','坐着发呆也行？','行，不用想着有什么用。','听着挺省心的。'],
 ['我又想起那个发呆角落了。','准备选在哪里？','客厅不是有立式暖光灯嘛，就想从那附近挑。','想象一下也行。'],
 '要是由你来布置，你最想往那个角落放什么？', []),
('home','kitchen','18:40','emotion_attunement',
 ['我总想把家里收拾得特别整齐。','非得一点不乱吗？','也不是，就是有时候停不下来。','留点随意的地方也没事。'],
 ['看着厨房的杯架，我又想给每个杯子排队了。','杯子还要站得一样齐呀？','说起来挺好笑，我自己也觉得累。','今天不想排也可以。'],
 '那我今天偷个懒，你可别笑我啊。', []),
('songjiangfu.neighborhood_cafe','普通座位区','14:20','follow_up_question',
 ['去咖啡店时，我不一定非要喝咖啡。','换点别的也行。','比如热可可，名字听起来就暖和。','下次看看。'],
 ['常规目录里有热可可。','你今天想喝这个？','嗯，不过目录上有也可能卖完了。','那看今天的供应。'],
 '今天还能点热可可吗？现在这个规格多少钱？', [('街角咖啡','是否营业','是'),('街角咖啡','今日供应','热可可今天已售罄；热牛奶目前有供应'),('街角咖啡','实售价',None)]),
('songjiangfu.neighborhood_cafe','窗边座位','16:10','idle_chat',
 ['有些地方一个人坐也挺舒服。','不觉得无聊？','偶尔会想找个人说两句。','也不用一直说话。'],
 ['这回坐在窗边，我倒不急着找话题。','那就慢慢坐。','可跟你在一起又会好奇你在想什么。','想到什么就问吧。'],
 '你觉得两个人安安静静待着，也算约会吗？', [('街角咖啡','是否营业','是'),('街角咖啡','座位使用情况','两人已坐在窗边座位，其他座位使用情况未登记')]),
('songjiangfu.neighborhood_cafe','点单收银区','12:30','follow_up_question',
 ['我挺怕把店里的人叫错。','不知道名字就别乱叫。','叫店员好像又有点生硬。','说声你好也可以。'],
 ['现在有人在收款台那边。','你要找人问点单的事？','嗯，不知道该怎么称呼。','先客气地打个招呼吧。'],
 '你知道正在收款的那位叫什么吗？', [('街角咖啡','是否营业','是'),('街角咖啡','店员是否在场','是'),('街角咖啡','收银负责人','一名店员正在收款'),('街角咖啡','已获知人员姓名',None)]),
('songjiangfu.neighborhood_convenience_store','食品货架区','17:20','follow_up_question',
 ['买零食我想控制一下数量。','一回别挑太多？','对，先拿一样喜欢的。','到时候你可别纠结半天。'],
 ['这回我只挑了一包原味薯片。','已经拿好了吗？','拿好了，钱还没付。','那结账之前还不算买完。'],
 '这包现在卖几块？我一共该付多少？', [('原味薯片','当前价格','所选这包价签为6元'),('本次购物','已挑选清单','一包原味薯片，无其他商品'),('本次购物','是否已结算','否'),('本次购物','付款结果','尚未付款')]),
('songjiangfu.neighborhood_convenience_store','日用品货架区','18:10','light_teasing',
 ['买日用品的时候我总觉得自己特别会过日子。','只是买个东西就这么得意？','仪式感嘛。','你还挺会给自己找理由。'],
 ['看到购物篮，我又有那种很会持家的感觉了。','篮子还没装东西呢。','所以才说只是感觉。','原来是空篮子的功劳。'],
 '那这位空篮子选手，今天能得你几分夸奖？', [('本次购物','已挑选清单','尚未挑选商品')]),
('songjiangfu.neighborhood_convenience_store','冷藏柜区域','19:10','idle_chat',
 ['如果包装能自己说话就有意思了。','会拼命劝你买它吧。','那我肯定躲得远远的。','说不定还有害羞的。'],
 ['你说的害羞包装，我还记着呢。','又想给它编台词？','嗯，这次假想一盒不想被人注意的牛奶。','听着有点矛盾。'],
 '你觉得这盒假想牛奶会小声嘀咕什么？', []),
('songjiangfu.shopping_walk','杂货与新奇小店段','15:30','follow_up_question',
 ['逛小街我想慢慢走，不赶着打卡。','走累了就歇会儿。','嗯，走多远算多远。','不用非走到头。'],
 ['我有点想歇脚了。','找地方坐一会儿？','先想知道到出口还远不远。','别把离家的距离算进来。'],
 '从我们现在这儿沿主街走到出口，还有多少米？', [('当前路线','当前地点','杂货与新奇小店段'),('当前路线','起点','街道入口'),('当前路线','目的地','街道出口'),('当前路线','已走距离','500米'),('当前路线','剩余距离','400米')]),
('songjiangfu.shopping_walk','沿街店铺一侧','16:40','idle_chat',
 ['如果有个小摆件，我想先给它起名字。','还没买就取名字？','只是想象，不一定买。','那名字可以随便一点。'],
 ['拾趣的橱窗里那只小船挺有意思。','你想接着玩取名字？','是啊，先不考虑买不买。','想叫它什么？'],
 '叫它“迟到号”会不会太委屈这只小船了？', [('拾趣杂货铺','橱窗物品','一只蓝色小船摆件'),('拾趣杂货铺','当前价格',None)]),
('songjiangfu.shopping_walk','顶棚休息处','17:15','emotion_attunement',
 ['有时候出门也没什么明确目的。','只是想换个地方待着？','嗯，不想一直想着该完成什么。','那散散步也好。'],
 ['我们坐这儿歇歇吧。','你不想接着逛了？','忽然觉得今天没买什么也挺好。','不买也不算白来。'],
 '嗯，今天就当陪我把脑袋放空一会儿，好不好？', [('百花里小街逛街途中','是否坐下','两人已坐在顶棚休息处'),('百花里小街逛街途中','遮雨处是否可用','可用'),('百花里小街逛街途中','是否购买','本次尚未购买任何东西')]),
('songjiangfu.protagonist_cafe','顾客座位区','20:20','idle_chat',
 ['店里的饮品名字，我想留点好玩的余地。','不一定都起得很正式？','是啊，偶尔冒出个奇怪的也不错。','别让人看不懂就行。'],
 ['今天打烊了，我想聊个新名字。','哪一款？','还没做，只是假想一款带桂花香的饮品。','先从名字想起呀。'],
 '叫它“晚一点回家”，会不会像在给我加班找借口？', [('心愿咖啡馆','是否营业','否'),('心愿咖啡馆','是否已打烊','是'),('心愿咖啡馆','顾客数量','0'),('试做饮品','名称或未命名状态','尚未定名的假想桂花饮品'),('试做饮品','是否已经制作','否')]),
('songjiangfu.protagonist_cafe','咖啡制作区','13:40','follow_up_question',
 ['光听咖啡机响，我很难猜出它做到哪儿了。','还是看具体进度吧。','嗯，不能靠声音猜已经做好。','不然容易拿错时候。'],
 ['我想核对一下这杯拿铁的进度。','你手上这单？','对，就这一杯。','你想确认哪一步？'],
 '现在是在做还是已经交给客人了？', [('心愿咖啡馆','是否营业','是'),('当前订单','品项','拿铁'),('当前订单','数量','1杯'),('当前订单','制作阶段','男主正在制作，尚未完成'),('当前订单','是否已交付','否')]),
('songjiangfu.protagonist_cafe','顾客座位区','20:35','emotion_attunement',
 ['忙的时候我总想赶紧弄完所有事。','弄完以后呢？','可能还会找下一件事。','那你什么时候休息？'],
 ['今天打烊了，我还是有点停不下来。','还有没做完的订单吗？','没有了，我就是心里惦记。','你已经说今天没有订单剩下了。'],
 '你陪我坐一会儿就好，先别帮我想明天的事。', [('心愿咖啡馆','是否营业','否'),('心愿咖啡馆','是否已打烊','是'),('心愿咖啡馆','当前订单','无未完成订单'),('心愿咖啡馆','顾客数量','0'),('心愿咖啡馆','白未晞本轮是否帮忙','未参与本轮经营或清洁')]),
]

def main():
    if (OUT/'config.json').exists(): raise ValueError('Batch already started; do not rewrite frozen inputs')
    OUT.mkdir(exist_ok=True)
    (OUT/'plans').mkdir(exist_ok=True)
    base=build_components()[0]
    _,_,anchor=_load_baiweixi_render_context()
    seeds=[]; previous=[]; checks=[]
    for i,(scene,area,time,task,memory,recent,question,states) in enumerate(CASES,1):
        sid=f'expanded16.{i:03d}'
        seed=copy.deepcopy(base)
        seed.update(sample_id=sid, task_type=task, known_profile={}, current_user=question, required_current_user=question,
                    topic='自然承接相关历史：'+question, review_criteria=['事实有来源，未知不编造；目录不等于库存，通常归位点不等于当前位置。','自然接续近期对白，保持白未晞的语气；主观假想不必改成查状态。'])
        seed.pop('fact_contract',None)
        seed['component_ids']['scene_id']=base['component_ids']['scene_id'] if scene=='home' else scene
        for key in ('family_id','snapshot_id','relation_id','utterance_id'): seed['component_ids'][key]=sid+'.'+key
        now=datetime.fromisoformat('2026-09-14T'+time+':00+08:00')
        seed['snapshot'].update(time=now.isoformat(),area=area,weather=['晴天','多云','阴天','小雨'][(i-1)%4],states=[])
        for name,data in seed['snapshot']['people'].items():
            data.update(area=area,activity='当面聊天',body='正常',emotion='平静')
        if i==15: seed['snapshot']['people']['男主'].update(area='咖啡制作区',activity='制作一杯拿铁并与白未晞说话')
        if i==16: seed['snapshot']['people']['男主']['emotion']='已主动说出有些放松不下来'
        for kind,texts,delta in [('memory_turns',memory,timedelta(days=2)),('recent_turns',recent,timedelta(minutes=4))]:
            seed[kind]=[dict(speaker='男主' if n%2==0 else '白未晞',text=text,time=(now-delta+timedelta(seconds=n*30)).isoformat(),source=f'{sid}.{kind}.{n}') for n,text in enumerate(texts)]
        seed['relation']='两天前的同主题对话→最近两轮承接→当前自然回应；新快照优先于旧状态，主观假想不当成已完成事件。'
        plan={'intent':seed['topic'],'states':[dict(subject=s,predicate=p,value=v,visibility='visible') for s,p,v in states]}
        row=apply_plan(seed,plan)
        reason=duplicate_reason(row,previous)
        if reason: raise ValueError(reason)
        previous.append(row)
        scenario=convert(row); messages=build_messages(scenario,anchor)
        coverage=validate_final_messages(scenario,messages)
        persist(OUT/'plans'/f'{sid}.world.json',{'plan':plan,'authoring_method':'assistant_curated_synthetic_fixture_before_reply'})
        persist(OUT/'plans'/f'{sid}.json',{'component':row,'authoring_method':'assistant_curated_related_history_not_model_generated'})
        checks.append({'sample_id':sid,'snapshot_sha256':freeze(row),'coverage':coverage,'input_characters':sum(len(m['content']) for m in messages)})
        seeds.append(seed)
    write_jsonl(OUT/'seeds.jsonl',seeds)
    persist(OUT/'curated_input_preflight.json',{'total':len(seeds),'scenes':dict(Counter(s['component_ids']['scene_id'] for s in seeds)),'checks':checks,'authoring_method':'assistant authored inputs; actual answers generated separately by Qwen3.5 27B'})
    print(f'Prepared {len(seeds)} new validated inputs: {OUT}')

if __name__=='__main__': main()
