# Qwen3-Reranker-0.6B 训练包 —— 白未晞记忆选择器（RTX 4090 专用）

> **0.6B 记忆选择器（reranker）** 训练包，用于白未晞长期记忆的精排召回。
> 对应 AI 程序侧 `select_e2e_profile_v1.json` 的 `formal_model_id: Qwen/Qwen3-Reranker-0.6B`。
> 框架：transformers + peft（LoRA），方案 V2 §9 目标函数（yes/no logits + BCEWithLogits）。

## 数据（640 条单对记录）

`data/baiweixi_reranker_train_v1.jsonl`：
- **640 条** query × candidate 单对记录（池场景 320 + 真实对话 320）
- 每条含 query_text / candidate_memory_text / label（positive/hard_negative/easy_negative）/ relevance_score / should_recall / label_evidence
- 候选记忆来自 45 个正典单元（15 timeline 事件 + 30 canon_fact），8 候选/样本均衡采样
- 符合 `F:\ai-girlfriend\AiPeople\eval\training_contract\schemas\reranker_record_baiweixi.schema.json`（640/640 校验通过）

标签分布：positive 202（32%）/ hard_negative 122（19%）/ easy_negative 316（49%）

## 训练（RTX 4090，bf16 全精度）

```bash
# 默认基座 Qwen/Qwen3-Reranker-0.6B（需联网下载 ~1.2GB；可设 HF_ENDPOINT=https://hf-mirror.com）
python scripts/train_reranker.py \
    --data data/baiweixi_reranker_train_v1.jsonl \
    --epochs 3 --batch-size 16 --out outputs/reranker_baiweixi_0_6b
```

默认参数（4090 最优）：

| 设置 | 值 | 说明 |
|---|---|---|
| 精度 | bf16 全精度 | 0.6B 显存 ~3GB，无需量化 |
| LoRA | rank 16 / alpha 32 / q,k,v,o | |
| epoch | 3 | 640 条充分学习 |
| lr | 2e-4 | cosine 调度 |
| batch | 16 | 4090 大 batch |
| 显存 | ~3GB | 4090 轻松 |
| 时长 | 3 epoch ≈ **5-10 分钟** | 0.6B 训练极快 |

> ⚠ 本地无 Qwen3-Reranker-0.6B 缓存时，可先用 `--model Qwen/Qwen3-1.7B` 验证流程
> （本机已缓存，方案 V2 允许 Qwen 同族模型承担精排职责）。
> ⚠ 8G 显存机器加 `--quant-4bit` 即可（QLoRA）。

## 目标函数（方案 V2 §9.3）

```text
activation_logit = logit("yes") - logit("no")
hard_probability = sigmoid(activation_logit)
loss = BCEWithLogits(activation_logit, hard_label)
```

固定指令（版本化训练合同，勿改）：
> Given a current intimate conversation and a past memory, judge whether the memory should be activated as background for Bai Weixi's natural reply. Reject memories that are merely topically related but unnecessary. Activation does not mean the memory must be explicitly mentioned.

## 验证

```bash
python scripts/eval_reranker.py \
    --data data/baiweixi_reranker_train_v1.jsonl \
    --adapter outputs/reranker_baiweixi_0_6b
```

输出：dev 集准确率 / positive 召回 / negative 拒绝 + 真实场景排序演示。
当前 Qwen3-1.7B 基线（640 条 2 epoch）：准确率 70.3%、negative 拒绝 86%、positive 召回 38%（0.6B 目标：positive 召回 ≥60%）。

## 部署（select_e2e 合同）

- 合并 LoRA → 转 GGUF（0.6B Q8_0 ~0.7GB / Q4_K_M ~0.4GB）
- 运行时：Top32 候选批量打分（yes/no logits），阈值经验证集冻结，超时 fail_closed
- 显存预算：0.6B Q8_0 推理 <1.5GB，满足 P0 总峰值 <5120MiB

## 环境

- 硬件：RTX 4090 24GB（8G 加 --quant-4bit 亦可）
- 依赖：`pip install transformers peft accelerate bitsandbytes`
- 基座：`Qwen/Qwen3-Reranker-0.6B`（首次自动下载；镜像 `HF_ENDPOINT=https://hf-mirror.com`）
