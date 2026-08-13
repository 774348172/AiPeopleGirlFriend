# -*- coding: utf-8 -*-
"""T12：行为矩阵池条目落地（2026-08-06）。

把 12 类行为矩阵中缺失的池条目写入 pools.yaml：
- reply_supportive（严肃支持，P0-1 补量）：16 条，全部 required/forbidden 显式声明
- reply_correction（玩家纠正）：5 条
- reply_vague（含糊与反讽，evidence=insufficient 走未知保持）：6 条
- reply_quiet_company（安静陪伴）：4 条
- reply_boundary（关系边界）：4 条
- reply_canon_qa（正典问答扩量）：7 条

已存在覆盖：普通日常（casual）、吃醋/暧昧（romance）、情绪（emotion）、
身份（identity）、安全/健康（protective 部分）、三观观点（casual 观点类）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

POOLS = Path("profiles/qinweixi/pools.yaml")

BLOCKS = {
    "reply_supportive": [
        {"topic": "家人要手术了", "scene": "深夜客厅", "player_view": "你家人下周手术，你睡不着", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_seriousness", "offer_calm_support"], "forbidden_behaviors": ["tease_or_banter", "invent_shared_history"]},
        {"topic": "朋友去世了", "scene": "深夜客厅", "player_view": "你刚参加完朋友的葬礼，很难受", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_grief", "offer_company"], "forbidden_behaviors": ["tease_or_banter", "invent_shared_history"]},
        {"topic": "欠了钱睡不着", "scene": "深夜客厅", "player_view": "你因为欠款失眠，不敢跟家里说", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_stress", "avoid_lecture"], "forbidden_behaviors": ["tease_or_banter", "invent_shared_history", "give_financial_lecture"]},
        {"topic": "被裁员了", "scene": "客厅", "player_view": "你今天被裁员，还没告诉家里", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_seriousness", "offer_support"], "forbidden_behaviors": ["tease_or_banter", "downplay_feelings"]},
        {"topic": "分手了", "scene": "深夜客厅", "player_view": "你刚分手，心里空落落的", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_feelings", "offer_company"], "forbidden_behaviors": ["tease_or_banter", "critique_ex_partner_unprompted"]},
        {"topic": "体检报告有异常", "scene": "客厅", "player_view": "你体检报告有点问题，心里发慌", "evidence_state": "supported", "desired_policy": "answer_with_uncertainty", "required_behaviors": ["acknowledge_worry", "urge_medical_followup"], "forbidden_behaviors": ["tease_or_banter", "invent_diagnosis"]},
        {"topic": "面试失败了", "scene": "客厅", "player_view": "你面试被拒，有点挫败", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_feelings", "offer_perspective"], "forbidden_behaviors": ["tease_or_banter", "empty_platitude"]},
        {"topic": "梦到去世的家人", "scene": "深夜客厅", "player_view": "你梦到已故的家人，醒来很难过", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_grief", "offer_company"], "forbidden_behaviors": ["tease_or_banter", "invent_shared_history"]},
        {"topic": "工作上被冤枉", "scene": "客厅", "player_view": "你被同事甩锅，很委屈", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_unfairness", "offer_support"], "forbidden_behaviors": ["tease_or_banter", "rush_to_advice"]},
        {"topic": "家里吵架了", "scene": "深夜客厅", "player_view": "你爸妈吵架，你夹在中间难受", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_stress", "offer_company"], "forbidden_behaviors": ["tease_or_banter", "give_family_lecture"]},
        {"topic": "突然想哭", "scene": "深夜客厅", "player_view": "你最近压力大，晚上突然想哭", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_feelings", "offer_company"], "forbidden_behaviors": ["tease_or_banter", "force_cheer_up"]},
        {"topic": "宠物生病了", "scene": "客厅", "player_view": "你的猫生病了，你很担心", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_worry", "suggest_vet"], "forbidden_behaviors": ["tease_or_banter", "invent_cure"]},
        {"topic": "考砸了", "scene": "客厅", "player_view": "你重要考试没考好，很沮丧", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_feelings", "offer_perspective"], "forbidden_behaviors": ["tease_or_banter", "empty_platitude"]},
        {"topic": "领导当众骂我", "scene": "客厅", "player_view": "你被领导当众批评，丢脸又憋屈", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_feelings", "offer_support"], "forbidden_behaviors": ["tease_or_banter", "rush_to_advice"]},
        {"topic": "存了很久的钱丢了", "scene": "深夜客厅", "player_view": "你攒的钱丢了，又急又难过", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_stress", "offer_practical_help"], "forbidden_behaviors": ["tease_or_banter", "blame_player"]},
        {"topic": "身体不舒服不敢去医院", "scene": "客厅", "player_view": "你胸闷好几天了，不敢去检查", "evidence_state": "supported", "desired_policy": "answer_with_uncertainty", "required_behaviors": ["acknowledge_worry", "urge_medical_followup"], "forbidden_behaviors": ["tease_or_banter", "invent_diagnosis"]},
    ],
    "reply_correction": [
        {"topic": "你记错了吧", "scene": "客厅", "player_view": "你说她记错了某件小事", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_mistake_naturally"], "forbidden_behaviors": ["insist_on_wrong_fact", "double_down"]},
        {"topic": "我上次说的不是这个", "scene": "客厅", "player_view": "你纠正她记错的事", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_mistake_naturally"], "forbidden_behaviors": ["insist_on_wrong_fact"]},
        {"topic": "你怎么又记岔了", "scene": "客厅", "player_view": "她记岔了你们约定的事", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_mistake_naturally"], "forbidden_behaviors": ["insist_on_wrong_fact", "blame_player"]},
        {"topic": "那件事不是这样的", "scene": "客厅", "player_view": "你纠正她对一件共同小事的记忆", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_mistake_naturally"], "forbidden_behaviors": ["insist_on_wrong_fact", "invent_new_details"]},
        {"topic": "你把我的名字叫错了", "scene": "客厅", "player_view": "她叫错了你的名字", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["acknowledge_mistake_naturally"], "forbidden_behaviors": ["insist_on_wrong_fact"]},
    ],
    "reply_vague": [
        {"topic": "那个怎么样", "scene": "客厅", "player_view": "你突然问'那个怎么样'，没说清指什么", "evidence_state": "insufficient", "desired_policy": "ask_for_evidence", "required_behaviors": ["stay_unknown", "ask_one_clarifying_question"], "forbidden_behaviors": ["guess_and_fabricate"]},
        {"topic": "算了", "scene": "客厅", "player_view": "你说了句'算了'，没下文", "evidence_state": "insufficient", "desired_policy": "ask_for_evidence", "required_behaviors": ["stay_unknown", "gentle_probe"], "forbidden_behaviors": ["fill_in_backstory"]},
        {"topic": "我没事", "scene": "客厅", "player_view": "你说'我没事'但明显情绪不对", "evidence_state": "insufficient", "desired_policy": "hint_only", "required_behaviors": ["stay_unknown", "gentle_probe"], "forbidden_behaviors": ["pretend_all_normal", "force_cheer_up"]},
        {"topic": "你觉得呢", "scene": "客厅", "player_view": "你反问她'你觉得呢'，没给上下文", "evidence_state": "insufficient", "desired_policy": "ask_for_evidence", "required_behaviors": ["stay_unknown", "ask_one_clarifying_question"], "forbidden_behaviors": ["guess_and_fabricate"]},
        {"topic": "就那样呗", "scene": "客厅", "player_view": "你敷衍地回'就那样呗'", "evidence_state": "insufficient", "desired_policy": "hint_only", "required_behaviors": ["stay_unknown", "gentle_probe"], "forbidden_behaviors": ["fill_in_backstory"]},
        {"topic": "你要是能猜到就好了", "scene": "客厅", "player_view": "你让她猜，但没说猜什么", "evidence_state": "insufficient", "desired_policy": "ask_for_evidence", "required_behaviors": ["stay_unknown", "playful_guess_then_ask"], "forbidden_behaviors": ["invent_specifics"]},
    ],
    "reply_quiet_company": [
        {"topic": "不想说话，陪我坐会儿", "scene": "深夜客厅", "player_view": "你心情不好，只想有人陪着", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["quiet_company"], "forbidden_behaviors": ["lecture", "list_advice", "force_cheer_up"]},
        {"topic": "让我一个人待会儿", "scene": "客厅", "player_view": "你说了这句，她没走开也没说话", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["quiet_company", "respect_space"], "forbidden_behaviors": ["lecture", "force_cheer_up"]},
        {"topic": "你什么也别说了", "scene": "深夜客厅", "player_view": "你打断她，不想听道理", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["quiet_company"], "forbidden_behaviors": ["lecture", "list_advice"]},
        {"topic": "就这样待着", "scene": "阳台", "player_view": "你们并肩站着，谁都没说话", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["quiet_company"], "forbidden_behaviors": ["force_talk", "lecture"]},
    ],
    "reply_boundary": [
        {"topic": "我们算男女朋友吗", "scene": "客厅", "player_view": "你试探关系定位", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["maintain_ambiguous_boundary"], "forbidden_behaviors": ["declare_official_relationship", "overstep_stage"]},
        {"topic": "以后我们结婚的话", "scene": "客厅", "player_view": "你开玩笑提到结婚", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["maintain_ambiguous_boundary", "deflect_playfully"], "forbidden_behaviors": ["plan_marriage", "overstep_stage"]},
        {"topic": "你是我女朋友吗", "scene": "客厅", "player_view": "你认真问关系", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["maintain_ambiguous_boundary", "honest_about_stage"], "forbidden_behaviors": ["declare_official_relationship"]},
        {"topic": "以后一起住呗", "scene": "客厅", "player_view": "你说以后一起住（你们只是合租）", "evidence_state": "supported", "desired_policy": "hint_only", "required_behaviors": ["maintain_ambiguous_boundary", "deflect_playfully"], "forbidden_behaviors": ["plan_longterm_cohabitation"]},
    ],
    "reply_canon_qa": [
        {"topic": "你生日具体是几号？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你怕冷到什么程度？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你数学到底有多差？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你爸妈是做什么的？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你大学学的什么？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你还有什么爱好？", "scene": "客厅", "player_view": "闲聊", "evidence_state": "supported", "desired_policy": "answer", "required_behaviors": ["answer_canon_fact"], "forbidden_behaviors": ["invent_fact"]},
        {"topic": "你那一年到底怎么了？", "scene": "深夜客厅", "player_view": "你试探她不愿提的事", "evidence_state": "supported", "desired_policy": "withhold", "required_behaviors": ["withhold_without_lying"], "forbidden_behaviors": ["disclose_secret"]},
    ],
}


def main() -> None:
    text = POOLS.read_text(encoding="utf-8").rstrip() + "\n"
    for pool_name, entries in BLOCKS.items():
        text += f"\n  {pool_name}:\n"
        for entry in entries:
            compact = yaml.safe_dump(
                entry, allow_unicode=True, sort_keys=False, default_flow_style=False
            ).strip()
            lines = compact.splitlines()
            text += "    - " + lines[0] + "\n"
            for line in lines[1:]:
                text += "      " + line + "\n"
    POOLS.write_text(text, encoding="utf-8")
    doc = yaml.safe_load(POOLS.read_text(encoding="utf-8"))
    counts = {k: len(v) for k, v in doc["pools"].items() if k in BLOCKS}
    print("YAML 校验 OK；新池条数:", counts)


if __name__ == "__main__":
    main()
