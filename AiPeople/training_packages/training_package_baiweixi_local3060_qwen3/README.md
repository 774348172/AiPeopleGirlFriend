# 本地 8G 训练包 Qwen3 —— 白未晞 (Qwen3-4B QLoRA)

> 在 **RTX 3060 8G / 3070 8G** 上完成白未晞人格微调 → 对话验证 → CPU 合并导出。
> 基座：**Qwen/Qwen3-4B**（纯文本 LLM）。
> **为什么用 Qwen3-4B 而不是 Qwen3.5-4B**：本包是纯文字聊天游戏，不需要多模态；
> 同机实测 Qwen3-4B 训练 **470s/步 → 100s/步（快 4.7 倍）**，人格质量无差别（2026-08-10 实测）。

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | RTX 3060 8G / 3070 8G（Ampere，sm_86，支持 bf16） |
| 系统 | Windows 10/11 |
| Python | 3.10–3.12 |
| 依赖 | `pip install llamafactory bitsandbytes accelerate peft transformers` |

> ⚠ Windows 注意：`preprocessing_num_workers: 1`（多进程 map 会丢数据）。
> ⚠ 优化器用 `adamw_bnb_8bit`，不要用 paged 版（Windows 慢到 ~46s/微步）。
> 模型已在本机缓存（~/.cache/huggingface，7.6G），离线训练可设 `HF_HUB_OFFLINE=1`。

## 数据说明（2026-08-10 版）

- 主数据：`data/baiweixi_ready.jsonl`（**1190 条** = 全部白未晞人工复核通过的 final 批次合并，已剔除 7 条 MEMORY_RERANK）
- 注册：`data/dataset_info.json` → `baiweixi_ready`（sharegpt 格式）
- 防泄漏：`eval_exclusions/chat01_baiweixi_v1.json`（白未晞评测集未冻结，空契约）

### V2 单一世界提示净化（2026-08-12）

- 当前模型和 `checkpoint-200/298` 的原始训练来源仍保留在 `data/baiweixi_ready.jsonl`，不得覆盖，以便复现实验。
- `scripts/clean_single_world_prompts.py` 只替换 1190 条样本的 system 消息，human/gpt 对白逐字保留。
- 净化结果写入 `data_v2/baiweixi_dialogue_single_world_v2.jsonl`，审计写入 `data_v2/single_world_cleanup_manifest.json`。
- 新提示只声明松江府单一世界、主角对白、最新世界状态和记忆证据，不再向角色引入现实玩家、外部世界、设备、线下、虚拟人物、AI 或助手概念。
- 当前训练配置仍指向旧数据。V2 净化结果只是下一版混合数据的对白层输入，完成 V2 数据合同前禁止直接启动训练。

生成与校验：

```bash
python scripts/clean_single_world_prompts.py
python -m pytest tests/training_contract/test_baiweixi_single_world_cleanup.py -q
```

## 训练

```bash
llamafactory-cli train configs/baiweixi_4b_8g.yaml
```

配置要点（8G 显存）：
- 4bit QLoRA（nf4）+ LoRA rank 16 / alpha 32 / all targets
- batch 1 × grad_accum 4（等效 batch 4），cutoff 1280
- **1 epoch**（约 8-9 小时）→ 验证人格 → 满意后 `num_train_epochs` 调 2-3 续训
- lr 1e-4 cosine，bf16，gradient_checkpointing，`adamw_bnb_8bit`

## 时长估算（Qwen3-4B 实测 ~100s/微步）

| Epoch | 微步数 | 预计时长 |
|---|---|---|
| 1 | 298 | **8 ~ 9 小时** |
| 2 | 595 | **16 ~ 18 小时** |
| 3 | 892 | **25 ~ 27 小时** |

> 若 s/it 明显 >120s：`cutoff_len` 降到 1024，或 `gradient_accumulation_steps` 降到 2。
> 换 5070 Ti 16G：可去掉 4bit 量化改 bf16 全精度 + batch 2，1 epoch ≈ 6 小时。

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
