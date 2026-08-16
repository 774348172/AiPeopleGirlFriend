from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ._reranker import (
    QWEN_RERANKER_MODEL_ID,
    RERANK_MAX_PAIR_TOKENS,
    RerankerIdentity,
)


RERANKER_MANIFEST_NAME = "reranker_manifest.json"


class RerankerAssetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalRerankerPackage:
    root: Path
    identity: RerankerIdentity
    activation_threshold: float
    relative_top_floor: float
    relative_top_margin: float
    weights: tuple[Path, ...]
    tokenizer_files: tuple[Path, ...]

    @classmethod
    def load(cls, root: str | Path) -> "LocalRerankerPackage":
        package_root = Path(root).resolve()
        if not package_root.is_dir():
            raise RerankerAssetError("local reranker package directory does not exist")
        manifest_path = package_root / RERANKER_MANIFEST_NAME
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RerankerAssetError("reranker manifest is missing or invalid") from error
        if manifest.get("schema_version") != 1:
            raise RerankerAssetError("reranker manifest schema_version must equal 1")
        if manifest.get("model_id") != QWEN_RERANKER_MODEL_ID:
            raise RerankerAssetError(
                f"reranker model_id must be {QWEN_RERANKER_MODEL_ID}"
            )
        revision = manifest.get("revision")
        deployment = manifest.get("deployment_profile")
        threshold = manifest.get("activation_threshold")
        relative_top_floor = manifest.get("relative_top_floor")
        relative_top_margin = manifest.get("relative_top_margin")
        if not isinstance(revision, str) or not revision.strip():
            raise RerankerAssetError("reranker revision must be explicit")
        if not isinstance(deployment, str) or not deployment.strip():
            raise RerankerAssetError("deployment_profile must be explicit")
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            raise RerankerAssetError("activation_threshold must be in [0, 1]")
        for value, name in (
            (relative_top_floor, "relative_top_floor"),
            (relative_top_margin, "relative_top_margin"),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise RerankerAssetError(f"{name} must be in [0, 1]")
        if manifest.get("maximum_pair_tokens") != RERANK_MAX_PAIR_TOKENS:
            raise RerankerAssetError("maximum_pair_tokens must equal 128")
        if manifest.get("output_contract") != "yes_no_logits_per_memory_id":
            raise RerankerAssetError("reranker output_contract is invalid")

        weights = _verify_files(package_root, manifest.get("weights"), "weights")
        tokenizer = _verify_files(
            package_root, manifest.get("tokenizer_files"), "tokenizer_files"
        )
        if not weights or not tokenizer:
            raise RerankerAssetError("reranker weights and tokenizer files are required")
        artifact_sha256 = _package_sha256(package_root, manifest)
        declared = manifest.get("artifact_sha256")
        if declared != artifact_sha256:
            raise RerankerAssetError("reranker package artifact SHA256 mismatch")
        return cls(
            root=package_root,
            identity=RerankerIdentity(
                model_id=QWEN_RERANKER_MODEL_ID,
                revision=revision,
                artifact_sha256=artifact_sha256,
                deployment_profile=deployment,
            ),
            activation_threshold=float(threshold),
            relative_top_floor=float(relative_top_floor),
            relative_top_margin=float(relative_top_margin),
            weights=weights,
            tokenizer_files=tokenizer,
        )


def canonical_package_sha256(root: str | Path) -> str:
    package_root = Path(root).resolve()
    manifest_path = package_root / RERANKER_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return _package_sha256(package_root, manifest)


def _verify_files(root: Path, values, field: str) -> tuple[Path, ...]:
    if not isinstance(values, list):
        raise RerankerAssetError(f"{field} must be an array")
    paths = []
    for item in values:
        if not isinstance(item, dict):
            raise RerankerAssetError(f"{field} entries must be objects")
        relative = item.get("path")
        declared_sha = item.get("sha256")
        if not isinstance(relative, str) or not _safe_relative_path(relative):
            raise RerankerAssetError(f"{field} contains an unsafe path")
        path = root / Path(PurePosixPath(relative))
        if not path.is_file():
            raise RerankerAssetError(f"reranker asset is missing: {relative}")
        actual = _file_sha256(path)
        if declared_sha != actual:
            raise RerankerAssetError(f"reranker asset SHA256 mismatch: {relative}")
        paths.append(path)
    return tuple(paths)


def _package_sha256(root: Path, manifest: dict) -> str:
    canonical = dict(manifest)
    canonical["artifact_sha256"] = None
    digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    file_entries = [
        *manifest.get("weights", []),
        *manifest.get("tokenizer_files", []),
    ]
    for item in sorted(file_entries, key=lambda value: value["path"]):
        relative = item["path"].encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        path = root / Path(PurePosixPath(item["path"]))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
