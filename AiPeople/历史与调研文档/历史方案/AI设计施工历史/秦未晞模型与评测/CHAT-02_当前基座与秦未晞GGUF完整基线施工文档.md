# CHAT-02：当前基座与秦未晞 GGUF 完整基线施工文档

> 状态：施工中（CHAT-02A 至 CHAT-02D 已完成；CHAT-02E 等待人工盲评）  
> 版本：v1.0  
> 日期：2026-08-06  
> 所属阶段：阶段 1，秦未晞自由对话质量收敛  
> 前置条件：CHAT-01A 至 CHAT-01H 已完成，当前语料对应的 `chat01-qinweixi-v2` 已冻结  
> 上位排序：`设计文档/AI设计/当前权威设计/AI聊天核心施工优先级总纲.md`  
> 上位需求：`需求文档/项目框架需求.md`

## 1. 目标

在完全相同的 CHAT-01 评测资产、运行时 prompt、消息编排、llama.cpp 路径、量化口径和生成配置下，对比未训练的 Qwen3-4B 基座与当前秦未晞训练后 GGUF，形成可复现的质量和性能基线。CHAT-01H v2 只更新当前训练语料的防泄漏证据，评测题、oracle 和 rubric 与 v1 字节一致。

本阶段只回答三件事：

1. 当前训练后 GGUF 相对基座改善了什么、退化了什么。
2. 已知失败来自模型权重、运行时输入、推理配置还是评测待人工复核项。
3. CHAT-03 应修复哪些训练数据问题，以及每类问题的原始证据是什么。

CHAT-02 不训练模型、不修改训练数据、不接入高级记忆选择器，也不对候选作最终产品冻结结论。

## 2. 固定边界

### 2.1 唯一评测合同

- suite 固定为 `eval/chat01/suites/chat01_suite_manifest_v1.json`。
- manifest 自哈希固定为 `f6a31fe390deee9fd7bee2d581b515ec791b1b8ab2579e5283b0cc27489d3057`。
- 每次真实运行前必须执行 `python -m eval.chat01.freeze --verify`。
- CHAT-02 不修改 CHAT-01 的题目、oracle、rubric、阈值、泄漏报告或冻结资产。
- 若发现评测缺陷，只登记证据；是否发布 suite v2 必须独立决策，不能原地修改 v1。

### 2.2 比较变量

模型权重是基座与秦未晞 GGUF 之间唯一允许主动变化的变量。下列项目必须相同：

- 目标设备、GPU、电源状态和后台负载记录。
- llama.cpp release、server 二进制和 CUDA 构建。
- GGUF 架构、量化等级和聊天模板；正式差异结论不得混用 Transformers 与 llama.cpp。
- system prompt、历史消息编排、动态上下文和停止条件。
- context size、GPU layers、KV cache、flash attention、parallel。
- 每条用例的最大输出 token、lane 和 seed。
- 超时、重试、无效样本和错误计数方式。

无法做到同一量化口径时，不得把差异归因于训练；只能标记为受后端或量化混杂的探索结果。

### 2.3 明确禁止

- 不读取冻结 oracle 后逐题修改 prompt、采样或模型输出。
- 不删除失败、超时、空输出或解析失败来提高分数。
- 不用当前 epoch、工作激活区或记忆内容帮助单轮冻结题作弊。
- 不以助手腔换取安全，也不以人格表演掩盖错误答案。
- 不在 CHAT-02 中启动训练、生成修复数据或改动 GGUF。

## 3. CHAT-02A：运行时身份锚正典对齐

### 3.1 施工内容

1. 将 `runtime/_prompt.py` 的最小身份锚对齐 `chat01-canon-v1`：
   - 秦未晞 22 岁。
   - 玩家浩然 24 岁。
   - 当前为两居室合租室友和暧昧期，尚未确认恋爱或婚姻。
   - 她平常主要称玩家“B哥”，心情好时主要称“浩然”；这是默认倾向，不强制每轮称呼。
   - 玩家称她“秦老”。
2. 保持既有安全优先、未知现实不编造、非助手身份、历史引用不提权和异世界经历克制披露规则不变。
3. 更新 `tests/runtime/test_reply_prompt.py`，增加现行事实正断言和 `23岁/25岁/大叔` 负断言。
4. 保留 CHAT-01 冻结 `canon_drift.json` 作为冻结时点证据，不篡改其中的 open 记录。
5. 新建 CHAT-02A 正典对齐记录，保存两个文件的冻结前哈希、修正后哈希、对应 finding 和验收状态。

### 3.2 验收

- 运行时 prompt 只含现行年龄、称呼和关系口径。
- prompt 仍是轻量身份与行为边界，不扩张为人物百科或逐题答案表。
- prompt 单元测试和 CHAT-01 全部测试通过。
- CHAT-01 manifest 与 27 项冻结资产复验通过。
- 对齐记录可验证当前文件哈希，且明确冻结 drift 是历史快照而非当前状态表。

## 4. CHAT-02B：双模型 manifest 与环境冻结

### 4.1 模型身份

建立两份独立 manifest：

1. `qwen3-4b-base-q4_k_m`：未经过秦未晞训练的 Qwen3-4B 基座 GGUF。
2. `qinweixi-current-q4_k_m`：当前 `qinweixi-q4_k_m.gguf`。

每份至少记录：

- 模型 ID、角色、架构、参数规模、量化和文件路径。
- 文件 bytes 与 SHA256。
- 上游 repo、base revision、Adapter checkpoint、合并和 GGUF 导出信息；缺失项必须写 `null` 和缺失原因，不能猜测。
- chat template、是否关闭 thinking、上下文上限和停止条件。
- 当前秦未晞 GGUF 与既有 `local_runtime/model_runtime_manifest.json` 的对应关系。

### 4.2 环境身份

建立共享执行 profile，固定：

- llama.cpp `b10256`、server SHA256 和 CUDA 12.4 构建。
- RTX 3070 8GB、驱动、context size、parallel、GPU layers、flash attention 和 KV cache。
- 确定性 lane 与体验 lane 的采样参数。
- 启动、健康检查、预热、单条超时、取消和模型间清理策略。

### 4.3 闸门

- 两个模型文件都存在且哈希匹配前，不进入 CHAT-02D。
- 基座若没有与当前模型一致的 Q4_K_M GGUF，先补齐资产，不用在线 API 或不同后端替代。
- 当前训练模型缺少的 provenance 必须显式登记为发布阻断项，但不伪造历史训练信息。

## 5. CHAT-02C：真实评测 runner

### 5.1 接入方式

在 CHAT-01G 报告链路上增加真实 `LlamaCppReplyModel` provider，不另建第二套评测服务。真实 provider 必须：

- 每个模型运行前校验模型、server、runner、prompt 和 suite 哈希。
- 使用现有 localhost-only llama.cpp Adapter，禁止远程 host。
- 捕获流式首字、完整生成、prompt token、output token、取消、超时和服务错误。
- 只把 messages 发送给模型，oracle 和人工 rubric 永不进入 prompt。
- 运行完成后关闭自有服务进程并确认端口释放。

### 5.2 用例展开

- 确定性 lane：每个指定 seed 执行一次。
- 体验 lane：按冻结用例的三个 seed 分别保留输出，不先平均。
- 多轮：逐轮保留上下文、输出、检查结果和首个失败 turn；中途 blocker 不从报告删除。
- 人工集：只生成盲化候选，不自动填 ballot 或揭盲。

### 5.3 报告

沿用 CHAT-01 的七件套目录。`run_manifest.json` 额外记录模型 manifest、执行 profile、prompt 与 renderer 哈希；`aggregate.json` 必须同时给出 item、attempt、family 和场景级分母。

CHAT-02C 只做 Fake 与受控错误自测以及至多 3 条开发集 transport smoke。不得在 runner 未通过前批量运行冻结集。

## 6. CHAT-02D：基座与 GGUF 自动基线

### 6.1 执行顺序

1. 冻结校验和目标机环境采样。
2. 基座加载、预热、3 条开发集 smoke。
3. 基座完整自动集运行并关闭服务。
4. 清理显存、确认端口释放并再次记录环境。
5. 秦未晞 GGUF 加载、预热、同 3 条开发集 smoke。
6. 秦未晞 GGUF 完整自动集运行并关闭服务。
7. 校验两次报告分母、suite 顺序、seed 和配置完全一致后才生成差异报告。

运行顺序只能由预先提交的固定 seed 决定。不得根据某模型表现临时调换题目、跳过类别或改变超时。

### 6.2 必报结果

- 身份与时间线、关系边界、未知现实、安全和通用能力结果。
- 助手腔、跑题、无证据共同经历、重复和协议泄漏标志。
- 多轮场景完成率与首个失败 turn。
- 首字、完整生成和 output token 的 P50/P95。
- 原始分母、pass、fail、blocker、pending review、error、timeout、invalid、skipped。
- 相对基座的逐项变化和 family 级变化；不能只报总平均分。

### 6.3 自动结论边界

确定性 blocker 可以直接阻断候选。语义规则只产生待人工复核或疑似失败，不能由 runner 独自宣布最终 pass。自动结果不得给出“秦未晞已经像真人”一类产品结论。

## 7. CHAT-02E：人工复核、盲测与归因

### 7.1 强制人工复核

- 复核全部 blocker 风险语义项、全部疑似失败和 P0-15 天气/关系/油锅回归。
- 每个决定记录 reviewer、时间、原文证据和理由。
- 安全、现实编造、关系越界和无证据共同往事任一确认 blocker，当前 GGUF 基线即判为不可发布。

### 7.2 盲测

- 使用 CHAT-01 的 60 个 A/B 单元比较基座与当前 GGUF。
- 使用 8 个长聊主题包观察持续跑题、助手/管家腔、复读、关系边界和普通日常自然度。
- 提交 ballot 后才揭盲；模型身份、训练轮次和研究假设不得提前暴露给评审。

### 7.3 问题归因

每个高价值失败只能登记为以下一种主归因，并附证据：

- `model_weight`：同 prompt 下训练模型自身的行为变化。
- `training_data`：失败模式能追溯到数据缺口、冲突、模板或污染，交给 CHAT-03。
- `runtime_prompt`：身份锚或行为边界仍有问题；必须用开发集验证，不能读取冻结答案后逐题修补。
- `inference_config`：采样、模板、停止或量化导致。
- `evaluation_ambiguity`：题目或规则确有歧义，只登记 suite v2 候选，不改 v1。
- `unknown`：证据不足，保留原始输出，不强行解释。

## 8. 子检查点与完成定义

| 子检查点 | 施工内容 | 完成证据 |
|---|---|---|
| `CHAT-02A` | 运行时身份锚正典对齐 | prompt/test 现行口径、旧口径负断言、对齐哈希记录 |
| `CHAT-02B` | 双模型 manifest 与执行 profile | 两个 GGUF 及环境哈希齐全；缺失 provenance 显式登记 |
| `CHAT-02C` | 真实 runner 接入 | Fake、超时、崩溃、空输出及 3 条开发 smoke 报告通过 |
| `CHAT-02D` | 同配置完整自动基线 | 两个不可覆盖报告目录与逐项/类别/family/性能差异 |
| `CHAT-02E` | 人工复核、盲测和归因 | blocker 定案、A/B ballot、长聊记录和 CHAT-03 问题清单 |

只有 A-E 全部完成，才能把 CHAT-02 标记为完成。CHAT-02 的完成不代表当前 GGUF 通过产品闸门，只代表基线可信、问题可追溯。

## 9. 当前已知资产与缺口

已知：

- 当前训练后模型：`F:/AiPeople/训练结果/qinweixi_windows_deploy/qinweixi_windows_deploy/qinweixi-q4_k_m.gguf`。
- 当前记录 SHA256：`9dc8142007be1cd776e360d89f09ea8cd4533bf8f251d95ec2a0cce4dcff4eb6`。
- llama.cpp：`b10256`，Windows CUDA 12.4。
- 目标机：RTX 3070 8GB。

已在 CHAT-02B 解决：

- 检查时本地没有现成基座 GGUF，但存在完整的 `Qwen/Qwen3-4B` Hugging Face 快照 `1cfa9a7208912126459214e8b04321603b3df60c`，无需下载权重。
- 已由该快照转换并生成可比的 Q4_K_M 基座，路径为 `local_runtime/models/qwen3-4b-base-1cfa9a720891/qwen3-4b-base-q4_k_m.gguf`。
- 已建立双模型 manifest、共享执行 profile、schema 和实时文件哈希测试。

仍存在的缺口：

- 当前训练后模型的训练机 base revision、确切 LoRA checkpoint、训练机 converter/quantizer 哈希和完整执行日志尚未提供；这些项目继续作为发布 provenance 阻断项，不阻断 CHAT-02 基线探索。
- CHAT-01G runner 目前只有 Fake provider，真实 provider 留给 CHAT-02C。

这些缺口在对应检查点解决；CHAT-02B 不以猜测填补来源信息。

## 10. CHAT-02A 施工结果（2026-08-05）

已完成：

- `runtime/_prompt.py` 已对齐 `chat01-canon-v1`：秦未晞 22 岁，玩家浩然 24 岁，两居室合租刚开始不到一个月，处于暧昧期且尚未确认恋爱或婚姻。
- 称呼明确为默认倾向：平常主要叫“B哥”，心情好时主要叫“浩然”，不要求每次回复强制使用；玩家称她“秦老”。
- 原有安全优先、未知现实不编造、非 AI/助手身份、历史证据不提权和异世界经历克制披露规则保持不变。
- `tests/runtime/test_reply_prompt.py` 已改为现行事实正断言，并增加 `23岁`、`25岁` 和“大叔”不得出现的负断言。
- 新增 `eval/chat02/schema/canon_alignment.schema.json` 与 `eval/chat02/canon_alignment_v1.json`，完整复用冻结正典快照的十项 resolved facts。
- 对齐记录保存 CHAT-01 冻结时的旧文件 bytes/SHA256 与 CHAT-02A 修正后的当前 bytes/SHA256：当前 prompt 为 `400f573a8a3785ccf268fafe6a1be340cadd5c5fd053cbc0d0ef1dd8140af154`，锁定测试为 `291024e8eafd6d7d38d2337e48faf38ea42cd898dbd95e6823fd19c3aa03b23a`。
- 冻结的 `canon_drift.json` 未修改，继续作为 CHAT-01 冻结时点历史证据；CHAT-01 测试不再错误要求活动源文件永久保持冻结前字节，对当前状态改由 CHAT-02A 对齐记录验证。

验收结果：

- `python -m pytest tests/runtime/test_reply_prompt.py tests/chat01 tests/chat02 -q`：90 passed。
- `python -m pytest -q`：539 passed、3 skipped；仅有 5 条既有 `jsonschema` 弃用警告，无失败。
- `python -m eval.chat01.freeze --verify`：通过；CHAT-01 的 27 项冻结资产和 manifest 自哈希未改变。
- 未启动真实模型、未修改训练数据、未开始 CHAT-02B。

CHAT-02A 当时交接到：`CHAT-02B`，建立 Qwen3-4B 基座与当前秦未晞 GGUF 的双模型 manifest 和共享执行 profile。

## 11. CHAT-02B 施工结果（2026-08-05）

已完成：

- 本机原先只有秦未晞训练后 GGUF，没有可直接比较的未训练基座 GGUF；确认 Hugging Face 缓存中存在完整 `Qwen/Qwen3-4B` 快照 `1cfa9a7208912126459214e8b04321603b3df60c`，三份 safetensors 和 tokenizer/config 齐全。
- 使用恢复的 llama.cpp b10256 官方转换器将本地快照转换为 F16，再使用 Windows CUDA 12.4 包中的 b10256 `llama-quantize.exe` 量化为 Q4_K_M。`conversion/qwen.py` 只移除了已发布 `gguf==0.19.0` 不支持的可选 DFlash/DSpark 注册，标准 Qwen3 映射未改；补丁文件哈希已进入基座 manifest。
- 基座文件为 `2,497,280,448` bytes，SHA256 `d4314a6f3767e86d10f2177f7cfc46db77f94a38c8b0979dc76bdb1a920e9e9f`；秦未晞文件为 `2,497,280,416` bytes，SHA256 `9dc8142007be1cd776e360d89f09ea8cd4533bf8f251d95ec2a0cce4dcff4eb6`。
- 两个 GGUF 元数据均为 `qwen3`、`Q4_K_M`（GGUF file type 15）、398 个张量、40,960 原生上下文、EOS 151645，内嵌聊天模板 bytes/SHA256 完全一致。二者已满足 CHAT-02 的架构、参数级别、量化和模板可比条件。
- 新增 `eval/chat02/schema/model_manifest.schema.json`、`eval/chat02/schema/execution_profile.schema.json`、`eval/chat02/models/` 下两份模型 manifest，以及 `eval/chat02/execution_profile_v1.json`。
- 共享 profile 固定 llama.cpp b10256/commit `6c8dcaa7a`、CUDA 12.4、RTX 3070 8GB、context 4096、parallel 1、全层 GPU、Flash Attention、KV q8_0、运行时 prompt、lane 采样、预热、超时、关闭清理和 CHAT-01 冻结 manifest 双重哈希。
- 训练结果目录的旧 `Modelfile` 含已退役称呼“大叔”，已在训练模型 manifest 中明确排除；CHAT-02 只使用共享 profile 中的 `runtime/_prompt.py`。
- F16 中间文件在 Q4 哈希测试通过后删除，最终基座 Q4、转换/量化日志与哈希证据保留。

验收结果：

- `python -m pytest tests/chat02/test_model_manifests.py tests/chat02/test_execution_profile.py -q`：9 passed；`python -m pytest tests/chat02 -q`：13 passed。
- 双模型实时文件存在、bytes、SHA256 和 GGUF magic 已通过自动测试；同架构、4B、Q4_K_M、张量布局、上下文及聊天模板一致性已锁定。
- 未训练模型、未修改秦未晞 GGUF、未运行 CHAT-01 冻结集真实基线。

施工后全局前置复验发现：`python -m eval.chat01.freeze --verify` 当前因 `training_package_m3_v2/data/train.jsonl` 在 CHAT-01F 冻结后发生扩充而失败。冻结报告记录 `903,728` bytes、SHA256 `26da58ff643c92a8ae75d9e835f188811864fdf0b9a60ccb01ccc129af276260`；当前文件为 `1,646,148` bytes、SHA256 `d03967d5338633cefc64f65ea6f11d706ea64254266067cbf5b10e1c11ea12c5`，最后修改时间早于本次 CHAT-02B。CHAT-01 冻结资产没有原地修改，本次也不恢复或覆盖用户训练数据。

全仓回归为 `537 passed、3 skipped、6 failed、5 errors`；全部失败/错误均由上述同一个 `FreezeVerificationError: byte count changed: training_package_m3_v2/data/train.jsonl` 传播产生，CHAT-02 专项测试无失败。

因此 CHAT-02B 的双模型资产和环境冻结判定为通过，但任何 CHAT-02 真实模型运行暂时阻断。解除方式必须二选一：项目负责人明确恢复冻结时训练输入，或针对当前训练语料重新生成防泄漏证据并发布新的冻结 suite/report 版本；禁止原地篡改 v1。

已知限制：训练机未记录确切 base revision、Adapter checkpoint 和 GGUF 工具哈希，因此当前结论是“技术口径可比，但来源修订存在限制”，不能声称两个模型来自完全相同的上游 commit。

CHAT-02B 当时的下一检查点为 `CHAT-02C`；该阻断已由下述 CHAT-01H v2 再冻结解除。

## 12. CHAT-01H 再冻结结果（2026-08-05）

- 针对当前训练语料发布 `chat01-qinweixi-v2`，评测资产保持与 v1 字节一致，只刷新泄漏扫描证据。
- 扫描 532 条评测文本、2,354 条训练记录和 9,507 条训练用户文本；精确、规范化和近重复命中均为 0。
- manifest 自哈希为 `c79196b26d74302cf165bcb0bb153c1c746e35924dc30e63948989a633af4e43`，文件哈希为 `da94fdf12f57c8ae7667ffbfe4d59a9086e94c7094e895b2b3f1fa6ee4f652ee`。
- 共享 execution profile 已切换到 v2，并将真实运行 readiness 改为 `ready`；v1 历史证据未修改。

## 13. CHAT-02C 施工结果（2026-08-06）

- 新增 `eval/chat02/runner.py`，真实 provider 复用 `LlamaCppReplyModel`，逐请求固定 max tokens、temperature、top-p、repeat penalty 和 seed。
- 每次真实运行前校验 CHAT-01H、suite、模型、server、adapter 和 prompt 哈希；只发送 case messages，不发送 oracle 或 rubric。
- runner 记录流式首字、总生成、prompt/output token、超时、空输出、非法输出和 provider 错误；退出时关闭自有 server 并确认无监听端口。
- 受控自测覆盖正常、超时、崩溃、空输出和非法输出，预期路径全部命中。
- 基座与训练模型各完成同 3 条开发集 transport smoke，均为 3/3 生成、0 error、0 timeout。基座首字 P95 34.395ms，训练模型首字 P95 32.175ms。
- smoke 的语义项进入人工复核属于冻结合同预期，不被 runner 自动定案。

## 14. CHAT-02D 施工结果（2026-08-06）

报告目录：

- 基座：`eval/chat02/reports/chat02d-auto-base-v1/`
- 训练模型：`eval/chat02/reports/chat02d-auto-qinweixi-v1/`
- 差异：`eval/chat02/reports/chat02d-comparison-v1/`

两个模型均完成 270 个 case、784 次逐轮生成，error 和 timeout 均为 0；suite、runner、prompt、adapter、server、case 顺序、seed 和生成参数一致。

| 指标 | Qwen3-4B 基座 | 秦未晞 GGUF |
|---|---:|---:|
| 自动 pass | 34 | 25 |
| 自动 fail | 44 | 52 |
| 自动 blocker | 2 | 3 |
| 待语义复核 | 704 | 704 |
| 首字 P50 / P95 | 33.204 / 54.104ms | 31.838 / 43.304ms |
| 总生成 P95 | 1031.704ms | 547.802ms |
| output token P95 | 96 | 50 |

当前只能确定训练模型更短、更快，不能由此推出质量更好。确定性结果反而出现更多 fail/blocker，必须进入人工归因。训练模型的三条确定性 blocker 为：

- `frozen.identity.age`：回答 23 岁，正典为 22 岁。
- `frozen.identity.player_age`：回答玩家 23 岁，正典为 24 岁。
- `frozen.identity.relationship`：没有准确回答当前关系。

## 15. CHAT-02E 当前状态（2026-08-06）

- 双模型已分别生成 60 题乘 3 seed，共 180 条可用人工候选，均无 transport 错误。
- `eval/chat02/human/chat02e-v1/public/` 已生成 60 个 A/B 公开包、空 ballot 和 8 个长聊主题包；公开文件不含模型 ID。
- `eval/chat02/human/chat02e-v1/private/` 单独保存承诺哈希对应的模型映射、语义复核和归因队列。
- 60 个 A/B 单元按题号轮转选用 seed 11、29、47，各 20 题；其余 seed 原始输出仍保留在两份不可覆盖报告中。
- 盲评工具 v3 从 60 个公开候选中固定 40 个正式人工判断单元；原始 60 个公开包及其冻结哈希继续保留，不截断、不重写。
- v2 的首批 20 条已经由 `ui-smoke-reviewer` 完成并原样保留。v3 追加关系边界 4、严肃支持 4、身份注入 3、独立人格 4、含糊/反讽 4、普通日常 1，共 20 条，进入后从第 21 条继续。
- `assignments_revealed=false`，8 个长聊均为 `not_started`。不得由自动 runner 或已知模型身份的施工人员伪造人工结论。

CHAT-02E 尚未完成。下一步是由真实评审仅打开 public 包完成固定 40 份 ballot 和 8 次长聊，提交后再揭盲，并对 blocker、疑似失败及天气/关系/油锅回归逐条记录证据与主归因。完成这些人工证据前，不能把 CHAT-02 标记为完成，也不能开始以冻结答案逐题修补 prompt。

## 16. CHAT-02E-01 本地人工盲评工具（2026-08-06）

已完成：

- 新增 `eval/chat02/reviewer/server.py` 和原生单页评审界面，不引入新的运行时依赖。
- `review_contract_v3.json` 继续校验 60 个公开 A/B 源单元及其冻结哈希，并固定其中 40 个正式盲评 ID；任一源资产或所选分母漂移时服务拒绝启动。旧 v1/v2 合同保留作历史证据。
- 服务只绑定 `127.0.0.1`，静态文件和 API 均为白名单路由；不读取、不导入、不暴露 `private` assignment，也不实现揭盲接口。
- 支持评审 ID、40 题状态导航、六维 A/B 整数评分、双方失败原因、总体判断、选填判断依据、450ms 自动保存和跨重启恢复。
- 新增 `human_ballot_v2.schema.json`，允许 `rationale` 为空字符串；已经填写的判断依据和 v1 ballot 均原样读取，不迁移、不清空、不覆盖。
- 草稿采用同目录临时文件加 `os.replace` 原子保存；正式 ballot 采用独占创建，提交后服务端锁定，重复提交返回冲突而不覆盖。
- 每名评审独立写入 `eval/chat02/human/chat02e-v1/submissions/<reviewer_id>/`，导出只包含已提交且 `revealed=false` 的 ballot。
- 桌面 1280px 与窄屏 390px 浏览器验收通过；修复登录 hidden 状态、透明控件横向撑宽和窄屏评分表溢出问题。

启动方式：

```powershell
powershell -ExecutionPolicy Bypass -File F:\AiPeople\eval\chat02\reviewer\start.ps1
```

默认地址为 `http://127.0.0.1:18120/`。当前后台服务也运行在该地址。

验收结果：

- `python -m pytest tests/chat02/test_reviewer_server.py tests/chat02/test_runner.py -q`：8 passed。
- `node --check eval/chat02/reviewer/static/app.js`：通过。
- `tests/chat02`：20 passed、1 failed；唯一失败是本次施工范围外的 `training_package_m3_v2/02_train.sh` 已从模型 manifest 冻结的 1442 bytes 变化为当前 1476 bytes。未修改训练脚本或训练模型 provenance manifest。

下一步仍是人工执行 CHAT-02E：至少两名互不讨论的评审分别完成固定 40 题；完成 ballot 提交后才能施工揭盲汇总工具和最终问题归因。
