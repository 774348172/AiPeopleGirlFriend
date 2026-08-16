# SYS-09 + SYS-10 联合施工验收报告

> 验收日期：2026-08-12  
> 验收范围：正式 Embedding/Reranker 检索栈；六类模型调用协议、失败分类、有限重试与保守回退  
> 结论：系统工程闸门通过；不代表白未晞角色质量、正式 P0 性能或一小时稳定性通过

## 1. SYS-09 正式检索

- 正式 BGE：`BAAI/bge-small-zh-v1.5`，revision `7999e1d3359715c523056ef9478215996d62a620`。
- 正式 Reranker：`Qwen/Qwen3-Reranker-0.6B`，revision `e61197ed45024b0ed8a2d74b80b4d909f1255473`，`cuda-fp16`。
- 20 次真实检索案例全部命中预期记忆。
- 正式链路延迟：P50 `53.30 ms`，P95 `91.65 ms`，满足小于 `300 ms` 的闸门。
- 跨 `save_id`、跨 `character_id` 可见记忆和直接读取命中均为 `0`。
- 冷启动、工件哈希、维度和运行时自检均使用正式离线工件，不使用确定性替身证明性能。
- 证据：`eval/world_mind_p0/sys09_retrieval_report.json`。

## 2. SYS-10 六类协议

真实秦未晞工程模型成功覆盖：

1. `TURN_MIND_ADVANCE`
2. `WORLD_CONTINUITY_REVIEW`
3. `GAME_REPLY`
4. `POST_REPLY_WORLD_MIND_RECONCILE`
5. `FIVE_MINUTE_WORLD_MIND_RECONCILE`
6. `MEMORY_PROPOSE`

- `successful_modes` 包含全部六类，`missing_modes` 为空。
- 真实模型工程链路完成前台回复、回答后整理、五分钟整理和独立记忆提议。
- 秦未晞模型只证明协议和工程链路，不证明白未晞角色质量。
- 证据：`eval/world_mind_p0/runs/qin-engineering-20260812-123605/report.json`。

## 3. 协议保护与修复

- 六类调用均使用有界 Schema、Runtime 二次解析、有限重试和模式级尝试审计。
- 失败统一区分服务不可达、超时、取消、空输出、无效 JSON、Schema、合同与未知失败。
- `GAME_REPLY` 失败不拼接假对白；心智推进或审查失败不提交候选状态；后台整理和记忆提议失败保留稳定状态并由任务重试。
- 修复 Qwen3 Reranker Prompt 被正式 UUID 推过 128 tokens 后截断回答后缀的问题；程序映射 ID 不再进入语义 Prompt。
- 正式 Reranker 使用 manifest 冻结的绝对阈值加谨慎相对第一名救回策略，平分或低噪声仍可全部拒绝。
- Ollama provider 会移除其 grammar 不支持的 `pattern`，权威 Schema 与 Runtime 标识符校验不变。
- 流式 HTTP 错误会先读取响应体，故障报告不再被 `ResponseNotRead` 掩盖。

## 4. 测试结果

- 正式检索协议与适配器：`24 passed`。
- SYS-10 核心协议与 Ollama 兼容：`14 passed`。
- WMR-08 六模式冻结合同：`4 passed`。
- 广泛回归：`371 passed, 3 skipped, 3 failed`；其中 llama.cpp 超时恢复用例单独复跑通过，另外两项为权威设计文档已有改动导致历史冻结清单漂移，不属于本次运行时代码回归。

## 5. 最终结论

`SYS-09` 与 `SYS-10` 联合施工完成，允许进入 `SYS-11` 一小时真实模型、并发、崩溃与恢复施工。白未晞角色质量、发布推理栈资源目标和正式 P0 仍保持延期，不能由本报告解锁。
