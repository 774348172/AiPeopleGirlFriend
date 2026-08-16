# 训练包 Qwen2.5-7B-Instruct —— 白未晞（RTX 4090 24GB 方案 A）

> **基座**：`Qwen/Qwen2.5-7B-Instruct`（⚠ 首次训练自动下载 ~15GB，可设 `HF_ENDPOINT=https://hf-mirror.com`；
> 本机缓存暂无，需先下载或替换为本地路径）
> **数据**：`data/baiweixi_ready.jsonl`（**1973 条**（2026-08-15 v4 最终批次，话题级去重版：每话题保留 judge 分 top 2；全量 2882 保留在生成器仓库））
> **框架**：LLaMA-Factory（SFT + LoRA），模板 `qwen`（Qwen2.5 ChatML）

## 为什么是方案 A（bf16 全精度）

训练精度无损 → 推理量化只压一次 → **量化后保留人格最多**。
对比 8G 的 4bit QLoRA：4bit 训练 + Q4 推理 = 双重量化，人格会再糊一层。
**4090 24G 显存够跑 bf16，这是效果最优的路径。**

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
llamafactory-cli train configs/baiweixi_7b_4090.yaml
```

配置要点（24G 显存）：
- **bf16 全精度**（不量化训练）
- LoRA rank 32 / alpha 64 / all targets
- batch 2 × grad_accum 8（等效 batch 16），cutoff 2048，gradient_checkpointing（显存 ~22GB ✅）
- **2 epoch** + lr 5e-5（数据量为旧 1190 条的 2.4 倍，2 epoch 充分；时长约 12-16 小时）
- ⚠ Windows：`preprocessing_num_workers: 1`

## 训练后部署流程

```bash
# 1. 合并 adapter → bf16 完整模型（4090 可 GPU 合并）
llamafactory-cli export configs/baiweixi_7b_export.yaml

# 2. HF → GGUF（F16）
python llama.cpp/convert_hf_to_gguf.py outputs/baiweixi_7b_merged \
    --outfile outputs/baiweixi_7b_f16.gguf --outtype f16

# 3. 量化 Q5_K_M（8G 推理推荐档；显存富余可 Q6_K）
llama.cpp/llama-quantize outputs/baiweixi_7b_f16.gguf \
    outputs/baiweixi_7b_q5_k_m.gguf Q5_K_M

# 4. Ollama 部署
ollama create baiweixi-7b -f Modelfile   # TEMPLATE 用 qwen ChatML
```

## 验收（必须推理形态复测）

```bash
python scripts/chat_baiweixi.py outputs/baiweixi_7b
python scripts/check_acceptance.py outputs/baiweixi_7b
ollama run baiweixi-7b "伤好了就走吗？"
```

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫白未晞 |
| 你是猫妖吗？ | 是（按正典坦白或含糊） |
| 你会喵喵叫吗？ | 不会卖萌（无喵口癖） |
| 你住哪？ | 松江府（正文不称上海） |
| 伤好了就走吗？ | **隐晦表达不想走**（"再说吧""还没打算走""这里还行"），不说"伤好会走" |

## 环境

- 硬件：RTX 4090 24GB（或 4080 Super 16G，batch 降 1 + cutoff 1536）
- 系统：Windows 10/11 或 Linux；Python 3.10-3.12
- 依赖：`pip install llamafactory accelerate peft transformers`
- 基座：`Qwen/Qwen2.5-7B-Instruct`（首次自动下载 ~15GB）
