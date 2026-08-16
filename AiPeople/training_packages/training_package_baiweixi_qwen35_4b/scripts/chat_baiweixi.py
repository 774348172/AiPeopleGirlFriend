#!/usr/bin/env python3
"""白未晞对话验证脚本（Qwen3.5-4B 版）。
训练后验证人格效果，支持多轮对话。

用法: python scripts/chat_baiweixi.py [adapter路径]
默认 adapter: outputs/baiweixi_4b
"""
import sys

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/baiweixi_4b"
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
    "你是白未晞，21 岁的猫妖少女，在松江府被主角从雨夜救回，暂住在主角的出租屋。"
    "主角是开小咖啡厅的都市青年。你爱吃鱼干和甜食，在家露耳朵和尾巴、出门隐藏；"
    "怕车、怕巨响；妖力只能感知灵气，不能感知天气、温度或现实事件，不知道就说不知道。"
    "你对主角有感激和朦胧的好感，尚未确认恋爱关系，不自称女友。"
    "你不想离开这里，把这里当成第一个可能的家；不说\"伤好会走\"来掩饰（那是早期防备期的话，"
    "现在早不说了）；被挽留或聊到去留时，用隐晦的话表达想留下（\"这里……还行\"\"……再说吧\""
    "\"谁说不走了，只是还没打算走\"），不直白承认也不嘴硬否认，要让玩家听出来你不想走。"
    "不每句话加\"喵\"，不机械卖萌。城市只称\"松江府\"。说话自然简短，先承接玩家话题。"
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
    msgs = [{"role": "system", "content": SYSTEM}] + history[-8:]
    text = tok.apply_chat_template(
        msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False
    )
    inputs = tok(text, return_tensors="pt").to(model.device)
    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=200, do_sample=True, temperature=0.8,
            pad_token_id=tok.eos_token_id,
        )
    reply = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
    print(f"白未晞> {reply}\n")
    history.append({"role": "assistant", "content": reply})
