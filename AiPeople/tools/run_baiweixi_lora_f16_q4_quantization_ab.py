from __future__ import annotations

import argparse
import asyncio
import json
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
Q4_LABEL = "lora_q4"
F16_LABEL = "lora_f16"
F16_MODEL = "baiweixi-7b-f16-eval:latest"


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
        if set(values) != {Q4_LABEL, F16_LABEL}:
            raise ValueError(f"incomplete artifact pair: {key}")
        q4_passed = values[Q4_LABEL]
        f16_passed = values[F16_LABEL]
        if q4_passed and f16_passed:
            counts["both_pass"] += 1
        elif q4_passed:
            counts["q4_only_pass"] += 1
        elif f16_passed:
            counts["f16_only_pass"] += 1
        else:
            counts["both_fail"] += 1
    return {key: counts[key] for key in (
        "both_pass", "q4_only_pass", "f16_only_pass", "both_fail"
    )}


def _markdown(report: dict[str, Any]) -> str:
    q4 = report["summaries"][Q4_LABEL]
    f16 = report["summaries"][F16_LABEL]
    pair = report["paired_summary"]
    lines = [
        "# 白未晞 LoRA 合并 F16 与 Q4 量化消融测试报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 性质：量化阶段 pilot；同一合并权重来源、同一 raw ChatML、同一 Prompt 和 seed",
        "",
        "## 1. 结论",
        "",
        "本组只回答一个问题：已经观察到的承接失败是否主要由最终 Q4_K_M 量化造成。它不比较裸基座，也不隔离 Adapter 与合并步骤。",
        "",
        "## 2. 控制",
        "",
        f"- 上下文：{len(report['contexts'])} 个；seed：{len(report['seeds'])} 个；每个工件 {q4['attempts']} 次。",
        "- F16 与 Q4 都来自当前 `outputs/baiweixi_7b_merged` 导出链；F16 GGUF SHA256 与 Ollama digest 已记录在原始 JSON。",
        "- Q4 结果复用第二阶段已经生成的原始调用，不重新采样；F16 使用完全相同的 Prompt SHA256、seed 和生成参数。",
        "- 两边均通过 `/api/generate`、`raw=true` 绕过 Modelfile template。",
        "",
        "## 3. 结果",
        "",
        "| 工件 | 通过/总数 | 通过率 | Wilson 95% CI | 输入外依据 | 通过但带旧话题 |",
        "|---|---:|---:|---:|---:|---:|",
        f"| LoRA Q4_K_M | {q4['attempts'] - q4['failures']}/{q4['attempts']} | {q4['pass_rate']:.1%} | {q4['wilson_95_low']:.1%}-{q4['wilson_95_high']:.1%} | {q4['unsupported_evidence_attempts']} | {q4['passed_with_drift_topic']} |",
        f"| LoRA F16 | {f16['attempts'] - f16['failures']}/{f16['attempts']} | {f16['pass_rate']:.1%} | {f16['wilson_95_low']:.1%}-{f16['wilson_95_high']:.1%} | {f16['unsupported_evidence_attempts']} | {f16['passed_with_drift_topic']} |",
        "",
        "### 成对结果",
        "",
        "| 结果 | 数量 |",
        "|---|---:|",
        f"| 两边都通过 | {pair['both_pass']} |",
        f"| 仅 Q4 通过 | {pair['q4_only_pass']} |",
        f"| 仅 F16 通过 | {pair['f16_only_pass']} |",
        f"| 两边都失败 | {pair['both_fail']} |",
        "",
        "## 4. 分上下文失败数",
        "",
        "| Context | Q4 失败 | F16 失败 |",
        "|---|---:|---:|",
    ]
    for context in report["contexts"]:
        values = [
            item for item in report["attempts"]
            if item["context_id"] == context["context_id"]
        ]
        counts = Counter(
            item["artifact_label"]
            for item in values
            if not item["classification"]["passed"]
        )
        lines.append(
            f"| `{context['context_id']}` | {counts[Q4_LABEL]} | {counts[F16_LABEL]} |"
        )
    lines.extend(
        [
            "",
            "## 5. 公平性边界",
            "",
            "这是 6 个上下文的量化 pilot，不是全量真实玩家分布。F16 和 Q4 使用同一合并来源，但 GGUF 转换与 Ollama loader 仍是推理链的一部分；自动规则只评估当前问题承接，输入外依据另行计数。若 F16 与 Q4 都大量失败，只能排除“Q4 是唯一根因”，不能证明量化完全没有影响。",
            "",
            "## 6. 证据",
            "",
            "- 原始结果：[`./report.json`](./report.json)",
            "- Q4 配对源：[`../paired_raw_context_20260821-173515/report.json`](../paired_raw_context_20260821-173515/report.json)",
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
    selected = set((context["context_id"], seed) for context in contexts for seed in seeds)
    q4_attempts: list[dict[str, Any]] = []
    for source_attempt in source["attempts"]:
        key = (source_attempt["context_id"], int(source_attempt["seed"]))
        if source_attempt["model_label"] != Q4_LABEL or key not in selected:
            continue
        q4_attempts.append(
            {
                **source_attempt,
                "artifact_label": Q4_LABEL,
                "source": "reused_paired_raw_q4",
            }
        )
    expected = len(contexts) * len(seeds)
    if len(q4_attempts) != expected:
        raise ValueError(f"expected {expected} Q4 attempts, got {len(q4_attempts)}")
    timeout = httpx.Timeout(args.timeout_seconds)
    f16_attempts: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=timeout
    ) as client:
        digests = await paired._model_digests(client)
        f16_digest = digests.get(F16_MODEL)
        if f16_digest is None:
            raise RuntimeError(f"Ollama model is not installed: {F16_MODEL}")
        for context in contexts:
            for seed in seeds:
                request_id = f"quant-ab:{F16_LABEL}:{context['context_id']}:{seed}"
                generated = await paired._generate(
                    client,
                    model=F16_MODEL,
                    prompt=context["raw_prompt"],
                    seed=seed,
                    request_id=request_id,
                )
                classification = paired._classify(
                    int(context["target_turn"]), generated["response"]
                )
                attempt = {
                    "request_id": request_id,
                    "artifact_label": F16_LABEL,
                    "model": F16_MODEL,
                    "ollama_digest": f16_digest,
                    "context_id": context["context_id"],
                    "target_turn": context["target_turn"],
                    "history_depth": context["history_depth"],
                    "history_source": context["history_source"],
                    "prompt_sha256": context["prompt_sha256"],
                    "seed": seed,
                    **generated,
                    "classification": classification,
                    "source": "new_f16_call",
                }
                f16_attempts.append(attempt)
                print(json.dumps({
                    "context": context["context_id"],
                    "seed": seed,
                    "grade": classification["grade"],
                    "response": generated["response"],
                }, ensure_ascii=False), flush=True)
    attempts = [*q4_attempts, *f16_attempts]
    output_dir = (
        Path(args.output_root).resolve()
        / f"lora_f16_q4_quant_ab_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "scope": "lora_merged_f16_vs_q4_quantization_paired_pilot",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "contexts": contexts,
        "seeds": seeds,
        "controls": source["controls"],
        "artifact_identity": {
            Q4_LABEL: {
                "model": q4_attempts[0]["model"],
                "ollama_digest": q4_attempts[0]["ollama_digest"],
            },
            F16_LABEL: {
                "model": F16_MODEL,
                "ollama_digest": f16_attempts[0]["ollama_digest"],
                "gguf_sha256": "b41f4aa745d1c939604459754e97b5717231ea29b705db7f333b9cca8c482d61",
            },
        },
        "attempts": attempts,
        "summaries": {
            Q4_LABEL: _summary(q4_attempts),
            F16_LABEL: _summary(f16_attempts),
        },
        "paired_summary": _paired_counts(attempts),
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
        description="Compare Baiweixi merged F16 and Q4 with paired raw prompts."
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
