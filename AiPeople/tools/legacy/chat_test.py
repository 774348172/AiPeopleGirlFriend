"""交互式对话：和林知微自由聊天。
用法: .venv/Scripts/python.exe chat_test.py
输入 quit/exit/q 退出。
"""
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["CUDA_VISIBLE_DEVICES"] = "0"

import torch
torch.cuda.set_per_process_memory_fraction(0.6)  # 限制显存留给桌面

from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

MODEL = "Qwen/Qwen3-4B"
ADAPTER = "training/outputs/qwen3_4b_persona"

print("加载中...（约30秒）")
tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
quantization_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.float16,
    bnb_4bit_use_double_quant=True,
)
base = AutoModelForCausalLM.from_pretrained(
    MODEL,
    quantization_config=quantization_config,
    device_map="auto",
    trust_remote_code=True,
)
model = PeftModel.from_pretrained(base, ADAPTER)
model.eval()
print("加载完成！输入 quit 退出。\n")

# 对话历史（保持多轮上下文）
history = []

SYSTEM = "你是林知微，成都长大的独立插画师，现居上海，养了一只橘白猫豆豆。内向、温和、爱画画。1996年生。"

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

    prompt = tok.apply_chat_template(
        msgs,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if isinstance(prompt, list):
        prompt = prompt[0]
    inputs = tok(prompt, return_tensors="pt").to(model.device)

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

    print(f"林知微> {resp}\n")

print("再见。")
