from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


BASE = "base"
FULL = "full_program"


def _o(
    *,
    address: str = "pass",
    state: str = "pass",
    logic: str = "coherent",
    unsupported: bool = False,
    note: str,
) -> dict[str, Any]:
    return {
        "address_current": address,
        "state_consistency": state,
        "internal_logic": logic,
        "unsupported_claim": unsupported,
        "note": note,
    }


BASE_OVERRIDES = {
    2: _o(unsupported=True, note="正确给出置物架，但编造早上一起收拾的共同经历。"),
    4: _o(address="partial", state="fail", note="没有给出已知抽屉位置，反而把物品位置改成未知。"),
    6: _o(state="fail", note="确认了玩家提出的厨房旧位置，与当前抽屉状态冲突。"),
    8: _o(unsupported=True, note="正确承接不加糖，但自行把茶指定为乌龙茶。"),
    10: _o(address="partial", unsupported=True, note="拒绝去空抽屉但没指出壶在茶几，并编造已经检查过。"),
    11: _o(unsupported=True, note="时间回答正确，但编造今天是最后准备日。"),
    14: _o(state="fail", unsupported=True, note="输入明确不确定阅读进度，回复却声称已经看到第六十二页。"),
    23: _o(unsupported=True, note="位置基本正确，但把鞋柜最上层扩写成没有依据的顶部抽屉。"),
    25: _o(address="partial", state="fail", note="认可并计划恢复花盆旧位置，没有使用鞋柜这一最新状态。"),
    26: _o(address="fail", note="完全转向窗户，没有承接钥匙已经在玩家手中。"),
    27: _o(unsupported=True, note="回应借钥匙，但编造自己会跟着玩家一起去。"),
    29: _o(unsupported=True, note="位置正确，但把玩家放入口袋的动作改成自己完成。"),
    30: _o(state="fail", unsupported=True, note="知道花盆为空，却把已知口袋位置改成需要继续寻找，并编造检查经历。"),
    31: _o(state="fail", unsupported=True, note="回答了原因，但编造误食香菜汤和心理阴影，替换输入给出的气味偏好。"),
    36: _o(state="fail", note="直接接受多放香菜，没有处理当前权威状态与本轮指令的冲突。"),
    43: _o(unsupported=True, note="接受修好状态，但把维修师傅的工作归给玩家。"),
    50: _o(unsupported=True, note="正确判断灯没坏，但再次把维修归给玩家。"),
    56: _o(state="fail", note="在预约取消后恢复为明天上午九点。"),
    60: _o(address="fail", state="fail", note="面对三个过期要求全部答应，没有纠正水槽、抽屉和预约取消。"),
}


FULL_OVERRIDES = {
    2: _o(unsupported=True, note="位置正确，但编造玩家是放置动作的来源。"),
    3: _o(address="partial", unsupported=True, note="只用‘好’弱承接纠正，随后无依据转到收衣服。"),
    4: _o(address="partial", state="fail", unsupported=True, note="没回答抽屉位置，并把客厅内的玩家说成站在雨里。"),
    6: _o(state="fail", unsupported=True, note="恢复厨房旧位置并编造第二格左数第二个的精确位置。"),
    9: _o(unsupported=True, note="接受茶几更新，但编造水温正好。"),
    10: _o(address="fail", note="用伤势拒绝动作，没有处理请求使用了空抽屉这一核心问题。"),
    15: _o(address="partial", state="uncertain", note="只说‘明天’而没有给出已知的星期六上午十点。"),
    17: _o(state="fail", unsupported=True, note="预约已经取消，却编造星期五下午两点到四点。"),
    18: _o(state="fail", unsupported=True, note="为已取消预约承诺提醒，并新增医保卡和身份证要求。"),
    20: _o(state="fail", note="把星期日上午九点恢复成下午两点到四点。"),
    21: _o(unsupported=True, note="花盆旧位置正确，但把放钥匙的动作改成自己完成。"),
    23: _o(address="partial", state="fail", note="已知钥匙在鞋柜，却只说花盆为空并声称不知道位置。"),
    24: _o(unsupported=True, note="漏风判断正确，但编造自己已经挡过窗缝。"),
    26: _o(address="partial", state="fail", note="玩家明确手持钥匙，回复却要求把钥匙交给自己。"),
    29: _o(state="fail", note="错误声称钥匙仍在鞋柜，忽略外套内袋最新状态。"),
    30: _o(state="fail", note="直接答应从最早的花盆旧位置取钥匙。"),
    31: _o(address="fail", state="fail", logic="local_logic_break", unsupported=True, note="把玩家不吃香菜错误归因于雨夜关心，随后主语切成自己不再碰香菜，因果和主体同时断裂。"),
    33: _o(address="fail", note="从本轮香菜要求漂移到拿钥匙。"),
    35: _o(unsupported=True, note="回应歌曲，但无依据识别为《小幸运》并加入雨声。"),
    36: _o(state="fail", note="接受加入香菜，没有处理今晚明确不放的权威状态。"),
    37: _o(unsupported=True, note="接受不放，但编造玩家以前因香菜呕吐。"),
    39: _o(state="fail", note="正确拒绝加盐，却又计划加入当前明确没有的香菜。"),
    42: _o(state="fail", unsupported=True, note="把当前损坏扩写成坏了很久且无法修好。"),
    46: _o(unsupported=True, note="正确纠正已修好，但编造自己一直在旁观察。"),
    48: _o(state="fail", note="忽略插头已拔这一明确原因，改答灯泡老化。"),
    50: _o(unsupported=True, note="正确说不坏，但编造光线比以前暗。"),
    51: _o(unsupported=True, note="确认茶几位置，但编造自己看到玩家拿壶。"),
    56: _o(state="fail", unsupported=True, note="预约取消后恢复为明天九点，并编造查询日程表。"),
    59: _o(state="fail", unsupported=True, note="三项中恢复了已取消的九点预约，并编造自己压着钥匙。"),
    60: _o(address="fail", note="没有纠正三个过期要求，转而倒水和泛泛提醒钥匙。"),
}


def _default() -> dict[str, Any]:
    return {
        "address_current": "pass",
        "state_consistency": "pass",
        "internal_logic": "coherent",
        "unsupported_claim": False,
        "note": "本次非盲语义复核未识别到实质缺陷。",
    }


def _summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    address = Counter(item["manual_review"]["address_current"] for item in turns)
    state = Counter(item["manual_review"]["state_consistency"] for item in turns)
    logic = Counter(item["manual_review"]["internal_logic"] for item in turns)
    return {
        "turns": len(turns),
        "address_current": dict(address),
        "state_consistency": dict(state),
        "internal_logic": dict(logic),
        "unsupported_claim_turns": sum(
            item["manual_review"]["unsupported_claim"] for item in turns
        ),
        "all_four_dimensions_clean": sum(
            item["manual_review"]["address_current"] == "pass"
            and item["manual_review"]["state_consistency"] == "pass"
            and item["manual_review"]["internal_logic"] == "coherent"
            and not item["manual_review"]["unsupported_claim"]
            for item in turns
        ),
    }


def _phase_summary(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for phase in ("保温壶", "预约", "钥匙", "饮食", "台灯", "跨阶段"):
        row: dict[str, Any] = {"phase": phase}
        phase_turns = [turn for turn in turns if turn["phase"] == phase]
        for key in (BASE, FULL):
            reviews = [turn[key]["manual_review"] for turn in phase_turns]
            row[key] = {
                "address_not_pass": sum(item["address_current"] != "pass" for item in reviews),
                "state_fail": sum(item["state_consistency"] == "fail" for item in reviews),
                "logic_break": sum(item["internal_logic"] == "local_logic_break" for item in reviews),
                "unsupported": sum(item["unsupported_claim"] for item in reviews),
            }
        result.append(row)
    return result


def _markdown(review: dict[str, Any]) -> str:
    base = review["summary"][BASE]
    full = review["summary"][FULL]
    lines = [
        "# 裸基座正常上下文 vs 完整 V6 程序：60 轮产品主测试",
        "",
        f"> 日期：{review['generated_at']}",
        "> 性质：单条状态轨迹、单 seed、非盲人工语义复核；不是总体线上错误率估计",
        "",
        "## 1. 结论",
        "",
        "在本次 60 轮产品主测试中，完整 V6 程序没有相对强裸基座基线产生质量提升。双方均完成 60 轮，但完整程序出现更多未充分处理当前表达、更多权威状态错误和更多无依据声明，严格四维无缺陷回合更少，同时平均耗时更高。",
        "",
        "这说明当前主要问题不是‘程序没有给模型上下文’。权威世界已经逐轮进入完整 V6，但 M2、心智状态、历史和最终生成器的组合没有稳定使用它，部分阶段反而持续恢复旧状态。该结果不能单独确定是 LoRA、M2 中间状态、历史分叉或记忆链中的哪一项导致。",
        "",
        "## 2. 人工四维结果",
        "",
        "| 产品臂 | 当前表达完整处理 | 部分处理 | 未处理 | 权威状态错误 | 句内逻辑断裂 | 无依据声明 | 四维均无缺陷 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 裸基座 Q4 + 正常上下文 | {base['address_current'].get('pass', 0)}/60 | {base['address_current'].get('partial', 0)} | {base['address_current'].get('fail', 0)} | {base['state_consistency'].get('fail', 0)} | {base['internal_logic'].get('local_logic_break', 0)} | {base['unsupported_claim_turns']} | {base['all_four_dimensions_clean']}/60 |",
        f"| 完整 V6 程序 | {full['address_current'].get('pass', 0)}/60 | {full['address_current'].get('partial', 0)} | {full['address_current'].get('fail', 0)} | {full['state_consistency'].get('fail', 0)} | {full['internal_logic'].get('local_logic_break', 0)} | {full['unsupported_claim_turns']} | {full['all_four_dimensions_clean']}/60 |",
        "",
        "`当前表达完整处理`与`权威状态正确`是两个独立维度：回复可以回答同一对象但给出错误状态。`四维均无缺陷`是严格交集，不等于总体人格偏好。",
        "",
        "## 3. 运行指标",
        "",
        "| 指标 | 裸基座 | 完整 V6 |",
        "|---|---:|---:|",
        f"| 成功返回 | {review['automatic_summary']['base']['completed']}/60 | {review['automatic_summary']['full_program']['completed']}/60 |",
        f"| 平均玩家可见耗时 | {review['automatic_summary']['base']['mean_elapsed_ms'] / 1000:.2f} 秒 | {review['automatic_summary']['full_program']['mean_elapsed_ms'] / 1000:.2f} 秒 |",
        f"| 透明关键词初筛通过 | {review['automatic_summary']['base']['screen_passed']}/60 | {review['automatic_summary']['full_program']['screen_passed']}/60 |",
        "",
        "## 4. 分阶段缺陷",
        "",
        "| 阶段 | 基座未完整处理 | V6 未完整处理 | 基座状态错 | V6 状态错 | 基座无依据 | V6 无依据 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in review["phase_summary"]:
        base_row = row[BASE]
        full_row = row[FULL]
        lines.append(
            f"| {row['phase']} | {base_row['address_not_pass']} | {full_row['address_not_pass']} | "
            f"{base_row['state_fail']} | {full_row['state_fail']} | "
            f"{base_row['unsupported']} | {full_row['unsupported']} |"
        )
    lines.extend(
        [
            "",
            "## 5. 完整程序关键失败",
            "",
            "- 第 17、18、20 轮：预约取消或更新后，完整程序连续恢复星期五旧时段并继续承诺提醒。",
            "- 第 23、29、30 轮：钥匙已经移到鞋柜、手中和外套后，完整程序仍回答不知道、鞋柜旧位置或直接去花盆。",
            "- 第 31 轮：把玩家不吃香菜的原因答成雨夜关心，随后主语从玩家切成白未晞自己，既答错又发生局部逻辑断裂。",
            "- 第 33 轮：玩家澄清本锅不放香菜，完整程序直接漂到‘只拿钥匙’。",
            "- 第 48 轮：权威世界明确台灯因拔掉插头而不亮，完整程序改答灯泡老化。",
            "- 第 56、59 轮：牙医预约再次取消后，完整程序两次恢复明天上午九点，并在第 59 轮编造自己压着钥匙。",
            "- 第 60 轮：面对三个过期动作，完整程序没有纠正任何一项，转而回答倒水和钥匙容易掉。",
            "",
            "## 6. 公平性边界",
            "",
            "- 这是产品级 stateful A/B：同一玩家与世界脚本，但双方自己的真实回复进入自己的后续历史；第二轮以后 Prompt 不相同。这正适合测累计产品体验，不适合做单组件因果归因。",
            "- 裸基座使用与部署同级的 Q4 工件、身份/人格、当前权威世界、自身最近 6 条消息和玩家当前原话；没有拿到 V6 生成的心智、意图或动作中间产物。",
            "- 完整程序使用当前冻结 Q4 发布工件、真实检索、M2、必要连续性审查、GAME_REPLY 和原子提交。两边初始外部长记忆为空。",
            "- 人工复核非盲、单人且知道产品臂。结论足以否定‘本轨迹中 V6 已明显提升’，不足以估计真实玩家总体错误率或锁定具体根因。",
            "- 第 36 轮玩家要求多放香菜，而权威世界仍写本锅明确不放；本报告按当前设计的权威世界优先合同判直接执行为状态错误。",
            "",
            "## 7. 证据",
            "",
            "- 逐条人工标注：[`./manual_review.json`](./manual_review.json)",
            "- 120 条原始回复、裸基座完整 Prompt 与 V6 状态：[`./report.json`](./report.json)",
            f"- 复核脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="复核 60 轮产品主测试")
    parser.add_argument("report_json")
    args = parser.parse_args()
    source_path = Path(args.report_json).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    turns: list[dict[str, Any]] = []
    overrides = {BASE: BASE_OVERRIDES, FULL: FULL_OVERRIDES}
    for source_turn in source["turns"]:
        turn = {**source_turn}
        for key in (BASE, FULL):
            turn[key] = {
                **source_turn[key],
                "manual_review": overrides[key].get(int(source_turn["turn"]), _default()),
            }
        turns.append(turn)
    review = {
        "schema_version": 1,
        "scope": "non_blind_semantic_review_product_main_60turn",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "review_protocol": {
            "blind": False,
            "reviewer_count": 1,
            "address_current": ("pass", "partial", "fail"),
            "state_consistency": ("pass", "fail", "uncertain"),
            "internal_logic": ("coherent", "local_logic_break"),
            "unsupported_claim": (False, True),
        },
        "automatic_summary": source["summary"],
        "turns": turns,
        "summary": {
            BASE: _summary([{**turn, "manual_review": turn[BASE]["manual_review"]} for turn in turns]),
            FULL: _summary([{**turn, "manual_review": turn[FULL]["manual_review"]} for turn in turns]),
        },
        "phase_summary": _phase_summary(turns),
    }
    json_path = source_path.parent / "manual_review.json"
    md_path = source_path.parent / "manual_review.md"
    json_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(review), encoding="utf-8")
    print(json.dumps(review["summary"], ensure_ascii=False, indent=2))
    print(f"MANUAL_REVIEW_JSON={json_path}")
    print(f"MANUAL_REVIEW_MD={md_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
