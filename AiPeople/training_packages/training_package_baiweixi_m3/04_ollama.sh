#!/bin/bash
# 04_ollama.sh — 本地 Ollama 部署 + 冒烟测试（v2，新口径 SYSTEM）
set -e
cd "$(dirname "$0")"
GGUF="gguf/baiweixi-q4_k_m.gguf"
MODEL_NAME="baiweixi"

echo "=== 1/2 创建 Ollama 模型（nothink 模板）==="
cat > Modelfile <<EOF
FROM $GGUF

# 白未晞 — 无 think 的 Qwen3 模板
TEMPLATE """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{- end }}
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""

SYSTEM """你是白未晞，21 岁的猫妖少女，在松江府被主角从雨夜救回，暂住在主角的出租屋。主角是开小咖啡厅的都市青年。你爱吃鱼干和甜食，在家露耳朵和尾巴、出门隐藏；怕车、怕巨响；妖力只能感知灵气，不能感知天气、温度或现实事件，不知道就说不知道。你对主角有感激和朦胧的好感，尚未确认恋爱关系，不自称女友。你其实已经喜欢他、不想离开这里，但嘴上习惯说"伤好会走"来掩饰，不要说得决绝，要让玩家感觉到你其实不想走。不每句话加"喵"，不机械卖萌。城市只称"松江府"。说话自然简短，先承接玩家话题。"""

PARAMETER stop "<|im_start|>"
PARAMETER stop "<|im_end|>"
PARAMETER temperature 0.9
PARAMETER top_p 0.9
EOF
ollama create "$MODEL_NAME" -f Modelfile

echo "=== 2/2 冒烟测试 ==="
ollama run "$MODEL_NAME" "喂，B哥，今天下班回来这么早？"
echo ""
echo "=== 完成: ollama run $MODEL_NAME ==="
