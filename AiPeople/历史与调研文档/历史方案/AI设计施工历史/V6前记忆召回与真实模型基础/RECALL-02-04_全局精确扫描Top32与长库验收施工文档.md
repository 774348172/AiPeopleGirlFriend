# RECALL-02～04：全局精确扫描、Top32 与长库验收施工文档

> 状态：工程实现已完成并冻结；真实 BGE `global_recall_at_32` 待本地模型工件  
> 上游：RECALL-01 查询表示合同  
> 合同：`eval/global_recall/recall02_04_contract_v1.json`

## 1. 连续施工范围

本施工段一次完成 RECALL-02、RECALL-03 和 RECALL-04 的工程闭环：读取 MEM-05 的 active generation 连续矩阵，对全部 selector views 做精确点积；按 `memory_id` 取最佳 view；稳定形成最多 32 个候选；从当前 MemoryRepresentation 装载人物、时间和认识状态元数据；最后执行目标规模性能、并发和冻结语义集验收。

本阶段不调用 reranker，不决定最终注入记忆，不使用人物名、时间词、关键词、current epoch 或固定窗口裁库，也不引入 ANN、FAISS、HNSW、Qdrant 或独立向量服务。

## 2. RECALL-02 全库精确扫描

输入必须是一个 MEM-05 `VectorMatrixSnapshot` 和一个 RECALL-01 `SelectorQueryEncoding`。矩阵保持 `float32` row-major 连续布局，通过 NumPy 执行一次 matrix-vector dot product；热路径不逐行查询 SQLite。

扫描前验证：

- view 数、row 数、dimension 和连续值数量一致；
- `(memory_id, view_ordinal)` 唯一；
- query encoder identity 与 generation 的 model/revision/artifact/dimension 完全一致；
- 空库必须是无 generation 的 `0x0` snapshot，或有 generation 的零行 snapshot。

扫描必须产生 finite score，并记录 pool memory/view、实际扫描 view、聚合 memory 和返回 candidate 数。`scanned_view_count` 必须严格等于 `pool_view_count`。调用方提供 active generation provider 时，扫描后再次核对 generation ID；投影在扫描中切换则整批失败并由上层重试，不能返回旧矩阵结果。

## 3. RECALL-03 memory 聚合与 Top32

一条记忆的多个 selector views 全部参与点积，记忆分数取最高 view score。同一记忆的 view 同分时取最小 ordinal；不同记忆同分时按 `memory_id` 升序，保证跨运行稳定。之后才截取 32 个唯一 `memory_id`。

Top32 是 reranker 候选数，不是最终选择数。空库返回零条，不足 32 条返回全部，任何负分候选仍可进入 Top32；下游 Qwen reranker 可以全部拒绝。

候选装载只在全局排序后按最多 32 个 ID 查询当前 memories。每条候选固定携带：

- memory ID/version、粗召回分数、memory kind 和 statement；
- subject type/entity/display name；
- temporal relation/source/start/end/timezone；
- epistemic polarity/modality；
- 获胜 selector view 及 source revision。

装载时重新验证 status 仍为 active/disputed、version 未变、canonical representation SHA256 与扫描 snapshot 一致。任何删除、修正、失效或 source 漂移都拒绝整批，避免把过期候选送入 reranker。

## 4. RECALL-04 长库工程性能

冻结基准模拟 10 万原始事件整理后的 1 万条当前有效记忆，每条 3 个 selector views，共 3 万行、512 维、61,440,000 bytes 连续矩阵。测试包含 3 次预热、30 次串行变化热回合，以及 4 worker 各 5 次只读并发扫描。

本机 Windows / Python 3.11 / NumPy 2.4.4 结果：

- 串行 exact scan + memory 聚合 + Top32：P50 `11.66ms`，P95 `21.08ms`；
- 4 路并发单请求：P50 `51.62ms`，P95 `92.41ms`；
- 粗扫描工程预算：串行 P95 `<100ms`、4 worker P95 `<150ms`，通过。

该数字只覆盖热矩阵点积、聚合和 Top32，不包含 BGE query encoding、SQLite projection rebuild、Qwen reranker 或最终装包。因此它证明全库 exact scan 在目标池规模无需 ANN，但不等于完整 selector 已达到 `<300ms`；完整门仍需真实 BGE 和 Qwen reranker 到达后测量。

## 5. 语义召回质量

`eval/global_recall/recall04_semantic_cases_v1.json` 冻结了共同经历、未来事件、修正事实、同名人物、角色自我经历和困难负例。`evaluate.py` 只接受每个 case 的唯一 Top32 memory IDs，拒绝池外 ID、重复 ID、缺 case 和超过 32 条，并计算 `global_recall_at_32`。

当前工作区没有真实本地 `BAAI/bge-small-zh-v1.5` 工件，因此语义分数保持 `pending_real_local_BGE_asset`。协议桩、关键词匹配和随机向量不能替代这个结果，也不会触发联网下载或训练。模型到达后必须记录 revision、artifact SHA256 和预测文件，再运行冻结 evaluator；是否保留原始 BGE 或需要外部微调只能由真实指标决定。

## 6. 完成与后续

RECALL-02 和 RECALL-03 工程合同完成。RECALL-04 的目标规模 exact scan 性能通过，真实 BGE 语义召回率因缺少工件保持显式待验收。下一个架构施工段是 SELECT-01～05，但其模型身份、量化和正式在线接入必须等待项目负责人交付 Qwen3-Reranker-0.6B 成品，不能由 Runtime 下载或训练替代。
