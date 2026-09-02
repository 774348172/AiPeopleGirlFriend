# Gemma 4 全量混合 Adapter 生产基线与回退接入

> 状态：当前生产基线
> 版本：V1.0
> 日期：2026-09-02
> 适用角色：白未晞

## 一、冻结结论

模型选型阶段结束。正式程序默认使用全量混合 Adapter；旧 V5 定向 Adapter 只作为人工选择的回退版本保留，不参与默认路由，也不得在运行时静默切换。

| profile | model_id | revision | adapter_id | 用途 |
|---|---|---|---|---|
| `primary` | `local/baiweixi-gemma-4-12B-v5-full-mixed` | `baiweixi-v5-full-mixed-20260901` | `baiweixi-v5-full-mixed-adapter` | 当前生产默认 |
| `fallback` | `local/baiweixi-gemma-4-12B-v5-targeted` | `baiweixi-v5-targeted-20260831` | `baiweixi-v5-targeted-adapter` | 故障或人工回退 |

两个 profile 使用相同的 `google/gemma-4-12B-it` 基座、NF4 + LoRA 推理栈和角色/世界身份。工件身份由对应 Manifest 的文件哈希和 `artifact_sha256` 绑定。

## 二、正式运行边界

正式前台协议固定为 `plain_reply_v1`：

1. 程序拥有世界状态、游戏时间、人物位置、物品状态、动作执行结果和事务版本。
2. 检索与记忆模块向模型提供当前快照和相关记忆；当前快照是只读证据。
3. Gemma Adapter 只生成白未晞真正说出口的自然语言正文。
4. 前台不要求角色 LoRA 生成 `MIND_PATCH_V2`、`JUDGE_TURN` 或动作 JSON，因此不把未训练的结构化输出格式放进每轮主链路。
5. 回复仍经过空文本、长度、协议边界和事务原子提交校验；后台记忆整理不阻塞已提交对白。

`GAME_REPLY` 的事实边界保持不变：当前世界和男主本轮明确纠正优先于旧记忆；没有直接证据时回答不知道；不得新增未授权事实、替男主行动或输出动作旁白。

## 三、代码入口

- Manifest：`AiPeople/local_runtime/gemma4_nf4_runtime_manifest.json`（默认）
- 回退 Manifest：`AiPeople/local_runtime/gemma4_nf4_fallback_manifest.json`
- 前台服务：`AiPeople/tools/baiweixi_chat_app.py`
- Gemma 后端：`AiPeople/runtime/adapters/gemma_nf4.py`
- 世界心智运行时：`AiPeople/runtime/world_mind/runtime.py`
- 纯对白上下文：`AiPeople/runtime/world_mind/model_payloads.py`

服务默认启动等价于：

```text
python -m tools.baiweixi_chat_app --port 8767 --model-profile primary
```

人工回退时显式使用：

```text
python -m tools.baiweixi_chat_app --port 8767 --model-profile fallback
```

切换前应停止旧进程、确认 `/api/health` 的 `model_profile`、`model`、`adapter_id` 和 `revision`，并记录切换原因。没有自动按质量或超时替换模型的逻辑。

## 四、验证要求与当前证据

发布前必须验证：Manifest 文件哈希、Adapter 已加载、`thinking=false`、无网络访问、前台请求没有结构化 `response_format`、纯对白响应非空且不含 JSON/动作旁白。

正式冒烟脚本：

```text
python AiPeople/tools/run_gemma_nf4_runtime_smoke.py
```

该脚本默认读取主 Manifest，覆盖冷启动纠正、热请求纠正和带近期对白的信息不足场景，并记录 GPU 显存、首字时间、总耗时和生成速度。自动门只检查最低合同，最终角色质量仍以人工审核和冻结多轮评测为准。

最新主版本冒烟报告：`AiPeople/eval/gemma_nf4_lora_runtime_smoke/report.json`。报告确认实际加载的是 `baiweixi_v5_full_mixed_adapter`，状态为 `passed`；4090 上加载后 Torch 分配约 7.82 GiB，热请求首字 `225.79 ms`、总耗时 `1497.82 ms`。冷启动加载约 `47 s`，不能作为每轮回复延迟。实际数值随驱动、并发和上下文长度变化，不能写死为 SLA。

已知质量风险：纯对白链路不会机械推导开放域事实。若当前世界和记忆都没有某个事实，模型仍可能受相似历史样本影响给出具体猜测；一次默认场景实测出现过此情况。该风险不改变本次 Adapter 路由结论，但在 unknown 可靠性达到产品门槛前，不应宣称所有未知问题都已被程序保证为“不知道”。

## 五、回退与恢复

- 回退只改变 `--model-profile`，不覆盖或修改主 Adapter 工件。
- 回退必须保留同一存档数据库和同一世界/记忆输入，便于复现问题。
- 发现模型身份、Manifest 哈希、Adapter 加载或协议门失败时，停止发布，不得把基座或未加载 Adapter 当成成功。
- 回退后仍需重新执行健康检查和至少一次纯对白冒烟；回退结果单独记录，不改写主版本结论。

## 六、变更纪律

改变默认 profile、Adapter 工件、基座 revision、量化配置、`plain_reply_v1` 上下文字段或事实边界时，必须同时更新本文件、Manifest、运行时合同测试和发布冒烟报告。结构化心智/动作协议仍可供后台或专用程序路径使用，但不得未经重新评测重新进入 Gemma 前台主链路。
