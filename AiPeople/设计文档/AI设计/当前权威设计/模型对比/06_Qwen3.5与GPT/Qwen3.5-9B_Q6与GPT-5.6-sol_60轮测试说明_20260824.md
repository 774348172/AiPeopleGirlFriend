# Qwen3.5-9B Q6 与 GPT-5.6-sol：60轮人工审核测试说明

> 测试日期：2026-08-24  
> 测试性质：实名、单轨迹、逐轮模型调用、用户人工审核  
> 自动质量结论：无

## 1. 测试对象

| 测试臂 | 工件或调用面 | 调用数 | 历史 |
|---|---|---:|---|
| Qwen3.5-9B Q6_K | Ollama `qwen3.5-9b-q6:latest`，digest `100770e1eb3d33361b14d697967e413923d6e0547c9ee0f939ca4d4bc3e963d2` | 60 | 该臂最近6条对白 |
| GPT-5.6-sol | 原测试中60个全新、互相隔离的Codex调用 | 60 | 该臂最近6条对白 |

两侧复用冻结的同一套无Oracle玩家输入。9B侧重新逐轮生成；GPT侧复用已经通过来源校验的隔离结果。

## 2. 模型工件

- 来源：`unsloth/Qwen3.5-9B-GGUF`
- 文件：`Qwen3.5-9B-Q6_K.gguf`
- 文件大小：`7,458,301,152`字节（6.95GiB）
- SHA256：`91898433cf5ce0a8f45516a4cc3e9343b6e01d052d01f684309098c66a326c59`
- SHA256与发布方LFS对象一致：是
- Ollama识别架构：`qwen35`
- 参数量：9.0B
- 量化：`Q6_K`

## 3. 生成设置

- Ollama版本：`0.32.14`
- 接口：`/api/chat`
- 原生消息模板：是
- 思考输出：关闭
- `num_ctx=4096`
- `num_predict=180`
- `temperature=0.75`
- `top_p=0.9`
- `repeat_penalty=1.1`
- 每轮固定seed：`2026082200 + 轮次`
- 每轮9B自己的回复进入其下一轮历史

没有把`expectation`、禁止事实、评分规则、GPT回复或人工审核结果传给9B。

## 4. 硬件实测

| 项目 | 结果 |
|---|---|
| 本次GPU | NVIDIA GeForce RTX 4090 24GB |
| Ollama加载大小 | 6.9GB |
| Ollama处理器 | `100% GPU` |
| 上下文 | 4096 |
| 生成完成 | 60/60 |
| 空回复 | 0 |
| 泄露思考内容 | 0 |
| 平均玩家可见耗时 | 1223.51ms |
| 中位耗时 | 1148.91ms |
| P95耗时 | 1618.85ms |
| 平均生成速度 | 102.2 token/s |
| 模型加载后整机显存占用快照 | 8079MiB / 24564MiB |

模型本体和4K上下文的实测占用明显低于16GB，但不同操作系统、显示器、桌面程序和驱动会改变总占用。本报告只证明当前工件具备16GB部署余量，不等于所有长上下文和并发设置都不会溢出。

## 5. 公平性边界

- 这是产品体验对比，不是同权重因果试验。
- 两侧共享同一输入合同，但各自回复进入各自后续历史，第二轮后历史自然分叉。
- 9B使用原生Qwen3.5模板，与27B测试方式一致。
- GPT侧是Codex内部调用，不是正式OpenAI API，不比较GPT延迟、费用和吞吐。
- 一条60轮人造轨迹不能估算真实玩家总体错误率。
- 本测试不做自动语义裁决，最终好坏由用户逐轮人工审核。

## 6. 证据

- 完整报告：`report.json`
- 无Oracle输入：`shared_inputs.jsonl`
- 输入Manifest：`input_manifest.json`
- GPT隔离回复：`gpt5_6_sol_responses_sequential.jsonl`
- GPT来源证明：`gpt_sequential_provenance.json`
- 运行脚本：`AiPeople/tools/run_qwen35_9b_q6_gpt56_60turn_comparison.py`
- 审核服务：`AiPeople/tools/qwen35_9b_q6_gpt56_60turn_app.py`
