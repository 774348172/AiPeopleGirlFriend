# AiPeopleCreate — 通用数据生成器仓库

> 2026-08-07 目录整理：通用数据生成器及其产物从 `F:\AiPeople` 独立至此，与 AI 程序（runtime / eval / 训练包）解耦。
> AI 程序侧见 `F:\AiPeople\README.md`。   

## 目录结构

```text
data_gen/              旧生成器（V1 管线：gen_qwx / gen_yuqian）
data_gen_v4/           V4 通用生成器核心（core / adapters / schemas / packages / prompts）
profiles/              V4 角色包（当前：baiweixi/ 白未晞 P0 女主角；qinweixi/ 秦未晞历史包）
人物设定/              正典 junction（物理副本在仓库根 `人物设定/`，与本目录同级；维护只改仓库根物理文件）
历史与调研文档/        历史角色正典副本（秦未晞 2026-08-08 迁入，供历史包编译对照）
gen_v4.py              V4 通用生成入口（--profile <id> / --count / --range-types / --regen-indexes）
gen_qin_v4.py          秦未晞历史兼容壳（等价 gen_v4.py --profile qinweixi，保留旧符号）
gen_qwx.py             V1 生成脚本（历史）
gen_yuqian_casual.py   V1 生成脚本（历史）
repair_qwx.py / repair_qwx2.py    V1 修复脚本（历史）
batch_experiment.py    批量实验（v2 消融 A 组用：V4 pool + checker）
tools/                 生成器工具（复核表 / 复核工具 / taxonomy / 去重 / blocklist / 正典同步…）
tests/                 生成器测试（adapters / core / schemas / stage3-5 + T2 锚一致性守卫）
data/                  V1 语料（life_corpus）与 V1 训练数据（sft），dataset_info.json
训练数据/              V4 生成产物（jsonl + sqlite + gate_report，按 {profile}_v4_{count} 命名）
设计文档/              生成器设计文档（通用数据生成器/ 与 分析/）
config.yaml            本仓库配置（正典指向本仓库副本）
.env.example           环境变量示例（复制为 .env 并填入密钥）
```

## 与 AI 程序（F:\AiPeople）的边界

| 方向 | 内容 |
|---|---|
| 生成器 → AI 程序（只读） | 评测文本 `F:\AiPeople\eval\chat02\...`（blocklist 工具）；锚快照 `runtime/_prompt.py`（T2 一致性守卫测试与 repair 工具）；旧 GGUF / llama-server（check_label_prefix 工具） |
| 生成器 → AI 程序（写） | `tools/build_chat02_blocklist.py` 把契约写入 `F:\AiPeople\training_package_*\eval_exclusions\` |
| 正典 | **唯一正典物理副本在仓库根 `人物设定/`**（2026-08-17 从本仓库迁至仓库根）。本目录 `人物设定/` 与 AI 程序侧 `AiPeople/人物设定` 均为指向仓库根 `人物设定/` 的 junction。维护只改仓库根物理文件；改后运行 `python tools/check_profile_sync.py --sync` 同步 `profiles/qinweixi/sources` |
| AI 程序 → 生成器 | 无代码 import（仅 junction 链接与 blocklist 契约读取） |

**规则**：正典唯一物理副本在仓库根 `人物设定/`；本目录与 AI 程序侧均经 junction 访问（若复制仓库请连同 junction 目标一起处理）。本仓库对 AI 程序侧除 blocklist 写入外一律只读。

## 使用

```bash
# 1. 首次使用：配置密钥
cp .env.example .env        # 填入 OPENAI_BASE_URL / OPENAI_API_KEY / STRONG_MODEL

# 2. 生成测试（.venv 或系统 python ≥3.11）
python -m pytest tests/ -x -q

# 3. V4 生产生成（生成前确认施工计划阶段门）
#    当前默认角色：白未晞（baiweixi，P0 女主角）；历史角色：秦未晞（qinweixi）
python gen_v4.py --count 1000 --range-types reply_casual=0:150,...

# 3b. MEMORY_RERANK（记忆选择器训练数据，2026-08-09）
#     机制验证用 --skip-admission；正式投产需程序侧 SELECT-01 重排标签合同冻结
python gen_v4.py --profile baiweixi --skip-admission --range-types rerank_memory=0:8

# 4. 复核与回流
python tools/build_review_sheet.py
python tools/taxonomy_report.py <sqlite>
```

## 多角色使用（2026-08-08 接线通用化）

生成器核心（data_gen_v4）角色无关；每个角色是一个 profile 包 `profiles/<id>/`。
新增角色三步：

1. 正典就位：`人物设定/<角色>/` 四件套（bible.yaml / canon.json / timeline.yaml
   / 角色设定定稿.md）。正典是唯一权威，profile 包只引用不复制。
2. 建包：复制 `profiles/baiweixi/` 骨架，改 `manifest.yaml`（profile_id）、
   `profile.yaml`（identity_sources 指向新正典 + policy_terms/anchor_contract/
   fact_labels/prompt_policy_block）、`recipe.yaml`（配额）、`release.yaml`、
   `pools.yaml`（内容池）。
3. 生成：`python gen_v4.py --profile <id> --count 20`。

bible voice 段支持两种 schema（通用 resolver 自动归一化）：

| 字段 | 秦式（历史） | 白未晞式（现行） |
|---|---|---|
| 口吻总述 | `voice.register` | `voice.tone` |
| 句长 | `voice.sentence_length` | `voice.sentence_style` |
| 标点 | `voice.punctuation` | （缺省跳过） |
| emoji/markdown | `voice.emoji/markdown` | （缺省 false） |
| 常用词/口头禅 | `voice.vocabulary/catchphrases` | （缺省空=无口癖，T8 退化克制） |
| 禁助手腔 | `voice.forbid_assistant_speak` | `voice.forbidden_tendencies` |
| 称呼 | `player.name_slots` | （无 player 段 → 昵称规则留空，不发明设定） |

称呼规则（name_slots）与玩家关系句（player_name + player_age）缺省时锚自动省略
——不发明正典没有的设定；补正典后自然生效，无需改代码。

同步工具：`tools/sync_runtime_anchor.py --profile <id> --const-name <常量名>`
（默认 qinweixi / QIN_WEIXI_REPLY_SYSTEM 行为不变；AI 程序侧
`F:\AiPeople\runtime\_prompt.py` 需先有对应角色常量）。

## 记忆选择器（MEMORY_RERANK）训练数据（2026-08-09）

生成器新增 `MEMORY_RERANK` 模式：为每个场景产出 query + 候选记忆 +
positive/hard_negative/easy_negative 标签配对，对齐 AI 程序侧
`eval/training_contract/schemas/reranker_record.schema.json`。
**多角色通用**：character_id/canon_snapshot 从 profile 包读，不写死；
候选记忆来自各 profile 自己的 timeline/canon（`profiles/<id>/pools.yaml` 的
`rerank_memory` 池条目用 `memory_pool` 声明候选 source_id）。

注意：freeze02 合同 `memory_reranker.data_admission=blocked_until_select01`，
正式投产需程序侧 SELECT-01 重排标签合同冻结（见
`设计文档/通用数据生成器/分析/Reranker训练数据生产方案_v1.md` §7.2）；
机制验证/测试用 `--skip-admission`。

## 相关施工文档

- 施工总计划（v2 优化版）：`设计文档/通用数据生成器/施工/施工总计划_v2.md`（权威依据：`设计文档/通用数据生成器/当前权威设计/通用数据生成器与重构方案审核报告_v2.md`）
- 分析：`设计文档/分析/主流方案对比与改造建议_v1.md`
- 设计文档索引：`设计文档/README.md`
