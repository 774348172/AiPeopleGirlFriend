---
library_name: peft
license: other
base_model: D:/AIPeopleGit/ai-girlfriend/AiPeople/training_packages/models/Qwen3.5-4B
tags:
- base_model:adapter:D:/AIPeopleGit/ai-girlfriend/AiPeople/training_packages/models/Qwen3.5-4B
- llama-factory
- lora
- transformers
pipeline_tag: text-generation
model-index:
- name: baiweixi_4b
  results: []
---

<!-- This model card has been generated automatically according to the information the Trainer had access to. You
should probably proofread and complete it, then remove this comment. -->

# baiweixi_4b

This model is a fine-tuned version of [D:/AIPeopleGit/ai-girlfriend/AiPeople/training_packages/models/Qwen3.5-4B](https://huggingface.co/D:/AIPeopleGit/ai-girlfriend/AiPeople/training_packages/models/Qwen3.5-4B) on the baiweixi_ready dataset.

## Model description

More information needed

## Intended uses & limitations

More information needed

## Training and evaluation data

More information needed

## Training procedure

### Training hyperparameters

The following hyperparameters were used during training:
- learning_rate: 0.0001
- train_batch_size: 1
- eval_batch_size: 8
- seed: 42
- gradient_accumulation_steps: 4
- total_train_batch_size: 4
- optimizer: Use OptimizerNames.ADAMW_BNB with betas=(0.9,0.999) and epsilon=1e-08 and optimizer_args=No additional optimizer arguments
- lr_scheduler_type: cosine
- lr_scheduler_warmup_steps: 0.05
- num_epochs: 1.0

### Training results



### Framework versions

- PEFT 0.18.1
- Transformers 5.6.0
- Pytorch 2.5.1+cu121
- Datasets 4.0.0
- Tokenizers 0.22.2