"""Frozen regression comparison; never export these cases as training data."""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import argparse
import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path

from run_v5_small_three_way import ROOT, _load_arm, _generate, _release, _prompt
from build_v5_pilot_dataset import _load_baiweixi_production_contract
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template
import torch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    package = ROOT.parent / "AiPeopleCreate/训练数据/baiweixi_frontend_full_20260908"
    candidate = args.candidate.resolve()
    frozen = package / "regression_review12.json"
    original = json.loads(frozen.read_text(encoding="utf-8"))
    metrics = json.loads((candidate / "run_metrics.json").read_text(encoding="utf-8"))
    if sha(candidate / "adapter_model.safetensors") != metrics["adapter_sha256"]:
        raise ValueError("Candidate hash mismatch")
    manifest = ROOT / "local_runtime/gemma4_nf4_runtime_manifest.json"
    report = {"schema_version": "aip.frontend_regression12_review.v1", "status": "running",
        "generated_at": datetime.now().astimezone().isoformat(), "source_sha256": sha(frozen),
        "human_adjudication_is_authoritative": True, "automatic_quality_judgment": False,
        "controls": {**original["controls"], "old_adapter_comparator": "current production full mixed, not V5 targeted",
                     "context_note": "same frozen model_view with current renderer; date formatting updated since Sept 3",
                     "training_data": False},
        "arms": {"base": {"label": "裸基座 · Gemma NF4"},
                 "old_adapter": {"label": "当前生产 · 旧全量混合 Adapter", "manifest": str(manifest)},
                 "new_adapter": {"label": "新训练 · 前台全量 Adapter", "path": str(candidate), "sha256": metrics["adapter_sha256"]}},
        "cases": []}
    anchor, _ = _load_baiweixi_production_contract()
    report["controls"]["system_anchor"] = anchor
    report["controls"]["system_anchor_sha256"] = hashlib.sha256(anchor.encode("utf-8")).hexdigest()
    report["controls"]["production_anchor_loaded_at_run_time"] = True
    for item in original["cases"]:
        case = {k: copy.deepcopy(item[k]) for k in ("ordinal", "sample_id", "task_type", "question", "model_view", "expected_reply")}
        messages = _prompt({"model_view": case["model_view"]}, anchor)
        case.update(messages=messages, prompt_sha256=hashlib.sha256(json.dumps(messages, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    arms={}, human_review={key: "" for key in report["arms"]})
        report["cases"].append(case)

    def save():
        temp = args.output / "report.tmp"
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(args.output / "report.json")
    save()
    for arm in report["arms"]:
        print("LOADING " + arm, flush=True)
        if arm == "new_adapter":
            model, tokenizer = FastModel.from_pretrained(model_name=str(candidate), max_seq_length=4096,
                dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True,
                use_exact_model_name=True, attn_implementation="eager")
            if not getattr(model, "peft_config", None):
                raise ValueError("Candidate Adapter not loaded")
            tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
            FastModel.for_inference(model)
        else:
            model, tokenizer, asset = _load_arm(arm, manifest if arm == "old_adapter" else None)
            report["arms"][arm]["adapter_loaded"] = asset is not None
        try:
            for index, case in enumerate(report["cases"]):
                case["arms"][arm] = _generate(model, tokenizer, case["messages"], 20260902 + index)
                save()
                print(json.dumps({"arm": arm, "turn": index+1, "reply": case["arms"][arm]["response"]}, ensure_ascii=False), flush=True)
        finally:
            _release(model, tokenizer)
    report["status"] = "awaiting_human_review"
    save()
    template = Path(__file__).with_name("frontend_regression12_review.html").read_text(encoding="utf-8")
    (args.output / "review.html").write_text(template.replace("__REPORT__", json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")), encoding="utf-8")
    print("COMPLETED 36/36", flush=True)


if __name__ == "__main__":
    main()
