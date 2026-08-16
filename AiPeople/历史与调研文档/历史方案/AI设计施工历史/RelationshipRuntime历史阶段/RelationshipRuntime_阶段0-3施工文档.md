# RelationshipRuntime 阶段 0-3 施工文档

> 状态：阶段 0-3 已实现并验收  
> 版本：v1.1  
> 日期：2026-08-04  
> 上位设计：`设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`  
> 覆盖范围：工程基线、运行时合同、追加式证据账本、FakeModel 纵向闭环

## 1. 文档目的

本文把唯一 AI 实现中的阶段 0-3 拆成可以逐项编码、测试和验收的施工任务。阶段结束时，系统尚不接入真实 Qwen、人格训练、工作激活区、语义投影、计划状态机、桌面前端或语音，但必须拥有一条可靠的文字回合主链路：

```text
UserMessage
  -> 原始输入提交到 SQLite
  -> FakeModel 流式生成
  -> TextDelta 流
  -> 完整回复提交到 SQLite
  -> Completed
```

失败、取消和重试也必须经过同一条链路，并留下可审计事件。

### 1.1 实施结果

2026-08-04 已完成本文范围：

- `runtime/` 深模块、合同、设置、FakeModel、迁移、账本和冒烟入口已实现。
- 自动测试共 46 项，全部通过。
- FakeModel 命令行冒烟通过，流式产生三个 delta 后提交完整回复。
- 临时数据库验证得到一条 `user/message` 和一条 `character/message`，FTS5 可检索回复。
- 公开导入未加载 torch、transformers 或 LanceDB。
- `.venv` 已通过 `ensurepip` 补齐 pip，并安装本项目 dev extra。

## 2. 权威边界

本文件从属于《AI女友最小心智系统设计》，不定义第二套架构。发生冲突时按以下顺序处理：

1. `需求文档/项目框架需求.md`
2. `设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`
3. 本施工文档

阶段 0-3 明确不做：

- 不接入真实模型、Ollama 或 llama.cpp。
- 不训练秦未晞 LoRA。
- 不实现 `RECALL_PLAN` 或 `MEMORY_PROPOSE`。
- 不建立独立 `memory` 模块或 memory 服务。
- 不使用 LanceDB、向量数据库、embedding 或摘要记忆。
- 不实现工作激活区、epoch、心境、关系阶段和计划状态机。
- 不实现 HTTP、桌面 UI、动作标签、STT 或 TTS。

## 3. 当前仓库基线

施工开始前的已知状态：

| 项目 | 当前状态 | 对施工的影响 |
|---|---|---|
| Python | `.venv` 为 Python 3.11 | 作为阶段 0-3 固定版本 |
| SQLite | 3.53.1，FTS5 可用 | 直接使用标准库 `sqlite3` |
| `runtime/` | 空目录 | 在此建立唯一深模块 |
| `memory/` | 空目录 | 不使用，不在其中增加实现 |
| 测试 | 无项目测试体系 | 阶段 0 首先建立 pytest |
| 模型 | 只有旧角色 Adapter | 阶段 0-3 使用 FakeModel |
| 秦未晞数据 | 295 条清洗后样本 | 本阶段不修改、不训练 |
| `requirements.txt` | 混有旧向量记忆依赖 | 本阶段不得从中引入运行时依赖 |

## 4. 模块和 seam

### 4.1 唯一外部模块

对调用方只提供 `RelationshipRuntime`。调用方不接触 SQLite 连接、表、模型 prompt 或重试实现。

```python
async with RelationshipRuntime.open(config, reply_model) as runtime:
    async for event in runtime.handle_turn(user_message):
        consume(event)
```

Python 中使用 `handle_turn`，对应上位设计中的语言无关名称 `handleTurn`，两者是同一个 interface。

外部 interface 只包含：

- `RelationshipRuntime.open(...)`
- `RelationshipRuntime.handle_turn(...)`
- 异步上下文管理所需的关闭行为

不要公开 `append_event()`、`save_message()`、`next_sequence()` 或数据库查询方法。这些属于内部实现。

### 4.2 模型内部 seam

模型是未来的本地进程依赖，因此定义一个内部 port：

```python
class ReplyModel(Protocol):
    def stream_reply(self, request: ReplyRequest) -> AsyncIterator[str]: ...
```

阶段 0-3 只有 `FakeReplyModel`；后续增加 `LlamaCppReplyModel` 后，这个 seam 才拥有生产和测试两个 Adapter。FakeModel 必须支持：固定分片、延迟、指定分片后异常、空回复和取消。

### 4.3 SQLite 不建立 repository port

SQLite 是唯一持久化实现，测试可以使用临时目录中的真实 SQLite，因此不额外定义 repository interface，也不写内存数据库假实现。这样可直接验证 WAL、约束、FTS5、重启和事务行为。

## 5. 目标目录

阶段 0-3 只创建以下结构：

```text
pyproject.toml
runtime/
  __init__.py
  contracts.py
  relationship_runtime.py
  _ledger.py
  _model.py
  _settings.py
  adapters/
    __init__.py
    fake_model.py
  migrations/
    001_event_ledger.sql
  demo_fake.py
tests/
  runtime/
    test_sqlite_capabilities.py
    test_contracts.py
    test_ledger.py
    test_relationship_runtime.py
```

命名以下划线开头的文件表示内部实现，不构成调用方 interface。不要为了每张表再拆一个浅模块。

## 6. 阶段 0：工程基线

### 6.1 目标

建立一套与旧训练实验隔离、可以重复运行的 Python 测试基线。

### 6.2 施工任务

1. 新建 `pyproject.toml`，固定 `requires-python = ">=3.11,<3.12"`。
2. 生产运行时阶段 0-3 不增加第三方依赖。
3. 仅增加开发依赖：`pytest` 和 `pytest-asyncio`。
4. 配置 pytest 只搜索 `tests/`，避免把根目录旧 `chat_test.py` 当成测试收集。
5. 将 `runtime/` 变成 Python package。
6. 配置测试临时目录，每个测试使用独立 SQLite 文件，不复用玩家数据。
7. 使用标准库 `logging`，默认只记录事件 ID、耗时和错误码，不记录完整对话正文。
8. 不移动、不删除旧训练脚本；现行运行时代码不得 import 它们。

建议的 pytest 配置：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

### 6.3 基线探针

在自动测试中加入一次 SQLite 能力探针：

- `PRAGMA journal_mode=WAL` 可启用。
- `json_valid()` 可用。
- FTS5 可创建虚拟表。
- `tokenize='trigram'` 可创建并检索至少三个汉字的连续短语。

如果目标 Windows Python 不支持 trigram，阶段 2 必须停止并明确选定中文 FTS 替代方式，不能静默退回效果未知的分词方案。

### 6.4 退出条件

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

必须满足：

- pytest 正常启动。
- 只收集 `tests/` 下的测试。
- SQLite 四项探针全部通过。
- 不加载 torch、transformers、LanceDB 或模型权重。

## 7. 阶段 1：运行时合同

### 7.1 输入合同

`contracts.py` 定义不可变值对象。阶段 0-3 只接受文字输入：

```python
@dataclass(frozen=True)
class UserMessage:
    request_id: str
    conversation_id: str
    text: str
    occurred_at: datetime
    timezone: str
    source: Literal["typed", "stt"] = "typed"
```

合同约束：

- `request_id` 由调用方生成，用于重试幂等；同一用户操作的所有重试必须复用它。
- `conversation_id` 不能为空；阶段 0-3 不负责创建多角色或多用户空间。
- 使用 `text.strip()` 只判断是否为空，但入库和传递时必须保留调用方提供的完整原文，不得自动裁剪空白或换行。
- 初始上限为 8192 个 Unicode 字符，超过时返回输入错误，不截断原文。
- `occurred_at` 必须带时区；入库时转换为 UTC，同时保存原始 `timezone`。
- `source` 虽预留 `stt`，阶段 0-3 的实际调用只使用 `typed`。

### 7.2 输出合同

```python
@dataclass(frozen=True)
class TextDelta:
    request_id: str
    text: str

@dataclass(frozen=True)
class Completed:
    request_id: str
    user_event_id: str
    assistant_event_id: str
    text: str
    metrics: TurnMetrics

@dataclass(frozen=True)
class Failed:
    request_id: str
    user_event_id: str | None
    code: str
    retryable: bool
```

阶段 0-3 不提前加入 emotion、action、effect 等空实现。后续在保持 `handle_turn()` 不变的前提下扩展 `ReplyEvent` 联合类型。

错误码初始集合：

| 错误码 | 可重试 | 含义 |
|---|---:|---|
| `invalid_input` | 否 | 输入不满足合同 |
| `request_conflict` | 否 | 同一 request ID 对应不同输入 |
| `turn_in_progress` | 是 | 相同 request ID 正在本进程生成 |
| `ledger_unavailable` | 是 | 用户输入未能提交，模型不得调用 |
| `model_unavailable` | 是 | FakeModel 或后续模型调用失败 |
| `empty_model_response` | 是 | 模型正常结束但未生成正文 |
| `commit_failed` | 是 | 回复已生成但完成事件提交失败 |

### 7.3 ReplyRequest

阶段 0-3 的内部 `ReplyRequest` 只包含本轮必要字段：

```python
@dataclass(frozen=True)
class ReplyRequest:
    request_id: str
    conversation_id: str
    user_event_id: str
    text: str
```

不要在此阶段伪造工作状态、历史、RecallFrame 或角色 prompt。后续由 prompt 编排实现扩展内部对象，不改变外部 interface。

### 7.4 合同测试

- 空文本、纯空白、无时区时间、空 ID 和超长输入被拒绝。
- 合法中文、换行、标点和首尾空白逐字符保持原样。
- `ReplyEvent` 不暴露 SQLite row、连接或模型内部对象。
- FakeModel 能按配置产生多个非空分片。
- FakeModel 能模拟空回复、异常和等待取消。

### 7.5 退出条件

- 合同测试全部通过。
- FakeModel 单独测试不访问文件、网络或 GPU。
- 外部调用方只需要理解 `UserMessage`、`ReplyEvent` 和生命周期。

## 8. 阶段 2：追加式证据账本

### 8.1 数据库位置和连接

生产默认路径由 `RuntimeConfig.data_dir` 决定：

```text
<data_dir>/relationship.sqlite3
<data_dir>/relationship.sqlite3.lock
```

运行时持有旁路 lock 文件的操作系统排他锁，防止第二个进程或实例同时打开同一个玩家数据库。正常关闭后释放锁；lock 文件可以保留，不包含聊天数据。

连接初始化固定执行：

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

一个 `RelationshipRuntime` 实例拥有一个账本连接管理器。阶段 0-3 仅支持单进程运行；禁止两个运行时同时操作同一个玩家数据库。进程内允许不同 conversation 顺序执行，暂不追求并行生成。

### 8.2 迁移表

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
) STRICT;
```

迁移必须按文件名版本顺序执行，同一数据库重复启动不得重复应用。迁移失败时启动失败，不允许带着半套 schema 继续聊天。

### 8.3 事件表

`001_event_ledger.sql` 建立：

```sql
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    occurred_timezone TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('user', 'character', 'system')),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('message', 'turn_failed', 'generation_cancelled')
    ),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    source TEXT NOT NULL CHECK (source IN ('typed', 'stt', 'runtime')),
    causation_event_id TEXT REFERENCES events(event_id),
    supersedes_event_id TEXT REFERENCES events(event_id),
    schema_version INTEGER NOT NULL,
    UNIQUE (conversation_id, sequence_no)
) STRICT;

CREATE UNIQUE INDEX uq_user_request
ON events(request_id)
WHERE actor = 'user' AND event_type = 'message';

CREATE UNIQUE INDEX uq_completed_reply
ON events(request_id)
WHERE actor = 'character' AND event_type = 'message';

CREATE INDEX ix_events_time ON events(occurred_at);
CREATE INDEX ix_events_actor ON events(actor);
CREATE INDEX ix_events_conversation ON events(conversation_id, sequence_no);
CREATE INDEX ix_events_causation ON events(causation_event_id);
```

`payload_json` 的阶段 0-3 结构：

```json
{"text":"原始文本","status":"complete"}
```

失败或取消事件使用：

```json
{
  "code":"model_unavailable",
  "partial_text":"已经显示给玩家但未完成的文字",
  "emitted_chars":14,
  "status":"failed"
}
```

已流式显示的部分文字也是发生过的交互，必须保存在失败或取消事件中，但不能伪装成完整 `character/message`。

### 8.4 追加不可变约束

创建触发器拒绝普通更新和删除：

```sql
CREATE TRIGGER events_reject_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;

CREATE TRIGGER events_reject_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only');
END;
```

未来“清空全部数据”通过关闭运行时、明确确认后删除整个数据库文件并重建实现，不为单条事件开放绕过触发器的接口。

### 8.5 FTS5 自动索引

阶段 2 同时建立自动全文索引：

```sql
CREATE VIRTUAL TABLE event_fts USING fts5(
    event_id UNINDEXED,
    text,
    tokenize = 'trigram'
);
```

只通过 `events` 的 INSERT trigger 为 `message` 事件写入 `event_fts`。业务代码不得先写 FTS 再写事件。阶段 2 只验证自动索引正确，不实现 RecallFrame 和召回排序。

trigram 适合无空格中文的连续短语。少于三个汉字的查询后续由结构化字段或受限 `LIKE` 补充，不在本阶段发明中文分词模块。

### 8.6 写入事务

每次追加使用 `BEGIN IMMEDIATE`：

1. 根据 `request_id` 检查幂等记录。
2. 对同一 conversation 查询当前最大 `sequence_no`。
3. 分配 `sequence_no + 1`。
4. 插入事件。
5. 由 trigger 自动写 FTS。
6. 提交事务。

任何一步失败都回滚整个追加操作。用户输入提交失败时不得调用 FakeModel。

### 8.7 账本内部测试

- 空数据库首次启动执行迁移。
- 重复启动迁移幂等。
- 两次追加获得连续且唯一的 sequence。
- 相同用户 `request_id` 不产生第二条用户事件。
- 相同 request ID 但不同 conversation、text 或 occurred_at 返回冲突。
- UPDATE 和 DELETE 被数据库触发器拒绝。
- 非法 actor、event type、source 和 JSON 被约束拒绝。
- causation 指向不存在事件时写入失败。
- 中文短语可通过 FTS 找到对应 event ID。
- 关闭并重新打开数据库后事件和索引仍存在。
- 人为破坏迁移时启动失败，不继续写入。

### 8.8 退出条件

- 账本测试全部通过。
- 用户输入一次事务即可完成事件和 FTS 写入。
- 数据库中不存在业务 UPDATE 或单条 DELETE 路径。
- SQLite 写入和幂等检查在开发机 P95 小于 50 毫秒。

## 9. 阶段 3：FakeModel 纵向闭环

### 9.1 正常回合时序

```text
调用 handle_turn(UserMessage)
          |
          v
校验输入和 request_id
          |
          v
BEGIN IMMEDIATE
追加 user/message + FTS
COMMIT
          |
          v
调用 FakeReplyModel.stream_reply
          |
          v
逐个产出 TextDelta，同时在内存累计完整正文
          |
          v
BEGIN IMMEDIATE
追加 character/message，causation=user_event_id
COMMIT
          |
          v
产出 Completed
```

不可改变的顺序：

1. 用户消息提交成功后才能调用模型。
2. 每个非空模型分片立即变成 `TextDelta`，不得等待完整回复。
3. 完整角色消息提交成功后才能产出 `Completed`。
4. `Completed.text` 必须等于全部 `TextDelta.text` 顺序拼接结果。

### 9.2 失败处理

#### 输入提交失败

- 不调用模型。
- 产出 `Failed(code="ledger_unavailable", user_event_id=None)`。

#### 模型生成失败

- 用户事件已经保留。
- 追加 `system/turn_failed`，`causation_event_id` 指向用户事件。
- payload 保存错误码、已显示部分正文和字符数，不保存异常堆栈。
- 产出可重试 `Failed`。

#### 空回复

- 按 `empty_model_response` 记录失败事件。
- 不写入完整角色消息。

#### 完成提交失败

- 不产出 `Completed`。
- 尝试记录 `commit_failed`；如果数据库本身不可写，只写本地结构化错误日志。
- 返回可重试 `Failed`，保留同一个 request ID。

#### 调用方取消

- 捕获 `asyncio.CancelledError`，也处理调用方主动关闭异步流的 `GeneratorExit`。
- 使用受保护的短事务追加 `system/generation_cancelled`，保存已显示部分正文。
- 完成记录后重新抛出取消，不把取消伪装成正常 `Failed` 或 `Completed`。

### 9.3 幂等重试

同一 `request_id` 的行为：

| 已有状态 | 重试行为 |
|---|---|
| 没有用户事件 | 正常提交并生成 |
| 用户事件存在，之前失败或取消 | 复用原 user event，不重复写入，重新生成 |
| 完整角色回复已存在 | 不再次调用模型；产出一个包含完整正文的 TextDelta，再返回原 Completed |
| 相同 request ID 但输入不同 | 返回 `request_conflict` |
| 同 request ID 正在本进程生成 | 返回 `turn_in_progress` |

阶段 0-3 使用进程内、按 request ID 的异步锁防止并发重复生成。系统当前是单进程单角色，不实现跨进程分布式锁。

### 9.4 可观察指标

每个 Completed 记录：

```text
ledger_user_commit_ms
model_first_delta_ms
model_total_ms
ledger_reply_commit_ms
total_ms
output_chars
delta_count
replayed
```

结构化日志只输出 request ID、event ID、错误码和指标，不输出 `text` 或 `partial_text`。

### 9.5 运行时 interface 测试

这些测试从 `RelationshipRuntime.handle_turn()` 进入，除故障注入外不直接调用内部方法：

1. 正常生成三个分片，按顺序收到三个 `TextDelta` 和一个 `Completed`。
2. FakeModel 被调用时，使用独立 SQLite 连接已经能看见用户事件，证明先提交后生成。
3. 正常完成后重启运行时，完整用户和角色事件仍存在。
4. 模型在第二个分片后异常，只记录失败事件和 partial text，不写完整角色消息。
5. 空回复写失败事件，不写完整角色消息。
6. 取消后写取消事件，调用方收到 `CancelledError`。
7. 失败后使用相同 request ID 重试，只存在一条用户事件。
8. 完成后使用相同 request ID 重试，FakeModel 调用次数不增加。
9. 使用相同 request ID 和不同文本重试，返回不可重试冲突。
10. SQLite 在用户提交时故障，FakeModel 调用次数为零。
11. SQLite 在回复提交时故障，不产出 `Completed`。
12. 两个相同 request ID 并发进入，最多一次调用 FakeModel。
13. `Completed.text` 与所有 delta 拼接严格相等。
14. 日志中不出现用户输入、角色回复和 partial text。

### 9.6 手工冒烟程序

测试通过后提供一个最小开发入口，但不把它作为正式前端：

```powershell
.\.venv\Scripts\python.exe -m runtime.demo_fake
```

允许输入一句文字，终端逐块显示 FakeModel 回复，退出后重新启动仍使用同一数据库。`demo_fake` 只能调用公开 interface，不得直接操作 `_ledger.py`。

### 9.7 退出条件

- 第 9.5 节所有自动测试通过。
- 手工冒烟能够流式显示，不等待完整回复。
- 强制异常、取消和重试后账本状态符合规定。
- 普通回合只有一次 FakeModel 调用。
- 不需要网络、GPU、torch、transformers 或角色 Adapter。

## 10. 分阶段提交顺序

实施时按以下顺序形成小提交或独立检查点：

1. `P0-00`：`pyproject.toml`、测试目录和 SQLite 能力探针。
2. `P0-01`：输入输出合同、配置和 FakeModel。
3. `P0-02`：迁移执行器及 schema migration 测试。
4. `P0-03`：events、不可变触发器和追加事务。
5. `P0-04`：FTS5 trigger、中文短语和重启测试。
6. `P0-05`：RelationshipRuntime 正常流式链路。
7. `P0-06`：失败、取消、幂等重试和并发保护。
8. `P0-07`：指标、隐私日志和 FakeModel 冒烟入口。

每个检查点必须保持全部已有测试通过。不要先创建所有空文件，再集中填充实现。

## 11. 总体验收清单

完成阶段 0-3 前逐项确认：

- [x] `RelationshipRuntime` 是调用方唯一需要理解的模块。
- [x] `memory/` 没有新增独立实现。
- [x] SQLite 是唯一事实来源，events 不可更新或单条删除。
- [x] 用户输入始终先于模型调用提交。
- [x] 完整角色回复始终先于 Completed 提交。
- [x] 部分回复在失败/取消事件中保留，但不伪装成完整消息。
- [x] request ID 重试不会重复保存用户输入或重复生成已完成回复。
- [x] FTS 由事件 INSERT trigger 自动维护。
- [x] 所有测试使用临时真实 SQLite，而不是内存 repository 假实现。
- [x] 日志不包含完整私密对话。
- [x] 自动测试和 FakeModel 冒烟均不加载 GPU 模型。

## 12. 阶段 3 后的下一入口

阶段 0-3 验收后，下一份施工文档从真实模型 Adapter 开始：

1. 下载或准备 Qwen3-4B 基座 GGUF。
2. 接入常驻 llama.cpp 和流式 HTTP Adapter。
3. 实现固定身份锚及 `REPLY` prompt。
4. 保持本文件定义的账本事务和外部 interface 不变。

在阶段 0-3 未通过前，不启动秦未晞正式训练，也不把真实模型接入主链路。
