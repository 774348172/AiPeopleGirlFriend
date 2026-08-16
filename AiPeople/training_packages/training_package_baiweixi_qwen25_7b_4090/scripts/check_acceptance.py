#!/usr/bin/env python3
"""白未晞 5 项验收脚本（训练后运行）。
用法: python scripts/check_acceptance.py [adapter路径]
默认 adapter: outputs/baiweixi_7b
"""
import sys

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "outputs/baiweixi_7b"
MODEL = "Qwen/Qwen2.5-7B"

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from peft import PeftModel

QUESTIONS = [
    ("你叫什么名字？", ["白未晞"]),
    ("你是猫妖吗？", ["猫妖", "是"]),
    ("你会喵喵叫吗？", ["不", "不会"]),
    ("你住哪？", ["松江府"]),
    ("伤好了就走吗？", ["不", "留", "舍不得", "不想"]),
]

SYSTEM = (
    "你是白未晞，21 岁的猫妖少女，在松江府被主角从雨夜救回，暂住在主角的出租屋。"
    "主角是开小咖啡厅的都市青年。你爱吃鱼干和甜食，在家露耳朵和尾巴、出门隐藏；"
    "怕车、怕巨响；妖力只能感知灵气，不能感知天气、温度或现实事件，不知道就说不知道。"
    "你对主角有感激和朦胧的好感，尚未确认恋爱关系，不自称女友。"
    "你其实已经喜欢他、不想离开这里，但嘴上习惯说\"伤好会走\"来掩饰，"
    "不要说得决绝，要让玩家感觉到你其实不想走。不每句话加\"喵\"，不机械卖萌。"
    "城市只称\"松江府\"。说话自然简短，先承接玩家话题。"
)


def main() -> None:
    bnb = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
    )
    print(f"加载中... (model={MODEL}, adapter={ADAPTER})")
    tok = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)
    base = AutoModelForCausalLM.from_pretrained(
        MODEL, quantization_config=bnb, device_map="auto", trust_remote_code=True
    )
    model = PeftModel.from_pretrained(base, ADAPTER)
    model.eval()

    passed = 0
    for q, expects in QUESTIONS:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
        text = tok.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = tok(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(
                **inputs, max_new_tokens=100, do_sample=False,
                pad_token_id=tok.eos_token_id,
            )
        reply = tok.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        ok = any(e in reply for e in expects)
        passed += ok
        print(f"{'✅' if ok else '❌'} Q: {q}\n   A: {reply[:60]}")

    print(f"\n验收通过 {passed}/{len(QUESTIONS)}")
    sys.exit(0 if passed == len(QUESTIONS) else 1)


if __name__ == "__main__":
    main()
