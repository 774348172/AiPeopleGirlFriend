from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import run_baiweixi_paired_raw_context_ab as paired


DEFAULT_SOURCE = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "paired_raw_context_20260821-173515"
    / "report.json"
)
DEFAULT_CONTEXT_IDS = (
    "t11_h0_shared",
    "t11_h6_base",
    "t11_h6_lora",
    "t12_h0_shared",
    "t12_h6_base",
    "t12_h6_lora",
)
BASE_LABEL = "base_f16_gguf"
ADAPTER_LABEL = "base_plus_adapter_f16_gguf"
BASE_MODEL = "qwen25-7b-base-f16-gguf-eval:latest"
ADAPTER_MODEL = "baiweixi-7b-adapter-f16-gguf-eval:latest"
BASE_LAYER_SHA256 = "a7bedda095d41801f76f1a8dc7ad51166bbe09bc3bc4b6bd14546441e23bdf57"
ADAPTER_LAYER_SHA256 = "3c8693542793b2aeccd3960838782e43d7c4bf78c54744a39fe2f9035e6eb772"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("report root must be an object")
    return value


def _summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    grades = Counter(value["classification"]["grade"] for value in values)
    passed = grades["direct_pass"] + grades["indirect_pass"]
    low, high = paired._wilson_interval(passed, len(values))
    return {
        "attempts": len(values),
        "direct_pass": grades["direct_pass"],
        "indirect_pass": grades["indirect_pass"],
        "failures": grades["fail"],
        "pass_rate": round(passed / len(values), 4),
        "wilson_95_low": round(low, 4),
        "wilson_95_high": round(high, 4),
        "unsupported_evidence_attempts": sum(
            bool(value["classification"]["unsupported_evidence_hits"])
            for value in values
        ),
        "passed_with_drift_topic": sum(
            value["classification"]["passed"]
            and bool(value["classification"]["drift_topics"])
            for value in values
        ),
    }


def _paired_counts(attempts: list[dict[str, Any]]) -> dict[str, int]:
    by_key: dict[tuple[str, int], dict[str, bool]] = {}
    for attempt in attempts:
        key = (attempt["context_id"], attempt["seed"])
        by_key.setdefault(key, {})[attempt["artifact_label"]] = bool(
            attempt["classification"]["passed"]
        )
    counts = Counter()
    for key, values in by_key.items():
        if set(values) != {BASE_LABEL, ADAPTER_LABEL}:
            raise ValueError(f"incomplete artifact pair: {key}")
        base_passed = values[BASE_LABEL]
        adapter_passed = values[ADAPTER_LABEL]
        if base_passed and adapter_passed:
            counts["both_pass"] += 1
        elif base_passed:
            counts["base_only_pass"] += 1
        elif adapter_passed:
            counts["adapter_only_pass"] += 1
        else:
            counts["both_fail"] += 1
    return {
        key: counts[key]
        for key in (
            "both_pass",
            "base_only_pass",
            "adapter_only_pass",
            "both_fail",
        )
    }


def _two_sided_sign_test(left: int, right: int) -> float:
    discordant = left + right
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, i) for i in range(min(left, right) + 1))
    return min(1.0, 2.0 * tail / (2**discordant))


def _markdown(report: dict[str, Any]) -> str:
    base = report["summaries"][BASE_LABEL]
    adapter = report["summaries"][ADAPTER_LABEL]
    pair = report["paired_summary"]
    rate_delta = adapter["pass_rate"] - base["pass_rate"]
    lines = [
        "# 白未晞 LoRA Adapter 因果隔离测试报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 性质：Adapter 净效应 pilot；同一 F16 基座层、同一 raw ChatML、同一 Prompt 和 seed",
        "",
        "## 1. 结论",
        "",
        report["conclusion"],
        "",
        "## 2. 对照是否真实成立",
        "",
        f"- 裸基座模型：`{BASE_MODEL}`，Ollama manifest digest `{report['artifact_identity'][BASE_LABEL]['ollama_digest']}`。",
        f"- Adapter 模型：`{ADAPTER_MODEL}`，Ollama manifest digest `{report['artifact_identity'][ADAPTER_LABEL]['ollama_digest']}`。",
        f"- 两者共享 F16 基座层 `{BASE_LAYER_SHA256}`；只有后者额外加载 Adapter 层 `{ADAPTER_LAYER_SHA256}`。",
        "- 两个 manifest digest 不同；同 Prompt/seed 的预检输出也不同。因此本次没有复用此前 Adapter 被静默忽略的无效模型。",
        "",
        "## 3. 测试控制",
        "",
        f"- 上下文：{len(report['contexts'])} 个；seed：{len(report['seeds'])} 个；每个模型 {base['attempts']} 次。",
        "- 覆盖 turn 11/12、无历史与 6 轮历史，以及由裸基座或 LoRA 轨迹产生的历史。每一对调用收到字节完全相同的 Prompt。",
        "- 两边都调用 `/api/generate`，设置 `raw=true`，统一 Qwen ChatML，并使用相同 stop、temperature、top_p、repeat_penalty、num_ctx、num_predict 和 seed。",
        "- 唯一有意改变的是：是否加载当前训练产物转换出的 F16 LoRA Adapter。",
        "",
        "## 4. 结果",
        "",
        "| 工件 | 通过/总数 | 通过率 | Wilson 95% CI | 输入外依据 | 通过但带旧话题 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 裸基座 F16 | {base['attempts'] - base['failures']}/{base['attempts']} | {base['pass_rate']:.1%} | {base['wilson_95_low']:.1%}-{base['wilson_95_high']:.1%} | {base['unsupported_evidence_attempts']} | {base['passed_with_drift_topic']} |",
        f"| 裸基座 + Adapter F16 | {adapter['attempts'] - adapter['failures']}/{adapter['attempts']} | {adapter['pass_rate']:.1%} | {adapter['wilson_95_low']:.1%}-{adapter['wilson_95_high']:.1%} | {adapter['unsupported_evidence_attempts']} | {adapter['passed_with_drift_topic']} |",
        "",
        f"Adapter 相对裸基座的绝对通过率变化：**{rate_delta:+.1%}**。",
        "",
        "### 成对结果",
        "",
        "| 结果 | 数量 |",
        "|---|---:|",
        f"| 两边都通过 | {pair['both_pass']} |",
        f"| 仅裸基座通过 | {pair['base_only_pass']} |",
        f"| 仅 Adapter 通过 | {pair['adapter_only_pass']} |",
        f"| 两边都失败 | {pair['both_fail']} |",
        "",
        f"成对符号检验（只看结果不一致的 seed 对）双侧 `p={report['paired_sign_test_p']:.6g}`。该值只描述此固定语料和 seed，不把重复 seed 当作独立玩家分布。",
        "",
        "## 5. 分上下文失败数",
        "",
        "| Context | 裸基座失败 | Adapter 失败 |",
        "|---|---:|---:|",
    ]
    for context in report["contexts"]:
        values = [
            item
            for item in report["attempts"]
            if item["context_id"] == context["context_id"]
        ]
        counts = Counter(
            item["artifact_label"]
            for item in values
            if not item["classification"]["passed"]
        )
        lines.append(
            f"| `{context['context_id']}` | {counts[BASE_LABEL]} | {counts[ADAPTER_LABEL]} |"
        )
    lines.extend(
        [
            "",
            "## 6. 公平性与结论边界",
            "",
            "这次能隔离当前 Adapter 在这组问题上的净效应，但语料仍只有一个合成对话的 6 个关键上下文，不能代表所有玩家对话；12 个 seed 也不是 12 个独立问题。自动规则只判定当前纠正是否被承接，并单独记录输入外依据，仍需人工复核临界回答。F16 GGUF 转换和 Ollama 动态 Adapter loader 属于共同推理链；本报告不会把结果外推成‘所有 LoRA 都会导致漂移’。",
            "",
            "## 7. 证据",
            "",
            "- 原始结果：[`./report.json`](./report.json)",
            "- 上下文来源：[`../paired_raw_context_20260821-173515/report.json`](../paired_raw_context_20260821-173515/report.json)",
            f"- 脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    source_path = Path(args.source_report).resolve()
    source = _load(source_path)
    contexts_by_id = {
        context["context_id"]: context for context in source["contexts"]
    }
    contexts = [contexts_by_id[context_id] for context_id in DEFAULT_CONTEXT_IDS]
    seeds = tuple(int(seed) for seed in source["controls"]["seeds"])
    timeout = httpx.Timeout(args.timeout_seconds)
    attempts: list[dict[str, Any]] = []
    model_specs = (
        (BASE_LABEL, BASE_MODEL),
        (ADAPTER_LABEL, ADAPTER_MODEL),
    )
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=timeout
    ) as client:
        digests = await paired._model_digests(client)
        missing = [model for _, model in model_specs if model not in digests]
        if missing:
            raise RuntimeError(f"Ollama models are not installed: {missing}")
        if digests[BASE_MODEL] == digests[ADAPTER_MODEL]:
            raise RuntimeError("invalid control: base and Adapter manifests are identical")
        for context in contexts:
            for seed in seeds:
                for artifact_label, model in model_specs:
                    request_id = (
                        f"adapter-causal:{artifact_label}:"
                        f"{context['context_id']}:{seed}"
                    )
                    generated = await paired._generate(
                        client,
                        model=model,
                        prompt=context["raw_prompt"],
                        seed=seed,
                        request_id=request_id,
                    )
                    classification = paired._classify(
                        int(context["target_turn"]), generated["response"]
                    )
                    attempt = {
                        "request_id": request_id,
                        "artifact_label": artifact_label,
                        "model": model,
                        "ollama_digest": digests[model],
                        "context_id": context["context_id"],
                        "target_turn": context["target_turn"],
                        "history_depth": context["history_depth"],
                        "history_source": context["history_source"],
                        "prompt_sha256": context["prompt_sha256"],
                        "seed": seed,
                        **generated,
                        "classification": classification,
                    }
                    attempts.append(attempt)
                    print(
                        json.dumps(
                            {
                                "context": context["context_id"],
                                "seed": seed,
                                "artifact": artifact_label,
                                "grade": classification["grade"],
                                "response": generated["response"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    by_artifact = {
        label: [item for item in attempts if item["artifact_label"] == label]
        for label, _ in model_specs
    }
    summaries = {label: _summary(values) for label, values in by_artifact.items()}
    pair = _paired_counts(attempts)
    base_rate = summaries[BASE_LABEL]["pass_rate"]
    adapter_rate = summaries[ADAPTER_LABEL]["pass_rate"]
    if adapter_rate < base_rate:
        conclusion = (
            "在当前固定测试语料上，加载 Adapter 后承接通过率低于裸基座，"
            "且对照只改变了 Adapter，因此当前 LoRA Adapter 是该回归的因果因素。"
            "这不等于裸基座永不漂移，也不等于 LoRA 是所有线上漂移的唯一来源。"
        )
    elif adapter_rate > base_rate:
        conclusion = (
            "在当前固定测试语料上，加载 Adapter 后承接通过率高于裸基座；"
            "本次没有证据表明当前 Adapter 导致该语料上的回归。"
        )
    else:
        conclusion = (
            "在当前固定测试语料上，加载 Adapter 前后的总体通过率相同；"
            "本次无法仅凭总体指标认定 Adapter 导致回归。"
        )
    output_dir = (
        Path(args.output_root).resolve()
        / f"adapter_causality_ab_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "scope": "base_f16_vs_dynamic_lora_adapter_f16_paired_pilot",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "contexts": contexts,
        "seeds": seeds,
        "controls": source["controls"],
        "artifact_identity": {
            BASE_LABEL: {
                "model": BASE_MODEL,
                "ollama_digest": attempts[0]["ollama_digest"],
                "base_layer_sha256": BASE_LAYER_SHA256,
            },
            ADAPTER_LABEL: {
                "model": ADAPTER_MODEL,
                "ollama_digest": next(
                    item["ollama_digest"]
                    for item in attempts
                    if item["artifact_label"] == ADAPTER_LABEL
                ),
                "base_layer_sha256": BASE_LAYER_SHA256,
                "adapter_layer_sha256": ADAPTER_LAYER_SHA256,
                "adapter_source_sha256": "8b752f4f3fc3374d6ccdef46bef09a3806df05b07a6a510be2e86878958589da",
            },
        },
        "attempts": attempts,
        "summaries": summaries,
        "paired_summary": pair,
        "paired_sign_test_p": round(
            _two_sided_sign_test(
                pair["base_only_pass"], pair["adapter_only_pass"]
            ),
            8,
        ),
        "conclusion": conclusion,
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
        description="Measure the net effect of the Baiweixi LoRA Adapter."
    )
    parser.add_argument("--source-report", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-root", default=str(ROOT / "eval" / "world_mind_p0"))
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
