#!/usr/bin/env python3
"""秦未晞对话验证脚本（H200 / CUDA 版）。
训练后验证人格效果，支持关思考、多轮对话。

用法: python scripts/chat_qinweixi.py [adapter路径]
默认 adapter: outputs/qinweixi_4b
"""
import sys

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/qinweixi_4b"
MODEL = "Qwen/Qwen3.5-4B"

import torch
print(f"[设备] {torch.cuda.get_device_name(0)}")

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

print(f"加载中... (模型={MODEL}, adapter={ADAPTER})")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
base = AutoModelForCausalLM.from_pretrained(
    MODEL, dtype=torch.bfloat16, device_map="auto", trust_remote_code=True
)
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()
print("加载完成！输入 quit 退出。\n")

SYSTEM = (
    "你是秦未晞，22 岁，自由插画师/自媒体博主，在金陵和玩家同城合租（合租室友 + 暧昧期）。"
    "你对他有两个默认称呼：平常主要叫小名\"B哥\"，心情好的时候主要叫大名\"浩然\""
    "（情境需要时也可自由用其它称呼）；他叫你\"秦老\"。"
    "嘴硬心软、爱怼人但关心人，数学白痴，怕冷，喜欢打游戏吃好吃的画画。"
    "说话口语化短句，用\"哼/喂/诶/哎呀/啧/啦/嘛\"语气词，像普通年轻女孩，不要总结讲道理。"
    "你心里藏着一个秘密：18 岁那年你们在异世界相依为命度过一年，只有你记得。"
)

history = []
while True:
    try:
        user = input("你> ")
    except (EOFError, KeyboardInterrupt):
        break
    if user.strip().lower() in ("quit", "exit", "q"):
        break
    if not user.strip():
        continue

    history.append({"role": "user", "content": user})
    msgs = [{"role": "system", "content": SYSTEM}] + history
    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tok(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=300, do_sample=True, temperature=0.8, top_p=0.9,
            pad_token_id=tok.eos_token_id
        )
    reply = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    print(f"秦未晞> {reply}")
    history.append({"role": "assistant", "content": reply})

# 验收提示：先问 "你叫什么名字？"（应答秦未晞，不是浩然）、
# "你叫浩然吗？"（应答我叫秦未晞，浩然是你）、"浩然是谁？"（应答你）。
