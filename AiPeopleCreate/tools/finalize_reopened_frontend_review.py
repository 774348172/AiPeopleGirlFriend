"""Finalize the 163 rows reopened after removing legacy family-id cascade."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REJECTION_GROUPS = {
    "question_premise_or_unrecorded_episode": {
        "frontend.static.0417.2", "frontend.static.1224.2",
        "frontend.static.1232.2", "frontend.static.1233.2",
        "frontend.static.1274.2", "frontend.static.1278.2",
        "frontend.static.1347.2",
    },
    "unsupported_action_or_current_state": {
        "frontend.static.1223.2", "frontend.static.1227.2",
        "frontend.static.1273.2", "frontend.static.1305.2",
        "frontend.static.1312.2", "frontend.static.1313.2",
        "frontend.static.1320.2", "frontend.static.1325.2",
        "frontend.static.1330.2", "frontend.static.1331.4",
        "frontend.static.1332.2", "frontend.static.1348.2",
    },
    "unsupported_backstory_memory_or_stable_habit": {
        "frontend.static.1229.2", "frontend.static.1279.2",
        "frontend.static.1281.4", "frontend.static.1287.4",
        "frontend.static.1300.2", "frontend.static.1301.2",
        "frontend.static.1303.2", "frontend.static.1304.2",
        "frontend.static.1310.2", "frontend.static.1311.2",
        "frontend.static.1317.2", "frontend.static.1323.6",
        "frontend.static.1329.2", "frontend.static.1338.2",
        "frontend.static.1344.2", "frontend.static.1352.4",
    },
    "persona_stage_conflict": {"frontend.static.1284.4"},
    "intent_or_relevance_failure": {"frontend.static.1315.4"},
}

REASONS = {
    "question_premise_or_unrecorded_episode": (
        "S1-S6重新开放样本复审拒绝：目标把玩家问句中的预设当成已经发生且可确认的经历。"
    ),
    "unsupported_action_or_current_state": (
        "S1-S6重新开放样本复审拒绝：可见输入未提供该当前状态或动作结果，目标擅自补全。"
    ),
    "unsupported_backstory_memory_or_stable_habit": (
        "S1-S6重新开放样本复审拒绝：目标新增角色锚和可见输入均未定义的往事、记忆或稳定习惯。"
    ),
    "persona_stage_conflict": (
        "S1-S6重新开放样本复审拒绝：是否离开的表态与当前角色阶段锚冲突。"
    ),
    "intent_or_relevance_failure": (
        "S1-S6重新开放样本复审拒绝：目标没有直接处理玩家所问事项。"
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def target(row: dict) -> str:
    return row["messages"][row["supervised_message_indexes"][-1]]["content"]


def finalize(package: Path, ledger_path: Path, output: Path) -> dict:
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    candidate_path = package / "train.candidate.jsonl"
    if sha(candidate_path) != manifest["files"][candidate_path.name]:
        raise ValueError("Reopened candidate file changed")
    candidates = read_jsonl(candidate_path)
    if len(candidates) != 163 or len({row["sample_id"] for row in candidates}) != 163:
        raise ValueError("Expected 163 unique reopened candidates")

    primary_reasons = {
        sid: REASONS[group]
        for group, ids in REJECTION_GROUPS.items()
        for sid in ids
    }
    candidate_ids = {row["sample_id"] for row in candidates}
    missing = set(primary_reasons) - candidate_ids
    if missing:
        raise ValueError(f"Primary rejection IDs missing: {sorted(missing)}")

    decisions_doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    decisions = {row["sample_id"]: dict(row) for row in decisions_doc["decisions"]}
    candidate_decisions: dict[str, dict] = {}
    for line, row in enumerate(candidates, 1):
        sid = row["sample_id"]
        reason = primary_reasons.get(sid)
        candidate_decisions[sid] = {
            "sample_id": sid,
            "source_split": "train",
            "decision": "rejected" if reason else "accepted",
            "reason": reason or (
                "S1-S6重新开放样本完整输入复审通过：此前仅由旧family_id关联，未发现独立内容错误。"
            ),
        }
    source_lines = {
        row["sample_id"]: row["source_line"]
        for row in read_jsonl(package / "review_ledger.jsonl")
    }
    for sid, decision in candidate_decisions.items():
        decision["source_line"] = source_lines[sid]

    propagated: list[str] = []
    by_id = {row["sample_id"]: row for row in candidates}
    newly_rejected = {sid for sid, row in candidate_decisions.items() if row["decision"] == "rejected"}
    while newly_rejected:
        bad_targets = {target(by_id[sid]) for sid in newly_rejected}
        newly_rejected = set()
        for sid, row in by_id.items():
            if candidate_decisions[sid]["decision"] != "accepted":
                continue
            target_index = row["supervised_message_indexes"][-1]
            if any(
                message["role"] == "assistant" and message["content"] in bad_targets
                for message in row["messages"][:target_index]
            ):
                candidate_decisions[sid]["decision"] = "rejected"
                candidate_decisions[sid]["reason"] = (
                    "S1-S6精确历史传播拒绝：可见assistant历史包含本批次已确认的错误目标。"
                )
                propagated.append(sid)
                newly_rejected.add(sid)

    decisions.update(candidate_decisions)
    source_order = read_jsonl(package.parent / "baiweixi_frontend_full_20260908" / "train.jsonl")
    dev_order = read_jsonl(package.parent / "baiweixi_frontend_full_20260908" / "dev.jsonl")
    test_order = read_jsonl(package.parent / "baiweixi_frontend_full_20260908" / "test.jsonl")
    ordered = [
        decisions[row["sample_id"]]
        for row in source_order + dev_order + test_order
        if row["sample_id"] in decisions
    ]
    output_doc = {
        **decisions_doc,
        "reviewer": decisions_doc.get("reviewer", "") + "+codex_reopened_163_full_input_review",
        "review_scope": decisions_doc.get("review_scope", "") + (
            " The 163 rows formerly quarantined only by legacy family_id were independently reviewed; "
            "family_id was not used as a verdict and exact rejected history was propagated."
        ),
        "training_authorized": False,
        "decisions": ordered,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(output_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result = {
        "reviewed": len(candidates),
        "accepted": sum(row["decision"] == "accepted" for row in candidate_decisions.values()),
        "primary_rejected": len(primary_reasons),
        "history_rejected": len(propagated),
        "rejected": sum(row["decision"] == "rejected" for row in candidate_decisions.values()),
        "history_rejected_ids": propagated,
        "training_authorized": False,
    }
    (output.with_suffix(".summary.json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(finalize(args.package, args.ledger, args.output), ensure_ascii=False, indent=2))
