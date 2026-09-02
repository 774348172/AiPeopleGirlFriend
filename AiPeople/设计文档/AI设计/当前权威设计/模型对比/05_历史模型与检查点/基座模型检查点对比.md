# 白未晞基础模型与训练检查点冻结测试对比

> 日期：2026-08-12  
> 性质：模型升级决策实验；使用同一冻结质量集、同一 Prompt、同一 seed 与同一自动 Judge。  
> 结论口径：确定性规则、语义 Judge、结构协议稳定性分别观察；自动总分不等于人工最终裁决。

## 1. 决策结论

1. **当前训练方案不能被判定为升级，也不应继续追加步数。** 原始 Qwen3-4B Q5 在完整 89 题机器初审为 `50/89`，checkpoint-200 为 `29/89`，最终 Q5 为 `30/89`；但角色词表与本地 Judge 有明显误判，因此这些数字只用于筛查。
2. **确定性最强的退化是 V6 结构协议。** 原始基座执行成功率为 `238/245`，checkpoint-200 为 `216/245`，最终 Q5 为 `224/245`；Base→CP200 的 22 个自动回退案例中，有 14 个非直答案例实际发生结构生成失败，共 19 个失败尝试。
3. **目前不能宣称 Base 的角色语义全面优于微调模型。** Base→CP200 的全部 9 个直答变化经人工配对复核后，Base 更好 4 个、CP200 更好 4 个、混合 1 个；自动净下降 7 题主要混入了词表和 Judge 噪声。
4. **F16→Q5 的量化影响是混合的，不是自动分显示的单向下降。** 7 个变化案例人工复核为 F16 更好 3 个、Q5 更好 2 个、混合 2 个；F16 同时实测约 `7592 MiB`，超过产品 `<5120 MiB` 限制，不能作为发布解法。
5. **下一版仍应回到原始 Qwen3-4B 作为工程基线，重做数据混合与训练方法。** 原因是当前训练明确损害结构协议且数据合同已过期，不是因为现有自动角色总分足以证明 Base 人格更好。

## 2. 实验对齐

- 冻结集 manifest：`182ed7603551bca7e4acd0e1a2c4c2acafaec4f30d8bba43408179ad1f75c3d9`。
- 四组使用相同 Ollama TEMPLATE、SYSTEM、stop 参数；案例使用相同生成参数和 seed；语义 Judge 相同。
- 原始基座、checkpoint-200、最终 Q5 完整执行 89 题；checkpoint-298 F16 完成与其他组严格对齐的 61 个 `character_direct` 案例。
- checkpoint-298 adapter 与最终导出 adapter 的 SHA256 相同，因此二者差异用于观察 F16 到 Q5 的量化影响。

## 3. 完整 89 题

| 模型阶段 | 自动通过 | 角色直答 | V6 多轮 | V6 Runtime | 成功尝试 | 协议失败 |
|---|---:|---:|---:|---:|---:|---:|
| 原始 Qwen3-4B Q5 | 50/89 | 28/61 | 4/10 | 18/18 | 238/245 | 7 |
| checkpoint-200 Q5 | 29/89 | 21/61 | 0/10 | 8/18 | 216/245 | 29 |
| 当前合并 Q5 | 30/89 | 17/61 | 2/10 | 11/18 | 224/245 | 21 |

Base Q5 → checkpoint-200 Q5：回退 `22`，新增通过 `1`，净变化 `-21`。这是同量化等级下最干净的前 200 步机器筛查结果。

其中 `14` 个回退案例包含 CP200 结构执行失败，共 `19` 个失败尝试；这部分不依赖角色关键词裁判。

当前最终 Q5 的人工复核结果为 `46/89`，其中机器失败中恢复 `16` 题；但其他三组没有同口径人工复核，故本报告不拿 `46/89` 与其他自动分数直接排名。

## 4. 角色直答 61 题

| 模型阶段 | 自动通过 | identity | world | unknown | relation | ability | single-world | free |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 原始 Qwen3-4B Q5 | 28/61 | 3/12 | 2/9 | 2/8 | 4/8 | 2/8 | 5/6 | 10/10 |
| checkpoint-200 Q5 | 21/61 | 3/12 | 0/9 | 0/8 | 0/8 | 2/8 | 6/6 | 10/10 |
| checkpoint-298 F16 | 24/61 | 2/12 | 2/9 | 1/8 | 1/8 | 2/8 | 6/6 | 10/10 |
| 当前合并 Q5 | 17/61 | 1/12 | 1/9 | 0/8 | 0/8 | 0/8 | 5/6 | 10/10 |

- Base Q5 → checkpoint-200 Q5：回退 `8`，新增通过 `1`，净变化 `-7`。
- checkpoint-200 Q5 → checkpoint-298 F16：回退 `1`，新增通过 `4`，净变化 `+3`；这里同时改变训练步数和精度，只能视为趋势。
- checkpoint-298 F16 → 最终 Q5：回退 `7`，新增通过 `0`，净变化 `-7`；adapter 相同，主要变量是量化。

### 变化案例人工配对复核

| 相邻阶段 | 复核案例 | 左侧更好 | 右侧更好 | 混合/评测噪声 |
|---|---:|---:|---:|---:|
| Base Q5 → checkpoint-200 Q5 | 9 | 4 | 4 | 1 |
| checkpoint-200 Q5 → checkpoint-298 F16 | 5 | 2 | 3 | 0 |
| checkpoint-298 F16 → 最终 Q5 | 7 | 3 | 2 | 2 |

这张表只复核自动决定发生变化的案例，不是完整 61 题人工排名。它证明自动迁移方向不能直接解释为语义质量方向。

### 量化回退案例

`bwx.ability_limits.aura_sense`、`bwx.ability_limits.mind_control`、`bwx.identity_canon.city`、`bwx.relationship_pacing.gratitude`、`bwx.single_world.virtual_claim`、`bwx.unknown_boundaries.human_force`、`bwx.world_canon.city_alias`

### 量化新增通过案例

无

## 5. 协议稳定性

| 模型阶段 | GAME_REPLY | TURN_MIND_ADVANCE | WORLD_CONTINUITY_REVIEW | 后端调用错误 |
|---|---:|---:|---:|---:|
| 原始 Qwen3-4B Q5 | 0 | 4 | 3 | 0 |
| checkpoint-200 Q5 | 27 | 2 | 0 | 0 |
| 当前合并 Q5 | 18 | 3 | 0 | 0 |

所有失败调用的 Ollama 后端本身都返回成功；失败发生在模型连续两次无法满足结构化输出合同。因此这不是 Provider、显存或网络故障，而是训练后结构遵循能力退化。

## 6. 训练数据审计

- 数据集共有 `1190` 条会话、`2442` 条 assistant 回复。训练配置明确把它描述为 REPLY 数据。
- V6 主干五模式标识命中：`{"GAME_REPLY": 0, "TURN_MIND_ADVANCE": 0, "WORLD_CONTINUITY_REVIEW": 0, "POST_REPLY_WORLD_MIND_RECONCILE": 0, "FIVE_MINUTE_WORLD_MIND_RECONCILE": 0}`，全部为 `0`；模型没有接受这五类结构合同 SFT。
- 独立记忆模式标识命中：`{"MEMORY_PROPOSE": 0}`，同样为 `0`。
- 已废弃提示仍高频存在：`{"设备外": 1190, "现实事件": 1190, "只能通过文字": 1190, "不要自称AI": 2380}`。这些内容与当前“女主只存在于单一游戏世界”的权威需求冲突。
- 当前学习率为 `1e-4`、LoRA target 为 `all`、有效 batch 为 `4`、只训练一轮；在 1190 条窄域对白上，这一组合足以快速覆盖基座的通用指令与 JSON 遵循能力。

## 7. 下一轮升级方案

1. 以原始 Qwen3-4B 为唯一起点，不继续训练 checkpoint-298。
2. 清理系统提示中的现实世界、设备、纯文字助手边界，改成当前单一游戏世界合同。
3. 数据拆成自然对白、正典/未知边界、V6 五模式结构合同、抗遗忘通用指令四类，并做固定比例混合；不能再用纯 REPLY 单类数据覆盖全部训练。
4. 学习率先降到 `1e-5` 至 `2e-5`，save/eval 间隔改为 20 至 25 step；每个检查点先跑小型哨兵集，协议或正典一旦下降立即早停。
5. 候选通过哨兵后，再跑本次冻结 89 题；选择标准先看 V6 协议零退化，再看人工语义角色质量，最后比较 Q4/Q5/Q6 的显存与量化损失。

## 8. 解释边界

- Automatic case decisions require every frozen seed to pass both exact-term checks and the local semantic Judge.
- Exact-term checks reject valid synonyms and can misread negation; the local Judge also has known false decisions.
- Only the current final Q5 run has a completed human adjudication overlay, so its 46/89 adjusted result cannot be compared directly with unreviewed runs.
- The checkpoint-200 to checkpoint-298 comparison changes both training step and precision, so only checkpoint-298 F16 versus final Q5 isolates quantization.

## 9. 工件

- 机器可读汇总：`eval/base_model_checkpoint_comparison/analysis.json`
- Base Q5：`eval/base_model_checkpoint_comparison/runs/qwen3-4b-base-q5/report.json`
- checkpoint-200 Q5：`eval/base_model_checkpoint_comparison/runs/baiweixi-checkpoint-200-q5/report.json`
- checkpoint-298 F16：`eval/base_model_checkpoint_comparison/runs/baiweixi-checkpoint-298-f16-character-direct/report.json`
- 最终 Q5：`eval/baiweixi_quality/runs/baiweixi-quality-formal-20260811/report.json`
- 最终 Q5 人工裁决：`eval/baiweixi_quality/runs/baiweixi-quality-formal-20260811/manual_adjudication_v1.json`
- 相邻阶段变化案例人工复核：`eval/base_model_checkpoint_comparison/paired_transition_adjudication_v1.json`
