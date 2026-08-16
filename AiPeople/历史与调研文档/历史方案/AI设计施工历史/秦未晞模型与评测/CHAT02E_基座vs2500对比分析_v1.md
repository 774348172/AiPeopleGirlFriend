# CHAT-02E 基座 vs v2500 对比分析报告（v1）

> 2026-08-07 | 数据源：`chat02e-reviewer-main-ballots.jsonl`（40 条评审）
> Suite：`qinweixi-base-vs-v2500-simple120-v1` | Rubric：`chat01-quality-rubric-v1`
> 评审人：reviewer-main（人工盲评，AB 双盲）| 提交：2026-08-07 09:49-10:10

## 一、测试方法

- **对比对象**：Qwen3-4B 基座（`qwen3-4b-base-q4_k_m`）vs 存量 2500 条 SFT 训练模型（`qinweixi-v2500-final-q4_k_m`，训练 loss 1.94）
- **测试集**：diagnostic120 中抽 40 题，覆盖 daily_relevance / emotion / general / identity / relationship / safety / style / unknown 8 类
- **评审方式**：人工盲评 AB 双模型输出，6 维度 1-5 分（relevance / correctness / persona_fit / relationship_naturalness / emotion_fit / naturalness）+ 总判定（a/b_much_better、a/b_better、tie、both_unacceptable）+ 失败原因标记
- **A/B 归属**：双盲平衡（`candidate_a_balance: v2500 20 / base 20`），A/B 由 `hash("diagnostic120-blind\x1f{case_id}") % 2` 确定性分配——本报告已按 `runner.py` 同款哈希重现每个 unit 的真实模型归属并归一化统计（直接按 A/B 槽位统计会得出误导结论）

## 二、总体结果

### 2.1 胜场（归一化后）

| 胜方 | 题数 | 占比 |
|---|---|---|
| **v2500** | **28** | 70% |
| base | 8 | 20% |
| 平 | 2 | 5% |
| 都不可用 | 2 | 5% |

**结论：2500 条 SFT 训练显著有效（28:8）**。

### 2.2 维度均值（40 题平均）

| 维度 | base | v2500 | 差 |
|---|---|---|---|
| persona_fit（人格贴合） | 3.75 | **4.83** | **+1.08** |
| correctness | 3.27 | 3.80 | +0.52 |
| relevance | 3.33 | 3.80 | +0.47 |
| naturalness | 4.55 | 4.92 | +0.38 |
| emotion_fit | 4.78 | 4.88 | +0.10 |
| relationship_naturalness | 4.85 | 4.83 | **-0.02** |

- 最大提升：**persona_fit（+1.08）**——人格锚训练的直接成效
- 唯一持平：relationship_naturalness（-0.02，可视为无差异）

### 2.3 失败原因（按模型）

| 原因 | base | v2500 | 变化 |
|---|---|---|---|
| fact_error（事实错误） | 19 | 12 | -7 |
| off_topic（跑题） | 12 | 11 | -1 |
| assistant_tone（助手腔） | **7** | **0** | **-7** ✅ |
| emotion_mismatch | 5 | 0 | -5 |
| overacting | 2 | 0 | -2 |
| relationship_boundary | 1 | 0 | -1 |
| fabricated_reality（编造现实） | 0 | 1 | +1 |
| unsafe | 0 | 1 | +1 |

### 2.4 按场景类别胜场

| 类别 | base | v2500 | 判断 |
|---|---|---|---|
| general（通用/逻辑） | 0 | **9** | 全胜——SFT 连通用能力都提升 |
| style | 0 | 4 | 全胜 |
| identity | 1 | 5 | 大幅领先 |
| emotion | 0 | 2 | 全胜 |
| unknown | 1 | 3 | 领先 |
| daily_relevance | 1 | 1 | 平 |
| safety | 2 | 2 | 平 |
| relationship | **3** | 2 | **唯一落后** |

## 三、核心发现

1. **训练整体有效**：28:8 胜场、persona_fit +1.08、correctness/relevance/naturalness 全面 +0.4~0.5
2. **助手腔被消灭**：base 7 次 assistant_tone → v2500 0 次；emotion_mismatch 5→0、overacting 2→0——人格锚 + 口吻约束生效
3. **通用能力同步提升**：general 类 9:0——SFT 数据（含正典事实问答）拉升了基础能力，未见灾难性遗忘
4. **事实错误明显减少但仍是最大失败源**：19→12，占 v2500 全部失败原因的大头（12/25）——与 T13 存量审计（属性朗读 1367 宽匹配 / 秘密细节 61 / 无证据经历 37）吻合：存量数据中的编造细节被模型学了一部分

## 四、残留问题与风险

| 问题 | 证据 | 处置方向 |
|---|---|---|
| **fact_error 12 次** | 最大失败源 | 新批次 grounding 门禁（T7/T14）+ 澄清正典应显著改善；下轮验证 |
| **off_topic 11 次** | 几乎未改善 | 需要更多"承接玩家话题"类样本（矩阵 supportive/vague 已补 60+ 条） |
| **relationship 落后（3:2）** | 唯一落后类别 | 暧昧关系处理可能过度口嗨/越级；矩阵 boundary 12 条 + 新 protective 24 条，量仍偏少 |
| **safety 平 + 1 次 unsafe** | protective 池仅 60 条 | 安全边界数据不足，需补量或加 hard-rule 门禁 |
| relationship_naturalness -0.02 | 维度持平 | 无显著退化，观察 |

## 五、建议与后续验证

1. **下轮验证基准**：M3 v4（矩阵 119 + 新批次 997）训练完成后，用同款 diagnostic120（40 题）再跑一次 base vs v4 对比，重点看 **fact_error / off_topic** 是否下降、relationship/safety 是否扳平
2. **数据配比**：若 relationship/safety 持续落后，新批次提高 boundary/protective 配比（当前 casual 489 占比过高）
3. **交叉印证**：4/5 称呼验收（缺"你叫我什么"样本，已补池）+ 28:8 胜场 + persona_fit +1.08 → 训练路线有效，继续推进
4. **验收口径**：v4 训练后验收 = 称呼 5 项全过 + diagnostic120 对比（fact_error ≤ 5、off_topic ≤ 5、relationship/safety 不落后）

## 附：方法论备注

- 原始 40 ballot 的 A/B 槽位统计（A 胜 16 / B 胜 20）与归一化后（v2500 28 / base 8）差异巨大——**盲评数据必须按真实模型归属归一化**，否则会得出"训练几乎无效"的错误结论
- 归属哈希与 `eval/diagnostic120/runner.py:_blind_pairs` 一致，可复现：`trained_is_a = int(sha256(f"diagnostic120-blind\x1f{case_id}").hexdigest(), 16) % 2 == 0`
- unit_id `diagnostic120.NNN` ↔ `cases_v1.jsonl` 第 NNN 条（已用"三杯水"题验证映射一致）
