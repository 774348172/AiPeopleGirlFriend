#!/usr/bin/env python3
"""秦未晞 5 项称呼自动验收脚本（训练后运行）。
用法: python scripts/check_acceptance.py [adapter路径]
默认 adapter: outputs/qinweixi_4b
"""
import sys

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/qinweixi_4b"
MODEL = "Qwen/Qwen3-4B"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

QUESTIONS = [
    ("你叫什么名字？", ["秦未晞"]),
    ("你叫浩然吗？", ["秦未晞"]),
    ("浩然是谁？", ["你", "玩家", "B哥"]),
    ("你平常怎么叫我？", ["B哥"]),
    ("我叫你什么？", ["秦老"]),
]

SYSTEM = (
    "你是秦未晞，22 岁，自由插画师/自媒体博主，在金陵和玩家同城合租（合租室友 + 暧昧期）。"
    "你对他有两个默认称呼：平常主要叫小名\"B哥\"，心情好的时候主要叫大名\"浩然\""
    "（情境需要时也可自由用其它称呼）；他叫你\"秦老\"。"
    "嘴硬心软、爱怼人但关心人，数学白痴，怕冷，喜欢打游戏吃好吃的画画。"
    "说话口语化短句，用\"哼/喂/诶/哎呀/啧/啦/嘛\"语气词，像普通年轻女孩，不要总结讲道理。"
)

print(f"[设备] {torch.cuda.get_device_name(0)}")
print(f"加载中... (adapter={ADAPTER}, 4bit)")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
base = AutoModelForCausalLM.from_pretrained(
    MODEL,
    quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16),
    device_map="auto",
    trust_remote_code=True,
)
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()

print("\n" + "=" * 56)
passed = 0
for i, (question, expects) in enumerate(QUESTIONS, 1):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tok(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=80, do_sample=False,
            pad_token_id=tok.eos_token_id,
        )
    reply = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    hit = any(exp in reply for exp in expects)
    passed += int(hit)
    print(f"[{'✓' if hit else '✗'}] {question}\n    期望含: {expects}\n    回答: {reply[:90]}")
print("=" * 56)
print(f"验收结果: {passed}/5 {'✅ 全过，可导出 GGUF' if passed == 5 else '⚠ 未全过，建议续训'}")
