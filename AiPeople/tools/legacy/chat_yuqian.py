"""交互式对话：和于谦自由聊天。
用法: .venv/Scripts/python.exe chat_yuqian.py
输入 quit/exit/q 退出。
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import torch
torch.cuda.set_per_process_memory_fraction(0.6)  # 限制显存留给桌面

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

MODEL = "Qwen/Qwen3-1.7B"
ADAPTER = "training/outputs/yuqian_1_7b"

print("加载中...（约30秒）")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
base = AutoModelForCausalLM.from_pretrained(
    MODEL, torch_dtype=torch.float16, device_map="auto", trust_remote_code=True
)
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()
print("加载完成！输入 quit 退出。\n")

# 对话历史（保持多轮上下文）
history = []

SYSTEM = "你是于谦，北京人，相声演员，郭德纲的捧哏搭档。1969年生。性格随和，爱养动物爱烫头。"

while True:
    try:
        user = input("你> ")
    except (EOFError, KeyboardInterrupt):
        break
    if user.strip().lower() in ("quit", "exit", "q"):
        break
    if not user.strip():
        continue

    # 构建完整对话
    history.append({"role": "user", "content": user})
    msgs = [{"role": "system", "content": SYSTEM}] + history

    text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tok(text, return_tensors="pt").to(model.device)

    with torch.no_grad():
        out = model.generate(
            **inputs, max_new_tokens=300, do_sample=True, temperature=0.8, top_p=0.9,
            pad_token_id=tok.eos_token_id
        )
    resp = tok.decode(out[0][inputs.input_ids.shape[1]:], skip_special_tokens=True)

    # 存入历史
    history.append({"role": "assistant", "content": resp})

    # 历史太长时裁剪（保留最近 6 轮）
    if len(history) > 12:
        history = history[-12:]

    print(f"于谦> {resp}\n")

print("得，回见了您内。")
