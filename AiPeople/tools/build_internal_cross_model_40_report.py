from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUITE_ROOT = ROOT / "eval" / "baiweixi_quality"
EXPERIMENT_ROOT = ROOT / "eval" / "cross_model_internal_40"
INPUT_PATH = EXPERIMENT_ROOT / "candidate_inputs_v1.jsonl"
LOCAL_PATH = EXPERIMENT_ROOT / "local_baiweixi_seed42.jsonl"
GPT_PATH = EXPERIMENT_ROOT / "gpt5_6_sol_internal.jsonl"
ADJUDICATION_PATH = EXPERIMENT_ROOT / "manual_adjudication_v1.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _case_map() -> dict[str, dict[str, Any]]:
    cases = _read_jsonl(SUITE_ROOT / "cases" / "frozen_single_v1.jsonl")
    return {str(case["case_id"]): case for case in cases}


def _candidate_map(path: Path, *, model: str) -> dict[str, dict[str, Any]]:
    rows = _read_jsonl(path)
    if len(rows) != 40:
        raise RuntimeError(f"{path.name} must contain 40 rows, got {len(rows)}")
    values: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = str(row.get("case_id", ""))
        if not case_id or case_id in values:
            raise RuntimeError(f"invalid or duplicate case_id in {path.name}: {case_id}")
        if str(row.get("model")) != model:
            raise RuntimeError(f"model mismatch in {path.name}: {row.get('model')}")
        if not str(row.get("response", "")).strip():
            raise RuntimeError(f"empty response in {path.name}: {case_id}")
        values[case_id] = row
    return values


def _strict_signals(response: str, oracle: dict[str, Any]) -> dict[str, object]:
    missing_groups = [
        group
        for group in oracle["required_term_groups"]
        if not any(str(term) in response for term in group)
    ]
    forbidden_hits = [
        str(term) for term in oracle["forbidden_terms"] if str(term) in response
    ]
    return {
        "passed": not missing_groups and not forbidden_hits,
        "missing_required_term_groups": missing_groups,
        "forbidden_term_hits": forbidden_hits,
    }


def _validate_alignment(
    inputs: list[dict[str, Any]],
    local: dict[str, dict[str, Any]],
    gpt: dict[str, dict[str, Any]],
) -> None:
    expected = [str(item["case_id"]) for item in inputs]
    if len(expected) != 40 or len(set(expected)) != 40:
        raise RuntimeError("candidate input must contain 40 unique cases")
    if set(expected) != set(local) or set(expected) != set(gpt):
        raise RuntimeError("candidate output coverage does not match the frozen input")
    for item in inputs:
        case_id = str(item["case_id"])
        ordinal = int(item["ordinal"])
        if int(local[case_id]["ordinal"]) != ordinal or int(gpt[case_id]["ordinal"]) != ordinal:
            raise RuntimeError(f"ordinal mismatch: {case_id}")


def _write_review_packet(
    inputs: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    local: dict[str, dict[str, Any]],
    gpt: dict[str, dict[str, Any]],
) -> None:
    lines = [
        "# 白未晞本地模型 vs GPT-5.6 Sol 四十题语义审核包",
        "",
        "> 候选生成时均未读取本文件中的 Oracle。严格词表只作为风险信号，最终判断必须阅读语义。",
        "",
    ]
    for item in inputs:
        case_id = str(item["case_id"])
        case = cases[case_id]
        turn = case["turns"][0]
        oracle = turn["oracle"]
        local_response = str(local[case_id]["response"])
        gpt_response = str(gpt[case_id]["response"])
        local_strict = _strict_signals(local_response, oracle)
        gpt_strict = _strict_signals(gpt_response, oracle)
        lines.extend(
            [
                f"## {item['ordinal']}. `{case_id}`",
                "",
                f"- 类别：`{case['category']}`；风险：`{case['risk']}`",
                f"- 问题：{turn['user_text']}",
                f"- 已知事实：{'；'.join(oracle['known_facts']) or '无'}",
                f"- 必须行为：{'；'.join(oracle['required_behaviors']) or '无'}",
                f"- 禁止主张：{'；'.join(oracle['forbidden_claims']) or '无'}",
                "",
                "**本地白未晞**",
                "",
                f"> {local_response}",
                "",
                f"严格信号：`{'pass' if local_strict['passed'] else 'fail'}`；缺词：`{json.dumps(local_strict['missing_required_term_groups'], ensure_ascii=False)}`；禁词：`{json.dumps(local_strict['forbidden_term_hits'], ensure_ascii=False)}`",
                "",
                "**GPT-5.6 Sol**",
                "",
                f"> {gpt_response}",
                "",
                f"严格信号：`{'pass' if gpt_strict['passed'] else 'fail'}`；缺词：`{json.dumps(gpt_strict['missing_required_term_groups'], ensure_ascii=False)}`；禁词：`{json.dumps(gpt_strict['forbidden_term_hits'], ensure_ascii=False)}`",
                "",
            ]
        )
    (EXPERIMENT_ROOT / "review_packet.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_dialogues_only(
    inputs: list[dict[str, Any]],
    local: dict[str, dict[str, Any]],
    gpt: dict[str, dict[str, Any]],
) -> None:
    lines = ["# 四十题双方对话", ""]
    for item in inputs:
        case_id = str(item["case_id"])
        lines.extend(
            [
                f"## {item['ordinal']}",
                "",
                f"**玩家**：{item['user_text']}",
                "",
                f"**本地白未晞**：{local[case_id]['response']}",
                "",
                f"**GPT-5.6 Sol**：{gpt[case_id]['response']}",
                "",
            ]
        )
    (EXPERIMENT_ROOT / "dialogues_only.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def _write_report(
    inputs: list[dict[str, Any]],
    cases: dict[str, dict[str, Any]],
    local: dict[str, dict[str, Any]],
    gpt: dict[str, dict[str, Any]],
) -> None:
    if not ADJUDICATION_PATH.is_file():
        return
    adjudication = json.loads(ADJUDICATION_PATH.read_text(encoding="utf-8"))
    decisions = adjudication["decisions"]
    by_case = {str(item["case_id"]): item for item in decisions}
    expected = {str(item["case_id"]) for item in inputs}
    if len(decisions) != 40 or set(by_case) != expected:
        raise RuntimeError("manual adjudication must cover the same 40 cases exactly")

    decision_counts = {
        "local": Counter(str(item["local_decision"]) for item in decisions),
        "gpt": Counter(str(item["gpt_decision"]) for item in decisions),
        "winner": Counter(str(item["winner"]) for item in decisions),
    }
    category_counts: dict[str, dict[str, Counter[str]]] = defaultdict(
        lambda: {"local": Counter(), "gpt": Counter(), "winner": Counter()}
    )
    for item in decisions:
        category = str(cases[str(item["case_id"])]["category"])
        category_counts[category]["local"][str(item["local_decision"])] += 1
        category_counts[category]["gpt"][str(item["gpt_decision"])] += 1
        category_counts[category]["winner"][str(item["winner"])] += 1

    local_strict_pass = 0
    gpt_strict_pass = 0
    for item in inputs:
        case_id = str(item["case_id"])
        oracle = cases[case_id]["turns"][0]["oracle"]
        local_strict_pass += bool(_strict_signals(str(local[case_id]["response"]), oracle)["passed"])
        gpt_strict_pass += bool(_strict_signals(str(gpt[case_id]["response"]), oracle)["passed"])

    lines = [
        "# 白未晞本地模型 vs GPT-5.6 Sol 内部四十题对比报告",
        "",
        "> 性质：Codex 内部临时 Lane R 质量对比，不是 OpenAI API 或 V6 五模式验收。  ",
        f"> 逐题语义裁决：`{adjudication['reviewer']}`；日期：`{adjudication['reviewed_at']}`。",
        "",
        "## 1. 总结果",
        "",
        "| 指标 | 本地白未晞 | GPT-5.6 Sol |",
        "|---|---:|---:|",
        f"| 逐题语义通过 | {decision_counts['local']['pass']}/40 | {decision_counts['gpt']['pass']}/40 |",
        f"| 逐题语义失败 | {decision_counts['local']['fail']}/40 | {decision_counts['gpt']['fail']}/40 |",
        f"| 逐题语义歧义 | {decision_counts['local']['ambiguous']}/40 | {decision_counts['gpt']['ambiguous']}/40 |",
        f"| 严格词表通过（仅信号） | {local_strict_pass}/40 | {gpt_strict_pass}/40 |",
        "",
        f"逐题胜负：本地胜 `{decision_counts['winner']['local']}`，GPT 胜 `{decision_counts['winner']['gpt']}`，平局 `{decision_counts['winner']['tie']}`，双方失败 `{decision_counts['winner']['both_fail']}`，无法裁决 `{decision_counts['winner']['unclear']}`。",
        "",
        "## 2. 分类别",
        "",
        "| 类别 | 题数 | 本地通过 | GPT通过 | 本地胜 | GPT胜 | 平局 | 双方失败 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for category in sorted(category_counts):
        counts = category_counts[category]
        total = sum(counts["local"].values())
        lines.append(
            f"| `{category}` | {total} | {counts['local']['pass']} | {counts['gpt']['pass']} | {counts['winner']['local']} | {counts['winner']['gpt']} | {counts['winner']['tie']} | {counts['winner']['both_fail']} |"
        )
    lines.extend(["", "## 3. 审核结论", ""])
    lines.extend(f"- {finding}" for finding in adjudication["findings"])
    lines.extend(
        [
            "",
            "## 4. 逐题裁决",
            "",
            "| # | 案例 | 本地 | GPT | 胜者 | 理由 |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for item in inputs:
        case_id = str(item["case_id"])
        decision = by_case[case_id]
        reason = str(decision["reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {item['ordinal']} | `{case_id}` | `{decision['local_decision']}` | `{decision['gpt_decision']}` | `{decision['winner']}` | {reason} |"
        )
    lines.extend(
        [
            "",
            "## 5. 边界",
            "",
            "- GPT 候选来自 Codex 内部代理，不代表正式 OpenAI API 请求结果。",
            "- 本轮只比较独立角色直答题，不包含 V6 世界状态链、结构化五模式、长期记忆或多轮轨迹。",
            "- GPT 没有可控 seed，也没有可比较的 API token、费用和供应商延迟数据。",
            "- 严格词表存在同义词和否定句误判，只保留为可复现信号，逐题语义裁决是本报告主结果。",
            "",
            "## 6. 工件",
            "",
            "- 选择合同：`eval/cross_model_internal_40/selection_manifest_v1.json`",
            "- 共享角色 Prompt：`eval/cross_model_internal_40/shared_character_prompt_v1.txt`",
            "- 去 Oracle 输入：`eval/cross_model_internal_40/candidate_inputs_v1.jsonl`",
            "- 本地答案：`eval/cross_model_internal_40/local_baiweixi_seed42.jsonl`",
            "- GPT 答案：`eval/cross_model_internal_40/gpt5_6_sol_internal.jsonl`",
            "- 语义审核包：`eval/cross_model_internal_40/review_packet.md`",
            "- 人工裁决：`eval/cross_model_internal_40/manual_adjudication_v1.json`",
        ]
    )
    (EXPERIMENT_ROOT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _finalize_manifest() -> None:
    if not ADJUDICATION_PATH.is_file():
        return
    manifest_path = EXPERIMENT_ROOT / "selection_manifest_v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report_path = EXPERIMENT_ROOT / "report.md"
    review_path = EXPERIMENT_ROOT / "review_packet.md"
    dialogues_path = EXPERIMENT_ROOT / "dialogues_only.md"
    manifest["status"] = "completed"
    manifest["candidates"]["gpt"].update(
        {
            "reasoning_effort": "low",
            "responses_sha256": _sha256(GPT_PATH),
        }
    )
    manifest["evaluation"] = {
        "method": "strict term signals plus per-case semantic adjudication",
        "review_packet_path": review_path.relative_to(ROOT).as_posix(),
        "review_packet_sha256": _sha256(review_path),
        "dialogues_only_path": dialogues_path.relative_to(ROOT).as_posix(),
        "dialogues_only_sha256": _sha256(dialogues_path),
        "adjudication_path": ADJUDICATION_PATH.relative_to(ROOT).as_posix(),
        "adjudication_sha256": _sha256(ADJUDICATION_PATH),
        "report_path": report_path.relative_to(ROOT).as_posix(),
        "report_sha256": _sha256(report_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    inputs = _read_jsonl(INPUT_PATH)
    cases = _case_map()
    local = _candidate_map(LOCAL_PATH, model="ollama/baiweixi:latest")
    gpt = _candidate_map(GPT_PATH, model="gpt-5.6-sol")
    _validate_alignment(inputs, local, gpt)
    _write_dialogues_only(inputs, local, gpt)
    _write_review_packet(inputs, cases, local, gpt)
    _write_report(inputs, cases, local, gpt)
    _finalize_manifest()
    print(
        json.dumps(
            {
                "cases": len(inputs),
                "review_packet": str(EXPERIMENT_ROOT / "review_packet.md"),
                "report_written": ADJUDICATION_PATH.is_file(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
