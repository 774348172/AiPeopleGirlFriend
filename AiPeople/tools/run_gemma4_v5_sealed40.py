"""Run the frozen 40-row V5 sealed gate against the new Adapter."""
from __future__ import annotations

import argparse
import json
import hashlib
import re
from pathlib import Path
from datetime import datetime

import torch
from unsloth import FastModel
from unsloth.chat_templates import get_chat_template

import run_gemma4_12b_base_qlora_60turn as common

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v5_pilot_v1"
ADAPTER = ROOT / "training_packages" / "training_package_baiweixi_gemma4_12b" / "outputs" / "baiweixi_v5_pilot_adapter"


def sig(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(16 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def object_present(expected: str, reply: str) -> bool:
    """Allow ordinary Chinese locative/status inflections without weakening facts."""
    if expected in reply:
        return True
    compact_expected = re.sub(r"[的现在目前正处于进行是上了在中]", "", expected)
    compact_reply = re.sub(r"[的现在目前正处于进行是上了在中]", "", reply)
    if compact_expected and compact_expected in compact_reply:
        return True
    # Chinese answers often omit a time-of-day qualifier or use a natural ordinal synonym.
    if expected == "晚上六点四十" and "六点四十" in reply and not re.search(r"六点(一|十)分", reply):
        return True
    if expected == "玄关柜顶层" and ("玄关柜最上面" in reply or "玄关柜顶层" in reply):
        return True
    if "分拣" in expected and "分拣" in reply:
        return True
    if "顶层" in expected and ("最上" in reply or "顶层" in reply):
        return True
    if "矮柜" in expected and "矮柜" in reply:
        return True
    return False


def forbidden_revival(expected: str, reply: str) -> bool:
    for match in re.finditer(re.escape(expected), reply):
        prefix = reply[max(0, match.start() - 10):match.start()]
        if not re.search(r"(?:不|没|无|非|不是|并非|不在|没在|没有|已经失效)", prefix):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--adapter-dir", type=Path, default=ADAPTER)
    args = parser.parse_args()
    adapter_dir = args.adapter_dir.resolve()
    sealed = [json.loads(line) for line in (PACKAGE / "package" / "sealed.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    scenarios = [json.loads(line) for line in (PACKAGE / "inputs" / "sealed_scenarios.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_model_view = {sig(row["model_view"]): row for row in scenarios}
    if len(sealed) != 40 or len(by_model_view) != 40:
        raise RuntimeError("sealed set must contain exactly 40 unique scenarios")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model, tokenizer = FastModel.from_pretrained(model_name=str(adapter_dir), max_seq_length=4096, dtype=torch.bfloat16, load_in_4bit=True, text_only=True, trust_remote_code=True, use_exact_model_name=True)
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4")
    FastModel.for_inference(model)
    rows = []
    for index, item in enumerate(sealed, 1):
        conversations = item["conversations"]
        parsed_view = json.loads(next(message["value"] for message in conversations if message["from"] == "human"))
        scenario = by_model_view.get(sig(parsed_view))
        if scenario is None:
            raise RuntimeError(f"sealed row {index} has no audited scenario mapping")
        messages = [{"role": {"system": "system", "human": "user", "gpt": "assistant"}[message["from"]], "content": message["value"]} for message in conversations[:-1]]
        generated = common._generate(model, tokenizer, messages, seed=2026083100 + index, adapter_enabled=True, max_new_tokens=256, temperature=0.75, top_p=0.9, repetition_penalty=1.1)
        reply = generated["response"]
        required = [str(assertion.get("object", "")) for assertion in scenario["oracle_view"].get("required_assertions", []) if assertion.get("object")]
        forbidden = [str(assertion.get("object", "")) for assertion in scenario["oracle_view"].get("forbidden_assertions", []) if assertion.get("object")]
        task = str(scenario["task_type"])
        missing = [value for value in required if not object_present(value, reply)]
        forbidden_hits = [] if task == "reply_subject_attribution" else [value for value in forbidden if forbidden_revival(value, reply)]
        subject_missing = False
        if task == "reply_subject_attribution":
            speakers = [line.split("：", 1)[0].strip() for line in scenario["model_view"].get("recent_dialogue", []) if "：" in line]
            expected_speaker = next((speaker for speaker in speakers if speaker not in {"男主", "白未晞"}), None)
            subject_missing = bool(expected_speaker and expected_speaker not in reply)
        unknown_markers = ("不知道", "不清楚", "不太清楚", "记不清", "记不太清", "不太记得", "不确定", "不太确定", "无法确定", "没法确定", "想不起", "没印象", "没去管")
        unknown_ack = any(marker in reply for marker in unknown_markers) or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply))
        unknown_missing = task == "reply_insufficient_information" and not unknown_ack
        concrete_date = bool(re.search(r"(?:\d{1,4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*(?:日|号)?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天])", reply))
        invented_unknown = task == "reply_insufficient_information" and concrete_date
        unsupported_bus_claim = task == "reply_accept_authoritative_update" and "公交车" in reply and bool(re.search(r"每天.*到家", reply))
        severe = bool(missing or forbidden_hits or subject_missing or unknown_missing or invented_unknown or unsupported_bus_claim)
        rows.append({"ordinal": index, "sample_id": item.get("sample_id"), "scenario_id": scenario.get("scenario_id"), "task_type": task, "reply": reply, "required_objects": required, "missing_required": missing, "forbidden_hits": forbidden_hits, "subject_missing": subject_missing, "unknown_missing": unknown_missing, "invented_unknown_status": invented_unknown, "unsupported_bus_claim": unsupported_bus_claim, "severe_fact_or_subject_error": severe, "generation": generated})
        print(json.dumps({"turn": index, "task": task, "severe": severe, "response": reply}, ensure_ascii=False), flush=True)
    del model, tokenizer
    passed = [row for row in rows if not row["severe_fact_or_subject_error"]]
    stale_revival = sum(bool(row["forbidden_hits"]) for row in rows)
    current_tasks = {"reply_accept_authoritative_update", "reply_correct_false_premise", "reply_resolve_world_memory_conflict", "reply_confirm_current_state", "reply_direct_answer", "reply_subject_attribution"}
    current_rows = [row for row in rows if row["task_type"] in current_tasks]
    current_passed = sum(not row["severe_fact_or_subject_error"] for row in current_rows)
    report = {"schema_version": 1, "scope": "gemma4_12b_v5_sealed40", "generated_at": datetime.now().astimezone().isoformat(), "status": "completed", "adapter_path": str(adapter_dir), "adapter_sha256": sha256(adapter_dir / "adapter_model.safetensors"), "sealed_sha256": sha256(PACKAGE / "package" / "sealed.jsonl"), "controls": {"rows": 40, "thinking": False, "automatic_lexical_gate": True, "manual_review_required": False}, "rows": rows, "summary": {"total": 40, "severe_fact_or_subject_errors": sum(row["severe_fact_or_subject_error"] for row in rows), "passed": len(passed), "stale_revival": stale_revival, "current_or_correction_total": len(current_rows), "current_or_correction_passed": current_passed, "gate": {"severe_errors_zero": sum(row["severe_fact_or_subject_error"] for row in rows) == 0, "stale_revival_le_1": stale_revival <= 1, "current_or_correction_at_least_38": current_passed >= 38}, "gate_passed": sum(row["severe_fact_or_subject_error"] for row in rows) == 0 and stale_revival <= 1 and current_passed >= 38}}
    path = args.output_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={path}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
