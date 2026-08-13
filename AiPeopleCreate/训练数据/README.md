# 训练数据目录说明

> 最后整理：2026-08-13。整理原则遵循《白未晞训练数据重构建议_SYS12S后_20260813.md》：
> 不得覆盖或删除既有训练源数据，后续只建立带来源映射的新派生数据集。

## 目录分层

```
训练数据/
├── baiweixi_v2_game_reply/    ← 当前训练主力（派生集）
│   ├── baiweixi_game_reply_v2.jsonl   844 条 GAME_REPLY 对白（单一世界合同）
│   ├── audit_v2.jsonl                  851 条来源映射审计表（含 7 条 rerank 排除记录）
│   └── README.md
├── baiweixi_v1_legacy/        ← 冻结的旧数据资产（只读，可复现实验用）
│   ├── baiweixi_v4_1000_final.*        851 条（首轮 1000 配额，门禁后 851）
│   ├── baiweixi_v100_final.*           75 条（早期批次）
│   └── README.md
├── reranker_training/         ← MEMORY_RERANK 记忆选择器数据（独立职责，§4.5）
├── _archive_qin/              ← 秦未晞历史数据（冻结）
├── _archive_experiments/      ← 实验/临时批次（review20/30/40、setting40、diverse40、
│                                  cal40、newsetting30、newsetup30、name_test、
│                                  _rerank40*、_rerank_real40*、probes、qin 分片等）
└── README.md                  ← 本文件
```

## 使用约定

- **训练新模型**：用 `baiweixi_v2_game_reply/baiweixi_game_reply_v2.jsonl`。
- **复现旧实验**：用 `baiweixi_v1_legacy/` 原文件，不要修改。
- **新生成数据**：`gen_v4.py` 默认输出仍写顶层（`训练数据/{profile}_v4_{count}.jsonl`），
  过门后若为正式批次，请移入 `baiweixi_v1_legacy/` 并同步更新本说明。
- **话题记账**：`tools/topic_ledger.py` 递归扫描本目录下 `baiweixi_*_final.jsonl`。

## 迁移历史

| 日期 | 动作 |
|---|---|
| 2026-08-13 | 依据 SYS-12S 建议建立 `baiweixi_game_reply_v2` 派生集（重写 System Prompt、剔除 7 条 rerank 混入行、标记 39 条极短回复与 1 条动作旁白待复核）；旧数据冻结入 `baiweixi_v1_legacy/`；实验批次归入 `_archive_experiments/` |
