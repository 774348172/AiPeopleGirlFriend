# RelationshipRuntime 阶段 5 P0-22 验收报告

> 日期：2026-08-05  
> 工程结论：通过，阶段 5 完成  
> 产品模型质量结论：未通过

## 结论

阶段 5 的关系连续性工程闭环已通过：当前 epoch 原文历史、工作激活区、明确线索召回、4K 预算、自动 rollover、跨重启恢复、取消/崩溃恢复和 10 万事件性能均满足施工标准。

当前 Qinweixi Q4_K_M 的内容质量没有通过。运行时在 3 个真实上下文使用场景中均正确送达历史或证据，但模型只在“重启后追问”中正确使用，立即追问和 rollover 后召回均答错。该结果归入数据、训练与模型评测，不通过重复注入、语义后处理或额外模型调用伪造通过。

## 端到端验收

- FakeModel 完成单个 epoch 20 轮对话，user/assistant role 与 sequence 顺序正确。
- runtime 关闭并重启后可继续第 21 轮，当前 user 不会重复出现在 history。
- 强制 rollover 后旧原文仍留在账本，可通过明确字面短语形成带 event ID 的 RecallFrame。
- 取消产生的部分角色正文不作为完整 assistant 历史；强杀 llama-server 后自动恢复且用户事件不重复。
- 查询文本、当前/历史正文、召回 excerpt、最终 prompt 和回复正文均未进入日志。

## 10 万事件性能

基于 `local_runtime/stage5_scale_data/scale_100k.sqlite3`，数据库含 100,000 条基准旧事件；最终基准样本数为 50，数据库大小 72,716,288 bytes。

| 指标 | P50 | P95 | 标准 | 结论 |
|---|---:|---:|---:|---|
| 字面短语 FTS 查询 | 0.627ms | 0.703ms | < 300ms | 通过 |
| 时间范围查询 | 4.173ms | 4.878ms | < 300ms | 通过 |
| 普通 context 构造 | 0.032ms | 0.148ms | < 100ms | 通过 |
| 召回 context 构造 | 0.729ms | 1.003ms | < 300ms | 通过 |

FTS 计划使用 `event_fts VIRTUAL TABLE INDEX 0:M2`；时间查询使用 `ix_events_conversation_time`。两者均未扫描完整 `events` 表。结果文件中的 `build_ms` 为 `null`，因为最终数据来自复用已完成的 10 万事件数据库，不声明精确构建耗时。

## 真实模型长对话

环境：llama.cpp b10256、Qinweixi Q4_K_M、RTX 3070 8GB、真实 context size 4096。测试通过小预算形成 696 token 的有效 prompt 上限并实际触发 rollover。

- 历史、跨重启历史和 rollover 召回证据全部正确送达。
- 最大 prompt 为 569 tokens，overflow 为 0。
- 每轮运行时 token 计量均与 llama.cpp generation usage 一致。
- prompt 计量 P95 为 64.582ms；工作激活加召回处理 P95 为 0.070ms。
- 取消部分正文排除、服务崩溃恢复和用户事件去重均通过。
- 模型正确使用上下文 1/3：立即追问失败，重启追问通过，rollover 明确召回失败。

因此 `context_delivery_passed=true`，但 `model_used_context_correctly` 未通过。这说明阶段 5 的工程接口和证据传递可靠，当前 GGUF 的训练质量仍不足。

## 回归与既有基线

- `compileall` 通过。
- 全仓：393 passed、3 skipped、5 warnings。
- 5 条 warning 来自未修改的 `data_gen_v4` schema validator 弃用提示。
- 阶段 4 的一小时稳定性、首字 P95、80/160 token 延迟和资源增长继续引用 `local_runtime/stage4_p0_16_results.json`，未重复运行一小时测试。

## 阶段边界

P0-22 没有加入语义召回、embedding、向量数据库、额外生成调用、语义 claim、计划/承诺状态机或 2D 前端。字面召回要求线索能匹配原文；召回预算不足时整条 evidence 被移除，二者均是当前确定性设计边界。
