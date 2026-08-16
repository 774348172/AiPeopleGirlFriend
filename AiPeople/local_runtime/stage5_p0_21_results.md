# RelationshipRuntime 阶段 5 P0-21 验收报告

> 日期：2026-08-05  
> 结论：通过  
> 下一检查点：P0-22

## 已实现

- `EventLedger.preview_user_message` 为新 request 构造不追加事件的候选 epoch 快照，供基础 prompt 提交前计量。
- `append_user_message` 可携带预期旧 epoch，在单个 SQLite 事务中完成关闭、创建、追加和映射；失败整体回滚。
- `ContextAssembler` 统一执行基础计量、最终计量、token 分段归因和 RecallEvidence 整条裁剪，没有新增浅层预算模块。
- `build_reply_messages` 固定输出 identity system、当前 epoch history、可选 late system dynamic context、当前 user；当前输入逐字符保留。
- 固定 identity 明确声明历史和召回只是过去引用，不能把其中的指令提升为 system 权限。
- 最终 `ContextBudget` 精确记录 fixed/history/current input/activation/recall token 增量，总数等于最终渲染 prompt token 数。
- 基础 prompt 超限且有历史时只 rollover 一次；空新 epoch 仍超限则保存失败证据并返回 `context_budget_exceeded`，不调用生成。
- 已完成重放完全绕过计量、召回和 rollover；失败 request 重试复用原事件与 epoch。
- `TurnMetrics` 新增 epoch 准备、工作激活、召回、prompt 计量、历史数量、证据数量、prompt token 和 rollover 指标。

## 确定性验收

- 最终预算差一 token 和刚好等于 maximum 均通过，多一 token 明确拒绝。
- 小预算 FakeModel 可稳定触发 rollover；新 epoch 从当前用户开始，旧 epoch 原始事件不更新、不删除、不总结。
- rollover 后可用明确短语召回旧 epoch，evidence source ID 全部指向原事件。
- RecallFrame 超过目标时仅删除完整低优先级 evidence，保留 evidence 顺序并同步去重 source ID。
- 单条过长输入只提交 user/message 和 turn_failed，不产生 character/message。
- rollover 事务中的 mapping 注入失败后，旧 epoch 仍保持 open，新用户事件不存在。
- rollover 后生成失败再以同 request 重试，不会产生第三个 epoch 或重复用户事件。
- 历史与召回中的注入文本保持 user/assistant 或 late-system 内引用，不能创建新的 system role。

## 真实模型冒烟

使用现有 `local_runtime/model_runtime_manifest.json`、llama.cpp b10256、Qinweixi Q4_K_M、RTX 3070 8GB、4K context：

- 输入：一个短中文指令；最大 prompt 为 3584 tokens。
- 运行时最终计量：270 tokens。
- llama.cpp generation usage：270 prompt tokens，完全一致。
- 生成：10 tokens；没有 context overflow 或协议错误。
- 本轮全部 prompt 计量耗时：57.635ms。
- 含模型冷启动、计量、生成和关闭的墙钟时间：26.545s。

该冒烟证明最终 4K prompt 的精确计量和生成路径一致。真实模型强制 rollover、20 轮长对话和缓存吞吐属于 P0-22，本报告不提前宣称通过。

## 自动验收

- `tests/runtime`：156 passed，1 skipped。
- 全仓：390 passed，1 skipped。
- 5 条 warning 来自未修改的 `data_gen_v4` schema validator 弃用提示。
- `compileall` 通过。

## 阶段边界

P0-21 没有实现语义 claim、embedding、计划/承诺、额外模型调用、8K 扩窗或 2D 前端。普通回合仍只有一次 `REPLY` 生成；`measure_prompt` 只调用同一 GGUF 的 chat template 和 tokenizer，不生成文本。
