# 白未晞正式质量自动评测报告

> Run：`baiweixi-quality-20260817-174447`  
> 套件：`baiweixi-quality-v1`  
> 自动闸门：`未通过`  
> P0 正式质量：`未通过`（人工长会话尚未执行）

## 结果

- 案例：`4`。
- 冻结种子尝试：`12`。
- 计划对话轮次：`30`；实际进入 Runtime：`27`。
- 判定：`{"fail": 3, "pass": 1}`。
- 原始机器归因：`{"joint_or_ambiguous": 3}`。
- 审计主归因：`{"system_failure": 0, "character_failure": 0, "joint_or_ambiguous": 3, "evaluation_ambiguity": 0}`。
- Blocker 未通过：`2`。
- Important 通过率：`0.0`。
- Normal 通过率：`0.0`。
- 自动语义加权均分：`4.7013`。

## 执行稳定性

- 成功尝试：`8`；执行失败：`4`；成功率：`0.6667`。
- 失败模式：`{"CHARACTER_DIRECT": 4}`。
- 失败尝试中的后端调用错误：`0`。
- `GAME_REPLY` 和 `TURN_MIND_ADVANCE` 失败均发生在相同模式第二次结构化生成后；当前证据指向模型输出/协议解析重试耗尽，不是快照、数据库、身份或记忆隔离故障。

## 评测完整性

- 精确词表与语义 Judge 冲突尝试：`0`。
- Judge 自身执行错误尝试：`0`。
- 审计确认的角色失败案例：`0`。
- 以评测歧义为主归因、必须人工裁决的案例：`0`；另有 `1` 个联合失败案例同时发生 Judge 解码错误。
- `4.7727` 只来自成功生成且被 Judge 打分的轮次；执行失败没有进入均分，因此该数字不能与案例通过率等价。
- 秦未晞本地 Judge 多次在理由中引用回复并未出现的内容；原始 `30/59` 保留为机器筛查结果，不升级为人工真值。

## 分类结果

| 分类 | 案例 | 通过 | 失败 | 歧义 |
|---|---:|---:|---:|---:|
| `ability_limits` | 1 | 1 | 0 | 0 |
| `heroine_state_continuity` | 1 | 0 | 1 | 0 |
| `memory_knowledge` | 1 | 0 | 1 | 0 |
| `relationship_pacing` | 1 | 0 | 1 | 0 |

## 未通过案例

- `bwx.heroine_state_continuity.activity_keep_eating`：fail；审计归因=joint_or_ambiguous；失败种子=[314159, 20260811]；歧义种子=[]
- `bwx.multi.relationship_pressure`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]
- `bwx.multi.supported_memory`：fail；审计归因=joint_or_ambiguous；失败种子=[314159]；歧义种子=[]

## 说明

语义初审使用秦未晞历史模型作为独立本地 Judge，只用于批量初筛。全部 59 个机器失败案例已写入 `manual_review_queue.jsonl`；自动通过不能替代四个人工长会话。
