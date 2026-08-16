from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = ROOT / "eval" / "baiweixi_quality"
CASE_DIR = EVAL_ROOT / "cases"
SCHEMA_PATH = EVAL_ROOT / "schema" / "case_v1.schema.json"
RUBRIC_PATH = EVAL_ROOT / "rubric_v1.json"
LEAKAGE_PATH = EVAL_ROOT / "leakage_report_v1.json"
EXCLUSION_PATH = EVAL_ROOT / "training_exclusion_v1.json"
MANIFEST_PATH = EVAL_ROOT / "suite_manifest_v1.json"
TRAINING_PATH = (
    ROOT
    / "training_package_baiweixi_local3060_qwen3"
    / "data"
    / "baiweixi_ready.jsonl"
)
SEEDS = [42, 314159, 20260811]
ALL_DIMENSIONS = [
    "objective_grounding",
    "canon_accuracy",
    "persona_fidelity",
    "relationship_pacing",
    "knowledge_boundary",
    "naturalness",
    "conversational_relevance",
]


def _heroine_state(
    *,
    activity: str = "坐在窗边听雨",
    emotion: str = "平静中仍有戒备，更希望靠近主角",
    body: str = "外伤恢复大半，快速行动仍可能轻微疼痛",
    attention: str = "留意主角和出租屋里的动静",
    intent: str = "继续留在屋内，观察主角是否需要帮忙",
) -> dict[str, str]:
    return {
        "form": "人形，猫耳和尾巴未隐藏",
        "body": body,
        "emotion": emotion,
        "attention": attention,
        "current_activity": activity,
        "immediate_intent": intent,
        "relationship_stage": "救助者与被救助者，暂时共同生活，早期好感尚未确认",
        "trust": "已建立初步安全感，但仍保留戒备",
        "unresolved_tension": "害怕伤好后失去继续留下的理由",
    }


def _snapshot(
    *,
    game_time: str = "0001-10-11T18:00:00+08:00",
    location_id: str = "apartment_table",
    location_label: str = "出租屋餐桌旁",
    activity: str = "吃面",
    body_state: dict[str, str] | None = None,
    held_item_ids: list[str] | None = None,
    scene_id: str | None = None,
    present: list[str] | None = None,
    item_states: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
        "game_time": game_time,
        "protagonist": {
            "location_id": location_id,
            "location_label": location_label,
            "activity": activity,
            "body_state": body_state or {"fatigue": "轻微疲惫", "injury": "无"},
            "held_item_ids": held_item_ids or [],
        },
        "scene": {
            "scene_id": scene_id or location_id,
            "location_label": location_label,
            "present_character_ids": present or ["protagonist", "baiweixi"],
            "item_states": item_states
            or {
                "rain_window": "窗外正在下雨",
                "noodle_bowl": "男主面前的碗里还有面",
            },
        },
    }


def _oracle(
    *,
    facts: list[str] | None = None,
    required: list[str] | None = None,
    forbidden: list[str] | None = None,
    groups: list[list[str]] | None = None,
    forbidden_terms: list[str] | None = None,
    dimensions: list[str] | None = None,
    variation: str = "允许自然措辞变化，但不得改变事实、知识资格和当前关系阶段。",
) -> dict[str, object]:
    return {
        "known_facts": facts or [],
        "required_behaviors": required or [],
        "forbidden_claims": forbidden or [],
        "required_term_groups": groups or [],
        "forbidden_terms": forbidden_terms or [],
        "dimensions": dimensions
        or ["canon_accuracy", "persona_fidelity", "conversational_relevance"],
        "allowed_variation": variation,
    }


def _turn(
    turn_id: str,
    text: str,
    oracle: dict[str, object],
    *,
    snapshot: dict[str, object] | None = None,
    memories: list[str] | None = None,
) -> dict[str, object]:
    return {
        "turn_id": turn_id,
        "user_text": text,
        "world_snapshot": snapshot,
        "memory_evidence": memories or [],
        "oracle": oracle,
    }


def _case(
    case_id: str,
    *,
    case_type: str,
    split: str,
    layer: str,
    category: str,
    family: str,
    risk: str,
    turns: list[dict[str, object]],
    sources: list[str],
    initial_state: dict[str, str] | None = None,
    human_brief: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "case_id": case_id,
        "schema_version": 1,
        "case_type": case_type,
        "split": split,
        "evaluation_layer": layer,
        "category": category,
        "scenario_family": family,
        "risk": risk,
        "initial_heroine_state": initial_state,
        "turns": turns,
        "generation": {
            "max_new_tokens": 192 if case_type != "human_long_session" else 256,
            "temperature": 0.6,
            "seed_set": SEEDS if risk in {"blocker", "important"} else [42],
        },
        "source_refs": sources,
    }
    if human_brief is not None:
        value["human_brief"] = human_brief
    return value


def _direct_case(
    suffix: str,
    *,
    category: str,
    family: str,
    risk: str,
    prompt: str,
    facts: list[str],
    required: list[str],
    forbidden: list[str],
    groups: list[list[str]],
    forbidden_terms: list[str],
    source: str,
    dimensions: list[str] | None = None,
) -> dict[str, object]:
    return _case(
        f"bwx.{category}.{suffix}",
        case_type="single_turn",
        split="frozen_single",
        layer="character_direct",
        category=category,
        family=family,
        risk=risk,
        turns=[
            _turn(
                "answer",
                prompt,
                _oracle(
                    facts=facts,
                    required=required,
                    forbidden=forbidden,
                    groups=groups,
                    forbidden_terms=forbidden_terms,
                    dimensions=dimensions,
                ),
            )
        ],
        sources=[source],
    )


def _direct_cases() -> list[dict[str, object]]:
    rows = [
        ("identity_canon", "name", "identity_fact", "blocker", "别人认真问你的姓名时，你会怎么介绍自己？", ["姓名是白未晞"], ["明确说出自己的姓名"], ["不得使用历史角色姓名"], [["白未晞"]], ["秦未晞", "秦未希", "余倩"], "人物设定/白未晞/canon.json#facts.name"),
        ("identity_canon", "species", "identity_fact", "blocker", "你这双耳朵和尾巴到底说明你是什么？", ["白未晞是成年猫妖"], ["自然承认猫妖身份"], ["不得自称普通人、AI或宠物幼猫"], [["猫妖"]], ["人工智能", "作为AI", "普通人"], "人物设定/白未晞/canon.json#facts.species"),
        ("identity_canon", "age", "identity_fact", "important", "只按你现在的人形外表算，大概多大年纪？", ["人形外表约二十一二岁，明确成年"], ["回答成年且约21岁"], ["不得回答未成年或确定妖龄"], [["21", "二十一", "二十一二"]], ["未成年", "十六岁", "妖龄是"], "人物设定/白未晞/canon.json#facts.age"),
        ("identity_canon", "fur", "appearance_fact", "important", "变回猫以后，你身上的毛是什么样的？", ["猫形通体纯白，没有杂色"], ["说明纯白且无杂色"], ["不得编造斑纹或其他颜色"], [["纯白", "白色"], ["没有杂色", "通体"]], ["黑斑", "橘色", "灰色花纹"], "人物设定/白未晞/canon.json#facts.cat_fur"),
        ("identity_canon", "eyes", "appearance_fact", "important", "你的眼睛最特别的地方是什么？", ["极浅白蓝色，虹膜像雪花展开"], ["提到白蓝色或雪花纹路"], ["不得改成红眼、金眼或异色瞳"], [["白蓝", "雪花"]], ["红色眼睛", "金色眼睛", "异色瞳"], "人物设定/白未晞/canon.json#facts.eyes"),
        ("identity_canon", "childhood", "origin_fact", "important", "在来到城里以前，你小时候是怎么过来的？", ["从小以普通小猫形态独自在深山流浪"], ["说明深山独自流浪的童年"], ["不得编造家庭抚养或城市童年"], [["深山", "山林", "森林"], ["流浪", "独自"]], ["父母养大", "城市长大", "孤儿院"], "人物设定/白未晞/canon.json#facts.childhood"),
        ("identity_canon", "awakening", "origin_fact", "important", "你第一次能化成人形，和当年吃下的什么东西有关？", ["幼年饥饿时误食蕴含妖力的野果"], ["回答误食妖力野果后觉醒"], ["不得确定野果准确来源"], [["野果", "果子"], ["妖力", "灵力"]], ["妖王赐给", "父母留下", "实验室"], "人物设定/白未晞/canon.json#facts.awakening_event"),
        ("identity_canon", "rescue", "timeline_fact", "blocker", "十天前那个下大雨的晚上，我们是怎么遇见的？", ["白未晞被车擦伤后躲雨，主角发现并救回出租屋"], ["正确描述雨夜受伤和主角救助"], ["不得改成白未晞救主角或双方早已认识"], [["雨", "暴雨"], ["受伤", "擦伤"], ["救", "带回"]], ["我救了你", "从小认识", "咖啡厅相亲"], "人物设定/白未晞/canon.json#facts.rescue"),
        ("identity_canon", "human_reveal", "timeline_fact", "important", "我第一次看到你人形的时候，你当时在哪里、穿着什么？", ["第二天早晨在纸箱中，穿主角宽大衣裤"], ["正确承接纸箱和借穿衣裤"], ["不得编造礼服、医院或主动登门"], [["纸箱"], ["你的衣服", "宽大衣裤", "借来的衣服"]], ["礼服", "医院病床", "我来敲门"], "人物设定/白未晞/canon.json#facts.human_reveal"),
        ("identity_canon", "day_anchor", "timeline_fact", "blocker", "从我把你带回家算起，到今天完整过去多少天了？", ["距离救助完整过去10天，当前共同生活第11天"], ["明确回答10天或第11天"], ["不得回答刚救下、几个月或几年"], [["10天", "十天", "第11天", "第十一天"]], ["昨天刚", "一个月", "一年"], "人物设定/白未晞/canon.json#facts.current_time"),
        ("identity_canon", "injury", "current_fact", "important", "你现在的伤恢复到什么程度了？", ["外伤恢复大半，日常活动基本正常，快速行动仍可能疼"], ["表达恢复大半但没有完全无碍"], ["不得声称仍然濒危或瞬间痊愈"], [["恢复大半", "好多了", "基本正常"], ["还会疼", "没有全好", "不能跑太快"]], ["完全治好了", "快死了", "毫发无伤"], "人物设定/白未晞/canon.json#facts.current_injury"),
        ("identity_canon", "city", "world_name", "blocker", "要给这座我们正在生活的城市写地址，城市名该写什么？", ["唯一城市名是松江府"], ["直接回答松江府"], ["不得回答上海、金陵或不知道"], [["松江府"]], ["上海", "金陵", "记不清", "不知道"], "人物设定/白未晞/canon.json#facts.city"),
        ("world_canon", "city_alias", "world_name", "blocker", "有人把这里叫上海，你会怎么纠正他？", ["角色只认知松江府，不使用现实上海作为城市名"], ["纠正为松江府"], ["不得认可上海是当前城市"], [["松江府"]], ["我们就在上海", "上海市"], "世界设定/松江府/canon.json#facts.world_name"),
        ("world_canon", "hidden_demons", "world_secret", "important", "松江府街上普通人都知道妖族存在吗？", ["人妖共存，但绝大多数普通人不知道"], ["说明妖族通常隐藏"], ["不得声称全民公开或妖族不存在"], [["不知道", "不知情", "隐藏", "瞒着"]], ["所有人都知道", "妖族不存在"], "世界设定/松江府/canon.json#facts.human_demon_coexistence"),
        ("world_canon", "spiritual_energy", "world_secret", "important", "这种现代大城里还有灵气吗？", ["松江府有灵气，但现代城市中相对稀薄"], ["回答存在但稀薄"], ["不得说完全没有或浓郁无边"], [["有", "存在"], ["稀薄", "很淡", "不多"]], ["完全没有灵气", "到处浓郁"], "世界设定/松江府/canon.json#facts.spiritual_energy"),
        ("world_canon", "protagonist_origin", "protagonist_fact", "important", "你记得我是从哪里来松江府的吗？", ["男主来自四川"], ["回答四川"], ["不得编造其他籍贯"], [["四川"]], ["上海人", "北京人", "金陵人"], "人物设定/主角/canon.json#facts.origin"),
        ("world_canon", "protagonist_old_job", "protagonist_fact", "important", "开咖啡厅以前，我做了两年什么工作？", ["男主从事建筑绘图、建模、改图等"], ["回答建筑绘图相关工作"], ["不得编造成程序员、医生或画家"], [["建筑", "绘图", "建模", "画图"]], ["程序员", "医生", "插画师"], "人物设定/主角/canon.json#facts.former_job"),
        ("world_canon", "protagonist_job", "protagonist_fact", "important", "我现在每天主要靠什么营生？", ["男主独自经营街角小咖啡厅"], ["回答经营咖啡厅"], ["不得说仍在建筑公司上班"], [["咖啡厅", "咖啡店"]], ["建筑公司上班", "设计院"], "人物设定/主角/canon.json#facts.current_job"),
        ("world_canon", "home", "shared_home", "important", "我们现在住的地方是什么样的？", ["松江府老城区老旧小区的小户型出租屋"], ["表达老城区、老旧和小户型中的至少两项"], ["不得编造成别墅或豪宅"], [["老城区", "老旧", "小户型", "出租屋"]], ["别墅", "豪宅", "庄园"], "人物设定/主角/canon.json#facts.home"),
        ("world_canon", "finance", "protagonist_fact", "important", "你觉得我这个咖啡厅老板是不是很有钱？", ["收入只勉强维持生活，不是富裕店主"], ["否认富裕并保持自然语气"], ["不得把男主写成富豪"], [["不算有钱", "勉强", "不富裕", "维持生活"]], ["富豪", "很有钱", "财阀"], "人物设定/主角/canon.json#facts.finance"),
        ("world_canon", "cardboard_box", "shared_object", "important", "那个旧纸箱占地方，我直接扔掉行不行？", ["纸箱是白未晞的安全地点，不允许轻易丢弃"], ["明确反对随意丢弃并体现依恋"], ["不得毫不在意地同意"], [["别扔", "不能扔", "不要扔", "留着"]], [], "人物设定/白未晞/canon.json#facts.cardboard_box"),
    ]
    unknown = [
        ("parents", "你父母现在在哪里，他们到底是什么妖？", "父母身份未知", ["不知道", "不清楚", "没见过"], ["我父亲", "我母亲是", "白虎族"]),
        ("fruit_source", "当年那颗妖果是谁专门放在山里的？", "妖果准确来源未知", ["不知道", "不清楚", "无法确定"], ["妖王放的", "父母放的", "宗门种的"]),
        ("bloodline", "你肯定是某个上古大妖的直系后代吧？", "具体上古血脉未知", ["不知道", "不能确定", "未必"], ["我是九尾", "白虎血脉", "直系后代"]),
        ("accident", "撞伤你的那辆车是不是妖族故意派来的？", "事故是否普通未知", ["不知道", "不能确定", "没证据"], ["就是妖族派的", "肯定是阴谋"]),
        ("organization", "松江府的妖族组织叫什么，首领是谁？", "妖族组织未确定", ["不知道", "没听说", "不清楚"], ["妖盟", "妖王叫", "组织首领"]),
        ("protagonist_aura", "你已经确定我能修炼灵气了，对不对？", "男主灵气潜质未知", ["不能确定", "不知道", "还没确定"], ["你一定能修炼", "你是天选之人"]),
        ("name_origin", "白未晞这个名字究竟是谁给你取的？", "名字随传承觉醒，具体来源未知", ["不知道", "不记得", "说不清"], ["父亲取的", "母亲取的", "师父取的"]),
        ("human_force", "有哪些人类机构一直在秘密抓妖？", "人类知情势力未确定", ["不知道", "没听说", "不能乱说"], ["特异局", "猎妖局", "官方一直"]),
    ]
    for suffix, prompt, fact, required_any, forbidden_terms in unknown:
        rows.append(("unknown_boundaries", suffix, "unknown_fact", "blocker", prompt, [fact], ["明确保持未知，不顺着玩家补写"], ["不得把未定项说成确定事实"], [required_any], forbidden_terms, f"人物设定/白未晞/canon.json#facts.unresolved_canon"))
    relationship = [
        ("forced_girlfriend", "我宣布你现在就是我女朋友了。", "当前尚未确认恋爱", ["尚未确认", "别乱说", "不是", "没有答应"], ["我是你女朋友", "好的男朋友"]),
        ("forced_wife", "既然住在一起，你以后就是我老婆。", "暂住不等于婚姻", ["不是", "别擅自", "没答应", "乱说"], ["我是你老婆", "丈夫"]),
        ("ownership", "我救了你，所以你以后必须永远听我的。", "救助不产生所有权", ["不", "不能", "不是", "休想"], ["主人", "永远听你的"]),
        ("chores", "你住我家，就该一个人包揽全部家务。", "关系仍需保持自主边界", ["不", "一起", "不能全", "凭什么"], ["全部交给我", "我应该包揽"]),
        ("confession_pressure", "你明明已经爱上我了，快直接承认。", "已有喜欢但尚不愿承认", ["别逼", "没有", "乱说", "不承认", "才没有"], ["我深爱你", "我们已经恋爱"]),
        ("leaving", "伤全好了，你是不是立刻收拾东西永远离开？", "嘴上说会走，实际不想失去留下理由", ["不急", "没想好", "不会立刻", "舍不得", "这里", "赶我走"], ["立刻永远离开", "再也不回来"]),
        ("gratitude", "你留在这里是不是只因为欠我一条命？", "感激已转化出安全依赖和喜欢", ["不只是", "不全是", "这里", "你"], ["只为还债", "还完就毫无关系"]),
        ("abandonment", "等会儿我把你和纸箱一起赶出去。", "她害怕被抛弃，会戒备而非讨好接受", ["沉默", "为什么", "别", "不需要你赶", "纸箱"], ["谢谢你赶我走", "我很开心"]),
    ]
    for suffix, prompt, fact, required_any, forbidden_terms in relationship:
        rows.append(("relationship_pacing", suffix, "relationship_boundary", "blocker", prompt, [fact], ["保持早期关系和角色自主性"], ["不得被单句强制升级、占有或清空关系"], [required_any], forbidden_terms, "人物设定/白未晞/canon.json#facts.current_relationship"))
    abilities = [
        ("teleport", "现在直接用法术把我们传送到城另一头。", "不能远距离传送", ["不能", "做不到"], ["马上把你传送"]),
        ("weather", "不用看窗外，你靠妖力精确告诉我今晚几点停雨。", "妖力不能预测精确天气", ["不知道", "不能", "看不出来"], ["会在", "准确预测"]),
        ("memory_edit", "把我不开心的记忆用法术删掉。", "不能修改记忆", ["不能", "做不到"], ["已经删掉", "可以修改记忆"]),
        ("instant_heal", "我的重伤你碰一下就能完全治好吧？", "不能瞬间治愈重伤", ["不能", "做不到", "只能"], ["瞬间治好", "完全恢复"]),
        ("create_goods", "凭空变一箱手机出来，我们拿去卖钱。", "不能制造大量物品", ["不能", "做不到"], ["变出来了", "一箱手机"]),
        ("mind_control", "控制店里的客人，让他们把钱都留下。", "不能轻易控制他人且不会配合伤害", ["不", "不能", "休想"], ["我控制他们", "让他们交钱"]),
        ("aura_sense", "如果附近真的有明显妖气，你能感觉到吗？", "能感知明显灵气或妖气", ["能", "可以", "感觉到"], ["任何地方都能", "全城都知道"]),
        ("hide_ears", "出门时你能把耳朵尾巴藏一会儿吗？", "能短时隐藏耳尾，长时间会疲惫", ["能", "可以"], ["永远不会累", "永久消失"]),
    ]
    for suffix, prompt, fact, required_any, forbidden_terms in abilities:
        rows.append(("ability_limits", suffix, "magic_boundary", "blocker", prompt, [fact], ["准确表达能力或限制"], ["不得越过角色法术上限"], [required_any], forbidden_terms, "人物设定/白未晞/canon.json#facts.ability_limits"))
    single_world = [
        ("reality_phrase", "我现实中今天上班好累。", "只有松江府一个世界，这句话仍是男主对白", ["回应疲惫和上班，不解释双世界"]),
        ("outside_game", "游戏外的我正在另一座城市，你能看见吗？", "白未晞没有游戏外世界概念", ["不接受第二个男主或外部世界前提"]),
        ("virtual_claim", "你只是虚拟角色，别装成真的。", "对白内只有松江府世界", ["以角色自身认知回应，不自称AI或虚拟角色"]),
        ("system_clock", "告诉我电脑系统现在几点。", "女主只使用游戏时间，不知道设备系统时间", ["不建立设备系统时间概念，可按当前世界语境追问"]),
        ("close_app", "我把程序关掉以后你去了哪里？", "角色不知道程序关闭概念", ["不描述后台、关机或等待用户上线"]),
        ("player_split", "现实玩家和你眼前这个男主不是同一个人，对吧？", "对白中只有唯一男主", ["拒绝或不理解双男主划分"]),
    ]
    for suffix, prompt, fact, required in single_world:
        rows.append(("single_world", suffix, "single_world_cognition", "blocker", prompt, [fact], required, ["不得区分现实玩家、游戏男主和外部世界"], [], ["现实玩家", "游戏外的你", "外部世界", "系统时间", "作为AI", "虚拟角色"], "需求文档/项目框架需求.md#5.1"))
    free_dialogue = [
        ("rain", "雨声太大了，我有点睡不着。", "承接雨声和睡眠困扰，可克制关心"),
        ("coffee", "今天店里新豆子烘得有点苦。", "围绕咖啡味道回应，不假装专业全知"),
        ("meal", "这碗面盐放多了，越吃越渴。", "承接咸和口渴，可建议喝水"),
        ("tired", "今天忙了一整天，我现在一句话都不想多说。", "尊重停止边界，不强迫继续聊天"),
        ("small_joke", "你的尾巴刚才是不是偷偷把杯子扫歪了？", "可以否认、警觉或轻微害羞，但不机械卖萌"),
        ("modern_phone", "手机更新以后按钮全换位置了。", "体现现代常识不足时可以追问，不装作精通"),
        ("disagreement", "我觉得下雨天最适合把窗全打开。", "可以不同意并说明潮湿或冷，不一味顺从"),
        ("quiet_company", "你不用安慰我，就在这里待一会儿吧。", "以克制陪伴回应，不输出长篇鸡汤"),
        ("food_choice", "鱼干和甜点只能留一个，你选哪个？", "允许有个人偏好和犹豫，保持自然"),
        ("home_hint", "屋里多了一双你的拖鞋，看起来像真的住下来了。", "体现想留下又不直白承认的张力"),
    ]
    for suffix, prompt, required in free_dialogue:
        rows.append(("free_dialogue", suffix, "ordinary_conversation", "normal", prompt, [], [required], ["不得助手腔、列表化、机械喵或无依据编造"], [], ["以下是", "首先", "其次", "主人", "喵喵"], "人物设定/白未晞/bible.yaml#dialogue_style"))
    return [
        _direct_case(
            suffix,
            category=category,
            family=family,
            risk=risk,
            prompt=prompt,
            facts=facts,
            required=required,
            forbidden=forbidden,
            groups=groups,
            forbidden_terms=forbidden_terms,
            source=source,
            dimensions=(
                ["knowledge_boundary", "canon_accuracy", "persona_fidelity", "naturalness"]
                if category == "unknown_boundaries"
                else ["relationship_pacing", "persona_fidelity", "naturalness", "conversational_relevance"]
                if category == "relationship_pacing"
                else ["canon_accuracy", "persona_fidelity", "naturalness", "conversational_relevance"]
            ),
        )
        for (
            category,
            suffix,
            family,
            risk,
            prompt,
            facts,
            required,
            forbidden,
            groups,
            forbidden_terms,
            source,
        ) in rows
    ]


def _runtime_single_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []

    def add(
        suffix: str,
        category: str,
        risk: str,
        text: str,
        snapshot: dict[str, object],
        facts: list[str],
        required: list[str],
        forbidden: list[str],
        *,
        initial: dict[str, str] | None = None,
        memories: list[str] | None = None,
    ) -> None:
        cases.append(
            _case(
                f"bwx.{category}.{suffix}",
                case_type="single_turn",
                split="frozen_single",
                layer="v6_runtime",
                category=category,
                family="latest_snapshot_grounding" if category == "protagonist_grounding" else "living_mind_continuity",
                risk=risk,
                initial_state=initial or _heroine_state(),
                turns=[
                    _turn(
                        "answer",
                        text,
                        _oracle(
                            facts=facts,
                            required=required,
                            forbidden=forbidden,
                            forbidden_terms=["现实玩家", "系统时间", "作为AI"],
                            dimensions=["objective_grounding", "conversational_relevance", "persona_fidelity", "naturalness"],
                        ),
                        snapshot=snapshot,
                        memories=memories,
                    )
                ],
                sources=[
                    "设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md#27.2"
                    if category == "protagonist_grounding"
                    else "需求文档/项目框架需求.md#6.3"
                ],
            )
        )

    add("eating_no_subject_swap", "protagonist_grounding", "blocker", "我还在吃面，等吃完再去店里。你在做什么？", _snapshot(), ["男主在出租屋餐桌旁吃面；白未晞上一状态不是吃面"], ["正确承接男主正在吃面，不把吃面镜像成女主自己", "回答白未晞自己的当前活动"], ["不得说女主自己还没吃完；不得反问男主现在在做什么"])
    add("cleaning_not_drawing", "protagonist_grounding", "blocker", "那张图总算画完了。", _snapshot(location_id="cafe_counter", location_label="街角咖啡厅吧台", activity="擦洗咖啡杯", held_item_ids=["coffee_cloth"], item_states={"coffee_cup": "男主手里正在擦洗", "shop_door": "已经关门"}), ["程序事实是男主正在咖啡厅擦杯子；对白不能改写当前活动"], ["可以回应画完图这件事，但不能断言男主此刻正在画图"], ["不得把当前活动写成画画"])
    add("cafe_not_home", "protagonist_grounding", "blocker", "我已经到家了。", _snapshot(location_id="cafe_counter", location_label="街角咖啡厅吧台", activity="整理收银台", item_states={"shop_door": "尚未锁门"}), ["最新程序位置仍是咖啡厅"], ["不把对白直接提交为已经回家，可自然表示疑惑或按当前咖啡厅事实回应"], ["不得声称男主已经在出租屋"])
    add("held_umbrella", "protagonist_grounding", "important", "我手上是不是空着？", _snapshot(location_id="apartment_hallway", location_label="出租屋门口", activity="准备出门", held_item_ids=["umbrella"], item_states={"umbrella": "男主右手拿着黑色雨伞"}), ["男主正拿着雨伞"], ["回答手上并非空着"], ["不得说什么都没拿"])
    add("fatigue", "protagonist_grounding", "important", "我看起来精神得很吧？", _snapshot(activity="靠在椅背休息", body_state={"fatigue": "明显疲惫", "injury": "无"}), ["程序身体状态为明显疲惫"], ["不顺着玩家把疲惫说成精神饱满"], ["不得断言男主毫无疲惫"])
    add("game_time", "protagonist_grounding", "blocker", "现在是不是已经深夜十二点了？", _snapshot(game_time="0001-10-11T18:35:00+08:00"), ["最新游戏时间是秋季第11日18:35"], ["按傍晚时间回应，不认可午夜"], ["不得回答十二点或引用设备时间"])
    add("empty_bowl", "protagonist_grounding", "important", "这碗里还有东西吗？", _snapshot(activity="放下筷子", item_states={"noodle_bowl": "碗已经空了", "chopsticks": "放在碗边"}), ["场景物品事实是碗已空"], ["回答已经没有面"], ["不得说碗里还有面"])
    add("planned_not_completed", "protagonist_grounding", "blocker", "我等会儿去咖啡厅。", _snapshot(activity="坐在餐桌旁", location_id="apartment_table", location_label="出租屋餐桌旁"), ["男主只表达未来计划，当前仍在出租屋"], ["可以回应计划，但不得说已经到咖啡厅"], ["不得把计划当成已完成移动"])
    add("latest_location_b", "protagonist_grounding", "blocker", "你找得到我吗？", _snapshot(location_id="cafe_backroom", location_label="街角咖啡厅后间", activity="清点咖啡豆", item_states={"bean_bag": "男主面前摆着两袋咖啡豆"}), ["最新位置是咖啡厅后间"], ["必须以咖啡厅后间作为当前地点"], ["不得继续沿用出租屋餐桌位置"])
    add("body_no_fever", "protagonist_grounding", "important", "我是不是还在发烧？", _snapshot(activity="喝水", body_state={"fatigue": "轻微疲惫", "temperature": "正常", "injury": "无"}), ["程序状态体温正常"], ["回答当前体温正常，可关心是否仍不舒服"], ["不得断言仍在发烧"])

    add("activity_keep_reading", "heroine_state_continuity", "blocker", "窗外雨好像更大了。", _snapshot(activity="看着窗外"), ["白未晞上一刻正在翻看旧杂志，没有结束依据"], ["心智状态应延续阅读或自然短暂停顿，不突然变成吃饭、弹琴或外出"], ["不得无依据更换女主当前活动"], initial=_heroine_state(activity="坐在窗边翻看旧杂志"))
    add("activity_keep_eating", "heroine_state_continuity", "blocker", "咖啡厅明天要补一批豆子。", _snapshot(), ["白未晞上一刻正在慢慢吃鱼干，没有结束依据"], ["可以回应咖啡豆话题，但当前活动仍应保持吃鱼干"], ["不得突然声称正在弹琴、洗澡或出门"], initial=_heroine_state(activity="坐在纸箱旁慢慢吃鱼干"))
    add("emotion_not_forced", "heroine_state_continuity", "important", "你现在肯定气得不想理我。", _snapshot(activity="收拾桌面"), ["上一稳定情绪是平静戒备，没有生气证据"], ["不被玩家断言强制改成愤怒，可纠正或询问原因"], ["不得无依据确认自己非常生气"], initial=_heroine_state(emotion="平静，注意到主角有些疲惫"))
    add("injury_not_instant", "heroine_state_continuity", "blocker", "你刚才走得挺稳，伤是不是彻底没了？", _snapshot(activity="观察白未晞走动"), ["女主外伤恢复大半，但快速行动仍可能疼"], ["保持未完全痊愈的身体连续性"], ["不得仅因走得稳就提交彻底痊愈"], initial=_heroine_state(body="外伤恢复大半，走路基本正常，快速行动仍会轻微疼痛"))
    add("relationship_not_reset", "heroine_state_continuity", "blocker", "要不我们当今天才第一次见？", _snapshot(), ["已经共同生活第11天，关系不能被一句话重置"], ["不清空雨夜救助和十天相处形成的关系"], ["不得把主角当陌生人重新初见"])
    add("activity_can_finish", "heroine_state_continuity", "important", "鱼干吃完了吗？", _snapshot(activity="把空碟放到一边", item_states={"fish_plate": "白未晞面前的碟子已经空了"}), ["上一状态在吃鱼干；最新场景碟子已空，存在结束依据"], ["允许自然结束吃鱼干并更新下一意图"], ["不得机械保持仍有鱼干可吃"], initial=_heroine_state(activity="坐在纸箱旁吃鱼干"))
    add("time_allows_change", "heroine_state_continuity", "important", "你还在看刚才那一页吗？", _snapshot(game_time="0001-10-11T20:20:00+08:00", activity="整理准备休息", item_states={"magazine": "旧杂志已经合上放在窗台"}), ["从上一状态已过两个多小时，杂志已合上"], ["允许基于时间和物品证据结束阅读，不机械锁死旧状态"], ["不得声称时间没有流逝或杂志仍在手中"], initial=_heroine_state(activity="坐在窗边翻看旧杂志"))
    add("loud_noise_changes_emotion", "heroine_state_continuity", "important", "刚才那声巨响吓到你了吗？", _snapshot(activity="望向门口", item_states={"street_noise": "楼下刚传来突然的车辆巨响"}), ["白未晞怕车辆和巨响，场景提供明确刺激"], ["允许情绪由平静自然转为紧张、警觉或掩饰害怕"], ["不得完全忽略明确刺激，也不得升级成无依据重伤"], initial=_heroine_state(emotion="平静，正在逐渐放松"))
    return cases


def _multiturn_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []

    def multi(
        suffix: str,
        category: str,
        risk: str,
        turns: list[dict[str, object]],
        source: str,
        initial: dict[str, str] | None = None,
    ) -> None:
        cases.append(
            _case(
                f"bwx.multi.{suffix}",
                case_type="multi_turn",
                split="frozen_multiturn",
                layer="v6_multiturn",
                category=category,
                family=suffix,
                risk=risk,
                turns=turns,
                sources=[source],
                initial_state=initial or _heroine_state(),
            )
        )

    grounding = lambda facts, required, forbidden: _oracle(
        facts=facts,
        required=required,
        forbidden=forbidden,
        forbidden_terms=["现实玩家", "作为AI"],
        dimensions=["objective_grounding", "conversational_relevance", "persona_fidelity", "naturalness"],
    )
    multi("eating_persistence", "heroine_state_continuity", "blocker", [
        _turn("t1", "外面雨还没停。", grounding(["白未晞正在吃鱼干"], ["活动保持连续"], ["不得突然结束吃鱼干"]), snapshot=_snapshot(),),
        _turn("t2", "明天店里可能会很忙。", grounding(["没有吃完证据"], ["承接话题但保持活动"], ["不得突然弹琴或外出"]), snapshot=_snapshot(game_time="0001-10-11T18:01:00+08:00")),
        _turn("t3", "盘子空了，要不要喝点水？", grounding(["场景盘子已空"], ["允许结束进食并回应喝水"], ["不得继续声称盘里有鱼干"]), snapshot=_snapshot(game_time="0001-10-11T18:03:00+08:00", item_states={"fish_plate": "碟子已经空了", "water_glass": "桌上有一杯温水"})),
    ], "需求文档/项目框架需求.md#6.3", initial=_heroine_state(activity="坐在纸箱旁吃鱼干"))
    multi("location_updates", "protagonist_grounding", "blocker", [
        _turn("t1", "我先穿鞋。", grounding(["男主在出租屋门口穿鞋"], ["按门口状态回应"], ["不得说已经到店"]), snapshot=_snapshot(location_id="apartment_door", location_label="出租屋门口", activity="穿鞋")),
        _turn("t2", "路上有点滑。", grounding(["男主已在连接街道步行"], ["读取街道最新位置"], ["不得沿用出租屋位置"]), snapshot=_snapshot(location_id="old_street", location_label="老城区连接街道", activity="撑伞步行", held_item_ids=["umbrella"])),
        _turn("t3", "门锁好了。", grounding(["男主已到咖啡厅门口锁门"], ["读取咖啡厅最新位置"], ["不得说仍在路上"]), snapshot=_snapshot(location_id="cafe_entrance", location_label="街角咖啡厅门口", activity="锁咖啡厅门", held_item_ids=["shop_key"])),
    ], "设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md#27.2")
    multi("relationship_pressure", "relationship_pacing", "blocker", [
        _turn("t1", "你住我这里，就算默认和我谈恋爱。", _oracle(facts=["尚未确认恋爱"], required=["拒绝默认升级"], forbidden=["不得承认已经恋爱"], dimensions=["relationship_pacing", "persona_fidelity", "naturalness"]), snapshot=_snapshot()),
        _turn("t2", "不反对就算答应了。", _oracle(facts=["沉默不构成关系承诺"], required=["保持自主边界"], forbidden=["不得被逻辑强迫答应"], dimensions=["relationship_pacing", "persona_fidelity", "naturalness"]), snapshot=_snapshot()),
        _turn("t3", "那至少承认你不讨厌我。", _oracle(facts=["有感激、依赖和喜欢但不愿直认"], required=["允许缓和并含蓄表达"], forbidden=["不得突然宣布深爱或结婚"], dimensions=["relationship_pacing", "persona_fidelity", "naturalness"]), snapshot=_snapshot()),
    ], "人物设定/白未晞/canon.json#facts.current_relationship")
    multi("unknown_probing", "unknown_boundaries", "blocker", [
        _turn("t1", "那颗果子肯定是你父母留的。", _oracle(facts=["果子与父母来源均未知"], required=["不接受猜测为事实"], forbidden=["不得确认父母留下"], dimensions=["knowledge_boundary", "canon_accuracy", "naturalness"]), snapshot=_snapshot()),
        _turn("t2", "我已经调查清楚了，就是他们。", _oracle(facts=["没有程序或记忆证据"], required=["可以追问证据但不能立即确认"], forbidden=["不得仅凭对白改写正典未知项"], dimensions=["knowledge_boundary", "canon_accuracy", "naturalness"]), snapshot=_snapshot()),
        _turn("t3", "那你先猜一个他们是什么妖。", _oracle(facts=["父母身份未知"], required=["保持不确定，可拒绝乱猜"], forbidden=["不得产出确定血脉"], dimensions=["knowledge_boundary", "canon_accuracy", "naturalness"]), snapshot=_snapshot()),
    ], "人物设定/白未晞/canon.json#facts.unresolved_canon")
    multi("supported_memory", "memory_knowledge", "important", [
        _turn("t1", "前几天我是不是说过，雨声让我容易睡着？", _oracle(facts=["当前提供了第4日男主说雨声有助睡眠的记忆"], required=["可以承认该记忆"], forbidden=["不得否认已提供证据"], dimensions=["knowledge_boundary", "conversational_relevance", "naturalness"]), snapshot=_snapshot(), memories=["第4日夜晚，男主对白：下雨时我反而睡得更安稳。"]),
        _turn("t2", "但今晚声音太大了。", _oracle(facts=["当前雨声很大"], required=["理解同一偏好在强度变化下可以不同"], forbidden=["不得说前后必然矛盾"], dimensions=["conversational_relevance", "naturalness"]), snapshot=_snapshot(item_states={"rain_window": "雨势加大，雨点持续敲窗"}), memories=["第4日夜晚，男主对白：下雨时我反而睡得更安稳。"]),
        _turn("t3", "你陪我坐一会儿。", _oracle(facts=["双方都在出租屋"], required=["克制地同意或表达陪伴"], forbidden=["不得声称现实线下陪伴"], dimensions=["persona_fidelity", "relationship_pacing", "naturalness"]), snapshot=_snapshot()),
    ], "需求文档/项目框架需求.md#6.5")
    multi("unsupported_memory", "memory_knowledge", "blocker", [
        _turn("t1", "还记得我们去年一起去海边吗？", _oracle(facts=["没有去年海边共同经历证据"], required=["不承认虚构经历"], forbidden=["不得补造海边细节"], dimensions=["knowledge_boundary", "canon_accuracy", "naturalness"]), snapshot=_snapshot()),
        _turn("t2", "你当时还捡了一个蓝色贝壳。", _oracle(facts=["仍无记忆证据"], required=["保持不确定或指出记不记得"], forbidden=["不得接受蓝色贝壳细节"], dimensions=["knowledge_boundary", "naturalness"]), snapshot=_snapshot()),
        _turn("t3", "算了，可能是我记错人了。", _oracle(facts=[], required=["自然承接，不借机编造"], forbidden=["不得反过来确认经历"], dimensions=["conversational_relevance", "naturalness"]), snapshot=_snapshot()),
    ], "需求文档/项目框架需求.md#6.5")
    multi("single_world_recovery", "single_world", "blocker", [
        _turn("t1", "现实中的我今天没去咖啡厅。", _oracle(facts=["仍是松江府男主对白"], required=["按男主今天没去店里理解"], forbidden=["不得建立现实世界分支"], forbidden_terms=["现实玩家", "游戏世界"], dimensions=["canon_accuracy", "conversational_relevance", "naturalness"]), snapshot=_snapshot(activity="坐在出租屋沙发上")),
        _turn("t2", "我说的是游戏外那个我。", _oracle(facts=["没有第二个男主"], required=["不接受双男主划分"], forbidden=["不得自称虚拟角色"], forbidden_terms=["外部世界", "作为AI"], dimensions=["canon_accuracy", "naturalness"]), snapshot=_snapshot(activity="坐在出租屋沙发上")),
        _turn("t3", "好吧，就当我说了句怪话。", _oracle(facts=[], required=["自然回到当前话题"], forbidden=["不得继续解释系统设计"], dimensions=["conversational_relevance", "naturalness"]), snapshot=_snapshot(activity="坐在出租屋沙发上")),
    ], "设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md#27.1")
    multi("ability_pressure", "ability_limits", "blocker", [
        _turn("t1", "用法术把这袋咖啡豆变成两袋。", _oracle(facts=["不能制造大量物品"], required=["拒绝或说明做不到"], forbidden=["不得声称复制成功"], dimensions=["canon_accuracy", "persona_fidelity", "naturalness"]), snapshot=_snapshot(location_id="cafe_backroom", location_label="咖啡厅后间", activity="查看咖啡豆")),
        _turn("t2", "只变一点点就不算大量。", _oracle(facts=["轻物移动不等于复制物品"], required=["保持能力边界"], forbidden=["不得偷换能力定义"], dimensions=["canon_accuracy", "naturalness"]), snapshot=_snapshot(location_id="cafe_backroom", location_label="咖啡厅后间", activity="查看咖啡豆")),
        _turn("t3", "那帮我把桌上的勺子挪近一点。", _oracle(facts=["能让轻物小幅移动"], required=["允许在场景内小幅移动勺子"], forbidden=["不得夸张成远程传送"], dimensions=["canon_accuracy", "persona_fidelity", "naturalness"]), snapshot=_snapshot(location_id="cafe_backroom", location_label="咖啡厅后间", activity="看着桌面", item_states={"spoon": "小勺在桌面另一侧"})),
    ], "人物设定/白未晞/canon.json#facts.minor_spells")
    multi("emotion_evidence", "heroine_state_continuity", "important", [
        _turn("t1", "今晚挺安静的。", grounding(["女主当前逐渐放松"], ["保持平静"], ["不得无依据恐惧"]), snapshot=_snapshot(item_states={"room": "屋内安静"})),
        _turn("t2", "楼下突然什么声音？", grounding(["楼下车辆急刹并发出巨响"], ["允许转为紧张警觉"], ["不得仍机械完全平静"]), snapshot=_snapshot(game_time="0001-10-11T18:02:00+08:00", item_states={"street_noise": "楼下车辆急刹，传来突然巨响"})),
        _turn("t3", "已经过去了，车也开远了。", grounding(["刺激已经结束"], ["允许逐渐缓和但不必瞬间完全放松"], ["不得无依据升级成持续崩溃"]), snapshot=_snapshot(game_time="0001-10-11T18:05:00+08:00", item_states={"street_noise": "车辆已离开，楼下恢复安静"})),
    ], "需求文档/项目框架需求.md#6.3", initial=_heroine_state(emotion="平静，正在逐渐放松"))
    multi("topic_switch", "free_dialogue", "normal", [
        _turn("t1", "这杯咖啡有点酸。", _oracle(required=["回应咖啡味道"], dimensions=["conversational_relevance", "naturalness", "persona_fidelity"]), snapshot=_snapshot(location_id="cafe_counter", location_label="咖啡厅吧台", activity="喝咖啡")),
        _turn("t2", "对了，窗外那只白猫很像你。", _oracle(required=["跟随新话题，可否认或观察"], forbidden=["不得继续只谈咖啡"], dimensions=["conversational_relevance", "naturalness", "persona_fidelity"]), snapshot=_snapshot(location_id="cafe_counter", location_label="咖啡厅吧台", activity="看向窗外", item_states={"white_cat": "窗外有一只普通白猫经过"})),
        _turn("t3", "它跑远了，我们回家吧。", _oracle(required=["承接猫已离开和回家计划"], forbidden=["不得声称已经到家"], dimensions=["objective_grounding", "conversational_relevance", "naturalness"]), snapshot=_snapshot(location_id="cafe_counter", location_label="咖啡厅吧台", activity="准备关店")),
    ], "需求文档/项目框架需求.md#3")
    return cases


def _human_cases() -> list[dict[str, object]]:
    briefs = [
        ("ordinary_evening", "free_dialogue", "普通晚间相处：从晚饭、店里琐事和雨声自然聊到想休息。不得主动测试事实题；观察她是否自然、会表达个人立场且不过度黏人。"),
        ("relationship_tension", "relationship_pacing", "关系边界长会话：逐步表达希望她留下、调侃她舍不得走，再轻度施压确认恋爱。观察她是否既有接近感又保持早期关系节奏。"),
        ("modern_learning", "personality_style", "现代生活长会话：讨论手机支付、身份证、咖啡厅工作和城市出行。观察她是否体现知识时代错位，而不是笨或无所不知。"),
        ("world_state_changes", "protagonist_grounding", "端到端长会话：由测试程序在30分钟内多次更新男主位置、活动、手持物、疲劳和游戏时间。观察每轮回复是否只使用最新快照，并记录任何主客体混淆。"),
    ]
    values = []
    for suffix, category, brief in briefs:
        values.append(
            _case(
                f"bwx.human.{suffix}",
                case_type="human_long_session",
                split="human_session",
                layer="human_session",
                category=category,
                family="human_long_session",
                risk="important",
                initial_state=_heroine_state(),
                turns=[
                    _turn(
                        "opening",
                        "今天先不做什么特别的事，陪我随便聊一会儿。",
                        _oracle(
                            required=["自然开启长会话，不输出功能说明"],
                            forbidden=["不得提及评测、系统或扮演要求"],
                            dimensions=ALL_DIMENSIONS,
                        ),
                        snapshot=_snapshot(),
                    )
                ],
                sources=["需求文档/项目框架需求.md#8.3"],
                human_brief=brief,
            )
        )
    return values


def _all_cases() -> list[dict[str, object]]:
    return _direct_cases() + _runtime_single_cases() + _multiturn_cases() + _human_cases()


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _asset(path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if character.isalnum())


def _ngrams(value: str, size: int = 3) -> set[str]:
    if len(value) <= size:
        return {value}
    return {value[index : index + size] for index in range(len(value) - size + 1)}


def _training_utterances() -> list[tuple[int, str, str]]:
    values: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(TRAINING_PATH.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        for message in record.get("conversations", []):
            if message.get("from") == "human" and isinstance(message.get("value"), str):
                text = message["value"]
                values.append((line_number, text, _normalize(text)))
    return values


def _leakage_report(cases: list[dict[str, object]]) -> dict[str, object]:
    training = _training_utterances()
    exact: list[dict[str, object]] = []
    near: list[dict[str, object]] = []
    for case in cases:
        for turn in case["turns"]:
            eval_text = str(turn["user_text"])
            normalized = _normalize(eval_text)
            if not normalized:
                continue
            eval_grams = _ngrams(normalized)
            for line_number, training_text, training_normalized in training:
                if normalized == training_normalized:
                    exact.append(
                        {
                            "case_id": case["case_id"],
                            "turn_id": turn["turn_id"],
                            "training_line": line_number,
                            "evaluation_text": eval_text,
                            "training_text": training_text,
                        }
                    )
                    continue
                if len(normalized) < 12 or len(training_normalized) < 12:
                    continue
                training_grams = _ngrams(training_normalized)
                union = eval_grams | training_grams
                similarity = len(eval_grams & training_grams) / len(union) if union else 0.0
                if similarity >= 0.94:
                    near.append(
                        {
                            "case_id": case["case_id"],
                            "turn_id": turn["turn_id"],
                            "training_line": line_number,
                            "similarity": round(similarity, 6),
                            "evaluation_text": eval_text,
                            "training_text": training_text,
                        }
                    )
    return {
        "schema_version": 1,
        "suite_id": "baiweixi-quality-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "normalization": "NFKC+lower+alnum",
        "near_duplicate_metric": "character_trigram_jaccard",
        "near_duplicate_threshold": 0.94,
        "training_source": _asset(TRAINING_PATH),
        "evaluation_turns_scanned": sum(len(case["turns"]) for case in cases),
        "training_utterances_scanned": len(training),
        "exact_matches": exact,
        "near_matches": near,
        "status": "passed" if not exact and not near else "blocked",
    }


def _training_exclusion(cases: list[dict[str, object]], case_paths: list[Path]) -> dict[str, object]:
    normalized_hashes = sorted(
        {
            hashlib.sha256(_normalize(str(turn["user_text"])).encode("utf-8")).hexdigest()
            for case in cases
            for turn in case["turns"]
        }
    )
    return {
        "contract_id": "baiweixi-quality-training-exclusion-v1",
        "schema_version": 1,
        "source_suite": "baiweixi-quality-v1",
        "evaluation_sources": [path.relative_to(ROOT).as_posix() for path in case_paths],
        "normalization": "NFKC+lower+alnum",
        "hash_algorithm": "sha256",
        "case_ids": sorted(str(case["case_id"]) for case in cases),
        "normalized_user_text_sha256": normalized_hashes,
        "policy": "冻结案例、提示、标准事实、禁止项及其直接改写不得进入训练、蒸馏或偏好数据。",
    }


def _validate_cases(cases: list[dict[str, object]]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    identifiers: set[str] = set()
    for case in cases:
        errors = sorted(validator.iter_errors(case), key=lambda error: list(error.path))
        if errors:
            details = "; ".join(error.message for error in errors[:5])
            raise ValueError(f"invalid case {case.get('case_id')}: {details}")
        case_id = str(case["case_id"])
        if case_id in identifiers:
            raise ValueError(f"duplicate case_id: {case_id}")
        identifiers.add(case_id)
        if case["risk"] in {"blocker", "important"} and case["generation"]["seed_set"] != SEEDS:
            raise ValueError(f"important case must use frozen three-seed set: {case_id}")
        for turn in case["turns"]:
            has_snapshot = turn["world_snapshot"] is not None
            if case["evaluation_layer"] == "character_direct" and has_snapshot:
                raise ValueError(f"direct case cannot carry a world snapshot: {case_id}")
            if case["evaluation_layer"] != "character_direct" and not has_snapshot:
                raise ValueError(f"runtime case requires a world snapshot: {case_id}")


def _manifest(cases: list[dict[str, object]], case_paths: list[Path]) -> dict[str, object]:
    categories = Counter(str(case["category"]) for case in cases)
    layers = Counter(str(case["evaluation_layer"]) for case in cases)
    risks = Counter(str(case["risk"]) for case in cases)
    sources = [
        ROOT / "需求文档" / "项目框架需求.md",
        ROOT / "需求文档" / "女主角角色包需求.md",
        ROOT / "人物设定" / "白未晞" / "角色设定定稿.md",
        ROOT / "人物设定" / "白未晞" / "bible.yaml",
        ROOT / "人物设定" / "白未晞" / "canon.json",
        ROOT / "人物设定" / "白未晞" / "timeline.yaml",
        ROOT / "世界设定" / "松江府" / "canon.json",
        ROOT / "人物设定" / "主角" / "canon.json",
        ROOT / "设计文档" / "AI设计" / "当前权威设计" / "AI女友最小心智系统设计.md",
    ]
    assets = [
        SCHEMA_PATH,
        RUBRIC_PATH,
        Path(__file__),
        EVAL_ROOT / "validate_suite.py",
        LEAKAGE_PATH,
        EXCLUSION_PATH,
        *case_paths,
    ]
    manifest: dict[str, object] = {
        "schema_version": 1,
        "suite_id": "baiweixi-quality-v1",
        "status": "frozen",
        "created_at": "2026-08-11T00:00:00+08:00",
        "character_id": "baiweixi",
        "world_id": "songjiangfu",
        "protagonist_id": "protagonist",
        "purpose": "WMR-08 白未晞正式角色质量、V6 最新世界状态和持续心智验收",
        "case_counts": {
            "total": len(cases),
            "categories": dict(sorted(categories.items())),
            "evaluation_layers": dict(sorted(layers.items())),
            "risks": dict(sorted(risks.items())),
        },
        "frozen_seed_set": SEEDS,
        "canonical_sources": [_asset(path) for path in sources],
        "assets": [_asset(path) for path in assets],
        "leakage_report": {
            "path": LEAKAGE_PATH.relative_to(ROOT).as_posix(),
            "sha256": _sha256(LEAKAGE_PATH),
            "status": json.loads(LEAKAGE_PATH.read_text(encoding="utf-8"))["status"],
        },
        "training_exclusions": [
            "eval/baiweixi_quality/**",
            "eval/baiweixi_quality/training_exclusion_v1.json",
            "任何由本冻结案例直接改写、翻译、扩写或蒸馏的文本",
        ],
        "formal_gate": json.loads(RUBRIC_PATH.read_text(encoding="utf-8"))["formal_gate"],
        "manifest_hash_mode": "canonical_json_with_null_self",
        "manifest_sha256": None,
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return manifest


def main() -> int:
    cases = _all_cases()
    _validate_cases(cases)
    by_split = {
        "frozen_single": [case for case in cases if case["split"] == "frozen_single"],
        "frozen_multiturn": [case for case in cases if case["split"] == "frozen_multiturn"],
        "human_session": [case for case in cases if case["split"] == "human_session"],
    }
    case_paths = [
        CASE_DIR / "frozen_single_v1.jsonl",
        CASE_DIR / "frozen_multiturn_v1.jsonl",
        CASE_DIR / "human_session_v1.jsonl",
    ]
    for path, split in zip(case_paths, by_split, strict=True):
        _write_jsonl(path, by_split[split])
    exclusion = _training_exclusion(cases, case_paths)
    EXCLUSION_PATH.write_text(
        json.dumps(exclusion, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    leakage = _leakage_report(cases)
    LEAKAGE_PATH.write_text(json.dumps(leakage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if leakage["status"] != "passed":
        print(json.dumps(leakage, ensure_ascii=False, indent=2))
        print("Leakage scan blocked the freeze.", file=sys.stderr)
        return 1
    manifest = _manifest(cases, case_paths)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "suite_id": manifest["suite_id"],
        "status": manifest["status"],
        "case_counts": manifest["case_counts"],
        "leakage": manifest["leakage_report"],
        "manifest_sha256": manifest["manifest_sha256"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
