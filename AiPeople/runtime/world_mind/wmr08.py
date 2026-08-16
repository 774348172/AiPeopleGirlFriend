from __future__ import annotations

import hashlib
import json
import math
import platform
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .memory_proposer import MEMORY_PROPOSE, MEMORY_PROPOSE_SYSTEM_BOUNDARY
from .model_gateway import (
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    GAME_REPLY,
    MIND_PATCH_V2,
    POST_REPLY_WORLD_MIND_RECONCILE,
    WORLD_CONTINUITY_REVIEW,
)
from .model_payloads import MODE_SYSTEM_BOUNDARIES
from .real_model_gateway import DEFAULT_MODE_PROFILES
from .short_protocol import B1_SYSTEM_BOUNDARY, M2_SYSTEM_BOUNDARY

WMR08_MODES = (
    MIND_PATCH_V2,
    GAME_REPLY,
    WORLD_CONTINUITY_REVIEW,
    POST_REPLY_WORLD_MIND_RECONCILE,
    FIVE_MINUTE_WORLD_MIND_RECONCILE,
    MEMORY_PROPOSE,
)
WMR08_SCHEMA_FILES = {
    MIND_PATCH_V2: "mind_patch_m2.schema.json",
    GAME_REPLY: "game_reply_plain_text_v2.schema.json",
    WORLD_CONTINUITY_REVIEW: "world_continuity_review_v1.schema.json",
    POST_REPLY_WORLD_MIND_RECONCILE: "background_mind_patch_b1.schema.json",
    FIVE_MINUTE_WORLD_MIND_RECONCILE: "background_mind_patch_b1.schema.json",
    MEMORY_PROPOSE: "heroine_memory_r2.schema.json",
}


def nearest_rank_summary(values: Iterable[float]) -> dict[str, float | int | None]:
    samples = sorted(float(value) for value in values)
    if not samples:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None}
    return {
        "count": len(samples),
        "min": samples[0],
        "p50": _nearest_rank(samples, 0.50),
        "p95": _nearest_rank(samples, 0.95),
        "max": samples[-1],
    }


def build_engineering_freeze_manifest(
    root: Path,
    *,
    probe_model: Mapping[str, object],
) -> dict[str, Any]:
    root = Path(root).resolve()
    schema_root = root / "runtime" / "schemas"
    migration_root = root / "runtime" / "migrations"
    character_root = root / "人物设定" / "白未晞"
    evaluation_contract = (
        root
        / "eval"
        / "world_mind_p0"
        / "wmr08_engineering_prefreeze_contract_v1.json"
    )
    schemas = {
        mode: _file_record(schema_root / filename)
        for mode, filename in WMR08_SCHEMA_FILES.items()
    }
    runtime_schemas = [
        _file_record(path)
        for path in sorted(schema_root.glob("*.json"), key=lambda item: item.name)
    ]
    mode_prompts = {
        **MODE_SYSTEM_BOUNDARIES,
        MIND_PATCH_V2: M2_SYSTEM_BOUNDARY,
        POST_REPLY_WORLD_MIND_RECONCILE: B1_SYSTEM_BOUNDARY,
        FIVE_MINUTE_WORLD_MIND_RECONCILE: B1_SYSTEM_BOUNDARY,
        MEMORY_PROPOSE: MEMORY_PROPOSE_SYSTEM_BOUNDARY,
    }
    prompts = {
        mode: {
            "sha256": _text_sha256(mode_prompts[mode]),
            "length": len(mode_prompts[mode]),
        }
        for mode in WMR08_MODES
    }
    profiles = {}
    for mode in WMR08_MODES:
        if mode == MEMORY_PROPOSE:
            profiles[mode] = {
                "max_tokens": 180,
                "temperature": 0.15,
                "top_p": 0.85,
                "repeat_penalty": 1.05,
                "timeout_seconds": 50.0,
                "retries": 0,
            }
            continue
        profile = DEFAULT_MODE_PROFILES[mode]
        profiles[mode] = {
            "max_tokens": profile.max_tokens,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "repeat_penalty": profile.repeat_penalty,
            "timeout_seconds": profile.timeout_seconds,
            "retries": profile.retries,
        }
    migrations = [
        _file_record(path)
        for path in sorted(migration_root.glob("[0-9][0-9][0-9]_*.sql"))
        if int(path.name.split("_", 1)[0]) >= 8
    ]
    character_files = [
        _file_record(path)
        for path in sorted(character_root.iterdir(), key=lambda item: item.name)
        if path.is_file()
    ]
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "scope": "wmr08_engineering_prefreeze",
        "not_final_p0": True,
        "probe_model": dict(probe_model),
        "evaluation_contract": _file_record(evaluation_contract),
        "mode_prompts": prompts,
        "mode_schemas": schemas,
        "runtime_schemas": runtime_schemas,
        "mode_profiles": profiles,
        "database_migrations": migrations,
        "character_package": {
            "character_id": "baiweixi",
            "files": character_files,
        },
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    return manifest


def evaluate_engineering_prefreeze(report: Mapping[str, Any]) -> dict[str, Any]:
    successful_modes = set(report.get("successful_modes", ()))
    missing_modes = sorted(set(WMR08_MODES) - successful_modes)
    database = report.get("database")
    database_ok = isinstance(database, Mapping) and (
        database.get("model_decisions", 0) >= 1
        and database.get("reconcile_jobs_failed") == 0
        and database.get("reconcile_jobs_completed", 0) >= 2
        and database.get("memory_proposals", 0) >= 1
        and database.get("r1_memory_jobs_failed") == 0
    )
    turns = report.get("turns")
    turns_ok = (
        isinstance(turns, list)
        and bool(turns)
        and all(item.get("result_type") == "Completed" for item in turns)
    )
    freeze_manifest = report.get("freeze_manifest")
    freeze_ok = isinstance(freeze_manifest, Mapping) and bool(
        freeze_manifest.get("manifest_sha256")
    )
    engineering_passed = not missing_modes and database_ok and turns_ok and freeze_ok
    return {
        "engineering_chain_passed": engineering_passed,
        "engineering_gate": "passed" if engineering_passed else "failed",
        "missing_modes": missing_modes,
        "formal_p0_gate": "deferred",
        "formal_p0_blockers": [
            "白未晞正式回复模型工件未接入",
            "白未晞角色质量评测未执行",
            "目标发布栈正式性能与一小时稳定性未执行",
        ],
    }


def environment_record() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
    }


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _nearest_rank(samples: list[float], percentile: float) -> float:
    return samples[max(0, math.ceil(percentile * len(samples)) - 1)]


def _file_record(path: Path) -> dict[str, object]:
    content = path.read_bytes()
    return {
        "path": path.name,
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
