"""Diverse seed schedules; world plans, history and answers are left to local models."""
import argparse
import copy
import hashlib
import json
import random
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path

from build_authority_component10 import build_components
from build_v5_pilot_dataset import _load_scene_canon, _load_baiweixi_render_context
from convert_component_to_game_reply import convert, validate_final_messages
from run_component_game_reply15_pilot import build_messages
from pilot_pipeline import persist

SCENES=['home.expanded','songjiangfu.neighborhood_cafe','songjiangfu.neighborhood_convenience_store','songjiangfu.shopping_walk','songjiangfu.protagonist_cafe']
IDLE=['把普通日子想成一本书，讨论章节名','如果把烦恼缩成小物件，想把它放在哪里','聊一种没有用途但让人开心的小发明','如果一天只能保留一种声音，想选什么','给假想中的旅行箱起名字','讨论不必实现的幼稚愿望','设计一场不买任何东西的小约会','想象能和影子说话的片刻','聊喜欢缓慢还是突然的惊喜','把今天比成一种颜色，但不声称真实经历','猜想会害羞的天气预报怎么说话','给虚构的路人编一个明显假想的小故事','讨论喜欢留白还是写满的明信片','不谈具体过去，聊理想中的周末','如果时间能装进瓶子，想留哪一刻','聊安静相处时是否需要找话题','假想给未来的自己留一句短话','讨论用什么比喻表达好奇','想象一个只收藏无用东西的盒子','聊有瑕疵的小东西会不会更亲切']
EMOTION=['男主主动说不想再比较自己与别人，希望被听见','男主明确说为尚未实施的小计划紧张，想有人陪聊','男主主动表达脑子很乱，暂时不想听解决办法','男主说有点没劲，希望轻松换个话题','男主担心自己的喜好幼稚，想被温和接纳']
TEASING=['给男主一个明确假想的奇怪称号','男主把一个微不足道的选择夸成大成就，善意打趣','两人玩明确虚构的颁奖游戏','男主提一个无害的笨办法，请女主吐槽','围绕男主当前说出的自夸轻松斗嘴']
FACTS={
SCENES[0]:['备用钥匙当前持有人和所在位置，与通常归位点分开','洗衣机程序已知而剩余时间未知','冰箱里剩菜及是否可食用，缺证据的保鲜结论不猜','微波炉内部食物与运行状态','厨房米桶剩余量，不从设施存在推库存','电热水壶加热状态与是否可以直接喝的区别','遥控器新位置与电视开关分别登记','阳台晾衣与衣物是否已经干分别登记','约定时间已知，取消或完成状态另记','白未晞纸箱的内容未知，不从默认位置推断内容'],
SCENES[1]:['今日供应与常规目录有区别','常规价目与本规格实售价有区别','店员在场但未告知姓名','谁正在收银与谁在制作分开记录','空位与插座是否可用分别确认','咖啡机正在运行但订单完成时间未知','已经点单但付款状态未知','热饮售罄与其他饮品可选范围','展示柜存在不代表今日有甜点','排队人数已知但等待分钟未知'],
SCENES[2]:['瓶装水具体规格与价签','鸡蛋库存已知但有效期未知','薯片在货架但还没有挑选','已挑选商品与已付款分开','收银是否开放与店员是否在场分开','购物篮存在不代表里面有什么','冷藏柜运行状态未知，不推食物保质期','一件商品有货不代表所有品牌都有','收银设备可用不证明某次付款成功','本次购物是否已经拿走与付款分别登记'],
SCENES[3]:['拾趣橱窗物品已知但售价未知','老街书铺是否营业与在售书名分别登记','百花小吃铺当前菜品与辣度','花间区域是否有临时花摊','主街长度与当前剩余路长分开','当前路线的目的地与是否到达分开','顶棚休息处本轮是否可用','购物袋持有人不证明是谁购买','已走距离与到达所需时间分开','当前看见的店铺与计划下一站分开'],
SCENES[4]:['当前订单制作阶段与交付状态','无员工，白未晞本轮也不能默认接待客人','已打烊但是否清洁完成另记','试做饮品未制作不能评价实际味道','常规十四款目录与今日库存分开','设备通电不代表正在制作订单','订单付款已知而交付未知','营业状态与当前是否继续接单','试做饮品命名与已试饮口味反馈分开','男主刚说今日无剩余订单，可接续轻松陪伴'],
}

def build_seeds(batch_id, count=200):
    if count not in (200,1000): raise ValueError('Supported balanced schedules: 200 or 1000 slots')
    if not batch_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in batch_id):
        raise ValueError('Use an ASCII batch ID for stable filenames')
    if count==1000:
        # Reuse topic families, never previously authored histories or answers.
        # Five directions diversify intent; only actual output dedup measures novelty.
        directions={
            'follow_up_question':['直接询问一个当前已知或未知的细节',
                '男主表达拿不准的猜想，邀请核实，不能把猜想写成事实',
                '先聊一个尚未实施的打算，再问决定前需要确认的条件',
                '围绕两项容易混淆的信息继续追问，分别保留已知与未知',
                '男主提出希望得到的结果，再问当前是否具备条件，不预设已完成'],
            'conversation':['从一个随口的感想引出，最后自然接话',
                '围绕同一主题交换两个主观选择，允许意见不同',
                '先表达一点犹豫，再自然追问对方的想法',
                '在同一主题上提出一个明确假想，接着聊细节',
                '男主先表达自己的主观看法，再邀请白未晞补充或反驳'],
        }
        groups=[]
        for index in range(5):
            rows=build_seeds(f'{batch_id}_part{index+1}',200)
            for row in rows:
                kind='follow_up_question' if row['task_type']=='follow_up_question' else 'conversation'
                row['topic']+=' 本条交流方向：'+directions[kind][index]+'。必须重新编写相关历史，不复用其他样本对白，不预写最终回复。'
                row['generation_schedule']={'version':'five_scene_1000_v1','part':index+1,
                    'history_policy':'independently_authored','topic_families_reused':True}
            groups.append(rows)
        return [groups[part][offset] for offset in range(200) for part in range(5)]
    rng=random.Random(int(hashlib.sha256(batch_id.encode()).hexdigest()[:16],16))
    base=build_components()[0]; schedules={}
    for scene in SCENES:
        spec=[('idle_chat',t) for t in IDLE]+[('emotion_attunement',t) for t in EMOTION]+[('light_teasing',t) for t in TEASING]+[('follow_up_question',t) for t in FACTS[scene]]
        rng.shuffle(spec); schedules[scene]=spec
    seeds=[]
    for j in range(40):
        for scene in SCENES:
            i=len(seeds); row=copy.deepcopy(base); sid=f'{batch_id}.{i+1:03d}'
            canon,_,home=_load_scene_canon(scene)
            zones=list(canon['layout']['spaces']) if home else canon['layout']['zones']
            if scene==SCENES[4]: zones=['顾客座位区','入口']
            zone=rng.choice(zones)
            task,topic=schedules[scene][j]
            day=datetime.fromisoformat('2026-09-15T00:00:00+08:00')+timedelta(days=rng.randrange(20))
            hours=range(8,22) if home else range(11,20) if scene==SCENES[1] else range(8,20) if scene==SCENES[2] else range(10,19) if scene==SCENES[3] else range(11,22)
            now=day+timedelta(hours=rng.choice(list(hours)),minutes=rng.randrange(60))
            if '已打烊' in topic: now=day+timedelta(hours=20,minutes=rng.randrange(10,60))
            name=canon.get('display_name',scene)
            row.update(sample_id=sid,task_type=task,known_profile={},topic=f'在{name}，围绕“{topic}”构建独立生活对话。话题由历史自然引出，不背诵设定。')
            row.pop('fact_contract',None); row.pop('required_current_user',None)
            row['component_ids']['scene_id']=scene
            for key in ('family_id','snapshot_id','relation_id','utterance_id'): row['component_ids'][key]=sid+'.'+key
            row['snapshot'].update(time=now.isoformat(),weather=rng.choice(['晴天','多云','阴天','小雨']),area=zone,states=[])
            for data in row['snapshot']['people'].values(): data.update(area=zone,activity='当面闲聊',body='正常',emotion='平静')
            for kind,delta in [('memory_turns',timedelta(days=rng.choice([1,2,4,7]))),('recent_turns',timedelta(minutes=rng.choice([3,4,6])))]:
                row[kind]=[dict(speaker='男主' if n%2==0 else '白未晞',text='待编剧生成相关原话',time=(now-delta+timedelta(seconds=n*25)).isoformat(),source=f'{sid}.{kind}.{n}') for n in range(4)]
            row['current_user']='待编剧生成自然承接的当前发言'
            row['relation']='规划阶段占位，不能作为候选；正式旧记忆、最近对话与当前发言由本地模型独立生成。'
            row['review_criteria']=['每条独立历史且与当前发言相关；日常聊天不要变成状态测验。','固定设施、常规资料、当前状态、过去原话必须区分。','未知不捏造；没有获知的男主人设与人员姓名不泄露。','简短回应可以自然成立；重复回复只作为多样性疑点，不直接判为事实错误。']
            seeds.append(row)
    return seeds

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--batch-id',required=True); parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--count',type=int,choices=[200,1000],default=200)
    args=parser.parse_args(); rows=build_seeds(args.batch_id,args.count)
    args.output_root.mkdir(exist_ok=False,parents=True)
    _,_,anchor=_load_baiweixi_render_context(); checks=[]
    for row in rows:
        scenario=convert(row)
        checks.append({'sample_id':row['sample_id'],'coverage':validate_final_messages(scenario,build_messages(scenario,anchor))})
    (args.output_root/'seeds.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    persist(args.output_root/'seed_schedule.json',{'batch_id':args.batch_id,'count':len(rows),'scenes':dict(Counter(r['component_ids']['scene_id'] for r in rows)),'tasks':dict(Counter(r['task_type'] for r in rows)),'inputs_are_planning_seeds_not_training_samples':True,'checks':checks})
    print(json.dumps({'count':len(rows),'output':str(args.output_root)},ensure_ascii=False))

if __name__=='__main__': main()
