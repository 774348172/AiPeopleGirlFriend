# RelationshipRuntime 阶段 6：确定性前瞻状态施工文档

> 状态：已暂停（P0-23 至 P0-24 已通过并休眠保留；P0-25 至 P0-28 不再作为当前下一步）  
> 版本：v1.0  
> 日期：2026-08-05  
> 施工范围：`P0-23` 至 `P0-28`  
> 前置条件：阶段 5 工程闭环完成
> 排序覆盖：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`

> 2026-08-05 决策：项目先完成自由对话、后台记忆整理、全局选择器和长期记忆接入。本文件未施工部分暂停；恢复前必须删除通用提醒/待办方向并重新冻结关系承诺、共同计划和自然关心范围。

## 1. 本阶段目标

阶段 6 实现计划、承诺和提醒的确定性前瞻状态。它们不能只留在自然语言历史中，也不能由模型自行猜测状态；建立、确认、到期、完成、取消和错过都必须经过状态机，并关联可审计证据。

调用方仍只使用 `RelationshipRuntime.handle_turn(UserMessage)`。SQLite、transition journal、投影重建、时间判断和 prompt 注入均留在运行时深模块内部。

## 2. 权威边界

施工依次服从：

1. `需求文档/项目框架需求.md`。
2. `设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`。
3. 阶段 0-5 已通过的运行时合同。

唯一 AI 实现不变，仍是“追加式证据账本 + 工作激活区 + 语义投影 + 前瞻状态 + 多线索索引”。本阶段不恢复历史记忆方案，不加入 embedding，不提前施工 `MEMORY_PROPOSE`。

## 3. 核心不变量

1. `plan_transitions` 是追加式可重放记录；`plans` 只是当前投影。
2. 玩家或角色触发的转换必须引用同一 conversation 的原始 event。
3. transition 不允许更新或删除；删除 `plans` 后必须可完整重建。
4. `reminder` 进入 `confirmed` 前必须有明确 `due_at` 和有效 IANA timezone。
5. 含糊时间只能保持 `proposed` 并在对话中确认，不能静默转换成确定时间。
6. request 重试不能重复建立或转换计划；每个写入命令必须有幂等键。
7. 普通回合仍只有一次 `REPLY`；计划规则处理不调用大模型。
8. 计划描述、用户原文和证据正文不得进入性能或错误日志。

## 4. 状态机

计划类型：

- `promise`：一方明确承诺执行的事项。
- `reminder`：需要在确定时间触发的提醒。
- `shared_plan`：双方共同约定的未来事项。

状态与允许转换：

| 当前状态 | 允许目标 |
|---|---|
| 新建 | `proposed`、`confirmed` |
| `proposed` | `confirmed`、`cancelled` |
| `confirmed` | `due`、`completed`、`cancelled`、`missed` |
| `due` | `completed`、`cancelled`、`missed` |
| `completed` | 无 |
| `cancelled` | 无 |
| `missed` | 无 |

取消后重新建立必须创建新 plan，不复活终态记录。计划内容或时间发生实质变化时，首版通过取消旧 plan 并建立新 plan 表达，避免隐式覆盖历史。

## 5. 数据合同

`plan_transitions` 保存每次转换的完整快照：plan/conversation、ordinal、前后状态、类型、描述、到期时间、时区、证据 event、转换时间和幂等键。完整快照使投影重建不依赖旧版本业务代码拼接增量。

`plans` 保存当前状态，并保留 `created_from_event_id`、`updated_from_event_id` 和 `last_transition_at`。任何查询只按 conversation 返回当前有效计划，不跨玩家对话共享。

## 6. 施工顺序

| 检查点 | 内容 | 通过后得到 |
|---|---|---|
| `P0-23` | schema、状态机、证据约束、幂等与投影重建 | 可靠前瞻状态内核 |
| `P0-24` | 明确计划/承诺/提醒的确定性 parser | 不调用模型的候选命令 |
| `P0-25` | user event 提交后原子应用命令、重试保护 | 对话驱动的可靠状态转换 |
| `P0-26` | 当前计划进入 WorkingActivation 与 prompt 预算 | 模型看到必要前瞻状态 |
| `P0-27` | 确定性 due/missed 扫描、时区和重启补偿 | 到期状态不依赖模型记忆 |
| `P0-28` | 端到端、性能、崩溃恢复和真实模型验收 | 阶段 6 工程闭环 |

## 7. P0-23 验收

- 新库和阶段 0-5 旧库均能应用 migration 004。
- 三种 kind、六种 state 和全部合法/非法转换有自动测试。
- 跨 conversation 证据、未知 event、非法 timezone、空描述和无到期时间的 confirmed reminder 被拒绝。
- 相同幂等键重放不增加 transition；冲突重用幂等键被拒绝。
- transition 写入与 `plans` 更新在一个事务内完成。
- 删除 `plans` 后可从 `plan_transitions` 字节级重建当前投影。
- transition 表禁止 update/delete，原始 events 不受影响。

## 8. 阶段边界

P0-23 不从自由文本自动猜计划，不生成主动消息，不执行系统通知，不加入后台模型调用。自动到期前必须先在 P0-27 定义可审计的时钟触发记录，不能拿创建事件冒充到期证据。

## 9. P0-23 施工结果（2026-08-05）

- migration 004 已落地 `plan_transitions` 追加式 journal 和 `plans` 当前投影。
- 三种 kind、六种持久状态、全部合法转换和终态保护已由确定性状态机约束。
- 创建/转换要求同 conversation 原始 event 证据，并在一个事务中同时追加 transition 和更新投影。
- 幂等键支持正常重放；同键不同描述、时间、时区、证据或目标状态均拒绝。
- confirmed reminder 缺少 `due_at`、无效 IANA timezone、未知证据和跨会话证据均拒绝。
- 删除 `plans` 后可从 transition 完整重建，重建前后行内容一致，events 和 transition 数量不变。
- 定向验收 63 passed；全仓 425 passed、3 skipped。详细证据见 `local_runtime/stage6_p0_23_results.md` 和同名 JSON。
- P0-23 通过，下一检查点为 P0-24：只解析明确、可执行的计划/承诺/提醒指令；含糊内容保持待确认。

## 10. P0-24：确定性计划解析器

验收口径：

- parser 是无副作用纯函数，不查 SQLite、不调用模型。
- 单条输入最多形成一个建立、确认、取消或完成候选。
- 建立候选必须有明确类型触发词和非空描述。
- 确认、取消和完成必须包含 `提醒/计划/承诺` 类型及可定位目标短语。
- 只有完整绝对日期时间，或今天/明天/后天加明确阿拉伯数字时刻，才能产生确定 `due_at`。
- 周末、下周、以后、过几天、左右、范围、过去和非法时间保持 `proposed`，不得自动补日期或时刻。
- 条件、转述、问题、否定、普通陈述和泛指目标不产生可执行命令。
- 相同原文、时钟和时区产生字节级稳定结果。

施工结果（2026-08-05）：

- 已实现内部 `parse_prospective_command(text, occurred_at, timezone_name)` interface，输出零个或一个结构化候选。
- 建立支持 reminder/shared_plan/promise；transition 支持 confirm/cancel/complete，且不会直接修改 P0-23 状态机。
- 明确时间统一转为 UTC，并保留原 IANA timezone；不存在的本地时间、过去时间、非法日期和多时刻表达均不能升级为 confirmed。
- reminder/shared_plan 无确切时间时为 proposed；promise 无时间但表达明确时可 confirmed，带模糊时间时仍为 proposed。
- 已覆盖正常指令、含糊时间、错误时间、泛指目标、条件句、转述、问题、否定和普通聊天反例。
- 定向验收 35 passed；全仓 460 passed、3 skipped。详细证据见 `local_runtime/stage6_p0_24_results.md` 和同名 JSON。
- P0-24 没有接入 turn、没有目标匹配、没有数据库写入或额外模型调用。按现行施工总纲在此暂停，不进入 P0-25；未来以新的 `REL-01` 起点重新收口前瞻状态。
