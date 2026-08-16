# MEM-05：selector view、本地 BGE 向量、增量失效与全量重建施工文档

> 状态：已完成（MEM-05A 至 MEM-05F 已冻结；下一检查点 MEM-06）  
> 上游：MEM-04 追加式记忆提交合同  
> 合同：`eval/memory_vectors/mem05_contract_v1.json`

## 1. 目标与边界

MEM-05 从 MEM-04 的当前 `memories` 投影读取 `active/disputed` MemoryRepresentation，为每条当前有效记忆生成一个或多个确定性 selector view，并用本地 CPU `BAAI/bge-small-zh-v1.5` 生成 L2 归一化向量。

selector view、向量和 generation 都是可删除、可重建的派生投影。完整语义仍在 MemoryRepresentation，证据事实仍在追加式事件账本。投影失败、损坏或过期不能改变记忆事实，也不能被当作“没有这段记忆”。本阶段不做相似度扫描、Top32、reranker、在线训练或后台调度。

## 2. 固定合同

- 索引状态仅为 `active`、`disputed`；`proposed`、`superseded`、`rejected` 不进入有效矩阵。
- view 顺序固定为 statement、可用 temporal、逐条 evidence；文本只做确定性的 Unicode 空白折叠。
- 每个 view 保存 `memory_id`、memory version、ordinal、kind、文本哈希和完整 MemoryRepresentation 的 canonical SHA256。
- 编码器模型 ID 固定为 `BAAI/bge-small-zh-v1.5`，CPU、512 维、mean pooling、最大序列 512；运行时再次执行 L2 归一化并落为 little-endian float32。
- 模型只允许从显式本地目录加载，revision 必填，目录全部文件的稳定 SHA256 必须进入 generation；运行时不静默下载模型。
- SQLite 保存 generation、view、向量 BLOB、版本与哈希；在线热快照导出为连续 row-major `array('f')`。本阶段不引入 FAISS、HNSW、Qdrant 或远程向量库。

## 3. selector view

`build_selector_views()` 是纯函数。非索引状态直接返回空集合；有效记忆至少产生 statement view，随后按固定顺序加入非空 temporal source text 和 evidence excerpt。同一 memory version 的相同输入产生字节一致的文本、ordinal 和哈希。

调用方通过 `MemoryVectorStore.from_memory_store()` 从现有 MEM-04 `MemoryStore` 建立投影入口，不需要访问账本或 SQLite 的私有连接。

完整 payload 不复制进 view。后续候选恢复仍使用 `memory_id` 回到 MemoryRepresentation 和证据账本，避免把面向粗召回的短表示升级为事实源。

## 4. 本地 BGE 适配器

`LocalBgeEncoder` 在构造时验证本地 sentence-transformers 资产：唯一 pooling 模块必须为 mean pooling、embedding dimension 必须为 512、`max_seq_length` 必须为 512。默认后端使用 `local_files_only=True`、`trust_remote_code=False`、`device=cpu`。

编码器通过 `EmbeddingEncoder` 协议注入，因此合同测试使用确定性 FakeEncoder，不下载权重。无穷值、NaN、零范数、行数错误和维度错误均显式失败，禁止写入投影。

## 5. 增量失效与全量重建

增量同步比较 current MemoryRepresentation 的 version 与 canonical SHA256：

1. 新增或版本变化的有效记忆重新生成全部 view/向量；
2. 状态变为非索引状态的记忆删除当前 generation 中的派生行；
3. 编码器身份或 view profile 漂移时，不混用旧向量，直接全量重建；
4. 即使尚未执行同步，矩阵快照也会 join 当前 `memories` 的状态和版本，过期行不会被读取。

全量重建先编码当前有效记忆，再在单个 `BEGIN IMMEDIATE` 事务中写入完整新 generation、切换唯一 active 指针并清理旧代。编码失败、源版本变化或任一故障注入点抛错时事务回滚，原 active generation 继续可用。

## 6. 完成项

- `MEM-05A`：迁移 006，建立 generation、selector view、embedding 和 active state 表。
- `MEM-05B`：确定性多 view 构建、文本哈希和 source revision。
- `MEM-05C`：本地 BGE 资产验真、CPU-only 加载和运行时 L2 归一化。
- `MEM-05D`：版本/状态/profile 漂移的增量替换与失效。
- `MEM-05E`：原子全量重建、旧代保活和连续矩阵快照。
- `MEM-05F`：覆盖空库、active/disputed、损坏 BLOB、维度/NaN、无下载、profile 漂移和三处崩溃注入，并冻结链式合同。

## 7. 验收

专项测试必须覆盖：view 确定性、状态过滤、向量有限且归一化、增量新增/版本更新/状态失效、编码器漂移、全量与增量结果一致、崩溃保留旧代、损坏哈希拒绝和本地资产边界。MEM-01 至 MEM-05 manifest 必须可链式复验。

下一阶段 MEM-06 只负责回复提交后的后台调度、取消和性能隔离；全库 exact dot-product、按 memory_id 取最大 view 分和 Top32 属于后续 RECALL-01/02，不提前塞入 MEM-05。
