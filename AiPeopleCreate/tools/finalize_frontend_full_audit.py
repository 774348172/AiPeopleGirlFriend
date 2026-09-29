from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


# The challenge judge overreached on current feelings, immediate self-reports,
# in-scene perceptions, and offers that do not claim completion. These rows were
# manually reconciled against S1-S6 after reading user, target, and visible input.
CHALLENGE_FALSE_REJECTS = {
    "frontend.static.0061.4",
    "frontend.static.0073.4",
    "frontend.static.0077.2",
    "frontend.static.0093.4",
    "frontend.static.0119.4",
    "frontend.static.0181.2",
    "frontend.static.0219.4",
    "frontend.static.0220.4",
    "frontend.static.0225.4",
    "frontend.static.0226.4",
    "frontend.static.0228.4",
    "frontend.static.0247.2",
    "frontend.static.0268.4",
    "frontend.static.0269.2",
    "frontend.static.0276.4",
    "frontend.static.0281.6",
    "frontend.static.0290.4",
    "frontend.static.0293.2",
    "frontend.static.0724.4",
    "frontend.static.0729.2",
    "frontend.static.0729.4",
    "frontend.static.0738.2",
    "frontend.static.1240.2",
    "frontend.static.1240.4",
    "frontend.static.1292.4",
    "frontend.static.1316.4",
    "frontend.static.1377.4",
    "frontend.static.1381.2",
    "frontend.static.1519.4",
    "frontend.static.1522.2",
    "frontend.static.1522.4",
}

# The schema-valid response contradicted itself by returning accepted with an
# unsupported_fact reason. Manual inspection confirmed both targets are unsafe.
FORCED_REJECTS = {
    "frontend.static.0804.2",
    "frontend.static.0806.2",
}


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def target(row: dict) -> str:
    return row["messages"][row["supervised_message_indexes"][-1]]["content"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--first-pass", type=Path, required=True)
    parser.add_argument("--challenge", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    first = json.loads(args.first_pass.read_text(encoding="utf-8"))["decisions"]
    challenge = json.loads(args.challenge.read_text(encoding="utf-8"))["decisions"]
    candidate_rows = read_jsonl(args.candidate)
    source_rows = read_jsonl(args.source)
    candidate_by_id = {row["sample_id"]: row for row in candidate_rows}
    source_by_id = {row["sample_id"]: row for row in source_rows}
    source_line = {row["sample_id"]: i for i, row in enumerate(source_rows, 1)}
    first_by_id = {row["sample_id"]: row for row in first}
    challenge_by_id = {row["sample_id"]: row for row in challenge}

    decisions = {}
    for row in ledger["decisions"]:
        normalized = dict(row)
        if "decision" not in normalized:
            normalized["decision"] = "rejected"
            if normalized["sample_id"] in source_line:
                normalized["source_line"] = source_line[normalized["sample_id"]]
            normalized["reason"] = "旧账本明确缺陷规范化为rejected：" + normalized["reason"]
        decisions[normalized["sample_id"]] = normalized
    for sample_id, first_decision in first_by_id.items():
        if first_decision["decision"] == "rejected":
            decision = "rejected"
            reason = f"S1-S6逐条初审拒绝（{first_decision['reason_code']}）：{first_decision['note']}"
        else:
            second = challenge_by_id[sample_id]
            if sample_id in FORCED_REJECTS:
                decision = "rejected"
                reason = "S1/S5复核拒绝：目标新增了未获输入支持的具体经历或已完成动作。"
            elif second["decision"] == "rejected" and sample_id not in CHALLENGE_FALSE_REJECTS:
                decision = "rejected"
                reason = f"S1-S6反向复核拒绝（{second['reason_code']}）：{second['note']}"
            else:
                decision = "accepted"
                reason = "S1-S6双重逐条审核通过：回复由可见场景、当前对话或角色锚支持，且未发现明确合同冲突。"
        decisions[sample_id] = {
            "sample_id": sample_id,
            "source_line": source_line[sample_id],
            "decision": decision,
            "reason": reason,
        }

    # A later turn cannot be accepted if it treats a previously rejected target
    # as valid assistant history. Iterate because each downgrade may expose more.
    cascade_count = 0
    while True:
        rejected_targets = {
            target(source_by_id[sample_id])
            for sample_id, decision in decisions.items()
            if decision["decision"] == "rejected" and sample_id in source_by_id
        }
        changed = 0
        for sample_id, decision in decisions.items():
            if decision["decision"] != "accepted" or sample_id not in source_by_id:
                continue
            row = source_by_id[sample_id]
            target_index = row["supervised_message_indexes"][-1]
            contaminated = any(
                message["role"] == "assistant" and message["content"] in rejected_targets
                for message in row["messages"][:target_index]
            )
            if contaminated:
                decision["decision"] = "rejected"
                decision["reason"] = "S1-S6历史污染级联拒绝：模型可见的先前assistant历史包含已明确拒绝的监督答案。"
                changed += 1
        cascade_count += changed
        if changed == 0:
            break

    missing = sorted(set(candidate_by_id) - set(decisions))
    if missing:
        raise SystemExit(f"missing candidate decisions: {missing[:10]}")
    candidate_decisions = [decisions[row["sample_id"]] for row in candidate_rows]
    if len({row["sample_id"] for row in candidate_decisions}) != len(candidate_rows):
        raise SystemExit("duplicate candidate decisions")

    # Preserve decisions outside this candidate file, but emit deterministic
    # source order for all rows that belong to the immutable source train split.
    ordered = [decisions[row["sample_id"]] for row in source_rows if row["sample_id"] in decisions]
    ordered_ids = {row["sample_id"] for row in ordered}
    ordered.extend(row for sample_id, row in decisions.items() if sample_id not in ordered_ids)
    document = {
        "standard_id": "reply_quality_v1_20260909",
        "reviewer": "codex_full_input_review_with_local_qwen25_14b_double_pass",
        "review_scope": (
            "All train candidates received an accepted/rejected decision bound to original sample_id and "
            "source_line. Remaining emotion, romance and casual rows received a local-model first pass, "
            "an accepted-row challenge pass, manual reconciliation of challenge disputes, and fixed-point "
            "rejected-history propagation. Targets and human ratings were not changed."
        ),
        "source_train_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "training_authorized": False,
        "decisions": ordered,
    }
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {name: sum(row["decision"] == name for row in candidate_decisions) for name in ("accepted", "rejected")}
    print(json.dumps({"candidate_count": len(candidate_rows), **counts, "cascade_downgrades": cascade_count}, ensure_ascii=False))


if __name__ == "__main__":
    main()
