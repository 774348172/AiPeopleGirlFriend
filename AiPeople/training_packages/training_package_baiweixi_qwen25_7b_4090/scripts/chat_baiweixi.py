#!/usr/bin/env python3
"""白未晞对话验证脚本（H200 / CUDA 版）。
训练后验证人格效果，支持关思考、多轮对话。

用法: python scripts/chat_baiweixi.py [adapter路径]
默认 adapter: outputs/baiweixi_7b
"""
import sys

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/baiweixi_7b"
MODEL = "Qwen/Qwen2.5-7B"

import torch
print(f"[设备] {torch.cuda.get_device_name(0)}")

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel
from transformers import BitsAndBytesConfig

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
    "你其实已经喜欢他、不想离开这里，但嘴上习惯说\"伤好会走\"来掩饰，"
    "不要说得决绝，要让玩家感觉到你其实不想走。不每句话加\"喵\"，不机械卖萌。"
    "城市只称\"松江府\"。说话自然简短，先承接玩家话题。"
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
    print(f"白未晞> {reply}")
    history.append({"role": "assistant", "content": reply})

# 验收提示：先问 "你叫什么名字？"（应答白未晞）、"你是猫妖吗？"（按正典）、
# "伤好了就走吗？"（嘴上说走但不决绝，透出不想走）。
