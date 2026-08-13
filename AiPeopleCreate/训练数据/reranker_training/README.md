# Reranker 训练数据（白未晞，SELECT-01）

> 记忆选择器（Qwen3-Reranker-0.6B）训练数据，遵循 freeze02 合同 memory_reranker 数据族。
> 标签语义：positive=应激活（背景相关即可）；hard_negative=相似但此刻不应激活；easy_negative=无关。

## 数据构成（v1，2026-08-13）

| 来源 | 样本数 | query 类型 |
|---|---|---|
| 池场景（`_rerank40`） | 40 条 × 8 候选 = **320** | 池条目虚构场景 |
| 真实对话（`_rerank_real40`） | 40 条 × 8 候选 = **320** | **真实玩家对话**（从 v4_1000_final 抽样） |
| **合计** | **640 条单对记录** | |

## 标签分布

| 标签 | 数量 | 占比 |
|---|---|---|
| positive | 202 | 32% |
| hard_negative | 122 | 19% |
| easy_negative | 316 | 49% |

（正负比例合理：positive 与 hard_negative 接近 1:1 对 reranker 区分度最有用）

## 候选记忆设计（泛化保障）

- 候选库 = 45 个记忆单元（15 timeline 事件 + 30 canon_fact），已排除元数据类
- 每条样本 8 个候选 = 4 语义锚点 + 4 全库均衡补足（环形轮转，seed 决定起点）
- 覆盖全部 45 单元，canon_fact 占比 ~40%（修复前只有 15 个 timeline 事件 → 泛化差）

## 格式

`baiweixi_reranker_train_v1.jsonl`（单对格式，每条 = query × 一个候选记忆）：
- 符合 `F:\AiPeople\eval\training_contract\schemas\reranker_record_baiweixi.schema.json`
- 640/640 jsonschema 校验通过
- 字段：query_text / candidate_memory_id / candidate_memory_text / label / relevance_score / should_recall / label_evidence

## 训练拆分注意

- `conversation_group_id` 按样本分组：同一对话的 8 条候选必须同入 train 或同入 dev/test（防泄漏）
- `canon_snapshot.sha256` 为占位（chat01-canon-v4 冻结后填充真实 hash）

## 复核状态

- 用户判定：全部通过（未逐条人工复核，整体抽检质量合格）
- 后续可继续扩充：真实对话路径可扩展至全部 844 条 REPLY 数据（每条 8 候选 ≈ 6750 条单对记录）
