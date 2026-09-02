"""Train the medium-scale counterfactual DPO Adapter from clean Gemma 4."""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import sys
import time
import types
from pathlib import Path
from typing import Any

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _isolate_missing_mergekit() -> None:
    try:
        available = importlib.util.find_spec("mergekit") is not None
    except (ImportError, ValueError):
        available = False
    if available:
        return
    package = types.ModuleType("mergekit")
    package.__spec__ = importlib.machinery.ModuleSpec("mergekit", loader=None, is_package=True)
    package.__path__ = []  # type: ignore[attr-defined]
    config = types.ModuleType("mergekit.config")
    config.__spec__ = importlib.machinery.ModuleSpec("mergekit.config", loader=None)
    config.MergeConfiguration = type("MergeConfiguration", (), {})
    merge = types.ModuleType("mergekit.merge")
    merge.__spec__ = importlib.machinery.ModuleSpec("mergekit.merge", loader=None)
    merge.MergeOptions = type("MergeOptions", (), {})
    merge.run_merge = lambda *args, **kwargs: None
    sys.modules.update({"mergekit": package, "mergekit.config": config, "mergekit.merge": merge})


_isolate_missing_mergekit()

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from unsloth import FastModel, PatchDPOTrainer  # noqa: E402
from unsloth.chat_templates import get_chat_template  # noqa: E402
from transformers.processing_utils import ProcessorMixin  # noqa: E402

PatchDPOTrainer()

from trl import DPOConfig, DPOTrainer  # noqa: E402


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
DATA_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_counterfactual_preference_medium_v2"
TRAIN_PATH = DATA_ROOT / "train.jsonl"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
MODEL_PATH = REPO_ROOT / "AiPeople" / "training_packages" / "models" / "Gemma-4-12B-it"
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "AiPeople"
    / "training_packages"
    / "training_package_baiweixi_gemma4_preference_medium"
    / "outputs"
    / "counterfactual_dpo_medium_v2"
)
SEED = 20260901
MAX_LENGTH = 896
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


class TextOnlyProcessor(ProcessorMixin):
    def __init__(self, tokenizer: Any) -> None:
        object.__setattr__(self, "tokenizer", tokenizer)
        super().__init__(tokenizer=tokenizer)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "tokenizer"), name)

    def __call__(self, *, images: Any = None, text: str, add_special_tokens: bool = False, **kwargs: Any) -> dict[str, Any]:
        if images is not None:
            raise RuntimeError("medium counterfactual preference run is text-only")
        encoded = self.tokenizer(text, add_special_tokens=add_special_tokens, **kwargs)
        return {key: [value] for key, value in encoded.items()}

    def apply_chat_template(self, *args: Any, **kwargs: Any) -> Any:
        return self.tokenizer.apply_chat_template(*args, **kwargs)

    def save_pretrained(self, path: str) -> Any:
        return self.tokenizer.save_pretrained(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def token_audit(tokenizer: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_lengths: list[int] = []
    prompt_lengths: list[int] = []
    completion_lengths: list[int] = []
    for row in rows:
        prompt_text = tokenizer.apply_chat_template(row["prompt"], tokenize=False, add_generation_prompt=True)
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        prompt_lengths.append(len(prompt_ids))
        for field in ("chosen", "rejected"):
            full_text = tokenizer.apply_chat_template(row["prompt"] + row[field], tokenize=False, add_generation_prompt=False)
            full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
            total_lengths.append(len(full_ids))
            completion_lengths.append(len(full_ids) - len(prompt_ids))
    if max(total_lengths) > MAX_LENGTH:
        raise RuntimeError(f"preference example exceeds {MAX_LENGTH} tokens: {max(total_lengths)}")
    if min(completion_lengths) <= 0:
        raise RuntimeError("one or more preference completions have no supervised tokens")
    return {
        "rows": len(rows),
        "min_total_tokens": min(total_lengths),
        "max_total_tokens": max(total_lengths),
        "max_prompt_tokens": max(prompt_lengths),
        "min_completion_tokens": min(completion_lengths),
        "max_completion_tokens": max(completion_lengths),
        "truncation_required": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-steps", type=int, default=120)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["files"]["train.jsonl"] != sha256(TRAIN_PATH):
        raise RuntimeError("medium preference train hash differs from manifest")
    rows = load_jsonl(TRAIN_PATH)
    if len(rows) != 240 or {row["condition"] for row in rows} != {"known", "unknown"}:
        raise RuntimeError("unexpected medium preference dataset shape")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")

    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(MODEL_PATH),
        max_seq_length=MAX_LENGTH,
        dtype=torch.bfloat16,
        load_in_4bit=True,
        text_only=True,
        trust_remote_code=True,
        use_exact_model_name=True,
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
        attn_implementation="eager",
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    processor = TextOnlyProcessor(tokenizer)
    audit = token_audit(tokenizer, rows)
    model = FastModel.get_peft_model(
        model,
        r=8,
        target_modules=TARGET_MODULES,
        lora_alpha=16,
        lora_dropout=0.0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=SEED,
    )
    dataset = Dataset.from_list(rows[:8] if args.smoke else rows)
    max_steps = 1 if args.smoke else args.max_steps
    training_args = DPOConfig(
        output_dir=str(output_dir),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=2 if args.smoke else 4,
        max_steps=max_steps,
        learning_rate=1e-5,
        lr_scheduler_type="cosine",
        warmup_ratio=0.1,
        weight_decay=0.0,
        max_grad_norm=1.0,
        bf16=True,
        fp16=False,
        gradient_checkpointing=True,
        optim="adamw_bnb_8bit",
        logging_steps=1,
        save_strategy="no" if args.smoke else "steps",
        save_steps=40,
        save_total_limit=2,
        report_to="none",
        seed=SEED,
        data_seed=SEED,
        dataset_num_proc=1,
        max_length=MAX_LENGTH,
        max_prompt_length=768,
        max_completion_length=96,
        beta=0.1,
        precompute_ref_log_probs=True,
        precompute_ref_batch_size=1,
        padding_free=False,
        remove_unused_columns=True,
    )
    trainer = DPOTrainer(
        model=model,
        ref_model=None,
        args=training_args,
        train_dataset=dataset,
        processing_class=processor,
    )
    (output_dir / "token_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    torch.cuda.reset_peak_memory_stats()
    started = time.time()
    result = trainer.train()
    elapsed = time.time() - started
    trainer.save_model(str(output_dir))
    tokenizer.save_pretrained(str(output_dir))
    trainer.save_state()
    adapter_path = output_dir / "adapter_model.safetensors"
    metrics = dict(result.metrics)
    metrics.update(
        {
            "status": "smoke_completed" if args.smoke else "completed",
            "elapsed_seconds": elapsed,
            "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
            "base_model": str(MODEL_PATH.resolve()),
            "base_model_is_clean": True,
            "legacy_sft_adapter_loaded": False,
            "training_method": "DPO",
            "training_rows": len(dataset),
            "max_steps": max_steps,
            "effective_epochs": max_steps * training_args.gradient_accumulation_steps / len(dataset),
            "learning_rate": 1e-5,
            "beta": 0.1,
            "lora_rank": 8,
            "lora_alpha": 16,
            "target_modules": TARGET_MODULES,
            "dataset_sha256": sha256(TRAIN_PATH),
            "manifest_sha256": sha256(MANIFEST_PATH),
            "adapter_sha256": sha256(adapter_path),
            "token_audit": audit,
        }
    )
    (output_dir / "run_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
