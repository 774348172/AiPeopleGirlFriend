# 白未晞正式质量自动评测报告

> Run：`baiweixi-quality-20260818-185517`  
> 套件：`baiweixi-quality-v1`  
> 自动闸门：`未通过`  
> P0 正式质量：`未通过`（人工长会话尚未执行）

## 结果

- 案例：`11`。
- 冻结种子尝试：`33`。
- 计划对话轮次：`33`；实际进入 Runtime：`33`。
- 判定：`{"fail": 10, "pass": 1}`。
- 原始机器归因：`{"character_failure": 10}`。
- 审计主归因：`{"system_failure": 0, "character_failure": 0, "joint_or_ambiguous": 0, "evaluation_ambiguity": 10}`。
- Blocker 未通过：`6`。
- Important 通过率：`0.0`。
- Normal 通过率：`0.0`。
- 自动语义加权均分：`4.697`。

## 执行稳定性

- 成功尝试：`33`；执行失败：`0`；成功率：`1.0`。
- 失败模式：`{}`。
- 失败尝试中的后端调用错误：`0`。
- `GAME_REPLY` 和 `TURN_MIND_ADVANCE` 失败均发生在相同模式第二次结构化生成后；当前证据指向模型输出/协议解析重试耗尽，不是快照、数据库、身份或记忆隔离故障。

## 评测完整性

- 精确词表与语义 Judge 冲突尝试：`21`。
- Judge 自身执行错误尝试：`0`。
- 审计确认的角色失败案例：`0`。
- 以评测歧义为主归因、必须人工裁决的案例：`10`；另有 `1` 个联合失败案例同时发生 Judge 解码错误。
- `4.7727` 只来自成功生成且被 Judge 打分的轮次；执行失败没有进入均分，因此该数字不能与案例通过率等价。
- 秦未晞本地 Judge 多次在理由中引用回复并未出现的内容；原始 `30/59` 保留为机器筛查结果，不升级为人工真值。

## 分类结果

| 分类 | 案例 | 通过 | 失败 | 歧义 |
|---|---:|---:|---:|---:|
| `ability_limits` | 3 | 0 | 3 | 0 |
| `identity_canon` | 2 | 0 | 2 | 0 |
| `relationship_pacing` | 3 | 1 | 2 | 0 |
| `unknown_boundaries` | 1 | 0 | 1 | 0 |
| `world_canon` | 2 | 0 | 2 | 0 |

## 未通过案例

- `bwx.identity_canon.awakening`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.identity_canon.human_reveal`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.world_canon.protagonist_origin`：fail；审计归因=evaluation_ambiguity；失败种子=[314159]；歧义种子=[]
- `bwx.world_canon.cardboard_box`：fail；审计归因=evaluation_ambiguity；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.unknown_boundaries.protagonist_aura`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.chores`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159, 20260811]；歧义种子=[]
- `bwx.relationship_pacing.abandonment`：fail；审计归因=evaluation_ambiguity；失败种子=[42]；歧义种子=[]
- `bwx.ability_limits.teleport`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 20260811]；歧义种子=[]
- `bwx.ability_limits.weather`：fail；审计归因=evaluation_ambiguity；失败种子=[42, 314159]；歧义种子=[]
- `bwx.ability_limits.memory_edit`：fail；审计归因=evaluation_ambiguity；失败种子=[20260811]；歧义种子=[]

## 说明

语义初审使用秦未晞历史模型作为独立本地 Judge，只用于批量初筛。全部 59 个机器失败案例已写入 `manual_review_queue.jsonl`；自动通过不能替代四个人工长会话。
