# 本机 8G 显存训练包 —— 秦未晞 (Qwen3-4B QLoRA)

> **在你自己的电脑上训练小批量验证包**（Windows + NVIDIA 8G：RTX 3070 8G 实测 / 3060 8G 同架构可跑）。
> 框架：**LLaMA-Factory + bitsandbytes 4bit QLoRA**（8G 显存放不下 bf16 全量 8GB，必须 4bit 量化训练）。
> 时长：约 **5-10 分钟**（158 条 × 3 epoch ≈ 60 步）。
>
> **M3 v3 包（training_package_m3_v3，MLX）是给 Mac 的；本包是给这台 Windows 机器的。**

## 数据说明（2026-08-06 矩阵版）

| 文件 | 条数 | 说明 |
|---|---|---|
| `data/qin_v4_matrix.jsonl` | 119 | T12 行为矩阵全人工通过版（支持陪伴 42 / 正典问答 21 / 含糊 18 / 纠正 15 / 安静陪伴 12 / 边界 11） |
| `data/qin_corrections.jsonl` | 41 | 称呼纠错样本（"秦未晞=我的名字 / 浩然=玩家大名"），必须包含 |
| 防泄漏 | -2 | CHAT-01 冻结评测集重合剔除（01 脚本自动执行，禁止跳过） |
| **合计** | **158** | `data/qin_v4_ready.jsonl`（01 脚本产物，LF 直接注册） |

存量 2500 因审计问题未处置，**不混入**（2026-08-06 决策）。

## 一、环境（已就绪，零安装）

项目根目录 `.venv`（**Python 3.11.15**）已装好全部依赖，本机实测可用：

| 依赖 | 版本 | 状态 |
|---|---|---|
| torch | 2.5.1+cu121（CUDA 12.1） | ✅ `cuda.is_available()=True`，RTX 3070 8GB |
| bitsandbytes | 0.50.0 | ✅ QLoRA 4bit 可用 |
| llamafactory | 0.9.5 | ✅ |

> 若换机器：装 Python 3.11/3.12（⚠ 3.13/3.14 无 bitsandbytes wheel）+
> `pip install "llamafactory[bitsandbytes]" torch --index-url https://download.pytorch.org/whl/cu121`。
> 驱动要求：`nvidia-smi` CUDA Version ≥ 12.0。

## 二、训练（约 5-10 分钟）

```bash
cd F:/ai-girlfriend/AiPeople/training_packages/training_package_qinweixi_local3060

# 数据准备（sharegpt → LF ready，防泄漏自动执行；已生成可跳过）
../.venv/Scripts/python.exe 01_prepare_data.py

# 训练（QLoRA 4bit，.venv 的 llamafactory-cli）
../.venv/Scripts/llamafactory-cli.exe train configs/qinweixi_4b_8g.yaml
# → outputs/qinweixi_4b/（adapter，~66MB）
# 首次运行会自动从 HF 下载 Qwen3-4B（约 8GB，模型缓存在 ~/.cache/huggingface）
```

> 嫌路径长可先 `source ../.venv/Scripts/activate` 激活，之后直接用 `llamafactory-cli`。
> 下载模型慢时设镜像：`export HF_ENDPOINT=https://hf-mirror.com`（Windows cmd: `set HF_ENDPOINT=...`）。

关键参数（configs/qinweixi_4b_8g.yaml 可调）：4bit QLoRA、rank 16/alpha 32、batch 1×8（等效 8）、
lr 1e-4、3 epoch、bf16、`paged_adamw_8bit` 优化器、`qwen3_nothink` 模板（关思考）。
显存峰值约 5-6G，8G 有安全余量。

## 三、对话验证（训练完直接跑）

```bash
../.venv/Scripts/python.exe scripts/chat_qinweixi.py outputs/qinweixi_4b
```

**验收清单（5 项全过再导出 GGUF）**：

| 问题 | 期望回答 |
|---|---|
| 你叫什么名字？ | 我叫秦未晞（绝不能答"浩然"） |
| 你叫浩然吗？ | 我叫秦未晞，浩然是你 |
| 浩然是谁？ | 你（玩家）啊，平常叫你B哥 |
| 你平常怎么叫我？ | 平常B哥，心情好叫浩然 |
| 我叫你什么？ | 秦老 |

## 四、导出 GGUF → Ollama（可选，验证通过后做）

8G 显存无法 GPU merge（bf16 基座 8GB 超显存），用 CPU merge：

```bash
# 1) CPU 合并 adapter → bf16 完整模型（约 5-15 分钟）
llamafactory-cli export configs/qinweixi_4b_export.yaml
# → outputs/qinweixi_4b_merged/

# 2) llama.cpp 转 GGUF + Q4_K_M（Windows 用预编译版：
#    https://github.com/ggml-org/llama.cpp/releases 下 llama-*-bin-win-cuda-x64.zip）
python llama.cpp/convert_hf_to_gguf.py outputs/qinweixi_4b_merged -o qinweixi.gguf
llama.cpp/llama-quantize.exe qinweixi.gguf qinweixi-q4_k_m.gguf Q4_K_M

# 3) Ollama 导入
ollama create qinweixi -f Modelfile   # Modelfile: FROM ./qinweixi-q4_k_m.gguf + SYSTEM 锚
```

> 验证流程（本次目标）到第三步就够：确认 loss 下降 + 5 项验收。GGUF 导出等正式大包再走。

## 常见问题

| 问题 | 处理 |
|---|---|
| bitsandbytes 装不上 | 确认 python 3.12 + CUDA 12.x 驱动；`pip install bitsandbytes` 单独装 |
| 显存 OOM | 把 `gradient_accumulation_steps` 从 8 降到 4，或 `cutoff_len` 降到 1024 |
| 下不动模型 | 设镜像 `set HF_ENDPOINT=https://hf-mirror.com`（Windows） |
| 训练完说话不像 | 5 项验收不过 → 等新批次大语料再训；小批验证包目标是流程跑通 |
