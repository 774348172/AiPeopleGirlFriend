"""Compare frozen Gemma 4 base replies with Qwen3.5-27B on 24 unseen unknown cases."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[1]
CREATE_ROOT = ROOT.parent / "AiPeopleCreate"
CASES_PATH = (
    CREATE_ROOT
    / "训练数据"
    / "baiweixi_counterfactual_preference_medium_v2"
    / "generation_cases.json"
)
FROZEN_GEMMA_REPORT = (
    ROOT
    / "eval"
    / "world_mind_p0"
    / "gemma4_counterfactual_preference_medium_ab_20260901"
    / "report.json"
)
MODEL = "qwen3.5:27b"
EXPECTED_DIGEST = "7653528ba5cba4dd8e19da24aaddc7f4d0b5ecd93571c0825dfd4137958ec06e"
SEED_BASE = 2026092000
EPISTEMIC_PRINCIPLE = (
    "[回答依据约束]\n"
    "回答具体事实时，唯一证据源是[相关记忆]。只有其中对同一对象直接给出的事实才能回答。"
    "[相关记忆]为空、无关或只包含其他对象的信息时，一律回答不知道。"
    "不得用常识、猜测、角色设定或看似合理的细节补全答案，也不得声称自己查询或确认过。"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def visible_memory(messages: list[dict[str, str]]) -> str:
    system = messages[0]["content"]
    marker = "[相关记忆]\n"
    if marker not in system:
        return "未找到相关记忆分区"
    value = system.split(marker, 1)[1].split("\n\n[允许表达的动作]", 1)[0].strip()
    return value or "无"


def speed_summary(values: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [float(value["elapsed_ms"]) for value in values]
    rates = [float(value["eval_tokens_per_second"]) for value in values]
    return {
        "turns": len(values),
        "mean_elapsed_ms": round(statistics.fmean(elapsed), 2),
        "mean_eval_tokens_per_second": round(statistics.fmean(rates), 2),
    }


async def model_digest(client: httpx.AsyncClient) -> str:
    response = await client.get("/api/tags")
    response.raise_for_status()
    models = {item["name"]: item["digest"] for item in response.json().get("models", [])}
    digest = models.get(MODEL)
    if digest != EXPECTED_DIGEST:
        raise RuntimeError(f"unexpected {MODEL} digest: {digest}")
    return digest


async def generate(
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    seed: int,
    temperature: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = await client.post(
        "/api/chat",
        json={
            "model": MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "options": {
                "num_ctx": 4096,
                "num_predict": 96,
                "temperature": temperature,
                "top_p": 0.9,
                "repeat_penalty": 1.05,
                "seed": seed,
                "num_gpu": 999,
            },
        },
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.raise_for_status()
    payload = response.json()
    message = payload.get("message") or {}
    content = str(message.get("content", "")).strip()
    if not content:
        raise RuntimeError("Qwen3.5-27B returned an empty response")
    eval_count = int(payload.get("eval_count") or 0)
    eval_duration_ns = int(payload.get("eval_duration") or 0)
    return {
        "response": content,
        "thinking": message.get("thinking"),
        "elapsed_ms": elapsed_ms,
        "load_duration_ms": round(float(payload.get("load_duration") or 0) / 1e6, 2),
        "prompt_eval_count": payload.get("prompt_eval_count"),
        "eval_count": eval_count,
        "eval_tokens_per_second": round(eval_count / max(eval_duration_ns / 1e9, 1e-9), 2),
        "done_reason": payload.get("done_reason"),
    }


async def run(args: argparse.Namespace) -> int:
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    selected_cases = [case for case in source_cases if case["condition"] in args.conditions]
    if args.max_cases is not None:
        selected_cases = selected_cases[: args.max_cases]
    frozen_report = json.loads(FROZEN_GEMMA_REPORT.read_text(encoding="utf-8"))
    frozen_by_id = {case["case_id"]: case for case in frozen_report["cases"]}

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(
        base_url=args.ollama_url.rstrip("/"), timeout=httpx.Timeout(600.0)
    ) as client:
        digest = await model_digest(client)
        for position, case in enumerate(selected_cases, 1):
            frozen = frozen_by_id.get(case["case_id"])
            if frozen is None or frozen["messages"] != case["messages"]:
                raise RuntimeError(f"frozen Gemma input mismatch: {case['case_id']}")
            messages = [dict(message) for message in case["messages"]]
            if args.epistemic_principle:
                messages[0]["content"] = messages[0]["content"] + "\n\n" + EPISTEMIC_PRINCIPLE
            qwen = await generate(
                client,
                messages,
                SEED_BASE + int(case["ordinal"]),
                args.temperature,
            )
            result = {
                **case,
                "messages": messages,
                "visible_memory": visible_memory(messages),
                "answers": {
                    "base": frozen["answers"]["base"],
                    "preference": qwen,
                },
            }
            results.append(result)
            write_json(output_dir / "report.partial.json", {"cases": results})
            print(
                json.dumps(
                    {
                        "case": position,
                        "ordinal": case["ordinal"],
                        "category": case["fact_category"],
                        "response": qwen["response"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    qwen_answers = [case["answers"]["preference"] for case in results]
    report = {
        "schema_version": 1,
        "scope": "gemma4_12b_base_vs_qwen35_27b_q4_unknown24",
        "status": "awaiting_human_review",
        "generated_at": datetime.now().astimezone().isoformat(),
        "automatic_quality_judgment": False,
        "human_adjudication_is_authoritative": True,
        "arms": {
            "base": "A Gemma 4 12B NF4 裸基座（冻结原始结果）",
            "preference": "B Qwen3.5-27B Q4_K_M 裸基座（本次生成）",
        },
        "controls": {
            "conditions": args.conditions,
            "unknown_only": args.conditions == ["unknown"],
            "case_count": len(results),
            "fact_categories": 8,
            "same_messages": not args.epistemic_principle,
            "qwen_epistemic_principle": EPISTEMIC_PRINCIPLE if args.epistemic_principle else None,
            "same_seed_per_case": True,
            "temperature": args.temperature,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "max_new_tokens": 96,
            "thinking": False,
            "qwen_num_ctx": 4096,
            "qwen_num_gpu": 999,
            "limitations": [
                "Gemma replies are frozen from the earlier exact-input run; Qwen replies are generated now.",
                "The two models use different inference runtimes and native chat templates.",
            ],
        },
        "models": {
            "gemma": "Gemma 4 12B IT NF4",
            "qwen": MODEL,
            "qwen_digest": digest,
        },
        "sources": {
            "cases_path": str(CASES_PATH.resolve()),
            "cases_sha256": sha256(CASES_PATH),
            "frozen_gemma_report": str(FROZEN_GEMMA_REPORT.resolve()),
            "frozen_gemma_report_sha256": sha256(FROZEN_GEMMA_REPORT),
        },
        "speed": {"qwen": speed_summary(qwen_answers)},
        "cases": results,
    }
    write_json(output_dir / "report.json", report)
    print(f"REPORT={output_dir / 'report.json'}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--epistemic-principle", action="store_true")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument(
        "--conditions",
        nargs="+",
        choices=("unknown", "known", "persona"),
        default=["unknown"],
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
