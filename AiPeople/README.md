# AiPeople

AiPeople 当前产品为《猫咪女友》：一款完全本地运行的男性向 AI 恋爱角色游戏。产品生命周期允许多个女主角生活在同一个松江府世界和同一个存档中，共享唯一男主角；P0 只验证一个激活女主角，首个角色为白未晞。

## 当前文档

- [项目框架需求](需求文档/项目框架需求.md)：产品范围、功能、质量和验收标准。
- [女主角角色包需求](需求文档/女主角角色包需求.md)：所有当前与未来女主角的通用接入、状态、记忆和资产合同。
- [共享世界](世界设定/README.md)：松江府世界正典；所有女主角和唯一主角共享。
- [人物设定](人物设定/README.md)：[唯一男主角](人物设定/主角/README.md)与当前首个女主角[白未晞](人物设定/白未晞/README.md)。
- [唯一 AI 实现](设计文档/AI设计/当前权威设计/AI女友最小心智系统设计.md)：对话运行时、关系连续性、记忆、回忆、心境和前瞻状态的唯一实施依据。
- [V6 施工总纲](设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md)：当前唯一施工排序，按 `WMR-00` 至 `WMR-08` 推进。
- [产品集成设计](设计文档/AI设计/当前权威设计/本地AI恋爱桌面宠物产品集成设计.md)：LLM、STT、TTS、前端、训练和部署集成，不定义第二套 AI 架构。
- [历史文档索引](历史与调研文档/文档索引.md)：旧方案、旧施工、模型实验、冻结证据和调研结论的统一入口。
- [外部模型资产](模型资产.md)：迁移后保留在 `F:\AiPeople` 的模型与 manifest 引用边界。
- **通用数据生成器位于同一仓库的 `F:\ai-girlfriend\AiPeopleCreate`**（见下方“目录边界”）。

## 当前阶段

V6 实时游戏世界持续心智设计已成为当前唯一 AI 权威。`WMR-00～07` 建立的身份、实时世界、连续 GameClock、最新冻结快照、女主独立记忆、长轨迹、恢复和多角色隔离基础继续保留。旧秦未晞模型只允许作为明确标记的工程兼容性测试资产。

2026-08-17～18 白未晞 7B 候选已从旧 Q5_K_M 工件（答非所问、外语碎片）切换为重新训练的 Q4_K_M 工件（SHA256 `df6eefdce93ec512587131d6e566c32287a77fa601c4a8c423b8f8073a44b842`，Ollama 标签 `baiweixi-7b-fix:latest`）：

- 正式质量集人工裁决后通过 75/89（84.3%）；修复评测工具 `event_memory` bug 并实现 M2 非法 Patch 安全降级（编译协议错误时降级为空 Patch 保持本轮，服务/截断错误不降级）后，11 个失败案例重跑判官零失败，5 个为评测词表误杀、2 个为真实角色问题（teleport 措辞、protagonist_aura 越权）。
- 针对三类失败模式（能力边界越权、身份主客体混淆、关系节奏偏差）创作 14 条补充训练样本并重训（loss 1.699，1987 条 × 2 epoch）。
- SYS-12 一小时发布长测通过：60/60 轮提交、0 事务污染、崩溃/投影/多女主隔离全部恢复、RSS 稳态增长为负、M2 容错零整轮失败；8GB 显存预验证完成（num_gpu=20 层 = 3.73GB，组合 reranker 后约 6.7GB 可行），发布 manifest 已冻结 `num_gpu: 20`。
- 正式检索资产（BGE-small-zh + Qwen3-Reranker-0.6B）已下载并冻结到 `local_runtime/models/retrieval/`。

`SYS-11 / SYS-12` 系统稳定性已通过；P0 正式冻结剩余项为：质量集词表修订（5 个误杀项）、2 个真实失败补样本后完整 89 案例复验、8GB 目标卡最终显存实测、4 个人工长会话。`SYS-12` 资源门槛中 RSS 增长已确认不超限（门槛放宽至 2048 MiB）。

实施顺序：

1. `WMR-00～01`：冻结 V6 schema，建立 `save_id/world_id/protagonist_id/character_id` 和白未晞角色包加载。
2. `WMR-02～03`：建立实时世界、连续 GameClock、男主状态、语义投影、最新快照和 FakeModel 原子回合。
3. `WMR-04～05`：建立女主独立记忆 Repository、真实状态推进、同模型 Critic 和最终回复。
4. `WMR-06～07`：实现回答后整理、五分钟模型整理、长轨迹和跨重启恢复。
5. `SYS-12S`：分离短心智 Patch 与纯文本 `GAME_REPLY`，验证失败无半提交并重跑 60 轮/一小时发布栈。
6. 后台 `B1 + R2 + available_at` 已完成代码施工和隔离 GPU 长测；后台协议、背压和恢复通过。
7. `WMR-08 / SYS-12`：7B Q4 新工件已通过 GAME_REPLY 最小探针；M2 非法 Patch 整轮失败已修复（协议错误降级为 keep）；RSS 增长与组合 GPU 峰值已预验证（8GB 卡需目标硬件最终实测）。
8. 当前入口：质量集词表修订与完整复验 → P0 冻结；通过后进入 P1 2D、P2 语音和第二位正式女主角立项。

## 主要目录

```text
需求文档/              当前有效产品需求
人物设定/              唯一男主角与当前女主角角色包（当前：主角/、白未晞/）
世界设定/              所有角色共享的松江府世界正典
设计文档/              当前有效技术实现方案（AI设计/ 与 通用数据生成器/ 分离，见下）
历史与调研文档/        旧版方案和研究资料
tools/                 AI 程序侧工具（v2500_chat_proxy / refreeze_v3）
training/              训练配置与数据；已有训练输出保留在外部模型仓库
training_packages/     所有平台训练包与训练压缩包
eval/                  固定评测与结果
artifacts/             发布包、下载缓存和历史运行日志
data/external/         外部训练数据镜像
local_runtime/         推理运行库与 manifest；模型权重仍为外部资产
```

## 目录边界（2026-08-07 整理）

**通用数据生成器（data_gen / data_gen_v4 / profiles / 生成脚本 / 生成器工具 / 生成器测试 /
语料与训练数据 / 生成器设计文档）位于 `F:\ai-girlfriend\AiPeopleCreate`**，与 AI 程序解耦：

- 生成器侧见 `F:\ai-girlfriend\AiPeopleCreate\README.md`（结构、配置、只读正典引用说明）。
- 本目录只保留 AI 程序（runtime、eval、训练包、评测契约）。
- **正典单副本（2026-08-17 起）**：物理正典只存在于仓库根 `人物设定/`（本目录与 `AiPeopleCreate/` 同级），本目录 `人物设定` 与 `AiPeopleCreate/人物设定` 都是指向仓库根 `人物设定/` 的 Windows junction，git 只跟踪仓库根一份。克隆新机器后需重建两个 junction：
  `mklink /J <repo>\AiPeople\人物设定 <repo>\人物设定`
  `mklink /J <repo>\AiPeopleCreate\人物设定 <repo>\人物设定`
  维护正典只改仓库根物理文件。junction 路径已写入仓库根 `.gitignore`，git 不跟踪链接本身。
- 生成器产出的历史秦未晞训练数据（原 `人物设定/秦/训练数据/`）位于 `F:\ai-girlfriend\AiPeopleCreate\训练数据/`；
  训练包为自包含产物，不受影响。
- 唯一跨侧引用：
  1. 本目录 `人物设定` 与生成器侧 `AiPeopleCreate/人物设定` 均为仓库根 `人物设定/` 的 junction（同一物理文件）；
  2. `runtime/_prompt.py` 的锚快照与生成器锚渲染的一致性守卫测试
     （`F:\ai-girlfriend\AiPeopleCreate\tests\runtime\test_reply_prompt.py`）从生成器侧读本快照；
  3. `eval/chat01v5/freeze.py` 的 scan_contract roots 仍保留 `data_gen_v4`/`profiles/qinweixi`
     字面量（冻结资产不得原地修改），目录已不存在时扫描为空；下次重冻结时移除。
- `eval/personality_fidelity.py` 已内联原 `data_gen.common` 依赖（2026-08-07）。

发生冲突时，依次遵循项目框架需求、共享世界正典、唯一主角正典、当前女主角角色包、唯一 AI 实现、产品集成方案。`历史与调研文档/` 不构成当前实施要求，其中历史角色和历史方案只用于追溯，不得恢复为现行实现。
