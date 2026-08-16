# RelationshipRuntime 全局记忆选择器训练与模型决策方案 V2

> 状态：现行记忆选择子系统设计基线，尚未施工  
> 版本：V2.1  
> 日期：2026-08-07  
> 适用阶段：P0 AI 文字聊天核心  
> 上位依据：`需求文档/项目框架需求.md`、`设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md`

## 1. 定位

本方案细化唯一 AI 实现中的“语义投影 + 多线索索引 + 工作激活区”，不建立第二套记忆系统。追加式事件账本仍是唯一事实来源，语义记忆和向量均为可由账本重建的派生投影。

本方案只解决两个问题：

1. 每轮玩家消息如何让全部当前有效记忆参与选择，同时不把全部原文塞入 Qwen3.5-4B。
2. 如何在 `P95 < 300ms` 的记忆处理预算内，用 AI 判断哪些记忆应成为本轮回复的背景。

“激活记忆”表示该记忆有助于秦未晞理解并自然回应当前对话，不表示回复必须直接提起、复述或执行这条记忆。

## 2. 核心决定

1. 每轮对全部当前有效记忆表示执行全局精确向量评分，不使用固定短目录、current epoch、工作激活区或时间窗口替代全局选择。
2. 全局评分后按记忆去重，形成固定 `Top32` 候选，再由一个关系记忆重排器批量评分。
3. 正式重排器固定为经过关系记忆数据微调的 `Qwen3-Reranker-0.6B`，在 GPU 上常驻运行，不再保留学生 CrossEncoder 在线型号分支。
4. Qwen3-Reranker 必须使用同一全局粗召回、固定 Top32、冻结输入合同和目标机基准完成训练与部署验收。
5. 记忆选择总延迟 P95 必须低于 300ms，P0 主模型、重排器和运行时在 `nvidia-smi` 中的 GPU 总峰值必须低于 5120 MiB；不满足时优化 Qwen 重排器精度、量化、批量、输入长度和进程部署方式，不能静默换成另一正式模型。
6. 正式客户端只做推理。玩家产生的新记忆只增加账本、投影和向量，不触发在线训练或修改模型权重。
7. Qwen3.5-4B 不生成 `DIRECT/FETCH` 控制头。记忆选择在正式回复前完成，Qwen3.5-4B 每轮只生成一次可见角色回复。

对上层仍只暴露：

```text
RelationshipRuntime.handleTurn(event) -> stream<ReplyEvent>
```

向量、候选 ID、重排分数、教师软标签和装包过程不得显示给玩家或由前端直接修改。

## 3. 在线总体流程

```text
玩家消息
  -> 提交 UserMessage 事件
  -> 更新确定性状态与 WorkingActivation
  -> 构造 SelectorQuery
  -> 查询编码器生成 query embedding
  -> 对全部有效 MemoryRepresentation 做精确向量评分
  -> 按 memory_id 聚合并取得 GlobalRecallTop32
  -> Qwen3-Reranker-0.6B GPU 批量评分
  -> 模型阈值允许零结果
  -> CPU 只做状态、证据、ID、去重与 token 预算校验
  -> 装配 SelectedMemoryFrame
  -> Qwen3.5-4B 单次 REPLY 流式生成秦未晞正文
  -> 提交 CharacterMessage
  -> GPU 空闲时运行 MEMORY_PROPOSE 与新记忆向量化
```

`Top32` 是进入精排的候选数量，不是最终注入条数。最终按模型激活分数、有效性和 token 预算选择零条或多条完整记忆。

## 4. 全局记忆空间

### 4.1 全局范围

每轮必须包含全部 `active` 且允许激活的语义记忆表示：

- 稳定事实和偏好。
- 人物关系和关系变化。
- 共同经历及过程。
- 情绪意义和秦未晞形成的个人立场。
- 修正、否定、冲突和 supersedes 链。
- 未完话题和后续生活事件。
- 双方承诺、共同计划和有跨回合意义的未来经历。
- 能追溯到证据的其他语义记忆类型。

原始 `events` 账本全部保留，但不是每条原始消息都自动成为独立语义记忆。未被语义投影覆盖的原文仍可通过重建、FTS 和证据恢复找到。

### 4.2 检索表示合同

第二步不要求记忆是固定“卡片”。每条记忆只需提供统一的检索合同：

```text
MemoryRepresentation
- memory_id
- selector_views[]
- embeddings[]
- full_payload_ref
- evidence_event_ids[]
- status
- supersedes_memory_id
- source_revision
```

`selector_views` 可以分别表达事件、关系意义、情绪意义、未来触发语义或必要证据片段。完整内容仍由 `full_payload_ref` 指向语义投影和原始证据。

### 4.3 全局精确扫描

首版将归一化向量常驻连续内存，每轮执行矩阵点积并取得 Top32，不引入 Qdrant、FAISS、HNSW 或独立向量服务。SQLite 保存元数据、版本和重建关系，不承担高频向量逐行计算。

如果一条记忆有多个向量视图，先保留该记忆所有视图中的最高学习式相似度，再按 `memory_id` 取得唯一候选。CPU 不根据主题、时间或关系权重重新解释语义。

未来只有全局池规模实测无法满足 NFR-13 时才评估 ANN。替换后仍必须证明全部有效记忆保持可选资格，并在冻结测试集上达到不低于精确扫描的约定召回下限。

## 5. SelectorQuery 与精排输入

`SelectorQuery` 只包含在线判断所必需的内容：

```text
SelectorQuery
- current_user_message
- recent_dialogue
- compact_working_state
- current_time_context
- schema_version
```

初始实验限制：

```text
recent_dialogue_turns = 2..4
reranker_candidates = 32
maximum_pair_tokens = 128
```

最终值必须通过准确率与 P95 联合冻结。不得为了提高离线分数无限增加最近对话或候选记忆文本。

候选文档由 `selector_views`、最小状态和必要证据组成。模型不能看到控制指令、数据库查询文本或玩家不可见的系统秘密。

## 6. 训练任务定义

### 6.1 目标标签

训练任务采用二分类激活语义：

```text
ACTIVATE = 这条记忆有助于理解并自然回应当前对话
IGNORE   = 不需要进入本轮工作记忆
```

`ACTIVATE` 不等于“必须在回复中说出来”。最终 Qwen3.5-4B 可以使用记忆形成理解，但保持沉默、换一种表达或不直接复述。

### 6.2 基础训练记录

```json
{
  "sample_id": "ms-000001",
  "scenario_id": "interview-finish-01",
  "selector_query": {
    "recent_dialogue": "秦未晞：紧张什么，正常发挥就行。",
    "current_user_message": "终于结束了，累死我了。",
    "working_state": "玩家今天有一场重要面试。"
  },
  "candidate": {
    "memory_id": "M103",
    "selector_text": "玩家今天参加了准备很久的面试，此前多次表达紧张。",
    "evidence_refs": ["E9001", "E9014"]
  },
  "label": 1,
  "label_source": "human_verified",
  "split_group": "interview-finish-01"
}
```

训练文件可以扁平化为 query-document pair，但拆分必须以 `scenario_id`、人物事件和证据链为组，禁止同一事件的改写跨入训练集和测试集。

## 7. 数据来源与覆盖

### 7.1 首版来源

1. 人工定义的关系记忆判断规范和高风险场景。
2. 数据生成器生成的历史记忆、最近对话、当前消息和候选集合。
3. 从全局向量粗召回结果中挖掘的困难负样本。
4. 强模型辅助复核与软分，但硬标签进入冻结集前必须通过人工审核。

现有“玩家消息 -> 秦未晞回复”SFT 数据只能作为场景原材料，不能直接当作重排训练数据。它缺少候选记忆和 `ACTIVATE/IGNORE` 标签。

真实玩家聊天不是首版训练前提。以后只有在玩家明确同意、本地脱敏并完成重新标注时，才能作为离线改进数据；正式客户端不得自动上传或训练私人对话。

### 7.2 必须覆盖的正样本

- 明确询问过去。
- “那个、她、终于结束了”等含糊指代。
- 当前情绪与过去经历存在真实关系。
- 旧经历能解释当前立场、担忧或反应。
- 多条记忆共同构成理解背景。
- 记忆应进入背景但不宜直接复述。

### 7.3 必须覆盖的困难负样本

- 同主题但不同事件。
- 同一人物但时间、关系或事件不符。
- 已被修正或 superseded 的旧事实。
- 语义相关但此刻提起突兀。
- 为证明“记得”而机械翻旧账。
- 玩家明确不想再谈的内容。
- 无证据的共同往事和模型推断。
- 普通闲聊、喝水、打招呼等不需要旧记忆的回合。
- 所有候选都应为 `IGNORE` 的无记忆回合。

每个正样本应配套多个由粗召回器实际命中的困难负样本。不能只使用随机无关文本，否则模型无法学会区分相似经历。

## 8. 全局粗召回训练

粗召回器的任务是保证正确记忆进入 Top32，不负责最终自然性判断。首个基线使用 `BAAI/bge-small-zh-v1.5`，记忆向量在后台预计算，在线每轮只编码一次 `SelectorQuery`。

使用与重排器相同的场景组训练或微调：

- `ACTIVATE` 记忆作为正对。
- 同一全局池中向量相似但标签为 `IGNORE` 的记忆作为 hard negatives。
- 训练目标使用对比学习，具体损失和采样比例在训练配置中冻结。
- 评测以 `global_recall_at_32` 为主要指标，不能用平均相似度代替。

如果未微调的 BGE 已达到冻结召回要求，可以保留原权重，避免无证据增加训练步骤。

## 9. Qwen3-Reranker-0.6B 微调

### 9.1 模型角色

Qwen 模型承担唯一正式在线精排职责：在 RTX 3060/3070 GPU 上批量读取 Top32 query-memory pairs，并输出每个候选的激活分数。

它不是回复模型，不生成解释或角色正文。训练和推理只使用 `yes/no` logits。

### 9.2 固定指令

```text
Given a current intimate conversation and a past memory, judge whether the
memory should be activated as background for Qin Weixi's natural reply.
Reject memories that are merely topically related but unnecessary.
Activation does not mean the memory must be explicitly mentioned.
```

中文对话和记忆保持原文，固定任务指令作为版本化训练合同，不在每次实验中随意改写。

### 9.3 目标函数

对每个 query-memory pair 计算：

```text
activation_logit = logit("yes") - logit("no")
hard_probability = sigmoid(activation_logit)
loss_hard = BCEWithLogits(activation_logit, hard_label)
```

首轮先用 LoRA 或 QLoRA 验证数据和任务合同。只有固定评测证明适配容量不足时才比较全参数微调；不能同时改变数据、损失、LoRA 配置和候选输入。

## 10. 精度、量化与部署型号冻结

正式模型身份固定为 `Qwen3-Reranker-0.6B`，但其 BF16、FP16、8bit 或 4bit 部署精度必须在同一验证集和目标机上比较。选择顺序为：先满足自然想起质量与无记忆拒绝要求，再在合格配置中选择 P95 更低、显存更小且一小时稳定的工件。

量化不能改变训练数据、固定指令、Top32 输入合同或阈值定义。每个候选工件必须记录基础模型 revision、Adapter、合并或量化工具、量化类型、文件哈希和运行时版本。若所有配置均无法同时满足 `<300ms` 与 P0 `<5GB`，本阶段判定阻塞并返回模型或输入优化，不引入未批准的替代架构。

## 11. 数据集边界

至少维护以下互斥集合：

```text
train_hard              人工或规则校验后的硬标签训练集
validation              阈值、温度、Top32和量化选择
test_frozen             最终模型验收，训练过程不可查看标签分布细节
long_memory_e2e         长对话、跨重启和十万事件端到端集
```

禁止：

- 同一事件或其改写跨集合。
- 用测试集调整阈值、prompt 或温度。
- 用最终秦未晞回复质量替代选择器标签正确性。

## 12. 在线推理与模型冻结

模型版本通过闸门后冻结并随产品发布。正式运行时：

- 玩家新消息写入账本。
- 后台 AI 可以形成新的语义记忆候选。
- 新记忆只计算 embedding 并加入全局矩阵。
- 每轮执行全局向量扫描和冻结重排器推理。
- 不计算梯度，不修改 embedding 或 reranker 权重。
- 不上传玩家私人聊天。

只有固定评测发现系统性缺陷、输入合同变化或新模型版本立项时，才在开发侧 GPU 机器离线训练下一版本。

## 13. 指标与基准

### 13.1 选择质量

必须至少报告：

| 指标 | 含义 |
|---|---|
| `global_recall_at_32` | 每轮全局扫描后，正确记忆是否进入 Top32 |
| `natural_recall_accuracy` | 精排器对自然激活与不激活判断的正确率 |
| `no_memory_false_activation_rate` | 无记忆回合中错误激活任意记忆的比例 |
| `hard_negative_rejection_rate` | 对相似人物、主题、错误事件和旧事实的拒绝能力 |
| `selected_memory_precision` | 最终通过阈值的记忆中正确项比例 |

阈值必须在 validation 上校准，并允许返回零条记忆。不得固定每轮必须选一条或固定选三到五条。

### 13.2 延迟与资源

在 RTX 3060 8GB 和 RTX 3070 8GB 分别测量：

```text
query_embedding_ms
global_scan_ms
top32_hydration_ms
reranker_queue_ms
reranker_inference_ms
validation_and_pack_ms
selector_total_ms
reply_first_token_ms
GPU_VRAM_peak
CPU_RAM_peak
```

基准必须常驻、预热、断网、使用变化输入，至少报告 P50/P95。加载和下载不计入热回合，但必须单独报告启动时间。

硬门槛：

- `selector_total_ms` P95 `< 300ms`。
- Qwen3.5-4B 最终回复仍满足首字 P95 `< 2s`。
- 无持续显存增长，连续运行一小时稳定。
- P0 主模型、Qwen3-Reranker 和运行时 GPU 总峰值 `< 5120 MiB`，且统计必须包含批量 Top32 精排、CUDA context 和框架分配器峰值。

## 14. 正式模型验收闸门

Qwen3-Reranker-0.6B 只有同时满足以下条件，其具体部署工件才可冻结：

1. `selector_total_ms` P95 `< 300ms`。
2. `natural_recall_accuracy`、`no_memory_false_activation_rate` 和 `hard_negative_rejection_rate` 达到冻结集预注册阈值。
3. 相对未微调 Qwen3-Reranker 基线的成对 bootstrap 95% 置信区间下界大于 0，证明关系数据训练确有收益。
4. P0 GPU 总峰值 `< 5120 MiB`。
5. 满足稳定性、Windows 断网部署和一小时运行要求。

任一条件不满足，本阶段保持未通过，不得以切换 CrossEncoder 或降低固定测试标准规避阻塞。

## 15. 失败与降级

| 失败 | 必须行为 |
|---|---|
| 全局向量投影不可用 | 使用当前 epoch 与工作激活区降级回复，排队重建；记录本轮未满足全局选择 |
| 粗召回模型失败 | 不伪装成完整全局检索；降级并记录错误 |
| 重排器超时或崩溃 | 返回零条长期记忆，使用当前对话回复；不得随机注入候选 |
| 重排输出非法 | 拒绝非法分数或 ID，不执行任意读取 |
| 选中记忆已失效或无证据 | CPU 排除并记录投影一致性错误，不补选未评分内容 |
| 详细记忆超 token 预算 | 按模型分数从低到高删除完整条目，不截断证据形成错误语义 |
| 后台 MEMORY_PROPOSE 失败 | 保留原始账本，稍后重试或重建 |

降级只用于故障回合，不能作为普通路径绕过 NFR-14。

## 16. 施工切分

本文件只冻结方案，不在本次修改中训练或接入模型。后续施工建议：

1. `V2-01`：MemoryRepresentation、embedding 版本和全局矩阵重建合同。
2. `V2-02`：SelectorQuery、全局精确扫描 Top32、日志和 FakeSelector。
3. `V2-03`：关系记忆硬标签 schema、生成、困难负样本挖掘和冻结拆分。
4. `V2-04`：BGE 粗召回基线、微调判定和 `global_recall_at_32` 验收。
5. `V2-05`：Qwen3-Reranker-0.6B LoRA/QLoRA、GPU 批量推理和质量验收。
6. `V2-06`：BF16/FP16/8bit/4bit 工件、目标机 P50/P95、显存和稳定性比较。
7. `V2-07`：Qwen3-Reranker-0.6B 正式部署工件与验收闸门冻结。
8. `V2-08`：正式重排器接入，Qwen3.5-4B 单次 REPLY，长程端到端验收。

## 17. 明确不采用

- 不让 Qwen3-Reranker 逐条读取全部完整记忆原文。
- 不保留 CrossEncoder 作为正式在线重排器或自动降级型号。
- 不用 current epoch、工作区、FTS 或固定短目录替代全局记忆空间。
- 不让 Qwen3.5-4B 生成记忆 ID、`DIRECT/FETCH` 或第二段取回请求。
- 不固定每轮必须选择记忆。
- 不把普通搜索相关性直接当作自然关系记忆判断。
- 不在玩家设备上持续训练或把私人聊天写入模型权重。
- 不在首版引入图数据库、向量服务器或多轮生成式检索代理。

## 18. 被替代边界

本方案替代《RelationshipRuntime 语义记忆与模型主动取回方案 V1》中的短索引目录、`DIRECT/FETCH`、第二段生成和相关训练协议。V1 文档移入 `历史与调研文档/历史方案/`，仅用于追溯，不再参与现行施工。

追加式证据账本、工作激活区、语义投影、确定性前瞻状态、证据校验、投影重建和 token 装包原则继续有效。
