# judge_gold：人工 gold 校准集（阶段 3 C-4）

> G3 验收："judge 换序一致率 ≥ 阈值 + 人工 gold 校准集达标"。
> 机制已就绪（`tools/calibrate_judge.py` + `data_gen_v4/core/judge.py`）；
> 本目录存放人工标注的 gold 数据，供真实校准（有 API key 时执行）。

## 格式

每条一行 JSON，两种条目：

1. **单候选打分**（衡量 judge 各轴精度 / MAE）：
```json
{"candidate": {"sample_id": "gold-001", "input": {"scene": "...", "topic": "..."}, "target": {"messages": [{"role": "human", "content": "..."}, {"role": "assistant", "content": "..."}]}}, "gold": {"scores": {"instruction_fulfillment": 0.9, "persona_naturalness": 0.8, "relationship_fit": 0.85, "conversational_progress": 0.7, "style_restraint": 0.9}}}
```

2. **配对偏好**（衡量换序一致率 + 人机一致率）：
```json
{"pair": [candidate_a, candidate_b], "gold": {"preferred": "a"}}
```

## 建议规模

- 单候选：~20 条（覆盖 casual/romance/safety/protective/supportive 等任务）
- 配对：~20 对（含明显好坏对与难分对，难分对用于测换序一致性）

## 使用

```bash
# 机制演示（ScriptedJudge，无模型调用）
python tools/calibrate_judge.py --gold judge_gold/gold_v1.jsonl

# 真实校准（需 .env 配置 API key；LLMJudge 接入见 tools/calibrate_judge.py main）
# 在 main 中把 ScriptedJudge 替换为 LLMJudge(model_pool) 后运行
```
