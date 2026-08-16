# 于谦人格 H200 训练包

> 目标：在 H200 上训练 Qwen3-4B 全精度 LoRA → 导出 GGUF Q4_K_M → ship 给 3060 8GB 玩家用 Ollama 跑。

## 工作流

```
H200 训练                          玩家 3060 8GB 推理
─────────                          ──────────────────
1. 装 LF + torch CUDA              1. 装 Ollama
2. 训练 (5554条, ~30min)           2. 拿到 GGUF 文件
3. 合并 adapter + 转 GGUF          3. ollama create + ollama run
4. 得到 yuqian_4b_q4.gguf (2.3GB)  4. 对话 (2.5GB 显存)
```

## 包内容

```
training_package_h200/
├── README.md                         ← 你在读这个
├── bible.yaml                        ← 于谦人格档案
├── canon.json                        ← 于谦事实正典
├── data/
│   ├── aipeople_persona.jsonl        ← 训练数据 5554 条 sharegpt
│   └── dataset_info.json             ← LLaMA-Factory 数据集注册
├── configs/
│   ├── yuqian_4b_h200.yaml           ← 训练配置（全精度, bf16, batch 8）
│   └── yuqian_4b_export.yaml         ← 导出配置（合并 adapter）
└── scripts/
    ├── chat_yuqian.py                ← 训练后对话验证（交互式）
    └── export_gguf.py                ← 合并 adapter + 转 GGUF
```

---

## Step 1：环境准备（H200 上一次性）

```bash
# 创建 conda 环境
conda create -n yuqian python=3.11 -y
conda activate yuqian

# 装 PyTorch（CUDA 12.x）
pip install torch --index-url https://download.pytorch.org/whl/cu121

# 装 LLaMA-Factory
pip install llamafactory[torch]

# 装 peft（合并 adapter 用）
pip install peft

# clone llama.cpp（转 GGUF 用）
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp && make && cd ..

# 验证
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
python -c "import llamafactory; print('LF:', llamafactory.__version__)"
```

## Step 2：训练

```bash
cd training_package_h200

# 训练（约 30 分钟，H200 上 4B 全精度很快）
llamafactory-cli train configs/yuqian_4b_h200.yaml
```

训练参数：
- 模型：Qwen3-4B 全精度（不量化）
- LoRA rank 16, alpha 32
- 3 epoch, batch 8, cutoff 2048
- bf16, template qwen3_nothink（关思考）
- 数据：5554 条（5479 Haruhi + 75 闲聊）

训练产出：`outputs/yuqian_4b/adapter_model.safetensors`（~65MB）

## Step 3：对话验证（训练后先测效果）

```bash
# 交互式对话，直接加载 adapter + 基座
python scripts/chat_yuqian.py
# 或指定 adapter 路径
python scripts/chat_yuqian.py outputs/yuqian_4b
```

输入问题，看于谦口吻/回答效果。满意后进 Step 4。

## Step 4：合并 + 转 GGUF（ship 给玩家）

```bash
# 合并 adapter 到基座 + 转 GGUF Q4_K_M
python scripts/export_gguf.py
```

产出：`outputs/yuqian_4b_q4.gguf`（~2.3GB）

这就是 ship 给玩家的最终产物。

## Step 5：玩家侧部署（3060 8GB）

把 `yuqian_4b_q4.gguf` 传给玩家，玩家执行：

```bash
# 1. 装 Ollama（https://ollama.com，双击安装）

# 2. 创建模型
ollama create yuqian -f - <<EOF
FROM ./yuqian_4b_q4.gguf
SYSTEM "你是于谦，北京人，相声演员，郭德纲的捧哏搭档。1969年生。性格随和，爱养动物爱烫头。"
PARAMETER temperature 0.8
PARAMETER num_ctx 2048
EOF

# 3. 对话
ollama run yuqian
```

玩家显存占用：~2.5GB（权重 2.3GB + KV cache），和游戏共享可行。

---

## 训练配置详解

### yuqian_4b_h200.yaml 要点

| 参数 | 值 | 理由 |
|---|---|---|
| model_name_or_path | Qwen/Qwen3-4B | 8GB 玩家卡的天花板 |
| quantization_bit | 不设 | H200 141GB 够装全精度 |
| template | qwen3_nothink | 关闭 reasoning 思考 |
| lora_rank | 16 | H200 可用更大 rank 提升人格容量 |
| cutoff_len | 2048 | H200 够长上下文 |
| bf16 | true | H200 原生支持 |
| batch_size | 8 | 大显存用大 batch 加速 |
| num_train_epochs | 3 | 防过拟合 |
| optim | adamw_torch | H200 不需要 8-bit 优化器 |

### 如果想训更大模型（H200 可以）

改 `model_name_or_path` 即可，H200 141GB 全装得下：

| 模型 | 训练时间（估计） | 玩家最终跑的 |
|---|---|---|
| Qwen3-4B | ~30 分钟 | 4B Q4 GGUF |
| Qwen3-7B | ~1-2 小时 | 7B Q4 GGUF（3060 12GB 可跑） |
| Qwen3-14B | ~3-5 小时 | 14B 需蒸馏到 4B 后 ship |

玩家 3060 8GB 最终只能跑 4B Q4，所以建议直接训 4B。

---

## 当前数据已知问题（详见《版本效果对比与问题分析》）

1. 95% 单轮数据 → 多轮对话弱（循环/丢上下文）
2. 平均回复 18 字 → 短俏皮话无逻辑支撑
3. 75 条闲聊被 5479 条淹没（1.3%）
4. Haruhi 事实 AI 生成（不一致）

### 可选优化（H200 上有算力可以做）

| 优化 | 做法 | 耗时 |
|---|---|---|
| 降 Haruhi 占比 | 取 1000-1500 条 + 闲聊 200-300 条 | 改配置即可 |
| 过滤短回复 | 去掉 <10 字的 Haruhi 样本 | 一行 Python |
| 加事实一致性 | 统一"养几只动物"等 canon 问答 | 用 glm-5.2 生成 |
| 蒸馏 14B→4B | H200 训 14B 当老师 → 蒸馏到 4B | 3-5 小时 |

---

## 文件清单（ship 给玩家的最终产物）

训练完成后，玩家只需要一个文件：

```
yuqian_4b_q4.gguf    2.3 GB    Ollama 一行命令加载
```

加上一行 Ollama 命令，玩家就能和于谦对话。
