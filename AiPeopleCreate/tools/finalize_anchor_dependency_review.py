"""Finalize role-anchor dependency review without rewriting any target."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REJECTIONS = {
    "frontend.static.0006.4", "frontend.static.0010.4",
    "frontend.static.0065.2", "frontend.static.0199.2",
    "frontend.static.0214.4", "frontend.static.0219.4",
    "frontend.static.0283.2", "frontend.static.0288.4",
    "frontend.static.0304.2", "frontend.static.0401.2",
    "frontend.static.0669.4", "frontend.static.0798.2",
    "frontend.static.0838.2", "frontend.static.0998.2",
    "frontend.static.1099.2", "frontend.static.1120.2",
    "frontend.static.1219.2", "frontend.static.1342.2",
    "frontend.static.1375.4",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def target(row: dict) -> str:
    return row["messages"][row["supervised_message_indexes"][-1]]["content"]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def finalize(package: Path, prior_report: Path, ledger: Path, source: Path, output: Path) -> dict:
    rows = []
    for split in ("train", "dev", "test"):
        for row in read_jsonl(package / f"{split}.accepted.jsonl"):
            rows.append((split, row))
    by_id = {row["sample_id"]: row for _, row in rows}
    if not REJECTIONS <= set(by_id):
        raise ValueError(f"Missing dependency rejection IDs: {sorted(REJECTIONS - set(by_id))}")

    prior = json.loads(prior_report.read_text(encoding="utf-8"))
    flagged = {row["sample_id"] for row in prior["candidates"]}
    reviewed_ids = flagged | {"frontend.static.0669.4"}
    decisions_doc = json.loads(ledger.read_text(encoding="utf-8"))
    decisions = {row["sample_id"]: dict(row) for row in decisions_doc["decisions"]}
    source_lines = {}
    source_order = []
    for split in ("train", "dev", "test"):
        for line, row in enumerate(read_jsonl(source / f"{split}.jsonl"), 1):
            source_lines[row["sample_id"]] = (split, line)
            source_order.append(row["sample_id"])

    for sid in REJECTIONS:
        split, line = source_lines[sid]
        decisions[sid] = {
            "sample_id": sid,
            "source_split": split,
            "source_line": line,
            "decision": "rejected",
            "reason": (
                "统一角色锚依赖复审拒绝：目标依赖新生产锚、动态上下文、对话或记忆均未支持的"
                "具体习惯/共同记忆，或违反当前角色披露边界。"
            ),
        }

    propagated = []
    newly_rejected = set(REJECTIONS)
    while newly_rejected:
        bad_targets = {target(by_id[sid]) for sid in newly_rejected if sid in by_id}
        newly_rejected = set()
        for _, row in rows:
            sid = row["sample_id"]
            if decisions[sid]["decision"] != "accepted":
                continue
            target_index = row["supervised_message_indexes"][-1]
            if any(
                message["role"] == "assistant" and message["content"] in bad_targets
                for message in row["messages"][:target_index]
            ):
                split, line = source_lines[sid]
                decisions[sid] = {
                    "sample_id": sid,
                    "source_split": split,
                    "source_line": line,
                    "decision": "rejected",
                    "reason": "统一角色锚依赖复审精确历史传播：可见assistant历史包含本轮确认的错误目标。",
                }
                propagated.append(sid)
                newly_rejected.add(sid)

    review_rows = []
    for sid in sorted(reviewed_ids):
        row = by_id[sid]
        review_rows.append({
            "sample_id": sid,
            "target_sha256": digest(target(row)),
            "decision": "rejected" if sid in REJECTIONS else "accepted",
            "reason": (
                "缺少生产可见依据或违反披露边界。" if sid in REJECTIONS
                else "完整输入与新共享角色锚复审后有明确依据，保留。"
            ),
        })
    review_doc = {
        "schema_version": "aip.frontend_anchor_dependency_decisions.v1",
        "source_package": str(package.resolve()),
        "training_authorized": False,
        "review_complete": True,
        "decisions": review_rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(review_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    merged = {
        **decisions_doc,
        "reviewer": decisions_doc.get("reviewer", "") + "+canonical_anchor_dependency_review",
        "review_scope": decisions_doc.get("review_scope", "") + (
            " Canonical production-anchor dependency candidates were fully reviewed; targets were not rewritten."
        ),
        "training_authorized": False,
        "decisions": [decisions[sid] for sid in source_order if sid in decisions],
    }
    ledger_output = output.with_name("decisions.after_anchor_dependency.json")
    ledger_output.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {
        "reviewed": len(review_rows),
        "accepted": sum(row["decision"] == "accepted" for row in review_rows),
        "primary_rejected": len(REJECTIONS),
        "history_rejected": len(propagated),
        "history_rejected_ids": propagated,
        "training_authorized": False,
        "ledger": str(ledger_output.resolve()),
    }
    output.with_suffix(".summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--prior-report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.package, args.prior_report, args.ledger, args.source, args.output), ensure_ascii=True, indent=2))
