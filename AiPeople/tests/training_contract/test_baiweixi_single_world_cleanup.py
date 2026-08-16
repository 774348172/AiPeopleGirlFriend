from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_local3060_qwen3"
    / "scripts"
    / "clean_single_world_prompts.py"
)
SOURCE = (
    ROOT
    / "training_packages"
    / "training_package_baiweixi_local3060_qwen3"
    / "data"
    / "baiweixi_ready.jsonl"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("single_world_cleanup", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_cleanup_preserves_every_dialogue_message() -> None:
    module = _load_module()
    source_rows = _load_jsonl(SOURCE)
    cleaned_rows, audit = module.clean_rows(source_rows)

    assert audit["rows"] == 1190
    assert audit["original_system_variants"] == 8
    assert audit["cleaned_system_variants"] == 8
    for source, cleaned in zip(source_rows, cleaned_rows, strict=True):
        assert source["conversations"][1:] == cleaned["conversations"][1:]


def test_cleanup_removes_old_world_concepts_and_adds_positive_contract() -> None:
    module = _load_module()
    cleaned_rows, _ = module.clean_rows(_load_jsonl(SOURCE))

    for row in cleaned_rows:
        prompt = row["conversations"][0]["value"]
        assert all(term not in prompt for term in module.FORBIDDEN_SYSTEM_TERMS)
        assert all(line in prompt for line in module.SINGLE_WORLD_LINES)
        assert "主角在此刻亲口说出" in prompt
        assert "你此刻实际得知的情况" in prompt


def test_build_writes_reproducible_manifest_without_activating_training(
    tmp_path: Path,
) -> None:
    module = _load_module()
    output = tmp_path / "cleaned.jsonl"
    manifest_path = tmp_path / "manifest.json"

    manifest = module.build(SOURCE, output, manifest_path)

    assert output.is_file()
    assert manifest_path.is_file()
    assert manifest["contract_id"] == "baiweixi-single-world-prompt-cleanup-v2"
    assert manifest["source"]["sha256"] == module.EXPECTED_SOURCE_SHA256
    assert manifest["output"]["rows"] == 1190
    assert manifest["output"]["training_config_activated"] is False
