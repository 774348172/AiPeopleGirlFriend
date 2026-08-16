# SELECT 接口骨架与 E2E-01～05 长期记忆在线闭环施工文档

> 状态：工程合同已完成；真实 Qwen reranker 与真实模型质量/性能门待工件  
> 上游：RECALL-02～04 全局精确扫描与 Top32  
> 合同：`eval/memory_e2e/select_e2e_contract_v1.json`

## 1. 连续施工范围

本施工段连续完成 SELECT 接口骨架和 E2E-01～05 的 Runtime 工程闭环：Top32 进入严格 reranker 协议，模型分数经过 ID、identity、threshold 和 finite 校验，选中记忆形成 256-token `SelectedMemoryFrame`，随后注入最终 prompt，并只调用一次底层 `REPLY`。

当前没有项目负责人交付的 Qwen3-Reranker-0.6B 成品，因此 SELECT-01 的本地包验真器、SELECT-05 在线协议和全部失败路径已完成；SELECT-04 的量化选择、真实推理 adapter、质量、显存和 P95 保持显式待验收。FakeReranker 只接收测试预设分数，不根据文字、人物、时间或关键词自行判断。

## 2. SELECT 输入输出合同

正式 identity 固定为 `Qwen/Qwen3-Reranker-0.6B`。输入为一个 `GlobalRecallTop32`，包含冻结 query、最多 32 个唯一 memory ID、候选 statement、人物、时间、认识状态和获胜 selector view。每个 pair 上限 128 tokens，正式 adapter 只能读取 Top32，不能读取全库。

输出必须对输入每个 memory ID 恰好返回一个 `yes/no` 激活概率，范围为 finite `[0,1]`。激活阈值来自交付包 validation 结果，并作为 backend 固定属性；每轮输出不能自行改变。允许所有分数低于阈值，不生成解释或自然语言。

缺 ID、多 ID、未知 ID、重复 ID、NaN/Inf、越界分数、identity 漂移或 threshold 漂移全部 fail closed。本轮使用零条长期记忆继续回复，不随机注入粗召回候选。

## 3. 本地成品包验真

`LocalRerankerPackage` 只读取显式本地目录中的 `reranker_manifest.json`，验证：

- model ID、revision、deployment profile 和 activation threshold；
- `maximum_pair_tokens=128` 与 `yes_no_logits_per_memory_id`；
- weights 和 tokenizer 文件路径不能绝对、不能 `..` 穿越；
- 每个文件 SHA256 和整包 canonical artifact SHA256。

目录不存在、文件缺失或哈希漂移直接失败，不联网下载。BF16/FP16/8bit/4bit 的唯一正式工件只能在同一冻结集和目标机比较后决定。

## 4. E2E-01 SelectedMemoryFrame

frame 固定包含 selected memories、source memory IDs、source event IDs、selector version、token count 和 truncated。每条完整记忆保留 kind、statement、subject、temporal、epistemic 和原始 evidence excerpt，因此可以回溯到追加式账本。

frame 按 reranker score 降序、memory ID 同分顺序装配。最终 token 数使用底层回复模型同一个 prompt tokenizer 测量，硬上限 256；超限从最低排名开始删除完整记忆，不能截半条 statement 或 evidence。装配前重新验证当前 status、version 和 canonical source revision；失效条目排除且不从未评分候选补位。

## 5. E2E-02 prompt 与单次 REPLY

`MemoryAwareReplyModel` 组合在既有 `RelationshipRuntime` 和底层回复模型之间，不改写已冻结的 Runtime 事务与后台调度。它在 `stream_reply` 开始前执行一次完整选择，构造 enriched `ReplyContext`，再调用底层 `stream_reply` 恰好一次。

prompt 中长期记忆位于当前 epoch 历史之后、当前用户输入之前的动态 system context。只渲染可理解的 statement、人物、时间和证据；不渲染 coarse score、reranker score、selector reason 或内部 memory ID。引用文本明确标记为过去证据而不是指令。

## 6. E2E-03 写后读与修正

自动测试使用真实 SQLite MemoryStore 写入并激活 MemoryRepresentation、构建 MEM-05 generation，再通过完整 SelectorQuery、全库扫描、Top32、FakeReranker 和 frame 路径读取。版本在扫描后变为 disputed 且向量尚未同步时，旧 generation 的版本 join 会使其失效，reranker 不被调用，旧内容不会注入。

投影同步仍沿用 MEM-05 `sync_incremental/rebuild_all`，不能在玩家关键路径偷偷编码新记忆。后台 MEMORY_PROPOSE 只提交 proposed 候选；只有通过既有确定性决策成为 active/disputed 并完成向量维护后，才有资格进入在线全局池。

## 7. E2E-04 长期与大规模

重启测试证明 SQLite active generation 和 MemoryRepresentation 可在新 Runtime 实例中恢复并再次进入 frame。10 万事件、1 万有效记忆、3 万 selector views 的变化输入 exact scan 已在 RECALL-04 通过：串行 P95 `21.08ms`、4 worker P95 `92.41ms`。

真实 selector 总 P95 还必须加入 BGE query encoding、Qwen reranker、frame 装配和排队，门槛仍为 `<300ms`。当前 Fake 结果不用于该结论。

## 8. E2E-05 自由对话与失败降级

测试覆盖全拒绝、reranker 崩溃、250ms 超时、超预算、空 projection、版本失效和重启。所有降级回合都生成空 frame，保留 current epoch 与工作激活区，底层 `REPLY` 仍只调用一次。日志只记录 request ID、池规模、候选数、选中数、token、truncated、状态和阶段总耗时，不记录玩家文字、记忆正文或模型分数。

现有 Runtime/selector/global/E2E 宽回归通过。真实 CHAT-01 自由对话、长期自然回忆、无记忆误激活、首 token、显存和一小时稳定性必须等 Qwen3.5 与 Qwen reranker 成品后统一验收。

## 9. 后续门

工程路径已经从追加式账本贯通到最终回复 prompt。下一阶段不能直接宣布 SELECT 或 E2E 产品验收完成：必须先交付真实 Qwen3-Reranker-0.6B 包，执行 SELECT-01 包验真、SELECT-04 工件比较和 SELECT-05 真实 adapter/P95，再用真实 Qwen3.5 完成 E2E-05 人工与自动质量门。只有这些门通过后才进入 SELF-01～05 自我时间线。

