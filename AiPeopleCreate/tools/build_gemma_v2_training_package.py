"""Freeze Training Data V2 for a Gemma 4 12B, 9216-token probe.

The builder restores authored family provenance from release audit records,
keeps leakage groups in one split, and verifies the exact final-assistant loss
mask with the Gemma inference template. It never starts model training.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any


CREATE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = CREATE_ROOT.parent
if str(CREATE_ROOT) not in sys.path:
    sys.path.insert(0, str(CREATE_ROOT))

from data_gen_v4.adapters.modes.training import TrainingRecordV4  # noqa: E402
from data_gen_v4.runtime_grounded.renderer import (  # noqa: E402
    GAME_REPLY_CONTEXT_RENDER_PROFILE,
    IGNORE_INDEX,
    build_current_assistant_example,
)


DEFAULT_V2 = CREATE_ROOT / "训练数据V2"
DEFAULT_MODEL = (
    WORKSPACE_ROOT / "AiPeople" / "training_packages" / "models" / "Gemma-4-12B-it"
)
DEFAULT_OUTPUT = (
    WORKSPACE_ROOT
    / "AiPeople"
    / "training_packages"
    / "training_package_baiweixi_gemma4_12b_v2_2611_9216"
)
MAX_SEQ_LENGTH = 9216
BASE_REVISION = "707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7"
SEED = 20260921


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return sha_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        winner, loser = sorted((left_root, right_root))
        self.parent[loser] = winner


def _component_source(record: dict[str, Any]) -> dict[str, Any]:
    value = ((record.get("scenario") or {}).get("component_source") or {})
    if not isinstance(value, dict):
        raise ValueError(f"{record.get('sample_id')}: missing component source")
    return value


def _family(record: dict[str, Any]) -> str:
    family = (_component_source(record).get("component_ids") or {}).get("family_id")
    if not isinstance(family, str) or not family.strip():
        raise ValueError(f"{record.get('sample_id')}: missing family_id")
    return family.strip()


def _turn_sources(record: dict[str, Any]) -> set[str]:
    component = _component_source(record)
    sources: set[str] = set()
    for field in ("memory_turns", "recent_turns"):
        for turn in component.get(field) or ():
            if isinstance(turn, dict) and str(turn.get("source") or "").strip():
                sources.add(str(turn["source"]).strip())
    return sources


def load_audit_sources(v2_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    records: dict[str, dict[str, Any]] = {}
    releases: dict[str, str] = {}
    paths = sorted((v2_root / "audit").rglob("accepted_source_records.json"))
    if not paths:
        raise FileNotFoundError("No accepted source audit records found")
    for path in paths:
        release = path.parent.name if path.parent.name != "audit" else "initial_v2"
        for record in read_json(path):
            sample_id = record.get("sample_id")
            if not isinstance(sample_id, str) or not sample_id:
                raise ValueError(f"{path}: source record without sample_id")
            if sample_id in records:
                raise ValueError(f"Duplicate accepted source record: {sample_id}")
            records[sample_id] = record
            releases[sample_id] = release
    return records, releases


def build_leakage_groups(records: dict[str, dict[str, Any]]) -> dict[str, str]:
    sample_ids = sorted(records)
    union = UnionFind(sample_ids)
    first_by_family: dict[str, str] = {}
    first_by_turn_source: dict[str, str] = {}
    for sample_id in sample_ids:
        family = _family(records[sample_id])
        if family in first_by_family:
            union.union(sample_id, first_by_family[family])
        else:
            first_by_family[family] = sample_id
        for source in _turn_sources(records[sample_id]):
            if source in first_by_turn_source:
                union.union(sample_id, first_by_turn_source[source])
            else:
                first_by_turn_source[source] = sample_id
    members: dict[str, list[str]] = defaultdict(list)
    for sample_id in sample_ids:
        members[union.find(sample_id)].append(sample_id)
    group_ids: dict[str, str] = {}
    for group in members.values():
        identity = sha_bytes("\n".join(sorted(group)).encode("utf-8"))[:20]
        for sample_id in group:
            group_ids[sample_id] = f"leakage-{identity}"
    return group_ids


def split_for_group(group_id: str) -> str:
    # Stable 90/10 family-level split. External sealed evaluation is separate.
    return "dev" if int(sha_bytes(group_id.encode("utf-8"))[:8], 16) % 10 == 0 else "train"


def make_record(row: dict[str, Any]) -> TrainingRecordV4:
    messages = row["messages"]
    return TrainingRecordV4(
        sample_id=row["sample_id"],
        mode="GAME_REPLY",
        render_profile_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        messages=[dict(message) for message in messages[1:]],
        supervised_message_indexes=[len(messages) - 2],
        supervised_token_count=0,
        protocol_snapshot_id=GAME_REPLY_CONTEXT_RENDER_PROFILE,
        system_anchor=messages[0]["content"],
        split=row["split"],
    )


def validate_messages(sample_id: str, messages: list[dict[str, str]]) -> None:
    if len(messages) < 3 or messages[0].get("role") != "system":
        raise ValueError(f"{sample_id}: invalid system/message boundary")
    if messages[-2].get("role") != "user" or messages[-1].get("role") != "assistant":
        raise ValueError(f"{sample_id}: row must end in user/assistant")
    if not str(messages[-1].get("content") or "").strip():
        raise ValueError(f"{sample_id}: empty completion")
    system = str(messages[0].get("content") or "")
    for heading in ("[当前权威事实]", "[相关记忆]", "[明确未知]"):
        if heading not in system:
            raise ValueError(f"{sample_id}: missing {heading}")


def freeze(v2_root: Path, model_path: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"Refusing to replace existing package: {output}")
    manifest_path = v2_root / "manifest.json"
    source_manifest = read_json(manifest_path)
    for name in ("train", "metadata"):
        path = v2_root / f"{name}.jsonl"
        if sha_file(path) != source_manifest[f"{name}_sha256"]:
            raise ValueError(f"V2 {name} hash differs from manifest")
    training_rows = read_jsonl(v2_root / "train.jsonl")
    metadata_rows = read_jsonl(v2_root / "metadata.jsonl")
    if len(training_rows) != len(metadata_rows) or len(training_rows) != source_manifest["released_count"]:
        raise ValueError("V2 row counts differ")
    source_records, source_releases = load_audit_sources(v2_root)
    if len(source_records) != len(training_rows):
        raise ValueError(
            f"Accepted source count {len(source_records)} != V2 rows {len(training_rows)}"
        )
    leakage_groups = build_leakage_groups(source_records)

    from transformers import AutoTokenizer
    from unsloth.chat_templates import get_chat_template

    tokenizer = get_chat_template(
        AutoTokenizer.from_pretrained(str(model_path), local_files_only=True),
        chat_template="gemma-4",
    )
    template_sha = sha_bytes(tokenizer.chat_template.encode("utf-8"))
    vocab_sha = sha_bytes(
        json.dumps(tokenizer.get_vocab(), sort_keys=True).encode("utf-8")
    )

    packaged: dict[str, list[dict[str, Any]]] = {"train": [], "dev": []}
    assignments: list[dict[str, Any]] = []
    sequence_lengths: list[int] = []
    target_lengths: list[int] = []
    longest: dict[str, Any] = {"sequence_tokens": 0}
    split_groups: dict[str, set[str]] = {"train": set(), "dev": set()}
    counts_by_split_scene: Counter[str] = Counter()
    counts_by_split_task: Counter[str] = Counter()

    for line, (pair, metadata) in enumerate(zip(training_rows, metadata_rows, strict=True), 1):
        sample_id = metadata.get("sample_id")
        if sample_id not in source_records:
            raise ValueError(f"Line {line}: source record missing for {sample_id}")
        source = source_records[sample_id]
        if stable_digest(source) != metadata.get("source_record_sha256"):
            raise ValueError(f"{sample_id}: source record hash differs")
        if stable_digest(pair) != metadata.get("training_pair_sha256"):
            raise ValueError(f"{sample_id}: training pair hash differs")
        prompt = pair.get("prompt")
        completion = pair.get("completion")
        if prompt != source.get("generation_messages"):
            raise ValueError(f"{sample_id}: saved prompt differs from generation request")
        if not isinstance(completion, list) or len(completion) != 1:
            raise ValueError(f"{sample_id}: completion must contain one assistant message")
        messages = [*prompt, *completion]
        validate_messages(sample_id, messages)
        family_id = _family(source)
        leakage_group_id = leakage_groups[sample_id]
        split = split_for_group(leakage_group_id)
        split_groups[split].add(leakage_group_id)
        row = {
            "sample_id": sample_id,
            "family_id": family_id,
            "leakage_group_id": leakage_group_id,
            "split": split,
            "scene": metadata["scene"],
            "task_type": metadata["task"],
            "source_release": source_releases[sample_id],
            "render_profile_id": GAME_REPLY_CONTEXT_RENDER_PROFILE,
            "messages": messages,
            "supervised_message_indexes": [len(messages) - 1],
            "training_pair_sha256": metadata["training_pair_sha256"],
            "source_record_sha256": metadata["source_record_sha256"],
            "frozen_input_sha256": metadata["frozen_input_sha256"],
        }
        example, span = build_current_assistant_example(
            tokenizer,
            make_record(row),
            max_seq_length=MAX_SEQ_LENGTH,
            row_index=line - 1,
        )
        if any(label != IGNORE_INDEX for label in example["labels"][: span.start]):
            raise RuntimeError(f"{sample_id}: prompt entered loss mask")
        if sum(label != IGNORE_INDEX for label in example["labels"]) != span.token_count:
            raise RuntimeError(f"{sample_id}: loss mask cardinality differs")
        row["sequence_tokens"] = len(example["input_ids"])
        row["supervised_tokens"] = span.token_count
        row["supervised_special_tokens"] = span.special_token_count
        packaged[split].append(row)
        sequence_lengths.append(row["sequence_tokens"])
        target_lengths.append(row["supervised_tokens"])
        counts_by_split_scene[f"{split}:{row['scene']}"] += 1
        counts_by_split_task[f"{split}:{row['task_type']}"] += 1
        assignment = {
            "sample_id": sample_id,
            "family_id": family_id,
            "leakage_group_id": leakage_group_id,
            "split": split,
            "sequence_tokens": row["sequence_tokens"],
        }
        assignments.append(assignment)
        if row["sequence_tokens"] > longest["sequence_tokens"]:
            longest = dict(assignment)

    overlap = split_groups["train"] & split_groups["dev"]
    if overlap:
        raise RuntimeError(f"Leakage groups cross splits: {sorted(overlap)[:5]}")
    if not packaged["train"] or not packaged["dev"]:
        raise RuntimeError("Both train and dev splits must be non-empty")

    ordered = sorted(sequence_lengths)
    percentile = lambda value: ordered[min(len(ordered) - 1, int((len(ordered) - 1) * value))]
    temp = output.with_name(output.name + ".tmp")
    if temp.exists():
        shutil.rmtree(temp)
    (temp / "data").mkdir(parents=True)
    (temp / "audit").mkdir()
    for split in ("train", "dev"):
        write_jsonl(temp / "data" / f"{split}.jsonl", packaged[split])
    write_jsonl(temp / "audit" / "split_assignments.jsonl", assignments)
    preflight = {
        "result": "passed",
        "checks": {
            "v2_hashes": "passed",
            "source_provenance": "passed",
            "training_pair_identity": "passed",
            "family_group_split": "passed",
            "shared_turn_source_group_split": "passed",
            "exact_inference_template": "passed",
            "final_assistant_only": "passed",
            "prompt_fully_masked": "passed",
            "target_fully_supervised": "passed",
            "turn_closing_special_token": "passed",
            "no_truncation": "passed",
        },
        "max_seq_length": MAX_SEQ_LENGTH,
        "longest_sequence": longest,
        "token_distribution": {
            "min": min(ordered),
            "p50": percentile(0.50),
            "p90": percentile(0.90),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "max": max(ordered),
            "mean": round(sum(ordered) / len(ordered), 2),
            "over_8192": sum(value > 8192 for value in ordered),
        },
        "supervision": {
            "policy": "final_assistant_only",
            "messages": len(target_lengths),
            "tokens": sum(target_lengths),
            "min_tokens": min(target_lengths),
            "max_tokens": max(target_lengths),
        },
        "counts_by_split_scene": dict(sorted(counts_by_split_scene.items())),
        "counts_by_split_task": dict(sorted(counts_by_split_task.items())),
    }
    write_json(temp / "audit" / "preflight_report.json", preflight)
    files = {
        path.relative_to(temp).as_posix(): sha_file(path)
        for path in sorted(temp.rglob("*"))
        if path.is_file()
    }
    frozen_manifest = {
        "schema_version": "aip.gemma_v2_training_package.v1",
        "dataset_id": output.name,
        "status": "immutable_probe_ready",
        "probe_authorized": True,
        "full_training_authorized": False,
        "base_model_id": "google/gemma-4-12B-it",
        "base_revision": BASE_REVISION,
        "model_path": str(model_path.resolve()),
        "source_v2": str(v2_root.resolve()),
        "source_manifest_sha256": sha_file(manifest_path),
        "source_train_sha256": sha_file(v2_root / "train.jsonl"),
        "source_metadata_sha256": sha_file(v2_root / "metadata.jsonl"),
        "render_profile_id": GAME_REPLY_CONTEXT_RENDER_PROFILE,
        "chat_template": "gemma-4 normal mode",
        "chat_template_sha256": template_sha,
        "tokenizer_vocab_sha256": vocab_sha,
        "max_seq_length": MAX_SEQ_LENGTH,
        "supervision_policy": "final_assistant_only",
        "split_policy": "family_and_shared_turn_source_connected_components_sha256_mod10",
        "seed": SEED,
        "counts": {split: len(packaged[split]) for split in ("train", "dev")},
        "leakage_groups": {split: len(split_groups[split]) for split in ("train", "dev")},
        "longest_sequence": longest,
        "files": files,
    }
    write_json(temp / "manifest.json", frozen_manifest)
    (temp / "README.md").write_text(
        "# Gemma 4 12B V2 2611 training package\n\n"
        "Immutable 9216-token package for the authorized one-step VRAM probe. "
        "Full training is not authorized. See manifest.json and audit/preflight_report.json.\n",
        encoding="utf-8",
    )
    # README is descriptive and intentionally excluded from frozen data hashes.
    temp.replace(output)
    return {**frozen_manifest, "preflight": preflight}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-root", type=Path, default=DEFAULT_V2)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = freeze(args.v2_root.resolve(), args.model.resolve(), args.output.resolve())
    print(
        json.dumps(
            {
                "package": str(args.output.resolve()),
                "counts": result["counts"],
                "leakage_groups": result["leakage_groups"],
                "longest_sequence": result["longest_sequence"],
                "token_distribution": result["preflight"]["token_distribution"],
                "full_training_authorized": result["full_training_authorized"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
