#!/usr/bin/env python3
"""合并 adapter + 转 GGUF（Q4_K_M）。
训练完后跑这个脚本，产出可直接 ship 给玩家的 GGUF 文件。

用法: python scripts/export_gguf.py
前提: 已完成训练（outputs/yuqian_4b/ 下有 adapter）
"""
import os
import sys
import subprocess

ADAPTER_DIR = "outputs/yuqian_4b"
MERGED_DIR = "outputs/yuqian_4b_merged"
GGUF_FILE = "outputs/yuqian_4b_q4.gguf"
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
# 需要 llama.cpp 的 convert 脚本
llama_cpp_convert = "llama.cpp/convert_hf_to_gguf.py"
if not os.path.exists(llama_cpp_convert):
    print("llama.cpp 未找到，请先 clone:")
    print("  git clone https://github.com/ggml-org/llama.cpp")
    print("  cd llama.cpp && make")
    sys.exit(1)

cmd = [
    sys.executable, llama_cpp_convert,
    MERGED_DIR,
    "--outfile", GGUF_FILE,
    "--outtype", "q4_k_m",
]
print(f"运行: {' '.join(cmd)}")
subprocess.run(cmd, check=True)
print(f"\nGGUF 完成: {GGUF_FILE}")
size_mb = os.path.getsize(GGUF_FILE) / 1024 / 1024
print(f"文件大小: {size_mb:.0f} MB")
print(f"\n这个文件就是 ship 给玩家的产物。")
