#!/usr/bin/env python3
"""合并 adapter + 转 GGUF（Q4_K_M）——秦未晞。
训练完后跑这个脚本，产出可直接 ship 给玩家的 GGUF 文件。

用法: python scripts/export_gguf.py
前提: 已完成训练（outputs/qinweixi_4b/ 下有 adapter）
"""
import os
import subprocess

ADAPTER_DIR = "outputs/qinweixi_4b"
MERGED_DIR = "outputs/qinweixi_4b_merged"
GGUF_FILE = "outputs/qinweixi_4b_q4.gguf"
MODEL = "Qwen/Qwen3-4B"


def step(msg):
    print(f"\n{'='*50}\n{msg}\n{'='*50}")


# Step 1: 合并 adapter
step("Step 1: 合并 adapter 到基座")
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

print(f"加载基座: {MODEL}")
base = AutoModelForCausalLM.from_pretrained(MODEL, dtype="float16")
print(f"加载 adapter: {ADAPTER_DIR}")
model = PeftModel.from_pretrained(base, ADAPTER_DIR)
print("合并中...")
model = model.merge_and_unload()
model.save_pretrained(MERGED_DIR, safe_serialization=True)
tok = AutoTokenizer.from_pretrained(MODEL)
tok.save_pretrained(MERGED_DIR)
print(f"合并完成: {MERGED_DIR}")

# Step 2: 转 GGUF
step("Step 2: 转 GGUF (Q4_K_M)")
llama_cpp_convert = "llama.cpp/convert_hf_to_gguf.py"
if not os.path.exists(llama_cpp_convert):
    print("未找到 llama.cpp，先 clone 并编译:")
    subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp.git"], check=True)
    subprocess.run(["make", "-C", "llama.cpp", "-j"], check=True)

subprocess.run(
    [
        "python", llama_cpp_convert, MERGED_DIR,
        "--outfile", GGUF_FILE,
        "--outtype", "q4_k_m",
    ],
    check=True,
)
print(f"\n完成: {GGUF_FILE}（~2.3GB）—— 这就是 ship 给玩家的文件")
