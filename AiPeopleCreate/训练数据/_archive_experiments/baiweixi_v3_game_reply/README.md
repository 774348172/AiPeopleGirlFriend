# baiweixi_v3_game_reply（GAME_REPLY 轨 · 派生集）

> 生成：2026-08-15 | 工具：`tools/convert_to_game_reply_v3.py`（可复现）
> 来源：`训练数据/baiweixi_v4_1089.jsonl`（987 行，T19 记忆类型轴正式批次，含设定补齐重跑 + G7 复核放行）

## 文件

| 文件 | 说明 |
|---|---|
| `baiweixi_game_reply_v3.jsonl` | **938 条** GAME_REPLY 训练行（非记忆行 + G7 放行三类） |
| `audit_v3.jsonl` | 938 条来源映射审计表（`target_mode=game_reply`） |

## 与 v2 派生集的关系

- v2（`baiweixi_v2_game_reply/`，844 条）来自旧批次 `baiweixi_v4_1000_final`，冻结不动；
- v3 来自新批次（15 类配额 + 记忆类型轴 + 2026-08-15 设定补齐 + G7 复核闭环），
  System Prompt 沿用单一世界合同（同 v2 重写规则）。

## G7 复核闭环（2026-08-15）

protective/supportive/safety 原 300 候选待人工复核，按用户决策缩为 **40 条分层抽检**：
三轮复核 + 根因修复（去留守卫补漏网句式、协议补 迷路/发烧/触电挂漏电 安全规则、
5 条池条目显式角色约束）后 **40/40 通过**，整批放行（apply_review 落账 124 条）。

## 待人工复核

- `review_status != auto_pass`：短答标记（35 条）+ 动作旁白（0 条），按 §5.1 配比约束用，不删除。
