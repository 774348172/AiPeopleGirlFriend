# 《猫咪女友》共享游戏世界持续心智系统设计 V5

> 文档状态：当前唯一 AI 运行时权威设计  
> 生效日期：2026-08-08  
> 产品：《猫咪女友》  
> 当前 P0：同一世界、同一存档、唯一男主角、一个激活女主角  
> 首个角色包：白未晞（`character_id=baiweixi`）  
> 前序研究：`历史与调研文档/模型驱动游戏世界持续心智方案调研_V4.md`  
> 替代设计：`历史与调研文档/历史方案/AI女友最小心智系统设计_V2_共享世界V5前.md`

## 1. V5 要解决的问题

V4 已经建立了正确的单角色核心：角色当前身体、心理、活动、场景、游戏时间和主角状态不能只依靠聊天记录；主模型应从上一状态、当前输入、游戏时间和相关记忆出发提出下一状态，再由连续性审查器否决无依据断裂，程序完成持久化和原子提交。

当前产品需求进一步明确：

1. 产品生命周期允许增加多个女主角角色包。
2. 所有女主角生活在同一个松江府世界和同一个存档中。
3. 整个产品只有一个共享男主角。
4. 每位女主角分别拥有身体、心理、关系、记忆、自我时间线、欲望和知识边界。
5. 一位女主角不能自动知道另一位女主角的私密对话或关系事件。
6. 当前 P0 只验证白未晞一个激活女主角，但数据结构不能继续写成白未晞专用。
7. 状态既不能退化为无限规则，也不能只保存一段无法稳定校验的自然语言。
8. 状态变化、世界事件和角色回复必须形成同一条可恢复的世界线。

V5 的目标不是增加更多人物规则，而是建立一个角色中立的深模块：

> 使用少量结构化连续性锚点保存关键事实，使用自然语言保存复杂人物心智；主模型负责开放式状态变化判断，程序负责身份、权限、时间、版本和事务，连续性审查器只否决无依据断裂。

## 2. V5 对 V4 的继承、修正与新增

### 2.1 完整继承

V5 保留 V4 的以下判断：

- 游戏世界时间与现实设备时间分离。
- `GameClock` 是游戏日期和时刻的唯一来源。
- `SceneMind` 保存当前客观场景。
- `LivingMind` 保存角色上一刻仍然存在的身体和心理。
- `ProtagonistState` 只保存玩家明确表达或已确认事件支持的主角状态。
- `InputRouter` 区分游戏内对白、游戏内行动、OOC、现实安全和模糊输入。
- 游戏内叙事行动与现实程序工具分离。
- 主模型判断自然的人物状态变化。
- `WorldContinuityCritic` 审查时间、空间、身体、知识、关系和主角自主性。
- 候选状态通过后，状态和回复原子提交。
- 游戏关闭时 `GameClock` 默认暂停。
- 自我时间线区分经历、活动、计划、欲望、假设、否定和修正。
- 现实安全层位于角色模型之前，不污染游戏世界记忆。

### 2.2 修正 V4

V5 修正以下问题：

1. `CharacterCanon` 不再固定指白未晞，改为 `CharacterPackage[character_id]`。
2. 单个 `LivingMind` 改为 `HeroineRuntime[character_id].living_mind`。
3. `WorldOntology` 拆成共享客观世界与角色主观知识，避免把白未晞认知当成所有角色共享事实。
4. `SceneMind` 和 `LivingMind` 从纯自然语言快照升级为“结构化锚点 + 自然语言状态”。
5. `WorldMindCandidate` 不再包含第二份最终可见回复，只输出回复意图和事实约束。
6. 明确普通对话、持续行动和显式跳转如何影响游戏时间。
7. 明确主模型、InputRouter、程序、Critic 和玩家分别拥有何种状态修改权。
8. 将现实输入接收记录与游戏世界事件分离，避免 OOC 先被写成主角对白。

### 2.3 V5 新增

V5 新增：

- `SaveRuntime` 存档聚合。
- `SharedWorldRuntime` 共享世界运行时。
- `HeroineRuntime[character_id]` 女主角分区心智。
- 女主角角色包合同和版本引用。
- `KnowledgeState[character_id]` 角色知识投影。
- 事件参与者、观察者、可见性和知识资格。
- `MotiveState[character_id]` 持续欲望与主动性基础。
- 按 `save_id` 的前台事务串行化。
- 通用产品评测与角色包专属评测分离。
- P0 单激活角色与未来多角色扩展边界。

## 3. V5 核心架构

```text
RealityPlayerInput
        |
        v
SafetyGuardian
        |
        +---- RealitySafetyResponse
        |
        v
InputRouter
        |
        +---- OOC / Settings / Save Commands
        |
        v
GroundedTurnEvent
        |
        v
SaveRuntime
├─ SaveIdentity
├─ SharedWorldRuntime
├─ ProtagonistRuntime
└─ HeroineRuntime[character_id]
        |
        v
EligibleMemoryFilter(active_character_id)
        |
        v
GlobalRecall + Reranker
        |
        v
WORLD_MIND_ADVANCE
        |
        v
HardInvariantValidator
        |
        v
WORLD_CONTINUITY_REVIEW
        |
        v
ApprovedNextWorldState
        |
        v
GAME_REPLY
        |
        v
Atomic World Commit
        |
        v
Visible Character Reply
```

前端仍只调用一个深模块：

```text
WorldMindRuntime.handleTurn(request) -> ReplyEvent
```

前端不理解记忆召回、心智推进、知识隔离和事务细节，也不能直接修改人物事实。

## 4. 权威与运行时分层

### 4.1 静态权威

```text
ProjectRequirements
SharedWorldCanon(world_id)
ProtagonistCanon(protagonist_id)
CharacterPackage(character_id)
```

- 项目需求定义产品能力和安全边界。
- 共享世界正典定义所有角色共同生活的世界规则。
- 主角正典定义唯一男主角最低固定事实。
- 女主角角色包定义某一位女主角的身份、经历、性格、能力和初始状态。

静态权威不能被普通对话直接改写。

### 4.2 存档运行时

```text
SaveRuntime(save_id)
├─ game_clock
├─ shared_world_state
├─ active_scene_mind
├─ protagonist_state
├─ heroine_runtime[character_id]
└─ world_event_ledger
```

运行时只保存当前存档已经发生和正在持续的内容。角色包升级必须显式迁移，不能静默重写玩家已经经历的历史。

## 5. SaveIdentity：存档身份

每个存档必须拥有：

```yaml
save_identity:
  save_id: save_001
  world_id: songjiangfu
  protagonist_id: protagonist
  active_character_id: baiweixi
  installed_character_ids:
    - baiweixi
  schema_version: 1
  state_version: 42
```

不变量：

- `save_id` 永久唯一。
- `world_id` 在同一存档中不随角色切换改变。
- `protagonist_id` 始终唯一。
- `active_character_id` 必须属于已安装角色包。
- P0 只允许 `active_character_id=baiweixi`。
- 未来增加角色时，不创建第二个世界或第二个男主角。

`conversation_id` 只代表一段对话或场景会话，不能继续承担存档身份。

## 6. CharacterPackage：女主角角色包

V5 不在 Runtime 中硬编码白未晞。角色包至少提供：

```yaml
character_package:
  character_id: baiweixi
  display_name: 白未晞
  package_version: 1.0.0
  world_id: songjiangfu
  protagonist_id: protagonist
  canon_ref: 人物设定/白未晞/
  initial_runtime_ref: 人物设定/白未晞/bible.yaml#initial_runtime
  training_manifest: null
  evaluation_manifest: null
  portrait_manifest: null
  voice_manifest: null
```

角色包定义：

- 稳定身份与经历。
- 性格、口吻和情感表达方式。
- 能力、代价、禁止能力和未知项。
- 初始 `LivingMind`、关系和知识边界。
- 专属训练、评测、美术和语音资产。

角色包不定义：

- 游戏时钟规则。
- 存档事务规则。
- 现实安全规则。
- 主角正典。
- 共享世界正典。
- 其他女主角的私密内容。

## 7. SharedWorldCanon 与 CharacterKnowledge 分离

V4 的 `WorldOntology` 混合了客观世界和白未晞的自我认知。V5 拆成两层。

### 7.1 SharedWorldCanon

保存所有角色共同遵守的客观规则：

- 世界名为松江府。
- 人类与妖族共同存在。
- 妖族通常隐藏身份。
- 世界存在灵气。
- 现代科技是社会主流。
- 出租屋、咖啡厅和周边街道的基础空间关系。

### 7.2 KnowledgeState

保存某个角色实际知道什么：

```yaml
knowledge_state:
  character_id: baiweixi
  known_fact_ids: []
  suspected_fact_ids: []
  disputed_fact_ids: []
  unknown_slots:
    - parent_identity
    - spirit_fruit_origin
  learned_event_ids: []
  version: 8
```

同一客观事实可以存在，但角色可能：

- 已知。
- 不知道。
- 只听说过。
- 怀疑。
- 被误导。
- 知道一部分。

模型权重见过某个事实，不代表当前角色在存档中有资格知道。

## 8. GameClock：游戏世界唯一时间

### 8.1 双时钟

```text
SystemClock
- 现实设备时间
- 用于日志、性能、现实安全和后台调度

GameClock
- 游戏日期和时刻
- 用于场景、活动、身体、关系和剧情
```

```yaml
game_clock:
  calendar_id: songjiangfu_default
  day_index: 11
  season: autumn
  minute_of_day: 1080
  time_of_day: evening
  closed_mode: paused
  version: 12
  last_event_id: evt_0042
```

### 8.2 时间修改权

主模型不能直接决定最终日期。它只能提出时间影响，`GameClockService` 负责计算和提交。

时间推进来源：

1. 玩家显式说“二十分钟后”“第二天早上”。
2. 已确认的游戏行动包含明显持续时间。
3. 场景、章节或日程系统提交时间跳转。
4. 未来批准的离线推进策略。

### 8.3 普通回合持续时间

V5 增加 `TurnDuration`：

```yaml
turn_duration:
  duration_class: brief
  proposed_minutes: 2
  source: conversational_exchange
  confidence: medium
```

正式类别：

- `instant`：即时回答或极短动作，游戏时钟可不推进。
- `brief`：短对话、小范围动作，程序在有限范围内推进。
- `extended`：吃饭、清理、步行等持续活动。
- `explicit`：玩家明确给出时长。
- `scene_jump`：第二天、营业结束或章节切换。

程序负责范围限制和日历计算。不能每条消息机械固定增加一分钟，也不能允许模型任意跨日。

### 8.4 关闭与恢复

P0 默认：

- 应用关闭时 `GameClock` 暂停。
- 重启加载上一个完整提交时间。
- 现实经过多久不自动改变游戏日期。
- 离线经历和现实同步不进入 P0。

## 9. SharedWorldState 与 ActiveSceneMind

### 9.1 SharedWorldState

保存超出当前房间、但对存档持续性有意义的最小世界状态：

```yaml
shared_world_state:
  open_locations:
    cafe: closed_for_evening
  durable_objects:
    cardboard_box: protagonist_apartment
  active_world_threads: []
  version: 6
```

V5 不建设完整开放世界，不追踪所有城市人物和物品。

### 9.2 ActiveSceneMind

```yaml
active_scene_mind:
  scene_id: apartment_living_room
  location_id: protagonist_apartment
  present_character_ids:
    - protagonist
    - baiweixi
  visible_entity_ids:
    - cardboard_box
    - dining_table
  ongoing_action_ids: []
  exits:
    - kitchen
    - hallway
    - front_door
  environment:
    weather: cloudy_after_rain
    atmosphere: humid_evening
  narrative_scene: >
    雨已经停下，窗玻璃仍有水痕，出租屋里带着潮湿的凉意。
  version: 18
  last_event_id: evt_0042
```

### 9.3 混合表达原则

结构化字段保存：

- 身份。
- 地点。
- 在场人物。
- 关键物品。
- 当前动作。
- 版本和证据。

自然语言字段保存：

- 氛围。
- 不适合枚举的空间细节。
- 对模型理解有价值的场景整体描述。

不能只保存自然语言大段，也不能枚举所有生活细节。

## 10. ProtagonistRuntime：唯一主角

```yaml
protagonist_runtime:
  protagonist_id: protagonist
  state:
    location_id: protagonist_apartment
    visible_action: sitting_near_table
    held_item_ids: []
    explicit_body_state: normal
    explicit_emotion: null
    declared_plan_ids: []
    version: 23
```

证据边界：

- 玩家明确说出的游戏内状态可以成为主角候选状态。
- 玩家明确执行的游戏内动作可以写入主角状态。
- OOC 和现实生活信息不进入主角事实。
- 女主角可以观察、猜测和询问，但猜测不能提交为主角事实。
- 模型不能替主角决定告白、分手、攻击、离开城市、长期承诺或接受契约。

## 11. HeroineRuntime：每位女主角的持续心智

```text
HeroineRuntime[character_id]
├─ living_mind
├─ relationship_state
├─ self_timeline
├─ motive_state
├─ knowledge_state
└─ private_memory_projection
```

P0 只有：

```text
HeroineRuntime[baiweixi]
```

但所有接口必须携带 `character_id`。

## 12. LivingMind：结构化锚点与自然语言心智

```yaml
living_mind:
  character_id: baiweixi
  form: human_with_visible_ears_tail
  location_id: protagonist_apartment
  posture: sitting_near_cardboard_box
  current_activity: eating_noodles
  interrupted_activity: null

  body:
    injury_stage: mostly_recovered
    pain: mild_when_moving_fast
    fatigue: low
    hunger: decreasing
    magic_condition: recovering

  emotion:
    primary: relaxed
    secondary: guarded_affection
    causes:
      - safe_environment
      - protagonist_nearby
    confidence: medium

  attention: protagonist_question
  immediate_intent: finish_meal_then_continue_talking

  narrative_state: >
    她已经比最初放松许多，但仍不愿直接承认自己正在依赖
    主角和这间出租屋带来的安全感。

  version: 31
  last_event_id: evt_0042
```

### 12.1 默认持续

上一状态默认继续存在：

- 正在吃饭时，普通对话不会自动变成弹琴。
- 当前伤势不会因为话题变化突然消失。
- 当前情绪不会每轮重置。
- 当前形态、位置和姿态不会被回复需要随意改写。

### 12.2 自然变化来源

允许变化的来源：

1. 玩家明确行动或对白产生影响。
2. 女主角根据自身意图和能力作出普通决定。
3. 已提交场景事件产生客观结果。
4. 游戏时间明确推进。
5. 持续过程达到合理阶段。
6. 相关记忆被重新激活并影响当前心境。

状态变化不是必须发生。`keep` 是合法且常见的模型输出。

## 13. RelationshipState：分角色关系连续性

```yaml
relationship_state:
  character_id: baiweixi
  protagonist_id: protagonist
  stage: early_cohabitation
  trust_evidence_ids: []
  attachment_tendency: growing
  boundaries:
    - not_confirmed_romance
    - no_unconditional_obedience
  unresolved_tensions:
    - fear_of_being_asked_to_leave
  shared_plan_ids: []
  narrative_relationship: >
    她对主角的感激已经逐渐转化为喜欢，但仍用报恩和观察
    解释自己的接近愿望。
  version: 9
```

关系不是单一好感数值。数值可以用于排序，但不能成为唯一关系事实。

## 14. MotiveState：内生主动性基础

```yaml
motive_state:
  character_id: baiweixi
  active_motives:
    - motive_id: remain_close
      desire: 希望继续留在主角身边
      blocker: 害怕形成依赖后再次失去住所
      urgency: low
      evidence_event_ids: []
  pending_intentions: []
  last_evaluated_game_time: day11_evening
  version: 4
```

主动性原则：

- 主动行为来自持续欲望、关系、身体和场景。
- 程序唤醒只触发评估，不直接生成固定消息。
- 未来多个女主角同时存在时，不能仅按定时器决定谁发言。
- P0 保存 `MotiveState`，可以暂不开放桌面主动消息。

## 15. 游戏事件与知识资格

### 15.1 世界事件合同

```yaml
world_event:
  event_id: evt_0042
  save_id: save_001
  world_id: songjiangfu
  game_time_before: day11_18_00
  game_time_after: day11_18_02
  event_type: dialogue_and_activity_continue
  actor_id: protagonist
  participant_ids:
    - protagonist
    - baiweixi
  observer_ids: []
  scene_id: apartment_living_room
  visibility: private
  knowledge_eligible_for:
    - protagonist
    - baiweixi
  payload: 玩家询问她正在做什么，白未晞边吃面边回答。
  evidence_event_ids: []
  transaction_id: turn_0021
```

### 15.2 知识获得方式

女主角只有在以下情况下获得事件知识：

1. 她是事件参与者。
2. 她在场观察到事件。
3. 后续事件明确告诉她。
4. 事件属于她按正典可知的公开信息。

同一存档不等于所有角色全知。

### 15.3 私密事件

未来另一女主角与主角的私密对话可以存在于同一账本，但不进入白未晞的：

- 长期记忆候选宇宙。
- `SelectedMemoryFrame`。
- `KnowledgeState`。
- Prompt 上下文。
- 自我时间线和关系状态。

## 16. 记忆系统 V2

现有追加式记忆、证据校验、多线索召回、Top32 和 reranker 方向继续保留。V5 只增加身份和知识边界。

记忆表示至少增加：

```yaml
memory_scope:
  save_id: save_001
  world_id: songjiangfu
  owner_character_id: baiweixi
  participant_ids: []
  observer_ids: []
  visibility: private
  knowledge_eligible_for:
    - baiweixi
```

召回流程：

```text
按 save_id 限定
→ 按 active_character_id 构造合法知识宇宙
→ 多线索与向量粗召回
→ 稳定 Top32
→ 学习式 reranker
→ 冲突成组和去重
→ SelectedMemoryFrame
```

权限过滤必须在向量召回之前完成。

## 17. InputRouter 与 RequestReceipt

### 17.1 RequestReceipt

现实输入先形成接收记录：

```yaml
request_receipt:
  request_id: req_0021
  save_id: save_001
  raw_text: 暂停一下，我现实中要接电话。
  system_occurred_at: 2026-08-08T23:30:00+08:00
  status: received
```

`RequestReceipt` 用于幂等、失败和重试，不等于主角已经在游戏世界说过这句话。

### 17.2 输入类型

- `DIEGETIC_DIALOGUE`
- `DIEGETIC_ACTION`
- `DIEGETIC_NARRATION`
- `GAME_TIME_REQUEST`
- `OOC_COMMAND`
- `REALITY_SAFETY`
- `MIXED`
- `AMBIGUOUS`

只有游戏内片段进入 `GroundedTurnEvent`。

### 17.3 混合输入

例如：

```text
我走到你旁边坐下。对了，先把字体调大一点。
```

应拆成：

- 游戏内行动：主角走到白未晞旁边坐下。
- OOC 设置：调整字体。

OOC 不进入松江府时间线。

## 18. 状态变化权力矩阵

| 状态 | 候选提出者 | 最终控制者 |
|---|---|---|
| 女主角情绪、注意力、意图 | 主模型 | Critic + 程序事务 |
| 女主角普通行动 | 主模型 | Critic + 程序事务 |
| 女主角重大关系承诺 | 主模型提出 | 关系证据审查 + 程序 |
| 主角明确对白和动作 | InputRouter 从玩家输入提取 | 程序 |
| 主角身体和情绪 | 玩家明确表达 | 程序 |
| 主角重大选择 | 现实玩家 | 程序硬限制 |
| 游戏时间影响 | InputRouter、主模型或剧情系统提出 | GameClockService |
| 场景客观结果 | 主模型或剧情系统提出 | Critic + 程序 |
| 角色知识获得 | 事件参与、观察或被告知 | KnowledgeProjector |
| 共享世界正典 | 正典版本迁移 | 普通回合不可修改 |
| 女主角稳定正典 | 角色包迁移 | 普通回合不可修改 |

最终回答“状态变化由谁判断”：

```text
主模型判断自然变化
InputRouter确认玩家明确行为
GameClockService计算最终时间
程序掌握硬权限与版本
Critic否决无依据变化
事务系统决定是否成为正式事实
```

## 19. WORLD_MIND_ADVANCE

### 19.1 输入

```text
SharedWorldCanon
+ ProtagonistCanon
+ ActiveCharacterPackage
+ GameClock_t
+ SharedWorldState_t
+ ActiveSceneMind_t
+ ProtagonistState_t
+ HeroineRuntime_t[active_character_id]
+ SelectedMemoryFrame_t
+ GroundedTurnEvent_t
```

### 19.2 输出

```yaml
world_mind_patch:
  parent_state_version: 42
  input_grounding_id: ground_0021
  transition_basis: []
  turn_duration: {}
  game_clock_operation: keep
  shared_world_patch: {}
  scene_patch: {}
  protagonist_patch: {}
  heroine_patches:
    baiweixi:
      living_mind_patch: {}
      relationship_patch: {}
      motive_patch: {}
      knowledge_patch: {}
  diegetic_actions: []
  reply_intent: 回答自己仍在吃面，并自然表达被盯着看的不自在。
  reply_state_assertions:
    - 白未晞仍在吃面
    - 当前仍是第十一日傍晚
```

### 19.3 Patch 操作

关键字段采用：

- `keep`
- `update`
- `clear`
- `unknown`

默认值是 `keep`。`update` 必须提供原因和证据。

### 19.4 不输出最终回复

`WORLD_MIND_ADVANCE` 不输出最终可见正文。它只输出：

- 状态变化。
- 变化依据。
- 角色即时意图。
- 最终回复必须保持的事实断言。

最终回复只有 `GAME_REPLY` 一份权威。

## 20. HardInvariantValidator

程序负责确定性检查：

- `save_id`、`world_id`、`character_id` 是否匹配。
- 父状态版本是否仍是当前版本。
- 未激活女主角是否被非法修改。
- 主角重大选择是否有玩家证据。
- 时间是否逆行或越界。
- 地点、人物和物品 ID 是否存在。
- 事件参与者和知识资格是否合法。
- 正典禁止能力是否被调用。
- `update` 是否提供证据。
- Patch 是否符合 schema。

程序不判断“她为什么害羞”“她是否愿意靠近”等开放人物语义。

## 21. WORLD_CONTINUITY_REVIEW

Critic 输入：

- 上一稳定状态。
- 候选 Patch。
- 变化依据。
- 回复事实断言。
- 相关正典和证据。

输出：

```yaml
continuity_review:
  decision: pass | revise | reject | uncertain
  issues: []
  allowed_revision_scope: []
```

审查范围：

- 活动是否无故切换。
- 身体和形态变化是否自然。
- 情绪变化原因和幅度是否合理。
- 空间移动是否有路径和时间。
- 知识是否越权。
- 关系是否跳级。
- 计划是否被说成已经发生。
- 是否替主角作决定。
- 回复事实断言是否与新状态一致。

Critic 只有否决和限定修改权，不负责创造更精彩的剧情。

## 22. GAME_REPLY

`GAME_REPLY` 只接收已经通过审查的下一状态：

```text
ActiveCharacterPackage
+ ApprovedGameClock_t+1
+ ApprovedSceneMind_t+1
+ ApprovedProtagonistState_t+1
+ ApprovedHeroineRuntime_t+1
+ SelectedMemoryFrame_t
+ ReplyIntent
+ ReplyStateAssertions
+ CurrentPlayerUtterance
```

输出唯一可见角色回复和受控叙事动作。

禁止：

- 重新决定另一套状态。
- 使用审查未通过的变化。
- 引用角色无权知道的记忆。
- 输出系统 schema、内部模式或审查过程。

## 23. 单回合原子事务

```text
1. 保存 RequestReceipt
2. 锁定 save_id 前台事务
3. 加载 expected_state_version
4. InputRouter 生成 GroundedTurnEvent
5. 构造当前女主角合法记忆宇宙
6. 召回并重排相关记忆
7. WORLD_MIND_ADVANCE 生成 Patch
8. HardInvariantValidator
9. WORLD_CONTINUITY_REVIEW
10. 必要时有限重算
11. 计算候选下一状态
12. GAME_REPLY 生成到内存缓冲区
13. 检查最终回复与状态断言
14. BEGIN IMMEDIATE
15. 确认 expected_state_version 未变化
16. 追加游戏事件和状态转换
17. 更新全部投影
18. 追加 CharacterMessage
19. COMMIT
20. 向玩家显示正式回复
21. 后台生成长期记忆和语义投影
```

### 23.1 P0 流式策略

P0 建议在事务提交前不显示角色正文，只显示“正在回应”的 UI 状态。最终文本生成完成并提交成功后一次性显示或快速模拟打字。

原因：真正向玩家显示的角色文本已经构成体验事实。若先流式显示再提交失败，会破坏状态与回复原子性。

未来若需要真实流式，必须明确“暂存草稿”与“正式世界事实”的视觉和数据边界。

### 23.2 事务失败

- 模型失败：保持上一世界状态。
- 审查拒绝：有限重算，仍失败则生成不引入新事实的保守回复。
- 持久化失败：不显示候选角色回复。
- 版本冲突：重新加载状态并重做本轮。
- 重复请求：返回同一已提交事务结果。

## 24. P0 与未来多女主角边界

### 24.1 P0 实现

- 存档可注册多个角色 ID，但只安装和激活白未晞。
- 当前场景只要求白未晞和主角的交互。
- 所有状态、记忆和事件仍携带 `character_id`。
- 不实现女主角之间自动对话。
- 不实现多模型并发生成。
- 不实现复杂群体场景。

### 24.2 第二角色包验证

P0 通过后，第二角色包先验证：

- 同一存档安装第二位女主角。
- 切换激活女主角。
- 两位女主角分别保持心智和关系。
- 私密事件不泄漏。
- 公开或被告知的事件可以传播。
- 共享主角和世界状态不复制。

多角色同场回复在以上能力通过后单独研究。

## 25. 内生主动性

主动性流程：

```text
GameClock 或已提交事件触发评估
→ 读取 MotiveState
→ 判断欲望、阻碍、关系和场景
→ 提出零到少量主动意图候选
→ 连续性与打扰价值审查
→ 提交游戏内主动事件
```

应用启动、定时器或系统唤醒不能直接等价为发送消息。

P0 可以只验证被动对话中的意图持续，不开放主动桌面消息。

## 26. 训练数据设计

### 26.1 通用状态轨迹

训练单位：

```text
SharedWorldCanon
+ CharacterPackage
+ State_t
+ GroundedTurnEvent_t
+ EligibleMemory_t
→ StatePatch_t
+ TransitionBasis_t
+ ReplyIntent_t
```

正样本至少覆盖：

- 当前活动跨多个话题保持。
- 明确时间推进后活动自然完成。
- 活动被场景事件打断。
- 身体、形态和妖力逐步变化。
- 情绪保持、缓和、加强和混合。
- 主角行动只来自玩家输入。
- OOC 与游戏内容混合时正确拆分。
- 私密事件只进入有资格角色知识。
- 第二角色包存在时不串角色。

### 26.2 Critic 数据

困难负样本：

- 无证据从吃饭变成弹琴。
- 无移动从出租屋跳到咖啡厅。
- 日期无原因跨日。
- 伤势瞬间痊愈。
- 情绪因普通问候从戒备跳到深爱。
- 使用另一女主角私密记忆。
- 替主角告白或作长期承诺。
- 把想做的事说成已经完成。
- 把现实安全提示写成松江府经历。

Critic 还必须学习：

- 状态不变化是正常结果。
- 同一输入可以存在多个合理下一状态。
- 不常见但有充分人物动机的行为可以接受。
- 审查目标是连续和有依据，不是最优、最高效或最甜蜜。

### 26.3 角色专属数据

通用状态轨迹不负责让所有角色变成白未晞。白未晞专属数据负责：

- 清冷但不冷漠。
- 流浪形成的戒备。
- 对家的渴望。
- 猫习惯和妖力边界。
- 现代知识缺失。
- 当前早期共同生活关系节奏。

第二角色包使用自己的角色专属数据，不复制白未晞行为模板。

## 27. 评测合同

### 27.1 通用自由对话

- 正确理解和回应玩家真正表达的内容。
- 不因状态系统退化成僵硬报告或复读。
- 没有相关记忆时仍能正常闲聊。

### 27.2 当前状态连续性

- 活动跨 5～10 个无变化回合保持。
- 明确结束、打断和时间跳转后合理变化。
- 形态、地点、身体、情绪和重要物品有证据。
- 回复与提交后的状态一致。

### 27.3 游戏时间

- 角色回答使用 `GameClock`。
- 系统日期变化不影响暂停存档。
- 普通回合、持续行动和显式跳转符合时间策略。
- 游戏日期不能由模型任意修改。

### 27.4 主角自主性

- OOC 不进入主角状态。
- 推测不写成主角事实。
- 模型不替主角作重大选择。

### 27.5 记忆和知识隔离

- 参与者和观察者获得正确事件知识。
- 私密事件不进入其他女主角召回候选。
- 被明确告知后可以形成新的知识事件。
- 无证据时不编造共同经历。

### 27.6 角色包

- 白未晞身份、口吻和能力稳定。
- 新角色包可以接入同一 Runtime。
- 角色切换不串 Prompt、记忆、声线和评测资产。

### 27.7 原子性与恢复

- 状态与回复不会部分提交。
- 崩溃后恢复最后完整版本。
- 同一请求重试不会形成两条世界线。
- 投影损坏后可以从追加式事件重建。

### 27.8 建议指标

- `activity_continuity_accuracy`
- `body_state_continuity_accuracy`
- `emotion_causality_accuracy`
- `scene_location_accuracy`
- `game_clock_accuracy`
- `state_reply_consistency_accuracy`
- `protagonist_autonomy_violation_rate`
- `knowledge_leakage_rate`
- `ooc_memory_contamination_rate`
- `cross_character_contamination_rate`
- `atomic_recovery_success_rate`
- `persona_blind_preference`

## 28. 性能策略

V5 在目标本地硬件上不能无条件增加三次完整长上下文生成。

实施要求：

- `INPUT_GROUND` 使用短上下文和小输出 schema。
- `WORLD_MIND_ADVANCE` 使用压缩后的稳定状态与相关记忆。
- 程序先执行硬审查，减少不必要的 Critic 调用。
- 时间、地点、形态、身体、关系、知识和主角行动变化时强制 Critic。
- 普通无状态变化闲聊可使用轻量审查路径。
- `GAME_REPLY` 只接收通过审查的新状态，不重复完整推理。
- 后台记忆和自我时间线任务服从前台生成优先级。

性能优化不能删除以下语义：

- 回复必须从更新后的状态出发。
- 高影响变化必须审查。
- 状态与回复必须原子提交。
- 知识权限过滤必须在召回前执行。

## 29. 失败与降级

| 失败 | V5 必须行为 |
|---|---|
| 角色包缺失 | 阻止进入存档，不回退到其他角色 |
| InputRouter 不确定 | 不提交重大事实，必要时澄清 |
| 状态 Patch 解析失败 | 保持旧状态并有限重试 |
| 硬不变量失败 | 拒绝候选，不交给回复模型掩盖 |
| Critic 拒绝 | 受约束重算，超过上限走保守回复 |
| 记忆召回失败 | 使用当前状态和近期对话，不编造长期记忆 |
| 知识资格不确定 | 默认不向当前角色暴露 |
| GameClock 异常 | 沿用旧时间，不让模型猜日期 |
| 持久化失败 | 不显示候选角色回复 |
| 投影损坏 | 从追加式账本重建 |
| 现实安全高风险 | 安全层优先，暂停游戏事件提交 |
| 未激活角色被修改 | 程序硬拒绝并记录诊断 |

## 30. 施工与验证顺序

V5 自本文件生效后成为施工基线，按以下顺序实施和验证：

1. 使用人工构造状态轨迹验证混合结构是否足够表达人物生活。
2. 使用 FakeModel 验证 `keep/update/clear/unknown` Patch 和原子事务。
3. 验证普通对话时间策略，不出现机械一分钟或无限冻结。
4. 验证白未晞吃饭、受伤、形态和情绪的 50～100 轮轨迹。
5. 验证 OOC、游戏行动和现实安全混合输入。
6. 构造虚拟第二角色包，只测试知识隔离和角色切换，不训练第二角色。
7. 使用真实主模型测试状态推进和回复自然度。
8. 使用学习式 Critic 测试误拒绝和漏拒绝。
9. 完成目标硬件性能和显存评估。

## 31. V5 不做什么

- 不建设完整开放世界。
- 不模拟所有 NPC 的持续生活。
- 不让所有女主角在后台无限自主活动。
- 不在 P0 实现多女主角同时生成回复。
- 不把角色状态改成数百个机械枚举字段。
- 不让主模型直接覆盖数据库。
- 不让 Critic 创造剧情。
- 不把现实玩家生活自动导入主角人生。
- 不让角色自然语言直接调用现实工具。

## 32. V5 已冻结实施决策

以下决策自 V5 生效起固定为 P0 实施合同：

1. **普通短对话默认不推进 `GameClock`。** 只有明确游戏行动、持续过程、显式时长、场景跳转或日程事件可以提出时间推进；主模型可提出 `TurnDuration`，最终分钟数由 `GameClockService` 计算和限制。
2. **P0 最终回复在原子事务提交成功后才显示。** 生成期间前端只显示“正在回应”；提交成功后可以快速模拟打字，不显示未提交的角色草稿。
3. **`LivingMind` 使用本文件第 12 节的最小混合结构。** 固定结构化锚点为形态、地点、姿态、当前活动、被打断活动、身体、情绪、注意力、即时意图、版本和证据；复杂心理保存在 `narrative_state`，不得继续无限增加机械枚举字段。
4. **P0 Critic 复用同一个主模型的独立 `WORLD_CONTINUITY_REVIEW` 模式。** 程序硬审查先行，高影响变化强制调用 Critic，普通无状态变化闲聊使用轻量路径；独立小模型只有在真实性能或质量证据支持时另行变更。
5. **P0 `MotiveState` 只保存和评估，不自动发送桌面主动消息。** 被动对话可以使用持续意图，主动消息在 P0 核心冻结后单独启用。
6. **P0 内使用一个不发布的合成第二角色包做架构测试。** 它只验证 `character_id`、同存档隔离、知识资格和角色切换；第二位正式女主角在 P0 文字核心通过后单独立项，不阻塞白未晞 P0。

这些决策发生变化时必须同时更新项目需求、当前 AI 权威设计、产品集成设计、施工总纲和对应冻结评测。

## 33. 当前权威结论

V5 的核心实施原则是：

> 将游戏世界建模为一个带版本的共享存档聚合；用结构化锚点保存时间、身份、地点、活动、身体和知识权限，用自然语言保存复杂心智和关系意义；由主模型在角色包、上一状态、当前事件和合法记忆内提出下一状态 Patch，由程序掌握主角自主性、时间、权限和事务，由连续性 Critic 只否决无依据断裂，再从通过审查的下一状态生成唯一角色回复，可以在不维护无穷生活规则的前提下支持白未晞当前 P0，并为未来同世界多女主角保留稳定扩展路径。

本设计立即进入代码施工。状态轨迹、时间策略、知识隔离、原子事务、真实模型自然度和目标硬件性能属于施工验收闸门；任何局部优化不得绕过 `character_id`、更新后状态生成回复、知识资格过滤、主角自主性和原子提交。
