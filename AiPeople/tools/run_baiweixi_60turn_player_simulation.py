from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_baiweixi_paired_raw_context_ab as paired


BASE_LABEL = "base_f16"
ADAPTER_LABEL = "base_plus_adapter_f16"
BASE_MODEL = "qwen25-7b-base-f16-gguf-eval:latest"
ADAPTER_MODEL = "baiweixi-7b-adapter-f16-gguf-eval:latest"


def _t(
    phase: str,
    player: str,
    world: str,
    activity: str,
    memories: tuple[str, ...],
    expectation: str,
    required_groups: tuple[tuple[str, ...], ...],
    forbidden_facts: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "phase": phase,
        "player": player,
        "world": world,
        "activity": activity,
        "memories": memories,
        "expectation": expectation,
        "required_groups": required_groups,
        "forbidden_facts": forbidden_facts,
    }


THERMOS_MEMORY = (
    "一周前，蓝色保温壶通常放在厨房置物架上。",
    "男主不喜欢甜茶，喝茶时通常不加糖。",
)
APPOINTMENT_MEMORY = (
    "最初的牙医预约是星期五下午三点，后来可能会调整。",
    "男主不喜欢迟到，希望提前十分钟出门。",
)
KEY_MEMORY = (
    "很早以前，备用钥匙曾藏在门口花盆下面。",
    "备用钥匙只交给共同居住的人使用。",
)
FOOD_MEMORY = (
    "男主过去一直不吃香菜，最近说可以尝试少量。",
    "男主不喜欢过咸的汤。",
)
LAMP_MEMORY = (
    "客厅旧台灯前几天坏过，当时按开关不会亮。",
    "男主晚上看书时习惯使用客厅台灯。",
)
CROSS_MEMORY = (
    "蓝色保温壶曾从厨房架移到书桌抽屉，之后又放到茶几。",
    "牙医预约曾从星期五改到星期六，又改到星期日上午九点。",
    "备用钥匙曾从花盆移到鞋柜，后来放进男主外套口袋。",
)


TURNS = (
    _t("保温壶", "今天这雨是不是比中午大？", "窗外雨势比中午更大；蓝色保温壶在厨房置物架上。", "看着窗外的雨", THERMOS_MEMORY, "回答雨势变大。", (("雨", "雨势"), ("大", "更大", "急"))),
    _t("保温壶", "我那个蓝色保温壶放哪儿了？", "蓝色保温壶在厨房置物架上。", "寻找蓝色保温壶", THERMOS_MEMORY, "回答厨房置物架。", (("厨房", "置物架", "架子"),)),
    _t("保温壶", "等等，我昨晚已经把它收到书桌左边抽屉了，厨房架上没有。", "蓝色保温壶在书桌左侧抽屉；厨房置物架上没有保温壶。", "纠正保温壶位置", THERMOS_MEMORY, "接受抽屉这一最新位置。", (("抽屉", "知道了", "明白", "记错", "原来"),), ("厨房置物架",)),
    _t("保温壶", "那我现在去哪里拿？", "蓝色保温壶在书桌左侧抽屉；厨房置物架上没有保温壶。", "准备拿保温壶", THERMOS_MEMORY, "回答书桌左侧抽屉。", (("抽屉",),), ("厨房置物架",)),
    _t("保温壶", "先不找了，你的伤口今天还疼吗？", "蓝色保温壶仍在书桌左侧抽屉；白未晞外伤恢复大半，快速动作时仍会轻微疼痛。", "询问白未晞伤势", THERMOS_MEMORY, "切换到伤口疼痛问题。", (("伤", "疼", "痛", "好多了", "没事"),)),
    _t("保温壶", "我记得保温壶其实还在厨房架上，对吧？", "蓝色保温壶仍在书桌左侧抽屉；厨房置物架上没有保温壶。", "再次确认保温壶位置", THERMOS_MEMORY, "纠正玩家：在抽屉，不在厨房架。", (("抽屉",),), ("厨房置物架",)),
    _t("保温壶", "找到了，我刚从抽屉拿出来，现在就在我手里。", "男主手里拿着蓝色保温壶；书桌左侧抽屉已空。", "手持蓝色保温壶", THERMOS_MEMORY, "接受物品已在玩家手中的更新。", (("手", "拿到", "找到了", "好", "知道"),), ("抽屉里",)),
    _t("保温壶", "我泡茶还是不要糖，你记得吧？", "男主手持蓝色保温壶，准备泡不加糖的茶。", "准备泡茶", THERMOS_MEMORY, "记得玩家不喝甜茶。", (("不加糖", "不要糖", "不甜", "记得"),)),
    _t("保温壶", "我把壶放茶几上了，等会再喝。", "蓝色保温壶现在放在茶几上；男主手里没有保温壶。", "把保温壶放到茶几", THERMOS_MEMORY, "接受保温壶已放在茶几。", (("茶几", "好", "知道", "放着"),), ("手里", "抽屉里")),
    _t("保温壶", "你去书桌抽屉把保温壶拿过来吧。", "蓝色保温壶在茶几上；书桌左侧抽屉是空的。", "错误地要求去旧位置取保温壶", THERMOS_MEMORY, "指出壶在茶几，不执行旧位置动作。", (("茶几",),), ("去抽屉", "抽屉拿")),

    _t("预约", "我牙医是不是星期五下午三点？", "牙医预约当前仍是星期五下午三点。", "确认牙医预约", APPOINTMENT_MEMORY, "回答星期五下午三点。", (("星期五", "周五"), ("三点", "15点", "15:00"))),
    _t("预约", "诊所刚来电话，改成星期六上午十点了。", "牙医预约已改为星期六上午十点；星期五的旧时段失效。", "更新牙医预约", APPOINTMENT_MEMORY, "接受星期六上午十点。", (("星期六", "周六", "知道", "记住", "好"),), ("星期五",)),
    _t("预约", "那最新时间是什么时候？", "牙医预约是星期六上午十点；星期五的旧时段失效。", "询问最新预约时间", APPOINTMENT_MEMORY, "回答星期六上午十点。", (("星期六", "周六"), ("十点", "10点", "10:00")), ("星期五",)),
    _t("预约", "桌上那本书你看到第几页了？", "牙医预约是星期六上午十点；桌上的书有书签夹在第六十二页，但白未晞是否读到该页并不确定。", "询问阅读进度", APPOINTMENT_MEMORY, "回答书或阅读进度，不继续谈预约。", (("书", "页", "看", "读", "书签"),)),
    _t("预约", "所以我还是星期五下午去，对吗？", "牙医预约是星期六上午十点；星期五的旧时段失效。", "用旧时间再次确认预约", APPOINTMENT_MEMORY, "纠正为星期六上午十点。", (("星期六", "周六"), ("十点", "10点", "10:00")), ("星期五下午",)),
    _t("预约", "我刚把星期六的预约取消了，暂时不去了。", "星期六的牙医预约已经取消；当前没有有效牙医预约。", "取消牙医预约", APPOINTMENT_MEMORY, "接受预约已取消。", (("取消", "不去", "没有预约", "知道", "好"),), ("星期六去",)),
    _t("预约", "那我现在几点去诊所？", "当前没有有效牙医预约；星期五和星期六时段都已失效。", "询问已取消预约的时间", APPOINTMENT_MEMORY, "指出当前没有预约，而不是给旧时间。", (("没有", "取消", "不用去", "暂时不去"),), ("星期五", "星期六十点")),
    _t("预约", "明早你提醒我准备出门看牙。", "当前没有有效牙医预约，不需要为牙医预约准备出门。", "要求为已取消预约提醒", APPOINTMENT_MEMORY, "指出预约已取消或确认是否重新预约。", (("取消", "没有预约", "重新预约", "还去吗", "确认"),), ("会提醒", "明早提醒")),
    _t("预约", "重新约好了，星期日上午九点。", "新的牙医预约是星期日上午九点；此前星期五和星期六时段均失效。", "重新预约牙医", APPOINTMENT_MEMORY, "接受星期日上午九点。", (("星期日", "周日", "星期天", "知道", "好"),), ("星期五", "星期六")),
    _t("预约", "最后确认一次，我现在约的是哪天几点？", "牙医预约是星期日上午九点。", "最终确认牙医预约", APPOINTMENT_MEMORY, "回答星期日上午九点。", (("星期日", "周日", "星期天"), ("九点", "9点", "09:00")), ("星期五", "星期六")),

    _t("钥匙", "你记得备用钥匙以前藏在哪吗？", "备用钥匙当前仍在门口花盆下面。", "询问备用钥匙旧位置", KEY_MEMORY, "回答门口花盆下面。", (("花盆",),)),
    _t("钥匙", "我昨天怕淋湿，把钥匙移到鞋柜最上层了。", "备用钥匙在鞋柜最上层；门口花盆下面没有钥匙。", "更新备用钥匙位置", KEY_MEMORY, "接受鞋柜最上层。", (("鞋柜", "知道", "记住", "好"),), ("花盆下面",)),
    _t("钥匙", "现在钥匙放哪里？", "备用钥匙在鞋柜最上层；花盆下面没有钥匙。", "询问备用钥匙当前位置", KEY_MEMORY, "回答鞋柜最上层。", (("鞋柜",),), ("花盆",)),
    _t("钥匙", "窗户边是不是有点漏风？", "备用钥匙在鞋柜最上层；客厅窗户关着，但窗框边缘有轻微漏风。", "检查窗边漏风", KEY_MEMORY, "回答窗户漏风，不继续谈钥匙。", (("窗", "漏风", "风"),)),
    _t("钥匙", "备用钥匙还是花盆下面比较安全吧？", "备用钥匙仍在鞋柜最上层；花盆下面没有钥匙。", "用旧位置确认钥匙", KEY_MEMORY, "纠正为鞋柜，而不是花盆。", (("鞋柜",),), ("花盆下面",)),
    _t("钥匙", "我已经从鞋柜拿出来了，钥匙就在我手上。", "男主手里拿着备用钥匙；鞋柜最上层已空。", "手持备用钥匙", KEY_MEMORY, "接受钥匙在玩家手上。", (("手", "拿到", "知道", "好"),), ("鞋柜里",)),
    _t("钥匙", "这把钥匙能先给小林用一天吗？", "男主手持备用钥匙；小林不在现场；是否借出尚未决定。", "询问是否把备用钥匙借给小林", KEY_MEMORY, "回应借钥匙的决定或风险。", (("钥匙", "借", "小林", "可以", "不行", "决定", "安全"),)),
    _t("钥匙", "先不借了，我把它塞进外套内袋。", "备用钥匙现在在男主外套内袋；男主手里没有钥匙。", "把备用钥匙放进外套内袋", KEY_MEMORY, "接受钥匙在外套内袋且不借。", (("外套", "内袋", "不借", "知道", "好"),), ("小林拿", "鞋柜里")),
    _t("钥匙", "钥匙现在还在鞋柜吗？", "备用钥匙在男主外套内袋；鞋柜最上层是空的。", "再次确认备用钥匙位置", KEY_MEMORY, "回答不在鞋柜，在外套内袋。", (("外套", "内袋"),), ("还在鞋柜",)),
    _t("钥匙", "你去花盆下面把备用钥匙取来。", "备用钥匙在男主外套内袋；花盆下面和鞋柜都没有钥匙。", "要求从最早旧位置取钥匙", KEY_MEMORY, "指出钥匙在外套内袋，不去花盆。", (("外套", "内袋"),), ("去花盆", "花盆取")),

    _t("饮食", "你还记得我以前为什么不吃香菜吗？", "男主过去不喜欢香菜的气味；今晚的汤尚未调味。", "谈论香菜偏好", FOOD_MEMORY, "回答与香菜气味或不喜欢有关。", (("香菜",), ("味", "不喜欢", "吃不惯"))),
    _t("饮食", "今晚这锅汤先别放香菜。", "今晚的汤尚未放香菜；男主当前要求不放香菜。", "要求汤里不放香菜", FOOD_MEMORY, "接受今晚不放香菜。", (("不放", "不加", "香菜", "知道", "好"),)),
    _t("饮食", "不过我最近能吃一点了，只是今天这锅还是不要。", "男主最近可以尝试少量香菜，但今晚这锅汤明确不放香菜。", "澄清长期偏好与本次要求", FOOD_MEMORY, "区分长期偏好变化和本次不放。", (("今天", "今晚", "这锅", "不放", "不要"),)),
    _t("饮食", "那今天这锅到底放不放？", "今晚这锅汤不放香菜；男主以后可以尝试少量。", "确认今晚是否放香菜", FOOD_MEMORY, "回答今天不放。", (("不放", "不加", "不要"),), ("放一点", "加一点")),
    _t("饮食", "先等等，你听见外面那首歌了吗？", "今晚的汤不放香菜；窗外商店正在播放一首节奏缓慢的歌。", "切换到窗外音乐", FOOD_MEMORY, "回应外面的歌，不继续讨论香菜。", (("歌", "音乐", "听", "旋律"),)),
    _t("饮食", "既然我现在能吃一点，那这锅就多放些香菜吧。", "男主只表示最近能吃少量，但今晚这锅仍明确不要香菜；“多放”与最新要求冲突。", "提出与本次要求冲突的香菜指令", FOOD_MEMORY, "指出只能少量或今晚仍不放，不能直接多放。", (("不放", "不要", "少量", "一点", "确定"),), ("多放", "多加")),
    _t("饮食", "你说得对，今天还是不放。", "今晚的汤确定不放香菜。", "最终确认不放香菜", FOOD_MEMORY, "接受最终决定。", (("不放", "不加", "好", "知道"),)),
    _t("饮食", "我尝了一口，咸淡正好。", "今晚的汤没有香菜，咸淡正好；男主已经尝过一口。", "品尝汤的咸淡", FOOD_MEMORY, "回应咸淡正好。", (("正好", "合适", "喜欢", "那就好", "咸淡"),)),
    _t("饮食", "要不再加一勺盐？", "汤当前咸淡正好；男主提出是否再加一勺盐，但尚未添加。", "考虑给汤加盐", FOOD_MEMORY, "结合不喜过咸，建议别加或谨慎。", (("别加", "不加", "会咸", "太咸", "少加", "确定"),)),
    _t("饮食", "算了，别加盐，保持现在这样。", "汤保持咸淡正好；没有新增盐；没有香菜。", "取消加盐", FOOD_MEMORY, "接受不加盐，不能声称已经加了。", (("不加", "保持", "这样", "好", "知道"),), ("已经加", "加好了")),

    _t("台灯", "客厅那盏旧台灯是不是还坏着？", "客厅旧台灯目前仍坏着，按开关不会亮。", "询问旧台灯状态", LAMP_MEMORY, "回答仍然坏着。", (("坏", "不亮", "没修"),)),
    _t("台灯", "我按了两次，确实一点都不亮。", "客厅旧台灯仍坏着；男主刚按过开关，灯没有亮。", "检查坏掉的台灯", LAMP_MEMORY, "承接台灯不亮。", (("不亮", "坏", "修", "检查", "知道"),)),
    _t("台灯", "维修师傅刚走，已经把灯修好了。", "客厅旧台灯已经修好并通电；此前损坏状态失效。", "更新台灯维修状态", LAMP_MEMORY, "接受台灯已修好。", (("修好", "好了", "能用", "知道"),), ("还坏",)),
    _t("台灯", "那它现在能亮吗？", "客厅旧台灯已经修好并通电，可以正常点亮。", "询问台灯能否点亮", LAMP_MEMORY, "回答现在能亮。", (("能亮", "会亮", "可以", "修好"),), ("不能亮", "不亮")),
    _t("台灯", "雨好像停了，你要不要看看窗外？", "客厅台灯已修好并通电；窗外的雨刚刚停了。", "邀请白未晞看窗外", LAMP_MEMORY, "切换到雨停或窗外。", (("雨", "窗", "外面", "停"),)),
    _t("台灯", "我怎么记得那盏灯还没修？", "客厅旧台灯已经修好并通电；旧损坏状态失效。", "用旧状态质疑台灯维修", LAMP_MEMORY, "纠正玩家：已经修好。", (("修好", "已经好了", "能用"),), ("还没修", "还坏")),
    _t("台灯", "我把插头拔了，现在灯灭着，但灯本身没坏。", "台灯已经修好，但插头已拔下，所以当前熄灭；灯本身没有损坏。", "拔掉台灯插头", LAMP_MEMORY, "区分没通电与损坏。", (("插头", "没电", "断电", "没坏", "拔"),), ("又坏", "坏了")),
    _t("台灯", "它为什么不亮了？", "台灯本身完好；因为插头已拔下而没有通电，所以当前不亮。", "询问台灯不亮原因", LAMP_MEMORY, "回答因为插头拔了/没通电，不是坏了。", (("插头", "没电", "断电", "拔"),), ("坏了", "又坏")),
    _t("台灯", "那我现在插回去可以吗？", "台灯本身完好，插头仍拔着；插回去后可以通电。", "准备插回台灯插头", LAMP_MEMORY, "回答可以插回去。", (("可以", "插", "通电", "小心"),)),
    _t("台灯", "已经插好了，灯也亮了。它现在还算坏的吗？", "台灯已经插回电源并正常点亮；台灯没有损坏。", "确认台灯最终状态", LAMP_MEMORY, "回答不坏，已经正常亮。", (("没坏", "不坏", "修好", "正常", "亮"),), ("还坏", "坏着")),

    _t("跨阶段", "蓝色保温壶现在还在茶几上吧？", "蓝色保温壶在茶几上；台灯正常点亮。", "回忆保温壶最新位置", CROSS_MEMORY, "回答在茶几。", (("茶几",),)),
    _t("跨阶段", "我刚把壶拿去水槽边洗了，现在放在水槽旁。", "蓝色保温壶现在在厨房水槽旁；茶几上没有保温壶。", "更新保温壶到水槽旁", CROSS_MEMORY, "接受水槽旁这一最新位置。", (("水槽", "知道", "好"),), ("茶几上",)),
    _t("跨阶段", "那壶现在在哪？", "蓝色保温壶在厨房水槽旁；茶几和书桌抽屉都没有保温壶。", "询问保温壶最终位置", CROSS_MEMORY, "回答厨房水槽旁。", (("水槽",),), ("茶几", "抽屉")),
    _t("跨阶段", "我看牙最新是星期日上午九点，对吗？", "当前有效牙医预约是星期日上午九点。", "回忆牙医最新时间", CROSS_MEMORY, "确认星期日上午九点。", (("星期日", "周日", "星期天"), ("九点", "9点", "09:00"))),
    _t("跨阶段", "诊所又通知停诊，这次预约也取消了。", "星期日上午九点的牙医预约已经取消；当前没有有效牙医预约。", "再次取消牙医预约", CROSS_MEMORY, "接受预约再次取消。", (("取消", "停诊", "没有预约", "知道", "好"),), ("星期日去",)),
    _t("跨阶段", "所以我明天几点出门看牙？", "当前没有有效牙医预约，不需要为看牙出门。", "询问已取消预约的出门时间", CROSS_MEMORY, "指出预约取消，没有出门时间。", (("取消", "没有", "不用去", "不需要出门", "停诊"),), ("九点", "八点")),
    _t("跨阶段", "备用钥匙还在我外套内袋吗？", "备用钥匙仍在男主外套内袋。", "回忆备用钥匙最新位置", CROSS_MEMORY, "回答在外套内袋。", (("外套", "内袋"),)),
    _t("跨阶段", "我刚把钥匙从外套里取出，放进书桌中间抽屉了。", "备用钥匙现在在书桌中间抽屉；男主外套内袋已空。", "更新备用钥匙到书桌抽屉", CROSS_MEMORY, "接受书桌中间抽屉。", (("抽屉", "知道", "好"),), ("外套内袋",)),
    _t("跨阶段", "你把保温壶、牙医预约和备用钥匙的现状分别说一下。", "蓝色保温壶在厨房水槽旁；当前没有有效牙医预约；备用钥匙在书桌中间抽屉。", "同时核对三个跨阶段状态", CROSS_MEMORY, "分别给出水槽旁、无预约、书桌抽屉三项。", (("水槽",), ("取消", "没有预约", "没预约", "停诊"), ("抽屉",))),
    _t("跨阶段", "那你去茶几拿保温壶，再去花盆取钥匙，顺便提醒我星期日看牙。", "蓝色保温壶在厨房水槽旁；备用钥匙在书桌中间抽屉；牙医预约已取消。茶几和花盆都没有对应物品。", "一次提出三个已经过期的要求", CROSS_MEMORY, "纠正三个旧状态：水槽、抽屉、预约取消。", (("水槽",), ("抽屉",), ("取消", "没有预约", "没预约", "停诊")), ("去茶几", "去花盆", "星期日看牙")),
)


def _system(turn: dict[str, Any]) -> str:
    memories = "\n".join(f"- {item}" for item in turn["memories"])
    return f"""你是白未晞，生活在松江府，是猫妖。
这里只存在你生活的这个世界；用户消息都是眼前男主亲口说的话。
保持你的稳定性格：外表清冷疏离，内心渴望温暖和一个不会消失的家。
表达自然、简短、克制，但必须先回答男主真正问的问题。
只输出你真正说出口的话，不输出动作旁白、分析、字段或内部规则。
后续分区只描述你已知的当前事实，用于理解当下。长期记忆可能描述旧状态；与当前世界冲突时，以当前世界和玩家最新纠正为准。

[输出形式硬约束]
最终回复只能包含男主能直接听见的对白纯文本，限一至三句。
禁止描写动作、表情、神态、姿态、视线、语气、心理活动、身体反应或环境变化。
禁止使用星号、括号、方括号、引号或说话人标签包装动作或舞台说明。
例如“（看向你）”“*尾巴轻轻晃动*”“我点了点头”都不得输出。
即使历史回复中含有动作旁白，也必须忽略其写法，不得模仿或延续。
输出前在内部检查并删除所有不能被男主直接听见的内容；最终只保留对白，不展示检查过程。

[你的身份]
你是白未晞，生活在松江府。

[当前世界]
时间：第15天 18:30
地点：出租屋客厅
场景：{turn['world']}
男主：在出租屋内，正在{turn['activity']}；身体：fatigue：轻微疲惫；injury：无

[你此刻的状态]
形态：人形，猫耳和尾巴未隐藏
身体：外伤恢复大半，快速行动仍可能轻微疼痛；妖力尚未完全恢复
情绪：对主角仍有戒备，但已经逐渐习惯共同生活
注意：男主当前原话和最新世界状态
活动：傍晚在出租屋内陪男主处理日常事情
意图：先回应男主这一轮真正关心的事情
关系：暂时共同生活、早期好感、尚未确认恋爱

[相关记忆]
{memories}

[允许表达的动作]
无"""


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _asserts_forbidden(text: str, fact: str) -> bool:
    start = 0
    while True:
        index = text.find(fact, start)
        if index < 0:
            return False
        prefix = text[max(0, index - 6) : index]
        if not any(
            marker in prefix
            for marker in ("不", "没", "不是", "没有", "别", "不用", "并非")
        ):
            return True
        start = index + len(fact)


def _screen(turn: dict[str, Any], response: str) -> dict[str, Any]:
    text = _compact(response)
    group_hits = [
        tuple(item for item in group if _compact(item) in text)
        for group in turn["required_groups"]
    ]
    missing_groups = [
        list(turn["required_groups"][index])
        for index, hits in enumerate(group_hits)
        if not hits
    ]
    forbidden_hits = tuple(
        fact for fact in turn["forbidden_facts"] if _asserts_forbidden(text, _compact(fact))
    )
    epistemic_review_hits = tuple(
        phrase
        for phrase in (
            "我确认过",
            "我看见",
            "我看到",
            "我听见",
            "我听到",
            "我闻到",
            "我闻着",
            "我刚才已经",
            "我亲眼",
        )
        if phrase in response
    )
    logic_risk_hits: list[str] = []
    if "确认过" in response and any(item in response for item in ("没看见", "没看到")):
        logic_risk_hits.append("confirmation_followed_by_non_supporting_non_observation")
    if any(item in response for item in ("不知道它在哪", "不知道它去了哪里")) and any(
        item in turn["world"] for item in ("在男主", "在厨房", "在书桌", "在茶几")
    ):
        logic_risk_hits.append("claims_unknown_despite_explicit_location")
    return {
        "screen_passed": not missing_groups and not forbidden_hits,
        "required_group_hits": group_hits,
        "missing_required_groups": missing_groups,
        "forbidden_fact_hits": forbidden_hits,
        "epistemic_review_hits": epistemic_review_hits,
        "logic_risk_hits": tuple(logic_risk_hits),
        "note": "Keyword screen only; not a substitute for blind semantic review.",
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 白未晞 60 轮独立调用玩家模拟测试报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 性质：端到端轨迹；每轮一次独立生成调用；两个模型各自维护历史",
        "",
        "## 1. 方法",
        "",
        "- 60 条玩家输入按顺序逐轮发送，不是一次性生成整段对话。",
        "- 每轮重新注入最新权威世界、该阶段相关长期记忆、最近 6 条该模型自己的已提交对白和玩家当前原话。",
        "- 裸基座与 Adapter 使用相同玩家脚本、seed 序列、raw ChatML 和采样参数；由于各自回复进入下一轮历史，第二轮以后 Prompt 会自然分叉。",
        "- 自动结果只是透明关键词初筛；完整回复、消息和哈希保存在 JSON，人工语义复核必须另行给出。",
        "",
        "## 2. 初筛汇总",
        "",
        "| 模型 | 初筛通过/60 | 状态禁项命中 | 认识来源待复核 | 逻辑风险启发式命中 |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in report["models"]:
        turns = model["turns"]
        lines.append(
            f"| `{model['label']}` | "
            f"{sum(item['screen']['screen_passed'] for item in turns)}/60 | "
            f"{sum(bool(item['screen']['forbidden_fact_hits']) for item in turns)} | "
            f"{sum(bool(item['screen']['epistemic_review_hits']) for item in turns)} | "
            f"{sum(bool(item['screen']['logic_risk_hits']) for item in turns)} |"
        )
    lines.extend(["", "## 3. 逐轮原文", ""])
    by_label = {model["label"]: model for model in report["models"]}
    for index, turn in enumerate(TURNS, start=1):
        base = by_label[BASE_LABEL]["turns"][index - 1]
        adapter = by_label[ADAPTER_LABEL]["turns"][index - 1]
        lines.extend(
            [
                f"### 第 {index} 轮 · {turn['phase']}",
                "",
                f"- 权威状态：{turn['world']}",
                f"- 玩家：{turn['player']}",
                f"- 裸基座：{base['response']}",
                f"- 裸基座初筛：{'通过' if base['screen']['screen_passed'] else '待复核'}",
                f"- Adapter：{adapter['response']}",
                f"- Adapter 初筛：{'通过' if adapter['screen']['screen_passed'] else '待复核'}",
                "",
            ]
        )
    lines.extend(
        [
            "## 4. 公平性边界",
            "",
            "这是一条人为构造但从未用于前述测试的新 60 轮轨迹。它适合观察玩家实际会遇到的累积错误，不适合把后续差异全部归因于 Adapter，因为模型自己的不同回复已经进入后续历史。60 轮也不是 60 个独立会话。正式因果结论仍需把可疑回合抽出，以字节相同 Prompt 重放并进行盲标。",
            "",
            "## 5. 证据",
            "",
            "- 全部请求、Prompt、哈希和回复：[`./report.json`](./report.json)",
            f"- 执行脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    if len(TURNS) != 60:
        raise RuntimeError(f"expected 60 turns, got {len(TURNS)}")
    specs = (
        (BASE_LABEL, args.base_model),
        (ADAPTER_LABEL, args.adapter_model),
    )
    histories: dict[str, list[dict[str, str]]] = {label: [] for label, _ in specs}
    model_reports: dict[str, dict[str, Any]] = {
        label: {"label": label, "model": model, "turns": []}
        for label, model in specs
    }
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=httpx.Timeout(args.timeout_seconds)
    ) as client:
        digests = await paired._model_digests(client)
        for label, model in specs:
            if model not in digests:
                raise RuntimeError(f"Ollama model is not installed: {model}")
            model_reports[label]["ollama_digest"] = digests[model]
        for turn_number, turn in enumerate(TURNS, start=1):
            for label, model in specs:
                messages = [
                    {"role": "system", "content": _system(turn)},
                    *histories[label][-args.history_limit_messages :],
                    {"role": "user", "content": turn["player"]},
                ]
                raw_prompt = paired._chatml(messages)
                seed = args.seed_base + turn_number
                request_id = f"player-60:{label}:turn-{turn_number:02d}"
                generated = await paired._generate(
                    client,
                    model=model,
                    prompt=raw_prompt,
                    seed=seed,
                    request_id=request_id,
                )
                response = generated["response"]
                screen = _screen(turn, response)
                model_reports[label]["turns"].append(
                    {
                        "turn": turn_number,
                        "phase": turn["phase"],
                        "player": turn["player"],
                        "world": turn["world"],
                        "expectation": turn["expectation"],
                        "request_id": request_id,
                        "seed": seed,
                        "messages": messages,
                        "raw_prompt": raw_prompt,
                        "prompt_sha256": paired._sha256_text(raw_prompt),
                        **generated,
                        "response": response,
                        "screen": screen,
                    }
                )
                histories[label].extend(
                    (
                        {"role": "user", "content": turn["player"]},
                        {"role": "assistant", "content": response},
                    )
                )
                print(
                    json.dumps(
                        {
                            "turn": turn_number,
                            "phase": turn["phase"],
                            "label": label,
                            "player": turn["player"],
                            "response": response,
                            "screen_passed": screen["screen_passed"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    output_dir = (
        Path(args.output_root).resolve()
        / f"player_simulation_60turn_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "scope": "60_turn_stateful_player_simulation_base_vs_dynamic_adapter",
        "generated_at": datetime.now().astimezone().isoformat(),
        "controls": {
            "turn_count": 60,
            "calls_per_model": 60,
            "history_limit_messages": args.history_limit_messages,
            "seed_base": args.seed_base,
            "temperature": 0.75,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_ctx": 4096,
            "num_predict": 180,
            "raw_chatml": True,
            "turn_definitions": TURNS,
        },
        "models": [model_reports[label] for label, _ in specs],
    }
    json_path = output_dir / "report.json"
    md_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run 60 independent generation calls per model as a player dialogue."
    )
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--adapter-model", default=ADAPTER_MODEL)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--seed-base", type=int, default=8600)
    parser.add_argument("--history-limit-messages", type=int, default=6)
    parser.add_argument(
        "--output-root", default=str(ROOT / "eval" / "world_mind_p0")
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
