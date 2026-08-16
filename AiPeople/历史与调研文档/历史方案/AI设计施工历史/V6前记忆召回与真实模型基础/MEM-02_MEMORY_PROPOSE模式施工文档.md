# MEM-02：MEMORY_PROPOSE 模式施工文档

> 状态：已完成（MEM-02A 至 MEM-02F 已冻结；下一检查点 MEM-03）  
> 版本：v1.0  
> 日期：2026-08-07  
> 上游合同：`eval/memory_contract/mem01_contract_v1.json`  
> 当前总纲：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`

## 1. 目标

MEM-02 冻结同一 Qwen3.5-4B 在后台执行 `MEMORY_PROPOSE` 时能够看见什么、只能输出什么、何时合法返回空结果，以及取消、超时、非法输出时系统必须如何失败。

本阶段只建立模式合同，不接真实模型，不访问 SQLite，不提交或物化记忆，不调度 GPU，也不生成向量。模型是否成功提出语义候选与候选是否能够进入语义投影是两件事；后者仍由 MEM-03/MEM-04 负责。

## 2. 唯一流程位置

```text
已完整提交的 UserMessage / CharacterMessage / 合法 app 或 offscreen 事件
  -> MEM-06 后台调度取得主模型槽位
  -> MEMORY_PROPOSE 低温结构化生成
  -> MEM-02 严格解析和窗口边界校验
  -> MemoryProposalDraft[] 或合法空结果
  -> MEM-03 查询证据账本并物化证据
  -> MEM-04 追加式提交
```

`MEMORY_PROPOSE` 不在玩家可见回复关键路径。它不是秦未晞的角色回复，也不允许输出任何角色正文。

## 3. 输入合同

`MemoryProposeRequest` 只包含：

- 固定 `schema_version=1` 和 `mode=MEMORY_PROPOSE`；
- Runtime 分配的 `proposal_run_id` 和 `conversation_id`；
- 本次覆盖的连续、有序事件范围；
- 1 至 32 条已提交事件，每条显式携带相同 `conversation_id`、角色、事件类型、UTC 时间、IANA timezone 和原文；
- 0 至 32 条同 conversation 的 `active/disputed` 已有记忆短上下文，只用于建议修正或冲突关系；
- 固定 `max_proposals=8`。

输入不允许出现取消生成、失败回合、模型草稿、内部推理、向量分数、未提交文本或其他 conversation 的事件。事件窗口如何从账本读取属于 MEM-03/MEM-06，本阶段不访问数据库。

## 4. 输出与空结果

输出根对象固定为：

```json
{
  "schema_version": 1,
  "mode": "MEMORY_PROPOSE",
  "proposal_run_id": "与请求一致",
  "proposals": []
}
```

`proposals` 可以包含 0 至 8 个 MEM-01 冻结的 `MemoryProposalDraft`。空数组是完整成功，不是错误、低置信占位或重试信号。普通寒暄、喝水等即时动作、没有跨回合意义的内容应优先返回空数组。

输出不能包含：

- `memory_id`、`status`、`created_at`、最终 offset/hash 等 Runtime 字段；
- 秦未晞对玩家说的话、解释文本、Markdown 或代码围栏；
- 窗口外 event ID、与事件原文不连续一致的 quote；
- `existing_memories` 以外的 supersedes/contradicts/refines 目标；
- 具体提醒、通知、自动发送任务或对含糊时间的猜测。

任一 proposal 非法时整批失败，不部分接受，避免同一次模型输出形成不可重放的半成功状态。

## 5. 模型模式 profile

首版 profile 冻结为：

- 复用常驻 Qwen3.5-4B 主模型，不增加第三个生成模型；
- `temperature=0.1`、`top_p=0.8`、最大输出 2048 tokens；
- 禁用 thinking 和 streaming；
- 使用 `memory_propose_result_v1.schema.json` 作为 JSON Schema grammar；
- 只在角色回复完整提交后后台运行；
- 玩家新回合可以抢占，绝不阻塞可见回复。

采样值是首版工程合同，不代表质量已经由真实模型验收。真实模型接入与功能评测仍需后续阶段执行。

## 6. 语义权限

AI 负责：

- 判断输入中是否有跨回合语义价值；
- 提出 kind、statement、认识状态、时间状态、逐字 quote、理由、置信度和关系建议；
- 在没有值得保留内容时返回空数组；
- 保留否定、不确定、假设、玩笑、引用、纠正和冲突。

确定性系统只负责：

- 校验 JSON/schema、数量、枚举和字段所有权；
- 校验 run ID、conversation、事件窗口、quote 连续匹配和关系目标边界；
- 将失败分类并阻止非法候选继续流转。

确定性系统不得用关键词替模型补做“值不值得记住”的判断，失败后也不得启用规则式记忆提取兜底。

## 7. 失败合同

稳定错误码：

- `memory_propose_cancelled`
- `memory_propose_timeout`
- `memory_propose_model_unavailable`
- `memory_propose_output_too_large`
- `memory_propose_invalid_json`
- `memory_propose_schema_invalid`
- `memory_propose_contract_invalid`
- `memory_propose_evidence_outside_input`
- `memory_propose_relation_outside_input`

只有超时和模型不可用可按后续调度策略重试。玩家抢占导致的取消不自动在前台重试。任何失败都产生零个可提交候选，不回滚已经提交的对话，不暴露原始 prompt、模型输出或聊天全文，不产生角色消息。

## 8. 施工结果

### MEM-02A：输入合同

- 新增 `MemoryProposeEvent`、`ExistingMemoryContext` 和 `MemoryProposeRequest` frozen/slots 类型。
- 冻结事件、已有记忆、数量、sequence range、conversation 和 committed 边界。
- 新增 `memory_propose_request_v1.schema.json`。

### MEM-02B：输出与空结果

- 新增 `MemoryProposeResult`，输出只允许 MEM-01 `MemoryProposalDraft` 数组。
- 空 proposals 是一等成功结果；重复 proposal 整批拒绝。
- 新增 `memory_propose_result_v1.schema.json`，其 proposal 定义由 MEM-01 schema 机械生成。

### MEM-02C：prompt 与 profile

- 新增固定后台 system prompt 和 canonical JSON 输入编排。
- 新增 `mem02_mode_profile_v1.json`，冻结采样、grammar、后台、抢占和共享模型槽位语义。

### MEM-02D：失败边界

- 新增严格 JSON 解析、32 KiB 输出上限、run ID、quote、anchor 和 relation target 校验。
- 新增 `MemoryProposeFailure` 和精确 retryable 策略；错误对象不保存 raw output 或 prompt。

### MEM-02E：夹具与自动测试

- 正例覆盖重要未来事项、普通内容空结果和对已有记忆的纠正。
- 反例覆盖角色正文、Markdown、半截 JSON、重复字段、错误 run ID、窗口外证据、quote 不匹配、窗口外关系和模型越权字段。
- schema、不可变 Python 合同、prompt、profile、空结果和失败码均有自动测试。

### MEM-02F：独立冻结

- 新增 `eval/memory_propose/freeze.py` 和 `mem02_contract_v1.json`。
- 冻结器先验证上游 MEM-01，再校验自身 manifest 自哈希、资产顺序、字节数和 SHA256。
- `--freeze` 拒绝覆盖既有合同；任何输入/output schema、profile、prompt、夹具或权威引用漂移都会失败。

## 9. 验收标准

1. 输入事件全部已提交、有序、唯一并属于同一 conversation。
2. 输出只能是 grammar 约束 JSON，不能混入角色正文或内部结构泄漏。
3. 零候选合法，普通内容不强迫制造记忆。
4. quote、时间 anchor 和关系目标只能引用本次输入允许的 ID。
5. 模型不能设置 Runtime 拥有的身份、状态、时间和证据定位字段。
6. 任一 proposal 非法时整批失败且不部分提交。
7. 取消、超时、模型不可用和非法输出有稳定错误码，不阻塞或污染玩家回复。
8. 没有规则式语义兜底，没有真实模型调用、数据库 migration、训练或向量施工。
9. 上游 MEM-01 和本阶段冻结 manifest 均可重复 `--verify`。

## 10. 后续衔接

下一阶段是 MEM-03：从追加式事件账本读取并验证模型引用的事件，定位最终 excerpt offset/hash，规范化可解析时间，并物化 `MemoryRepresentation`。MEM-03 仍不能直接把候选设为 active；追加式 accept/reject 和重试提交属于 MEM-04。
