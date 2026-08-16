# 秦未晞 H200 训练包 Qwen3.5（2026-08-07）

> 在 H200 上训练 **Qwen3.5-4B** 全精度 LoRA → 导出 GGUF Q4_K_M → ship 给玩家用 Ollama 跑。
> ⚠ Qwen3.5-4B 为 VLM 架构：LLaMA-Factory 0.9.5 + transformers 5.6 已支持（qwen3_5_nothink 模板）；本机 8G 已实测 QLoRA 可训（视觉塔自动冻结）。H200 全精度训练不受显存限制。
> 数据：**2500 条新正典**（B哥/浩然默认称呼）+ 41 条称呼纠错。与 M3 包同数据同口径，框架为 LLaMA-Factory (PyTorch CUDA)。

## 工作流

```
H200 训练                          玩家 3060 8GB 推理
─────────                          ──────────────────
1. 装 LF + torch CUDA              1. 装 Ollama
2. 数据准备 (01_prepare_data.py)   2. 拿到 GGUF 文件
3. 训练 (2539条, ~30-40min)        3. ollama create + ollama run
4. 合并 adapter + 转 GGUF          4. 对话 (2.5GB 显存)
5. 得到 qinweixi_4b_q4.gguf (2.3GB)
```

## 包内容

```
training_package_qinweixi_h200/
├── README.md                         ← 你在读这个
├── bible.yaml / canon.json           ← 秦未晞正典（2026-08-05 新口径）
├── 01_prepare_data.py                ← 数据准备：合并 + 称呼澄清注入 + CHAT-01 防泄漏
├── data/
│   ├── qin_v4_2530.jsonl             ← 主数据 2530 条（2500 + 30 三观观点类）
│   ├── qin_corrections.jsonl         ← 41 条称呼纠错（名字归属强制区分）
│   ├── qin_v4_ready.jsonl            ← 01 产物（合并+澄清+防泄漏后，LF 直接吃）
│   └── dataset_info.json             ← LLaMA-Factory 数据集注册
├── configs/
│   ├── qinweixi_4b_h200.yaml         ← 训练配置（全精度, bf16, batch 8, 3 epoch）
│   └── qinweixi_4b_export.yaml       ← 导出配置（合并 adapter）
├── eval_exclusions/chat01_v1.json    ← CHAT-01 防泄漏 blocklist（01 必须读它）
└── scripts/
    ├── chat_qinweixi.py              ← 训练后对话验证（交互式）
    └── export_gguf.py                ← 合并 adapter + 转 GGUF
```

## Step 1：环境准备（H200 上一次性）

```bash
conda create -n qinweixi python=3.11 -y && conda activate qinweixi
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install llamafactory[torch] peft
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp && make -j && cd ..
python -c "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

## Step 2：数据准备 + 训练

```bash
cd training_package_qinweixi_h200
python 01_prepare_data.py          # → data/qin_v4_ready.jsonl（约 2539 条）
llamafactory-cli train configs/qinweixi_4b_h200.yaml
```

训练参数：Qwen3-4B 全精度 LoRA（rank 16/alpha 32，target all），bf16，batch 8×2=16，3 epoch，lr 5e-5，cutoff 2048，template `qwen3_nothink`（关思考）。
产出：`outputs/qinweixi_4b/adapter_model.safetensors`（~65MB）。预计 30-40 分钟。

## Step 3：验收（融合导出前必测）

```bash
python scripts/chat_qinweixi.py
```

| 问题 | 期望回答 |
|---|---|
| 你叫什么名字？ | 我叫秦未晞（绝不能答"浩然"） |
| 你叫浩然吗？ | 我叫秦未晞，浩然是你 |
| 浩然是谁？ | 你（玩家）啊，平常叫你B哥 |
| 你平常怎么叫我？ | 主要叫B哥，心情好叫浩然 |
| 我叫你什么？ | 秦老 |

5 问全过再导出；任何一项答错 → 用 `configs/qinweixi_4b_h200.yaml` 降低 epoch（如 1.0）或补纠错数据续训。

## Step 4：合并 + 转 GGUF（ship 给玩家）

```bash
llamafactory-cli export configs/qinweixi_4b_export.yaml   # 或直接:
python scripts/export_gguf.py
```

产出：`outputs/qinweixi_4b_q4.gguf`（~2.3GB）

## Step 5：玩家侧部署（3060 8GB）

```bash
ollama create qinweixi -f - <<EOF
FROM ./qinweixi_4b_q4.gguf
SYSTEM "你是秦未晞，22 岁，自由插画师/自媒体博主，和玩家同城合租（合租室友 + 暧昧期）。你对他有两个默认称呼：平常主要叫小名\"B哥\"，心情好的时候主要叫大名\"浩然\"；他叫你\"秦老\"。嘴硬心软、爱怼人但关心人，数学白痴，怕冷。说话口语化短句，用\"哼/喂/诶/哎呀/啧/啦/嘛\"语气词。你心里藏着一个秘密：18 岁那年你们在异世界相依为命度过一年，只有你记得。"
PARAMETER temperature 0.8
PARAMETER num_ctx 2048
EOF
ollama run qinweixi
```

玩家显存占用：~2.5GB，和游戏共享可行。

## 与 M3 包的差异

| | M3 包（mlx） | H200 包（本包） |
|---|---|---|
| 框架 | MLX（Apple 原生） | LLaMA-Factory (PyTorch CUDA) |
| 数据 | 同 2500 条（train 2296/valid 263） | 同 2500 条（train 全量 2539，LF 自动切验证） |
| 训练时长 | M3 Max 2-3 小时 | H200 ~30-40 分钟 |
| 产物 | 相同 GGUF Q4_K_M | 相同 GGUF Q4_K_M |
