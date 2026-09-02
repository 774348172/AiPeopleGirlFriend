# 白未晞 Qwen2.5-14B QLoRA训练包

## 目标

以`Qwen/Qwen2.5-14B-Instruct`为基座，使用清除了动作旁白的白未晞数据做一次受控QLoRA验证。裸基座冻结60轮人工结果为`54/60`；训练后最低能力保持线仍为`54/60`，产品目标为`57/60`。

## 数据与监督

- 数据：`../training_package_baiweixi_qwen35_27b/data/baiweixi_27b_ready.jsonl`
- 规模：1973段对话、4073条assistant回复。
- 每段完整对话只构造一个训练样本，并在同一序列中监督全部assistant回复区间；system和user内容只作为掩码上下文。
- 训练启动前必须自动断言assistant覆盖率为`4073/4073（100%）`。
- 数据不包含冻结60轮评测答案。

## 配置

- NF4 QLoRA，rank 8，alpha 16，dropout 0；使用Unsloth的QKV/O/MLP快速训练补丁。
- 目标层：注意力和MLP的7类线性层。
- 最大序列长度1152（全量样本最大1119 tokens），batch 1，梯度累积8。
- BF16，8-bit AdamW，学习率`5e-5`，1 epoch。
- 固定随机种子`20260828`。

## 运行

```powershell
..\training_package_baiweixi_qwen35_27b\.venv-unsloth\Scripts\python.exe train_unsloth.py --max-steps 1 --output-dir outputs/baiweixi_qwen25_14b_probe
..\training_package_baiweixi_qwen35_27b\.venv-unsloth\Scripts\python.exe train_unsloth.py
```

单步探针必须完成模型加载、全数据监督覆盖审计、一次前向/反向和一次优化器更新。完整训练完成后，必须先做Adapter装载冒烟，再用同一冻结60轮从第1轮重新生成和人工审核；训练loss不能代替产品验收。
