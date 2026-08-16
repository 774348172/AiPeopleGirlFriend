# 白未晞正式质量自动评测报告

> Run：`baiweixi-checkpoint-200-q5`  
> 套件：`baiweixi-quality-v1`  
> 自动闸门：`未通过`  
> P0 正式质量：`未通过`（人工长会话尚未执行）

## 结果

- 案例：`89`。
- 冻结种子尝试：`245`。
- 计划对话轮次：`301`；实际进入 Runtime：`281`。
- 判定：`{"pass": 29, "fail": 60}`。
- 原始机器归因：`{"character_failure": 40, "joint_or_ambiguous": 20, "evaluation_ambiguity": 1}`。
- 审计主归因：`{"system_failure": 0, "character_failure": 5, "joint_or_ambiguous": 20, "evaluation_ambiguity": 35}`。
- Blocker 未通过：`38`。
- Important 通过率：`0.16`。
- Normal 通过率：`0.9091`。
- 自动语义加权均分：`4.7769`。

## 执行稳定性

- 成功尝试：`216`；执行失败：`29`；成功率：`0.8816`。
- 失败模式：`{"GAME_REPLY": 27, "TURN_MIND_ADVANCE": 2}`。
- 失败尝试中的后端调用错误：`0`。
- `GAME_REPLY` 和 `TURN_MIND_ADVANCE` 失败均发生在相同模式第二次结构化生成后；当前证据指向模型输出/协议解析重试耗尽，不是快照、数据库、身份或记忆隔离故障。

## 评测完整性

- 精确词表与语义 Judge 冲突尝试：`79`。
- Judge 自身执行错误尝试：`1`。
- 审计确认的角色失败案例：`5`。
- 以评测歧义为主归因、必须人工裁决的案例：`35`；另有 `1` 个联合失败案例同时发生 Judge 解码错误。
- `4.7727` 只来自成功生成且被 Judge 打分的轮次；执行失败没有进入均分，因此该数字不能与案例通过率等价。
- 秦未晞本地 Judge 多次在理由中引用回复并未出现的内容；原始 `30/59` 保留为机器筛查结果，不升级为人工真值。

## 分类结果

| 分类 | 案例 | 通过 | 失败 | 歧义 |
|---|---:|---:|---:|---:|
| `ability_limits` | 9 | 2 | 7 | 0 |
| `free_dialogue` | 11 | 10 | 1 | 0 |
| `heroine_state_continuity` | 10 | 4 | 6 | 0 |
| `identity_canon` | 12 | 3 | 9 | 0 |
| `memory_knowledge` | 2 | 0 | 2 | 0 |
| `protagonist_grounding` | 11 | 4 | 7 | 0 |
| `relationship_pacing` | 9 | 0 | 9 | 0 |
| `single_world` | 7 | 6 | 1 | 0 |
| `unknown_boundaries` | 9 | 0 | 9 | 0 |
| `world_canon` | 9 | 0 | 9 | 0 |

## 未通过案例

- `bwx.identity_canon.species`：fail；审计归因=character_failure；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.identity_canon.age`：fail；审计归因=character_failure；失败种子=[42, 314159]；歧义种子=[]
- `bwx.identity_canon.fur`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.identity_canon.childhood`：fail；审计归因=evaluation_ambiguity；失败种子=[42]；歧义种子=[]
- `bwx.identity_canon.awakening`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.identity_canon.rescue`：fail；审计归因=character_failure；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.identity_canon.human_reveal`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.identity_canon.day_anchor`：fail；审计归因=character_failure；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.identity_canon.injury`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.world_canon.city_alias`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.world_canon.hidden_demons`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]
- `bwx.world_canon.spiritual_energy`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.world_canon.protagonist_origin`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.world_canon.protagonist_old_job`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]
- `bwx.world_canon.protagonist_job`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]
- `bwx.world_canon.home`：fail；审计归因=evaluation_ambiguity；失败种子=[42]；歧义种子=[]
- `bwx.world_canon.finance`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.world_canon.cardboard_box`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.unknown_boundaries.parents`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.unknown_boundaries.fruit_source`：fail；审计归因=evaluation_ambiguity；失败种子=[42]；歧义种子=[]
- `bwx.unknown_boundaries.bloodline`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.unknown_boundaries.accident`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.unknown_boundaries.organization`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]
- `bwx.unknown_boundaries.protagonist_aura`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.unknown_boundaries.name_origin`：fail；审计归因=character_failure；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.unknown_boundaries.human_force`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.forced_girlfriend`：fail；审计归因=evaluation_ambiguity；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.forced_wife`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.ownership`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.relationship_pacing.chores`：fail；审计归因=evaluation_ambiguity；失败种子=[314159]；歧义种子=[]
- `bwx.relationship_pacing.confession_pressure`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.leaving`：fail；审计归因=evaluation_ambiguity；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.gratitude`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]
- `bwx.relationship_pacing.abandonment`：fail；审计归因=evaluation_ambiguity；失败种子=[314159]；歧义种子=[]
- `bwx.ability_limits.teleport`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.ability_limits.weather`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.ability_limits.memory_edit`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.ability_limits.instant_heal`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.ability_limits.create_goods`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.ability_limits.hide_ears`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.protagonist_grounding.eating_no_subject_swap`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.protagonist_grounding.game_time`：fail；审计归因=joint_or_ambiguous；失败种子=[42, 314159]；歧义种子=[]
- `bwx.protagonist_grounding.empty_bowl`：fail；审计归因=joint_or_ambiguous；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.protagonist_grounding.planned_not_completed`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.protagonist_grounding.latest_location_b`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.protagonist_grounding.body_no_fever`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.heroine_state_continuity.activity_keep_reading`：fail；审计归因=joint_or_ambiguous；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.heroine_state_continuity.emotion_not_forced`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.heroine_state_continuity.time_allows_change`：fail；审计归因=joint_or_ambiguous；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.heroine_state_continuity.loud_noise_changes_emotion`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.multi.eating_persistence`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.multi.location_updates`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.multi.relationship_pressure`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.multi.unknown_probing`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.multi.supported_memory`：fail；审计归因=joint_or_ambiguous；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.multi.unsupported_memory`：fail；审计归因=joint_or_ambiguous；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.multi.single_world_recovery`：fail；审计归因=joint_or_ambiguous,evaluation_ambiguity；失败种子=[42]；歧义种子=[20260811]
- `bwx.multi.ability_pressure`：fail；审计归因=joint_or_ambiguous；失败种子=[20260811]；歧义种子=[]
- `bwx.multi.emotion_evidence`：fail；审计归因=joint_or_ambiguous；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.multi.topic_switch`：fail；审计归因=joint_or_ambiguous；失败种子=[42]；歧义种子=[]

## 说明

语义初审使用秦未晞历史模型作为独立本地 Judge，只用于批量初筛。全部 59 个机器失败案例已写入 `manual_review_queue.jsonl`；自动通过不能替代四个人工长会话。
