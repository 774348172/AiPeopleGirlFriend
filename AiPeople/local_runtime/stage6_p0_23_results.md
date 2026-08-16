# RelationshipRuntime 阶段 6 P0-23 验收报告

> 日期：2026-08-05  
> 结论：通过  
> 下一检查点：P0-24

## 已实现

- migration 004 新增 `plan_transitions` 追加式记录和 `plans` 当前投影。
- 支持 `promise`、`reminder`、`shared_plan` 三种类型。
- 支持 `proposed`、`confirmed`、`due`、`completed`、`cancelled`、`missed` 六种持久状态及严格转换表。
- 每次创建和转换均引用同一 conversation 的原始 event；未知或跨 conversation 证据被拒绝。
- transition 与 `plans` 投影更新处于同一 `BEGIN IMMEDIATE` 事务。
- 幂等键正常重放不追加 transition；参数或目标冲突时返回 `RequestConflictError`。
- confirmed reminder 必须有明确到期时间；时区必须是有效 IANA timezone。
- `plan_transitions` 禁止 update/delete；`plans` 删除后可由 transition 完整重建。
- 活跃计划按 conversation、到期时间和转换时间确定性排序。

## 状态机

- 新建只允许 `proposed` 或 `confirmed`。
- `proposed` 只允许确认或取消。
- `confirmed` 允许到期、完成、取消或错过。
- `due` 允许完成、取消或错过。
- `completed`、`cancelled`、`missed` 均为终态，不允许复活。

## 自动验收

- P0-23 与迁移定向测试：63 passed。
- 全仓：425 passed、3 skipped、5 warnings。
- `compileall` 通过。
- 5 条 warning 来自未修改的 `data_gen_v4` schema validator 弃用提示。

## 阶段边界

P0-23 没有解析自然语言、自动修改计划、注入 prompt、扫描到期状态或发送主动消息，也没有新增模型调用。`RelationshipRuntime.handle_turn()` 外部 interface 保持不变。

