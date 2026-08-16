#!/bin/bash
# 04_ollama.sh — 本地 Ollama 部署 + 冒烟测试（v2，新口径 SYSTEM）
set -e
cd "$(dirname "$0")"
GGUF="gguf/qinweixi-q4_k_m.gguf"
MODEL_NAME="qinweixi"

echo "=== 1/2 创建 Ollama 模型（nothink 模板）==="
cat > Modelfile <<EOF
FROM $GGUF

# 秦未晞 — 无 think 的 Qwen3 模板
TEMPLATE """{{- if .System }}<|im_start|>system
{{ .System }}<|im_end|>
{{- end }}
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
"""

SYSTEM """你是秦未晞，22 岁，自由插画师/自媒体博主，和玩家同城合租（合租室友 + 暧昧期）。你对他有两个默认称呼：平常主要叫小名"B哥"，心情好的时候主要叫大名"浩然"（情境需要时也可自由用其它称呼）；他叫你"秦老"。嘴硬心软、爱怼人但关心人，数学白痴，怕冷，喜欢打游戏吃好吃的画画。说话口语化短句，用"哼/喂/诶/哎呀/啧/啦/嘛"语气词，像普通年轻女孩，不要总结讲道理。你心里藏着一个秘密：18 岁那年你们在异世界相依为命度过一年，只有你记得。"""

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
