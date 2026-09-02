# 模型对比资料索引

> 建立日期：2026-08-24  
> 用途：集中存放当前项目已经形成的模型对比结论、人工审核材料与关键结果快照。

## 1. 使用原则

本目录是便于设计审查的汇总副本，不替代 `AiPeople/eval/` 下的原始评测目录。

- 原始报告、逐轮输入输出、模型工件信息和运行脚本仍以 `AiPeople/eval/` 为准。
- 本目录不移动或改写原始证据，避免破坏原报告中的相对链接和复现实验脚本。
- Markdown 报告以可读结论为主；JSON 只收录对理解历史模型对比有直接价值的快照。
- 不同报告的测试对象、Prompt、历史窗口和评分规则并不完全相同，不能把各报告百分比直接横向排名。

## 2. 当前总判断

2026-08-28 Gemma NF4资源更新：`Gemma 4 12B`官方裸基座以Transformers 5.5 + Unsloth + bitsandbytes NF4运行，未加载Adapter，在冻结第60轮正式上下文中测得整机GPU峰值`9939MiB`、相对探针基线增加`8311MiB`。结合既有裸基座`58/60（96.7%）`和平均首字`1.65秒`，该裸基座已同时满足当前质量、3秒首字和12GB显存门槛；当前白未晞QLoRA仍为`39/60`并淘汰。详见[`07_低显存选型/Gemma4-12B_NF4裸基座显存实测_20260828.md`](./07_低显存选型/Gemma4-12B_NF4裸基座显存实测_20260828.md)。

2026-08-28低显存选型更新：`Qwen2.5-14B-Instruct`白未晞QLoRA同栈60轮人工审核已完成。NF4裸基座为`55/60（91.7%）`，加载Adapter后仅`39/60（65.0%）`，下降16轮；相对选择为裸基座胜16轮、QLoRA胜1轮、持平43轮。当前Adapter低于`54/60`保持线，已淘汰。详见[`07_低显存选型/Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md`](./07_低显存选型/Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md)。

1. 此前 LoRA Q4 与GPT-5.6-sol的60轮人工审核分别为好 `22/60` 和 `60/60`。这证明在同一产品输入契约和该条测试轨迹上，错误不是“训练数据未覆盖原句就必然无法回答”，更关键的是模型能否正确处理当前输入、权威状态和纠正关系。
2. 同一 F16 基座的 Adapter 因果隔离试验中，裸基座通过率为 `54/72`，加载当前 Adapter 后为 `31/72`，下降 `31.9` 个百分点，成对符号检验 `p=5.65e-06`。这支持“当前 Adapter 是该固定测试集上的回归因素”，但不能外推为所有 LoRA 或所有线上错误都由 Adapter 导致。
3. 增加上下文是必要条件，但不是充分条件。当前 LoRA 已经能看到正确的当前世界，仍会忽略、误用或给正确结论附加虚构内容。
4. V3 语义审核适合作为残余错误拦截层，不能替代生成模型本身的指令遵循、事实约束和局部语言逻辑能力。
5. `GLM-4-9B-0414 Q6_K`和`GLM-4.6V-Flash Q4_K_M`的10轮关键状态硬门均为`4/6`。为避免误判，用户要求继续完整60轮；人工审核到第18轮时因错误密集、整体质量很差而主动停止。已审核部分分别为`14/18`好和`13/18`好，两模型均淘汰，不补剩余审核。
6. 剩余候选也已完成硬门：`Moonlight Q4_K_M`整机`12598MiB`超过12GB；降为Q3后关键状态仅`1/6`且契约违规`10/10`。`Kimi-VL Q4_K_M`纯文本峰值`11557MiB`，但关键状态仅`3/6`且契约违规`4/10`。两者均淘汰，不进入LoRA训练。
7. `Qwen2.5-14B-Instruct Q4_K_M`裸基座为`54/60`；随后白未晞QLoRA同栈复验中，NF4裸基座`55/60`、Adapter为`39/60`。全assistant监督仍未阻止能力回归，当前Adapter淘汰。
8. `DeepSeek-R1-Distill-Qwen-14B Q4`原始硬门为语义`8/10`、关键状态`5/6`，但思考模式热启动首字`3.20秒`。200字限制复测约束遵守为`0/10`并发生4轮分析/旁白泄漏；随后原生`think=false`复测仍返回10/10完整思考，回复和隐藏思考与基线逐字相同。当前工件停止验证，不进入60轮。

最新结论先读：[`07_低显存选型/Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md`](./07_低显存选型/Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md)

## 3. 文件目录

### 01_基座与LoRA

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `裸基座与LoRA_正式多轮对话测试及公平性审查_20260821.md` | 正式上下文下的初步多轮诊断与公平性边界 | `AiPeople/eval/world_mind_p0/BASE_LORA_正式多轮对话测试与公平性审查报告_20260821.md` |
| `裸基座与LoRA_同输入上下文对照_20260821.md` | 配对 raw context 对照 | `AiPeople/eval/world_mind_p0/paired_raw_context_20260821-173515/report.md` |
| `裸基座与Adapter_记忆纠正逐轮模拟_20260821.md` | 历史记忆、两次纠正、话题切换和旧动作诱导；含句内逻辑缺陷记录 | `AiPeople/eval/world_mind_p0/memory_correction_trajectory_20260821-191652/report.md` |

### 02_Adapter因果与量化

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `LoRA_Adapter因果隔离测试_20260821.md` | 同一 F16 基座只改变 Adapter 的因果隔离结果 | `AiPeople/eval/world_mind_p0/adapter_causality_ab_20260821-182156/report.md` |
| `LoRA_Adapter因果隔离公平性复核_20260821.md` | 上述试验的公平性复核 | `AiPeople/eval/world_mind_p0/adapter_causality_ab_20260821-182156/fairness_report.md` |
| `LoRA_F16与Q4量化对照_20260821.md` | 检查量化是否构成主要回归来源 | `AiPeople/eval/world_mind_p0/lora_f16_q4_quant_ab_20260821-175129/report.md` |

### 03_六十轮产品测试

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `LoRA_60轮玩家模拟测试报告_20260821.md` | 连续玩家轨迹测试结果 | `AiPeople/eval/world_mind_p0/player_simulation_60turn_20260821-213213/report.md` |
| `LoRA_60轮玩家模拟人工审核_20260821.md` | 对应逐轮人工审核 | `AiPeople/eval/world_mind_p0/player_simulation_60turn_20260821-213213/manual_review.md` |
| `裸基座正常上下文_vs_完整程序_60轮人工审核_20260822.md` | 产品主对照的人工审核结果 | `AiPeople/eval/world_mind_p0/product_main_60turn_20260822-112309/manual_review.md` |

### 04_LoRA与GPT

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `当前LoRA_Q4与GPT-5.6-sol_60轮人工审核结论_20260824.md` | 本次审核的正式分析结论 | 本目录新生成 |
| `当前LoRA_Q4与GPT-5.6-sol_60轮测试说明_20260822.md` | 测试身份、Oracle 隔离和公平性边界 | `AiPeople/eval/world_mind_p0/lora_gpt56_internal_60turn_20260822-181838/测试说明.md` |
| `当前LoRA_Q4与GPT-5.6-sol_60轮用户审核_20260824.json` | 用户审核原始副本，60/60 已评 | `C:/Users/songa/Downloads/lora_gpt56_60turn_user_review (1).json` |

### 05_历史模型与检查点

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `历史模型内部40项对比.md` | 较早的跨模型内部对照 | `AiPeople/eval/cross_model_internal_40/report.md` |
| `基座模型检查点对比.md` | 基座检查点选择对照 | `AiPeople/eval/base_model_checkpoint_comparison/report.md` |
| `Q4模型对比_20260817.json` | Q4 工件结果快照 | `AiPeople/eval/world_mind_p0/q4_models_compare_20260817-154351.json` |
| `系统12推理对比.json` | 系统 12 推理结果快照 | `AiPeople/eval/world_mind_p0/sys12_inference_comparison.json` |
| `系统12推理对比_Q4.json` | 系统 12 Q4 推理结果快照 | `AiPeople/eval/world_mind_p0/sys12_inference_comparison_q4.json` |

### 06_Qwen3.5与GPT

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `Qwen3.5-27B_Q4与GPT-5.6-sol_60轮测试说明_20260824.md` | Qwen3.5-27B在RTX 4090上的60轮测试身份、生成设置与公平性边界；等待用户人工审核 | `AiPeople/eval/world_mind_p0/qwen35_27b_gpt56_60turn_20260824-114447/测试说明.md` |
| `Qwen3.5-9B_Q6与GPT-5.6-sol_60轮测试说明_20260824.md` | 面向16GB显存的Qwen3.5-9B Q6 60轮测试身份、工件校验、性能与公平性边界；等待用户人工审核 | `AiPeople/eval/world_mind_p0/qwen35_9b_q6_gpt56_60turn_20260824-162809/测试说明.md` |

### 07_低显存选型

| 文件 | 内容 | 原始来源 |
|---|---|---|
| `12GB显存与3秒首字约束下模型选型结论_20260826.md` | 汇总Gemma、Ministral、Qwen及两款GLM候选的质量、首字速度和显存证据，给出12GB部署主候选、硬门淘汰项与剩余未测模型 | 低显存60轮报告、用户审核、27B卸载探针及GLM 10轮硬门 |
| `Gemma4-12B_普通与思考_60轮人工审核结论_20260826.md` | Gemma 4 12B普通/思考模式的人工质量、速度、显存与选型影响 | `AiPeople/eval/world_mind_p0/gemma4_12b_60turn_20260826-174500/report.json`及用户审核JSON |
| `Gemma4-12B_裸基座与白未晞QLoRA_60轮人工审核结论_20260827.md` | 同一Gemma官方基座和NF4运行栈开关Adapter的因果对照；记录裸基座58/60、QLoRA 39/60、回归根因分析及当前Adapter淘汰结论 | `AiPeople/eval/world_mind_p0/gemma4_12b_base_qlora_60turn_20260827-104300/report.json`及用户审核JSON |
| `Gemma4-12B_NF4裸基座与QLoRA质量差距根因研究_20260828.md` | 专项解释58/60降至39/60的原因；证明最终回复单点监督、任务错位、纠正方向混淆和风格偏置，并补充checkpoint-200/247回归阶段探针 | 训练脚本、数据统计、正式60轮A/B及`gemma4_12b_qlora_checkpoint_probe_20260828` |
| `Gemma4-12B_全Assistant监督修复与重新训练结果_20260830.md` | 修复只监督最后回复的问题；4073/4073条assistant、15种行为类型全部100%监督，完成247步重新训练并记录新Adapter哈希、检查点和验收边界 | 新训练脚本、自动测试及`baiweixi_gemma4_12b_all_assistant_v2`训练工件 |
| `Gemma4-12B_裸基座旧QLoRA新全监督QLoRA_10轮对比_20260830.md` | 冻结前10轮三方硬门：裸基座9/10、旧Adapter既有人工5/10、新全监督Adapter核心5/10且严格2/10；新最终工件不进入60轮 | 旧正式60轮前10轮及`gemma4_12b_base_old_new_10turn_20260830/new_adapter/probe.json` |
| `Gemma4-12B_全Assistant监督早期检查点_冻结10轮对比_20260830.md` | checkpoint-25/50/75/100冻结10轮硬门；最佳checkpoint-75仅严格4/10、核心7/10、关键状态4/6，证实早停不能根治回归并停止当前数据路线 | `AiPeople/eval/world_mind_p0/gemma4_12b_all_assistant_checkpoint_10turn_20260830/` |
| `Gemma4-12B_裸基座与白未晞QLoRA_60轮用户审核_20260827.json` | 上述结论对应的60轮用户审核内容快照；换行格式已按仓库规范化 | `C:/Users/songa/Downloads/gemma4_base_qlora_60turn_user_review.json` |
| `Gemma4-12B_NF4裸基座显存实测_20260828.md` | 官方裸基座NF4未加载Adapter，在冻结正式上下文中整机峰值9939MiB、相对基线增加8311MiB；补齐12GB同栈资源证据 | `AiPeople/eval/world_mind_p0/gemma4_12b_nf4_base_vram_20260828.json` |
| `Ministral3-14B_60轮训练前淘汰结论_20260826.md` | Ministral 3 14B的性能、状态错误、输出契约问题及不进入LoRA训练的决策 | `AiPeople/eval/world_mind_p0/ministral3_14b_60turn_20260826-214500/report.json` |
| `GLM两款低显存候选_60轮提前终止人工审核结论_20260827.md` | 两款GLM完成60轮生成、人工审核前18轮后因错误密集提前终止；记录部分计分、典型错误与淘汰结论 | `AiPeople/eval/world_mind_p0/glm_low_vram_60turn_20260827-172736/report.json`及`user_review.json` |
| `Moonlight与KimiVL_10轮硬门淘汰结论_20260827.md` | Moonlight Q4资源失败、Q3质量失败，以及Kimi-VL纯文本状态纠正和输出契约失败结论 | `AiPeople/eval/world_mind_p0/moonlight_kimi_probe_20260827-224251/hard_gate_review.md` |
| `Qwen2.5-14B_10轮硬门通过结论_20260828.md` | Qwen2.5-14B关键状态5/6、零契约违规、10.43GB整机GPU的硬门通过结论及第10轮主体归属缺陷 | `AiPeople/eval/world_mind_p0/qwen25_14b_probe_20260828-000303/hard_gate_review.md` |
| `Qwen2.5-14B与Qwen3.5-27B_60轮测试说明_20260828.md` | Qwen2.5-14B本次60轮与冻结Qwen3.5-27B对照的模型身份、公平性、速度和资源证据；人工审核已完成 | `AiPeople/eval/world_mind_p0/qwen25_14b_60turn_20260828-094615/report.json` |
| `Qwen2.5-14B与Qwen3.5-27B_60轮人工审核结论_20260828.md` | 用户完成60/60轮审核：14B为54/60，27B对照为56/60；14B进入受控QLoRA验证 | `AiPeople/eval/world_mind_p0/qwen25_14b_60turn_20260828-094615/user_review.json` |
| `Qwen2.5-14B与Qwen3.5-27B_60轮用户审核_20260828.json` | 本次网页保存的原始逐轮人工审核副本 | `AiPeople/eval/world_mind_p0/qwen25_14b_60turn_20260828-094615/user_review.json` |
| `Qwen2.5-14B_白未晞QLoRA训练结果_20260828.md` | 4073/4073 assistant监督、完整训练指标、Adapter哈希与加载冒烟；60轮复验为39/60，当前Adapter淘汰 | `AiPeople/training_packages/training_package_baiweixi_qwen25_14b/outputs/baiweixi_qwen25_14b_unsloth/run_metrics.json` |
| `Qwen2.5-14B_裸基座与白未晞QLoRA_60轮测试说明_20260828.md` | 同一NF4基座关闭/开启Adapter的60轮因果A/B；人工审核已完成 | `AiPeople/eval/world_mind_p0/qwen25_14b_base_qlora_60turn_20260828-115723/report.json` |
| `Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md` | 用户完成60/60轮审核：NF4裸基座55/60、QLoRA 39/60；当前Adapter淘汰，并整理后续候选缺口 | 同一运行目录的`report.json`及`user_review.json` |
| `DeepSeek-R1-Distill-Qwen-14B_Q4_10轮硬门结论_20260828.md` | 原配置质量压线、首字未达标；200字限制不被遵守，原生关闭思考也未生效，当前停止验证 | 三个DeepSeek 10轮探针目录的`hard_gate_review.md` |

## 4. 解释边界

- GPT 一侧是 60 个隔离的 Codex 内部 GPT-5.6-sol 调用，不是正式 OpenAI API 接口测试，因此本结果不提供可比较的 API 费用、延迟或吞吐结论。
- 最新 60 轮测试是一条连续的人造产品轨迹，不是 60 个独立玩家会话，不能直接估算真实玩家总体错误率。
- 两个模型各自的回复会进入各自后续历史，第二轮后历史自然分叉；该测试衡量产品体验，不是纯权重因果试验。
- “GPT 本次 60/60 为好”只描述本批人工审核，不表示 GPT 在未来输入上保证零错误。
