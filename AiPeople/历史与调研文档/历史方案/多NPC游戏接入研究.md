# AiPeople 多 NPC 游戏接入：性能压力与可行性研究

> 性质：研究文档（可行性 + 压力分析 + 需做的改变），不含实现动作。
> 数据时点：2026-08-01，经 GitHub / 各官方文档核实。星标与性能为时点快照，关键数在自机实测前为估算。
> 背景：我们当前是"单一虚构人格烤进权重 + 双系统记忆（快轨实时/慢轨周期LoRA固化）+ 纯文本 + 8GB 单卡"。现新增需求：**把模型塞进游戏，本地服务多个 NPC**。

---

## 0. 需求重述与核心张力

新需求 = **一个本地模型，游戏内多个 NPC 共用**。这和我们当前"为单一人格服务"的设计在三处直接冲突：

1. **人格落点**：当前是"这个人的全部人格在权重里"。多 NPC = 多个不同人格，不可能 N 个独立 4B 模型塞一张卡。
2. **慢轨（持续权重学习）**：当前是"这个人随经历周期 LoRA 固化成长"。多 NPC = N× 训练成本，是本项目最致命的不可行点。
3. **服务形态**：当前是单请求对话。游戏 = 多 NPC 并发、短轮、突发，需要连续批处理与状态管理。

好消息：快轨（实时情景记忆）**几乎不用改**就能扩展到多 NPC——它是本项目唯一天然可规模化的部分。

---

## 1. 可行性总判定（先给结论）

| 维度 | 判定 | 一句话 |
|---|---|---|
| 多 NPC 并发推理（本地单卡） | ⚠️ 可行但范围窄 | 8GB 真实 2-4 个同时在说话；24GB 8-20 个；"多"比想象小 |
| 多人格共享一个模型 | ✅ 可行 | 共享基座 + LoRA adapter 池 + 确定性路由（NPC_id→adapter） |
| 多 NPC 记忆/状态 | ✅ 可行 | 单共享存储按 agent_id 分区，非 N 个库，存储 trivial |
| per-NPC 持续权重学习（慢轨×N） | ❌ 不可行 | 训练 ≫ 推理几个数量级；全市场已决定 growth=memory 非 weights |

**三道墙，按出现顺序**：① KV-cache 显存（能常驻几个 NPC）→ ② 解码吞吐（能同时说话几个）→ ③ 持续学习 N×（哪些 NPC 能成长）。

**最重要的 myth-buster**：斯坦福 25-agent 小镇（Smallville，所有人引用的"多 NPC"模板）**是串行跑的，不是并发 25 个 LLM**——跑了两天、花了数千美元。所有可验证的量产部署（NVIDIA ACE、Inworld、Convai）都把 LLM 推到**云端**。本地多 NPC 在消费级单卡上是前沿，没现成范式。

---

## 2. 性能压力分析

### 2.1 并发上限：KV-cache 显存（第一道墙）

公式：`单 NPC KV = 2 × 层数 × KV头数 × head_dim × 上下文 × dtype字节`
Qwen3-4B（36 层、8 KV 头、head_dim 128、2k 上下文）：
- fp16 KV ≈ **288 MiB / NPC**
- int8 KV ≈ **144 MiB**（LMDeploy 支持在线量化）
- int4 KV ≈ **72 MiB**
权重 4-bit ≈ 2.3 GiB（常驻一次，共享）。

| GPU | 扣权重后余量 | fp16 KV 并发上限 | int8 KV | int4 KV |
|---|---|---|---|---|
| 8 GB (4060) | ~5 GB | ~17 | ~34 | ~69 |
| 12 GB (3060/4070) | ~9 GB | ~31 | ~62 | ~125 |
| 16 GB (4060Ti 16G) | ~13 GB | ~46 | ~91 | ~180 |
| 24 GB (3090/4090) | ~21 GB | ~73 | ~146 | ~290 |

**这些都是内存上限**。真实并发远低于此，因为吞吐墙先到（见 2.2）。

### 2.2 解码吞吐：真实墙（能同时说话几个）

内存能常驻几十个 NPC，但**解码带宽禁止几十个 NPC 同时出字**。连续批处理每加一个 slot，每步 decode 都变慢（每步搬更多显存）。所以：

| GPU | 内存可常驻 | **真实可同时说话** |
|---|---|---|
| 8 GB | ~17 | **2-4** |
| 12 GB | ~31 | **4-8** |
| 24 GB | ~73 | **8-20** |

**设计原则**：架构成"**N 个常驻 NPC，K≪N 同时说话**"——NPC 时间表/错峰，只附近/参与对话的 NPC 生成。这正是 ACE/Inworld 实际做法（云端时分），也是斯坦福小镇字面做法（串行）。

### 2.3 延迟预算

- 文本 NPC：玩家容忍 ~1-2 秒；>3 秒破沉浸。
- 语音 NPC：端到端 <~800ms（感知→LLM→合成）。
- LLM 份额：TTFT <200-300ms，且能在几百 ms 内吐出 ~30-40 token 一句台词。
- **连续批处理对"多短请求"友好**（第二个 NPC 来了不必排在第一个后面等，TTFT 大改善）；但**饱和后每个 NPC 都变慢** → 必须封顶 `max-num-seqs`/`-np`，按延迟预算而非吞吐上限设。

### 2.4 多人格的服务路线（不能 N 个独立模型）

**唯一可行范式**：一个共享基座 + 一池 LoRA adapter + 确定性选择器。来自 S-LoRA / Punica 的 SGMV 内核：**一个 forward 批次里混用不同 adapter，跨 adapter 批处理近乎免费**。

| 项 | 数 |
|---|---|
| 单个 adapter（Qwen3-4B，r16，全线性） | ~56 MB |
| 50 个 adapter | ~2.8 GB |
| adapter 热交换（cache miss 时） | ~2 ms / adapter |
| 常驻 adapter 批处理 | ~0 额外延迟（SGMV 同核批） |

- **12-24GB**：50 个 adapter 全常驻，零 swap。
- **8GB**：常驻 ~15-20 个 hero/原型 adapter，其余 CPU 暂存 LRU，miss 代价 ~2ms。
- **不要**学路由器（LoRAMoE/LoRAHub 是研究级）——游戏**知道是哪个 NPC 在说话**，用确定性 `NPC_id → adapter_id` 查表，零路由误差零延迟。
- **不要**把 LoRA 加到 `embed_tokens/lm_head`（Qwen3 词表大、tie 权重，r8 单此一项就 ~2.5GB）。
- **混合架构（Neeko 的设计，推荐）**：基座=通用角色能力 SFT（"如何作为一个角色对话"，烤一次）；per-NPC LoRA=稳定人格/口吻/世界观；prompt anchor + per-NPC memory=易变事实（关系、近况、世界事件）。**"人格在权重"属性保留，但只限定在稳定层**——不丢失我们 deliberately 建的性质，只是把易变的东西还给 prompt+memory。

### 2.5 多 NPC 记忆与状态

**可行，且不是瓶颈**。所有平台都是**单共享存储按 agent_id 分区**，不是 N 个库：
- mem0：分层（对话/会话/user/org）按 `user_id/run_id` 分区；"org memory"显式跨 agent 共享。
- Letta：每个 agent 一个 MemFS 目录（`memfs/<agent-id>/memory`），共享一个本地后端。
- 存储成本 trivial：1536 维 fp32 embedding ~6KB，10k 记忆/NPC × 100 NPC = 1M 向量 ≈ 6GB。
- **模型无状态，状态在外部**：每轮 = 从分区取 k 条 + 拼 prompt + 调共享模型。检索与拼装便宜可并行；**串行资源是模型吞吐**（2.2 的墙）。

---

## 3. 最大的不可行：per-NPC 持续权重学习（慢轨 ×N）

这是我们架构**唯一致命的规模问题**，三个独立原因：

1. **算力**：训练 ≫ 推理几个数量级。每 NPC 每反思周期一次反向传播，N 个 NPC 累积 N× 训练负载且随时间增长。100 NPC = 单人设计 100 倍训练量，循环往复。
2. **遗忘+漂移**：单人人格流上持续微调本就是遗忘最严重的 regime；N 个独立 adapter 各自漂移，合并 N 个发散 adapter 是未解问题（mode/task interference）。
3. **全市场已决定 growth=memory，非 weights**：
   - Letta 的"持续学习"=记忆+状态编辑（dreaming = git worktree 改记忆，不动权重；persona 在 `system/persona.md` 每轮注入）。
   - mem0 靠分层记忆增长，从不碰权重。
   - AIRI 靠记忆层，无微调闭环。
   - NVIDIA ACE 用 RAG 知识 + **按模型尺寸/角色分 tier**（4B/9B；助手/伙伴/队友/敌人/市民），**不是 per-NPC 权重**。

**关键区分（务必不要混淆）**：
- multi-LoRA **服务**（S-LoRA/LoRAX/vLLM）= 便宜、可上千 adapter、近乎常量延迟 → **成熟可用**。
- multi-LoRA **训练**（产生那些 adapter 的 N× 持续学习）= 不可行 → **无人做**。
我们的慢轨在训练那侧，正是不可行的部分。

---

## 4. 为满足此需求必须做的改变

### 4.1 人格落点：从"全进权重" → 分 tier

| Tier | 占比(~100 NPC) | 人格落点 | 记忆(快轨) | 权重学习(慢轨) | 服务 |
|---|---|---|---|---|---|
| **Hero** | 1-5 | per-NPC LoRA adapter（人格在权重） | 完整情景记忆 + 反射 | ✅ 周期批量固化 | 多 LoRA (vLLM/LoRAX) |
| **具名/支线** | ~5-20 | prompt persona + archetype LoRA（原型复用） | per-NPC 分区记忆 | ❌（可选消费一个共享"世界文化"adapter） | 共享模型 + 共享 adapter |
| **背景/氛围** | 其余 | 极短 prompt | 轻量共享池或无状态 | ❌ | 小模型 / 共享基座 |

**含义**："人格在权重"不再对全体 NPC 成立——**只对 hero 保留**；多数 NPC 退回 prompt + memory（即 Letta 的 `persona.md` 每轮注入模式）。这不是妥协，是所有量产 stateful-agent 平台的实际做法。我们 deliberately 建的"人格在权重"性质**降级为 hero 专属**，但没消失。

### 4.2 慢轨：从"每人持续学习" → hero-only 批量固化 + 共享固化层

- **慢轨只给 hero（1-5 个）**，周期批量，用 vLLM `load_inplace` 热替换 adapter（为"持续更新的 adapter"设计，非 hack）。
- **背景 NPC 永不碰权重**，成长全靠快轨记忆。
- **加中间"共享固化"层**（替代 O(N) 训练的 O(1)/O(hero) 路径）：周期批量作业把多个背景 NPC 的反思 episode 折叠成 ① 一个共享"世界/文化"RAG 语料 和/或 ② 一个共享 adapter，随 hero adapter 一起服务。
- **快轨（实时记忆）全 tier 通用，几乎不变**——这是本项目唯一天然抗规模化的部分，直接复用。

> 反射闭环（我们原计划的全栈空白点/原创价值）**保留，但收窄到 hero tier + 共享固化层**。它从"让一个人长期成长"变成"让少数主角长期成长 + 整个 NPC 世界集体演化"——价值仍在，且变得可行。

### 4.3 服务栈：从 LF/单模型 → 多 LoRA 连续批处理服务

| 栈 | Windows 原生? | 适合场景 |
|---|---|---|
| **llama.cpp server**（`llama-server.exe`） | ✅ 原生 | 随游戏分发（单 .exe），`-cb` 默认开、`-np` slot |
| **LMDeploy TurboMind** | ✅（tp=1 自 2023.8） | Windows 原生高吞吐，**int8/int4 KV 量化翻倍并发** |
| **SGLang** | ❌ 需 WSL2 | 最大吞吐，**prefix caching 对共享 persona prompt 最优**（共享前缀只算一次） |
| **vLLM 多 LoRA / LoRAX** | ❌ 需 WSL2 | 服务 hero adapter 池，`load_inplace` 热替换 |
| Ollama | ✅ | ⚠️ `OLLAMA_NUM_PARALLEL` 默认 1（串行！），必须覆盖 |

**建议**：开发机（接受 WSL2）用 **SGLang + vLLM 多 LoRA** 拿最大 NPC 数；随游戏发到玩家 PC 用 **llama.cpp server** 或 **LMDeploy**（唯一 Windows 原生高吞吐，配 KV 量化）。**别用默认 Ollama**。

### 4.4 运行时：多 NPC 调度

- NPC 时间表/错峰，**只附近/参与对话的 NPC 生成**（呼应 2.2 的"N 常驻 K 说话"）。
- 共享无状态模型 + 外部状态/记忆（agent_id 分区）。
- 确定性 `NPC_id → adapter_id` 选择（hero）/ `NPC_id → archetype` 选择（支线）/ 无 adapter（背景）。

---

## 5. 现有设计 vs 多 NPC 游戏需求：逐项差异

| 组件 | 单一人格（现状） | 多 NPC 游戏（需要） | 改动 |
|---|---|---|---|
| 人格落点 | 全进权重（1人） | hero=LoRA；多数=prompt+memory | **分 tier** |
| 基座 | 单一 SFT 人格 | 共享"角色能力"基座 + adapter 池 | **加共享基座层** |
| 快轨记忆 | 单人情景记忆 | per-NPC 分区（agent_id） | 几乎不变（加分区键） |
| 慢轨固化 | 每人周期 LoRA | hero-only 批量 + 共享固化层 | **收窄+加共享层** |
| 反射闭环 | 单人 fast→slow | hero + 集体演化 | 收窄范围 |
| 服务 | LF/单请求 | 多 LoRA 连续批处理 | **换服务栈** |
| 调度 | 无 | N 常驻 K 说话 + 时间表 | **加调度器** |
| 评测 | 人格保真 | + 并发/延迟/adapter 路由正确性 | 加性能与路由评测 |

---

## 6. 推荐目标架构（tiered）

```
游戏引擎
  └─ NPC 调度器（错峰；只附近/参与 NPC 生成；NPC_id→tier）
       ├─ Hero tier（1-5）：NPC_id→专属LoRA
       │     ├─ 共享基座(角色能力SFT, 4-bit) + 多LoRA服务(vLLM/LoRAX)
       │     ├─ 快轨：per-NPC 分区情景记忆（三分量检索+DECIDE+反射）
       │     └─ 慢轨：周期批量LoRA固化(replay+soup) → load_inplace 热替换
       ├─ 具名/支线 tier（~5-20）：NPC_id→原型LoRA(商人/守卫/贵族/儿童…)
       │     ├─ prompt persona（Letta式 persona.md）+ per-NPC 分区记忆
       │     └─ 无权重学习（可消费共享"世界文化"adapter）
       └─ 背景 tier（其余）：共享基座 + 短 prompt + 轻量/共享记忆
共享固化层（周期批量）：多背景NPC反思 episode → 共享RAG语料 / 共享adapter
服务栈：开发=WSL2 SGLang+vLLM多LoRA；分发=llama.cpp/LMDeploy(int8/int4 KV)
```

---

## 7. 风险与待实测

1. **命门仍待实锤**：合成人生语料一致性未在真实模型验证（M0 真实验证没跑）——多 NPC 放大此风险（N 个人设的语料都要一致）。
2. **8GB 天花板**：4B + 少量 hero adapter + 小并发（2-4 同时说话）。要更多 NPC 需 12/24GB 或退到云端。
3. **慢轨仍研究级**：即便收窄到 hero，持续学习的遗忘/稳定性是未解难题；共享固化层无现成范式。
4. **端到端延迟未实测**：8GB 上 4B + 多 LoRA + KV，TTFT/吐字率需在自机实测（`-np` ∈ {2,4,8,16} 找延迟拐点）。
5. **多 NPC 语料一致性**：从"1 个人的人生语料"变成"N 个人的语料"，互相要一致（同一世界观、关系网、时间线）——canon 要扩成"世界正典"。
6. **分发难题**：随游戏发到玩家 PC = 要 llama.cpp 原生 + 模型 GGUF + adapter 池，非简单事。

---

## 8. 结论与建议

- **可行，但要重新定位**：从"一个会成长的人"调整为"**少数主角会成长 + 一个会集体演化的 NPC 世界**"。这是多 NPC 游戏接入的必然形态，也是所有量产平台的做法。
- **快轨是本项目的抗规模化的资产**，几乎原样扩展到 per-NPC 分区。
- **慢轨必须降级**：hero-only 批量固化 + 共享固化层替代 per-NPC 持续学习——否则 N× 训练压垮一切。
- **服务栈要换**：LF/单请求 → 多 LoRA 连续批处理（SGLang+vLLM 开发；llama.cpp/LMDeploy 分发）。
- **不立刻全做**：建议先完成单一人格 M1/M2（人格在权重 + 实时记忆，已规划的合格门槛），再用 hero tier + 多 LoRA 服务做"第一个多 NPC 原型"，验证并发/延迟/路由，再决定 tier 全铺开。
- **与之前对比文档的关系**：本研究的"分 tier + 慢轨收窄"修正了《技术路线对比》里"全栈自洽、慢轨全量闭环"的乐观定位——多 NPC 场景下，"全进权重 + 全员持续成长"不可行，必须 tier 化。但**差异化价值仍在**（hero 的反射闭环 + 集体演化的共享固化层，OSS 仍无人做）。

---

### 关键来源
- NVIDIA ACE（Nemotron 4B/9B GGUF、NVIGI 进程内 CUDA 调度、RAG、角色 tier）https://developer.nvidia.com/ace-for-games
- Inworld（云端 Realtime API）https://www.inworld.ai ｜ Convai https://convai.com
- 斯坦福 generative agents（25 agent **串行**、GPT-3.5、两天、数千美元）https://arxiv.org/abs/2304.03442
- SGLang（连续批处理 + RadixAttention prefix caching + 多 LoRA csgmv）https://github.com/sgl-project/sglang
- LMDeploy（**Windows 原生 TurboMind**、int8/int4 KV 量化、W4A16）https://github.com/InternLM/lmdeploy
- llama.cpp server（`-cb` 默认、`-np` slot、原生 `llama-server.exe`）https://github.com/ggml-org/llama.cpp
- Ollama（`OLLAMA_NUM_PARALLEL` **默认 1**）https://github.com/ollama/ollama
- vLLM 多 LoRA（`max_loras`、per-request、`load_inplace` 热替换）https://docs.vllm.ai/en/latest/features/lora.html
- S-LoRA（单 GPU 上千 adapter、4× 吞吐、SGMV）https://arxiv.org/abs/2311.03285 ｜ LoRAX https://loraexchange.ai
- Neeko（per-character LoRA + 增量学习，本用例的架构蓝本）https://github.com/weiyifan1023/Neeko
- Letta MemFS/stateful（`memfs/<agent-id>`、persona.md 每轮注入、dreaming=git worktree 改记忆）https://docs.letta.com/concepts/memfs
- mem0 分层分区记忆 https://docs.mem0.ai/core-concepts/memory-types
