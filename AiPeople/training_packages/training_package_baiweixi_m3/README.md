# Apple M3 训练包 Qwen3.5 —— 白未晞 (Qwen3.5-4B LoRA)

> 在 Mac 上完成全部训练 → 导出 GGUF Q4_K_M（~2.3GB）→ 本地 Ollama 跑。
> 框架：**MLX**（Apple Silicon 原生，全精度 LoRA，无需量化训练）。
> 基座：**Qwen3.5-4B**（VLM 架构，纯文本训练；MLX 需 mlx-lm 支持 Qwen3_5 架构，首次运行如报架构不支持先 `pip install -U mlx-lm`）。

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | Apple Silicon（M3/M3 Pro/M3 Max；内存 ≥ 16GB，推荐 36GB+） |
| 系统 | macOS 14+ |
| Python | 3.10–3.12 |
| 依赖 | `pip install mlx mlx-lm huggingface_hub` |

## 数据说明（2026-08-10 版）

| 文件 | 条数 | 口径 |
|---|---|---|
| `baiweixi_cal40_final.jsonl` | 41 | 校准批次（人工复核通过） |
| `baiweixi_diverse40_final.jsonl` | 40 | 多样性批次（人工复核通过） |
| `baiweixi_name_test_final.jsonl` | 22 | 称呼测试批次 |
| `baiweixi_newsetting30_final.jsonl` | 25 | 新设定批次 |
| `baiweixi_newsetup30b_final.jsonl` | 27 | 设定批次 b |
| `baiweixi_review20_final.jsonl` | 20 | ⭐ 验证集（人工 20 条全通过） |
| `baiweixi_review30_final.jsonl` | 25 | 复核批次 |
| `baiweixi_review40_final.jsonl` | 35 | 复核批次 |
| `baiweixi_setting40_final.jsonl` | 36 | 设定批次 |
| `baiweixi_v100_final.jsonl` | 75 | 100 条大批次（通过 75） |
| `baiweixi_v4_1000_final.jsonl` | 844 | ⭐ 1000 条大批次（已剔除 7 条 rerank） |
| **合计** | **1190** | 全部 REPLY 数据 |

> **训练/验证切分**：01 脚本产出 train 1081 / valid 129（review20 为验证锚 + 训练池 9:1 留出）。
> **MEMORY_RERANK 剔除**：v4_1000 中 7 条 rerank 数据已自动剔除（freeze02 合同禁止混入 REPLY SFT）。
> **CHAT-01 防泄漏**：`eval_exclusions/chat01_baiweixi_v1.json`（白未晞评测集未冻结，当前空契约，检查结构保留；评测集冻结后填充）。

## 称呼机制（与秦未晞不同，重要）

- 白未晞对玩家**不绑定单一名字**：生成时 80% 零称呼 + 20% 多样名字注入（浩然/阿伟/林墨等）。
- 训练数据自带称呼，01 不统一改写——**多样名字让模型不绑定单一名字**。
- 验收时不要用"我叫浩然吗"这类单一名字问题（白未晞会按上下文/注入名回答）。

## 训练

```bash
# 首次训练（iters 自动 = 1.5 × train 条数）
./02_train.sh
# 追加数据后续训
RESUME=1 ./02_train.sh
```

## 融合与部署

```bash
./03_fuse_gguf.sh   # LoRA 融合 → GGUF → Q4_K_M
./04_ollama.sh      # 创建 Ollama 模型 + 冒烟
```

## 验收清单（融合导出前必测）

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊，视话题） |
| 你会喵喵叫吗？ | 不会卖萌（白未晞无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | 嘴上说走但不决绝，透出不想走 |
