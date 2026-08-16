from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ._memory_vectors import (
    BGE_DIMENSION,
    BGE_MAX_SEQUENCE_LENGTH,
    BGE_MODEL_ID,
    BGE_POOLING,
    EncoderIdentity,
)
from ._reranker_assets import LocalRerankerPackage
from .world_mind.contracts import _require_identifier


BGE_MANIFEST_NAME = "bge_manifest.json"
REPLY_MANIFEST_NAME = "reply_manifest.json"
QWEN35_MODEL_ID = "Qwen/Qwen3.5-4B"


class RealAssetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LocalRetrievalAssetBundle:
    manifest_path: Path
    bge: LocalBgePackage
    reranker: LocalRerankerPackage

    @classmethod
    def load(cls, manifest_path: str | Path) -> "LocalRetrievalAssetBundle":
        path = Path(manifest_path).resolve()
        manifest = _load_manifest(path, "retrieval asset bundle")
        if manifest.get("schema_version") != 1:
            raise RealAssetError("retrieval asset bundle schema_version must equal 1")
        if manifest.get("network_access_at_runtime") != "forbidden":
            raise RealAssetError("retrieval asset bundle network access must be forbidden")
        bge_record = manifest.get("bge")
        reranker_record = manifest.get("reranker")
        if not isinstance(bge_record, dict) or not isinstance(reranker_record, dict):
            raise RealAssetError("retrieval asset bundle components are invalid")
        bge = LocalBgePackage.load(_record_path(bge_record, "bge"))
        reranker = LocalRerankerPackage.load(
            _record_path(reranker_record, "reranker")
        )
        if bge_record.get("revision") != bge.identity.revision or bge_record.get(
            "artifact_sha256"
        ) != bge.identity.artifact_sha256:
            raise RealAssetError("retrieval BGE identity mismatch")
        if reranker_record.get(
            "revision"
        ) != reranker.identity.revision or reranker_record.get(
            "artifact_sha256"
        ) != reranker.identity.artifact_sha256:
            raise RealAssetError("retrieval reranker identity mismatch")
        return cls(manifest_path=path, bge=bge, reranker=reranker)


@dataclass(frozen=True, slots=True)
class LocalBgePackage:
    root: Path
    identity: EncoderIdentity
    model_files: tuple[Path, ...]

    @classmethod
    def load(cls, root: str | Path) -> "LocalBgePackage":
        package_root = _require_directory(root, "local BGE package")
        manifest = _load_manifest(package_root / BGE_MANIFEST_NAME, "BGE")
        if manifest.get("schema_version") != 1:
            raise RealAssetError("BGE manifest schema_version must equal 1")
        if manifest.get("model_id") != BGE_MODEL_ID:
            raise RealAssetError(f"BGE model_id must be {BGE_MODEL_ID}")
        revision = _required_text(manifest, "revision", "BGE")
        if manifest.get("dimension") != BGE_DIMENSION:
            raise RealAssetError("BGE dimension must equal 512")
        if manifest.get("maximum_sequence_length") != BGE_MAX_SEQUENCE_LENGTH:
            raise RealAssetError("BGE maximum_sequence_length must equal 512")
        if manifest.get("pooling") != BGE_POOLING:
            raise RealAssetError(f"BGE pooling must equal {BGE_POOLING}")
        if manifest.get("normalization") != "runtime_l2":
            raise RealAssetError("BGE normalization must equal runtime_l2")
        if manifest.get("device") != "cpu" or manifest.get("network_access") != "forbidden":
            raise RealAssetError("BGE must be CPU-only with network_access forbidden")
        files = _verify_file_entries(package_root, manifest.get("model_files"), "model_files")
        required = {
            "modules.json",
            "sentence_bert_config.json",
            "1_Pooling/config.json",
        }
        relative_files = {item.relative_to(package_root).as_posix() for item in files}
        if not required <= relative_files:
            raise RealAssetError("BGE package is missing its frozen pooling profile files")
        _verify_bge_profile(package_root)
        artifact_sha256 = _package_sha256(package_root, manifest, BGE_MANIFEST_NAME)
        if manifest.get("artifact_sha256") != artifact_sha256:
            raise RealAssetError("BGE package artifact SHA256 mismatch")
        return cls(
            root=package_root,
            identity=EncoderIdentity(
                model_id=BGE_MODEL_ID,
                revision=revision,
                artifact_sha256=artifact_sha256,
                dimension=BGE_DIMENSION,
            ),
            model_files=files,
        )


@dataclass(frozen=True, slots=True)
class ReplyModelIdentity:
    model_id: str
    revision: str
    base_revision: str
    artifact_sha256: str
    deployment_profile: str
    model_role: str
    character_id: str
    world_id: str
    protagonist_id: str


@dataclass(frozen=True, slots=True)
class LocalQwen35Package:
    root: Path
    identity: ReplyModelIdentity
    gguf_path: Path
    chat_template_path: Path
    quantization: str

    @classmethod
    def load(cls, root: str | Path) -> "LocalQwen35Package":
        package_root = _require_directory(root, "local Qwen3.5 package")
        manifest = _load_manifest(package_root / REPLY_MANIFEST_NAME, "reply model")
        if manifest.get("schema_version") != 2:
            raise RealAssetError("reply manifest schema_version must equal 2")
        if manifest.get("model_id") != QWEN35_MODEL_ID:
            raise RealAssetError(f"reply model_id must be {QWEN35_MODEL_ID}")
        model_role = manifest.get("model_role")
        if model_role != "heroine_reply":
            raise RealAssetError("reply model_role must be heroine_reply")
        character_id = _manifest_identifier(manifest, "character_id")
        world_id = _manifest_identifier(manifest, "world_id")
        protagonist_id = _manifest_identifier(manifest, "protagonist_id")
        revision = _required_text(manifest, "revision", "reply")
        base_revision = _required_text(manifest, "base_revision", "reply")
        deployment = _required_text(manifest, "deployment_profile", "reply")
        if manifest.get("network_access") != "forbidden":
            raise RealAssetError("reply model network_access must be forbidden")
        gguf = _verify_single_file(package_root, manifest.get("gguf"), "gguf")
        chat_template = _verify_single_file(
            package_root, manifest.get("chat_template"), "chat_template"
        )
        quantization = manifest.get("gguf", {}).get("quantization")
        if not isinstance(quantization, str) or not quantization.strip():
            raise RealAssetError("reply GGUF quantization must be explicit")
        if manifest.get("thinking") is not False:
            raise RealAssetError("reply model must freeze thinking=false")
        artifact_sha256 = _package_sha256(package_root, manifest, REPLY_MANIFEST_NAME)
        if manifest.get("artifact_sha256") != artifact_sha256:
            raise RealAssetError("reply package artifact SHA256 mismatch")
        return cls(
            root=package_root,
            identity=ReplyModelIdentity(
                model_id=QWEN35_MODEL_ID,
                revision=revision,
                base_revision=base_revision,
                artifact_sha256=artifact_sha256,
                deployment_profile=deployment,
                model_role=model_role,
                character_id=character_id,
                world_id=world_id,
                protagonist_id=protagonist_id,
            ),
            gguf_path=gguf,
            chat_template_path=chat_template,
            quantization=quantization,
        )


@dataclass(frozen=True, slots=True)
class LocalRealAssetBundle:
    manifest_path: Path
    bge: LocalBgePackage
    reranker: LocalRerankerPackage
    reply: LocalQwen35Package
    llama_runtime_manifest: Path

    @classmethod
    def load(cls, manifest_path: str | Path) -> "LocalRealAssetBundle":
        path = Path(manifest_path).resolve()
        manifest = _load_manifest(path, "real asset bundle")
        if manifest.get("schema_version") != 1:
            raise RealAssetError("real asset bundle schema_version must equal 1")
        if manifest.get("network_access") != "forbidden":
            raise RealAssetError("real asset bundle network_access must be forbidden")
        bge_root = _absolute_path(manifest, "bge_package", "real asset bundle")
        reranker_root = _absolute_path(manifest, "reranker_package", "real asset bundle")
        reply_root = _absolute_path(manifest, "reply_package", "real asset bundle")
        runtime_manifest = _absolute_file(
            manifest, "llama_runtime_manifest", "real asset bundle"
        )
        bge = LocalBgePackage.load(bge_root)
        reranker = LocalRerankerPackage.load(reranker_root)
        reply = LocalQwen35Package.load(reply_root)
        expected = manifest.get("expected_artifact_sha256")
        actual = {
            "bge": bge.identity.artifact_sha256,
            "reranker": reranker.identity.artifact_sha256,
            "reply": reply.identity.artifact_sha256,
        }
        if expected != actual:
            raise RealAssetError("real asset bundle component SHA256 mismatch")
        return cls(
            manifest_path=path,
            bge=bge,
            reranker=reranker,
            reply=reply,
            llama_runtime_manifest=runtime_manifest,
        )


def canonical_bge_package_sha256(root: str | Path) -> str:
    package_root = Path(root).resolve()
    manifest = _load_manifest(package_root / BGE_MANIFEST_NAME, "BGE")
    return _package_sha256(package_root, manifest, BGE_MANIFEST_NAME)


def canonical_reply_package_sha256(root: str | Path) -> str:
    package_root = Path(root).resolve()
    manifest = _load_manifest(package_root / REPLY_MANIFEST_NAME, "reply model")
    return _package_sha256(package_root, manifest, REPLY_MANIFEST_NAME)


def _load_manifest(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RealAssetError(f"{label} manifest is missing or invalid") from error
    if not isinstance(value, dict):
        raise RealAssetError(f"{label} manifest must be an object")
    return value


def _require_directory(value: str | Path, label: str) -> Path:
    path = Path(value).resolve()
    if not path.is_dir():
        raise RealAssetError(f"{label} directory does not exist")
    return path


def _required_text(manifest: dict[str, Any], field: str, label: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RealAssetError(f"{label} {field} must be explicit")
    return value


def _manifest_identifier(manifest: dict[str, Any], field: str) -> str:
    value = manifest.get(field)
    try:
        _require_identifier(value, field)
    except ValueError as error:
        raise RealAssetError(str(error)) from error
    return value


def _absolute_path(manifest: dict[str, Any], field: str, label: str) -> Path:
    value = _required_text(manifest, field, label)
    path = Path(value)
    if not path.is_absolute():
        raise RealAssetError(f"{label} {field} must be an absolute path")
    return path.resolve()


def _absolute_file(manifest: dict[str, Any], field: str, label: str) -> Path:
    path = _absolute_path(manifest, field, label)
    if not path.is_file():
        raise RealAssetError(f"{label} {field} does not exist")
    return path


def _record_path(record: dict[str, Any], label: str) -> Path:
    value = record.get("path")
    if not isinstance(value, str) or not value.strip():
        raise RealAssetError(f"retrieval {label} path must be explicit")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise RealAssetError(f"retrieval {label} path must be an existing absolute directory")
    return path.resolve()


def _verify_file_entries(root: Path, values: Any, field: str) -> tuple[Path, ...]:
    if not isinstance(values, list) or not values:
        raise RealAssetError(f"{field} must be a non-empty array")
    paths: list[Path] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            raise RealAssetError(f"{field} entries must be objects")
        relative = item.get("path")
        if not isinstance(relative, str) or not _safe_relative_path(relative):
            raise RealAssetError(f"{field} contains an unsafe path")
        if relative in seen:
            raise RealAssetError(f"{field} contains duplicate paths")
        seen.add(relative)
        path = root / Path(PurePosixPath(relative))
        if not path.is_file():
            raise RealAssetError(f"asset is missing: {relative}")
        if item.get("sha256") != _file_sha256(path):
            raise RealAssetError(f"asset SHA256 mismatch: {relative}")
        paths.append(path)
    return tuple(paths)


def _verify_single_file(root: Path, value: Any, field: str) -> Path:
    paths = _verify_file_entries(root, [value] if isinstance(value, dict) else value, field)
    if len(paths) != 1:
        raise RealAssetError(f"{field} must describe exactly one file")
    return paths[0]


def _package_sha256(root: Path, manifest: dict[str, Any], manifest_name: str) -> str:
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
    file_entries: list[dict[str, Any]] = []
    for field in ("model_files", "gguf", "chat_template"):
        value = manifest.get(field)
        if isinstance(value, list):
            file_entries.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            file_entries.append(value)
    for item in sorted(file_entries, key=lambda entry: str(entry.get("path"))):
        relative = item.get("path")
        if isinstance(relative, str) and relative != manifest_name:
            path = root / Path(PurePosixPath(relative))
            digest.update(relative.encode("utf-8"))
            digest.update(bytes.fromhex(_file_sha256(path)))
    return digest.hexdigest()


def _verify_bge_profile(root: Path) -> None:
    try:
        modules = json.loads((root / "modules.json").read_text(encoding="utf-8"))
        sentence = json.loads(
            (root / "sentence_bert_config.json").read_text(encoding="utf-8")
        )
        pooling = json.loads((root / "1_Pooling" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RealAssetError("BGE pooling profile is missing or invalid") from error
    pooling_modules = [
        item
        for item in modules
        if isinstance(item, dict) and str(item.get("type", "")).endswith(".Pooling")
    ]
    if len(pooling_modules) != 1 or pooling_modules[0].get("path") != "1_Pooling":
        raise RealAssetError("BGE package must freeze exactly one 1_Pooling module")
    if (
        pooling.get("word_embedding_dimension") != BGE_DIMENSION
        or pooling.get("pooling_mode_mean_tokens") is not True
        or pooling.get("pooling_mode_cls_token") is not False
        or sentence.get("max_seq_length") != BGE_MAX_SEQUENCE_LENGTH
    ):
        raise RealAssetError("BGE package does not match the frozen mean-pooling profile")


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
