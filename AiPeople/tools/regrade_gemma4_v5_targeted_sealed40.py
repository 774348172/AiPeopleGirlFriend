"""Regrade a single-Adapter sealed40 report with the targeted V5 contracts."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from regrade_gemma4_v5_sealed40_attribution import UNKNOWN_MARKERS, object_present

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT.parent / "AiPeopleCreate" / "训练数据" / "baiweixi_v5_pilot_v1"
UNAUTHORIZED_ACTION = re.compile(r"(?:我|我们)?(?:刚刚|已经)?(?:确认过|查过|联系过|通知过|安排过)")
UNBOUNDED = re.compile(r"每天|每次|一直|永远|都会|都有")
CONCRETE_DATE = re.compile(r"(?:\d{1,4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*(?:日|号)?|\d{1,2}\s*月\s*\d{1,2}\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天]|前天|昨天|两周前)")


def signature(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def acknowledged_unknown(reply: str) -> bool:
    return (
        any(marker in reply for marker in UNKNOWN_MARKERS)
        or "没记清" in reply
        or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply))
        or bool(re.search(r"(?:没|没有|从来没).*(?:提到|说过|告诉)", reply))
    )


def stale_revival(expected: str, reply: str, required_present: bool) -> bool:
    for match in re.finditer(re.escape(expected), reply):
        prefix = reply[max(0, match.start() - 14):match.start()]
        if re.search(r"(?:不|没|无|非|不是|并非|不在|没在|没有|已经失效)", prefix):
            continue
        if required_present and re.search(r"(?:以前|之前|原来|过去|曾经|上次)", prefix):
            continue
        return True
    return False


def present(expected: str, reply: str) -> bool:
    if object_present(expected, reply):
        return True
    return expected == "玄关柜顶层" and "玄关柜" in reply and "第一层" in reply


def evaluate(reply: str, scenario: dict) -> dict:
    task = str(scenario["task_type"])
    oracle = scenario["oracle_view"]
    required = [str(item.get("object", "")) for item in oracle.get("required_assertions", []) if item.get("object")]
    forbidden = [str(item.get("object", "")) for item in oracle.get("forbidden_assertions", []) if item.get("object")]
    required_ok = all(present(value, reply) for value in required)
    missing = [value for value in required if not present(value, reply)]
    forbidden_hits = [] if task == "reply_subject_attribution" else [value for value in forbidden if stale_revival(value, reply, required_ok)]
    speakers = [line.split("：", 1)[0].strip() for line in scenario["model_view"].get("recent_dialogue", []) if "：" in line]
    expected_speaker = next((speaker for speaker in speakers if speaker not in {"男主", "白未晞"}), None)
    speaker_missing = task == "reply_subject_attribution" and bool(expected_speaker and expected_speaker not in reply)
    unknown_missing = task == "reply_insufficient_information" and not acknowledged_unknown(reply)
    unknown_hallucination = task == "reply_insufficient_information" and not acknowledged_unknown(reply) and bool(CONCRETE_DATE.search(reply))
    unauthorized_actions = UNAUTHORIZED_ACTION.findall(reply)
    unbounded_expansions = UNBOUNDED.findall(reply)
    utterance = str(scenario["model_view"].get("current_protagonist_utterance", ""))
    ownership_shift = "你的" in utterance and "我的" in reply
    severe = bool(missing or forbidden_hits or speaker_missing or unknown_missing or unknown_hallucination or unauthorized_actions or unbounded_expansions or ownership_shift)
    return {
        "missing_required": missing,
        "forbidden_hits": forbidden_hits,
        "speaker_missing": speaker_missing,
        "unknown_missing": unknown_missing,
        "unknown_hallucination": unknown_hallucination,
        "unauthorized_actions": unauthorized_actions,
        "unbounded_expansions": unbounded_expansions,
        "ownership_shift": ownership_shift,
        "severe": severe,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_report", type=Path)
    parser.add_argument("output_report", type=Path)
    parser.add_argument("--arm", choices=("auto", "reply", "base", "new_v5"), default="auto")
    args = parser.parse_args()
    input_path, output_path = args.input_report, args.output_report
    source = json.loads(input_path.read_text(encoding="utf-8"))
    scenarios = [json.loads(line) for line in (PACKAGE / "inputs" / "sealed_scenarios.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {str(row["scenario_id"]): row for row in scenarios}
    rows = []
    for row in source["rows"]:
        arm = args.arm
        if arm == "auto":
            arm = "reply" if "reply" in row else "new_v5"
        reply = str(row["reply"] if arm == "reply" else row[arm]["response"])
        scenario = row if "oracle_view" in row and "model_view" in row else by_id[str(row["scenario_id"])]
        updated = dict(row)
        updated["evaluated_reply"] = reply
        updated["targeted_contract_evaluation"] = evaluate(reply, scenario)
        rows.append(updated)
    failures = [row for row in rows if row["targeted_contract_evaluation"]["severe"]]
    by_task = {}
    for task in sorted({row["task_type"] for row in rows}):
        selected = [row for row in rows if row["task_type"] == task]
        by_task[task] = {"total": len(selected), "passed": sum(not row["targeted_contract_evaluation"]["severe"] for row in selected)}
    reason_counts = Counter()
    for row in failures:
        for key, value in row["targeted_contract_evaluation"].items():
            if key != "severe" and value:
                reason_counts[key] += 1
    result = {
        "schema_version": 1,
        "scope": "gemma4_v5_targeted_sealed40_contract_regrade",
        "generated_at": datetime.now().astimezone().isoformat(),
        "source_report": str(input_path.resolve()),
        "evaluated_arm": args.arm,
        "adapter_path": source.get("adapter_path"),
        "adapter_sha256": source.get("adapter_sha256"),
        "gate_version": "targeted_contract_v1_20260831",
        "summary": {"total": len(rows), "passed": len(rows) - len(failures), "severe": len(failures), "reason_counts": dict(reason_counts), "by_task": by_task},
        "rows": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"REPORT={output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
