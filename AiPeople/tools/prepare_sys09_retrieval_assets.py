from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime._real_assets import canonical_bge_package_sha256
from runtime._reranker_assets import canonical_package_sha256


BGE_REPO = "BAAI/bge-small-zh-v1.5"
RERANKER_REPO = "Qwen/Qwen3-Reranker-0.6B"
BGE_REVISION = "7999e1d3359715c523056ef9478215996d62a620"
RERANKER_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
BGE_FILES = (
    "1_Pooling/config.json",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.txt",
)
RERANKER_FILES = (
    "chat_template.jinja",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)


def prepare_assets(
    output_root: Path,
    *,
    endpoint: str,
    bge_revision: str = BGE_REVISION,
    reranker_revision: str = RERANKER_REVISION,
) -> dict[str, object]:
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    endpoint = endpoint.rstrip("/")
    if len(bge_revision) != 40 or len(reranker_revision) != 40:
        raise RuntimeError("model revisions must be immutable commit hashes")
    bge = _prepare_bge(output_root, bge_revision, endpoint)
    reranker = _prepare_reranker(output_root, reranker_revision, endpoint)
    return {
        "schema_version": 1,
        "network_access_at_runtime": "forbidden",
        "download_endpoint": endpoint,
        "bge": bge,
        "reranker": reranker,
    }


def _prepare_bge(output_root: Path, revision: str, endpoint: str) -> dict[str, object]:
    target = output_root / "bge-small-zh-v1.5"
    stage = _stage_path(output_root, "bge")
    _download_files(BGE_REPO, revision, BGE_FILES, stage, endpoint)
    _remove_download_metadata(stage)
    required = {
        "1_Pooling/config.json",
        "config.json",
        "model.safetensors",
        "modules.json",
        "sentence_bert_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "vocab.txt",
    }
    _require_files(stage, required)
    pooling_path = stage / "1_Pooling" / "config.json"
    pooling = json.loads(pooling_path.read_text(encoding="utf-8"))
    pooling.update(
        {
            "word_embedding_dimension": 512,
            "pooling_mode_mean_tokens": True,
            "pooling_mode_cls_token": False,
        }
    )
    _write_json(pooling_path, pooling)
    sentence_path = stage / "sentence_bert_config.json"
    sentence = json.loads(sentence_path.read_text(encoding="utf-8"))
    sentence["max_seq_length"] = 512
    _write_json(sentence_path, sentence)
    files = _file_entries(stage)
    manifest = {
        "schema_version": 1,
        "model_id": BGE_REPO,
        "revision": revision,
        "artifact_sha256": None,
        "dimension": 512,
        "maximum_sequence_length": 512,
        "pooling": "mean",
        "normalization": "runtime_l2",
        "device": "cpu",
        "network_access": "forbidden",
        "model_files": files,
    }
    _write_json(stage / "bge_manifest.json", manifest)
    manifest["artifact_sha256"] = canonical_bge_package_sha256(stage)
    _write_json(stage / "bge_manifest.json", manifest)
    _replace_directory(stage, target)
    return {
        "path": str(target),
        "revision": revision,
        "artifact_sha256": manifest["artifact_sha256"],
        "size_bytes": sum(item["size_bytes"] for item in files),
    }


def _prepare_reranker(
    output_root: Path, revision: str, endpoint: str
) -> dict[str, object]:
    target = output_root / "qwen3-reranker-0.6b"
    stage = _stage_path(output_root, "reranker")
    _download_files(RERANKER_REPO, revision, RERANKER_FILES, stage, endpoint)
    _remove_download_metadata(stage)
    required = {"config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json"}
    _require_files(stage, required)
    weights = _file_entries(stage, lambda path: path.suffix == ".safetensors")
    tokenizers = _file_entries(stage, lambda path: path.suffix != ".safetensors")
    manifest = {
        "schema_version": 1,
        "model_id": RERANKER_REPO,
        "revision": revision,
        "deployment_profile": "cuda-fp16",
        "activation_threshold": 0.5,
        "relative_top_floor": 0.1,
        "relative_top_margin": 0.05,
        "maximum_pair_tokens": 128,
        "output_contract": "yes_no_logits_per_memory_id",
        "weights": weights,
        "tokenizer_files": tokenizers,
        "artifact_sha256": None,
    }
    _write_json(stage / "reranker_manifest.json", manifest)
    manifest["artifact_sha256"] = canonical_package_sha256(stage)
    _write_json(stage / "reranker_manifest.json", manifest)
    _replace_directory(stage, target)
    return {
        "path": str(target),
        "revision": revision,
        "artifact_sha256": manifest["artifact_sha256"],
        "size_bytes": sum(item["size_bytes"] for item in (*weights, *tokenizers)),
    }


def _file_entries(root: Path, predicate=lambda path: True) -> list[dict[str, object]]:
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.name.endswith("_manifest.json") or not predicate(path):
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _file_sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return entries


def _download_files(
    repo_id: str,
    revision: str,
    files: tuple[str, ...],
    target: Path,
    endpoint: str,
) -> None:
    for filename in files:
        destination = target / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"{endpoint}/{repo_id}/resolve/{revision}/{filename}"
        command = [
            "curl.exe",
            "--fail",
            "--location",
            "--retry",
            "8",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--max-time",
            "1800",
            "--continue-at",
            "-",
            "--output",
            str(destination),
            url,
        ]
        result = subprocess.run(command, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"asset download failed: {repo_id}/{filename}")


def _require_files(root: Path, values: set[str]) -> None:
    missing = sorted(value for value in values if not (root / value).is_file())
    if missing:
        raise RuntimeError(f"downloaded package is incomplete: {missing}")


def _stage_path(root: Path, label: str) -> Path:
    path = root / f".{label}-{uuid.uuid4().hex}.staging"
    path.mkdir()
    return path


def _replace_directory(stage: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.backup")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        target.replace(backup)
    try:
        stage.replace(target)
    except BaseException:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _remove_download_metadata(root: Path) -> None:
    metadata = root / ".cache"
    if metadata.exists():
        shutil.rmtree(metadata)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare frozen SYS-09 retrieval assets.")
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "local_runtime" / "models" / "retrieval"),
    )
    parser.add_argument("--endpoint", default="https://hf-mirror.com")
    parser.add_argument("--bge-revision", default=BGE_REVISION)
    parser.add_argument("--reranker-revision", default=RERANKER_REVISION)
    return parser


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = _parser().parse_args()
    result = prepare_assets(
        Path(args.output_root),
        endpoint=args.endpoint,
        bge_revision=args.bge_revision,
        reranker_revision=args.reranker_revision,
    )
    _write_json(Path(args.output_root).resolve() / "retrieval_assets.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
