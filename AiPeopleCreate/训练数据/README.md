# 训练数据目录说明

> 最后整理：2026-08-15（T19 扩充归档）。整理原则遵循《白未晞训练数据重构建议_SYS12S后_20260813.md》：
> 不得覆盖或删除既有训练源数据，后续只建立带来源映射的新派生数据集。

## 目录分层

```
训练数据/
├── baiweixi_v4_training.jsonl  ← 当前训练主文件（2882 行 ShareGPT，game+memory 两轨合并）
├── baiweixi_v4_final.*         ← 最终批次源（2882 行，全 15 类型，含 G7 放行）
├── baiweixi_v4_game_reply/     ← GAME_REPLY 轨派生集（2741 条，固定锚）
│   ├── baiweixi_game_reply_v4.jsonl
│   └── audit_v4.jsonl
├── baiweixi_v4_memory_reply/   ← MEMORY_REPLY 轨派生集（141 条，锚带记忆证据帧）
│   ├── baiweixi_memory_reply_v4.jsonl
│   └── audit_v4.jsonl
├── baiweixi_v1_legacy/         ← 冻结的旧数据资产（只读，可复现实验用）
├── baiweixi_v2_game_reply/     ← 旧派生集（冻结；来自 v1 批次 844 条，只读）
├── reranker_training/          ← MEMORY_RERANK 记忆选择器数据（独立职责）
├── _archive_experiments/       ← 归档：中间批次源（v4_1089/v4_2089）、v3 派生集、
│                                  G7 复核子集与结果、t19 实验批次
├── _archive_qin/               ← 秦未晞历史数据（冻结）
└── README.md                   ← 本文件
```

## 当前训练数据（2026-08-15 扩充归档后）

**主训练文件：`baiweixi_v4_training.jsonl`（1973 行 · 话题级去重版）**

全量 2882 行（T19 987 + 扩池重跑 1659 + G7 放行 236）经**话题级去重**：每话题保留 judge 分
top 2，消除跨批次/轮转重复（全量保留在 `baiweixi_v4_final.*`）。

| 类型 | 行数 |
|---|---|
| reply_casual | 574 |
| reply_romance | 427 |
| reply_emotion | 209 |
| reply_protective | 184 |
| reply_item | 118 |
| reply_memory | 98 |
| reply_identity | 76 |
| reply_supportive | 61 |
| reply_general | 57 |
| reply_safety | 36 |
| reply_canon_qa | 34 |
| reply_correction | 29 |
| reply_vague | 28 |
| reply_quiet_company | 21 |
| reply_boundary | 21 |
| **合计** | **1973** |

记忆类型：persona 110 / item 118 / general 57 / special 98 / 日常 1590。

## 话题记账与互补生成（2026-08-15）

- 已用话题 1087 个登记于 `_topic_ledger.jsonl`（`tools/topic_ledger.py --scan`）。
- 未用话题 26 个（casual 2 / romance 10 / identity 2 / emotion 1 / protective 2 /
  correction 2 / quiet_company 2 / canon_qa 3 / memory 1）：`tools/topic_ledger.py --unused`。
- **下次扩充用 `--topics` 只生成未用话题** → 与现有数据零话题重复（批次间互补）。

## 使用约定

- **训练新模型**：用 `baiweixi_v4_training.jsonl`（或两轨分文件：game 2741 + memory 141）。
- **复现旧实验**：用 `baiweixi_v1_legacy/` 原文件，不要修改。
- **话题记账**：`tools/topic_ledger.py` 递归扫描本目录下 `baiweixi_*_final.jsonl`。

## 迁移历史

| 日期 | 动作 |
|---|---|
| 2026-08-13 | 建立 v2 派生集（844 条）；旧数据冻结入 v1_legacy |
| 2026-08-15 | T19 记忆类型轴闭环（987 行 → v3 派生集两轨）；设定补齐 + G7 复核；扩池（雷同修复）重跑 1659 行；最终合并 2882 行 → v4 派生集两轨（game 2741 + memory 141）+ 训练主文件；中间批次与 v3 归档 |
