# 白未晞 LoRA Adapter 因果隔离测试报告

> 日期：2026-08-21T18:21:56.337205+08:00
> 性质：Adapter 净效应 pilot；同一 F16 基座层、同一 raw ChatML、同一 Prompt 和 seed

## 1. 结论

在当前固定测试语料上，加载 Adapter 后承接通过率低于裸基座，且对照只改变了 Adapter，因此当前 LoRA Adapter 是该回归的因果因素。这不等于裸基座永不漂移，也不等于 LoRA 是所有线上漂移的唯一来源。

## 2. 对照是否真实成立

- 裸基座模型：`qwen25-7b-base-f16-gguf-eval:latest`，Ollama manifest digest `af3c7320e7dcf1d9352a23622c779971df862ebc9461812d07d6119401c53ba3`。
- Adapter 模型：`baiweixi-7b-adapter-f16-gguf-eval:latest`，Ollama manifest digest `71b205dcfdc8616f6ceb2179d1f908b1609722f8773c0f426aaa91dd23823670`。
- 两者共享 F16 基座层 `a7bedda095d41801f76f1a8dc7ad51166bbe09bc3bc4b6bd14546441e23bdf57`；只有后者额外加载 Adapter 层 `3c8693542793b2aeccd3960838782e43d7c4bf78c54744a39fe2f9035e6eb772`。
- 两个 manifest digest 不同；同 Prompt/seed 的预检输出也不同。因此本次没有复用此前 Adapter 被静默忽略的无效模型。

## 3. 测试控制

- 上下文：6 个；seed：12 个；每个模型 72 次。
- 覆盖 turn 11/12、无历史与 6 轮历史，以及由裸基座或 LoRA 轨迹产生的历史。每一对调用收到字节完全相同的 Prompt。
- 两边都调用 `/api/generate`，设置 `raw=true`，统一 Qwen ChatML，并使用相同 stop、temperature、top_p、repeat_penalty、num_ctx、num_predict 和 seed。
- 唯一有意改变的是：是否加载当前训练产物转换出的 F16 LoRA Adapter。

## 4. 结果

| 工件 | 通过/总数 | 通过率 | Wilson 95% CI | 输入外依据 | 通过但带旧话题 |
|---|---:|---:|---:|---:|---:|
| 裸基座 F16 | 54/72 | 75.0% | 63.9%-83.6% | 2 | 7 |
| 裸基座 + Adapter F16 | 31/72 | 43.1% | 32.3%-54.6% | 20 | 8 |

Adapter 相对裸基座的绝对通过率变化：**-31.9%**。

### 成对结果

| 结果 | 数量 |
|---|---:|
| 两边都通过 | 29 |
| 仅裸基座通过 | 25 |
| 仅 Adapter 通过 | 2 |
| 两边都失败 | 16 |

成对符号检验（只看结果不一致的 seed 对）双侧 `p=5.65e-06`。该值只描述此固定语料和 seed，不把重复 seed 当作独立玩家分布。

## 5. 分上下文失败数

| Context | 裸基座失败 | Adapter 失败 |
|---|---:|---:|
| `t11_h0_shared` | 10 | 12 |
| `t11_h6_base` | 2 | 6 |
| `t11_h6_lora` | 3 | 6 |
| `t12_h0_shared` | 3 | 12 |
| `t12_h6_base` | 0 | 2 |
| `t12_h6_lora` | 0 | 3 |

## 6. 公平性与结论边界

这次能隔离当前 Adapter 在这组问题上的净效应，但语料仍只有一个合成对话的 6 个关键上下文，不能代表所有玩家对话；12 个 seed 也不是 12 个独立问题。自动规则只判定当前纠正是否被承接，并单独记录输入外依据，仍需人工复核临界回答。F16 GGUF 转换和 Ollama 动态 Adapter loader 属于共同推理链；本报告不会把结果外推成‘所有 LoRA 都会导致漂移’。

## 7. 证据

- 原始结果：[`./report.json`](./report.json)
- 上下文来源：[`../paired_raw_context_20260821-173515/report.json`](../paired_raw_context_20260821-173515/report.json)
- 脚本：[`../../../tools/run_baiweixi_adapter_causality_ab.py`](../../../tools/run_baiweixi_adapter_causality_ab.py)
