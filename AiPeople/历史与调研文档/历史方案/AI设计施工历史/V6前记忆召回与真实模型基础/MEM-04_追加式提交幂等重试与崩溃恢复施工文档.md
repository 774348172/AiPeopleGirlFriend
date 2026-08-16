# MEM-04：追加式提交、幂等重试与崩溃恢复施工文档

> 状态：已完成（MEM-04A 至 MEM-04F 已冻结；下一检查点 MEM-05）  
> 版本：v1.0  
> 日期：2026-08-07  
> 上游合同：`eval/memory_materialization/mem03_contract_v1.json`  
> 当前总纲：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`

## 1. 目标与权限边界

MEM-04 将 MEM-03 物化的 `proposed MemoryRepresentation` 以追加事件提交到 SQLite，并提供 proposal run 生命周期、显式 accept/reject/disputed 决策、幂等重试、原子修正链和投影重建。

本阶段不使用置信度阈值、关键词或规则替模型判断语义价值。提交批次只产生 `proposed`；进入 `active`、`disputed` 或 `rejected` 必须经过显式决策命令。MEM-04 不生成 selector view 或向量，后者属于 MEM-05。

## 2. 存储分层

事实层永久追加：

- `memory_run_transitions`：记录 pending、失败、重试和 committed；
- `memory_transitions`：记录 proposed、active、disputed、superseded、rejected 的完整版本。

工作投影可删除重建：

- `memory_proposal_runs`：每个 proposal run 的当前状态与 attempt；
- `memories`：每条记忆的当前版本与状态。

两个事实表均由 SQLite trigger 拒绝 UPDATE/DELETE。投影表不是事实源；删除后必须由 transition 顺序重放恢复。

## 3. Proposal Run 与重试

冻结状态机：

```text
NULL -> pending
pending -> committed | failed_retryable | failed_terminal
failed_retryable -> pending (attempt + 1)
failed_terminal / committed -> 终止
```

run ID 永久绑定 conversation 和 sequence window。相同 idempotency key 只能重放完全相同的命令；复用到其他 run 或参数时拒绝。失败事件只保存稳定 failure code 和 retryable 结论，不保存 raw prompt 或模型原始输出。

## 4. 原子批次与丢失响应

一个非空或空 proposal batch 与 run 的 committed 转换在同一 `BEGIN IMMEDIATE` 事务中完成。任一 transition、关系目标、证据或投影写入失败，整个事务回滚，run 仍为 pending。

为处理“数据库已提交，但进程在返回调用方前崩溃”，run 保存语义批次 SHA256。指纹覆盖 AI 语义、证据、时间、关系、run 与 ordinal，但排除重物化时会重新分配的 `memory_id` 和 `created_at`。同语义重试直接返回已持久化结果；不同语义使用同一 run 拒绝。

## 5. 决策与修正

- proposed 的 accept/reject/disputed 使用独立 idempotency key 与冻结 reason code；
- decision 可引用同 conversation 的新事件作为确认或纠正证据；
- unresolved `contradicts` 不能直接 active，必须先进入 disputed；
- correction 被显式激活时，其 `supersedes` 目标在同一事务转为 superseded；
- 修正链任一写入失败时，新旧记忆状态一起回滚。

关系目标在提交时必须真实存在、属于同 conversation，并且当前为 active 或 disputed；模型输入中的摘要不能替代持久投影验真。

## 6. 崩溃恢复与重建

投影重建同时重放 run transitions 与 memory transitions，并验证：

1. ordinal 连续；
2. from/to 状态满足冻结状态机；
3. representation version 连续；
4. 除 version/status 外的记忆内容未被转换事件篡改；
5. 原始证据、conversation、关系目标和最终 representation 仍通过 MEM-01 合同。

重建发生异常时事务回滚，不留下半份投影。

## 7. 施工结果

- `MEM-04A`：新增迁移 005，建立两张追加事实表与两张可重建投影表。
- `MEM-04B`：实现 proposal run begin/failure/retry/commit 状态机。
- `MEM-04C`：实现批次原子提交和丢失响应后的语义幂等恢复。
- `MEM-04D`：实现显式决策、冲突保护和 correction supersedes 原子链。
- `MEM-04E`：实现双投影全量重建、证据复验与不可变内容校验。
- `MEM-04F`：覆盖故障注入、终止失败、重复 key、空批次、修正回滚、旧数据库升级和 append-only trigger 测试，并冻结独立 manifest。

## 8. 后续衔接

下一阶段是 MEM-05：只为当前 `active/disputed` MemoryRepresentation 生成版本化 selector view 与本地 BGE 向量，支持增量失效和全量重建。向量与 selector view 仍是投影，不得成为事实源。
