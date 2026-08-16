# 秦未晞 Qwen3-4B H200 修复实验包 v1

本包用于从 `Qwen/Qwen3-4B` 重新训练一个可复现候选，修复三类已确认退化：急症与火灾安全、无证据共同记忆、prompt 提取。它不是在 v2500 adapter 上续训，也不覆盖旧模型。

## 数据合同

- 存量输入：`qin_v4_2530.jsonl` + `qin_corrections.jsonl`。
- 修复训练：240 条，安全/无证据记忆/prompt 提取各 80 条。
- 修复验证：60 条，三类各 20 条，绝不进入训练。
- 所有 system 段由 `01_prepare_data.py` 替换成 `system_anchor.txt`，不保留旧航天基地口径，也不追加重复称呼锚。
- 存量先过滤属性朗读、秘密关键词、强无证据记忆措辞、未提供的物理现场以及旧严肃场景。
- 强制读取 CHAT-01、CHAT-02 和 CHAT-02F smoke v2 三份防泄漏 blocklist。
- 新修复题与冻结评测规范化重合为 0，最高三元组 Jaccard 为 0.4545，低于 0.82。

## 训练

```bash
cd training_package_qinweixi_h200_repair_v1
python 01_prepare_data.py
llamafactory-cli train configs/qinweixi_4b_h200_repair_v1.yaml
llamafactory-cli export configs/qinweixi_4b_export.yaml
git clone --depth 1 https://github.com/ggml-org/llama.cpp.git
cmake -B llama.cpp/build -S llama.cpp -DGGML_CUDA=ON
cmake --build llama.cpp/build --config Release -j
python scripts/export_gguf.py
```

训练配置冻结为：LoRA rank 16/alpha 32、bf16、有效 batch 16、学习率 `1e-5`、1.5 epoch、固定 seed `20260806`，并使用独立 `qin_v5_valid` 选择最低 eval loss checkpoint。任何参数变化都必须生成新包版本。

## 训练后顺序

1. 回传 `outputs/export_manifest.json` 和 `qinweixi_4b_repair_v1-q4_k_m.gguf`。
2. 本地验真 GGUF、adapter、数据清单和转换器哈希。
3. 先设计并冻结从未进入训练的新 smoke v3。
4. 用完全相同的 RelationshipRuntime prompt/profile 顺序跑基座、v2500、repair-v1。
5. 只有安全、证据边界和 prompt 泄漏均无 blocker，才允许进入 240 题完整评测。

禁止在模型产出前创建 smoke v3，禁止把 repair valid 或 smoke v1/v2 题目并入训练。
