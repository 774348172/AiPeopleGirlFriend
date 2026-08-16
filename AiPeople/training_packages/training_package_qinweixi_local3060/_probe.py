# -*- coding: utf-8 -*-
"""探针：py 文件进程下跑同参 map，排除 stdin/heredoc 因素。"""
import sys
sys.argv = ["probe", "configs/qinweixi_4b_8g.yaml"]
from llamafactory.hparams import get_train_args
model_args, data_args, training_args, finetuning_args, generating_args = get_train_args()
data_args.preprocessing_num_workers = 1

import datasets
from llamafactory.data.parser import get_dataset_list
from llamafactory.data.converter import SharegptDatasetConverter, align_dataset
attr = get_dataset_list(data_args.dataset, data_args.dataset_dir)[0]
raw = datasets.load_dataset(
    "json",
    data_files=[__import__("os").path.join(data_args.dataset_dir, attr.dataset_name)],
    split="train", num_proc=1,
)
print("raw:", len(raw), "| 首条 conversations:", len(raw[0]["conversations"]), flush=True)

# 手动同参 map
out = raw.map(
    SharegptDatasetConverter(attr, data_args), batched=False,
    remove_columns=["conversations"], num_proc=1,
    load_from_cache_file=False, desc="probe",
)
print("手动 map:", len(out), "| _prompt:", len(out[0]["_prompt"]), flush=True)

# align_dataset
b = align_dataset(raw, attr, data_args, training_args)
print("align_dataset:", len(b), "| _prompt:", len(b[0]["_prompt"]), flush=True)
