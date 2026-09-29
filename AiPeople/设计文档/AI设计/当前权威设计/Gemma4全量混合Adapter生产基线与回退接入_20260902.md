# Gemma 4 全量混合 Adapter 生产基线与回退接入

> 2026-09-28 更新：本页下文的 V5 默认模型与 V1/纯文本模型输出描述为历史方案。当前默认模型为 V3 step2332，采用紧凑 V3 + reply JSON、程序解析后展示对白。已完成的接入和实测边界见 [V3 step2332 运行接入](./Gemma4_V3_step2332运行接入_20260928.md)。

> 2026-09-09 验收修订：可见对白与训练目标使用 [对白质量与训练目标验收标准 V1](./对白质量与训练目标验收标准_V1_20260909.md)。本次只更新验收标准；完整角色锚同构、状态报告接入和正文语义校验尚不能由旧冒烟或渲染器单测推定完成。生产配置不变。

> 状态：当前生产候选；质量门通过前不得视为已发布基线
> 版本：V1.1
> 日期：2026-09-03
> 适用角色：白未晞

## 一、冻结结论

模型选型阶段的默认配置指向全量混合 Adapter；旧 V5 定向 Adapter 只作为人工选择的回退版本保留，不参与默认路由，也不得在运行时静默切换。该配置只有在事实、主体、纠正、unknown 和角色风格质量门均通过后，才能升级为已发布生产基线。

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

`GAME_REPLY` 的事实边界保持不变：当前游戏世界和男主本轮明确更新优先于旧记忆；没有直接证据时回答不知道；不得新增未授权事实、替男主行动或输出动作旁白。当前世界事实不是可选记忆，凡是回答所需的客观事实必须在本轮输入中明确呈现。

### 2.1 当前前台输入合同

当前前台只采用 `GAME_REPLY_CONTEXT_V1`，由程序唯一渲染，并供训练数据生成器和评测复用。固定区块为：

```text
[角色锚]
[当前权威事实]       游戏时间、地点、物品、预约、进行中动作和已完成结果
[本轮玩家事实报告]   玩家本轮明确提供的更新，带来源和待确认状态
[相关记忆]           包含主体、时间关系和 current/stale/unknown 标记
[第三方对话证据]     说话人、原话、来源：近期对话
[女主状态]
[回答依据约束]
[玩家本轮对白]
```

游戏已管理的状态缺失或投影失败时应记录缺失原因，不能伪装为已知事实。开放聊天缺少答案证据时可以构造明确表达未知或澄清的回复请求；不假定程序能穷举所有问题所需事实。一般建议与具体事实断言按新验收标准区分。`GAME_REPLY` 必须允许使用 `[当前权威事实]`，不能只允许使用记忆和玩家对白。

近期对话的参与者边界固定如下：男主历史话语才使用聊天模板的 `user`，白未晞历史话语才使用 `assistant`。任何第三方不能伪装成 `assistant`，必须进入 `[第三方对话证据]`，逐条保留说话人、原话与“来源：近期对话”。过滤第三方后若形成连续 `user` 或连续 `assistant`，渲染器必须合并相邻同角色消息并标明本轮边界，保证聊天模板角色交替；不得插入虚构的白未晞话语作为占位。

### 2.2 玩家纠正与世界写回边界

- 游戏动作、游戏事件和明确的结构化状态变更由游戏在生成快照前写入客观世界。
- 玩家自然语言陈述默认是带来源的“玩家事实报告”，不能仅凭模型回复中的“我记住了”改变客观世界。
- 产品若要求玩家陈述直接覆盖客观世界，必须增加独立的 `player_fact_update` 程序入口，由程序提交、版本化并在下一轮快照中可见；模型不得自行决定写回。
- 回复模型只负责表达，不负责确认世界已经发生变化。

### 2.3 可选文本事实校验

`REPLY_FACT_CHECK_V1` 是概率性实验组件，不是确定性事实裁判。默认只能在 Shadow 中输入本轮权威事实、玩家对白和主模型回复，输出 `PASS`、`CONFLICT`、`UNSUPPORTED` 或 `UNCERTAIN`并记录；不得修改世界、阻断回复、触发重试或生成替代对白。只有明确封闭的游戏事实切片在独立封存集和人工审核中证明最终可见质量净提升、误放和误杀达到冻结门槛后，才可另行评审一次重试或替换策略。具体边界以《游戏-LLM接口契约设计 V2》第七节为准。

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

已知质量风险：纯对白链路不会机械推导开放域事实。若当前世界和记忆都没有某个事实，模型仍可能受相似历史样本影响给出具体猜测；一次默认场景实测出现过此情况。该风险意味着当前工件仍是生产候选，不得在 unknown 可靠性达到产品门槛前宣称已经保证所有未知问题都回答“不知道”。

训练与风格边界：当前主 Adapter 是全量混合工件，不是纯 V5 工件。其数据清单为 2189 行，其中旧 V4 为 1973 行、V5 grounded 为 192 行、定向补充为 24 行；V5 grounded 的监督 token 约占总监督 token 的 4.8%。V5 grounded 的输入是 JUDGE 投影，而线上前台是 `GAME_REPLY_CONTEXT_V1` 时，二者不得宣称同构；在共享渲染器和合同测试完成前，不能从 Adapter 已加载或事实冒烟通过推导白未晞语气和事实能力已经达到验收标准。角色风格需另行进行同输入基座/Adapter 对照，并记录为发布证据。

生产发布硬门：新 Adapter 在与裸基座完全相同的 `GAME_REPLY_CONTEXT_V1` 输入上，事实、主体、纠正和 unknown 不得劣于裸基座；任一核心能力回归、严重 unsupported assertion 或主体错位，均保持“候选”状态，不得仅凭速度、格式或风格分升级为生产基线。

## 五、回退与恢复

- 回退只改变 `--model-profile`，不覆盖或修改主 Adapter 工件。
- 回退必须保留同一存档数据库和同一世界/记忆输入，便于复现问题。
- 发现模型身份、Manifest 哈希、Adapter 加载或协议门失败时，停止发布，不得把基座或未加载 Adapter 当成成功。
- 回退后仍需重新执行健康检查和至少一次纯对白冒烟；回退结果单独记录，不改写主版本结论。

## 六、变更纪律

改变默认 profile、Adapter 工件、基座 revision、量化配置、`plain_reply_v1` 上下文字段或事实边界时，必须同时更新本文件、Manifest、运行时合同测试和发布冒烟报告。结构化心智/动作协议仍可供后台或专用程序路径使用，但不得未经重新评测重新进入 Gemma 前台主链路。

## 七、2026-09-10 角色锚统一状态

`GAME_REPLY` 固定角色锚现在只能由 `CharacterPackagePromptComposer` 根据当前角色包正典生成；生产程序、训练生成器和评测器不得再各自维护白未晞角色文案。稳定身份、身世、知识缺口、猫系习惯、生活能力边界、妖术上限与明确未知可以进入固定锚，并保留 `direct_allowed / hint_only / withhold` 的披露含义。

时间、地点、伤势、妖力当前值、关系阶段、当前活动、意图和动作结果不得写死进固定锚，必须由每轮 `GAME_REPLY_CONTEXT_V1` 动态区块提供。静态迁移样本若代表角色包当前基线，关系字段必须使用角色包的 `initial_runtime.relationship`，不得写成“未提供”后又监督明确去留态度。

当前清理账本为 `baiweixi_frontend_full_reviewed_batch014_20260910`：2514 条接受、1653 条拒绝、89 条精确坏历史隔离，待审核为零。重渲染候选包为 `baiweixi_frontend_full_canonical_anchor_final_20260910`，训练目标未改写，角色锚依赖复审和生产输入精确对齐均已完成。该包仍保持 `training_authorized=false`，只有完成最终训练硬门后才能授权训练。
