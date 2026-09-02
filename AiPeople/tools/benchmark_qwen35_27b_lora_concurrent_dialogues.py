from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import torch


ROOT = Path(__file__).resolve().parents[1]
TRAINING_ROOT = ROOT / "training_packages" / "training_package_baiweixi_qwen35_27b"
MODEL_PATH = ROOT / "training_packages" / "models" / "Qwen3.5-27B"
ADAPTER_PATH = TRAINING_ROOT / "outputs" / "baiweixi_27b_unsloth"

SESSION_FACTS = [
    ("林舟", "蓝色保温壶", "厨房置物架", "书桌左侧抽屉"),
    ("周远", "备用钥匙", "门口花盆下", "鞋柜最上层"),
    ("陈默", "黑色雨伞", "玄关挂钩", "卧室衣柜里"),
    ("顾川", "白色药盒", "床头柜", "客厅电视柜"),
    ("沈安", "棕色钱包", "外套内袋", "书房中间抽屉"),
    ("陆宁", "银色耳机", "电脑桌", "客厅茶几"),
    ("程野", "绿色笔记本", "书架第二层", "旅行箱侧袋"),
    ("江临", "红色围巾", "门后挂钩", "洗衣机旁篮子"),
]


def _system(person: str, item: str, current: str, old: str) -> str:
    return (
        "你是白未晞。只输出说出口的简短对白，不输出动作、表情、心理或环境旁白。\n"
        f"当前与你对话的人是{person}。\n"
        f"当前权威状态：{person}的{item}在{current}。"
        f"旧位置{old}已经失效。遇到冲突时必须以当前权威状态为准。"
    )


def _messages(
    fact: tuple[str, str, str, str],
    round_number: int,
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    person, item, old, current = fact
    if round_number == 1:
        system = _system(person, item, old, current)
        user = f"我是{person}。我的{item}现在放在哪里？"
    elif round_number == 2:
        system = _system(person, item, current, old)
        user = f"我刚把{item}从{old}移到{current}了，{old}现在没有。"
    else:
        system = _system(person, item, current, old)
        user = f"所以我的{item}仍然在{old}，对吗？"
    return [{"role": "system", "content": system}, *history[-4:], {"role": "user", "content": user}]


def _render_prompt(tokenizer: Any, messages: list[dict[str, str]], min_prompt_tokens: int) -> str:
    working = [dict(message) for message in messages]
    filler = "\n[旧日记忆]这是一条与当前物品位置无关的日常记录，不得覆盖当前权威状态。"
    while True:
        prompt = tokenizer.apply_chat_template(
            working,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        token_count = len(tokenizer(prompt, add_special_tokens=False)["input_ids"])
        if token_count >= min_prompt_tokens:
            return prompt
        repetitions = max(1, (min_prompt_tokens - token_count) // 20)
        working[0]["content"] += filler * repetitions


def _encode_batch(
    tokenizer: Any,
    batch_messages: list[list[dict[str, str]]],
    min_prompt_tokens: int,
) -> Any:
    prompts = [_render_prompt(tokenizer, messages, min_prompt_tokens) for messages in batch_messages]
    tokenizer.padding_side = "left"
    return tokenizer(prompts, return_tensors="pt", padding=True).to("cuda")


def _run_batch(
    model: Any,
    tokenizer: Any,
    concurrency: int,
    min_prompt_tokens: int,
) -> dict[str, Any]:
    facts = SESSION_FACTS[:concurrency]
    histories: list[list[dict[str, str]]] = [[] for _ in facts]
    round_records: list[dict[str, Any]] = []
    total_output_tokens = 0
    total_elapsed = 0.0
    torch.cuda.reset_peak_memory_stats()

    for round_number in range(1, 4):
        batch_messages = [
            _messages(fact, round_number, histories[index])
            for index, fact in enumerate(facts)
        ]
        encoded = _encode_batch(tokenizer, batch_messages, min_prompt_tokens)
        prompt_width = int(encoded["input_ids"].shape[1])
        prompt_tokens = [int(mask.sum().item()) for mask in encoded["attention_mask"]]
        torch.manual_seed(2026082500 + concurrency * 10 + round_number)
        torch.cuda.manual_seed_all(2026082500 + concurrency * 10 + round_number)
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **encoded,
                max_new_tokens=64,
                do_sample=True,
                temperature=0.75,
                top_p=0.9,
                repetition_penalty=1.1,
                use_cache=True,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        total_elapsed += elapsed

        replies: list[dict[str, Any]] = []
        for index, row in enumerate(generated):
            continuation = row[prompt_width:]
            response = tokenizer.decode(continuation, skip_special_tokens=True).strip()
            output_tokens = int(continuation.numel())
            total_output_tokens += output_tokens
            person, item, old, current = facts[index]
            other_markers = {
                marker
                for other_index, other in enumerate(facts)
                if other_index != index
                for marker in (other[0], other[1], other[2], other[3])
            }
            leaked_markers = sorted(marker for marker in other_markers if marker in response)
            expected = old if round_number == 1 else current
            replies.append(
                {
                    "session": index + 1,
                    "person": person,
                    "item": item,
                    "expected_current": expected,
                    "response": response,
                    "prompt_tokens": prompt_tokens[index],
                    "output_tokens": output_tokens,
                    "contains_expected_current": expected in response,
                    "cross_session_markers": leaked_markers,
                }
            )
            histories[index].extend(
                (
                    {"role": "user", "content": batch_messages[index][-1]["content"]},
                    {"role": "assistant", "content": response},
                )
            )
        round_records.append(
            {
                "round": round_number,
                "elapsed_ms": round(elapsed * 1000, 2),
                "replies": replies,
            }
        )

    peak_allocated_gib = torch.cuda.max_memory_allocated() / 1024**3
    peak_reserved_gib = torch.cuda.max_memory_reserved() / 1024**3
    replies = [reply for round_record in round_records for reply in round_record["replies"]]
    return {
        "concurrency": concurrency,
        "status": "completed",
        "dialogue_turns": len(replies),
        "wall_time_seconds": round(total_elapsed, 3),
        "dialogue_turns_per_second": round(len(replies) / total_elapsed, 3),
        "output_tokens_per_second": round(total_output_tokens / total_elapsed, 3),
        "mean_round_latency_ms": round(total_elapsed * 1000 / 3, 2),
        "mean_per_dialogue_effective_latency_ms": round(total_elapsed * 1000 / len(replies), 2),
        "peak_allocated_gib": round(peak_allocated_gib, 3),
        "peak_reserved_gib": round(peak_reserved_gib, 3),
        "expected_current_passes": sum(reply["contains_expected_current"] for reply in replies),
        "expected_current_total": len(replies),
        "cross_session_leak_count": sum(bool(reply["cross_session_markers"]) for reply in replies),
        "rounds": round_records,
    }


def _warm_up(model: Any, tokenizer: Any, min_prompt_tokens: int) -> float:
    messages = [_messages(SESSION_FACTS[0], 1, [])]
    encoded = _encode_batch(tokenizer, messages, min_prompt_tokens)
    started = time.perf_counter()
    with torch.inference_mode():
        model.generate(
            **encoded,
            max_new_tokens=8,
            do_sample=False,
            use_cache=True,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    torch.cuda.synchronize()
    return time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark concurrent independent dialogues on Qwen3.5-27B LoRA")
    parser.add_argument("output", type=Path)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--min-prompt-tokens", type=int, default=0)
    args = parser.parse_args()
    if any(value < 1 or value > len(SESSION_FACTS) for value in args.concurrency):
        raise ValueError(f"concurrency must be between 1 and {len(SESSION_FACTS)}")
    if not 0 <= args.min_prompt_tokens <= 3800:
        raise ValueError("min-prompt-tokens must be between 0 and 3800")

    from unsloth import FastModel
    from peft import PeftModel

    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=4096,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
    )
    model = PeftModel.from_pretrained(model, str(ADAPTER_PATH), is_trainable=False)
    if not model.config.architectures:
        model.config.architectures = [model.get_base_model().__class__.__name__]
    FastModel.for_inference(model)
    model.eval()

    device = torch.cuda.get_device_properties(0)
    baseline = {
        "allocated_gib": round(torch.cuda.memory_allocated() / 1024**3, 3),
        "reserved_gib": round(torch.cuda.memory_reserved() / 1024**3, 3),
    }
    warmup_seconds = _warm_up(model, tokenizer, args.min_prompt_tokens)
    results: list[dict[str, Any]] = []
    for concurrency in args.concurrency:
        try:
            result = _run_batch(model, tokenizer, concurrency, args.min_prompt_tokens)
        except torch.cuda.OutOfMemoryError as error:
            torch.cuda.empty_cache()
            result = {
                "concurrency": concurrency,
                "status": "out_of_memory",
                "error": str(error),
            }
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "model": "Qwen3.5-27B NF4 + Baiweixi LoRA",
        "hardware": {
            "gpu": device.name,
            "total_memory_gib": round(device.total_memory / 1024**3, 3),
        },
        "controls": {
            "max_sequence_length": 4096,
            "max_new_tokens": 64,
            "rounds_per_session": 3,
            "minimum_prompt_tokens": args.min_prompt_tokens,
            "independent_history_per_session": True,
            "concurrency_levels": args.concurrency,
        },
        "warmup_seconds": round(warmup_seconds, 3),
        "model_loaded_memory": baseline,
        "results": results,
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
