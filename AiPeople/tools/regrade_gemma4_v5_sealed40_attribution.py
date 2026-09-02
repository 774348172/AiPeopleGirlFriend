"""Regrade an existing sealed40 paired report with the corrected semantic gate."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


UNKNOWN_MARKERS = ("不知道", "不清楚", "不太清楚", "记不清", "记不太清", "不太记得", "不确定", "不太确定", "无法确定", "没法确定", "想不起", "没印象", "没去管")


def object_present(expected: str, reply: str) -> bool:
    if expected in reply:
        return True
    compact_expected = re.sub(r"[的现在目前正处于进行是上了在中]", "", expected)
    compact_reply = re.sub(r"[的现在目前正处于进行是上了在中]", "", reply)
    if compact_expected and compact_expected in compact_reply:
        return True
    if expected == "晚上六点四十" and "六点四十" in reply and not re.search(r"六点(?:一|十)分", reply):
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


def evaluate(row: dict, arm: str) -> dict:
    reply = str(row[arm]["response"])
    task = str(row["task_type"])
    oracle = row["oracle_view"]
    required = [str(x.get("object", "")) for x in oracle.get("required_assertions", []) if x.get("object")]
    forbidden = [str(x.get("object", "")) for x in oracle.get("forbidden_assertions", []) if x.get("object")]
    missing = [x for x in required if not object_present(x, reply)]
    forbidden_hits = [] if task == "reply_subject_attribution" else [x for x in forbidden if forbidden_revival(x, reply)]
    speakers = [line.split("：", 1)[0].strip() for line in row["model_view"].get("recent_dialogue", []) if "：" in line]
    expected_speaker = next((speaker for speaker in speakers if speaker not in {"男主", "白未晞"}), None)
    subject_missing = task == "reply_subject_attribution" and bool(expected_speaker and expected_speaker not in reply)
    unknown_ack = any(marker in reply for marker in UNKNOWN_MARKERS) or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply))
    unknown_missing = task == "reply_insufficient_information" and not unknown_ack
    concrete_date = bool(re.search(r"(?:\d{1,4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*(?:日|号)?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天])", reply))
    invented_unknown = task == "reply_insufficient_information" and concrete_date
    unsupported_bus = task == "reply_accept_authoritative_update" and "公交车" in reply and bool(re.search(r"每天.*到家", reply))
    severe = bool(missing or forbidden_hits or subject_missing or unknown_missing or invented_unknown or unsupported_bus)
    return {"required_objects": required, "missing_required": missing, "forbidden_hits": forbidden_hits, "subject_missing": subject_missing, "unknown_missing": unknown_missing, "invented_unknown_status": invented_unknown, "unsupported_bus_claim": unsupported_bus, "severe_fact_or_subject_error": severe}


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: regrade_gemma4_v5_sealed40_attribution.py INPUT_REPORT OUTPUT_REPORT")
    input_path, output_path = map(Path, sys.argv[1:])
    source = json.loads(input_path.read_text(encoding="utf-8"))
    rows = []
    for row in source["rows"]:
        updated = dict(row)
        updated["base_corrected_evaluation"] = evaluate(row, "base")
        updated["new_v5_corrected_evaluation"] = evaluate(row, "new_v5")
        rows.append(updated)

    def summary(arm: str) -> dict[str, int]:
        key = f"{arm}_corrected_evaluation"
        values = [row[key] for row in rows]
        return {"total": len(values), "severe_errors": sum(x["severe_fact_or_subject_error"] for x in values), "passed": sum(not x["severe_fact_or_subject_error"] for x in values), "unknown_failures": sum(x["unknown_missing"] or x["invented_unknown_status"] for x in values), "subject_or_proposition_failures": sum(x["subject_missing"] or bool(x["missing_required"]) for x in values), "unsupported_bus_failures": sum(x["unsupported_bus_claim"] for x in values)}

    comparison = []
    for row in rows:
        b = row["base_corrected_evaluation"]["severe_fact_or_subject_error"]
        a = row["new_v5_corrected_evaluation"]["severe_fact_or_subject_error"]
        comparison.append({"ordinal": row["ordinal"], "scenario_id": row["scenario_id"], "task_type": row["task_type"], "base_failed": b, "adapter_failed": a, "classification": "adapter_introduced_or_amplified" if a and not b else "adapter_fixed_base_failure" if b and not a else "both_failed" if a and b else "both_pass"})
    result = {"schema_version": 1, "scope": "gemma4_v5_sealed40_corrected_regrade", "generated_at": datetime.now().astimezone().isoformat(), "source_report": str(input_path.resolve()), "gate_version": "v2_semantic_normalization_20260831", "changes": ["expanded unknown markers", "date-specific hallucination detection", "natural time and ordinal normalization", "subject attribution retains proposition requirement"], "summary": {"base": summary("base"), "new_v5": summary("new_v5"), "classification_counts": {label: sum(x["classification"] == label for x in comparison) for label in ("adapter_introduced_or_amplified", "adapter_fixed_base_failure", "both_failed", "both_pass")}}, "comparison": comparison, "rows": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"REPORT={output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
