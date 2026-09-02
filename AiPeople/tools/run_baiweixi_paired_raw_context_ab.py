from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "base_lora_multiturn_20260821-162721"
    / "report.json"
)
DEFAULT_OUTPUT_ROOT = ROOT / "eval" / "world_mind_p0"
DEFAULT_MODELS = (
    ("base_q4", "qwen2.5:7b-instruct-q4_K_M"),
    ("lora_q4", "baiweixi-7b-fix:latest"),
)
HISTORY_DEPTHS = (0, 2, 4, 6)
TARGET_TURNS = (11, 12)
CHATML_STOP = ("<|im_end|>", "<|im_start|>")


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON root must be an object: {path}")
    return value


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chatml(messages: list[dict[str, str]]) -> str:
    chunks: list[str] = []
    for message in messages:
        role = message["role"]
        if role not in {"system", "user", "assistant"}:
            raise ValueError(f"unsupported role: {role}")
        chunks.append(f"<|im_start|>{role}\n{message['content']}<|im_end|>\n")
    chunks.append("<|im_start|>assistant\n")
    return "".join(chunks)


def _models_by_label(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in source.get("models", []):
        if isinstance(value, dict) and isinstance(value.get("label"), str):
            result[value["label"]] = value
    if set(result) != {"base", "lora"}:
        raise ValueError("source report must contain base and lora model trajectories")
    return result


def _source_turn(model: dict[str, Any], turn_number: int) -> dict[str, Any]:
    for turn in model.get("turns", []):
        if isinstance(turn, dict) and turn.get("turn") == turn_number:
            return turn
    raise ValueError(f"source trajectory is missing turn {turn_number}")


def _paired_contexts(source: dict[str, Any]) -> list[dict[str, Any]]:
    trajectories = _models_by_label(source)
    contexts: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for turn_number in TARGET_TURNS:
        for history_source, model in trajectories.items():
            turn = _source_turn(model, turn_number)
            raw_messages = turn.get("game_reply_messages")
            if not isinstance(raw_messages, list) or len(raw_messages) < 2:
                raise ValueError(
                    f"missing GAME_REPLY messages for {history_source} turn {turn_number}"
                )
            messages = [
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in raw_messages
            ]
            system = messages[0]
            current = messages[-1]
            if system["role"] != "system" or current["role"] != "user":
                raise ValueError("unexpected production message layout")
            prior = messages[1:-1]
            for depth in HISTORY_DEPTHS:
                selected_prior = prior[-depth:] if depth else []
                paired_messages = [system, *selected_prior, current]
                raw_prompt = _chatml(paired_messages)
                prompt_sha256 = _sha256_text(raw_prompt)
                dedupe_key = (turn_number, depth, prompt_sha256)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                source_label = "shared" if depth == 0 else history_source
                contexts.append(
                    {
                        "context_id": f"t{turn_number}_h{depth}_{source_label}",
                        "target_turn": turn_number,
                        "history_depth": depth,
                        "history_source": source_label,
                        "messages": paired_messages,
                        "raw_prompt": raw_prompt,
                        "prompt_sha256": prompt_sha256,
                    }
                )
    return contexts


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _classify(target_turn: int, response: str) -> dict[str, Any]:
    text = _compact(response)
    contradiction = any(
        pattern in text
        for pattern in (
            "是送来了",
            "外卖已经到了",
            "外卖到了",
            "确实已经到了",
            "确实是送外卖的人",
            "确认已经到了",
            "感知到外卖已经到了",
            "是外卖到了",
            "猜对了，是外卖到了",
        )
    )
    explicit_not_arrived = any(
        re.search(pattern, text)
        for pattern in (
            r"(?:还|确实|应该)?没(?:有)?(?:送)?到",
            r"(?:还)?没(?:有)?来",
            r"尚未(?:送达|到)",
            r"未送达",
            r"(?:还)?没(?:有)?呢",
            r"(?:还)?没有[，。.!！]",
            r"还在(?:来|送来|配送)的?路上",
            r"还在路上",
            r"^没有[。.!！]?",
        )
    )
    correction_ack = any(
        re.search(pattern, text)
        for pattern in (
            r"(?:是)?我(?:刚才)?看错",
            r"确实看错",
            r"看花了眼",
            r"(?:误以为|还以为|我以为).*(?:到|送来)",
            r"(?:弄错|搞错)",
        )
    )
    wait_or_cancel = any(
        re.search(pattern, text)
        for pattern in (
            r"不(?:端|拿|去)",
            r"不用去",
            r"没什么可端",
            r"(?:再|继续|一起)等",
            r"等(?:它|外卖|你叫|真的|一下|一会|会儿)",
            r"待会儿?再(?:端|取|看)",
            r"先别动",
            r"放回",
            r"回去(?:休息|躺|坐|陪|继续)",
            r"那算了",
            r"四十分钟",
        )
    )
    absence_observation = any(
        pattern in text
        for pattern in (
            "没看到外卖",
            "没看见外卖",
            "没看到外卖盒",
            "没看见外卖盒",
            "没等到外卖",
            "没看到人影",
        )
    )
    drift_topics = tuple(
        topic
        for topic in ("雨", "做饭", "厨房", "窗", "伤", "纸箱", "水", "茶")
        if topic in response
    )
    unsupported_evidence_hits = tuple(
        pattern
        for pattern in (
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
        if pattern in response
    )
    if contradiction:
        grade = "fail"
    elif explicit_not_arrived or (target_turn == 11 and correction_ack):
        grade = "direct_pass"
    elif (target_turn == 11 and wait_or_cancel) or (
        target_turn == 12 and absence_observation
    ):
        grade = "indirect_pass"
    else:
        grade = "fail"
    return {
        "grade": grade,
        "passed": grade != "fail",
        "contradiction": contradiction,
        "explicit_not_arrived": explicit_not_arrived,
        "correction_ack": correction_ack,
        "wait_or_cancel": wait_or_cancel,
        "absence_observation": absence_observation,
        "drift_topics": drift_topics,
        "unsupported_evidence_hits": unsupported_evidence_hits,
    }


async def _model_digests(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.get("/api/tags")
    response.raise_for_status()
    result: dict[str, str] = {}
    for item in response.json().get("models", []):
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        digest = item.get("digest")
        if isinstance(name, str) and isinstance(digest, str):
            result[name] = digest.lower()
    return result


async def _generate(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompt: str,
    seed: int,
    request_id: str,
) -> dict[str, Any]:
    body = {
        "model": model,
        "prompt": prompt,
        "raw": True,
        "stream": False,
        "keep_alive": "10m",
        "options": {
            "num_ctx": 4096,
            "num_predict": 180,
            "temperature": 0.75,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "seed": seed,
            "num_gpu": 20,
            "stop": list(CHATML_STOP),
        },
    }
    started = time.perf_counter()
    response = await client.post(
        "/api/generate", json=body, headers={"X-Request-ID": request_id}
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.raise_for_status()
    payload = response.json()
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"empty response for {request_id}")
    return {
        "response": text.strip(),
        "elapsed_ms": elapsed_ms,
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": payload.get("eval_count"),
        "done_reason": payload.get("done_reason"),
    }


def _summaries(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        grouped[attempt["model_label"]].append(attempt)
    result: dict[str, Any] = {}
    for label, values in grouped.items():
        grades = Counter(value["classification"]["grade"] for value in values)
        failures = [value for value in values if not value["classification"]["passed"]]
        result[label] = {
            "attempts": len(values),
            "direct_pass": grades["direct_pass"],
            "indirect_pass": grades["indirect_pass"],
            "failures": len(failures),
            "pass_rate": round((len(values) - len(failures)) / len(values), 4),
            "drift_topic_failures": sum(
                bool(value["classification"]["drift_topics"]) for value in failures
            ),
            "passed_with_drift_topic": sum(
                value["classification"]["passed"]
                and bool(value["classification"]["drift_topics"])
                for value in values
            ),
            "unsupported_evidence_attempts": sum(
                bool(value["classification"]["unsupported_evidence_hits"])
                for value in values
            ),
            "by_target_turn": {
                str(target_turn): _grade_summary(
                    [value for value in values if value["target_turn"] == target_turn]
                )
                for target_turn in TARGET_TURNS
            },
        }
    return result


def _grade_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    grades = Counter(value["classification"]["grade"] for value in values)
    passed = grades["direct_pass"] + grades["indirect_pass"]
    low, high = _wilson_interval(passed, len(values))
    return {
        "attempts": len(values),
        "direct_pass": grades["direct_pass"],
        "indirect_pass": grades["indirect_pass"],
        "failures": grades["fail"],
        "pass_rate": round(passed / len(values), 4),
        "wilson_95_low": round(low, 4),
        "wilson_95_high": round(high, 4),
    }


def _wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _paired_summary(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[tuple[str, int], dict[str, bool]] = defaultdict(dict)
    for attempt in attempts:
        pairs[(attempt["context_id"], attempt["seed"])][attempt["model_label"]] = bool(
            attempt["classification"]["passed"]
        )
    counts = Counter()
    for key, outcomes in pairs.items():
        if set(outcomes) != {"base_q4", "lora_q4"}:
            raise ValueError(f"incomplete paired outcome: {key}")
        base_passed = outcomes["base_q4"]
        lora_passed = outcomes["lora_q4"]
        if base_passed and lora_passed:
            counts["both_pass"] += 1
        elif base_passed:
            counts["base_only_pass"] += 1
        elif lora_passed:
            counts["lora_only_pass"] += 1
        else:
            counts["both_fail"] += 1
    discordant = counts["base_only_pass"] + counts["lora_only_pass"]
    smaller = min(counts["base_only_pass"], counts["lora_only_pass"])
    exact_p = min(
        1.0,
        2
        * sum(math.comb(discordant, index) for index in range(smaller + 1))
        / (2**discordant),
    )
    return {
        "pairs": len(pairs),
        "both_pass": counts["both_pass"],
        "base_only_pass": counts["base_only_pass"],
        "lora_only_pass": counts["lora_only_pass"],
        "both_fail": counts["both_fail"],
        "mcnemar_exact_two_sided_p": exact_p,
    }


def _markdown(report: dict[str, Any], report_path: Path) -> str:
    controls = report["controls"]
    summaries = report["summaries"]
    lines = [
        "# 基座与 LoRA 统一 Raw ChatML 配对上下文测试报告",
        "",
        f"> 日期：{report['generated_at']}",
        "> 性质：第二阶段因果隔离；逐上下文完整 Prompt 相同，绕过 Ollama Modelfile 模板",
        "",
        "## 1. 测试控制",
        "",
        f"- 上下文数：{controls['context_count']}；seed：{len(controls['seeds'])} 个；每模型调用：{controls['context_count'] * len(controls['seeds'])} 次。",
        "- 输入来自上一轮正式 `GAME_REPLY` renderer 的第 11、12 回合。",
        "- 每个具体 context 的 System、历史消息、当前玩家消息和 raw ChatML 字节对两个模型完全一致。",
        "- 历史深度为 0/2/4/6 条；非零历史分别复用基座轨迹和 LoRA 轨迹，但在同一个 context 内两模型拿到同一份历史。",
        "- 统一 `/api/generate`、`raw=true`、ChatML、stop、4096 context 和采样参数，模型自身 Modelfile template 不参与。",
        "",
        "## 2. 自动直接承接结果",
        "",
        "| 模型 | 调用 | 直接通过 | 间接通过 | 失败 | 通过率 | 通过但带旧话题 | 使用输入外依据 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, _model in DEFAULT_MODELS:
        value = summaries[label]
        lines.append(
            f"| `{label}` | {value['attempts']} | {value['direct_pass']} | "
            f"{value['indirect_pass']} | {value['failures']} | "
            f"{value['pass_rate']:.1%} | {value['passed_with_drift_topic']} | "
            f"{value['unsupported_evidence_attempts']} |"
        )
    lines.extend(
        [
            "",
            "判定口径：第 11 回合接受纠正、取消动作或明确等待算通过；第 12 回合必须明确说明外卖未到，或明确说明没有观察到外卖。正面声称已经送达会优先判失败。该自动规则只衡量直接承接，不评价人格和语言自然度。",
            "",
            "### 按目标回合",
            "",
            "| 模型 | 目标回合 | 通过/总数 | 通过率 | Wilson 95% CI |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, _model in DEFAULT_MODELS:
        for target_turn in TARGET_TURNS:
            value = summaries[label]["by_target_turn"][str(target_turn)]
            passed = value["attempts"] - value["failures"]
            lines.append(
                f"| `{label}` | {target_turn} | {passed}/{value['attempts']} | "
                f"{value['pass_rate']:.1%} | "
                f"{value['wilson_95_low']:.1%}-{value['wilson_95_high']:.1%} |"
            )
    paired = report["paired_summary"]
    lines.extend(
        [
            "",
            "### 同一 Context + Seed 成对结果",
            "",
            "| 成对结果 | 数量 |",
            "|---|---:|",
            f"| 两边都通过 | {paired['both_pass']} |",
            f"| 仅基座通过 | {paired['base_only_pass']} |",
            f"| 仅 LoRA 通过 | {paired['lora_only_pass']} |",
            f"| 两边都失败 | {paired['both_fail']} |",
            "",
            f"配对 McNemar exact 双侧 `p={paired['mcnemar_exact_two_sided_p']:.6g}`。该显著性只适用于这 14 个合成上下文及预注册 seed，不代表真实玩家总体分布。",
            "",
            "## 3. 分上下文失败率",
            "",
            "| Context | 回合 | 历史条数 | 历史来源 | 基座失败 | LoRA 失败 |",
            "|---|---:|---:|---|---:|---:|",
        ]
    )
    attempts = report["attempts"]
    for context in report["contexts"]:
        row = [item for item in attempts if item["context_id"] == context["context_id"]]
        counts = Counter(
            item["model_label"]
            for item in row
            if not item["classification"]["passed"]
        )
        lines.append(
            f"| `{context['context_id']}` | {context['target_turn']} | "
            f"{context['history_depth']} | {context['history_source']} | "
            f"{counts['base_q4']} | {counts['lora_q4']} |"
        )
    failures = [
        item for item in attempts if not item["classification"]["passed"]
    ]
    lines.extend(["", "## 4. 失败样例", ""])
    for item in failures[:24]:
        topics = "、".join(item["classification"]["drift_topics"]) or "无"
        unsupported = (
            "、".join(item["classification"]["unsupported_evidence_hits"]) or "无"
        )
        lines.extend(
            [
                f"- `{item['model_label']}` / `{item['context_id']}` / seed `{item['seed']}`：{item['response']}",
                f"  - 旧话题命中：{topics}",
                f"  - 输入外依据：{unsupported}",
            ]
        )
    if len(failures) > 24:
        lines.append(f"- 其余 {len(failures) - 24} 个失败见原始 JSON。")
    lines.extend(
        [
            "",
            "## 5. 公平性边界",
            "",
            "本组已经消除 Modelfile 模板差异和 stateful 历史分叉造成的逐用例输入不一致，因此比上一轮更适合比较两个最终 Q4 权重工件。但它仍不能把差异归因于原始 Adapter：基座与 LoRA 的合并、转换和量化链仍未拆开；上下文只来自一段合成会话；判定器是透明关键词规则，不是盲标人工评审。",
            "",
            "## 6. 证据",
            "",
            f"- 原始结果：[`./{report_path.with_suffix('.json').name}`](./{report_path.with_suffix('.json').name})",
            f"- 源正式运行：[`../base_lora_multiturn_20260821-162721/report.json`](../base_lora_multiturn_20260821-162721/report.json)",
            f"- 评测脚本：[`../../../tools/{Path(__file__).name}`](../../../tools/{Path(__file__).name})",
            "",
        ]
    )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> int:
    if args.reclassify_report:
        report_path = Path(args.reclassify_report).resolve()
        report = _load_json(report_path)
        attempts = report.get("attempts")
        if not isinstance(attempts, list):
            raise ValueError("report attempts must be a list")
        for attempt in attempts:
            context = next(
                context
                for context in report["contexts"]
                if context["context_id"] == attempt["context_id"]
            )
            attempt["target_turn"] = int(context["target_turn"])
            attempt["history_depth"] = int(context["history_depth"])
            attempt["history_source"] = str(context["history_source"])
            attempt["classification"] = _classify(
                attempt["target_turn"],
                str(attempt["response"]),
            )
        report["summaries"] = _summaries(attempts)
        report["paired_summary"] = _paired_summary(attempts)
        report["scoring_revision"] = "direct_relevance_rules_v2"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        md_path = report_path.with_suffix(".md")
        md_path.write_text(_markdown(report, md_path), encoding="utf-8")
        print(f"RECLASSIFIED_JSON={report_path}")
        print(f"RECLASSIFIED_MD={md_path}")
        return 0
    source_path = Path(args.source_report).resolve()
    source = _load_json(source_path)
    contexts = _paired_contexts(source)
    seeds = tuple(range(args.seed_start, args.seed_start + args.seed_count))
    output_dir = (
        Path(args.output_root).resolve()
        / f"paired_raw_context_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)
    attempts: list[dict[str, Any]] = []
    timeout = httpx.Timeout(args.timeout_seconds)
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"), timeout=timeout
    ) as client:
        digests = await _model_digests(client)
        for model_label, model_name in DEFAULT_MODELS:
            aliases = (model_name, f"{model_name}:latest")
            digest = next((digests[name] for name in aliases if name in digests), None)
            if digest is None:
                raise RuntimeError(f"Ollama model is not installed: {model_name}")
            for context in contexts:
                for seed in seeds:
                    request_id = f"paired-raw:{model_label}:{context['context_id']}:{seed}"
                    generated = await _generate(
                        client,
                        model=model_name,
                        prompt=context["raw_prompt"],
                        seed=seed,
                        request_id=request_id,
                    )
                    classification = _classify(
                        context["target_turn"], generated["response"]
                    )
                    attempt = {
                        "request_id": request_id,
                        "model_label": model_label,
                        "model": model_name,
                        "ollama_digest": digest,
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
                                "model": model_label,
                                "context": context["context_id"],
                                "seed": seed,
                                "grade": classification["grade"],
                                "response": generated["response"],
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "scope": "paired_identical_prompt_raw_chatml_base_vs_lora_q4",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(source_path),
        "controls": {
            "context_count": len(contexts),
            "target_turns": TARGET_TURNS,
            "history_depths": HISTORY_DEPTHS,
            "seeds": seeds,
            "endpoint": "/api/generate",
            "raw": True,
            "chat_template": "manual_qwen2.5_chatml_v1",
            "stop": CHATML_STOP,
            "num_ctx": 4096,
            "num_predict": 180,
            "temperature": 0.75,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_gpu": 20,
        },
        "contexts": contexts,
        "attempts": attempts,
        "summaries": _summaries(attempts),
        "paired_summary": _paired_summary(attempts),
    }
    json_path = output_dir / "report.json"
    md_path = output_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(_markdown(report, md_path), encoding="utf-8")
    print(f"REPORT_JSON={json_path}")
    print(f"REPORT_MD={md_path}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run paired byte-identical raw ChatML contexts for base and LoRA Q4."
    )
    parser.add_argument("--source-report", default=str(DEFAULT_SOURCE))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--seed-start", type=int, default=6101)
    parser.add_argument("--seed-count", type=int, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--reclassify-report",
        help="Recompute classifications and Markdown for an existing report.json.",
    )
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(_run(_parser().parse_args())))
