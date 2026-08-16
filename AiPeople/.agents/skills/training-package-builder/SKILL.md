---
name: training-package-builder
description: Build Qin Weixi (秦未晞/qinweixi) training packages for Apple M3 (MLX) or NVIDIA H200 (LLaMA-Factory) and archive them to F:\ai-girlfriend\AiPeople\training_packages\archives. Use whenever the user asks to 生成/更新训练包、训练压缩包、M3训练、H200训练、打包训练数据、打包2500条, or mentions shipping data to train on a Mac or H200.
---

# 训练压缩包生成（秦未晞）

把 `人物设定/秦/训练数据/` 下的新正典数据打包成可训练的压缩包，统一归档到 `F:\ai-girlfriend\AiPeople\training_packages\archives\`。

## 数据源与口径（2026-08-06 当前状态）

- 主数据：`人物设定/秦/训练数据/qin_v4_2500.jsonl`（2500 条 = 首批 1100 + 追加 1400，sharegpt 格式，自带 system 人格锚）
- 纠错数据：`人物设定/秦/训练数据/qin_corrections.jsonl`（41 条人工称呼纠错，**必须合并**）
- 验证批次：`qin_v4_20.jsonl`（20 条）
- 旧数据隔离：`训练数据/_archive_*/`（旧称呼版不得混训）；包内 `data/legacy/` 默认不合并（`--legacy` 才合并）
- 正典：`人物设定/秦/bible.yaml`、`canon.json`（称呼配置中心 = bible `player.name_slots`：formal 大名 / informal 小名，当前 浩然/B哥）

## 称呼配置（重要）

- 秦对玩家两个**默认称呼**（设定非强制，情境可自由用其它称呼）：大名（心情好时主要叫）/ 小名（平常主要叫）
- 改称呼只改两处：bible `player.name_slots` + 包内 `01_prepare_data.py` 顶部 `PLAYER_FORMAL`/`PLAYER_INFORMAL`
- **称呼澄清段**（修复"浩然/秦未晞"混淆的训练验收问题）：01 脚本给每条 system 段追加 `【称呼澄清】你的名字是秦未晞……`，所有包必须包含

## 数据准备（01_prepare_data.py，两个包共用逻辑）

1. 合并：2500 主数据 + 41 纠错
2. 澄清注入：每条 conversations 的 system 段追加 NAME_CLARIFICATION
3. **CHAT-01 防泄漏**：用 `eval_exclusions/chat01_v1.json` blocklist 过滤 human 台词重合样本（每次跑 01 都会排除 2 条左右），**禁止跳过**（contract_id 必须匹配 `chat01-training-exclusions-v1`）
4. 输出：M3 包 → `data/train.jsonl`+`valid.jsonl`（MLX text，nothink 模板）；H200 包 → `data/qin_v4_ready.jsonl`（LF sharegpt）

## 两个包模板（复制模板目录再改，不要从零写）

### M3 包（`training_package_m3_v2/`，MLX）
- 01 渲染 Qwen3 nothink 模板（`<|im_start|>system/user/assistant`，无 think 段）；行内锚优先 + 澄清段；`qin_v4_1100.jsonl` 存在时优先用 2500（否则 600+500 拼接）
- 02_train.sh：首次 `--iters 3000`（train ~2296 条 ≈ 1.3 epoch）；续训 `RESUME=1 ./02_train.sh`（--resume-adapter + 300 iters）
- 03_fuse_gguf.sh / 04_ollama.sh（nothink Modelfile + 新口径 SYSTEM）

### H200 包（`training_package_qinweixi_h200/`，LLaMA-Factory）
- dataset_info.json 注册 `qin_v4_ready`（sharegpt，columns.messages → conversations）
- 训练配置：Qwen3-4B，lora all/rank 16/alpha 32，bf16，batch 8×2，`template: qwen3_nothink`，cutoff 2048，3 epoch（数据量变化时按 ~1-3 epoch 调）
- scripts/chat_qinweixi.py（验证）+ export_gguf.py（合并 + Q4_K_M）

## 打包与归档

```bash
# 打包（排除 __pycache__；zip 用 python zipfile 避免编码问题）
cd F:/ai-girlfriend/AiPeople
./.venv/Scripts/python.exe -c "
import zipfile, os
src = '<包目录>'; out = '<包目录>.zip'
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, '.'))
"
# 归档（所有训练压缩包统一放这里）
mv <包目录>.zip F:/ai-girlfriend/AiPeople/training_packages/archives/
```

- 包内不要留旧版主数据（如 qin_v4_1100.jsonl 被 2500 包含时删掉）
- 每个包必须包含：README（数据表 + 验收清单 + 改称呼说明）、01_prepare_data.py、eval_exclusions/chat01_v1.json
- 打包后校验：zip 文件数/大小 + 抽查包内 01 可运行（本地 `python <包>/01_prepare_data.py`）

## 验收清单（写进每个包 README，融合导出前必测）

| 问题 | 期望 |
|---|---|
| 你叫什么名字？ | 我叫秦未晞（绝不能答"浩然"） |
| 你叫浩然吗？ | 我叫秦未晞，浩然是你 |
| 浩然是谁？ | 你（玩家）啊，平常叫你B哥 |
| 你平常怎么叫我？ | 主要叫B哥，心情好叫浩然 |
| 我叫你什么？ | 秦老 |

## 数据生成（如需追加量）

- 生成器：`gen_qin_v4.py`（compile → 分批生成），配方 `profiles/qinweixi/recipe.yaml`，内容池 `profiles/qinweixi/pools.yaml`
- 大批量追加：`--range-types reply_casual=a:b,reply_romance=c:d` 分批（每批 ≤150 条防 API 降速卡死），完成合并后更新 recipe 配额 + `tests/stage5` 断言
- 速度优化已生效：semantic+style 合并单次调用（`reply_merge.txt`）、120s 超时、workers 8、max_tokens 8192（实验确认）
- 方案 A（单 prompt 多输出）经实验否决：deepseek-v4-flash 批量任务 reasoning 失控（8192/12288 均空内容/截断）
