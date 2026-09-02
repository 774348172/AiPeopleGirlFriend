from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ._real_assets import RealAssetError, ReplyModelIdentity
from .world_mind.contracts import _require_identifier


GEMMA4_BASE_MODEL_ID = "google/gemma-4-12B-it"
GEMMA4_FULL_MIXED_MODEL_ID = "local/baiweixi-gemma-4-12B-v5-full-mixed"
GEMMA4_TARGETED_MODEL_ID = "local/baiweixi-gemma-4-12B-v5-targeted"
# The default runtime identity is the promoted production Adapter.
GEMMA4_MODEL_ID = GEMMA4_FULL_MIXED_MODEL_ID
GEMMA4_ADAPTER_IDS = {
    GEMMA4_FULL_MIXED_MODEL_ID: "baiweixi-v5-full-mixed-adapter",
    GEMMA4_TARGETED_MODEL_ID: "baiweixi-v5-targeted-adapter",
}
GEMMA_NF4_MANIFEST_NAME = "gemma4_nf4_runtime_manifest.json"


@dataclass(frozen=True, slots=True)
class GemmaNF4RuntimeProfile:
    max_seq_length: int
    dtype: str
    device: str
    attention_implementation: str


@dataclass(frozen=True, slots=True)
class LocalGemmaNF4Package:
    manifest_path: Path
    model_root: Path
    adapter_root: Path
    base_model_id: str
    identity: ReplyModelIdentity
    model_files: tuple[Path, ...]
    adapter_files: tuple[Path, ...]
    runtime: GemmaNF4RuntimeProfile

    @classmethod
    def load(cls, manifest_path: str | Path) -> "LocalGemmaNF4Package":
        path = Path(manifest_path).resolve()
        manifest = _load_manifest(path)
        if manifest.get("schema_version") != 1:
            raise RealAssetError("Gemma NF4 manifest schema_version must equal 1")
        model_id = manifest.get("model_id")
        if model_id not in GEMMA4_ADAPTER_IDS:
            raise RealAssetError("Gemma NF4 model_id is not an approved release")
        if manifest.get("base_model_id") != GEMMA4_BASE_MODEL_ID:
            raise RealAssetError(
                f"Gemma NF4 base_model_id must be {GEMMA4_BASE_MODEL_ID}"
            )
        if manifest.get("model_role") != "heroine_reply":
            raise RealAssetError("Gemma NF4 model_role must be heroine_reply")
        if manifest.get("adapter_id") != GEMMA4_ADAPTER_IDS[model_id]:
            raise RealAssetError("Gemma NF4 adapter_id does not match model_id")
        if manifest.get("network_access") != "forbidden":
            raise RealAssetError("Gemma NF4 network access must be forbidden")
        if manifest.get("thinking") is not False:
            raise RealAssetError("Gemma NF4 must freeze thinking=false")
        if manifest.get("adapter_loaded") is not True:
            raise RealAssetError("Gemma NF4 role package must freeze adapter_loaded=true")
        if manifest.get("quantization") != "bitsandbytes-nf4-runtime+lora":
            raise RealAssetError("Gemma NF4 quantization profile is invalid")

        model_root = _absolute_directory(manifest.get("model_root"), "model_root")
        model_files = _verify_files(model_root, manifest.get("model_files"))
        required = {
            "model.safetensors",
            "config.json",
            "chat_template.jinja",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        relative = {item.relative_to(model_root).as_posix() for item in model_files}
        if not required <= relative:
            raise RealAssetError("Gemma NF4 package is missing required model files")

        adapter_root = _absolute_directory(
            manifest.get("adapter_root"), "adapter_root"
        )
        adapter_files = _verify_files(adapter_root, manifest.get("adapter_files"))
        required_adapter = {
            "adapter_model.safetensors",
            "adapter_config.json",
            "chat_template.jinja",
            "tokenizer.json",
            "tokenizer_config.json",
        }
        relative_adapter = {
            item.relative_to(adapter_root).as_posix() for item in adapter_files
        }
        if not required_adapter <= relative_adapter:
            raise RealAssetError("Gemma NF4 package is missing required Adapter files")
        _verify_adapter_profile(adapter_root, model_root)

        runtime = _runtime_profile(manifest.get("runtime"))
        artifact_sha256 = _canonical_artifact_sha256(
            manifest, model_root, adapter_root, verify_files=False
        )
        if manifest.get("artifact_sha256") != artifact_sha256:
            raise RealAssetError("Gemma NF4 artifact SHA256 mismatch")

        return cls(
            manifest_path=path,
            model_root=model_root,
            adapter_root=adapter_root,
            base_model_id=GEMMA4_BASE_MODEL_ID,
            identity=ReplyModelIdentity(
                model_id=model_id,
                revision=_text(manifest.get("revision"), "revision"),
                base_revision=_text(manifest.get("base_revision"), "base_revision"),
                artifact_sha256=artifact_sha256,
                deployment_profile=_text(
                    manifest.get("deployment_profile"), "deployment_profile"
                ),
                model_role="heroine_reply",
                character_id=_identifier(manifest.get("character_id"), "character_id"),
                world_id=_identifier(manifest.get("world_id"), "world_id"),
                protagonist_id=_identifier(
                    manifest.get("protagonist_id"), "protagonist_id"
                ),
            ),
            model_files=model_files,
            adapter_files=adapter_files,
            runtime=runtime,
        )


def canonical_gemma_nf4_artifact_sha256(manifest_path: str | Path) -> str:
    path = Path(manifest_path).resolve()
    manifest = _load_manifest(path)
    model_root = _absolute_directory(manifest.get("model_root"), "model_root")
    adapter_root = _absolute_directory(manifest.get("adapter_root"), "adapter_root")
    return _canonical_artifact_sha256(
        manifest, model_root, adapter_root, verify_files=True
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RealAssetError("Gemma NF4 manifest is missing or invalid") from error
    if not isinstance(value, dict):
        raise RealAssetError("Gemma NF4 manifest must be an object")
    return value


def _runtime_profile(value: Any) -> GemmaNF4RuntimeProfile:
    if not isinstance(value, dict):
        raise RealAssetError("Gemma NF4 runtime profile must be an object")
    if value.get("load_in_4bit") is not True or value.get("text_only") is not True:
        raise RealAssetError("Gemma NF4 runtime must freeze 4-bit text-only loading")
    if value.get("trust_remote_code") is not True:
        raise RealAssetError("Gemma NF4 runtime must freeze trust_remote_code=true")
    if value.get("use_exact_model_name") is not True:
        raise RealAssetError("Gemma NF4 runtime must freeze use_exact_model_name=true")
    max_seq_length = value.get("max_seq_length")
    if not isinstance(max_seq_length, int) or max_seq_length < 1024:
        raise RealAssetError("Gemma NF4 max_seq_length must be at least 1024")
    dtype = _text(value.get("dtype"), "runtime.dtype")
    device = _text(value.get("device"), "runtime.device")
    attention = _text(
        value.get("attention_implementation"), "runtime.attention_implementation"
    )
    if dtype != "bfloat16" or device != "cuda" or attention != "eager":
        raise RealAssetError("Gemma NF4 runtime profile does not match the frozen stack")
    return GemmaNF4RuntimeProfile(max_seq_length, dtype, device, attention)


def _verify_files(root: Path, values: Any) -> tuple[Path, ...]:
    if not isinstance(values, list) or not values:
        raise RealAssetError("Gemma NF4 model_files must be a non-empty array")
    paths: list[Path] = []
    seen: set[str] = set()
    for item in values:
        if not isinstance(item, dict):
            raise RealAssetError("Gemma NF4 model_files entries must be objects")
        relative = item.get("path")
        if not isinstance(relative, str) or not _safe_relative(relative):
            raise RealAssetError("Gemma NF4 model_files contains an unsafe path")
        if relative in seen:
            raise RealAssetError("Gemma NF4 model_files contains duplicate paths")
        seen.add(relative)
        path = root / Path(PurePosixPath(relative))
        if not path.is_file():
            raise RealAssetError(f"Gemma NF4 asset is missing: {relative}")
        expected_bytes = item.get("bytes")
        if not isinstance(expected_bytes, int) or path.stat().st_size != expected_bytes:
            raise RealAssetError(f"Gemma NF4 asset size mismatch: {relative}")
        if item.get("sha256") != _file_sha256(path):
            raise RealAssetError(f"Gemma NF4 asset SHA256 mismatch: {relative}")
        paths.append(path)
    return tuple(paths)


def _canonical_artifact_sha256(
    manifest: dict[str, Any],
    model_root: Path,
    adapter_root: Path,
    *,
    verify_files: bool,
) -> str:
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
    for label, root, field in (
        ("model", model_root, "model_files"),
        ("adapter", adapter_root, "adapter_files"),
    ):
        values = manifest.get(field)
        if not isinstance(values, list):
            raise RealAssetError(f"Gemma NF4 {field} must be an array")
        for item in sorted(values, key=lambda entry: str(entry.get("path"))):
            relative = item.get("path")
            declared = item.get("sha256")
            if not isinstance(relative, str) or not isinstance(declared, str):
                raise RealAssetError(f"Gemma NF4 {label} file identity is invalid")
            actual = (
                _file_sha256(root / Path(PurePosixPath(relative)))
                if verify_files
                else declared
            )
            if verify_files and actual != declared:
                raise RealAssetError(
                    f"Gemma NF4 {label} asset SHA256 mismatch: {relative}"
                )
            digest.update(label.encode("ascii"))
            digest.update(relative.encode("utf-8"))
            try:
                digest.update(bytes.fromhex(actual))
            except ValueError as error:
                raise RealAssetError("Gemma NF4 asset SHA256 is invalid") from error
    return digest.hexdigest()


def _verify_adapter_profile(adapter_root: Path, model_root: Path) -> None:
    try:
        config = json.loads(
            (adapter_root / "adapter_config.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RealAssetError("Gemma NF4 Adapter config is invalid") from error
    if not isinstance(config, dict) or config.get("peft_type") != "LORA":
        raise RealAssetError("Gemma NF4 Adapter must be a PEFT LoRA")
    if config.get("inference_mode") is not True:
        raise RealAssetError("Gemma NF4 Adapter must freeze inference_mode=true")
    configured_base = config.get("base_model_name_or_path")
    if not isinstance(configured_base, str) or Path(configured_base).resolve() != model_root:
        raise RealAssetError("Gemma NF4 Adapter base model path mismatch")


def _absolute_directory(value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RealAssetError(f"Gemma NF4 {field} must be explicit")
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise RealAssetError(f"Gemma NF4 {field} must be an existing absolute directory")
    return path.resolve()


def _identifier(value: Any, field: str) -> str:
    try:
        _require_identifier(value, field)
    except ValueError as error:
        raise RealAssetError(str(error)) from error
    return value


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RealAssetError(f"Gemma NF4 {field} must be explicit")
    return value


def _safe_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
