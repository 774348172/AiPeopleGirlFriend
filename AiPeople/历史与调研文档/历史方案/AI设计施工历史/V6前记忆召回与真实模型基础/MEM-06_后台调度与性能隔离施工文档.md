# MEM-06：后台调度与性能隔离施工文档

> 状态：已完成（MEM-06A 至 MEM-06F 已冻结；下一检查点 RECALL-01）  
> 上游：MEM-05 selector view 与向量投影合同  
> 合同：`eval/memory_scheduler/mem06_contract_v1.json`

## 1. 目标与边界

MEM-06 把已冻结的 `MEMORY_PROPOSE`、确定性物化和幂等提交接到可见回复之后。角色消息只有成功提交后才产生后台 job；后台模式不返回角色正文、不阻塞已经完成的玩家回复，也不能与新的 REPLY 争抢主模型。

本阶段只接 `MEMORY_PROPOSE`。`SELF_TIMELINE_PROPOSE`、`OFFSCREEN_UPDATE` 和 `MOTIVE_EVALUATE` 仍按后续阶段施工；全库向量扫描、Top32 和 reranker 属于 RECALL/SELECT，不进入本调度模块。

## 2. 持久 outbox

迁移 007 建立 `memory_background_jobs`。RelationshipRuntime 在提交最终 CharacterMessage 的同一 SQLite 事务内写入 job，因此不存在“回复已经持久化、进程却在排队前崩溃”的丢任务窗口。

- job ID 与 proposal run ID 都由 assistant event ID 确定性派生；丢失响应后的重复提交不会重复排队。
- `queue_ordinal` 在提交事务内单调分配，严格 FIFO，不依赖相同时钟或随机 UUID 排序。
- 状态固定为 pending、running、failed_retryable、failed_terminal、completed。
- 启动时把遗留 running 恢复为 pending；关闭或前台抢占时也把已取消 job 放回 pending。
- retryable failure 只在下次启动或新回复提交后重试，避免无间隔自旋；terminal failure 保持显式状态。

## 3. 前台优先级门

现有 Runtime 允许不同 conversation 的 REPLY 并发，该合同继续保留。优先级门采用“前台共享、后台独占”：

1. 没有前台 active/waiting 时，只运行一个后台 worker；
2. 任意新 REPLY 到达时取消正在运行的后台 task，并等待其清理模型调用；
3. 多个不同 conversation 的 REPLY 可按既有能力并发；
4. 所有前台退出后，从最小 queue ordinal 重新运行 pending job；
5. Runtime 关闭时先取消后台并持久化 pending，再关闭模型和账本。

取消依赖模型适配器遵守 asyncio cancellation。MODEL-02 的真实 Qwen3.5 接入仍必须验证 HTTP 流取消、服务恢复和模型槽位实际释放；吞掉取消的适配器不满足本合同。

## 4. 内置 MEMORY_PROPOSE pipeline

`MemoryProposalPipeline` 提供可直接使用的后台 worker。调用方传入实现 `generate_memory_propose()` 的同一主模型适配器，pipeline 顺序执行：

1. 从 outbox 的 user/assistant sequence 窗口读取已提交事件；
2. 建立或恢复 MEM-04 proposal run；
3. 构造 MEM-02 冻结 messages，调用低温结构化模式；
4. 严格解析 JSON，并用账本重新验证 quote、offset、时间与证据；
5. 物化 proposed MemoryRepresentation，执行 MEM-04 原子提交；
6. 空 proposals 作为成功的空批次提交。

取消时 proposal run 保持 pending，重试沿用同一 run/job；超时和模型不可用进入 retryable，非法 JSON、合同或证据错误进入 terminal。模型原始输出不写日志，也不混入 ReplyEvent。

## 5. 性能隔离

确定性测试连续制造 20 次“后台正在占用、玩家新消息到达”的场景，测量从调用 `handle_turn` 到首个 TextDelta 的 handoff P95，冻结门槛为 100ms；同时验证取消次数、FIFO 恢复、前后台不重叠和后台最大并行度为 1。

这只是调度开销门，不替代真实模型性能验收。产品目标仍是热启动首字 P95 小于 2 秒，且开启后台整理相对关闭状态没有不可接受回归。当前工作区没有交付 Qwen3.5 成品，因此真实 GPU/llama.cpp P95 必须在 MODEL-02/03 资产到达后补测，不能用 FakeModel 数字冒充最终结论。

## 6. 完成项

- `MEM-06A`：migration 007 与角色回复同事务 outbox。
- `MEM-06B`：显式 FIFO、状态机、重启接管与幂等排队。
- `MEM-06C`：前台共享、后台独占的 cooperative preemption gate。
- `MEM-06D`：MEM-02 至 MEM-04 内置后台 pipeline 与失败映射。
- `MEM-06E`：关闭保留 pending、retryable 唤醒和 terminal 隔离。
- `MEM-06F`：20 次抢占 P95、崩溃恢复、并发、日志边界和链式冻结。

## 7. 下一检查点

下一步进入 RECALL-01：冻结 SelectorQuery 的输入、编码器 identity、mean pooling、512 输入上限和归一化合同。RECALL-01 只能定义查询表示；全库 exact dot-product 与 Top32 分别在 RECALL-02/03 实现。
