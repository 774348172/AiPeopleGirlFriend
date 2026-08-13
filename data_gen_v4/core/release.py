"""原子发布（阶段 4 D-4，设计 §15.4）。

DatasetRelease / manifest：对齐 AI 程序侧 dataset_manifest.schema.json 契约
（files 每 split 带 bytes/sha256/records、freeze.manifest_sha256/immutable），
生成器扩展字段（package_lock_hash/sample_ids/分布/dedup/split/gate summary）。

事务语义（G4：release 无 manifest 不产出）：
1. manifest 写临时文件 → rename 原子替换（成功 = manifest 存在）；
2. 全部成功才写 sqlite release_created 事件；
3. 事件失败 → manifest 回滚删除 → 无 release。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .records import LineageHeaderV4, RunEvent


def canonical_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ReleaseInput:
    """发布输入（publish 工具组装）。"""
    dataset_id: str
    character_id: str
    dataset_family: str
    split_policy: dict[str, Any]
    samples: list[dict[str, Any]]  # 每条含 sample_id/split_anchor_ids/text/task_type/family_id/...
    files_by_split: dict[str, list[Path]]  # split -> 训练文件
    package_lock_hash: str
    profile_id: str
    mode: str
    gate_summary: dict[str, Any] = field(default_factory=dict)
    dedup_report: dict[str, Any] = field(default_factory=dict)
    split_report: dict[str, Any] = field(default_factory=dict)
    contamination_report: dict[str, Any] = field(default_factory=dict)
    contract: dict[str, Any] = field(default_factory=dict)
    generator_version: str = "0.1.0"


def build_manifest(input_: ReleaseInput) -> dict[str, Any]:
    """组装 manifest（不含 freeze.manifest_sha256，由 publish 计算）。"""
    from collections import Counter

    sample_ids: dict[str, list[str]] = {}
    for sample in input_.samples:
        sample_ids.setdefault(sample.get("_split", "train"), []).append(sample["sample_id"])
    files = []
    for split, paths in sorted(input_.files_by_split.items()):
        for path in paths:
            raw = path.read_bytes()
            files.append(
                {
                    "split": split,
                    "path": str(path),
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "records": sum(1 for _ in raw.splitlines() if _.strip()),
                }
            )
    return {
        "dataset_id": input_.dataset_id,
        "schema_version": "aip.dataset_manifest.v1",
        "dataset_family": input_.dataset_family,
        "release_scope": "training",
        "character_id": input_.character_id,
        "status": "frozen",
        "contract": input_.contract,
        "generator": {
            "generator_id": "data_gen_v4",
            "version": input_.generator_version,
            "config_sha256": input_.package_lock_hash,
        },
        "split_policy": input_.split_policy,
        "package_lock_hash": input_.package_lock_hash,
        "profile_id": input_.profile_id,
        "mode": input_.mode,
        "sample_ids": sample_ids,
        "distributions": {
            "evidence_state": dict(Counter(s.get("evidence_state", "") for s in input_.samples)),
            "desired_policy": dict(Counter(s.get("desired_policy", "") for s in input_.samples)),
            "family": dict(Counter(s.get("family_id", "") for s in input_.samples)),
            "task_type": dict(Counter(s.get("task_type", "") for s in input_.samples)),
        },
        "files": files,
        "dedup_cluster_report": input_.dedup_report,
        "split_anchor_report": input_.split_report,
        "contamination_report": input_.contamination_report,
        "gate_summary": input_.gate_summary,
        "freeze": {
            "hash_algorithm": "sha256",
            "manifest_hash_mode": "canonical_json_with_null_self",
            "immutable": True,
        },
    }


def publish_release(
    manifest: dict[str, Any],
    manifest_path: Path,
    sink: Any,
    *,
    run_id: str,
    header: LineageHeaderV4,
) -> dict[str, Any]:
    """原子发布：manifest 临时文件 + rename → 成功才写 release_created 事件。

    任一步失败 → manifest 回滚删除、无 release（G4：release 无 manifest 不产出）。
    """
    # 计算 manifest_sha256（自指除外：freeze.manifest_sha256 本身）
    freeze = manifest["freeze"]
    freeze["manifest_sha256"] = (
        "sha256:"
        + hashlib.sha256(
            canonical_json({k: v for k, v in manifest.items() if k != "freeze"})
            .encode("utf-8")
        ).hexdigest()
    )
    tmp = manifest_path.with_name(manifest_path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(manifest_path)  # 原子替换：成功 = manifest 存在
    event = RunEvent(
        header=header.with_record(record_type="run"),
        event_name="release_created",
    )
    try:
        sink.append(
            event,
            json.dumps([run_id, "release", manifest["dataset_id"]], separators=(",", ":")),
        )
    except Exception:
        manifest_path.unlink(missing_ok=True)  # 事件失败 → 回滚 manifest
        raise
    return manifest
