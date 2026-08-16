# Qwen2.5-7B 训练包 —— 白未晞（RTX 4090 24GB 专用）

> **4090 24GB 专用**：bf16 全精度训练（方案 A 最优）→ 合并 → GGUF Q5_K_M 推理部署。
> 数据：**1190 条**人工复核通过的 REPLY 数据（`data/baiweixi_ready.jsonl`）。
> 框架：**LLaMA-Factory**（SFT + LoRA），模板 `qwen`（Qwen2.5 ChatML）。

## 为什么是方案 A（bf16 全精度）

训练精度无损 → 推理量化只压一次 → **量化后保留人格最多**。
对比 8G 的 4bit QLoRA：4bit 训练 + Q4 推理 = 双重量化，人格会再糊一层。
**4090 24G 显存够跑 bf16，这是效果最优的路径。**

## 训练配置（baiweixi_7b_4090.yaml）

| 设置 | 值 | 说明 |
|---|---|---|
| 精度 | **bf16 全精度** | 不量化训练 |
| LoRA | rank 32 / alpha 64 / all targets | 7B 容量大，细节更足 |
| epoch | **3** | 1190 条充分学习 |
| lr | **5e-5** | 低 lr 配 3 epoch 防过拟合 |
| batch | 2 × 8 = 等效 16 | 梯度平滑 |
| cutoff | 2048 | 全覆盖 |
| gradient_checkpointing | 开 | 显存 ~22GB ✅ |
| 时长 | 3 epoch ≈ **7-9 小时** | 1 epoch ≈ 2-3h |

```bash
llamafactory-cli train configs/baiweixi_7b_4090.yaml
```

## 训练后部署流程

```bash
# 1. 合并 adapter → bf16 完整模型（4090 可 GPU 合并）
llamafactory-cli export configs/baiweixi_7b_export.yaml

# 2. HF → GGUF（F16）
python llama.cpp/convert_hf_to_gguf.py outputs/baiweixi_7b_merged \
    --outfile outputs/baiweixi_7b_f16.gguf --outtype f16

# 3. 量化 Q5_K_M（8G 推理推荐档；显存富余可 Q6_K）
llama.cpp/llama-quantize outputs/baiweixi_7b_f16.gguf \
    outputs/baiweixi_7b_q5_k_m.gguf Q5_K_M

# 4. Ollama 部署
ollama create baiweixi-7b -f Modelfile   # 参考 04_ollama 思路（TEMPLATE 用 qwen ChatML）
```

**量化档位**（8G 推理）：

| 档位 | 体积 | 显存 | 质量 |
|---|---|---|---|
| Q6_K | ~6.6GB | ~7.5GB | 好 |
| **Q5_K_M** | ~5.4GB | ~6GB | **推荐** |
| Q4_K_M | ~4.6GB | ~5GB | 最省 |

## 验收（必须推理形态复测）

```bash
# adapter 快速验证
python scripts/check_acceptance.py outputs/baiweixi_7b
# ⭐ GGUF 部署后复测（量化可能掉人格）
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

- 硬件：RTX 4090 24GB（或 4080 Super 16G，batch 降 1 + cutoff 1536）
- 系统：Windows 10/11 或 Linux；Python 3.10-3.12
- 依赖：`pip install llamafactory accelerate peft transformers`
- 基座：`Qwen/Qwen2.5-7B`（首次自动下载 ~15GB；可设 `HF_ENDPOINT=https://hf-mirror.com`）
- ⚠ Windows：`preprocessing_num_workers: 1`（多进程 map 会丢数据）
