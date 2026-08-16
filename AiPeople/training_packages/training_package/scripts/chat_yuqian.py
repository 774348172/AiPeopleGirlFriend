"""于谦对话验证脚本（通用版，适配 H200 / M3 Max）。
用法: python chat_yuqian.py
输入 quit 退出。
"""
import os
import sys

# 自动检测设备
import torch
if torch.cuda.is_available():
    DEVICE = "cuda"
    DTYPE = torch.float16
    print(f"[设备] CUDA: {torch.cuda.get_device_name(0)}")
elif torch.backends.mps.is_available():
    DEVICE = "mps"
    DTYPE = torch.float16
    print("[设备] Apple MPS")
else:
    DEVICE = "cpu"
    DTYPE = torch.float32
    print("[设备] CPU（很慢）")

from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

MODEL = "Qwen/Qwen3-4B"
# adapter 路径：训练后在 outputs/yuqian_4b/ 下
ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/yuqian_4b"

print(f"加载中...（模型={MODEL}, adapter={ADAPTER}）")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
base = AutoModelForCausalLM.from_pretrained(
    MODEL, dtype=DTYPE, device_map="auto", trust_remote_code=True
)
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()
print("加载完成！输入 quit 退出。\n")

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
    history.append({"role": "assistant", "content": resp})
    if len(history) > 12:
        history = history[-12:]
    print(f"于谦> {resp}\n")

print("得，回见了您内。")
