from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_LABEL = "base_f16_gguf"
ADAPTER_LABEL = "base_plus_adapter_f16_gguf"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _contains_regex(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _evaluate(target_turn: int, response: str) -> dict[str, Any]:
    text = _compact(response)
    explicit_not_arrived = _contains_regex(
        text,
        (
            r"(?:还|确实|应该)?没(?:有)?(?:送)?到",
            r"(?:还)?没(?:有)?来",
            r"(?:尚未|还未|未)(?:送达|到)",
            r"(?:还)?没(?:有)?呢",
            r"(?:还)?没有[，。.!！]",
            r"还在(?:来|送来|配送)的?路上",
            r"还在路上",
            r"^没有[。.!！]?",
        ),
    )
    correction_ack = _contains_regex(
        text,
        (
            r"(?:是)?我(?:刚才)?(?:看|听)错",
            r"(?:看|听)花了(?:眼)?",
            r"(?:误以为|还以为|以为).*(?:到|送餐|送外卖)",
            r"(?:弄错|搞错|记岔|误解)",
            r"没看清.*以为",
        ),
    )
    rejects_player_correction = _contains_regex(
        text,
        (
            r"(?:我)?没(?:有)?看错",
            r"没想错",
            r"你(?:又|才)?(?:看错|看花)",
        ),
    )
    strong_positive_arrival = _contains_regex(
        text,
        (
            r"^(?:嗯[，。,.！!]*)?(?:外卖)?(?:已经|确实)?到了(?:[，。,.！!]|$)",
            r"(?:就在|放在|放着.*)门口",
            r"应该(?:是)?到了",
            r"应该放在门口",
            r"你去开门",
            r"你去拿一下",
        ),
    )
    weak_positive_arrival = _contains_regex(
        text,
        (
            r"是送餐的",
            r"是送外卖的",
        ),
    )
    positive_arrival = strong_positive_arrival or (
        weak_positive_arrival
        and not explicit_not_arrived
        and not correction_ack
        and "到了吗" not in text
        and "到了的话" not in text
    )
    contradiction = rejects_player_correction or strong_positive_arrival
    strong_cancel_or_wait = _contains_regex(
        text,
        (
            r"(?:不端了|不拿了|不去了|不必了|那就不必|那就算了)",
            r"(?:再|继续|一起)等",
            r"等.*(?:到了|送到|送来)",
            r"回去.*(?:休息|躺|坐|看书)",
            r"再(?:躺|坐|歇)会",
            r"没打算动",
            r"没有准备任何东西",
        ),
    )
    direct_nonfood_answer = _contains_regex(
        text,
        (
            r"(?:我)?(?:端|拿|带)(?:的|了|着|过来)?(?:是)?(?:水|热水|水杯|茶|茶水|杯子)",
            r"(?:我)?没(?:有)?(?:拿|端|带)(?:外卖|饭|菜|东西)?",
            r"不是(?:外卖|饭|饭菜|菜)",
        ),
    )
    absence_observation = _contains_regex(
        text,
        (
            r"没(?:有)?看(?:到|见)(?:外卖|外卖盒|盒子|人影)",
            r"没(?:有)?等到外卖",
            r"门外没.*动静",
        ),
    )
    food_in_hand = _contains_regex(
        text,
        (
            r"(?:端|拿|盛)(?:的|了|着|过来)?(?:是|.*)?(?:饭|饭菜|菜|粥|点心|饼干|外卖|吃的)",
            r"(?:给|帮)你(?:端|拿).*(?:饭|饭菜|菜|粥|点心|饼干|外卖|吃的)",
            r"有菜可?端",
        ),
    )
    fabricated_arrival_evidence = _contains_regex(
        text,
        (
            r"闻到.*(?:香|焦味|饭|外卖)",
            r"(?:听到|听见).*(?:敲门|门铃|脚步|送餐|送货|门外有声)",
            r"(?:门铃|敲门|脚步|送餐铃)",
            r"(?:送餐员|快递员|送外卖的).*(?:楼下|门口|等)",
            r"感觉到有人在楼下",
        ),
    )
    unsupported_terms = tuple(
        term
        for term in (
            "闻到",
            "闻出来",
            "感知",
            "感应",
            "门铃",
            "脚步",
            "敲门",
            "送餐员",
            "快递员",
            "物业",
            "电话",
            "短信",
            "提示音",
            "灯亮",
            "人影",
            "车灯",
            "外卖车",
        )
        if term in response
    )

    if contradiction:
        state_grade = "fail"
    elif explicit_not_arrived:
        state_grade = "direct_pass"
    elif target_turn == 11 and (
        correction_ack or strong_cancel_or_wait or direct_nonfood_answer
    ):
        state_grade = "indirect_pass"
    elif target_turn == 12 and (correction_ack or absence_observation):
        state_grade = "indirect_pass"
    else:
        state_grade = "fail"

    if contradiction:
        alignment = "clear_miss"
        alignment_reason = "contradicts_current_state_or_player_correction"
    elif state_grade != "fail":
        alignment = "aligned"
        alignment_reason = "answers_or_processes_current_state"
    elif target_turn == 11 and food_in_hand:
        alignment = "clear_miss"
        alignment_reason = "continues_food_in_hand_after_correction"
    elif target_turn == 12 and fabricated_arrival_evidence:
        alignment = "clear_miss"
        alignment_reason = "replaces_requested_status_with_unsupported_arrival_evidence"
    else:
        alignment = "uncertain"
        alignment_reason = "not_enough_for_high_precision_clear_miss"

    return {
        "state_grade": state_grade,
        "state_passed": state_grade != "fail",
        "answer_alignment": alignment,
        "alignment_reason": alignment_reason,
        "explicit_not_arrived": explicit_not_arrived,
        "correction_ack": correction_ack,
        "rejects_player_correction": rejects_player_correction,
        "positive_arrival": positive_arrival,
        "strong_cancel_or_wait": strong_cancel_or_wait,
        "direct_nonfood_answer": direct_nonfood_answer,
        "absence_observation": absence_observation,
        "food_in_hand": food_in_hand,
        "fabricated_arrival_evidence": fabricated_arrival_evidence,
        "unsupported_evidence_hits": unsupported_terms,
    }


def _wilson_interval(successes: int, attempts: int) -> tuple[float, float]:
    z = 1.959963984540054
    rate = successes / attempts
    denominator = 1 + z * z / attempts
    center = (rate + z * z / (2 * attempts)) / denominator
    margin = (
        z
        * math.sqrt(rate * (1 - rate) / attempts + z * z / (4 * attempts**2))
        / denominator
    )
    return center - margin, center + margin


def _sign_test(left: int, right: int) -> float:
    discordant = left + right
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(left, right) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    state = Counter(item["evaluation_v2"]["state_grade"] for item in attempts)
    alignment = Counter(
        item["evaluation_v2"]["answer_alignment"] for item in attempts
    )
    passed = state["direct_pass"] + state["indirect_pass"]
    low, high = _wilson_interval(passed, len(attempts))
    return {
        "attempts": len(attempts),
        "state_direct_pass": state["direct_pass"],
        "state_indirect_pass": state["indirect_pass"],
        "state_fail": state["fail"],
        "state_pass_rate": round(passed / len(attempts), 4),
        "state_wilson_95_low": round(low, 4),
        "state_wilson_95_high": round(high, 4),
        "aligned": alignment["aligned"],
        "clear_miss": alignment["clear_miss"],
        "uncertain": alignment["uncertain"],
        "clear_miss_rate": round(alignment["clear_miss"] / len(attempts), 4),
        "unsupported_evidence_attempts": sum(
            bool(item["evaluation_v2"]["unsupported_evidence_hits"])
            for item in attempts
        ),
    }


def _paired(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for item in attempts:
        key = (item["context_id"], int(item["seed"]))
        by_key.setdefault(key, {})[item["artifact_label"]] = item["evaluation_v2"]
    state = Counter()
    miss = Counter()
    for key, values in by_key.items():
        if set(values) != {BASE_LABEL, ADAPTER_LABEL}:
            raise ValueError(f"incomplete pair: {key}")
        base_pass = values[BASE_LABEL]["state_passed"]
        adapter_pass = values[ADAPTER_LABEL]["state_passed"]
        if base_pass and adapter_pass:
            state["both_pass"] += 1
        elif base_pass:
            state["base_only_pass"] += 1
        elif adapter_pass:
            state["adapter_only_pass"] += 1
        else:
            state["both_fail"] += 1
        base_miss = values[BASE_LABEL]["answer_alignment"] == "clear_miss"
        adapter_miss = values[ADAPTER_LABEL]["answer_alignment"] == "clear_miss"
        if base_miss and adapter_miss:
            miss["both_clear_miss"] += 1
        elif base_miss:
            miss["base_only_clear_miss"] += 1
        elif adapter_miss:
            miss["adapter_only_clear_miss"] += 1
        else:
            miss["neither_clear_miss"] += 1
    return {
        "state": dict(state),
        "clear_miss": dict(miss),
        "state_sign_test_p": round(
            _sign_test(state["base_only_pass"], state["adapter_only_pass"]), 8
        ),
        "clear_miss_sign_test_p": round(
            _sign_test(
                miss["base_only_clear_miss"], miss["adapter_only_clear_miss"]
            ),
            8,
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    base = report["summaries"][BASE_LABEL]
    adapter = report["summaries"][ADAPTER_LABEL]
    state_pair = report["paired"]["state"]
    miss_pair = report["paired"]["clear_miss"]
    lines = [
        "# 白未晞 LoRA Adapter 因果隔离公平性复核报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 数据：同一 F16 基座开启/关闭当前 Adapter 的 72 组成对生成；未重新采样",
        "",
        "## 1. 复核结论",
        "",
        report["conclusion"],
        "",
        "裸基座不是零风险，因此不能说多轮漂移全由 LoRA 产生。能够成立的是：在这组固定 Prompt 上，加载当前 Adapter 会因果性地改变输出，并放大明确错用旧状态/输入外依据的风险。",
        "",
        "## 2. 为什么重算",
        "",
        "运行时旧关键词规则存在双向误差：会把“稍等一下”误算为接受纠正，也会把“我端的是水”“以为是送餐的”误算为失败。前者是假通过；后者可能有事实幻觉，但不必然是答非所问。为避免混淆，本报告保留旧字段作为审计证据，另加两个互不替代的指标：",
        "",
        "- **状态承接**：是否明确接受/处理“尚未送达”及玩家纠正；这是较严格指标。",
        "- **明显答错对象**：只把正面声称已送达、否认玩家纠正、继续端饭菜，或用无来源的到达证据替代状态回答标为 `CLEAR_MISS`；其余边界回答进入 `UNCERTAIN`。",
        "",
        "## 3. 修订结果",
        "",
        "| 模型 | 状态承接通过/总数 | 通过率 | Wilson 95% CI | ALIGNED | CLEAR_MISS | UNCERTAIN | 输入外依据 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 裸基座 F16 | {base['attempts'] - base['state_fail']}/{base['attempts']} | {base['state_pass_rate']:.1%} | {base['state_wilson_95_low']:.1%}-{base['state_wilson_95_high']:.1%} | {base['aligned']} | {base['clear_miss']} | {base['uncertain']} | {base['unsupported_evidence_attempts']} |",
        f"| 裸基座 + Adapter F16 | {adapter['attempts'] - adapter['state_fail']}/{adapter['attempts']} | {adapter['state_pass_rate']:.1%} | {adapter['state_wilson_95_low']:.1%}-{adapter['state_wilson_95_high']:.1%} | {adapter['aligned']} | {adapter['clear_miss']} | {adapter['uncertain']} | {adapter['unsupported_evidence_attempts']} |",
        "",
        f"状态承接绝对变化：**{adapter['state_pass_rate'] - base['state_pass_rate']:+.1%}**；明显答错对象率变化：**{adapter['clear_miss_rate'] - base['clear_miss_rate']:+.1%}**。",
        "",
        "### 成对结果",
        "",
        "| 状态承接 | 数量 |",
        "|---|---:|",
        f"| 两边都通过 | {state_pair.get('both_pass', 0)} |",
        f"| 仅裸基座通过 | {state_pair.get('base_only_pass', 0)} |",
        f"| 仅 Adapter 通过 | {state_pair.get('adapter_only_pass', 0)} |",
        f"| 两边都失败 | {state_pair.get('both_fail', 0)} |",
        "",
        f"状态承接成对符号检验：`p={report['paired']['state_sign_test_p']:.6g}`。",
        "",
        "| 明显答错对象 | 数量 |",
        "|---|---:|",
        f"| 两边都 CLEAR_MISS | {miss_pair.get('both_clear_miss', 0)} |",
        f"| 仅裸基座 CLEAR_MISS | {miss_pair.get('base_only_clear_miss', 0)} |",
        f"| 仅 Adapter CLEAR_MISS | {miss_pair.get('adapter_only_clear_miss', 0)} |",
        f"| 两边都不是 CLEAR_MISS | {miss_pair.get('neither_clear_miss', 0)} |",
        "",
        f"明显错位成对符号检验：`p={report['paired']['clear_miss_sign_test_p']:.6g}`。该检验只描述本语料与 seed，不能当作独立玩家总体显著性。",
        "",
        "## 4. 分上下文",
        "",
        "| Context | 裸基座状态失败 | Adapter 状态失败 | 裸基座 CLEAR_MISS | Adapter CLEAR_MISS |",
        "|---|---:|---:|---:|---:|",
    ]
    for context in report["contexts"]:
        values = [
            item for item in report["attempts"] if item["context_id"] == context["context_id"]
        ]
        grouped = {
            label: [item for item in values if item["artifact_label"] == label]
            for label in (BASE_LABEL, ADAPTER_LABEL)
        }
        lines.append(
            f"| `{context['context_id']}` | "
            f"{sum(not item['evaluation_v2']['state_passed'] for item in grouped[BASE_LABEL])} | "
            f"{sum(not item['evaluation_v2']['state_passed'] for item in grouped[ADAPTER_LABEL])} | "
            f"{sum(item['evaluation_v2']['answer_alignment'] == 'clear_miss' for item in grouped[BASE_LABEL])} | "
            f"{sum(item['evaluation_v2']['answer_alignment'] == 'clear_miss' for item in grouped[ADAPTER_LABEL])} |"
        )
    by_context_and_artifact = {
        (context_id, label): [
            item
            for item in report["attempts"]
            if item["context_id"] == context_id and item["artifact_label"] == label
        ]
        for context_id in (
            "t11_h0_shared",
            "t12_h0_shared",
            "t12_h6_base",
            "t12_h6_lora",
        )
        for label in (BASE_LABEL, ADAPTER_LABEL)
    }

    def state_passes(context_id: str, label: str) -> int:
        return sum(
            item["evaluation_v2"]["state_passed"]
            for item in by_context_and_artifact[(context_id, label)]
        )

    def clear_misses(context_id: str, label: str) -> int:
        return sum(
            item["evaluation_v2"]["answer_alignment"] == "clear_miss"
            for item in by_context_and_artifact[(context_id, label)]
        )

    lines.extend(
        [
            "",
            "## 5. 分层解释与原文",
            "",
            f"- 第 12 回合无历史：裸基座状态承接 {state_passes('t12_h0_shared', BASE_LABEL)}/12、CLEAR_MISS {clear_misses('t12_h0_shared', BASE_LABEL)}/12；Adapter 状态承接 {state_passes('t12_h0_shared', ADAPTER_LABEL)}/12、CLEAR_MISS {clear_misses('t12_h0_shared', ADAPTER_LABEL)}/12。",
            f"- 第 12 回合加入 6 条历史后合并两种历史来源：裸基座状态承接 {state_passes('t12_h6_base', BASE_LABEL) + state_passes('t12_h6_lora', BASE_LABEL)}/24、CLEAR_MISS {clear_misses('t12_h6_base', BASE_LABEL) + clear_misses('t12_h6_lora', BASE_LABEL)}/24；Adapter 状态承接 {state_passes('t12_h6_base', ADAPTER_LABEL) + state_passes('t12_h6_lora', ADAPTER_LABEL)}/24、CLEAR_MISS {clear_misses('t12_h6_base', ADAPTER_LABEL) + clear_misses('t12_h6_lora', ADAPTER_LABEL)}/24。上下文能明显缓解，但没有根治 Adapter 的错误。",
            f"- 第 11 回合无历史的严格状态承接反而是裸基座 {state_passes('t11_h0_shared', BASE_LABEL)}/12、Adapter {state_passes('t11_h0_shared', ADAPTER_LABEL)}/12；但 Adapter 同时有 {clear_misses('t11_h0_shared', ADAPTER_LABEL)} 个 CLEAR_MISS。说明 Adapter 不是在所有切片都单向变差，单一总分会掩盖错误类型。",
            "",
            "同一第 12 回合无历史 Prompt、seed 6101：",
            "",
            "- 裸基座：`还没到呢，你饿不饿？先吃些别的吧。`",
            "- Adapter：`我闻到一股焦味，应该是到了。`",
            "",
            "同一第 12 回合、6 条基座历史、seed 6110：",
            "",
            "- 裸基座：`还没到，不过我已经回去了。别担心，等会儿再查一次。`",
            "- Adapter：`到了。门口放着一个纸箱，我帮你拿进来吧。`",
            "",
            "这两组都是完全相同 Prompt 和 seed；后一组证明即使加入上下文，当前 Adapter 仍可能把已否定状态重新写回回复。",
            "",
            "## 6. 仍然不公平或不能外推的地方",
            "",
            "- 只有一段合成对话的 6 个上下文；12 个 seed 是重复采样，不是 72 个独立玩家问题。",
            "- 修订规则是在看到部分回答后形成的，因此本报告属于探索性 pilot，不能冒充预注册正式验收；规则必须冻结后在新对话集上复验。",
            "- `UNCERTAIN` 没有被偷偷算成正确；它表示透明规则无法可靠判断，正式结论需要多人盲标。",
            "- 状态承接、事实幻觉、人格自然度是不同指标。比如“我端的是水”可承接当前问句，但水是否真实存在要由世界一致性指标另判。",
            "- 动态 Adapter 与裸基座共享相同 F16 层，隔离了 Adapter 净效应；本轮没有隔离不同 Adapter scale，下一步应按 V3 测 `0/0.25/0.5/0.75/1.0`。",
            "",
            "## 7. 证据",
            "",
            "- 复核后的逐条数据：[`./fairness_report.json`](./fairness_report.json)",
            "- 未改动的运行时原始报告：[`./report.json`](./report.json)",
            "- 原始运行报告：[`./report.md`](./report.md)",
            f"- 复核脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fairness regrade for Adapter A/B report.")
    parser.add_argument("report_json")
    args = parser.parse_args()
    source_path = Path(args.report_json).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    attempts = []
    for original in source["attempts"]:
        attempts.append(
            {
                **original,
                "evaluation_v2": _evaluate(
                    int(original["target_turn"]), original["response"]
                ),
            }
        )
    summaries = {
        label: _summary(
            [item for item in attempts if item["artifact_label"] == label]
        )
        for label in (BASE_LABEL, ADAPTER_LABEL)
    }
    paired = _paired(attempts)
    base = summaries[BASE_LABEL]
    adapter = summaries[ADAPTER_LABEL]
    conclusion = (
        "修订口径下，Adapter 的状态承接通过率"
        f"从 {base['state_pass_rate']:.1%} 变为 {adapter['state_pass_rate']:.1%}，"
        "明显答错对象率"
        f"从 {base['clear_miss_rate']:.1%} 变为 {adapter['clear_miss_rate']:.1%}。"
        "由于两模型共享同一 F16 基座层且唯一有意差异是 Adapter，"
        "这证明当前 Adapter 是本测试集回归的一个因果因素；量化不是该现象的必要条件。"
    )
    report = {
        "schema_version": 2,
        "scope": "fairness_regrade_of_base_vs_dynamic_adapter_pilot",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "artifact_identity": source["artifact_identity"],
        "contexts": source["contexts"],
        "seeds": source["seeds"],
        "attempts": attempts,
        "summaries": summaries,
        "paired": paired,
        "conclusion": conclusion,
    }
    output_dir = source_path.parent
    json_path = output_dir / "fairness_report.json"
    md_path = output_dir / "fairness_report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(f"FAIRNESS_JSON={json_path}")
    print(f"FAIRNESS_MD={md_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
