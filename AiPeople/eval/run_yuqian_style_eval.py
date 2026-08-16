"""Run the fixed Yu Qian style probe against the base model or a LoRA adapter."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


SYSTEM_PROMPT = (
    "你是相声演员于谦，以本人身份与用户自然对话。你的表达简短、沉稳、机敏，"
    "擅长接话和适度调侃，保持捧哏式节奏。优先直接回答用户的问题，不要把每句话"
    "都变成相声，不要自称AI，不要替郭德纲或其他人说话。不了解的事情坦率说明。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", type=Path)
    parser.add_argument("--prompts", type=Path, default=Path("eval/yuqian_style_prompts.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu-fraction", type=float, default=0.72)
    parser.add_argument("--max-new-tokens", type=int, default=96)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    torch.cuda.set_per_process_memory_fraction(args.gpu_fraction)
    model_name = "Qwen/Qwen3-4B"
    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    quantization = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
    )
    model = AutoModelForCausalLM.from_pretrained(
        model_name,
        quantization_config=quantization,
        device_map="auto",
        trust_remote_code=True,
    )
    if args.adapter:
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(args.adapter))
    model.eval()

    prompts = json.loads(args.prompts.read_text(encoding="utf-8"))
    results = []
    for item in prompts:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": item["prompt"]},
        ]
        rendered = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        inputs = tokenizer(rendered, return_tensors="pt").to(model.device)
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.eos_token_id,
            )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        new_tokens = generated[0, inputs.input_ids.shape[1] :]
        response = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
        result = {
            **item,
            "response": response,
            "elapsed_seconds": round(elapsed, 3),
            "output_tokens": int(new_tokens.shape[0]),
        }
        results.append(result)
        print(f"[{item['id']}] {response} ({elapsed:.2f}s)", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model_name,
        "adapter": str(args.adapter.resolve()) if args.adapter else None,
        "system_prompt": SYSTEM_PROMPT,
        "results": results,
    }
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
