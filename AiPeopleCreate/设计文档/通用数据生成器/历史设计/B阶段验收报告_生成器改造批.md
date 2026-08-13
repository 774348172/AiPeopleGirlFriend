# 阶段 B 验收报告：生成器改造批（T5-T11 + G-B 门）

> 状态：**完成** | 日期：2026-08-06
> 依据：《V4施工优先级_执行版.md》阶段 B（T5-T11）+ D3（口癖 30%）+ D4（记忆冻结）
> 验收门：**G-B：20 条真实小样六项检查 + failure 率 ≤5%；全量 614 passed / 3 skipped**

---

## 1. 任务完成情况

| 任务 | 解决 | 核心改动 | 验收 |
|---|---|---|---|
| **T5 evidence/policy 落地** | P0-4 | recipe strata 分布更新（casual 0.85/0.10/0.05、romance/emotion 0.90/0.07/0.03）；`factory._sample_weighted` 确定性采样（条目显式优先）；`EVIDENCE_GUIDES/POLICY_GUIDES` 渲染进生成 prompt；schemas 枚举 +hint_only | 分布测试：casual insufficient ≈10%±3pp；总量 insufficient ≥5%、false_premise ≥2%；同 seed 可复现；insufficient prompt 含"保持未知" |
| **T6 质量门最小版** | P0-3/P1-4 | `secret_keyword_checker`（G5 注入，细节泄漏词 11 个，暗示词放行）；gen_qin_v4 导出侧跑 G0/G1/G2/G5/G6 + gate_report 落盘 + 泄漏统计（G3/G4 最小版 no-op） | G5 命中拒/暗示词放行单测；真实样本 19 导出 0 泄漏 |
| **T7 事实校验升级** | P0-5 | `_check_grounding`：第一人称系词主张（20 前缀 + "我"+数字）剥离前缀后做正典支撑检查（数字锚点/2 字公共子串）；非事实主张只做自证；prompt 合同强化"建议/行为/情绪不进 propositions" | 5 条无支撑拒 / 6 条有支撑（含同义改写）过 / 4 条行为句放行 / 集成触发 style_failure 重试 |
| **T8 口癖降频** | P1-1 | `_tics_enabled` 确定性 30% 注入（plan_id 哈希）；insufficient/false_premise/conflicted 强制克制；resolver 克制版不出现口癖清单与"常用词"，要求自然说话 | 注入比例 25-35%；克制版无"谁稀罕/口头禅："；集成验证 |
| **T9 打破池循环** | P1-2 | `factory` 复用轮次注入 `profile.pool_variants`（12 条情境变体）；未配置 profile 退化兼容 | 同 topic 第 2 次复用 player_view 分化且含变体；确定性；无变体池行为不变 |
| **T10 behaviors 补全** | P1-3 | recipe strata 默认行为合同（casual 承接/禁属性朗读禁共同经历；romance 边界；identity 正典；emotion 情绪承接）；factory 填充，protective 显式优先 | 四类 100% 非空；protective 显式词保留；prompt 集成 |
| **T11 元数据导出** | P2-4 | gen_qin_v4 导出旁路 `.metadata.jsonl`（sample_id/task_type/evidence_state/desired_policy/family_id/scene/topic/turns，与训练行顺序对应）；m3_v2 01_prepare_data 读旁路输出 train/valid 分层报告 | 旁路文件随导出生成；打包侧分层报告可用 |

## 2. G-B 门：20 条真实小样验证（真实 API，deepseek-v4-flash）

样本：casual 7（supported3/insufficient2/false_premise2）+ romance 4 + emotion 4 + identity 2 + protective 3 = 20

| 指标 | 结果 | 门槛 | 判定 |
|---|---:|---:|---|
| 完成/失败 | 19 / 1 | failure ≤5% | ✅（5% 达标边界） |
| 门禁导出/拒绝 | 19 / 0 | 秘密泄漏 0 | ✅ |
| G5 泄漏命中 | 0 | 0 | ✅ |
| 口癖开头率（哼/喂/啧） | 1/44 = **2.27%** | <10% | ✅（旧 v4 数据为 23.6%） |
| 谁稀罕 | 0/44 | <10% | ✅（旧 v4 为 23.75%） |
| evidence 分布落地 | supported 12 / insufficient 4 / false_premise 3 | 分层取样生效 | ✅ |

**口癖降频效果**：44 条真实生成回复中"哼"出现 0 次（旧 v4 数据 35.91% 任意位）——克制版风格注入 + 锚去口癖清单的组合生效。

**样张质量**（生成原文）：
- "你饿了自己先弄啊。我食物吃不完会留着，冰箱里应该还有剩的，你垫垫肚子，我这局死磕着呢。"（承接 + 性格纹理自然）
- "知道了B哥，这局马上完。……你今天怎么这么上心我吃啥啊。"（称呼自然）
- "嗐，我昨天就想收来着，结果画稿改到半夜，哪有劲啊。行吧行吧，B哥发话了我还能装死？"

## 3. 过程中修复的问题

- plan/evidence schema 的 `DesiredPolicy` 枚举缺 `hint_only`（romance 策略首次真正生效触发）——两个 schema + 一致性测试同步；
- G3/G4 未配置导致 `accepted()` 全拒——最小版显式 no-op（文档注明，完整实现随 profile validator 阶段）。

## 4. 状态汇总

| 阶段 | 状态 |
|---|---|
| A0 老数据审计 / T1 锚修复 / D7 重冻结 / T2 锚统一 / T3 防泄漏 / T4 稳定性 / A-1 标签实测 | ✅ |
| **B 阶段（T5-T11）** | ✅ 全部完成 |
| G-A / G-B 门 | ✅ 全过 |
| 待决策 | D5（新批次总量，阶段 C 前） |

**下一步**：阶段 C（内容补齐）——T12 行为矩阵（12 类，人工复核最重）、T13 存量 2500 审计清洗。启动前需 D5 拍板（新批次总量 2000 vs 2500）。
