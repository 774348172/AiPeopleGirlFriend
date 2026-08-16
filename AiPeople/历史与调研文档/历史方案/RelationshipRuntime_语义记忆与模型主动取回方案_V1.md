# RelationshipRuntime 语义记忆与模型主动取回方案 V1

> 状态：已归档，被全局记忆选择器 V2 替代，不得作为现行方案施工  
> 版本：V1  
> 日期：2026-08-05  
> 适用阶段：P0 AI 文字聊天核心  
> 上位依据：`需求文档/项目框架需求.md`、`设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`

> 替代方案：`设计文档/AI设计/当前权威设计/RelationshipRuntime_全局记忆选择器训练与模型决策方案_V2.md`

## 1. 定位

本方案细化唯一现行 AI 实现中的“语义投影 + 多线索索引 + 工作激活区”，不建立第二套记忆架构。追加式事件账本仍是唯一事实来源，现有 current epoch、工作激活区、FTS、上下文预算和自动 rollover 继续保留。

V1 解决两个具体问题：

1. 现有确定性召回会在模型调用前直接选择完整证据，CPU 规则事实上提前决定了模型能够想起什么。
2. 每轮额外调用一次 Qwen 做记忆判级或排序，会串行占用当前 `parallel=1` 的模型，破坏首字延迟目标。

V1 不把秦未晞改造成资料查询助手。记忆用于自然理解、关系连续性、共同经历和带个人立场的关心，不以最大化任务完成、提醒覆盖率或档案复述为目标。

## 2. 核心决定

1. 取消“普通、相关、复杂”这种需要先判级的动态预算。
2. 后台 `MEMORY_PROPOSE` 负责一次完整的 AI 语义巩固，同时生成可检索的简短记忆索引。
3. 在线 CPU 检索只构造高召回、低信息量的 `MemoryIndexFrame`，不直接决定哪些完整记忆进入回答上下文。
4. `REPLY` 首先看到简短索引，由同一个 Qwen 输出 `DIRECT` 或有序 `FETCH`。
5. `DIRECT` 在同一段生成中继续输出角色正文；`FETCH` 最多触发一次详细记忆读取和第二段模型生成。
6. AI 输出的 ID 顺序就是相关度顺序。CPU 只验证、去重、保持顺序并按 token 预算装包，不进行第二次语义排序。
7. 对前端仍只暴露：

```text
RelationshipRuntime.handleTurn(event) -> stream<ReplyEvent>
```

内部控制头、记忆 ID、证据和 `FETCH` 过程不得作为秦未晞正文显示，也不得由前端直接修改。

## 3. 与当前实现的关系

阶段 0-5 已具备：

- SQLite 追加式事件账本和事务不变量。
- current epoch、历史恢复和自动 rollover。
- `WorkingActivation`、`RecallFrame`、FTS5 和时间召回。
- 4K 精确 prompt 计量、256-token 回复预留和 256-token 安全余量。
- 常驻 llama.cpp、Qwen3-4B GGUF、流式输出、取消、恢复和 `parallel=1` 串行生成。

尚未具备：

- 运行时 `MEMORY_PROPOSE` 后台任务。
- `memory_claims`、证据关系和短索引投影。
- `MemoryIndexFrame`。
- `DIRECT/FETCH` 控制协议。
- 同一逻辑回合内的详细记忆取回和第二段生成。

现有确定性 `RecallFrame` 在 V1 施工期间保留为回归基线和降级路径。V1 通过验收后，它不再直接向普通 `REPLY` 注入完整历史证据，而是参与构造短索引和在投影不可用时降级。

## 4. 记忆数据模型

### 4.1 原始证据

`events` 继续保存所有原始消息和应用事件。已提交事件不原地修改；修正通过新事件和 supersedes 关系表达。任何派生记忆删除后必须可以仅凭事件账本重建。

### 4.2 语义记忆

V1 的语义投影至少覆盖：

- `fact`：稳定事实。
- `preference`：偏好与习惯。
- `relationship`：人物关系和关系变化。
- `episode`：共同经历及其过程。
- `emotional_significance`：一件事对玩家、秦未晞或双方的情绪意义。
- `character_stance`：秦未晞形成的看法、在意或介意之处。
- `correction`：修正、否定和冲突。
- `open_thread`：未完话题、等待后续的生活事件。
- `shared_commitment`：双方承诺和共同计划。
- `future_event`：有跨回合意义的未来经历，不等同于提醒任务。

建议投影形状：

```text
memory_claims
- memory_id
- memory_type
- subject
- content_json
- perspective          player_said | character_believes | shared | inferred
- certainty            stated | inferred | uncertain | conflicted
- status               proposed | active | disputed | superseded
- valid_from
- valid_to
- emotional_weight
- relational_weight
- created_at
- supersedes_memory_id
```

```text
memory_evidence
- memory_id
- event_id
- evidence_role        support | contradict | correction | context
- excerpt_start
- excerpt_end
```

```text
memory_links
- source_memory_id
- relation             same_episode | caused_by | follows | about_person | about_topic
- target_memory_id
- evidence_event_id
```

数值权重只用于候选召回，不得解释为好感度、服从度或任务优先级。

### 4.3 简短记忆索引

每条可激活记忆生成一条可重建索引：

```text
memory_index_entries
- memory_id
- compact_text
- entity_keys
- topic_keys
- time_keys
- emotion_keys
- relation_keys
- status
- valid_from
- valid_to
- source_revision
```

`compact_text` 只表达足够让模型判断是否需要展开的信息，不承担完整回答。例如：

```text
[M103][未完经历] 玩家今天参加一场很重视的面试，结果未知。
[M104][偏好] 玩家不喜欢面试前被连续追问准备情况。
[M105][共同经历] 秦未晞昨晚嘴硬地祝他别紧张。
```

索引不得包含无证据推断出的确定事实，也不得包含控制模型行为的指令。

## 5. 后台语义巩固

角色回复提交后，GPU 空闲时运行一次 `MEMORY_PROPOSE`。它一次完成：

- 记忆分类。
- 主体、视角、时间和不确定性解释。
- 情绪意义和关系意义分析。
- 原始证据片段选择。
- 修正、冲突、前后事件和同义线索关联。
- `compact_text` 与结构化索引候选生成。

CPU 校验器只执行不可伪造的不变量：

- 事件和证据片段真实存在。
- excerpt 范围与原文一致。
- schema、枚举、时间格式和 ID 合法。
- AI 不能删除账本、伪造证据或把推断直接提交为无争议事实。

CPU 不重新判断一段话的语义类型、跨回合意义或情绪意义。新玩家消息到来时，后台任务必须取消或暂停，不能与可见回复争抢当前单槽 GPU。

## 6. 短索引目录

### 6.1 MemoryIndexFrame

每轮在线回复前只构造短索引目录：

```text
MemoryIndexFrame
- entries              有序的短索引条目
- source_memory_ids
- build_reason         current | recent | entity | time | fts | semantic_key
- token_count
- truncated
```

初始预算：

```text
target_index_tokens = 128..192
maximum_index_tokens = 256
maximum_detail_tokens = 256
maximum_fetch_ids = 6
maximum_fetch_rounds = 1
```

这些是 V1 实验基线，不是已经通过性能验收的最终值。施工必须分别测试 128、192、256-token 索引；最终只冻结一个普通回合预算，不在运行时调用模型判级。

### 6.2 CPU 可以做什么

CPU 可通过已有多线索索引广泛收集短条目：

- 当前 open thread 和工作激活项。
- 最近形成或最近被修正的记忆。
- 明确人物、时间和 FTS5 词项命中。
- 后台 AI 已生成的人物、主题、情绪和同义线索。
- 有效状态、冲突状态和证据可用性。

这一步追求候选覆盖，不宣称最终相关。CPU 不读取完整证据后自行判断“玩家现在真正想起哪件事”，也不把某个索引命中自动转换为提醒或待办。

### 6.3 长期规模边界

不能把全部 `memory_index_entries` 放进 prompt。十年使用后，即使每条索引很短也会越过任何本地上下文预算。

V1 先使用现有结构化索引、FTS5 和后台 AI 生成的语义 key 构造有界目录，不引入向量数据库。只有固定长程评测证明“同义表达导致正确记忆索引无法进入目录”，才评估小型中文 embedding。embedding 只补充候选覆盖，不能替代 Qwen 的最终 `DIRECT/FETCH` 判断。

## 7. DIRECT/FETCH 协议

### 7.1 快路径

模型必须先输出一个内部控制头：

```text
DIRECT
秦未晞正文从这里开始……
```

运行时隐藏 `DIRECT`，从正文第一个 token 开始向前端流式发送。控制头应保持最短，不能输出分析过程、记忆 ID 解释或助手式“正在检索”。

`DIRECT` 表示短索引、当前 epoch 和工作状态已经足够，或本轮无需旧记忆。它不是“没有记忆”，也不是要求模型复述索引。

### 7.2 主动取回路径

模型认为必须查看详细记忆才能自然、可靠地回答时，输出：

```text
FETCH M103,M105
```

约束：

1. `FETCH` 后不得同时输出可见角色正文。
2. ID 必须来自本轮 `MemoryIndexFrame`。
3. ID 顺序表示 AI 的相关度顺序。
4. 最多 6 个 ID，最多一次取回；第二段禁止再次 `FETCH`。
5. 不需要精确细节时应使用 `DIRECT`，不得为了展示记忆能力而频繁取回。

CPU 按以下顺序执行：

```text
验证 ID 属于本轮目录
-> 保序去重
-> 排除无证据、失效或不允许激活的条目
-> 依 AI 顺序读取语义记忆和必要证据
-> 按 256-token 硬上限逐条装入
-> 形成 MemoryDetailFrame
```

CPU 不对剩余 ID 进行新的语义排序。预算不足时从 AI 排序末尾删除完整条目，不能截断证据造成语义失真。

### 7.3 第二段生成

`FETCH` 取回成功后，第二段上下文按逻辑追加：

```text
固定身份锚
当前 epoch 历史
工作状态与 MemoryIndexFrame
当前用户消息
内部 FETCH 决策
MemoryDetailFrame
强制 ANSWER 指令
```

第二段只允许输出秦未晞正文，不允许再次取回。第一段内部控制不得作为 `CharacterMessage` 提交账本；只有完整可见回复提交。

当前 llama.cpp Adapter 使用无状态 `/v1/chat/completions`，因此 V1 首版的 `FETCH` 至少是两个物理 HTTP 模型请求。它们属于同一个逻辑 `handleTurn`，但不能在性能报告中冒充一次生成。只有后续实验证明 llama.cpp slot/KV continuation 可可靠复用时，才允许在不改变外部 interface 的前提下优化实现。

## 8. 单回合流程

```text
1. 校验并提交 UserMessage
2. 更新确定性状态和 WorkingActivation
3. 从语义投影构造有界 MemoryIndexFrame
4. 调用 REPLY
5. 模型输出 DIRECT 或 FETCH
6a. DIRECT：隐藏控制头，立即流式返回正文
6b. FETCH：读取 MemoryDetailFrame，执行第二段 ANSWER 并流式返回正文
7. 提交最终 CharacterMessage
8. 更新工作状态
9. GPU 空闲时运行 MEMORY_PROPOSE
10. 校验并更新可重建语义投影和短索引
```

`RECALL_PLAN` 不再是 V1 普通在线路径的必需模式。它保留为冻结训练协议和未来实验入口；在 V1 固定评测证明 `DIRECT/FETCH` 无法处理某类含糊回忆前，不接入额外串行调用。

## 9. 模块与接口

`RelationshipRuntime` 继续作为对上层的唯一深模块。前端不学习 SQLite 表、索引格式、控制头、两段生成或 token 装包规则。

V1 的复杂性必须集中在运行时内部：

- 从账本重建语义投影。
- 构造和计量 `MemoryIndexFrame`。
- 解析并验证 `DIRECT/FETCH`。
- 读取和装配 `MemoryDetailFrame`。
- 管理最多一次的取回状态。
- 保证取消、重试和提交幂等。

实现时只有在 FakeModel 与真实 llama.cpp 确实需要不同适配行为时，才在模型 seam 增加相应 Adapter；不能把 `fetchMemory()` 或 `commitClaim()` 暴露给前端。

## 10. 上下文与性能合同

V1 保持已经验收的 4K 基线：

```text
context_size = 4096
reply_reserve_tokens = 256
safety_margin_tokens = 256
maximum_prompt_tokens = 3584
```

当前 223-token 重复 prompt 的首字 P95 约 30ms，但该数据可能包含高比例前缀缓存，不能证明全新长 prompt 的延迟。V1 必须分别记录总 prompt token 和本轮实际处理 token；若 llama.cpp 接口无法报告缓存命中，必须通过变化前缀基准测量，不能用重复 prompt 代替。

性能要求：

- `DIRECT` 普通回合继续受产品全局首字 P95 `< 2s` 约束。
- `FETCH` 必须单独统计第一段路由、数据库读取、第二段 prefill 和首个可见 token。
- `FETCH` 不是 NFR-02 的豁免；若其占比或延迟导致整体 P95 超标，V1 不通过。
- 短索引构造和 SQLite 读取属于记忆处理，普通回合新增 P95 仍需 `< 300ms`。
- 后台 `MEMORY_PROPOSE` 不进入可见回复关键路径。

验收矩阵至少覆盖：

| 总 prompt | 短索引 | 路径 |
|---:|---:|---|
| 512 / 1024 / 2048 / 3072 | 128 | DIRECT |
| 512 / 1024 / 2048 / 3072 | 192 | DIRECT |
| 512 / 1024 / 2048 / 3072 | 256 | DIRECT |
| 512 / 1024 / 2048 / 3072 | 192 + detail 256 | FETCH |

每格至少 30 个变化输入样本，报告 P50/P95，不得连续复用完全相同 prompt。

## 11. 人格与自然度约束

- 不向玩家显示“已记录、正在检索、找到三条记忆”等系统行为。
- 不因某条未来事件被激活就自动承诺提醒。
- 模型可以看到记忆但不提及；相关不等于必须复述。
- 秦未晞可以自然地记得核心、忘记枝节、承认不确定或询问线索。
- 记忆可以触发关心、调侃、吃醋、回避或个人看法，而不只触发任务完成。
- 安全硬要求和现实未知边界不因记忆或角色态度而降低。

## 12. 失败与降级

| 失败 | 行为 |
|---|---|
| 语义投影或短索引不可用 | 使用当前 epoch 和现有确定性召回降级，不编造 |
| 控制头非法 | 不执行任意 ID；本轮失败或按已验证的纯正文降级策略处理，不能泄漏内部文本 |
| `FETCH` ID 不在目录 | 拒绝该 ID并记录协议错误 |
| 详细记忆读取失败 | 不提交第一段控制文本；自然承认记不清或返回可重试错误 |
| 详细包超预算 | 按 AI 顺序删除末尾完整条目 |
| 第二段再次 `FETCH` | 协议错误，禁止循环 |
| 第一段或第二段取消 | 记录取消状态，不提交不完整角色回复 |
| `MEMORY_PROPOSE` 失败 | 原始账本不受影响，后台重试或重建 |

降级策略必须在施工前冻结并用 FakeModel 验证，不能在错误发生后临时拼接助手式兜底文本。

## 13. 训练与评测要求

现有秦未晞 GGUF 未训练 `DIRECT/FETCH` 协议，不能假设仅靠 prompt 就能稳定执行。数据生成器需增加独立样本族：

- 无需旧记忆时选择 `DIRECT`，正文自然且不提系统。
- 短索引已经足够时选择 `DIRECT` 并正确使用索引。
- 只有详细证据才能回答时输出有序 `FETCH`。
- 多个相似索引中选择正确 ID。
- 修正、冲突和无证据情况下不取错记忆。
- 不为了表现“记得”而频繁 `FETCH`。
- 第二段根据详细记忆回答，不复述 ID、不泄漏证据协议、不再次取回。
- 未来事件只作为生活连续性，不自动变成提醒和任务。

固定评测必须分别报告：

- `index_recall`：正确记忆是否进入短目录。
- `route_accuracy`：`DIRECT/FETCH` 是否正确。
- `fetch_precision`：模型选择的 ID 是否相关。
- `detail_grounding`：回复是否只使用有证据的详细内容。
- `natural_memory_use`：是否自然、有关系感、无助手/管家腔。
- `unnecessary_fetch_rate`：无需细节却触发取回的比例。
- `first_visible_token_p95`：DIRECT、FETCH 和整体分布。

## 14. 施工切分

V1 文档只冻结方案，不在本次修改中施工。后续施工建议按以下检查点编写独立施工文档：

1. `V1-01`：语义投影、证据、关系和短索引 schema；支持从账本重建。
2. `V1-02`：后台 `MEMORY_PROPOSE` 调度、取消、校验和幂等提交。
3. `V1-03`：`MemoryIndexFrame` 的高召回构造、精确计量和 128/192/256 基准。
4. `V1-04`：FakeModel 下的 `DIRECT/FETCH` 协议、隐藏控制、一次取回上限和失败降级。
5. `V1-05`：真实 llama.cpp 两段生成、取消、重试、缓存证据和隐私日志。
6. `V1-06`：训练协议数据、长程记忆质量、自然度和性能联合验收。

在 V1-06 通过前，现有确定性召回继续作为可运行基线；不得一边删除旧路径一边建设未验收的新路径。

## 15. V1 暂不采用

- 不使用规则或额外 Qwen 调用给每轮先判“普通/复杂”等级。
- 不让 CPU 对 AI 选择的 ID 再做语义排序。
- 不把全部记忆索引塞进模型上下文。
- 不允许无限多轮工具取回。
- 不增加第二个常驻生成模型。
- 不把 embedding 或向量数据库设为 V1 前置条件。
- 不让记忆系统自动生成提醒、待办和机械确认。
- 不把内部控制协议暴露成角色正文。

## 16. V1 验收结论条件

只有同时满足以下条件，V1 才能替代现有完整证据直注入路径：

1. 所有语义记忆都可追溯到原始事件，投影可重建。
2. 短目录在冻结长程集上的正确索引覆盖率达到施工文档规定阈值。
3. 模型能稳定区分 `DIRECT/FETCH`，无控制头泄漏和无限取回。
4. 详细记忆使用正确，不制造共同往事，不忽略有效修正。
5. 自由对话盲测中不增加助手腔、管家腔和机械记忆复述。
6. DIRECT、FETCH 和整体首字 P95 均有真实变化 prompt 数据，整体满足现行产品要求。
7. 失败、取消、重试、崩溃和跨重启均不丢原始事件、不重复提交回复。
