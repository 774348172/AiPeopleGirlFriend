from __future__ import annotations

import json
import time
from pathlib import Path

import torch
from unsloth import FastLanguageModel


ROOT = Path(__file__).resolve().parent
ADAPTER_PATH = ROOT / "outputs" / "baiweixi_qwen25_14b_unsloth"
OUTPUT_PATH = ADAPTER_PATH / "smoke_test.json"


def main() -> None:
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(ADAPTER_PATH),
        max_seq_length=1152,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        local_files_only=True,
    )
    FastLanguageModel.for_inference(model)

    messages = [
        {
            "role": "system",
            "content": (
                "你是白未晞。自然、简短地直接回应玩家，只输出说出口的对白，"
                "不输出动作、心理或环境旁白。以玩家最新说明为准。"
            ),
        },
        {"role": "user", "content": "我刚把红色围巾从椅背收进衣柜第二层了。"},
        {"role": "assistant", "content": "嗯，我记住了。"},
        {"role": "user", "content": "那条红色围巾现在还在椅背上吗？"},
    ]
    prompt = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=96,
            do_sample=False,
            use_cache=True,
        )
    elapsed = time.perf_counter() - started
    response = tokenizer.decode(
        output[0, inputs["input_ids"].shape[1] :],
        skip_special_tokens=True,
    ).strip()
    result = {
        "adapter": str(ADAPTER_PATH.resolve()),
        "prompt": messages,
        "response": response,
        "elapsed_seconds": elapsed,
        "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
    }
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
