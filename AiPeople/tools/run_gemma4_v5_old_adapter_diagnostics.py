"""Run the old V5 Adapter in the current environment and merge a fair 3-arm diagnostic."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common
from run_gemma4_v5_failure_diagnostics import SEED_BASE, build_cases, evaluate

ROOT = Path(__file__).resolve().parents[1]
OLD_ADAPTER = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "baiweixi_v5_pilot_adapter"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("targeted_report", type=Path)
    parser.add_argument("output_report", type=Path)
    parser.add_argument("--old-adapter-dir", type=Path, default=OLD_ADAPTER)
    args = parser.parse_args()
    source = json.loads(args.targeted_report.read_text(encoding="utf-8"))
    cases = build_cases()
    source_by_id = {row["id"]: row for row in source["rows"]}
    if set(source_by_id) != {case["id"] for case in cases}:
        raise RuntimeError("targeted report does not match the frozen diagnostic cases")
    old_adapter = args.old_adapter_dir.resolve()
    model, tokenizer = FastModel.from_pretrained(model_name=str(old_adapter), max_seq_length=4096, dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    rows = []
    for index, case in enumerate(cases, 1):
        human = json.dumps(case["view"], ensure_ascii=False, separators=(",", ":"))
        messages = [{"role": "system", "content": "你是白未晞。只输出角色说出口的自然回复。"}, {"role": "user", "content": human}]
        generated = common._generate(model, tokenizer, messages, seed=SEED_BASE + index, adapter_enabled=True, max_new_tokens=160, temperature=0.7, top_p=0.9, repetition_penalty=1.1)
        old = {"response": generated["response"], "generation": generated, "evaluation": evaluate(case, generated["response"])}
        current = source_by_id[case["id"]]
        base = dict(current["base"])
        targeted = dict(current["new_v5"])
        base["evaluation"] = evaluate(case, base["response"])
        targeted["evaluation"] = evaluate(case, targeted["response"])
        rows.append({"id": case["id"], "cluster": case["cluster"], "expected": current["expected"], "base": base, "old_v5": old, "targeted_v5": targeted})
        print(json.dumps({"id": case["id"], "old_v5_severe": old["evaluation"]["severe"], "response": old["response"]}, ensure_ascii=False), flush=True)
    del model, tokenizer
    summary = {}
    for cluster in ("unknown", "subject_plus_proposition", "fact_granularity"):
        selected = [row for row in rows if row["cluster"] == cluster]
        summary[cluster] = {arm: {"total": len(selected), "passed": sum(not row[arm]["evaluation"]["severe"] for row in selected), "severe": sum(row[arm]["evaluation"]["severe"] for row in selected)} for arm in ("base", "old_v5", "targeted_v5")}
    result = {"schema_version": 1, "scope": "gemma4_v5_three_arm_failure_diagnostics", "generated_at": datetime.now().astimezone().isoformat(), "controls": {"same_current_environment": True, "same_cases": True, "same_seed_per_case": True, "thinking": False}, "old_adapter_path": str(old_adapter), "old_adapter_sha256": sha256(old_adapter / "adapter_model.safetensors"), "targeted_adapter_path": source.get("adapter_path"), "targeted_adapter_sha256": source.get("adapter_sha256"), "summary": summary, "rows": rows}
    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={args.output_report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
