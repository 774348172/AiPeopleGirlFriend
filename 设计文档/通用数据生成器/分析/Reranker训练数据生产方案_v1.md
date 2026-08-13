# 记忆选择器（Reranker）训练数据生产方案（研究文档）

> 状态：**研究文档，未施工** | 日期：2026-08-09
> 目的：把 P1 缺口（Reranker 训练数据生产）细化为可评审方案，供项目负责人拍板后再施工
> 关联：AI 程序侧《RelationshipRuntime_全局记忆选择器训练与模型决策方案_V2.md》（现行权威）、
> `eval/training_contract/freeze02_contract_v2.json`（freeze02 合同）、
> `eval/training_contract/schemas/reranker_record.schema.json`（已冻结记录 schema）

---

## 一、背景与依据

### 1.1 程序侧合同（V2 文档 §6-§11）

- 训练任务：`Qwen3-Reranker-0.6B` 二分类 `ACTIVATE` / `IGNORE`（激活 = 有助于理解并自然回应当前对话；激活 ≠ 必须在回复中说出来）。
- 固定指令（§9.2）已冻结，不随实验改写。
- 目标函数（§9.3）：`activation_logit = logit("yes") - logit("no")` + BCEWithLogits；首轮 LoRA/QLoRA 验证合同。
- **数据来源第 2 条（§7.1）点名数据生成器**：
  > "数据生成器生成的历史记忆、最近对话、当前消息和候选集合"
- **现有 REPLY SFT 数据不能直接充当 reranker 数据**（§7.1）：
  > "它缺少候选记忆和 `ACTIVATE/IGNORE` 标签。"
- 验收必须证明微调收益（§9 相关 + REAL 系列）：相对未微调基线的成对 bootstrap 95% CI 下界 > 0。

### 1.2 已冻结的记录 schema（`reranker_record.schema.json`，2026-08-07 冻结）

程序侧已存在正式记录 schema（`$id: .../memory-reranker-record-v1.json`），字段：

| 字段 | 约束 |
|---|---|
| `sample_id` / `query_id` / `candidate_memory_id` | stableId（`^[A-Za-z0-9][A-Za-z0-9._:-]{2,191}$`） |
| `schema_version` / `dataset_family` / `mode` | `1` / `memory_reranker` / `MEMORY_RERANK` |
| `character_id` | **`const: "qinweixi"` —— 白未晞接入需变更** |
| `split` | `train` / `dev` / `test` |
| `conversation_group_id` / `leakage_group_id` / `scenario_family` | stableId（组级隔离键） |
| `source_record_ids` | ≥1 条 stableId |
| `canon_snapshot` | `{snapshot_id: chat01-canon-v4, sha256}` |
| `generation` | `$defs/generation`（生成溯源） |
| `content_kind` | `ranking_pair` |
| `query_text` / `candidate_memory_text` | 1..8000 字符 |
| `label` | `positive` / `hard_negative` / `easy_negative` |
| `relevance_score` | 0..1 |
| `should_recall` | bool |
| `label_evidence` | 1..2000 字符（标签依据） |

### 1.3 freeze02 合同对 reranker 数据的准入（**关键前置条件**）

`freeze02_contract_v2.json` `memory_reranker` family：

```json
"allowed_modes": ["MEMORY_RERANK"],
"data_admission": "blocked_until_select01",
"separation_rules": [
  "只包含 query、候选记忆、相关性分数、困难负样本和全拒绝标签。",
  "不得使用普通玩家回复 SFT 直接充当 reranker 数据。",
  "SELECT-01 冻结重排标签合同前禁止生产或训练。"
]
```

**结论：`blocked_until_select01` —— SELECT-01（重排标签合同）尚未冻结前，生成器即使实现 MEMORY_RERANK 模式，也会被 `Freeze02Admission` 阻断（AdmissionBlocked）。** 这是施工的前提门：先由程序侧冻结 SELECT-01，生成器才能投产。生成器侧 `core/admission.py` 的机制已就绪（角色无关、按 mode 判断），无需改动。

---

## 二、目标：生成器要产出什么

### 2.1 产出形态（对齐 V2 §6.2 样例 + 已冻结 schema）

每条记录 = `query + 候选记忆` 配对 + 标签：

```json
{
  "sample_id": "ms-000001",
  "scenario_id": "interview-finish-01",
  "selector_query": {
    "recent_dialogue": "白未晞：嗯，你出门前说紧张。",
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

落盘格式按 `reranker_record.schema.json`（`label: positive/hard_negative/easy_negative`、`should_recall`、`label_evidence`）。

### 2.2 正样本覆盖（V2 §7.2，6 类）

1. 明确询问过去
2. 含糊指代（"那个、她、终于结束了"）
3. 当前情绪与过去经历有真实关系
4. 旧经历解释当前立场/担忧/反应
5. 多条记忆共同构成理解背景
6. 记忆应进入背景但不宜直接复述（**ACTIVATE 但回复中不提** —— 最难的一类）

### 2.3 困难负样本覆盖（V2 §7.3，9 类）

1. 同主题但不同事件
2. 同一人物但时间/关系/事件不符
3. 已被修正或 superseded 的旧事实
4. 语义相关但此刻提起突兀
5. 为证明"记得"而机械翻旧账
6. 玩家明确不想再谈的内容
7. 无证据的共同往事和模型推断
8. 普通闲聊、喝水、打招呼等不需要旧记忆的回合
9. 所有候选都应 IGNORE 的无记忆回合

**硬性要求**：每个正样本应配套多个由粗召回器实际命中的困难负样本，"不能只使用随机无关文本"（否则模型学不会区分相似经历）。

### 2.4 数据集边界（V2 §11，互斥四集）

```text
train_hard            人工或规则校验后的硬标签训练集
validation            阈值/温度/Top32/量化选择
test_frozen           最终模型验收（训练过程不可查看标签分布）
long_memory_e2e       长对话、跨重启和十万事件端到端集
```

- 禁止同一事件或其改写跨集合；以 `scenario_id`、人物事件和证据链为组。
- 禁止用测试集调阈值/prompt/温度；禁止用最终回复质量替代选择器标签正确性。

---

## 三、生产方案设计

### 3.1 总体流程

```text
白未晞正典/时间线（人物设定/白未晞/）
  → 场景剧本生成（新模式，见 3.2）：为每个 scenario 生成
     「历史记忆集 + 最近对话 + 当前消息 + 候选集」
  → 候选构造（3.3）：正样本 + 三类负样本（含粗召回挖掘）
  → 标签流水线（3.4）：规则初标 → LLM judge 软分 → 人工 hard label
  → 四集切分（3.5）：按 scenario/事件/证据链分组隔离
  → 原子发布（复用 D-4 publish_dataset）
  → 防泄漏检查（复用 contamination_check + chat01/02 blocklist）
```

### 3.2 场景剧本生成（生成器新增能力，核心）

**新数据来源形态**：每个 scenario = 一个"记忆场景剧本"：

```
scenario_id: interview-finish-01
working_state: 玩家今天有一场重要面试（咖啡厅店主，上午请假）
记忆集（本场景的候选记忆来源，来自 timeline + 派生）:
  - M101: 玩家三天前开始紧张准备面试，多次问她"要是没过怎么办"（证据 E9001）
  - M102: 玩家今天早上出门前又检查了一遍简历（证据 E9002）
  - M103: 白未晞说"紧张正常，正常发挥就行"（证据 E9003）
  - M104: superseded 旧事实：一个月前玩家说"面试肯定没戏"（后被 M101 覆盖）
recent_dialogue:
  - 白未晞：紧张什么，正常发挥就行。
current_user_message: 终于结束了，累死我了。
候选集合（构造 → 标签）:
  - M101 → positive（当前情绪与过去经历有真实关系）
  - M103 → positive（背景宜入不宜复述）
  - M104 → hard_negative（superseded 旧事实）
  - M102 → hard_negative（语义相关但此刻突兀/机械翻旧账）
```

**实现要点**：
- 场景剧本 = 结构化数据（非对话生成）：正典/时间线事件 → 记忆文本（确定性渲染，对齐 MEM-05 selector view 渲染约定）→ 对话片段（少量，可复用 REPLY 生成能力或人工编写种子）。
- 场景类型覆盖 V2 §7.2/§7.3 的 6 正 + 9 负类别，按行为族蓝图（blueprint）组织——与现有 blueprint 机制同构。
- **关键差异**：这不是 ShareGPT 对话数据，是"记忆场景"结构化数据；不可混入 REPLY 训练行（程序侧模式隔离原则）。

### 3.3 候选构造

| 候选类型 | 来源 | 标签 |
|---|---|---|
| 正样本 | scenario 的记忆集（与当前消息语义关联） | positive |
| 困难负样本·构造类 | 同主题不同事件 / superseded 旧事实 / 时间关系不符（确定性构造，从 timeline 选） | hard_negative |
| 困难负样本·挖掘类 | **跑一遍 BGE 粗召回**（复用 MEM-05 本地 BGE）：query 的 Top32 中标签为 IGNORE 的记忆 | hard_negative |
| 易负样本 | 随机无关记忆（场景外） | easy_negative |
| 全拒绝回合 | 普通闲聊场景（无记忆需要） | 全部 hard/easy_negative |

### 3.4 标签流水线（对齐 V2 §7.1 来源 4）

1. **规则初标**：确定性（scenario 剧本自带标签 → 初标）
2. **LLM judge 软分**：复用 `core/judge.py` + `LLMJudge`（现有基础设施），输出 `relevance_score`
3. **人工 hard label**：`label_source: human_verified`（V2 硬性要求：硬标签进冻结集前必须人工审核）——复用 `judge_gold/` + 复核表工具链
4. 争议样本（judge 与初标冲突）→ `mark_for_human_review`

### 3.5 切分与发布

- 复用阶段 4 D-2 连通分量切分机制：以 `scenario_id + 事件证据链` 为锚组（与现有 split_anchor_ids 同构），四集互斥
- 复用 D-4 原子发布（manifest 含 split 统计、dedup、contamination、gate summary）
- 新增：`memory_reranker` 数据集 family 的 manifest 变体（含 label 分布、正负比、should_recall 统计）

### 3.6 与现有生成器能力的映射

| 现有能力 | 复用方式 |
|---|---|
| `FilePackageRegistry` / profile 包 | 白未晞正典/时间线作为记忆来源 |
| `core/admission.py` Freeze02Admission | MEMORY_RERANK 模式准入（**当前 blocked_until_select01，等 SELECT-01**） |
| `core/judge.py` / `judge_gold/` | 软分 + 人工校准 |
| D-2 splitter / D-4 publish_dataset / contamination_check | 切分/发布/防泄漏 |
| `tools/` 复核工具链 | 人工 hard label 复核表 |
| 模式隔离原则（adapters/modes + export） | 新 `MEMORY_RERANK` 模式适配器 + 独立导出（不混 REPLY） |

---

## 四、工程量与施工顺序（研究建议，待拍板）

| 步骤 | 内容 | 规模 |
|---|---|---|
| 0（前置） | 程序侧冻结 SELECT-01 重排标签合同（freeze02 的 next_checkpoint） | 程序侧，非生成器 |
| 1 | schema 对齐：`reranker_record.schema.json` 的 `character_id` 从 `qinweixi` 改可配置（**程序侧合同变更，白未晞接入前提**） | 程序侧 + 生成器读取 |
| 2 | 新增 `MEMORY_RERANK` 模式适配器 + 场景剧本 schema + 记忆渲染（对齐 MEM-05 selector view） | 生成器中 |
| 3 | 场景剧本池（正 6 类 + 负 9 类，每类若干 scenario） | 内容中 |
| 4 | 候选构造器（构造类 + BGE 粗召回挖掘类） | 生成器中 |
| 5 | 标签流水线（初标 + judge 软分 + 人工 hard label） | 生成器小-中 |
| 6 | 四集切分 + 原子发布 + manifest | 复用为主，小改 |
| 7 | 验收：V2 §9 训练合同 + bootstrap CI > 0 | 训练侧 |

## 五、风险与开放问题

1. **SELECT-01 未冻结**：当前 freeze02 明确 `blocked_until_select01` —— 生成器实现可先行，但投产前必须等程序侧冻结；这是唯一硬前置。
2. **character_id 硬编码 qinweixi**：已冻结 schema 内 `const: "qinweixi"` —— 白未晞接入需要程序侧 schema 变更（可配置或新增 baiweixi 版本），不能由生成器单方面绕开。
3. **"粗召回实际命中的困难负样本"依赖 BGE 与记忆库**：生成侧需要能跑真实 BGE（本地 CPU bge-small-zh-v1.5，MEM-05 约定）对生成的记忆集做 Top32 挖掘——生成器仓库当前没有 BGE 依赖，需引入或接受"构造类负样本先行、挖掘类后补"的分期。
4. **人工 hard label 成本**：V2 要求硬标签人工审核；规模与抽查比例需拍板（建议：train_hard 全量人工 + 其余抽查，参考 judge_gold 校准流程）。
5. **场景剧本 vs 回复数据的边界**：剧本含"当前消息/最近对话"片段，若直接用真实 REPLY 数据派生需防止与回复训练集交叉（切分隔离 + 污染检查）。
6. **AVERAGE 记忆文本渲染一致性**：生成器渲染的记忆文本（selector_text）与运行时 MEM-05 selector view 渲染必须一致，否则训练/推理分布漂移——建议复用同一渲染函数（跨仓共享或快照+守卫测试，参考 T2 锚一致性模式）。

## 六、结论

- **生成器需要新增能力**：`MEMORY_RERANK` 模式（场景剧本 + 候选构造 + 标签流水线 + 四集发布）——现有 REPLY 生产链路完全不覆盖。
- **大部分基础设施可复用**：profile 包、admission、judge、splitter、publish、复核工具链。
- **两个硬前置在程序侧**：SELECT-01 冻结、`reranker_record.schema.json` 的 character_id 可配置化。
- **建议分期**：P1a（构造类负样本 + 人工标签，不依赖 BGE）→ P1b（接入 BGE 粗召回挖掘类负样本）→ P1c（真实训练 + bootstrap 验收）。

---

## 七、施工状态（2026-08-09 机制层已完成）

### 7.1 已落地（生成器侧，多角色通用）

| 文件 | 内容 |
|---|---|
| `data_gen_v4/adapters/modes/rerank.py` | `MemoryRerankAdapter`：候选蒸馏（timeline/canon units → selector_text）+ 教师标注 + jsonschema 校验 + labels 覆盖完整性检查（未知/遗漏 memory_id 拒绝） |
| `data_gen_v4/schemas/mode_v4/memory_rerank_input.schema.json` | input schema（场景 + 候选记忆列表） |
| `data_gen_v4/schemas/mode_v4/memory_rerank_target.schema.json` | target schema（query + labels：positive/hard_negative/easy_negative + relevance_score/should_recall/label_evidence） |
| `data_gen_v4/prompts/memory_rerank.txt` | 教师标注 prompt（激活语义对齐 V2 §9.2 固定指令） |
| `data_gen_v4/adapters/exporters/reranker_jsonl.py` | `RerankerJsonlExportAdapter`：一条 TrainingRecord 展开为每条 (query, candidate) 一行，字段对齐 `reranker_record.schema.json`；**character_id/canon_snapshot/split 从 export_profile 读（不写死）** |
| `data_gen_v4/packages/protocols/relationship-runtime-v1.yaml` | `modes` 注册 `MEMORY_RERANK`（mode_adapter_id/schema refs/render_profile_id/scheduling=offline_training_data） |
| `data_gen_v4/adapters/modes/factory.py` | item input 透传 `working_state`/`memory_pool`（REPLY 条目缺省空值，无副作用） |
| `gen_v4.py` | `--skip-admission` 参数；mode 分轨导出（REPLY→ShareGPT / MEMORY_RERANK→reranker JSONL）；metadata 加 `mode`/`records` 字段；样张按 mode 分支 |
| `profiles/baiweixi/recipe.yaml` | `rerank_memory` stratum（target_count 8，review_requirement full【硬标签人工审核对齐 V2】） |
| `profiles/baiweixi/pools.yaml` | 8 个种子场景剧本（面试/雨夜/咖啡厅/手机/纸箱/猫叫/帮忙/家），每场景 4 个候选记忆 |
| `tools/publish_dataset.py` | `--character-id/--profile-id/--dataset-family/--mode` 参数化（默认保持 qinweixi/visible_reply/REPLY 兼容）；RERANK 样本 text 提取 query_text+candidate_memory_text |
| 测试 | `tests/stage5/test_rerank_mode.py` + `test_rerank_export.py`（17 个用例：蒸馏/校验/导出字段/多角色 character_id 断言）；协议包 mode 注册断言更新为 4 个 |

**多角色验证**：`character_id` 从 profile 读（baiweixi 编译产出 `character_id=baiweixi`，非 qinweixi 写死）；候选记忆来自各 profile 自己的 timeline/canon。

### 7.2 程序侧前置（待办，生成器不可自行放行）

1. **SELECT-01 重排标签合同冻结**（freeze02 `blocked_until_select01` → `allowed_after_dataset_freeze`）；
2. **`reranker_record.schema.json` 多角色化**（`character_id: const qinweixi`、`canon_snapshot.snapshot_id: const chat01-canon-v4` → 发 schema v2，走新 freeze 流程，旧版本保持不可变）；
3. 同名连带：`dataset_manifest.schema.json` 的 character_id/snapshot_id const、freeze02 `physical_root` 字面量参数化。

### 7.3 P1b 规划（未施工）

- query/memory 文本与运行时 `render_selector_query_text` / `build_selector_views` 逐字对齐（生成器复刻 + 守卫测试，T2 锚一致性模式）；
- 引入本地 BGE（bge-small-zh-v1.5，MEM-05 约定）跑粗召回挖掘困难负样本；
- 标签流水线：规则初标 → LLM judge 软分 → 人工 hard label（label_source=human_verified）；
- 四集切分（train_hard/validation/test_frozen/long_memory_e2e）+ 防泄漏扫描（query_text/candidate_memory_text/label_evidence 三字段对照 chat01 评测集）。
