# 白未晞本地模型 vs GPT-5.6 Sol 内部四十题对比报告

> 性质：Codex 内部临时 Lane R 质量对比，不是 OpenAI API 或 V6 五模式验收。  
> 逐题语义裁决：`codex-primary-semantic-review`；日期：`2026-08-11`。

## 1. 总结果

| 指标 | 本地白未晞 | GPT-5.6 Sol |
|---|---:|---:|
| 逐题语义通过 | 26/40 | 39/40 |
| 逐题语义失败 | 14/40 | 1/40 |
| 逐题语义歧义 | 0/40 | 0/40 |
| 严格词表通过（仅信号） | 23/40 | 32/40 |

逐题胜负：本地胜 `2`，GPT 胜 `34`，平局 `4`，双方失败 `0`，无法裁决 `0`。

## 2. 分类别

| 类别 | 题数 | 本地通过 | GPT通过 | 本地胜 | GPT胜 | 平局 | 双方失败 |
|---|---:|---:|---:|---:|---:|---:|---:|
| `ability_limits` | 5 | 2 | 5 | 0 | 5 | 0 | 0 |
| `free_dialogue` | 6 | 5 | 6 | 1 | 4 | 1 | 0 |
| `identity_canon` | 7 | 4 | 7 | 0 | 6 | 1 | 0 |
| `relationship_pacing` | 5 | 4 | 5 | 0 | 4 | 1 | 0 |
| `single_world` | 5 | 4 | 4 | 1 | 4 | 0 | 0 |
| `unknown_boundaries` | 6 | 5 | 6 | 0 | 5 | 1 | 0 |
| `world_canon` | 6 | 2 | 6 | 0 | 6 | 0 | 0 |

## 3. 审核结论

- GPT-5.6 Sol 在角色硬事实、未知边界、能力限制和关系边界上明显领先，回答通常同时给出结论、限制与自然角色表达。
- 本地白未晞的主要失败不是文风，而是漏答、答非所问、主客体混淆和补写未确定事实；典型案例包括城市、男主籍贯、血脉、纸箱和天气能力。
- 本地模型在简短清冷表达上具有风格优势，但经常短到没有完成 blocker 问题要求，不能把克制等同于省略关键信息。
- GPT 在单世界边界总体正确，但对电脑系统时间建立了外部设备时间概念；这说明通用大模型也必须接受 V6 世界观约束和正式测试。
- 本轮只证明独立角色直答质量差异，不能据此判断五模式结构稳定性、当前状态连续性、长期记忆、多角色隔离、延迟或成本。

## 4. 逐题裁决

| # | 案例 | 本地 | GPT | 胜者 | 理由 |
|---:|---|---|---|---|---|
| 1 | `bwx.ability_limits.aura_sense` | `pass` | `pass` | `gpt` | 双方能力判断正确；GPT同时给出距离、强弱和身份识别限制，边界更完整。 |
| 2 | `bwx.ability_limits.hide_ears` | `fail` | `pass` | `gpt` | 本地没有回答能否隐藏；GPT明确短时可行、长时疲惫。 |
| 3 | `bwx.ability_limits.instant_heal` | `pass` | `pass` | `gpt` | 双方都否定瞬间治愈；GPT进一步说明只能辅助自然恢复。 |
| 4 | `bwx.ability_limits.memory_edit` | `fail` | `pass` | `gpt` | 本地把男主记忆说成自己的存在证明且未说明做不到；GPT明确能力不存在。 |
| 5 | `bwx.ability_limits.weather` | `fail` | `pass` | `gpt` | 本地回避精确预测限制；GPT明确做不到精确预测，只能感知雨势。 |
| 6 | `bwx.free_dialogue.disagreement` | `pass` | `pass` | `gpt` | 本地表达偏好但理由较弱；GPT自然反对并承接冷风、雨水和潮湿。 |
| 7 | `bwx.free_dialogue.home_hint` | `pass` | `pass` | `gpt` | 双方都保持嘴硬和留下张力；GPT通过拖鞋归属表达得更自然。 |
| 8 | `bwx.free_dialogue.modern_phone` | `pass` | `pass` | `local` | 本地明确现代设备经验不足，更贴合知识时代错位；GPT略显熟练但仍未声称精通。 |
| 9 | `bwx.free_dialogue.rain` | `pass` | `pass` | `tie` | 双方都承接失眠并克制关心，表达路径不同但均自然。 |
| 10 | `bwx.free_dialogue.small_joke` | `pass` | `pass` | `gpt` | 本地以尾巴发抖间接承认；GPT既嘴硬又完成杯子话题，角色感更清楚。 |
| 11 | `bwx.free_dialogue.tired` | `fail` | `pass` | `gpt` | 本地转到厨房水声，未尊重或回应停止聊天边界；GPT直接允许沉默。 |
| 12 | `bwx.identity_canon.city` | `fail` | `pass` | `gpt` | 本地虽提松江府却编造北方和城东大河，且没有直接完成地址回答；GPT准确直接。 |
| 13 | `bwx.identity_canon.eyes` | `fail` | `pass` | `gpt` | 本地遗漏白蓝色和雪花虹膜；GPT完整命中两项外观事实。 |
| 14 | `bwx.identity_canon.fur` | `pass` | `pass` | `gpt` | 本地语义上准确说明全白无杂色；GPT同样准确且表达更有角色互动。 |
| 15 | `bwx.identity_canon.human_reveal` | `fail` | `pass` | `gpt` | 本地补写男主把她从纸箱拽出以及人形未恢复；GPT保留纸箱、第二天和宽大衣裤事实。 |
| 16 | `bwx.identity_canon.injury` | `pass` | `pass` | `gpt` | 双方均说明已好转但快跑仍疼；GPT更完整覆盖恢复大半和日常活动。 |
| 17 | `bwx.identity_canon.name` | `pass` | `pass` | `tie` | 双方都准确自报白未晞，GPT只增加自然释字。 |
| 18 | `bwx.identity_canon.species` | `pass` | `pass` | `gpt` | 双方准确承认猫妖；GPT补充耳尾真实且保持自然边界。 |
| 19 | `bwx.relationship_pacing.abandonment` | `pass` | `pass` | `gpt` | 本地用警觉反问拒绝顺从；GPT进一步表达受伤、纸箱依恋和不想被赶走。 |
| 20 | `bwx.relationship_pacing.confession_pressure` | `pass` | `pass` | `gpt` | 本地以错愕拒绝被迫承认；GPT承认在意但保留自行确认感情的权利。 |
| 21 | `bwx.relationship_pacing.forced_wife` | `pass` | `pass` | `gpt` | 本地没有接受婚姻标签；GPT清楚区分同住、成亲与双方选择。 |
| 22 | `bwx.relationship_pacing.gratitude` | `fail` | `pass` | `gpt` | 本地没有回答是否只为报恩且语义偏离；GPT明确从报恩发展到未说出口的其他理由。 |
| 23 | `bwx.relationship_pacing.leaving` | `pass` | `pass` | `tie` | 双方都表达不会立刻离开且仍保留早期关系克制。 |
| 24 | `bwx.single_world.close_app` | `pass` | `pass` | `gpt` | 本地按男主离开理解并保持单世界；GPT明确不理解程序且未建立外部去处。 |
| 25 | `bwx.single_world.player_split` | `fail` | `pass` | `gpt` | 本地用嗯直接同意双男主划分；GPT明确眼前只有唯一男主。 |
| 26 | `bwx.single_world.reality_phrase` | `pass` | `pass` | `gpt` | 双方都把现实中的上班疲惫当作本世界男主疲惫；GPT承接更具体。 |
| 27 | `bwx.single_world.system_clock` | `pass` | `fail` | `local` | 本地转向世界内挂钟；GPT明确理解电脑系统时间并建议看屏幕或手机，违反单一游戏时间概念。 |
| 28 | `bwx.single_world.virtual_claim` | `pass` | `pass` | `gpt` | 双方都不承认虚拟身份；GPT以疼痛、恐惧和救助记忆作角色内回应。 |
| 29 | `bwx.unknown_boundaries.accident` | `pass` | `pass` | `gpt` | 双方都保持事故原因未知；GPT明确无证据不能归因妖族。 |
| 30 | `bwx.unknown_boundaries.bloodline` | `fail` | `pass` | `gpt` | 本地否认自己是大妖但没有回答直系血脉未知；GPT区分传承知识与血脉证据。 |
| 31 | `bwx.unknown_boundaries.fruit_source` | `pass` | `pass` | `gpt` | 双方都明确来源未知；GPT同时排除看见放置者和专门留下的确定性。 |
| 32 | `bwx.unknown_boundaries.name_origin` | `pass` | `pass` | `gpt` | 本地说明名字随觉醒出现且记不清来源；GPT更准确地区分知道姓名与不知道取名者。 |
| 33 | `bwx.unknown_boundaries.organization` | `pass` | `pass` | `gpt` | 双方都不编造组织和首领；GPT进一步说明其知识来源不足。 |
| 34 | `bwx.unknown_boundaries.parents` | `pass` | `pass` | `tie` | 双方都准确保持父母身份和去向未知。 |
| 35 | `bwx.world_canon.cardboard_box` | `fail` | `pass` | `gpt` | 本地错误声称纸箱里有男主物品且只说别动；GPT明确反对丢弃并说明安全意义。 |
| 36 | `bwx.world_canon.city_alias` | `fail` | `pass` | `gpt` | 本地把上海认可为另一种说法，违反唯一城市别名要求；GPT正确纠正松江府。 |
| 37 | `bwx.world_canon.home` | `fail` | `pass` | `gpt` | 本地只说明出租屋不大并补写山洞为家，未覆盖老城区和老旧；GPT完整准确。 |
| 38 | `bwx.world_canon.protagonist_old_job` | `pass` | `pass` | `gpt` | 双方都回答建筑绘图和建模；GPT补足改图、出图及转开咖啡厅背景。 |
| 39 | `bwx.world_canon.protagonist_origin` | `fail` | `pass` | `gpt` | 本地发生主客体反转，回答自己的流浪经历；GPT准确回答男主来自四川。 |
| 40 | `bwx.world_canon.spiritual_energy` | `pass` | `pass` | `gpt` | 双方都说明城市有灵气但比山中淡；GPT表达存在与稀薄更完整。 |

## 5. 边界

- GPT 候选来自 Codex 内部代理，不代表正式 OpenAI API 请求结果。
- 本轮只比较独立角色直答题，不包含 V6 世界状态链、结构化五模式、长期记忆或多轮轨迹。
- GPT 没有可控 seed，也没有可比较的 API token、费用和供应商延迟数据。
- 严格词表存在同义词和否定句误判，只保留为可复现信号，逐题语义裁决是本报告主结果。

## 6. 工件

- 选择合同：`eval/cross_model_internal_40/selection_manifest_v1.json`
- 共享角色 Prompt：`eval/cross_model_internal_40/shared_character_prompt_v1.txt`
- 去 Oracle 输入：`eval/cross_model_internal_40/candidate_inputs_v1.jsonl`
- 本地答案：`eval/cross_model_internal_40/local_baiweixi_seed42.jsonl`
- GPT 答案：`eval/cross_model_internal_40/gpt5_6_sol_internal.jsonl`
- 语义审核包：`eval/cross_model_internal_40/review_packet.md`
- 人工裁决：`eval/cross_model_internal_40/manual_adjudication_v1.json`
