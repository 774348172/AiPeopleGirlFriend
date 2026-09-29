"""Author reviewable synthetic inputs for the original 17 isolated cases.

States and dialogue history are authored fixtures, not claimed model outputs.
The batch runner still checks dependencies before generating real model replies.
"""
import argparse
import copy
import json
from datetime import datetime,timedelta
from pathlib import Path
from joint_scene_planning import apply_plan,freeze
from pilot_pipeline import persist
from convert_component_to_game_reply import convert,validate_final_messages
from run_component_game_reply15_pilot import build_messages
from build_v5_pilot_dataset import _load_baiweixi_render_context
from run_mixed_candidate_batch import duplicate_reason

ROOT=Path(__file__).resolve().parents[1]
CASES={
'015':dict(question='今天菜单上哪款饮品还没定好名字？',
 states=[('心愿咖啡馆','菜单明细','桂花蜂蜜饮品尚未定名，其他饮品本条未登记')],
 memory=['我想给菜单上的饮品起点合适的名字。','不用每个都起得很特别。','是，先记住哪些还没定名。','再慢慢想也行。'],
 recent=['今天又聊到菜单了。','还想接着想名字？','对，但我得先弄清楚哪款还没定。','先把那款挑出来。']),
'018':dict(question='所以对你来说，谨慎是怕浪费，还是怕承担选错的后果？',states=[],
 memory=['有时候我买东西会犹豫。','怕选错？','有点，也怕买回去浪费。','两种担心不太一样。'],
 recent=['刚才说到犹豫，我还想聊聊。','你在想哪一种担心更多？','嗯，不用举以前发生的事，就说现在的想法。','这样也能聊。']),
'023':dict(question='收银台现在有人值班吗，前面要排队吗？',
 states=[('邻里便利店','是否营业','是'),('邻里便利店','店员是否在场','是，在收银台值班'),('邻里便利店','收银负责人','一名值班店员，姓名未登记'),('邻里便利店','排队情况','前面有两位顾客')],
 memory=['挑东西的时候我容易忘了看看结账那边。','怕排队太久？','对，想先安排一下。','先确认再挑也可以。'],
 recent=['我想先看看结账要不要等。','还没决定买什么？','嗯，先问清楚收银那边的情况。','你说。']),
'028':dict(question='这次买鸡蛋的钱付过了吗？我记不准了。',
 states=[('鸡蛋','库存','有'),('邻里便利店','付款结果','本次鸡蛋购买尚未付款')],
 memory=['买完东西最好把账记清楚。','免得过后记混？','是，尤其别把挑好当成付过。','这两件事得分开。'],
 recent=['我刚才想到这次鸡蛋的账。','你拿不准哪一步？','不是问有没有库存，是问付款。','嗯，付款的事。']),
'037':dict(question='这家咖啡店现在还在营业吗？',states=[('街角咖啡','是否营业','是')],
 memory=['下次找个地方坐着慢慢聊。','你不想赶来赶去？','嗯，不过得在营业的时候去。','到时候先确认。'],
 recent=['我想在这里再坐一会儿。','还想聊刚才的话题？','想，但先确认店的营业状态。','嗯。']),
'065':dict(question='如果一杯想象中的饮品像雨后照在积水里的阳光，你会给它起什么名字？',states=[],
 memory=['给饮品起名字不一定都要实用。','当个小游戏？','对，不是真的往菜单里加。','那可以随便想想。'],
 recent=['接着玩起名字吧。','这回是什么感觉？','只是想象一杯饮品，店里没有也没关系。','嗯，你形容一下。']),
'078':dict(question='这家便利店现在营业吗，店员在店里吗？',
 states=[('邻里便利店','是否营业','是'),('邻里便利店','店员是否在场','是，正在收银台值班')],
 memory=['我有时会把安静理解成没人在。','安静不一定没人。','对，最好还是确认一下。','别只凭感觉猜。'],
 recent=['刚才说到店里安静，我想到一件事。','你想确认什么？','营业和有没有店员得分别问清楚。','嗯，分开确认。']),
'083':dict(question='如果拿选零食当个小游戏，只选第一眼顺眼的，你敢不敢试试？',states=[],
 memory=['选零食也能想半天。','选择多也麻烦。','下次想试个省心的玩法。','别把自己弄得太紧张。'],
 recent=['我想到一个随意点的选择办法。','什么办法？','先说愿不愿意，不用真的拿东西。','嗯，你说说规则。']),
'092':dict(question='现在店里有人负责收钱吗？',
 states=[('街角咖啡','是否营业','是'),('街角咖啡','店员是否在场','是'),('街角咖啡','收银负责人','一名店员正在收银台负责收款，姓名未登记')],
 memory=['我想把点单和收款弄清楚。','怕找错人？','是，谁负责什么要问明白。','别只凭站的位置猜。'],
 recent=['这回我想先确认谁负责收款。','不问饮品了？','饮品等会儿说，先说收银这件事。','嗯。']),
'132':dict(question='现在点单要排队吗，具体要等多久？',
 states=[('街角咖啡','是否营业','是'),('街角咖啡','排队情况','前面有两位顾客'),('街角咖啡','预计等待时间',None)],
 memory=['点饮品的时候我不想催人。','但想知道要等多久？','嗯，不清楚就说不清楚，别估一个数。','先弄清楚能知道的。'],
 recent=['我在想等会儿点单的事。','要赶时间吗？','先不催，我想知道排队情况和等待时间。','这两件可以分开问。']),
'139':dict(question='如果哪天你不想开口，你会怎么让我知道你在想什么？',states=[],
 memory=['有时不说话也不一定是不开心。','可能只是想安静一会儿。','嗯，我想知道怎么相处会舒服些。','可以慢慢商量。'],
 recent=['刚才聊到安静，我想到以后可能遇到的情况。','只是打个比方？','对，不是说你现在不愿意说话。','那你问吧。']),
'170':dict(question='你那个纸箱里面现在都放着什么？',
 states=[('白未晞的纸箱','箱内物品',None)],
 memory=['我想找时间整理一下自己的东西。','别把我的纸箱随便扔了。','不会，我会先问你。','嗯。'],
 recent=['说到收纳，我想起你的纸箱了。','怎么了？','我没有翻过，只是想问问里面的东西。','你问吧。']),
'177':dict(question='这家店今天有什么热饮可以买？',
 states=[('街角咖啡','是否营业','是'),('街角咖啡','具体饮品和甜点库存','热牛奶和热可可目前有供应，甜点库存未登记')],
 memory=['天凉的时候我会想喝点热的。','先看看有什么可选。','对，不用先猜店里卖什么。','到时候再确认。'],
 recent=['刚才说到热饮，我想确认今天的供应。','想现在挑？','嗯，只挑这家店实际有的。','那先说有哪些。']),
'180':dict(question='心愿现在还营业吗？我想边歇会儿边接着聊饮品名字。',
 states=[('心愿咖啡馆','是否营业','是')],
 memory=['我挺喜欢随便聊聊饮品名字的。','不一定马上定下来。','是，空闲的时候说说就行。','不用急着选。'],
 recent=['今天还想接着聊名字。','你想先歇一会儿？','嗯，先确认心愿现在是不是营业。','先把当前状态弄清楚。']),
'193':dict(question='这排货架上有原味薯片吗？',
 states=[('邻里便利店','目标商品是否在货架','原味薯片在当前食品货架上')],
 memory=['下次买零食我想少纠结一点。','先选一种试试？','嗯，先从简单的口味开始。','到时候看看有没有。'],
 recent=['今天我先不比较那么多口味了。','想选哪种？','先考虑原味薯片，只是不知道货架上有没有。','那就确认这一种。']),
'196':dict(question='今天买菜的付款到底有没有成功？',states=[('电视','电源状态','已关闭')],
 memory=['记账时我容易把不同的事情混到一起。','你想怎么分开？','付款单独确认，别靠别的事去猜。','嗯，账的事单独说。'],
 recent=['电视和晚饭先放一边。','想确认别的事？','对，今天买菜的付款我还拿不准。','你说。']),
'198':dict(question='有时候不再纠结该不该买，就算是一种休息了吧？',states=[],
 memory=['买东西也会让我有点累。','不是走路累？','是总想着值不值得买。','每样都比较确实费神。'],
 recent=['我想今天先不做那么多决定。','让脑子歇歇？','嗯，尤其是不停想该不该买。','不用每回都选最好的。']),
}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-root',type=Path,required=True);a=p.parse_args()
    a.output_root.mkdir(parents=True,exist_ok=False); (a.output_root/'plans').mkdir()
    originals=[json.loads(x) for x in (ROOT/'训练数据/mixed200_20260914/inputs/all_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    _,_,anchor=_load_baiweixi_render_context();previous=[]; report=[]
    for old in originals:
        if not old.get('quarantined'):continue
        spec=CASES[old['sample_id'].split('.')[-1]];seed=copy.deepcopy(old['scenario']['component_source'])
        sid=old['sample_id']+'.jointretry';seed.update(sample_id=sid,retry_of=old['sample_id']);seed['snapshot']['states']=[]
        for key in ('family_id','snapshot_id','relation_id','utterance_id'):seed['component_ids'][key]=sid+'.'+key
        plan={'intent':'保留原话题，以明确问题自然承接：'+spec['question'],'states':[{'subject':s,'predicate':p,'value':v,'visibility':'visible'} for s,p,v in spec['states']]}
        row=apply_plan(seed,plan);now=datetime.fromisoformat(row['snapshot']['time'])
        for kind,key,delta in [('memory_turns','memory',timedelta(days=2)),('recent_turns','recent',timedelta(minutes=4))]:
            for n,(turn,text) in enumerate(zip(row[kind],spec[key])):turn.update(text=text,source=f'{sid}.{kind}.{n}',time=(now-delta+timedelta(seconds=n*30)).isoformat())
        row['current_user']=spec['question'];row['relation']='旧对话引出原话题，近期对话铺垫当前问题；合成场景状态预先确定，未登记内容保持未知。'
        assert duplicate_reason(row,previous) is None;previous.append(row)
        scenario=convert(row);coverage=validate_final_messages(scenario,build_messages(scenario,anchor))
        persist(a.output_root/'plans'/f'{sid}.world.json',{'plan':plan,'authoring_method':'assistant_curated_synthetic_fixture_before_reply'})
        persist(a.output_root/'plans'/f'{sid}.json',{'component':row,'authoring_method':'assistant_curated_related_history_not_model_generated'})
        report.append({'sample_id':sid,'snapshot_sha256':freeze(row),'coverage':coverage})
    assert len(report)==17
    persist(a.output_root/'curated_input_preflight.json',{'total':17,'authoring_method':'assistant_curated_inputs; replies will be generated by local model','samples':report})
    print('17 checked input fixtures prepared')
if __name__=='__main__':main()
