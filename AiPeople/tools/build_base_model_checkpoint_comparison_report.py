from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path(os.environ.get("AIPEOPLE_MODEL_ROOT", "F:/AiPeople"))
TRAINING_PACKAGE_ROOT = (
    ROOT / "training_packages" / "training_package_baiweixi_local3060_qwen3"
)
EXPERIMENT_ROOT = ROOT / "eval" / "base_model_checkpoint_comparison"
RUN_ROOT = EXPERIMENT_ROOT / "runs"
FORMAL_RUN = (
    ROOT
    / "eval"
    / "baiweixi_quality"
    / "runs"
    / "baiweixi-quality-formal-20260811"
)

RUNS = {
    "base_q5": RUN_ROOT / "qwen3-4b-base-q5",
    "checkpoint_200_q5": RUN_ROOT / "baiweixi-checkpoint-200-q5",
    "checkpoint_298_f16": RUN_ROOT / "baiweixi-checkpoint-298-f16-character-direct",
    "final_q5": FORMAL_RUN,
}

LABELS = {
    "base_q5": "原始 Qwen3-4B Q5",
    "checkpoint_200_q5": "checkpoint-200 Q5",
    "checkpoint_298_f16": "checkpoint-298 F16",
    "final_q5": "当前合并 Q5",
}

TRAIN_CONFIG = (
    TRAINING_PACKAGE_ROOT
    / "configs"
    / "baiweixi_4b_8g.yaml"
)
TRAIN_DATA = (
    TRAINING_PACKAGE_ROOT
    / "data"
    / "baiweixi_ready.jsonl"
)
MANUAL_ADJUDICATION = FORMAL_RUN / "manual_adjudication_v1.json"
PAIRED_TRANSITION_ADJUDICATION = (
    EXPERIMENT_ROOT / "paired_transition_adjudication_v1.json"
)
MODELFILE_ROOT = EXPERIMENT_ROOT / "modelfiles"
FINAL_ADAPTER = (
    MODEL_ROOT
    / "training_package_baiweixi_local3060_qwen3"
    / "outputs"
    / "baiweixi_4b"
    / "adapter_model.safetensors"
)
CHECKPOINT_298_ADAPTER = FINAL_ADAPTER.parent / "checkpoint-298" / FINAL_ADAPTER.name


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_modelfile(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("FROM "):
        raise RuntimeError(f"invalid Modelfile: {path}")
    return "\n".join(lines[1:]).strip()


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["case_id"]): item for item in report["case_results"]}


def _attempt_map(run_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        str(item["attempt_key"]): item
        for item in _read_jsonl(run_dir / "attempts_judged.jsonl")
    }


def _turn_signal_counts(attempts: dict[str, dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for attempt in attempts.values():
        if not attempt.get("success"):
            counts["execution_failure"] += 1
        for turn in attempt.get("judged_turns", []):
            deterministic = "pass" if turn["deterministic"]["passed"] else "fail"
            semantic = str(turn["semantic_judge"]["decision"])
            counts[f"deterministic_{deterministic}"] += 1
            counts[f"semantic_{semantic}"] += 1
            counts[f"pair_{deterministic}_{semantic}"] += 1
    return dict(counts)


def _transition(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    *,
    layer: str | None = None,
) -> dict[str, Any]:
    shared = sorted(set(left) & set(right))
    if layer is not None:
        shared = [
            case_id
            for case_id in shared
            if left[case_id]["evaluation_layer"] == layer
            and right[case_id]["evaluation_layer"] == layer
        ]
    matrix: Counter[str] = Counter()
    regressions: list[str] = []
    gains: list[str] = []
    for case_id in shared:
        left_decision = str(left[case_id]["decision"])
        right_decision = str(right[case_id]["decision"])
        matrix[f"{left_decision}_to_{right_decision}"] += 1
        if left_decision == "pass" and right_decision != "pass":
            regressions.append(case_id)
        elif left_decision != "pass" and right_decision == "pass":
            gains.append(case_id)
    return {
        "shared_cases": len(shared),
        "matrix": dict(matrix),
        "net_pass_change": len(gains) - len(regressions),
        "regressions": regressions,
        "gains": gains,
    }


def _execution_failure_cases(
    case_ids: list[str],
    attempts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    selected = [
        attempt
        for attempt in attempts.values()
        if str(attempt["case_id"]) in case_ids and not attempt.get("success")
    ]
    return {
        "cases": sorted({str(attempt["case_id"]) for attempt in selected}),
        "attempts": len(selected),
    }


def _paired_adjudication_summary(
    transitions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    adjudication = _read_json(PAIRED_TRANSITION_ADJUDICATION)
    summaries: dict[str, Any] = {}
    for name, comparison in adjudication["comparisons"].items():
        rows = comparison["adjudications"]
        reviewed_ids = {str(item["case_id"]) for item in rows}
        transition = transitions[name]
        changed_ids = set(transition["regressions"]) | set(transition["gains"])
        if reviewed_ids != changed_ids:
            raise RuntimeError(f"paired adjudication does not cover {name} exactly")
        counts = Counter(str(item["verdict"]) for item in rows)
        summaries[name] = {
            "left": comparison["left"],
            "right": comparison["right"],
            "reviewed_cases": len(rows),
            "verdict_counts": dict(counts),
            "adjudications": rows,
        }
    return {
        "review_type": adjudication["review_type"],
        "reviewer": adjudication["reviewer"],
        "reviewed_at": adjudication["reviewed_at"],
        "comparisons": summaries,
        "scope_warning": adjudication["policy"][-1],
    }


def _category_direct_counts(report: dict[str, Any]) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = defaultdict(Counter)
    for item in report["case_results"]:
        if item["evaluation_layer"] != "character_direct":
            continue
        grouped[str(item["category"])][str(item["decision"])] += 1
    return {
        category: {
            "cases": sum(counts.values()),
            "pass": counts.get("pass", 0),
            "fail": counts.get("fail", 0),
            "ambiguous": counts.get("ambiguous", 0),
        }
        for category, counts in sorted(grouped.items())
    }


def _training_data_audit() -> dict[str, Any]:
    rows = _read_jsonl(TRAIN_DATA)
    primary_mode_terms = (
        "GAME_REPLY",
        "TURN_MIND_ADVANCE",
        "WORLD_CONTINUITY_REVIEW",
        "POST_REPLY_WORLD_MIND_RECONCILE",
        "FIVE_MINUTE_WORLD_MIND_RECONCILE",
    )
    memory_mode_terms = (
        "MEMORY_PROPOSE",
    )
    stale_terms = ("设备外", "现实事件", "只能通过文字", "不要自称AI")
    mode_hits: Counter[str] = Counter()
    stale_hits: Counter[str] = Counter()
    assistant_messages = 0
    for row in rows:
        serialized = json.dumps(row, ensure_ascii=False)
        for term in primary_mode_terms + memory_mode_terms:
            mode_hits[term] += serialized.count(term)
        for term in stale_terms:
            stale_hits[term] += serialized.count(term)
        assistant_messages += sum(
            message.get("from") == "gpt"
            for message in row.get("conversations", [])
        )
    return {
        "rows": len(rows),
        "assistant_messages": assistant_messages,
        "v6_primary_mode_term_hits": {
            term: mode_hits[term] for term in primary_mode_terms
        },
        "memory_mode_term_hits": {
            term: mode_hits[term] for term in memory_mode_terms
        },
        "stale_prompt_term_hits": dict(stale_hits),
        "config_path": str(TRAIN_CONFIG.relative_to(ROOT)).replace("\\", "/"),
        "data_path": str(TRAIN_DATA.relative_to(ROOT)).replace("\\", "/"),
    }


def build_analysis() -> dict[str, Any]:
    reports = {name: _read_json(path / "report.json") for name, path in RUNS.items()}
    attempts = {name: _attempt_map(path) for name, path in RUNS.items()}
    case_maps = {name: _case_map(report) for name, report in reports.items()}
    manifests = {str(report["suite"]["manifest_sha256"]) for report in reports.values()}
    if len(manifests) != 1:
        raise RuntimeError("comparison runs do not share one frozen suite manifest")

    direct_sets = {
        name: {
            case_id
            for case_id, item in case_maps[name].items()
            if item["evaluation_layer"] == "character_direct"
        }
        for name in RUNS
    }
    if len({frozenset(values) for values in direct_sets.values()}) != 1:
        raise RuntimeError("character-direct case sets are not aligned")

    judge_digests = {
        str(report["semantic_judge"]["ollama"]["digest"])
        for report in reports.values()
    }
    if len(judge_digests) != 1:
        raise RuntimeError("comparison runs do not share one semantic Judge")

    normalized_modelfiles = {
        _normalized_modelfile(path)
        for path in MODELFILE_ROOT.glob("*.Modelfile")
    }
    if len(normalized_modelfiles) != 1:
        raise RuntimeError("comparison Modelfiles differ beyond their FROM artifact")

    final_adapter_sha256 = _sha256(FINAL_ADAPTER)
    checkpoint_298_adapter_sha256 = _sha256(CHECKPOINT_298_ADAPTER)
    if final_adapter_sha256 != checkpoint_298_adapter_sha256:
        raise RuntimeError("checkpoint-298 and final adapters differ")

    manual = _read_json(MANUAL_ADJUDICATION)
    run_summaries: dict[str, Any] = {}
    for name, report in reports.items():
        run_summaries[name] = {
            "label": LABELS[name],
            "coverage_cases": int(report["suite"]["automatic_cases"]),
            "automatic_pass": int(report["decision_counts"].get("pass", 0)),
            "automatic_fail": int(report["decision_counts"].get("fail", 0)),
            "layers": report["evaluation_layer_results"],
            "execution_stability": report["execution_stability"],
            "turn_signals": _turn_signal_counts(attempts[name]),
            "direct_categories": _category_direct_counts(report),
            "artifact_path": report["candidate_model"]["artifact_path"],
            "artifact_sha256": report["candidate_model"]["artifact_sha256"],
            "ollama_digest": report["candidate_model"]["ollama"]["digest"],
            "quantization": report["candidate_model"]["ollama"]["details"][
                "quantization_level"
            ],
        }

    run_summaries["final_q5"]["human_adjusted"] = manual["summary"]
    transitions = {
        "base_q5_to_checkpoint_200_q5_full": _transition(
            case_maps["base_q5"], case_maps["checkpoint_200_q5"]
        ),
        "base_q5_to_checkpoint_200_q5_direct": _transition(
            case_maps["base_q5"],
            case_maps["checkpoint_200_q5"],
            layer="character_direct",
        ),
        "checkpoint_200_q5_to_checkpoint_298_f16_direct": _transition(
            case_maps["checkpoint_200_q5"],
            case_maps["checkpoint_298_f16"],
            layer="character_direct",
        ),
        "checkpoint_298_f16_to_final_q5_direct": _transition(
            case_maps["checkpoint_298_f16"],
            case_maps["final_q5"],
            layer="character_direct",
        ),
    }
    for name, right_name in (
        ("base_q5_to_checkpoint_200_q5_full", "checkpoint_200_q5"),
        ("base_q5_to_checkpoint_200_q5_direct", "checkpoint_200_q5"),
        ("checkpoint_200_q5_to_checkpoint_298_f16_direct", "checkpoint_298_f16"),
        ("checkpoint_298_f16_to_final_q5_direct", "final_q5"),
    ):
        transitions[name]["right_execution_failures_in_regressions"] = (
            _execution_failure_cases(
                transitions[name]["regressions"], attempts[right_name]
            )
        )
    return {
        "schema_version": 1,
        "suite_manifest_sha256": manifests.pop(),
        "comparison_contract": {
            "same_character_direct_cases": len(next(iter(direct_sets.values()))),
            "same_modelfile_prompt_and_parameters": True,
            "same_seed_set_and_generation_options": True,
            "same_semantic_judge": True,
            "checkpoint_298_adapter_equals_final_adapter": True,
            "checkpoint_298_adapter_sha256": checkpoint_298_adapter_sha256,
            "final_adapter_sha256": final_adapter_sha256,
            "checkpoint_298_full_suite_not_run": (
                "F16 used about 7592 MiB and exceeded the product limit of 5120 MiB; "
                "the aligned 61-case character-direct subset completed."
            ),
        },
        "runs": run_summaries,
        "transitions": transitions,
        "paired_transition_adjudication": _paired_adjudication_summary(transitions),
        "training_data_audit": _training_data_audit(),
        "limitations": [
            "Automatic case decisions require every frozen seed to pass both exact-term checks and the local semantic Judge.",
            "Exact-term checks reject valid synonyms and can misread negation; the local Judge also has known false decisions.",
            "Only the current final Q5 run has a completed human adjudication overlay, so its 46/89 adjusted result cannot be compared directly with unreviewed runs.",
            "The checkpoint-200 to checkpoint-298 comparison changes both training step and precision, so only checkpoint-298 F16 versus final Q5 isolates quantization.",
        ],
    }


def _format_transition(value: dict[str, Any]) -> str:
    matrix = value["matrix"]
    return (
        f"回退 `{matrix.get('pass_to_fail', 0) + matrix.get('pass_to_ambiguous', 0)}`，"
        f"新增通过 `{matrix.get('fail_to_pass', 0) + matrix.get('ambiguous_to_pass', 0)}`，"
        f"净变化 `{value['net_pass_change']:+d}`"
    )


def _format_case_ids(case_ids: list[str]) -> str:
    return "、".join(f"`{case_id}`" for case_id in case_ids) or "无"


def render_report(analysis: dict[str, Any]) -> str:
    runs = analysis["runs"]
    transitions = analysis["transitions"]
    audit = analysis["training_data_audit"]
    paired = analysis["paired_transition_adjudication"]["comparisons"]
    base_cp200_review = paired["base_q5_to_checkpoint_200_q5_direct"]
    cp200_cp298_review = paired[
        "checkpoint_200_q5_to_checkpoint_298_f16_direct"
    ]
    f16_q5_review = paired["checkpoint_298_f16_to_final_q5_direct"]
    protocol_regressions = transitions["base_q5_to_checkpoint_200_q5_full"][
        "right_execution_failures_in_regressions"
    ]
    lines = [
        "# 白未晞基础模型与训练检查点冻结测试对比",
        "",
        "> 日期：2026-08-12  ",
        "> 性质：模型升级决策实验；使用同一冻结质量集、同一 Prompt、同一 seed 与同一自动 Judge。  ",
        "> 结论口径：确定性规则、语义 Judge、结构协议稳定性分别观察；自动总分不等于人工最终裁决。",
        "",
        "## 1. 决策结论",
        "",
        "1. **当前训练方案不能被判定为升级，也不应继续追加步数。** 原始 Qwen3-4B Q5 在完整 89 题机器初审为 `50/89`，checkpoint-200 为 `29/89`，最终 Q5 为 `30/89`；但角色词表与本地 Judge 有明显误判，因此这些数字只用于筛查。",
        f"2. **确定性最强的退化是 V6 结构协议。** 原始基座执行成功率为 `238/245`，checkpoint-200 为 `216/245`，最终 Q5 为 `224/245`；Base→CP200 的 22 个自动回退案例中，有 {len(protocol_regressions['cases'])} 个非直答案例实际发生结构生成失败，共 {protocol_regressions['attempts']} 个失败尝试。",
        "3. **目前不能宣称 Base 的角色语义全面优于微调模型。** Base→CP200 的全部 9 个直答变化经人工配对复核后，Base 更好 4 个、CP200 更好 4 个、混合 1 个；自动净下降 7 题主要混入了词表和 Judge 噪声。",
        "4. **F16→Q5 的量化影响是混合的，不是自动分显示的单向下降。** 7 个变化案例人工复核为 F16 更好 3 个、Q5 更好 2 个、混合 2 个；F16 同时实测约 `7592 MiB`，超过产品 `<5120 MiB` 限制，不能作为发布解法。",
        "5. **下一版仍应回到原始 Qwen3-4B 作为工程基线，重做数据混合与训练方法。** 原因是当前训练明确损害结构协议且数据合同已过期，不是因为现有自动角色总分足以证明 Base 人格更好。",
        "",
        "## 2. 实验对齐",
        "",
        f"- 冻结集 manifest：`{analysis['suite_manifest_sha256']}`。",
        "- 四组使用相同 Ollama TEMPLATE、SYSTEM、stop 参数；案例使用相同生成参数和 seed；语义 Judge 相同。",
        "- 原始基座、checkpoint-200、最终 Q5 完整执行 89 题；checkpoint-298 F16 完成与其他组严格对齐的 61 个 `character_direct` 案例。",
        "- checkpoint-298 adapter 与最终导出 adapter 的 SHA256 相同，因此二者差异用于观察 F16 到 Q5 的量化影响。",
        "",
        "## 3. 完整 89 题",
        "",
        "| 模型阶段 | 自动通过 | 角色直答 | V6 多轮 | V6 Runtime | 成功尝试 | 协议失败 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("base_q5", "checkpoint_200_q5", "final_q5"):
        run = runs[name]
        stability = run["execution_stability"]
        lines.append(
            f"| {run['label']} | {run['automatic_pass']}/{run['coverage_cases']} | "
            f"{run['layers']['character_direct']['pass']}/61 | "
            f"{run['layers']['v6_multiturn']['pass']}/10 | "
            f"{run['layers']['v6_runtime']['pass']}/18 | "
            f"{stability['successful_attempts']}/245 | {stability['failed_attempts']} |"
        )
    lines.extend(
        [
            "",
            f"Base Q5 → checkpoint-200 Q5：{_format_transition(transitions['base_q5_to_checkpoint_200_q5_full'])}。这是同量化等级下最干净的前 200 步机器筛查结果。",
            "",
            f"其中 `{len(protocol_regressions['cases'])}` 个回退案例包含 CP200 结构执行失败，共 `{protocol_regressions['attempts']}` 个失败尝试；这部分不依赖角色关键词裁判。",
            "",
            "当前最终 Q5 的人工复核结果为 `46/89`，其中机器失败中恢复 `16` 题；但其他三组没有同口径人工复核，故本报告不拿 `46/89` 与其他自动分数直接排名。",
            "",
            "## 4. 角色直答 61 题",
            "",
            "| 模型阶段 | 自动通过 | identity | world | unknown | relation | ability | single-world | free |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    category_order = (
        "identity_canon",
        "world_canon",
        "unknown_boundaries",
        "relationship_pacing",
        "ability_limits",
        "single_world",
        "free_dialogue",
    )
    for name in RUNS:
        run = runs[name]
        categories = run["direct_categories"]
        direct = run["layers"]["character_direct"]
        cells = [f"{categories[category]['pass']}/{categories[category]['cases']}" for category in category_order]
        lines.append(
            f"| {run['label']} | {direct['pass']}/61 | " + " | ".join(cells) + " |"
        )
    lines.extend(
        [
            "",
            f"- Base Q5 → checkpoint-200 Q5：{_format_transition(transitions['base_q5_to_checkpoint_200_q5_direct'])}。",
            f"- checkpoint-200 Q5 → checkpoint-298 F16：{_format_transition(transitions['checkpoint_200_q5_to_checkpoint_298_f16_direct'])}；这里同时改变训练步数和精度，只能视为趋势。",
            f"- checkpoint-298 F16 → 最终 Q5：{_format_transition(transitions['checkpoint_298_f16_to_final_q5_direct'])}；adapter 相同，主要变量是量化。",
            "",
            "### 变化案例人工配对复核",
            "",
            "| 相邻阶段 | 复核案例 | 左侧更好 | 右侧更好 | 混合/评测噪声 |",
            "|---|---:|---:|---:|---:|",
            f"| Base Q5 → checkpoint-200 Q5 | {base_cp200_review['reviewed_cases']} | {base_cp200_review['verdict_counts'].get('left_better', 0)} | {base_cp200_review['verdict_counts'].get('right_better', 0)} | {base_cp200_review['verdict_counts'].get('mixed_or_evaluator_artifact', 0)} |",
            f"| checkpoint-200 Q5 → checkpoint-298 F16 | {cp200_cp298_review['reviewed_cases']} | {cp200_cp298_review['verdict_counts'].get('left_better', 0)} | {cp200_cp298_review['verdict_counts'].get('right_better', 0)} | {cp200_cp298_review['verdict_counts'].get('mixed_or_evaluator_artifact', 0)} |",
            f"| checkpoint-298 F16 → 最终 Q5 | {f16_q5_review['reviewed_cases']} | {f16_q5_review['verdict_counts'].get('left_better', 0)} | {f16_q5_review['verdict_counts'].get('right_better', 0)} | {f16_q5_review['verdict_counts'].get('mixed_or_evaluator_artifact', 0)} |",
            "",
            "这张表只复核自动决定发生变化的案例，不是完整 61 题人工排名。它证明自动迁移方向不能直接解释为语义质量方向。",
            "",
            "### 量化回退案例",
            "",
            _format_case_ids(transitions["checkpoint_298_f16_to_final_q5_direct"]["regressions"]),
            "",
            "### 量化新增通过案例",
            "",
            _format_case_ids(transitions["checkpoint_298_f16_to_final_q5_direct"]["gains"]),
            "",
            "## 5. 协议稳定性",
            "",
            "| 模型阶段 | GAME_REPLY | TURN_MIND_ADVANCE | WORLD_CONTINUITY_REVIEW | 后端调用错误 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for name in ("base_q5", "checkpoint_200_q5", "final_q5"):
        run = runs[name]
        modes = run["execution_stability"]["failure_modes"]
        lines.append(
            f"| {run['label']} | {modes.get('GAME_REPLY', 0)} | "
            f"{modes.get('TURN_MIND_ADVANCE', 0)} | "
            f"{modes.get('WORLD_CONTINUITY_REVIEW', 0)} | "
            f"{run['execution_stability']['backend_call_errors']} |"
        )
    lines.extend(
        [
            "",
            "所有失败调用的 Ollama 后端本身都返回成功；失败发生在模型连续两次无法满足结构化输出合同。因此这不是 Provider、显存或网络故障，而是训练后结构遵循能力退化。",
            "",
            "## 6. 训练数据审计",
            "",
            f"- 数据集共有 `{audit['rows']}` 条会话、`{audit['assistant_messages']}` 条 assistant 回复。训练配置明确把它描述为 REPLY 数据。",
            f"- V6 主干五模式标识命中：`{json.dumps(audit['v6_primary_mode_term_hits'], ensure_ascii=False)}`，全部为 `0`；模型没有接受这五类结构合同 SFT。",
            f"- 独立记忆模式标识命中：`{json.dumps(audit['memory_mode_term_hits'], ensure_ascii=False)}`，同样为 `0`。",
            f"- 已废弃提示仍高频存在：`{json.dumps(audit['stale_prompt_term_hits'], ensure_ascii=False)}`。这些内容与当前“女主只存在于单一游戏世界”的权威需求冲突。",
            "- 当前学习率为 `1e-4`、LoRA target 为 `all`、有效 batch 为 `4`、只训练一轮；在 1190 条窄域对白上，这一组合足以快速覆盖基座的通用指令与 JSON 遵循能力。",
            "",
            "## 7. 下一轮升级方案",
            "",
            "1. 以原始 Qwen3-4B 为唯一起点，不继续训练 checkpoint-298。",
            "2. 清理系统提示中的现实世界、设备、纯文字助手边界，改成当前单一游戏世界合同。",
            "3. 数据拆成自然对白、正典/未知边界、V6 五模式结构合同、抗遗忘通用指令四类，并做固定比例混合；不能再用纯 REPLY 单类数据覆盖全部训练。",
            "4. 学习率先降到 `1e-5` 至 `2e-5`，save/eval 间隔改为 20 至 25 step；每个检查点先跑小型哨兵集，协议或正典一旦下降立即早停。",
            "5. 候选通过哨兵后，再跑本次冻结 89 题；选择标准先看 V6 协议零退化，再看人工语义角色质量，最后比较 Q4/Q5/Q6 的显存与量化损失。",
            "",
            "## 8. 解释边界",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in analysis["limitations"])
    lines.extend(
        [
            "",
            "## 9. 工件",
            "",
            "- 机器可读汇总：`eval/base_model_checkpoint_comparison/analysis.json`",
            "- Base Q5：`eval/base_model_checkpoint_comparison/runs/qwen3-4b-base-q5/report.json`",
            "- checkpoint-200 Q5：`eval/base_model_checkpoint_comparison/runs/baiweixi-checkpoint-200-q5/report.json`",
            "- checkpoint-298 F16：`eval/base_model_checkpoint_comparison/runs/baiweixi-checkpoint-298-f16-character-direct/report.json`",
            "- 最终 Q5：`eval/baiweixi_quality/runs/baiweixi-quality-formal-20260811/report.json`",
            "- 最终 Q5 人工裁决：`eval/baiweixi_quality/runs/baiweixi-quality-formal-20260811/manual_adjudication_v1.json`",
            "- 相邻阶段变化案例人工复核：`eval/base_model_checkpoint_comparison/paired_transition_adjudication_v1.json`",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    analysis = build_analysis()
    (EXPERIMENT_ROOT / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (EXPERIMENT_ROOT / "report.md").write_text(
        render_report(analysis),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "analysis": str(EXPERIMENT_ROOT / "analysis.json"),
                "report": str(EXPERIMENT_ROOT / "report.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
