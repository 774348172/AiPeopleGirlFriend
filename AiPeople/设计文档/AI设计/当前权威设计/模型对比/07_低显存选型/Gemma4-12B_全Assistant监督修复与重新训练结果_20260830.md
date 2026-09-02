# Gemma 4 12B全Assistant监督修复与重新训练结果

> 日期：2026-08-30  
> 状态：训练完成，工件与加载冒烟通过；尚未完成60轮质量验收  
> 新工件：`baiweixi_gemma4_12b_all_assistant_v2`  
> Adapter SHA256：`38a8b7d55d5aac4bc6b8d0f2475215afa4de2ca30f602cc6acbe5a293e3fe129`

> 后续10轮硬门（2026-08-30）：新最终Adapter核心语义命中`5/10`，严格可用仅`2/10`，未通过进入60轮的门槛。裸基座为`9/10`，旧Adapter既有用户审核为`5/10`。详见`Gemma4-12B_裸基座旧QLoRA新全监督QLoRA_10轮对比_20260830.md`。

## 1. 本次目标

本次只施工根因研究中的第一优先项：

1. 每段对话中的所有assistant回复都参与loss；
2. 训练前按行为类型输出assistant总数、实际监督数和监督token数，任一核心类型覆盖不完整即停止；
3. 对文本拼接边界、token拼接边界、特殊token和assistant mask逐token验证；
4. 修复通过后重新训练Gemma 4 12B NF4白未晞QLoRA。

本次没有新增动态世界训练数据，也没有修正`reply_correction`内部两种相反的纠正方向。因此新工件是否解决60轮状态漂移仍需单独评测。

## 2. 代码修复

新增独立监督构造器：

- 对每一条assistant消息分别渲染“生成前缀”和“包含该回复的完整前缀”；
- 同时验证两者是完整训练文本和完整token序列的精确前缀；
- 只将每条assistant正文及其轮次结束边界写入labels；
- system、user和assistant角色起始控制token保持`-100`；
- 禁止截断。任一样本超过`MAX_SEQ_LENGTH`直接报错，不能静默丢失assistant目标；
- 根据metadata逐行统计15种核心行为类型，任一类型assistant覆盖率不是100%直接停止；
- 打包训练数据必须是权威源数据经过已声明的纯对白转换所得，任何未声明内容差异直接停止。

自动测试覆盖：

- 多条assistant span全部监督；
- `<turn|>`类结束特殊token被监督，角色起始边界被屏蔽；
- 每个token的label状态与assistant span严格一致；
- 超长序列禁止截断；
- generation prompt与完整模板边界不一致时失败关闭。

测试结果：`3 passed`。

## 3. 训练前真实数据审计

真实Gemma 4普通模式tokenizer对全部1973段数据完成逐行、逐assistant、逐token审计：

| 指标 | 旧方案 | 新方案 |
|---|---:|---:|
| 数据段数 | 1973 | 1973 |
| assistant消息总数 | 4073 | 4073 |
| 实际监督assistant | 1973 | 4073 |
| assistant覆盖率 | 48.4% | 100% |
| 监督token | 33120 | 64201 |
| 被监督的轮次结束特殊token | 未单独审计 | 4073 |

新方案新增`31081`个监督token，总监督量约为旧方案的`1.94`倍。

各行为类型审计：

| 行为类型 | 段数 | assistant总数 | 实际监督 | 监督token |
|---|---:|---:|---:|---:|
| reply_boundary | 21 | 44 | 44 | 612 |
| reply_canon_qa | 34 | 69 | 69 | 1253 |
| reply_casual | 574 | 1172 | 1172 | 18865 |
| reply_correction | 29 | 60 | 60 | 982 |
| reply_emotion | 209 | 432 | 432 | 6603 |
| reply_general | 57 | 117 | 117 | 2075 |
| reply_identity | 76 | 154 | 154 | 2628 |
| reply_item | 118 | 242 | 242 | 3886 |
| reply_memory | 98 | 204 | 204 | 4360 |
| reply_protective | 184 | 375 | 375 | 5060 |
| reply_quiet_company | 21 | 45 | 45 | 609 |
| reply_romance | 427 | 895 | 895 | 12905 |
| reply_safety | 36 | 78 | 78 | 1343 |
| reply_supportive | 61 | 128 | 128 | 1998 |
| reply_vague | 28 | 58 | 58 | 1022 |

所有15种类型均为100%覆盖。最关键的`reply_correction`从旧方案漏掉大量中间纠正，变为`60/60`条assistant全部监督。

## 4. 重新训练结果

| 指标 | 结果 |
|---|---:|
| 优化步骤 | 247/247 |
| epoch | 1.0 |
| 实测耗时 | 6615.5秒，约110.3分钟 |
| 最终平均train loss | 1.9667855 |
| 峰值PyTorch GPU分配 | 12590.48 MiB |
| 可训练参数 | 32784384 |
| 实际加载参数 | 6793905760 |
| 量化 | bitsandbytes NF4 |
| rank / alpha / dropout | 8 / 16 / 0.05 |
| 学习率 | 5e-5，cosine |
| 最大序列长度 | 1280，禁止截断 |

保存的检查点：`25、50、75、100、125、150、175、200、225、247`。旧训练只保留200和247，本次可更早定位能力回归。

49条loss日志均为有限值，无NaN/Inf。裁剪前梯度在第15、75、185和220步出现较高单点，其中第220步为`29.99`；下一记录第225步恢复至`4.09`。配置`max_grad_norm=1.0`会裁剪这些梯度。当前没有连续数值发散，但后续检查点质量对照仍应关注这些阶段。

## 5. 与旧工件的可比性边界

学习率、epoch、rank、alpha、dropout、数据和语言层目标类别均沿用当前训练方案，核心有意变化是loss mask。

但新旧最终工件并非绝对单变量：

- 旧工件记录为`35,414,016`个可训练参数，旧Adapter使用通用模块名，曾为非文本分支建立未更新的占位Adapter；
- 新工件使用328个完整语言模块路径，严格排除非语言参数，可训练参数为`32,784,384`；
- 旧分析已确认非文本分支`lora_B`为零，没有证据表明其影响文本输出，但参数范围实现差异仍必须登记。

因此后续质量变化应表述为“全assistant监督修复版相对旧工件的效果”，不能用最严格口径声称只有一个底层变量变化。对文本语言层而言，目标仍是全部`q/k/v/o/gate/up/down`投影。

## 6. 工件验证

- Adapter文件存在，大小`131234472`字节；
- Adapter SHA256与`run_metrics.json`一致；
- 最终`global_step=247`；
- 10个检查点均存在；
- 监督审计文件SHA256：`84b6ff59699f8f23dfe8efd4f918a2cabc0c6bac9d53f1eeeefdc68137734cd9`；
- 训练指标文件SHA256：`b6f780fee47ba45ef13095130a05db0633c9225689bb4c0ea03e6c9255ab1039`；
- 普通模式加载与生成冒烟通过。

冒烟输入明确当前世界“保温壶在茶几、抽屉为空”，询问是否去抽屉拿。新Adapter回复：

> 不，保温壶在茶几上。

这只证明加载、普通模式模板和基本生成可用，不是质量验收结果。

## 7. 当前结论与下一步

已完成并证实：

- 旧方案“只监督最后一条assistant”的代码缺陷已修复；
- 4073条assistant已经全部进入loss；
- 行为类型覆盖门、数据映射门、token边界门和特殊token门已经固化进正式训练脚本；
- 新Adapter完整训练并通过工件验收。

尚未证实：

- 新Adapter是否能达到裸基座`58/60`；
- 是否解决主体错位、旧状态复活、句子损坏和多事实漏答；
- 当前数据任务错位及纠正方向混淆是否仍造成回归。

冻结10轮三方对比及早期检查点对比均已完成。新最终Adapter未通过；`checkpoint-25/50/75/100`也全部未通过。相对最佳的`checkpoint-75`只有严格`4/10`、核心语义`7/10`、关键状态`4/6`，低于`8/10 + 5/6`硬门。因此不进入60轮，停止在当前数据分布上继续调学习率、步数或早停点，转入第二优先：重构同构动态状态数据、纠正方向和主体归属对照样本。

## 8. 证据

- 训练脚本：`AiPeople/training_packages/training_package_baiweixi_gemma4_12b/train_unsloth.py`
- 监督构造器：`AiPeople/training_packages/training_package_baiweixi_gemma4_12b/supervision.py`
- 自动测试：`AiPeople/tests/training_contract/test_gemma4_all_assistant_supervision.py`
- 新工件目录：`AiPeople/training_packages/training_package_baiweixi_gemma4_12b/outputs/baiweixi_gemma4_12b_all_assistant_v2/`
- 训练前审计：上述目录中的`supervision_audit.json`
- 训练指标：上述目录中的`run_metrics.json`
- 根因研究：`Gemma4-12B_NF4裸基座与QLoRA质量差距根因研究_20260828.md`
- 早期检查点硬门：`Gemma4-12B_全Assistant监督早期检查点_冻结10轮对比_20260830.md`
