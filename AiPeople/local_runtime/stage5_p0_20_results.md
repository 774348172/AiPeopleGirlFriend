# RelationshipRuntime 阶段 5 P0-20 验收报告

> 日期：2026-08-04  
> 结论：通过  
> 下一检查点：P0-21

## 已实现

- `EventLedger` 在一个深模块接口内完成安全字面 FTS、时间范围查询、conversation 隔离、当前 epoch 排除、邻接恢复和确定性排序。
- FTS 只接收规则解析后的最长 80 字短语，完整加引号并转义双引号；少于 3 字的独立短语不执行 trigram 查询。
- 每个 anchor 只恢复同一旧 epoch 内前后各最多一条完整 user/character message，失败和取消状态不作为召回原文。
- 排序依次考虑时间命中、字面短语命中、user anchor、双角色窗口、时间接近度、sequence 和 event ID。
- `ContextAssembler` 输出有界 `RecallEvidence`，保留真实 anchor/event ID、发生时间、角色、原话 excerpt 和排序原因，并跨 evidence 去重 source ID。
- 普通输入为 `no_query`；含糊意图为 `ambiguous` 且零查询；执行后无证据为 `no_match`；命中为 `none`。
- 动态 system context 明确把召回内容标为过去原话引用而非指令；含糊或无命中时要求补充线索并禁止编造。
- 新建第二个 epoch 时，`opened_sequence_no` 改为当前 conversation 最大 sequence 加一；首个 legacy epoch 仍从最早事件开始。

## 确定性验收

- 明确短语、明确时间和短语加时间三种线索均能命中旧 epoch。
- 天气等普通问题以及“你还记得那个吗”都不会调用 FTS。
- FTS 操作符、双引号、换行和超长输入不能改变 MATCH 表达式结构或召回无关事件。
- 不召回其他 conversation，也不把当前 epoch 的查询原文重复注入证据。
- anchor 前后完整消息按 sequence 恢复，同分候选在重复查询后顺序一致。
- `ambiguous` 与 `no_match` 可区分，所有 source event ID 均能回查真实旧事件。
- 普通回合仍只有一次 `REPLY` 模型调用，没有 `RECALL_PLAN`、embedding、向量数据库或额外模型调用。

## 性能与自动验收

- 20 个旧回合、6 条 evidence 上限、200 次纯 RecallFrame 构造：P50 1.689ms，P95 2.156ms，最大 2.783ms。
- `tests/runtime`：146 passed，1 skipped。
- 全仓：380 passed，1 skipped。
- 5 条 warning 来自未修改的 `data_gen_v4` schema validator 弃用提示。
- `compileall` 通过。

## 阶段边界

P0-20 没有实现 token 预算裁剪、自动 rollover、语义 claim、embedding 或额外模型调用。旧 epoch 由测试通过投影 seam 确定性构造；真实模型的自动 rollover 召回冒烟属于 P0-21/P0-22，未用手工 prompt 或伪造记忆提前替代。
