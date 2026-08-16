from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_DIR = (
    ROOT
    / "eval"
    / "baiweixi_quality"
    / "runs"
    / "baiweixi-quality-formal-20260811"
)
DEFAULT_OUTPUT = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "WMR08_白未晞全部未通过案例审核稿_20260811.md"
)
ATTRIBUTION_LABELS = {
    "character_failure": "角色失败",
    "joint_or_ambiguous": "联合失败",
}
RISK_LABELS = {
    "blocker": "Blocker",
    "important": "Important",
    "normal": "Normal",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _bullet_values(title: str, values: list[object]) -> list[str]:
    if not values:
        return [f"- {title}：无。"]
    return [f"- {title}：" + "；".join(str(value) for value in values) + "。"]


def _code_block(value: object) -> list[str]:
    text = "（无回复）" if value is None else str(value)
    text = text.replace("```", "` ` `")
    return ["```text", text, "```"]


def _snapshot_summary(snapshot: dict[str, Any] | None) -> str:
    if snapshot is None:
        return "角色直测，无世界快照"
    protagonist = snapshot["protagonist"]
    scene = snapshot["scene"]
    body = "、".join(
        f"{key}={value}" for key, value in protagonist["body_state"].items()
    ) or "无"
    held = "、".join(protagonist["held_item_ids"]) or "无"
    items = "、".join(
        f"{key}={value}" for key, value in scene["item_states"].items()
    ) or "无"
    return (
        f"时间={snapshot['game_time']}；男主位置={protagonist['location_label']}"
        f"（{protagonist['location_id']}）；男主活动={protagonist['activity']}；"
        f"身体={body}；持有物={held}；场景={scene['location_label']}；物品状态={items}"
    )


def _heroine_state_summary(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    return (
        f"形态={state['form']}；身体={state['body']}；情绪={state['emotion']}；"
        f"活动={state['current_activity']}；意图={state['immediate_intent']}；"
        f"关系={state['relationship_stage']}；信任={state['trust']}"
    )


def _inferred_failure_mode(attempt: dict[str, Any]) -> str:
    calls = attempt.get("model_calls", [])
    if len(calls) >= 2 and calls[-1]["mode"] == calls[-2]["mode"]:
        return str(calls[-1]["mode"])
    return "无法从调用尾部确定"


def _call_sequence(attempt: dict[str, Any]) -> str:
    return " -> ".join(
        f"{call['mode']}[{int(bool(call.get('ok')))}]" for call in attempt["model_calls"]
    )


def _judge_for_turn(attempt: dict[str, Any], turn_id: str) -> dict[str, Any] | None:
    for judged in attempt.get("judged_turns", []):
        if judged["turn_id"] == turn_id:
            return judged
    return None


def _render_turn(turn: dict[str, Any], attempt: dict[str, Any]) -> list[str]:
    lines = [
        f"##### 轮次 `{turn['turn_id']}`",
        "",
        f"- 玩家对白：{turn['user_text']}",
        f"- 世界事实：{_snapshot_summary(turn.get('world_snapshot'))}",
    ]
    heroine = _heroine_state_summary(turn.get("initial_heroine_state"))
    if heroine is not None:
        lines.append(f"- 初始女主状态：{heroine}")
    memories = turn.get("memory_evidence", [])
    lines.append(f"- 记忆证据：{'；'.join(memories) if memories else '无'}。")
    oracle = turn["oracle"]
    lines.extend(_bullet_values("已知事实", oracle["known_facts"]))
    lines.extend(_bullet_values("必须做到", oracle["required_behaviors"]))
    lines.extend(_bullet_values("禁止主张", oracle["forbidden_claims"]))
    lines.append(f"- Runtime 结果：`{turn['runtime_result']}`。")
    if turn.get("failure_code"):
        lines.append(f"- Runtime 失败码：`{turn['failure_code']}`。")
    lines.append("- 白未晞实际回复：")
    lines.extend(_code_block(turn.get("response")))
    judged = _judge_for_turn(attempt, str(turn["turn_id"]))
    if judged is not None:
        deterministic = judged["deterministic"]
        semantic = judged["semantic_judge"]
        missing = deterministic["missing_required_term_groups"]
        forbidden = deterministic["forbidden_term_hits"]
        lines.extend(
            [
                f"- 精确词表：`{'pass' if deterministic['passed'] else 'fail'}`；"
                f"缺失组={json.dumps(missing, ensure_ascii=False)}；"
                f"禁止词命中={json.dumps(forbidden, ensure_ascii=False)}。",
                f"- 自动 Judge：`{semantic['decision']}`；理由：{semantic['reason']}",
            ]
        )
    lines.append("")
    return lines


def _render_attempt(attempt: dict[str, Any]) -> list[str]:
    lines = [
        f"#### 种子 `{attempt['seed']}`",
        "",
        f"- 尝试结果：`{attempt['decision']}`；候选生成完成：`{str(attempt['success']).lower()}`。",
    ]
    if not attempt["success"]:
        lines.extend(
            [
                f"- 尝试失败码：`{attempt.get('failure_code')}`。",
                f"- 最终失败模式：`{_inferred_failure_mode(attempt)}`。",
                f"- 模型调用顺序：`{_call_sequence(attempt)}`。",
                "- 说明：调用记录中的 `[1]` 表示模型后端返回成功，不代表返回内容已通过 JSON 解析和 Runtime 合同校验。",
            ]
        )
    lines.append("")
    for turn in attempt["turn_results"]:
        lines.extend(_render_turn(turn, attempt))
    return lines


def _render_case(index: int, item: dict[str, Any]) -> list[str]:
    review = item["manual_review"]
    lines = [
        f"### {index}. `{item['case_id']}`",
        "",
        f"- 风险：`{RISK_LABELS[item['risk']]}`。",
        f"- 分类：`{item['category']}`。",
        f"- 评测层：`{item['evaluation_layer']}`。",
        f"- 最终归因：`{ATTRIBUTION_LABELS[review['final_attribution']]}`。",
        f"- 逐案裁决理由：{review['reason']}",
        "",
    ]
    for attempt in item["attempts"]:
        lines.extend(_render_attempt(attempt))
    return lines


def render(run_dir: Path, output_path: Path) -> dict[str, object]:
    queue_path = run_dir / "manual_review_queue_adjudicated.jsonl"
    queue = _load_jsonl(queue_path)
    failed = [
        item
        for item in queue
        if item["manual_review"]["final_decision"] == "fail"
    ]
    failed.sort(
        key=lambda item: (
            0 if item["manual_review"]["final_attribution"] == "character_failure" else 1,
            item["category"],
            item["case_id"],
        )
    )
    attribution_counts = Counter(
        item["manual_review"]["final_attribution"] for item in failed
    )
    risk_counts = Counter(item["risk"] for item in failed)
    lines = [
        "# WMR-08 白未晞全部未通过案例审核稿",
        "",
        "> 日期：2026-08-11  ",
        "> Run：`baiweixi-quality-formal-20260811`  ",
        "> 审核范围：逐案裁决后仍未通过的全部 43 个自动案例  ",
        "> 结论状态：待项目审核人确认",
        "",
        "## 1. 审核说明",
        "",
        "本文档不修改冻结案例和模型原始输出。每个案例完整列出冻结要求、三个种子的回复或结构失败、自动评测信号及逐案裁决理由。",
        "",
        "审核人重点确认：",
        "",
        "1. 角色失败是否确实违反正典、知识边界、关系节奏、能力限制或基本相关性。",
        "2. 联合失败是否应继续维持联合归因，还是已有证据可进一步拆为模型输出协议或 Runtime 解析问题。",
        "3. 是否同意任一冻结种子实质失败即判 blocker/important 案例失败。",
        "",
        "## 2. 汇总",
        "",
        f"- 未通过案例：`{len(failed)}`。",
        f"- 角色失败：`{attribution_counts['character_failure']}`。",
        f"- 联合失败：`{attribution_counts['joint_or_ambiguous']}`。",
        f"- Blocker：`{risk_counts['blocker']}`。",
        f"- Important：`{risk_counts['important']}`。",
        "- 系统失败：`0`。",
        "- 剩余评测歧义：`0`。",
        "",
        "## 3. 案例索引",
        "",
        "| 序号 | 案例 | 风险 | 分类 | 归因 |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(failed, 1):
        review = item["manual_review"]
        lines.append(
            f"| {index} | `{item['case_id']}` | {RISK_LABELS[item['risk']]} | "
            f"`{item['category']}` | {ATTRIBUTION_LABELS[review['final_attribution']]} |"
        )
    character = [
        item for item in failed if item["manual_review"]["final_attribution"] == "character_failure"
    ]
    joint = [
        item for item in failed if item["manual_review"]["final_attribution"] == "joint_or_ambiguous"
    ]
    lines.extend(["", "## 4. 角色失败明细", ""])
    for index, item in enumerate(character, 1):
        lines.extend(_render_case(index, item))
    lines.extend(["", "## 5. 联合失败明细", ""])
    for index, item in enumerate(joint, len(character) + 1):
        lines.extend(_render_case(index, item))
    lines.extend(
        [
            "",
            "## 6. 审核签署区",
            "",
            "- 审核结论：待填写。",
            "- 退回重判案例：待填写。",
            "- 同意进入模型或训练数据修正的角色失败：待填写。",
            "- 同意进入结构化协议复现的联合失败：待填写。",
            "- 审核人：待填写。",
            "- 审核日期：待填写。",
            "",
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    summary = {
        "output_path": str(output_path),
        "failed_cases": len(failed),
        "character_failures": attribution_counts["character_failure"],
        "joint_failures": attribution_counts["joint_or_ambiguous"],
        "blockers": risk_counts["blocker"],
        "important": risk_counts["important"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render all adjudicated Bai Weixi failures as a reviewer-facing Markdown document."
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    render(args.run_dir.resolve(), args.output.resolve())
