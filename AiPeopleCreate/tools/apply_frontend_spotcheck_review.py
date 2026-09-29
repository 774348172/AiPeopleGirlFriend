"""Bind final human spot-check ratings and build a targeted remediation queue."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


BAD_REASONS = {
    "frontend.static.1553.4": (
        "人工最终抽检拒绝：输入未提供饭已凉，也未授权热饭动作；目标新增状态并声称将执行动作。"
    ),
    "frontend.static.0476.2": (
        "人工最终抽检拒绝：夜视能力没有回答猫头鹰昼伏夜出的原因，逻辑关联不足。"
    ),
    "frontend.static.0341.4": (
        "人工最终抽检拒绝：句首衔接不成立，且是否住下的表态与当前角色阶段冲突。"
    ),
}

TARGETED_REJECTIONS = {
    "frontend.static.1553.2": "定向复审拒绝：玩家问是否吃饭，目标转去谈腿伤和玩家休息，未回答问题。",
    "frontend.static.0457.2": "定向复审拒绝：先承认无法感知天气，随后又用猫的直觉断言今晚会凉，自相矛盾并补造实时天气。",
    "frontend.static.0481.2": "定向复审拒绝：输入没有提供广告牌文字，目标擅自补出“茶馆”。",
    "frontend.static.1660.4": "定向复审拒绝：当前角色正典和可见输入未定义睡觉会蜷起这一稳定个人习惯。",
    "frontend.static.1671.2": "定向复审拒绝：只说明声音反射，没有正确回答空旷处回声更明显的原因，后句把传得远误当成回声成因。",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def target(row: dict) -> str:
    return row["messages"][row["supervised_message_indexes"][-1]]["content"]


def validate_review(review: dict, package: Path) -> None:
    if review.get("schema_version") != "aip.frontend_final_spotcheck80.v1":
        raise ValueError("Unsupported spot-check schema")
    if review.get("status") != "human_review_complete":
        raise ValueError("Human spot-check is incomplete")
    if Path(review["source_package"]).resolve() != package.resolve():
        raise ValueError("Spot-check source package changed")
    if review.get("source_manifest_sha256") != sha(package / "manifest.json"):
        raise ValueError("Spot-check package manifest changed")
    rows = review.get("reviews") or []
    if len(rows) != 80 or len({row["sample_id"] for row in rows}) != 80:
        raise ValueError("Spot-check must contain 80 unique rows")
    if any(row.get("rating") not in {"good", "bad"} for row in rows):
        raise ValueError("Every spot-check row must have a good/bad rating")


def risk_clusters(row: dict, bad_ids: set[str]) -> list[str]:
    value = target(row)
    system = row["messages"][0]["content"]
    risks: list[str] = []
    prefix = row["sample_id"].rsplit(".", 1)[0]
    if any(other.rsplit(".", 1)[0] == prefix for other in bad_ids):
        risks.append("direct_conversation_neighbor")
    if row.get("task_type") in {"reply_emotion", "reply_supportive", "reply_protective"}:
        if re.search(r"我(?:去|来|给你|帮你|替你|马上|这就)", value) and "[允许表达的动作]\n无" in system:
            risks.append("unsupported_state_or_action")
    if row.get("task_type") == "reply_general":
        risks.append("general_answer_relevance")
    if row.get("task_type") == "reply_identity" and re.search(r"离开|留下|住下|山里|伤好", value):
        risks.append("persona_stage_or_coherence")
    return risks


def apply(review_path: Path, package: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    validate_review(review, package)
    decisions_doc = json.loads((package / "decisions.json").read_text(encoding="utf-8"))
    decisions = {row["sample_id"]: row for row in decisions_doc["decisions"]}
    package_rows: dict[str, tuple[str, dict]] = {}
    for split in ("train", "dev", "test"):
        for row in read_jsonl(package / f"{split}.accepted.jsonl"):
            package_rows[row["sample_id"]] = (split, row)

    bad_reviews = [row for row in review["reviews"] if row["rating"] == "bad"]
    bad_ids = {row["sample_id"] for row in bad_reviews}
    if bad_ids != set(BAD_REASONS):
        raise ValueError(f"Unexpected human bad-rating set: {sorted(bad_ids)}")
    for reviewed in review["reviews"]:
        sid = reviewed["sample_id"]
        if sid not in package_rows or decisions[sid]["decision"] != "accepted":
            raise ValueError(f"Reviewed row is no longer accepted: {sid}")
        row = package_rows[sid][1]
        if target(row) != reviewed["target"]:
            raise ValueError(f"Reviewed target changed: {sid}")
    for sid in bad_ids:
        decisions[sid] = {
            **decisions[sid],
            "decision": "rejected",
            "reason": BAD_REASONS[sid],
            "human_review_source": review_path.name,
            "human_rating": "bad",
        }

    for sid, reason in TARGETED_REJECTIONS.items():
        if sid not in package_rows or decisions[sid]["decision"] != "accepted":
            raise ValueError(f"Targeted-review row is no longer accepted: {sid}")
        decisions[sid] = {
            **decisions[sid],
            "decision": "rejected",
            "reason": reason,
            "targeted_review_source": "three_cluster_review_after_human_spotcheck80",
        }

    propagated: list[str] = []
    newly_rejected = set(bad_ids) | set(TARGETED_REJECTIONS)
    while newly_rejected:
        rejected_targets = {target(package_rows[sid][1]) for sid in newly_rejected}
        newly_rejected = set()
        for sid, (_, row) in package_rows.items():
            if decisions[sid]["decision"] != "accepted":
                continue
            target_index = row["supervised_message_indexes"][-1]
            if any(
                message["role"] == "assistant" and message["content"] in rejected_targets
                for message in row["messages"][:target_index]
            ):
                decisions[sid] = {
                    **decisions[sid],
                    "decision": "rejected",
                    "reason": "人工/定向差评精确历史传播：可见assistant历史包含本轮新确认的错误监督答案。",
                    "targeted_review_source": "exact_rejected_assistant_history",
                }
                propagated.append(sid)
                newly_rejected.add(sid)

    revised = {
        **decisions_doc,
        "reviewer": decisions_doc.get("reviewer", "") + "+human_final_spotcheck80",
        "review_scope": decisions_doc.get("review_scope", "") + (
            " Final 80-row human spot-check was bound without rewriting targets; "
            "three human bad ratings override prior automated acceptance. Five additional rows were "
            "rejected by complete-input review of the targeted risk queue."
        ),
        "training_authorized": False,
        "decisions": [decisions[row["sample_id"]] for row in decisions_doc["decisions"]],
    }
    ledger_path = output / "decisions.after_human_spotcheck.json"
    ledger_path.write_text(json.dumps(revised, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "human_review.json").write_bytes(review_path.read_bytes())

    queue = []
    for sid, (split, row) in package_rows.items():
        if sid in bad_ids:
            continue
        clusters = risk_clusters(row, bad_ids)
        if clusters:
            queue.append({
                "sample_id": sid,
                "source_split": split,
                "task_type": row.get("task_type"),
                "family_id": row.get("family_id"),
                "risk_clusters": clusters,
                "messages": row["messages"][: row["supervised_message_indexes"][-1]],
                "target": target(row),
                "review_status": (
                    "rejected_after_targeted_review"
                    if sid in TARGETED_REJECTIONS
                    else "accepted_after_targeted_review"
                ),
                "review_reason": TARGETED_REJECTIONS.get(
                    sid, "完整输入复审未发现该风险簇的明确错误；保留原逐条接受结论。"
                ),
            })
    risk_doc = {
        "schema_version": "aip.frontend_targeted_risk_review.v1",
        "source_package": str(package.resolve()),
        "source_manifest_sha256": sha(package / "manifest.json"),
        "automatic_decision": False,
        "family_id_used_for_rejection": False,
        "training_authorized": False,
        "counts": {
            "total": len(queue),
            "accepted_after_targeted_review": sum(
                row["review_status"] == "accepted_after_targeted_review" for row in queue
            ),
            "rejected_after_targeted_review": sum(
                row["review_status"] == "rejected_after_targeted_review" for row in queue
            ),
            "by_cluster": dict(Counter(cluster for row in queue for cluster in row["risk_clusters"])),
        },
        "candidates": queue,
    }
    (output / "targeted_risk_review.json").write_text(
        json.dumps(risk_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "human_good": 80 - len(bad_ids),
        "human_bad": len(bad_ids),
        "targeted_rejected": len(TARGETED_REJECTIONS),
        "total_new_rejections": len(bad_ids) + len(TARGETED_REJECTIONS) + len(propagated),
        "exact_history_rejections": len(propagated),
        "exact_history_rejected_ids": propagated,
        "rejected_ids": sorted(bad_ids),
        "targeted_candidates": len(queue),
        "by_cluster": risk_doc["counts"]["by_cluster"],
        "training_authorized": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(apply(args.review, args.package, args.output), ensure_ascii=False, indent=2))
