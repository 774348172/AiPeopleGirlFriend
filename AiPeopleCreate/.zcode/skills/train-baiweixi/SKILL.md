---
name: train-baiweixi
description: 白未晞人格模型本机训练。在训练包就绪（D:\AIPeopleGit\ai-girlfriend\AiPeople\training_packages\training_package_baiweixi_*）时，按本技能启动/监控/续训。资源红线：CPU ≤60%、GPU ≤95%，全程受控。
---

# 白未晞训练（本机 RTX 4090 24GB）

## 训练包（就绪于 2026-08-16）

| 包 | 基座 | 方式 | 配置 | 数据 |
|---|---|---|---|---|
| `training_package_baiweixi_qwen35_4b` | Qwen3.5-4B | 4bit QLoRA rank16 | `configs/baiweixi_4b_8g.yaml` | data/baiweixi_ready.jsonl（1973 条） |
| `training_package_baiweixi_qwen25_7b_instruct` | Qwen2.5-7B-Instruct | bf16 LoRA rank32 | `configs/baiweixi_7b_4090.yaml` | data/baiweixi_ready.jsonl（1973 条） |

基座缓存：`~/.cache/huggingface/hub/`。Qwen2.5-7B 已缓存；Qwen3.5-4B 需下载（可用 `HF_ENDPOINT=https://hf-mirror.com`）。

## 资源红线（强制）

- **CPU 占用 ≤ 60%**：启动训练进程前设置 CPU 亲和性（32 逻辑核 → 19 核，`0x7FFFF`）。
- **GPU 占用 ≤ 95%**：训练期间监控 `nvidia-smi` GPU-Util；持续超限时降低 `per_device_train_batch_size`（4b: 1→1 不变，可降 grad_accum；7b: 2→1 + grad_accum ×2）或暂停整机其它 GPU 负载。
- 监控脚本：`python tools/monitor_training.py --log monitor.log`，每 10 秒采样 CPU/GPU，超限写告警行。

## 启动训练（在包目录内执行）

```bash
cd /d/AIPeopleGit/ai-girlfriend/AiPeople/training_packages/training_package_baiweixi_qwen35_4b
# 1) 设置 CPU 亲和性（限制 19/32 核 ≈ 59%）
powershell -Command "$p = Get-Process -Id $PID; $p.ProcessorAffinity = 0x7FFFF"   # 在训练前对新进程生效用 wrapper
# 2) 训练（后台）：
llamafactory-cli train configs/baiweixi_4b_8g.yaml
```

实际执行用 Python wrapper：启动子进程后立刻设置 ProcessorAffinity（见 `tools/launch_train.py`），或后台任务内用 PowerShell 对 PID 设置。

## 顺序与衔接

- 严格串行（24G 显存一次只跑一个训练）。
- 基座未缓存时：先在训练间隙后台下载（HF_ENDPOINT 视网络设置），下载不占用 GPU。
- 一个包完成（train_loss 收敛、adapter 保存）→ 运行下一个包。

## 验收（训练完成后）

```bash
python scripts/chat_baiweixi.py outputs/baiweixi_4b
python scripts/check_acceptance.py outputs/baiweixi_4b
# 5 项验收：名字=白未晞 / 猫妖=坦白或含糊 / 无喵口癖 / 住址=松江府 / 伤好不走=隐晦表达
```

## 续训（人格不满意时）

`num_train_epochs` 调大后 RESUME：`llamafactory-cli train configs/baiweixi_4b_8g.yaml --resume_from_checkpoint outputs/baiweixi_4b/checkpoint-*`。
