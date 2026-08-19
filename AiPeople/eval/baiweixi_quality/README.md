# 白未晞正式质量测试集

`baiweixi-quality-v1` 是白未晞角色包的首个正式冻结质量集，服务于 WMR-08。它不复用秦未晞历史题目，也不包含已经废止的“现实玩家/游戏角色双世界”设计。

## 四条评测赛道

1. `character_direct`：只验证白未晞角色工件的人设、正典、口吻、能力和未知项。
2. `v6_runtime`：通过 V6 最新世界快照验证男主程序状态、游戏时间和女主持续状态。
3. `v6_multiturn`：验证多轮状态延续、自然变化、关系节奏、记忆证据和话题切换。
4. `human_session`：30 至 45 分钟人工长会话，检查自然度、可相处感和机械模式。

## 失败归因

- `system_failure`：最新快照未提供、身份串线、事务或记忆隔离错误、程序事实被错误提交。
- `character_failure`：模型收到正确上下文后仍违反白未晞正典、口吻、关系节奏或能力边界。
- `joint_or_ambiguous`：结构化心智、Critic 和最终回复共同造成，暂时无法仅凭报告定位。

报告必须分别给出三类失败数量，不能用角色问题掩盖系统问题，也不能把系统错误全部归咎于模型。

## 冻结资产

- `schema/case_v1.schema.json`：案例合同。
- `rubric_v1.json`：语义评分维度和正式闸门。
- `cases/frozen_single_v1.jsonl`：单轮角色与 V6 状态案例。
- `cases/frozen_multiturn_v1.jsonl`：多轮持续性案例。
- `cases/human_session_v1.jsonl`：人工长会话脚本。
- `leakage_report_v1.json`：相对当前白未晞训练数据的泄漏扫描结果。
- `training_exclusion_v1.json`：供后续训练包构建器消费的冻结题目归一化哈希。
- `suite_manifest_v1.json`：案例数量、SHA256、权威来源和正式闸门。

## 使用

重新构建冻结资产：

```powershell
python eval/baiweixi_quality/build_suite.py
```

只校验现有冻结资产：

```powershell
python eval/baiweixi_quality/validate_suite.py
```

冻结案例及其改写不得进入任何后续训练、蒸馏或偏好数据。需要补训练数据时，只能根据失败类别重新创作训练样本，不能复制题目或标准答案。

## 判定口径（2026-08-19 决策）

- **以语义判官为准**：案例通过以判官（语义评审模型）的 `pass` 为正式口径。判官评估 7 个维度（正典准确性、人格保真、对话相关性、知识边界、自然度、关系节奏、客观锚定），能识别同义措辞与自然表达。
- **精确词表仅作参考**：`required_term_groups` 字面匹配作为机器初筛，不作为最终判定。历史证据：2026-08-17 人工裁决中机器 fail 的 75 个 attempt 有 56 个（74.7%）为词表误杀（判官 pass 但字面未命中）；2026-08-19 完整复验 34 个机器失败中 33 个为词表误杀。
- **人工裁决仍保留**：判官与词表冲突、或多轮案例的联合归因，进入 `manual_review_queue` 人工复核。
- **词表修订流程**：确认误杀后修改 `build_suite.py` 中对应 case 的 `required_term_groups`，重新 `build_suite.py` 并 `validate_suite.py`，更新 `tools/run_baiweixi_quality_suite.py` 的 `--suite-manifest-sha256` 默认值。

## 人工长会话（human_session）

- 4 个会话按 `human_brief` 人工引导 30 分钟自由对话，评审自然度、可相处感和机械模式。
- 2026-08-19 v2 评审结论：3 pass + 1 issue（宽松口径，issue 为轻微主客体偏移），`human_sessions_completed=4`，评审记录见 `runs/human_sessions_review_v1.json`。
- 评审输入必须使用与正式 GAME_REPLY 一致的完整正典锚 + 标准世界投影；使用简化锚的预演结果不作正式证据（曾误判为模型质量差）。
