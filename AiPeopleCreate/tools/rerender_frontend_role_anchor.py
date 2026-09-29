"""Re-render reviewed GAME_REPLY rows with the canonical production role anchor."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from build_v5_pilot_dataset import _load_baiweixi_production_contract


SPLITS = ("train", "dev", "test")
MARKER = "\n\n[角色锚]"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dependency_risks(row: dict) -> list[str]:
    target_index = row["supervised_message_indexes"][-1]
    target = row["messages"][target_index]["content"]
    system = row["messages"][0]["content"]
    risks: list[str] = []
    if re.search(r"不想走|不打算走|不离开|留下|住下|像(?:个)?家|以后.*这里", target):
        if "关系：未提供" in system:
            risks.append("dynamic_relationship_missing")
    if re.search(r"妖力(?:还|尚|没|没有)|伤(?:已经|还|快|好)|腿(?:还|不)疼", target):
        if "身体：未提供" in system and "外伤恢复大半" not in system:
            risks.append("dynamic_body_or_magic_missing")
    if re.search(r"我(?:会|学会了|习惯|记得|以前)|流浪时|山里.*学", target):
        risks.append("specific_skill_memory_or_habit")
    return risks


def rerender(package: Path, output: Path, dependency_review: Path | None = None) -> dict:
    source_manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    if source_manifest.get("status") != "semantic_review_complete_training_not_authorized":
        raise ValueError("Source package must be semantically complete and training-blocked")
    if source_manifest.get("training_authorized") is not False:
        raise ValueError("Source package unexpectedly authorizes training")
    output.mkdir(parents=True, exist_ok=False)
    canonical_anchor, initial_runtime = _load_baiweixi_production_contract()
    reviewed = {}
    if dependency_review is not None:
        review_doc = json.loads(dependency_review.read_text(encoding="utf-8"))
        if review_doc.get("schema_version") != "aip.frontend_anchor_dependency_decisions.v1":
            raise ValueError("Unsupported anchor dependency review")
        reviewed = {row["sample_id"]: row for row in review_doc["decisions"]}
    old_prefixes: set[str] = set()
    risks: list[dict] = []
    counts: dict[str, int] = {}

    for split in SPLITS:
        source_path = package / f"{split}.accepted.jsonl"
        rows = read_jsonl(source_path)
        rendered: list[dict] = []
        for row in rows:
            row = dict(row)
            row["messages"] = [dict(message) for message in row["messages"]]
            system = row["messages"][0]["content"]
            if MARKER not in system:
                raise ValueError(f"Missing context marker: {row['sample_id']}")
            old_prefix, context = system.split(MARKER, 1)
            old_prefixes.add(old_prefix)
            rendered_context = MARKER + context
            missing_relationship = "关系：未提供；未提供；"
            if row.get("source") == "persona" and missing_relationship in rendered_context:
                relationship = initial_runtime.relationship
                rendered_context = rendered_context.replace(
                    missing_relationship,
                    "关系：{stage}；{trust}；{tension}".format(
                        stage=relationship["stage"],
                        trust=relationship["trust"],
                        tension=relationship["unresolved_tension"],
                    ),
                    1,
                )
            row["messages"][0]["content"] = canonical_anchor + rendered_context
            row["role_anchor_sha256"] = sha_text(canonical_anchor)
            rendered.append(row)
            row_risks = dependency_risks(row)
            if row_risks:
                target_index = row["supervised_message_indexes"][-1]
                reviewed_decision = reviewed.get(row["sample_id"])
                if reviewed_decision and reviewed_decision["target_sha256"] != sha_text(
                    row["messages"][target_index]["content"]
                ):
                    raise ValueError(f"Dependency review target changed: {row['sample_id']}")
                risks.append({
                    "sample_id": row["sample_id"],
                    "source_split": split,
                    "task_type": row.get("task_type"),
                    "risks": row_risks,
                    "messages": row["messages"][:target_index],
                    "target": row["messages"][target_index]["content"],
                    "decision": (
                        reviewed_decision["decision"]
                        if reviewed_decision else "needs_anchor_dependency_review"
                    ),
                })
        destination = output / f"{split}.accepted.jsonl"
        destination.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rendered),
            encoding="utf-8",
        )
        counts[split] = len(rendered)

    report = {
        "schema_version": "aip.frontend_anchor_dependency_review.v1",
        "source_package": str(package.resolve()),
        "source_manifest_sha256": sha(package / "manifest.json"),
        "canonical_anchor_sha256": sha_text(canonical_anchor),
        "old_anchor_sha256": sorted(sha_text(value) for value in old_prefixes),
        "targets_rewritten": 0,
        "training_authorized": False,
        "counts": {
            "candidates": len(risks),
            "by_risk": dict(Counter(risk for row in risks for risk in row["risks"])),
        },
        "candidates": risks,
    }
    (output / "anchor_dependency_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    pending_dependencies = sum(
        row["decision"] == "needs_anchor_dependency_review" for row in risks
    )
    rejected_dependencies = [row["sample_id"] for row in risks if row["decision"] == "rejected"]
    if rejected_dependencies:
        raise ValueError(
            "Source package still contains dependency-rejected rows: "
            + ", ".join(rejected_dependencies[:5])
        )
    manifest = {
        "dataset_id": output.name,
        "status": (
            "rerendered_anchor_dependency_review_complete"
            if pending_dependencies == 0 else "rerendered_pending_anchor_dependency_review"
        ),
        "training_authorized": False,
        "source_package": str(package.resolve()),
        "source_manifest_sha256": sha(package / "manifest.json"),
        "render_profile_id": "GAME_REPLY_CONTEXT_V1",
        "role_anchor_sha256": sha_text(canonical_anchor),
        "old_role_anchor_sha256": sorted(sha_text(value) for value in old_prefixes),
        "counts": counts,
        "targets_rewritten": 0,
        "messages_except_system_changed": False,
        "anchor_dependency_review_complete": pending_dependencies == 0,
        "production_input_alignment_verified": False,
        "files": {},
    }
    manifest["files"] = {path.name: sha(path) for path in output.iterdir()}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**manifest, "dependency_candidates": len(risks), "risk_counts": report["counts"]["by_risk"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-review", type=Path)
    args = parser.parse_args()
    print(json.dumps(rerender(args.package, args.output, args.dependency_review), ensure_ascii=True, indent=2))
