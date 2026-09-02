# Qwen2.5-14B白未晞QLoRA训练结果

> 日期：2026-08-28  
> 状态：完整训练与Adapter加载冒烟通过；冻结60轮人工审核为39/60，当前Adapter已淘汰  
> 基座：`Qwen/Qwen2.5-14B-Instruct`

## 1. 当前结论

白未晞Qwen2.5-14B QLoRA已经完成1 epoch全量训练，Adapter文件完整，能够在全新进程中重新加载并生成有效中文回复。但是用户完成同栈60轮审核后，裸基座为`55/60`，QLoRA仅`39/60`。当前Adapter没有通过质量验收，应淘汰。

训练前Q4_K_M裸基座的冻结60轮人工结果为`54/60（90.0%）`。本次NF4同栈对照中的裸基座为`55/60（91.7%）`，加载Adapter后降为`39/60（65.0%）`，减少16轮。它既没有守住`54/60`最低能力线，也没有达到`57/60（95%）`产品目标。

## 2. 训练配置

| 项目 | 数值 |
|---|---|
| 训练方式 | NF4 QLoRA |
| LoRA | rank 8，alpha 16，dropout 0 |
| 目标层 | `q/k/v/o_proj`与`gate/up/down_proj` |
| 最大长度 | 1152；全量最长样本1119 tokens |
| batch | 1，梯度累积8，有效batch 8 |
| 学习率 | `5e-5`，余弦衰减，1 epoch |
| 优化器 | 8-bit AdamW |
| 数值格式 | BF16 |
| 随机种子 | `20260828` |

## 3. 监督范围修正

训练数据共有1973段完整对话、4073条assistant回复。此次没有沿用Gemma旧实验“只监督每段最后一条assistant回复”的错误掩码，而是在每段完整对话中同时监督全部assistant区间：

- 训练样本：`1973`
- 发现assistant区间：`4073`
- 实际监督assistant区间：`4073`
- 覆盖率：`100%`
- 监督tokens：`61,761`
- 因长度丢弃的上下文：`0`

system和user内容只作为上下文，不计算训练loss。该设计既保留完整多轮关系，又确保每条角色回复都获得一次监督。

## 4. 训练结果

| 指标 | 结果 |
|---|---:|
| 完成步骤 | 247/247 |
| epoch | 1.0 |
| 训练耗时 | 1664.28秒，约27分44秒 |
| 最终平均训练loss | 1.8560 |
| PyTorch峰值显存 | 10813.68MiB |
| 可训练参数 | 34,406,400 |
| Adapter大小 | 137,714,904 bytes |
| Adapter SHA256 | `f2c7e46004980045b8760ded951f14493c9ac1c09fad2e35a281032616b69417` |

保存了`checkpoint-200`、`checkpoint-247`和最终Adapter。训练数据SHA256为`52404515128d27b527ea43a3887fbed3cfed931257f495cea46ae0ee16e7d21a`。

## 5. 加载与生成冒烟

全新进程从最终Adapter重新加载NF4基座后，使用未出现在冻结60轮中的位置更新对话：

> 玩家：我刚把红色围巾从椅背收进衣柜第二层了。  
> 白未晞：嗯，我记住了。  
> 玩家：那条红色围巾现在还在椅背上吗？

模型回复：

> 不在了。你刚才已经把它收进衣柜第二层了。

该回复正确接受最新位置，没有动作旁白。单次完整生成约`2.40秒`，PyTorch峰值约`10067MiB`。这个单例只证明Adapter能加载和生成，不能据此推导60轮正确率或首字速度。

## 6. 60轮验收结论

同一冻结60轮已从第1轮重新生成完毕，NF4裸基座与NF4+Adapter均完成`60/60`且生成失败均为0。用户人工审核结果为：

- NF4裸基座：好`55/60`、差`5/60`。
- NF4+QLoRA：好`39/60`、差`21/60`。
- 相对选择：裸基座胜16轮、QLoRA胜1轮、持平43轮。

按预先冻结的判定标准：

- 低于`54/60`：Adapter淘汰。
- `54/60`至`56/60`：守住最低线，但未达到95%目标。
- 至少`57/60`：达到当前质量目标，再继续12GB部署与并发验收。
- 动作旁白、主体归属、旧状态复活、无依据追加和多事实遗漏必须单独记录。

实际结果触发“低于54/60”淘汰条件。训练loss下降、加载成功和单例冒烟均不能替代产品验收。完整结论见`Qwen2.5-14B_裸基座与白未晞QLoRA_60轮人工审核结论_20260828.md`。

## 7. 工件

- 训练脚本：`AiPeople/training_packages/training_package_baiweixi_qwen25_14b/train_unsloth.py`
- 最终Adapter：`AiPeople/training_packages/training_package_baiweixi_qwen25_14b/outputs/baiweixi_qwen25_14b_unsloth/`
- 训练指标：`AiPeople/training_packages/training_package_baiweixi_qwen25_14b/outputs/baiweixi_qwen25_14b_unsloth/run_metrics.json`
- 冒烟结果：`AiPeople/training_packages/training_package_baiweixi_qwen25_14b/outputs/baiweixi_qwen25_14b_unsloth/smoke_test.json`
- 60轮报告：`AiPeople/eval/world_mind_p0/qwen25_14b_base_qlora_60turn_20260828-115723/report.json`
- 用户审核：`AiPeople/eval/world_mind_p0/qwen25_14b_base_qlora_60turn_20260828-115723/user_review.json`
