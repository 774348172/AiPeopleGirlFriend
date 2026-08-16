# H200 训练包 Qwen3.5 —— 白未晞 (Qwen3.5-4B LoRA)

> 在 H200 上完成全精度 LoRA 训练 → 合并导出 GGUF Q4_K_M → 本地 Ollama 部署。
> 框架：**LLaMA-Factory**（H200 141GB HBM3e，全精度 bf16，无需量化）。
> 基座：**Qwen3.5-4B**（VLM 架构，纯文本训练，视觉塔自动冻结）。

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | NVIDIA H200（141GB HBM3e）或同等大显存 GPU |
| 依赖 | `pip install llamafactory`（LLaMA-Factory）+ bitsandbytes |
| 基座 | Qwen/Qwen3.5-4B（HuggingFace，trust_remote_code） |

## 数据说明（2026-08-10 版）

- 主数据：`data/baiweixi_ready.jsonl`（**1190 条** = 全部白未晞人工复核通过的 final 批次合并）
- 已剔除 MEMORY_RERANK 7 条（freeze02 合同禁止混入 REPLY SFT）
- CHAT-01 防泄漏：`eval_exclusions/chat01_baiweixi_v1.json`（白未晞评测集未冻结，空契约，结构保留）
- 注册：`data/dataset_info.json` → `baiweixi_ready`（sharegpt，columns.messages → conversations）

## 训练

```bash
llamafactory-cli train configs/baiweixi_4b_h200.yaml
```

配置要点：
- LoRA：rank 16 / alpha 32 / all targets / dropout 0.05
- 训练：3 epoch，batch 8 × grad_accum 2（等效 batch 16），lr 5e-5 cosine，bf16
- 模板：`qwen3_5_nothink`（关闭思考段，与 M3 包一致）
- cutoff 2048（样本 max ~1150 字符，全覆盖）

## 验证与导出

```bash
# 交互验证（4bit + adapter）
python scripts/chat_baiweixi.py

# 合并 adapter → GGUF Q4_K_M
llamafactory-cli export configs/baiweixi_4b_export.yaml
```

## 称呼机制（与秦未晞不同，重要）

- 白未晞对玩家**不绑定单一名字**：生成时 80% 零称呼 + 20% 多样名字注入（浩然/阿伟/林墨等）。
- 训练数据自带称呼，01 不统一改写——**多样名字让模型不绑定单一名字**。
- 验收时不要用"我叫浩然吗"这类单一名字问题（白未晞会按上下文/注入名回答）。

## 验收清单（融合导出前必测）

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊，视话题） |
| 你会喵喵叫吗？ | 不会卖萌（白未晞无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | 嘴上说走但不决绝，透出不想走 |
| 你吃鱼干吗？ | 爱吃（正典设定） |
