# 本地 3060 8G 训练包 Qwen3.5 —— 白未晞 (Qwen3.5-4B QLoRA)

> 在 **RTX 3060 8G**（或 3070 8G）上完成白未晞人格微调 → 对话验证 → CPU 合并导出。
> 框架：**LLaMA-Factory**（4bit QLoRA，8G 显存必选）。
> 基座：**Qwen/Qwen3.5-4B**（VLM 架构，纯文本训练，视觉塔自动冻结）。

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | RTX 3060 8G / 3070 8G（Ampere，sm_86，支持 bf16） |
| 系统 | Windows 10/11 |
| Python | 3.10–3.12 |
| 依赖 | `pip install llamafactory bitsandbytes accelerate peft transformers` |

> ⚠ Windows 注意：`preprocessing_num_workers: 1`（datasets 多进程 map 在 Windows 会丢数据）。
> ⚠ 优化器用 `adamw_bnb_8bit`，**不要用 paged 版**（Windows 上实测慢到 ~46s/微步）。

## 数据说明（2026-08-10 版）

- 主数据：`data/baiweixi_ready.jsonl`（**1190 条** = 全部白未晞人工复核通过的 final 批次合并，已剔除 7 条 MEMORY_RERANK）
- 注册：`data/dataset_info.json` → `baiweixi_ready`（sharegpt 格式）
- 防泄漏：`eval_exclusions/chat01_baiweixi_v1.json`（白未晞评测集未冻结，空契约）

## 训练

```bash
llamafactory-cli train configs/baiweixi_4b_8g.yaml
```

配置要点（8G 显存）：
- 4bit QLoRA（nf4）+ LoRA rank 16 / alpha 32 / all targets
- batch 1 × grad_accum 4（等效 batch 4），cutoff 1280
- **1 epoch**（约 8-10 小时）→ 验证人格 → 满意后 `num_train_epochs` 调 2-3 续训
- lr 1e-4 cosine，bf16，gradient_checkpointing

## 时长估算（3060 8G 实测 ~100-115s/微步）

| Epoch | 微步数 | 预计时长 |
|---|---|---|
| 1 | 298 | **8 ~ 9.5 小时** |
| 2 | 595 | **16.5 ~ 19 小时** |
| 3 | 892 | **25 ~ 28.5 小时** |

> 若运行中 s/it 明显 >120s：把 `cutoff_len` 降到 1024，或 `gradient_accumulation_steps` 降到 2。

## 验证与验收

```bash
# 交互对话（4bit + adapter）
python scripts/chat_baiweixi.py outputs/baiweixi_4b

# 自动验收 5 项（名字/猫妖/卖萌/松江府/想留不想走）
python scripts/check_acceptance.py outputs/baiweixi_4b
```

## 合并导出（CPU）

```bash
llamafactory-cli export configs/baiweixi_4b_export.yaml
# → outputs/baiweixi_4b_merged/（bf16 完整模型 ~8GB，供 GGUF 转换）
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
