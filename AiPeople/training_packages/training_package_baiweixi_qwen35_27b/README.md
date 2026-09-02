# 白未晞 Qwen3.5-27B LoRA 训练包

本包用于在 RTX 4090 24GB 上对官方 `Qwen/Qwen3.5-27B` 做单卡 NF4 QLoRA。

## 固定输入

- 基座：`Qwen/Qwen3.5-27B`，官方 revision `fc05daec18b0a78c049392ed2e771dde82bdf654`
- 原始数据：相邻 4B 训练包中的 `data/baiweixi_ready.jsonl`
- 原始数据 SHA256：`9A9433AFC83B3B5F36C85C88964A66D5A76CCA5BFF0D776EE0A88CEE73731751`
- 数据量：1973 条多轮 ShareGPT 样本
- 60 轮评测样本不会加入训练数据

`prepare_data.py` 会生成可追溯的 27B 训练副本：

1. 每条 system 追加纯对白和先回应当前输入约束。
2. 清除 5 行、7 条 assistant 回复中的括号动作旁白。
3. 不修改原始 4B 数据文件。
4. 输出 `data/data_manifest.json` 记录输入和输出哈希。

## 配置

- NF4 4-bit QLoRA
- LoRA rank 8 / alpha 16 / all language linear targets
- 冻结视觉塔和多模态投影层
- batch 1，gradient accumulation 8
- cutoff 1024；实测全部样本为 845–1014 tokens，无截断
- 1 epoch，学习率 `5e-5`
- 关闭思考模板 `qwen3_5_nothink`

## 执行

```powershell
python prepare_data.py
.\.venv-unsloth\Scripts\python.exe train_unsloth.py --max-steps 1 --output-dir outputs/baiweixi_27b_unsloth_probe
.\.venv-unsloth\Scripts\python.exe train_unsloth.py
```

`train_unsloth.py` 使用 text-only 加载跳过视觉塔，但显式覆盖全部 12 类语言线性层。训练只对 assistant 回复计算损失。先执行单步 probe；只有 probe 完成一次前向、反向和优化器更新后，才允许启动完整训练。

LLaMA-Factory 配置保留为兼容性基线。该后端在当前 Windows 环境缺少快速 GatedDeltaNet 内核，实测单步需要 646.6 秒，不用于正式全量训练。

## 2026-08-25 正式训练结果

- 输出：`outputs/baiweixi_27b_unsloth`
- 1973 条样本，1 epoch，247 个优化步骤
- 训练耗时：3405.4 秒（56 分 45 秒）
- 平均训练 loss：`1.507769840448974`
- 峰值 GPU 显存：`18610.10 MiB`
- 可训练参数：`58,363,904`
- 最终适配器 SHA256：`D5B28DB20D1F27A8A6C8CBCD5C236A0783C49588E39DB643A512B044CDCD962E`
- 训练日志中没有 NaN/Inf
- `checkpoint-200` 和 `checkpoint-247` 可用于恢复或对照

## 显存前提

训练开始前应有至少约 21GB 可用显存。当前机器上的 ComfyUI 若加载模型，会导致 27B 在模型加载阶段直接 OOM；训练脚本不会自动结束其他用户进程。
