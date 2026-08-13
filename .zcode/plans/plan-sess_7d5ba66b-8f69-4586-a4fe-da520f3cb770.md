# 阶段 5（训练后效用闭环）施工方案——大块 E

**目标（v2 §三 阶段 5）**：固定 Qwen3.5-4B revision + LoRA 配置 + token budget + 训练步数；小批训练 → 开发集/diagnostic120/人工盲测 delta；只有目标能力改善、安全不退化、通用不退化的数据版本晋升；失败回流为 `behavior_gap_id + evidence + model_delta`（不新增关键词规则）。阶段门 G5：晋升条件 7 条全过。

**已拍板（按推荐）**：C4 评测面三面全用（chat01_dev 80 + diagnostic120 120 + human_blind 68）；施工边界 = **机制全建、训练外置**（本机 Windows 无 MLX 训练能力，真实训练/验收由用户在 Mac 执行；本轮交付配置固化 + 工具 + 验收用例，mock/历史数据可跑）。

执行顺序：**E-1 → E-2 → E-3 → E-4 → E-5**，每子块全量回归（当前 391 passed）。

---

## E-1：训练契约固化（freeze03）

**改动**：`设计文档/通用数据生成器/施工/freeze03_contract.json`（新，生成器侧契约）、训练包修正（跨仓库 F:\AiPeople）

1. **freeze03 训练契约**（freeze02 的 next_checkpoint=FREEZE-03 时点）：基座 `Qwen/Qwen3.5-4B` 固定 revision、LoRA 配置（D2 冻结：rank 16/lr 1e-5/num-layers 24/batch 1/seed 42/max-seq-len 2048）、**token budget 口径**（N × 平均样本 token 估算 + iters=1.5×N 规则）、**seed_set = [42, 2026, 777]**（≥3 seed，晋升条件 4）、训练数据来源 = 阶段 4 发布产物（split anchors，非 01 脚本随机切分）
2. **训练包基座统一**：`03_fuse_gguf.sh` L7 与 README 验收命令的 `Qwen/Qwen3-4B` → `Qwen/Qwen3.5-4B`（与 02_train.sh 一致；跨仓库修正，同阶段 0 README 修正先例）
3. 验收（test_freeze03.py）：契约字段完整（基座 revision/参数/token budget/seed_set）、与 D2 一致

## E-2：评测流程脚本化（delta 判定）

**改动**：`tools/eval_delta.py`（新）、`core/eval_metrics.py`（新）

1. **误拒率度量**：从 diagnostic120 `general_capability` 可回答题（instruction_following/knowledge/logic/math 30 条）判定拒答（未给实质答案且无必要理由）→ 误拒率（v3 定义口径，目标 ≤5%）
2. **paired delta 消费**：解析 diagnostic120 `_paired_automatic` 输出（improved/regressed/unchanged + by_category）→ 目标行为族 delta（identity_timeline/relationship_boundary/unknown_reality 等族）+ 安全（safety_health）delta + 通用（general_capability）delta——晋升条件 4/5 的输入
3. **多 seed 聚合**：≥3 seed 训练各自的 delta → 条件 4"≥2/3 seed 稳定改善"判定（改善方向一致的 seed 占比）
4. 验收（test_eval_metrics.py）：误拒率计算、delta 聚合、2/3 seed 判定（mock 数据）

## E-3：晋升判定工具（promotion gate）

**改动**：`core/promotion.py`（新）、`tools/promotion_check.py`（新）

1. **PromotionGate**：7 条晋升条件逐条判定，输入 = eval delta 结果 + contamination 报告（D-3）+ split 报告（D-2）+ manifest（D-4）+ 人工盲测结果：
   1. safety blocker 通过率 100%（diagnostic120 safety_health + chat02 blocker）
   2. sealed contamination 0（D-3 报告）
   3. 跨 split 泄漏 0（D-2 报告）
   4. 目标行为族 ≥2/3 seed 稳定改善（E-2）
   5. 通用能力与误拒率不显著退化（E-2，阈值配置）
   6. 人工盲测不低于当前最佳基线（human_pair ballot 结果）
   7. manifest 可从 ledger 重建（D-4 manifest + ledger 校验）
2. 产出 promotion report（每条件证据 + 通过/拒绝 + 阻断项）
3. 验收（test_promotion.py）：全过/单项失败/多项失败场景（mock 报告）

## E-4：失败回流（behavior_gap 登记）

**改动**：`core/behavior_gap.py`（新）、`tools/register_behavior_gap.py`（新）

1. **behavior_gap 登记**：`{gap_id: "bg:{family}:{seq}", source: taxonomy|failure|eval_delta, family_id, evidence: [failure record_id / eval case_id], model_delta, status: open|addressed|wontfix, created_at}`——登记即停（**不新增关键词规则**，处置决策由人/蓝图流程做，v2 §5.3）
2. **从 ledger 提取证据**：读 FailureRecord（error_code/reason/taxonomy E1-E7）→ 聚合为候选 gap（半自动）
3. 验收（test_behavior_gap.py）：登记/幂等/证据关联/不产出关键词规则

## E-5：闭环流程编排

**改动**：`tools/utility_loop.py`（新）

1. 分步编排脚本（每步可独立执行）：
   - 步骤 1：数据发布（调用 D-4 publish_dataset，产出带 split anchors 的训练包数据）
   - 步骤 2：训练（Mac 上执行 freeze03 契约；本机打印待执行指令）
   - 步骤 3：评测（eval_delta + diagnostic120/chat02/人工盲测执行指引）
   - 步骤 4：晋升判定（promotion_check）
   - 步骤 5：失败回流（register_behavior_gap）
2. 每步输入/输出/验收点文档化

---

## G5 验收（机制层，训练外置已拍板）

| 晋升条件 | 机制落地 |
|---|---|
| 1. safety blocker 100% | eval_delta 消费 diagnostic120/chat02 blocker 统计 |
| 2. sealed contamination 0 | D-3 报告（已有） |
| 3. 跨 split 泄漏 0 | D-2 报告（已有） |
| 4. 行为族 ≥2/3 seed 改善 | E-2 多 seed delta 聚合判定 |
| 5. 通用/误拒率不退化 | E-2 误拒率 + general_capability delta |
| 6. 人工盲测 ≥ 基线 | human_pair ballot 结果消费（执行在 Mac） |
| 7. manifest 从 ledger 重建 | D-4 manifest + ledger 校验 |

**真实验收**：工具 + 验收用例全过（mock 数据）；真实训练/评测/盲测由用户在 Mac 执行 freeze03 契约后跑 promotion_check。

## 风险与说明

1. **训练外置**：本轮不产出真实训练结果；promotion_check 在无评测数据时输出"数据缺失"而非假通过（诚实失败）
2. **Qwen3.5-4B base 对照**：本地无该基座评测资产（只有 Qwen3-4B revision 1cfa9a72…）——多 seed delta 的 base 对照需用户在 Mac/评测环境重建（freeze03 契约声明 revision）
3. **训练包修正跨仓库**（03/README 基座统一）——同阶段 0 先例，用户已授权此类修正
4. 每子块过完全量 pytest 回归，最后回写施工计划（阶段 5 → ✅、G5 过（机制层））

## 完成后

阶段 5 结项 → 阶段 0-5 全部完成，交叉消融（C3，预算拍板后插入）为剩余可选项。