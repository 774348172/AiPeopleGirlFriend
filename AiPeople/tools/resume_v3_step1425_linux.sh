#!/bin/bash
set -eu
cd /mnt/d/AIPeopleGit/ai-girlfriend
run=AiPeople/training_packages/training_package_baiweixi_gemma4_12b/outputs/baiweixi_v3_linux_epoch1_20260923
exec /home/apple/venvs/aip-gemma-linux-upgraded/bin/python -u AiPeople/tools/train_gemma_v2_9216.py \
  --optimized-v2 --candidate-source AiPeopleCreate/优化数据V3候选 \
  --output "$run" --stage-max-steps 2332 \
  --resume-from-checkpoint "$run/checkpoints/checkpoint-1425" --resume-interrupted \
  --model-path /opt/aip-gemma-models/Gemma-4-12B-it \
  --disable-unsloth-compile --user-authorized >> "$run/resume1425.log" 2>&1 < /dev/null
