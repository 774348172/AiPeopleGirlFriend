# 于谦人格训练包

> 换机器训练用。包含数据 + 配置 + 对话脚本 + 两套步骤（H200 / M3 Max）。

## 包内容

```
training_package/
├── README.md                 ← 你在读这个
├── bible.yaml                ← 于谦人格档案（口吻/事实规范）
├── canon.json                ← 于谦事实正典
├── data/
│   ├── aipeople_persona.jsonl   ← 训练数据 5554 条 sharegpt
│   └── dataset_info.json        ← LLaMA-Factory 数据集注册
├── configs/
│   ├── yuqian_4b_h200.yaml      ← H200 训练配置（全精度, bf16, 大 batch）
│   └── yuqian_4b_m3max.yaml     ← M3 Max 训练配置（全精度, fp16, MPS）
└── scripts/
    └── chat_yuqian.py           ← 训练后对话验证脚本
```

## 数据概况

- 角色：于谦
- 数据量：5554 条 sharegpt
- 构成：5479 条 ChatHaruhi Q&A（口吻底子）+ 75 条 glm-5.2 生成的闲聊（自由对话）
- 格式：`{"conversations": [{"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]}`
- 平均回复长度：18 字（捧哏风格短回复）
- 单轮占比 95%，多轮占比 5%

---

## 方案 A：NVIDIA H200（推荐，最快最稳）

H200 = 141GB HBM3e，CUDA 原生，LLaMA-Factory 直接跑。全精度训练，不需要量化。4B 训练预计 **30-60 分钟**。

### 步骤

```bash
# 1) 环境准备
conda create -n yuqian python=3.11 -y
conda activate yuqian

# 2) 安装 PyTorch（CUDA 版，H200 需 CUDA 12.x）
pip install torch --index-url https://download.pytorch.org/whl/cu121

# 3) 安装 LLaMA-Factory
pip install llamafactory[torch]

# 4) 把 training_package 目录上传到 H200 机器
#    假设放在 ~/yuqian/

# 5) 训练
cd ~/yuqian
llamafactory-cli train configs/yuqian_4b_h200.yaml

# 6) 训完后对话验证
python scripts/chat_yuqian.py outputs/yuqian_4b
```

### H200 配置要点
- **不量化**（去掉 quantization_bit）——141GB 够装全精度 4B
- **bf16: true**——H200 原生支持
- **batch_size: 8**——大显存用大 batch 加速
- **cutoff_len: 2048**——显存够，用长上下文
- **rank: 16**——更大 LoRA rank 提升人格容量
- 预计训练时间：30-60 分钟（4B × 5554 条 × 3 epoch）

### H200 上可以试更大模型
H200 141GB 可以直接训 7B/14B/32B（改配置里的 `model_name_or_path`）：
- Qwen3-7B：~1 小时
- Qwen3-14B：~2-3 小时
- Qwen3-32B：~4-8 小时

---

## 方案 B：Apple M3 Max（可行但有限制）

M3 Max = 36-64GB 统一内存，MPS（Metal），**无 CUDA**。

### ⚠️ 关键限制
1. **bitsandbytes 不支持 MPS** → 不能用 4-bit QLoRA，只能全精度 LoRA
2. **LLaMA-Factory 对 MPS 支持不完善**——可能遇到兼容问题
3. 4B 全精度训练 = ~8GB 权重 + 训练开销，M3 Max 36GB 内存够用
4. 预计训练时间：2-4 小时（MPS 比 CUDA 慢，但比 CPU 快很多）

### 步骤（LLaMA-Factory 方式，如果 MPS 支持）

```bash
# 1) 环境
conda create -n yuqian python=3.11 -y
conda activate yuqian

# 2) 安装 PyTorch（MPS 版，即默认 Mac 版）
pip install torch

# 3) 安装 LLaMA-Factory
pip install llamafactory[torch]
# 注意：不要装 bitsandbytes（不支持 MPS）

# 4) 把 training_package 上传到 Mac
#    假设放在 ~/yuqian/

# 5) 训练
cd ~/yuqian
llamafactory-cli train configs/yuqian_4b_m3max.yaml

# 6) 如果报 MPS 相关错误，用备选方案（见下）

# 7) 训完后对话验证
python scripts/chat_yuqian.py outputs/yuqian_4b
```

### M3 Max 配置要点
- **不量化**（无 bitsandbytes）
- **fp16: true**（MPS 支持 fp16）
- **batch_size: 2**（统一内存够但带宽不如 HBM）
- **cutoff_len: 1024**
- 不设 quantization_bit / quantization_method

### 备选方案：如果 LLaMA-Factory 不支持 MPS

用 **mlx-lm**（Apple 原生 ML 框架，原生支持 M3 Max 量化训练）：

```bash
# 1) 安装 mlx-lm
pip install mlx-lm

# 2) 转换数据格式（sharegpt → mlx-lm 格式）
#    mlx-lm 需要 JSONL 格式：{"text": "...", ...} 或标准对话格式
#    参考 mlx-lm 文档：https://github.com/ml-explore/mlx-lm

# 3) 训练
mlx_lm.lora --model Qwen/Qwen3-4B-4bit --data data/ --iters 3000

# 4) 对话
mlx_lm.generate --model Qwen/Qwen3-4B-4bit --adapter-path outputs/
```

mlx-lm 的优势：原生支持 4-bit 量化训练（Apple 自己的量化方案），M3 Max 上比全精度快。

---

## 训练后：导出合并模型（可选）

如果要把 adapter 合并进基座（方便分发）：

```bash
# LLaMA-Factory 合并
llamafactory-cli export configs/yuqian_4b_h200.yaml  # 改 output_dir 为合并目录
# 或手动合并：
python -c "
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer
base = AutoModelForCausalLM.from_pretrained('Qwen/Qwen3-4B', dtype='float16')
model = PeftModel.from_pretrained(base, 'outputs/yuqian_4b')
model = model.merge_and_unload()
model.save_pretrained('outputs/yuqian_4b_merged')
tok = AutoTokenizer.from_pretrained('Qwen/Qwen3-4B')
tok.save_pretrained('outputs/yuqian_4b_merged')
print('合并完成')
"
```

## 训练后：转 GGUF（用于 Ollama / llama.cpp 推理）

```bash
# 合并后转 GGUF
python llama.cpp/convert_hf_to_gguf.py outputs/yuqian_4b_merged --outfile yuqian_4b.gguf --outtype q4_k_m

# 用 Ollama 跑
ollama create yuqian -f - <<EOF
FROM ./yuqian_4b.gguf
SYSTEM "你是于谦，北京人，相声演员，郭德纲的捧哏搭档。1969年生。"
EOF
ollama run yuqian
```

---

## 当前版本已知问题（参考《版本效果对比与问题分析》）

1. 95% 单轮数据 → 不会多轮对话（重复/循环/语境丢失）
2. 平均回复 18 字 → 短俏皮话无逻辑支撑（胡言乱语）
3. 75 条闲聊被 5479 条淹没（占比 1.3%）
4. Haruhi 事实是 AI 生成（养几只猫等不一致）
5. 1.7B 多轮推理弱（换 4B/7B 应改善）

## 下一步优化建议（换机器后可以做更大模型）

1. 降低 Haruhi 占比到 1000-1500 条
2. 生成 200-300 条闲聊（占比到 15-20%）
3. 过滤 Haruhi <10 字短回复
4. H200 上可训 7B 或 14B
5. 加事实一致性样本
