import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from quarantine_frontend_training import build, classify


def row(sid, family, target="answer", history=None):
    return {"sample_id": sid, "family_id": family, "messages": [
        {"role": "system", "content": "unknown time"}, *(history or []),
        {"role": "user", "content": "question"}, {"role": "assistant", "content": target}],
        "supervised_message_indexes": [2 + len(history or [])]}


def decision():
    return {"sample_id": "bad", "expected_target": "Wednesday", "evidence_quote": "unknown time", "reason": "unsupported"}


def test_exact_evidence_and_history_quarantine_without_family_cascade():
    splits = {"train": [row("bad", "f", "Wednesday"), row("sibling", "f"), row("pending", "g")],
              "dev": [row("history", "h", history=[{"role": "assistant", "content": "Wednesday"}])], "test": []}
    original = copy.deepcopy(splits)
    result = classify(splits, [decision()])
    assert {r["sample_id"]: r["status"] for r in result} == {
        "bad": "rejected", "sibling": "needs_review", "pending": "needs_review", "history": "related_quarantine"}
    assert splits == original


@pytest.mark.parametrize("change", ["target", "evidence", "missing", "duplicate"])
def test_stale_decisions_rejected(change):
    splits = {"train": [row("bad", "f", "Wednesday")]}
    d = decision()
    if change == "target":
        d["expected_target"] = "Thursday"
    if change == "evidence":
        d["evidence_quote"] = "missing evidence"
    if change == "missing":
        d["sample_id"] = "absent"
    with pytest.raises(ValueError):
        classify(splits, [d, d] if change == "duplicate" else [d])


def test_compact_decision_is_bound_to_source_line_and_dataset_hash():
    compact = {"sample_id": "bad", "source_line": 1, "decision": "rejected", "reason": "reviewed"}
    assert classify({"train": [row("bad", "f", "Wednesday")]}, [compact])[0]["status"] == "rejected"
    compact["source_line"] = 2
    with pytest.raises(ValueError, match="Source line changed"):
        classify({"train": [row("bad", "f", "Wednesday")]}, [compact])


def test_compact_rejection_quarantines_exact_assistant_history():
    compact = {"sample_id": "bad", "source_line": 1, "decision": "rejected", "reason": "reviewed"}
    splits = {"train": [
        row("bad", "f", "bad target"),
        row("followup", "g", history=[{"role": "assistant", "content": "bad target"}]),
    ]}

    result = classify(splits, [compact])

    assert {r["sample_id"]: r["status"] for r in result} == {
        "bad": "rejected", "followup": "related_quarantine"}


def test_short_replies_and_advice_not_auto_rejected():
    rows = [row("name", "f", "是周老师说的。"), row("ack", "g", "嗯，那就先不去了。"),
            row("advice", "h", "我不建议把钥匙放外面。")]
    assert all(r["status"] == "needs_review" for r in classify({"train": rows}, []))


def test_family_id_never_overrides_individual_review_but_bad_history_does():
    good = {"sample_id": "good", "decision": "accepted", "expected_target": "answer",
            "evidence_quote": "unknown time", "reason": "full input reviewed"}
    splits = {"train": [row("bad", "f", "Wednesday"), row("good", "f")]}
    result = classify(splits, [decision(), good])
    assert {r["sample_id"]: r["status"] for r in result} == {"bad": "rejected", "good": "accepted"}
    splits["train"][1] = row("good", "g", history=[{"role": "assistant", "content": "Wednesday"}])
    with pytest.raises(ValueError, match="rejected assistant history"):
        classify(splits, [decision(), good])


def test_package_hashes_partition_and_training_block(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    for split, rows in {"train": [row("bad", "f", "Wednesday"), row("pending", "g")], "dev": [], "test": []}.items():
        data = "".join(json.dumps(r) + "\n" for r in rows).encode()
        (source / f"{split}.jsonl").write_bytes(data)
        files[f"{split}.jsonl"] = hashlib.sha256(data).hexdigest()
    (source / "manifest.json").write_text(json.dumps({"files": files}))
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"standard_id": "reply_quality_v1_20260909", "source_train_sha256": files["train.jsonl"], "decisions": [decision()]}))
    output = tmp_path / "output"
    result = build(source, decisions, output)
    assert result["training_authorized"] is False
    assert result["counts"] == {"rejected": 1, "needs_review": 1}
    assert not (output / "train.jsonl").exists()
    assert json.loads((output / "train.candidate.jsonl").read_text())["sample_id"] == "pending"
    assert hashlib.sha256((source / "train.jsonl").read_bytes()).hexdigest() == files["train.jsonl"]
    with pytest.raises(FileExistsError):
        build(source, decisions, output)
    (source / "train.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        build(source, decisions, tmp_path / "other")


def test_fully_reviewed_package_is_complete_but_not_authorized(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    files = {}
    rows_by_split = {"train": [row("good", "f")], "dev": [], "test": []}
    for split, rows in rows_by_split.items():
        data = "".join(json.dumps(value) + "\n" for value in rows).encode()
        (source / f"{split}.jsonl").write_bytes(data)
        files[f"{split}.jsonl"] = hashlib.sha256(data).hexdigest()
    (source / "manifest.json").write_text(json.dumps({"files": files}))
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "standard_id": "reply_quality_v1_20260909",
        "source_train_sha256": files["train.jsonl"],
        "training_authorized": False,
        "decisions": [{"sample_id": "good", "source_line": 1,
                       "decision": "accepted", "reason": "reviewed"}],
    }))

    result = build(source, decisions, tmp_path / "output")

    assert result["full_semantic_review_complete"] is True
    assert result["training_authorized"] is False
    assert result["status"] == "semantic_review_complete_training_not_authorized"
    assert (tmp_path / "output" / "train.candidate.jsonl").read_text() == ""
