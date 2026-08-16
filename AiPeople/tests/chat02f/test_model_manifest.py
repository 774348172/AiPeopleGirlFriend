from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from eval.chat02f.gguf_metadata import read_gguf_metadata


ROOT = Path(__file__).resolve().parents[2]
CHAT02 = ROOT / "eval" / "chat02"
CHAT02F = ROOT / "eval" / "chat02f"
MANIFEST_PATH = CHAT02F / "models" / "qinweixi-v2500-final-q4_k_m.json"
BASE_MANIFEST_PATH = CHAT02 / "models" / "qwen3-4b-base-q4_k_m.json"
METADATA_KEYS = {
    "general.architecture",
    "general.file_type",
    "general.name",
    "qwen3.context_length",
    "tokenizer.ggml.eos_token_id",
    "tokenizer.chat_template",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_live_artifact(artifact: dict) -> None:
    path = Path(artifact["path"])
    assert path.is_absolute()
    assert path.is_file()
    assert path.stat().st_size == artifact["bytes"]
    assert _sha256(path) == artifact["sha256"]


def test_schema_is_valid_and_manifest_conforms() -> None:
    schema = _load_json(CHAT02F / "schema" / "model_manifest.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        _load_json(MANIFEST_PATH)
    )


def test_v2500_asset_and_excluded_modelfile_match_live_files() -> None:
    manifest = _load_json(MANIFEST_PATH)
    _assert_live_artifact(manifest["asset"])
    assert len(manifest["excluded_inputs"]) == 1
    excluded = manifest["excluded_inputs"][0]
    _assert_live_artifact(excluded["artifact"])
    assert Path(excluded["artifact"]["path"]).name == "Modelfile"
    assert "primary comparison" in excluded["reason"]


def test_v2500_identity_matches_live_gguf_metadata() -> None:
    manifest = _load_json(MANIFEST_PATH)
    identity = manifest["identity"]
    metadata = read_gguf_metadata(manifest["asset"]["path"], METADATA_KEYS)
    template = metadata["tokenizer.chat_template"].encode("utf-8")

    assert metadata["__gguf_version__"] == identity["gguf_version"] == 3
    assert metadata["__tensor_count__"] == identity["tensor_count"] == 398
    assert metadata["general.name"] == identity["general_name"]
    assert metadata["general.architecture"] == identity["architecture"] == "qwen3"
    assert metadata["general.file_type"] == identity["gguf_file_type"] == 15
    assert metadata["qwen3.context_length"] == identity["native_context_length"]
    assert metadata["tokenizer.ggml.eos_token_id"] == identity["eos_token_id"]
    assert len(template) == identity["chat_template_bytes"]
    assert hashlib.sha256(template).hexdigest() == identity["chat_template_sha256"]


def test_v2500_and_base_have_the_controlled_model_identity() -> None:
    trained = _load_json(MANIFEST_PATH)
    base = _load_json(BASE_MANIFEST_PATH)
    for key in (
        "architecture",
        "parameter_class",
        "quantization",
        "gguf_file_type",
        "tensor_count",
        "native_context_length",
        "eos_token_id",
        "chat_template_bytes",
        "chat_template_sha256",
    ):
        assert trained["identity"][key] == base["identity"][key]


def test_provenance_gaps_are_explicit_and_not_invented() -> None:
    manifest = _load_json(MANIFEST_PATH)
    assert manifest["status"] == "provenance_limited"
    assert manifest["source"]["local_source_revision"] is None
    assert manifest["source"]["training_source_revision"] is None
    assert manifest["adaptation"]["adapter_checkpoint"] is None
    assert manifest["adaptation"]["merge_method"] is None
    assert manifest["adaptation"]["evidence"] == []
    assert all(
        manifest["export_provenance"][key] is None
        for key in ("converter", "converter_module", "gguf_python_version", "quantizer")
    )


def test_existing_base_manifest_still_matches_its_live_asset() -> None:
    base = _load_json(BASE_MANIFEST_PATH)
    _assert_live_artifact(base["asset"])
    with Path(base["asset"]["path"]).open("rb") as stream:
        assert stream.read(4) == b"GGUF"
