# RECALL-01：查询表示合同施工文档

> 状态：已完成（查询构造与编码合同已冻结；下一检查点 RECALL-02）  
> 上游：MEM-06 后台调度与性能隔离合同  
> 合同：`eval/selector_query/recall01_contract_v1.json`

## 1. 目标与边界

RECALL-01 把每轮在线判断需要的内容收敛成唯一的 `SelectorQuery`，并冻结确定性序列化、哈希、长度门和本地 BGE 编码行为。本阶段不读取记忆矩阵，不计算相似度，不聚合 `memory_id`，也不产生 Top32；这些分别属于 RECALL-02 和 RECALL-03。

查询只能包含玩家当前输入、少量最近对话、白名单工作状态和 Runtime 注入的本地时间。控制指令、数据库查询、候选 ID、事件 ID、完整历史记忆及玩家不可见系统秘密均不能进入查询表示。

## 2. SelectorQuery v1

字段固定为：

```text
SelectorQuery
- schema_version = recall-selector-query-v1
- current_user_message
- recent_dialogue
- compact_working_state
- current_time_context
```

`recent_dialogue` 冻结为最多 4 个最近的完整消息事件，保持原顺序和连续后缀。当前玩家事件已由 `ReplyContext` 排除在 history 外，构造器再次以该边界为前提，不复制当前消息。长度不足时允许 0 至 4 个事件；不能为了凑固定数量读取更早的不连续消息。

文本采用 `" ".join(value.split())` 规范化 Unicode 空白，不做 Unicode 兼容折叠，不改写文字内容。canonical JSON 使用 UTF-8、键排序、紧凑分隔符和禁止 NaN；encoder 文本使用固定字段标签及固定顺序。事件 ID 和 sequence number 只属于上游 provenance，不进入 canonical query 或 encoder 文本。

## 3. Compact Working State

当前工作激活区尚未提供冻结的情绪或关系阶段，因此 v1 只允许两个不会泄露内部标识的确定性字段：

- `has_completed_exchange`：当前 epoch 是否至少已有完整的一问一答；
- `unresolved_prior_user_turns`：排除当前事件后，尚未得到角色回复的玩家事件数量。

`explicit_recall_cues`、最近事件 ID、完成事件 ID 和 epoch ID 均不进入 query。以后增加 mood、relationship stage 等语义状态必须升 query schema 和冻结 manifest，不能在 v1 下静默加字段。

## 4. 时间与长度

时间只读取 `WorkingActivation.current_time/current_timezone`，禁止在构造器内调用系统当前时间。冻结字段为带 offset 的秒级本地时间、IANA 时区字符串、ISO weekday 1 至 7，以及 `night/morning/afternoon/evening` 四段。

构造期先执行 480 Unicode 字符的稳定预算。超限时从最旧 recent dialogue 开始按完整事件删除；当前消息及任何单条历史消息都不截断。删除全部历史后仍超限则显式失败。

字符预算不是 token 预算的替代。编码前必须用与目标 encoder 完全相同 identity 的本地 tokenizer 计数，包含其正常 special tokens 后不得超过 512；超限时不调用 encoder。

## 5. 编码合同

查询编码器沿用 MEM-05 唯一 profile：

- `BAAI/bge-small-zh-v1.5`；
- CPU、本地显式资产、禁止联网下载；
- 512 维、mean pooling、max sequence length 512；
- Runtime L2 normalization，输出 `float32le`；
- model ID、revision、artifact SHA256 和 dimension 必须与 active memory vector generation 完全一致。

每个 query 只允许一次 tokenizer 计数和一次 `encoder.encode((rendered_text,))` 调用。返回必须恰好一行，且维度、finite、非零 norm 和 float32 L2 均通过校验；结果保存 query hash、renderer hash、vector hash 和 encoder identity，供 RECALL-02 日志与复现使用。

## 6. 验收证据

专项测试覆盖 deterministic JSON/hash/renderer、0/2/4 轮边界、最新 4 轮、当前消息不重复、完整事件裁剪、Unicode 空白、注入时间、状态白名单、token 上限、单次编码、identity 漂移、错误维度、NaN/Inf、零向量和链式 manifest 漂移。

当前工作区没有交付真实 `BAAI/bge-small-zh-v1.5` 本地资产，因此本阶段用协议桩验证合同和数学边界，不声称已经完成真实模型向量逐字节复现或目标机编码 P95。真实资产到达后必须以 manifest 中的 revision/artifact SHA256 重跑同 query 的离线/在线一致性与性能验收。

## 7. 下一检查点

下一步进入 RECALL-02：读取 MEM-05 active generation 的连续向量矩阵，对全部当前有效 selector views 执行 exact dot product，并冻结空库、稳定排序、generation 漂移和全库覆盖日志。RECALL-02 不负责 `memory_id` 去重或 Top32，后者仍留给 RECALL-03。

