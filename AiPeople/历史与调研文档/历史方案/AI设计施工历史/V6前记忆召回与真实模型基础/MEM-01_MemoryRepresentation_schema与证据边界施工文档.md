# MEM-01：MemoryRepresentation schema 与证据边界施工文档

> 状态：已完成（MEM-01A 至 MEM-01F 已冻结；下一检查点 MEM-02）  
> 版本：v1.0  
> 日期：2026-08-07  
> 施工范围：`MEM-01A` 至 `MEM-01F`  
> 当前总纲：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`  
> 唯一 AI 实现：`设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`

## 1. 目标

MEM-01 只建立“系统里一条语义记忆是什么”的稳定合同，使后续 AI 整理、证据校验、追加提交、向量投影和全局记忆选择使用同一种表示。

完成后必须得到：

1. 模型候选草稿 `MemoryProposalDraft` 的 JSON Schema。
2. 经过运行时校验后形成的 `MemoryRepresentation` JSON Schema。
3. 与 schema 一致的不可变 Python 类型和纯结构校验器。
4. 证据、时间、主体、认识状态、冲突和修正的明确边界。
5. 正反例夹具、自动合同测试、冻结 manifest 和 SHA256。

MEM-01 不调用真实模型，不整理现有聊天，不新增数据库表，不生成向量，也不接入回复 prompt。外部训练进度不影响本阶段施工。

## 2. 权威边界

施工依次服从：

1. `需求文档/项目框架需求.md`。
2. `设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`。
3. `设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`。
4. 已存在的 `events` 追加式账本、epoch 和运行时合同。

唯一实现仍是：

```text
追加式证据账本
  -> AI 后台提出语义候选
  -> 确定性证据与结构校验
  -> 可重建语义投影
  -> 全局向量扫描与精排
  -> SelectedMemoryFrame
```

原始 `events` 是唯一事实源。`MemoryRepresentation` 只是对证据的当前解释，删除全部语义投影后必须能从账本重建。

## 3. 本阶段明确不做

- 不设计 `MEMORY_PROPOSE` prompt、grammar、采样或后台调度；这些属于 MEM-02。
- 不读取 SQLite 验证 event 是否存在，也不提交 accept/reject；这些属于 MEM-03 和 MEM-04。
- 不建立 migration 005、投影表或追加式记忆 journal；这些属于 MEM-04。
- 不生成 `selector_views`、embedding 或向量矩阵；这些属于 MEM-05。
- 不把未来事件实现成提醒、闹钟或待办任务。
- 不建立角色自我时间线状态机；`character_self_claim` 只是后续 SELF 阶段的候选语义表示。
- 不让 CPU 规则判断一句话“值不值得长期记住”。

## 4. 核心不变量

1. 每条记忆至少引用一个已提交事件；无证据不能形成记忆。
2. 模型只能输出候选内容，不能分配 `memory_id`、设置 `active`、写入时间或批准自己。
3. 运行时不能把模型给出的 quote、offset、时间或关系当作已经验证的事实。
4. 证据引用必须保留原文，不允许用模型摘要代替原始 excerpt。
5. 否定、假设、玩笑、引用和不确定表达不得在整理时被抹掉。
6. “玩家说过 X”与“X 已由双方确认”必须是不同的认识状态。
7. 修正通过新表示和关系边表达；旧表示不得原地覆盖或物理删除。
8. 含糊未来时间可以被记为“玩家提过一个含糊未来事件”，但不得擅自补成确定日期。
9. 普通未来事件不是提醒任务，不含 `due_at`、通知或自动触发字段。
10. 低置信、冲突和待确认不等于丢弃原文；原始事件始终保留。
11. 所有枚举、长度、集合上限和跨字段规则都必须由确定性代码复验。
12. 未知字段一律拒绝，schema 使用 `additionalProperties: false`。

## 5. 两层信任合同

### 5.1 `MemoryProposalDraft`

这是 MEM-02 未来允许模型输出的单条候选。模型负责提出语义，字段包括：

- `kind`
- `statement`
- `subject`
- `epistemic`
- `temporal`
- `evidence_quotes`
- `semantic_reason`
- `confidence`
- `relation_suggestions`

模型不得输出：

- `memory_id`
- `conversation_id`
- `status`
- `created_at`
- 最终 excerpt offset 或 SHA256
- 已批准、已失效、已覆盖等运行时决定

草稿只代表“模型建议这样理解”，不代表已经进入当前有效记忆。

### 5.2 `MemoryRepresentation`

这是运行时校验、补齐系统字段后形成的不可变版本记录。它保留模型语义，同时增加：

- 运行时生成的身份与幂等字段。
- 已定位的证据 offset 和 excerpt SHA256。
- 已验证的 conversation 边界。
- 当前 lifecycle 状态和版本。
- 已验证的跨记忆关系。

`active` 和 `disputed` 可以进入 MEM-05 的向量投影，但必须携带状态：`active` 可作为当前认识，`disputed` 只能作为需要保留不确定性或避免矛盾的冲突信息，不能伪装成已确认事实。`proposed`、`superseded` 和 `rejected` 保留审计记录，不参加普通当前记忆扫描；需要回看时仍可沿证据链恢复原文。

## 6. `MemoryRepresentation` v1 字段

### 6.1 顶层字段

| 字段 | 类型 | 所有者 | 规则 |
|---|---|---|---|
| `schema_version` | integer | Runtime | v1 固定为 `1` |
| `memory_id` | string | Runtime | 非空稳定 ID；模型不能指定 |
| `version` | integer | Runtime | 从 `1` 开始；同一逻辑记忆的新版本递增 |
| `conversation_id` | string | Runtime | 所有证据必须属于该 conversation |
| `proposal_run_id` | string | Runtime | 标识一次后台整理请求 |
| `proposal_ordinal` | integer | Runtime | 同一请求内从 `0` 开始 |
| `kind` | enum | AI 提议 | 见 6.2 |
| `statement` | string | AI 提议 | 1-500 字符，必须保留否定和不确定性 |
| `subject` | object | AI 提议 | 见 6.3 |
| `epistemic` | object | AI 提议 | 见 6.4 |
| `temporal` | object | AI 提议、Runtime 规范化 | 见 6.5 |
| `evidence` | array | Runtime 定位 | 1-8 条，见第 7 节 |
| `semantic_reason` | string | AI 提议 | 1-300 字符，解释跨回合意义，不用于用户展示 |
| `confidence` | number | AI 提议 | `[0,1]`；不能单独决定 active |
| `relations` | object | AI 提议、Runtime 校验 | 见第 8 节 |
| `status` | enum | Runtime | `proposed/active/disputed/superseded/rejected` |
| `created_at` | RFC3339 UTC | Runtime | 记录物化时间，不伪装事件发生时间 |
| `idempotency_key` | string | Runtime | 同一次 proposal 重试不得产生重复表示 |

`idempotency_key` 首版按 `proposal_run_id + proposal_ordinal` 生成。它只解决请求重放，不承担语义去重；语义相同但来源不同的事件不能因哈希碰巧相同而丢失。

### 6.2 `kind`

| kind | 含义 | 不能替代 |
|---|---|---|
| `player_fact` | 玩家关于自己的稳定或阶段性事实 | 客观现实认证 |
| `preference_boundary` | 偏好、厌恶、习惯、称呼和关系边界 | 隐藏好感度 |
| `person_relation` | 玩家、秦未晞或第三人的人物关系 | 关系阶段状态机 |
| `shared_experience` | 对话中有证据支持的双方共同经历 | 无证据共同往事 |
| `relationship_meaning` | 某段互动对关系的意义或未解决影响 | 自动升级关系阶段 |
| `future_event` | 有跨回合意义但不保证触发的未来事项 | reminder、待办或系统通知 |
| `unfinished_topic` | 值得后续自然延续的问题、话题或情绪线 | 主动发送资格 |
| `character_self_claim` | 秦未晞关于自己经历、计划或状态的已发送表达 | 已确认自我时间线状态 |

首版不允许插件自行扩展 kind。新增类型必须升 schema 版本，不能塞入自由字符串。

### 6.3 `subject`

```json
{
  "type": "player | character | both | relationship | third_party",
  "entity_id": null,
  "display_name": null
}
```

- `player`、`character`、`both`、`relationship` 的 `entity_id` 和 `display_name` 必须为 `null`。
- `third_party` 必须有稳定 `entity_id`；`display_name` 只作可读信息，不能作为唯一索引。
- `shared_experience` 通常要求 `subject.type=both`。
- `relationship_meaning` 要求 `subject.type=relationship`。
- 主语不明确时保持候选或拒绝，不得由 CPU 猜测指代。

### 6.4 `epistemic`

```json
{
  "polarity": "affirmed | negated",
  "modality": "asserted | uncertain | hypothetical | joking | quoted",
  "grounding": "speaker_report | acknowledged | mutually_confirmed | runtime_observed"
}
```

- `speaker_report` 只证明某个说话者表达过该内容。
- `acknowledged` 表示另一方在对话中明确接住或认可，但不自动证明外部现实真实性。
- `mutually_confirmed` 至少需要用户和角色两类支持证据。
- `runtime_observed` 只能引用运行时实际提交的应用事件，不能用于模型想象。
- `hypothetical/joking/quoted` 可以形成“这段对话发生过”的记忆，但 `statement` 必须保留其模式，不能转写成既成事实。

### 6.5 `temporal`

```json
{
  "relation": "past | present | future | atemporal | unknown",
  "resolution": "resolved | relative | ambiguous | not_applicable",
  "source_text": null,
  "anchor_event_id": null,
  "start_at": null,
  "end_at": null,
  "timezone": null,
  "precision": "instant | minute | hour | part_of_day | day | range | unknown | not_applicable"
}
```

规则：

- 所有确定时间保存 RFC3339 UTC，另存原 IANA timezone。
- 区间采用 `[start_at, end_at)`；有 `end_at` 时必须晚于 `start_at`。
- `relative/ambiguous` 必须保留 `source_text` 和 `anchor_event_id`，不得填猜测时间。
- 可确定的“明天下午三点”只能相对证据事件时间和合法 timezone 解析。
- “下周”“改天”“过阵子”保持 `ambiguous` 或 `relative`，不能补成某一天。
- `future_event` 不提供 `due_at`、到期扫描或通知语义。

## 7. 证据合同

### 7.1 草稿证据

模型候选只输出：

```json
{
  "event_id": "...",
  "role": "support | contradict | correction | context",
  "quote": "账本中的连续原文片段",
  "start_hint": null
}
```

`start_hint` 只用于重复文本消歧，不能被信任为最终 offset。

### 7.2 物化证据

运行时根据账本原文定位后生成：

```json
{
  "event_id": "...",
  "role": "support",
  "excerpt_start": 0,
  "excerpt_end": 8,
  "excerpt": "原始连续片段",
  "excerpt_sha256": "64位小写十六进制"
}
```

offset 按 Python Unicode code point 计数，并满足：

```text
event.text[excerpt_start:excerpt_end] == excerpt
sha256(excerpt.encode("utf-8")) == excerpt_sha256
```

### 7.3 可用与不可用证据

可作为支持证据：

- 已提交的用户 `message`。
- 已完整生成并提交的角色 `message`。
- 后续正式定义并提交的合法 app/offscreen 事件。

不能作为支持证据：

- 模型内部思考、草稿或 prompt。
- 流式生成中的未完成文本。
- `generation_cancelled` 或 `turn_failed` 内容。
- 日志、向量、摘要、reranker 分数。
- 另一个 conversation 的事件。
- 只有 `context` 角色、没有 `support/correction` 的证据集合。

`character_self_claim` 只能由已提交角色消息或未来合法 offscreen 事件支持。玩家说“你昨天吃了面”只能证明玩家提出了这个说法，不能直接成为秦未晞的已确认自我经历。

`shared_experience` 必须有明确表达“双方共同发生过”的证据。仅因为用户和角色都在同一窗口说过话，不能推导出线下共同经历。

## 8. 冲突、修正与生命周期

### 8.1 关系边

```json
{
  "semantic_slot": null,
  "supersedes": [],
  "contradicts": [],
  "refines": []
}
```

- `semantic_slot` 是 AI 提议的冲突线索，不是 CPU 语义真理。
- 所有目标 memory 必须存在、属于同一 conversation，且不能指向自身。
- 三组 ID 内部唯一；同一目标不能同时出现在 `supersedes` 和 `refines`。
- `supersedes` 只有在新证据明确修正旧认识时成立。
- `contradicts` 只标记冲突，不能自动选择哪一个为真。
- `refines` 补充粒度，不使旧表示失效。

### 8.2 状态转换

允许转换：

| 当前状态 | 允许目标 |
|---|---|
| 新建 | `proposed` |
| `proposed` | `active`、`rejected`、`disputed` |
| `active` | `disputed`、`superseded` |
| `disputed` | `active`、`superseded`、`rejected` |
| `superseded` | 无 |
| `rejected` | 无 |

状态变化以后必须由追加式 journal 表达。MEM-01 只冻结状态集合和合法转换，不实现持久化。

## 9. 示例

玩家在一个已提交事件中说：“我下周可能去上海出差，还没完全定。”合理候选应保持不确定和含糊时间：

```json
{
  "kind": "future_event",
  "statement": "玩家表示下周可能去上海出差，但尚未确定",
  "subject": {
    "type": "player",
    "entity_id": null,
    "display_name": null
  },
  "epistemic": {
    "polarity": "affirmed",
    "modality": "uncertain",
    "grounding": "speaker_report"
  },
  "temporal": {
    "relation": "future",
    "resolution": "ambiguous",
    "source_text": "下周",
    "anchor_event_id": "event-user-123",
    "start_at": null,
    "end_at": null,
    "timezone": null,
    "precision": "unknown"
  },
  "evidence_quotes": [
    {
      "event_id": "event-user-123",
      "role": "support",
      "quote": "我下周可能去上海出差，还没完全定。",
      "start_hint": null
    }
  ],
  "semantic_reason": "数日后继续聊天时可能自然关心出差是否确定",
  "confidence": 0.96,
  "relation_suggestions": {
    "semantic_slot": "player.future.travel.shanghai",
    "supersedes": [],
    "contradicts": [],
    "refines": []
  }
}
```

该候选可以记住，但不能生成具体日期、提醒任务或自动消息。

## 10. 模块与文件规划

MEM-01 实施时新增：

```text
runtime/
  _memory_contracts.py
  schemas/
    memory_proposal_draft_v1.schema.json
    memory_representation_v1.schema.json

eval/memory_contract/
  fixtures/
    valid/
    invalid/
  mem01_contract_v1.json

tests/memory_contract/
  test_schema.py
  test_contracts.py
  test_cross_field_rules.py
  test_evidence_boundaries.py
  test_freeze.py
```

`_memory_contracts.py` 只包含枚举、不可变值对象、纯结构校验和序列化。它不导入 llama.cpp adapter、prompt、SQLite ledger 实现或向量库，防止合同层耦合后续机制。

## 11. 施工检查点

### MEM-01A：术语、枚举与字段冻结

- 建立本文件定义的 kind、subject、epistemic、temporal、evidence、relation 和 status 枚举。
- 明确字段所有权和长度/数量上限。

完成标准：字段表无自由扩展枚举，无“模型可直接 active”的入口。

施工结果（2026-08-07）：

- 新增 `runtime/_memory_contracts.py`，冻结 8 类记忆、主体、认识状态、时间、证据角色、关系类型和 lifecycle 枚举。
- 新增 `eval/memory_contract/mem01_vocabulary_v1.json`，机器可读地冻结草稿/物化字段、字段所有权、长度与数量上限及信任边界。
- 模型草稿严格限制为 9 个语义字段；`memory_id`、`status`、`created_at`、证据 offset/hash 和幂等信息全部归 Runtime。
- 未来事件字段集中不存在 `due_at`、`reminder`、`timer`、`notification` 或自动发送入口。
- 新增 `tests/memory_contract/test_vocabulary.py`；定向验收 `7 passed`。
- 本检查点没有创建 JSON Schema、数据库 migration、模型调用、向量或训练资产。

### MEM-01B：两份 JSON Schema

- 使用 JSON Schema Draft 2020-12。
- 所有对象关闭未知字段。
- 用 `$defs` 复用嵌套结构，启用 RFC3339 和 SHA256 格式约束。

完成标准：schema 自身合法，正例通过，未知字段和缺失字段失败。

施工结果（2026-08-07）：

- 新增 `runtime/schemas/memory_proposal_draft_v1.schema.json`，只接受 MEM-01A 冻结的 9 个 AI 候选字段。
- 新增 `runtime/schemas/memory_representation_v1.schema.json`，要求全部 18 个物化字段，并约束 schema version、UTC 时间与小写 SHA256。
- 两份 schema 均使用 Draft 2020-12、`$defs` 和递归 `additionalProperties: false`；必填字段、枚举、长度、数量、唯一集合和格式均可执行验证。
- 共享主体、认识状态、关系和时间枚举由自动测试约束为一致，防止两份 schema 独立漂移。
- `pyproject.toml` 已将 `runtime/schemas/*.json` 纳入安装包资源。
- 新增 `tests/memory_contract/test_schema.py`；MEM-01A/B 定向验收 `26 passed`，现有 Runtime 回归 `230 passed, 3 skipped`。
- 本检查点没有实现主体/kind、时间组合、证据 offset 或关系边语义校验；这些仍属于 MEM-01D。

### MEM-01C：Python 不可变合同

- 使用 `dataclass(frozen=True, slots=True)` 和 `Literal/TypeAlias`，保持当前 runtime 风格。
- 实现确定性序列化和反序列化，不接受隐式类型转换。

完成标准：JSON 与 Python 往返一致，集合顺序和 canonical JSON 稳定。

施工结果（2026-08-07）：

- `runtime/_memory_contracts.py` 已新增 `MemoryProposalDraft`、`MemoryRepresentation` 及主体、认识状态、时间、草稿证据、物化证据和关系的 frozen/slots 值对象。
- 新增严格 dict/JSON 解析入口；拒绝缺失字段、未知字段、非字符串 key、重复 JSON key、NaN/Infinity、字符串冒充数字和布尔冒充整数。
- JSON array 经过逐项验证后显式转为 tuple；直接构造合同对象时不接受 list，嵌套集合不可修改。
- 新增确定性 dict 序列化和 UTF-8 canonical JSON；字段排序、紧凑编码和中文原文输出稳定。
- 物化 `created_at/start_at/end_at` 只接受 UTC `Z`；草稿允许带 offset 的 RFC3339 候选，等待 Runtime 规范化。
- 新增 `tests/memory_contract/test_contracts.py` 和独立测试包；MEM-01A/B/C 定向验收 `41 passed`。
- 非 llama Runtime 回归 `195 passed, 3 skipped`；联合回归中的两个既有 llama.cpp 子进程计数用例各出现一次时序抖动，单独重跑均 `1 passed`，未修改无关适配器。
- 本检查点仍未实现主体/kind、时间组合、excerpt 内容/hash 和关系边重叠等跨字段语义；这些属于 MEM-01D。

### MEM-01D：跨字段纯校验

- 校验主体与 kind、时间 resolution、认识状态、证据数量和关系边组合。
- 校验 excerpt offset/hash 的纯函数；不在本阶段访问数据库。

完成标准：每条不变量均有最小失败用例和稳定错误码。

施工结果（2026-08-07）：

- `runtime/_memory_contracts.py` 已提供主体/kind、时间组合、认识状态、证据角色、原文 offset/hash、关系目标、生命周期和幂等冲突的纯校验入口。
- `EvidenceSource` 只承载调用方已取得的事件快照；校验器不访问 SQLite、模型、向量库或网络。
- 已锁定未知字段、非法枚举、跨字段、证据缺失、原文不匹配、跨 conversation、未提交输出、关系目标、状态转换和幂等冲突错误码。
- 新增 `tests/memory_contract/test_cross_field_rules.py`，覆盖所有 kind/subject 合法组合、IANA timezone、区间顺序、双方证据、角色自述来源和关系边界。

### MEM-01E：证据边界夹具

- 覆盖玩家事实、偏好边界、共同经历、关系意义、未来事件、未完话题和角色自我表达。
- 覆盖否定、假设、玩笑、引用、不确定、修正、冲突、跨 conversation、取消输出和空证据。

完成标准：正反例说明语义边界，不把关键词命中当作事实成立。

施工结果（2026-08-07）：

- 新增 `eval/memory_contract/fixtures/valid` 与 `invalid` 夹具，八类 memory kind 均有可执行正例。
- 正例保留 `negated`、`uncertain`、`hypothetical`、`joking`、`quoted`，并覆盖 correction、supersedes 和 contradicts，不把它们压平成确定事实。
- 反例覆盖跨 conversation、取消或未提交输出、原文不匹配、仅 context、玩家代替角色自述、单方冒充双方确认、含糊时间被猜成日期和空证据。
- “零候选”作为合法集合结果单独冻结；系统不会为了记忆数量强迫模型制造候选。
- 新增 `tests/memory_contract/test_evidence_boundaries.py`，同一夹具同时经过 Draft 2020-12 schema、不可变 Python 合同和跨字段纯校验。

### MEM-01F：合同冻结

- 生成文件清单、字节数、SHA256 和规范化 manifest 自哈希。
- 冻结需求与唯一 AI 设计的当前引用版本，不原地覆盖旧 CHAT-01/FREEZE-03 合同。

完成标准：`--verify` 可重复通过；任何 schema、枚举或夹具漂移都会失败。

施工结果（2026-08-07）：

- 新增 `eval/memory_contract/freeze.py` 和 `eval/memory_contract/mem01_contract_v1.json`，逐项冻结规范化相对路径、字节数与 SHA256。
- manifest 使用 `manifest_sha256=null` 的 canonical JSON 计算自哈希，且 `--freeze` 拒绝覆盖已经存在的合同。
- `--verify` 校验 manifest 自哈希、资产精确顺序、成员、字节数和 SHA256；schema、词表、校验器、夹具或权威引用漂移均会失败。
- 冻结独立于旧 CHAT-01/FREEZE-03 合同，没有原地覆盖历史评测资产。
- 新增 `tests/memory_contract/test_freeze.py`，覆盖重复冻结拒绝、资产漂移和 manifest 篡改。

## 12. 必测矩阵

| 场景 | 预期 |
|---|---|
| “我喜欢吃辣” | `preference_boundary`，玩家 speaker report |
| “我不喜欢吃辣” | 保留 `negated`，不能转成喜欢 |
| “如果我去上海就好了” | `hypothetical`，不能成为确定行程 |
| “我下周可能面试” | `future_event + uncertain + ambiguous`，无 due_at |
| “提醒我明天交材料” | 可记为玩家提及的未来事项，但 MEM-01 不创建提醒 |
| 玩家说“你昨天吃了面” | 不能直接成为 active character self fact |
| 角色完整回复“我昨天吃了面” | 可形成 `character_self_claim` 候选，仍待 SELF 阶段确认 |
| 取消生成中的“我昨天吃了海鲜” | 不可作为证据 |
| “我们上次一起去海边”但账本无此前支持 | 只能记为当前说话者报告，不得伪造成 mutually confirmed |
| 后续明确纠正“不是海边，是湖边” | 新表示通过 correction/supersedes 关联旧表示 |
| quote 与 event 原文不一致 | 拒绝 |
| event 属于其他 conversation | 拒绝 |
| 相同 proposal 重试 | 相同幂等结果，不新增表示 |
| 模型返回零个候选 | 合法，不强迫生成记忆 |

## 13. 错误合同

首版错误使用稳定机器码，不依赖异常文本判断：

- `memory_schema_invalid`
- `memory_unknown_field`
- `memory_invalid_enum`
- `memory_cross_field_invalid`
- `memory_evidence_missing`
- `memory_evidence_excerpt_mismatch`
- `memory_evidence_cross_conversation`
- `memory_evidence_uncommitted`
- `memory_relation_missing_target`
- `memory_relation_cross_conversation`
- `memory_transition_invalid`
- `memory_idempotency_conflict`

错误结果不能包含完整聊天原文；调试记录只保留 event_id、字段路径和错误码。

## 14. 验收标准

MEM-01 只有同时满足以下条件才算完成：

1. 两份 schema 均通过 Draft 2020-12 自校验。
2. Python 合同与 JSON Schema 对全部冻结夹具结论一致。
3. 所有 kind、认识状态、时间状态和 lifecycle 转换均有正反测试。
4. 模型无法通过草稿字段设置 ID、active、时间戳或证据 hash。
5. 无证据、跨 conversation、未提交输出和 quote 不匹配均被拒绝。
6. 否定、假设、玩笑、不确定和修正不会被扁平化成确定事实。
7. 含糊未来事件不产生具体日期、due_at 或提醒任务。
8. 零候选合法，不能为了“记忆数量”制造低价值条目。
9. 合同冻结 manifest 可独立复验。
10. 未修改训练数据、未调用真实模型、未新增数据库 migration。

## 15. 后续衔接

MEM-01 完成后进入 MEM-02：冻结 `MEMORY_PROPOSE` 模式。MEM-02 只能产生本文件定义的 `MemoryProposalDraft`；MEM-03 才使用账本验证证据并物化 `MemoryRepresentation`，MEM-04 才追加提交状态变化，MEM-05 才为 `active/disputed` 表示生成带状态的 selector view 和向量。

任何后续阶段都不能绕过这条边界直接把模型 JSON 写入当前有效记忆。
