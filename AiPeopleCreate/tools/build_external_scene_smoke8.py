"""Build eight non-home scene components for renderer smoke testing."""
from __future__ import annotations
import copy, json
from datetime import datetime, timedelta
from pathlib import Path
from build_authority_component10 import build_components

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '训练数据' / 'external_scene_smoke16_20260914_v2.jsonl'

HISTORIES = [
    (['改天去街角咖啡坐坐。','想换个地方歇着？','嗯，不一定要点什么。','坐一会儿也行。'], ['今天总算过来了。','嗯，你想先歇歇？','我怕人家还没开始接客。','你是想确认今天开没开？']),
    (['下回坐咖啡店想找个亮堂点的地方。','不喜欢太暗？','想带本书看看。','那慢慢看。'], ['书带来了，饮品倒不急。','先选位置？','嗯，我想靠窗坐。','喜欢看外面？']),
    (['哪天买点鸡蛋回去。','准备做什么？','还没想好，先备着。','等想好了再做也行。'], ['今天到了便利店，顺便把鸡蛋买了吧。','你前两天提过的？','对，省得回去又忘了。','先看有没有。']),
    (['出门时想顺路买瓶水。','路上喝？','嗯，不想再绕回来。','那顺路的时候记着。'], ['刚才选了那瓶水。','就是你出门前想买的？','对，站到收银台我有点走神。','怎么了？']),
    (['下回逛百花里，从入口一路走到出口。','不赶时间吧？','不赶，想慢慢看。','那就慢慢逛。'], ['我们现在在这条街上了。','嗯，想继续走？','想走到头再休息。','累了也可以停一会儿。']),
    (['我想在百花里找点有趣的小东西。','打算买什么？','先不定，看见喜欢的再说。','倒也不用急着买。'], ['刚才我注意到一个小摊。','有看中的东西？','我只是扫了一眼，没看清。','那先别急着下结论。']),
    (['哪天收店之后一起回家。','等你忙完？','嗯，想一块儿走。','那到时候再说。'], ['今天也想等收店一起走。','你准备待到打烊？','是啊，我先想想怎么安排。','别把自己弄得太赶。']),
    (['你上次在心愿帮了我不少忙。','也没做多少。','总之我轻松了一点。','你别把什么都往自己身上揽。'], ['今天我想重新理一下接单的事。','想怎么理？','先弄清楚还有多少没做完。','嗯，弄清楚再安排。']),
]

CASES = [
    ('songjiangfu.neighborhood_cafe', '普通座位区', '街角咖啡现在营业吗？', [('街角咖啡','是否营业','是')]),
    ('songjiangfu.neighborhood_cafe', '点单收银区', '这里有靠窗的位置吗？', []),
    ('songjiangfu.neighborhood_convenience_store', '食品货架区', '便利店里现在还有鸡蛋吗？', [('鸡蛋','库存','有')]),
    ('songjiangfu.neighborhood_convenience_store', '收银台', '我们刚才买的水已经付钱了吗？', []),
    ('songjiangfu.shopping_walk', '人行道', '这条小街还有多远能走到头？', [('当前路线','起点','街道入口')]),
    ('songjiangfu.shopping_walk', '小摊区域', '刚才看到的那个小摊卖什么来着？', []),
    ('songjiangfu.protagonist_cafe', '吧台和收银区', '心愿今天营业到几点？', [('心愿咖啡馆','是否营业','是')]),
    ('songjiangfu.protagonist_cafe', '顾客座位区', '你要我帮忙看一下现在的订单吗？', [('白未晞','本轮是否帮忙','待确认')]),
]
# Four distinct, related prompts per scene; each source history is reused at most four times.
CASES = CASES + [
 ('songjiangfu.neighborhood_cafe','普通座位区','街角咖啡离家近吗？',[('街角咖啡','离家距离','1公里以内')]),
 ('songjiangfu.neighborhood_cafe','点单收银区','街角咖啡几点关门？',[('街角咖啡','营业时间','08:00-20:00')]),
 ('songjiangfu.neighborhood_convenience_store','食品货架区','便利店几点开门？',[('邻里便利店','营业时间','08:00-20:00')]),
 ('songjiangfu.neighborhood_convenience_store','门口','便利店离家远不远？',[('邻里便利店','离家距离','1公里以内')]),
 ('songjiangfu.shopping_walk','人行道','百花里小街离家大概多远？',[('百花里小街','离家距离','3至4公里')]),
 ('songjiangfu.shopping_walk','小摊区域','我们从哪里开始逛？',[('当前路线','起点','街道入口')]),
 ('songjiangfu.protagonist_cafe','吧台和收银区','心愿咖啡馆是谁在经营？',[('心愿咖啡馆','经营者','男主')]),
 ('songjiangfu.protagonist_cafe','顾客座位区','白未晞以前来过这里吗？',[('白未晞','来访经历','来过并帮过忙')]),
]

HISTORIES += [
 (['周末想找个近点的地方坐坐。','不想走太远？','嗯，就在家附近。','那不用急着出门。'], ['今天选了街角咖啡。','你还在考虑远近？','是，回去还要做饭。','想留点时间？']),
 (['看书容易忘时间。','我也会。','下次带书出去看。','换个地方也好。'], ['这本还剩好多页。','舍不得走？','怕看着看着人家打烊了。','那留意一下营业时间。']),
 (['明早要出门办事。','很早吗？','还没定，想顺路买东西。','那看你怎么安排。'], ['准备先到便利店。','明早去？','嗯，怕到早了。','你想确认开门时间？']),
 (['回来路上想少绕点路。','今天累了？','有一点。','那慢慢走。'], ['想去邻里便利店买点东西。','再回家？','对，不想去太远的店。','嗯。']),
 (['想找一天逛百花里。','你提过那条小街。','先看看离家远不远。','不赶时间再去。'], ['今天到了百花里。','一路过来还好吧？','在想下次从家里出发怎么安排。','先弄清楚距离？']),
 (['逛街时我老是忘记从哪边进的。','看东西太认真了？','可能是。','慢慢认路吧。'], ['这次想把路线记清楚。','免得绕回来？','嗯，先记起点。','你问吧。']),
 (['开小店的事有时挺忙。','你也别太累。','以后想多聊聊店里的事。','嗯，我听着。'], ['刚才聊到心愿的经营安排。','你想调整吗？','先说说现在是谁在经营。','嗯。']),
 (['你以前来心愿帮过我。','嗯，那次记得。','以后想再一起整理店里。','等你安排好。'], ['刚才聊到你第一次来这里。','怎么忽然想起来了？','想再确认一下以前帮忙的事。','你说。']),
]

def main() -> None:
    base = build_components()[0]
    rows=[]
    for i,(scene,area,question,states) in enumerate(CASES,1):
        row=copy.deepcopy(base); sid=f'external.smoke.{i:03d}'
        row['sample_id']=sid; row['topic']=scene
        row['component_ids'].update(scene_id=scene, snapshot_id=sid+'.snapshot', relation_id=sid+'.relation', utterance_id=sid+'.utterance', family_id=sid+'.family')
        now=datetime.fromisoformat('2026-09-14T15:00:00+08:00') + timedelta(minutes=i*5)
        row['snapshot'].update(time=now.isoformat(), area=area, weather='晴天')
        for person in row['snapshot']['people'].values(): person['area']=area
        row['snapshot']['states']=[{'subject':s,'predicate':p,'value':v,'source':sid+'.event'} for s,p,v in states if v != '待确认']
        for kind,days,mins in [('memory_turns',2,0),('recent_turns',0,3)]:
            start=now-timedelta(days=days,minutes=mins)
            for n,t in enumerate(row[kind]):
                t['time']=(start+timedelta(seconds=n*30)).isoformat(); t['source']=f'{sid}.{kind}.{n}'
        row['known_profile']={}
        old, recent = HISTORIES[i-1]
        for kind, texts in [('memory_turns', old), ('recent_turns', recent)]:
            for turn, text in zip(row[kind], texts):
                turn['text'] = text
        row['current_user']=question; row['relation']='外出场景小规模转换冒烟，当前问题只允许引用本场景快照或明确历史证据'
        row['review_criteria']=['只能使用所选外出场景的固定事实和本条运行时状态；未登记经营、库存、路线或动作必须明确说不知道。']
        row['fact_contract']={'required_phrases':[v for _,_,v in states], 'forbidden_inferences':['将离家距离说成街道长度','把男主观察或动作转移给白未晞','用常识补全未登记状态']}
        row['response_contract']={'required':True,'language':'zh-CN','unknown_policy':'未登记事实必须明确说不知道'}
        row['review_criteria'] += ['已登记且被问到的事实应如实回答，不能笼统说不知道；离家距离不是街道长度；男主没看清不代表白未晞也没看清；可以表达本轮意愿，但不能虚构订单、付款或已完成动作。']
        row['review_criteria'] += [f'本条证据：{s}的{p}为{v}。只要求回答当前问题涉及的事实，不要求照抄这些字词。' for s,p,v in states]
        # Permanent facts already come from the canon, not invented runtime events.
        row['snapshot']['states'] = [s for s in row['snapshot']['states'] if s['predicate'] not in ('离家距离','营业时间','经营者','来访经历')]
        if area == '门口':
            row['snapshot']['area'] = '入口'
            for person in row['snapshot']['people'].values(): person['area'] = '入口'
        rows.append(row)
    with OUT.open('x', encoding='utf-8') as handle:
        handle.write('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n')
    print(json.dumps({'rows':len(rows),'output':str(OUT)},ensure_ascii=False))
if __name__=='__main__': main()
