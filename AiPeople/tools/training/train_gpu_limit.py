"""带 GPU 显存限制的训练启动脚本。

在 LF 启动前注入 torch.cuda.set_per_process_memory_fraction，
限制 PyTorch 只用指定比例的显存，留出空间给桌面/其它程序。

用法: .venv/Scripts/python.exe train_gpu_limit.py [显存比例 0-1]
默认 0.5（8GB 卡 → PyTorch 最多用 4GB，留 4GB 给桌面）
"""
import os
import sys

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("DISABLE_VERSION_CHECK", "1")

fraction = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
print(f"[GPU限制] 显存占比: {fraction:.0%}")

import torch
if fraction < 1.0:
    torch.cuda.set_per_process_memory_fraction(fraction)
    print(f"[GPU限制] 已限制显存到 {fraction:.0%}（{8192*fraction:.0f}MB / 8GB）")
else:
    print(f"[GPU限制] 不限制显存（满载，桌面可能卡 3-5 分钟）")
print(f"[GPU限制] device={torch.cuda.get_device_name(0)}")

# 降进程优先级（让桌面调度优先）
try:
    import ctypes
    BELOW_NORMAL = 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
    print("[GPU限制] 进程优先级设为 BELOW_NORMAL（桌面优先）")
except Exception as e:
    print(f"[GPU限制] 设优先级失败: {e}")

# 启动 LF 训练
config_name = sys.argv[2] if len(sys.argv) > 2 else "qwen3_4b_qlora"
config_path = f"training/configs/{config_name}.yaml"
print(f"[训练] 配置: {config_path}")
from llamafactory.cli import main as lf_main
sys.argv = ["llamafactory-cli", "train", config_path]
lf_main()
