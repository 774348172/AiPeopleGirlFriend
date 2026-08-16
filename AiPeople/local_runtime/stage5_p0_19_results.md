# RelationshipRuntime 阶段 5 P0-19 验收报告

> 日期：2026-08-04  
> 结论：通过  
> 下一检查点：P0-20

## 已实现

- `ContextAssembler` 作为内部深模块，从 epoch 快照一次性构造 `ReplyContext` 和最小 `WorkingActivation`。
- `recent_verbatim_event_ids` 来自当前 epoch 的完整 message 事件，按账本顺序排列。
- 最后一组完整 user/character 因果来回可定位到真实 event ID。
- 没有完整 character/message 回复的用户事件保持 unresolved；失败、取消和重试后可确定性重建。
- 明确回忆意图、时间范围和引用短语形成结构化 `RecallCue`，最多 8 条。
- 单独叙述“昨天我去了公园”不会触发回忆线索；时间线索只有与明确回忆意图组合时才生效。
- 当前时间来自注入的运行时时钟并转换到玩家 IANA 时区；动态 system context 只包含当前本地时间和时区，不包含内部 ID。
- 不生成 `current_topic`、goal、mood 或 relationship 推断，不调用额外模型。

当前 epoch 的最近原文暂保留全部 event ID。按 token 预算裁剪由 P0-21 统一完成，P0-19 不用固定消息条数伪装 token 预算。

## 确定性验收

- 正常第二轮能恢复最近原文、上一组完整来回和当前 unresolved 用户事件。
- 失败和取消的部分角色正文不进入最近原文，相关用户事件保持 unresolved。
- 同 request 重试复用同一用户 event ID，WorkingActivation 不产生重复事件。
- 重启后从账本得到相同工作激活区。
- 普通问候、天气、数学、安全问题和普通过去叙述不产生 recall cue。
- 同一账本、固定时钟和输入得到字节级相同的动态 context。
- 工作激活区重建本身不调用 `measure_prompt` 或任何生成模式。

## 真实模型冒烟

使用 llama.cpp b10256 和当前 `qinweixi` GGUF：

1. 第一轮输入临时代号“青塔”，prompt 为 252 tokens。
2. 第二轮带当前历史和动态 system context，prompt 为 303 tokens。
3. 模型正确回答“青塔”，说明 late-system 模板和上下文交付可用。

本次冷启动加两轮生成墙钟时间为 210.8 秒，明显偏离阶段 4 热态基线，因此不作为性能通过证据，后续仍以独立分段计时为准。

模型同时虚构了未提供的“上次黑曜”共同经历，所以 `context_delivery_passed=true`，但 `content_quality_passed=false`。该问题进入训练数据与模型评测，不通过追加 prompt 或伪造运行时记忆掩盖。

## 自动验收

- `tests/runtime`：132 passed，1 skipped。
- 全仓：366 passed，1 skipped。
- 5 条 warning 来自未修改的 V4 schema validator 弃用提示。
- `compileall` 与差异空白检查通过。

## 阶段边界

P0-19 没有执行 FTS 查询、构造 RecallFrame、切换 epoch 或实现语义投影。结构化 `RecallCue` 只为 P0-20 提供确定性输入。
