# 训练包 Qwen3.5-4B —— 白未晞（RTX 3060/3070 8G QLoRA）

> **基座**：`Qwen/Qwen3.5-4B`（本机缓存 ✅；VLM 架构，视觉塔自动冻结，纯文本训练）
> **数据**：`data/baiweixi_ready.jsonl`（**1973 条**（2026-08-15 v4 最终批次，话题级去重版：每话题保留 judge 分 top 2；全量 2882 保留在生成器仓库））
> **框架**：LLaMA-Factory（SFT + LoRA），模板 `qwen3_nothink`（关思考）

## 数据说明（2026-08-15 v4 最终批次 · 话题级去重版）

- **1973 条** = 全量 2882 行（T19 记忆类型轴 987 + 扩池重跑 1659 + G7 放行 236）
  经**话题级去重**：每话题保留 judge 分 top 2，消除跨批次/轮转重复（全量 2882 保留在生成器仓库）。
- 全 15 类型：casual 574 / romance 427 / emotion 209 / protective 184 / item 118 /
  memory 98 / identity 76 / supportive 61 / general 57 / safety 36 / canon_qa 34 /
  correction 29 / vague 28 / quiet_company 21 / boundary 21。
- 记忆类型：persona 110 / item 118 / general 57 / special 98 / 日常 1590。
- **记忆行（98 条）锚带记忆证据帧**（与运行时 selected_memory_frame 同构，A 方案）。
- 2026-08-15 设定补齐已生效：去留设定改写（内心不想走 + 隐晦表达 + 不嘴硬）、
  现代物品知识状态、通识边界、能力封闭条款、特殊记忆细节（first_help=钥匙）。
- 话题记账：已用话题 1087 个登记于生成器 `训练数据/_topic_ledger.jsonl`；
  未用话题 26 个（casual 2/romance 10/identity 2/emotion 1/protective 2/correction 2/
  quiet_company 2/canon_qa 3/memory 1），下次扩充用 `--topics` 生成互补批次（零重复）。
## 训练

```bash
llamafactory-cli train configs/baiweixi_4b_8g.yaml
```

配置要点（8G 显存）：
- 4bit QLoRA（nf4）+ LoRA rank 16 / alpha 32 / all targets
- batch 1 × grad_accum 4（等效 batch 4），cutoff 2048
- **1 epoch**（约 20 小时，Qwen3.5-4B 与 Qwen3-4B 同量级 ~100s/微步，720 微步）→ 验证人格 → 满意后 RESUME 续训
- lr 1e-4 cosine，bf16，gradient_checkpointing，`adamw_bnb_8bit`（⚠ Windows 勿用 paged 版）

## 训练后部署流程

```bash
# 1. 合并 adapter → 完整模型（8G 用 CPU 合并）
llamafactory-cli export configs/baiweixi_4b_export.yaml

# 2. HF → GGUF（F16）
python llama.cpp/convert_hf_to_gguf.py outputs/baiweixi_4b_merged \
    --outfile outputs/baiweixi_4b_f16.gguf --outtype f16

# 3. 量化 Q4_K_M（8G 推理推荐档）
llama.cpp/llama-quantize outputs/baiweixi_4b_f16.gguf \
    outputs/baiweixi_4b_q4_k_m.gguf Q4_K_M

# 4. Ollama 部署
ollama create baiweixi-4b -f Modelfile   # TEMPLATE 用 qwen3 关思考模板
```

## 验收（必须推理形态复测）

```bash
python scripts/chat_baiweixi.py outputs/baiweixi_4b       # adapter 快速验证
python scripts/check_acceptance.py outputs/baiweixi_4b     # 5 项自动验收
ollama run baiweixi-4b "伤好了就走吗？"                     # GGUF 部署后复测
```

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊） |
| 你会喵喵叫吗？ | 不会卖萌（无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | **隐晦表达不想走**（"再说吧""还没打算走""这里还行"），不说"伤好会走" |

## 环境

- 硬件：RTX 3060 8G / 3070 8G（Ampere，sm_86）
- 系统：Windows 10/11；Python 3.10-3.12
- 依赖：`pip install llamafactory bitsandbytes accelerate peft transformers`
- 基座已在 `~/.cache/huggingface`（离线可设 `HF_HUB_OFFLINE=1`）
- ⚠ Windows：`preprocessing_num_workers: 1`（多进程 map 会丢数据）
