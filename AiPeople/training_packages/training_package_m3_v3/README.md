# Apple M3 训练包 v3 —— 秦未晞 (Qwen3-4B LoRA)

> 在 Mac 上完成全部训练 → 导出 GGUF Q4_K_M（~2.3GB）→ 玩家 3060 8G 用 Ollama 本地跑。
> 框架：**MLX**（Apple Silicon 原生，全精度 LoRA，无需量化训练）。M3 Max 训练约 10-20 分钟（153 条小批）。
> **v3（2026-08-06，T12/T14）与 v2 的区别：只含 T12 行为矩阵 119 条全人工通过版 + 41 纠错 + 20 验证——存量 2500 因审计问题（秘密细节/属性朗读等）按决策不混入。v3 用途：先跑通 M3 全流程（训练→GGUF→Ollama）验证；正式语料等新批次重生成后另行打包。**

## 环境要求

| 项 | 要求 |
|---|---|
| 硬件 | Apple Silicon（M3/M3 Pro/M3 Max 均可；内存 ≥ 16GB，推荐 36GB+） |
| 系统 | macOS 14+ |
| Python | 3.10–3.12（Homebrew: `brew install python@3.11`） |
| 依赖 | `pip install mlx mlx-lm huggingface_hub` |

## 数据说明（2026-08-06 矩阵版）

| 文件 | 条数 | 口径 | 说明 |
|---|---|---|---|
| `data/qin_v4_matrix.jsonl` | 119 | **新正典（T12 矩阵全通过版）** | 六类行为矩阵：支持陪伴 42 / 正典问答 21 / 含糊回应 18 / 记忆纠正 15 / 安静陪伴 12 / 关系边界 11。95 条一审通过 + 24 条人工复核后重生成（根因修复：属性朗读/玩家视角错位/基地编造/轨道计算错位/秘密泄漏） |
| `data/qin_v4_20.jsonl` | 20 | **新正典** | 验证批次，01 脚本取其中 10 条做 valid 集 |
| `data/qin_corrections.jsonl` | 41 | **新正典** | ⭐ 称呼纠错样本（人工编写）：强制区分"秦未晞=我的名字 / 浩然=玩家大名"，**必须包含** |
| ~~存量 2500~~ | — | — | **不混入**（2026-08-06 决策：审计发现问题未处置前不进训练） |

> **生成方式**：`python 01_prepare_data.py --new-only`（默认主数据只取矩阵；不带 `--new-only` 会按 v2 逻辑取 qin_v4_2530 存量，本包已删除存量文件，必须用 `--new-only`）。
> **训练步数**：02_train.sh 按 M3 路线冻结规则自动算 `iters = 1.5 × N`（train 153 条 → 230 iters）。
> **CHAT-01 防泄漏**：01 脚本用 `eval_exclusions/chat01_v1.json` blocklist 过滤 human 台词重合样本（本次 119 条排除 2 条），禁止跳过。

> **关于称呼混淆（重要）**：初版训练验收发现模型把"浩然"误认为角色名。根因：system 锚中
> "浩然（心情好时）/ B哥（平常）"与"秦未晞"并列且无标签。本包已修复：
> ① 01 脚本给所有样本 system 段**追加称呼澄清段**（"你的名字是秦未晞。玩家有两个称呼：大名是浩然、小名是B哥……"）；
> ② 合并 41 条人工纠错样本（"你叫浩然吗？"→"我叫秦未晞，浩然是你"）。
> 训练后必须通过下方**验收清单**再导出 GGUF。
>
> **改玩家称呼**：秦对玩家有两个称呼——大名（心情好时叫）与小名（平常叫）。改玩家设定只需
> 修改 `01_prepare_data.py` 顶部 `PLAYER_FORMAL` / `PLAYER_INFORMAL` 两个常量（当前：浩然 / B哥），
> 重新 `python 01_prepare_data.py --new-only` + `RESUME=1 ./02_train.sh` 续训即可，无需改数据文件。

## 称呼验收清单（融合导出前必测）

```bash
mlx_lm.generate --model Qwen/Qwen3-4B --adapter-path adapters/qinweixi \
  --max-tokens 128 --temp 0.7 \
  -p "system: 你是秦未晞…\nuser: 你叫什么名字？"
```
| 问题 | 期望回答 |
|---|---|
| 你叫什么名字？ | 我叫秦未晞（绝不能答"浩然"） |
| 你叫浩然吗？ | 我叫秦未晞，浩然是你 |
| 浩然是谁？ | 你（玩家）啊，平常叫你B哥 |
| 你平常怎么叫我？ | 平常B哥，心情好叫浩然 |
| 我叫你什么？ | 秦老 |

5 项全过再 `./03_fuse_gguf.sh`；任何一项答错→ `RESUME=1 ./02_train.sh` 续训后再测。

默认训练只用新正典 600+20 条（约 590 训练 / 60 验证）；确需混入旧数据时：
`python 01_prepare_data.py --legacy`（README 提示：混训会稀释新称呼的一致性，不推荐）。

## 训练路线

```
矩阵 119+41 纠错 (自带锚) ──01(--new-only)──> MLX text ──02──> LoRA 微调 ──03──> 融合+GGUF+Q4 ──04──> Ollama
```

## 步骤

```bash
# 1) 装依赖（一次）
pip install mlx mlx-lm huggingface_hub

# 2) 数据准备（sharegpt → MLX text；必须 --new-only，主数据只取矩阵）
python 01_prepare_data.py --new-only
#    → data/train.jsonl (153条) + data/valid.jsonl (25条)

# 3) 训练（MLX 首次会自动下载 Qwen3-4B 并转 MLX 格式，约 8GB）
./02_train.sh
#    → adapters/qinweixi/*.safetensors + adapter_config.json
#    预计: M3 Max 约 10-20 分钟（230 iters）；it 自动 = 1.5 × N

# 4) 融合 + 转 GGUF + 量化（需要 llama.cpp，脚本自动 clone 编译）
./03_fuse_gguf.sh
#    → gguf/qinweixi-q4_k_m.gguf (~2.3GB) ← 发给玩家的文件

# 5) 本机部署验证
./04_ollama.sh
```

## 关键参数说明（02_train.sh 可调）

| 参数 | 值 | 理由 |
|---|---|---|
| `--num-layers 24` | 24/36 层 | 覆盖全部 QKV+MLP，人格学习充分 |
| `--lora-rank 16` | 16 | 比默认 8 容量大，小数据也不易欠拟合 |
| `--learning-rate 1e-5` | 低 LR | 保持基座语言能力，只改人格口吻 |
| `--iters 230` | 自动 = 1.5×N | M3 路线冻结规则（train 153 条 → 230）；过拟合时减半 |
| `--max-seq-len 2048` | 2048 | 覆盖全部样本（最长 ~1076 字符），省显存 |

预计时长：M3 Max 约 10-20 分钟；M3 基础款约 20-40 分钟（v3 小批验证版）。

## 输出与交付

```
gguf/qinweixi-q4_k_m.gguf   ← 发给玩家（~2.3GB）
```

玩家端（Windows 3060 8G）部署——Ollama:

```bash
# 玩家电脑:
# 1) 安装 Ollama
# 2) 放一个 Modelfile（见 04_ollama.sh 里的模板），然后:
ollama create qinweixi -f Modelfile
ollama run qinweixi
# VRAM: Qwen3-4B Q4_K_M ≈ 2.5GB，3060 8G 无压力
```

## 已知坑

1. **Qwen3 的 think 标签**：Qwen3 原生模板会输出 `think` 段。本包训练数据用 nothink 模板渲染（01 脚本），Modelfile 的 TEMPLATE 也无 think——模型不会思考，直接说人话。**不要**用 Ollama 自带的 qwen3 模板加载。
2. **MLX 首次下载模型**：`mlx_lm.lora` 首次会从 HF 下载 Qwen3-4B 并转换，需要网络 + ~10GB 磁盘；慢可设 `export HF_ENDPOINT=https://hf-mirror.com`。
3. **M3 基础款（8GB 内存）**：跑不动 4B 训练，请用 H200 包或改 `02_train.sh` 的 MODEL 为 Qwen3-1.7B。
4. **验证集 loss**：02 训练日志里 valid loss 应逐步下降；若升高说明过拟合，把 `--iters` 减到 600。
5. **转换 GGUF 报错**：若 `convert_hf_to_gguf.py` 报结构不识别，先 `python llama.cpp/convert_hf_to_gguf.py --outfile ... --outtype f16 --model-name qinweixi` 或升级 llama.cpp（`git -C llama.cpp pull` 后重编）。
