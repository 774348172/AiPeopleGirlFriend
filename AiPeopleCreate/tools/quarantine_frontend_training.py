"""Apply evidence-bound review decisions without rewriting training targets."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


SPLITS = ("train", "dev", "test")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify(splits: dict[str, list[dict]], decisions: list[dict]) -> list[dict]:
    indexed = {}
    for split, rows in splits.items():
        for line, row in enumerate(rows, 1):
            sid = row["sample_id"]
            if sid in indexed:
                raise ValueError(f"Duplicate sample ID: {sid}")
            if not row.get("family_id") or not row.get("messages"):
                raise ValueError(f"Missing family or messages: {sid}")
            indexed[sid] = (split, line, row)
    reviewed = {}
    rejected = {}
    for decision in decisions:
        sid = decision["sample_id"]
        if sid in reviewed or sid not in indexed:
            raise ValueError(f"Duplicate or missing decision: {sid}")
        row = indexed[sid][2]
        if "source_line" in decision and decision["source_line"] != indexed[sid][1]:
            raise ValueError(f"Source line changed: {sid}")
        expected_target = decision.get("expected_target", row["messages"][-1]["content"])
        if row["messages"][-1]["role"] != "assistant" or row["messages"][-1]["content"] != expected_target:
            raise ValueError(f"Target changed: {sid}")
        if not decision.get("reason") or ("expected_target" not in decision and "source_line" not in decision):
            raise ValueError(f"Missing review binding: {sid}")
        if decision.get("evidence_quote") and not any(decision["evidence_quote"] in m["content"] for m in row["messages"][:-1]):
            raise ValueError(f"Evidence changed: {sid}")
        status = decision.get("decision", "rejected")
        if status not in {"accepted", "rejected"}:
            raise ValueError(f"Unsupported decision: {sid}")
        reviewed[sid] = decision
        if status == "rejected":
            rejected[sid] = decision
    records = []
    for sid, (split, line, row) in indexed.items():
        causes = set()
        # Exact reviewed bad utterances in retained history are quarantined too.
        for m in row["messages"][:-1]:
            if m["role"] == "assistant":
                causes.update(
                    key for key, decision in rejected.items()
                    if m["content"] == decision.get(
                        "expected_target", indexed[key][2]["messages"][-1]["content"]
                    )
                )
        if sid in reviewed and reviewed[sid].get("decision", "rejected") == "accepted":
            if any(key != sid for key in causes):
                bad_history = any(
                    m["role"] == "assistant" and
                    any(m["content"] == decision.get("expected_target", indexed[key][2]["messages"][-1]["content"])
                        for key, decision in rejected.items())
                    for m in row["messages"][:-1]
                )
                if bad_history:
                    raise ValueError(f"Accepted sample contains rejected assistant history: {sid}")
            status, reason = "accepted", reviewed[sid]["reason"]
        elif sid in rejected:
            status, reason = "rejected", rejected[sid]["reason"]
        elif causes:
            status, reason = "related_quarantine", "Contains an exact rejected assistant utterance in visible history; requires individual review."
        else:
            status, reason = "needs_review", "Not individually approved under the new standard."
        records.append({"sample_id": sid, "source_split": split, "source_line": line,
                        "status": status, "reason": reason, "related_rejections": sorted(causes), "row": row})
    return records


def build(source: Path, decisions_path: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output == source or output in source.parents or source in output.parents:
        raise ValueError("Output must be separate from source")
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    if decisions["standard_id"] != "reply_quality_v1_20260909":
        raise ValueError("Unsupported review standard")
    source_hashes = {}
    for split in SPLITS:
        filename = f"{split}.jsonl"
        source_hashes[filename] = sha(source / filename)
        if source_hashes[filename] != manifest["files"][filename]:
            raise ValueError(f"Source hash mismatch: {filename}")
    if source_hashes["train.jsonl"] != decisions["source_train_sha256"]:
        raise ValueError("Decision source hash mismatch")
    splits = {split: read_rows(source / f"{split}.jsonl") for split in SPLITS}
    records = classify(splits, decisions["decisions"])
    output.mkdir(parents=True)

    def write_rows(name, rows):
        (output / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    for split in SPLITS:
        write_rows(f"{split}.candidate.jsonl", (r["row"] for r in records if r["source_split"] == split and r["status"] == "needs_review"))
        write_rows(f"{split}.accepted.jsonl", (r["row"] for r in records if r["source_split"] == split and r["status"] == "accepted"))
    write_rows("quarantine.jsonl", (r for r in records if r["status"] != "needs_review"))
    write_rows("review_ledger.jsonl", ({k: v for k, v in r.items() if k != "row"} for r in records))
    (output / "decisions.json").write_bytes(decisions_path.read_bytes())
    remaining_review = sum(r["status"] == "needs_review" for r in records)
    review_complete = remaining_review == 0
    training_authorized = review_complete and bool(decisions.get("training_authorized", False))
    summary = {"dataset_id": output.name, "standard_id": decisions["standard_id"],
               "status": ("semantic_review_complete_training_not_authorized" if review_complete and not training_authorized
                          else "semantic_review_complete_training_authorized" if training_authorized
                          else "partial_cleanup_pending_semantic_review"),
               "training_authorized": training_authorized,
               "source": str(source), "source_files_sha256": source_hashes,
               "source_manifest_sha256": sha(source / "manifest.json"), "decisions_sha256": sha(decisions_path),
               "counts": dict(Counter(r["status"] for r in records)),
               "by_split": {split: dict(Counter(r["status"] for r in records if r["source_split"] == split)) for split in SPLITS},
               "human_ratings_changed": False, "targets_rewritten": 0,
               "full_semantic_review_complete": review_complete,
               "sealed_modified": False, "production_input_alignment_verified": False,
               "files": {p.name: sha(p) for p in output.iterdir()}}
    (output / "manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state_line = ("状态：全切分语义审核完成，但尚未授权训练。" if review_complete and not training_authorized
                  else "状态：全切分语义审核完成，已授权训练。" if training_authorized
                  else "状态：部分审核完成，禁止用于训练；未审核不等于合格。")
    lines = ["# 训练数据逐条审核进度", "", state_line, "",
             f"源包：`{source}`", "", "## 数量", "", "| 切分 | 已接受 | 明确拒收 | 关联隔离 | 待审核 |", "| --- | ---: | ---: | ---: | ---: |"]
    for split, counts in summary["by_split"].items():
        lines.append(f"| {split} | {counts.get('accepted', 0)} | {counts.get('rejected', 0)} | {counts.get('related_quarantine', 0)} | {counts.get('needs_review', 0)} |")
    lines.extend(["", "## 已核实错误", ""])
    for r in records:
        if r["status"] == "rejected":
            lines.extend([f"### {r['sample_id']}", "", f"源位置：{r['source_split']}.jsonl 第 {r['source_line']} 行。", "",
                          f"目标：{r['row']['messages'][-1]['content']}", "", r["reason"], ""])
    lines.extend(["## 使用边界", "", "原始包与工件不变。candidate 文件是待审核样本，accepted 文件是已逐条接受样本；当前均不是完整训练集。",
                  "只有包含已确认错误assistant原话的历史样本会关联隔离；旧family_id不再作为自动拒绝或隔离依据。",
                  "本次没有用关键词判好、没有自动改写答案、没有继承旧 approve 为新批准。",
                  "24条既有人审记录保留在原包，不因本次清理改变；仍需确认新标准和完整生产输入。",
                  "剩余样本必须逐条复审完整输入与监督目标，生产入口对齐后才可另行发布。"])
    (output / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if any(sha(source / name) != value for name, value in source_hashes.items()):
        raise ValueError("Source changed during cleanup")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.decisions, args.output), ensure_ascii=True, indent=2))
