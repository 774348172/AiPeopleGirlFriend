# RelationshipRuntime 阶段 5 P0-18 验收报告

> 日期：2026-08-04  
> 结论：通过  
> 下一检查点：P0-19

## 已实现

- 新增 `002_epoch_projection.sql`，包含 `conversation_epochs`、`event_epochs`、唯一 open epoch、唯一 event mapping、顺序索引和 conversation 一致性约束。
- 用户事件与 epoch 在同一事务提交；完整角色事件、失败状态和取消状态继承用户事件 epoch，映射失败时对应事件整体回滚。
- 阶段 0-4 旧库升级后按 conversation 首次使用时懒建立投影，不更新或删除旧 events。
- epoch 投影删除后可仅凭 events 重建，原始事件保持字节级不变。
- 同 conversation 从上下文准备到角色提交按回合串行；不同 conversation 可并发进入模型 seam。
- 当前用户之前、同 epoch 的完整 user/character message 按 sequence 原文进入 `ReplyContext`。
- 当前用户事件不在历史中重复；失败和取消的部分角色正文不进入历史，用户原文保留。
- 重启后继续使用原 open epoch 及原文历史。

## 真实模型两轮冒烟

使用 llama.cpp b10256 和当前 `qinweixi` GGUF：

1. 第一轮输入临时测试代号“蓝钟”，prompt 为 198 tokens。
2. 第二轮询问刚才的代号，带历史 prompt 增长到 220 tokens。
3. 第二轮回复包含“蓝钟”。

因此 `context_delivery_passed=true`，本次样本 `model_used_context_correctly=true`。这只证明当前 epoch 两轮原文链路可用，不替代后续固定长程评测。

## 自动验收

- `tests/runtime`：119 passed，1 skipped。
- 全仓：353 passed，1 skipped；5 条 warning 来自未修改的 V4 schema validator 弃用提示。
- 覆盖新库/旧库迁移、约束、幂等、映射回滚、投影重建、重启、失败、取消、同 conversation 串行、等待取消清理和跨 conversation 隔离。
- 修复了自有 llama.cpp 进程崩溃后端口尚未释放便重启的时序问题；两个崩溃场景连续 5 轮重复通过。
- `compileall` 与差异空白检查通过。

## 阶段边界

P0-18 没有实现 token 预算 rollover、召回线索解析、FTS RecallFrame 或动态 WorkingActivation。当前所有既有事件先进入单个 open epoch；P0-21 才按最终 prompt 预算切换 epoch。
