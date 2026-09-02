"""Continue the approved V5 Adapter with a low-strength response-repair RPO pass."""
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


def _isolate_optional_dependencies() -> None:
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


_isolate_optional_dependencies()

import torch  # noqa: E402
from datasets import Dataset  # noqa: E402
from unsloth import FastModel, PatchDPOTrainer  # noqa: E402
from unsloth.chat_templates import get_chat_template  # noqa: E402
from transformers.processing_utils import ProcessorMixin  # noqa: E402

PatchDPOTrainer()

from trl import DPOConfig, DPOTrainer  # noqa: E402


CREATE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CREATE_ROOT.parent
DATA_ROOT = CREATE_ROOT / "训练数据" / "baiweixi_v5_response_repair_preference_v1"
TRAIN_PATH = DATA_ROOT / "train.jsonl"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
START_ADAPTER = (
    REPO_ROOT
    / "AiPeople"
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v5_targeted_adapter"
)
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "AiPeople"
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b"
    / "outputs"
    / "baiweixi_v5_response_repair_rpo_probe"
)
SEED = 20260902
MAX_LENGTH = 1536
LEARNING_RATE = 1e-6
RPO_ALPHA = 0.2


class TextOnlyProcessor(ProcessorMixin):
    def __init__(self, tokenizer: Any) -> None:
        object.__setattr__(self, "tokenizer", tokenizer)
        super().__init__(tokenizer=tokenizer)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "tokenizer"), name)

    def __call__(self, *, images: Any = None, text: str, add_special_tokens: bool = False, **kwargs: Any) -> dict[str, Any]:
        if images is not None:
            raise RuntimeError("V5 response repair is text-only")
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
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def smoke_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = (
        "stale_action_completion", "invalid_plan_request", "direct_decision",
        "multi_request_resolution", "retention_known", "retention_unknown", "retention_persona",
    )
    selected = []
    for cluster in wanted:
        selected.append(next(row for row in rows if row["cluster"] == cluster))
    return selected


def token_audit(tokenizer: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals: list[int] = []
    prompts: list[int] = []
    completions: list[int] = []
    by_cluster: dict[str, list[int]] = {}
    for row in rows:
        prompt_text = tokenizer.apply_chat_template(row["prompt"], tokenize=False, add_generation_prompt=True)
        prompt_ids = tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        prompts.append(len(prompt_ids))
        for field in ("chosen", "rejected"):
            full_text = tokenizer.apply_chat_template(row["prompt"] + row[field], tokenize=False, add_generation_prompt=False)
            full_ids = tokenizer(full_text, add_special_tokens=False)["input_ids"]
            completion = len(full_ids) - len(prompt_ids)
            totals.append(len(full_ids))
            completions.append(completion)
            by_cluster.setdefault(row["cluster"], []).append(completion)
    if max(totals) > MAX_LENGTH:
        raise RuntimeError(f"preference example exceeds {MAX_LENGTH} tokens: {max(totals)}")
    if min(completions) <= 0:
        raise RuntimeError("preference completion contains no supervised tokens")
    return {
        "rows": len(rows),
        "min_total_tokens": min(totals),
        "max_total_tokens": max(totals),
        "max_prompt_tokens": max(prompts),
        "min_completion_tokens": min(completions),
        "max_completion_tokens": max(completions),
        "completion_tokens_by_cluster": {
            key: {"min": min(values), "max": max(values)} for key, values in sorted(by_cluster.items())
        },
        "truncation_required": False,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-steps", type=int, default=16)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["files"]["train.jsonl"] != sha256(TRAIN_PATH):
        raise RuntimeError("response-repair train hash differs from manifest")
    start_adapter_path = START_ADAPTER / "adapter_model.safetensors"
    if manifest["source"]["starting_adapter_sha256"] != sha256(start_adapter_path):
        raise RuntimeError("starting V5 Adapter hash differs from manifest")
    rows = load_jsonl(TRAIN_PATH)
    expected_clusters = {
        "stale_action_completion", "invalid_plan_request", "direct_decision",
        "multi_request_resolution", "retention_known", "retention_unknown", "retention_persona",
    }
    if len(rows) != 132 or {row["cluster"] for row in rows} != expected_clusters:
        raise RuntimeError("unexpected response-repair dataset shape")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required")

    torch.manual_seed(SEED)
    torch.cuda.manual_seed_all(SEED)
    model, tokenizer = FastModel.from_pretrained(
        model_name=str(START_ADAPTER),
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
    if not getattr(model, "peft_config", None) or "default" not in model.peft_config:
        raise RuntimeError("starting V5 PEFT Adapter was not loaded")
    model.load_adapter(str(START_ADAPTER), adapter_name="reference", is_trainable=False)
    model.set_adapter("default")
    trainable_names = [name for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not trainable_names or any(".reference." in name for name in trainable_names):
        raise RuntimeError("reference Adapter is trainable or repair Adapter has no trainable parameters")

    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    processor = TextOnlyProcessor(tokenizer)
    audit = token_audit(tokenizer, rows)
    dataset_rows = smoke_rows(rows) if args.smoke else rows
    dataset = Dataset.from_list(dataset_rows)
    max_steps = 1 if args.smoke else args.max_steps
    gradient_accumulation_steps = 2 if args.smoke else 4
    training_args = DPOConfig(
        output_dir=str(output_dir),
        per_device_train_batch_size=1,
        gradient_accumulation_steps=gradient_accumulation_steps,
        max_steps=max_steps,
        learning_rate=LEARNING_RATE,
        lr_scheduler_type="cosine",
        warmup_steps=0 if args.smoke else 2,
        weight_decay=0.0,
        max_grad_norm=1.0,
        bf16=True,
        fp16=False,
        gradient_checkpointing=True,
        optim="adamw_bnb_8bit",
        logging_steps=1,
        save_strategy="no",
        report_to="none",
        seed=SEED,
        data_seed=SEED,
        dataset_num_proc=1,
        max_length=MAX_LENGTH,
        max_prompt_length=1408,
        max_completion_length=96,
        beta=0.1,
        rpo_alpha=RPO_ALPHA,
        model_adapter_name="default",
        ref_adapter_name="reference",
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
    model.set_adapter("default")
    model.save_pretrained(str(output_dir), selected_adapters=["default"])
    tokenizer.save_pretrained(str(output_dir))
    trainer.save_state()

    adapter_path = output_dir / "adapter_model.safetensors"
    if not adapter_path.is_file():
        raise RuntimeError("trained repair Adapter was not saved at the output root")
    metrics = dict(result.metrics)
    metrics.update({
        "status": "smoke_completed" if args.smoke else "probe_completed",
        "elapsed_seconds": elapsed,
        "peak_gpu_memory_mib": torch.cuda.max_memory_allocated() / 1024**2,
        "starting_adapter": str(START_ADAPTER.resolve()),
        "starting_adapter_sha256": sha256(start_adapter_path),
        "starting_adapter_loaded": True,
        "frozen_reference_adapter_loaded": True,
        "clean_base_training": False,
        "training_method": "continued RPO (DPO + chosen NLL)",
        "training_rows": len(dataset),
        "full_dataset_rows": len(rows),
        "max_steps": max_steps,
        "effective_epochs": max_steps * gradient_accumulation_steps / len(dataset),
        "learning_rate": LEARNING_RATE,
        "beta": 0.1,
        "rpo_alpha": RPO_ALPHA,
        "trainable_parameters": sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad),
        "dataset_sha256": sha256(TRAIN_PATH),
        "manifest_sha256": sha256(MANIFEST_PATH),
        "adapter_sha256": sha256(adapter_path),
        "token_audit": audit,
    })
    (output_dir / "run_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
