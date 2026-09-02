# 当前 LoRA Q4 vs GPT-5.6-sol：60 轮多轮对话测试说明

> 测试日期：2026-08-22  
> 测试性质：实名、单轨迹、逐轮模型调用、用户人工审核  
> 自动质量结论：无

## 1. 正式对照

| 测试臂 | 工件或调用面 | 调用数 | 历史 |
|---|---|---:|---|
| 当前 LoRA Q4 | `baiweixi-7b-fix:latest`，Ollama digest `5bfda0d813c5a3565baa31cfd94cf2cd9f10a3a86802321b152dfc4d40757e92` | 60 | 该臂最近 6 条对白 |
| GPT-5.6-sol | 60 个全新、互相隔离的 Codex child-agent 调用，`reasoning_effort=low` | 60 | 该臂最近 6 条对白 |

两侧共享同一份无 Oracle 输入。输入 SHA256：

```text
0593a2606b470b7dd4e0ab34cf59ac96095d996fb2022bb8f8ba4ac2aa2e6720
```

每轮输入只包含当前 system、该臂最近 6 条已提交的 user/assistant 消息和当前玩家原话。GPT 每轮使用一个全新实例，当前调用看不到未来轮次，也看不到 6 条之前的对白。

## 2. Oracle 隔离

候选生成输入明确排除了：

- `expectation`；
- `required_groups`；
- `forbidden_facts`；
- 既有模型回复；
- 既有人工审核结果。

权威世界会进入 system，因为正式产品调用同样会向回复模型提供当前世界。期望处理只在全部候选完成后重新关联到审核页面。

## 3. 被排除的第一批 GPT 候选

最初生成的 `gpt5_6_sol_responses.jsonl` 来自一个隔离任务内的 60 轮逻辑生成，但该任务一次读取了完整输入文件，理论上能看到未来轮次。因此它不进入正式 `report.json`，只保留为过程审计证据。

正式报告只读取：

- `gpt5_6_sol_responses_sequential.jsonl`；
- `gpt_sequential_provenance.json`。

合并脚本会强制检查 `future_turns_visible=false`、`history_limit_messages=6` 和 `call_count=60`，否则拒绝生成报告。

## 4. 公平性边界

- 这是产品体验轨迹，不是 60 个独立会话；每个模型自己的回复会进入自己的后续历史，所以第二轮后两侧 Prompt 自然分叉。
- LoRA 侧是本地 Ollama 推理；GPT 侧是 Codex 内部模型调用，不是 OpenAI API 后端。GPT 的 API 延迟、token、费用、seed 和供应商响应元数据不可用。
- Codex 宿主指令无法与本地 ChatML 做到字节完全相同，因此本测试适合比较当前可见多轮回复质量，不适合比较纯权重因果、API 性能或价格。
- 只有一条 60 轮人为轨迹，不能据此估计真实玩家总体错误率。
- 本批不做自动语义裁决。关键词、长度或模型身份都不自动决定好坏，最终结果由用户在实名审核页面逐轮标记。

## 5. 证据

- 正式完整报告：[`report.json`](./report.json)
- 无 Oracle 输入：[`shared_inputs.jsonl`](./shared_inputs.jsonl)
- 输入 Manifest：[`input_manifest.json`](./input_manifest.json)
- 严格 GPT 回复：[`gpt5_6_sol_responses_sequential.jsonl`](./gpt5_6_sol_responses_sequential.jsonl)
- 严格 GPT 来源：[`gpt_sequential_provenance.json`](./gpt_sequential_provenance.json)
- LoRA/GPT 合并脚本：[`../../../tools/run_lora_gpt56_60turn_comparison.py`](../../../tools/run_lora_gpt56_60turn_comparison.py)
- 审核页服务：[`../../../tools/lora_gpt56_60turn_app.py`](../../../tools/lora_gpt56_60turn_app.py)
