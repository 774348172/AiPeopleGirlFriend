# Legacy Baseline 存档清单（阶段 0 冻结）

> 2026-08-07 | 依据《通用数据生成器施工总计划_v2.md》§三 阶段 0（G0 门）
> 用途：冻结 v2 阶段 0 开工前的全部基线资产，供后续阶段审计对照；本快照只读。

## 一、数据快照（本目录 data/）

| 文件 | 条数 | sha256(前16) | 说明 |
|---|---|---|---|
| qin_v4_1000_final.jsonl | 997 | 1bbc4a1e56928c73 | 997 候选集（T15 新批次） |
| qin_v4_1000_final.metadata.jsonl | 997 | 5fa8dfe2aac7440f | 含 `review_status: candidate_unreviewed` |
| qin_v4_t12_matrix.jsonl | 119 | ad86de1c08b84c62 | 119 行为矩阵（T12） |
| qin_v4_t12_matrix.metadata.jsonl | 119 | dfb3517d2cee34b9 | 含 `review_status: human_review_incomplete` |
| qin_v4_t12_matrix_final.jsonl | 119 | 24472280ea3c0c32 | 矩阵 final 版 |
| qin_v4_t12_matrix_final.metadata.jsonl | 119 | e43de1562fd939d8 | 矩阵 final 元数据 |
| qin_v4_t12_matrix.gate_report.jsonl | 126 | 2181bf952a71a516 | 矩阵门禁报告 |
| qin_v4_20.jsonl | 20 | 65d3fc0f944538c0 | 验证批次（valid 集来源） |
| qin_v4_p1_safety_final.jsonl | 18 | e5c89638f110fd99 | P1 安全/急救样本 |
| qin_v4_p1_safety_final.metadata.jsonl | 18 | e5b7ee984d3b467f | P1 元数据 |
| qin_v4_p2_general.jsonl | 24 | 6e5411bdcd7da9ea | P2 常识/逻辑问答 |
| qin_v4_p2_general.metadata.jsonl | 24 | 504c2b4e2c4946fa | P2 元数据 |
| qin_v4_p3_casual.jsonl | 6 | ac28c60866d9eece | P3 反朗读抽查 |
| qin_v4_p3_casual.metadata.jsonl | 6 | b240cbc51ea2c47a | P3 元数据 |
| qin_corrections.jsonl | 41 | 678a3a508142e9cd | 称呼纠错（人工编写，必须包含） |

来源：`F:\AiPeopleCreate\训练数据\`（原件不动，继续留作审计对照）。

## 二、脚本（原地只读，不复制）

- V4 生产生成：`F:\AiPeopleCreate\gen_qin_v4.py`
- 冻结标记工具（本次新增）：`F:\AiPeopleCreate\tools\mark_freeze_status.py`
- 复核表：`tools/build_review_sheet.py`、`tools/build_t12_review_tool.py`
- 其他工具：`tools/`（taxonomy_report / blocklist / 去重 / 正典同步 check_profile_sync / rerender_anchors）
- 测试：`tests/`（2026-08-07 全量 294 passed）

## 三、训练配置（D2 冻结，施工总计划_v2 §2.1）

| 项 | 值 |
|---|---|
| 路线 | Apple M3（MLX 脚本链）；h200 归档备选 |
| 参数 | rank 16 / lr 1e-5 / num-layers 24 / batch 1 / seed 42 / max-seq-len 2048 |
| 步数 | iters = 1.5 × N |
| 基座 | Qwen3.5-4B（2026-08-07 升级；旧基座 Haruhi 为过程记录） |
| 脚本 | `F:\AiPeople\training_package_m3_qwen35\01_prepare_data.py`、`02_train.sh`（train 1093 / valid 130） |

## 四、模型（AI 程序侧，只读引用）

- 基座下载：`F:\AiPeople\local_runtime\models\qwen3-4b-base-1cfa9a720891`
- 已训 LoRA 正式产物：无（`training_package_m3_qwen35/adapters/` 下仅空 `legacy/`；旧基座一轮 loss 1.94 非交付物）

## 五、评测结果（AI 程序侧，只读引用）

- `F:\AiPeople\eval\results\`：yuqian_base.json / yuqian_small_lora.json
- `F:\AiPeople\eval\diagnostic120\`、`chat01*/`、`chat02*/`、`training_contract/`、`memory_contract/`、`model_comparison_contract/`
- `F:\AiPeople\local_runtime\model_runtime_manifest.json`、`smoke_test_2026-08-04.md`

## 六、冻结声明（阶段 0 生效，§8.3 禁止事项 1）

1. 997/119 仅为候选（`candidate_unreviewed` / `human_review_incomplete`），**"已生成/已导出" ≠ "已发布"**。
2. 阶段 0 完成前：停止扩大数据规模，不删除旧数据，不重新生成。
3. 后续任何数据修改，以本快照为差异基准。
4. `training_package_m3_qwen35/README.md` 中"全部人工复核通过"声明已于本日修正。
