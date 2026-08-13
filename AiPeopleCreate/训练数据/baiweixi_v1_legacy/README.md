# baiweixi_v1_legacy（冻结旧数据资产 · 只读）

> 依据《白未晞训练数据重构建议_SYS12S后_20260813.md》§10：
> 冻结旧数据作为可复现实验资产，**不直接清洗原文件**，不删除任何原始样本。
> 旧 System Prompt 已整体过期（含双世界/设备限制/内部协议概念），仅作复现与对照用，
> 训练新模型请使用 `../baiweixi_v2_game_reply/baiweixi_game_reply_v2.jsonl`。

## 批次清单

| 文件 | 条数 | 说明 |
|---|---|---|
| `baiweixi_v4_1000_final.jsonl` | 851 | 首轮 1000 配额（门禁后 851 条），主力对话批次，2026-08-10 |
| `baiweixi_v100_final.jsonl` | 75 | 早期 100 条验证批次，2026-08-10 |

每个批次含三件套：`.jsonl`（ShareGPT 训练行）+ `.metadata.jsonl`（类型/话题/质量分）+ `.gate_report.jsonl`（质量门决策记录）。

## 已知问题（v2 已处理）

- System Prompt 含"设备外现实世界/只能文字交谈/内部 JSON"等过期概念（844/851 条）。
- 7 条 MEMORY_RERANK 协议记录混入（`sha256:` 头）。
- 话题池轮转导致同话题多条（casual 364 条仅 169 唯一话题）。
- 39 条极短回复、1 条动作旁白（已标记，待人工复核）。
