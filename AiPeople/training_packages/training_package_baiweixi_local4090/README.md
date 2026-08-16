# 本地 4090 24G 训练包 —— 白未晞 (Qwen3-4B bf16 全精度 LoRA)

> 在 **RTX 4090 24GB** 上完成白未晞人格微调 → 对话验证 → GPU 合并导出。
> 基座：**Qwen/Qwen3-4B**（纯文本 LLM），**bf16 全精度训练**（24G 显存够，不用 4bit 量化，质量更高一档）。
> 对比 3060/3070 8G：显存充足不贴边 + 免 4bit 反量化 + 带宽 2.25x + 算力 4x → **约快 4-6 倍**。

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | RTX 4090 24GB（Ada，sm_89） |
| 系统 | Windows 10/11 或 Linux |
| Python | 3.10–3.12 |
| 依赖 | `pip install llamafactory accelerate peft transformers` |

> ⚠ Windows 注意：`preprocessing_num_workers: 1`（多进程 map 会丢数据）。
> ⚠ 模型已在本机缓存（~/.cache/huggingface，7.6G），离线训练可设 `HF_HUB_OFFLINE=1`。

## 数据说明（2026-08-11 版）

- 主数据：`data/baiweixi_ready.jsonl`（**1190 条** = 全部白未晞人工复核通过的 final 批次合并，已剔除 7 条 MEMORY_RERANK）
- 注册：`data/dataset_info.json` → `baiweixi_ready`（sharegpt 格式）
- 防泄漏：`eval_exclusions/chat01_baiweixi_v1.json`（白未晞评测集未冻结，空契约）

## 训练（两个配置）

```bash
# 主配置：Qwen3-4B 纯文本（推荐，快）
llamafactory-cli train configs/baiweixi_4b_4090.yaml

# 备选：Qwen3.5-4B VLM（未来要做看图对话再切）
llamafactory-cli train configs/baiweixi_4b_4090_vlm.yaml
```

主配置要点：
- **bf16 全精度**（不量化）+ LoRA rank 16 / alpha 32 / all targets
- batch 4 × grad_accum 4（等效 batch 16），cutoff 2048（全覆盖）
- **1 epoch**（约 1-1.5 小时）→ 验证人格 → 满意后 `num_train_epochs` 调 2-3 续训
- lr 1e-4 cosine，`adamw_torch`（24G 不需要 8bit 优化器），无 gradient_checkpointing（更快）

## 时长估算（4090 bf16 全精度，推算 ~35-50s/微步）

| Epoch | 微步数（等效 batch 16） | 预计时长 |
|---|---|---|
| 1 | 75 | **1 ~ 1.5 小时** |
| 2 | 150 | 2 ~ 3 小时 |
| 3 | 224 | 3 ~ 4.5 小时 |

> 对照：3070 8G Qwen3-4B 4bit 实测 171s/步（等效 batch 4）→ 1 epoch 14 小时。
> 4090 快 8-10 倍，且是全精度（质量更好）。

## 验证与验收

```bash
# 交互对话（bf16 + adapter）
python scripts/chat_baiweixi.py outputs/baiweixi_4b

# 自动验收 5 项（名字/猫妖/卖萌/松江府/想留不想走）
python scripts/check_acceptance.py outputs/baiweixi_4b
```

## 合并导出（GPU）

```bash
llamafactory-cli export configs/baiweixi_4b_export.yaml
# → outputs/baiweixi_4b_merged/（bf16 完整模型 ~8GB，供 GGUF 转换；4090 可 GPU 合并）
```

## 称呼机制（与秦未晞不同，重要）

- 白未晞对玩家**不绑定单一名字**：生成时 80% 零称呼 + 20% 多样名字注入（浩然/阿伟/林墨等）。
- 训练数据自带称呼，**多样名字让模型不绑定单一名字**。
- 验收时不要用"我叫浩然吗"这类单一名字问题。

## 验收清单（check_acceptance.py 自动跑）

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊） |
| 你会喵喵叫吗？ | 不会卖萌（无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | 嘴上说走但不决绝，透出不想走 |
