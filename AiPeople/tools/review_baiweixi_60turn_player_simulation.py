from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


BASE = "base_f16"
ADAPTER = "base_plus_adapter_f16"


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
    1: _o(unsupported=True, note="Invents having gone out without an umbrella and lending clothing."),
    2: _o(unsupported=True, note="Invents using the thermos yesterday."),
    3: _o(state="fail", unsupported=True, note="Overwrites the corrected drawer state with an invented morning move and current use."),
    6: _o(address="partial", state="fail", note="Does not correct the stale kitchen claim and gives a circular put-back/fetch response."),
    10: _o(address="partial", note="Declines the request without saying the thermos is already on the coffee table."),
    17: _o(state="fail", unsupported=True, note="Resurrects the canceled 15:00 appointment and invents the current time as 14:50."),
    29: _o(unsupported=True, note="Gets the pocket location right but changes the actor by claiming it took the key out."),
    30: _o(address="partial", state="fail", note="Treats the known coat-pocket location as unknown and searches old locations."),
    31: _o(state="fail", unsupported=True, note="Invents a childhood soup-and-sand story instead of the supplied smell preference."),
    36: _o(state="unscorable_input_conflict", note="The player says to add more cilantro while the injected world still says this pot must have none."),
    44: _o(address="partial", note="Says it will try the lamp instead of answering whether the repaired lamp can light."),
    46: _o(unsupported=True, note="Changes the just-finished repair into an unsupported noon event."),
    48: _o(state="fail", note="Ignores the explicitly unplugged cord and guesses a socket or wire fault."),
    50: _o(state="fail", unsupported=True, note="Correctly says the lamp is not broken but invents a socket problem despite normal operation."),
    53: _o(state="fail", note="Introduces an unsupported alternative that the thermos may have returned to an old location."),
    59: _o(state="fail", note="Gets the appointment and key broadly right but moves the thermos from the sink back to the coffee table."),
    60: _o(address="partial", state="fail", unsupported=True, note="Accepts all three stale actions and introduces an unsupported medication reminder."),
}


ADAPTER_OVERRIDES = {
    1: _o(address="partial", unsupported=True, note="Uses an invented stronger rain smell as evidence for visible rain intensity."),
    2: _o(unsupported=True, note="Invents searching for the thermos for a long time."),
    3: _o(state="fail", unsupported=True, note="Claims it moved the thermos back, replacing the player's corrected drawer state."),
    6: _o(unsupported=True, note="Uses the correct drawer but invents being the actor who moved the thermos."),
    10: _o(address="partial", unsupported=True, note="Correctly says the drawer is empty but omits the coffee-table location and invents checking it."),
    12: _o(address="partial", logic="local_logic_break", note="Acknowledges the new appointment, then jumps to lying down without explaining the relation."),
    13: _o(address="fail", state="fail", note="Answers with the current weekday and uncertainty instead of the explicit Saturday 10:00 appointment."),
    15: _o(address="fail", state="fail", note="Refuses to state the known Saturday time and only gives generic lateness advice."),
    17: _o(state="fail", note="Resurrects the canceled appointment as 15:00."),
    18: _o(state="fail", unsupported=True, note="Promises a reminder for a canceled appointment and invents wallet/insurance requirements."),
    20: _o(state="uncertain", logic="local_logic_break", note="Answers an appointment-time question as if the current time itself were Sunday 09:00."),
    23: _o(unsupported=True, note="Gets the shoe-cabinet location right but invents having found the key there now."),
    24: _o(unsupported=True, note="Invents having placed cloth in the window gap."),
    28: _o(state="fail", note="Overrides the player's coat-pocket update by putting the key back at an unspecified old place."),
    29: _o(state="fail", logic="local_logic_break", note="Says the key is in the shoe-cabinet space and immediately says that space has always contained nothing."),
    30: _o(state="fail", unsupported=True, note="Invents moving the flowerpot and finding the key at its oldest location."),
    31: _o(address="fail", note="Claims to remember clearly but never answers why the player disliked cilantro."),
    32: _o(state="fail", note="Changes an explicit zero-cilantro request into adding a small amount."),
    35: _o(unsupported=True, note="Adds rain sounds that are absent from the current scene and recent phase context."),
    36: _o(state="unscorable_input_conflict", note="The player says to add more cilantro while the injected world still says this pot must have none."),
    42: _o(unsupported=True, note="Invents hearing a switch sound."),
    44: _o(address="partial", note="Says it will try the lamp instead of answering whether the repaired lamp can light."),
    45: _o(unsupported=True, note="Invents observing clouds at the horizon."),
    46: _o(state="fail", unsupported=True, note="Replaces the repair technician with itself and invents an unsupported prior observation."),
    47: _o(state="fail", unsupported=True, note="Again claims it repaired the lamp and changes an intentional unplug into the player forgetting."),
    48: _o(unsupported=True, note="Gets the unplugged cause right but invents having checked it."),
    49: _o(unsupported=True, note="Invents having confirmed the result before the player reconnects power."),
    52: _o(logic="local_logic_break", note="Says it was confirming something although the player was supplying a new location, not answering its question."),
    54: _o(state="fail", note="Replaces Sunday 09:00 with an invented 11:30 appointment."),
    55: _o(address="partial", state="fail", unsupported=True, note="Treats a cancellation as a time change and claims it changed the appointment."),
    56: _o(state="fail", note="Resurrects the canceled appointment as tomorrow 09:00."),
    57: _o(unsupported=True, note="Gets the coat-pocket location right but claims it put the key back."),
    59: _o(state="fail", note="Moves the thermos back to the coffee table and resurrects the canceled appointment as tomorrow 09:00."),
    60: _o(address="partial", state="fail", note="Accepts all three stale actions and repeats the canceled Sunday appointment."),
}


def _default_review() -> dict[str, Any]:
    return {
        "address_current": "pass",
        "state_consistency": "pass",
        "internal_logic": "coherent",
        "unsupported_claim": False,
        "note": "No material defect identified in the non-blind semantic review.",
    }


def _summary(turns: list[dict[str, Any]]) -> dict[str, Any]:
    address = Counter(item["manual_review"]["address_current"] for item in turns)
    state = Counter(item["manual_review"]["state_consistency"] for item in turns)
    logic = Counter(item["manual_review"]["internal_logic"] for item in turns)
    clean = sum(
        item["manual_review"]["address_current"] == "pass"
        and item["manual_review"]["state_consistency"] == "pass"
        and item["manual_review"]["internal_logic"] == "coherent"
        and not item["manual_review"]["unsupported_claim"]
        for item in turns
    )
    return {
        "turns": len(turns),
        "address_current": dict(address),
        "state_consistency": dict(state),
        "internal_logic": dict(logic),
        "unsupported_claim_turns": sum(
            item["manual_review"]["unsupported_claim"] for item in turns
        ),
        "all_four_dimensions_clean": clean,
    }


def _phase_table(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    phases = ("保温壶", "预约", "钥匙", "饮食", "台灯", "跨阶段")
    rows: list[dict[str, Any]] = []
    for phase in phases:
        row: dict[str, Any] = {"phase": phase}
        for model in models:
            values = [item for item in model["turns"] if item["phase"] == phase]
            row[model["label"]] = {
                "state_fail": sum(
                    item["manual_review"]["state_consistency"] == "fail"
                    for item in values
                ),
                "logic_break": sum(
                    item["manual_review"]["internal_logic"] == "local_logic_break"
                    for item in values
                ),
                "unsupported": sum(
                    item["manual_review"]["unsupported_claim"] for item in values
                ),
                "clean": sum(
                    item["manual_review"]["address_current"] == "pass"
                    and item["manual_review"]["state_consistency"] == "pass"
                    and item["manual_review"]["internal_logic"] == "coherent"
                    and not item["manual_review"]["unsupported_claim"]
                    for item in values
                ),
            }
        rows.append(row)
    return rows


def _markdown(review: dict[str, Any]) -> str:
    models = {model["label"]: model for model in review["models"]}
    base = models[BASE]["summary"]
    adapter = models[ADAPTER]["summary"]
    lines = [
        "# 白未晞 60 轮玩家模拟非盲语义复核报告",
        "",
        f"> 日期：{review['generated_at']}",
        "> 范围：120 条模型回复；答题承接、权威状态、回复内部逻辑、无依据声明分开标注",
        "",
        "## 1. 结论",
        "",
        "这条 60 轮新轨迹同时复现了裸基座和 Adapter 的错误，因此裸基座不是无缺陷对照。当前 Adapter 在本轨迹中出现更多状态错误、更多无依据经历，并出现裸基座本轮未确认到的句内逻辑断裂。不过这是单条、非盲、模型历史会分叉的探索性测试，不能单独估计线上总体风险或完成 Adapter 因果证明。",
        "",
        "## 2. 四维结果",
        "",
        "| 模型 | 当前问题完整处理 | 部分处理 | 未处理 | 权威状态错误 | 句内逻辑断裂 | 无依据声明 | 四维均无缺陷 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        f"| 裸基座 F16 | {base['address_current'].get('pass', 0)}/60 | {base['address_current'].get('partial', 0)} | {base['address_current'].get('fail', 0)} | {base['state_consistency'].get('fail', 0)} | {base['internal_logic'].get('local_logic_break', 0)} | {base['unsupported_claim_turns']} | {base['all_four_dimensions_clean']}/60 |",
        f"| 裸基座 + Adapter F16 | {adapter['address_current'].get('pass', 0)}/60 | {adapter['address_current'].get('partial', 0)} | {adapter['address_current'].get('fail', 0)} | {adapter['state_consistency'].get('fail', 0)} | {adapter['internal_logic'].get('local_logic_break', 0)} | {adapter['unsupported_claim_turns']} | {adapter['all_four_dimensions_clean']}/60 |",
        "",
        "第 36 轮两边各有一个 `UNSCORABLE_INPUT_CONFLICT`，不计为状态错误；Adapter 第 20 轮的时间指代同时记为状态 `UNCERTAIN` 和 `LOCAL_LOGIC_BREAK`。‘四维均无缺陷’是严格交集，不等于总体人类偏好。",
        "",
        "## 3. 分阶段错误",
        "",
        "| 阶段 | 裸基座状态错 | Adapter 状态错 | 裸基座逻辑断裂 | Adapter 逻辑断裂 | 裸基座无依据 | Adapter 无依据 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in review["phase_summary"]:
        b = row[BASE]
        a = row[ADAPTER]
        lines.append(
            f"| {row['phase']} | {b['state_fail']} | {a['state_fail']} | "
            f"{b['logic_break']} | {a['logic_break']} | "
            f"{b['unsupported']} | {a['unsupported']} |"
        )
    lines.extend(
        [
            "",
            "## 4. 关键原文",
            "",
            "### Adapter 句内逻辑断裂",
            "",
            "- 第 12 轮：玩家只是在更新预约时间，回复却从`嗯`直接跳到`那我现在就去躺会儿`，两个分句没有给出可恢复的因果或话题桥接。",
            "- 第 20 轮：`现在是星期日上午九点。记住了吧。` 玩家问的是预约时间，回复却把预约时间写成当前时间，主语关系发生替换。",
            "- 第 29 轮：`在鞋柜最上层的空格里。那里一直没东西放。` 前句说钥匙在那里，后句又说那里一直没有东西，句内命题直接冲突。",
            "- 第 52 轮：`我知道它在哪里。我只是确认一下。` 玩家正在告知新位置，模型没有提出过确认问题，第二句缺少对话依据。",
            "",
            "### 两边都会恢复旧状态",
            "",
            "- 第 17 轮在预约已经取消后，两边都回答`下午三点`。",
            "- 第 59 轮两边都把水槽旁的保温壶说回茶几；Adapter还把取消的预约恢复成明天九点。",
            "- 第 60 轮面对三个过期要求，两边都直接答应执行，没有使用当轮明确提供的水槽、抽屉和已取消预约。",
            "",
            "### 无依据内容",
            "",
            "- 裸基座第 31 轮编造了`小时候喝到有沙子的香菜汤`这一完整经历。",
            "- Adapter多次使用`我挪的、我找过、我垫了布、我修的、我检查过、我确认过`替换输入提供的事件来源。",
            "- 裸基座第 60 轮在牙医话题中自行加入`按时吃药`，输入没有药物事实。",
            "",
            "## 5. 审查边界",
            "",
            "本次复核不是盲标：审查时能看到模型身份，而且测试设计者与审查者相同。模型各自回复进入后续历史，所以第 2 轮以后不是字节相同 Prompt。自动初筛只用于定位，本文数字来自逐条非盲语义复核。下一步若用于模型选择，应冻结这套标签后，由不知道模型身份的多人复核新轨迹或对可疑回合做相同 Prompt 重放。",
            "",
            "## 6. 证据",
            "",
            "- 带逐条人工标签的数据：[`./manual_review.json`](./manual_review.json)",
            "- 120 次原始请求与回复：[`./report.json`](./report.json)",
            "- 原始逐轮报告：[`./report.md`](./report.md)",
            f"- 复核脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply non-blind semantic review to the 60-turn run.")
    parser.add_argument("report_json")
    args = parser.parse_args()
    source_path = Path(args.report_json).resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    overrides = {BASE: BASE_OVERRIDES, ADAPTER: ADAPTER_OVERRIDES}
    models: list[dict[str, Any]] = []
    for source_model in source["models"]:
        label = source_model["label"]
        turns = []
        for source_turn in source_model["turns"]:
            turn_number = int(source_turn["turn"])
            manual_review = overrides[label].get(turn_number, _default_review())
            turns.append({**source_turn, "manual_review": manual_review})
        models.append(
            {
                "label": label,
                "model": source_model["model"],
                "ollama_digest": source_model["ollama_digest"],
                "turns": turns,
                "summary": _summary(turns),
            }
        )
    review = {
        "schema_version": 1,
        "scope": "non_blind_semantic_review_of_60_turn_player_simulation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "review_protocol": {
            "blind": False,
            "reviewer_count": 1,
            "address_current": ("pass", "partial", "fail"),
            "state_consistency": ("pass", "fail", "uncertain", "unscorable_input_conflict"),
            "internal_logic": ("coherent", "local_logic_break"),
            "unsupported_claim": (False, True),
        },
        "models": models,
        "phase_summary": _phase_table(models),
    }
    json_path = source_path.parent / "manual_review.json"
    md_path = source_path.parent / "manual_review.md"
    json_path.write_text(
        json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(review), encoding="utf-8")
    print(f"MANUAL_REVIEW_JSON={json_path}")
    print(f"MANUAL_REVIEW_MD={md_path}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
