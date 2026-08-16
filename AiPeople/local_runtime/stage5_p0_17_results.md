# RelationshipRuntime 阶段 5 P0-17 验收报告

> 日期：2026-08-04  
> 结论：通过  
> 对象：llama.cpp b10256 + `qinweixi` GGUF

## 唯一计量方式

真实 Adapter 对最终 messages 执行：

1. `POST /apply-template`，传入 `add_generation_prompt=true` 和 `enable_thinking=false`。
2. 将返回的完整 prompt 传给 `POST /tokenize`，设置 `add_special=false`、`parse_special=true`。
3. 使用返回 token ID 数量作为 prompt token 精确值。

不启用字符/token 比例、Transformers tokenizer、第二个模型或生成调用估算。

## 模板探针

- `system + history + late system + current user` 可由 b10256 正确应用。
- late system 位于历史 assistant 之后，当前 user 位于 late system 之后。
- generation prompt 已包含，Qwen3 thinking 已关闭。
- 探针模板得到 42 tokens；一次 1-token generation 的 usage 同为 42 tokens。

## 实现后精确性

通过 `LlamaCppReplyModel.measure_prompt()` 计量正常请求得到 189 tokens；随后使用相同 messages 生成 1 token，llama.cpp usage 报告 prompt 也是 189 tokens。结果完全一致。

## 8K 字符性能

8,000 个中文字符输入计量为 8,185 tokens。预热 3 次后测量 30 次：

| 指标 | 结果 | 标准 |
|---|---:|---:|
| P50 | 16.296 ms | - |
| P95 | 20.037 ms | < 100 ms |
| 最大值 | 28.012 ms | - |

P95 通过。该测试只验证计量性能；8,185-token prompt 本身超过当前 4K 窗口，后续由 P0-21 的 rollover 和超限拒绝负责。

## 工程验收

- 内部 `ReplyContext`、`ContextMessage`、`WorkingActivation`、`RecallFrame`、`RecallEvidence` 和 `ContextBudget` 已建立为不可变合同。
- `ReplyRequest` 必须携带与当前事件、原文一致的 context。
- Fake 与真实 Adapter 均实现 `measure_prompt(context)`。
- `RuntimeConfig` 固定 4K 基线：256 reply reserve、256 safety margin、3,584 maximum prompt。
- 生产 Runtime 与真实模型 context size 不一致时启动失败。
- 外部 `RelationshipRuntime.handle_turn(UserMessage)` interface 未改变。
- `tests/runtime`：104 passed，1 skipped。
- `compileall` 与差异空白检查通过。

## 阶段边界

P0-17 没有实现 epoch 持久化、当前历史注入、确定性召回或 rollover。当前请求只携带确定性的单轮初始 context；P0-18 将以账本 epoch 投影替换该过渡状态。
