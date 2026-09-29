"""Create an isolated, reversible presentation-only candidate from Training V2."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "AiPeople" / "runtime" / "world_mind"))
from compact_game_reply_context import (  # noqa: E402
    decode_system_v2, encode_system_v2,
)

SOURCE = ROOT / "AiPeopleCreate" / "训练数据V2"
BACKUP = ROOT / "AiPeopleCreate" / "训练数据V2归档" / "20260922_优化前2611条完整备份"
DEST = ROOT / "AiPeopleCreate" / "优化数据V2"


def sha_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                raise ValueError(f"Empty JSONL line: {path}")
            yield json.loads(line)


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build() -> None:
    if DEST.exists():
        raise FileExistsError(f"Refusing to overwrite {DEST}")
    source_files = sorted(p.relative_to(SOURCE) for p in SOURCE.rglob("*") if p.is_file())
    backup_files = sorted(p.relative_to(BACKUP) for p in BACKUP.rglob("*") if p.is_file())
    if source_files != backup_files or len(source_files) != 15:
        raise ValueError("Source and backup inventory mismatch")
    for rel in source_files:
        if sha_file(SOURCE / rel) != sha_file(BACKUP / rel):
            raise ValueError(f"Backup mismatch: {rel}")
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    if sha_file(SOURCE / "train.jsonl") != manifest["train_sha256"] or sha_file(SOURCE / "metadata.jsonl") != manifest["metadata_sha256"]:
        raise ValueError("V2 manifest hash mismatch")

    records = {}
    provenance_files = sorted((SOURCE / "audit").rglob("accepted_source_records.json"))
    for path in provenance_files:
        for record in json.loads(path.read_text(encoding="utf-8")):
            key = record["sample_id"]
            if key in records:
                raise ValueError(f"Duplicate source record: {key}")
            records[key] = digest(record)

    prepared = []
    source_ids = set()
    pair_hashes = set()
    counters = Counter()
    for index, (pair, meta) in enumerate(zip(rows(SOURCE / "train.jsonl"), rows(SOURCE / "metadata.jsonl")), 1):
        if meta["line"] != index or meta["sample_id"] in source_ids:
            raise ValueError(f"Invalid metadata ordering at {index}")
        sid = meta["sample_id"]
        source_ids.add(sid)
        if records.get(sid) != meta["source_record_sha256"]:
            raise ValueError(f"Source record mismatch: {sid}")
        old_pair_hash = digest(pair)
        if old_pair_hash != meta["training_pair_sha256"] or digest(pair["prompt"]) != meta["prompt_sha256"]:
            raise ValueError(f"Original pair/prompt hash mismatch: {sid}")
        if old_pair_hash in pair_hashes:
            raise ValueError(f"Duplicate original pair: {sid}")
        pair_hashes.add(old_pair_hash)
        if pair["prompt"][0]["role"] != "system":
            raise ValueError(f"Missing system: {sid}")
        old_system = pair["prompt"][0]["content"]
        new_system = encode_system_v2(old_system)
        if decode_system_v2(new_system).encode("utf-8") != old_system.encode("utf-8"):
            raise ValueError(f"Nonreversible system: {sid}")
        new_pair = json.loads(json.dumps(pair, ensure_ascii=False))
        new_pair["prompt"][0]["content"] = new_system
        if new_pair["prompt"][1:] != pair["prompt"][1:] or new_pair["completion"] != pair["completion"]:
            raise ValueError(f"Changed dialogue or answer: {sid}")
        new_meta = dict(meta)
        new_meta["original_training_pair_sha256"] = old_pair_hash
        new_meta["original_prompt_sha256"] = meta["prompt_sha256"]
        new_meta["training_pair_sha256"] = digest(new_pair)
        new_meta["prompt_sha256"] = digest(new_pair["prompt"])
        counters["original_system_utf8_bytes"] += len(old_system.encode("utf-8"))
        counters["optimized_system_utf8_bytes"] += len(new_system.encode("utf-8"))
        prepared.append((new_pair, new_meta))
    if len(prepared) != manifest["released_count"] or len(records) != len(prepared):
        raise ValueError(f"Row/source count mismatch: {len(prepared)} / {len(records)}")

    DEST.mkdir()
    with (DEST / "train.jsonl").open("w", encoding="utf-8", newline="\n") as train, (DEST / "metadata.jsonl").open("w", encoding="utf-8", newline="\n") as metadata:
        for pair, meta in prepared:
            train.write(json.dumps(pair, ensure_ascii=False) + "\n")
            metadata.write(json.dumps(meta, ensure_ascii=False) + "\n")
    audit = {
        "rows_verified": len(prepared),
        "source_records_verified": len(records),
        "system_byte_roundtrips": len(prepared),
        "dialogue_and_completion_unchanged": len(prepared),
        "backup_files_sha256_verified": len(source_files),
        "original_system_utf8_bytes": counters["original_system_utf8_bytes"],
        "optimized_system_utf8_bytes": counters["optimized_system_utf8_bytes"],
    }
    write_json(DEST / "roundtrip_audit.json", audit)
    candidate_manifest = {
        "dataset": "优化数据V2", "status": "experimental_candidate",
        "released_count": len(prepared), "training_authorized": False,
        "runtime_alignment_pending": True, "trained": False,
        "transform": "reversible_grouped_provenance_and_fixed_state_presentation_v2",
        "source_manifest_sha256": sha_file(SOURCE / "manifest.json"),
        "backup_manifest_sha256": sha_file(BACKUP / "manifest.json"),
        "source_train_sha256": manifest["train_sha256"],
        "source_metadata_sha256": manifest["metadata_sha256"],
        "train_sha256": sha_file(DEST / "train.jsonl"),
        "metadata_sha256": sha_file(DEST / "metadata.jsonl"),
        "roundtrip_audit_sha256": sha_file(DEST / "roundtrip_audit.json"),
    }
    write_json(DEST / "manifest.json", candidate_manifest)
    (DEST / "README.md").write_text(
        "# 优化数据V2（实验候选）\n\n"
        "基于训练数据V2的2611条训练对，仅重排 system 内重复来源和未知说明，并对固定设定与本轮状态分层。"
        "原训练数据和完整备份未修改。每条 system 可以字节级还原，其他历史消息与目标回复保持不变。\n\n"
        "尚未接入正式运行时、未建立新训练包、未授权训练。旧9216训练包仍对应原格式，不能直接混用。"
        "模型实际 token 节省需单独审计；字节数不是 token 数。\n",
        encoding="utf-8",
    )
    print(json.dumps({"path": str(DEST), "audit": audit, "manifest": candidate_manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build()
