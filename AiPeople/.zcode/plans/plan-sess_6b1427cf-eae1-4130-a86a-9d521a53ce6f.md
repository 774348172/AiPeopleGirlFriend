# AiPeople — 人格 LLM 实施计划

## 0. 需求锁定（已确认）
- **虚构的人**：你设计人设，生成其"一生"语料训练
- **双系统记忆**：快速轨（外部情景记忆，实时读写）+ 慢轨（周期性 LoRA 固化进权重 + replay 抗遗忘）
- **纯文本**
- **个人少量GPU**：7B–8B 级 + QLoRA，Windows 原生
- **合格门槛**：快照人格 + 实时新记忆（= M2 结束达成）；成长性 = M3

## 1. 架构总览

```
┌──────────────── 运行时（实时）────────────────┐
│ 用户输入                                        │
│   → 检索（三分量评分）快速轨记忆                │
│   → 构造 prompt（人格锚 + 检索记忆 + 对话）     │
│   → 基座生成（SFT 权重 = 这个人）               │
│   → 抽取 salience → DECIDE → 写回快速轨        │
│   → 返回                                        │
└────────────────────────────────────────────────┘
            │ 反射触发（importance 累积超阈值）
            ▼
┌──────────────── 慢轨（周期性）────────────────┐
│ 反射：原始情景记忆 → 高价值样本                │
│   + replay buffer（旧样本抗遗忘）              │
│   → 低 LR LoRA 持续微调 → model soup 稳定      │
│   → 新 adapter（人格内化新记忆，缓慢成长）     │
└────────────────────────────────────────────────┘
```

**先例映射**：快速轨 = Generative Agents 检索评分 + mem0 DECIDE + MemGPT 自编辑工具调用；反射 = Generative Agents reflection；慢轨 = RoleLLM 切分 + replay+低LR LoRA+model soup。

## 2. 技术栈（已 Windows/单卡 核实）

| 环节 | 选择 | 备选/说明 |
|---|---|---|
| 基座 | **Qwen3-8B** (Apache-2.0) | Qwen2.5-7B-Instruct；按 VRAM 缩放：≥16GB→8B，~12GB→Qwen3-4B，~8GB→Qwen3-1.7B |
| 微调 | **unsloth** QLoRA（原生 Windows，最简） | peft+trl SFTTrainer（退路） |
| 量化 | bitsandbytes 4-bit（原生 Windows wheel） | — |
| embedding | **Qwen3-Embedding-0.6B** | bge-m3 |
| 记忆抽取 | **Qwen3-0.6B**（非思考模式，Q4，CPU/小GPU） | Qwen3-1.7B |
| 向量库 | **LanceDB**（嵌入式，Windows 原生） | Chroma |
| 推理 v1 | HF transformers（与训练同环境） | 优化：导 GGUF→Ollama（需正确 Qwen chat template + eos） |
| WSL2 | 不需要 | 仅 vLLM/编译卡壳时启用 |

## 3. 组件设计

### 3.1 人设档案 `persona/`（正典种子）
- `bible.yaml`：姓名/出生日期/职业/家乡/家庭/关系网（一个小配角表）/大五人格（OCEAN 数值）/价值观/恐惧/目标/口吻特征（语域、用词、口头禅、句长、是否用 emoji）/禁区。
- `timeline.yaml`：有序人生事件（童年→今），每条 {date, summary, valence, people, importance 1-10}。这同时是 SFT 语料来源 + 模型必须"记得的过去"。
- `canon.json`：结构化可校验事实（扁平 k-v），供一致性检查器比对。

### 3.2 人生生成器 `data_gen/`（命门）
- `life_generator.py`：按 timeline 节点生成 {日记 / 与某配角的聊天 / 对某事的反应}。每次=强模型 API 调用，system prompt=bible+相关 canon 事实+"你在 DATE 以这个人身份写"。输出 JSONL。
- `consistency_check.py`：抽取样本中的断言事实比对 canon.json；检风格一致性（无助手腔、第一人称、口吻匹配）；拒绝/标记。
- `prompts/`：生成+检查模板。
- 产物：`data/life_corpus/*.jsonl` → 格式化为 `data/sft/*.jsonl`（Qwen chat template，日记+对话混合；**绝不含助手腔**）。

### 3.3 海马体 / 快速轨 `memory/`（实时新记忆）
- `schema.py`：memory={id, timestamp, type(episode/fact/reflection), content, embedding, importance 1-10, participants, source, related_entities, last_accessed}。
- `extractor.py`：每轮跑 Qwen3-0.6B 非思考模式抽取候选记忆 + importance（mem0 extract）。
- `store_writer.py`：对每个候选检索相似旧记忆 → DECIDE(ADD/UPDATE/DELETE/NOOP) → 写 LanceDB（mem0 DECIDE，保持有界+无矛盾）。
- `retriever.py`：score=recency+importance+relevance（α=1，Generative Agents）。recency=指数衰减（自上次检索）；relevance=查询与记忆 embedding 余弦；importance=存储 1-10 归一化。返回适配上下文预算的 top-k。
- `store.py`：LanceDB 接口（add/search/update/get_all）。
- `reflector.py`：FAST→SLOW 桥。importance 累积超阈值（或定时）触发：取最近 100 条→生成 3 个问题→检索→生成 5 条带引用的高价值 insight→存为 reflection 记忆。这些是慢轨的精炼训练样本。

### 3.4 慢轨固化器 `training/`
- `consolidate.py`：周期任务——① 取上次固化后的新记忆+reflection ② 格式化为 SFT 样本 ③ 与 replay buffer 混合（约 50/50）④ 低 LR 持续 LoRA ⑤ model soup 新 adapter 与上一版 ⑥ 存 adapter。
- `replay_buffer.py`：管理旧样本集（抗遗忘主力）。
- 注意：慢轨固化**风格/身份演化 + 内化经历**，**不固化可变事实**（可变事实留在快速轨——RoleLLM 切分）。
- `sft_train.py`：首次 SFT（M1）也在此。

### 3.5 运行时 `runtime/`
- `server.py`：FastAPI（v1 可先 CLI）。
- `persona_anchor.py`：构造每请求 system prompt=固定身份锚（姓名、按当前日期算的当前年龄、当前日期、核心特质蒸馏）+ 检索记忆格式化（"你的相关记忆: …"）。**人格主体在权重，锚只管"我是X、今天是Y"的时间/身份清晰**。
- `loop.py`：输入→embed→检索→构造→生成→抽取→写回→返回。

### 3.6 评测 `eval/`（不评推理）
- `personality_consistency.py`：大五一致性（跨运行场景）。
- `self_memory_recall.py`：问它 baked-in 人生事件（M1）+ 问它本会话更早的事（M2）。
- `anti_assistant.py`：检"分三点/希望能帮到你/步骤"漏出，应≈0。
- `drift_monitor.py`（M3）：跨固化周期人格漂移。

## 4. 目录结构

```
AiPeople/
  persona/        bible.yaml, timeline.yaml, canon.json
  data_gen/        life_generator.py, consistency_check.py, prompts/
  data/            life_corpus/, sft/, consolidated/
  memory/          schema.py, store.py, extractor.py, store_writer.py, retriever.py, reflector.py
  training/        sft_train.py, consolidate.py, replay_buffer.py, configs/
  runtime/         server.py, persona_anchor.py, loop.py
  eval/            *.py
  README.md        架构+运行说明
  requirements.txt
```

## 5. 里程碑与 Definition of Done

| 里程碑 | 内容 | DoD |
|---|---|---|
| **M0** 命门验证 | bible+timeline+canon；生成器产出 ~50–200 条过一致性检查 | canon 矛盾<5%、助手腔<5%、口吻匹配 bible |
| **M1** 快照人格 | ~1–5k 样本 QLoRA SFT Qwen3-8B | 口吻像此人+自传记忆答对+无助手腔 |
| **M2** 实时新记忆 | memory/ 全组件接入 loop | **=你的合格门槛**：快照人格+实时记住跨轮/跨会话新事 |
| **M3** 成长 | reflector+慢轨固化（replay+低LR+soup） | 固化后关闭快速轨仍能召回（已内化进权重）；drift 稳定 |
| **M4** 加固 | 完整评测套件+drift 监控+retention 策略 | 全套评测通过 |

## 6. 风险与缓解
- **合成人生一致性**（命门）：canon 约束 + 一致性校验门控，M0 先验证再扩张。
- **灾难性遗忘**（慢轨）：replay buffer 是主力；低 LR + LoRA（冻结基座）+ model soup；EWC 跳过。
- **记忆膨胀**（快速轨）：mem0 DECIDE 去重 + AdaMem 启发的 retention 策略（M4）。
- **助手腔残留**：训练数据零助手腔 + anti_assistant 评测门控。
- **人格漂移**（慢轨）：replay 含人格锚样本 + drift_monitor 跨周期跟踪。
- **VRAM 不足**：模型按 VRAM 缩放（8B/4B/1.7B）。

## 7. 先做什么（M0）
1. 脚手架目录 + `requirements.txt` + `README.md`
2. 写一份**示例虚构人设** bible+timeline+canon（你可替换）
3. `life_generator.py` 原型：给定 canon+一天产 ~50 条，过 `consistency_check.py`
4. 跑通看命门是否成立（矛盾率/助手腔率/口吻匹配）

## 8. 需要你确认的两个参数（不阻塞，review 时告诉我即可）
- **GPU 型号 + VRAM**：决定 8B / 4B / 1.7B。
- **基座家族偏好**：默认 Qwen3-8B（Apache，工程最优）；若你偏好 GLM 系则用 GLM-4-9B-Chat（license 较严）。
