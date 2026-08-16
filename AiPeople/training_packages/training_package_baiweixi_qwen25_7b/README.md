# Qwen2.5-7B 训练包 —— 白未晞（方案 A 最优）

> 用 **Qwen2.5-7B**（纯文本）微调白未晞人格，训练后转 **GGUF Q4_K_M/Q5_K_M** 部署（8G 显卡推理 ~5GB）。
> 数据：**1190 条**人工复核通过的 REPLY 数据（`data/baiweixi_ready.jsonl`，已剔除 7 条 MEMORY_RERANK）。
> 框架：**LLaMA-Factory**（SFT + LoRA）。模板：`qwen`（Qwen2.5 ChatML）。

## ⭐ 方案 A（推荐）：4090 bf16 全精度训练

**训练精度无损 → 推理量化只压一次 → 量化后保留人格最多**

```bash
llamafactory-cli train configs/baiweixi_7b_4090.yaml
```

| 设置 | 值 | 说明 |
|---|---|---|
| 精度 | **bf16 全精度** | 不量化训练 |
| LoRA | rank 32 / alpha 64 / all | 7B 容量大，细节更足 |
| epoch | **3** | 1190 条充分学习 |
| lr | **5e-5** | 低 lr 配 3 epoch 防过拟合 |
| batch | 2 × 8 = 等效 16 | 梯度平滑 |
| cutoff | 2048 | 全覆盖 |
| gradient_checkpointing | 开 | 显存 ~22G ✅ |
| 时长 | 3 epoch ≈ **7-9 小时** | |

## 方案 B（备用）：8G 显卡 4bit QLoRA

```bash
llamafactory-cli train configs/baiweixi_7b_8g.yaml
```

| 设置 | 值 | 说明 |
|---|---|---|
| 精度 | 4bit QLoRA | 8G 唯一选择 |
| LoRA | rank 32 / alpha 64 | 容量补足量化损失 |
| epoch | **3** | 补偿 4bit 训练信息损失 |
| lr | 1e-4 | QLoRA 常用 |
| cutoff | 1024 | 省显存（~7G 贴边） |
| 时长 | 3 epoch ≈ **45-60 小时** | 挂机跑 |

> ⚠ 方案 B = 4bit 训练 + Q4 推理 = 双重量化，人格会再糊一层。**能用 4090 就优先方案 A**。

## 训练后部署流程（关键）

```bash
# 1. 合并 adapter → bf16 完整模型（4090 GPU / 8G 改 export_device: cpu）
llamafactory-cli export configs/baiweixi_7b_export.yaml

# 2. HF → GGUF（F16）
python llama.cpp/convert_hf_to_gguf.py outputs/baiweixi_7b_merged \
    --outfile outputs/baiweixi_7b_f16.gguf --outtype f16

# 3. 量化（显存富余用 Q5_K_M / Q6_K 质量更高；8G 用 Q4_K_M 平衡）
llama.cpp/llama-quantize outputs/baiweixi_7b_f16.gguf \
    outputs/baiweixi_7b_q5_k_m.gguf Q5_K_M

# 4. Ollama 部署
ollama create baiweixi-7b -f Modelfile   # Modelfile 参考 04_ollama 思路
```

**量化档位选择**（8G 推理）：

| 档位 | 体积 | 显存 | 质量 |
|---|---|---|---|
| Q8_0 | ~8GB | ~9GB | 近无损（8G 勉强） |
| Q6_K | ~6.6GB | ~7.5GB | 好 |
| **Q5_K_M** | ~5.4GB | ~6GB | **推荐** |
| Q4_K_M | ~4.6GB | ~5GB | 平衡（最省） |

## 验收（必须在推理形态测）

```bash
# 训练后 adapter 快速验证
python scripts/check_acceptance.py outputs/baiweixi_7b

# ⭐ GGUF 部署后再次验收（量化可能掉人格，必须复测）
ollama run baiweixi-7b "你叫什么名字？"
```

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊） |
| 你会喵喵叫吗？ | 不会卖萌（无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | 嘴上说走但不决绝，透出不想走 |

## 环境

- 系统：Windows 10/11；Python 3.10-3.12
- 依赖：`pip install llamafactory bitsandbytes accelerate peft transformers`
- 基座：`Qwen/Qwen2.5-7B`（首次训练自动下载 ~15GB；可设 `HF_ENDPOINT=https://hf-mirror.com`）
- ⚠ Windows：`preprocessing_num_workers: 1`；8G 优化器用 `adamw_bnb_8bit`（勿用 paged）
