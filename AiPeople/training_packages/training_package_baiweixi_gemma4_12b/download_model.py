from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT.parent / "models" / "Gemma-4-12B-it"
MODEL_ID = "google/gemma-4-12B-it"
REVISION = "707f0a3b8a3c7ad586ed01e27eafbad8a27dd0f7"
CONFIG_COMPATIBILITY_MAP = {
    "Gemma4UnifiedForConditionalGeneration": "Gemma4ForConditionalGeneration",
    "gemma4_unified": "gemma4",
    "gemma4_unified_audio": "gemma4_audio",
    "gemma4_unified_text": "gemma4_text",
    "gemma4_unified_vision": "gemma4_vision",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_transformers_55_compatible() -> tuple[Path, Path]:
    config_path = MODEL_DIR / "config.json"
    upstream_path = MODEL_DIR / "config.upstream.json"
    shutil.copyfile(config_path, upstream_path)

    config = json.loads(config_path.read_text(encoding="utf-8"))

    def replace_names(value):
        if isinstance(value, dict):
            return {key: replace_names(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace_names(item) for item in value]
        if isinstance(value, str):
            return CONFIG_COMPATIBILITY_MAP.get(value, value)
        return value

    compatible = replace_names(config)
    config_path.write_text(
        json.dumps(compatible, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return upstream_path, config_path


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MODEL_ID,
        revision=REVISION,
        local_dir=MODEL_DIR,
        max_workers=4,
    )
    required = [
        MODEL_DIR / "config.json",
        MODEL_DIR / "model.safetensors",
        MODEL_DIR / "tokenizer.json",
        MODEL_DIR / "tokenizer_config.json",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Downloaded model is incomplete: {missing}")

    upstream_config, training_config = make_transformers_55_compatible()

    info = HfApi().model_info(MODEL_ID, revision=REVISION)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now().astimezone().isoformat(),
        "model_id": MODEL_ID,
        "requested_revision": REVISION,
        "resolved_revision": info.sha,
        "model_dir": str(MODEL_DIR.resolve()),
        "model_safetensors_bytes": (MODEL_DIR / "model.safetensors").stat().st_size,
        "model_safetensors_sha256": sha256(MODEL_DIR / "model.safetensors"),
        "upstream_config_sha256": sha256(upstream_config),
        "training_config_sha256": sha256(training_config),
        "config_compatibility_map": CONFIG_COMPATIBILITY_MAP,
        "config_compatibility_scope": "class and model_type names only; model tensors unchanged",
    }
    (MODEL_DIR / "download_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
