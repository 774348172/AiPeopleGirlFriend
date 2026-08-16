# MEM-03：确定性证据校验与物化施工文档

> 状态：已完成（MEM-03A 至 MEM-03F 已冻结；下一检查点 MEM-04）  
> 版本：v1.0  
> 日期：2026-08-07  
> 上游合同：`eval/memory_propose/mem02_contract_v1.json`  
> 当前总纲：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`

## 1. 目标与边界

MEM-03 将通过 MEM-02 的 `MemoryProposalDraft` 对照追加式事件账本做确定性验证，并物化为初始状态为 `proposed` 的 `MemoryRepresentation`。本阶段不提交记忆、不执行 accept/reject、不持久化重试、不生成向量，也不负责后台调度；这些依次属于 MEM-04、MEM-05 和 MEM-06。

确定性程序只验证模型已经给出的证据和结构，不使用关键词、人物名或规则重新判断“是否值得记住”。空 proposals 仍是一等成功结果，并且不读取账本、不分配 memory ID。

## 2. 唯一流程位置

```text
MEM-02 MemoryProposeRequest + MemoryProposeResult
  -> 校验 run ID、schema 与窗口边界
  -> 对照账本验真整个输入事件快照
  -> 唯一定位逐字 quote，计算 offset 与 SHA256
  -> 将可解析时间统一规范化为 RFC3339 UTC Z
  -> 校验证据角色、grounding、conversation 与关系边界
  -> 整批物化 proposed MemoryRepresentation[]
  -> MEM-04 追加式提交 accept/reject 与重试状态
```

## 3. 证据验真合同

每个输入事件必须在账本中存在，且 conversation、sequence、actor、event type、UTC occurred_at、IANA timezone、原文和 complete 状态与模型输入快照完全一致。任一事件缺失或漂移，整批失败，不能只接受剩余候选。

证据 quote 必须是账本原文的连续逐字片段：

- 有 `start_hint` 时，必须精确指向该片段；
- 无 `start_hint` 时，原文中必须只有一个匹配；
- 同一 quote 重复出现而模型未给 offset 时拒绝，禁止随意选择第一次出现；
- 最终保存 `excerpt_start`、`excerpt_end`、`excerpt` 与小写 SHA256。

## 4. 时间与 Runtime 字段

`resolved` 时间允许模型输出带时区偏移的 RFC3339，物化时统一转换为 UTC `Z`；含糊和相对时间仍保留原始 `source_text` 与 anchor，不猜测日期。

Runtime 独占并设置：

- `memory_id`；
- `version=1`；
- `proposal_ordinal`；
- `status=proposed`；
- `created_at`；
- `idempotency_key={proposal_run_id}:{proposal_ordinal}`；
- 最终 evidence offset/hash。

## 5. 失败与批处理

稳定失败码冻结在 `eval/memory_materialization/mem03_profile_v1.json`。所有非空批次均为全有或全无；任何事件伪造、quote 缺失或歧义、时间非法、证据角色不满足 grounding/kind，都产生零个可提交结果。MEM-03 不记录 raw prompt 或模型原始输出。

## 6. 施工结果

- `MEM-03A`：新增只读 `EventLedger.get_event`，不暴露连接或允许改写事件。
- `MEM-03B`：实现完整输入窗口与已提交账本逐字段验真。
- `MEM-03C`：实现 quote 唯一定位、offset、UTF-8 SHA256 和歧义拒绝。
- `MEM-03D`：实现 UTC 时间规范化和 Runtime 独占字段物化。
- `MEM-03E`：覆盖成功、空结果、快照漂移、缺失事件、重复 quote、错误说话者和真实账本读取测试。
- `MEM-03F`：新增独立 profile、夹具、上游链式冻结与不可覆盖 manifest。

## 7. 验收标准

1. 账本不存在或与模型输入不一致的事件不能成为证据。
2. 最终 excerpt 可按 offset 从原文逐字恢复，hash 可重复计算。
3. 重复 quote 没有 start hint 时不能任意选取位置。
4. 玩家事实、角色自我事实和双方确认满足 MEM-01 的证据角色约束。
5. resolved 时间统一为 UTC Z；含糊时间不被猜成具体日期。
6. 所有表示初始只能是 proposed，模型不能越权激活或覆盖。
7. 空结果不读账本、不分配 ID、不制造占位记忆。
8. 上游 MEM-01/MEM-02 与本阶段 manifest 均可重复验证。

## 8. 后续衔接

下一阶段是 MEM-04：把 proposal run、物化结果、accept/reject、修正关系和失败重试写成追加事件，并提供幂等提交、崩溃恢复与从账本重建。MEM-04 才能改变持久状态；MEM-03 保持纯验证与物化边界。
