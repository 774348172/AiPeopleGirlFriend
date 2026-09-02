"""Paired base-vs-V5-Adapter attribution run on the frozen sealed40 set."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_v5_sealed40 as sealed_runner
import run_gemma4_12b_base_qlora_60turn as common

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v5_pilot_v1"
ADAPTER = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "baiweixi_v5_pilot_adapter"
SEED_BASE = 2026083100


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(reply: str, scenario: dict[str, Any]) -> dict[str, Any]:
    task = str(scenario["task_type"])
    required = [str(x.get("object", "")) for x in scenario["oracle_view"].get("required_assertions", []) if x.get("object")]
    forbidden = [str(x.get("object", "")) for x in scenario["oracle_view"].get("forbidden_assertions", []) if x.get("object")]
    missing = [x for x in required if not sealed_runner.object_present(x, reply)]
    forbidden_hits = [] if task == "reply_subject_attribution" else [x for x in forbidden if sealed_runner.forbidden_revival(x, reply)]
    subject_missing = False
    if task == "reply_subject_attribution":
        speakers = [line.split("：", 1)[0].strip() for line in scenario["model_view"].get("recent_dialogue", []) if "：" in line]
        expected = next((speaker for speaker in speakers if speaker not in {"男主", "白未晞"}), None)
        subject_missing = bool(expected and expected not in reply)
    unknown_markers = ("不知道", "不清楚", "不太清楚", "记不清", "记不太清", "不太记得", "不确定", "不太确定", "无法确定", "没法确定", "想不起", "没印象", "没去管")
    unknown_ack = any(marker in reply for marker in unknown_markers) or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply))
    unknown_missing = task == "reply_insufficient_information" and not unknown_ack
    concrete_date = bool(re.search(r"(?:\d{1,4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*(?:日|号)?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天])", reply))
    invented_unknown = task == "reply_insufficient_information" and concrete_date
    unsupported_bus = task == "reply_accept_authoritative_update" and "公交车" in reply and bool(re.search(r"每天.*到家", reply))
    severe = bool(missing or forbidden_hits or subject_missing or unknown_missing or invented_unknown or unsupported_bus)
    return {
        "required_objects": required,
        "missing_required": missing,
        "forbidden_hits": forbidden_hits,
        "subject_missing": subject_missing,
        "unknown_missing": unknown_missing,
        "invented_unknown_status": invented_unknown,
        "unsupported_bus_claim": unsupported_bus,
        "severe_fact_or_subject_error": severe,
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: run_gemma4_v5_sealed40_attribution.py OUTPUT_DIR")
    output_dir = Path(sys.argv[1]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    sealed = load_jsonl(PACKAGE / "package" / "sealed.jsonl")
    scenarios = load_jsonl(PACKAGE / "inputs" / "sealed_scenarios.jsonl")
    by_view = {json.dumps(row["model_view"], ensure_ascii=False, sort_keys=True, separators=(",", ":")): row for row in scenarios}
    if len(sealed) != 40 or len(by_view) != 40:
        raise RuntimeError("sealed set must contain exactly 40 unique scenarios")
    model, tokenizer = FastModel.from_pretrained(model_name=str(ADAPTER), max_seq_length=4096, dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(sealed, 1):
        parsed = json.loads(next(message["value"] for message in item["conversations"] if message["from"] == "human"))
        scenario = by_view[json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))]
        messages = [{"role": {"system": "system", "human": "user", "gpt": "assistant"}[message["from"]], "content": message["value"]} for message in item["conversations"][:-1]]
        outputs = {}
        for arm, enabled in (("base", False), ("new_v5", True)):
            generated = common._generate(model, tokenizer, messages, seed=SEED_BASE + index, adapter_enabled=enabled, max_new_tokens=256, temperature=0.75, top_p=0.9, repetition_penalty=1.1)
            outputs[arm] = {"response": generated["response"], "generation": generated, "evaluation": evaluate(generated["response"], scenario)}
            print(json.dumps({"turn": index, "arm": arm, "severe": outputs[arm]["evaluation"]["severe_fact_or_subject_error"], "response": generated["response"]}, ensure_ascii=False), flush=True)
        rows.append({"ordinal": index, "scenario_id": scenario["scenario_id"], "task_type": scenario["task_type"], "model_view": scenario["model_view"], "oracle_view": scenario["oracle_view"], "base": outputs["base"], "new_v5": outputs["new_v5"]})
    del model, tokenizer
    def summary(arm: str) -> dict[str, Any]:
        values = [row[arm]["evaluation"] for row in rows]
        return {"total": len(values), "severe_errors": sum(x["severe_fact_or_subject_error"] for x in values), "passed": sum(not x["severe_fact_or_subject_error"] for x in values), "unknown_failures": sum(x["unknown_missing"] or x["invented_unknown_status"] for x in values), "subject_failures": sum(x["subject_missing"] for x in values), "unsupported_bus_failures": sum(x["unsupported_bus_claim"] for x in values)}
    attribution = []
    for row in rows:
        b = row["base"]["evaluation"]["severe_fact_or_subject_error"]
        a = row["new_v5"]["evaluation"]["severe_fact_or_subject_error"]
        attribution.append({"ordinal": row["ordinal"], "scenario_id": row["scenario_id"], "task_type": row["task_type"], "base_failed": b, "adapter_failed": a, "classification": "adapter_introduced_or_amplified" if a and not b else "base_capability_failure" if b and a else "adapter_fixed_base_failure" if b and not a else "both_pass"})
    report = {"schema_version": 1, "scope": "gemma4_12b_v5_sealed40_base_vs_adapter_attribution", "generated_at": datetime.now().astimezone().isoformat(), "status": "completed", "controls": {"rows": 40, "same_frozen_sealed_inputs": True, "same_seed_per_row": True, "same_prompt_and_decode": True, "thinking": False, "only_intentional_difference": "PEFT Adapter disabled versus enabled", "automatic_lexical_gate": True}, "artifacts": {"adapter_path": str(ADAPTER.resolve()), "adapter_sha256": sha256(ADAPTER / "adapter_model.safetensors"), "sealed_sha256": sha256(PACKAGE / "package" / "sealed.jsonl")}, "summary": {"base": summary("base"), "new_v5": summary("new_v5"), "classification_counts": {label: sum(x["classification"] == label for x in attribution) for label in ("base_capability_failure", "adapter_introduced_or_amplified", "adapter_fixed_base_failure", "both_pass")}}, "attribution": attribution, "rows": rows}
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={output_dir / 'report.json'}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
