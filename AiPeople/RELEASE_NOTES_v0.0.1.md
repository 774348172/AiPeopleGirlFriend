# AiPeople v0.0.1

发布日期：2026-09-02

这是白未晞 Gemma 4 全量混合 Adapter 的首个可运行源码基线。它提供本地对话服务、程序拥有的世界/记忆上下文和可审计的模型路由；不是完整 2D 游戏发行版。

## 包含内容

- 默认使用 `baiweixi-v5-full-mixed-adapter`，旧 `baiweixi-v5-targeted-adapter` 作为显式回退 profile；
- `plain_reply_v1` 前台协议：模型只生成对白，世界状态、事务、记忆队列由程序维护；
- FastAPI 正式对话入口与 `/api/health` 模型身份检查；
- Gemma NF4 推理后端、运行时合同、V5 数据生成与训练脚本；
- 生产 Adapter 的冷/热请求冒烟报告及当前权威设计。

## 运行

在 `AiPeople` 目录安装本地 Python、CUDA、PyTorch、Unsloth 和 `requirements.txt` 后，准备 Manifest 中指定的基座和 Adapter 资产，再启动：

```powershell
python -m tools.baiweixi_chat_app --port 8767 --model-profile primary
```

可使用 `--model-profile fallback` 显式切到旧 V5 定向 Adapter。模型权重、Adapter 权重、tokenizer 副本、SQLite 存档和训练 checkpoint 均不随源码版本发布；运行时会按 Manifest 校验本地工件身份。

## 验证证据

- `tests/world_mind/test_production_baseline_contract.py`：生产默认路由与纯对白协议合同；
- `tools/run_gemma_nf4_runtime_smoke.py`：实际加载 Adapter、无结构化前台请求、纠正和未知场景冒烟；
- 主 Adapter 冒烟报告：`eval/gemma_nf4_lora_runtime_smoke/report.json`。

RTX 4090 实测加载后 Torch 分配约 `7.82 GiB`；热请求首字约 `226 ms`、总耗时约 `1.50 s`。这些是单机实测，不构成并发 SLA。

## 已知边界

- 当前网页是正式对话服务，不是已接入真实 2D 游戏的完整客户端；
- 动作闭环接口和测试桩已存在，但真实游戏投影与执行端尚未接入；
- 当世界、记忆和本轮对话都没有直接证据时，模型仍可能猜测具体事实；`unknown` 尚未具备程序级的绝对保证；
- 长时间稳定性、真实并发和完整游戏回归不属于本版本的完成声明。

详见 [Gemma4全量混合Adapter生产基线与回退接入_20260902.md](设计文档/AI设计/当前权威设计/Gemma4全量混合Adapter生产基线与回退接入_20260902.md)。
