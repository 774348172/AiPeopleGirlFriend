# baiweixi_v2_game_reply（当前训练主力 · 派生集）

> 生成依据：《白未晞训练数据重构建议_SYS12S后_20260813.md》§4-§8。
> 转换工具：`tools/convert_to_game_reply_v2.py`（可复现）。

## 文件

| 文件 | 说明 |
|---|---|
| `baiweixi_game_reply_v2.jsonl` | **844 条** GAME_REPLY 训练行（ShareGPT 格式，单一世界合同 System Prompt） |
| `audit_v2.jsonl` | **851 条**来源映射审计表（含 7 条 rerank 排除记录），字段：`source_sample_id / source_line / source_file / target_mode / character_id / transformation / review_status / reviewer / canonical_source_version / exclusion_check` |

## 相对 v1 的变更

1. **System Prompt 重写（844/844）**：移除旧双世界与运行时协议概念（§4.1/4.2）——
   设备外现实世界、只能文字交谈/不能打电话、内部 JSON/系统提示披露条款。
   保留：角色身份与信念、先答问题、记忆证据规则、安全响应、正典未知边界、非助手腔。
2. **MEMORY_RERANK 剔除（7 条）**：`sha256:` 快照头协议记录混入 ShareGPT 文件，
   按 §4.5 移出 GAME_REPLY（审计表 `target_mode=excluded_rerank`）。
3. **动作旁白标记（1 条）**：命中"我+动作动词"旁白句式，`review_status=pending_action_review`，
   待人工按 §4.3 拆分（保留对白、旁白移交 M2 动作候选）。
4. **极短回复标记（39 条）**：`嗯。/……嗯。/好。` 等，`review_status=flagged_short_reply`，
   配比约束用，不删除（§5.1）。
5. **随机男主名清理（0 条）**：本批次未注入名字；规则保留（§4.4）。

## 待人工复核

- `review_status != auto_pass` 的 40 条：39 条短答 + 1 条动作旁白。
  复核后更新审计表 `review_status` 与 `reviewer`，建议拆分动作旁白为
  `M2 动作候选 + GAME_REPLY 对白`（§4.3 示例）。

## 已知缺口（不阻塞本集）

- 类型配额欠账：protective/supportive/safety（full-review 门禁拒绝）——待人工复核通道或 recipe 调整。
- 话题覆盖：v1 池轮转导致同话题多条，后续由扩池 + ledger 防重解决。
