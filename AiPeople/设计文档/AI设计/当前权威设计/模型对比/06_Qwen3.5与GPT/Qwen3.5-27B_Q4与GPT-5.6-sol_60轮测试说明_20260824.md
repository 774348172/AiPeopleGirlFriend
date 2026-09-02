# Qwen3.5-27B Q4 与 GPT-5.6-sol：60轮人工审核测试说明

> 测试日期：2026-08-24  
> 测试性质：实名、单轨迹、逐轮模型调用、用户人工审核  
> 自动质量结论：无

## 1. 测试对象

| 测试臂 | 工件或调用面 | 调用数 | 历史 |
|---|---|---:|---|
| Qwen3.5-27B Q4 | Ollama `qwen3.5:27b`，digest `7653528ba5cba4dd8e19da24aaddc7f4d0b5ecd93571c0825dfd4137958ec06e` | 60 | 该臂最近6条对白 |
| GPT-5.6-sol | 原测试中60个全新、互相隔离的Codex调用 | 60 | 该臂最近6条对白 |

两侧复用冻结的同一套无Oracle玩家输入。Qwen3.5侧重新逐轮生成；GPT侧复用已经通过来源校验的隔离结果，不重新调用。

## 2. Qwen3.5生成设置

- Ollama版本：`0.32.14`
- 接口：`/api/chat`
- 模型原生消息模板：是
- 思考输出：关闭
- `num_ctx=4096`
- `num_predict=180`
- `temperature=0.75`
- `top_p=0.9`
- `repeat_penalty=1.1`
- 每轮固定seed：`2026082200 + 轮次`
- 每轮模型自己的回复进入该模型下一轮历史

没有把`expectation`、禁止事实、评分规则、GPT回复或人工审核结果传给Qwen3.5。

## 3. 4090实测

| 项目 | 结果 |
|---|---|
| GPU | NVIDIA GeForce RTX 4090 24GB |
| Ollama模型大小 | 16GB（模型层下载大小17.42GB） |
| Ollama处理器 | `100% GPU` |
| 上下文 | 4096 |
| 生成完成 | 60/60 |
| 空回复 | 0 |
| 泄露思考内容 | 0 |
| Qwen3.5平均玩家可见耗时 | 1584.99ms |
| 模型加载后整机显存占用快照 | 18128MiB / 24564MiB |

测试时桌面、ComfyUI等程序仍处于运行状态，没有为了测试擅自关闭用户程序。该延迟只代表当前机器状态，不是干净性能基准。

## 4. 公平性边界

- 这是产品体验对比，不是同权重因果试验。Qwen3.5和GPT的推理后端、模板、seed能力和宿主环境不同。
- 两侧共享同一输入合同，但各自回复会进入各自后续历史，因此第二轮后对话历史自然分叉。
- Qwen3.5使用原生模板，因为强行套用旧Qwen2.5 LoRA的Raw ChatML会不公平地偏离其正式用法。
- GPT侧是Codex内部调用，不是正式OpenAI API，因此不比较GPT延迟、费用和吞吐。
- 一条60轮人造轨迹不能估算真实玩家总体错误率。
- 本测试不做自动语义裁决，最终好坏由用户逐轮人工审核。

## 5. 证据

- 完整报告：`report.json`
- 无Oracle输入：`shared_inputs.jsonl`
- 输入Manifest：`input_manifest.json`
- GPT隔离回复：`gpt5_6_sol_responses_sequential.jsonl`
- GPT来源证明：`gpt_sequential_provenance.json`
- 运行脚本：`AiPeople/tools/run_qwen35_27b_gpt56_60turn_comparison.py`
- 审核服务：`AiPeople/tools/qwen35_27b_gpt56_60turn_app.py`
