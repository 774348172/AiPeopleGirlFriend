from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
CHAT02 = ROOT / "eval" / "chat02"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_execution_profile_schema_and_record_validate() -> None:
    schema = _load_json(CHAT02 / "schema" / "execution_profile.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        _load_json(CHAT02 / "execution_profile_v1.json")
    )


def test_execution_profile_live_artifacts_and_suite_hashes_match() -> None:
    profile = _load_json(CHAT02 / "execution_profile_v1.json")
    artifacts = [
        profile["backend"]["server"],
        profile["backend"]["runtime_adapter"],
        profile["request_contract"]["prompt_renderer"],
    ]
    for artifact in artifacts:
        path = Path(artifact["path"])
        assert path.is_file()
        assert path.stat().st_size == artifact["bytes"]
        assert _sha256(path) == artifact["sha256"]
    suite_path = Path(profile["suite_contract"]["manifest_path"])
    suite = _load_json(suite_path)
    assert _sha256(suite_path) == profile["suite_contract"]["manifest_file_sha256"]
    assert suite["freeze"]["manifest_sha256"] == profile["suite_contract"]["manifest_self_sha256"]


def test_shared_profile_is_weight_only_and_disables_thinking() -> None:
    profile = _load_json(CHAT02 / "execution_profile_v1.json")
    assert profile["comparison_contract"]["only_active_variable"] == "model_weights"
    assert profile["request_contract"]["thinking"] is False
    assert profile["request_contract"]["explicit_stop_sequences"] == []
    assert profile["launch"] == {
        "host": "127.0.0.1", "port": 18081, "context_size": 4096,
        "parallel": 1, "gpu_layers": "all", "flash_attention": True,
        "kv_cache_k": "q8_0", "kv_cache_v": "q8_0",
    }


def test_chat01h_refreeze_allows_real_run() -> None:
    profile = _load_json(CHAT02 / "execution_profile_v1.json")
    readiness = profile["readiness"]
    assert readiness["chat02b"] == "passed"
    assert readiness["chat01h"] == "passed"
    assert readiness["next_real_model_run"] == "ready"
    assert readiness["blockers"] == []
    assert profile["suite_contract"]["suite_id"] == "chat01-qinweixi-v3"
