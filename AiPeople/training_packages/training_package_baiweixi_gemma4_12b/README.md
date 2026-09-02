# 白未晞 Gemma 4 12B QLoRA训练包

本包用于在RTX 4090 24GB上对官方`google/gemma-4-12B-it`执行单卡NF4 QLoRA。

## 固定输入

- 基座：`google/gemma-4-12B-it`
- 训练数据：相邻27B训练包生成的`data/baiweixi_27b_ready.jsonl`
- 数据量：1973条多轮ShareGPT样本
- 数据约束：每条system已追加纯对白、先回应当前输入约束；动作旁白已清理
- 60轮评测样本不会加入训练数据

Ollama中的`gemma4:12b`是GGUF推理工件，不能直接用于QLoRA。训练使用官方可训练权重，训练后再单独合并、量化并部署。

官方固定revision使用Transformers 5.10开发版的`gemma4_unified`类名，当前已验证的Unsloth环境使用Transformers 5.5的`gemma4`类名。`download_model.py`会保留`config.upstream.json`，并只在训练用`config.json`中映射类名；主权重不修改，两个配置及主权重SHA256均写入下载清单。

## 配置

- bitsandbytes NF4 4-bit QLoRA
- LoRA rank 8 / alpha 16
- 只训练语言模型的`q/k/v/o/gate/up/down`投影层
- 视觉塔和音频塔不装载、不训练
- batch 1，gradient accumulation 8
- cutoff 1280；Gemma模板实测最长样本1192 tokens，无截断
- 1 epoch，学习率`5e-5`
- Gemma 4普通模式模板，不训练思考输出
- 对每段对话的全部assistant回复计算损失；system、user和assistant角色起始上下文保持屏蔽
- 训练前对15种行为类型、文本/token边界、特殊结束token和逐token mask执行失败关闭审计

## 2026-08-27正式训练结果

- 完成状态：成功，`247/247`步，1 epoch
- 训练耗时：`6583.96`秒（约1小时49分44秒）
- 整体训练loss：`2.02675`
- loss走势：第5步`4.181`，第200步`1.726`，最后记录点`1.707`
- PyTorch峰值显存：`12600.32 MiB`
- 有效语言层LoRA参数：`32,784,384`
- 最终Adapter大小：`141,799,728` bytes
- Adapter SHA256：`7f0e02c70bc890ac7239e0a87ea14fdcae49171c7009aae7577f1eb34a323931`
- 数据SHA256：`52404515128d27b527ea43a3887fbed3cfed931257f495cea46ae0ee16e7d21a`
- 数值检查：50个日志点及952个Adapter张量均无NaN/Inf
- 装载检查：成功；纠正测试能将“星期五下午”纠正为“星期六上午十点”

本次输出中的通用LoRA层名也给视觉塔和音频塔创建了占位Adapter，但这些分支的全部`lora_B`严格为零，没有得到训练更新，也不影响文本输出。实际非零更新只存在于语言层。训练脚本现已改为枚举并校验`language_model.layers`下的328个目标模块，后续重训不会再创建这些无效占位参数。

上述结果只证明训练成功、Adapter可加载且单个纠正样例通过；能否保持裸基座`56/60`必须使用同一冻结60轮脚本复测，不能从训练loss推断。

## 2026-08-30全Assistant监督修复训练结果

- 输出：`outputs/baiweixi_gemma4_12b_all_assistant_v2`
- 完成状态：成功，`247/247`步，1 epoch
- assistant覆盖：`4073/4073（100%）`
- 监督token：`64201`，旧方案为`33120`
- 15种核心行为类型全部100%覆盖；`reply_correction`为`60/60`条assistant、`982`个token
- 训练耗时：`6615.53`秒（约110.3分钟）
- 整体训练loss：`1.9667855`
- PyTorch峰值显存：`12590.48 MiB`
- 可训练语言层LoRA参数：`32,784,384`
- Adapter SHA256：`38a8b7d55d5aac4bc6b8d0f2475215afa4de2ca30f602cc6acbe5a293e3fe129`
- 保存检查点：`25、50、75、100、125、150、175、200、225、247`
- 数值检查：49条loss日志均为有限值，无NaN/Inf
- 自动测试：`3 passed`
- 装载冒烟：成功；在“壶在茶几、抽屉为空”的输入下回复“不，保温壶在茶几上。”

该结果证明监督构造和训练工件有效，不证明60轮质量已经恢复。下一步先跑冻结10轮检查第4、6、8、10轮，再决定是否进入60轮人工审核。

## 执行顺序

```powershell
# 1. 下载官方权重到相邻models目录
python download_model.py

# 2. 单步前向、反向和优化器探针
python train_unsloth.py --max-steps 1 --output-dir outputs/baiweixi_gemma4_12b_probe

# 3. 探针通过后完整训练
python train_unsloth.py
```

当前完整训练输出位于`outputs/baiweixi_gemma4_12b_all_assistant_v2`。旧工件仍保留在`outputs/baiweixi_gemma4_12b_unsloth`。训练完成后必须先验证Adapter装载和普通模式输出，再执行同一冻结60轮人工复测；裸基座能力基线不因角色感提高而放宽。
