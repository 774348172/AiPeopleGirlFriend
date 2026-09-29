from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


DEV_FALSE_CHALLENGE_REJECTS = {"frontend.static.0204.2"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_decisions(path: Path) -> dict[str, dict]:
    return {row["sample_id"]: row for row in json.loads(path.read_text(encoding="utf-8"))["decisions"]}


def target(row: dict) -> str:
    return row["messages"][row["supervised_message_indexes"][-1]]["content"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--prior-review-ledger", type=Path, required=True)
    parser.add_argument("--dev-first", type=Path, required=True)
    parser.add_argument("--dev-challenge", type=Path, required=True)
    parser.add_argument("--test-first", type=Path, required=True)
    parser.add_argument("--test-challenge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    split_rows = {split: read_jsonl(args.source / f"{split}.jsonl") for split in ("train", "dev", "test")}
    indexed = {}
    for split, rows in split_rows.items():
        for line, row in enumerate(rows, 1):
            indexed[row["sample_id"]] = (split, line, row)

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    decisions = {row["sample_id"]: dict(row) for row in ledger["decisions"]}
    audits = (
        (load_decisions(args.dev_first), load_decisions(args.dev_challenge), DEV_FALSE_CHALLENGE_REJECTS),
        (load_decisions(args.test_first), load_decisions(args.test_challenge), set()),
    )
    for first, challenge, false_rejects in audits:
        for sample_id, first_row in first.items():
            split, line, _ = indexed[sample_id]
            if first_row["decision"] == "rejected":
                status = "rejected"
                reason = f"S1-S6逐条初审拒绝（{first_row['reason_code']}）：{first_row['note']}"
            else:
                second = challenge[sample_id]
                if second["decision"] == "rejected" and sample_id not in false_rejects:
                    status = "rejected"
                    reason = f"S1-S6反向复核拒绝（{second['reason_code']}）：{second['note']}"
                else:
                    status = "accepted"
                    reason = "S1-S6双重逐条审核通过：回复由可见输入支持，且未发现明确合同冲突。"
            decisions[sample_id] = {
                "sample_id": sample_id,
                "source_split": split,
                "source_line": line,
                "decision": status,
                "reason": reason,
            }

    cascade_count = 0
    while True:
        rejected_targets = {
            target(indexed[sid][2]) for sid, decision in decisions.items()
            if decision.get("decision", "rejected") == "rejected" and sid in indexed
        }
        changed = 0
        for sid, decision in decisions.items():
            if decision.get("decision") != "accepted" or sid not in indexed:
                continue
            row = indexed[sid][2]
            target_index = row["supervised_message_indexes"][-1]
            if any(m["role"] == "assistant" and m["content"] in rejected_targets
                   for m in row["messages"][:target_index]):
                decision["decision"] = "rejected"
                decision["reason"] = "S1-S6历史污染级联拒绝：先前assistant历史包含已明确拒绝的监督答案。"
                changed += 1
        cascade_count += changed
        if not changed:
            break

    missing = set(indexed) - set(decisions)
    prior_records = read_jsonl(args.prior_review_ledger)
    allowed_related = {row["sample_id"] for row in prior_records if row["status"] == "related_quarantine"}
    unexpected_missing = sorted(missing - allowed_related)
    if unexpected_missing or missing != allowed_related:
        raise SystemExit(
            f"unexpected missing decisions: {unexpected_missing[:10]} (missing {len(missing)}, "
            f"allowed related {len(allowed_related)})"
        )
    ordered = [
        decisions[row["sample_id"]]
        for split in ("train", "dev", "test")
        for row in split_rows[split]
        if row["sample_id"] in decisions
    ]
    document = {
        "standard_id": "reply_quality_v1_20260909",
        "reviewer": "codex_full_input_review_with_local_qwen25_14b_double_pass",
        "review_scope": (
            "All train/dev/test rows are accepted or rejected under S1-S6. Local-model first pass, "
            "accepted-row challenge pass, manual dispute reconciliation and fixed-point rejected-history "
            "propagation completed. Targets and human ratings were not changed."
        ),
        "source_train_sha256": hashlib.sha256((args.source / "train.jsonl").read_bytes()).hexdigest(),
        "source_dev_sha256": hashlib.sha256((args.source / "dev.jsonl").read_bytes()).hexdigest(),
        "source_test_sha256": hashlib.sha256((args.source / "test.jsonl").read_bytes()).hexdigest(),
        "training_authorized": False,
        "decisions": ordered,
    }
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for split in ("train", "dev", "test"):
        ids = {row["sample_id"] for row in split_rows[split] if row["sample_id"] in decisions}
        counts[split] = {
            status: sum(decisions[sid]["decision"] == status for sid in ids)
            for status in ("accepted", "rejected")
        }
    print(json.dumps({"counts": counts, "cascade_downgrades": cascade_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
