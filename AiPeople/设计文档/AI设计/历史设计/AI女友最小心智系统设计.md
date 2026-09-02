> 【归档声明 2026-08-27】本文档所属的旧 AI 管线设计已被《Harness组装与单次判断设计_20260827.md》取代，归档至本目录。仅作历史追溯，不作为现行施工依据；现行权威见「当前权威设计」目录。

# 《猫咪女友》实时游戏世界持续心智系统设计 V6

> 文档性质：当前唯一 AI 运行时权威设计  
> 生效日期：2026-08-09  
> 当前产品：《猫咪女友》  
> P0 激活女主角：白未晞，`character_id=baiweixi`  
> 替代版本：V5 及此前所有持续心智方案  
> V5 归档：`历史与调研文档/历史方案/AI女友最小心智系统设计_V5_实时世界V6前.md`
> 2026-08-13 协议修正：废止合并式 `short_semantic_v1 / M1` 前台合同，改为 `MIND_PATCH_V2 / M2 -> ApprovedHeroineState -> GAME_REPLY`
> 2026-08-14 输入修正：前台模型只接收角色包短锚与职责所需的确定性最小语义投影；完整 Runtime 对象与程序元数据保留在调用外。

## 1. V6 要解决的问题

V6 建立在以下已经确认的产品事实之上：

1. 对女主角而言只存在松江府这一个世界，不存在“现实世界”和“游戏世界”的双世界认知。
2. 玩家在角色聊天入口输入的任何文字，唯一语义都是游戏世界男主角说出的一句话。
3. 男主角的位置、动作、身体和其他当前状态由游戏程序提供，女主角只读取，不自行推算或修改。
4. 游戏世界由程序持续实时运行；位置、动作、物体、场景和时间不会等待聊天或五分钟任务才变化。
5. `GameClock` 连续运行，女主每次回复前读取的都是当时最新游戏时间。
6. 每次回复前只获取一次最新完整世界快照，并在本轮生成期间冻结该观察结果；实时世界本身不暂停。
7. 每位女主角拥有自己的记忆账本、语义记忆、向量索引和召回入口，互相不可查询、不可污染。
8. 每运行五分钟触发一次模型主导的世界心智整理；程序只执行极少量确定性校验。
9. 每次正式回复提交后执行一次后台整理，用于关系、记忆、自我时间线和回复后续含义，不推动时间。
10. 当前状态、历史事件、长期记忆和角色正典必须分层，不能继续把聊天记录当作人物“现在”。

V6 的目标不是建设一个由大量生活规则驱动的模拟器，而是：

> 让程序实时维护客观世界事实，让模型持续理解这些事实对人物意味着什么，并用最小硬边界保证身份、权限、版本、时间、记忆隔离和事务一致性。

## 2. V6 对 V5 的继承与替换

### 2.1 完整继承

V6 继续保留：

- `save_id`、`world_id`、`protagonist_id` 和 `character_id` 全链路身份合同。
- 同一存档、同一世界、同一男主角和未来多个女主角角色包。
- 白未晞只是首个角色包，不是 Runtime 架构本身。
- 追加式世界事件账本、工作激活区、语义投影和多线索召回。
- `LivingMind` 保存女主角当前身体、心理、活动、注意力和即时意图。
- 主模型提出开放语义变化，连续性审查模型否决无依据断裂。
- 最终回复只从已经批准的更新后状态生成。
- 回复、事件和本轮状态变化原子提交后才成为正式世界事实。
- 程序不得用无限枚举字段和生活规则替代人物理解模型。

### 2.2 V6 替换的 V5 决策

V6 废止以下 V5 设计：

1. 废止 `RealityPlayerInput`、现实输入分流和角色聊天入口中的 OOC 分类。
2. 废止“聊天输入可以同时代表对白、动作、叙述和程序命令”的合同。
3. 废止角色认知中的 `SystemClock` 与 `GameClock` 双时钟。
4. 废止普通对话、`TurnDuration` 或聊天轮数推动游戏时间的设计。
5. 废止应用运行期间等待五分钟 Tick 才改变 `GameClock` 的设计。
6. 废止从玩家文字中直接提取男主客观动作并修改男主当前状态。
7. 废止全局记忆库先搜索再按 `character_id` 过滤的召回主干。
8. 废止主要依靠程序死规则完成五分钟世界状态整理的方案。
9. 废止“回答期间整个世界停止更新”的理解。
10. 废止一次结构化调用同时生成心智 Patch、女主动作和最终回复的 `short_semantic_v1 / M1` 合同；最终对白必须回到独立的纯自然语言 `GAME_REPLY`。

### 2.3 V6 新增

V6 新增：

- `LiveWorldState`：游戏程序实时维护的客观世界事实。
- `ProtagonistLiveState`：程序权威的男主当前状态。
- `WorldStateProjection`：将引擎事实转换成模型可读语义。
- `WorldStateProvider.getLatestSnapshot()`：回复前唯一读取入口。
- `TurnWorldSnapshot`：本轮一致的冻结观察结果。
- `HeroineMemoryRepository[character_id]`：每位女主角独立记忆库。
- `POST_REPLY_WORLD_MIND_RECONCILE`：回复后的模型整理模式。
- `FIVE_MINUTE_WORLD_MIND_RECONCILE`：五分钟模型主导整理模式。
- `WorldUpdateCoordinator`：协调实时更新、回答事务和后台模型任务。
- 模型任务安全点、延迟、合并和下一轮可见性合同。

## 3. 单一游戏世界认知

### 3.1 女主角只有一个世界

女主角的世界本体中只有：

- 松江府。
- 唯一男主角。
- 当前场景中的人物、地点和物品。
- 她实际知道的世界事实。
- 她自己的经历、关系和记忆。

角色 Prompt、记忆、状态和训练数据不得向女主角提供以下概念：

- 现实玩家。
- 现实世界男主角。
- 设备系统时间。
- 游戏外地点。
- “我只是虚拟人物”。
- “我不能去现实陪你”。

如果男主说“现实中今天上班好累”，它仍然只是一句松江府世界内的男主对白。“现实中”可以被角色理解为“实际情况”“说真的”或奇怪措辞，但不能触发第二世界解释。

### 3.2 聊天入口的唯一语义

角色聊天入口合同固定为：

```yaml
player_utterance:
  speaker_id: protagonist
  text: 玩家输入原文
  request_id: req_0001
```

不再存在：

- `DIEGETIC_ACTION`
- `DIEGETIC_NARRATION`
- `OOC_COMMAND`
- `REALITY_SAFETY`
- `MIXED`
- `AMBIGUOUS`

设置、保存、退出和调试属于独立 UI 与程序接口，不进入角色聊天入口，不要求女主角理解。

### 3.3 对白不等于客观动作

玩家说：

```text
我正在画画。
```

如果程序提供的男主状态是：

```yaml
current_activity: eating_noodles
```

那么这句话只是男主说出了一项与客观状态不一致的内容。女主可以怀疑、询问或指出矛盾，但 Runtime 不得把男主活动直接改成画画。

## 4. V6 核心架构

```text
Game Engine / Program Runtime
├─ Continuous GameClock
├─ LiveWorldState
├─ ProtagonistLiveState
├─ Scene and Object State
└─ Event Stream
          |
          v
WorldStateProjection
          |
          v
ModelReadableWorldState
          |
          +-------------------------------+
          |                               |
          v                               v
Reply Request                     Background Triggers
          |                       ├─ post_reply
          v                       └─ five_minute
getLatestSnapshot()                       |
          |                               v
          v                     WorldUpdateCoordinator
TurnWorldSnapshot                         |
          |                               v
          v                     WORLD_MIND_RECONCILE
HeroineMemoryRepository                   |
          |                               v
          v                     HardInvariantValidator
MIND_PATCH_V2 / M2                        |
  ├─ Changed Heroine Fields                v
  ├─ Evidence Aliases            WORLD_CONTINUITY_REVIEW
  └─ Controlled Heroine Actions            |
          |                                v
          v                      Atomic State Commit
HardInvariantValidator
          |
          v
Conditional WORLD_CONTINUITY_REVIEW
          |
          v
ApprovedHeroineState
          |
          v
GAME_REPLY (plain natural language)
          |
          v
Reply Consistency Validation
          |
          v
Atomic Turn Commit
          |
          v
Visible Reply
```

顶层 Runtime 仍只暴露：

```text
WorldMindRuntime.handleTurn(request) -> ReplyEvent
```

前端不直接访问记忆、状态模型和数据库事务。

## 5. 存档聚合

```text
SaveRuntime(save_id)
├─ save_identity
├─ game_clock_anchor
├─ live_world_state
├─ model_readable_world_state
├─ active_scene_state
├─ protagonist_live_state
├─ world_event_ledger
├─ heroine_runtime[character_id]
└─ background_task_state
```

存档身份：

```yaml
save_identity:
  save_id: save_001
  world_id: songjiangfu
  protagonist_id: protagonist
  active_character_id: baiweixi
  installed_character_ids:
    - baiweixi
  schema_version: 2
  state_version: 42
```

不变量：

- 一个存档只有一个 `world_id`。
- 一个存档只有一个 `protagonist_id`。
- 女主角可以增加，但必须生活在同一世界和同一存档。
- P0 只激活白未晞。
- `conversation_id` 不是存档身份。

## 6. CharacterPackage

每个女主角角色包至少提供：

```yaml
character_package:
  character_id: baiweixi
  display_name: 白未晞
  package_version: 1.0.0
  world_id: songjiangfu
  protagonist_id: protagonist
  canon_ref: 人物设定/白未晞/
  initial_runtime_ref: 人物设定/白未晞/bible.yaml#initial_runtime
```

角色包定义：

- 身份和外貌。
- 经历和知识缺口。
- 性格、防御方式和关系节奏。
- 能力边界。
- 初始 `LivingMind`、关系和动机。
- 角色专属 Prompt、评测、训练、立绘和声音引用。

角色包不定义：

- 游戏时钟机制。
- 男主实时状态。
- 存档和事务规则。
- 其他女主角记忆。
- 共享世界正典。

## 7. LiveWorldState：实时客观世界

`LiveWorldState` 是游戏程序持续维护的客观事实源：

```yaml
live_world_state:
  game_time: day11_18:03:24
  active_scene_id: protagonist_apartment_dining
  protagonist:
    location_id: dining_area
    position: {x: 12.8, y: 4.2}
    posture: sitting
    current_activity: eating_noodles
    held_item_ids: [chopsticks]
  characters:
    baiweixi:
      location_id: dining_area
      visible_action: sitting_opposite_protagonist
  objects:
    noodle_bowl:
      location_id: dining_table
  version: 1281
```

### 7.1 更新频率

以下内容由程序每帧、每个引擎 Tick 或事件发生时立即更新：

- 精确位置和移动。
- 当前动作和动画事实。
- 场景进入、离开和在场名单。
- 物品位置、持有关系和交互结果。
- 游戏程序确认的身体数值或状态。
- 连续 `GameClock`。

这些变化不等待：

- 玩家聊天。
- 女主回复。
- 五分钟整理任务。
- 模型调用。

### 7.2 事件驱动主动更新

男主从 A 点走到 B 点时：

```text
Program Movement
-> LiveWorldState position update
-> arrival event
-> ProtagonistLiveState.location_id = B
-> ActiveSceneState update
-> append world event
```

女主下一次回复前直接读取 B 点的最新状态。

## 8. GameClock：连续游戏世界时间

角色世界只存在 `GameClock`。

```yaml
game_clock_anchor:
  anchor_game_time: day11_18:00:00
  running_elapsed_seconds: 204
  time_scale: 1.0
  run_mode: running
  version: 12
```

当前时间由程序计算：

```text
CurrentGameTime
= anchor_game_time
+ running_elapsed_seconds * time_scale
```

要求：

- 程序运行时，游戏时间每时每秒连续变化。
- 聊天不会推动、暂停或重置游戏时间。
- 女主回复前读取当时最新游戏时间。
- 模型不能修改、快进或倒退游戏时间。
- 不需要每秒写数据库；时间锚点和运行差值可以计算当前值。
- P0 应用关闭时暂停，重启后从最后持久化锚点继续；离线推进另行立项。
- 操作系统计时能力只是底层实现，不构成角色认知中的第二套时间。

## 9. ProtagonistLiveState：程序权威男主状态

```yaml
protagonist_live_state:
  protagonist_id: protagonist
  location_id: dining_area
  posture: sitting
  current_activity: eating_noodles
  held_item_ids:
    - chopsticks
  body:
    fatigue: medium
    hunger: decreasing
  emotion:
    explicit: null
  visible_state: 正坐在桌边吃面
  source: game_program
  version: 31
```

### 9.1 权威边界

程序最终控制：

- 男主位置。
- 男主姿态和当前活动。
- 男主持有物品。
- 程序确认的身体状态。
- 男主是否完成移动或交互。
- 场景中的客观可见状态。

模型可以：

- 让女主观察、理解、怀疑或询问。
- 根据男主客观状态改变女主自己的情绪和意图。

模型不可以：

- 修改 `ProtagonistLiveState`。
- 根据一句对白替男主完成动作。
- 替男主决定告白、离开、攻击、承诺或其他重大选择。
- 把女主猜测提交为男主事实。

## 10. WorldStateProjection

模型不直接读取每帧坐标、动画帧和所有引擎组件。`WorldStateProjection` 将程序事实转换成稳定语义：

```text
position=(12.8, 4.2)
+ scene volume=dining_area
+ animation=eat_noodle_loop
+ held item=chopsticks
-> 男主坐在餐桌边吃面
```

投影层只做程序能够确定的事实映射，不解释复杂人物心理。

```yaml
model_readable_world_state:
  game_time: day11_18:03:24
  scene:
    location_id: protagonist_apartment_dining
    present_character_ids: [protagonist, baiweixi]
    environment:
      weather: light_rain
  protagonist:
    location_id: dining_area
    posture: sitting
    current_activity: eating_noodles
  heroine_visible_fact:
    location_id: dining_area
    posture: sitting_opposite_protagonist
  version: 1281
```

## 11. TurnWorldSnapshot

每轮回复前调用：

```text
WorldStateProvider.getLatestSnapshot(save_id, active_character_id)
```

返回：

```yaml
turn_world_snapshot:
  snapshot_id: snap_0042
  captured_game_time: day11_18:03:24
  live_world_version: 1281
  mind_state_version: 42
  game_clock: {}
  shared_world_state: {}
  active_scene_state: {}
  protagonist_live_state: {}
  active_heroine_runtime: {}
  player_utterance: {}
```

规则：

- GET 时必须先读取程序已经准备好的最新状态。
- 当前回复只使用这一份快照。
- 回复生成期间实时世界继续运行。
- 生成期间发生的新变化不进入当前回复，下一轮读取时可见。
- 不允许一句回复前半段使用旧位置、后半段使用新位置。

## 12. HeroineRuntime

```text
HeroineRuntime[character_id]
├─ living_mind
├─ relationship_state
├─ self_timeline
├─ motive_state
├─ knowledge_state
└─ memory_repository_ref
```

### 12.1 LivingMind

```yaml
living_mind:
  character_id: baiweixi
  form: human_with_visible_ears_tail
  location_id: dining_area
  posture: sitting_opposite_protagonist
  current_activity: watching_protagonist_eat
  interrupted_activity: null
  body:
    injury_stage: mostly_recovered
    pain: mild_when_moving_fast
    fatigue: low
    hunger: decreasing
    magic_condition: recovering
  emotion:
    primary: relaxed
    secondary: [cautious_warmth]
    intensity: low
  attention:
    target_id: protagonist
    focus: 吃面速度有些快
  immediate_intent:
    intent: 提醒他慢一点
    status: pending
  narrative_state: >
    她已经不像最初几天那样时刻准备逃走，但仍不习惯
    直接表达关心，通常会用冷淡或简短的方式掩饰。
  evidence_event_ids: []
  version: 18
```

默认操作是 `keep`。只有存在时间、事件、对白或内部持续动机依据时，模型才能提出 `update` 或 `clear`。

### 12.2 RelationshipState

关系不是单一好感数值，至少保存：

- 当前关系阶段。
- 信任和戒备的叙事意义。
- 已确认共同经历。
- 未解决张力。
- 持续计划和承诺。
- 关系变化证据。

### 12.3 MotiveState

保存跨回合持续的：

- 欲望。
- 阻碍。
- 未完成意图。
- 关注事项。
- 与关系、身体和场景相关的主动倾向。

P0 只保存和评估，不自动发送桌面主动消息。

## 13. 每位女主角独立记忆库

### 13.1 物理和逻辑隔离

```text
HeroineMemoryRepository[baiweixi]
├─ private_event_ledger
├─ semantic_memory_store
├─ vector_index
├─ selector_state
├─ working_activation
└─ self_timeline_projection

HeroineMemoryRepository[future_heroine]
├─ private_event_ledger
├─ semantic_memory_store
├─ vector_index
├─ selector_state
├─ working_activation
└─ self_timeline_projection
```

召回入口固定为：

```text
HeroineMemoryRepository[active_character_id].recall(query)
```

禁止：

```text
GlobalMemory.recall(...)
-> filter by character_id
```

每位女主角必须拥有独立：

- 记忆写入入口。
- 语义记忆存储。
- 向量索引。
- 召回状态。
- 工作激活区。
- 自我时间线。

底层可以共享数据库进程或文件系统，但 Repository API、索引命名空间和查询权限必须不可跨角色。

### 13.2 世界事件不等于角色记忆

`WorldEventLedger` 保存客观发生的事件，不能被女主角直接当作记忆库搜索。

事件发生后，根据参与、观察或被告知关系，分别投影到符合条件的女主角独立记忆库。同一事件可以在不同女主角心中形成不同理解。

### 13.3 记忆召回

```text
active_character_id
-> open heroine repository
-> current dialogue query representation
-> multi-cue candidate generation
-> stable TopK
-> reranker
-> SelectedMemoryFrame
```

当前活动“正在吃面”属于 `LivingMind`，不是长期记忆。已经发生并结束的重要经历才可能进入长期语义记忆。

## 14. 单回合回复主干

```text
1. 接收男主对白 RequestReceipt
2. 锁定 save_id 前台回复事务
3. 等待上一轮必须完成的后台状态提交
4. getLatestSnapshot()
5. 冻结 TurnWorldSnapshot
6. 打开当前女主角独立记忆库
7. 召回并重排相关记忆
8. `MIND_PATCH_V2 / M2` 只生成短心智变化、证据别名和受控女主动作候选，不生成最终对白
9. Runtime Decoder/Compiler 绑定证据并补齐版本、快照和程序事实断言
10. HardInvariantValidator
11. 仅在高影响变化、变化过多、矛盾风险或显式抽样时执行 `WORLD_CONTINUITY_REVIEW`
12. Runtime 从上一稳定状态和批准 Patch 计算 `ApprovedHeroineState`
13. `GAME_REPLY` 从冻结快照、批准后状态、相关记忆、近期已提交对白和男主本轮原始对白生成纯自然语言回复
14. 校验回复与批准状态、冻结快照、身份、知识权限和男主程序事实一致
15. 原子追加男主对白、女主状态变化、女主回复、动作事件、模型决策和审计记录
16. 在同一事务内写入 `POST_REPLY_WORLD_MIND_RECONCILE` 与当前女主 `R1` 记忆作业
17. COMMIT
18. 显示正式回复
19. 当前女主后台槽先执行回答后心智整理，再执行 R1 提议、物化与索引
```

男主程序状态、`GameClock` 和客观场景事实在本轮模型链中只读。

## 15. MIND_PATCH_V2 / M2

### 15.1 输入

```text
SharedWorldCanon
+ ProtagonistCanon
+ ActiveCharacterPackage
+ TurnWorldSnapshot
+ ActiveHeroineRuntime
+ SelectedMemoryFrame
+ CurrentPlayerUtterance
```

### 15.2 短输入与输出

M2 使用固定位置短数组传入当前女主状态和冻结世界事实，使用数字别名传入相关记忆证据。模型只输出真正变化的字段和受控女主动作候选：

```json
{
  "c": [[5, "提醒男主慢一点吃，避免烫到", [0, 1]]]
}
```

字段代码 `0～8` 对应固定女主状态，`9`、`10` 对应动态动机和知识键。证据别名 `0` 是冻结世界快照，`1` 是本轮预分配的男主对白事件，`2+` 是本轮选中的相关记忆。无变化必须输出 `"c":[]`。

Runtime 从短输出编译：

```text
HeroineMindPatch
+ MindAdvanceResult
+ identity/version/evidence/transaction metadata
```

禁止输出：

- `protagonist_patch`
- `game_clock_operation`
- 客观物体位置修改
- 未被程序确认的场景结果
- 程序身份、版本、精确时间和世界事实断言
- 最终对白、回复正文或任何 `r` 字段

### 15.3 协议版本边界

`MIND_PATCH_V2 / M2` 是 Patch-only 合同。既有 `short_semantic_v1 / M1` 是已否决的合并合同：它要求一次 JSON 生成同时输出 Patch、动作和 `r` 回复，真实交互中已经出现写满 500 字符上限、重复小说化叙事和角色名损坏。它只保留为历史诊断证据，不得作为现行运行模式，也不得在 M2 或 `GAME_REPLY` 失败时静默回退。

不兼容变更必须建立 `M3`，不得重新定义 M1 或 M2 的含义。请求路由、模型工件 Manifest、原始输出和审计记录必须保存协议版本。

## 16. GAME_REPLY

### 16.1 输入

```text
ActiveCharacterPackage 生成的短稳定身份与表达锚
+ frozen TurnWorldSnapshot 的确定性最小世界语义投影
+ ApprovedHeroineState 的确定性语义投影
+ SelectedMemoryFrame 中本轮选中记忆的陈述
+ ApprovedHeroineActions 的描述
+ RecentCommittedDialogue
+ CurrentPlayerUtterance as exact protagonist dialogue
```

男主本轮对白必须以原始自然语言放在用户角色中。`GAME_REPLY` 看到的是 Runtime 已经批准的更新后女主状态，因此它不负责再次推断或修改状态。

Runtime 必须继续完整持有正典、冻结快照、身份、版本、证据、事实断言和事务对象，但不得把完整 Runtime JSON 发送给普通 `GAME_REPLY`。`GameReplyContextProjection` 只能裁剪、重命名和格式化已经批准的数据，不得推断新情绪、改写关系阶段或补造意图。

`GAME_REPLY` 输入中禁止出现 `save_id`、`world_id`、`protagonist_id`、`character_id`、`request_id`、`snapshot_id`、世界/心智版本、选择器版本、证据 ID 数组、投影计数、数据库字段和事务元数据。完整对象仍用于调用外校验、审计和原子提交。

### 16.2 输出

`GAME_REPLY` 只输出女主对男主说出的自然语言正文，例如：

```text
烫就慢一点，没人和你抢。
```

回复正文不使用 JSON Grammar，不嵌入 `r`、`reply` 或其他结构字段，不要求模型复制 `snapshot_id`、心智版本、世界断言和事务元数据。推理服务可以内部流式生成 token，但 P0 必须缓冲到完整回复通过校验并原子提交后才向玩家显示。

Runtime 根据调用外持有的冻结快照和批准状态建立 `GameReplyResult`、事实断言与审计元数据；程序不得改写模型正文来伪造角色已经说过的话。

### 16.3 回复校验与失败

禁止：

- 重新决定另一套人物状态。
- 否认程序提供的男主位置和活动。
- 引用其他女主角记忆。
- 提到现实玩家、外部世界或设备系统时间。
- 把男主一句话直接写成已经发生的客观动作。
- 输出内部 Schema、模型模式或审查过程。

Patch 失败、Critic 拒绝、`GAME_REPLY` 生成失败、回复校验失败或原子提交失败时，本轮男主对白、女主状态变化、女主回复和模型决策均不得部分提交。有限重试必须绑定同一 `request_id`、同一预分配男主事件 ID 和同一冻结快照；如果需要读取新快照，必须建立新的回合尝试而不是偷换当前上下文。

## 17. 实时更新、回答和后台任务的并发边界

### 17.1 回答期间继续运行

女主生成回复期间，以下内容继续更新：

- `GameClock`。
- 男主实时位置和动作。
- 场景和物体状态。
- 游戏引擎事件。

当前回复仍只使用回答开始时冻结的 `TurnWorldSnapshot`。

### 17.2 回答期间延迟的任务

以下模型整理任务不与当前回答并发提交心智状态：

- `FIVE_MINUTE_WORLD_MIND_RECONCILE`
- 可能修改同一女主 `LivingMind` 的后台任务
- 需要重建语义投影的高影响任务

到期时登记为待处理任务，回复事务完成后重新 GET 最新世界状态再执行。

### 17.3 WorldUpdateCoordinator

```yaml
background_task_state:
  foreground_reply_status: answering
  pending_reconcile_reasons:
    - five_minute
  last_reconcile_game_time: day11_18:00:00
  required_before_next_turn: true
```

规则：

- 实时世界更新不进入该锁。
- 同一 `save_id` 的心智状态提交必须串行。
- 下一轮回复前必须完成上一轮标记为 `required_before_next_turn` 的整理。
- 多个积压五分钟任务可以合并为一次，输入完整经过时间和事件差量。

## 18. POST_REPLY_WORLD_MIND_RECONCILE

每次正式回复提交后触发一次轻量模型整理。

输入重点：

- 男主刚说的话。
- 女主刚回复的话。
- 本轮已经批准的女主动作。
- 本轮关系和情绪变化。
- 当前最新程序世界状态。

整理内容：

- 回复产生的关系含义。
- 女主即时意图是否完成或变化。
- 自我时间线候选。
- 当前女主角记忆候选。
- 下一轮需要持续的注意事项。

它不负责：

- 推动 `GameClock`。
- 修改男主客观状态。
- 重写刚刚显示的回复。
- 访问其他女主角记忆。

## 19. FIVE_MINUTE_WORLD_MIND_RECONCILE

### 19.1 定位

每运行五分钟触发一次模型主导的全量心智整理。五分钟只是整理周期，不是世界和时间的更新频率。

```text
程序实时事实
+ 过去五分钟事件差量
+ 上一稳定心智状态
+ 当前女主独立记忆
-> 模型理解这些事实对人物意味着什么
```

### 19.2 输入

```yaml
reconciliation_input:
  trigger:
    type: periodic
    elapsed_game_time: 5_minutes
  previous_stable_mind_state: {}
  latest_world_snapshot: {}
  event_delta: []
  active_heroine_runtime: {}
  selected_private_memories: []
  unresolved_plans: []
```

如果任务积压十二分钟，可以合并：

```yaml
trigger:
  type: periodic_coalesced
  elapsed_game_time: 12_minutes
  missed_intervals: 2
```

### 19.3 模型主要整理内容

模型负责判断：

- 女主持续活动是否结束、继续、转移或被打断。
- 身体感受是否随时间、活动和事件自然变化。
- 情绪、注意力和即时意图是否仍然成立。
- 持续欲望、心理阻碍和未完成计划是否变化。
- 最近事件对关系意味着什么。
- 场景高层叙事是否需要更新。
- 哪些内容值得成为当前女主角的记忆候选。
- 上一稳定心智状态是否遗漏了重要变化。

程序不得写死：

- 吃饭二十分钟必定结束。
- 休息五分钟疲劳固定下降一级。
- 晚上固定增加困倦数值。
- 男主离开后女主固定切换某种情绪。
- 某个事件固定增加好感度。

### 19.4 最小 Patch

模型不重写全部状态，默认 `keep`：

```yaml
world_mind_reconcile_patch:
  parent_mind_state_version: 42
  latest_world_version: 1281
  keep:
    - heroine.form
    - heroine.location_id
    - relationship.stage
  update:
    heroine.current_activity:
      from: watching_protagonist_eat
      to: watching_kitchen_door
      reason: 男主已结束吃面并进入厨房
      evidence_event_ids: [evt_0102]
    heroine.immediate_intent:
      value: 想询问是否需要帮忙
      reason: 她希望证明自己不是负担，但仍担心被拒绝
  memory_candidates: []
```

### 19.5 无变化也是合法结果

```yaml
reconciliation_decision:
  meaningful_change: false
  operation: keep
  reason: 过去五分钟没有足以改变当前心智状态的新事件
```

## 20. 极少量程序硬校验

`HardInvariantValidator` 只负责程序能够百分之百确定的边界。

### 20.1 身份和权限

- `save_id`、`world_id`、`protagonist_id`、`character_id` 匹配。
- 当前角色不能修改或访问其他女主角 Runtime 和记忆库。
- 未安装角色包不能被激活。

### 20.2 程序权威事实

- 模型不能修改 `GameClock`。
- 模型不能修改 `ProtagonistLiveState`。
- 模型不能覆盖游戏引擎确认的场景、位置、物品和交互结果。

### 20.3 版本和结构

- 父版本必须仍是当前版本。
- 时间不能倒退。
- 引用 ID 必须存在。
- Patch 必须符合 Schema。
- `update` 必须提供依据。
- 同一任务必须幂等。

### 20.4 事务

- 状态、事件和回复必须原子提交。
- 失败不得留下半条世界线。
- 崩溃后恢复最后完整版本。

程序不判断：

- 女主为什么害羞。
- 她是否已经吃完。
- 她是否愿意靠近男主。
- 五分钟后疼痛应该下降多少。
- 某个片段对关系意味着什么。
- 某件事是否值得长期记住。

## 21. WORLD_CONTINUITY_REVIEW

程序硬不变量仍然每轮执行。语义连续性审查改为风险触发，输入：

- 上一稳定心智状态。
- 最新程序世界快照。
- 候选 Patch。
- 变化依据和事件证据。
- 当前女主角正典和合法记忆。

审查：

- 活动是否无故切换。
- 身体变化是否符合时间和事件。
- 情绪变化幅度是否自然。
- 是否把意图写成已经发生。
- 是否把猜测写成客观事实。
- 是否否认男主程序状态。
- 是否产生关系跳级。
- 是否越权读取其他女主记忆。
- 是否出现第二世界或现实玩家认知。

输出：

```yaml
continuity_review:
  decision: pass | revise | reject | uncertain
  issues: []
  allowed_revision_scope: []
```

M1 初版 Critic 只有批准和拒绝权，不负责创造剧情。因为对白已经从候选变化后的自己生成，Critic 若要求修订，本轮必须拒绝提交；在短 Critic 的局部删除协议单独冻结前，不能修改状态后继续提交旧对白。

## 22. 世界事件、知识和记忆投影

```yaml
world_event:
  event_id: evt_0102
  save_id: save_001
  game_time: day11_18:01:43
  event_type: protagonist_entered_location
  participant_ids: [protagonist]
  observer_ids: [baiweixi]
  location_id: kitchen
  payload:
    from_location_id: dining_area
    to_location_id: kitchen
```

规则：

- 世界事件只记录客观发生内容。
- 女主是否观察到由程序可见性和事件关系确定。
- 女主如何理解事件由模型整理。
- 形成的记忆只写入该女主自己的 Repository。
- 同一事件不代表所有女主角拥有同一记忆。

## 23. 原子提交与失败处理

### 23.1 回复事务

回复正文在事务提交成功后显示。若生成、审查或持久化失败，保留上一稳定心智状态，不显示未提交角色草稿。

### 23.2 后台整理事务

- 模型失败：保留上一稳定心智状态，记录重试任务。
- Critic 拒绝：有限重算；仍失败则 `keep`。
- 版本冲突：重新读取最新世界和心智版本。
- 多个周期任务积压：合并，不重复逐次调用。
- 记忆投影失败：不影响已提交回复，可从事件账本重建。
- 每个已提交回复只生成一个去重的 `R1` 作业；作业身份固定为 `save_id + world_id + protagonist_id + character_id + request_id`。
- `R1` 只能读取该作业绑定的当前女主两条已提交事件和该女主独立 Repository；来源身份不一致直接拒绝。
- `R1` 草案、记忆记录、向量索引与作业完成标记作为一个原子后台提交；任一步失败不得留下半条记忆。
- `R1` 运行中崩溃后恢复为待处理，可独立重试；失败不回滚正式回复，也不回滚已经完成的回答后心智整理。

### 23.3 实时世界不依赖模型任务成功

即使五分钟模型整理失败：

- `GameClock` 仍继续运行。
- 男主位置和动作仍由程序更新。
- 场景和物体仍由游戏引擎更新。
- 下一轮可以使用上一稳定女主心智状态和最新程序世界事实生成保守回复。

## 24. 模型角色分工

P0 可以复用同一个主模型，通过模式隔离承担：

| 模式 | 作用 |
|---|---|
| `MIND_PATCH_V2 / M2` | 从短身份锚、最小动态状态帧和相关记忆中只提出本轮女主心智变化、证据别名和受控女主动作候选 |
| `GAME_REPLY` | 从冻结快照与 `ApprovedHeroineState` 的确定性最小语义投影生成纯自然语言唯一回复 |
| `POST_REPLY_WORLD_MIND_RECONCILE` | 整理刚完成回合的关系、意图和记忆含义 |
| `FIVE_MINUTE_WORLD_MIND_RECONCILE` | 理解过去一段时间对持续心智的影响 |
| `WORLD_CONTINUITY_REVIEW` | 审查语义断裂、越权和关系跳变 |
| `MEMORY_PROPOSE / R1` | 为当前女主独立记忆库提出短长期记忆草案 |

模型模式必须使用独立输出 Schema，不能让回复模式直接覆盖数据库。

## 25. 性能与调度

优先级：

```text
前台回复
> 必须在下一轮前完成的心智整理
> 当前女主 R1 记忆提议、物化与索引
> 五分钟周期整理
> 低优先级压缩和维护
```

性能原则：

- 实时引擎状态不调用大模型。
- 回复前 GET 只读取已经准备好的状态。
- 五分钟模型整理使用事件差量和最小上下文，不发送所有帧数据。
- 前台 M2 和 `GAME_REPLY` 只发送职责所需语义，不发送完整正典、完整 Runtime 对象或程序元数据。
- 无变化时允许模型输出 `keep`。
- 积压周期任务合并为一次。
- 后台任务不能与前台回复争抢到破坏响应体验。
- 每个 `save_id + character_id` 只有一个后台执行槽；同一女主的心智整理与记忆任务串行，不创建第二条全局记忆队列。

## 26. P0 边界

P0 必须实现：

- 白未晞角色包动态加载。
- 实时 `GameClock`。
- 程序权威 `ProtagonistLiveState`。
- `LiveWorldState` 与模型可读投影。
- 回复前 `getLatestSnapshot()`。
- 白未晞独立记忆库。
- 一次短 `MIND_PATCH_V2 / M2`、程序硬校验、条件同模型 Critic 和一次纯文本 `GAME_REPLY`。
- 回答后模型整理。
- 五分钟模型主导整理。
- 原子回复与心智状态提交。
- 跨重启恢复。

P0 不做：

- 多位女主角同时生成回复。
- 无限后台自主生活模拟。
- 完整开放世界和任意 NPC 社会模拟。
- 角色聊天入口程序命令。
- 现实玩家和第二世界认知。
- 用大量生活规则代替模型判断。
- 每帧调用模型。
- 每五分钟重写全部人物状态。
- 让模型修改男主程序状态和游戏时钟。

## 27. 评测合同

### 27.1 单一世界认知

- 女主不提及现实玩家、外部世界和系统设备时间。
- 玩家任何聊天文字都按男主对白处理。
- “现实中”不会触发第二世界解释。

### 27.2 最新世界状态

- 男主从 A 点到 B 点后，下一轮女主读取 B 点。
- 男主正在吃面时，女主不会无依据说他正在画画。
- 回复事实与本轮 `TurnWorldSnapshot` 一致。
- 回答期间世界变化不会导致同一句回复状态撕裂。

### 27.3 GameClock

- 程序运行时游戏时间连续变化。
- 不聊天时游戏时间仍继续。
- 连续聊天不会按轮数机械增加固定分钟。
- 女主回答时间问题使用 GET 时最新游戏时间。
- 五分钟整理失败不停止 `GameClock`。

### 27.4 五分钟模型整理

- 大部分开放语义变化由模型判断。
- 程序不存在吃饭、疲劳、情绪、好感等大量固定生活规则。
- 无变化时状态保持。
- 有充分事件和时间依据时状态自然变化。
- 积压任务合并后仍能正确理解完整经过时间。

### 27.5 独立记忆

- 白未晞召回只访问自己的 Repository。
- 合成第二女主角不能读取白未晞私密记忆。
- 同一世界事件可以投影为两份不同角色记忆。
- 当前活动不错误写成长久共同经历。

### 27.6 权威和原子性

- 模型不能修改男主实时状态和 `GameClock`。
- 回复、女主状态和事件不会部分提交。
- 崩溃恢复最后完整版本。
- 后台整理失败不破坏实时游戏运行。

## 28. 施工顺序

1. 完成角色包、`save_id`、`world_id`、`protagonist_id`、`character_id` 身份链。
2. 移除秦未晞和历史默认角色依赖。
3. 建立 `LiveWorldState`、`ProtagonistLiveState` 和连续 `GameClock`。
4. 建立 `WorldStateProjection` 与 `getLatestSnapshot()`。
5. 建立白未晞独立 `HeroineMemoryRepository`。
6. 实现 `MIND_PATCH_V2 / M2`、Decoder/Compiler 和男主状态只读合同。
7. 实现程序硬校验、条件同模型 Critic、`ApprovedHeroineState` 和纯文本 `GAME_REPLY`。
8. 实现回答后模型整理。
9. 实现五分钟模型主导整理及任务合并。
10. 建立实时世界、回复事务和后台任务的并发验收。
11. 使用合成第二女主角验证记忆、Prompt 和状态隔离。
12. 完成目标硬件延迟、显存、稳定性和恢复验收。

## 29. V6 冻结实施决策

以下决策自 V6 生效起固定为 P0 实施合同：

1. **角色聊天入口中的所有玩家文字都是男主角对白。** 不存在程序命令、OOC、现实输入、动作叙述和混合输入分类。
2. **角色认知中只存在松江府这一个世界。** 不向女主提供现实玩家、外部世界和设备系统时间概念。
3. **游戏世界实时运行。** 位置、动作、场景、物体和程序确认状态按帧或事件立即更新，不等待模型和五分钟任务。
4. **`GameClock` 连续运行。** 聊天与时间推进没有因果关系；五分钟任务不推动时间。
5. **男主当前状态由程序最终控制。** 女主和模型只读 `ProtagonistLiveState`，玩家对白不能直接修改男主客观状态。
6. **每轮回复前只 GET 一次最新完整世界状态。** 本轮使用冻结 `TurnWorldSnapshot`，实时世界在生成期间继续运行。
7. **每位女主角拥有完全独立的记忆 Repository。** 不允许全局记忆先搜索再过滤角色。
8. **每次回复后运行一次模型整理。** 它整理关系、意图、时间线和记忆含义，不推动时间、不重写当前回复。
9. **每运行五分钟触发一次模型主导的心智整理。** 大部分生活语义由模型判断，程序只执行极少量硬校验。
10. **回答期间到期的模型整理任务延后执行。** 实时世界继续运行；任务在回复后重新 GET 最新状态并允许合并。
11. **最终回复只从批准后的女主状态生成。** 男主程序状态、当前游戏时间和客观场景作为只读事实进入回复。
12. **回复、事件和本轮女主心智变化原子提交后才显示。** 后台记忆和周期整理失败不得破坏已提交回复和实时世界。
13. **正常前台分离状态决定与自然回复。** `MIND_PATCH_V2 / M2` 只输出短心智变化和受控动作；Runtime 批准更新状态后，`GAME_REPLY` 再从该状态生成纯自然语言正文，条件 Critic 单独计数。
14. **长期记忆生产调用使用 R2 短草案。** 每条候选只必填种类、陈述和证据别名，确定性、时间与已有记忆关系按需输出；Runtime 推导主体、默认确定性、时间缺省、身份和完整 `MemoryProposalDraft`。旧 R1 Decoder 只保留兼容，不再作为生产模型合同。
15. **合并式 `short_semantic_v1 / M1` 已被否决。** 它只作为历史失败证据保留，任何正式配置、兼容路径和错误回退都不得重新启用该合同。
16. **回答后和五分钟整理统一使用 B1。** B1 输入只含当前女主短状态、最新世界语义、经过时间、有限事件变化、相关记忆和可选本轮对白；输出只含短状态变化及可选记忆/时间线候选。Runtime 在调用外恢复存档、世界、角色、快照、版本和证据对象。
17. **后台队列使用持久化退避和单槽背压。** B1 输出预算固定为 320 token，R2 固定为 180 token，二者生产默认不做模型级重试；暂时性失败按作业 attempt 退避，确定性 HTTP 400、请求、Schema、JSON 和合同错误不再作业级重试。过期回答后整理允许合并，周期整理继续合并经过时间。
18. **发布模型兼容合同逐模型冻结。** `model.runtime_compatibility` 必须声明 `chat_template`、`stop_sequences` 和 `structured_output`；llama.cpp 启动时显式绑定模板，每次请求显式发送停止序列。内部预热可以接受 token 上限结束，任何正式 M2、GAME_REPLY、B1、R2 或连续性审查收到 `finish_reason=length` 都按协议失败关闭，不得把截断文本当成成功。

### 29.1A 隔离 GPU 正式复验结论（2026-08-14）

- 冻结 60 轮负载持续 `3636.56s`，59 轮提交、1 轮 M2 协议失败；事务污染为 0，冻结快照、崩溃恢复、投影重建和多女主隔离通过。
- B1/R2 未再出现结构截断、HTTP 400 或合同失败；R2 记忆作业 59/59 完成，后台队列峰值 10 并最终排空。
- 唯一未提交轮次的 M2 连续生成语义非法 Patch：把字段名当成字段值，并引用输入中不存在的证据别名。Runtime 正确拒绝并保持无半提交，但当前仍会让整轮对白失败。
- 进程树稳态段 RSS 增长约 `1382.77 MiB`，超过 `512 MiB` 有界增长门槛；隔离组合 GPU 峰值 `5336 MiB`，超过 `5120 MiB` 门槛 `216 MiB`。
- `SYS-11 / SYS-12 / P0` 继续保持未通过。下一设计入口只处理 M2 非法 Patch 的安全降级和发布栈 RSS/GPU 资源治理，不回退已通过的 B1/R2、账本、快照、记忆隔离和恢复合同。

### 29.1C 7B 运行时兼容层与短协议复验（2026-08-14）

- 白未晞 Qwen2.5-7B Q5_K_M 发布清单已冻结 `chatml`、`<|im_end|>/<|im_start|>` 和 `json_schema` 能力；llama.cpp 启动参数、HTTP 请求与长度截断检测已贯通，适配器与 SYS-12 合同回归 `44 passed`。
- 独立短协议探针中 M2、B1、R2 均通过严格 Schema，分别生成 `16 / 33 / 17` token；说明结构化短合同可被当前 7B 工件执行。
- 最小 GAME_REPLY 在 140 token 自然结束，但出现 `assistant/assis` 角色续写、外语碎片和乱码；一回合真实 V6 链路中 GAME_REPLY 四次均打满 360 token 并被新截断保护拒绝，回合保持零提交。
- 同一 GGUF 通过原始 Ollama Modelfile 回答“你是谁”仍打满 64 token，并开始替男主续写下一轮。当前剩余主故障属于模型产物的回合终止和对白质量，不是 llama.cpp 未发送模板或 stop。
- 7B 状态为 `compatibility_partial`：结构化短协议通过，GAME_REPLY 不通过。不得启动 10/60 轮或一小时正式验收，也不得用缩短 token 上限、接受截断文本或程序拼接假对白掩盖模型失败。

### 29.1D 重训 7B Q4 工件与发布复验（2026-08-17～19）

- 2026-08-17 重新训练白未晞 7B（Qwen2.5-7B-Instruct，bf16 LoRA，1987 条 = 原 1973 + 14 条针对性样本，loss 1.699），导出 Q4_K_M（SHA256 `df6eefdce93ec512587131d6e566c32287a77fa601c4a8c423b8f8073a44b842`，Ollama `baiweixi-7b-fix:latest`）。原 Q5_K_M 工件的答非所问、外语碎片、数字串和重复词全部消失。
- 14 条针对性样本覆盖三类失败：能力边界越权（删记忆/传送/预测天气/感知男主灵气）、身份主客体混淆（觉醒事件/人形初现/男主籍贯）、关系节奏偏差（家务/离开/占有）。
- **M2 安全降级/容错**：M2 编译协议错误（重复证据别名、字段名当字段值、引用不存在别名）重试耗尽后降级为空 Patch（keep 本轮），服务/超时/截断错误不降级。降级保留 snapshot 证据引用与事实断言、版本递增维持前台事务语义，`changed_field_codes=()` 使 Critic 直接批准，审计记录 `fallback: m2_protocol_keep`。
- 质量集 11 个失败案例重跑：判官零失败，5 个词表误杀 + 2 个真实问题（teleport 措辞、protagonist_aura 越权）；完整 89 案例复验判官零失败，34 个机器失败中 33 个词表误杀、1 个真实失败（protagonist_job 编造）。
- 4 个人工长会话 v2 评审：3 pass + 1 issue（宽松口径，issue 为轻微主客体偏移），`human_sessions_completed=4`。
- SYS-11 一小时发布长测（2026-08-18）：60/60 轮、0 事务污染、0 撕裂快照、崩溃/投影/多女主隔离恢复、后台队列排空、每轮延迟 p50 1186ms、RSS 稳态不增长。
- 组合 GPU 降载：发布配置 `num_gpu=20`（3.73 GB 模型显存），8GB 卡组合 reranker 约 6.7 GB 可行；8GB 目标卡最终实测待目标硬件。

### 29.1 B1 / R2 编译边界

- `B1` 模型不能看到或复制 `save_id`、`world_id`、`character_id`、`snapshot_id` 和版本字段；冻结快照、男主对白、女主回复、世界事件和女主独立记忆通过局部数字别名引用。
- `B1 Compiler` 根据原始 `ReconcileMindRequest` 恢复 `parent_mind_state_version`、`latest_world_version`、`snapshot_id`、真实证据 ID 和完整 `HeroineMindPatch`。
- `R2` 每条记忆以 `{k,s,e}` 为最小核心，可选 `q/t/r`；Runtime 从记忆种类推导主体，并把事件别名恢复为完整已提交证据。
- 数据库迁移 `014_world_mind_background_backpressure.sql` 为回答后整理和长期记忆作业增加 `available_at`。领取查询只读取到期作业；重启恢复、抢占重排和失败退避都写回同一持久化字段。
- 连续性审查仍保留，不能仅按“变化字段少”旁路；单字段也可能发生无依据的剧烈情绪跳变。

这些决策发生变化时，必须同步更新项目需求、当前 AI 权威设计、产品集成设计、施工总纲和冻结评测。

## 30. 当前权威结论

V6 的核心实施原则是：

> 对角色只建模一个真实存在的松江府世界；由游戏程序每时每刻维护时间、位置、动作、场景、物品和男主状态等客观事实，由女主在每次回复前一次性读取最新世界快照；每位女主角只访问自己的持续心智和独立记忆库；主模型负责理解对白和过去一段时间对女主身体、情绪、活动、关系、动机和记忆意味着什么，程序只守住身份、权限、版本、客观事实和原子事务底线，连续性模型否决无依据断裂。这样既保证世界实时、回复一致和多女主隔离，也避免用无穷程序规则模拟自然人物生活。

本文件自 2026-08-09 起替代 V5，作为《猫咪女友》AI 运行时施工、评测和验收的唯一权威设计。
