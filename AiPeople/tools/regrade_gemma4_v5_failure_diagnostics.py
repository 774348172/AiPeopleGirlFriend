"""Regrade stored failure-diagnostic outputs after evaluator rule updates."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from regrade_gemma4_v5_sealed40_attribution import UNKNOWN_MARKERS, object_present, forbidden_revival

UNAUTHORIZED_ACTION = re.compile(r"(?:我|我们)?(?:刚刚|已经)?(?:确认过|查过|联系过|通知过|安排过)")
UNBOUNDED = re.compile(r"每天|每次|一直|永远|都会|都有")


def score(row: dict, arm: str) -> dict:
    expected = row["expected"]
    reply = row[arm]["response"]
    cluster = row["cluster"]
    missing = [value for value in expected.get("required") or [] if not object_present(value, reply)]
    forbidden = [value for value in expected.get("forbidden") or [] if forbidden_revival(value, reply)]
    speaker_missing = bool(expected.get("speaker") and expected["speaker"] not in reply)
    unknown_ack = any(marker in reply for marker in UNKNOWN_MARKERS) or "没记清" in reply or bool(re.search(r"(?:没有|无).*(?:具体|历史)?(?:记录|资料|信息)", reply)) or bool(re.search(r"(?:没|没有|从来没).*(?:提到|说过|告诉)", reply))
    unknown_missing = cluster == "unknown" and not unknown_ack
    date_hallucination = cluster == "unknown" and not unknown_ack and bool(re.search(r"(?:\d+\s*月\s*\d+\s*[日号]|[一二三四五六七八九十两零〇]+月[一二三四五六七八九十两零〇]+[日号]|前?周[一二三四五六七日天]|星期[一二三四五六七日天]|前天|昨天|两周前)", reply))
    unsupported = [value for value in expected.get("unsupported") or [] if value in reply]
    unauthorized_actions = UNAUTHORIZED_ACTION.findall(reply)
    unbounded_expansions = UNBOUNDED.findall(reply)
    severe = bool(missing or forbidden or speaker_missing or unknown_missing or date_hallucination or unsupported or unauthorized_actions or unbounded_expansions)
    return {"missing_required": missing, "forbidden_hits": forbidden, "speaker_missing": speaker_missing, "unknown_missing": unknown_missing, "date_hallucination": date_hallucination, "unsupported_hits": unsupported, "unauthorized_actions": unauthorized_actions, "unbounded_expansions": unbounded_expansions, "severe": severe}


def main() -> int:
    if len(sys.argv) != 3:
        raise SystemExit("usage: regrade_gemma4_v5_failure_diagnostics.py INPUT_REPORT OUTPUT_REPORT")
    input_path, output_path = map(Path, sys.argv[1:])
    source = json.loads(input_path.read_text(encoding="utf-8"))
    arms = [arm for arm in ("base", "new_v5", "old_v5", "targeted_v5") if arm in source["rows"][0]]
    rows = []
    for row in source["rows"]:
        updated = dict(row)
        for arm in arms:
            updated[f"{arm}_corrected_evaluation"] = score(row, arm)
        rows.append(updated)

    summary = {}
    for cluster in ("unknown", "subject_plus_proposition", "fact_granularity"):
        selected = [row for row in rows if row["cluster"] == cluster]
        summary[cluster] = {arm: {"total": len(selected), "severe": sum(row[f"{arm}_corrected_evaluation"]["severe"] for row in selected), "passed": sum(not row[f"{arm}_corrected_evaluation"]["severe"] for row in selected)} for arm in arms}
    comparison = []
    if "base" in arms and "new_v5" in arms:
        for row in rows:
            base_failed = row["base_corrected_evaluation"]["severe"]
            adapter_failed = row["new_v5_corrected_evaluation"]["severe"]
            comparison.append({"id": row["id"], "cluster": row["cluster"], "base_failed": base_failed, "adapter_failed": adapter_failed, "classification": "adapter_regression" if adapter_failed and not base_failed else "adapter_fix" if base_failed and not adapter_failed else "both_failed" if base_failed and adapter_failed else "both_pass"})
    result = {"schema_version": 1, "scope": "gemma4_v5_failure_diagnostics_corrected", "source_report": str(input_path.resolve()), "gate_version": "diagnostic_v2_20260831", "summary": summary, "comparison": comparison, "rows": rows}
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"REPORT={output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
