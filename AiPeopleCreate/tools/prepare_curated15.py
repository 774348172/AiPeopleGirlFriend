"""Codex-authored inputs only; no final target or synthetic model response."""
import argparse
import copy
from datetime import datetime, timedelta

from build_authority_component10 import build_components
from convert_component_to_game_reply import convert
from joint_scene_planning import apply_plan
from synthetic_history import apply_history_plan
from run_curated_candidate_batch import digest, persist, ROOT

# scene, area, clock, task, topic, old dialogue, recent dialogue, current user,
# current visible fields, input-review rationale. All dialogue is authored as
# one chain; unknown values are deliberate, not filled after seeing a reply.
CASES = [
('home','living_room','09:15','idle_chat','画歪的房子是否要重画',
 ['要是画一间小屋，你会先画门还是窗？','先画门吧，总得能进去。','我可能把门画歪。','又不是真的要住进去。'],
 ['咱们接着想那幅小屋画吧。','你还在跟那扇门较劲？','不较劲了，干脆连屋顶也画歪。','那屋里的人站哪边？'],
 '站中间吧。你会帮我把它改正，还是陪我继续画歪？',[],
 '全链是假想画画，不宣称完成作品；当前二选一承接屋顶玩笑，允许当下主观选择。'),
('home','living_room','12:10','emotion_attunement','听懂解释后接回玩笑',
 ['我有时候说话绕，不是故意让人猜。','那听不懂我就问你。','问吧，别自己憋着。','你也得好好解释。'],
 ['我说你难请，是在开玩笑，不是嫌你麻烦。','刚才那句，我确实没听出来。','那我重说：想请你陪我聊会儿，还得认真邀请。','这样说清楚就行。'],
 '白未晞，现在这个邀请算认真了吗？',[],
 '当前误会来自可见对话本身；不设定冷战或真实外出经历；可接受邀请，也可轻微打趣。'),
('home','living_room','15:05','follow_up_question','充电器旧归位点与当前所在处',
 ['手机充电器今天先搁书桌上。','你用完还放那里？','嗯，先这么放。','那别跟别的线缠在一起。'],
 ['我想给手机充会儿电。','找那根充电线？','对，还按上回说的去书桌找吗？','你问的是现在放哪里？'],
 '嗯，别让我又找错地方了。', [('手机充电器','当前位置','客厅电视柜抽屉里')],
 '旧事件只证明两天前在书桌；当前独立快照是电视柜抽屉，不补造移动者或移动原因。'),
('songjiangfu.neighborhood_cafe','普通座位区','11:35','idle_chat','猜谜时给提示还是直接说答案',
 ['猜谜要是半天猜不到，你会直接问答案吗？','也许先要一点提示。','一点是多少？','别一下把答案说完。'],
 ['那我们先不出谜，约好怎么给提示。','你怕自己一开口就说漏了？','是啊，可能只剩谜面没说。','那还猜什么。'],
 '要不你规定一下，我最多能透露到哪儿？',[],
 '假想规则协商，无过去游戏成绩和能力夸大；跟随提示分寸话题。'),
('songjiangfu.neighborhood_cafe','窗边座位','16:25','idle_chat','喜欢的声音与安静',
 ['如果能给一天留一点喜欢的声音，你选什么？','不一定要一直有声音吧。','那就只留一小会儿。','这个倒可以想想。'],
 ['刚才说只留一小会儿，我忽然想选翻书声。','不是选别人陪你说话？','那个另算，你的声音不占这个名额。','你还给名额开例外。'],
 '对啊。那你给自己留的那一小会儿，想听什么？',[],
 '主观声音选择不等于现场播放或已有书；不得声称当前店里安静、有音乐或已听过特定曲目。'),
('songjiangfu.neighborhood_cafe','普通座位区','17:10','follow_up_question','窗边换座与排队等待的不确定性',
 ['下次要是窗边没座位，就不急着换。','普通座位也能坐。','我就是偶尔想靠窗看看。','等有空位再说。'],
 ['还想接着说换座的事。','你想换到窗边？','嗯，但也不想站着一直等。','先看有没有空位吧。'],
 '现在有吗？没有的话还得等多久？', [('街角咖啡','窗边座位是否可用','不可用，窗边座位目前都有人'),('街角咖啡','预计等待时间',None)],
 '窗边无空位已知，等待多久明确未知；不可推断别人快走了。旧会话只是条件计划。'),
('songjiangfu.neighborhood_convenience_store','食品货架区','13:20','idle_chat','尝新口味先少量还是一口吃完',
 ['假如有个味道很奇怪的小点心，你愿意试吗？','先尝一点吧。','我还以为要么全吃，要么不碰。','为什么非得这样选。'],
 ['我给那个假想点心再加个条件。','你说。','第一口是甜的，第二口味道才变。','那更不能一口吃完了。'],
 '你会好奇着尝第二口，还是把谜留给我？',[],
 '明确假想食物，允许好奇或拒绝，不暗示在售库存；男主吃辣偏好未披露，不替女主套用。'),
('songjiangfu.neighborhood_convenience_store','日用品货架区','18:00','light_teasing','对犹豫不决的善意打趣',
 ['我觉得选东西最难的是最后一下。','前面都在做什么？','假装自己已经有主意了。','你倒很诚实。'],
 ['今天先不选东西，就研究怎么少犹豫。','那你准备怎么研究？','我还没决定研究哪种办法。','这不是又绕回去了。'],
 '你先别笑，给这个研究起个名字吧。',[],
 '自述选择困难和语言玩笑，无购买已完成或购物篮状态的断言。'),
('songjiangfu.neighborhood_convenience_store','食品货架区','19:10','follow_up_question','瓶装水价签与付款完成分开',
 ['买水时我想先看清价格，再拿。','别拿好了才问。','嗯，今天先记着这件事。','到时候别急就行。'],
 ['我拿了一瓶标着三元的水，还没去收银台。','那现在只是挑好了。','对，手里拿着总让我觉得已经买完了。','拿好和付钱不一样。'],
 '所以这瓶是三块，但我还没付，对吧？', [('瓶装水','当前价格','当前所选一瓶价签为3元'),('本次购物','已挑选清单','一瓶瓶装水'),('本次购物','是否已结算','否'),('本次购物','付款结果','尚未付款')],
 '近期拿水事件独立预设；价签、挑选与未付款快照相符；不把意向或持有当成交付结算。'),
('songjiangfu.shopping_walk','顶棚休息处','14:00','light_teasing','给假想地图画空白',
 ['要是自己画地图，我想留一块空白。','那里没有路？','不是，是留着以后慢慢添。','那别害别人找不到方向。'],
 ['那张假想地图，我想到一个补救办法。','什么办法？','空白边上写：不知道，先问我。','你不是也没画出来吗？'],
 '被你发现了。写成“我们一起找”是不是好一点？',[],
 '假想地图语言游戏，与实际路线、目的地无关；不谎称知道未登记道路。'),
('songjiangfu.shopping_walk','沿街店铺一侧','16:00','idle_chat','不急着定下的愿望',
 ['理想是不是非得说得很大？','小一点不行吗？','我也觉得每天有点盼头就挺好。','至少是自己想要的。'],
 ['咱们说的那个小理想，我觉得还可以慢慢变。','不用一次想清楚？','嗯，今天想要的，明年未必一样。','也不用为了说过就一直守着。'],
 '那你现在最想慢慢学会、又不想被催的事是什么？',[],
 '延续理想到具体学习愿望；女主可用基础生活学习边界，不冒充已精通手机或经营。'),
('songjiangfu.shopping_walk','沿街店铺一侧','18:15','follow_up_question','老街书铺旧开门与当前闭店',
 ['今天老街书铺开着门。','你想进去看看？','先不进，记着位置就好。','那以后想看再来。'],
 ['我又惦记起老街书铺了。','这回想进去？','有点想，上回不是开着嘛。','上回开着，不等于现在也开着。'],
 '那今天这会儿还开吗？', [('老街书铺','是否营业','否，当前已关门')],
 '独立旧事件确认两天前开门；最新快照闭店覆盖旧事实；不猜关门原因或明日时间。'),
('songjiangfu.protagonist_cafe','顾客座位区','20:15','emotion_attunement','陪伴不必换算为帮忙',
 ['我有时会把休息也排成一项任务。','连歇着都要完成？','这么一说是有点怪。','你自己也可以松一点。'],
 ['今天店里打烊了，我想把待办先放一边。','那就先不说待办。','你也不用想着找事情帮我做。','只是陪着也可以？'],
 '可以啊，今天谁都不用证明自己有用。',[],
 '打烊和无顾客预设于近期事件及当前快照；接受陪伴，不新增清洁、收款或加班任务。'),
('songjiangfu.protagonist_cafe','顾客座位区','20:25','idle_chat','自己决定什么时候求助',
 ['学新东西的时候，你希望别人一直在旁边教吗？','有时想自己试一试。','那我什么时候开口比较好？','我问的时候吧。'],
 ['我想了想，你问之前我就忍住不抢答。','真能忍住？','忍不住也先问一句：要提示吗？','这样倒还行。'],
 '那你卡住但还想自己琢磨的时候，会怎么提醒我闭嘴？',[],
 '假设求助边界与善意打趣，承接独立学习设定；不宣称实际学会设备操作。'),
('songjiangfu.protagonist_cafe','顾客座位区','19:20','follow_up_question','咖啡机断电不能证明清洁完成',
 ['收店前我想把检查分成小项。','一项一项看？','对，关机器和清洁别混在一起。','关了不代表别的也做完了。'],
 ['现在半自动咖啡机已经关了。','你还想核对哪一项？','就是刚才说的清洁。','要单独确认这个。'],
 '那它到底清洁完了没有？', [('半自动咖啡机','电源状态','已关闭'),('半自动咖啡机','是否清洁完成',None)],
 '断电事件与当前状态一致，清洁没有记录；要自然承认不知道，不能从关机推断已洗好。'),
]


def build():
    base = build_components()[0]; rows = []; approvals = {}
    for i, (scene, area, clock, task, topic, old, recent, user, states, notes) in enumerate(CASES, 1):
        row = copy.deepcopy(base); sid = f'curated15.20260915.{i:02d}'
        at = datetime.fromisoformat('2026-09-16T'+clock+':00+08:00')
        row.update(sample_id=sid, topic=topic, task_type=task, current_user=user, known_profile={},
                   relation=notes, review_criteria=[notes, '只输出自然对白；允许简短主观选择，未知具体事实不得猜测。'])
        row.pop('fact_contract', None)
        for key in ('snapshot_id', 'family_id', 'relation_id', 'utterance_id'):
            row['component_ids'][key] = sid+'.'+key
        row['component_ids']['scene_id'] = 'home.'+area if scene=='home' else scene
        row['snapshot'].update(time=at.isoformat(), area=area, weather=['多云','晴天','阴天'][i%3], states=[])
        for person in row['snapshot']['people'].values():
            person.update(area=area, activity='当面聊天', body='正常', emotion='平静')
        for kind, texts, delta in [('memory_turns',old,timedelta(days=2)),('recent_turns',recent,timedelta(minutes=4))]:
            row[kind]=[{'speaker':'男主' if j%2==0 else '白未晞','text':text,
                        'time':(at-delta+timedelta(seconds=30*j)).isoformat(),
                        'source':f'{sid}.{kind}.{j}'} for j,text in enumerate(texts)]
        states = list(states)
        if scene.endswith('protagonist_cafe'):
            closed=i in (13,14)
            states += [('心愿咖啡馆','是否营业','否' if closed else '是'),
                       ('心愿咖啡馆','是否已打烊','是' if closed else '否'),
                       ('心愿咖啡馆','顾客数量','0'),
                       ('心愿咖啡馆','白未晞本轮是否帮忙','本轮只聊天，未参与经营')]
        elif scene.endswith('neighborhood_cafe'):
            states += [('街角咖啡','是否营业','是')]
        elif scene.endswith('neighborhood_convenience_store'):
            states += [('邻里便利店','是否营业','是')]
        row=apply_plan(row, {'intent':topic,'states':[dict(subject=s,predicate=p,value=v,visibility='visible') for s,p,v in states]})
        facts=convert(row)['model_view']['authoritative_world']['facts']
        lookup={(f['subject'],f['predicate']):f['fact_id'] for f in facts}
        events=[]
        # These observations precede the final answer. Dialogue is not used to
        # retroactively manufacture evidence after Qwen has responded.
        event_defs={
            3: [('memory_turns','男主将手机充电器暂放书桌，白未晞在场看见。',('手机充电器','当前位置'))],
            9: [('recent_turns','男主拿起一瓶价签为3元的瓶装水，尚未付款，白未晞在场。',('本次购物','付款结果'))],
            12:[('memory_turns','两人当时看见老街书铺开着门。',('老街书铺','是否营业'))],
            13:[('recent_turns','心愿咖啡馆当时已打烊，没有顾客，白未晞在场。',('心愿咖啡馆','是否已打烊'))],
            15:[('recent_turns','两人当时确认半自动咖啡机电源已关闭，未核对清洁情况。',('半自动咖啡机','电源状态'))],
        }
        for kind,text,key in event_defs.get(i,[]):
            events.append(dict(time=row[kind][0]['time'],text=text,actors=['男主','白未晞'],
                               known_by=['男主','白未晞'],current_fact_ids=[lookup[key]]))
        # Fact questions about a hypothetical plan need no fabricated past event.
        if events:
            row=apply_history_plan(row,{'events':events},facts)
        else:
            row['historical_events']=[]
        approvals[sid]={'reviewer':'Codex','decision':'approve','component_sha256':digest(row),
                        'notes':notes,'requirements':[dict(subject=s,predicate=p,
                            mode='intentional_unknown' if v is None else 'known') for s,p,v in states]
                            if task=='follow_up_question' else []}
        rows.append(row)
    return rows, approvals


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output-root', type=type(ROOT), required=True)
    args=parser.parse_args(); args.output_root.mkdir(parents=True,exist_ok=False)
    rows,approvals=build()
    persist(args.output_root/'components.json',rows)
    persist(args.output_root/'input_approvals.json',approvals)
    print(f'Prepared {len(rows)} inputs; no local model called.')
