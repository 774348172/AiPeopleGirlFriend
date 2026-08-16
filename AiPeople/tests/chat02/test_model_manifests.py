from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
CHAT02 = ROOT / "eval" / "chat02"
MODEL_DIR = CHAT02 / "models"


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


def _manifests() -> tuple[dict, dict]:
    return (
        _load_json(MODEL_DIR / "qwen3-4b-base-q4_k_m.json"),
        _load_json(MODEL_DIR / "qinweixi-current-q4_k_m.json"),
    )


def test_model_schema_and_both_manifests_validate() -> None:
    schema = _load_json(CHAT02 / "schema" / "model_manifest.schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for manifest in _manifests():
        validator.validate(manifest)


def test_all_manifest_artifacts_match_live_files() -> None:
    for manifest in _manifests():
        _assert_live_artifact(manifest["asset"])
        for evidence in manifest["adaptation"]["evidence"]:
            _assert_live_artifact(evidence)
        for key in ("converter", "converter_module", "quantizer"):
            artifact = manifest["export_provenance"][key]
            if artifact is not None:
                _assert_live_artifact(artifact)
        runtime_reference = manifest.get("runtime_manifest_reference")
        if runtime_reference is not None:
            _assert_live_artifact(runtime_reference)
        for excluded in manifest["excluded_inputs"]:
            _assert_live_artifact(excluded["artifact"])


def test_both_gguf_assets_have_valid_magic_and_controlled_identity() -> None:
    base, trained = _manifests()
    assert base["role"] == "base"
    assert trained["role"] == "trained"
    for manifest in (base, trained):
        with Path(manifest["asset"]["path"]).open("rb") as stream:
            assert stream.read(4) == b"GGUF"
    for key in (
        "architecture", "parameter_class", "quantization", "gguf_file_type",
        "tensor_count", "native_context_length", "eos_token_id",
        "chat_template_bytes", "chat_template_sha256",
    ):
        assert base["identity"][key] == trained["identity"][key]
    assert base["identity"]["architecture"] == "qwen3"
    assert base["identity"]["parameter_class"] == "4B"
    assert base["identity"]["quantization"] == "Q4_K_M"


def test_unknown_training_provenance_is_not_invented() -> None:
    base, trained = _manifests()
    assert base["source"]["local_source_revision"] == "1cfa9a7208912126459214e8b04321603b3df60c"
    assert base["source"]["training_source_revision"] is None
    assert trained["source"]["training_source_revision"] is None
    assert trained["adaptation"]["adapter_checkpoint"] is None
    assert trained["export_provenance"]["converter"] is None
    assert trained["export_provenance"]["quantizer"] is None


def test_stale_deployment_modelfile_is_explicitly_excluded() -> None:
    _, trained = _manifests()
    assert len(trained["excluded_inputs"]) == 1
    excluded = trained["excluded_inputs"][0]
    assert Path(excluded["artifact"]["path"]).name == "Modelfile"
    assert "不" in excluded["reason"] or "does not" in excluded["reason"]
